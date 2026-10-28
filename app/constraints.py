"""用户约束抽取与结构化核验（纯函数）。

边界：
- 只比较证据 `attributes` 中的结构化字段，不从自由文本猜结论；
- 没有属性一律 unknown，并生成信息缺口；
- 该模块只判断“证据是否覆盖/违反约束”，不判断结论在语义上是否正确；
- 推荐/排除是基于结构化属性的确定性结果，不是 LLM 判断。
"""

from __future__ import annotations

import re
from typing import Any

from app.schema.constraints import Constraint, ConstraintFinding, ConstraintSummary
from app.schema.evidence import Evidence

_SCALE_UNITS = {"万": 10_000, "亿": 100_000_000, "million": 1_000_000,
                "m": 1_000_000, "billion": 1_000_000_000, "b": 1_000_000_000}
_COST_TIERS = {"low": 0, "低成本": 0, "中": 1, "medium": 1, "high": 2, "高": 2}


def _add(out: list[Constraint], kind: str, text: str, value: str = "", unit: str = "") -> None:
    if any(c.kind == kind and c.value == value for c in out):
        return
    out.append(Constraint(constraint_id=f"C{len(out) + 1}", kind=kind, text=text,
                          value=value, unit=unit))


def extract_constraints(question: str) -> list[Constraint]:
    """从问题中抽取可机器核验的约束；抽取不到的约束不会凭自由文本推断。"""
    q = question.strip()
    out: list[Constraint] = []

    # 数据规模：5000 万条 768 维向量 / 50 million vectors / 1,000,000 records
    for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(万|亿|million|billion|m|b)\s*(?:条|个)?"
        r"[^。；，,]{0,16}?(?:向量|文档|事件|记录|vectors?|rows?|events?)",
        q,
        re.IGNORECASE,
    ):
        number, unit = m.group(1), m.group(2).lower()
        value = int(float(number) * _SCALE_UNITS[unit])
        _add(out, "scale", m.group(0), str(value), "vectors")
    for m in re.finditer(r"(\d{4,})\s*(?:条|个)?\s*(?:向量|文档|事件|记录|vectors?|rows?|events?)",
                         q, re.IGNORECASE):
        if not any(c.kind == "scale" for c in out):
            _add(out, "scale", m.group(0), str(int(m.group(1))), "vectors")

    # 团队规模 / 运维能力
    for m in re.finditer(r"(\d+)\s*(?:人|名(?:工程师|研发|运维)?|people|engineers?|person)",
                         q, re.IGNORECASE):
        _add(out, "team", m.group(0), m.group(1), "people")

    # 成本
    if re.search(r"低成本|成本敏感|预算有限|low[- ]cost|budget[- ]constrained", q, re.IGNORECASE):
        _add(out, "cost", "低成本/预算有限", "low", "tier")
    for m in re.finditer(r"(?:预算|成本)[^，。；]{0,10}?(\d+(?:\.\d+)?)\s*(万|美元|usd|元)", q, re.IGNORECASE):
        _add(out, "cost", m.group(0), f"{m.group(1)}{m.group(2)}", "budget")

    # 部署方式
    for keyword, value in (("自托管", "self-hosted"), ("私有化", "self-hosted"),
                           ("on-prem", "self-hosted"), ("self-hosted", "self-hosted"),
                           ("云托管", "managed-cloud"), ("managed cloud", "managed-cloud")):
        if keyword in q.lower():
            _add(out, "deployment", keyword, value)
            break

    # 技术栈 / 复用既有基础设施
    for m in re.finditer(
        r"(?:必须复用|复用|必须使用|基于)\s*(PostgreSQL|MySQL|Kubernetes|K8s|Java|Python|Go|Rust|Redis|Kafka)",
        q,
        re.IGNORECASE,
    ):
        _add(out, "stack", m.group(0), m.group(1).lower(), "stack")
    if re.search(r"不引入新(?:数据库|中间件|组件)|避免引入新(?:数据库|中间件|组件)", q):
        _add(out, "stack", "不引入新数据库/中间件", "no_new_infra", "policy")

    # 延迟 / 可用性
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%\s*(?:可用性|availability|SLA)", q, re.IGNORECASE):
        _add(out, "latency", m.group(0), m.group(1), "availability_pct")
    for m in re.finditer(r"P99\s*(?:<|低于|<=)?\s*(\d+)\s*(?:ms|毫秒)", q, re.IGNORECASE):
        _add(out, "latency", m.group(0), m.group(1), "p99_ms")

    # 合规
    for keyword in ("合规", "审计", "等保", "GDPR", "数据不出境"):
        if keyword.lower() in q.lower():
            _add(out, "compliance", keyword, keyword.lower())
            break

    return out[:8]


