"""图组装与运行时资源管理。

build_default_app 是组合根（composition root）：按 Settings 装配
检索器、KBClient（MCP 或进程内）、搜索 Provider、LLM Provider、
记忆库并建图。返回 ResearchRuntime 统一管理资源生命周期
（MCP 子进程与 SQLite 连接需要显式关闭）。

MCP 化前后图结构不变：把 InProcessKBClient 换成 McpKBClient 即切换
检索路的实现方式，子 Agent 与主图零改动。
"""

from dataclasses import dataclass
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import as_graph_node
from app.agents.rag_agent import build_rag_agent
from app.agents.web_agent import build_web_agent
from app.config import Settings
from app.domain_registry import DomainRegistry
from app.graph.nodes import (
    make_dispatch_router,
    make_load_context_node,
    make_merge_node,
    make_persist_node,
    make_plan_node,
    make_synthesize_node,
    make_verify_node,
    route_after_verify,
)
from app.graph.state import ResearchState
from app.llm.base import LLMProvider
from app.llm.heuristic import HeuristicProvider
from app.memory.store import MemoryStore
from app.tools.kb_client import InProcessKBClient, KBClient
from app.tools.search import DemoSearchProvider
from knowledge_base.retriever import KBRetriever


@dataclass
class ResearchRuntime:
    """一次装配的全部资源：图 + 需要显式释放的依赖。"""

    graph: CompiledStateGraph
    settings: Settings
    memory: MemoryStore
    kb_client: KBClient

    def close(self) -> None:
        close_kb = getattr(self.kb_client, "close", None)
        if close_kb is not None:
            close_kb()
        self.memory.close()


def build_research_app(
    *,
    settings: Settings,
    kb_client: KBClient,
    search_provider: DemoSearchProvider,
    llm_provider: LLMProvider,
    registry: DomainRegistry,
    memory_store: MemoryStore,
) -> CompiledStateGraph:
    graph = StateGraph(ResearchState)

    graph.add_node("load_context", make_load_context_node(settings, memory_store))
    graph.add_node("plan", make_plan_node(llm_provider, settings))
    graph.add_node("rag_agent", as_graph_node(build_rag_agent(kb_client, settings)))
    graph.add_node("web_agent", as_graph_node(build_web_agent(search_provider, settings)))
    graph.add_node("merge", make_merge_node(settings))
    graph.add_node("verify", make_verify_node(registry, settings))
    graph.add_node("synthesize", make_synthesize_node(llm_provider, settings))
    graph.add_node("persist", make_persist_node(memory_store))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "plan")
    graph.add_conditional_edges("plan", make_dispatch_router(settings), {"synthesize": "synthesize"})
    graph.add_edge("rag_agent", "merge")
    graph.add_edge("web_agent", "merge")
    graph.add_edge("merge", "verify")
    graph.add_conditional_edges(
        "verify",
        lambda s: route_after_verify(s, settings),
        {"plan": "plan", "synthesize": "synthesize"},
    )
    graph.add_edge("synthesize", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


def _build_llm_provider(settings: Settings, registry: DomainRegistry) -> LLMProvider:
    if settings.offline or not settings.llm_api_key:
        return HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    from app.llm.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        registry=registry,
    )


def _build_kb_client(settings: Settings, retriever: KBRetriever) -> KBClient:
    if settings.mcp_kb_enabled:
        from app.mcp_client.client import McpKBClient

        return McpKBClient(
            corpus_dir=settings.kb_corpus_dir,
            connect_timeout=settings.mcp_connect_timeout,
            call_timeout=settings.mcp_call_timeout,
        )
    return InProcessKBClient(retriever)


def build_default_app(settings: Settings | None = None) -> ResearchRuntime:
    settings = settings or Settings()
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever.docs)
    kb_client = _build_kb_client(settings, retriever)
    search_provider = DemoSearchProvider.from_dir(settings.web_demo_dir)
    memory_store = MemoryStore(settings.memory_db_path)
    llm_provider = _build_llm_provider(settings, registry)

    graph = build_research_app(
        settings=settings,
        kb_client=kb_client,
        search_provider=search_provider,
        llm_provider=llm_provider,
        registry=registry,
        memory_store=memory_store,
    )
    return ResearchRuntime(graph=graph, settings=settings, memory=memory_store, kb_client=kb_client)
