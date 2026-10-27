"""主图端到端（离线）：报告、引用一致性、双路汇聚。"""

import re


def _run(app_graph, question: str) -> dict:
    final = app_graph.invoke(
        {"question": question, "user_id": "test", "run_id": "run-test0001"},
        config={"recursion_limit": 64},
    )
    return final


def test_e2e_comparison_question_produces_cited_report(research_app):
    final = _run(research_app, "RAG 系统的向量库选 Qdrant 还是 Milvus？")
    report = final["report"]
    assert report is not None
    evidences = final["evidences"]
    assert evidences, "端到端应有证据产出"

    # 引用强制：报告中的 [E#] 必须全部来自真实证据集合
    cited = set(re.findall(r"\[(E\d+)\]", report.markdown))
    valid = {e.evidence_id for e in evidences}
    assert cited, "报告应包含引用标注"
    assert cited <= valid, f"存在编造引用: {cited - valid}"

    # 来源一致性：报告来源清单与证据来源一一对应
    assert report.sources
    source_ids = {s.source_id for s in report.sources}
    assert all(e.source.source_id in source_ids for e in evidences)

    # 双路汇聚
    assert set(report.meta.routes_used) == {"kb", "web"}
    # 对比型报告应有分维度对比表
    assert "分维度对比" in report.markdown
    assert "Qdrant" in report.markdown and "Milvus" in report.markdown
    # 无降级、单轮收敛（内置语料覆盖全部维度）
    assert report.meta.degraded is False
    assert report.meta.plan_rounds == 1
    assert report.trace_id == "run-test0001" == report.report_id


def test_e2e_citations_in_comparison_table(research_app):
    final = _run(research_app, "向量库 Qdrant 和 Milvus 怎么选？")
    report = final["report"]
    table_locations = [c for c in report.citations if c.location.startswith("table#")]
    assert table_locations, "对比表单元格应记录引用"
    valid = {e.evidence_id for e in final["evidences"]}
    assert all(set(c.evidence_ids) <= valid for c in table_locations)


def test_e2e_out_of_domain_still_converges(research_app):
    final = _run(research_app, "怎么评估开源项目的健康度？")
    report = final["report"]
    assert report is not None, "领域外问题也必须产出报告（泛化降级路径）"
    assert report.meta.plan_rounds == 1  # 无领域缺口 -> 不 replan


def test_e2e_agent_framework_question(research_app):
    final = _run(research_app, "Agent 编排用 LangGraph 还是自研？")
    report = final["report"]
    assert report is not None
    assert "LangGraph" in report.markdown
    evidences = final["evidences"]
    assert {e.topic for e in evidences} == {"agent-framework"}


def test_merge_assigns_global_ids(research_app):
    final = _run(research_app, "DeepSeek 和通义千问 API 怎么选？")
    evidences = final["evidences"]
    ids = [e.evidence_id for e in evidences]
    assert ids == [f"E{i}" for i in range(1, len(ids) + 1)]
    source_ids = {e.source.source_id for e in evidences}
    assert source_ids == {f"S{i}" for i in range(1, len(source_ids) + 1)}
    # 同一来源重复命中只保留最高分
    locators = [(e.source.kind, e.source.url or e.source.doc_id, e.source.locator or "") for e in evidences]
    assert len(locators) == len(set(locators))
