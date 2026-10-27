"""API 层端到端：TestClient 走 lifespan 全装配。"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["mode"] == "offline"


def test_research_end_to_end(client):
    resp = client.post("/api/research", json={"question": "RAG 向量库选 Qdrant 还是 Milvus？"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["markdown"].startswith("# 技术选型研究报告")
    assert body["trace_id"] == body["report_id"]
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
