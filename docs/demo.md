# 演示样例（真实运行产物，自动生成）

本页由 `python scripts/capture_demo.py` 生成，**不要手工编辑**。页面中的报告正文、事件流与计数全部来自 `docs/artifacts/demo/` 下的运行产物：

| 产物 | 路径 |
| --- | --- |
| 正常路径报告 | [`happy/report.md`](artifacts/demo/happy/report.md) |
| 正常路径 Trace | [`happy/trace.json`](artifacts/demo/happy/trace.json) |
| 信息不足报告 | [`insufficient/report.md`](artifacts/demo/insufficient/report.md) |
| 失败路径报告 | [`failure/report.md`](artifacts/demo/failure/report.md) |
| 失败路径 Trace | [`failure/trace.json`](artifacts/demo/failure/trace.json) |
| 约束型问题报告 | [`constraints/report.md`](artifacts/demo/constraints/report.md) |
| Replan 报告 | [`replan/report.md`](artifacts/demo/replan/report.md) |
| Guard 报告 | [`guard/report.md`](artifacts/demo/guard/report.md) |
| 运行清单 | [`manifest.json`](artifacts/demo/manifest.json) |

## 生成环境

| 字段 | 值 |
| --- | --- |
| 生成时间 | `2026-10-28T19:28:38+00:00` |
| Git commit | `f482c387924b39b164beb2a15b46d37a4b83293a` |
| 工作区有未提交改动 | `True` |
| Python | `3.12.14` |
| 平台 | `macOS-26.6-arm64-arm-64bit` |
| 运行模式 | `offline`（启发式规划器 + 内置/合成语料，0 token） |
| 源码树指纹 | `7a22bef99f3005bdf6e4c88d41e737391c00d930579aa714bf1485ab0737e97c` |

复现：

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/capture_demo.py
```

## 场景一：正常路径（真实 MCP 知识库 + 并行扇出）

**问题**：RAG 系统的向量库选 Qdrant 还是 Milvus？

知识库后端：`McpKBClient`；检索路：`['kb', 'web']`；证据 13 条；来源 13 条；计划轮数 1；降级 False。

### 真实报告正文

```text
# 技术选型研究报告

> 研究问题：RAG 系统的向量库选 Qdrant 还是 Milvus？

## 结论摘要

**Qdrant**：性能：Qdrant 单机即可承载千万级向量，分片集群用于横向扩展读写吞吐。 [E8]；生态：Qdrant 提供官方 Python/Go/Rust/JS 等 10+ 语言客户端，与 LangChain、LlamaIndex 均有官方维护的 VectorStore 适配器。 [E10]；成本：Qdrant 采用 Apache-2.0 协议，开源版无功能阉割。 [E11]；成熟度：Qdrant 2017 年启动，2021 年 v1.0 前 API 曾有破坏性变更，1.x 后保持稳定版本节奏（约每月一版）。 [E12]。

**Milvus**：性能：VectorDBBench 2025 年快照（演示整理）：千万级 768 维数据集下，Qdrant 单机 QPS 约 1200-1800，Milvus 集群 QPS 约 2000-4000（6 节点）； [E1]；生态：Milvus 是 LF AI & DATA 基金会毕业项目，GitHub Star 超过 30k。 [E4]；成本：Milvus 分布式版依赖 etcd、对象存储（MinIO/S3）与消息队列（Pulsar/Kafka）三个外部组件，最小生产集群通常需要 6-10 个节点规格。 [E5]；成熟度：Milvus 2019 年开源，是最早的国产开源向量数据库，银行、电信等大规模生产案例公开可查（亿级向量场景多）。 [E7]。

## 分维度对比

| 维度 | Qdrant | Milvus |
|---|---|---|
| 性能 | Qdrant 单机即可承载千万级向量，分片集群用于横向扩展读写吞吐。 [E8] | VectorDBBench 2025 年快照（演示整理）：千万级 768 维数据集下，Qdrant 单机 QPS 约 1200-1800，Milvus 集群 QPS 约 2000-4000（6 节点）； [E1] |
| 生态 | Qdrant 提供官方 Python/Go/Rust/JS 等 10+ 语言客户端，与 LangChain、LlamaIndex 均有官方维护的 VectorStore 适配器。 [E10] | Milvus 是 LF AI & DATA 基金会毕业项目，GitHub Star 超过 30k。 [E4] |
| 成本 | Qdrant 采用 Apache-2.0 协议，开源版无功能阉割。 [E11] | Milvus 分布式版依赖 etcd、对象存储（MinIO/S3）与消息队列（Pulsar/Kafka）三个外部组件，最小生产集群通常需要 6-10 个节点规格。 [E5] |
| 成熟度 | Qdrant 2017 年启动，2021 年 v1.0 前 API 曾有破坏性变更，1.x 后保持稳定版本节奏（约每月一版）。 [E12] | Milvus 2019 年开源，是最早的国产开源向量数据库，银行、电信等大规模生产案例公开可查（亿级向量场景多）。 [E7] |

