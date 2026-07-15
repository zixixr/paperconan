from __future__ import annotations

import gc
import weakref

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


def test_state_lease_accounting_identity_is_immutable():
    budget = StateBudget(4)
    lease = budget.try_reserve("array", 2)
    assert lease is not None

    for attribute, value in (
        ("_budget", StateBudget(10)),
        ("name", "other"),
        ("units", 4),
    ):
        with pytest.raises(AttributeError):
            setattr(lease, attribute, value)

    assert budget.live_units == 2
    assert budget.live_names == frozenset({"array"})
    lease.release()
    assert budget.live_units == 0


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


def test_bounded_collector_reentrant_builder_never_exceeds_cap():
    collector = BoundedFindingCollector(
        ("relations",), cap=1, severity_rank=RANK
    )
    assert collector.offer(
        "relations",
        "low",
        lambda: {"id": "seed", "severity": "low"},
    )
    observed_retained = []
    nested_results = []

    def build_replacement():
        observed_retained.append(collector.retained)
        nested_results.append(
            collector.offer(
                "relations",
                "high",
                lambda: {"id": "nested", "severity": "high"},
            )
        )
        observed_retained.append(collector.retained)
        return {"id": "replacement", "severity": "high"}

    assert collector.offer("relations", "high", build_replacement)

    assert nested_results == [False]
    assert observed_retained == [1, 1]
    assert collector.retained == 1
    assert collector.materialize() == {
        "relations": [{"id": "replacement", "severity": "high"}],
    }


def test_bounded_collector_replacement_is_transactional_if_builder_raises():
    collector = BoundedFindingCollector(
        ("relations",), cap=1, severity_rank=RANK
    )
    assert collector.offer(
        "relations",
        "low",
        lambda: {"id": "seed", "severity": "low"},
    )

    def fail_build():
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        collector.offer("relations", "high", fail_build)

    assert collector.retained == 1
    assert collector.evicted == 0
    assert collector.materialize() == {
        "relations": [{"id": "seed", "severity": "low"}],
    }
    assert collector.offer(
        "relations",
        "medium",
        lambda: {"id": "next", "severity": "medium"},
    )
    assert collector.evicted == 1
    assert collector.materialize() == {
        "relations": [{"id": "next", "severity": "medium"}],
    }


def test_bounded_collector_unknown_severity_is_worse_than_configured_ranks():
    calls = []
    collector = BoundedFindingCollector(
        ("relations",),
        cap=1,
        severity_rank={"high": 10, "low": 20},
    )
    assert collector.offer(
        "relations",
        "low",
        lambda: {"id": "known", "severity": "low"},
    )

    assert not collector.offer(
        "relations",
        "unknown",
        _builder(calls, id="unknown", severity="unknown"),
    )
    assert calls == []
    assert collector.materialize() == {
        "relations": [{"id": "known", "severity": "low"}],
    }


def test_atomic_batch_has_an_explicit_live_payload_budget():
    class Payload(dict):
        pass

    collector = BoundedFindingCollector(
        ("relations",), cap=3, severity_rank=RANK
    )
    references = []
    live_counts = []

    def tracked_builder(identifier):
        def build():
            payload = Payload(id=identifier)
            references.append(weakref.ref(payload))
            gc.collect()
            live_counts.append(
                sum(ref() is not None for ref in references)
            )
            return payload

        return build

    for index in range(3):
        assert collector.offer(
            "relations",
            "low",
            tracked_builder(f"old-{index}"),
        )

    live_counts.clear()
    assert collector.offer_batch(
        [
            *(
                (
                    "relations",
                    "medium",
                    tracked_builder(f"medium-{index}"),
                )
                for index in range(3)
            ),
            *(
                (
                    "relations",
                    "high",
                    tracked_builder(f"high-{index}"),
                )
                for index in range(3)
            ),
        ]
    ) == (True, True, True, True, True, True)

    gc.collect()
    assert collector.transaction_payload_limit == 3
    assert collector.max_live_payloads == 6
    assert max(live_counts) <= collector.max_live_payloads
    assert sum(ref() is not None for ref in references) == collector.retained


def test_atomic_batch_failure_restores_payloads_counters_and_depths():
    class BatchAbort(BaseException):
        pass

    class Payload(dict):
        pass

    collector = BoundedFindingCollector(
        ("relations",), cap=2, severity_rank=RANK
    )
    old_references = []
    staged_references = []

    def old_builder(identifier):
        def build():
            payload = Payload(id=identifier)
            old_references.append(weakref.ref(payload))
            return payload

        return build

    for index in range(2):
        assert collector.offer(
            "relations",
            "low",
            old_builder(f"old-{index}"),
        )

    def staged_builder():
        payload = Payload(id="staged")
        staged_references.append(weakref.ref(payload))
        return payload

    def fail_builder():
        payload = Payload(id="failed")
        staged_references.append(weakref.ref(payload))
        raise BatchAbort

    with pytest.raises(BatchAbort):
        collector.offer_batch([
            ("relations", "medium", staged_builder),
            ("relations", "high", fail_builder),
        ])

    gc.collect()
    assert collector.materialize() == {
        "relations": [
            {"id": "old-0"},
            {"id": "old-1"},
        ],
    }
    assert collector.offered == 2
    assert collector.evicted == 0
    assert collector.retained == 2
    assert collector.omitted == 0
    assert collector._building_depth == 0
    assert collector._batch_depth == 0
    assert sum(ref() is not None for ref in old_references) == 2
    assert all(ref() is None for ref in staged_references)
    assert collector._group_sequences == {"relations": 2}

    assert collector.offer(
        "relations",
        "medium",
        lambda: {"id": "next-0"},
    )
    assert collector.offer(
        "relations",
        "medium",
        lambda: {"id": "next-1"},
    )
    rejected_builds = []
    assert not collector.offer(
        "relations",
        "medium",
        lambda: rejected_builds.append("built") or {
            "id": "next-2"
        },
    )
    assert rejected_builds == []
    assert collector.materialize() == {
        "relations": [
            {"id": "next-0"},
            {"id": "next-1"},
        ],
    }
    assert collector.offered == 5
    assert collector.evicted == 2
    assert collector.retained == 2
    assert collector.omitted == 3


def test_reentrant_atomic_batch_is_rejected_without_building_payload():
    collector = BoundedFindingCollector(
        ("relations",), cap=1, severity_rank=RANK
    )
    assert collector.offer(
        "relations",
        "low",
        lambda: {"id": "seed"},
    )
    nested_results = []
    nested_builds = []

    def outer_builder():
        nested_results.append(
            collector.offer_batch([
                (
                    "relations",
                    "high",
                    lambda: nested_builds.append("built") or {
                        "id": "nested"
                    },
                ),
            ])
        )
        return {"id": "outer"}

    assert collector.offer_batch([
        ("relations", "high", outer_builder),
    ]) == (True,)

    assert nested_results == [(False,)]
    assert nested_builds == []
    assert collector.materialize() == {
        "relations": [{"id": "outer"}],
    }
    assert collector.offered == 3
    assert collector.evicted == 1
    assert collector.retained == 1
    assert collector.omitted == 2
    assert collector._building_depth == 0
    assert collector._batch_depth == 0
