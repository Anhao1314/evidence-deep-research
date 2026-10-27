"""弹性 LLM Provider：在线主路 + 规则兜底 + 统一引用 guard。

降级语义（都会落 trace）：
- plan 失败（网络/解析）-> HeuristicProvider 的规则分解兜底；
- compose 失败 -> 模板合成兜底；
- compose 成功但引用非法 -> 受限重写一次，仍非法则剥离并告警。

离线模式不经过本类（直接用 HeuristicProvider），但 ResilientProvider
用 fake primary 测试，失败恢复行为在离线测试里可完整验证。
"""

from app.report.citation_guard import (
    check_citations,
    extract_citations,
    rewrite_prompt,
    strip_invalid_citations,
)
from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.schema.plan import Plan
from app.trace.store import TraceStore


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
            return self._fallback.compose(ctx)

        valid = {e.evidence_id for e in ctx.evidences}
        invalid = check_citations(result.markdown, valid)
        if not invalid:
            return result

        # 受限重写一次
        rewritten: str | None = None
        rewriter = getattr(self._primary, "rewrite", None)
        if rewriter is not None:
            try:
                rewritten = rewriter(result.markdown, invalid)
            except Exception:  # noqa: BLE001 —— 重写失败走剥离
                rewritten = None

        if rewritten and not check_citations(rewritten, valid):
            self._record(ctx.trace_id, "degrade", {
                "stage": "citation_rewrite", "invalid_ids": invalid, "fixed_by": "rewrite",
            })
            return ComposeResult(
                markdown=rewritten,
                summary=result.summary,
                citations=extract_citations(rewritten, ctx.evidences),
                sources=result.sources,
            )

        # 二次仍非法：剥离非法引用（宁可无引用，不可假引用）
        cleaned = strip_invalid_citations(result.markdown, invalid)
        self._record(ctx.trace_id, "error", {
            "stage": "citation_guard",
            "invalid_ids": invalid,
            "action": "stripped",
        })
        return ComposeResult(
            markdown=cleaned,
            summary=result.summary,
            citations=extract_citations(cleaned, ctx.evidences),
            sources=result.sources,
        )

    def _record(self, trace_id: str | None, event_type: str, payload: dict) -> None:
        if self._trace_store is not None and trace_id:
            self._trace_store.record(trace_id, node="llm", event_type=event_type, payload=payload)
