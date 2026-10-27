"""研究报告模型：合成产物 + 引用清单。"""

from pydantic import BaseModel, Field

from app.schema.evidence import Evidence, Source


class Citation(BaseModel):
    """报告内一处引用：位置 -> 证据编号集合。"""

    location: str = Field(description="报告内锚点，如 分维度对比#qdrant.performance")
    evidence_ids: list[str]


class ReportMeta(BaseModel):
    """报告元信息：评测与审计需要的关键行为输出。"""

    routes_used: list[str] = Field(default_factory=list, description="实际使用的检索路，如 [web, kb]")
    degraded: bool = Field(default=False, description="是否发生降级（如某路全失败）")
    plan_rounds: int = 0
    step_count: int = 0
    token_usage: int = 0
    information_gaps: list[str] = Field(default_factory=list, description="verify 阶段发现的信息缺口")


class Report(BaseModel):
    report_id: str
    question: str
    trace_id: str = Field(description="与 trace 关联（M3 起写入 trace store）")
    markdown: str
    summary: str = Field(default="", description="结论摘要纯文本（记忆沉淀与列表展示用）")
    sources: list[Source]
    citations: list[Citation]
    evidences: list[Evidence] = Field(
        default_factory=list, description="编号后的最终证据明细（记忆沉淀与评测断言的输入）"
    )
    meta: ReportMeta
