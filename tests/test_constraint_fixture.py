"""约束 fixture Runner：三条合成用例必须证明约束改变结论方向。"""

import json
import sys

from eval import run_constraint_fixture as runner


def test_constraint_fixture_passes(tmp_path, monkeypatch):
    out = tmp_path / "constraint-report.json"
    monkeypatch.setattr(sys, "argv", [
        "eval.run_constraint_fixture", "--output", str(out),
    ])
    assert runner.main() == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "constraint_fixture"
    assert data["totals"] == {"total": 3, "passed": 3, "failed": 0}
    by_id = {c["case_id"]: c for c in data["cases"]}
    assert by_id["constraint-recommend-pgvector"]["constraint_summary"]["verdict"] == "recommend"
    assert by_id["constraint-no-solution"]["recommended_objects"] == []
    assert "nebula" in by_id["constraint-insufficient-attributes"]["unknown_objects"]