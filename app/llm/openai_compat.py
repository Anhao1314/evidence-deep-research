"""在线 Provider：OpenAI 兼容协议（DeepSeek / 通义 / OpenAI 可切换）。

M1 提供基础实现；在线链路的重试/降级/引用校验在 M3 与 Trace 一起完善。
离线测试不触网，本模块在 CI 中仅做导入与提示词构造的单元校验。
"""

import json
import re
import uuid

import httpx

from app.domain_registry import DomainRegistry
from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.report.synth import collect_sources
from app.schema.evidence import Evidence
from app.schema.plan import Plan, SubTask
from app.schema.report import Citation


class PlanError(RuntimeError):
    """计划解析失败（模型输出不是合法 JSON 或不符合 schema）。"""


class OpenAICompatProvider:
    def __init__(self, *, base_url: str, api_key: str, model: str, registry: DomainRegistry,
                 timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.registry = registry
        self.timeout = timeout

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
        )
        return self._parse_plan(payload)

    def _plan_user_prompt(self, ctx: PlanContext) -> str:
        topics = "; ".join(
            f"{t.topic}(对象:{','.join(t.objects)}|维度:{','.join(t.aspects)})"
            for t in self.registry.topics.values()
        )
        parts = [f"研究问题：{ctx.question}", f"已知主题域：{topics}"]
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
        )
        return ComposeResult(markdown=md, citations=self._extract_citations(md, ctx.evidences),
                             sources=collect_sources(list(ctx.evidences)))

    def _extract_citations(self, markdown: str, evidences: list[Evidence]) -> list[Citation]:
        """从生成的 markdown 抽取 [E#] 引用；非法编号在 M3 的 guard 中处理。"""
        valid = {e.evidence_id for e in evidences}
        citations: list[Citation] = []
        for i, line in enumerate(markdown.splitlines()):
            ids = [m for m in re.findall(r"\[(E\d+)\]", line) if m in valid]
            if ids:
                citations.append(Citation(location=f"line#{i + 1}", evidence_ids=sorted(set(ids))))
        return citations

    # ---------- HTTP ----------

    def _chat(self, *, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _extract_json(raw: str) -> str:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"响应中不含 JSON: {raw[:120]}")
        return raw[start : end + 1]
