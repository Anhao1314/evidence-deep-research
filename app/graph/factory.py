"""图组装：依赖注入 + 编译。

build_default_app 是组合根（composition root）：按 Settings 装配
检索器、KBClient、搜索 Provider、LLM Provider 并建图。
M2 的 MCP 化只需把 InProcessKBClient 换成 McpKBClient，图结构不变。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import as_graph_node
from app.agents.rag_agent import build_rag_agent
from app.agents.web_agent import build_web_agent
from app.config import Settings
from app.domain_registry import DomainRegistry
from app.graph.nodes import (  # noqa: F401
    make_dispatch_router,
    make_load_context_node,
    make_merge_node,
    make_plan_node,
    make_synthesize_node,
    make_verify_node,
    route_after_verify,
)
from app.graph.state import ResearchState
from app.llm.base import LLMProvider
from app.llm.heuristic import HeuristicProvider
from app.tools.kb_client import InProcessKBClient, KBClient
from app.tools.search import DemoSearchProvider
from knowledge_base.corpus import load_corpus
from knowledge_base.retriever import KBRetriever


def build_research_app(
    *,
    settings: Settings,
    kb_client: KBClient,
    search_provider: DemoSearchProvider,
    llm_provider: LLMProvider,
    registry: DomainRegistry,
) -> CompiledStateGraph:
    graph = StateGraph(ResearchState)

    graph.add_node("load_context", make_load_context_node(settings))
    graph.add_node("plan", make_plan_node(llm_provider, settings))
    graph.add_node("rag_agent", as_graph_node(build_rag_agent(kb_client, settings)))
    graph.add_node("web_agent", as_graph_node(build_web_agent(search_provider, settings)))
    graph.add_node("merge", make_merge_node(settings))
    graph.add_node("verify", make_verify_node(registry, settings))
    graph.add_node("synthesize", make_synthesize_node(llm_provider, settings))

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
    graph.add_edge("synthesize", END)
    return graph.compile()


def build_default_app(settings: Settings | None = None) -> CompiledStateGraph:
    settings = settings or Settings()
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever.docs)
    kb_client = InProcessKBClient(retriever)
    search_provider = DemoSearchProvider.from_dir(settings.web_demo_dir)

    if settings.offline or not settings.llm_api_key:
        llm_provider: LLMProvider = HeuristicProvider(registry, max_subtasks=settings.max_subtasks)
    else:
        from app.llm.openai_compat import OpenAICompatProvider

        llm_provider = OpenAICompatProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            registry=registry,
        )

    return build_research_app(
        settings=settings,
        kb_client=kb_client,
        search_provider=search_provider,
        llm_provider=llm_provider,
        registry=registry,
    )
