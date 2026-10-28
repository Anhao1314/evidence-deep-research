"""主图状态定义。

两个证据通道的语义差异是本图的关键设计：
- raw_evidences：Annotated + operator.add，接收并行子 Agent 的 map-reduce 汇聚；
- evidences：LastValue 覆盖语义，仅由 merge 节点写入（去重、编号、预算截断后的最终证据），
  下游 verify / synthesize 只信这个通道。
"""

import operator
from typing import Annotated, TypedDict

from app.schema.constraints import Constraint, ConstraintSummary
from app.schema.evidence import Evidence, EvidenceDisposition
from app.schema.plan import Plan
from app.schema.report import Report


class ResearchState(TypedDict, total=False):
    # 输入
    question: str
    user_id: str
    run_id: str

    # 上下文（记忆召回产物）
    preferences: dict
    related_history: list[dict]

    # 计划与护栏
    plan: Plan
    plan_history: list[str]
    plan_rounds: int
    plan_is_duplicate: bool
    replan_focus: list[dict]
    step_count: int

    # 检索汇聚
    raw_evidences: Annotated[list[Evidence], operator.add]
    route_failures: Annotated[list[dict], operator.add]

    # merge 之后的最终证据（覆盖语义）
    evidences: list[Evidence]
    coverage: dict
    evidence_disposition: list[EvidenceDisposition]

    # 用户约束与核验（verify 写入，compose 消费）
    constraints: list[Constraint]
    constraint_summary: ConstraintSummary | None

    # verify 产物
    information_gaps: list[str]

    # 产物
    report: Report
