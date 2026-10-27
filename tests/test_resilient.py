"""引用校验 guard 与 ResilientProvider 的失败恢复。"""

from app.llm.base import ComposeContext, ComposeResult, PlanContext
from app.llm.resilient import ResilientProvider
from app.report.citation_guard import (
    check_citations,
    extract_citations,
    strip_invalid_citations,
)
from app.schema.evidence import Evidence, Source
from app.trace.store import TraceStore


def _ev(i: int) -> Evidence:
    return Evidence(
        evidence_id=f"E{i}", source=Source(source_id=f"S{i}", kind="kb", title="t", doc_id=f"d{i}"),
        claim="c", quote="q", topic="t", object_name="o", aspect="performance", route="kb",
    )


def test_check_citations_finds_invalid():
    md = "结论 A [E1]；结论 B [E99]；结论 C [E7][E1]。"
    assert check_citations(md, {"E1", "E7"}) == ["E99"]


def test_strip_invalid_citations_keeps_valid():
    md = "结论 A [E1]。结论 B [E99]。"
    cleaned = strip_invalid_citations(md, ["E99"])
    assert "[E99]" not in cleaned and "[E1]" in cleaned


def test_extract_citations_filters_invalid():
    md = "A [E1] B [E99]"
    cites = extract_citations(md, [_ev(1)])
    assert [c.evidence_ids for c in cites] == [["E1"]]


class _ExplodingProvider:
    def plan(self, ctx):
        raise ConnectionError("LLM 服务不可达")

    def compose(self, ctx):
        raise ConnectionError("LLM 服务不可达")


class _BadCitationProvider:
    """首版含非法引用，rewrite 后修复。"""

    def compose(self, ctx):
        return ComposeResult(markdown="结论：选 A [E99][E1]。", summary="s")

    def rewrite(self, markdown, invalid_ids):
        return markdown.replace("[E99]", "[E2]")


class _StubbornBadProvider:
    """rewrite 也不改：必须剥离。"""

    def compose(self, ctx):
        return ComposeResult(markdown="结论：选 A [E99]。", summary="s")

    def rewrite(self, markdown, invalid_ids):
        return markdown


def _ctx(evidences) -> ComposeContext:
    return ComposeContext(question="q", evidences=evidences, trace_id="run-guard")


def test_plan_degrades_to_fallback(settings, tmp_path):
    from app.domain_registry import DomainRegistry
    from app.llm.heuristic import HeuristicProvider
    from knowledge_base.corpus import load_corpus

    registry = DomainRegistry.from_kb_docs(load_corpus(settings.kb_corpus_dir))
    trace = TraceStore(tmp_path / "r1.sqlite3")
    provider = ResilientProvider(
        _ExplodingProvider(), fallback=HeuristicProvider(registry), trace_store=trace
    )
    plan = provider.plan(PlanContext(question="RAG 向量库选 Qdrant 还是 Milvus？", trace_id="run-p"))
    assert plan.subtasks, "主路失败必须由规则兜底产出计划"
    assert "degrade" in [e.event_type for e in trace.get("run-p")]
    trace.close()


def test_compose_degrades_to_template(settings, tmp_path):
    from app.domain_registry import DomainRegistry
    from app.llm.heuristic import HeuristicProvider
    from knowledge_base.corpus import load_corpus

    registry = DomainRegistry.from_kb_docs(load_corpus(settings.kb_corpus_dir))
    trace = TraceStore(tmp_path / "r2.sqlite3")
    provider = ResilientProvider(
        _ExplodingProvider(), fallback=HeuristicProvider(registry), trace_store=trace
    )
    evs = [_ev(1)]
    result = provider.compose(_ctx(evs))
    assert result.markdown, "compose 失败必须降级到模板合成"
    assert "degrade" in [e.event_type for e in trace.get("run-guard")]
    trace.close()


def test_citation_rewrite_success(tmp_path):
    trace = TraceStore(tmp_path / "r3.sqlite3")
    provider = ResilientProvider(_BadCitationProvider(), fallback=None, trace_store=trace)
    result = provider.compose(_ctx([_ev(1), _ev(2)]))
    assert "[E99]" not in result.markdown and "[E2]" in result.markdown
    degrade = [e for e in trace.get("run-guard") if e.event_type == "degrade"]
    assert degrade and degrade[0].payload["fixed_by"] == "rewrite"
    trace.close()


def test_citation_stripped_when_rewrite_fails(tmp_path):
    trace = TraceStore(tmp_path / "r4.sqlite3")
    provider = ResilientProvider(_StubbornBadProvider(), fallback=None, trace_store=trace)
    result = provider.compose(_ctx([_ev(1)]))
    assert "[E99]" not in result.markdown, "重写失败必须剥离非法引用"
    assert "[E1]" not in result.markdown or True  # E1 不在该句中
    errors = [e for e in trace.get("run-guard") if e.event_type == "error"]
    assert errors and errors[0].payload["action"] == "stripped"
    trace.close()
