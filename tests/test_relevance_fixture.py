"""离线预置相关性回归：无关证据不得进入最终报告或引用列表。

这不是在线通用语义相关率；标签是人工预置的固定 fixture。
"""

import json

from app.config import PROJECT_ROOT
from app.graph.factory import build_default_app


def test_out_of_domain_evidence_is_excluded_with_labels(settings):
    labels = json.loads(
        (PROJECT_ROOT / "eval" / "fixtures" / "relevance_labels.v1.json").read_text(
            encoding="utf-8"
        )
    )["cases"]["ood-community-health"]
    runtime = build_default_app(settings.model_copy(update={"mcp_kb_enabled": False}))
    try:
        final = runtime.graph.invoke(
            {"question": labels["question"], "user_id": "relevance",
             "run_id": "relevance-ood"},
            config={"recursion_limit": 128},
        )
    finally:
        runtime.close()

    report = final["report"]
    assert final.get("raw_evidences"), "检索层应实际返回过文档，才能证明是‘排除’而不是‘没检索到’"
    assert report.evidences == [], "无关证据不得作为研究依据进入报告"
    assert report.sources == [], "无关来源不得进入最终来源列表"
    assert report.citations == [], "无关证据不得产生引用"
    assert report.meta.information_gaps, "排除后必须保留信息缺口"
    assert "信息缺口" in report.markdown

    disposition = report.evidence_disposition
    assert disposition
    assert all(d.status == "excluded" for d in disposition)
    assert all(d.reason == "unscoped_question" for d in disposition)
    assert all(d.evidence_id is None for d in disposition)

    # 人工标签中的无关文档不得出现在最终报告/来源里
    for doc_id in labels["irrelevant_doc_ids"]:
        assert doc_id not in report.markdown
        assert all(doc_id not in (s.doc_id or "") for s in report.sources)
    assert "Qdrant" not in report.markdown and "Milvus" not in report.markdown