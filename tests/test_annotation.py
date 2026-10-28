"""人工复核模板、运行绑定与分层抽样：未标注必须是 NOT_MEASURED。"""

import csv
import hashlib
import json

import pytest

from app.schema.evidence import Evidence, Source
from app.schema.report import Citation, Report, ReportMeta
from eval.annotation import (
    LABELS,
    TEMPLATE_FIELDS,
    build_rows,
    extract_claims,
    load_labels,
    summarize_labels,
    validate_labels,
    write_template,
)


def _report(markdown: str | None = None) -> Report:
    src = Source(source_id="S1", kind="web", title="Qdrant docs", url="https://qdrant.tech/x",
                 content_hash="h", resolution="retrieval_snapshot")
    ev1 = Evidence(evidence_id="E1", source=src, claim="Qdrant 单机可承载千万级向量",
                   quote="Qdrant 单机即可承载千万级向量，分片集群用于横向扩展。",
                   topic="vector-db", object_name="qdrant", aspect="performance", route="web")
    ev2 = Evidence(evidence_id="E2", source=src, claim="Qdrant 使用 Apache-2.0",
                   quote="Qdrant 采用 Apache-2.0 协议，开源版无功能阉割。",
                   topic="vector-db", object_name="qdrant", aspect="cost", route="web")
    md = markdown or """# 技术选型研究报告

## 结论摘要

**Qdrant**：性能：单机可承载千万级向量 [E1]。

## 分维度对比

| 维度 | Qdrant |
| --- | --- |
| 成本 | Apache-2.0，无功能阉割 [E2] |

## 证据明细

- **[E1]** Qdrant 单机可承载千万级向量（来源 [S1]：https://qdrant.tech/x）

## 参考来源

- **[S1]** Qdrant docs — https://qdrant.tech/x"""
    return Report(
        report_id="r1", question="q", trace_id="r1", markdown=md, summary="s",
        sources=[src], citations=[Citation(location="line#5", evidence_ids=["E1"])],
        evidences=[ev1, ev2],
        meta=ReportMeta(routes_used=["web"], plan_rounds=1, step_count=3),
    )


def _row(claim_id="r1-C1", label="", annotator="alice", **overrides):
    base = {
        "run_id": "run-1",
        "report_sha256": hashlib.sha256(_report().markdown.encode("utf-8")).hexdigest(),
        "template_sha256": "tpl",
        "sampling_seed": "0",
        "claim_id": claim_id,
        "case_id": "c1",
        "category": "multi_object",
        "report_id": "r1",
        "object_name": "qdrant",
        "section": "结论摘要",
        "stratum": "结论摘要|qdrant",
        "claim_text": "t",
        "cited_evidence_ids": "E1",
        "evidence_quotes": "q",
        "source_refs": "s",
        "label": label,
        "rationale": "",
        "annotator": annotator if label else "",
        "annotated_at": "",
    }
    base.update(overrides)
    return base


def _make_run(tmp_path):
    run_dir = tmp_path / "run"
    case_dir = run_dir / "cases" / "c1"
    case_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps({"run_id": "run-1"}),
                                               encoding="utf-8")
    (case_dir / "report.json").write_text(_report().model_dump_json(), encoding="utf-8")
    (case_dir / "report.md").write_text(_report().markdown, encoding="utf-8")
    (case_dir / "result.json").write_text(
        json.dumps({"case_id": "c1", "category": "multi_object",
                    "run_id": "run-1-c1"}), encoding="utf-8"
    )
    return run_dir


