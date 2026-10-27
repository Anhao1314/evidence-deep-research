"""知识库 RAG 子 Agent：检索（经 KBClient）-> 证据抽取。

M1 走 InProcessKBClient；M2 知识库检索迁入自研 MCP Server 后，
仅需把 KBClient 实现换成 McpKBClient（stdio 跨进程），子图逻辑零改动。
"""

from langgraph.graph.state import CompiledStateGraph

from app.agents.base import SubAgentState, build_retrieval_agent, fetch_with_retry, first_sentence, route_failure
from app.config import Settings
from app.schema.evidence import Evidence, Source
from app.tools.kb_client import KBClient

RAG_TOP_K = 8  # 覆盖单对象全部研究维度（4 aspect）+ 对比性基准文档，保证领域内问题首轮收敛


def build_rag_agent(kb_client: KBClient, settings: Settings) -> CompiledStateGraph:
    def retrieve(state: SubAgentState) -> dict:
        subtask = state["subtask"]
        result, error = fetch_with_retry(
            lambda: kb_client.search(subtask.query, top_k=RAG_TOP_K, topic=subtask.topic or None),
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
                    ),
                    claim=first_sentence(doc.text),
                    quote=doc.text[: settings.evidence_quote_max_chars],
                    topic=subtask.topic or doc.topic,
                    # 文档自身元数据优先：检索常命回对比文档，对象归属以语料标注为准
                    object_name=doc.object_name or subtask.object_name,
                    aspect=doc.aspect or subtask.aspect,
                    route="kb",
                    score=hit.score,
                )
            )
        return {"evidences": evidences}

    return build_retrieval_agent(retrieve_fn=retrieve, extract_fn=extract)
