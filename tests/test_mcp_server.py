"""自研 MCP Server 真链路：stdio 子进程 + 标准 MCP 协议调用。

这些测试验证的就是"主流程作为 MCP Client 跨进程调用知识库"的完整路径，
不是进程内函数调用的近似。
"""

import pytest

from app.config import PROJECT_ROOT
from app.mcp_client.client import McpKBClient


@pytest.fixture(scope="module")
def mcp_client():
    client = McpKBClient(corpus_dir=PROJECT_ROOT / "data" / "corpus", connect_timeout=60.0)
    yield client
    client.close()


def test_search_roundtrip_via_mcp(mcp_client):
    docs = mcp_client.search("Qdrant 性能 延迟", top_k=3)
    assert docs, "MCP kb_search 应返回结果"
    assert docs[0].doc.doc_id == "kb-qdrant-perf"
    assert docs[0].score > 0
    assert docs[0].doc.topic == "vector-db"


def test_search_respects_topic_filter(mcp_client):
    docs = mcp_client.search("性能 吞吐", top_k=10, topic="agent-framework")
    assert docs
    assert all(d.doc.topic == "agent-framework" for d in docs)


def test_get_doc_roundtrip(mcp_client):
    doc = mcp_client.get_doc("kb-qdrant-cost")
    assert doc["doc_id"] == "kb-qdrant-cost"
    assert "Apache-2.0" in doc["text"]
    assert doc["aspect"] == "cost"


def test_get_doc_missing_returns_error_payload(mcp_client):
    doc = mcp_client.get_doc("kb-not-exist")
    assert "error" in doc


def test_connection_failure_raises_fast():
    """无效的 server 启动命令必须在连接超时前暴露为明确异常。"""
    with pytest.raises((ConnectionError, TimeoutError, OSError)):
        McpKBClient(
            corpus_dir=PROJECT_ROOT / "data" / "corpus",
            python_exe="definitely-not-a-python-binary",
            connect_timeout=10.0,
        )


def test_client_close_is_idempotent(mcp_client):
    mcp_client.close()
    mcp_client.close()  # 二次关闭不应抛错
