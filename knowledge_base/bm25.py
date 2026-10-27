"""Okapi BM25（k1=1.5, b=0.75）内存倒排实现。

语料规模在数百条以内时，内存倒排远快于引入向量库/搜索引擎，
且离线模式零依赖、测试确定性可复现。
"""

import math
from collections import Counter
from dataclasses import dataclass, field

from knowledge_base.tokenize import tokenize


@dataclass
class _Posting:
    doc_id: str
    term_freq: float
    length: float


@dataclass
class BM25Index:
    k1: float = 1.5
    b: float = 0.75
    _postings: dict[str, list[_Posting]] = field(default_factory=dict, init=False)
    _doc_len: dict[str, float] = field(default_factory=dict, init=False)
    _total_len: float = 0.0

    def add_document(self, doc_id: str, text: str) -> None:
        tokens = tokenize(text)
        if not tokens:
            return
        length = float(len(tokens))
        self._doc_len[doc_id] = length
        self._total_len += length
        for term, tf in Counter(tokens).items():
            self._postings.setdefault(term, []).append(_Posting(doc_id, float(tf), length))

    def score(self, query: str) -> list[tuple[str, float]]:
        """返回 [(doc_id, bm25分数)]，按分数降序；仅包含至少命中一个查询词的文档。"""
        if not self._doc_len:
            return []
        avg_len = self._total_len / len(self._doc_len)
        n_docs = len(self._doc_len)
        scores: dict[str, float] = {}
        for term in tokenize(query):
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = math.log(1.0 + (n_docs - len(postings) + 0.5) / (len(postings) + 0.5))
            for p in postings:
                tf_norm = p.term_freq * (self.k1 + 1.0)
                denom = p.term_freq + self.k1 * (1.0 - self.b + self.b * p.length / avg_len)
                scores[p.doc_id] = scores.get(p.doc_id, 0.0) + idf * tf_norm / denom
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
