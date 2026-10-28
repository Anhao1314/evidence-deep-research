# 技术选型研究报告

> 研究问题：我们已有 PostgreSQL 团队，准备上线 500 万条 768 维向量的 RAG，团队 5 人、自托管、预算有限，并尽量不引入新数据库。Atlas、Helios 和 pgvector 应该选哪个？

## 结论摘要

**Atlas**：性能：Atlas 是单机专用向量库，官方标称单机支持 1000 万条 768 维向量； [E1]；生态：Atlas 提供 Python/Go/Rust 客户端和常见 Agent 框架适配器； [E2]；成本：Atlas 社区版免费，单机 16GB 内存可承载百万级向量，2 人团队可完成日常运维； [E3]；成熟度：Atlas 2021 年发布 1.0，社区规模中等，生产案例以百万级 RAG 为主； [E4]。

**Helios**：性能：Helios 是分布式向量数据库，官方标称支持 1 亿条以上向量； [E5]；生态：Helios 提供多语言 SDK 与连接器，适合已有平台团队做二次开发； [E6]；成本：Helios 分布式版需要 8-12 个节点，存储与消息队列带来额外成本； [E7]；成熟度：Helios 在大型企业有亿级向量案例，版本节奏稳定； [E8]。

**pgvector**：性能：pgvector 是 PostgreSQL 扩展，官方建议单表千万级向量以内使用 HNSW； [E11]；生态：pgvector 直接复用 PostgreSQL 的连接池、备份、权限与监控，已有 PostgreSQL 团队无需学习新的数据库运维体系。 [E12]；成本：pgvector 不需要新增数据库集群，复用现有 PostgreSQL 资源； [E13]；成熟度：pgvector 由 PostgreSQL 社区维护，版本随 PostgreSQL 生态更新； [E14]。

**Nebula**：性能：Nebula 声称支持大规模向量检索，但公开资料没有给出可核验的容量上限与最低团队配置。 [E9]；成熟度：Nebula 的版本历史与生产案例缺少公开证据，成熟度无法核验。 [E10]。

## 约束核对

- [C1] 500 万条 768 维向量：Atlas 满足（支持上限 10,000,000 / 要求 5,000,000）；Helios 满足（支持上限 100,000,000 / 要求 5,000,000）；pgvector 满足（支持上限 10,000,000 / 要求 5,000,000）
- [C2] 5 人：Atlas 满足（最低团队 2 人 / 可用 5 人）；Helios 违反（最低团队 10 人 / 可用 5 人）；pgvector 满足（最低团队 2 人 / 可用 5 人）
- [C3] 低成本/预算有限：Atlas 满足（成本等级 0 / 上限 0）；Helios 违反（成本等级 2 / 上限 0）；pgvector 满足（成本等级 0 / 上限 0）
- [C4] 自托管：Atlas 满足（支持部署方式 ['managed-cloud', 'self-hosted'] / 要求 self-hosted）；Helios 满足（支持部署方式 ['self-hosted'] / 要求 self-hosted）；pgvector 满足（支持部署方式 ['self-hosted'] / 要求 self-hosted）
- [C5] 不引入新数据库/中间件：Atlas 违反（需要新基础设施=True）；Helios 违反（需要新基础设施=True）；pgvector 满足（需要新基础设施=False）

**约束结论**：在给定约束下，唯一满足全部可核验约束的是 pgvector；Atlas 违反 C5；Helios 违反 C2, C3, C5。

## 分维度对比

| 维度 | Atlas | Helios | pgvector | Nebula |
|---|---|---|---|---|
| 性能 | Atlas 是单机专用向量库，官方标称单机支持 1000 万条 768 维向量； [E1] | Helios 是分布式向量数据库，官方标称支持 1 亿条以上向量； [E5] | pgvector 是 PostgreSQL 扩展，官方建议单表千万级向量以内使用 HNSW； [E11] | Nebula 声称支持大规模向量检索，但公开资料没有给出可核验的容量上限与最低团队配置。 [E9] |
| 生态 | Atlas 提供 Python/Go/Rust 客户端和常见 Agent 框架适配器； [E2] | Helios 提供多语言 SDK 与连接器，适合已有平台团队做二次开发； [E6] | pgvector 直接复用 PostgreSQL 的连接池、备份、权限与监控，已有 PostgreSQL 团队无需学习新的数据库运维体系。 [E12] | —（本轮无证据） |
| 成本 | Atlas 社区版免费，单机 16GB 内存可承载百万级向量，2 人团队可完成日常运维； [E3] | Helios 分布式版需要 8-12 个节点，存储与消息队列带来额外成本； [E7] | pgvector 不需要新增数据库集群，复用现有 PostgreSQL 资源； [E13] | —（本轮无证据） |
| 成熟度 | Atlas 2021 年发布 1.0，社区规模中等，生产案例以百万级 RAG 为主； [E4] | Helios 在大型企业有亿级向量案例，版本节奏稳定； [E8] | pgvector 由 PostgreSQL 社区维护，版本随 PostgreSQL 生态更新； [E14] | Nebula 的版本历史与生产案例缺少公开证据，成熟度无法核验。 [E10] |

