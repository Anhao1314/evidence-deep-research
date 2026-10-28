"""评测指标纯函数：结构合法性、引用完整性、来源解析、延迟与失败分类。

边界约定：
- 这里只计算可自动判定的结构指标，不判断结论是否事实正确；
- 无法从产物计算的值返回 None，并由上层写成 NOT_MEASURED；
- 所有比率都保留分子/分母；
- pre-guard 指标观察模型原始引用行为，post-guard 指标评价最终输出；
  两者不可混用，也不得称为语义支撑率。
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.schema.evidence import Evidence
from app.schema.guard import GuardAudit
from app.schema.report import Report

_CITE_RE = re.compile(r"\[(E\d+)\]")

FAILURE_TYPE_ZH = {
    "pipeline_exception": "流程异常",
    "no_report": "未产出报告",
    "no_evidence": "无证据",
    "route_not_covered": "路线未覆盖",
    "post_guard_invalid_citation": "最终引用仍非法",
    "source_unresolved": "来源未解析",
    "gaps_not_stated": "未声明信息缺口",
    "missing_object": "研究对象缺失",
    "tool_or_llm_degraded": "工具/模型降级",
    "tool_retry": "工具重试",
    "guard_rewrite_rejected": "受限重写被拒绝",
    "guard_claim_quarantined": "结论被隔离",
    "guard_fallback": "Guard 回退模板",
    "guard_fail_closed": "Guard fail-closed",
    "structural_check_failed": "结构检查未通过",
}


def citation_counts(markdown: str, valid_ids: set[str]) -> dict[str, Any]:
    """统计文本中 [E#] 的出现次数：合法率按“出现次数”而非去重编号计算。"""
    found = _CITE_RE.findall(markdown or "")
    invalid = [i for i in found if i not in valid_ids]
    valid = len(found) - len(invalid)
    return {
        "total": len(found),
        "valid": valid,
        "invalid": len(invalid),
        "validity_rate": (valid / len(found)) if found else None,
        "invalid_ids": sorted(set(invalid)),
    }


def citation_coverage(report: Report) -> float | None:
    """结论区域被引用到的证据数 / 证据总数。

    只使用 report.citations；该字段由结论区域抽取，排除证据明细/参考来源。
    """
    if not report.evidences:
        return None
    cited = {eid for c in report.citations for eid in c.evidence_ids}
    valid = {e.evidence_id for e in report.evidences}
    return len(cited & valid) / len(valid)


def source_resolution(report: Report) -> dict[str, Any]:
    """来源解析与引用完整率。

    - 需要来源编号存在于 source 清单；
    - 需要 URL 或 doc_id；
    - 需要本次检索快照/知识库文档的 content_hash；
    - resolution 必须是 kb_document 或 retrieval_snapshot；
    - URL 当前是否可打开单独记录，评测不做网络探测时写 NOT_MEASURED。
    """
    source_ids = {s.source_id for s in report.sources}
    resolved_sources = [
        s for s in report.sources
        if (s.url or s.doc_id) and s.content_hash
        and s.resolution in {"kb_document", "retrieval_snapshot"}
    ]
    unresolved_sources = [s.source_id for s in report.sources if s not in resolved_sources]
    missing_hash = [s.source_id for s in report.sources if not s.content_hash]
    missing_locator = [s.source_id for s in report.sources if not s.locator]
    orphan_sources = sorted({e.source.source_id for e in report.evidences} - source_ids)
    resolved_evidence = [
        e for e in report.evidences
        if e.source.source_id in source_ids
        and (e.source.url or e.source.doc_id)
        and e.source.content_hash
        and e.source.resolution in {"kb_document", "retrieval_snapshot"}
    ]
    total = len(report.evidences)
    return {
        "evidence_total": total,
        "evidence_resolved": len(resolved_evidence),
        "source_reference_integrity_rate": (len(resolved_evidence) / total) if total else None,
        "source_total": len(report.sources),
        "source_resolved": len(resolved_sources),
        "source_unresolved_ids": unresolved_sources,
        "source_missing_content_hash": missing_hash,
        "source_missing_locator": missing_locator,
        "orphan_evidence_sources": orphan_sources,
        "url_reachable": {
            "status": "NOT_MEASURED",
            "reason": "评测不做网络探测；URL 可访问性不等于来源内容可回溯",
        },
    }


def guard_metrics(report: Report) -> dict[str, Any]:
    """pre/post guard 引用指标与重写/隔离计数。"""
    valid = {e.evidence_id for e in report.evidences}
    raw = report.raw_markdown or report.markdown
    pre = citation_counts(raw, valid)
    post = citation_counts(report.markdown, valid)
    audit: GuardAudit | None = report.guard_audit
    return {
        "pre_guard_citation_total": pre["total"],
        "pre_guard_citation_valid": pre["valid"],
        "pre_guard_citation_invalid": pre["invalid"],
        "pre_guard_citation_validity_rate": pre["validity_rate"],
        "post_guard_citation_total": post["total"],
        "post_guard_citation_valid": post["valid"],
        "post_guard_citation_invalid": post["invalid"],
        "post_guard_citation_integrity_rate": post["validity_rate"],
        "guard_ran": bool(audit and audit.ran),
        "guard_rewrite_attempted": bool(audit and audit.rewrite_attempted),
        "guard_rewrite_accepted": audit.rewrite_accepted if audit else None,
        "guard_claims_checked": audit.claims_checked if audit else 0,
        "guard_claims_quarantined": audit.claims_quarantined if audit else 0,
        "guard_fallback_used": bool(audit and audit.fallback_used),
        "guard_fail_closed": bool(audit and audit.fail_closed),
    }


def route_coverage(expected_routes: list[str], used_routes: list[str]) -> float | None:
    """期望路线中被实际产出证据的路线覆盖比例；expected 为空时不计入。"""
    expected = set(expected_routes)
    if not expected:
        return None
    return len(expected & set(used_routes)) / len(expected)


def dimension_coverage(
    expected_dimensions: list[str],
    expected_objects: list[str],
    evidences: list[Evidence],
) -> float | None:
    """期望维度被证据覆盖的比例；指定对象时只统计该对象的证据。"""
    expected = set(expected_dimensions)
    if not expected:
        return None
    objects = {o.lower() for o in expected_objects}
    relevant = [e for e in evidences if not objects or e.object_name.lower() in objects]
    covered = {e.aspect for e in relevant}
    return len(covered & expected) / len(expected)


def percentile(values: list[float], p: float) -> float | None:
    """nearest-rank 百分位（p ∈ (0, 100]）。空列表返回 None。"""
    if not values:
        return None
    if not 0 < p <= 100:
        raise ValueError("p 必须在 (0, 100] 之间")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def latency_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None,
                "method": "nearest-rank over per-case wall-clock latency"}
    return {
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "min_ms": round(min(values), 2),
        "max_ms": round(max(values), 2),
        "method": "nearest-rank over per-case wall-clock latency",
    }


