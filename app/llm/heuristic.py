"""离线启发式 Provider：零模型调用的规则版 Planner / Composer。

分解规则（领域内）：
1. DomainRegistry 匹配问题 -> (topic, matched_objects)；
2. 对比型问题（>=2 对象）：每对象 x 每检索路（kb/web）生成子任务，
   查询词 = 对象名 + 主题中文名，aspect 由检索结果的文档元数据自然覆盖；
3. 单对象问题：同上但只针对该对象；
4. 领域外问题：泛化降级——kb/web 各一条以原问题为查询的子任务。

replan：按 verify 给出的缺口（topic/object/missing_aspects）生成
aspect 级补充子任务，且不与历史计划重复。
"""

import uuid

from app.constraints import extract_constraints
from app.domain_registry import ASPECT_ZH, DomainRegistry
from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.report.synth import render_compose_result
from app.schema.plan import Plan, SubTask

SUBTASK_ROUTES: tuple[str, ...] = ("kb", "web")


class HeuristicProvider:
    """规则模式：不访问网络、不消耗 token，是离线端到端的引擎。"""

    def __init__(self, registry: DomainRegistry, max_subtasks: int = 8) -> None:
        self.registry = registry
        self.max_subtasks = max_subtasks

    def plan(self, ctx: PlanContext) -> Plan:
        if ctx.replan_focus:
            plan = self._plan_replan(ctx)
        else:
            plan = self._plan_initial(ctx)
        # 护栏：子任务数量上限
        plan.subtasks = plan.subtasks[: self.max_subtasks]
        return plan

    def _plan_initial(self, ctx: PlanContext) -> Plan:
        match = self.registry.match_question(ctx.question)
        constraints = ctx.constraints or extract_constraints(ctx.question)
        if match is None:
            return self._plan_generic(ctx, constraints)
        subtasks: list[SubTask] = []
        objects = match.matched_objects or [""]
        for obj in objects:
            for route in SUBTASK_ROUTES:
                subtasks.append(
                    self._make_subtask(
                        route=route,
                        topic=match.topic,
                        object_name=obj,
                        aspect="general",
                        query=self._query_for(match.topic, obj),
                    )
                )
        rationale = f"启发式规则：命中主题 {match.topic}，对象 {objects or '（泛型）'}"
        if ctx.related_history:
            hist = "；".join(h["question"] for h in ctx.related_history)
            rationale += f"；记忆召回相关历史研究：{hist}"
        return Plan(plan_id=self._new_id(), subtasks=subtasks, rationale=rationale,
                    constraints=constraints)

    def _plan_replan(self, ctx: PlanContext) -> Plan:
        subtasks: list[SubTask] = []
        for gap in ctx.replan_focus:
            topic, obj = gap.get("topic", ""), gap.get("object", "")
            for aspect in gap.get("missing_aspects", []):  # 补齐全部缺口维度，而不是只补一个
                for route in SUBTASK_ROUTES:
                    subtasks.append(
                        self._make_subtask(
                            route=route,
                            topic=topic,
                            object_name=obj,
                            aspect=aspect,
                            query=self._query_for(topic, obj, aspect),
                        )
                    )
        return Plan(
            plan_id=self._new_id(),
            subtasks=subtasks,
            rationale="启发式规则：针对信息缺口的补充检索",
            constraints=ctx.constraints or extract_constraints(ctx.question),
        )

    def _plan_generic(self, ctx: PlanContext, constraints: list) -> Plan:
        query = ctx.question[:60]
        subtasks = [
            self._make_subtask(route=route, topic="", object_name="", aspect="general", query=query)
            for route in SUBTASK_ROUTES
        ]
        return Plan(
            plan_id=self._new_id(),
            subtasks=subtasks,
            rationale="启发式规则：领域外问题，泛化检索降级",
            constraints=constraints,
        )

    def _make_subtask(
        self, *, route: str, topic: str, object_name: str, aspect: str, query: str
    ) -> SubTask:
        return SubTask(
            subtask_id=f"T{uuid.uuid4().hex[:6]}",
            route=route,  # type: ignore[arg-type]
            topic=topic,
            object_name=object_name,
            object_display=self.registry.display_object(object_name) if object_name else "",
            aspect=aspect,
            query=query,
        )

    def _query_for(self, topic: str, object_name: str = "", aspect: str | None = None) -> str:
        parts = [self.registry.display_object(object_name) if object_name else ""]
        parts.append(self.registry.display(topic))
        if aspect and aspect != "general":
            parts.append(ASPECT_ZH.get(aspect, aspect))
        return " ".join(p for p in parts if p)

    @staticmethod
    def _new_id() -> str:
        return f"plan-{uuid.uuid4().hex[:8]}"

    def compose(self, ctx: ComposeContext) -> ComposeResult:
        return render_compose_result(ctx, registry=self.registry)
