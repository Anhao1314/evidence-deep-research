# 2～3 分钟录屏脚本

目标：用真实运行产物讲清五件事——问题分解与并行检索、证据合并与报告、Guard 的 fail-closed、约束怎样改变结论、以及一个受控失败案例；最后说明在线评测当前是 `NOT_RUN`。

> 不要手工编辑任何报告或数字。所有展示内容来自 `docs/artifacts/` 或现场命令输出。Guard/约束/replan 场景是受控 fixture，录制时必须说明；失败案例是脚本注入故障，不能暗示成真实线上事故。

## 0. 录制前准备（不录）

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python -m eval.run_eval --output docs/artifacts/eval-report.json
python -m eval.run_offline_suite --output docs/artifacts/offline-suite-report.json
python -m eval.run_constraint_fixture --output docs/artifacts/constraint-fixture-report.json
python scripts/capture_demo.py
```

打开：`README.md` 首屏、`docs/demo.md`、`docs/artifacts/demo/guard/report.json`、`docs/artifacts/online/NOT_RUN.json`。

## 1. 时间线（总时长约 2 分 50 秒）

| 时间 | 画面 | 操作 | 讲解要点 |
| --- | --- | --- | --- |
| 0:00–0:15 | README | 滚动到“3 分钟看懂” | 解决什么问题：结论必须回到证据；无法追溯的结论被隔离 |
| 0:15–0:35 | 架构图 | `plan → Send 扇出 → merge → verify → synthesize` | 主图规划收敛；RAG/Web 子图并行；失败与缺口进 Trace |
| 0:35–0:55 | 终端 / API | 发一次真实离线请求 | 展示问题；离线模式无需 Key，使用 MCP stdio 知识库 + 演示搜索 |
| 0:55–1:15 | Trace | `docs/artifacts/demo/happy/trace.json` | `dispatch` fan_out=4；4 个并行 `tool_call`；22 条原始证据去重为 13 条 |
| 1:15–1:35 | 报告 | `docs/artifacts/demo/happy/report.md` | 结论摘要、维度对比、证据明细、来源；可追溯结论带 `[E#]` |
| 1:35–1:55 | Guard | `docs/demo.md` 场景六 | 原始输出有一条合法引用、一条 `[E99]`、一条无引用事实结论；重写拒绝；后两条被隔离；主报告只保留可追溯结论 |
| 1:55–2:15 | 约束 | `docs/demo.md` 场景四 | 500 万向量/5 人/自托管/低成本/不引入新库：pgvector 唯一推荐，Atlas/Helios 被排除；这些名称仅作为合成 fixture 中的评测对象，结构化属性为测试构造，不代表同名真实产品的真实能力，也不构成真实技术选型建议 |
| 2:15–2:30 | 无关证据 | `docs/demo.md` 场景二 | 检索到 12 条，采用 0，全部 `unscoped_question` 排除；信息缺口保留 |
| 2:30–2:42 | Replan | `docs/demo.md` 场景五 | 首轮缺口“生态、成熟度”；第二轮 plan 与检索词出现；最终缺口为空 |
| 2:42–2:55 | 失败 + 边界 | `docs/demo.md` 场景三、`NOT_RUN.json` | 注入重试/降级；报告仍产出但不伪造证据；在线 NOT_RUN，无在线数字 |

## 2. 现场命令

### 2.1 发一次真实离线请求

```bash
DR_MCP_KB_ENABLED=true uvicorn app.api.main:app --port 8000
```

另一个终端：

```bash
curl -s -X POST localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"question":"RAG 系统的向量库选 Qdrant 还是 Milvus？","user_id":"me"}' \
  | python -c 'import json,sys; d=json.load(sys.stdin); print("report_id:", d["report_id"]); print("routes:", d["meta"]["routes_used"]); print("evidences:", len(d["evidences"])); print("guard_ran:", d["guard_audit"]["ran"]); print(d["markdown"][:400])'
```

### 2.2 展示 Guard 审计

```bash
python - <<'PY'
import json
r=json.load(open('docs/artifacts/demo/guard/report.json'))
g=r['guard_audit']
print('raw chars:', len(g['raw_markdown']), 'final chars:', len(g['final_markdown']))
print('invalid:', g['invalid_citation_ids'], 'rewrite:', g['rewrite_attempted'], g['rewrite_accepted'], g['rewrite_rejected_reason'])
print('checked:', g['claims_checked'], 'quarantined:', g['claims_quarantined'])
for c in r['unverified_claims']:
    print('QUARANTINED:', c['text'], c['invalid_citation_ids'], c['reason'])
PY
```

### 2.3 展示约束与 replan 产物

```bash
python - <<'PY'
import json
c=json.load(open('docs/artifacts/demo/constraints/scenario.json'))
print('verdict:', c['constraint_summary']['verdict'])
print('recommended:', c['constraint_summary']['recommended_objects'])
print('excluded:', c['constraint_summary']['excluded'])
r=json.load(open('docs/artifacts/demo/replan/scenario.json'))
print('plan_rounds:', r['plan_rounds'], 'plan_events:', r['plan_event_count'])
PY
```

### 2.4 展示失败与 NOT_RUN

```bash
python - <<'PY'
import json
f=json.load(open('docs/artifacts/online/NOT_RUN.json'))
print('status:', f['status'])
print('reasons:', f['reasons'])
PY
```

## 3. 口播稿（可直接照读）

> “这个项目解决的是技术选型研究里的可追溯性问题。用户问 Qdrant 还是 Milvus，系统先把问题拆成对象乘维度的子任务，用 Send API 并行派发到知识库和 Web，再把证据去重、编号，最后合成报告。可追溯的结论带证据编号；无法追溯的结论不会留在主报告。”
>
> “这里一次运行扇出 4 个子任务，四个工具调用并发执行，22 条原始证据去重成 13 条。报告里的 E1 到 E13 都能在证据明细里找到，来源带 URL 或文档定位与内容哈希。”
>
> “Guard 场景里，模型原始输出有一条合法引用、一条不存在的 `[E99]`、一条没有引用的结论。受限重写被拒绝，因为程序校验发现正文会变；后两条结论被隔离到 unverified_claims，主报告只保留能追溯的那条。Guard 不判断语义是否支撑，那仍然需要人工复核。”
>
> “约束场景是合成 fixture：500 万向量、5 人、自托管、低成本、不引入新数据库，结构化核验让 pgvector 成为唯一推荐，Atlas 和 Helios 被明确排除。换一组 5000 万、5 人、低成本，fixture 会输出没有任何对象同时满足全部约束，而不是硬推荐一个。”
>
> “领域外问题里，检索层确实返回了 12 条文档，但全部被标记为 unscoped 并排除；报告没有把 Qdrant/Milvus 包装成社区健康度证据，信息缺口仍然保留。”
>
> “Replan 场景是脚本化 fixture：第一轮只有性能和成本，verify 发现生态和成熟度缺口，第二轮真的重新规划并检索这两个维度，最终缺口为空。这证明离线 replan 行为，不代表在线规划质量。”
>
> “失败场景是显式注入的知识库连接重置和搜索超时：系统重试、降级，把缺口写进报告，不伪造证据。在线评测当前是 NOT_RUN，环境没有模型和搜索 Key；Runner 没有调用任何外部 API，也没有生成任何在线数字。”

## 4. 录制注意事项

- 不要展示 `.env`、终端历史里的 Key 或任何 `sk-`/`tvly-` 字符串；
- 不要手动修改 `docs/demo.md` 或 `docs/artifacts/` 里的数字；更新请重跑 `python scripts/capture_demo.py`；
- Guard/约束/replan 场景必须说明是“受控 fixture/合成数据”，不能剪掉；
- 失败案例必须说明“故障注入”，不能剪掉；
- 在线评测只展示 `NOT_RUN.json`，不要用离线结构回归配“在线效果很好”的口播；
- 如果现场 API 启动失败，直接用已生成的 `docs/artifacts/demo/*` 讲解，不要临时改数字。