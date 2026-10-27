"""知识库检索器：内置语料上的行为契约。"""

from knowledge_base.retriever import KBRetriever


def test_retriever_hits_expected_doc(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    docs = [s.doc for s in retriever.search("Qdrant 性能 延迟", top_k=3)]
    assert docs, "应有命中"
    assert any(d.doc_id == "kb-qdrant-perf" for d in docs[:2])


def test_retriever_topic_filter(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    docs = [s.doc for s in retriever.search("性能 吞吐", top_k=10, topic="vector-db")]
    assert docs
    assert all(d.topic == "vector-db" for d in docs)


def test_retriever_scores_descending(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    scored = retriever.search("Milvus 成本 部署", top_k=5)
    scores = [s.score for s in scored]
    assert scores == sorted(scores, reverse=True)


def test_retriever_empty_result_for_garbage_query(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    assert retriever.search("zzzzzz qqqqq", top_k=3) == []
