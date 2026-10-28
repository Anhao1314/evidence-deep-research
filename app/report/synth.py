"""离线报告合成：模板化生成 + 引用强制绑定。

离线模式不经 LLM，结论句直接由证据条目构造，引用 [E#] 与证据一一对应，
离线模板本身不会引入不存在的编号；在线模式的 LLM 生成由 fail-closed
引用 Guard 处理（重写校验、结论隔离、必要时模板回退），复用本模块的
collect_sources / 编号体系。

记忆系统注入的偏好（report_language）在此真实生效——跨会话偏好影响输出，
是 M2 记忆闭环的可见证据。
"""

from collections import OrderedDict

from app.domain_registry import ASPECT_ZH
from app.llm.base import ComposeContext, ComposeResult
from app.schema.evidence import Evidence, Source
from app.schema.report import Citation

# 维度展示顺序：性能 -> 生态 -> 成本 -> 成熟度 -> 其他
_ASPECT_ORDER = list(ASPECT_ZH)

_T = {
    "zh": {
        "title": "技术选型研究报告",
        "question": "研究问题",
        "summary_h": "结论摘要",
        "constraint_h": "约束核对",
        "constraint_conclusion_h": "约束结论",
        "status_satisfied": "满足",
        "status_violated": "违反",
        "status_unknown": "无法核验",
        "table_h": "分维度对比",
        "dimension_h": "维度",
        "detail_h": "证据明细",
        "gaps_h": "信息缺口",
        "sources_h": "参考来源",
        "no_gaps": "无（本轮检索已覆盖计划的全部维度）",
        "empty_summary": "本轮检索未获得可用证据，无法给出有依据的结论。",
        "no_evidence_gap": "全部检索路均未命中",
        "general": "综合结论",
        "no_evidence_cell": "—（本轮无证据）",
    },
    "en": {
        "title": "Technology Selection Research Report",
        "question": "Research question",
        "summary_h": "Executive Summary",
        "constraint_h": "Constraint Check",
        "constraint_conclusion_h": "Constraint Conclusion",
        "status_satisfied": "satisfied",
        "status_violated": "violated",
        "status_unknown": "unknown",
        "table_h": "Dimension Comparison",
        "dimension_h": "Dimension",
        "detail_h": "Evidence Details",
        "gaps_h": "Information Gaps",
        "sources_h": "Sources",
        "no_gaps": "None - all planned aspects are covered by this run",
        "empty_summary": "No usable evidence was retrieved in this run; no grounded conclusion can be drawn.",
        "no_evidence_gap": "No hits from any retrieval route",
        "general": "General",
        "no_evidence_cell": "- (no evidence this run)",
    },
}

_ASPECT_LABEL_EN = {
    "performance": "Performance",
    "ecosystem": "Ecosystem",
    "cost": "Cost",
    "maturity": "Maturity",
}


def _labels(preferences: dict[str, str]) -> dict:
    lang = preferences.get("report_language", "zh")
    labels = _T.get(lang, _T["zh"])
    aspect_map = ASPECT_ZH if lang == "zh" else _ASPECT_LABEL_EN
    return labels, aspect_map


def _strip_citations(text: str) -> str:
    """摘要纯文本：剥离 [E#] 引用标记与残留分隔符。"""
    return text.split("[E")[0].strip().rstrip("；;，,").rstrip("。") + "。"


def collect_sources(evidences: list[Evidence]) -> list[Source]:
    """按来源实体（url / doc_id）去重，合并段落定位。"""
    merged: OrderedDict[str, Source] = OrderedDict()
    for ev in evidences:
        s = ev.source
        key = f"{s.kind}:{s.url or s.doc_id}"
        if key not in merged:
            merged[key] = s.model_copy(deep=True)
        else:
            existing = merged[key]
            if s.locator and s.locator not in (existing.locator or ""):
                parts = [p for p in (existing.locator or "").split("/") if p]
                parts.append(s.locator)
                existing.locator = "/".join(parts[:4])
    return list(merged.values())


