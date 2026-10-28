"""约束 fixture Runner：python -m eval.run_constraint_fixture

只跑离线合成语料，验证“用户约束是否真正改变结论方向”，不是在线效果评测。
Atlas/Helios/pgvector/Nebula 均为 fixture 对象，不代表真实产品。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT, Settings
from app.graph.factory import build_default_app
from eval.metrics import source_resolution
from eval.provenance import git_commit, git_dirty, now_iso, sha256_file, source_manifest

DEFAULT_SUITE = Path(__file__).resolve().parent / "fixtures" / "constraint_tasks.v1.json"
_CITE_RE = re.compile(r"\[(E\d+)\]")
DISCLAIMER = (
    "离线合成 fixture 回归，不是在线效果评测：这些名称仅作为合成 fixture 中的评测对象，"
    "结构化属性为测试构造，不代表同名真实产品的真实能力，也不构成真实技术选型建议；"
    "只验证约束传递、结构化核验与报告方向。"
)


class FixtureCase(BaseModel):
    case_id: str
    question: str
    expected_verdict: str
    expected_recommended: list[str] = Field(default_factory=list)
    expected_excluded_objects: list[str] = Field(default_factory=list)
    expected_unknown_objects: list[str] = Field(default_factory=list)
    expected_constraint_kinds: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    expect_gaps: bool = False


class FixtureSuite(BaseModel):
    suite_id: str
    version: str
    description: str
    fixture: dict
    cases: list[FixtureCase]


def load_suite(path: Path = DEFAULT_SUITE) -> FixtureSuite:
    return FixtureSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def run_case(graph, trace_store, case: FixtureCase, fixture_dir: Path) -> dict:
    run_id = f"fixture-{case.case_id}"
    final = graph.invoke(
        {"question": case.question, "user_id": "constraint-fixture", "run_id": run_id},
        config={"recursion_limit": 128},
    )
    report = final["report"]
    summary = report.constraint_summary
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("report_produced", report is not None, "报告已产出" if report else "未产出报告")
    if report is None:
        return {"case_id": case.case_id, "passed": False, "checks": checks}

    constraints = report.constraints
    kinds = {c.kind for c in constraints}
    check("constraints_extracted", bool(constraints), f"抽取 {len(constraints)} 条约束：{sorted(kinds)}")
    missing_kinds = sorted(set(case.expected_constraint_kinds) - kinds)
    check("constraint_kinds", not missing_kinds, f"缺失约束类型 {missing_kinds}")

    verdict = summary.verdict if summary else None
    check("constraint_verdict", verdict == case.expected_verdict,
          f"实际 {verdict}，期望 {case.expected_verdict}")

    recommended = sorted(summary.recommended_objects) if summary else []
    check("recommended_objects", recommended == sorted(case.expected_recommended),
          f"实际 {recommended}，期望 {sorted(case.expected_recommended)}")

    excluded = sorted((summary.excluded or {}).keys()) if summary else []
    missing_excluded = sorted(set(case.expected_excluded_objects) - set(excluded))
    check("excluded_objects", not missing_excluded,
          f"实际 {excluded}，期望包含 {case.expected_excluded_objects}")

    unknown = sorted(summary.unknown_objects) if summary else []
    missing_unknown = sorted(set(case.expected_unknown_objects) - set(unknown))
    check("unknown_objects", not missing_unknown,
          f"实际 {unknown}，期望包含 {case.expected_unknown_objects}")

    md = report.markdown
    for text in case.must_contain:
        check(f"must_contain:{text}", text in md, "出现" if text in md else "缺失")
    for text in case.must_not_contain:
        check(f"must_not_contain:{text}", text not in md, "未出现" if text not in md else "出现了")

    has_gaps = bool(report.meta.information_gaps)
    check("gaps", has_gaps == case.expect_gaps,
          f"实际缺口 {len(report.meta.information_gaps)} 条，期望{'有' if case.expect_gaps else '无'}")

    valid = {e.evidence_id for e in report.evidences}
    cited = set(_CITE_RE.findall(md))
    fabricated = sorted(cited - valid)
    check("no_fabricated_citations", not fabricated, f"编造引用 {fabricated}" if fabricated else "全部可回溯")

    resolution = source_resolution(report)
    check("source_reference_integrity",
          resolution["source_reference_integrity_rate"] == 1.0
          and not resolution["orphan_evidence_sources"],
          f"解析 {resolution['evidence_resolved']}/{resolution['evidence_total']}；"
          f"孤立 {resolution['orphan_evidence_sources']}")

    disposition = report.evidence_disposition
    adopted = [d for d in disposition if d.status == "adopted"]
    excluded_disp = [d for d in disposition if d.status == "excluded"]
    check("evidence_disposition",
          bool(adopted) and all(d.evidence_id in valid for d in adopted)
          and all(d.reason for d in excluded_disp),
          f"adopted={len(adopted)}, excluded={len(excluded_disp)}")

    check("guard_not_triggered_offline", report.unverified_claims == [],
          f"unverified={len(report.unverified_claims)}")

    trace = trace_store.get(run_id)
    return {
        "case_id": case.case_id,
        "question": case.question,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "verdict": verdict,
        "recommended_objects": recommended,
        "excluded": summary.excluded if summary else {},
        "unknown_objects": unknown,
        "constraints": [c.model_dump() for c in constraints],
        "constraint_summary": summary.model_dump() if summary else None,
        "information_gaps": report.meta.information_gaps,
        "report_markdown": md,
        "evidence_count": len(report.evidences),
        "trace_event_types": [e.event_type for e in trace],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线约束 fixture 回归")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=Path("constraint-fixture-report.json"))
    args = parser.parse_args()

    suite = load_suite(args.suite)
    fixture_dir = PROJECT_ROOT / suite.fixture["corpus_dir"]
    web_dir = PROJECT_ROOT / suite.fixture["web_demo_dir"]
    tmp = Path(__file__).resolve().parent / ".run"
    tmp.mkdir(exist_ok=True)
    settings = Settings(
        mode="offline",
        kb_corpus_dir=fixture_dir,
        web_demo_dir=web_dir,
        mcp_kb_enabled=False,
        rag_top_k=20,
        tool_retry_base_delay=0.0,
        memory_db_path=tmp / "constraint-memory.sqlite3",
        trace_db_path=tmp / "constraint-trace.sqlite3",
    )
    runtime = build_default_app(settings)
    results = []
    try:
        for case in suite.cases:
            row = run_case(runtime.graph, runtime.trace, case, fixture_dir)
            results.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            passed = sum(1 for c in row["checks"] if c["passed"])
            print(f"[{mark}] {case.case_id}: {passed}/{len(row['checks'])} 检查通过")
    finally:
        runtime.close()
        for f in tmp.glob("constraint-*.sqlite3"):
            f.unlink(missing_ok=True)

    passed = sum(1 for r in results if r["passed"])
    report = {
        "schema_version": "1.0",
        "report_type": "constraint_fixture",
        "disclaimer": DISCLAIMER,
        "generated_at": now_iso(),
        "git_commit": git_commit(PROJECT_ROOT),
        "git_dirty": git_dirty(PROJECT_ROOT),
        "source_manifest": source_manifest(PROJECT_ROOT),
        "suite": {
            "path": _display_path(args.suite),
            "suite_id": suite.suite_id,
            "version": suite.version,
            "sha256": sha256_file(args.suite),
            "fixture": suite.fixture,
            "corpus_sha256": sha256_file(fixture_dir / "corpus.jsonl"),
        },
        "totals": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "cases": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n约束 fixture：{passed}/{len(results)} 通过，报告已写入 {args.output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())