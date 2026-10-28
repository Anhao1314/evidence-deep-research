"""人工可读的研究任务集：schema、加载与到行为断言的转换。

任务集与 `eval/cases.jsonl` 的分工：
- `eval/cases.jsonl` 是 **离线行为回归集**，只覆盖内置语料可确定性的路径，CI 必跑；
- `eval/tasks/research_tasks.v1.json` 是 **真实任务评测集**，包含离线不可覆盖的
  在线任务、信息不足任务与检索缺口任务；其中 `offline_eligible=true` 的子集
  可以在没有 API Key 的情况下做结构回归，其余用例必须在线运行后才能评价。

本模块只描述“期望”，不产生任何结果。任何指标都必须来自实际运行产物。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from eval.assertions import EvalCase, EvalExpect

DEFAULT_SUITE = Path(__file__).resolve().parent / "tasks" / "research_tasks.v1.json"

Category = Literal["multi_object", "single_object", "insufficient_info", "replan_gap"]
RouteSpec = Literal["kb", "web", "both", "any"]
ASPECTS = ("performance", "ecosystem", "cost", "maturity")


class AutomatedChecks(BaseModel):
    """单条用例的自动检查阈值。只放结构可判定的规则，语义正确性不在此处。"""

    min_evidences: int = Field(default=0, ge=0)
    citation_coverage_min: float = Field(default=0.0, ge=0.0, le=1.0)
    require_citations: bool = False
    min_dimension_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    must_state_gaps: bool = False
    expect_replan_or_gap: bool = False
    max_plan_rounds: int = Field(default=3, ge=1)
    max_steps: int = Field(default=32, ge=1)
    require_trace: bool = True


class TaskCase(BaseModel):
    case_id: str
    category: Category
    title: str
    question: str = Field(min_length=2)
    research_dimensions: list[str] = Field(
        min_length=1, description="人工可读的预期研究维度（用于人工复核）"
    )
    dimension_aspects: list[str] = Field(
        default_factory=list, description="可自动统计的维度子集，取值见 ASPECTS"
    )
    expected_route: RouteSpec = "any"
    expected_objects: list[str] = Field(
        default_factory=list, description="报告中应出现的对象显示名"
    )
    dimension_objects: list[str] = Field(
        default_factory=list,
        description="证据层对象内部名（如 custom/qwen）；留空时复用 expected_objects",
    )
    automated_checks: AutomatedChecks = Field(default_factory=AutomatedChecks)
    offline_eligible: bool = Field(
        default=False,
        description="内置语料能否确定性覆盖；true 时进入 CI 离线结构回归",
    )
    why_this_case: str = Field(min_length=1)
    human_review_focus: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("dimension_aspects")
    @classmethod
    def _validate_aspects(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(ASPECTS))
        if unknown:
            raise ValueError(f"未知维度 {unknown}，允许 {list(ASPECTS)}")
        return value

    @model_validator(mode="after")
    def _validate_offline(self) -> TaskCase:
        if self.offline_eligible and self.expected_route == "web":
            raise ValueError("offline_eligible 用例不能要求 web-only 路线（内置 Web 语料不含泛化主题）")
        return self

    def expected_routes(self) -> list[str]:
        if self.expected_route == "any":
            return []
        if self.expected_route == "both":
            return ["kb", "web"]
        return [self.expected_route]

    def to_eval_case(self) -> EvalCase:
        checks = self.automated_checks
        return EvalCase(
            case_id=self.case_id,
            category=self.category,
            question=self.question,
            expect=EvalExpect(
                routes=self.expected_routes(),
                min_evidences=checks.min_evidences,
                citation_coverage_min=checks.citation_coverage_min,
                require_citations=checks.require_citations,
                objects_in_report=self.expected_objects,
                must_converge=True,
                max_plan_rounds=checks.max_plan_rounds,
                max_steps=checks.max_steps,
                trace_complete=checks.require_trace,
                must_state_gaps=checks.must_state_gaps,
                expected_dimensions=self.dimension_aspects,
                expected_objects=self.dimension_objects or self.expected_objects,
                min_dimension_coverage=checks.min_dimension_coverage,
                expect_replan_or_gap=checks.expect_replan_or_gap,
            ),
        )


class TaskSuite(BaseModel):
    suite_id: str
    version: str
    created_at: str
    description: str
    route_mode: Literal["both", "web_only"] = "both"
    parent_suite_id: str | None = None
    cases: list[TaskCase]

    @model_validator(mode="after")
    def _validate_unique(self) -> TaskSuite:
        ids = [c.case_id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"case_id 重复: {duplicates}")
        return self

    def offline_cases(self) -> list[TaskCase]:
        return [c for c in self.cases if c.offline_eligible]

    def by_id(self, case_id: str) -> TaskCase | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


def load_suite(path: Path = DEFAULT_SUITE) -> TaskSuite:
    return TaskSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))


def suite_sha256(path: Path = DEFAULT_SUITE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()