## 证据明细

- **[E1]** VectorDBBench 2025 年快照（演示整理）：千万级 768 维数据集下，Qdrant 单机 QPS 约 1200-1800，Milvus 集群 QPS 约 2000-4000（6 节点）；（来源 [S1]：doc:kb-vdb-bench §5 基准 (kb)）
- **[E2]** Milvus 采用存算分离的云原生架构，支持十亿级向量的分布式检索。（来源 [S2]：doc:kb-milvus-perf §1 架构与延迟 (kb)）
- **[E3]** GPU 索引让亿级向量检索吞吐提升 10 倍量级。（来源 [S3]：https://milvus.io/blog/accelerate-milvus-with-cagra.md 2024-06-12 (web)）
- **[E4]** Milvus 是 LF AI & DATA 基金会毕业项目，GitHub Star 超过 30k。（来源 [S4]：doc:kb-milvus-eco §2 生态 (kb)）
- **[E5]** Milvus 分布式版依赖 etcd、对象存储（MinIO/S3）与消息队列（Pulsar/Kafka）三个外部组件，最小生产集群通常需要 6-10 个节点规格。（来源 [S5]：doc:kb-milvus-cost §3 成本 (kb)）
- **[E6]** 托管 Milvus 按存储与 CU 计费，起步价高于自托管。（来源 [S6]：https://zilliz.com/pricing 2025-01-15 (web)）
- **[E7]** Milvus 2019 年开源，是最早的国产开源向量数据库，银行、电信等大规模生产案例公开可查（亿级向量场景多）。（来源 [S7]：doc:kb-milvus-maturity §4 成熟度 (kb)）
- **[E8]** Qdrant 单机即可承载千万级向量，分片集群用于横向扩展读写吞吐。（来源 [S8]：https://qdrant.tech/documentation/guides/distributed_deployment/ 2025-03-01 (web)）
- **[E9]** Qdrant 以 Rust 实现，单节点默认使用 HNSW 索引，官方文档声称在 100 万条 768 维向量上 P99 查询延迟约 3-10ms（取决于过滤条件与硬件）。（来源 [S9]：doc:kb-qdrant-perf §1 架构与延迟 (kb)）
- **[E10]** Qdrant 提供官方 Python/Go/Rust/JS 等 10+ 语言客户端，与 LangChain、LlamaIndex 均有官方维护的 VectorStore 适配器。（来源 [S10]：doc:kb-qdrant-eco §2 生态 (kb)）
- **[E11]** Qdrant 采用 Apache-2.0 协议，开源版无功能阉割。（来源 [S11]：doc:kb-qdrant-cost §3 成本 (kb)）
- **[E12]** Qdrant 2017 年启动，2021 年 v1.0 前 API 曾有破坏性变更，1.x 后保持稳定版本节奏（约每月一版）。（来源 [S12]：doc:kb-qdrant-maturity §4 成熟度 (kb)）
- **[E13]** 社区共识：千万级以下选 Qdrant 省心，需要分布式与 GPU 再上 Milvus。（来源 [S13]：https://www.reddit.com/r/vector_database/comments/selection_2025/ 2025-05-20 (web)）

## 信息缺口

- 无（本轮检索已覆盖计划的全部维度）

## 参考来源

- **[S1]** 向量库公开基准快照（演示数据） — doc:kb-vdb-bench §5 基准 (kb)
- **[S2]** Milvus 性能架构（演示快照） — doc:kb-milvus-perf §1 架构与延迟 (kb)
- **[S3]** Accelerating Milvus with NVIDIA CAGRA GPU Index — https://milvus.io/blog/accelerate-milvus-with-cagra.md 2024-06-12 (web)
- **[S4]** Milvus 生态与集成（演示快照） — doc:kb-milvus-eco §2 生态 (kb)
- **[S5]** Milvus 部署与成本（演示快照） — doc:kb-milvus-cost §3 成本 (kb)
- **[S6]** Zilliz Cloud Pricing — https://zilliz.com/pricing 2025-01-15 (web)
- **[S7]** Milvus 成熟度（演示快照） — doc:kb-milvus-maturity §4 成熟度 (kb)
- **[S8]** Qdrant Documentation - Distributed Deployment — https://qdrant.tech/documentation/guides/distributed_deployment/ 2025-03-01 (web)
- **[S9]** Qdrant 性能架构（演示快照） — doc:kb-qdrant-perf §1 架构与延迟 (kb)
- **[S10]** Qdrant 生态与集成（演示快照） — doc:kb-qdrant-eco §2 生态 (kb)
- **[S11]** Qdrant 部署与成本（演示快照） — doc:kb-qdrant-cost §3 成本 (kb)
- **[S12]** Qdrant 成熟度（演示快照） — doc:kb-qdrant-maturity §4 成熟度 (kb)
- **[S13]** Qdrant vs Milvus for a 10M-vector RAG app? — https://www.reddit.com/r/vector_database/comments/selection_2025/ 2025-05-20 (web)
```

### 关键行为（从 Trace 计算）

- `dispatch` 扇出：4 个子任务；目标 ['rag_agent', 'web_agent', 'rag_agent', 'web_agent']
- `tool_call` 次数：4；`retry` 事件：0；`degrade` 事件：0
- `merge`：原始证据 22 条，去重后 13 条
- `verify`：缺口 0 条，replan_focus=空；最终计划轮数 1（是否 replan 还受 max_plan_rounds 护栏约束）
- `synthesize`：证据 13 条，引用 10 处，来源 13 条，降级=False
- Trace 事件总数：15；Token：0

### 完整 Trace 事件流

| seq | 节点 | 事件 | payload（截断） |
| --- | --- | --- | --- |
| 1 | `load_context` | `memory_recall` | `{"preferences": {}, "related_topics": 0, "snippets": 0}` |
| 2 | `plan` | `plan` | `{"plan_id": "plan-fbc65975", "subtasks": [{"subtask_id": "Te99453", "route": "kb", "topic": "vector-db", "object_name": "qdrant", "object_display": "Qdrant", "aspect": "general", "query": "Qdrant 向量数据…` |
| 3 | `dispatch` | `dispatch` | `{"fan_out": 4, "targets": ["rag_agent", "web_agent", "rag_agent", "web_agent"], "subtask_ids": ["Te99453", "T264dd8", "T728d0f", "Tc509bb"]}` |
| 4 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "Te99453", "query": "Qdrant 向量数据库", "top_k": 8, "constraints": []}` |
| 5 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "T264dd8", "query": "Qdrant 向量数据库", "top_k": 4, "constraints": []}` |
| 6 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "T728d0f", "query": "Milvus 向量数据库", "top_k": 8, "constraints": []}` |
| 7 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "Tc509bb", "query": "Milvus 向量数据库", "top_k": 4, "constraints": []}` |
| 8 | `web_agent` | `tool_result` | `{"route": "web", "tool": "web_search", "hits": 3, "latency_ms": 0.04}` |
| 9 | `web_agent` | `tool_result` | `{"route": "web", "tool": "web_search", "hits": 4, "latency_ms": 0.03}` |
| 10 | `rag_agent` | `tool_result` | `{"route": "kb", "tool": "kb_search", "hits": 7, "latency_ms": 18.84}` |
| 11 | `rag_agent` | `tool_result` | `{"route": "kb", "tool": "kb_search", "hits": 8, "latency_ms": 18.15}` |
| 12 | `merge` | `merge` | `{"raw_evidences": 22, "after_scope": 22, "deduped": 13, "after_budget_and_prefs": 13, "excluded_by_reason": {"duplicate_source": 9}, "excluded": [{"source_ref": "doc:kb-vdb-bench §5 基准 (kb)", "claim":…` |
| 13 | `verify` | `verify` | `{"gaps": [], "replan": false, "constraint_summary": {"verdict": "not_checked", "recommended_objects": [], "excluded": {}, "unknown_objects": [], "findings": [], "gaps": [], "checked_objects": []}}` |
| 14 | `synthesize` | `synthesize` | `{"markdown_chars": 3996, "citations": 10, "evidences": 13, "sources": 13, "degraded": false, "guard_ran": false, "guard_quarantined": 0, "guard_fallback": false, "constraint_verdict": "not_checked"}` |
| 15 | `persist` | `persist` | `{"topic_recorded": true, "snippets": 13}` |

## 场景二：信息不足（无关证据被排除）

**问题**：怎么评估一个开源项目的社区健康度？

该问题不在内置知识域内。检索层确实返回了文档，但范围门把它们标记为 `unscoped_question` 并在 merge 阶段排除；原始结果保留在 Trace，最终报告不把无关证据包装成研究依据。

- 检索到并写入 disposition：12 条
- 采用：0；排除：12；排除原因：['unscoped_question']
- 最终证据 0 条，来源 0 条，信息缺口 1 条

### 结论摘要（真实报告片段）

```text
## 结论摘要

本轮检索未获得可用证据，无法给出有依据的结论。
```

### 信息缺口（真实报告片段）

```text
## 信息缺口

- 问题未包含可核实的研究对象，或当前知识域未覆盖该问题；本轮为泛化检索，结论仅作线索，需补充对象、约束与时间范围后再判断。
```

完整报告：[`insufficient/report.md`](artifacts/demo/insufficient/report.md)。该场景只证明预置 fixture 下的证据排除行为，不是在线通用语义相关率。

Trace：11 个事件，Token 0；完整事件流见 [`insufficient/trace.json`](artifacts/demo/insufficient/trace.json)。

## 场景三：真实失败（显式故障注入）

**问题**：Qdrant 和 Milvus 哪个更适合生产环境？

该场景由脚本显式注入两路检索故障：

- KB：`ConnectionError('demo fault injection: MCP KB connection reset')`
- Web：`TimeoutError('demo fault injection: search API timeout')`
- `tool_max_retries=1`

> 故障由脚本显式注入，用于展示重试、降级、缺口与 Trace；不代表真实线上事故。

即使两路都失败，主图仍产出报告：没有证据时明确说“无法给出有依据的结论”，并把失败写入信息缺口，而不是包装成成功。

### 真实报告正文

```text
# 技术选型研究报告

> 研究问题：Qdrant 和 Milvus 哪个更适合生产环境？

## 结论摘要

本轮检索未获得可用证据，无法给出有依据的结论。

## 信息缺口

- Qdrant 缺少维度证据：性能、生态、成本、成熟度
- Milvus 缺少维度证据：性能、生态、成本、成熟度
- kb 路检索失败（已降级）：Qdrant 向量数据库 — 第2次尝试失败: demo fault injection: MCP KB connection reset
- web 路检索失败（已降级）：Qdrant 向量数据库 — 第2次尝试失败: demo fault injection: search API timeout
- kb 路检索失败（已降级）：Milvus 向量数据库 — 第2次尝试失败: demo fault injection: MCP KB connection reset
- web 路检索失败（已降级）：Milvus 向量数据库 — 第2次尝试失败: demo fault injection: search API timeout
```

### 关键行为（从 Trace 计算）

- `dispatch` 扇出：4 个子任务；目标 ['rag_agent', 'web_agent', 'rag_agent', 'web_agent']
- `tool_call` 次数：4；`retry` 事件：8；`degrade` 事件：4
- `merge`：原始证据 0 条，去重后 0 条
- `verify`：缺口 6 条，replan_focus=非空；最终计划轮数 1（是否 replan 还受 max_plan_rounds 护栏约束）
- `synthesize`：证据 0 条，引用 0 处，来源 0 条，降级=True
- Trace 事件总数：23；Token：0

### 完整 Trace 事件流

| seq | 节点 | 事件 | payload（截断） |
| --- | --- | --- | --- |
| 1 | `load_context` | `memory_recall` | `{"preferences": {}, "related_topics": 0, "snippets": 0}` |
| 2 | `plan` | `plan` | `{"plan_id": "plan-3532e8f7", "subtasks": [{"subtask_id": "T304f94", "route": "kb", "topic": "vector-db", "object_name": "qdrant", "object_display": "Qdrant", "aspect": "general", "query": "Qdrant 向量数据…` |
| 3 | `dispatch` | `dispatch` | `{"fan_out": 4, "targets": ["rag_agent", "web_agent", "rag_agent", "web_agent"], "subtask_ids": ["T304f94", "Tc7f6a6", "T3553bb", "Tbbb558"]}` |
| 4 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "T304f94", "query": "Qdrant 向量数据库", "top_k": 8, "constraints": []}` |
| 5 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "Tc7f6a6", "query": "Qdrant 向量数据库", "top_k": 4, "constraints": []}` |
| 6 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "T3553bb", "query": "Milvus 向量数据库", "top_k": 8, "constraints": []}` |
| 7 | `rag_agent` | `retry` | `{"route": "kb", "subtask_id": "T304f94", "attempt": 0, "error": "第1次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 8 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "Tbbb558", "query": "Milvus 向量数据库", "top_k": 4, "constraints": []}` |
| 9 | `web_agent` | `retry` | `{"route": "web", "subtask_id": "Tc7f6a6", "attempt": 0, "error": "第1次尝试失败: demo fault injection: search API timeout"}` |
| 10 | `rag_agent` | `retry` | `{"route": "kb", "subtask_id": "T3553bb", "attempt": 0, "error": "第1次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 11 | `rag_agent` | `retry` | `{"route": "kb", "subtask_id": "T304f94", "attempt": 1, "error": "第2次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 12 | `web_agent` | `retry` | `{"route": "web", "subtask_id": "Tbbb558", "attempt": 0, "error": "第1次尝试失败: demo fault injection: search API timeout"}` |
| 13 | `web_agent` | `retry` | `{"route": "web", "subtask_id": "Tbbb558", "attempt": 1, "error": "第2次尝试失败: demo fault injection: search API timeout"}` |
| 14 | `rag_agent` | `retry` | `{"route": "kb", "subtask_id": "T3553bb", "attempt": 1, "error": "第2次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 15 | `rag_agent` | `degrade` | `{"route": "kb", "subtask_id": "T304f94", "latency_ms": 2.74, "error": "第2次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 16 | `web_agent` | `retry` | `{"route": "web", "subtask_id": "Tc7f6a6", "attempt": 1, "error": "第2次尝试失败: demo fault injection: search API timeout"}` |
| 17 | `web_agent` | `degrade` | `{"route": "web", "subtask_id": "Tbbb558", "latency_ms": 1.26, "error": "第2次尝试失败: demo fault injection: search API timeout"}` |
| 18 | `rag_agent` | `degrade` | `{"route": "kb", "subtask_id": "T3553bb", "latency_ms": 2.05, "error": "第2次尝试失败: demo fault injection: MCP KB connection reset"}` |
| 19 | `web_agent` | `degrade` | `{"route": "web", "subtask_id": "Tc7f6a6", "latency_ms": 4.73, "error": "第2次尝试失败: demo fault injection: search API timeout"}` |
| 20 | `merge` | `merge` | `{"raw_evidences": 0, "after_scope": 0, "deduped": 0, "after_budget_and_prefs": 0, "excluded_by_reason": {}, "excluded": [], "coverage": {}}` |
| 21 | `verify` | `verify` | `{"gaps": ["Qdrant 缺少维度证据：性能、生态、成本、成熟度", "Milvus 缺少维度证据：性能、生态、成本、成熟度", "kb 路检索失败（已降级）：Qdrant 向量数据库 — 第2次尝试失败: demo fault injection: MCP KB connection reset", "web 路检索失败（已降级）：Qdrant 向量数据库 — 第2次尝试失败: dem…` |
| 22 | `synthesize` | `synthesize` | `{"markdown_chars": 483, "citations": 0, "evidences": 0, "sources": 0, "degraded": true, "guard_ran": false, "guard_quarantined": 0, "guard_fallback": false, "constraint_verdict": "not_checked"}` |
| 23 | `persist` | `persist` | `{"topic_recorded": true, "snippets": 0}` |

## 场景四：用户约束真正影响结论（离线合成 fixture）

**问题**：我们已有 PostgreSQL 团队，准备上线 500 万条 768 维向量的 RAG，团队 5 人、自托管、预算有限，并尽量不引入新数据库。Atlas、Helios 和 pgvector 应该选哪个？

> 这些名称仅作为合成 fixture 中的评测对象，结构化属性为测试构造，不代表同名真实产品的真实能力，也不构成真实技术选型建议。

结构化约束核验结果：verdict=`recommend`，推荐=['pgvector']，排除={'atlas': ['C5'], 'helios': ['C2', 'C3', 'C5']}，未知=[]。

### 约束核对与结论（真实报告片段）

```text
## 约束核对

- [C1] 500 万条 768 维向量：Atlas 满足（支持上限 10,000,000 / 要求 5,000,000）；Helios 满足（支持上限 100,000,000 / 要求 5,000,000）；pgvector 满足（支持上限 10,000,000 / 要求 5,000,000）
- [C2] 5 人：Atlas 满足（最低团队 2 人 / 可用 5 人）；Helios 违反（最低团队 10 人 / 可用 5 人）；pgvector 满足（最低团队 2 人 / 可用 5 人）
- [C3] 低成本/预算有限：Atlas 满足（成本等级 0 / 上限 0）；Helios 违反（成本等级 2 / 上限 0）；pgvector 满足（成本等级 0 / 上限 0）
- [C4] 自托管：Atlas 满足（支持部署方式 ['managed-cloud', 'self-hosted'] / 要求 self-hosted）；Helios 满足（支持部署方式 ['self-hosted'] / 要求 self-hosted）；pgvector 满足（支持部署方式 ['self-hosted'] / 要求 self-hosted）
- [C5] 不引入新数据库/中间件：Atlas 违反（需要新基础设施=True）；Helios 违反（需要新基础设施=True）；pgvector 满足（需要新基础设施=False）

**约束结论**：在给定约束下，唯一满足全部可核验约束的是 pgvector；Atlas 违反 C5；Helios 违反 C2, C3, C5。
```

完整报告：[`constraints/report.md`](artifacts/demo/constraints/report.md)。Trace：19 个事件。

## 场景五：确定性 replan（脚本化检索 fixture）

**问题**：Atlas 向量数据库的性能、生态、成本和成熟度怎么样？

> 脚本化 KB 只返回部分维度；仅证明离线 replan 行为与 Trace，不代表在线 replan 质量。

- 计划轮数：2；Trace 中 `plan` 事件：2 次；`dispatch`：2 次
- 第二轮子任务维度：['ecosystem', 'maturity']
- 第二轮检索词：['Atlas 向量数据库 生态', 'Atlas 向量数据库 生态', 'Atlas 向量数据库 成熟度', 'Atlas 向量数据库 成熟度']
- 首轮 verify 缺口：['Atlas 缺少维度证据：生态、成熟度']
- 最终 verify 缺口：[]
- 最终信息缺口：[]

完整报告：[`replan/report.md`](artifacts/demo/replan/report.md)；Trace：23 个事件。该测试只证明离线 replan 行为，不代表在线 replan 质量。

## 场景六：引用 Guard（受控模型输出）

> 模型输出由脚本固定注入，包含合法引用、非法引用、无引用事实性结论；用于验证 Guard 行为，不是在线模型效果。

### Guard 前原始模型输出

```text
# 技术选型研究报告（模型原始输出，受控 fixture）

## 结论摘要

根据证据 [E1]，该系统在千万级向量规模下有性能数据。

Milvus 的 GPU 索引吞吐提升 10 倍 [E99]。

Qdrant 是最好的选择。
```

### Guard 后最终报告

```text
# 技术选型研究报告（模型原始输出，受控 fixture）

## 结论摘要

根据证据 [E1]，该系统在千万级向量规模下有性能数据。
```

### 结构化差异

```text
--- pre_guard
+++ post_guard
@@ -3,7 +3,3 @@
 ## 结论摘要

 根据证据 [E1]，该系统在千万级向量规模下有性能数据。
-
-Milvus 的 GPU 索引吞吐提升 10 倍 [E99]。
-
-Qdrant 是最好的选择。
```

### 被隔离结论

| location | claim | invalid IDs | reason |
| --- | --- | --- | --- |
| `line#7` | Milvus 的 GPU 索引吞吐提升 10 倍 [E99]。 | ['E99'] | invalid_citation_after_rewrite |
| `line#9` | Qdrant 是最好的选择。 | [] | missing_citation |

- 检查结论 3 条；隔离 2 条；重写尝试 True，接受 False，拒绝原因 `rewrite_still_invalid`
- 最终动作：['rewrite_rejected', 'claim_quarantined']
- Guard 事件：['error']

完整报告：[`guard/report.md`](artifacts/demo/guard/report.md)；Trace：16 个事件。

## 这不是在线效果评测

以上运行使用离线启发式规划器与内置演示语料，Token 为 0，不能用来宣称真实模型效果。在线评测方法、指标定义与运行命令见 [`docs/evaluation.md`](evaluation.md)。

在线评测当前状态：见 [`artifacts/online/NOT_RUN.json`](artifacts/online/NOT_RUN.json)（若该文件不存在，说明尚未执行过在线 Runner 的 preflight）。
