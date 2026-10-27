"""检索证据模型：来源（Source）与证据（Evidence）。

设计约束：报告中每个结论的引用都必须回溯到真实检索产出的 Evidence，
Evidence 必须携带完整的 Source 定位信息（URL 或 知识库文档+段落）。
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Source(BaseModel):
    """一条可定位的来源。web 路必填 url，kb 路必填 doc_id。"""

    source_id: str = Field(description="全局来源编号，如 S1")
    kind: Literal["web", "kb"]
    title: str
    url: str | None = None
    doc_id: str | None = None
    locator: str | None = Field(default=None, description="定位：网页锚点或知识库段落号，如 §2.1")
    retrieved_at: str = Field(default_factory=utc_now)

    @property
    def ref(self) -> str:
        """人读引用串，如 `qdrant.tech/docs (web)` 或 `doc:qdrant-perf §2.1 (kb)`。"""
        where = self.url if self.kind == "web" else f"doc:{self.doc_id}"
        loc = f" {self.locator}" if self.locator else ""
        return f"{where}{loc} ({self.kind})"


class Evidence(BaseModel):
    """一条检索证据：从某个 Source 摘录的片段及其支撑的论断。"""

    evidence_id: str = Field(description="全局证据编号，如 E1，供报告引用标注")
    source: Source
    claim: str = Field(description="该证据支撑的论断（一句话）")
    quote: str = Field(description="来源原文摘录（截断），禁止改写为模型自拟内容")
    topic: str = Field(description="所属研究主题，如 vector-db")
    object_name: str = Field(default="", description="研究对象，如 qdrant；泛型证据可为空")
    aspect: str = Field(description="研究维度，如 performance / ecosystem / cost / maturity")
    route: Literal["web", "kb"]
    score: float = Field(default=0.0, description="检索打分（BM25 或等价），用于 merge 去重排序")
