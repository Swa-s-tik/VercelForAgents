"""Pure unit tests for rollout._split_primaries (no Postgres) - the proportional canary rescale that
preserves every existing primary instead of collapsing to the heaviest arm."""
from agentctl.rollback.rollout import _split_primaries


def _sum(rules):
    return sum(r["weight"] for r in rules)


def test_single_primary_fills_budget():
    assert _split_primaries([{"deployment_id": 1, "weight": 10000}], 8000) == \
        [{"deployment_id": 1, "weight": 8000}]


def test_two_primaries_keep_relative_split_and_sum_exactly():
    rules = _split_primaries(
        [{"deployment_id": 1, "weight": 6000}, {"deployment_id": 2, "weight": 3000}], 8000)
    assert _sum(rules) == 8000                              # exact budget
    assert {r["deployment_id"] for r in rules} == {1, 2}    # nobody dropped
    w = {r["deployment_id"]: r["weight"] for r in rules}
    assert w[1] > w[2]                                      # 2:1 split preserved


def test_rounding_drift_absorbed_so_sum_is_exact():
    rules = _split_primaries([{"deployment_id": i, "weight": 1000} for i in (1, 2, 3)], 9001)
    assert _sum(rules) == 9001                              # drift folded onto the largest arm
    assert len(rules) == 3                                  # all three preserved


def test_no_arm_dropped_for_many_primaries():
    primaries = [{"deployment_id": i, "weight": 100 * i} for i in range(1, 6)]
    rules = _split_primaries(primaries, 9500)
    assert _sum(rules) == 9500
    assert {r["deployment_id"] for r in rules} == {1, 2, 3, 4, 5}
    assert all(r["weight"] >= 0 for r in rules)
