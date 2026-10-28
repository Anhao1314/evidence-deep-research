"""在线 Runner：没有真实 Key 时必须 NOT_RUN；冒烟模式必须显式标记、不得冒充在线。"""

import json
import sys

from app.config import Settings
from eval import online_runner as runner
from eval.task_suite import load_suite


def _offline_settings(**overrides) -> Settings:
    base = {
        "mode": "offline",
        "tool_retry_base_delay": 0.0,
        "mcp_kb_enabled": False,
        "llm_api_key": None,
        "search_provider": "demo",
    }
    base.update(overrides)
    return Settings(**base)


def test_preflight_lists_missing_online_requirements():
    problems = runner.preflight(_offline_settings(search_provider="tavily"))
    assert len(problems) >= 3
    text = "\n".join(problems)
    assert "DR_MODE" in text and "DR_LLM_API_KEY" in text and "DR_TAVILY_API_KEY" in text


def test_preflight_accepts_fully_configured_online(tmp_path):
    (tmp_path / "corpus.jsonl").write_text('{"doc_id":"d1"}\n', encoding="utf-8")
    settings = Settings(
        mode="online",
        llm_api_key="sk-test",
        search_provider="tavily",
        tavily_api_key="tvly-test",
        kb_corpus_dir=tmp_path,
        kb_corpus_version="test-v1",
    )
    assert runner.preflight(settings) == []


def test_preflight_rejects_demo_search_even_with_llm_key():
    settings = Settings(mode="online", llm_api_key="sk-test", search_provider="demo")
    problems = runner.preflight(settings)
    assert any("DR_SEARCH_PROVIDER" in p for p in problems)


def test_full_mode_rejects_demo_kb_for_both_tasks():
    settings = Settings(
        mode="online", llm_api_key="sk-test", search_provider="tavily",
        tavily_api_key="tvly-test", kb_enabled=True,
    )
    tasks = load_suite().cases
    problems = runner.preflight(settings, mode="full", tasks=tasks)
    assert any("演示 KB" in p for p in problems)


def test_web_only_mode_allows_demo_kb_and_requires_web_cases():
    settings = Settings(
        mode="online", llm_api_key="sk-test", search_provider="tavily",
        tavily_api_key="tvly-test",
    )
    web_suite = load_suite(runner.DEFAULT_WEB_ONLY_SUITE)
    problems = runner.preflight(settings, mode="web-only", tasks=web_suite.cases)
    assert not any("演示 KB" in p for p in problems)

    full_suite = load_suite().cases
    problems = runner.preflight(settings, mode="web-only", tasks=full_suite)
    assert any("非 web 路线" in p for p in problems)


def test_web_only_smoke_manifest_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Settings", lambda: _offline_settings())
    out = tmp_path / "web-only"
    monkeypatch.setattr(sys, "argv", [
        "eval.online_runner", "--mode", "web-only",
        "--output-dir", str(out), "--limit", "1", "--allow-offline-smoke",
    ])
    assert runner.main() == 0
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_mode"] == "offline_smoke"
    assert manifest["evaluation_scope"] == "web_only_subset"
    assert manifest["route_mode"] == "web_only"
    assert manifest["config"]["kb_enabled"] is False
    assert manifest["suite"]["parent_suite_id"] == "research-tasks-v1"
    assert any("Web-only 子集" in w for w in manifest["warnings"])


def test_config_summary_never_contains_secret():
    settings = Settings(
        mode="online", llm_api_key="sk-super-secret", search_provider="tavily",
        tavily_api_key="tvly-super-secret",
    )
    dumped = json.dumps(runner._config_summary(settings), ensure_ascii=False)
    assert "sk-super-secret" not in dumped
    assert "tvly-super-secret" not in dumped
    assert '"secrets_included": false' in dumped


def test_not_run_writes_only_manifest_and_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Settings", lambda: _offline_settings())
    out = tmp_path / "not-run"
    monkeypatch.setattr(sys, "argv", [
        "eval.online_runner", "--output-dir", str(out), "--limit", "1",
    ])
    assert runner.main() == 2
    assert (out / "NOT_RUN.json").exists()
    assert not (out / "summary.json").exists()
    assert not (out / "cases").exists()
    payload = json.loads((out / "NOT_RUN.json").read_text(encoding="utf-8"))
    assert payload["status"] == "NOT_RUN"
    assert payload["reasons"]
    assert "未调用任何模型" in payload["note"]


def test_offline_smoke_is_labeled_and_produces_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Settings", lambda: _offline_settings())
    out = tmp_path / "smoke"
    monkeypatch.setattr(sys, "argv", [
        "eval.online_runner",
        "--output-dir", str(out),
        "--limit", "1",
        "--allow-offline-smoke",
    ])
    assert runner.main() == 0

    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_mode"] == "offline_smoke"
    assert manifest["status"] == "COMPLETED"
    assert manifest["providers"]["llm"] == "heuristic_offline"
    assert manifest["config"]["secrets_included"] is False
    assert "git_commit" in manifest and "git_dirty" in manifest
    assert manifest["config"]["kb_corpus_is_demo_snapshot"] is True
    assert any("内置演示语料" in w for w in manifest["warnings"])

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution_mode"] == "offline_smoke"
    assert summary["automatic_metrics"]["counts"]["total"] == 1
    assert summary["automatic_metrics"]["pipeline_completion_rate"]["value"] == 1.0
    assert summary["semantic_metrics"]["status"] == "NOT_MEASURED"
    assert any("在线模型效果" in item for item in summary["unmeasured"])
    assert any("真实知识库覆盖" in item for item in summary["unmeasured"])
    assert "OFFLINE_SMOKE" in (out / "summary.md").read_text(encoding="utf-8")
    assert "运行警告" in (out / "summary.md").read_text(encoding="utf-8")

    case_dir = out / "cases" / "vdb-qdrant-vs-milvus-50m"
    assert (case_dir / "report.md").exists()
    assert (case_dir / "report.json").exists()
    trace = json.loads((case_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["event_count"] > 0
    result = json.loads((case_dir / "result.json").read_text(encoding="utf-8"))
    assert result["case_id"] == "vdb-qdrant-vs-milvus-50m"
    assert result["has_report"] is True
    assert result["all_checks_passed"] is True
    assert result["failure_types"] == []
    assert (out / "annotation_template.csv").exists()


def test_unknown_case_id_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Settings", lambda: _offline_settings())
    out = tmp_path / "bad-case"
    monkeypatch.setattr(sys, "argv", [
        "eval.online_runner", "--output-dir", str(out),
        "--allow-offline-smoke", "--case-ids", "does-not-exist",
    ])
    assert runner.main() == 2
    assert not (out / "summary.json").exists()