"""主图节点工厂：每个节点是闭包，捕获自身依赖（provider / registry / settings）。

节点的返回值只包含它负责更新的 state key；步数计数由每个节点自增，
条件边与护栏（Guardrails）共享同一判断，保证 README 中的状态图与代码一致。
"""

import uuid
from typing import TYPE_CHECKING

from langgraph.types import Send

from app.agents.base import SubAgentState
from app.config import Settings
from app.domain_registry import ASPECT_ZH, DomainRegistry
from app.guardrails import Guardrails, is_duplicate_plan
from app.llm.base import ComposeContext, LLMProvider, PlanContext
from app.report.synth import collect_sources
from app.schema.evidence import Evidence
from app.schema.plan import Plan
from app.schema.report import Report, ReportMeta

if TYPE_CHECKING:
    from app.memory.store import MemoryStore

# 维度排序：性能 -> 生态 -> 成本 -> 成熟度 -> 其他
_ASPECT_ORDER = list(ASPECT_ZH)


def _aspect_rank(aspect: str) -> int:
    return _ASPECT_ORDER.index(aspect) if aspect in _ASPECT_ORDER else 99


def make_load_context_node(settings: Settings, memory: "MemoryStore"):
    """记忆召回：偏好全量注入 + 历史主题/结论按 BM25 相似度召回（跨会话长期记忆）。"""

    def load_context(state: dict) -> dict:
        recall = memory.recall(state.get("user_id", "default"), state["question"])
        return {
            "preferences": recall.preferences,
            "related_history": [
                {"question": t.question, "summary": t.summary} for t in recall.related_topics
            ],
            "step_count": state.get("step_count", 0) + 1,
        }

    return load_context


def make_plan_node(provider: LLMProvider, settings: Settings):
    def plan(state: dict) -> dict:
        ctx = PlanContext(
            question=state["question"],
            preferences=state.get("preferences", {}),
            related_history=state.get("related_history", []),
            replan_focus=state.get("replan_focus", []),
        )
        plan = provider.plan(ctx)
        history = list(state.get("plan_history", []))
        duplicate = is_duplicate_plan(plan, history)
        history.append(plan.signature())
        return {
            "plan": plan,
            "plan_history": history,
            "plan_is_duplicate": duplicate,
            "plan_rounds": state.get("plan_rounds", 0) + 1,
            "replan_focus": [],  # 已消费
            "step_count": state.get("step_count", 0) + 1,
        }

    return plan


def make_dispatch_router(settings: Settings):
    """plan 之后的条件边：护栏判定 -> synthesize；否则用 Send API 动态扇出子任务。

    LangGraph 1.x 中 Send 列表须从条件边 router 返回（节点返回 Send 已废弃），
    因此"派发"实现为 plan 出边上的动作，而非独立节点。
    """

    def dispatch(state: dict) -> str | list[Send]:
        if state.get("plan_is_duplicate"):
            return "synthesize"
        plan = state.get("plan")
        if not plan or not plan.subtasks:
            return "synthesize"
        if state.get("step_count", 0) >= settings.max_steps:
            return "synthesize"
        sends: list[Send] = []
        for subtask in plan.subtasks:
            target = "rag_agent" if subtask.route == "kb" else "web_agent"
            payload: SubAgentState = {"subtask": subtask, "question": state["question"]}
            sends.append(Send(target, payload))
        return sends

    return dispatch


def make_merge_node(settings: Settings):
    def merge(state: dict) -> dict:
        evidences = _dedupe_and_assign(list(state.get("raw_evidences", [])), settings.evidence_budget)
        # 记忆偏好可收紧单对象证据上限（跨会话偏好的第二个生效点）
        per_object = int(state.get("preferences", {}).get("max_evidences_per_object", "0") or 0)
        if per_object > 0:
            evidences = _cap_per_object(evidences, per_object)
        coverage: dict[str, list[str]] = {}
        for ev in evidences:
            key = f"{ev.topic}/{ev.object_name}"
            aspects = set(coverage.get(key, []))
            aspects.add(ev.aspect)
            coverage[key] = sorted(aspects)
        return {"evidences": evidences, "coverage": coverage,
                "step_count": state.get("step_count", 0) + 1}

    return merge


def _cap_per_object(evidences: list[Evidence], per_object: int) -> list[Evidence]:
    counts: dict[str, int] = {}
    out: list[Evidence] = []
    for ev in evidences:  # 已按相关度排序，超限的丢弃
        key = ev.object_name or ev.topic
        counts[key] = counts.get(key, 0)
        if counts[key] >= per_object:
            continue
        counts[key] += 1
        out.append(ev)
    return out


