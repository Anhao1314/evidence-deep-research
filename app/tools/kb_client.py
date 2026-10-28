"""知识库访问抽象。

M1：InProcessKBClient 直接调用进程内检索器。
M2：知识库检索迁入自研 MCP Server，主流程改用 McpKBClient（stdio 子进程，
真实 MCP 协议调用）。上层子 Agent 只依赖 KBClient 协议，切换零改动——
这是"工具系统可替换"的接缝设计。
"""

from typing import Protocol

from knowledge_base.retriever import Retriever, ScoredDoc


class KBClient(Protocol):
    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]: ...


class InProcessKBClient:
    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]:
        return self._retriever.search(query, top_k=top_k, topic=topic)


class EmptyKBClient:
    """Web-only 评测使用：显式关闭 KB 路，返回空结果而不是悄悄用演示语料。"""

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]:
        return []
