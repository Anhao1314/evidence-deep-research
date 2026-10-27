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
from app.report.citation_guard import extract_citations, rewrite_prompt
from app.report.synth import collect_sources
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
                "只输出 JSON：{\"rationale\": str, \"subtasks\": [{\"route\": \"web\"|\"kb\", "
                "\"topic\": str, \"object_name\": str, \"object_display\": str, \"aspect\": str, \"query\": str}]}。"
                "对比型问题按对象拆分；每对象同时给 web 与 kb 两条路；"
                "replan 时只针对给定缺口生成补充子任务。"
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
            return Plan(plan_id=f"plan-{uuid.uuid4().hex[:8]}", subtasks=subtasks,
                        rationale=data.get("rationale", ""))
        except (ValueError, KeyError, TypeError) as exc:
            raise PlanError(f"模型计划解析失败: {exc}") from exc

    # ---------- compose ----------

    def compose(self, ctx: ComposeContext) -> ComposeResult:
        evidence_lines = [
            f"[{e.evidence_id}] (来源[{e.source.source_id}]) {e.claim}：{e.quote[:160]}"
            for e in ctx.evidences
        ]
        md = self._chat(
            system=(
                "你是研究报告撰写者。仅依据给定证据撰写中文研究报告（markdown），"
                "包含：结论摘要、分维度对比（若为对比问题）、风险与信息缺口。"
                "每个结论句末必须标注其依据的证据编号，形如 [E3][E7]；"
                "禁止使用清单之外的编号；证据不足的结论必须明说。"
            ),
            user=f"研究问题：{ctx.question}\n\n证据清单：\n" + "\n".join(evidence_lines),
            trace_id=ctx.trace_id,
            stage="compose",
        )
        return ComposeResult(
            markdown=md,
            summary=self._first_paragraph(md),
            citations=extract_citations(md, ctx.evidences),
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
                    self._record_usage(trace_id, stage, int(data.get("usage", {}).get("total_tokens", 0)))
                    return data["choices"][0]["message"]["content"]
                last_error = RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:120]}")
                if resp.status_code < 500 and resp.status_code != 429:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(self.retry_base_delay * (2**attempt))
        raise last_error or RuntimeError("LLM 调用失败")

    def _record_usage(self, trace_id: str | None, stage: str, tokens: int) -> None:
        if self._trace_store is not None and trace_id and tokens > 0:
            self._trace_store.record(
                trace_id, node="llm", event_type="usage",
                payload={"stage": stage, "model": self.model}, token_usage=tokens,
            )

    @staticmethod
    def _extract_json(raw: str) -> str:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"响应中不含 JSON: {raw[:120]}")
        return raw[start : end + 1]