def _dedupe_and_assign(evidences: list[Evidence], budget: int) -> list[Evidence]:
    """来源去重 -> 排序 -> 统一分配 S*/E* 编号 -> 预算截断。

    去重粒度：同一来源同一定位（url 或 doc_id+locator）只保留得分最高的一条。
    """
    best: dict[tuple, Evidence] = {}
    for ev in evidences:
        key = (ev.source.kind, ev.source.url or ev.source.doc_id, ev.source.locator or "")
        if key not in best or ev.score > best[key].score:
            best[key] = ev

    ordered = sorted(
        best.values(),
        key=lambda e: (e.topic, e.object_name, _aspect_rank(e.aspect), -e.score),
    )

    source_ids: dict[tuple, str] = {}
    out: list[Evidence] = []
    for i, ev in enumerate(ordered, start=1):
        skey = (ev.source.kind, ev.source.url or ev.source.doc_id)
        if skey not in source_ids:
            source_ids[skey] = f"S{len(source_ids) + 1}"
        source = ev.source.model_copy(update={"source_id": source_ids[skey]})
        out.append(ev.model_copy(update={"evidence_id": f"E{i}", "source": source}))
    return out[:budget]


def make_verify_node(registry: DomainRegistry, settings: Settings):
    def verify(state: dict) -> dict:
        coverage = state.get("coverage", {})
        plan: Plan = state["plan"]
        gaps: list[dict] = []
        seen: set[str] = set()
        for subtask in plan.subtasks:
            if not subtask.object_name:
                continue
            key = f"{subtask.topic}/{subtask.object_name}"
            if key in seen:
                continue
            seen.add(key)
            topic_info = registry.topics.get(subtask.topic)
            expected = topic_info.aspects if topic_info else []
            covered = set(coverage.get(key, []))
            missing = [a for a in expected if a not in covered]
            if missing:
                missing_zh = "、".join(ASPECT_ZH.get(a, a) for a in missing)
                display = registry.display_object(subtask.object_name)
                gaps.append({
                    "topic": subtask.topic,
                    "object": subtask.object_name,
                    "missing_aspects": missing,
                    "text": f"{display} 缺少维度证据：{missing_zh}",
                })

        failures = state.get("route_failures", [])
        for f in failures:
            gaps.append({"topic": "", "object": "", "missing_aspects": [],
                         "text": f"{f['route']} 路检索失败（已降级）：{f['query']} — {f['error']}"})

        replan_focus = [
            {"topic": g["topic"], "object": g["object"], "missing_aspects": g["missing_aspects"]}
            for g in gaps
            if g["missing_aspects"]
        ]
        return {"information_gaps": [g["text"] for g in gaps],
                "replan_focus": replan_focus,
                "step_count": state.get("step_count", 0) + 1}

    return verify


def route_after_verify(state: dict, settings: Settings) -> str:
    """缺口 -> replan；轮数/步数上限或无可补缺口 -> 收敛到合成。"""
    replan_focus = state.get("replan_focus", [])
    if not replan_focus:
        return "synthesize"
    guardrails = Guardrails(max_steps=settings.max_steps, max_plan_rounds=settings.max_plan_rounds)
    if not guardrails.can_replan(plan_rounds=state.get("plan_rounds", 0),
                                 step_count=state.get("step_count", 0)):
        return "synthesize"
    return "plan"


def make_synthesize_node(provider: LLMProvider, settings: Settings):
    def synthesize(state: dict) -> dict:
        evidences = list(state.get("evidences", []))
        run_id = state.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"
        ctx = ComposeContext(
            question=state["question"],
            evidences=evidences,
            information_gaps=state.get("information_gaps", []),
            preferences=state.get("preferences", {}),
        )
        result = provider.compose(ctx)
        plan: Plan = state["plan"]
        routes = sorted({e.route for e in evidences}) or sorted({s.route for s in plan.subtasks})
        report = Report(
            report_id=run_id,
            question=state["question"],
            trace_id=run_id,
            markdown=result.markdown,
            summary=result.summary,
            sources=result.sources or collect_sources(evidences),
            citations=result.citations,
            evidences=evidences,
            meta=ReportMeta(
                routes_used=routes,
                degraded=bool(state.get("route_failures")),
                plan_rounds=state.get("plan_rounds", 0),
                step_count=state.get("step_count", 0),
                token_usage=0,  # M3 随 Trace 落账
                information_gaps=state.get("information_gaps", []),
            ),
        )
        return {"report": report, "run_id": run_id,
                "step_count": state.get("step_count", 0) + 1}

    return synthesize


def make_persist_node(memory: "MemoryStore"):
    """记忆沉淀：报告落定后写入长期记忆（只沉淀有证据支撑的结论）。"""

    def persist(state: dict) -> dict:
        report = state.get("report")
        if report is not None:
            memory.record_research(state.get("user_id", "default"), state["question"], report)
        return {"step_count": state.get("step_count", 0) + 1}

    return persist
