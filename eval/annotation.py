"""引用语义支撑的人工复核：模板生成、运行绑定与统计。

为什么不用 LLM Judge：
- 自动指标只能证明“引用编号存在且可回溯”，不能证明“证据支撑结论”；
- 用另一个模型给事实正确率打分，会把模型偏差包装成人工指标；
- 因此语义支撑只接受人工标签，未标注时输出 NOT_MEASURED。

运行绑定：
- 每行绑定 run_id、report_sha256、template_sha256、claim_id；
- summarize 时拒绝未知 claim、跨 run 标签、重复冲突、模板/报告哈希不匹配；
- 抽样按 section/object 分层，记录 seed；样本不足时如实说明。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.schema.report import Report

LABELS = ("supported", "partial", "unsupported", "cannot_judge")
_CITE_RE = re.compile(r"\[(E\d+)\]")
_SKIP_SECTIONS = {"证据明细", "参考来源", "evidence details", "sources"}

TEMPLATE_FIELDS = [
    "run_id",
    "report_sha256",
    "template_sha256",
    "sampling_seed",
    "claim_id",
    "case_id",
    "category",
    "report_id",
    "object_name",
    "section",
    "stratum",
    "claim_text",
    "cited_evidence_ids",
    "evidence_quotes",
    "source_refs",
    "label",
    "rationale",
    "annotator",
    "annotated_at",
]
_FILL_FIELDS = {"label", "rationale", "annotator", "annotated_at"}

DEFINITION = (
    "人工语义支撑率：复核者阅读 claim 与所引证据原文后，判断证据是否足以支撑该结论。"
    "supported=完全支撑；partial=部分支撑或需要额外限定；unsupported=不支撑/相反；"
    "cannot_judge=来源缺失、打不开或信息不足，无法判断。分母为已标注 claim 数。"
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[#>\-\*\s]+", "", text)
    text = text.replace("**", "").strip()
    return text[:500]


def extract_claims(markdown: str, report: Report, skip_sections: set[str] | None = None) -> list[dict]:
    """从报告正文抽取带 [E#] 的结论句/表格单元；跳过证据明细与来源清单。"""
    skip = {s.lower() for s in (skip_sections or _SKIP_SECTIONS)}
    evidence_by_id = {e.evidence_id: e for e in report.evidences}
    claims: list[dict] = []
    seen: set[str] = set()
    section = "正文"

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("##"):
            section = line.lstrip("#").strip()
            continue
        if section.lower() in skip or not line:
            continue
        if re.match(r"^[-*]\s*\*\*\[E\d+\]\*\*", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")] if line.startswith("|") else [line]
        for cell in cells:
            ids = sorted(set(_CITE_RE.findall(cell)))
            if not ids:
                continue
            text = _clean_text(cell)
            if not text or text in seen:
                continue
            seen.add(text)
            quotes, sources, objects = [], [], []
            for eid in ids:
                ev = evidence_by_id.get(eid)
                if ev is None:
                    continue
                quotes.append(f"[{eid}] {ev.quote[:220]}")
                sources.append(f"[{ev.source.source_id}] {ev.source.ref}")
                if ev.object_name:
                    objects.append(ev.object_name)
            obj = objects[0] if objects else ""
            claims.append({
                "section": section,
                "object_name": obj,
                "stratum": f"{section}|{obj or 'general'}",
                "claim_text": text,
                "cited_evidence_ids": " ".join(ids),
                "evidence_quotes": " || ".join(quotes),
                "source_refs": " || ".join(dict.fromkeys(sources)),
            })
    return claims


def _iter_case_dirs(run_dir: Path):
    cases_dir = run_dir / "cases"
    if not cases_dir.exists():
        raise FileNotFoundError(f"{run_dir} 下没有 cases/ 目录，无法生成标注模板")
    return sorted(p for p in cases_dir.iterdir() if p.is_dir())


def _sample_stratified(claims: list[dict], per_case: int, seed: int, case_id: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for claim in claims:
        groups[claim["stratum"]].append(claim)
    rng = random.Random(f"{seed}:{case_id}")
    for group in groups.values():
        rng.shuffle(group)
    keys = sorted(groups)
    picked: list[dict] = []
    while len(picked) < per_case and any(groups[k] for k in keys):
        for key in keys:
            if groups[key] and len(picked) < per_case:
                picked.append(groups[key].pop(0))
    return picked


def _template_hash(rows: list[dict[str, str]]) -> str:
    canonical = [
        {k: v for k, v in row.items() if k != "template_sha256" and k not in _FILL_FIELDS}
        for row in rows
    ]
    return _sha256_text(json.dumps(canonical, ensure_ascii=False, sort_keys=True))


def build_template(run_dir: Path, per_case: int = 3, seed: int = 0) -> tuple[list[dict[str, str]], dict]:
    manifest_path = run_dir / "run_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )
    default_run_id = manifest.get("run_id", "unknown-run")
    rows: list[dict[str, str]] = []
    shortfall = False
    for case_dir in _iter_case_dirs(run_dir):
        report_path = case_dir / "report.json"
        if not report_path.exists():
            continue
        result_path = case_dir / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
        claims = extract_claims(report.markdown, report)
        picked = _sample_stratified(claims, per_case, seed, result.get("case_id", case_dir.name))
        if len(picked) < per_case:
            shortfall = True
        report_hash = _sha256_text(report.markdown)
        for i, claim in enumerate(picked, start=1):
            rows.append({
                "run_id": default_run_id,
                "report_sha256": report_hash,
                "template_sha256": "",
                "sampling_seed": str(seed),
                "claim_id": f"{report.report_id}-C{i}",
                "case_id": result.get("case_id", case_dir.name),
                "category": result.get("category", ""),
                "report_id": report.report_id,
                "object_name": claim["object_name"],
                "section": claim["section"],
                "stratum": claim["stratum"],
                "claim_text": claim["claim_text"],
                "cited_evidence_ids": claim["cited_evidence_ids"],
                "evidence_quotes": claim["evidence_quotes"],
                "source_refs": claim["source_refs"],
                "label": "",
                "rationale": "",
                "annotator": "",
                "annotated_at": "",
            })
    template_sha = _template_hash(rows) if rows else ""
    for row in rows:
        row["template_sha256"] = template_sha
    rows_meta = {
        "schema_version": "1.0",
        "run_id": default_run_id,
        "template_sha256": template_sha,
        "sampling_seed": seed,
        "per_case": per_case,
        "sample_shortfall": shortfall,
        "sampled_claims": len(rows),
        "claims": {
            row["claim_id"]: {
                "report_id": row["report_id"],
                "report_sha256": row["report_sha256"],
                "case_id": row["case_id"],
                "claim_text": row["claim_text"],
                "object_name": row["object_name"],
                "section": row["section"],
                "stratum": row["stratum"],
            }
            for row in rows
        },
        "note": "抽样按 section/object 分层；seed 固定时样本可复现。",
    }
    return rows, rows_meta


def build_rows(run_dir: Path, per_case: int = 3, seed: int = 0) -> list[dict[str, str]]:
    """兼容接口：只返回行；需要模板元数据时用 build_template。"""
    return build_template(run_dir, per_case=per_case, seed=seed)[0]


def write_template(run_dir: Path, output: Path, per_case: int = 3, seed: int = 0) -> int:
    rows, meta = build_template(run_dir, per_case=per_case, seed=seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    meta["template_file_sha256"] = _sha256_file(output)
    meta_path = output.with_name(f"{output.stem}.meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def load_labels(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    missing = [f for f in TEMPLATE_FIELDS if rows and f not in rows[0]]
    if missing:
        raise ValueError(f"标注文件缺少字段: {missing}")
    for i, row in enumerate(rows, start=2):
        label = (row.get("label") or "").strip()
        if label and label not in LABELS:
            raise ValueError(f"第 {i} 行 label={label!r} 非法，允许 {LABELS}")
    return rows


def validate_labels(
    rows: list[dict[str, str]],
    run_dir: Path,
    *,
    template_path: Path | None = None,
    meta_path: Path | None = None,
) -> dict[str, Any]:
    """拒绝未知 claim、跨 run、重复冲突、模板/报告哈希不匹配的标签。"""
    template_path = template_path or (run_dir / "annotation_template.csv")
    meta_path = meta_path or template_path.with_name(f"{template_path.stem}.meta.json")
    if not template_path.exists():
        raise ValueError(f"模板不存在: {template_path}")
    if not meta_path.exists():
        raise ValueError(f"模板元数据不存在: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"run_manifest.json 不存在: {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = manifest.get("run_id")
    if meta.get("run_id") != run_id:
        raise ValueError(f"模板属于 run={meta.get('run_id')}，当前 run={run_id}")
    if meta.get("template_file_sha256") != _sha256_file(template_path):
        raise ValueError("模板文件哈希与元数据不一致：模板已被修改")

    template_rows = load_labels(template_path)
    if _template_hash(template_rows) != meta.get("template_sha256"):
        raise ValueError("模板内容哈希与元数据不一致：模板已被修改")

    claims_meta = meta.get("claims", {})
    current_reports: dict[str, Report] = {}
    for case_dir in _iter_case_dirs(run_dir):
        report_path = case_dir / "report.json"
        if report_path.exists():
            report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
            current_reports[report.report_id] = report

    seen: dict[tuple[str, str], str] = {}
    for i, row in enumerate(rows, start=2):
        claim_id = row.get("claim_id", "")
        if row.get("run_id") != run_id:
            raise ValueError(f"第 {i} 行跨 run 标签: {row.get('run_id')} != {run_id}")
        if claim_id not in claims_meta:
            raise ValueError(f"第 {i} 行未知 claim: {claim_id}")
        expected = claims_meta[claim_id]
        if row.get("template_sha256") != meta.get("template_sha256"):
            raise ValueError(f"第 {i} 行 template_sha256 不匹配")
        if row.get("report_sha256") != expected.get("report_sha256"):
            raise ValueError(f"第 {i} 行 report_sha256 与模板不匹配")
        report = current_reports.get(row.get("report_id", ""))
        if report is None or _sha256_text(report.markdown) != row.get("report_sha256"):
            raise ValueError(f"第 {i} 行报告哈希与当前产物不匹配：报告已变化")
        label = (row.get("label") or "").strip()
        annotator = (row.get("annotator") or "").strip()
        if label:
            key = (claim_id, annotator or f"row{i}")
            if key in seen:
                raise ValueError(f"第 {i} 行重复标注: claim={claim_id}, annotator={annotator}")
            seen[key] = label
    by_claim: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        label = (row.get("label") or "").strip()
        if label:
            by_claim[row.get("claim_id", "")].add(label)
    conflicts = sorted(k for k, v in by_claim.items() if len(v) > 1)
    if conflicts:
        raise ValueError(f"同一 claim 存在冲突标签: {conflicts}")

    return {
        "status": "VALIDATED",
        "run_id": run_id,
        "template_sha256": meta.get("template_sha256"),
        "template_file_sha256": meta.get("template_file_sha256"),
        "sampling_seed": meta.get("sampling_seed"),
        "claims_in_template": len(claims_meta),
        "report_hashes_checked": len(current_reports),
    }


def summarize_labels(rows: list[dict[str, str]], *, validation: dict | None = None) -> dict[str, Any]:
    """统计人工标签。未标注时返回 NOT_MEASURED，所有比率保持 None。"""
    labeled = [r for r in rows if (r.get("label") or "").strip()]
    counts = Counter((r.get("label") or "").strip() for r in labeled)
    n = len(labeled)
    rates = {label: (round(counts.get(label, 0) / n, 4) if n else None) for label in LABELS}

    by_case: dict[str, Counter] = defaultdict(Counter)
    for row in labeled:
        by_case[row.get("case_id", "")][row["label"].strip()] += 1

    per_claim: dict[str, list[str]] = defaultdict(list)
    for row in labeled:
        per_claim[row.get("claim_id", "")].append(row["label"].strip())
    multi = {k: v for k, v in per_claim.items() if len(v) > 1}
    if multi:
        agreement = round(sum(1 for v in multi.values() if len(set(v)) == 1) / len(multi), 4)
        agreement_block = {
            "status": "MEASURED",
            "value": agreement,
            "claims_with_multiple_labels": len(multi),
            "method": "同一 claim 的多个标签完全一致的比例",
        }
    else:
        agreement_block = {
            "status": "NOT_MEASURED",
            "value": None,
            "reason": "没有 claim 获得多份独立标注",
        }

    return {
        "status": "MEASURED" if n else "NOT_MEASURED",
        "definition": DEFINITION,
        "validation": validation or {"status": "NOT_CHECKED",
                                     "reason": "未提供 run_dir/template，无法验证运行绑定"},
        "sampled_claims": len(rows),
        "labeled_claims": n,
        "unlabeled_claims": len(rows) - n,
        "label_coverage": round(n / len(rows), 4) if rows else None,
        "labels": {label: counts.get(label, 0) for label in LABELS},
        "rates": rates,
        "denominator_note": "四个 rate 的分母均为 labeled_claims；cannot_judge 不并入 unsupported",
        "annotators": sorted({(r.get("annotator") or "").strip() for r in labeled
                              if (r.get("annotator") or "").strip()}),
        "inter_annotator_agreement": agreement_block,
        "by_case": {k: dict(v) for k, v in sorted(by_case.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="引用语义支撑人工标注")
    sub = parser.add_subparsers(dest="command", required=True)

    p_template = sub.add_parser("template", help="从一次运行的产物生成标注 CSV")
    p_template.add_argument("--run-dir", type=Path, required=True)
    p_template.add_argument("--output", type=Path, required=True)
    p_template.add_argument("--per-case", type=int, default=3)
    p_template.add_argument("--seed", type=int, default=0)

    p_summary = sub.add_parser("summarize", help="校验并统计已填写的人工标签")
    p_summary.add_argument("--labels", type=Path, required=True)
    p_summary.add_argument("--run-dir", type=Path, required=True)
    p_summary.add_argument("--template", type=Path)
    p_summary.add_argument("--meta", type=Path)
    p_summary.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "template":
        n = write_template(args.run_dir, args.output, per_case=args.per_case, seed=args.seed)
        print(f"已生成 {n} 条分层标注样本 -> {args.output}")
        return 0

    rows = load_labels(args.labels)
    validation = validate_labels(
        rows, args.run_dir, template_path=args.template, meta_path=args.meta
    )
    summary = summarize_labels(rows, validation=validation)
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"人工指标已写入 {args.output}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())