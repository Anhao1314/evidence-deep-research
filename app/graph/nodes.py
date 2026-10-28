"""主图节点工厂：每个节点是闭包，捕获自身依赖（provider / registry / settings）。

节点的返回值只包含它负责更新的 state key；步数计数由每个节点自增，
条件边与护栏（Guardrails）共享同一判断，保证 README 中的状态图与代码一致。
"""

import uuid
from typing import TYPE_CHECKING

from langgraph.types import Send

from app.agents.base import SubAgentState
from app.config import Settings
from app.constraints import evaluate_constraints, extract_constraints
from app.domain_registry import ASPECT_ZH, DomainRegistry
from app.guardrails import Guardrails, is_duplicate_plan
from app.llm.base import ComposeContext, LLMProvider, PlanContext
from app.report.synth import collect_sources
from app.schema.evidence import Evidence, EvidenceDisposition
from app.schema.guard import GuardAction, GuardAudit
from app.schema.plan import Plan
from app.schema.report import Report, ReportMeta

if TYPE_CHECKING:
    from app.memory.store import MemoryStore

# 维度排序：性能 -> 生态 -> 成本 -> 成熟度 -> 其他
_ASPECT_ORDER = list(ASPECT_ZH)


def _aspect_rank(aspect: str) -> int:
    return _ASPECT_ORDER.index(aspect) if aspect in _ASPECT_ORDER else 99


def make_load_context_node(settings: Settings, memory: "MemoryStore", trace_store=None):
    """记忆召回：偏好全量注入 + 历史主题/结论按 BM25 相似度召回（跨会话长期记忆）。"""

    def load_context(state: dict) -> dict:
        recall = memory.recall(state.get("user_id", "default"), state["question"])
        if trace_store is not None:
            trace_store.record(
                state.get("run_id", ""), node="load_context", event_type="memory_recall",
                payload={
                    "preferences": recall.preferences,
                    "related_topics": len(recall.related_topics),
                    "snippets": len(recall.snippets),
                },
            )
        return {
            "preferences": recall.preferences,
            "related_history": [
                {"question": t.question, "summary": t.summary} for t in recall.related_topics
            ],
            "step_count": state.get("step_count", 0) + 1,
        }

    return load_context


def make_plan_node(provider: LLMProvider, settings: Settings, trace_store=None):
    def plan(state: dict) -> dict:
        ctx = PlanContext(
            question=state["question"],
            preferences=state.get("preferences", {}),
            related_history=state.get("related_history", []),
            replan_focus=state.get("replan_focus", []),
            constraints=state.get("constraints", []),
            trace_id=state.get("run_id"),
        )
        plan = provider.plan(ctx)
        if not plan.constraints:
            plan.constraints = extract_constraints(state["question"])
        if not settings.kb_enabled:
            plan.subtasks = [s for s in plan.subtasks if s.route == "web"]
        history = list(state.get("plan_history", []))
        duplicate = is_duplicate_plan(plan, history)
        history.append(plan.signature())
        if trace_store is not None:
            trace_store.record(
                state.get("run_id", ""), node="plan", event_type="plan",
                payload={
                    "plan_id": plan.plan_id,
                    "subtasks": [t.model_dump() for t in plan.subtasks],
                    "rationale": plan.rationale[:200],
                    "is_duplicate": duplicate,
                    "round": state.get("plan_rounds", 0) + 1,
                    "constraints": [c.model_dump() for c in plan.constraints],
                },
            )
        return {
            "plan": plan,
            "plan_history": history,
            "plan_is_duplicate": duplicate,
            "plan_rounds": state.get("plan_rounds", 0) + 1,
            "replan_focus": [],  # 已消费
            "constraints": plan.constraints,
            "step_count": state.get("step_count", 0) + 1,
        }

    return plan


