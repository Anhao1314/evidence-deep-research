"""API 层端到端：TestClient 走 lifespan 全装配（含 MCP 真链路与记忆）。

用 module 级 client：lifespan 只启动一次（含 MCP 子进程），
记忆跨请求可见（跨会话语义）。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["mode"] == "offline"
    assert resp.json()["kb_backend"] == "McpKBClient"


def test_research_end_to_end(client):
    resp = client.post("/api/research", json={"question": "RAG 向量库选 Qdrant 还是 Milvus？"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["markdown"].startswith("# 技术选型研究报告")
    assert body["trace_id"] == body["report_id"]
    assert body["evidences"], "响应应携带证据明细"
    assert {"kb", "web"} <= set(body["meta"]["routes_used"])

    report_id = body["report_id"]
    got = client.get(f"/api/reports/{report_id}")
    assert got.status_code == 200
    assert got.json()["question"].startswith("RAG")


def test_report_404(client):
    assert client.get("/api/reports/run-nonexistent").status_code == 404


def test_question_validation(client):
    resp = client.post("/api/research", json={"question": "啊"})
    assert resp.status_code == 422


def test_preference_and_memory_flow(client):
    """偏好设置 -> 研究一次 -> 记忆可见（跨会话记忆的 API 闭环）。"""
    resp = client.post(
        "/api/preferences",
        json={"user_id": "api-user", "key": "report_language", "value": "en"},
    )
    assert resp.status_code == 204

    research = client.post(
        "/api/research", json={"question": "向量库 Qdrant 还是 Milvus？", "user_id": "api-user"}
    )
    assert research.status_code == 201
    assert research.json()["markdown"].startswith("# Technology Selection Research Report")

    memory = client.get("/api/memory/api-user")
    assert memory.status_code == 200
    body = memory.json()
    assert body["preferences"]["report_language"] == "en"
    assert body["topics"], "研究完成后历史主题应可见"
    assert body["topics"][0]["summary"]