def _merge_attributes(evidences: list[Evidence]) -> dict[str, dict[str, Any]]:
    by_object: dict[str, dict[str, Any]] = {}
    for ev in evidences:
        obj = ev.object_name.lower()
        if not obj or not ev.attributes:
            continue
        merged = by_object.setdefault(obj, {})
        for key, value in ev.attributes.items():
            if isinstance(value, list):
                existing = merged.setdefault(key, [])
                if isinstance(existing, list):
                    merged[key] = sorted({*existing, *value})
            elif isinstance(value, dict):
                existing = merged.setdefault(key, {})
                if isinstance(existing, dict):
                    merged[key] = {**existing, **value}
            elif key not in merged:
                merged[key] = value
    return by_object


def _finding(constraint: Constraint, object_name: str, attrs: dict[str, Any],
             evidence_ids: list[str]) -> ConstraintFinding:
    kind = constraint.kind
    if not attrs:
        return ConstraintFinding(
            constraint_id=constraint.constraint_id, object_name=object_name, status="unknown",
            evidence_ids=evidence_ids, detail="缺少结构化属性，无法核验",
        )

    if kind == "scale":
        max_vectors = attrs.get("max_vectors")
        if not isinstance(max_vectors, (int, float)):
            return _unknown(constraint, object_name, evidence_ids, "缺少 max_vectors")
        required = int(constraint.value or 0)
        ok = max_vectors >= required
        return _status(constraint, object_name, evidence_ids, ok,
                       f"支持上限 {int(max_vectors):,} / 要求 {required:,}")

    if kind == "team":
        min_team = attrs.get("min_team")
        if not isinstance(min_team, (int, float)):
            return _unknown(constraint, object_name, evidence_ids, "缺少 min_team")
        required = int(constraint.value or 0)
        ok = min_team <= required
        return _status(constraint, object_name, evidence_ids, ok,
                       f"最低团队 {int(min_team)} 人 / 可用 {required} 人")

    if kind == "cost":
        tier = attrs.get("cost_tier")
        required_text = (constraint.value or "").lower()
        if not isinstance(tier, (int, float)) or required_text not in _COST_TIERS:
            return _unknown(constraint, object_name, evidence_ids, "缺少可比较的 cost_tier")
        required = _COST_TIERS[required_text]
        ok = tier <= required
        return _status(constraint, object_name, evidence_ids, ok,
                       f"成本等级 {int(tier)} / 上限 {required}")

    if kind == "deployment":
        deployments = attrs.get("deployment")
        if deployments is None:
            return _unknown(constraint, object_name, evidence_ids, "缺少 deployment")
        values = deployments if isinstance(deployments, list) else [deployments]
        ok = constraint.value in {str(v).lower() for v in values}
        return _status(constraint, object_name, evidence_ids, ok,
                       f"支持部署方式 {values} / 要求 {constraint.value}")

    if kind == "stack":
        supports = {str(v).lower() for v in attrs.get("supports_stack", [])}
        excludes = {str(v).lower() for v in attrs.get("excludes_stack", [])}
        required = constraint.value.lower()
        if required in excludes:
            return _status(constraint, object_name, evidence_ids, False,
                           f"明确排除 {required}")
        if required in supports:
            return _status(constraint, object_name, evidence_ids, True,
                           f"支持 {required}")
        if required == "no_new_infra" and "new_infra_required" in attrs:
            ok = not bool(attrs["new_infra_required"])
            return _status(constraint, object_name, evidence_ids, ok,
                           f"需要新基础设施={bool(attrs['new_infra_required'])}")
        return _unknown(constraint, object_name, evidence_ids,
                        f"属性未覆盖技术栈 {required}")

    if kind == "latency":
        if constraint.unit == "availability_pct":
            availability = attrs.get("availability_pct")
            if not isinstance(availability, (int, float)):
                return _unknown(constraint, object_name, evidence_ids, "缺少 availability_pct")
            required = float(constraint.value)
            return _status(constraint, object_name, evidence_ids, availability >= required,
                           f"可用性 {availability}% / 要求 {required}%")
        p99 = attrs.get("p99_latency_ms")
        if not isinstance(p99, (int, float)):
            return _unknown(constraint, object_name, evidence_ids, "缺少 p99_latency_ms")
        required = float(constraint.value)
        return _status(constraint, object_name, evidence_ids, p99 <= required,
                       f"P99 {p99}ms / 要求 ≤{required}ms")

    if kind == "compliance":
        values = {str(v).lower() for v in attrs.get("compliance", [])}
        if not values:
            return _unknown(constraint, object_name, evidence_ids, "缺少 compliance 属性")
        return _status(constraint, object_name, evidence_ids,
                       constraint.value.lower() in values,
                       f"合规项 {sorted(values)} / 要求 {constraint.value}")

    return _unknown(constraint, object_name, evidence_ids, "暂不支持的约束类型")


