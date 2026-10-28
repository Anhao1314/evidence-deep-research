"""弹性 LLM Provider：在线主路 + 规则兜底 + 引用 Guard。

Guard 语义（与 ADR-005 一致）：
- 非法引用先做一次受限重写；重写必须通过“去掉引用后正文不变”的程序校验；
- 重写仍不合法时，隔离受影响结论，不保留无引用结论；
- 所有结论都被隔离时，回退到有引用的模板报告或安全占位（fail-closed）。
"""

import re

from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.report.citation_guard import (
    enforce_citation_guard,
    extract_conclusion_citations,
)
from app.report.synth import collect_sources
from app.schema.guard import GuardAction, GuardAudit
from app.schema.plan import Plan
from app.trace.store import TraceStore

_CITE_RE = re.compile(r"\[(E\d+)\]")


def _summary_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip()
        if not text or text.startswith(("#", "|")):
            continue
        text = _CITE_RE.sub("", text).strip()
        if text:
            return text[:300]
    return ""


class ResilientProvider:
    """实现 LLMProvider 协议；primary 期望为 OpenAICompatProvider（有 rewrite 能力）。"""

    def __init__(self, primary, fallback, trace_store: TraceStore | None = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._trace_store = trace_store

    def plan(self, ctx: PlanContext) -> Plan:
        try:
            return self._primary.plan(ctx)
        except Exception as exc:  # noqa: BLE001 —— 任何主路失败都降级，不阻塞研究流程
            self._record(ctx.trace_id, "degrade", {
                "stage": "plan",
                "error": str(exc)[:200],
                "fallback": type(self._fallback).__name__,
            })
            return self._fallback.plan(ctx)

    def compose(self, ctx: ComposeContext) -> ComposeResult:
        try:
            result = self._primary.compose(ctx)
        except Exception as exc:  # noqa: BLE001
            self._record(ctx.trace_id, "degrade", {
                "stage": "compose",
                "error": str(exc)[:200],
                "fallback": type(self._fallback).__name__,
            })
            fallback = self._fallback.compose(ctx)
            fallback.guard_audit = GuardAudit(
                ran=False,
                raw_markdown=fallback.markdown,
                final_markdown=fallback.markdown,
                actions=[GuardAction(action="not_run", detail="在线主路失败，使用模板兜底")],
            )
            return fallback

        valid_ids = {e.evidence_id for e in ctx.evidences}
        rewriter = getattr(self._primary, "rewrite", None)
        fallback_result: ComposeResult | None = None

        def _fallback_markdown() -> str:
            nonlocal fallback_result
            if self._fallback is None:
                raise RuntimeError("no fallback provider")
            fallback_result = self._fallback.compose(ctx)
            return fallback_result.markdown

        audit = enforce_citation_guard(
            result.markdown,
            valid_ids,
            rewrite_fn=rewriter,
            fallback_fn=_fallback_markdown if self._fallback is not None else None,
        )
        final_md = audit.final_markdown
        citations = extract_conclusion_citations(final_md, ctx.evidences)
        sources = result.sources or collect_sources(ctx.evidences)
        summary = _summary_from_markdown(final_md)
        if fallback_result is not None:
            sources = fallback_result.sources or sources
            summary = fallback_result.summary or summary

        self._record_guard(ctx.trace_id, audit)
        return ComposeResult(
            markdown=final_md,
            raw_markdown=result.markdown,
            summary=summary,
            citations=citations,
            sources=sources,
            guard_audit=audit,
            unverified_claims=audit.unverified_claims,
        )

    def _record_guard(self, trace_id: str | None, audit: GuardAudit) -> None:
        if self._trace_store is None or not trace_id:
            return
        if (not audit.invalid_citation_ids and not audit.rewrite_attempted
                and not audit.claims_quarantined and not audit.fallback_used
                and not audit.fail_closed):
            return
        quarantined = audit.claims_quarantined > 0
        event_type = "error" if (quarantined or audit.fail_closed) else "degrade"
        self._record(trace_id, event_type, {
            "stage": "citation_guard",
            "actions": [a.model_dump() for a in audit.actions],
            "invalid_ids": audit.invalid_citation_ids,
            "rewrite_attempted": audit.rewrite_attempted,
            "rewrite_accepted": audit.rewrite_accepted,
            "rewrite_rejected_reason": audit.rewrite_rejected_reason,
            "claims_checked": audit.claims_checked,
            "claims_quarantined": audit.claims_quarantined,
            "fallback_used": audit.fallback_used,
            "fail_closed": audit.fail_closed,
            "unverified_claims": [c.model_dump() for c in audit.unverified_claims],
            "diff": audit.diff,
        })

    def _record(self, trace_id: str | None, event_type: str, payload: dict) -> None:
        if self._trace_store is not None and trace_id:
            self._trace_store.record(trace_id, node="llm", event_type=event_type, payload=payload)