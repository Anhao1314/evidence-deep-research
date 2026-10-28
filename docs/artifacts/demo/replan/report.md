# 技术选型研究报告

> 研究问题：Atlas 向量数据库的性能、生态、成本和成熟度怎么样？

## 结论摘要

**Atlas**：性能：Atlas 是单机专用向量库，官方标称单机支持 1000 万条 768 维向量； [E1]；生态：Atlas 提供 Python/Go/Rust 客户端和常见 Agent 框架适配器； [E2]；成本：Atlas 社区版免费，单机 16GB 内存可承载百万级向量，2 人团队可完成日常运维； [E3]；成熟度：Atlas 2021 年发布 1.0，社区规模中等，生产案例以百万级 RAG 为主； [E4]。

## 证据明细

- **[E1]** Atlas 是单机专用向量库，官方标称单机支持 1000 万条 768 维向量；（来源 [S1]：doc:fixture-atlas-perf §1 容量 (kb)）
- **[E2]** Atlas 提供 Python/Go/Rust 客户端和常见 Agent 框架适配器；（来源 [S2]：doc:fixture-atlas-eco §2 生态 (kb)）
- **[E3]** Atlas 社区版免费，单机 16GB 内存可承载百万级向量，2 人团队可完成日常运维；（来源 [S3]：doc:fixture-atlas-cost §3 成本 (kb)）
- **[E4]** Atlas 2021 年发布 1.0，社区规模中等，生产案例以百万级 RAG 为主；（来源 [S4]：doc:fixture-atlas-maturity §4 成熟度 (kb)）

## 信息缺口

- 无（本轮检索已覆盖计划的全部维度）

## 参考来源

- **[S1]** Atlas 性能与容量（离线 fixture） — doc:fixture-atlas-perf §1 容量 (kb)
- **[S2]** Atlas 生态（离线 fixture） — doc:fixture-atlas-eco §2 生态 (kb)
- **[S3]** Atlas 成本（离线 fixture） — doc:fixture-atlas-cost §3 成本 (kb)
- **[S4]** Atlas 成熟度（离线 fixture） — doc:fixture-atlas-maturity §4 成熟度 (kb)
