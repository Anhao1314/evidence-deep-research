"""指标纯函数：比率必须带分母，pre/post guard 分开，无法测量必须是 None。"""

from app.schema.evidence import Evidence, Source
from app.schema.guard import GuardAudit
from app.schema.report import Citation, Report, ReportMeta
from eval.metrics import (
    citation_counts,
    citation_coverage,
    classify_failure_types,
    cost_usd,
    dimension_coverage,
    guard_metrics,
    percentile,
    route_coverage,
    source_resolution,
    summarize_structural,
    token_usage_from_events,
)


def _report(*, markdown="结论 [E1] [E9]", raw_markdown="", sources=None,
            evidences=None, citations=None, guard=None) -> Report:
    return Report(
        report_id="r1", question="q", trace_id="r1", markdown=markdown,
        raw_markdown=raw_markdown, summary="s", sources=sources or [],
        citations=citations or [Citation(location="line#1", evidence_ids=["E1"])],
        evidences=evidences or [], guard_audit=guard,
        meta=ReportMeta(routes_used=["kb", "web"], plan_rounds=1, step_count=4),
    )


def _source(sid="S1", kind="web", url="https://x", doc_id=None, h="h", resolution="retrieval_snapshot"):
    return Source(source_id=sid, kind=kind, title="t", url=url, doc_id=doc_id,
                  content_hash=h, resolution=resolution)


def test_citation_counts_distinguishes_total_and_invalid():
    stats = citation_counts("a [E1] b [E9] c [E1]", {"E1"})
    assert stats["total"] == 3
    assert stats["valid"] == 2
    assert stats["invalid"] == 1
    assert stats["validity_rate"] == 2 / 3
    assert stats["invalid_ids"] == ["E9"]


def test_citation_counts_without_citations_is_not_measured():
    stats = citation_counts("no citations", {"E1"})
    assert stats["total"] == 0
    assert stats["validity_rate"] is None


def test_source_resolution_flags_unresolved_and_orphans():
    src_ok = _source("S1")
    src_no_hash = _source("S2", kind="kb", url=None, doc_id="d2", h="", resolution="unresolved")
    src_orphan = _source("S9", kind="kb", url=None, doc_id="d9")
    ev_ok = Evidence(evidence_id="E1", source=src_ok, claim="c", quote="q",
                     topic="t", aspect="performance", route="web")
    ev_orphan = Evidence(evidence_id="E2", source=src_orphan, claim="c", quote="q",
                         topic="t", aspect="cost", route="kb")
    report = _report(sources=[src_ok, src_no_hash], evidences=[ev_ok, ev_orphan])
    stats = source_resolution(report)
    assert stats["evidence_total"] == 2
    assert stats["evidence_resolved"] == 1
    assert stats["source_reference_integrity_rate"] == 0.5
    assert stats["source_resolved"] == 1
    assert stats["source_unresolved_ids"] == ["S2"]
    assert stats["source_missing_content_hash"] == ["S2"]
    assert stats["orphan_evidence_sources"] == ["S9"]
    assert stats["url_reachable"]["status"] == "NOT_MEASURED"


def test_guard_metrics_separate_pre_and_post():
    guard = GuardAudit(
        ran=True, raw_markdown="raw [E1] [E99]", final_markdown="final [E1]",
        rewrite_attempted=True, rewrite_accepted=False, claims_checked=2,
        claims_quarantined=1, fallback_used=True, fail_closed=True,
    )
    ev = Evidence(evidence_id="E1", source=_source(), claim="c", quote="q",
                  topic="t", aspect="performance", route="web")
    report = _report(markdown="final [E1]", raw_markdown="raw [E1] [E99]",
                     evidences=[ev], guard=guard)
    metrics = guard_metrics(report)
    assert metrics["pre_guard_citation_total"] == 2
    assert metrics["pre_guard_citation_valid"] == 1
    assert metrics["pre_guard_citation_validity_rate"] == 0.5
    assert metrics["post_guard_citation_total"] == 1
    assert metrics["post_guard_citation_valid"] == 1
    assert metrics["post_guard_citation_integrity_rate"] == 1.0
    assert metrics["guard_rewrite_attempted"] is True
    assert metrics["guard_claims_quarantined"] == 1
    assert metrics["guard_fail_closed"] is True


def test_citation_coverage_and_dimension_coverage():
    src = _source()
    ev1 = Evidence(evidence_id="E1", source=src, claim="c", quote="q",
                   topic="t", object_name="qdrant", aspect="performance", route="web")
    ev2 = Evidence(evidence_id="E2", source=src, claim="c", quote="q",
                   topic="t", object_name="qdrant", aspect="cost", route="web")
    report = _report(sources=[src], evidences=[ev1, ev2])
    assert citation_coverage(report) == 0.5
    assert dimension_coverage(["performance", "cost", "maturity"], ["Qdrant"], [ev1, ev2]) == 2 / 3
    assert dimension_coverage([], [], [ev1]) is None


def test_route_coverage_any_is_not_measured():
    assert route_coverage(["kb", "web"], ["kb"]) == 0.5
    assert route_coverage(["kb"], ["kb", "web"]) == 1.0
    assert route_coverage([], ["kb"]) is None


def test_percentile_nearest_rank():
    values = [5.0, 1.0, 9.0, 3.0]
    assert percentile(values, 50) == 3.0
    assert percentile(values, 95) == 9.0
    assert percentile([], 50) is None


