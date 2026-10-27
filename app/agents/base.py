"""子 Agent 公共骨架：统一 state 形状、重试与失败降级约定。

子 Agent 是可独立 invoke 的 LangGraph 子图（retrieve -> extract 两个节点）：
- 作为主图节点时，只向主图回写 evidences / route_failures 两个 key；
- 单独 invoke 时返回完整内部 state，便于独立测试与调试。
"""

import time
from typing import Callable, TypeVar

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from app.schema.plan import SubTask

T = TypeVar("T")


class SubAgentState(TypedDict, total=False):
    subtask: SubTask
    question: str
    hits: list  # 检索中间产物（ScoredDoc / WebHit）
    evidences: list  # list[Evidence]
    route_failures: list  # list[dict]
    attempts: int


def fetch_with_retry(
    fetch: Callable[[], T], *, max_retries: int, base_delay: float
) -> tuple[T | None, str | None]:
    """指数退避重试：成功返回 (结果, None)；耗尽后返回 (None, 错误描述)。

    失败不上抛：由调用方写 route_failures 走降级路径，不阻塞主图。
    """
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            return fetch(), None
        except Exception as exc:  # noqa: BLE001 —— 工具失败一律降级，不区分异常类型
            last_error = f"第{attempt + 1}次尝试失败: {exc}"
            if attempt < max_retries:
                time.sleep(base_delay * (2**attempt))
    return None, last_error


def route_failure(subtask: SubTask, error: str) -> dict:
    return {
        "route": subtask.route,
        "subtask_id": subtask.subtask_id,
        "query": subtask.query,
        "error": error,
    }


def first_sentence(text: str, limit: int = 120) -> str:
    """取第一条完整句子；句子超长才在 limit 处硬截。

    只认中文句读（。；！？与英文分号），不含英文句点——避免把
    版本号（v1.0）与小数（2.0）误判为句界；英文长句走长度截断。
    """
    positions = [p for p in (text.find(sep) for sep in ("。", "；", "！", "？", ";")) if p != -1]
    if positions:
        sentence = text[: min(positions) + 1]
    else:
        sentence = text
    return sentence[:limit]


def build_retrieval_agent(
    *, retrieve_fn: Callable, extract_fn: Callable
) -> CompiledStateGraph:
    graph = StateGraph(SubAgentState)
    graph.add_node("retrieve", retrieve_fn)
    graph.add_node("extract", extract_fn)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "extract")
    graph.add_edge("extract", END)
    return graph.compile()


def as_graph_node(compiled_subgraph: CompiledStateGraph) -> Callable[[dict], dict]:
    """把子图包装成主图节点：只向 raw_evidences / route_failures 两个 reducer 通道回写。

    直接把编译子图挂为主图节点会让并行实例回写全部内部 state（如 question），
    触发 LastValue 通道并发更新冲突；包装后子图内部状态完全私有，
    多个并行实例的产出经 operator.add 汇聚（map-reduce）。
    """

    def run(payload: dict) -> dict:
        final = compiled_subgraph.invoke(
            {"subtask": payload["subtask"], "question": payload.get("question", "")}
        )
        return {
            "raw_evidences": final.get("evidences", []),
            "route_failures": final.get("route_failures", []),
        }

    return run
