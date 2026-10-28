"""知识库 RAG 子 Agent：检索（经 KBClient，真实 MCP 调用）-> 证据抽取。

检索事件（tool_call/tool_result/retry/degrade）落 trace；
M2 前走 InProcessKBClient，MCP 化后本子图零改动——事件名不变。
"""

import hashlib
import time

from langgraph.graph.state import CompiledStateGraph

from app.agents.base import (
    SubAgentState,
    build_retrieval_agent,
    fetch_with_retry,
    first_sentence,
    route_failure,
)
from app.config import Settings
from app.schema.evidence import Evidence, Source
from app.tools.kb_client import KBClient
from app.trace.store import TraceStore

RAG_TOP_K = 8  # 默认值；fixture/特殊语料可通过 Settings.rag_top_k 调整


def build_rag_agent(
    kb_client: KBClient, settings: Settings, trace_store: TraceStore | None = None
) -> CompiledStateGraph:
    def retrieve(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        trace_id = state.get("trace_id")

        def _trace(event_type: str, payload: dict) -> None:
            if trace_store is not None and trace_id:
                trace_store.record(trace_id, node="rag_agent", event_type=event_type, payload=payload)

        def _on_retry(attempt: int, error: str) -> None:
            _trace("retry", {"route": "kb", "subtask_id": subtask.subtask_id, "attempt": attempt, "error": error})

        _trace("tool_call", {"route": "kb", "tool": "kb_search", "subtask_id": subtask.subtask_id,
                             "query": subtask.query, "top_k": settings.rag_top_k,
                             "constraints": [c.constraint_id for c in state.get("constraints", [])]})
        started = time.monotonic()
        result, error = fetch_with_retry(
            lambda: kb_client.search(subtask.query, top_k=settings.rag_top_k,
                                     topic=subtask.topic or None),
            max_retries=settings.tool_max_retries,
            base_delay=settings.tool_retry_base_delay,
            on_retry=_on_retry,
        )
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        if error is not None:
            _trace("degrade", {"route": "kb", "subtask_id": subtask.subtask_id, "latency_ms": latency_ms,
                               "error": error})
            return {"hits": [], "route_failures": [route_failure(subtask, error)]}
        _trace("tool_result", {"route": "kb", "tool": "kb_search", "hits": len(result or []),
                               "latency_ms": latency_ms})
        return {"hits": result or [], "attempts": settings.tool_max_retries + 1}

    def extract(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        evidences = []
        for hit in state.get("hits", []):
            doc = hit.doc
            evidences.append(
                Evidence(
                    evidence_id="",  # 全局编号在主图 merge 节点统一分配
                    source=Source(
                        source_id="",
                        kind="kb",
                        title=doc.title,
                        doc_id=doc.doc_id,
                        locator=doc.section or None,
                        url=None,
                        content_hash=hashlib.sha256(doc.text.encode("utf-8")).hexdigest(),
                        resolution="kb_document",
                    ),
                    claim=first_sentence(doc.text),
                    quote=doc.text[: settings.evidence_quote_max_chars],
                    topic=subtask.topic or doc.topic,
                    # 文档自身元数据优先：检索常命回对比文档，对象归属以语料标注为准
                    object_name=doc.object_name or subtask.object_name,
                    aspect=doc.aspect or subtask.aspect,
                    route="kb",
                    score=hit.score,
                    attributes=doc.attributes,
                )
            )
        return {"evidences": evidences}

    return build_retrieval_agent(retrieve_fn=retrieve, extract_fn=extract)
