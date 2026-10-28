"""研究报告模型：合成产物 + 引用清单。"""

from pydantic import BaseModel, Field

from app.schema.constraints import Constraint, ConstraintSummary
from app.schema.evidence import Evidence, EvidenceDisposition, Source
from app.schema.guard import GuardAudit, UnverifiedClaim


class Citation(BaseModel):
    """报告内一处引用：位置 -> 证据编号集合。"""

    location: str = Field(description="报告内锚点，如 分维度对比#qdrant.performance")
    evidence_ids: list[str]


class ReportMeta(BaseModel):
    """报告元信息：评测与审计需要的关键行为输出。"""

    routes_used: list[str] = Field(
        default_factory=list, description="实际产出证据的检索路，如 [web, kb]"
    )
    routes_attempted: list[str] = Field(
        default_factory=list, description="计划/尝试过的检索路；失败路不计入 routes_used"
    )
    degraded: bool = Field(default=False, description="是否发生降级（如某路全失败）")
    plan_rounds: int = 0
    step_count: int = 0
    token_usage: int = 0
    information_gaps: list[str] = Field(default_factory=list, description="verify 阶段发现的信息缺口")


class Report(BaseModel):
    report_id: str
    question: str
    trace_id: str = Field(description="与 trace 关联（M3 起写入 trace store）")
    markdown: str = Field(description="Guard 之后的最终报告（主报告只应包含已验证/已标注结论）")
    raw_markdown: str = Field(
        default="", description="Guard 之前的原始模型输出；离线模板模式为空或与 markdown 相同"
    )
    summary: str = Field(default="", description="结论摘要纯文本（记忆沉淀与列表展示用）")
    sources: list[Source]
    citations: list[Citation]
    evidences: list[Evidence] = Field(
        default_factory=list, description="编号后的最终证据明细（记忆沉淀与评测断言的输入）"
    )
    evidence_disposition: list[EvidenceDisposition] = Field(
        default_factory=list, description="原始检索证据的采用/排除审计"
    )
    constraints: list[Constraint] = Field(
        default_factory=list, description="用户明确提出的约束（从问题中抽取）"
    )
    constraint_summary: ConstraintSummary | None = Field(
        default=None, description="约束与结构化证据属性的核验结果"
    )
    guard_audit: GuardAudit | None = Field(
        default=None, description="引用 Guard 的原始输出、重写、隔离与最终动作"
    )
    unverified_claims: list[UnverifiedClaim] = Field(
        default_factory=list, description="被移出主报告的结论（不进入 citations/sources 主张）"
    )
    meta: ReportMeta
