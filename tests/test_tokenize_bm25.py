"""分词与 BM25 基础能力：离线检索质量的底座。"""

from knowledge_base.bm25 import BM25Index
from knowledge_base.tokenize import tokenize


def test_tokenize_mixed_cjk_ascii():
    tokens = tokenize("Qdrant 是向量数据库 Vector-DB 2.4")
    assert "qdrant" in tokens
    assert "vector" in tokens and "db" in tokens
    # 中文单字 + bigram
    assert "向量" in tokens and "量数" in tokens
    assert "是" in tokens


def test_tokenize_deterministic():
    assert tokenize("向量库选型") == tokenize("向量库选型")


def _index():
    idx = BM25Index()
    idx.add_document("a", "Qdrant 单节点 HNSW 索引 P99 延迟 3-10ms，支持标量量化")
    idx.add_document("b", "Milvus 分布式架构，支持十亿级向量与 GPU 索引")
    idx.add_document("c", "今天天气不错，适合出门散步")
    return idx


def test_bm25_relevant_doc_ranks_first():
    idx = _index()
    hits = idx.score("Qdrant 延迟")
    assert hits and hits[0][0] == "a"


def test_bm25_unrelated_doc_not_scored():
    idx = _index()
    doc_ids = [d for d, _ in idx.score("Qdrant 延迟")]
    assert "c" not in doc_ids


def test_bm25_empty_index_or_query():
    assert BM25Index().score("任何查询") == []
    assert _index().score("") == []
