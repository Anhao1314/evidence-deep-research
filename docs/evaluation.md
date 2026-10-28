# 评测方法

> 本仓库只报告能由产物复核的指标。**结构合法 ≠ 语义正确**：引用编号存在、来源可解析、路线覆盖，都不能证明结论为真，也不能证明证据在语义上支撑结论。语义部分只接受人工标注；未标注时写 `NOT_MEASURED`。

## 0. 当前状态

| 项目 | 状态 | 可复核产物 |
| --- | --- | --- |
| 单元 / 集成测试（离线） | PASS | [`artifacts/verification.log`](artifacts/verification.log) |
| 既有离线行为回归（5 条） | 5/5 PASS，每条 11/11 断言 | [`artifacts/eval-report.json`](artifacts/eval-report.json) |
| 离线任务集结构回归（10 条 offline-eligible） | 10/10 PASS，每条 13/13 断言 | [`artifacts/offline-suite-report.json`](artifacts/offline-suite-report.json) |
| 约束 fixture（3 条合成场景） | 3/3 PASS | [`artifacts/constraint-fixture-report.json`](artifacts/constraint-fixture-report.json) |
| 在线真实评测（full 双路） | **NOT_RUN** | [`artifacts/online/NOT_RUN.json`](artifacts/online/NOT_RUN.json) |
| Web-only 在线评测 | **NOT_RUN** | [`artifacts/online-web/NOT_RUN.json`](artifacts/online-web/NOT_RUN.json) |
| 在线 Badcase | **NOT_RUN／未产生在线样本** | 未运行，不能写成数量为 0 |
| 引用语义人工复核 | **NOT_MEASURED** | 尚无真实人员标注 |
| 成本 | **NOT_MEASURED** | 未配置单价且未在线运行 |

在线未运行原因写在 `NOT_RUN.json`：当前环境没有 `DR_LLM_API_KEY`、`DR_TAVILY_API_KEY`，且 `DR_MODE=offline`；在线 preflight 还要求 full 模式使用真实 `DR_KB_CORPUS_DIR`。Runner 拒绝运行并只写 NOT_RUN，没有调用任何外部 API。

## 1. 评测分层

| 层 | 目的 | 外部 API | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- | --- |
| L1 单元 / 集成 | 图结构、护栏、MCP、Guard、约束、replan | 否 | 代码行为符合断言 | 研究质量 |
| L2 离线行为回归 | 5 条确定性用例 | 否 | 收敛、双路、引用、来源 | 事实正确率 |
| L3 离线任务集结构回归 | 20 条中 10 条 | 否 | 结构、引用、路线、维度、缺口 | 语义质量、在线效果 |
| L3b 约束 fixture | 3 条合成约束场景 | 否 | 约束是否改变结论方向 | 真实产品建议 |
| L4 在线真实评测 | full 双路 / web-only 子集 | 是 | 在线完成率、引用、延迟、Token/成本 | 事实正确率（需人工） |
| L5 人工语义复核 | 引用是否支撑结论 | 否 | `supported/partial/unsupported/cannot_judge` | 大规模自动化 |

L3/L3b 的 `report_type` 与 `disclaimer` 明确标记为离线/合成；L4 的 `evaluation_scope` 区分 `full_dual_route`、`web_only_subset` 与 `offline_smoke`。

## 2. 任务集与 fixture

### 2.1 全量双路任务集

[`eval/tasks/research_tasks.v1.json`](../eval/tasks/research_tasks.v1.json)：20 条，多对象 6、单对象 6、信息不足/领域外 4、replan/gap 4。字段包括 `question`、`research_dimensions`、`dimension_aspects`、`expected_route`、`expected_objects`、`automated_checks`、`offline_eligible`、`why_this_case`、`human_review_focus`。

10 条 `offline_eligible=true` 进入 CI；另外 10 条依赖真实搜索/真实 KB，只有在线运行后才能评价。

### 2.2 Web-only 子集

[`eval/tasks/research_tasks.v1.web-only.json`](../eval/tasks/research_tasks.v1.web-only.json)：9 条，`route_mode=web_only`，所有用例只要求 web 路线。在线 Runner 在 web-only 模式中显式关闭 KB 路（`DR_KB_ENABLED=false`），manifest 记录 `evaluation_scope=web_only_subset`。它**不是**原 20 条全量双路评测，统计分母只覆盖这 9 条。

