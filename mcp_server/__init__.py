"""自研知识库 MCP Server。

把知识库检索做成标准 MCP 工具服务（stdio transport），主流程作为
MCP Client 跨进程调用——工具系统的能力证明，而非进程内函数调用。
启动：python -m mcp_server
"""