def token_usage_from_events(events: list[Any]) -> dict[str, int]:
    """从 trace usage 事件汇总 token；usage_events=0 表示没有可用 Token 数据。"""
    prompt = completion = total = usage_events = 0
    for e in events:
        payload = getattr(e, "payload", None) or {}
        if getattr(e, "event_type", "") != "usage" and not isinstance(e, dict):
            continue
        if isinstance(e, dict):
            if e.get("event_type") != "usage":
                continue
            payload = e.get("payload") or {}
            event_total = int(e.get("token_usage") or 0)
        else:
            event_total = int(getattr(e, "token_usage", 0) or 0)
        usage_events += 1
        prompt += int(payload.get("prompt_tokens") or 0)
        completion += int(payload.get("completion_tokens") or 0)
        total += event_total or int(payload.get("total_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": total, "events": usage_events}


def cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_1m: float | None,
    output_price_per_1m: float | None,
) -> float | None:
    """只有输入/输出单价都配置时才计算成本；否则 NOT_MEASURED。"""
    if input_price_per_1m is None or output_price_per_1m is None:
        return None
    return round(
        prompt_tokens / 1_000_000 * input_price_per_1m
        + completion_tokens / 1_000_000 * output_price_per_1m,
        6,
    )


def classify_failure_types(
    *,
    error: str | None,
    has_report: bool,
    evidence_count: int,
    expected_routes: list[str],
    routes_used: list[str],
    post_guard_invalid_citations: int,
    source_rate: float | None,
    information_gaps: list[str],
    must_state_gaps: bool,
    expected_objects: list[str],
    report_markdown: str,
    trace_types: set[str],
    guard_audit: GuardAudit | None = None,
    assertions: list[dict] | None = None,
) -> list[str]:
    """把失败归因到可复核的类型；一个 case 可同时有多个失败类型。"""
    types: set[str] = set()
    if error:
        types.add("pipeline_exception")
    if not has_report:
        types.add("no_report")
        return sorted(types)

    if evidence_count == 0:
        types.add("no_evidence")
    if set(expected_routes) - set(routes_used):
        types.add("route_not_covered")
    if post_guard_invalid_citations:
        types.add("post_guard_invalid_citation")
    if source_rate is not None and source_rate < 1.0:
        types.add("source_unresolved")
    if must_state_gaps and not information_gaps:
        types.add("gaps_not_stated")
    if [o for o in expected_objects if o not in (report_markdown or "")]:
        types.add("missing_object")
    if "degrade" in trace_types:
        types.add("tool_or_llm_degraded")
    if "retry" in trace_types:
        types.add("tool_retry")
    if guard_audit is not None:
        if guard_audit.rewrite_attempted and guard_audit.rewrite_accepted is False:
            types.add("guard_rewrite_rejected")
        if guard_audit.claims_quarantined > 0:
            types.add("guard_claim_quarantined")
        if guard_audit.fallback_used:
            types.add("guard_fallback")
        if guard_audit.fail_closed:
            types.add("guard_fail_closed")
    if any(not a.get("passed", True) for a in assertions or []):
        types.add("structural_check_failed")
    return sorted(types)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _rate(numerator: int, denominator: int, definition: str) -> dict[str, Any]:
    return {
        "value": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "definition": definition,
    }