### 2.3 约束 fixture

[`data/fixtures/constraint_selection/corpus.jsonl`](../data/fixtures/constraint_selection/corpus.jsonl) 是合成语料。这些名称仅作为合成 fixture 中的评测对象，结构化属性为测试构造，不代表同名真实产品的真实能力，也不构成真实技术选型建议。文档带结构化属性：`max_vectors`、`min_team`、`deployment`、`cost_tier`、`new_infra_required` 等。任务见 [`eval/fixtures/constraint_tasks.v1.json`](../eval/fixtures/constraint_tasks.v1.json)：

| case | 期望 | 证明什么 |
| --- | --- | --- |
| `constraint-recommend-pgvector` | `recommend`，仅 pgvector | 规模/团队/成本/部署/技术栈约束共同排除 Atlas/Helios |
| `constraint-no-solution` | `no_solution` | 5000 万、5 人、低成本冲突，没有对象同时满足 |
| `constraint-insufficient-attributes` | `insufficient`，Nebula unknown | 缺少结构化属性时不允许编造推荐 |

### 2.4 相关性 fixture

[`eval/fixtures/relevance_labels.v1.json`](../eval/fixtures/relevance_labels.v1.json) 人工预置了领域外问题的无关文档列表。`tests/test_relevance_fixture.py` 验证：检索层确实返回文档（`raw_evidences>0`），但最终 `evidences=[]`、`sources=[]`、`citations=[]`，disposition 全部为 `excluded: unscoped_question`，信息缺口仍存在。该检查只称为“离线预置场景下的证据相关性回归”，不是在线通用语义相关率。

### 2.5 replan fixture

`tests/test_replan_deterministic.py` 用脚本化 KB：首轮只返回 performance/cost，查询包含“生态/成熟度”时补文档。断言两轮 `plan`、第二轮 `ecosystem/maturity` 子任务、第二轮检索词、首轮 verify 有缺口、最终 verify 无缺口；另一个 `AlwaysPartialKB` 用例断言达到 `max_plan_rounds` 后停止并留下未闭合缺口。只证明离线 replan 行为，不代表在线 replan 质量。

## 3. 自动指标

所有比率保留分子/分母；无数据为 `null` / `NOT_MEASURED`。旧名 `task_completion_rate`、`citation_id_validity_rate`、`source_traceability_rate` 不再出现在新产物中。

### 3.1 流程与检查

| 指标 | 公式 | 说明 |
| --- | --- | --- |
| `pipeline_completion_rate` | 产出报告且流程未抛异常的 case 数 / 总 case 数 | 只看流程完成，不看质量 |
| `all_checks_pass_rate` | 全部自动断言通过的 case 数 / 总 case 数 | 不等于事实正确率或通用任务成功率 |

### 3.2 Guard 前后引用

| 指标 | 公式 | 说明 |
| --- | --- | --- |
| `pre_guard_citation_valid_rate` | 原始模型输出合法 `[E#]` 出现次数 / 原始引用总次数 | 观察模型原始引用行为 |
| `post_guard_citation_integrity_rate` | 最终报告合法 `[E#]` 出现次数 / 最终引用总次数 | 评价系统最终输出完整性 |
| `guard_rewrite_rate` | 触发受限重写的 case 数 / 原始输出含非法引用的 case 数 | case 级；分母为 0 时 N/A |
| `guard_claim_quarantine_rate` | 被隔离结论数 / Guard 检查结论数 | 结论级 |
| `guard_fallback_rate` | 使用模板回退的 case 数 / Guard 运行过的 case 数 | 极端降级 |

post-guard 100% 不能代表模型原始引用能力；pre-guard 指标也不能称为语义支撑率。离线模板模式 `guard_ran=false`，pre/post 只表示模板引用完整，不代表模型行为。Guard 行为见 [ADR-005](adr/ADR-005-citation-guard.md)。

### 3.3 来源与引用覆盖

