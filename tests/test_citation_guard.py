"""引用 Guard 的行为边界：合法保留、重写修复、结论隔离、混合引用、无引用结论。"""

from app.graph.nodes import make_synthesize_node
from app.llm.base import ComposeResult
from app.report.citation_guard import enforce_citation_guard
from app.schema.evidence import Evidence, Source
from app.schema.guard import GuardAudit, UnverifiedClaim
from app.schema.plan import Plan


def _guard(markdown: str, valid: set[str], *, rewrite=None, fallback=None):
    return enforce_citation_guard(
        markdown, valid, rewrite_fn=rewrite,
        fallback_fn=(lambda: fallback) if fallback is not None else None,
    )


def test_valid_citation_kept_and_guard_not_triggered():
    md = "## 结论\n\nQdrant 支持千万级向量 [E1]。"
    audit = _guard(md, {"E1"})
    assert audit.ran is True
    assert audit.final_markdown == md
    assert audit.claims_checked == 1
    assert audit.claims_quarantined == 0
    assert audit.unverified_claims == []
    assert [a.action for a in audit.actions] == ["kept"]


def test_rewrite_fixes_invalid_citation():
    md = "## 结论\n\nQdrant 支持千万级向量 [E99]。"
    audit = _guard(md, {"E1"}, rewrite=lambda text, ids: text.replace("[E99]", "[E1]"))
    assert audit.rewrite_attempted is True
    assert audit.rewrite_accepted is True
    assert audit.claims_quarantined == 0
    assert "[E1]" in audit.final_markdown and "[E99]" not in audit.final_markdown


def test_rewrite_still_invalid_quarantines_claim_and_falls_back():
    md = "## 结论\n\nQdrant 支持千万级向量 [E99]。"
    audit = _guard(md, {"E1"}, rewrite=lambda text, ids: text,
                   fallback="## 结论\n\n模板结论 [E1]。")
    assert audit.rewrite_accepted is False
    assert audit.rewrite_rejected_reason == "rewrite_still_invalid"
    assert audit.claims_quarantined == 1
    assert audit.fallback_used is True
    assert audit.fail_closed is True
    assert "Qdrant 支持千万级" not in audit.final_markdown
    assert "模板结论" in audit.final_markdown
    assert audit.unverified_claims[0].invalid_citation_ids == ["E99"]


def test_mixed_valid_and_invalid_citations_quarantine_whole_claim():
    md = "## 结论\n\nQdrant 支持千万级向量 [E1][E99]。"
    audit = _guard(md, {"E1"})
    assert audit.claims_quarantined == 1
    assert "Qdrant 支持千万级" not in audit.final_markdown
    claim = audit.unverified_claims[0]
    assert claim.valid_citation_ids == ["E1"]
    assert claim.invalid_citation_ids == ["E99"]


def test_multiple_conclusions_in_one_sentence_are_split():
    md = "## 结论\n\nQdrant 支持千万级 [E1]；Milvus 需要 GPU [E99]。"
    audit = _guard(md, {"E1"})
    assert audit.claims_checked == 2
    assert audit.claims_quarantined == 1
    assert "Qdrant 支持千万级" in audit.final_markdown
    assert "Milvus 需要 GPU" not in audit.final_markdown


def test_factual_claim_without_citation_is_quarantined():
    md = "## 结论\n\nQdrant 是最好的选择。\n\nQdrant 支持千万级 [E1]。"
    audit = _guard(md, {"E1"})
    assert audit.claims_quarantined == 1
    assert "最好的选择" not in audit.final_markdown
    assert "Qdrant 支持千万级" in audit.final_markdown
    assert audit.unverified_claims[0].reason == "missing_citation"


def test_uncertainty_and_gap_statements_without_citation_are_allowed():
    md = (
        "# 报告\n\n## 结论\n\n本轮检索未获得可用证据，无法给出有依据的结论。\n\n"
        "## 信息缺口\n\n- 没有可用证据。\n\n## 参考来源\n\n- [S1] docs"
    )
    audit = _guard(md, set())
    assert audit.claims_checked == 0
    assert audit.claims_quarantined == 0
    assert audit.final_markdown == md


def test_rewrite_that_changes_body_is_rejected():
    md = "## 结论\n\nQdrant 支持千万级向量 [E99]。"

    def _bad_rewrite(text: str, ids: list[str]) -> str:
        return text.replace("千万级", "百万级").replace("[E99]", "[E1]")

    audit = _guard(md, {"E1"}, rewrite=_bad_rewrite)
    assert audit.rewrite_accepted is False
    assert audit.rewrite_rejected_reason == "rewrite_changed_body"
    assert audit.claims_quarantined == 1


def test_rewrite_deleting_citation_does_not_keep_unverified_claim():
    md = "## 结论\n\nQdrant 支持千万级向量 [E99]。"
    audit = _guard(md, {"E1"}, rewrite=lambda text, ids: text.replace("[E99]", ""),
                   fallback="## 结论\n\n模板 [E1]。")
    assert audit.rewrite_accepted is True
    assert audit.claims_quarantined == 1
    assert "Qdrant 支持千万级" not in audit.final_markdown


def test_table_cell_with_invalid_citation_is_replaced():
    md = (
        "## 结论\n\nQdrant 支持千万级 [E1]。\n\n"
        "## 对比\n\n| 维度 | Qdrant |\n| --- | --- |\n| 性能 | 千万级 [E99] |"
    )
    audit = _guard(md, {"E1"})
    assert audit.claims_quarantined == 1
    assert "—（未验证）" in audit.final_markdown
    assert "[E99]" not in audit.final_markdown
    assert "Qdrant 支持千万级 [E1]" in audit.final_markdown


def test_synthesize_copies_guard_audit_into_report(settings):
    src = Source(source_id="S1", kind="web", title="t", url="https://x",
                 content_hash="h", resolution="retrieval_snapshot")
    evidence = Evidence(evidence_id="E1", source=src, claim="c", quote="q",
                        topic="t", object_name="o", aspect="performance", route="web")
    claim = UnverifiedClaim(location="line#2", text="bad", invalid_citation_ids=["E99"],
                            reason="invalid_citation")
    audit = GuardAudit(
        ran=True, raw_markdown="raw [E99]", final_markdown="final [E1]",
        invalid_citation_ids=["E99"], claims_checked=1, claims_quarantined=1,
        actions=[], unverified_claims=[claim], fail_closed=True,
    )

    class _Provider:
        def compose(self, ctx):
            return ComposeResult(
                markdown="final [E1]", raw_markdown="raw [E99]",
                summary="s", sources=[src], citations=[], guard_audit=audit,
                unverified_claims=[claim],
            )

    node = make_synthesize_node(_Provider(), settings)
    out = node({
        "question": "q", "evidences": [evidence], "information_gaps": [],
        "plan": Plan(plan_id="p", subtasks=[]), "run_id": "run-guard-report",
    })
    report = out["report"]
    assert report.raw_markdown == "raw [E99]"
    assert report.guard_audit is not None and report.guard_audit.ran is True
    assert report.unverified_claims[0].text == "bad"
    assert report.markdown == "final [E1]"