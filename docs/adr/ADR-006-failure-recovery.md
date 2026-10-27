# ADR-006: 失败恢复与防死循环的三层护栏 + 降级链

状态：已采纳 | 日期：2026-10-28

## 背景

多智能体系统的两大运行时风险：工具/模型失败放大为整次研究失败；replan 循环失控（计划器反复产出等价计划）。

## 决策

**三层护栏（纯函数，节点与条件边共享 `Guardrails`）：**

1. 全局步数上限（`max_steps`，每节点自增计数）；
2. plan 轮数上限（`max_plan_rounds`）；
3. 重复计划指纹（Plan.signature 哈希入历史，重复即判定 replan 无收敛希望，强制收敛到合成）。

**失败降级链（每一级都落 trace 事件）：**

```
工具调用失败
  → 指数退避重试（tool_max_retries，retry 事件）
  → 耗尽后写 route_failures（degrade 事件），该路降级为空，不阻塞主图
  → verify 把失败转为显式"信息缺口"
  → 报告正常产出，缺口写进"信息缺口"节（可审计的降级，而非静默失败）

LLM 失败（ResilientProvider）
  → plan/compose 异常 → 降级到 HeuristicProvider 规则模式（degrade 事件）
  → 引用非法 → 受限重写一次 → 剥离 + error 事件
```

## 理由

- **护栏是纯函数**：`Guardrails.can_replan / steps_exhausted / is_duplicate_plan` 无副作用，节点、条件边、pytest 三方共享同一判定，不存在"文档说一套代码做一套"；
- **降级而不是失败**：检索失败的部分转化为信息缺口比整次 500 更有价值——用户能看到"哪些结论有据、哪些维度缺失"；trace 的 retry/degrade/error 事件让每次降级可审计；
- **重复计划检测用指纹而非语义相似**：规则模式下重复必然是"指纹完全一致"；在线模式下 LLM 产出等价计划的概率低，指纹足够（语义级去重是过度设计）。

## 已验证的行为（有测试锁定）

- 检索恒空 → 每轮 verify 有缺口 → 打满 `max_plan_rounds` 后收敛出报告（`test_persistent_gap_converges_within_plan_rounds`）；
- 步数预算极小 → 第一轮 verify 后立即收敛，不进入第二轮 plan；
- 工具抛异常 → retry 耗尽 → degrade → 报告仍产出（`test_tool_failure_emits_retry_and_degrade`）。

## 后果

+ 任何单点失败都不会让研究流程白跑，且降级路径全部可审计；
− 极端场景下产出的是"信息缺口很多"的报告而非报错——评测断言 `must_converge` 明确了这是期望行为，`min_evidences`/覆盖率断言守住质量下限。