def _write_labels(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_extract_claims_skips_evidence_and_source_sections():
    report = _report()
    claims = extract_claims(report.markdown, report)
    texts = [c["claim_text"] for c in claims]
    assert any("千万级向量" in t for t in texts)
    assert any("Apache-2.0" in t for t in texts)
    assert not any("Qdrant docs" in t for t in texts)
    assert all(c["cited_evidence_ids"] for c in claims)
    assert all(c["stratum"] for c in claims)


def test_template_binds_run_and_stratified_sampling(tmp_path):
    run_dir = _make_run(tmp_path)
    rows = build_rows(run_dir, per_case=2, seed=7)
    assert len(rows) == 2
    assert {r["stratum"] for r in rows} == {"结论摘要|qdrant", "分维度对比|qdrant"}
    assert all(r["run_id"] == "run-1" for r in rows)
    assert all(r["sampling_seed"] == "7" for r in rows)
    assert len({r["template_sha256"] for r in rows}) == 1

    out = run_dir / "annotation_template.csv"
    assert write_template(run_dir, out, per_case=2, seed=7) == 2
    assert (run_dir / "annotation_template.meta.json").exists()
    with out.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == TEMPLATE_FIELDS
        assert len(list(reader)) == 2
    assert len(load_labels(out)) == 2


def test_summarize_unlabeled_is_not_measured():
    summary = summarize_labels([_row(), _row("r1-C2")])
    assert summary["status"] == "NOT_MEASURED"
    assert summary["labeled_claims"] == 0
    assert summary["label_coverage"] == 0.0
    assert all(v is None for v in summary["rates"].values())
    assert summary["validation"]["status"] == "NOT_CHECKED"


def test_summarize_label_distribution_and_agreement():
    rows = [
        _row("r1-C1", "supported"),
        _row("r1-C2", "partial"),
        _row("r1-C3", "unsupported"),
        _row("r1-C4", "cannot_judge"),
        _row("r1-C1", "supported"),  # 第二份独立标注，与第一份一致
    ]
    summary = summarize_labels(rows)
    assert summary["status"] == "MEASURED"
    assert summary["labeled_claims"] == 5
    assert summary["labels"] == {"supported": 2, "partial": 1, "unsupported": 1,
                                 "cannot_judge": 1}
    assert summary["rates"]["supported"] == 0.4
    assert summary["inter_annotator_agreement"]["status"] == "MEASURED"
    assert summary["inter_annotator_agreement"]["value"] == 1.0
    assert summary["annotators"] == ["alice"]


def test_load_labels_rejects_unknown_label(tmp_path):
    path = tmp_path / "labels.csv"
    _write_labels(path, [_row("r1-C1", "maybe")])
    with pytest.raises(ValueError):
        load_labels(path)


def test_validate_labels_accepts_matching_run(tmp_path):
    run_dir = _make_run(tmp_path)
    out = run_dir / "annotation_template.csv"
    write_template(run_dir, out, per_case=2)
    rows = load_labels(out)
    rows[0]["label"] = "supported"
    rows[0]["annotator"] = "alice"
    validation = validate_labels(rows, run_dir)
    assert validation["status"] == "VALIDATED"
    assert validation["run_id"] == "run-1"


def test_validate_labels_rejects_cross_run_unknown_and_hash_mismatch(tmp_path):
    run_dir = _make_run(tmp_path)
    out = run_dir / "annotation_template.csv"
    write_template(run_dir, out, per_case=2)
    base = load_labels(out)

    wrong_run = [dict(r) for r in base]
    wrong_run[0]["run_id"] = "other-run"
    with pytest.raises(ValueError, match="跨 run"):
        validate_labels(wrong_run, run_dir)

    unknown = [dict(r) for r in base]
    unknown[0]["claim_id"] = "unknown-claim"
    with pytest.raises(ValueError, match="未知 claim"):
        validate_labels(unknown, run_dir)

    hash_mismatch = [dict(r) for r in base]
    hash_mismatch[0]["report_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="report_sha256"):
        validate_labels(hash_mismatch, run_dir)

    template_mismatch = [dict(r) for r in base]
    template_mismatch[0]["template_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="template_sha256"):
        validate_labels(template_mismatch, run_dir)


def test_validate_labels_rejects_conflicting_duplicates(tmp_path):
    run_dir = _make_run(tmp_path)
    out = run_dir / "annotation_template.csv"
    write_template(run_dir, out, per_case=2)
    base = load_labels(out)
    rows = [dict(base[0]), dict(base[0])]
    rows[0].update({"label": "supported", "annotator": "alice"})
    rows[1].update({"label": "unsupported", "annotator": "bob"})
    with pytest.raises(ValueError, match="冲突标签"):
        validate_labels(rows, run_dir)

    same = [dict(base[0]), dict(base[0])]
    same[0].update({"label": "supported", "annotator": "alice"})
    same[1].update({"label": "supported", "annotator": "alice"})
    with pytest.raises(ValueError, match="重复标注"):
        validate_labels(same, run_dir)


def test_validate_labels_rejects_template_or_report_changed(tmp_path):
    run_dir = _make_run(tmp_path)
    out = run_dir / "annotation_template.csv"
    write_template(run_dir, out, per_case=2)
    rows = load_labels(out)

    # 改动模板后，文件哈希不再匹配 meta
    out.write_text(out.read_text(encoding="utf-8-sig").replace("技术选型", "技术选型 "),
                   encoding="utf-8-sig")
    with pytest.raises(ValueError, match="模板文件哈希"):
        validate_labels(rows, run_dir)

    # 重新生成模板后改动报告，报告哈希不匹配
    write_template(run_dir, out, per_case=2)
    rows = load_labels(out)
    report_path = run_dir / "cases" / "c1" / "report.json"
    changed = _report(markdown=_report().markdown.replace("千万级", "百万级"))
    report_path.write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="报告哈希"):
        validate_labels(rows, run_dir)


def test_allowed_labels_are_frozen():
    assert LABELS == ("supported", "partial", "unsupported", "cannot_judge")