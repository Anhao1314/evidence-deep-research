"""全链路结构化 Trace：每次 run 一个 trace_id（= run_id），事件流落 SQLite。

事件类型枚举（README 状态图上每个动作都有对应事件）：
plan / dispatch / tool_call / tool_result / retry / degrade / merge /
verify / memory_recall / synthesize / persist / usage / error。

写入方是图节点与子 Agent（同步 invoke 下并行节点跑在线程池），
因此 SQLite 连接加锁；查询走 GET /api/traces/{id}，也是后续
评测框架消费的关键行为输出的原始来源。
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    node TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    latency_ms REAL NOT NULL DEFAULT 0,
    token_usage INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trace_events ON trace_events(trace_id);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
"""

EVENT_TYPES = (
    "plan", "dispatch", "tool_call", "tool_result", "retry", "degrade",
    "merge", "verify", "memory_recall", "synthesize", "persist", "usage", "error",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TraceEvent(BaseModel):
    seq: int
    ts: str
    node: str
    event_type: str
    payload: dict = Field(default_factory=dict)
    latency_ms: float = 0.0
    token_usage: int = 0


class TraceStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._seq: dict[str, int] = {}

    def record(
        self,
        trace_id: str,
        node: str,
        event_type: str,
        payload: dict | None = None,
        *,
        latency_ms: float = 0.0,
        token_usage: int = 0,
    ) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"未知事件类型: {event_type}")
        with self._lock:
            seq = self._seq.get(trace_id, 0) + 1
            self._seq[trace_id] = seq
            self._conn.execute(
                "INSERT INTO trace_events(trace_id, seq, ts, node, event_type, payload, latency_ms, token_usage)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (trace_id, seq, _now(), node, event_type,
                 json.dumps(payload or {}, ensure_ascii=False), latency_ms, token_usage),
            )
            self._conn.commit()

    def get(self, trace_id: str) -> list[TraceEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, node, event_type, payload, latency_ms, token_usage"
                " FROM trace_events WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            ).fetchall()
        return [
            TraceEvent(seq=r[0], ts=r[1], node=r[2], event_type=r[3],
                       payload=json.loads(r[4]), latency_ms=r[5], token_usage=r[6])
            for r in rows
        ]

    # ---------- 报告持久化（运行产物库：events + reports 同库） ----------

    def save_report(self, report) -> None:
        from datetime import datetime, timezone

        with self._lock:
            self._conn.execute(
                "INSERT INTO reports(report_id, question, created_at, body) VALUES(?,?,?,?) "
                "ON CONFLICT(report_id) DO UPDATE SET body=excluded.body",
                (report.report_id, report.question,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 report.model_dump_json()),
            )
            self._conn.commit()

    def get_report(self, report_id: str):
        from app.schema.report import Report

        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        return Report.model_validate_json(row[0]) if row else None

    def close(self) -> None:
        self._conn.close()