def _unknown(constraint: Constraint, object_name: str, evidence_ids: list[str],
             detail: str) -> ConstraintFinding:
    return ConstraintFinding(constraint_id=constraint.constraint_id, object_name=object_name,
                             status="unknown", evidence_ids=evidence_ids, detail=detail)


def _status(constraint: Constraint, object_name: str, evidence_ids: list[str], ok: bool,
            detail: str) -> ConstraintFinding:
    return ConstraintFinding(constraint_id=constraint.constraint_id, object_name=object_name,
                             status="satisfied" if ok else "violated",
                             evidence_ids=evidence_ids, detail=detail)


def evaluate_constraints(constraints: list[Constraint],
                         evidences: list[Evidence],
                         candidate_objects: list[str] | None = None) -> ConstraintSummary:
    if not constraints:
        return ConstraintSummary(verdict="not_checked")

    attrs_by_object = _merge_attributes(evidences)
    evidence_ids_by_object: dict[str, list[str]] = {}
    for ev in evidences:
        if ev.object_name:
            evidence_ids_by_object.setdefault(ev.object_name.lower(), []).append(ev.evidence_id)

    if candidate_objects:
        objects = sorted({o.lower() for o in candidate_objects if o})
    else:
        objects = sorted(set(attrs_by_object) | set(evidence_ids_by_object))
    if not objects:
        return ConstraintSummary(
            verdict="insufficient",
            gaps=["没有可用于约束核验的对象证据；无法判断约束是否满足。"],
        )

    findings: list[ConstraintFinding] = []
    excluded: dict[str, list[str]] = {}
    unknown_objects: list[str] = []
    satisfied_objects: list[str] = []
    gaps: list[str] = []

    for obj in objects:
        attrs = attrs_by_object.get(obj, {})
        obj_findings = [
            _finding(c, obj, attrs, sorted(set(evidence_ids_by_object.get(obj, []))))
            for c in constraints
        ]
        findings.extend(obj_findings)
        violated = [f.constraint_id for f in obj_findings if f.status == "violated"]
        unknown = [f for f in obj_findings if f.status == "unknown"]
        if violated:
            excluded[obj] = violated
        if unknown:
            unknown_objects.append(obj)
            for f in unknown:
                gaps.append(f"{obj} 的约束 {f.constraint_id} 无法核验（{f.detail}）")
        if not violated and not unknown:
            satisfied_objects.append(obj)

    if satisfied_objects:
        if len(satisfied_objects) == 1 and all(
            obj in excluded for obj in objects if obj != satisfied_objects[0]
        ):
            verdict = "recommend"
        else:
            verdict = "conditional"
    elif excluded and len(excluded) == len(objects):
        verdict = "no_solution"
        gaps.append("没有任何对象同时满足全部约束；需要放宽至少一项约束或补充新方案。")
    else:
        verdict = "insufficient"

    return ConstraintSummary(
        verdict=verdict,
        recommended_objects=satisfied_objects if verdict in {"recommend", "conditional"} else [],
        excluded=excluded,
        unknown_objects=sorted(set(unknown_objects)),
        findings=findings,
        gaps=gaps,
        checked_objects=objects,
    )