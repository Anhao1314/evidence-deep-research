"""离线任务集 Runner：python -m eval.run_offline_suite

只运行 `offline_eligible=true` 的用例（内置语料可确定性覆盖的 10 条），
输出结构合法性的机器可读报告，供 CI 与人工复核使用。

这不是在线效果评测：没有真实模型、没有真实搜索、没有事实正确率。
报告里的 `report_type=offline_structural` 与 `disclaimer` 字段用于防止误读。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.graph.factory import build_default_app
from eval.assertions import run_case
from eval.provenance import git_commit, git_dirty, now_iso, sha256_file, source_manifest
from eval.task_suite import DEFAULT_SUITE, load_suite, suite_sha256

DISCLAIMER = (
    "本报告是离线结构回归，不是在线效果评测：使用启发式规划器与内置演示语料，"
    "不调用真实模型或搜索 API，不度量事实正确率或引用语义支撑率。"
)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _failure_result(case, error: Exception) -> dict:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "question": case.question,
        "passed": False,
        "assertions": [],
        "report_id": None,
        "error": f"{type(error).__name__}: {error}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线任务集结构回归")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=Path("offline-suite-report.json"))
    parser.add_argument(
        "--include-online-only",
        action="store_true",
        help="同时跑 online-only 用例；结果仅用于观察失败方式，不能替代在线评测",
    )
    parser.add_argument("--keep-db", action="store_true", help="保留运行产生的 SQLite 文件")
    args = parser.parse_args()

    suite = load_suite(args.suite)
    cases = suite.cases if args.include_online_only else suite.offline_cases()
    if not cases:
        print("任务集中没有 offline_eligible 用例", file=sys.stderr)
        return 2

    tmp = Path(__file__).resolve().parent / ".run"
    tmp.mkdir(exist_ok=True)
    settings = Settings(
        mode="offline",
        tool_retry_base_delay=0.0,
        memory_db_path=tmp / "offline-suite-memory.sqlite3",
        trace_db_path=tmp / "offline-suite-trace.sqlite3",
        mcp_kb_enabled=False,
    )
    runtime = build_default_app(settings)
    results: list[dict] = []
    try:
        for task in cases:
            eval_case = task.to_eval_case()
            try:
                result = run_case(runtime.graph, eval_case, trace_store=runtime.trace,
                                  user_id="offline-suite")
                row = result.model_dump()
                row["offline_eligible"] = task.offline_eligible
            except Exception as exc:  # noqa: BLE001 —— 记录失败而不是中断整套评测
                row = _failure_result(eval_case, exc)
                row["offline_eligible"] = task.offline_eligible
            results.append(row)
            mark = "PASS" if row["passed"] else "FAIL"
            passed_n = sum(1 for a in row.get("assertions", []) if a["passed"])
            total_n = len(row.get("assertions", []))
            print(f"[{mark}] {task.case_id}: {passed_n}/{total_n} 断言通过")
    finally:
        runtime.close()
        if not args.keep_db:
            for f in tmp.glob("*.sqlite3"):
                f.unlink(missing_ok=True)

    passed = sum(1 for r in results if r.get("passed"))
    report = {
        "schema_version": "1.0",
        "report_type": "offline_structural",
        "disclaimer": DISCLAIMER,
        "generated_at": now_iso(),
        "git_commit": git_commit(PROJECT_ROOT),
        "git_dirty": git_dirty(PROJECT_ROOT),
        "source_manifest": source_manifest(PROJECT_ROOT),
        "suite": {
            "path": _display_path(args.suite),
            "suite_id": suite.suite_id,
            "version": suite.version,
            "sha256": suite_sha256(args.suite) or sha256_file(args.suite),
            "selected_cases": len(cases),
            "offline_eligible_total": len(suite.offline_cases()),
            "suite_total": len(suite.cases),
            "included_online_only": args.include_online_only,
        },
        "totals": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "cases": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n离线结构回归：{passed}/{len(results)} 通过，报告已写入 {args.output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())