"""知识库文档模型与语料加载。

内置演示语料（data/corpus/*.jsonl）为离线模式服务：
内容是基于公开资料整理的演示快照，URL 指向真实文档站点，
但片段文本仅用于离线演示与测试，不代表最新事实。
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field


class KBDocument(BaseModel):
    doc_id: str
    title: str
    text: str
    section: str = Field(default="", description="文档内段落定位，如 §2.1")
    topic: str = Field(description="研究主题，如 vector-db")
    object_name: str = Field(default="", description="所属对比对象，如 qdrant")
    aspect: str = Field(default="general", description="研究维度 performance/ecosystem/cost/maturity")
    source_url: str | None = None


def load_corpus(corpus_dir: Path) -> list[KBDocument]:
    """加载目录下全部 *.jsonl，每行一个 KBDocument。"""
    docs: list[KBDocument] = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                docs.append(KBDocument.model_validate(json.loads(line)))
    return docs
