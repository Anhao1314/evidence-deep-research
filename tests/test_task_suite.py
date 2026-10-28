"""真实任务评测集 schema 与分布：保证 20 条用例可读、可校验、离线子集稳定。"""

from collections import Counter

from eval.task_suite import ASPECTS, load_suite, suite_sha256


def test_suite_has_expected_distribution():
    suite = load_suite()
    assert suite.suite_id == "research-tasks-v1"
    assert len(suite.cases) == 20
    counts = Counter(c.category for c in suite.cases)
    assert counts == {
        "multi_object": 6,
        "single_object": 6,
        "insufficient_info": 4,
        "replan_gap": 4,
    }
    assert len({c.case_id for c in suite.cases}) == 20
    assert suite_sha256()


def test_offline_subset_is_ten_and_never_web_only():
    suite = load_suite()
    offline = suite.offline_cases()
    assert len(offline) == 10
    assert all(c.expected_route != "web" for c in offline)
    online_only = [c for c in suite.cases if not c.offline_eligible]
    assert len(online_only) == 10


def test_every_case_is_human_readable_and_checkable():
    suite = load_suite()
    for case in suite.cases:
        assert case.title and case.why_this_case
        assert case.research_dimensions, case.case_id
        assert all(a in ASPECTS for a in case.dimension_aspects), case.case_id
        assert case.expected_route in {"kb", "web", "both", "any"}
        assert case.automated_checks.max_steps <= 32
        assert 1 <= case.automated_checks.max_plan_rounds <= 3
        if case.category in {"multi_object", "single_object"}:
            assert case.expected_objects, case.case_id
            assert case.dimension_aspects, case.case_id
        if case.category == "insufficient_info":
            assert case.automated_checks.must_state_gaps, case.case_id


def test_replan_gap_cases_exist_and_are_online_only():
    suite = load_suite()
    gap_cases = [c for c in suite.cases if c.category == "replan_gap"]
    assert len(gap_cases) == 4
    assert all(not c.offline_eligible for c in gap_cases)


def test_task_case_to_eval_case_mapping():
    suite = load_suite()
    case = suite.by_id("vdb-qdrant-vs-milvus-50m")
    assert case is not None
    ev = case.to_eval_case()
    assert ev.expect.routes == ["kb", "web"]
    assert ev.expect.min_evidences == case.automated_checks.min_evidences
    assert ev.expect.expected_objects == case.dimension_objects
    assert ev.expect.require_citations is True