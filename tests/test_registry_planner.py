"""领域注册表与离线 Planner：问题 -> 子任务的规则行为。"""

from app.llm.base import PlanContext
from app.llm.heuristic import HeuristicProvider


def test_match_by_object_names(registry):
    match = registry.match_question("RAG 系统的向量库选 Qdrant 还是 Milvus？")
    assert match is not None and match.topic == "vector-db"
    assert match.matched_objects == ["qdrant", "milvus"]  # 按出现顺序


def test_match_by_chinese_hint(registry):
    match = registry.match_question("向量数据库该怎么选？")
    assert match is not None and match.topic == "vector-db"
    assert match.matched_objects == []


def test_match_out_of_domain_returns_none(registry):
    assert registry.match_question("今晚吃什么比较好？") is None


def test_plan_comparison_question(settings, registry):
    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    plan = provider.plan(PlanContext(question="RAG 向量库选 Qdrant 还是 Milvus？"))
    routes = {s.route for s in plan.subtasks}
    assert routes == {"kb", "web"}  # 双路并行
    pairs = {(s.object_name, s.route) for s in plan.subtasks}
    assert ("qdrant", "kb") in pairs and ("milvus", "web") in pairs
    assert plan.subtasks[0].object_display  # 有显示名


def test_plan_out_of_domain_generic_fallback(settings, registry):
    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    plan = provider.plan(PlanContext(question="怎么评估开源项目的健康度？"))
    assert {s.route for s in plan.subtasks} == {"kb", "web"}
    assert all(s.object_name == "" for s in plan.subtasks)


def test_plan_respects_max_subtasks(registry):
    provider = HeuristicProvider(registry, max_subtasks=3)
    plan = provider.plan(PlanContext(question="Qdrant 与 Milvus 怎么选？"))
    assert len(plan.subtasks) <= 3


def test_replan_focuses_on_missing_aspects(settings, registry):
    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    plan = provider.plan(
        PlanContext(
            question="Qdrant 和 Milvus 哪个更适合？",
            replan_focus=[
                {"topic": "vector-db", "object": "qdrant", "missing_aspects": ["maturity"]},
            ],
        )
    )
    aspects = {s.aspect for s in plan.subtasks}
    assert aspects == {"maturity"}
    assert all("成熟度" in s.query for s in plan.subtasks)


def test_plan_signature_detects_duplicates(registry, settings):
    from app.guardrails import is_duplicate_plan

    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    ctx = PlanContext(question="Qdrant 还是 Milvus？")
    plan1 = provider.plan(ctx)
    plan2 = provider.plan(ctx)  # 规则模式下同一问题产出相同子任务集
    assert plan1.signature() == plan2.signature()
    assert not is_duplicate_plan(plan1, [])
    assert is_duplicate_plan(plan2, [plan1.signature()])
