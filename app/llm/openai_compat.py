"""在线 Provider：OpenAI 兼容协议（DeepSeek / 通义 / OpenAI 可切换）。

带指数退避重试与 token usage 落 trace；引用合法性由 ResilientProvider
统一 guard（本类提供 rewrite 受限重写能力）。离线测试不触网，
本模块在 CI 中仅做提示词构造与解析逻辑的单元校验。
"""

import json
import time
import uuid

import httpx

from app.domain_registry import DomainRegistry
from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.report.citation_guard import extract_conclusion_citations, rewrite_prompt
from app.report.synth import collect_sources
from app.schema.constraints import Constraint
from app.schema.plan import Plan, SubTask


class PlanError(RuntimeError):
    """计划解析失败（模型输出不是合法 JSON 或不符合 schema）。"""


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        registry: DomainRegistry,
        timeout: float = 60.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
        trace_store=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.registry = registry
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._trace_store = trace_store

    # ---------- plan ----------

    def plan(self, ctx: PlanContext) -> Plan:
        payload = self._chat(
            system=(
                "你是研究任务规划器。把用户的技术选型问题分解为检索子任务。"
                "只输出 JSON：{\"rationale\": str, \"constraints\": [{\"kind\": "
                "\"scale|team|cost|deployment|stack|latency|compliance|other\", \"text\": str, "
                "\"value\": str, \"unit\": str}], \"subtasks\": [{\"route\": \"web\"|\"kb\", "
                "\"topic\": str, \"object_name\": str, \"object_display\": str, \"aspect\": str, \"query\": str}]}。"
                "对比型问题按对象拆分；每对象同时给 web 与 kb 两条路；"
                "replan 时只针对给定缺口生成补充子任务，并沿用已有约束。"
            ),
            user=self._plan_user_prompt(ctx),
            trace_id=ctx.trace_id,
            stage="plan",
        )
        return self._parse_plan(payload)

    def _plan_user_prompt(self, ctx: PlanContext) -> str:
        topics = "; ".join(
            f"{t.topic}(对象:{','.join(t.objects)}|维度:{','.join(t.aspects)})"
            for t in self.registry.topics.values()
        )
        parts = [f"研究问题：{ctx.question}", f"已知主题域：{topics}"]
        if ctx.related_history:
            hist = "; ".join(h.get("question", "") for h in ctx.related_history)
            parts.append(f"该用户的相关历史研究（避免重复、可复用结论）：{hist}")
        if ctx.constraints:
            parts.append(
                "已抽取的用户约束（沿用，不得忽略）："
                + json.dumps([c.model_dump() for c in ctx.constraints], ensure_ascii=False)
            )
        if ctx.replan_focus:
            parts.append(f"上轮信息缺口：{json.dumps(ctx.replan_focus, ensure_ascii=False)}")
        return "\n".join(parts)

    def _parse_plan(self, raw: str) -> Plan:
        try:
            data = json.loads(self._extract_json(raw))
            subtasks = [
                SubTask(
                    subtask_id=f"T{i + 1}",
                    route=s["route"],
                    topic=s.get("topic", ""),
                    object_name=s.get("object_name", ""),
                    object_display=s.get("object_display", ""),
                    aspect=s.get("aspect", "general"),
                    query=s["query"],
                )
                for i, s in enumerate(data.get("subtasks", []))
            ]
            constraints = []
            allowed_kinds = {"scale", "team", "cost", "deployment", "stack",
                             "latency", "compliance", "other"}
            for i, item in enumerate(data.get("constraints", []), start=1):
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind", "other")
                if kind not in allowed_kinds:
                    kind = "other"
                constraints.append(Constraint(
                    constraint_id=f"C{i}",
                    kind=kind,
                    text=str(item.get("text", "")),
                    value=str(item.get("value", "")),
                    unit=str(item.get("unit", "")),
                ))
            return Plan(plan_id=f"plan-{uuid.uuid4().hex[:8]}", subtasks=subtasks,
                        rationale=data.get("rationale", ""), constraints=constraints)
        except (ValueError, KeyError, TypeError) as exc:
            raise PlanError(f"模型计划解析失败: {exc}") from exc

    # ---------- compose ----------

    def compose(self, ctx: ComposeContext) -> ComposeResult:
        evidence_lines = [
            f"[{e.evidence_id}] (来源[{e.source.source_id}]) {e.claim}：{e.quote[:160]}"
            for e in ctx.evidences
        ]
        parts = [f"研究问题：{ctx.question}"]
        if ctx.constraints:
            parts.append(
                "用户约束（必须逐项回应；不得忽略，也不得编造满足情况）：\n"
                + "\n".join(
                    f"- [{c.constraint_id}] {c.text}（kind={c.kind}, value={c.value}）"
                    for c in ctx.constraints
                )
            )
        if ctx.constraint_summary is not None:
            parts.append(
                "结构化约束核验结果（只能据此陈述 satisfied/violated/unknown，"
                "unknown 必须写成无法判断）：\n"
                + json.dumps(ctx.constraint_summary.model_dump(), ensure_ascii=False)
            )
        if ctx.information_gaps:
            parts.append(
                "已知信息缺口（必须在报告中单列并明确说明，禁止假装已解决）：\n"
                + "\n".join(f"- {g}" for g in ctx.information_gaps)
            )
        parts.append("证据清单：\n" + "\n".join(evidence_lines))
        md = self._chat(
            system=(
                "你是研究报告撰写者。仅依据给定证据撰写中文研究报告（markdown），"
                "包含：结论摘要、约束核对、分维度对比（若为对比问题）、风险与信息缺口。"
                "每个结论句末必须标注其依据的证据编号，形如 [E3][E7]；"
                "禁止使用清单之外的编号；证据不足的结论必须明说。"
                "不得把 unknown 的结构化约束写成已满足。"
            ),
            user="\n\n".join(parts),
            trace_id=ctx.trace_id,
            stage="compose",
        )
        return ComposeResult(
            markdown=md,
            raw_markdown=md,
            summary=self._first_paragraph(md),
            citations=extract_conclusion_citations(md, ctx.evidences),
            sources=collect_sources(list(ctx.evidences)),
        )

    def rewrite(self, markdown: str, invalid_ids: list[str]) -> str:
        """受限重写：只允许修正非法引用，用于引用 guard 的第一次修复尝试。"""
        return self._chat(
            system="你是文本修正器。严格遵守用户指令，只做被允许的修改。",
            user=rewrite_prompt(markdown, invalid_ids),
            response_json=False,
        )

    @staticmethod
    def _first_paragraph(markdown: str) -> str:
        """取标题之后的第一个非空段落作为摘要。"""
        lines = [ln.strip() for ln in markdown.splitlines() if ln.strip()]
        for line in lines:
            if not line.startswith("#"):
                return line[:300]
        return ""

    # ---------- HTTP ----------

    def _chat(self, *, system: str, user: str, trace_id: str | None = None,
              stage: str = "chat", response_json: bool = True) -> str:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if response_json:
            body["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    total_tokens = int(
                        usage.get("total_tokens") or prompt_tokens + completion_tokens
                    )
                    self._record_usage(
                        trace_id, stage, total_tokens, prompt_tokens, completion_tokens
                    )
                    return data["choices"][0]["message"]["content"]
                last_error = RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:120]}")
                if resp.status_code < 500 and resp.status_code != 429:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(self.retry_base_delay * (2**attempt))
        raise last_error or RuntimeError("LLM 调用失败")

    def _record_usage(
        self,
        trace_id: str | None,
        stage: str,
        tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        if self._trace_store is not None and trace_id and tokens > 0:
            self._trace_store.record(
                trace_id, node="llm", event_type="usage",
                payload={
                    "stage": stage,
                    "model": self.model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": tokens,
                },
                token_usage=tokens,
            )

    @staticmethod
    def _extract_json(raw: str) -> str:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"响应中不含 JSON: {raw[:120]}")
        return raw[start : end + 1]
