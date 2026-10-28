"""引用校验 Guard：阻止无法追溯的结论继续作为“已验证研究结论”展示。

设计边界（ADR-005）：
- 只保证“不存在的证据编号不会继续作为有效引用展示”；
- 不判断合法编号是否在语义上支撑结论，后者属于人工复核；
- 非法引用经过一次受限重写仍不合法时，不再“删掉引用、保留结论”，
  而是把受影响结论移出主报告，写入 unverified_claims；
- 如果无法可靠保留任何已验证结论，fail-closed：回退到有引用的模板报告，
  或输出明确的安全占位，不把无引用结论伪装成研究结论。
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.schema.guard import GuardAction, GuardAudit, UnverifiedClaim

_CITE_RE = re.compile(r"\[(E\d+)\]")
_META_SECTIONS = {
    "信息缺口", "参考来源", "证据明细", "information gaps", "sources", "evidence details",
}
_UNCERTAIN_MARKERS = (
    "无法判断", "无法给出", "无法核验", "无法确定", "未获得", "没有足够",
    "没有可用证据", "暂无", "不确定", "需要补充", "仅供参考", "不能给出",
    "no evidence", "insufficient", "cannot determine", "not available", "unknown",
    "need more",
)
_NON_CLAIM_PATTERNS = (
    r"^结论如下",
    r"^如下",
    r"^说明[:：]",
    r"^注[:：]",
    r"^数据来源",
    r"^本轮",
    r"^以下",
)


@dataclass
class _Unit:
    kind: str  # sentence | table_cell
    text: str
    ids: list[str]
    start: int = 0
    end: int = 0
    cell_index: int | None = None


def extract_citations(markdown: str, evidences: list) -> list:
    """兼容旧接口：抽取正文中的合法 [E#]（按行定位）。"""
    from app.schema.report import Citation

    valid = {e.evidence_id for e in evidences}
    citations: list[Citation] = []
    for i, line in enumerate(markdown.splitlines()):
        ids = sorted({m for m in _CITE_RE.findall(line) if m in valid})
        if ids:
            citations.append(Citation(location=f"line#{i + 1}", evidence_ids=ids))
    return citations


def extract_conclusion_citations(markdown: str, evidences: list) -> list:
    """只从结论区域抽取引用：跳过证据明细、参考来源、代码块。"""
    from app.schema.report import Citation

    valid = {e.evidence_id for e in evidences}
    citations: list[Citation] = []
    section = ""
    in_code = False
    for i, line in enumerate(markdown.splitlines()):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip().lower()
            continue
        if section in _META_SECTIONS:
            continue
        ids = sorted({m for m in _CITE_RE.findall(line) if m in valid})
        if ids:
            citations.append(Citation(location=f"line#{i + 1}", evidence_ids=ids))
    return citations


def check_citations(markdown: str, valid_ids: set[str]) -> list[str]:
    """返回 markdown 中出现的非法引用编号列表。"""
    return sorted({m for m in _CITE_RE.findall(markdown) if m not in valid_ids})


def strip_invalid_citations(markdown: str, invalid_ids: list[str]) -> str:
    """兼容旧接口：只剥离非法引用（不再作为主流程的降级策略）。"""

    def _sub(match: re.Match) -> str:
        return match.group(0) if match.group(1) not in invalid_ids else ""

    cleaned = _CITE_RE.sub(_sub, markdown)
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff\w）)])\s+(?=[，。；])", "", cleaned)
    return cleaned


def rewrite_prompt(markdown: str, invalid_ids: list[str]) -> str:
    """受限重写指令：只允许替换/删除非法引用，禁止改写其他内容。"""
    valid_hint = "、".join(sorted(invalid_ids))
    return (
        "你上一版报告使用了不存在于证据清单中的引用编号：[" + valid_hint + "]。\n"
        "请只修正这些非法引用：替换为该结论实际依据的证据编号，或删除该引用标记。\n"
        "禁止改动报告的其他任何文字。直接输出修正后的完整 markdown：\n\n" + markdown
    )


