"""MCP 知识库客户端：stdio 子进程 + 持久会话的同步桥接。

三个关键取舍（ADR 同款理由）：
1. 真实跨进程：server 以 `python -m mcp_server` 子进程启动，走 stdio，
   不是进程内函数调用——pytest 验证的就是这条真链路。
2. 持久会话：MCP handshake 有真实成本，client 生命周期内复用同一个
   ClientSession，而不是每次检索重新连接。
3. 线程桥接：KBClient 协议与 LangGraph 节点都是同步的，而 MCP Python
   SDK 是 async 的；用后台线程跑 event loop，同步调用经
   run_coroutine_threadsafe 提交，关闭时让 async context 正常展开、
   子进程干净退出（而不是 loop.stop 留下孤儿进程）。
"""

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from knowledge_base.corpus import KBDocument
from knowledge_base.retriever import ScoredDoc

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_KB_DOC_FIELDS = ("doc_id", "title", "section", "text", "topic", "object_name", "aspect", "source_url")


class McpKBClient:
    """KBClient 协议的 MCP 实现：search 经真实 MCP 工具调用完成。"""

    def __init__(
        self,
        *,
        corpus_dir: Path,
        connect_timeout: float = 30.0,
        call_timeout: float = 30.0,
        python_exe: str = sys.executable,
    ) -> None:
        self._params = StdioServerParameters(
            command=python_exe,
            args=["-m", "mcp_server"],
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "DR_KB_CORPUS_DIR": str(corpus_dir)},
        )
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-kb-client", daemon=True)
        self._session: ClientSession | None = None
        self._ready = threading.Event()
        self._stack: AsyncExitStack | None = None
        self._stop_event: asyncio.Event | None = None
        self._serve_future: asyncio.Future | None = None

        self._thread.start()
        self._serve_future = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        self._wait_ready()

    # ---------- 生命周期 ----------

    async def _serve(self) -> None:
        """建立会话后挂起保活；close 触发 stop_event 使 async with 正常展开。"""
        self._stop_event = asyncio.Event()
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(self._params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            self._stack = stack
            self._ready.set()
            await self._stop_event.wait()
        self._session = None

    def _wait_ready(self) -> None:
        if self._ready.wait(self._connect_timeout):
            return
        if self._serve_future is not None and self._serve_future.done():
            exc = self._serve_future.exception()
            if exc is not None:
                raise ConnectionError(f"MCP 知识库服务连接失败: {exc}") from exc
        raise TimeoutError(f"MCP 知识库服务连接超时（{self._connect_timeout}s）")

    def close(self) -> None:
        if not self._thread.is_alive() or self._stop_event is None:
            return

        async def _shutdown() -> None:
            self._stop_event.set()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
        if self._serve_future is not None:
            try:
                self._serve_future.result(timeout=5)
            except Exception:  # noqa: BLE001 —— 关闭路径尽力而为
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ---------- KBClient 协议 ----------

    def search(self, query: str, top_k: int = 4, *, topic: str | None = None) -> list[ScoredDoc]:
        self._wait_ready()
        future = asyncio.run_coroutine_threadsafe(
            self._call("kb_search", {"query": query, "top_k": top_k, "topic": topic}), self._loop
        )
        payload = future.result(timeout=self._call_timeout)
        docs = []
        for row in payload:
            doc = KBDocument(**{k: row.get(k) for k in _KB_DOC_FIELDS})
            docs.append(ScoredDoc(doc=doc, score=row.get("score", 0.0)))
        return docs

    def get_doc(self, doc_id: str) -> dict:
        self._wait_ready()
        future = asyncio.run_coroutine_threadsafe(self._call("kb_get_doc", {"doc_id": doc_id}), self._loop)
        return future.result(timeout=self._call_timeout)

    # ---------- MCP 调用 ----------

    async def _call(self, tool: str, arguments: dict) -> Any:
        if self._session is None:
            raise ConnectionError("MCP 会话已关闭")
        result = await self._session.call_tool(tool, arguments)
        if getattr(result, "isError", False):
            raise RuntimeError(f"MCP 工具 {tool} 返回错误: {result.content}")
        return self._extract_payload(result)

    @staticmethod
    def _extract_payload(result: Any) -> Any:
        """聚合工具返回：优先 structuredContent，回退到逐个解析 TextContent。

        mcp 2.x 会把 list[dict] 返回值拆成多个 TextContent（每个元素一段 JSON），
        必须全部聚合；单个 dict（如 kb_get_doc）则原样返回。
        """
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        parsed = [json.loads(item.text) for item in result.content if isinstance(item, TextContent)]
        if not parsed:
            raise ValueError("MCP 工具返回为空")
        if len(parsed) == 1:
            return parsed[0]
        return parsed
