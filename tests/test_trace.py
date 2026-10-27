"""全链路 Trace：事件类型、顺序、payload 结构、失败降级事件。"""

from app.graph.factory import build_research_app
from app.llm.heuristic import HeuristicProvider
from app.memory.store import MemoryStore
from app.trace.store import TraceStore
from knowledge_base.retriever import KBRetriever


class _EmptySearchProvider:
    def search(self, query, top_k=4, *, topic=None):
        return []


class _ExplodingKBClient:
    def search(self, query, top_k=4, *, topic=None):
        raise ConnectionError("MCP 连接中断")


def test_full_run_emits_ordered_trace_events(research_app, settings):
    run_id = "run-trace-1"
    research_app.invoke(
        {"question": "RAG 向量库选 Qdrant 还是 Milvus？", "user_id": "t", "run_id": run_id},
        config={"recursion_limit": 64},
    )
    store = TraceStore(settings.trace_db_path)
    events = store.get(run_id)
    store.close()
    assert events, "trace 应有事件"

    types = [e.event_type for e in events]
    for expected in ("memory_recall", "plan", "dispatch", "tool_call", "tool_result",
                     "merge", "verify", "synthesize", "persist"):
        assert expected in types, f"缺少事件 {expected}"

    def _first(t: str) -> int:
        return types.index(t)

    # 主链路顺序
    assert (_first("memory_recall") < _first("plan") < _first("dispatch")
            < _first("merge") < _first("verify") < _first("synthesize") < _first("persist"))

    # plan 事件带子任务明细
    plan_ev = next(e for e in events if e.event_type == "plan")
    assert plan_ev.payload["plan_id"] and plan_ev.payload["subtasks"]

    # 双路工具调用与结果（含 latency）
    calls = [e for e in events if e.event_type == "tool_call"]
    assert {e.payload["route"] for e in calls} == {"kb", "web"}
    results = [e for e in events if e.event_type == "tool_result"]
    assert results and all("latency_ms" in e.payload for e in results)
    assert all(e.payload["hits"] > 0 for e in results)


def test_tool_failure_emits_retry_and_degrade(settings, tmp_path):
    """工具抛异常 -> 重试耗尽 -> degrade 事件 + route_failures -> verify 缺口。"""
    settings = settings.model_copy(update={"max_plan_rounds": 1})
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    from app.domain_registry import DomainRegistry

    registry = DomainRegistry.from_kb_docs(retriever.docs)
    trace_store = TraceStore(tmp_path / "fail-trace.sqlite3")
    app = build_research_app(
        settings=settings,
        kb_client=_ExplodingKBClient(),
        search_provider=_EmptySearchProvider(),
        llm_provider=HeuristicProvider(registry, max_subtasks=settings.max_subtasks),
        registry=registry,
        memory_store=MemoryStore(tmp_path / "fail-memory.sqlite3"),
        trace_store=trace_store,
    )
    run_id = "run-fail-1"
    app.invoke(
        {"question": "Qdrant 和 Milvus 哪个好？", "user_id": "t", "run_id": run_id},
        config={"recursion_limit": 128},
    )
    events = trace_store.get(run_id)
    trace_store.close()
    types = [e.event_type for e in events]
    assert "retry" in types, "工具异常应产生重试事件"
    degrade_events = [e for e in events if e.event_type == "degrade"]
    assert degrade_events and all(e.payload["route"] == "kb" for e in degrade_events)
    verify_ev = next(e for e in events if e.event_type == "verify")
    assert verify_ev.payload["gaps"], "检索失败应转化为信息缺口"
    # degrade 不阻塞主图：报告仍然产出（synthesize/persist 存在）
    assert "synthesize" in types and "persist" in types
