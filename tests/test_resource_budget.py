from __future__ import annotations

import pytest

from paperconan._resources import (
    BoundedFindingCollector,
    StateBudget,
    state_units_for_nbytes,
)


def test_state_units_round_up_to_eight_byte_units():
    assert state_units_for_nbytes(0) == 0
    assert state_units_for_nbytes(1) == 1
    assert state_units_for_nbytes(8) == 1
    assert state_units_for_nbytes(9) == 2


def test_state_budget_rejects_before_allocation_and_reuses_released_units():
    budget = StateBudget(3)
    first = budget.try_reserve("first", 2)
    assert first is not None
    assert budget.live_units == 2
    assert budget.live_names == frozenset({"first"})
    assert budget.try_reserve("blocked", 2) is None
    assert budget.live_units == 2
    assert budget.required_peak_units == 4
    first.release()
    second = budget.try_reserve("second", 3)
    assert second is not None
    second.release()
    assert budget.live_units == 0
    assert budget.peak_units == 3


def test_state_lease_validates_actual_bytes_and_releases_once():
    budget = StateBudget(4)
    lease = budget.try_reserve("array", 2)
    assert lease is not None
    lease.validate_nbytes(8, 8)
    with pytest.raises(AssertionError, match="reserved"):
        lease.validate_nbytes(17)
    lease.release()
    with pytest.raises(AssertionError, match="released"):
        lease.release()


def test_state_budget_rejects_duplicate_live_name():
    budget = StateBudget(4)
    lease = budget.try_reserve("array", 1)
    assert lease is not None
    with pytest.raises(AssertionError, match="already reserved"):
        budget.try_reserve("array", 1)
    lease.release()


RANK = {"high": 0, "medium": 1, "low": 2}


def _builder(calls, **payload):
    def build():
        calls.append(payload["id"])
        return dict(payload)
    return build


def test_bounded_collector_matches_severity_then_stable_order():
    calls = []
    collector = BoundedFindingCollector(
        ("relations", "grim"),
        cap=3,
        severity_rank=RANK,
    )
    collector.offer(
        "relations", "low", _builder(calls, id="low-early", severity="low")
    )
    collector.offer(
        "grim", "medium", _builder(calls, id="medium", severity="medium")
    )
    collector.offer(
        "relations", "high", _builder(calls, id="high-early", severity="high")
    )
    collector.offer(
        "grim", "high", _builder(calls, id="high-late", severity="high")
    )
    collector.offer(
        "relations", "low", _builder(calls, id="low-late", severity="low")
    )

    assert collector.materialize() == {
        "relations": [
            {"id": "high-early", "severity": "high"},
        ],
        "grim": [
            {"id": "medium", "severity": "medium"},
            {"id": "high-late", "severity": "high"},
        ],
    }
    assert collector.offered == 5
    assert collector.retained == 3
    assert collector.evicted == 1
    assert collector.omitted == 2
    assert "low-late" not in calls


def test_bounded_collector_unlimited_and_zero_cap_semantics():
    unlimited = BoundedFindingCollector(
        ("relations",), cap=None, severity_rank=RANK
    )
    zero = BoundedFindingCollector(
        ("relations",), cap=0, severity_rank=RANK
    )
    unlimited_calls = []
    zero_calls = []
    for index in range(4):
        unlimited.offer(
            "relations",
            "low",
            _builder(unlimited_calls, id=index, severity="low"),
        )
        zero.offer(
            "relations",
            "high",
            _builder(zero_calls, id=index, severity="high"),
        )

    assert [item["id"] for item in unlimited.materialize()["relations"]] == [
        0, 1, 2, 3
    ]
    assert unlimited.omitted == 0
    assert zero.materialize() == {"relations": []}
    assert zero.omitted == 4
    assert zero_calls == []
