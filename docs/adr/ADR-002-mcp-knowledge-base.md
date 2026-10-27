# ADR-002: 知识库检索 MCP Server 化（stdio 子进程）

状态：已采纳 | 日期：2026-10-28

## 背景

知识库检索是最核心的工具能力。它可以做成进程内函数（最省事）、HTTP 服务、或标准 MCP Server。简历能力点要求证明"工具系统"的工程化程度——糊弄的进程内调用经不起追问。

## 决策

用 MCP 官方 Python SDK（2.x `MCPServer`）实现独立知识库服务（`mcp_server/`），暴露 `kb_search` / `kb_get_doc` 两个工具，stdio transport；主流程经 `McpKBClient` 以 `python -m mcp_server` **子进程**方式连接，持久会话复用。

## 理由

- **真实跨进程**：pytest 验证的就是标准 MCP 协议链路（handshake → tools/call），不是进程内函数的近似；
- **stdio 而非 HTTP**：单机部署零端口暴露、子进程生命周期与主进程绑定（Runtime.close() 干净回收）、无网络栈开销；
- **持久会话**：MCP handshake 有真实成本（子进程 spawn + initialize），ClientSession 在客户端生命周期内复用，而不是每次检索重连；
- **协议换解耦**：`KBClient` 协议下 `InProcessKBClient`（测试加速）/ `McpKBClient`（生产行为）可配置切换，主图与子 Agent 零改动。

## 关键实现细节

- MCP SDK 是 async 的而 `KBClient` 协议与 LangGraph 节点是同步的：后台线程跑 event loop，同步调用经 `run_coroutine_threadsafe` 提交；关闭时 set stop 事件让 async context 正常展开，子进程干净退出（直接 `loop.stop()` 会留孤儿进程）；
- mcp 2.x 把 `list[dict]` 返回值拆成多个 `TextContent`（每元素一段 JSON），解析端必须逐段聚合，不能只取第一段。

## 后果

+ 工具系统的标准协议证明，第三方 MCP 客户端（如 Claude/其他 Agent）也能直接接这个知识库；
+ 测试里 `InProcessKBClient` 保证离线秒级，MCP 真链路有专门测试覆盖；
− 多了一个子进程与协议层，调试时需要同时看两端日志（trace 的 tool_call/tool_result 事件缓解了这一点）。
