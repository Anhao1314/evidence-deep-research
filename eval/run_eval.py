"""评测 runner：python -m eval.run_eval --output eval-report.json

离线模式全量可跑（无需 API Key）；输出机器可读 JSON（CaseResult 流），
全部用例通过 exit 0，否则 exit 1——CI 与外部评测框架的统一入口。
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.graph.factory import build_default_app
from eval.assertions import DEFAULT_CASES, load_cases, run_case
from eval.provenance import git_commit, git_dirty, now_iso, source_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep Research 评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="评测集 JSONL 路径")
    parser.add_argument("--output", type=Path, default=Path("eval-report.json"), help="结果输出路径")
    parser.add_argument("--keep-db", action="store_true", help="保留运行产生的 SQLite 文件")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    tmp = Path(__file__).resolve().parent / ".run"
    tmp.mkdir(exist_ok=True)
    settings = Settings(
        mode="offline",
        tool_retry_base_delay=0.0,
        memory_db_path=tmp / "eval-memory.sqlite3",
        trace_db_path=tmp / "eval-trace.sqlite3",
    )
    runtime = build_default_app(settings)
    results = []
    try:
        for case in cases:
            result = run_case(runtime.graph, case, trace_store=runtime.trace, user_id="eval")
            results.append(result.model_dump())
            mark = "PASS" if result.passed else "FAIL"
            print(f"[{mark}] {case.case_id}: {sum(a.passed for a in result.assertions)}"
                  f"/{len(result.assertions)} 断言通过")
    finally:
        runtime.close()
        if not args.keep_db:
            for f in tmp.glob("*.sqlite3"):
                f.unlink(missing_ok=True)

    passed = sum(1 for r in results if r["passed"])
    report = {"total": len(results), "passed": passed, "failed": len(results) - passed,
              "generated_at": now_iso(), "git_commit": git_commit(PROJECT_ROOT),
              "git_dirty": git_dirty(PROJECT_ROOT),
              "source_manifest": source_manifest(PROJECT_ROOT),
              "results": results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n评测完成：{passed}/{len(results)} 通过，报告已写入 {args.output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