| 指标 | 公式 | 说明 |
| --- | --- | --- |
| `source_reference_integrity_rate` | 可解析且带定位/内容哈希的证据数 / 证据总数 | 来源编号在清单、URL/doc_id、`content_hash`、`resolution` 均有效 |
| `source_resolution` | 来源/证据已解析数、缺失 content_hash/定位数、孤立来源数 | `source.url_reachable` 单独记 `NOT_MEASURED`，评测不做网络探测 |
| `citation_coverage` | 被引用证据数 / 证据总数，按 case 求均值 | **只统计结论区域**，排除证据明细/附录/参考来源 |

### 3.4 其他

- `route_coverage`：期望路线与 `routes_used`（实际产出证据的路线）的交集 / 期望路线数；`routes_attempted` 单独记录，失败路不算覆盖。
- `dimension_coverage`：期望 aspect 覆盖比例。
- `latency` / `latency_all_cases`：nearest-rank P50/P95，分别只统计成功 case / 全部 case。
- `token_usage`：trace usage 事件汇总，`usage_events=0` 表示没有数据。
- `cost`：配置输入/输出单价且存在 usage 事件时计算，否则 `NOT_MEASURED`。
- 失败类型：`pipeline_exception`、`no_report`、`no_evidence`、`route_not_covered`、`post_guard_invalid_citation`、`source_unresolved`、`gaps_not_stated`、`missing_object`、`tool_or_llm_degraded`、`tool_retry`、`guard_rewrite_rejected`、`guard_claim_quarantined`、`guard_fallback`、`guard_fail_closed`、`structural_check_failed`。

## 4. 人工语义复核

不使用 LLM Judge。模板绑定：

- `run_id`、`report_sha256`、`template_sha256`、`claim_id`；
- 按 `section|object` 分层抽样，记录 `sampling_seed`；样本不足时 `sample_shortfall=true`；
- 标签：`supported` / `partial` / `unsupported` / `cannot_judge`，附 `rationale`、`annotator`、`annotated_at`。

`summarize` 会拒绝：未知 claim、跨 run 标签、重复且冲突标签、模板文件/内容哈希不匹配、报告哈希与当前产物不匹配。所有 rate 的分母都是已标注 claim 数；`cannot_judge` 不并入 `unsupported`；未标注时 `NOT_MEASURED`。人工指标单独保存，不回写篡改当次自动运行的 summary 快照。

## 5. 运行命令

### 5.1 离线（无需 Key，CI 使用）

```bash
python -m pytest -q
python -m eval.run_eval --output docs/artifacts/eval-report.json
python -m eval.run_offline_suite --output docs/artifacts/offline-suite-report.json
python -m eval.run_constraint_fixture --output docs/artifacts/constraint-fixture-report.json
python scripts/capture_demo.py
```

### 5.2 在线 full 双路（需要真实 Key + 真实 KB）

```bash
DR_MODE=online \
DR_LLM_BASE_URL=https://api.deepseek.com/v1 \
DR_LLM_API_KEY=<key> DR_LLM_MODEL=deepseek-chat \
DR_SEARCH_PROVIDER=tavily DR_TAVILY_API_KEY=<key> \
DR_KB_ENABLED=true DR_KB_CORPUS_DIR=/path/to/real-corpus \
DR_KB_CORPUS_VERSION=<version> DR_KB_CORPUS_DESCRIPTION="<description>" \
python -m eval.online_runner --mode full \
  --output-dir eval/runs/online-full-$(date +%Y%m%d-%H%M%S)
```

full 模式如果仍指向内置 `data/corpus` 演示快照，preflight 会拒绝；manifest 会记录语料路径、文件清单、每个文件的 SHA256、版本/说明，以及 `kb_corpus_is_demo_snapshot=false`。

### 5.3 在线 Web-only 子集（没有真实 KB）

```bash
DR_MODE=online \
DR_LLM_API_KEY=<key> DR_SEARCH_PROVIDER=tavily DR_TAVILY_API_KEY=<key> \
python -m eval.online_runner --mode web-only \
  --output-dir eval/runs/online-web-$(date +%Y%m%d-%H%M%S)
```

`evaluation_scope=web_only_subset`，KB 关闭，任务集/分母只覆盖 9 条 web 用例。

### 5.4 人工标注

