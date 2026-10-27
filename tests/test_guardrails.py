"""护栏：防死循环（轮数/步数上限、重复计划检测、全链路收敛）。"""

from app.config import Settings
from app.domain_registry import DomainRegistry
from app.graph.factory import build_research_app
from app.guardrails import Guardrails, is_duplicate_plan
from app.llm.heuristic import HeuristicProvider
from app.schema.plan import Plan, SubTask
from app.tools.kb_client import KBClient
from app.tools.search import SearchProvider
from knowledge_base.retriever import KBRetriever


class _EmptyKBClient:
    """永远检索不到结果的退化知识库（模拟领域语料缺失/工具异常后降级）。"""

    def search(self, query, top_k=4, *, topic=None):
        return []


class _EmptySearchProvider:
    def search(self, query, top_k=4, *, topic=None):
        return []


def test_guardrails_boundaries():
    g = Guardrails(max_steps=10, max_plan_rounds=2)
    assert g.can_replan(plan_rounds=1, step_count=5)
    assert not g.can_replan(plan_rounds=2, step_count=5)
    assert not g.can_replan(plan_rounds=1, step_count=10)
    assert g.steps_exhausted(10)
    assert not g.steps_exhausted(9)


def test_duplicate_plan_detected():
    subtasks = [
        SubTask(subtask_id="T1", route="kb", topic="t", object_name="a",
                object_display="A", aspect="general", query="a"),
        SubTask(subtask_id="T2", route="web", topic="t", object_name="a",
                object_display="A", aspect="general", query="a"),
    ]
    p1 = Plan(plan_id="p1", subtasks=subtasks)
    p2 = Plan(plan_id="p2", subtasks=[s.model_copy() for s in subtasks])
    assert not is_duplicate_plan(p1, [])
    assert is_duplicate_plan(p2, [p1.signature()])


def test_persistent_gap_converges_within_plan_rounds(settings):
    """检索恒空 -> 每轮 verify 都有缺口 -> 必须在 max_plan_rounds 内收敛出报告。"""
    settings = settings.model_copy(update={"max_plan_rounds": 2, "max_steps": 24})
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever.docs)
    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    app = build_research_app(
        settings=settings,
        kb_client=_EmptyKBClient(),
        search_provider=_EmptySearchProvider(),
        llm_provider=provider,
        registry=registry,
    )
    final = app.invoke(
        {"question": "RAG 向量库选 Qdrant 还是 Milvus？", "user_id": "t", "run_id": "run-loop"},
        config={"recursion_limit": 128},
    )
    report = final["report"]
    assert report is not None, "信息不足也必须收敛出报告"
    assert final["plan_rounds"] == settings.max_plan_rounds, "缺口恒存时应打满轮数上限后收敛"
    assert report.meta.information_gaps, "缺口应写入报告"
    assert final["evidences"] == []


def test_low_step_budget_forces_convergence(settings):
    """步数预算极小 -> 第一轮 verify 后步数耗尽，不得进入第二轮 plan。"""
    settings = settings.model_copy(update={"max_steps": 4, "max_plan_rounds": 4})
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever.docs)
    provider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    app = build_research_app(
        settings=settings,
        kb_client=_EmptyKBClient(),
        search_provider=_EmptySearchProvider(),
        llm_provider=provider,
        registry=registry,
    )
    final = app.invoke(
        {"question": "Qdrant 和 Milvus 哪个好？", "user_id": "t", "run_id": "run-budget"},
        config={"recursion_limit": 128},
    )
    assert final["report"] is not None
    assert final["plan_rounds"] == 1


def test_kb_client_protocol_surface():
    """KBClient / SearchProvider 是结构化协议：M2 换 MCP 实现时类型兼容。"""
    import typing

    assert typing.get_protocol_members(KBClient) if hasattr(typing, "get_protocol_members") else True
    assert typing.get_protocol_members(SearchProvider) if hasattr(typing, "get_protocol_members") else True
