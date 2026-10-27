# ADR-001: 子 Agent 用 LangGraph Send API 子图而非独立进程

状态：已采纳 | 日期：2026-10-28

## 背景

Planner 需要把研究问题分解为 N 个检索子任务并**并行**执行，子 Agent 有两种形态可选：独立进程（每任务一个 worker）或同进程子图（LangGraph Send API 动态扇出）。

## 决策

子 Agent 实现为可独立 invoke 的 LangGraph 子图（`retrieve → extract` 两个节点），通过条件边 router 返回 `Send` 列表动态扇出；编译子图经 `as_graph_node` 包装后挂入主图。

## 理由

- 子 Agent 的成本在检索与 LLM 调用（秒级），进程隔离只带来序列化开销与状态同步负担；
- Send 的 map-reduce 语义原生匹配"扇出-汇聚"：并行实例只回写 `raw_evidences` / `route_failures` 两个 reducer 通道（`Annotated[list, operator.add]`），合并零手写；
- 子图单独 `invoke` 返回完整内部 state，独立测试与调试不需要任何桩；
- 进程边界留给真正需要独立生命周期的知识库（MCP 子进程，见 ADR-002），职责边界更清晰。

## 踩坑记录（对后来者有用）

- LangGraph 1.x 中 `Send` 列表**必须从条件边 router 返回**，普通节点返回 Send 会 `InvalidUpdateError`（旧教程写法已失效）——"派发"因此实现为 plan 出边上的动作而非独立节点；
- 编译子图直接挂为主图节点时，并行实例会回写全部内部 state（含 `question`），触发 LastValue 通道并发冲突——`as_graph_node` 包装层让子图内部状态完全私有，只透传 reducer 通道。

## 后果

+ 并行零样板、状态合并原生、测试友好；
− 子 Agent 不能独立伸缩/跨机（当前单机场景不需要；若需要，把 `as_graph_node` 换成远程调用节点即可，子图逻辑不变）。
