"""中英混合分词：英文/数字按词切分并小写，中文按字 + 二元组（bigram）切分。

选二元组而非单字：中文检索里单字区分度太低（"向量库"会命中一切含"量"的文档），
bigram 在无分词器依赖的前提下保留了大部分区分度。
"""

import re

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def tokenize(text: str) -> list[str]:
    """输出 token 序列，用于 BM25 索引与查询侧，两侧必须一致。"""
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        tokens.append(word.lower())

    # 中文连续段：单字 + bigram
    buf: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            buf.append(ch)
        elif buf:
            _emit_cjk(tokens, buf)
            buf = []
    if buf:
        _emit_cjk(tokens, buf)
    return tokens


def _emit_cjk(tokens: list[str], buf: list[str]) -> None:
    tokens.extend(buf)
    tokens.extend(f"{a}{b}" for a, b in zip(buf, buf[1:]))