def summarize_structural(case_records: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合结构指标；所有分母显式给出，N/A 不写成 0。"""
    total = len(case_records)
    completed = [r for r in case_records if r.get("has_report")]
    citation_cov_vals = [r["citation_coverage"] for r in case_records
                         if r.get("citation_coverage") is not None]
    route_vals = [r["route_coverage"] for r in case_records if r.get("route_coverage") is not None]
    dim_vals = [r["dimension_coverage"] for r in case_records if r.get("dimension_coverage") is not None]
    latencies_completed = [r["latency_ms"] for r in case_records
                           if r.get("has_report") and r.get("latency_ms") is not None]
    latencies_all = [r["latency_ms"] for r in case_records if r.get("latency_ms") is not None]

    pre_total = sum(r.get("pre_guard_citation_total") or 0 for r in case_records)
    pre_valid = sum(r.get("pre_guard_citation_valid") or 0 for r in case_records)
    post_total = sum(r.get("post_guard_citation_total") or 0 for r in case_records)
    post_valid = sum(r.get("post_guard_citation_valid") or 0 for r in case_records)
    rewrite_cases = sum(1 for r in case_records if r.get("guard_rewrite_attempted"))
    invalid_pre_cases = sum(1 for r in case_records if (r.get("pre_guard_citation_invalid") or 0) > 0)
    guard_checked = sum(r.get("guard_claims_checked") or 0 for r in case_records)
    guard_quarantined = sum(r.get("guard_claims_quarantined") or 0 for r in case_records)
    fallback_cases = sum(1 for r in case_records if r.get("guard_fallback_used"))
    guard_ran_cases = sum(1 for r in case_records if r.get("guard_ran"))

    source_total = sum((r.get("source_resolution") or {}).get("evidence_total", 0)
                       for r in case_records)
    source_resolved = sum((r.get("source_resolution") or {}).get("evidence_resolved", 0)
                          for r in case_records)
    src_sources_total = sum((r.get("source_resolution") or {}).get("source_total", 0)
                            for r in case_records)
    src_sources_resolved = sum((r.get("source_resolution") or {}).get("source_resolved", 0)
                               for r in case_records)
    missing_hash = sum(len((r.get("source_resolution") or {}).get("source_missing_content_hash", []))
                       for r in case_records)
    missing_locator = sum(len((r.get("source_resolution") or {}).get("source_missing_locator", []))
                          for r in case_records)
    orphan_sources = sum(len((r.get("source_resolution") or {}).get("orphan_evidence_sources", []))
                         for r in case_records)

    failure_dist: dict[str, int] = {}
    for r in case_records:
        for t in r.get("failure_types") or []:
            failure_dist[t] = failure_dist.get(t, 0) + 1

    token_prompt = sum((r.get("token_usage") or {}).get("prompt_tokens", 0) for r in case_records)
    token_completion = sum((r.get("token_usage") or {}).get("completion_tokens", 0) for r in case_records)
    token_total = sum((r.get("token_usage") or {}).get("total_tokens", 0) for r in case_records)
    usage_events = sum((r.get("token_usage") or {}).get("events", 0) for r in case_records)
    costs = [r["cost_usd"] for r in case_records if r.get("cost_usd") is not None]

    return {
        "counts": {
            "total": total,
            "report_produced": len(completed),
            "report_missing": total - len(completed),
            "all_checks_passed": sum(1 for r in case_records if r.get("all_checks_passed")),
        },
        "pipeline_completion_rate": {
            **_rate(len(completed), total,
                    "产出报告且流程未抛异常的 case 数 / 总 case 数"),
        },
        "all_checks_pass_rate": {
            **_rate(sum(1 for r in case_records if r.get("all_checks_passed")), total,
                    "全部自动断言通过的 case 数 / 总 case 数；不等于事实正确率"),
        },
        "pre_guard_citation_valid_rate": {
            **_rate(pre_valid, pre_total,
                    "原始模型输出中合法 [E#] 出现次数 / 原始引用总出现次数；观察模型原始行为"),
        },
        "post_guard_citation_integrity_rate": {
            **_rate(post_valid, post_total,
                    "最终报告中合法 [E#] 出现次数 / 最终引用总出现次数；评价系统最终输出"),
        },
        "guard_rewrite_rate": {
            **_rate(rewrite_cases, invalid_pre_cases,
                    "触发受限重写的 case 数 / 原始输出含非法引用的 case 数"),
        },
        "guard_claim_quarantine_rate": {
            **_rate(guard_quarantined, guard_checked,
                    "被隔离结论数 / Guard 检查结论数"),
        },
        "guard_fallback_rate": {
            **_rate(fallback_cases, guard_ran_cases,
                    "使用模板回退的 case 数 / Guard 运行过的 case 数"),
        },
        "source_reference_integrity_rate": {
            **_rate(source_resolved, source_total,
                    "来源编号可解析且带定位/内容哈希的证据数 / 证据总数"),
        },
        "source_resolution": {
            "source_total": src_sources_total,
            "source_resolved": src_sources_resolved,
            "source_unresolved": src_sources_total - src_sources_resolved,
            "evidence_total": source_total,
            "evidence_resolved": source_resolved,
            "missing_content_hash": missing_hash,
            "missing_locator": missing_locator,
            "orphan_evidence_sources": orphan_sources,
            "url_reachable": {
                "status": "NOT_MEASURED",
                "reason": "评测不做网络探测；URL 可访问性不等于内容可回溯",
            },
        },
        "citation_coverage": {
            "mean": _mean(citation_cov_vals),
            "n": len(citation_cov_vals),
            "definition": "结论区域被引用证据数 / 证据总数（按 case 求均值；排除证据明细/参考来源）",
        },
        "route_coverage": {
            "mean": _mean(route_vals),
            "n": len(route_vals),
            "strict_rate": (
                round(sum(1 for v in route_vals if v == 1.0) / len(route_vals), 4)
                if route_vals else None
            ),
            "definition": "期望路线中被实际产出证据的路线覆盖比例；expected=any 不计入",
        },
        "dimension_coverage": {
            "mean": _mean(dim_vals),
            "n": len(dim_vals),
            "definition": "期望维度被证据覆盖的比例（按 case 求均值）",
        },
        "latency": {**latency_stats(latencies_completed), "scope": "只统计产出报告的 case"},
        "latency_all_cases": {
            **latency_stats(latencies_all),
            "scope": "包含失败 case 的全部 wall-clock 延迟",
        },
        "token_usage": {
            "prompt_tokens": token_prompt,
            "completion_tokens": token_completion,
            "total_tokens": token_total,
            "usage_events": usage_events,
            "note": "来自 trace usage 事件；usage_events=0 表示没有可用 Token 数据",
        },
        "cost": {
            "usd_sum": round(sum(costs), 6) if costs else None,
            "n": len(costs),
            "status": "MEASURED" if costs else "NOT_MEASURED",
            "note": "需同时配置输入/输出单价且存在 usage 事件",
        },
        "failure_type_distribution": {
            "counts": dict(sorted(failure_dist.items(), key=lambda kv: (-kv[1], kv[0]))),
            "zh": {k: FAILURE_TYPE_ZH.get(k, k) for k in failure_dist},
            "denominator": total,
        },
    }