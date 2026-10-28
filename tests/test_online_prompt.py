"""在线提示词与合成兜底：信息缺口必须进入 compose 输入，空证据必须声明。"""

import json

from app.domain_registry import DomainRegistry
from app.graph.nodes import make_synthesize_node
from app.llm.base import ComposeContext, ComposeResult
from app.llm.openai_compat import OpenAICompatProvider
from app.schema.evidence import Evidence, Source
from app.schema.plan import Plan


class _CaptureChat:
    def __init__(self):
        self.kwargs = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return "报告正文 [E1]"


def _ctx(gaps):
    src = Source(source_id="S1", kind="web", title="t", url="https://x")
    ev = Evidence(evidence_id="E1", source=src, claim="c", quote="q",
                  topic="t", object_name="o", aspect="performance", route="web")
    return ComposeContext(question="q", evidences=[ev], information_gaps=gaps, trace_id="tr")


def test_online_compose_prompt_includes_information_gaps():
    provider = OpenAICompatProvider(
        base_url="https://example.invalid/v1", api_key="test", model="m",
        registry=DomainRegistry({}),
    )
    capture = _CaptureChat()
    provider._chat = capture  # type: ignore[method-assign]

    provider.compose(_ctx(["缺少 GPU 支持证据", "缺少合规条款"]))
    user = capture.kwargs["user"]
    assert "缺少 GPU 支持证据" in user
    assert "缺少合规条款" in user
    assert "禁止假装已解决" in user
    assert "证据清单" in user


def test_online_compose_without_gaps_has_no_gap_block():
    provider = OpenAICompatProvider(
        base_url="https://example.invalid/v1", api_key="test", model="m",
        registry=DomainRegistry({}),
    )
    capture = _CaptureChat()
    provider._chat = capture  # type: ignore[method-assign]

    provider.compose(_ctx([]))
    assert "已知信息缺口" not in capture.kwargs["user"]


def test_online_plan_parses_constraints():
    provider = OpenAICompatProvider(
        base_url="https://example.invalid/v1", api_key="test", model="m",
        registry=DomainRegistry({}),
    )
    plan = provider._parse_plan(json.dumps({
        "rationale": "r",
        "constraints": [
            {"kind": "team", "text": "5 人团队", "value": "5", "unit": "people"},
            {"kind": "unknown-kind", "text": "X", "value": "1", "unit": ""},
        ],
        "subtasks": [
            {"route": "web", "topic": "t", "object_name": "o", "object_display": "O",
             "aspect": "general", "query": "q"},
        ],
    }))
    assert [c.kind for c in plan.constraints] == ["team", "other"]
    assert plan.constraints[0].value == "5"


def test_synthesize_adds_gap_when_no_evidence(settings):
    class _Provider:
        def compose(self, ctx: ComposeContext) -> ComposeResult:
            assert ctx.information_gaps == ["本轮未获得任何可用证据，无法给出有依据的结论。"]
            return ComposeResult(markdown="无证据", summary="无", sources=[], citations=[])

    node = make_synthesize_node(_Provider(), settings)
    out = node({
        "question": "q",
        "evidences": [],
        "information_gaps": [],
        "plan": Plan(plan_id="p1"),
        "run_id": "run-empty",
    })
    report = out["report"]
    assert report.meta.information_gaps == ["本轮未获得任何可用证据，无法给出有依据的结论。"]
    assert report.meta.routes_used == []
    assert report.meta.routes_attempted == []