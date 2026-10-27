"""评测断言接口：pytest 与外部评测框架共用的行为断言层。

断言全部基于结构化产物（Report / TraceEvent），不解析自然语言，
保证离线可测、确定性可复现。每个断言返回 AssertionResult（含细节），
聚合为 CaseResult / EvalReport——评测框架拿到的是机器可读的结果流。
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.schema.report import Report

_CITE_RE = re.compile(r"\[(E\d+)\]")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = Path(__file__).resolve().parent / "cases.jsonl"


class EvalExpect(BaseModel):
    routes: list[str] = Field(default_factory=list)
    min_evidences: int = 0
    citation_coverage_min: float = 0.0
    objects_in_report: list[str] = Field(default_factory=list)
    must_converge: bool = True
    max_plan_rounds: int = 2


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
    citation_coverage: float = 0.0


def citation_coverage(report: Report) -> float:
    """结论性内容引用的证据占比：union(citations.evidence_ids) / len(evidences)。"""
    if not report.evidences:
        return 0.0
    cited = {eid for c in report.citations for eid in c.evidence_ids}
    return len(cited) / len(report.evidences)


def run_assertions(case: EvalCase, final_state: dict, trace_events: list) -> list[AssertionResult]:
    """对单次 run 的产物执行全部断言。final_state 为 LangGraph 最终 state。"""
    expect = case.expect
    report: Report | None = final_state.get("report")
    evidences = final_state.get("evidences", [])
    out: list[AssertionResult] = []

    # 1. 必须收敛出报告
    out.append(AssertionResult(
        name="must_converge", passed=report is not None,
        detail="报告已产出" if report else "未产出报告",
    ))
    if report is None:
        return out

    # 2. 工具调用序列：实际使用的检索路覆盖期望
    routes_used = set(report.meta.routes_used)
    missing_routes = sorted(set(expect.routes) - routes_used)
    out.append(AssertionResult(
        name="routes_used",
        passed=not missing_routes,
        detail=f"实际 {sorted(routes_used)}，期望至少 {expect.routes}"
               + (f"，缺失 {missing_routes}" if missing_routes else ""),
    ))

    # 3. 证据数量
    out.append(AssertionResult(
        name="min_evidences", passed=len(evidences) >= expect.min_evidences,
        detail=f"实际 {len(evidences)}，下限 {expect.min_evidences}",
    ))

    # 4. 引用无编造：报告中的 [E#] 全部来自真实证据集合
    valid = {e.evidence_id for e in evidences}
    cited = set(_CITE_RE.findall(report.markdown))
    fabricated = sorted(cited - valid)
    out.append(AssertionResult(
        name="no_fabricated_citations", passed=not fabricated,
        detail=f"编造引用: {fabricated}" if fabricated else f"引用 {len(cited)} 处全部可回溯",
    ))

    # 5. 引用覆盖率
    coverage = citation_coverage(report)
    out.append(AssertionResult(
        name="citation_coverage", passed=coverage >= expect.citation_coverage_min,
        detail=f"实际 {coverage:.2f}，下限 {expect.citation_coverage_min}",
    ))

    # 6. 来源可回溯：来源必须有定位（url/doc_id），证据的来源编号必须存在于来源清单
    source_ids = {s.source_id for s in report.sources}
    unlocated = [s.source_id for s in report.sources if not (s.url or s.doc_id)]
    orphans = sorted({e.source.source_id for e in evidences} - source_ids)
    out.append(AssertionResult(
        name="sources_traceable",
        passed=not unlocated and not orphans,
        detail=f"无定位来源 {unlocated}；孤立证据来源 {orphans}"
               if (unlocated or orphans) else f"{len(report.sources)} 条来源全部可定位",
    ))

    # 7. 结论一致性：期望对象出现在报告
    missing_objects = [o for o in expect.objects_in_report if o not in report.markdown]
    out.append(AssertionResult(
        name="objects_in_report", passed=not missing_objects,
        detail=f"缺失对象 {missing_objects}" if missing_objects
               else f"对象 {expect.objects_in_report} 全部出现",
    ))

    # 8. 护栏：计划轮数与步数上限
    out.append(AssertionResult(
        name="plan_rounds_within_limit",
        passed=report.meta.plan_rounds <= expect.max_plan_rounds,
        detail=f"实际 {report.meta.plan_rounds} 轮，上限 {expect.max_plan_rounds}",
    ))
    out.append(AssertionResult(
        name="steps_within_limit", passed=report.meta.step_count <= 32,
        detail=f"实际 {report.meta.step_count} 步，上限 32",
    ))

    # 9. trace 完整性：关键事件存在（工具调用与结论生成可审计）
    if trace_events:
        types = {e.event_type for e in trace_events}
        need = {"plan", "tool_call", "synthesize"}
        out.append(AssertionResult(
            name="trace_complete", passed=need <= types,
            detail=f"缺失事件 {sorted(need - types)}" if not need <= types else "trace 关键事件齐全",
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
    return CaseResult(
        case_id=case.case_id, category=case.category, question=case.question,
        passed=all(a.passed for a in assertions), assertions=assertions,
        report_id=report.report_id if report else None,
        plan_rounds=report.meta.plan_rounds if report else 0,
        step_count=report.meta.step_count if report else 0,
        evidence_count=len(final_state.get("evidences", [])),
        citation_coverage=citation_coverage(report) if report else 0.0,
    )
