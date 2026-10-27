"""跨会话记忆：SQLite 三表 + 召回/沉淀。

记忆划分（ADR 与 README 详述）：
- 短期记忆 = LangGraph State，run 结束即生命周期终点；
- 长期记忆 = 本文件三张表，只进结构化、可再利用的信息：
  user_preferences（偏好，全量注入）、research_topics（历史主题，BM25 召回）、
  knowledge_snippets（结论沉淀，按主题关联取回）。

召回发生在主图 load_context 节点，沉淀发生在 persist 节点（报告落定后写入），
保证只沉淀"已经完成并有证据支撑"的研究。
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from knowledge_base.bm25 import BM25Index
from pydantic import BaseModel, Field

from app.schema.report import Report

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS research_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES research_topics(id),
    claim TEXT NOT NULL,
    source_refs TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_user ON research_topics(user_id);
CREATE INDEX IF NOT EXISTS idx_snippets_topic ON knowledge_snippets(topic_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RelatedTopic(BaseModel):
    topic_id: int
    question: str
    summary: str


class RecallContext(BaseModel):
    preferences: dict[str, str] = Field(default_factory=dict)
    related_topics: list[RelatedTopic] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list)


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- 偏好 ----------

    def get_preferences(self, user_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {k: v for k, v in rows}

    def set_preference(self, user_id: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO user_preferences(user_id, key, value, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (user_id, key, value, _now()),
        )
        self._conn.commit()

    # ---------- 召回 ----------

    def recall(self, user_id: str, question: str, top_k: int = 3) -> RecallContext:
        related = self._match_topics(user_id, question, top_k=top_k)
        snippets: list[str] = []
        if related:
            ids = [t.topic_id for t in related]
            marks = ",".join("?" * len(ids))
            rows = self._conn.execute(
                f"SELECT claim FROM knowledge_snippets WHERE topic_id IN ({marks})", ids
            ).fetchall()
            snippets = [r[0] for r in rows]
        return RecallContext(
            preferences=self.get_preferences(user_id),
            related_topics=related,
            snippets=snippets,
        )

    def _match_topics(self, user_id: str, question: str, top_k: int) -> list[RelatedTopic]:
        rows = self._conn.execute(
            "SELECT id, question, summary FROM research_topics WHERE user_id = ?", (user_id,)
        ).fetchall()
        if not rows:
            return []
        index = BM25Index()
        for topic_id, q, s in rows:
            index.add_document(str(topic_id), f"{q} {q} {s}")
        hits = index.score(question)[:top_k]
        by_id = {str(topic_id): (topic_id, q, s) for topic_id, q, s in rows}
        return [RelatedTopic(topic_id=tid, question=q, summary=s) for tid, _, _ in
                (by_id[doc_id] for doc_id, _ in hits)]

    # ---------- 沉淀 ----------

    def record_research(self, user_id: str, question: str, report: Report) -> int:
        """落库一次研究：主题 + 摘要 + 每维度最高分证据的结论沉淀。"""
        cur = self._conn.execute(
            "INSERT INTO research_topics(user_id, question, summary, created_at) VALUES(?,?,?,?)",
            (user_id, question, report.summary, _now()),
        )
        topic_id = cur.lastrowid
        rows = []
        seen: set[tuple] = set()
        for ev in report.evidences:
            # report.evidences 已按相关度排序，每组 (topic, object, aspect) 取首个（最高分）
            key = (ev.topic, ev.object_name, ev.aspect)
            if key in seen:
                continue
            seen.add(key)
            rows.append((topic_id, ev.claim, json.dumps([ev.source.source_id]), _now()))
        self._conn.executemany(
            "INSERT INTO knowledge_snippets(topic_id, claim, source_refs, created_at) VALUES(?,?,?,?)",
            rows,
        )
        self._conn.commit()
        return topic_id

    def list_topics(self, user_id: str, limit: int = 20) -> list[RelatedTopic]:
        rows = self._conn.execute(
            "SELECT id, question, summary FROM research_topics WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [RelatedTopic(topic_id=r[0], question=r[1], summary=r[2]) for r in rows]

    def close(self) -> None:
        self._conn.close()
