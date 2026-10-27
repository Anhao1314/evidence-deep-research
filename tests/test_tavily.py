"""TavilySearchProvider：映射与重试策略（monkeypatch httpx，不触外网）。"""

import httpx
import pytest

from app.tools.tavily_search import TavilySearchProvider


def _resp(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


def test_result_mapping(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert "tavily" in url
        assert json["api_key"] == "k"
        return _resp(200, {"results": [
            {"title": "T1", "url": "https://a.example", "content": "content-1", "score": 0.91,
             "published_date": "2025-01-01"},
        ]})

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", fake_post)
    provider = TavilySearchProvider(api_key="k", retry_base_delay=0.0)
    hits = provider.search("q", top_k=3, topic="vector-db")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "T1" and hit.url == "https://a.example"
    assert hit.score == 0.91 and hit.topic == "vector-db"
    assert hit.hit_id  # 由 URL 稳定派生


def test_retries_on_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(503, {})
        return _resp(200, {"results": []})

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", flaky)
    provider = TavilySearchProvider(api_key="k", max_retries=2, retry_base_delay=0.0)
    assert provider.search("q") == []
    assert calls["n"] == 2


def test_client_error_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def bad_key(url, json=None, timeout=None):
        calls["n"] += 1
        return _resp(401, {"detail": "invalid key"})

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", bad_key)
    provider = TavilySearchProvider(api_key="k", max_retries=3, retry_base_delay=0.0)
    with pytest.raises(RuntimeError, match="401"):
        provider.search("q")
    assert calls["n"] == 1


def test_exhausted_retries_raise(monkeypatch):
    def always_500(url, json=None, timeout=None):
        return _resp(500, {})

    monkeypatch.setattr("app.tools.tavily_search.httpx.post", always_500)
    provider = TavilySearchProvider(api_key="k", max_retries=1, retry_base_delay=0.0)
    with pytest.raises(RuntimeError, match="500"):
        provider.search("q")
