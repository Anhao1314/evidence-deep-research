"""Web 搜索抽象。

M1 提供 DemoSearchProvider（对内置演示网页数据做 BM25 检索），
保证离线端到端可跑；在线搜索 Provider（如 Tavily/SerpAPI）在 M3 接入，
实现同一 SearchProvider 协议即可替换。
"""

import json
from pathlib import Path
from typing import Protocol

from knowledge_base.bm25 import BM25Index
from pydantic import BaseModel, Field


class WebHit(BaseModel):
    hit_id: str
    url: str
    title: str
    snippet: str = ""
    content: str = ""
    topic: str = ""
    object_name: str = ""
    aspect: str = "general"
    published: str = ""
    score: float = 0.0  # 检索打分，随结果透传给证据层


class SearchProvider(Protocol):
    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[WebHit]: ...


def load_web_hits(web_demo_dir: Path) -> list[WebHit]:
    hits: list[WebHit] = []
    for path in sorted(web_demo_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                hits.append(WebHit.model_validate(json.loads(line)))
    return hits


class DemoSearchProvider:
    """离线演示搜索：对内置网页片段建 BM25 索引。"""

    def __init__(self, hits: list[WebHit]) -> None:
        self.hits = hits
        self._by_id = {h.hit_id: h for h in hits}
        self._index = BM25Index()
        for h in hits:
            self._index.add_document(h.hit_id, f"{h.title} {h.snippet} {h.content}")

    @classmethod
    def from_dir(cls, web_demo_dir: Path) -> "DemoSearchProvider":
        return cls(load_web_hits(web_demo_dir))

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[WebHit]:
        out: list[WebHit] = []
        for hit_id, score in self._index.score(query):
            hit = self._by_id[hit_id]
            if topic is not None and hit.topic != topic:
                continue
            out.append(hit.model_copy(update={"score": round(score, 4)}))
            if len(out) >= top_k:
                break
        return out
