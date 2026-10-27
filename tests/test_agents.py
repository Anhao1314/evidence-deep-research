"""双子 Agent：作为独立子图可单独 invoke、可注入失败测试。"""

from app.agents.rag_agent import build_rag_agent
from app.agents.web_agent import build_web_agent
from app.schema.plan import SubTask
from app.tools.kb_client import InProcessKBClient
from app.tools.search import DemoSearchProvider
from knowledge_base.retriever import KBRetriever


def _kb_subtask() -> SubTask:
    return SubTask(
        subtask_id="T1", route="kb", topic="vector-db", object_name="qdrant",
        object_display="Qdrant", aspect="general", query="Qdrant 向量数据库",
    )


def _web_subtask() -> SubTask:
    return SubTask(
        subtask_id="T2", route="web", topic="vector-db", object_name="qdrant",
        object_display="Qdrant", aspect="general", query="Qdrant 向量数据库",
    )


class _ExplodingKBClient:
    def search(self, *args, **kwargs):
        raise ConnectionError("模拟网络故障")


def test_rag_agent_produces_cited_evidence(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    agent = build_rag_agent(InProcessKBClient(retriever), settings)
    final = agent.invoke({"subtask": _kb_subtask(), "question": "Qdrant 与 Milvus 选型"})
    assert final["evidences"], "RAG Agent 应产出证据"
    ev = final["evidences"][0]
    assert ev.source.kind == "kb" and ev.source.doc_id
    assert ev.quote and ev.claim
    assert ev.topic == "vector-db" and ev.object_name == "qdrant"
    assert ev.score > 0


def test_web_agent_produces_cited_evidence(settings):
    agent = build_web_agent(DemoSearchProvider.from_dir(settings.web_demo_dir), settings)
    final = agent.invoke({"subtask": _web_subtask(), "question": "Qdrant 与 Milvus 选型"})
    assert final["evidences"], "Web Agent 应产出证据"
    ev = final["evidences"][0]
    assert ev.source.kind == "web" and ev.source.url
    assert ev.quote


def test_rag_agent_degrades_on_tool_failure(settings):
    agent = build_rag_agent(_ExplodingKBClient(), settings)
    final = agent.invoke({"subtask": _kb_subtask(), "question": "Qdrant 与 Milvus 选型"})
    assert final["evidences"] == []
    failures = final["route_failures"]
    assert len(failures) == 1
    assert failures[0]["route"] == "kb"
    assert "模拟网络故障" in failures[0]["error"]


def test_rag_agent_object_attribution_follows_corpus(settings):
    """回归：检索命回对比文档时，对象归属以语料标注为准，不得沿用子任务对象名。"""
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    agent = build_rag_agent(InProcessKBClient(retriever), settings)
    subtask = SubTask(
        subtask_id="T1", route="kb", topic="vector-db", object_name="qdrant",
        object_display="Qdrant", aspect="general", query="Milvus 分布式 部署 成本",
    )
    final = agent.invoke({"subtask": subtask, "question": "Qdrant 与 Milvus 选型"})
    objects = {ev.object_name for ev in final["evidences"]}
    assert "milvus" in objects, "命中的 Milvus 文档应归属 milvus 对象"


def test_agents_have_expected_structure(settings):
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    rag = build_rag_agent(InProcessKBClient(retriever), settings)
    web = build_web_agent(DemoSearchProvider.from_dir(settings.web_demo_dir), settings)
    for agent in (rag, web):
        assert set(agent.get_graph().nodes) >= {"retrieve", "extract"}
