"""用户约束与约束核验结构。

约束核验只比较“证据中携带的结构化属性”与用户约束；没有属性就返回
unknown，不根据自由文本猜结论。离线 fixture 提供属性，真实在线运行
可以在 KB 文档 attributes 中提供同类信息。
"""

from typing import Literal

from pydantic import BaseModel, Field

ConstraintKind = Literal[
    "scale", "team", "cost", "deployment", "stack", "latency", "compliance", "other"
]
ConstraintStatus = Literal["satisfied", "violated", "unknown"]
ConstraintVerdict = Literal["recommend", "conditional", "no_solution", "insufficient", "not_checked"]


class Constraint(BaseModel):
    constraint_id: str
    kind: ConstraintKind
    text: str
    value: str = Field(default="", description="归一化后的值，如 50000000")
    unit: str = ""
    source: Literal["user"] = "user"


class ConstraintFinding(BaseModel):
    constraint_id: str
    object_name: str
    status: ConstraintStatus
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = ""


class ConstraintSummary(BaseModel):
    verdict: ConstraintVerdict = "not_checked"
    recommended_objects: list[str] = Field(default_factory=list)
    excluded: dict[str, list[str]] = Field(
        default_factory=dict, description="object_name -> 违反的 constraint_id 列表"
    )
    unknown_objects: list[str] = Field(default_factory=list)
    findings: list[ConstraintFinding] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    checked_objects: list[str] = Field(default_factory=list)