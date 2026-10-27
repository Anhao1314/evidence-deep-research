"""检索器协议与默认 BM25 实现。

Retriever 是本系统检索能力的唯一抽象：RAG Agent、MCP Server（M2）
都只依赖它；pgvector 语义检索作为可选扩展时实现同一协议即可替换。
"""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from knowledge_base.bm25 import BM25Index
from knowledge_base.corpus import KBDocument, load_corpus


class ScoredDoc(BaseModel):
    doc: KBDocument
    score: float


class Retriever(Protocol):
    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]: ...


class KBRetriever:
    def __init__(self, docs: list[KBDocument]) -> None:
        self.docs = docs
        self._by_id = {d.doc_id: d for d in docs}
        self._index = BM25Index()
        for d in docs:
            # title 重复一次参与索引，轻度加权标题命中
            self._index.add_document(d.doc_id, f"{d.title}\n{d.title}\n{d.text}")

    @classmethod
    def from_dir(cls, corpus_dir: Path) -> "KBRetriever":
        return cls(load_corpus(corpus_dir))

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]:
        hits = self._index.score(query)
        out: list[ScoredDoc] = []
        for doc_id, score in hits:
            doc = self._by_id[doc_id]
            if topic is not None and doc.topic != topic:
                continue
            out.append(ScoredDoc(doc=doc, score=round(score, 4)))
            if len(out) >= top_k:
                break
        return out
