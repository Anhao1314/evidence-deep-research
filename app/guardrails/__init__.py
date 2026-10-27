"""防死循环护栏：步数上限、计划轮数上限、重复计划检测。

原则：护栏判断是纯函数，节点与条件边共享同一份判断，测试直接覆盖。
"""

from dataclasses import dataclass

from app.schema.plan import Plan


@dataclass
class Guardrails:
    max_steps: int = 24
    max_plan_rounds: int = 2

    def steps_exhausted(self, step_count: int) -> bool:
        return step_count >= self.max_steps

    def can_replan(self, *, plan_rounds: int, step_count: int) -> bool:
        return plan_rounds < self.max_plan_rounds and not self.steps_exhausted(step_count)


def is_duplicate_plan(plan: Plan, plan_history: list[str]) -> bool:
    """计划指纹已在历史中出现 -> 视为重复（replan 只会原地打转）。"""
    return plan.signature() in plan_history