def render_compose_result(ctx: ComposeContext, registry=None) -> ComposeResult:
    evidences = list(ctx.evidences)
    sources = collect_sources(evidences)
    t, aspect_label = _labels(ctx.preferences)
    _display = (lambda name: registry.display_object(name) if registry else name)

    if not evidences:
        return _render_empty(ctx, t, sources, _display)

    q = ctx.question.lower()
    groups = _group_by_object(evidences)
    # 对象展示顺序：按问题中出现的先后，再按名称兜底
    groups = OrderedDict(
        sorted(groups.items(), key=lambda kv: (q.find(kv[0]) if kv[0] and q.find(kv[0]) != -1 else 10**9, kv[0]))
    )
    citations: list[Citation] = []
    summary_parts: list[str] = []

    lines: list[str] = [f"# {t['title']}", "", f"> {t['question']}：{ctx.question}", ""]

    # 结论摘要：每对象一段
    lines += [f"## {t['summary_h']}", ""]
    for obj, evs in groups.items():
        citations.append(Citation(location=f"summary#{obj or 'general'}", evidence_ids=[e.evidence_id for e in evs]))
        points, plain = [], []
        for aspect, aspect_evs in _group_by_aspect(evs).items():
            top = aspect_evs[0]
            label = aspect_label.get(aspect, aspect)
            points.append(f"{label}：{top.claim} [{top.evidence_id}]")
            plain.append(f"{label}：{_strip_citations(top.claim)}")
        lines.append(f"**{_display(obj) if obj else t['general']}**：{'；'.join(points)}。")
        lines.append("")
        summary_parts.append(f"{_display(obj) if obj else t['general']}：" + "；".join(plain) + "。")

    # 约束核对：约束、相关证据、可判断/无法判断的部分分开呈现
    if ctx.constraints:
        constraint_lines, constraint_conclusion = _render_constraints(
            ctx, citations, _display, t
        )
        lines += constraint_lines
        if constraint_conclusion:
            summary_parts.append(constraint_conclusion)

    # 分维度对比表（仅当存在 >=2 个研究对象）
    if _object_count(groups) >= 2:
        lines += _render_comparison_table(groups, citations, _display, t, aspect_label)

    # 证据明细
    lines += [f"## {t['detail_h']}", ""]
    for ev in evidences:
        lines.append(f"- **[{ev.evidence_id}]** {ev.claim}（来源 [{ev.source.source_id}]：{ev.source.ref}）")
    lines.append("")

    # 信息缺口
    lines += [f"## {t['gaps_h']}", ""]
    if ctx.information_gaps:
        lines += [f"- {g}" for g in ctx.information_gaps]
    else:
        lines.append(f"- {t['no_gaps']}")
    lines.append("")

    # 参考来源
    lines += [f"## {t['sources_h']}", ""]
    for s in sources:
        lines.append(f"- **[{s.source_id}]** {s.title} — {s.ref}")
    lines.append("")

    return ComposeResult(markdown="\n".join(lines), summary=" ".join(summary_parts),
                         citations=citations, sources=sources)


def _render_empty(ctx: ComposeContext, t: dict, sources: list, display) -> ComposeResult:
    lines = [f"# {t['title']}", "", f"> {t['question']}：{ctx.question}", ""]
    lines += [f"## {t['summary_h']}", "", t["empty_summary"], ""]
    citations: list[Citation] = []
    summary = t["empty_summary"]
    if ctx.constraints:
        constraint_lines, conclusion = _render_constraints(ctx, citations, display, t)
        lines += constraint_lines
        if conclusion:
            summary = f"{summary} {conclusion}"
    lines += [f"## {t['gaps_h']}", ""]
    lines += [f"- {g}" for g in ctx.information_gaps] or [f"- {t['no_evidence_gap']}"]
    if sources:
        lines += ["", f"## {t['sources_h']}", ""]
        lines += [f"- **[{s.source_id}]** {s.title} — {s.ref}" for s in sources]
    return ComposeResult(markdown="\n".join(lines), summary=summary,
                         citations=citations, sources=sources)


