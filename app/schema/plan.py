"""研究计划模型：Planner 产出的子任务集合。"""

from typing import Literal

from pydantic import BaseModel, Field


class SubTask(BaseModel):
    """一个可派发给子 Agent 的最小研究单元。"""

    subtask_id: str = Field(description="如 T1")
    route: Literal["web", "kb"] = Field(description="派发给哪条检索路")
    topic: str = Field(description="研究主题，如 vector-db")
    object_name: str = Field(default="", description="对比对象，如 qdrant；泛型子任务为空")
    object_display: str = Field(default="", description="对象显示名，如 Qdrant")
    aspect: str = Field(description="研究维度，如 performance；general 表示全维度检索")
    query: str = Field(description="给子 Agent 的检索查询词")


class Plan(BaseModel):
    """一轮任务分解结果。"""

    plan_id: str
    subtasks: list[SubTask] = Field(default_factory=list)
    rationale: str = Field(default="", description="分解依据（离线=规则名，在线=模型自述）")

    def signature(self) -> str:
        """计划指纹：用于重复计划检测（避免 replan 死循环）。"""
        return "|".join(f"{t.route}:{t.topic}:{t.object_name}:{t.aspect}:{t.query}" for t in self.subtasks)
