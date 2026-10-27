"""Web 搜索子 Agent：搜索（经 SearchProvider）-> 证据抽取。

M1 用 DemoSearchProvider（内置演示网页语料，离线可跑）；
在线搜索 Provider（Tavily 等）实现同一协议后在 M3 接入。
"""

from langgraph.graph.state import CompiledStateGraph

from app.agents.base import SubAgentState, build_retrieval_agent, fetch_with_retry, first_sentence, route_failure
from app.config import Settings
from app.schema.evidence import Evidence, Source
from app.tools.search import SearchProvider

WEB_TOP_K = 4


def build_web_agent(search_provider: SearchProvider, settings: Settings) -> CompiledStateGraph:
    def retrieve(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        result, error = fetch_with_retry(
            lambda: search_provider.search(subtask.query, top_k=WEB_TOP_K, topic=subtask.topic or None),
            max_retries=settings.tool_max_retries,
            base_delay=settings.tool_retry_base_delay,
        )
        if error is not None:
            return {"hits": [], "route_failures": [route_failure(subtask, error)]}
        return {"hits": result, "attempts": settings.tool_max_retries + 1}

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
