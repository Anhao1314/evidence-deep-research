"""CI 卡点：pytest 消费与 eval runner 相同的断言接口，评测不达标即红。"""

import pytest

from eval.assertions import load_cases, run_case


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.case_id)
def test_eval_case(research_app, runtime, case):
    result = run_case(research_app, case, trace_store=runtime.trace, user_id="pytest-eval")
    failed = [a for a in result.assertions if not a.passed]
    assert not failed, f"评测断言失败: {[(a.name, a.detail) for a in failed]}"
    assert result.passed


def test_eval_runner_exits_zero():
    """runner 子进程全链路（构建 runtime -> 逐 case -> JSON 报告）。"""
    import subprocess
    import sys

    from app.config import PROJECT_ROOT

    subprocess.run(
        [sys.executable, "-m", "eval.run_eval", "--output", ".eval-smoke.json"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300, check=True,
    )
    assert ".eval-smoke.json" in {p.name for p in PROJECT_ROOT.iterdir()}
