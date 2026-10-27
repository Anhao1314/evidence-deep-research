"""跨会话记忆：三表 schema、召回、沉淀、跨会话生命周期。"""

from app.memory.store import MemoryStore
from knowledge_base.retriever import KBRetriever


def _make_report(question: str, evidences):
    """构造最小报告对象用于沉淀测试（evidences 为编号后的 Evidence 列表）。"""
    from app.schema.report import Report, ReportMeta

    return Report(
        report_id="run-test", question=question, trace_id="run-test",
        markdown="# r", summary="摘要", sources=[e.source for e in evidences],
        citations=[], evidences=evidences, meta=ReportMeta(),
    )


def test_preferences_set_get_update(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    assert store.get_preferences("u1") == {}
    store.set_preference("u1", "report_language", "zh")
    store.set_preference("u1", "report_language", "en")  # 覆盖更新
    store.set_preference("u1", "source_priority", "official")
    assert store.get_preferences("u1") == {"report_language": "en", "source_priority": "official"}
    assert store.get_preferences("u2") == {}  # 用户隔离


def test_recall_empty_store(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    recall = store.recall("u1", "向量库怎么选？")
    assert recall.preferences == {}
    assert recall.related_topics == []
    assert recall.snippets == []


def test_record_and_recall_roundtrip(tmp_path, settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    from app.schema.evidence import Evidence, Source

    evidences = [
        Evidence(
            evidence_id="E1",
            source=Source(source_id="S1", kind="kb", title="q", doc_id="kb-qdrant-perf", locator="§1"),
            claim="Qdrant 单机 P99 延迟 3-10ms", quote="...", topic="vector-db",
            object_name="qdrant", aspect="performance", route="kb", score=5.0,
        ),
        Evidence(
            evidence_id="E2",
            source=Source(source_id="S2", kind="kb", title="m", doc_id="kb-milvus-cost", locator="§3"),
            claim="Milvus 分布式版依赖三个外部组件", quote="...", topic="vector-db",
            object_name="milvus", aspect="cost", route="kb", score=4.0,
        ),
    ]
    store = MemoryStore(tmp_path / "m.sqlite3")
    topic_id = store.record_research("u1", "RAG 向量库选 Qdrant 还是 Milvus？", _make_report("q", evidences))
    assert topic_id > 0

    recall = store.recall("u1", "向量库选型：Qdrant 和 Milvus 哪个好？")
    assert recall.related_topics, "相似问题应召回历史主题"
    assert "Qdrant 还是 Milvus" in recall.related_topics[0].question
    assert set(recall.snippets) == {"Qdrant 单机 P99 延迟 3-10ms", "Milvus 分布式版依赖三个外部组件"}


def test_recall_unrelated_question_misses(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    store.record_research("u1", "RAG 向量库选 Qdrant 还是 Milvus？", _make_report("q", []))
    recall = store.recall("u1", "今晚吃什么")
    assert recall.related_topics == []


def test_cross_session_persistence(tmp_path, settings):
    """跨会话语义：独立 MemoryStore 实例（同 db 文件）读到前一会话的数据。"""
    db = tmp_path / "cross.sqlite3"
    session1 = MemoryStore(db)
    session1.set_preference("u1", "report_language", "en")
    session1.record_research("u1", "LangGraph 与自研编排对比", _make_report("q", []))
    session1.close()

    session2 = MemoryStore(db)  # 模拟下一个会话/进程
    assert session2.get_preferences("u1") == {"report_language": "en"}
    assert len(session2.list_topics("u1")) == 1
    assert session2.recall("u1", "编排框架怎么选").related_topics
    session2.close()
