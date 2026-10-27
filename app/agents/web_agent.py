"""Web 搜索子 Agent：搜索（经 SearchProvider）-> 证据抽取。

检索事件（tool_call/tool_result/retry/degrade）落 trace。
"""

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
from app.tools.search import SearchProvider
from app.trace.store import TraceStore

WEB_TOP_K = 4


def build_web_agent(
    search_provider: SearchProvider, settings: Settings, trace_store: TraceStore | None = None
) -> CompiledStateGraph:
    def retrieve(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        trace_id = state.get("trace_id")

        def _trace(event_type: str, payload: dict) -> None:
            if trace_store is not None and trace_id:
                trace_store.record(trace_id, node="web_agent", event_type=event_type, payload=payload)

        def _on_retry(attempt: int, error: str) -> None:
            _trace("retry", {"route": "web", "subtask_id": subtask.subtask_id, "attempt": attempt, "error": error})

        _trace("tool_call", {"route": "web", "tool": "web_search", "subtask_id": subtask.subtask_id,
                             "query": subtask.query, "top_k": WEB_TOP_K})
        started = time.monotonic()
        result, error = fetch_with_retry(
            lambda: search_provider.search(subtask.query, top_k=WEB_TOP_K, topic=subtask.topic or None),
            max_retries=settings.tool_max_retries,
            base_delay=settings.tool_retry_base_delay,
            on_retry=_on_retry,
        )
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        if error is not None:
            _trace("degrade", {"route": "web", "subtask_id": subtask.subtask_id, "latency_ms": latency_ms,
                               "error": error})
            return {"hits": [], "route_failures": [route_failure(subtask, error)]}
        _trace("tool_result", {"route": "web", "tool": "web_search", "hits": len(result or []),
                               "latency_ms": latency_ms})
        return {"hits": result or [], "attempts": settings.tool_max_retries + 1}

    def extract(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        evidences = []
        for hit in state.get("hits", []):
            claim = hit.snippet or first_sentence(hit.content)
            evidences.append(
                Evidence(
                    evidence_id="",
                    source=Source(
                        source_id="",
                        kind="web",
                        title=hit.title,
                        url=hit.url,
                        locator=hit.published or None,
                    ),
                    claim=claim,
                    quote=hit.content[: settings.evidence_quote_max_chars],
                    topic=subtask.topic or hit.topic,
                    object_name=hit.object_name or subtask.object_name,
                    aspect=hit.aspect or subtask.aspect,
                    route="web",
                    score=hit.score,
                )
            )
        return {"evidences": evidences}

    return build_retrieval_agent(retrieve_fn=retrieve, extract_fn=extract)