def _body_without_citations(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n")
    text = _CITE_RE.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    if not text.strip():
        return []
    spans = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[^。！？；!?]+[。！？；!?]?", text)]
    return [(s, e, t) for s, e, t in spans if t.strip()]


def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_table_separator(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and bool(re.fullmatch(r"[\s:|-]+", s)) and "-" in s


def _is_meta_or_uncertain(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if any(t.lower().startswith(p) or re.search(p, t) for p in _NON_CLAIM_PATTERNS):
        return True
    return any(marker.lower() in t.lower() for marker in _UNCERTAIN_MARKERS)


def _analyze_sentence(text: str, section: str) -> list[_Unit]:
    units: list[_Unit] = []
    for start, end, piece in _sentence_spans(text):
        ids = _CITE_RE.findall(piece)
        if ids:
            units.append(_Unit(kind="sentence", text=piece, ids=ids, start=start, end=end))
        elif section.lower() not in _META_SECTIONS and not _is_meta_or_uncertain(piece):
            units.append(_Unit(kind="sentence", text=piece, ids=[], start=start, end=end))
    return units


def _analyze_line(line: str, section: str) -> list[_Unit]:
    if not line.strip() or _is_heading(line) or line.lstrip().startswith(">"):
        return []
    if line.strip().startswith("|"):
        if _is_table_separator(line):
            return []
        cells = line.strip().strip("|").split("|")
        units: list[_Unit] = []
        for idx, cell in enumerate(cells):
            if idx == 0:  # 维度/对象标签列不要求引用
                continue
            cell_text = cell.strip()
            if not cell_text or cell_text in {"—", "-", "N/A", "—（无证据）", "—（未验证）"}:
                continue
            for unit in _analyze_sentence(cell_text, section):
                unit.kind = "table_cell"
                unit.cell_index = idx
                units.append(unit)
        return units
    return _analyze_sentence(line, section)


def _rebuild_line(line: str, units_to_remove: set[int], units: list[_Unit]) -> str:
    if not units_to_remove:
        return line
    if line.strip().startswith("|"):
        cells = line.strip().strip("|").split("|")
        for idx, cell in enumerate(cells):
            if any(u.cell_index == idx and id(u) in units_to_remove for u in units):
                cells[idx] = " —（未验证） "
        return "|" + "|".join(cells) + "|"
    # 句子级：保留未被移除的 span；保持原始字符
    kept: list[str] = []
    cursor = 0
    for unit in sorted(units, key=lambda u: u.start):
        if id(unit) in units_to_remove:
            kept.append(line[cursor:unit.start])
            cursor = unit.end
    kept.append(line[cursor:])
    return "".join(kept)


def _quarantine(
    markdown: str,
    units_by_line: dict[int, list[_Unit]],
    to_remove: set[int],
    reasons: dict[int, str],
    invalid_by_unit: dict[int, list[str]],
    valid_by_unit: dict[int, list[str]],
) -> tuple[str, list[UnverifiedClaim]]:
    lines = markdown.splitlines()
    claims: list[UnverifiedClaim] = []
    out: list[str] = []
    for idx, line in enumerate(lines):
        units = units_by_line.get(idx, [])
        removable = {id(u) for u in units if id(u) in to_remove}
        for unit in units:
            if id(unit) not in removable:
                continue
            claims.append(UnverifiedClaim(
                location=f"line#{idx + 1}" + (f"[cell {unit.cell_index}]" if unit.cell_index else ""),
                text=unit.text.strip(),
                invalid_citation_ids=invalid_by_unit.get(id(unit), []),
                valid_citation_ids=valid_by_unit.get(id(unit), []),
                reason=reasons.get(id(unit), "unverified_claim"),
            ))
        rebuilt = _rebuild_line(line, removable, units)
        if rebuilt.strip() or not line.strip():
            out.append(rebuilt)
    return "\n".join(out).rstrip() + ("\n" if markdown.endswith("\n") else ""), claims


def _safe_stub() -> str:
    return (
        "# 研究报告（引用校验未通过）\n\n"
        "## 结论\n\n"
        "本轮模型输出未能通过引用校验，相关结论已被隔离，不作为已验证研究结论展示。\n\n"
        "## 信息缺口\n\n"
        "- 模型输出了无法追溯到证据集合的引用；需要重新检索或人工复核。\n"
    )


def enforce_citation_guard(
    markdown: str,
    valid_ids: set[str],
    *,
    rewrite_fn: Callable[[str, list[str]], str] | None = None,
    fallback_fn: Callable[[], str] | None = None,
) -> GuardAudit:
    """执行一次引用校验与降级。返回完整审计；不修改输入。"""
    raw = markdown
    actions: list[GuardAction] = []
    invalid_all = check_citations(raw, valid_ids)

    rewrite_attempted = False
    rewrite_accepted: bool | None = None
    rewrite_reason: str | None = None
    base = raw
    if invalid_all and rewrite_fn is not None:
        rewrite_attempted = True
        try:
            rewritten = rewrite_fn(raw, invalid_all)
        except Exception as exc:  # noqa: BLE001 —— 重写失败按拒绝处理
            rewrite_accepted = False
            rewrite_reason = f"rewrite_error: {exc}"
        else:
            if _body_without_citations(rewritten) != _body_without_citations(raw):
                rewrite_accepted = False
                rewrite_reason = "rewrite_changed_body"
            elif check_citations(rewritten, valid_ids):
                rewrite_accepted = False
                rewrite_reason = "rewrite_still_invalid"
            else:
                rewrite_accepted = True
                base = rewritten
                actions.append(GuardAction(action="rewrite_accepted",
                                           detail="受限重写仅改动引用编号，且已消除非法编号"))
        if rewrite_accepted is False:
            actions.append(GuardAction(action="rewrite_rejected", detail=rewrite_reason or ""))

    section = ""
    in_code = False
    units_by_line: dict[int, list[_Unit]] = {}
    lines = base.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if _is_heading(line):
            section = stripped.lstrip("#").strip()
            continue
        # 表头：下一行是分隔行
        if stripped.startswith("|") and idx + 1 < len(lines) and _is_table_separator(lines[idx + 1]):
            continue
        units = _analyze_line(line, section)
        if units:
            units_by_line[idx] = units

    to_remove: set[int] = set()
    reasons: dict[int, str] = {}
    invalid_by_unit: dict[int, list[str]] = {}
    valid_by_unit: dict[int, list[str]] = {}
    checked = 0
    for units in units_by_line.values():
        for unit in units:
            checked += 1
            invalid = [i for i in unit.ids if i not in valid_ids]
            valid = [i for i in unit.ids if i in valid_ids]
            invalid_by_unit[id(unit)] = invalid
            valid_by_unit[id(unit)] = valid
            if invalid and not rewrite_accepted:
                to_remove.add(id(unit))
                reasons[id(unit)] = "invalid_citation_after_rewrite" if rewrite_attempted \
                    else "invalid_citation"
            elif not unit.ids:
                to_remove.add(id(unit))
                reasons[id(unit)] = "missing_citation"

    if to_remove:
        final_markdown, unverified = _quarantine(
            base, units_by_line, to_remove, reasons, invalid_by_unit, valid_by_unit
        )
        actions.append(GuardAction(
            action="claim_quarantined",
            detail=f"隔离 {len(unverified)} 条无法追溯的结论；未写入主报告",
        ))
        if any(u.kind == "table_cell" for units in units_by_line.values()
               for u in units if id(u) in to_remove):
            actions.append(GuardAction(action="table_cell_quarantined",
                                       detail="受影响表格单元替换为“—（未验证）”"))
    else:
        final_markdown = base
        unverified = []
        if not rewrite_accepted:
            actions.append(GuardAction(action="kept", detail="所有结论引用均通过校验"))

    verified_units = 0
    for units in units_by_line.values():
        for unit in units:
            if id(unit) in to_remove:
                continue
            if any(i in valid_ids for i in unit.ids):
                verified_units += 1

    fallback_used = False
    fail_closed = False
    if checked > 0 and verified_units == 0 and unverified:
        fail_closed = True
        if fallback_fn is not None:
            try:
                final_markdown = fallback_fn()
                fallback_used = True
                actions.append(GuardAction(
                    action="fallback_template",
                    detail="模型结论全部被隔离，回退到基于证据的模板报告",
                ))
            except Exception as exc:  # noqa: BLE001 —— 回退失败则安全占位
                final_markdown = _safe_stub()
                actions.append(GuardAction(action="fail_closed",
                                           detail=f"模板回退失败，使用安全占位: {exc}"))
        else:
            final_markdown = _safe_stub()
            actions.append(GuardAction(
                action="fail_closed", detail="无可回退模板，输出安全占位，不展示无引用结论"
            ))

    remaining_invalid = check_citations(final_markdown, valid_ids)
    if remaining_invalid:
        fail_closed = True
        if fallback_fn is not None and not fallback_used:
            try:
                final_markdown = fallback_fn()
                fallback_used = True
                actions.append(GuardAction(
                    action="fallback_template",
                    detail="最终文本仍含非法引用，回退到基于证据的模板报告",
                ))
            except Exception as exc:  # noqa: BLE001
                final_markdown = _safe_stub()
                actions.append(GuardAction(action="fail_closed",
                                           detail=f"模板回退失败，使用安全占位: {exc}"))
        else:
            final_markdown = _safe_stub()
            actions.append(GuardAction(
                action="fail_closed", detail="最终文本仍含非法引用，输出安全占位"
            ))
        if check_citations(final_markdown, valid_ids):
            final_markdown = _safe_stub()

    diff = "\n".join(difflib.unified_diff(
        raw.splitlines(), final_markdown.splitlines(),
        fromfile="pre_guard", tofile="post_guard", lineterm="",
    ))
    return GuardAudit(
        ran=True,
        raw_markdown=raw,
        final_markdown=final_markdown,
        diff=diff,
        invalid_citation_ids=invalid_all,
        rewrite_attempted=rewrite_attempted,
        rewrite_accepted=rewrite_accepted,
        rewrite_rejected_reason=rewrite_reason,
        claims_checked=checked,
        claims_quarantined=len(unverified),
        actions=actions,
        unverified_claims=unverified,
        fallback_used=fallback_used,
        fail_closed=fail_closed,
    )