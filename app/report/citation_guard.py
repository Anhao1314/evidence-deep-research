"""引用校验 guard：报告中的 [E#] 必须来自真实证据集合。

这是"引用不允许模型编造"的强制机制：提示词约束只是软约束，
后处理校验才是硬约束。非法引用的处理策略分级：
1. 受限重写一次（把非法编号反馈给生成方修正）；
2. 二次仍非法 -> 剥离非法标记并记录告警（宁可无引用，不可假引用）。
"""

import re

from pydantic import BaseModel, Field

_CITE_RE = re.compile(r"\[(E\d+)\]")


def extract_citations(markdown: str, evidences: list) -> list:
    """从 markdown 抽取 [E#] 引用（按行定位），非法编号自动过滤。"""
    from app.schema.report import Citation

    valid = {e.evidence_id for e in evidences}
    citations: list[Citation] = []
    for i, line in enumerate(markdown.splitlines()):
        ids = sorted({m for m in _CITE_RE.findall(line) if m in valid})
        if ids:
            citations.append(Citation(location=f"line#{i + 1}", evidence_ids=ids))
    return citations


class GuardResult(BaseModel):
    markdown: str
    valid: bool
    invalid_ids: list[str] = Field(default_factory=list)
    stripped: bool = Field(default=False, description="是否剥离过非法引用")


def check_citations(markdown: str, valid_ids: set[str]) -> list[str]:
    """返回 markdown 中出现的非法引用编号列表。"""
    return sorted({m for m in _CITE_RE.findall(markdown) if m not in valid_ids})


def strip_invalid_citations(markdown: str, invalid_ids: list[str]) -> str:
    """剥离指定非法引用（连同前导空格），保留正文。"""

    def _sub(match: re.Match) -> str:
        return match.group(0) if match.group(1) not in invalid_ids else ""

    cleaned = _CITE_RE.sub(_sub, markdown)
    # 清理剥离后残留的连续标点/空格
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff\w）)])\s+(?=[，。；])", "", cleaned)
    return cleaned


def rewrite_prompt(markdown: str, invalid_ids: list[str]) -> str:
    """受限重写指令：只允许替换/删除非法引用，禁止改写其他内容。"""
    valid_hint = "、".join(sorted(invalid_ids))
    return (
        "你上一版报告使用了不存在于证据清单中的引用编号：[" + valid_hint + "]。\n"
        "请只修正这些非法引用：替换为该结论实际依据的证据编号，或直接删除该标记。\n"
        "禁止改动报告的其他任何文字。直接输出修正后的完整 markdown：\n\n" + markdown
    )
