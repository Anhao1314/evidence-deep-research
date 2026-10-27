"""离线报告合成：模板化生成 + 引用强制绑定。

离线模式不经 LLM，结论句直接由证据条目构造，引用 [E#] 与证据一一对应，
结构上不可能出现编造引用；在线模式的 LLM 生成在 M3 增加引用校验 guard
（非法 [E#] 触发受限重写），复用本模块的 collect_sources / 编号体系。
"""

from collections import OrderedDict

from app.domain_registry import ASPECT_ZH
from app.llm.base import ComposeContext, ComposeResult
from app.schema.evidence import Evidence, Source
from app.schema.report import Citation

# 维度展示顺序：性能 -> 生态 -> 成本 -> 成熟度 -> 其他
_ASPECT_ORDER = list(ASPECT_ZH)


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

    if not evidences:
        md = _render_empty(ctx.question, ctx.information_gaps)
        return ComposeResult(markdown=md, citations=[], sources=sources)

    groups = _group_by_object(evidences)
    # 对象展示顺序：按问题中出现的先后，再按名称兜底
    q = ctx.question.lower()
    groups = OrderedDict(
        sorted(groups.items(), key=lambda kv: (q.find(kv[0]) if kv[0] and q.find(kv[0]) != -1 else 10**9, kv[0]))
    )
    _display = (lambda name: registry.display_object(name) if registry else name)
    citations: list[Citation] = []

    lines: list[str] = [f"# 技术选型研究报告", "", f"> 研究问题：{ctx.question}", ""]

    # 结论摘要：每对象一段
    lines += ["## 结论摘要", ""]
    for obj, evs in groups.items():
        summary_ids = [e.evidence_id for e in evs]
        citations.append(Citation(location=f"summary#{obj or 'general'}", evidence_ids=summary_ids))
        points = []
        for aspect, aspect_evs in _group_by_aspect(evs).items():
            top = aspect_evs[0]
            label = ASPECT_ZH.get(aspect, aspect)
            points.append(f"{label}：{top.claim} [{top.evidence_id}]")
        lines.append(f"**{_display(obj) if obj else '综合结论'}**：{'；'.join(points)}。")
        lines.append("")

    # 分维度对比表（仅当存在 >=2 个研究对象）
    if _object_count(groups) >= 2:
        lines += _render_comparison_table(groups, citations, _display)

    # 证据明细
    lines += ["## 证据明细", ""]
    for ev in evidences:
        lines.append(f"- **[{ev.evidence_id}]** {ev.claim}（来源 [{ev.source.source_id}]：{ev.source.ref}）")
    lines.append("")

    # 信息缺口
    lines += ["## 信息缺口", ""]
    if ctx.information_gaps:
        lines += [f"- {g}" for g in ctx.information_gaps]
    else:
        lines.append("- 无（本轮检索已覆盖计划的全部维度）")
    lines.append("")

    # 参考来源
    lines += ["## 参考来源", ""]
    for s in sources:
        lines.append(f"- **[{s.source_id}]** {s.title} — {s.ref}")
    lines.append("")

    if ctx.preferences.get("report_language") == "en":
        pass  # 预留：记忆系统可注入偏好（M2 起生效）

    return ComposeResult(markdown="\n".join(lines), citations=citations, sources=sources)


def _render_empty(question: str, gaps: list[str]) -> str:
    lines = ["# 技术选型研究报告", "", f"> 研究问题：{question}", ""]
    lines += ["## 结论摘要", "", "本轮检索未获得可用证据，无法给出有依据的结论。", ""]
    lines += ["## 信息缺口", ""]
    lines += [f"- {g}" for g in gaps] or ["- 全部检索路均未命中"]
    return "\n".join(lines)


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
) -> list[str]:
    """对比表：行=维度，列=对象；单元格=该对象该维度最高分证据的论断+引用。"""
    objects = [obj for obj in groups if obj]
    aspects: list[str] = []
    for obj in objects:
        for a in _group_by_aspect(groups[obj]):
            if a not in aspects:
                aspects.append(a)
    aspects.sort(key=lambda a: (_ASPECT_ORDER.index(a) if a in _ASPECT_ORDER else 99))

    shown = [display(o) for o in objects]
    lines = ["## 分维度对比", "", "| 维度 | " + " | ".join(shown) + " |",
             "|---|" + "---|" * len(objects)]
    for aspect in aspects:
        row = [ASPECT_ZH.get(aspect, aspect)]
        for obj in objects:
            evs = _group_by_aspect(groups[obj]).get(aspect)
            if evs:
                top = evs[0]
                row.append(f"{top.claim} [{top.evidence_id}]")
                citations.append(
                    Citation(location=f"table#{aspect}#{obj}", evidence_ids=[e.evidence_id for e in evs])
                )
            else:
                row.append("—（本轮无证据）")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines
