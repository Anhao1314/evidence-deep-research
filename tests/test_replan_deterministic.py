"""确定性 replan：首轮缺口 -> verify -> 第二轮补充检索 -> 收敛/上限停止。

只证明离线 replan 行为与 Trace，不代表在线 replan 质量。
"""

from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.domain_registry import DomainRegistry
from app.graph.factory import build_research_app
from app.llm.heuristic import HeuristicProvider
from app.memory.store import MemoryStore
from app.trace.store import TraceStore
from knowledge_base.corpus import load_corpus
from knowledge_base.retriever import ScoredDoc


class _ScriptedKB:
    """首轮只返回 performance/cost；查询包含“生态/成熟度”时补对应文档。"""

    def __init__(self, docs):
        self._by_aspect = {}
        for doc in docs:
            if doc.object_name == "atlas":
                self._by_aspect.setdefault(doc.aspect, []).append(doc)

    def search(self, query, top_k=4, *, topic=None):
        docs = list(self._by_aspect.get("performance", [])) + list(
            self._by_aspect.get("cost", [])
        )
        if "生态" in query or "ecosystem" in query:
            docs += self._by_aspect.get("ecosystem", [])
        if "成熟度" in query or "maturity" in query:
            docs += self._by_aspect.get("maturity", [])
        return [ScoredDoc(doc=d, score=1.0) for d in docs[:top_k]]


class _AlwaysPartialKB(_ScriptedKB):
    """永远只返回 performance/cost，用来验证轮数上限与未闭合缺口。"""

    def search(self, query, top_k=4, *, topic=None):
        docs = list(self._by_aspect.get("performance", [])) + list(
            self._by_aspect.get("cost", [])
        )
        return [ScoredDoc(doc=d, score=1.0) for d in docs[:top_k]]


class _EmptyWeb:
    def search(self, query, top_k=4, *, topic=None):
        return []


def _run(tmp_path: Path, kb_client, max_plan_rounds: int = 2):
    fixture = PROJECT_ROOT / "data" / "fixtures" / "constraint_selection" / "corpus.jsonl"
    docs = load_corpus(fixture.parent)
    settings = Settings(
        mode="offline", tool_retry_base_delay=0.0, mcp_kb_enabled=False,
        max_plan_rounds=max_plan_rounds, max_steps=24,
        memory_db_path=tmp_path / "m.sqlite3", trace_db_path=tmp_path / "t.sqlite3",
    )
    registry = DomainRegistry.from_kb_docs(docs)
    trace = TraceStore(settings.trace_db_path)
    memory = MemoryStore(settings.memory_db_path)
    graph = build_research_app(
        settings=settings,
        kb_client=kb_client,
        search_provider=_EmptyWeb(),
        llm_provider=HeuristicProvider(registry, max_subtasks=settings.max_subtasks),
        registry=registry,
        memory_store=memory,
        trace_store=trace,
    )
    run_id = "replan-test"
    final = graph.invoke(
        {"question": "Atlas 向量数据库的性能、生态、成本和成熟度怎么样？",
         "user_id": "replan", "run_id": run_id},
        config={"recursion_limit": 128},
    )
    events = trace.get(run_id)
    trace.close()
    memory.close()
    return final, events


def test_replan_closes_gap_with_second_round(tmp_path):
    fixture = PROJECT_ROOT / "data" / "fixtures" / "constraint_selection" / "corpus.jsonl"
    docs = load_corpus(fixture.parent)
    final, events = _run(tmp_path, _ScriptedKB(docs))
    report = final["report"]

    assert report.meta.plan_rounds == 2
    plan_events = [e for e in events if e.event_type == "plan"]
    dispatch_events = [e for e in events if e.event_type == "dispatch"]
    verify_events = [e for e in events if e.event_type == "verify"]
    assert len(plan_events) == 2
    assert len(dispatch_events) == 2
    assert any(e.payload["round"] == 2 for e in plan_events)
    second_subtasks = plan_events[1].payload["subtasks"]
    assert {s["aspect"] for s in second_subtasks} == {"ecosystem", "maturity"}
    assert any("生态" in e.payload.get("query", "") for e in events
               if e.event_type == "tool_call")
    assert any("成熟度" in e.payload.get("query", "") for e in events
               if e.event_type == "tool_call")
    assert verify_events[0].payload["replan"] is True
    assert verify_events[0].payload["gaps"]
    assert verify_events[-1].payload["gaps"] == []
    assert report.meta.information_gaps == []
    assert report.meta.routes_used == ["kb"]


def test_replan_stops_at_round_limit_with_open_gaps(tmp_path):
    fixture = PROJECT_ROOT / "data" / "fixtures" / "constraint_selection" / "corpus.jsonl"
    docs = load_corpus(fixture.parent)
    final, events = _run(tmp_path, _AlwaysPartialKB(docs), max_plan_rounds=2)
    report = final["report"]
    plan_events = [e for e in events if e.event_type == "plan"]
    verify_events = [e for e in events if e.event_type == "verify"]

    assert report.meta.plan_rounds == 2
    assert len(plan_events) == 2
    assert verify_events[-1].payload["gaps"], "达到轮数上限后必须留下未闭合缺口"
    assert report.meta.information_gaps, "未闭合缺口必须进入报告"
    assert any("缺少维度证据" in g for g in report.meta.information_gaps)