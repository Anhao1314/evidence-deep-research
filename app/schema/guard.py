"""引用 Guard 的审计结构。

Guard 只保证“不存在的证据编号不会继续作为有效引用展示”；它不判断
证据是否在语义上支撑结论。因此所有被处理过的结论都必须留下原始文本、
非法编号、重写结果与最终动作，供人工复核。
"""

from typing import Literal

from pydantic import BaseModel, Field

GuardActionType = Literal[
    "not_run",
    "kept",
    "rewrite_accepted",
    "rewrite_rejected",
    "claim_quarantined",
    "table_cell_quarantined",
    "fallback_template",
    "fail_closed",
]


class UnverifiedClaim(BaseModel):
    """被移出主报告的结论：正文仍在审计结构里，但不作为已验证结论展示。"""

    location: str
    text: str
    invalid_citation_ids: list[str] = Field(default_factory=list)
    valid_citation_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    action: Literal["quarantined", "fallback"] = "quarantined"


class GuardAction(BaseModel):
    action: GuardActionType
    detail: str = ""


class GuardAudit(BaseModel):
    ran: bool = False
    raw_markdown: str = ""
    final_markdown: str = ""
    diff: str = Field(default="", description="原始输出与最终报告的 unified diff")
    invalid_citation_ids: list[str] = Field(default_factory=list)
    rewrite_attempted: bool = False
    rewrite_accepted: bool | None = None
    rewrite_rejected_reason: str | None = None
    claims_checked: int = 0
    claims_quarantined: int = 0
    actions: list[GuardAction] = Field(default_factory=list)
    unverified_claims: list[UnverifiedClaim] = Field(default_factory=list)
    fallback_used: bool = False
    fail_closed: bool = False