def make_dispatch_router(settings: Settings, trace_store=None):
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
            payload: SubAgentState = {
                "subtask": subtask,
                "question": state["question"],
                "trace_id": state.get("run_id", ""),
                "constraints": state.get("constraints", []),
            }
            sends.append(Send(target, payload))
        if trace_store is not None:
            trace_store.record(
                state.get("run_id", ""), node="dispatch", event_type="dispatch",
                payload={
                    "fan_out": len(sends),
                    "targets": [s.node for s in sends],
                    "subtask_ids": [t.subtask_id for t in plan.subtasks],
                },
            )
        return sends

    return dispatch


def make_merge_node(settings: Settings, trace_store=None):
    def merge(state: dict) -> dict:
        raw = list(state.get("raw_evidences", []))
        kept, excluded = _scope_filter(raw, state.get("plan"))
        deduped, duplicate_excluded = _dedupe_by_source(kept)
        excluded.update(duplicate_excluded)
        ordered = _order_evidences(deduped)
        budgeted = ordered[: settings.evidence_budget]
        for ev in ordered[settings.evidence_budget:]:
            excluded[id(ev)] = "budget_exceeded"
        # 记忆偏好可收紧单对象证据上限（跨会话偏好的第二个生效点）
        per_object = int(state.get("preferences", {}).get("max_evidences_per_object", "0") or 0)
        if per_object > 0:
            capped, cap_excluded = _cap_per_object(budgeted, per_object)
            for ev in cap_excluded:
                excluded[id(ev)] = "per_object_cap"
            budgeted = capped
        evidences = _assign_ids(budgeted)
        disposition = _build_disposition(raw, budgeted, evidences, excluded)
        coverage: dict[str, list[str]] = {}
        for ev in evidences:
            key = f"{ev.topic}/{ev.object_name}"
            aspects = set(coverage.get(key, []))
            aspects.add(ev.aspect)
            coverage[key] = sorted(aspects)
        if trace_store is not None:
            trace_store.record(
                state.get("run_id", ""), node="merge", event_type="merge",
                payload={
                    "raw_evidences": len(raw),
                    "after_scope": len(kept),
                    "deduped": len(deduped),
                    "after_budget_and_prefs": len(evidences),
                    "excluded_by_reason": _count_reasons(excluded, raw),
                    "excluded": [
                        {"source_ref": d.source_ref, "claim": d.claim, "reason": d.reason}
                        for d in disposition if d.status == "excluded"
                    ][:50],
                    "coverage": coverage,
                },
            )
        return {"evidences": evidences, "coverage": coverage,
                "evidence_disposition": disposition,
                "step_count": state.get("step_count", 0) + 1}

    return merge


def _evidence_key(ev: Evidence) -> tuple[str, str, str]:
    return (
        ev.source.kind,
        ev.source.url or ev.source.doc_id or ev.source.title,
        ev.source.locator or "",
    )


def _scope_filter(raw: list[Evidence], plan: Plan | None) -> tuple[list[Evidence], dict[int, str]]:
    """范围门：没有主题/对象的问题不允许把检索到的文档包装成研究依据。"""
    if plan is None:
        return raw, {}
    topics = {t.topic for t in plan.subtasks if t.topic}
    objects = {t.object_name for t in plan.subtasks if t.object_name}
    if not topics and not objects:
        return [], {id(ev): "unscoped_question" for ev in raw}
    kept: list[Evidence] = []
    excluded: dict[int, str] = {}
    for ev in raw:
        if topics and ev.topic and ev.topic not in topics:
            excluded[id(ev)] = "out_of_scope_topic"
            continue
        kept.append(ev)
    return kept, excluded


def _dedupe_by_source(evidences: list[Evidence]) -> tuple[list[Evidence], dict[int, str]]:
    best: dict[tuple, Evidence] = {}
    excluded: dict[int, str] = {}
    for ev in evidences:
        key = _evidence_key(ev)
        if key not in best:
            best[key] = ev
            continue
        if ev.score > best[key].score:
            excluded[id(best[key])] = "duplicate_source"
            best[key] = ev
        else:
            excluded[id(ev)] = "duplicate_source"
    return list(best.values()), excluded


