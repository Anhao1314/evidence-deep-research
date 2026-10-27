"""LLM Provider 抽象：plan（任务分解）与 compose（报告撰写）两个能力。

离线 HeuristicProvider 用领域规则实现；在线 OpenAICompatProvider 走
OpenAI 兼容协议（DeepSeek/通义/OpenAI 可切换）。上层图逻辑不感知差异。
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.schema.plan import Plan
from app.schema.report import Citation


@dataclass
class PlanContext:
    question: str
    preferences: dict[str, str] = field(default_factory=dict)
    # replan 时聚焦的缺口：[{"topic","object","missing_aspects":[...]}]
    replan_focus: list[dict] = field(default_factory=list)


@dataclass
class ComposeContext:
    question: str
    evidences: list  # list[Evidence]
    information_gaps: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)


@dataclass
class ComposeResult:
    markdown: str
    citations: list[Citation] = field(default_factory=list)
    sources: list = field(default_factory=list)  # list[Source]，去重合并后的来源清单


class LLMProvider(Protocol):
    def plan(self, ctx: PlanContext) -> Plan: ...
    def compose(self, ctx: ComposeContext) -> ComposeResult: ...
