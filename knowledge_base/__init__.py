"""知识库检索：自研 BM25 + 中英混合分词，零外部依赖（离线可跑的核心）。

pgvector 语义检索留为可选扩展：上层只依赖 Retriever 协议，
替换实现不影响 MCP Server 与子 Agent。
"""

from knowledge_base.bm25 import BM25Index
from knowledge_base.corpus import KBDocument, load_corpus
from knowledge_base.retriever import KBRetriever, Retriever, ScoredDoc
from knowledge_base.tokenize import tokenize

__all__ = [
    "BM25Index",
    "KBDocument",
    "KBRetriever",
    "Retriever",
    "ScoredDoc",
    "load_corpus",
    "tokenize",
]
