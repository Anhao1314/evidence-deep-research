"""记忆与主图的集成：召回注入、偏好生效、沉淀落库。"""

import re


def test_persist_writes_topic_after_run(research_app):
    research_app.invoke(
        {"question": "RAG 向量库选 Qdrant 还是 Milvus？", "user_id": "mem-user", "run_id": "run-m1"},
        config={"recursion_limit": 64},
    )
    # persist 节点在 END 前：topics 里已有本次研究（通过下一问召回验证）
    final = research_app.invoke(
        {"question": "向量库 Qdrant 与 Milvus 怎么选？", "user_id": "mem-user", "run_id": "run-m2"},
        config={"recursion_limit": 64},
    )
    assert final["related_history"], "第二次研究应召回第一次的历史主题"
    assert "Qdrant" in final["related_history"][0]["question"]


def test_plan_rationale_mentions_recalled_history(research_app):
    research_app.invoke(
        {"question": "Qdrant 和 Milvus 对比", "user_id": "hist-user", "run_id": "run-h1"},
        config={"recursion_limit": 64},
    )
    final = research_app.invoke(
        {"question": "Qdrant 和 Milvus 对比", "user_id": "hist-user", "run_id": "run-h2"},
        config={"recursion_limit": 64},
    )
    assert "历史" in final["plan"].rationale


def test_report_language_preference_takes_effect(research_app, runtime):
    runtime.memory.set_preference("lang-user", "report_language", "en")
    final = research_app.invoke(
        {"question": "向量库 Qdrant 还是 Milvus？", "user_id": "lang-user", "run_id": "run-lang"},
        config={"recursion_limit": 64},
    )
    md = final["report"].markdown
    assert md.startswith("# Technology Selection Research Report")
    assert "Dimension Comparison" in md


def test_default_report_is_chinese(research_app):
    final = research_app.invoke(
        {"question": "向量库 Qdrant 还是 Milvus？", "user_id": "zh-user", "run_id": "run-zh"},
        config={"recursion_limit": 64},
    )
    assert final["report"].markdown.startswith("# 技术选型研究报告")


def test_report_carries_evidences_for_memory_and_eval(research_app):
    final = research_app.invoke(
        {"question": "Qdrant 和 Milvus 怎么选？", "user_id": "ev-user", "run_id": "run-ev"},
        config={"recursion_limit": 64},
    )
    report = final["report"]
    assert report.evidences, "报告应携带证据明细（记忆沉淀与评测断言的输入）"
    assert report.summary, "报告应携带结论摘要（记忆召回展示用）"
    # 摘要不携带引用标记
    assert not re.search(r"\[E\d+\]", report.summary)