def test_cost_is_none_without_prices():
    assert cost_usd(1000, 500, None, 2.0) is None
    assert cost_usd(1_000_000, 1_000_000, 1.0, 2.0) == 3.0


def test_token_usage_handles_old_and_new_events():
    events = [
        {"event_type": "usage", "token_usage": 10, "payload": {"prompt_tokens": 6,
                                                              "completion_tokens": 4}},
        {"event_type": "usage", "token_usage": 5, "payload": {}},
        {"event_type": "plan", "token_usage": 99, "payload": {}},
    ]
    usage = token_usage_from_events(events)
    assert usage == {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 15,
                     "events": 2}


def test_classify_failure_types():
    no_report = classify_failure_types(
        error="boom", has_report=False, evidence_count=0, expected_routes=["kb"],
        routes_used=[], post_guard_invalid_citations=0, source_rate=None,
        information_gaps=[], must_state_gaps=False, expected_objects=[],
        report_markdown="", trace_types=set(),
    )
    assert no_report == ["no_report", "pipeline_exception"]

    guard = GuardAudit(
        ran=True, rewrite_attempted=True, rewrite_accepted=False,
        claims_checked=3, claims_quarantined=1, fallback_used=True, fail_closed=True,
    )
    types = classify_failure_types(
        error=None, has_report=True, evidence_count=0, expected_routes=["kb", "web"],
        routes_used=["kb"], post_guard_invalid_citations=2, source_rate=0.5,
        information_gaps=[], must_state_gaps=True, expected_objects=["Qdrant"],
        report_markdown="无", trace_types={"degrade", "retry"}, guard_audit=guard,
        assertions=[{"name": "min_evidences", "passed": False}],
    )
    assert {
        "no_evidence", "route_not_covered", "post_guard_invalid_citation",
        "source_unresolved", "gaps_not_stated", "missing_object",
        "tool_or_llm_degraded", "tool_retry", "guard_rewrite_rejected",
        "guard_claim_quarantined", "guard_fallback", "guard_fail_closed",
        "structural_check_failed",
    } <= set(types)


def test_summarize_structural_keeps_denominators_and_separates_latency():
    records = [
        {
            "has_report": True, "all_checks_passed": True,
            "pre_guard_citation_total": 10, "pre_guard_citation_valid": 9,
            "pre_guard_citation_invalid": 1, "pre_guard_citation_validity_rate": 0.9,
            "post_guard_citation_total": 9, "post_guard_citation_valid": 9,
            "post_guard_citation_invalid": 0, "post_guard_citation_integrity_rate": 1.0,
            "guard_ran": True, "guard_rewrite_attempted": True,
            "guard_claims_checked": 5, "guard_claims_quarantined": 1,
            "guard_fallback_used": False, "guard_fail_closed": False,
            "citation_coverage": 0.8, "route_coverage": 1.0, "dimension_coverage": 0.5,
            "latency_ms": 100.0, "failure_types": [],
            "source_resolution": {
                "evidence_total": 10, "evidence_resolved": 9,
                "source_total": 5, "source_resolved": 4,
                "source_missing_content_hash": ["S5"],
                "source_missing_locator": ["S2"],
                "orphan_evidence_sources": [],
            },
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5,
                            "total_tokens": 15, "events": 2},
            "cost_usd": None,
        },
        {
            "has_report": False, "all_checks_passed": False,
            "pre_guard_citation_total": 0, "pre_guard_citation_valid": 0,
            "pre_guard_citation_invalid": 0, "post_guard_citation_total": 0,
            "post_guard_citation_valid": 0, "post_guard_citation_invalid": 0,
            "guard_ran": False, "guard_rewrite_attempted": False,
            "guard_claims_checked": 0, "guard_claims_quarantined": 0,
            "guard_fallback_used": False, "guard_fail_closed": False,
            "citation_coverage": None, "route_coverage": None, "dimension_coverage": None,
            "latency_ms": 200.0, "failure_types": ["no_report"],
            "source_resolution": {}, "token_usage": {}, "cost_usd": None,
        },
    ]
    out = summarize_structural(records)
    assert out["pipeline_completion_rate"]["value"] == 0.5
    assert out["pipeline_completion_rate"]["numerator"] == 1
    assert out["pre_guard_citation_valid_rate"]["value"] == 0.9
    assert out["pre_guard_citation_valid_rate"]["denominator"] == 10
    assert out["post_guard_citation_integrity_rate"]["value"] == 1.0
    assert out["guard_rewrite_rate"]["value"] == 1.0
    assert out["guard_claim_quarantine_rate"]["value"] == 0.2
    assert out["source_reference_integrity_rate"]["value"] == 0.9
    assert out["source_resolution"]["missing_content_hash"] == 1
    assert out["latency"]["n"] == 1 and out["latency"]["p50_ms"] == 100.0
    assert out["latency_all_cases"]["n"] == 2 and out["latency_all_cases"]["p95_ms"] == 200.0
    assert out["cost"]["status"] == "NOT_MEASURED"
    assert out["failure_type_distribution"]["counts"] == {"no_report": 1}
    assert out["counts"]["all_checks_passed"] == 1
    assert "task_completion_rate" not in out
    assert "citation_id_validity_rate" not in out
    assert "source_traceability_rate" not in out