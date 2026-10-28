"""MCP Server 主体：知识库 BM25 检索暴露为标准 MCP 工具。

与客户端的契约：
- kb_search(query, top_k, topic) -> [{doc_id, title, section, text, topic,
  object_name, aspect, source_url, score}]：按相关度降序；
- kb_get_doc(doc_id) -> 完整文档（无定位字段的展开形式），未命中返回 {error: ...}。

语料目录通过环境变量 DR_KB_CORPUS_DIR 注入（client 启动子进程时传），
默认使用仓库内置演示语料。
"""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from knowledge_base.retriever import KBRetriever

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_retriever: KBRetriever | None = None


def _corpus_dir() -> Path:
    env = os.environ.get("DR_KB_CORPUS_DIR")
    return Path(env) if env else PROJECT_ROOT / "data" / "corpus"


def get_retriever() -> KBRetriever:
    """懒加载：stdio server 启动时不应做任何重活，首次工具调用再建索引。"""
    global _retriever
    if _retriever is None:
        _retriever = KBRetriever.from_dir(_corpus_dir())
    return _retriever


server = MCPServer(
    "knowledge-base",
    instructions="技术选型调研知识库：返回带 topic/object/aspect 元数据与段落定位的文档片段，供引用溯源使用。",
)


@server.tool()
def kb_search(query: str, top_k: int = 4, topic: str | None = None) -> list[dict]:
    """BM25 检索知识库，按相关度降序返回文档片段及元数据。"""
    hits = get_retriever().search(query, top_k=top_k, topic=topic)
    return [
        {
            "doc_id": h.doc.doc_id,
            "title": h.doc.title,
            "section": h.doc.section,
            "text": h.doc.text,
            "topic": h.doc.topic,
            "object_name": h.doc.object_name,
            "aspect": h.doc.aspect,
            "source_url": h.doc.source_url,
            "attributes": h.doc.attributes,
            "score": h.score,
        }
        for h in hits
    ]


@server.tool()
def kb_get_doc(doc_id: str) -> dict:
    """按 doc_id 取回完整文档，用于报告引用的原文核对。"""
    for doc in get_retriever().docs:
        if doc.doc_id == doc_id:
            return {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "section": doc.section,
                "text": doc.text,
                "topic": doc.topic,
                "object_name": doc.object_name,
                "aspect": doc.aspect,
                "source_url": doc.source_url,
                "attributes": doc.attributes,
            }
    return {"error": f"doc not found: {doc_id}"}


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
