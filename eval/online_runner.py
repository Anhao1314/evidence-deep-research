"""在线评测 Runner：python -m eval.online_runner

复用 app.graph.factory.build_default_app / ResearchRuntime，不复制研究逻辑。
只有真实配置了 OpenAI 兼容模型与真实搜索服务时才允许标记为 online；
否则写出 NOT_RUN 清单并退出，绝不拿离线结果冒充在线结果。

每次运行落盘：
- cases/<case_id>/report.json + report.md   原始报告
- cases/<case_id>/trace.json                完整事件流
- cases/<case_id>/case.json                 用例定义
- cases/<case_id>/result.json               自动断言、指标、失败信息
- run_manifest.json                         模型、搜索、时间、Git SHA、配置摘要
- summary.json / summary.md                 自动指标 + 人工指标占位 + 未测量项
- annotation_template.csv                   引用语义人工复核模板

用法（真正在线运行）：
    DR_MODE=online \
    DR_LLM_BASE_URL=https://api.deepseek.com/v1 \
    DR_LLM_API_KEY=... DR_LLM_MODEL=deepseek-chat \
    DR_SEARCH_PROVIDER=tavily DR_TAVILY_API_KEY=... \
    python -m eval.online_runner --suite eval/tasks/research_tasks.v1.json \
        --output-dir eval/runs/online-$(date +%Y%m%d-%H%M%S)
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, Settings
from app.graph.factory import build_default_app
from app.schema.report import Report
from eval.annotation import write_template
from eval.assertions import run_assertions
from eval.metrics import (
    citation_counts,
    citation_coverage,
    classify_failure_types,
    cost_usd,
    dimension_coverage,
    guard_metrics,
    route_coverage,
    source_resolution,
    summarize_structural,
    token_usage_from_events,
)
from eval.provenance import (
    corpus_inventory,
    git_commit,
    git_dirty,
    now_iso,
    sha256_file,
    source_manifest,
)
from eval.task_suite import DEFAULT_SUITE, load_suite, suite_sha256

SCHEMA_VERSION = "1.0"
DEFAULT_WEB_ONLY_SUITE = Path(__file__).resolve().parent / "tasks" / "research_tasks.v1.web-only.json"
ONLINE_ENV_REQUIRED = [
    "DR_MODE=online",
    "DR_LLM_API_KEY",
    "DR_SEARCH_PROVIDER=tavily",
    "DR_TAVILY_API_KEY",
]


def _corpus_has_files(directory: Path) -> bool:
    return directory.exists() and any(p.is_file() for p in directory.rglob("*"))


def preflight(settings: Settings, *, mode: str = "full", tasks=None) -> list[str]:
    problems: list[str] = []
    if settings.mode != "online":
        problems.append(f"DR_MODE 当前为 {settings.mode!r}，在线评测必须为 'online'")
    if not settings.llm_api_key:
        problems.append("DR_LLM_API_KEY 未配置：无法调用真实模型")
    if settings.search_provider != "tavily":
        problems.append(
            f"DR_SEARCH_PROVIDER 当前为 {settings.search_provider!r}；在线评测需要真实搜索（tavily）"
        )
    elif not settings.tavily_api_key:
        problems.append("DR_TAVILY_API_KEY 未配置：无法调用真实搜索")

    tasks = list(tasks or [])
    if mode == "full":
        if not settings.kb_enabled:
            problems.append("full 双路评测要求 DR_KB_ENABLED=true")
        requires_kb = any("kb" in t.expected_routes() for t in tasks) or not tasks
        if requires_kb and _knowledge_base_is_demo(settings):
            problems.append(
                "full 双路评测不能使用内置演示 KB（data/corpus）；"
                "请设置 DR_KB_CORPUS_DIR 指向真实语料，或改用 --mode web-only"
            )
        if requires_kb and not _corpus_has_files(settings.kb_corpus_dir):
            problems.append(f"KB 语料目录不可用或为空：{settings.kb_corpus_dir}")
    elif mode == "web-only":
        if not tasks:
            problems.append("web-only 模式没有可用用例")
        non_web = [t.case_id for t in tasks if "web" not in t.expected_routes()]
        if non_web:
            problems.append(f"web-only 模式包含非 web 路线用例：{non_web}")
    else:
        problems.append(f"未知评测模式：{mode}")
    return problems


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _knowledge_base_is_demo(settings: Settings) -> bool:
    try:
        return settings.kb_corpus_dir.resolve() == (PROJECT_ROOT / "data" / "corpus").resolve()
    except OSError:
        return False


def _config_summary(settings: Settings) -> dict[str, Any]:
    """只记录非敏感配置；API Key 永不落盘。"""
    return {
        "mode": settings.mode,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_timeout_seconds": settings.llm_timeout,
        "llm_max_retries": settings.llm_max_retries,
        "search_provider": settings.search_provider,
        "tavily_timeout_seconds": settings.tavily_timeout,
        "kb_corpus_dir": _display_path(settings.kb_corpus_dir),
        "kb_corpus_is_demo_snapshot": _knowledge_base_is_demo(settings),
        "kb_corpus_version": settings.kb_corpus_version,
        "kb_corpus_description": settings.kb_corpus_description,
        "kb_enabled": settings.kb_enabled,
        "max_steps": settings.max_steps,
        "max_plan_rounds": settings.max_plan_rounds,
        "max_subtasks": settings.max_subtasks,
        "evidence_budget": settings.evidence_budget,
        "tool_max_retries": settings.tool_max_retries,
        "mcp_kb_enabled": settings.mcp_kb_enabled,
        "llm_price_input_per_1m": settings.llm_price_input_per_1m,
        "llm_price_output_per_1m": settings.llm_price_output_per_1m,
        "price_currency": (
            "USD"
            if (settings.llm_price_input_per_1m is not None
                and settings.llm_price_output_per_1m is not None)
            else None
        ),
        "secrets_included": False,
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "case"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"online-{stamp}-{int(time.time() * 1000) % 1000:03d}"


def _write_not_run(
    output_dir: Path,
    settings: Settings,
    reasons: list[str],
    *,
    requested_mode: str = "full",
    suite_path: Path | None = None,
    case_ids: list[str] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "online_evaluation",
        "status": "NOT_RUN",
        "checked_at": now_iso(),
        "git_commit": git_commit(PROJECT_ROOT),
        "git_dirty": git_dirty(PROJECT_ROOT),
        "source_manifest": source_manifest(PROJECT_ROOT),
        "requested_mode": requested_mode,
        "evaluation_scope": "web_only_subset" if requested_mode == "web-only" else "full_dual_route",
        "suite_path": _display_path(suite_path) if suite_path else None,
        "selected_case_ids": case_ids or [],
        "reasons": reasons,
        "required_env": ONLINE_ENV_REQUIRED,
        "model_would_be": settings.llm_model,
        "run_command": (
            "DR_MODE=online DR_LLM_BASE_URL=<url> DR_LLM_API_KEY=<key> "
            "DR_LLM_MODEL=<model> DR_SEARCH_PROVIDER=tavily DR_TAVILY_API_KEY=<key> "
            "DR_KB_CORPUS_DIR=<real-corpus> "
            "python -m eval.online_runner --mode full --suite eval/tasks/research_tasks.v1.json "
            "--output-dir <run_dir>"
        ),
        "note": "未调用任何模型或搜索 API，未生成任何在线效果数字。本文件是唯一产物。",
    }
    _write_json(output_dir / "NOT_RUN.json", payload)


def _run_one(runtime, task, *, run_id: str, case_dir: Path, settings: Settings) -> dict:
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_json(case_dir / "case.json", task.model_dump())

    eval_case = task.to_eval_case()
    started_at = now_iso()
    t0 = time.perf_counter()
    error: str | None = None
    stack: str | None = None
    final_state: dict = {}
    try:
        final_state = runtime.graph.invoke(
            {"question": task.question, "user_id": "online-eval", "run_id": run_id},
            config={"recursion_limit": max(settings.max_steps * 4, 64)},
        )
    except Exception as exc:  # noqa: BLE001 —— 单 case 失败不终止整套评测
        error = f"{type(exc).__name__}: {exc}"
        stack = traceback.format_exc(limit=8)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    finished_at = now_iso()
    events = runtime.trace.get(run_id)
    report: Report | None = final_state.get("report")

    if report is not None:
        _write_json(case_dir / "report.json", report.model_dump())
        (case_dir / "report.md").write_text(report.markdown, encoding="utf-8")

    trace_dump = {
        "trace_id": run_id,
        "event_count": len(events),
        "token_usage": sum(e.token_usage for e in events),
        "events": [e.model_dump() for e in events],
    }
    _write_json(case_dir / "trace.json", trace_dump)
    if error:
        (case_dir / "error.txt").write_text(stack or error, encoding="utf-8")

    assertions = run_assertions(eval_case, final_state, events)
    assertion_dicts = [a.model_dump() for a in assertions]
    has_report = report is not None and error is None

    if report is not None:
        valid_ids = {e.evidence_id for e in report.evidences}
        counts = citation_counts(report.markdown, valid_ids)
        guard = guard_metrics(report)
        resolution = source_resolution(report)
        citation_cov = citation_coverage(report)
        route_cov = route_coverage(task.expected_routes(), report.meta.routes_used)
        dim_cov = dimension_coverage(
            task.dimension_aspects,
            task.dimension_objects or task.expected_objects,
            report.evidences,
        )
        information_gaps = report.meta.information_gaps
        routes_used = report.meta.routes_used
        routes_attempted = report.meta.routes_attempted
        plan_rounds = report.meta.plan_rounds
        step_count = report.meta.step_count
        degraded = report.meta.degraded
        evidence_count = len(report.evidences)
        source_count = len(report.sources)
        report_id = report.report_id
        markdown = report.markdown
        guard_audit = report.guard_audit
    else:
        counts = {"total": 0, "valid": 0, "invalid": 0, "validity_rate": None,
                  "invalid_ids": []}
        guard = {
            "pre_guard_citation_total": 0, "pre_guard_citation_valid": 0,
            "pre_guard_citation_invalid": 0, "pre_guard_citation_validity_rate": None,
            "post_guard_citation_total": 0, "post_guard_citation_valid": 0,
            "post_guard_citation_invalid": 0, "post_guard_citation_integrity_rate": None,
            "guard_ran": False, "guard_rewrite_attempted": False,
            "guard_rewrite_accepted": None, "guard_claims_checked": 0,
            "guard_claims_quarantined": 0, "guard_fallback_used": False,
            "guard_fail_closed": False,
        }
        resolution = {
            "source_reference_integrity_rate": None, "source_total": 0,
            "source_resolved": 0, "source_unresolved_ids": [],
            "source_missing_content_hash": [], "source_missing_locator": [],
            "orphan_evidence_sources": [], "evidence_total": 0, "evidence_resolved": 0,
            "url_reachable": {"status": "NOT_MEASURED"},
        }
        citation_cov = route_cov = dim_cov = None
        information_gaps = []
        routes_used = []
        routes_attempted = []
        plan_rounds = step_count = 0
        degraded = False
        evidence_count = source_count = 0
        report_id = None
        markdown = ""
        guard_audit = None

    tokens = token_usage_from_events(events)
    estimated_cost = (
        cost_usd(
            tokens["prompt_tokens"],
            tokens["completion_tokens"],
            settings.llm_price_input_per_1m,
            settings.llm_price_output_per_1m,
        )
        if tokens["events"] > 0
        else None
    )
    trace_types = {e.event_type for e in events}
    failure_types = classify_failure_types(
        error=error,
        has_report=has_report,
        evidence_count=evidence_count,
        expected_routes=task.expected_routes(),
        routes_used=routes_used,
        post_guard_invalid_citations=counts["invalid"],
        source_rate=resolution.get("source_reference_integrity_rate"),
        information_gaps=information_gaps,
        must_state_gaps=task.automated_checks.must_state_gaps,
        expected_objects=task.expected_objects,
        report_markdown=markdown,
        trace_types=trace_types,
        guard_audit=guard_audit,
        assertions=assertion_dicts,
    )

    record = {
        "case_id": task.case_id,
        "category": task.category,
        "title": task.title,
        "question": task.question,
        "offline_eligible": task.offline_eligible,
        "status": "completed" if has_report else "failed",
        "completed": has_report,
        "has_report": has_report,
        "started_at": started_at,
        "finished_at": finished_at,
        "latency_ms": latency_ms,
        "run_id": run_id,
        "report_id": report_id,
        "plan_rounds": plan_rounds,
        "step_count": step_count,
        "routes_used": routes_used,
        "routes_attempted": routes_attempted,
        "expected_routes": task.expected_routes(),
        "degraded": degraded,
        "evidence_count": evidence_count,
        "source_count": source_count,
        "citation_total": counts["total"],
        "citation_valid": counts["valid"],
        "citation_invalid": counts["invalid"],
        "citation_validity_rate": counts["validity_rate"],
        "pre_guard_citation_total": guard["pre_guard_citation_total"],
        "pre_guard_citation_valid": guard["pre_guard_citation_valid"],
        "pre_guard_citation_invalid": guard["pre_guard_citation_invalid"],
        "pre_guard_citation_validity_rate": guard["pre_guard_citation_validity_rate"],
        "post_guard_citation_total": guard["post_guard_citation_total"],
        "post_guard_citation_valid": guard["post_guard_citation_valid"],
        "post_guard_citation_invalid": guard["post_guard_citation_invalid"],
        "post_guard_citation_integrity_rate": guard["post_guard_citation_integrity_rate"],
        "guard_ran": guard["guard_ran"],
        "guard_rewrite_attempted": guard["guard_rewrite_attempted"],
        "guard_rewrite_accepted": guard["guard_rewrite_accepted"],
        "guard_claims_checked": guard["guard_claims_checked"],
        "guard_claims_quarantined": guard["guard_claims_quarantined"],
        "guard_fallback_used": guard["guard_fallback_used"],
        "guard_fail_closed": guard["guard_fail_closed"],
        "citation_coverage": citation_cov,
        "source_reference_integrity_rate": resolution.get("source_reference_integrity_rate"),
        "source_resolution": resolution,
        "route_coverage": route_cov,
        "dimension_coverage": dim_cov,
        "information_gaps": information_gaps,
        "token_usage": tokens,
        "cost_usd": estimated_cost,
        "failure_types": failure_types,
        "all_checks_passed": bool(assertion_dicts) and all(a["passed"] for a in assertion_dicts),
        "assertions": assertion_dicts,
        "error": error,
        "artifacts": {
            "case": f"cases/{case_dir.name}/case.json",
            "report_json": f"cases/{case_dir.name}/report.json" if report else None,
            "report_md": f"cases/{case_dir.name}/report.md" if report else None,
            "trace": f"cases/{case_dir.name}/trace.json",
            "error": f"cases/{case_dir.name}/error.txt" if error else None,
        },
    }
    _write_json(case_dir / "result.json", record)
    return record


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def _md_row(name: str, value: str, ratio: str, definition: str) -> str:
    return f"| {name} | {value} | {ratio} | {definition} |"


def _render_summary_md(summary: dict, manifest: dict, records: list[dict]) -> str:
    structural = summary["automatic_metrics"]
    semantic = summary["semantic_metrics"]
    mode = manifest["execution_mode"]
    scope = manifest.get("evaluation_scope", "full_dual_route")
    if mode == "offline_smoke":
        banner = "OFFLINE_SMOKE（不是在线评测结果）"
    elif scope == "web_only_subset":
        banner = "ONLINE / WEB_ONLY_SUBSET（仅 Web 路线，不是 20 条全量双路）"
    else:
        banner = "ONLINE / FULL_DUAL_ROUTE"
    lines = [
        f"# 评测运行摘要：{manifest['run_id']}",
        "",
        f"> **执行模式：{banner}**  ",
        f"> 评测范围：`{scope}`；路线模式：`{manifest.get('route_mode')}`  ",
        f"> 状态：{manifest['status']}  ",
        f"> 生成时间：{summary['generated_at']}  ",
        f"> Git commit：`{manifest.get('git_commit')}`（dirty={manifest.get('git_dirty')}）  ",
        f"> 模型：`{manifest.get('config', {}).get('llm_model')}`  ",
        f"> 搜索：`{manifest.get('config', {}).get('search_provider')}`  ",
        "",
    ]
    if mode == "offline_smoke":
        lines += [
            "**本文件不是在线评测报告。** 它使用离线启发式规划器与内置演示语料，",
            "用于验证 Runner 的产物、指标与失败记录是否工作；不能用于宣称在线效果。",
            "",
        ]
    if scope == "web_only_subset":
        lines += [
            ("**本文件不是原 20 条全量双路评测。** 本次只运行显式 Web-only 子集，"
             "KB 路已关闭；统计分母只覆盖本任务集用例。"),
            "",
        ]
    if manifest.get("warnings"):
        lines += ["## 运行警告", ""]
        lines += [f"- {warning}" for warning in manifest["warnings"]]
        lines.append("")

    lines += [
        "## 自动指标（结构合法 / 可回溯 / 路线覆盖 / 延迟 / Token）",
        "",
        "| 指标 | 值 | 分子/分母 | 定义 |",
        "| --- | --- | --- | --- |",
        _md_row(
            "流程完成率",
            _pct(structural["pipeline_completion_rate"]["value"]),
            f"{structural['pipeline_completion_rate']['numerator']}/"
            f"{structural['pipeline_completion_rate']['denominator']}",
            structural["pipeline_completion_rate"]["definition"],
        ),
        _md_row(
            "全部自动检查通过率",
            _pct(structural["all_checks_pass_rate"]["value"]),
            f"{structural['all_checks_pass_rate']['numerator']}/"
            f"{structural['all_checks_pass_rate']['denominator']}",
            structural["all_checks_pass_rate"]["definition"],
        ),
        _md_row(
            "pre-guard 引用合法率",
            _pct(structural["pre_guard_citation_valid_rate"]["value"]),
            f"{structural['pre_guard_citation_valid_rate']['numerator']}/"
            f"{structural['pre_guard_citation_valid_rate']['denominator']}",
            structural["pre_guard_citation_valid_rate"]["definition"],
        ),
        _md_row(
            "post-guard 引用完整率",
            _pct(structural["post_guard_citation_integrity_rate"]["value"]),
            f"{structural['post_guard_citation_integrity_rate']['numerator']}/"
            f"{structural['post_guard_citation_integrity_rate']['denominator']}",
            structural["post_guard_citation_integrity_rate"]["definition"],
        ),
        _md_row(
            "Guard 受限重写率",
            _pct(structural["guard_rewrite_rate"]["value"]),
            f"{structural['guard_rewrite_rate']['numerator']}/"
            f"{structural['guard_rewrite_rate']['denominator']}",
            structural["guard_rewrite_rate"]["definition"],
        ),
        _md_row(
            "Guard 结论隔离率",
            _pct(structural["guard_claim_quarantine_rate"]["value"]),
            f"{structural['guard_claim_quarantine_rate']['numerator']}/"
            f"{structural['guard_claim_quarantine_rate']['denominator']}",
            structural["guard_claim_quarantine_rate"]["definition"],
        ),
        _md_row(
            "Guard 回退率",
            _pct(structural["guard_fallback_rate"]["value"]),
            f"{structural['guard_fallback_rate']['numerator']}/"
            f"{structural['guard_fallback_rate']['denominator']}",
            structural["guard_fallback_rate"]["definition"],
        ),
        _md_row(
            "来源引用完整率",
            _pct(structural["source_reference_integrity_rate"]["value"]),
            f"{structural['source_reference_integrity_rate']['numerator']}/"
            f"{structural['source_reference_integrity_rate']['denominator']}",
            structural["source_reference_integrity_rate"]["definition"],
        ),
        _md_row(
            "来源解析（来源/证据）",
            f"{structural['source_resolution']['source_resolved']}/"
            f"{structural['source_resolution']['source_total']}",
            f"证据 {structural['source_resolution']['evidence_resolved']}/"
            f"{structural['source_resolution']['evidence_total']}",
            "URL 可访问性：NOT_MEASURED（评测不做网络探测）",
        ),
        _md_row(
            "正文引用覆盖率（均值）",
            _num(structural["citation_coverage"]["mean"]),
            f"n={structural['citation_coverage']['n']}",
            structural["citation_coverage"]["definition"],
        ),
        _md_row(
            "路线覆盖率（均值）",
            _pct(structural["route_coverage"]["mean"]),
            f"n={structural['route_coverage']['n']}",
            structural["route_coverage"]["definition"],
        ),
        _md_row(
            "维度覆盖率（均值）",
            _pct(structural["dimension_coverage"]["mean"]),
            f"n={structural['dimension_coverage']['n']}",
            structural["dimension_coverage"]["definition"],
        ),
        _md_row(
            "P50 延迟",
            f"{_num(structural['latency']['p50_ms'])} ms",
            f"n={structural['latency']['n']}",
            structural["latency"]["method"],
        ),
        _md_row(
            "P95 延迟",
            f"{_num(structural['latency']['p95_ms'])} ms",
            f"n={structural['latency']['n']}",
            structural["latency"]["method"],
        ),
        _md_row(
            "Token 总量",
            str(structural["token_usage"]["total_tokens"]),
            f"prompt={structural['token_usage']['prompt_tokens']}, "
            f"completion={structural['token_usage']['completion_tokens']}, "
            f"usage_events={structural['token_usage']['usage_events']}",
            structural["token_usage"]["note"],
        ),
    ]
    cost = structural["cost"]
    lines.append(
        f"| 成本 | {'$' + str(cost['usd_sum']) if cost['usd_sum'] is not None else 'NOT_MEASURED'} | "
        f"n={cost['n']} | {cost['note']} |"
    )
    lines += [
        "",
        ("> pre-guard 指标观察模型原始引用行为；post-guard 指标评价系统最终输出。"
         "两者都不是语义支撑率，也不能代表事实正确率。"),
        "",
    ]

    lines += ["", "## 人工指标（引用语义支撑）", ""]
    if semantic.get("status") == "MEASURED":
        lines += [
            (
                f"已标注 {semantic['labeled_claims']}/{semantic['sampled_claims']} 条，"
                f"标注覆盖率 {_pct(semantic['label_coverage'])}。"
            ),
            "",
            "| 标签 | 数量 | 占已标注 |",
            "| --- | --- | --- |",
        ]
        for label, count in semantic["labels"].items():
            lines.append(f"| {label} | {count} | {_pct(semantic['rates'][label])} |")
        lines += ["", f"一致率：{semantic['inter_annotator_agreement']}", ""]
    else:
        lines += [
            "**NOT_MEASURED**：尚未完成人工标注。",
            "",
            "先填写 `annotation_template.csv`，再执行：",
            "",
            "```bash",
            "python -m eval.annotation summarize --labels annotation_template.csv",
            "```",
            "",
            "在标注完成前，本报告不提供任何“事实正确率”或“引用语义支撑率”。",
            "",
        ]

    lines += ["## 未测量项", ""]
    for item in summary["unmeasured"]:
        lines.append(f"- {item}")

    lines += ["", "## 失败类型分布", ""]
    dist = structural["failure_type_distribution"]["counts"]
    if dist:
        lines += ["| 类型 | 中文 | 次数 |", "| --- | --- | --- |"]
        for key, count in dist.items():
            lines.append(f"| `{key}` | {structural['failure_type_distribution']['zh'].get(key, key)} | {count} |")
    else:
        lines.append("- 无失败类型记录")

    lines += [
        "",
        "## 逐用例结果",
        "",
        "| case | 状态 | 自动检查 | 路线(used/attempted) | 证据 | post-guard完整率 | 延迟(ms) | 失败类型 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        checks = f"{sum(1 for a in r['assertions'] if a['passed'])}/{len(r['assertions'])}"
        lines.append(
            f"| `{r['case_id']}` | {r['status']} | {checks} | "
            f"{','.join(r['routes_used']) or '-'}/{','.join(r.get('routes_attempted') or []) or '-'} | "
            f"{r['evidence_count']} | {_pct(r['post_guard_citation_integrity_rate'])} | "
            f"{r['latency_ms']:.0f} | {', '.join(r['failure_types']) or '-'} |"
        )

    failed = [r for r in records if not r["all_checks_passed"]]
    lines += ["", "## Badcase 列表（自动检查未通过）", ""]
    if failed:
        for r in failed[:10]:
            failed_names = [a["name"] for a in r["assertions"] if not a["passed"]]
            lines.append(
                f"- `{r['case_id']}`：失败检查 {failed_names or ['pipeline_exception']}；"
                f"失败类型 {r['failure_types']}；报告产物 "
                f"{r['artifacts'].get('report_md') or '无'}"
            )
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _unmeasured_items(manifest: dict, structural: dict, semantic: dict) -> list[str]:
    items = [
        "事实正确率：任务集没有 gold answer，自动指标不判断结论真假",
        "引用语义支撑率：只接受人工标注，未完成标注时为 NOT_MEASURED",
        "检索召回率：没有人工标注的相关文档集合，无法计算 recall",
        "跨运行方差：本报告只包含一次运行，不提供稳定性区间",
    ]
    if structural["cost"]["status"] == "NOT_MEASURED":
        config = manifest.get("config", {})
        if (config.get("llm_price_input_per_1m") is None
                or config.get("llm_price_output_per_1m") is None):
            items.append("成本：未同时配置 DR_LLM_PRICE_INPUT_PER_1M 与 DR_LLM_PRICE_OUTPUT_PER_1M")
        else:
            items.append("成本：已配置单价，但本次没有任何 usage 事件，无法计算")
    if manifest["execution_mode"] != "online":
        items += [
            "在线模型效果：本次不是在线运行",
            "真实搜索覆盖：本次不是在线运行",
            "在线延迟与 Token：本次不是在线运行",
        ]
    if manifest.get("config", {}).get("kb_enabled") is False:
        items.append("KB 路：本次为 Web-only 子集，KB 已显式关闭，未评测双路")
    elif manifest.get("config", {}).get("kb_corpus_is_demo_snapshot"):
        items.append("真实知识库覆盖：本次 KB 使用内置演示语料，kb 证据不代表生产知识库")
    items.append("URL 可访问性：评测不做网络探测，source.url_reachable 为 NOT_MEASURED")
    if semantic.get("status") != "MEASURED":
        items.append("人工指标：标注模板已生成，但尚未填写")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="在线评测 Runner（真实模型 + 真实搜索）")
    parser.add_argument("--mode", choices=["full", "web-only"], default="full")
    parser.add_argument("--suite", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--case-ids", help="逗号分隔的 case_id 子集")
    parser.add_argument("--limit", type=int, help="只跑前 N 条")
    parser.add_argument(
        "--allow-offline-smoke",
        action="store_true",
        help="显式允许用离线 Provider 验证 Runner；产物标记 OFFLINE_SMOKE，不能冒充在线结果",
    )
    parser.add_argument("--sample-claims", type=int, default=3)
    parser.add_argument("--sample-seed", type=int, default=0)
    parser.add_argument("--fail-under-completion", type=float)
    args = parser.parse_args()

    settings = Settings()
    requested_mode = args.mode
    suite_path = args.suite or (
        DEFAULT_WEB_ONLY_SUITE if requested_mode == "web-only" else DEFAULT_SUITE
    )
    suite = load_suite(suite_path)
    expected_route_mode = "web_only" if requested_mode == "web-only" else "both"
    if suite.route_mode != expected_route_mode:
        print(
            f"任务集 route_mode={suite.route_mode!r} 与 --mode={requested_mode!r} 不一致",
            file=sys.stderr,
        )
        return 2

    cases = suite.cases
    if args.case_ids:
        wanted = {c.strip() for c in args.case_ids.split(",") if c.strip()}
        unknown = sorted(wanted - {c.case_id for c in cases})
        if unknown:
            print(f"未知 case_id: {unknown}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c.case_id in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("没有可运行的用例", file=sys.stderr)
        return 2

    run_id = args.run_id or _build_run_id()
    run_dir = args.output_dir or (PROJECT_ROOT / "eval" / "runs" / run_id)
    problems = preflight(settings, mode=requested_mode, tasks=cases)
    if problems and not args.allow_offline_smoke:
        _write_not_run(
            run_dir, settings, problems,
            requested_mode=requested_mode,
            suite_path=suite_path,
            case_ids=[c.case_id for c in cases],
        )
        print("在线评测未运行（NOT_RUN）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"已写入 {run_dir / 'NOT_RUN.json'}；未调用任何模型或搜索 API。", file=sys.stderr)
        return 2

    execution_mode = "offline_smoke" if args.allow_offline_smoke else "online"
    evaluation_scope = "web_only_subset" if requested_mode == "web-only" else "full_dual_route"
    if args.allow_offline_smoke:
        settings = settings.model_copy(
            update={
                "mode": "offline",
                "search_provider": "demo",
                "llm_api_key": None,
                "kb_enabled": requested_mode == "full",
            }
        )
    elif requested_mode == "web-only":
        settings = settings.model_copy(update={"kb_enabled": False})

    run_dir.mkdir(parents=True, exist_ok=True)
    settings = settings.model_copy(update={
        "memory_db_path": run_dir / "runtime" / run_id / "memory.sqlite3",
        "trace_db_path": run_dir / "runtime" / run_id / "trace.sqlite3",
    })

    config_summary = _config_summary(settings)
    warnings: list[str] = []
    if config_summary["kb_enabled"] and config_summary["kb_corpus_is_demo_snapshot"]:
        warnings.append(
            "KB 使用内置演示语料（data/corpus）；在线结果中的 kb 证据不代表真实知识库覆盖。"
            "若需真实知识库，请设置 DR_KB_CORPUS_DIR。"
        )
    if evaluation_scope == "web_only_subset":
        warnings.append(
            "本次是 Web-only 子集，不是原 20 条全量双路评测；统计分母只覆盖所选 web 用例。"
        )
    kb_manifest = (
        corpus_inventory(settings.kb_corpus_dir)
        if settings.kb_enabled else
        {"enabled": False, "reason": "web-only 模式显式关闭 KB 路"}
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "online_evaluation",
        "status": "RUNNING",
        "execution_mode": execution_mode,
        "evaluation_scope": evaluation_scope,
        "route_mode": suite.route_mode,
        "run_id": run_id,
        "started_at": now_iso(),
        "finished_at": None,
        "git_commit": git_commit(PROJECT_ROOT),
        "git_dirty": git_dirty(PROJECT_ROOT),
        "source_manifest": source_manifest(PROJECT_ROOT),
        "suite": {
            "path": _display_path(suite_path),
            "suite_id": suite.suite_id,
            "version": suite.version,
            "route_mode": suite.route_mode,
            "parent_suite_id": suite.parent_suite_id,
            "sha256": suite_sha256(suite_path) or sha256_file(suite_path),
            "total_cases": len(suite.cases),
            "selected_cases": len(cases),
            "selected_case_ids": [c.case_id for c in cases],
        },
        "config": config_summary,
        "kb_corpus_manifest": kb_manifest,
        "warnings": warnings,
        "providers": {
            "llm": "openai_compatible" if execution_mode == "online" else "heuristic_offline",
            "search": settings.search_provider,
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
    }
    _write_json(run_dir / "run_manifest.json", manifest)

    records: list[dict] = []
    try:
        runtime = build_default_app(settings)
    except Exception as exc:  # noqa: BLE001 —— 装配失败必须显式记录
        manifest.update({
            "status": "FAILED",
            "finished_at": now_iso(),
            "error": f"{type(exc).__name__}: {exc}",
            "stack": traceback.format_exc(limit=8),
        })
        _write_json(run_dir / "run_manifest.json", manifest)
        print(f"运行时装配失败：{exc}", file=sys.stderr)
        return 1

    try:
        for i, task in enumerate(cases, start=1):
            case_run_id = f"{run_id}-{_safe_name(task.case_id)}"
            case_dir = run_dir / "cases" / _safe_name(task.case_id)
            print(f"[{i}/{len(cases)}] {task.case_id} ...", flush=True)
            record = _run_one(
                runtime, task, run_id=case_run_id, case_dir=case_dir, settings=settings
            )
            records.append(record)
            checks = sum(1 for a in record["assertions"] if a["passed"])
            print(
                f"    -> {record['status']}, 检查 {checks}/{len(record['assertions'])}, "
                f"证据 {record['evidence_count']}, 延迟 {record['latency_ms']:.0f}ms, "
                f"失败类型 {record['failure_types'] or '无'}",
                flush=True,
            )
    finally:
        runtime.close()

    manifest.update({"status": "COMPLETED", "finished_at": now_iso()})
    _write_json(run_dir / "run_manifest.json", manifest)

    structural = summarize_structural(records)
    semantic = {
        "status": "NOT_MEASURED",
        "reason": (
            "本文件不自动合并人工标签；请填写 annotation_template.csv 后运行 "
            "python -m eval.annotation summarize，人工指标写入独立文件"
        ),
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "online_evaluation",
        "run_id": run_id,
        "execution_mode": execution_mode,
        "evaluation_scope": evaluation_scope,
        "route_mode": suite.route_mode,
        "status": manifest["status"],
        "generated_at": now_iso(),
        "git_commit": manifest["git_commit"],
        "git_dirty": manifest["git_dirty"],
        "source_manifest": manifest["source_manifest"],
        "selected_case_ids": [c.case_id for c in cases],
        "kb_corpus_manifest": manifest["kb_corpus_manifest"],
        "automatic_metrics": structural,
        "semantic_metrics": semantic,
        "unmeasured": _unmeasured_items(manifest, structural, semantic),
        "cases": [
            {
                "case_id": r["case_id"],
                "status": r["status"],
                "failure_types": r["failure_types"],
                "all_checks_passed": r["all_checks_passed"],
                "result": r["artifacts"]["report_json"],
            }
            for r in records
        ],
    }
    _write_json(run_dir / "summary.json", summary)
    (run_dir / "summary.md").write_text(
        _render_summary_md(summary, manifest, records), encoding="utf-8"
    )
    try:
        write_template(run_dir, run_dir / "annotation_template.csv",
                       per_case=args.sample_claims, seed=args.sample_seed)
    except FileNotFoundError:
        pass

    completion = structural["pipeline_completion_rate"]["value"]
    print(
        f"\n完成：{structural['counts']['report_produced']}/{structural['counts']['total']} 产出报告；"
        f"自动检查全通过 {structural['counts']['all_checks_passed']}/{structural['counts']['total']}。"
    )
    print(f"产物目录：{run_dir}")
    if completion is None:
        return 1
    if args.fail_under_completion is not None and completion < args.fail_under_completion:
        print(
            f"完成率 {completion:.2%} 低于阈值 {args.fail_under_completion:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())