def _order_evidences(evidences: list[Evidence]) -> list[Evidence]:
    return sorted(
        evidences,
        key=lambda e: (e.topic, e.object_name, _aspect_rank(e.aspect), -e.score),
    )


def _cap_per_object(evidences: list[Evidence], per_object: int) -> tuple[list[Evidence], list[Evidence]]:
    counts: dict[str, int] = {}
    kept: list[Evidence] = []
    excluded: list[Evidence] = []
    for ev in evidences:  # 已按相关度排序，超限的丢弃
        key = ev.object_name or ev.topic
        counts[key] = counts.get(key, 0)
        if counts[key] >= per_object:
            excluded.append(ev)
            continue
        counts[key] += 1
        kept.append(ev)
    return kept, excluded


def _assign_ids(evidences: list[Evidence]) -> list[Evidence]:
    """统一分配 S*/E* 编号（来源按实体去重，证据按最终顺序编号）。"""
    source_ids: dict[tuple, str] = {}
    out: list[Evidence] = []
    for i, ev in enumerate(evidences, start=1):
        skey = (ev.source.kind, ev.source.url or ev.source.doc_id)
        if skey not in source_ids:
            source_ids[skey] = f"S{len(source_ids) + 1}"
        source = ev.source.model_copy(update={"source_id": source_ids[skey]})
        out.append(ev.model_copy(update={"evidence_id": f"E{i}", "source": source}))
    return out


