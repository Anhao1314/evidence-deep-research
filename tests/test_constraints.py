"""约束抽取与结构化核验：约束必须真正改变结论方向，而不是关键词装饰。"""

from app.constraints import evaluate_constraints, extract_constraints
from app.schema.evidence import Evidence, Source


def _ev(i: int, obj: str, attrs: dict | None = None) -> Evidence:
    return Evidence(
        evidence_id=f"E{i}", source=Source(source_id=f"S{i}", kind="kb", title="t",
                                          doc_id=f"d{i}", content_hash="h",
                                          resolution="kb_document"),
        claim="c", quote="q", topic="vector-db", object_name=obj,
        aspect="performance", route="kb", attributes=attrs or {},
    )


def test_extract_constraints_scale_team_cost_deployment_stack():
    q = ("我们需要 5000 万条 768 维向量、5 人团队、自托管、预算有限，"
         "并尽量不引入新数据库；必须复用 PostgreSQL。")
    constraints = extract_constraints(q)
    kinds = {c.kind for c in constraints}
    assert {"scale", "team", "cost", "deployment", "stack"} <= kinds
    scale = next(c for c in constraints if c.kind == "scale")
    assert scale.value == "50000000"
    team = next(c for c in constraints if c.kind == "team")
    assert team.value == "5"
    assert any(c.value == "no_new_infra" for c in constraints)
    assert any(c.value == "postgresql" for c in constraints)


def test_evaluate_recommend_when_one_object_satisfies_all():
    constraints = extract_constraints("500 万条向量、5 人团队、自托管、预算有限、不引入新数据库")
    evs = [
        _ev(1, "atlas", {"max_vectors": 10_000_000, "min_team": 2,
                         "deployment": ["self-hosted"], "cost_tier": 0,
                         "new_infra_required": True}),
        _ev(2, "pgvector", {"max_vectors": 10_000_000, "min_team": 2,
                            "deployment": ["self-hosted"], "cost_tier": 0,
                            "new_infra_required": False}),
    ]
    summary = evaluate_constraints(constraints, evs, candidate_objects=["atlas", "pgvector"])
    assert summary.verdict == "recommend"
    assert summary.recommended_objects == ["pgvector"]
    assert "atlas" in summary.excluded


def test_evaluate_no_solution_when_every_object_violates():
    constraints = extract_constraints("5000 万条向量、5 人团队、自托管、预算有限")
    evs = [
        _ev(1, "atlas", {"max_vectors": 10_000_000, "min_team": 2,
                         "deployment": ["self-hosted"], "cost_tier": 0}),
        _ev(2, "helios", {"max_vectors": 100_000_000, "min_team": 10,
                          "deployment": ["self-hosted"], "cost_tier": 2}),
    ]
    summary = evaluate_constraints(constraints, evs, candidate_objects=["atlas", "helios"])
    assert summary.verdict == "no_solution"
    assert summary.recommended_objects == []
    assert summary.gaps


def test_evaluate_insufficient_when_attributes_missing():
    constraints = extract_constraints("5000 万条向量、5 人团队")
    evs = [_ev(1, "atlas", {"max_vectors": 100_000_000}),
           _ev(2, "nebula", {})]
    summary = evaluate_constraints(constraints, evs, candidate_objects=["atlas", "nebula"])
    assert summary.verdict == "insufficient"
    assert "nebula" in summary.unknown_objects
    assert summary.recommended_objects == []


def test_candidate_objects_exclude_unplanned_kb_hits():
    constraints = extract_constraints("5000 万条向量、5 人团队、自托管、预算有限")
    evs = [
        _ev(1, "atlas", {"max_vectors": 10_000_000, "min_team": 2,
                         "deployment": ["self-hosted"], "cost_tier": 0}),
        _ev(2, "helios", {"max_vectors": 100_000_000, "min_team": 10,
                          "deployment": ["self-hosted"], "cost_tier": 2}),
        _ev(3, "nebula", {}),
    ]
    summary = evaluate_constraints(constraints, evs, candidate_objects=["atlas", "helios"])
    assert summary.verdict == "no_solution"
    assert "nebula" not in summary.checked_objects
    assert summary.recommended_objects == []