def _render_constraints(ctx: ComposeContext, citations: list[Citation], display, t: dict):
    """渲染约束、每个对象的核验状态、相关证据与无法判断的部分。"""
    summary = ctx.constraint_summary
    status_labels = {
        "satisfied": t["status_satisfied"],
        "violated": t["status_violated"],
        "unknown": t["status_unknown"],
    }
    finding_by_key = {
        (f.constraint_id, f.object_name): f for f in (summary.findings if summary else [])
    }
    lines = [f"## {t['constraint_h']}", ""]
    for constraint in ctx.constraints:
        parts: list[str] = []
        if summary:
            for obj in summary.checked_objects:
                finding = finding_by_key.get((constraint.constraint_id, obj))
                if finding is None:
                    continue
                parts.append(
                    f"{display(obj)} {status_labels.get(finding.status, finding.status)}"
                    f"（{finding.detail}）"
                )
                if finding.evidence_ids:
                    citations.append(Citation(
                        location=f"constraint#{constraint.constraint_id}#{obj}",
                        evidence_ids=finding.evidence_ids,
                    ))
        if not parts:
            parts.append(t["status_unknown"])
        lines.append(f"- [{constraint.constraint_id}] {constraint.text}：" + "；".join(parts))
    conclusion = _constraint_conclusion(ctx, display, t)
    if conclusion:
        lines += ["", f"**{t['constraint_conclusion_h']}**：{conclusion}", ""]
    else:
        lines.append("")
    return lines, conclusion


def _constraint_conclusion(ctx: ComposeContext, display, t: dict) -> str:
    summary = ctx.constraint_summary
    if summary is None:
        return "未进行结构化约束核验。"
    if summary.verdict == "not_checked":
        return ""
    if summary.verdict == "recommend":
        chosen = "、".join(display(o) for o in summary.recommended_objects)
        excluded = "；".join(
            f"{display(obj)} 违反 {', '.join(ids)}" for obj, ids in summary.excluded.items()
        )
        return f"在给定约束下，唯一满足全部可核验约束的是 {chosen}；{excluded}。"
    if summary.verdict == "no_solution":
        return "没有任何对象同时满足全部约束；需要放宽至少一项约束或补充新方案。"
    if summary.verdict == "conditional":
        chosen = "、".join(display(o) for o in summary.recommended_objects)
        return f"多个对象满足已知约束（{chosen}），无法据此唯一推荐；需要进一步比较。"
    return "约束核验信息不足，无法判断是否满足；见信息缺口。"


def _group_by_object(evidences: list[Evidence]) -> "OrderedDict[str, list[Evidence]]":
    groups: OrderedDict[str, list[Evidence]] = OrderedDict()
    for ev in sorted(evidences, key=lambda e: e.evidence_id):
        groups.setdefault(ev.object_name, []).append(ev)
    return groups


def _group_by_aspect(evidences: list[Evidence]) -> "OrderedDict[str, list[Evidence]]":
    groups: OrderedDict[str, list[Evidence]] = OrderedDict()
    ordered = sorted(
        evidences,
        key=lambda e: (_ASPECT_ORDER.index(e.aspect) if e.aspect in _ASPECT_ORDER else 99, e.evidence_id),
    )
    for ev in ordered:
        groups.setdefault(ev.aspect, []).append(ev)
    return groups


def _object_count(groups: "OrderedDict[str, list[Evidence]]") -> int:
    return sum(1 for obj in groups if obj)


def _render_comparison_table(
    groups: "OrderedDict[str, list[Evidence]]",
    citations: list[Citation],
    display=lambda name: name,
    t: dict | None = None,
    aspect_label: dict | None = None,
) -> list[str]:
    """对比表：行=维度，列=对象；单元格=该对象该维度最高分证据的论断+引用。"""
    t = t or _T["zh"]
    aspect_label = aspect_label or ASPECT_ZH
    objects = [obj for obj in groups if obj]
    aspects: list[str] = []
    for obj in objects:
        for a in _group_by_aspect(groups[obj]):
            if a not in aspects:
                aspects.append(a)
    aspects.sort(key=lambda a: (_ASPECT_ORDER.index(a) if a in _ASPECT_ORDER else 99))

    shown = [display(o) for o in objects]
    lines = [f"## {t['table_h']}", "", "| " + t["dimension_h"] + " | " + " | ".join(shown) + " |",
             "|---|" + "---|" * len(objects)]
    for aspect in aspects:
        row = [aspect_label.get(aspect, aspect)]
        for obj in objects:
            evs = _group_by_aspect(groups[obj]).get(aspect)
            if evs:
                top = evs[0]
                row.append(f"{top.claim} [{top.evidence_id}]")
                citations.append(
                    Citation(location=f"table#{aspect}#{obj}", evidence_ids=[e.evidence_id for e in evs])
                )
            else:
                row.append(t["no_evidence_cell"])
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines
