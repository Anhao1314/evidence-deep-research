# 演示样例（真实运行产出）

本页内容全部来自一次实际运行（离线模式，`run_id = run-demo-20261028`），
非手写示例。复现方式：

```bash
uvicorn app.api.main:app --port 8000
curl -s -X POST localhost:8000/api/research   -H "Content-Type: application/json"   -d '{"question": "RAG 系统的向量库选 Qdrant 还是 Milvus？", "user_id": "me"}'
```

## 研究报告（POST /api/research 返回的 markdown 字段）

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


## 全链路 Trace（GET /api/traces/run-demo-20261028 的事件流）

一次完整 run 共 15 个事件：4 个子任务经 Send API 并行扇出，
22 条原始证据去重合并为 13 条，双对象四维度全覆盖，单轮收敛（无 replan）。

| seq | 节点 | 事件 | payload（截断） |
|---|---|---|---|
| 1 | `load_context` | `memory_recall` | `{"preferences": {}, "related_topics": 0, "snippets": 0}` |
| 2 | `plan` | `plan` | `{"plan_id": "plan-41017de4", "subtasks": [{"subtask_id": "Tc024fc", "route": "kb", "topic": "vector-db", "object_name": "qdrant", "object_display": "Q …` |
| 3 | `dispatch` | `dispatch` | `{"fan_out": 4, "targets": ["rag_agent", "web_agent", "rag_agent", "web_agent"], "subtask_ids": ["Tc024fc", "T25216d", "Te24fe6", "T15edce"]}` |
| 4 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "Tc024fc", "query": "Qdrant 向量数据库", "top_k": 8}` |
| 5 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "T25216d", "query": "Qdrant 向量数据库", "top_k": 4}` |
| 6 | `rag_agent` | `tool_call` | `{"route": "kb", "tool": "kb_search", "subtask_id": "Te24fe6", "query": "Milvus 向量数据库", "top_k": 8}` |
| 7 | `web_agent` | `tool_call` | `{"route": "web", "tool": "web_search", "subtask_id": "T15edce", "query": "Milvus 向量数据库", "top_k": 4}` |
| 8 | `rag_agent` | `tool_result` | `{"route": "kb", "tool": "kb_search", "hits": 7, "latency_ms": 0.0}` |
| 9 | `web_agent` | `tool_result` | `{"route": "web", "tool": "web_search", "hits": 3, "latency_ms": 0.0}` |
| 10 | `rag_agent` | `tool_result` | `{"route": "kb", "tool": "kb_search", "hits": 8, "latency_ms": 0.0}` |
| 11 | `web_agent` | `tool_result` | `{"route": "web", "tool": "web_search", "hits": 4, "latency_ms": 0.0}` |
| 12 | `merge` | `merge` | `{"raw_evidences": 22, "deduped": 13, "after_budget_and_prefs": 13, "coverage": {"vector-db/milvus": ["cost", "ecosystem", "maturity", "performance"],  …` |
| 13 | `verify` | `verify` | `{"gaps": [], "replan": false}` |
| 14 | `synthesize` | `synthesize` | `{"markdown_chars": 3996, "citations": 10, "evidences": 13, "sources": 13, "degraded": false}` |
| 15 | `persist` | `persist` | `{"topic_recorded": true, "snippets": 13}` |

## 评测结果（python -m eval.run_eval）

```
[PASS] vector-db-compare: 10/10 断言通过
[PASS] agent-framework-compare: 10/10 断言通过
[PASS] llm-api-compare: 10/10 断言通过
[PASS] single-object: 10/10 断言通过
[PASS] out-of-domain: 10/10 断言通过

评测完成：5/5 通过
```
