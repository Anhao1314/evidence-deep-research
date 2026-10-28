"""评测断言接口：pytest、离线任务集与在线 Runner 共用的行为断言层。

断言全部基于结构化产物（Report / TraceEvent），不解析自然语言，
保证离线可测、确定性可复现。每个断言返回 AssertionResult（含细节），
聚合为 CaseResult / EvalReport——评测框架拿到的是机器可读的结果流。

边界：这里只验证结构与引用合法性。报告是否事实正确、引用是否在语义上
支撑结论，必须由人工标注（见 eval/annotation.py），本模块不提供分数。
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.schema.report import Report
from eval.metrics import (
    citation_counts,
    guard_metrics,
    source_resolution,
)
from eval.metrics import (
    citation_coverage as _citation_coverage,
)
from eval.metrics import (
    dimension_coverage as _dimension_coverage,
)

_CITE_RE = re.compile(r"\[(E\d+)\]")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = Path(__file__).resolve().parent / "cases.jsonl"


class EvalExpect(BaseModel):
    routes: list[str] = Field(default_factory=list)
    min_evidences: int = 0
    citation_coverage_min: float = 0.0
    require_citations: bool = False
    objects_in_report: list[str] = Field(default_factory=list)
    must_converge: bool = True
    max_plan_rounds: int = 2
    max_steps: int = 32
    trace_complete: bool = True
    must_state_gaps: bool = False
    expect_replan_or_gap: bool = False
    expected_dimensions: list[str] = Field(default_factory=list)
    expected_objects: list[str] = Field(default_factory=list)
    min_dimension_coverage: float = 0.0


class EvalCase(BaseModel):
    case_id: str
    category: str
    question: str
    expect: EvalExpect


def load_cases(path: Path = DEFAULT_CASES) -> list[EvalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(EvalCase.model_validate(json.loads(line)))
    return cases


class AssertionResult(BaseModel):
    name: str
    passed: bool
    detail: str


class CaseResult(BaseModel):
    case_id: str
    category: str
    question: str
    passed: bool
    assertions: list[AssertionResult]
    report_id: str | None = None
    plan_rounds: int = 0
    step_count: int = 0
    evidence_count: int = 0
    citation_coverage: float | None = None
    pre_guard_citation_total: int = 0
    pre_guard_citation_valid: int = 0
    pre_guard_citation_invalid: int = 0
    pre_guard_citation_validity_rate: float | None = None
    post_guard_citation_total: int = 0
    post_guard_citation_valid: int = 0
    post_guard_citation_invalid: int = 0
    post_guard_citation_integrity_rate: float | None = None
    guard_ran: bool = False
    guard_rewrite_attempted: bool = False
    guard_rewrite_accepted: bool | None = None
    guard_claims_checked: int = 0
    guard_claims_quarantined: int = 0
    guard_fallback_used: bool = False
    guard_fail_closed: bool = False
    dimension_coverage: float | None = None
    source_reference_integrity_rate: float | None = None
    source_resolution: dict = Field(default_factory=dict)


def citation_coverage(report: Report) -> float:
    """兼容旧接口：无证据时返回 0.0；新指标请直接使用 eval.metrics。"""
    value = _citation_coverage(report)
    return value if value is not None else 0.0


def _claim_fingerprint(text: str, limit: int = 40) -> str:
    cleaned = _CITE_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def run_assertions(case: EvalCase, final_state: dict, trace_events: list) -> list[AssertionResult]:
    """对单次 run 的产物执行全部自动断言。final_state 为 LangGraph 最终 state。"""
    expect = case.expect
    report: Report | None = final_state.get("report")
    evidences = final_state.get("evidences", [])
    out: list[AssertionResult] = []

    out.append(AssertionResult(
        name="must_converge", passed=report is not None,
        detail="报告已产出" if report else "未产出报告",
    ))
    if report is None:
        return out

    routes_used = set(report.meta.routes_used)
    missing_routes = sorted(set(expect.routes) - routes_used)
    out.append(AssertionResult(
        name="routes_used",
        passed=not missing_routes,
        detail=f"实际 {sorted(routes_used)}，期望至少 {expect.routes}"
               + (f"，缺失 {missing_routes}" if missing_routes else ""),
    ))

    out.append(AssertionResult(
        name="min_evidences", passed=len(evidences) >= expect.min_evidences,
        detail=f"实际 {len(evidences)}，下限 {expect.min_evidences}",
    ))

    valid_ids = {e.evidence_id for e in evidences}
    counts = citation_counts(report.markdown, valid_ids)
    out.append(AssertionResult(
        name="no_fabricated_citations", passed=counts["invalid"] == 0,
        detail=f"编造引用: {counts['invalid_ids']}" if counts["invalid"]
               else f"引用 {counts['total']} 处全部可回溯",
    ))

    if expect.require_citations:
        out.append(AssertionResult(
            name="has_citations", passed=counts["total"] > 0,
            detail=f"引用出现 {counts['total']} 次",
        ))

    coverage = citation_coverage(report)
    out.append(AssertionResult(
        name="citation_coverage", passed=coverage >= expect.citation_coverage_min,
        detail=f"结论区域引用覆盖 {coverage:.2f}，下限 {expect.citation_coverage_min}",
    ))

    resolution = source_resolution(report)
    integrity_rate = resolution["source_reference_integrity_rate"]
    source_ok = (
        (resolution["evidence_total"] == 0 and not resolution["orphan_evidence_sources"])
        or (integrity_rate == 1.0 and not resolution["orphan_evidence_sources"])
    )
    out.append(AssertionResult(
        name="source_reference_integrity",
        passed=source_ok,
        detail=(
            f"解析 {resolution['evidence_resolved']}/{resolution['evidence_total']}，"
            f"缺 content_hash {len(resolution['source_missing_content_hash'])}，"
            f"无定位 {len(resolution['source_missing_locator'])}，"
            f"孤立 {resolution['orphan_evidence_sources']}"
        ),
    ))

    missing_objects = [o for o in expect.objects_in_report if o not in report.markdown]
    out.append(AssertionResult(
        name="objects_in_report", passed=not missing_objects,
        detail=f"缺失对象 {missing_objects}" if missing_objects
               else f"对象 {expect.objects_in_report} 全部出现",
    ))

    leaked = []
    for claim in report.unverified_claims:
        fingerprint = _claim_fingerprint(claim.text)
        if fingerprint and fingerprint in report.markdown:
            leaked.append(fingerprint)
    out.append(AssertionResult(
        name="unverified_claims_excluded", passed=not leaked,
        detail=f"未验证结论仍出现在主报告: {leaked}" if leaked
               else f"隔离 {len(report.unverified_claims)} 条结论，主报告无泄漏",
    ))

    if expect.must_state_gaps:
        gaps = report.meta.information_gaps
        out.append(AssertionResult(
            name="states_information_gaps", passed=bool(gaps),
            detail=f"声明 {len(gaps)} 条缺口" if gaps else "未声明任何信息缺口",
        ))

    dim_cov = _dimension_coverage(
        expect.expected_dimensions, expect.expected_objects, report.evidences
    )
    if expect.expected_dimensions:
        passed = dim_cov is not None and dim_cov >= expect.min_dimension_coverage
        out.append(AssertionResult(
            name="dimension_coverage",
            passed=passed,
            detail=(
                f"实际 {dim_cov:.2f}，下限 {expect.min_dimension_coverage}，"
                f"维度 {expect.expected_dimensions}，对象 {expect.expected_objects}"
                if dim_cov is not None else "无可统计维度"
            ),
        ))

    if expect.expect_replan_or_gap:
        replanned = report.meta.plan_rounds >= 2
        has_gaps = bool(report.meta.information_gaps)
        out.append(AssertionResult(
            name="replan_or_gap",
            passed=replanned or has_gaps,
            detail=f"计划轮数 {report.meta.plan_rounds}，缺口 {len(report.meta.information_gaps)} 条",
        ))

    out.append(AssertionResult(
        name="plan_rounds_within_limit",
        passed=report.meta.plan_rounds <= expect.max_plan_rounds,
        detail=f"实际 {report.meta.plan_rounds} 轮，上限 {expect.max_plan_rounds}",
    ))
    out.append(AssertionResult(
        name="steps_within_limit", passed=report.meta.step_count <= expect.max_steps,
        detail=f"实际 {report.meta.step_count} 步，上限 {expect.max_steps}",
    ))

    if expect.trace_complete:
        if trace_events:
            types = {e.event_type for e in trace_events}
            need = {"plan", "tool_call", "synthesize"}
            out.append(AssertionResult(
                name="trace_complete", passed=need <= types,
                detail=f"缺失事件 {sorted(need - types)}" if not need <= types else "trace 关键事件齐全",
            ))
        else:
            out.append(AssertionResult(
                name="trace_complete", passed=False, detail="未采集到 trace 事件",
            ))
    return out


def run_case(graph, case: EvalCase, *, trace_store=None, user_id: str = "eval") -> CaseResult:
    run_id = f"eval-{case.case_id}"
    final_state = graph.invoke(
        {"question": case.question, "user_id": user_id, "run_id": run_id},
        config={"recursion_limit": 128},
    )
    events = trace_store.get(run_id) if trace_store is not None else []
    assertions = run_assertions(case, final_state, events)
    report = final_state.get("report")
    if report is None:
        return CaseResult(
            case_id=case.case_id, category=case.category, question=case.question,
            passed=all(a.passed for a in assertions), assertions=assertions,
            evidence_count=len(final_state.get("evidences", [])),
        )
    guard = guard_metrics(report)
    resolution = source_resolution(report)
    return CaseResult(
        case_id=case.case_id, category=case.category, question=case.question,
        passed=all(a.passed for a in assertions), assertions=assertions,
        report_id=report.report_id,
        plan_rounds=report.meta.plan_rounds,
        step_count=report.meta.step_count,
        evidence_count=len(final_state.get("evidences", [])),
        citation_coverage=_citation_coverage(report),
        pre_guard_citation_total=guard["pre_guard_citation_total"],
        pre_guard_citation_valid=guard["pre_guard_citation_valid"],
        pre_guard_citation_invalid=guard["pre_guard_citation_invalid"],
        pre_guard_citation_validity_rate=guard["pre_guard_citation_validity_rate"],
        post_guard_citation_total=guard["post_guard_citation_total"],
        post_guard_citation_valid=guard["post_guard_citation_valid"],
        post_guard_citation_invalid=guard["post_guard_citation_invalid"],
        post_guard_citation_integrity_rate=guard["post_guard_citation_integrity_rate"],
        guard_ran=guard["guard_ran"],
        guard_rewrite_attempted=guard["guard_rewrite_attempted"],
        guard_rewrite_accepted=guard["guard_rewrite_accepted"],
        guard_claims_checked=guard["guard_claims_checked"],
        guard_claims_quarantined=guard["guard_claims_quarantined"],
        guard_fallback_used=guard["guard_fallback_used"],
        guard_fail_closed=guard["guard_fail_closed"],
        dimension_coverage=_dimension_coverage(
            case.expect.expected_dimensions, case.expect.expected_objects, report.evidences
        ),
        source_reference_integrity_rate=resolution["source_reference_integrity_rate"],
        source_resolution=resolution,
    )