```bash
# 保留原始模板用于哈希校验，复制一份填写
cp <run_dir>/annotation_template.csv labels.csv
# 填写 label / rationale / annotator / annotated_at 后：
python -m eval.annotation summarize \
  --labels labels.csv --template <run_dir>/annotation_template.csv \
  --run-dir <run_dir> --output human-metrics.json
```

不要直接修改原始 `annotation_template.csv`：它的文件哈希会被 `summarize` 校验，修改后会被拒绝。

## 6. 产物结构

```text
<run_dir>/
  run_manifest.json          # 时间、模型、搜索、配置、Git SHA、源码树指纹、KB 清单
  summary.json / summary.md  # 自动指标 + 人工指标占位 + 未测量项
  annotation_template.csv / annotation_template.meta.json
  cases/<case_id>/
    report.json              # 最终报告 + raw_markdown + guard_audit + unverified_claims
    report.md
    trace.json
    result.json
    case.json
  runtime/<run_id>/          # 本次 SQLite
  NOT_RUN.json               # preflight 失败时唯一产物
```

full 模式的 KB 语料清单写在 `run_manifest.json` 的 `kb_corpus_manifest` 字段（路径、版本、说明、文件清单与 SHA256）。

`report.json` 现在包含：`raw_markdown`（Guard 前）、`guard_audit`（diff/actions/unverified）、`unverified_claims`、`evidence_disposition`、`constraints`、`constraint_summary`。`run_manifest.json`/`summary.json` 包含 `source_manifest`：tracked diff 的 SHA256、未跟踪源码文件清单与哈希、`source_tree_fingerprint`。

## 7. 当前已验证与未验证

已验证（离线/合成）：

- 135 个测试通过；5/5 行为回归（11/11 断言）；10/10 任务集结构回归（13/13）；3/3 约束 fixture；
- Guard：合法保留、重写修复、重写改正文拒绝、重写仍非法隔离、混合引用、多结论拆分、无引用事实性结论隔离、表格单元隔离、fail-closed；
- 约束：fixture 中 pgvector 唯一推荐、no_solution、unknown 三种方向；
- 无关证据排除与信息缺口保留；
- 确定性 replan 两轮与轮数上限停止；
- 来源解析：content_hash + KB 文档/检索快照分辨率；
- 在线 Runner 的 NOT_RUN、full/web-only/offline-smoke 模式标记。

未验证：

- 在线模型 + 真实搜索 + 真实 KB 的完成率、Guard 前后引用率、延迟、Token/成本、失败分布；
- 真实在线 replan 质量；
- 事实正确率、引用语义支撑率；
- 真实 KB 覆盖、URL 当前可访问性；
- 跨运行方差。

## 8. 已知限制

- **Guard 只处理引用编号与结论边界**：合法编号仍可能语义不支撑；句子级隔离可能连带移除同句中的其他结论。
- **离线模板不会做开放域综合**：默认演示语料缺少结构化约束属性，真实问题约束只能标记“无法核验”；约束闭环只在合成 fixture 中验证。
- **相关性门是保守范围门**：当前范围门对无可核实对象的泛化问题采取保守排除策略，只解决演示语料误入报告的问题，不等于通用开放域相关性判断。
- **replan 测试是脚本化 fixture**：证明行为，不证明在线规划质量。
- **人工抽样是分层的但样本量小**：默认每 case 3 条；不能代表整体语义正确率。
- **单次运行**：不提供方差/置信区间；在线结果受模型版本、搜索索引、限流影响。
- **URL 可访问性未测量**：评测不做网络探测；`url_reachable` 为 `NOT_MEASURED`。
- **成本仅在配置单价且存在 usage 事件时计算**。
- **同步接口 + SQLite**：面向单机与中小规模。

## 9. 复现与审计

- `run_manifest.json` / `NOT_RUN.json` 记录时间、Git SHA、工作区 dirty、`source_manifest`（tracked diff SHA256 + 未跟踪源码文件哈希）、任务集 SHA256、KB 语料清单；
- `docs/demo.md` 由 `scripts/capture_demo.py` 从真实运行产物自动生成，禁止手改；
- 如果最后一轮验证后又修改代码，必须重新生成受影响产物；不能沿用旧数字；
- 本文件引用的每个数字都能在对应 JSON/Markdown 产物中定位；在线部分没有数字，因为状态是 `NOT_RUN`。