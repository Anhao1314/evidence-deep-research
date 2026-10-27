"""在线 Web 搜索 Provider：Tavily API，实现与离线 Demo 相同的 SearchProvider 协议。

搜索失败按指数退避重试，耗尽后抛出异常——由子 Agent 的重试/降级路径
（route_failures）接管，不阻塞主图。测试通过 monkeypatch httpx.post 覆盖，
不触真实外网。
"""

import hashlib
import json
from pathlib import Path

import httpx

from app.tools.search import SearchProvider, WebHit
from knowledge_base.bm25 import BM25Index  # noqa: F401 —— 保持与 Demo 相同的检索面

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchProvider(SearchProvider):
    def __init__(
        self,
        *,
        api_key: str,
        max_results: int = 5,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
        endpoint: str = TAVILY_ENDPOINT,
    ) -> None:
        self._api_key = api_key
        self._max_results = max_results
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._endpoint = endpoint

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[WebHit]:
        data = self._post(query, min(top_k, self._max_results))
        hits: list[WebHit] = []
        for i, item in enumerate(data.get("results", [])):
            content = item.get("content", "")
            hits.append(
                WebHit(
                    hit_id=hashlib.md5(item.get("url", f"{query}-{i}").encode()).hexdigest()[:12],
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=content[:160],
                    content=content,
                    topic=topic or "",
                    aspect="general",
                    published=item.get("published_date", ""),
                    score=float(item.get("score", 0.0)),
                )
            )
        return hits

    def _post(self, query: str, top_k: int) -> dict:
        import time

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = httpx.post(
                    self._endpoint,
                    json={"api_key": self._api_key, "query": query, "max_results": top_k,
                          "include_answer": False},
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    return resp.json()
                last_error = RuntimeError(f"Tavily HTTP {resp.status_code}: {resp.text[:120]}")
                if resp.status_code < 500 and resp.status_code != 429:
                    break  # 客户端错误重试无意义
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self._max_retries:
                time.sleep(self._retry_base_delay * (2**attempt))
        raise last_error or RuntimeError("Tavily 搜索失败")