def _count_reasons(excluded: dict[int, str], raw: list[Evidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ev in raw:
        reason = excluded.get(id(ev))
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _build_disposition(
    raw: list[Evidence],
    kept: list[Evidence],
    final: list[Evidence],
    excluded: dict[int, str],
) -> list[EvidenceDisposition]:
    kept_by_key = {_evidence_key(ev): ev for ev in kept}
    final_by_key = {_evidence_key(ev): ev for ev in final}
    out: list[EvidenceDisposition] = []
    for ev in raw:
        key = _evidence_key(ev)
        adopted = final_by_key.get(key)
        if adopted is not None and id(ev) in {id(k) for k in kept_by_key.values()} and \
                _evidence_key(adopted) == key:
            # 只有实际进入最终列表的原始对象才算 adopted；重复来源中被丢弃的副本不算
            status, reason = "adopted", ""
        else:
            status, reason = "excluded", excluded.get(id(ev), "not_selected")
        out.append(EvidenceDisposition(
            evidence_key=":".join(key),
            claim=ev.claim,
            source_kind=ev.source.kind,
            source_ref=ev.source.ref,
            source_title=ev.source.title,
            status=status,
            reason=reason,
            evidence_id=adopted.evidence_id if status == "adopted" and adopted else None,
        ))
    return out


def make_verify_node(registry: DomainRegistry, settings: Settings, trace_store=None):
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

        # 没有任何可核实对象：说明本轮只能泛化检索。必须显式声明，
        # 避免“检索到了一些文本”被当成“回答了问题”。
        if plan.subtasks and not any(t.object_name for t in plan.subtasks):
            gaps.append({
                "topic": "", "object": "", "missing_aspects": [],
                "text": (
                    "问题未包含可核实的研究对象，或当前知识域未覆盖该问题；"
                    "本轮为泛化检索，结论仅作线索，需补充对象、约束与时间范围后再判断。"
                ),
            })

        failures = state.get("route_failures", [])
        for f in failures:
            gaps.append({"topic": "", "object": "", "missing_aspects": [],
                         "text": f"{f['route']} 路检索失败（已降级）：{f['query']} — {f['error']}"})

        constraint_summary = evaluate_constraints(
            state.get("constraints", []),
            state.get("evidences", []),
            candidate_objects=[t.object_name for t in plan.subtasks if t.object_name],
        )
        seen_texts = {g["text"] for g in gaps}
        for gap_text in constraint_summary.gaps:
            if gap_text not in seen_texts:
                gaps.append({"topic": "", "object": "", "missing_aspects": [],
                             "text": gap_text})
                seen_texts.add(gap_text)

        replan_focus = [
            {"topic": g["topic"], "object": g["object"], "missing_aspects": g["missing_aspects"]}
            for g in gaps
            if g["missing_aspects"]
        ]
        if trace_store is not None:
            trace_store.record(
                state.get("run_id", ""), node="verify", event_type="verify",
                payload={
                    "gaps": [g["text"] for g in gaps],
                    "replan": bool(replan_focus),
                    "constraint_summary": constraint_summary.model_dump(),
                },
            )
        return {"information_gaps": [g["text"] for g in gaps],
                "replan_focus": replan_focus,
                "constraint_summary": constraint_summary,
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


def make_synthesize_node(provider: LLMProvider, settings: Settings, trace_store=None):
    def synthesize(state: dict) -> dict:
        evidences = list(state.get("evidences", []))
        information_gaps = list(state.get("information_gaps", []))
        if not evidences and not information_gaps:
            information_gaps = ["本轮未获得任何可用证据，无法给出有依据的结论。"]
        run_id = state.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"
        ctx = ComposeContext(
            question=state["question"],
            evidences=evidences,
            information_gaps=information_gaps,
            preferences=state.get("preferences", {}),
            constraints=state.get("constraints", []),
            constraint_summary=state.get("constraint_summary"),
            trace_id=run_id,
        )
        result = provider.compose(ctx)
        plan: Plan = state["plan"]
        routes_used = sorted({e.route for e in evidences})
        routes_attempted = sorted({s.route for s in plan.subtasks})
        raw_markdown = result.raw_markdown or result.markdown
        guard_audit = result.guard_audit or GuardAudit(
            ran=False,
            raw_markdown=raw_markdown,
            final_markdown=result.markdown,
            actions=[GuardAction(action="not_run", detail="离线模板合成，无模型引用需要校验")],
        )
        report = Report(
            report_id=run_id,
            question=state["question"],
            trace_id=run_id,
            markdown=result.markdown,
            raw_markdown=raw_markdown,
            summary=result.summary,
            sources=result.sources or collect_sources(evidences),
            citations=result.citations,
            evidences=evidences,
            evidence_disposition=state.get("evidence_disposition", []),
            constraints=state.get("constraints", []),
            constraint_summary=state.get("constraint_summary"),
            guard_audit=guard_audit,
            unverified_claims=result.unverified_claims,
            meta=ReportMeta(
                routes_used=routes_used,
                routes_attempted=routes_attempted,
                degraded=bool(state.get("route_failures")),
                plan_rounds=state.get("plan_rounds", 0),
                step_count=state.get("step_count", 0),
                token_usage=0,  # 在线模式由 provider 层 usage 事件落 trace
                information_gaps=information_gaps,
            ),
        )
        if trace_store is not None:
            trace_store.record(
                run_id, node="synthesize", event_type="synthesize",
                payload={
                    "markdown_chars": len(result.markdown),
                    "citations": len(result.citations),
                    "evidences": len(evidences),
                    "sources": len(report.sources),
                    "degraded": report.meta.degraded,
                    "guard_ran": guard_audit.ran,
                    "guard_quarantined": guard_audit.claims_quarantined,
                    "guard_fallback": guard_audit.fallback_used,
                    "constraint_verdict": (
                        report.constraint_summary.verdict if report.constraint_summary else None
                    ),
                },
            )
        return {"report": report, "run_id": run_id,
                "step_count": state.get("step_count", 0) + 1}

    return synthesize


def make_persist_node(memory: "MemoryStore", trace_store=None):
    """记忆沉淀：报告落定后写入长期记忆（只沉淀有证据支撑的结论）。"""

    def persist(state: dict) -> dict:
        report = state.get("report")
        if report is not None:
            memory.record_research(state.get("user_id", "default"), state["question"], report)
            if trace_store is not None:
                trace_store.record(
                    state.get("run_id", ""), node="persist", event_type="persist",
                    payload={"topic_recorded": True, "snippets": len(report.evidences)},
                )
        return {"step_count": state.get("step_count", 0) + 1}

    return persist