## 证据明细

- **[E1]** Atlas 是单机专用向量库，官方标称单机支持 1000 万条 768 维向量；（来源 [S1]：doc:fixture-atlas-perf §1 容量 (kb)）
- **[E2]** Atlas 提供 Python/Go/Rust 客户端和常见 Agent 框架适配器；（来源 [S2]：doc:fixture-atlas-eco §2 生态 (kb)）
- **[E3]** Atlas 社区版免费，单机 16GB 内存可承载百万级向量，2 人团队可完成日常运维；（来源 [S3]：doc:fixture-atlas-cost §3 成本 (kb)）
- **[E4]** Atlas 2021 年发布 1.0，社区规模中等，生产案例以百万级 RAG 为主；（来源 [S4]：doc:fixture-atlas-maturity §4 成熟度 (kb)）
- **[E5]** Helios 是分布式向量数据库，官方标称支持 1 亿条以上向量；（来源 [S5]：doc:fixture-helios-perf §1 容量 (kb)）
- **[E6]** Helios 提供多语言 SDK 与连接器，适合已有平台团队做二次开发；（来源 [S6]：doc:fixture-helios-eco §2 生态 (kb)）
- **[E7]** Helios 分布式版需要 8-12 个节点，存储与消息队列带来额外成本；（来源 [S7]：doc:fixture-helios-cost §3 成本 (kb)）
- **[E8]** Helios 在大型企业有亿级向量案例，版本节奏稳定；（来源 [S8]：doc:fixture-helios-maturity §4 成熟度 (kb)）
- **[E9]** Nebula 声称支持大规模向量检索，但公开资料没有给出可核验的容量上限与最低团队配置。（来源 [S9]：doc:fixture-nebula-perf §1 容量 (kb)）
- **[E10]** Nebula 的版本历史与生产案例缺少公开证据，成熟度无法核验。（来源 [S10]：doc:fixture-nebula-maturity §4 成熟度 (kb)）
- **[E11]** pgvector 是 PostgreSQL 扩展，官方建议单表千万级向量以内使用 HNSW；（来源 [S11]：doc:fixture-pgvector-perf §1 容量 (kb)）
- **[E12]** pgvector 直接复用 PostgreSQL 的连接池、备份、权限与监控，已有 PostgreSQL 团队无需学习新的数据库运维体系。（来源 [S12]：doc:fixture-pgvector-eco §2 生态 (kb)）
- **[E13]** pgvector 不需要新增数据库集群，复用现有 PostgreSQL 资源；（来源 [S13]：doc:fixture-pgvector-cost §3 成本 (kb)）
- **[E14]** pgvector 由 PostgreSQL 社区维护，版本随 PostgreSQL 生态更新；（来源 [S14]：doc:fixture-pgvector-maturity §4 成熟度 (kb)）

## 信息缺口

- 无（本轮检索已覆盖计划的全部维度）

## 参考来源

- **[S1]** Atlas 性能与容量（离线 fixture） — doc:fixture-atlas-perf §1 容量 (kb)
- **[S2]** Atlas 生态（离线 fixture） — doc:fixture-atlas-eco §2 生态 (kb)
- **[S3]** Atlas 成本（离线 fixture） — doc:fixture-atlas-cost §3 成本 (kb)
- **[S4]** Atlas 成熟度（离线 fixture） — doc:fixture-atlas-maturity §4 成熟度 (kb)
- **[S5]** Helios 性能与容量（离线 fixture） — doc:fixture-helios-perf §1 容量 (kb)
- **[S6]** Helios 生态（离线 fixture） — doc:fixture-helios-eco §2 生态 (kb)
- **[S7]** Helios 成本（离线 fixture） — doc:fixture-helios-cost §3 成本 (kb)
- **[S8]** Helios 成熟度（离线 fixture） — doc:fixture-helios-maturity §4 成熟度 (kb)
- **[S9]** Nebula 性能（离线 fixture，缺少结构化属性） — doc:fixture-nebula-perf §1 容量 (kb)
- **[S10]** Nebula 成熟度（离线 fixture，缺少结构化属性） — doc:fixture-nebula-maturity §4 成熟度 (kb)
- **[S11]** pgvector 性能与容量（离线 fixture） — doc:fixture-pgvector-perf §1 容量 (kb)
- **[S12]** pgvector 生态（离线 fixture） — doc:fixture-pgvector-eco §2 生态 (kb)
- **[S13]** pgvector 成本（离线 fixture） — doc:fixture-pgvector-cost §3 成本 (kb)
- **[S14]** pgvector 成熟度（离线 fixture） — doc:fixture-pgvector-maturity §4 成熟度 (kb)
