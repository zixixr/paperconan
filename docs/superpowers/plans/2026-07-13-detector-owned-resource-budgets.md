# Detector-Owned Resource Budgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move proportional state, work, finding, and summary-capacity checks
into the code paths that consume those resources, while preserving normal
paperconan output exactly.

**Architecture:** Add a dependency-free internal resource module with explicit
state leases and a severity-stable bounded finding collector. Dense detectors,
cross-sheet pair helpers, axis classification, and cross-sheet summary
construction consume those primitives at their allocation/work points;
`_audit.py` remains the compatible public implementation surface and only
aggregates structured coverage.

**Tech Stack:** Python 3.10-3.14, NumPy, SciPy, pytest, uv, setuptools.

## Global Constraints

- Work only in `/Users/xiaotong/Dev/paperconan/.worktrees/project-hardening`.
- Do not access the main checkout, `recheck/`, `batches/`, or private data.
- Use neutral statistical-signal and data-inconsistency language everywhere.
- Preserve detector thresholds, finding kinds, normal-input finding substance,
  severity, order, evidence, profile behavior, and serialized output.
- Preserve direct detector calls without a budget or collector as unlimited
  list-returning calls.
- Preserve archived `scan.json` and legacy/current verdict compatibility.
- Preserve deterministic output for identical input.
- Keep Python 3.10-3.14 support.
- Apply every resource limit before the rejected proportional state or work
  grows.
- Report exact omissions where known and explicit lower bounds otherwise.
- Do not add a new public environment variable.
- Use strict RED/GREEN TDD for every production change.
- Do not commit `.superpowers/`, generated archives, caches, or source data.

## Pre-Execution Gate

This plan, its approved specification, and their two sorted `MANIFEST.in`
entries are one documentation commit. Before Task 1, verify that commit is
present and restore the packaging baseline:

```bash
git status --short
.venv/bin/python -m pytest -q \
  tests/test_packaging.py::test_sdist_allowlist_matches_tracked_public_files
```

Expected: tracked status is clean and the allowlist test passes. Do not begin
Task 1 from an untracked copy of this plan.

## File Structure

**Create**

- `src/paperconan/_resources.py`: dependency-free state leases and bounded
  finding collection.
- `tests/test_resource_budget.py`: direct invariants for the new resource
  primitives.

**Modify**

- `src/paperconan/_audit.py`: detector integration, detector-owned dense
  sessions, exact pair/axis accounting, summary reservation transaction, and
  coverage aggregation.
- `tests/test_findings_cap.py`: bounded block collector oracle and end-to-end
  finding-state tests.
- `tests/test_resource_lifetime.py`: dense allocation ownership and
  fingerprint preconstruction guards.
- `tests/test_relations_tolerance.py`: relation-output parity under resource
  sessions.
- `tests/test_detector_coverage.py`: structured dense/summary limitation
  integration.
- `tests/test_collisions.py`: feasible-family and axis-pass accounting.
- `tests/test_cross_sheet_summaries.py`: transactional summary reservations and
  rollback.
- `tests/test_module_boundaries.py`: removal of superseded orchestration
  estimators.
- `MANIFEST.in`: include the new internal resource module in the exact sdist
  allowlist.
- `tests/test_packaging.py`: assert source/archive closure for the new module and
  verify built sdist/wheel members and source bytes.
- `docs/cli.md`: detector-owned work/state units and stopping behavior.
- `skills/paperconan/references/output-schema.md`: additive coverage fields and
  lower-bound semantics.
- `tests/test_skill_docs.py`: governance assertions for the revised public
  documentation.
- `.superpowers/sdd/final-review-fix-wave-4-report.md`: ignored local execution
  record; append RED/GREEN/review evidence but never commit it.
- `.superpowers/sdd/progress.md`: ignored local progress record; update after
  implementation and reviews but never commit it.

---

### Task 1: Add Resource Leases And A Bounded Finding Collector

**Files:**

- Create: `src/paperconan/_resources.py`
- Create: `tests/test_resource_budget.py`
- Modify: `MANIFEST.in`
- Modify: `tests/test_packaging.py`

**Interfaces:**

- Produces:
  - `state_units_for_nbytes(nbytes: int) -> int`
  - `StateBudget(limit_units: int | None)`
  - `StateBudget.try_reserve(name: str, units: int) -> StateLease | None`
  - `StateBudget.live_names -> frozenset[str]`
  - `StateBudget.required_peak_units`
  - `StateLease.validate_nbytes(*sizes: int) -> None`
  - `StateLease.release() -> None`
  - `BoundedFindingCollector(group_names, cap, severity_rank)`
  - `BoundedFindingCollector.offer(group, severity, builder) -> bool`
  - `BoundedFindingCollector.materialize() -> dict[str, list[dict]]`
  - `BoundedFindingCollector.omitted -> int`
- Consumes: no paperconan detector modules.

- [ ] **Step 1: Write failing state-budget tests**

Create `tests/test_resource_budget.py` with:

```python
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
```

Append to `tests/test_packaging.py`:

```python
def test_sdist_includes_detector_resource_module():
    assert "src/paperconan/_resources.py" in _sdist_allowlist()
```

- [ ] **Step 2: Write failing bounded-collector tests**

Append to `tests/test_resource_budget.py`:

```python
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
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_resource_budget.py

.venv/bin/python -m pytest -q \
  tests/test_packaging.py::test_sdist_allowlist_matches_tracked_public_files

.venv/bin/python -m pytest -q \
  tests/test_packaging.py::test_sdist_includes_detector_resource_module
```

Expected: the first command fails during collection with
`ModuleNotFoundError: No module named 'paperconan._resources'`; the second
command passes because this plan and its specification were added to
`MANIFEST.in` in the preceding documentation commit; the third command fails
because the new module does not exist yet. Keeping these as separate pytest
invocations ensures the collection failure cannot prevent either packaging
assertion from executing.

- [ ] **Step 4: Implement the resource primitives**

Create `src/paperconan/_resources.py`:

```python
from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


def state_units_for_nbytes(nbytes: int) -> int:
    return (max(0, int(nbytes)) + 7) // 8


class StateBudget:
    def __init__(self, limit_units: int | None):
        self.limit_units = (
            None if limit_units is None else max(0, int(limit_units))
        )
        self.live_units = 0
        self.peak_units = 0
        self.required_peak_units = 0
        self.seen_names: set[str] = set()
        self._live: dict[str, StateLease] = {}

    @property
    def live_names(self) -> frozenset[str]:
        return frozenset(self._live)

    def try_reserve(self, name: str, units: int) -> StateLease | None:
        if name in self._live:
            raise AssertionError(f"state name already reserved: {name}")
        units = max(0, int(units))
        required_live = self.live_units + units
        self.required_peak_units = max(
            self.required_peak_units, required_live
        )
        if (
            self.limit_units is not None
            and required_live > self.limit_units
        ):
            return None
        lease = StateLease(self, name, units)
        self._live[name] = lease
        self.live_units += units
        self.peak_units = max(self.peak_units, self.live_units)
        self.seen_names.add(name)
        return lease

    def _release(self, lease: StateLease) -> None:
        current = self._live.get(lease.name)
        if current is not lease:
            raise AssertionError(f"state lease already released: {lease.name}")
        del self._live[lease.name]
        self.live_units -= lease.units


@dataclass
class StateLease:
    _budget: StateBudget
    name: str
    units: int
    _released: bool = False

    @property
    def released(self) -> bool:
        return self._released

    def validate_nbytes(self, *sizes: int) -> None:
        actual = sum(state_units_for_nbytes(size) for size in sizes)
        if actual > self.units:
            raise AssertionError(
                f"{self.name} used {actual} units but reserved {self.units}"
            )

    def release(self) -> None:
        if self._released:
            raise AssertionError(f"state lease already released: {self.name}")
        self._budget._release(self)
        self._released = True

    def __enter__(self) -> StateLease:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


@dataclass(frozen=True)
class _FindingEntry:
    group: str
    group_order: int
    group_sequence: int
    rank: int
    payload: dict[str, Any]


class BoundedFindingCollector:
    def __init__(
        self,
        group_names: Iterable[str],
        *,
        cap: int | None,
        severity_rank: Mapping[str, int],
    ):
        self.group_names = tuple(group_names)
        self.group_order = {
            name: index for index, name in enumerate(self.group_names)
        }
        self.cap = None if cap is None else max(0, int(cap))
        self.severity_rank = dict(severity_rank)
        self.offered = 0
        self.evicted = 0
        self._group_sequences = {
            name: 0 for name in self.group_names
        }
        self._next_token = 0
        self._entries: dict[int, _FindingEntry] = {}
        self._worst_heap: list[tuple[int, int, int, int]] = []

    @property
    def retained(self) -> int:
        return len(self._entries)

    @property
    def omitted(self) -> int:
        return self.offered - self.retained

    def offer(
        self,
        group: str,
        severity: str,
        builder: Callable[[], dict[str, Any]],
    ) -> bool:
        if group not in self.group_names:
            raise KeyError(group)
        self.offered += 1
        group_order = self.group_order[group]
        group_sequence = self._group_sequences[group]
        self._group_sequences[group] += 1
        rank = self.severity_rank.get(str(severity).lower(), 3)
        if self.cap == 0:
            return False

        if self.cap is not None and len(self._entries) >= self.cap:
            worst_rank = -self._worst_heap[0][0]
            worst_group = -self._worst_heap[0][1]
            worst_sequence = -self._worst_heap[0][2]
            if (
                rank,
                group_order,
                group_sequence,
            ) >= (
                worst_rank,
                worst_group,
                worst_sequence,
            ):
                return False
            _neg_rank, _neg_group, _neg_sequence, token = heapq.heappop(
                self._worst_heap
            )
            del self._entries[token]
            self.evicted += 1

        token = self._next_token
        self._next_token += 1
        payload = builder()
        self._entries[token] = _FindingEntry(
            group=group,
            group_order=group_order,
            group_sequence=group_sequence,
            rank=rank,
            payload=payload,
        )
        heapq.heappush(
            self._worst_heap,
            (-rank, -group_order, -group_sequence, token),
        )
        return True

    def materialize(self) -> dict[str, list[dict[str, Any]]]:
        groups = {name: [] for name in self.group_names}
        for entry in sorted(
            self._entries.values(),
            key=lambda item: (
                item.group_order,
                item.group_sequence,
            ),
        ):
            groups[entry.group].append(entry.payload)
        return groups
```

Add these exact lines in the corresponding sorted sections of `MANIFEST.in`:

```text
include src/paperconan/_resources.py
include tests/test_resource_budget.py
```

- [ ] **Step 5: Stage Task 1 so the tracked-file allowlist sees new files**

`test_sdist_allowlist_matches_tracked_public_files` reads `git ls-files`, so
stage the new module and test before running it:

```bash
git add src/paperconan/_resources.py \
  tests/test_resource_budget.py tests/test_packaging.py MANIFEST.in
```

- [ ] **Step 6: Run direct primitive tests under strict warnings**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_resource_budget.py \
  tests/test_packaging.py::test_sdist_includes_detector_resource_module \
  tests/test_packaging.py::test_sdist_allowlist_matches_tracked_public_files
```

Expected: `8 passed`.

- [ ] **Step 7: Commit Task 1**

```bash
git commit -m "feat: add detector resource primitives"
```

---

### Task 2: Bound Block Findings During Detection

**Files:**

- Modify: `src/paperconan/_audit.py:1302-2615`
- Modify: `src/paperconan/_audit.py:5300-5324`
- Modify: `src/paperconan/_audit.py:5799-6095`
- Modify: `tests/test_findings_cap.py`
- Modify: `tests/test_detector_coverage.py`

**Interfaces:**

- Consumes:
  - `BoundedFindingCollector` from Task 1.
  - Existing `BLOCK_FINDING_GROUPS` and `_SEVERITY_RANK`.
- Produces:
  - `_finding_emitter(group, sink) -> (list[dict], callable)`
  - `_BoundedRankedFindingBuffer(cap)` for detector-local ranked caps.
  - Optional keyword-only `_finding_sink=None` on every block detector.
  - Exact block-cap omissions without a complete pre-cap finding list.

- [ ] **Step 1: Add a collector-vs-oracle regression**

Append to `tests/test_findings_cap.py`:

```python
from paperconan._resources import BoundedFindingCollector


def test_bounded_collector_matches_post_materialization_oracle():
    emitted = [
        ("relations", {"severity": "low", "i": 0}),
        ("relations", {"severity": "high", "i": 1}),
        ("grim", {"severity": "medium", "i": 2}),
        ("relations", {"severity": "high", "i": 3}),
        ("grim", {"severity": "low", "i": 4}),
        ("grim", {"severity": "medium", "i": 5}),
    ]
    oracle = {name: [] for name in BLOCK_FINDING_GROUPS}
    collector = BoundedFindingCollector(
        BLOCK_FINDING_GROUPS,
        cap=3,
        severity_rank=A._SEVERITY_RANK,
    )
    for group, finding in emitted:
        oracle[group].append(dict(finding))
        collector.offer(
            group,
            finding["severity"],
            lambda finding=finding: dict(finding),
        )

    omitted = _cap_block_findings(oracle, 3)
    assert collector.materialize() == oracle
    assert collector.omitted == omitted
```

- [ ] **Step 2: Add an end-to-end peak-retention regression**

Append to `tests/test_findings_cap.py`:

```python
def test_scan_path_never_retains_more_than_block_cap_during_detection(
    monkeypatch,
):
    offered = []

    def flood_relations(
        *_args, _finding_sink=None, **_kwargs
    ):
        for index in range(10_000):
            offered.append(index)
            _finding_sink.offer(
                "relations",
                "low",
                lambda index=index: {
                    "kind": "marker",
                    "severity": "low",
                    "rule": f"marker {index}",
                    "index": index,
                },
            )
            assert _finding_sink.retained <= 7
        return []

    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 7)
    monkeypatch.setattr(A, "detect_relations", flood_relations)
    for name in [
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ]:
        monkeypatch.setattr(
            A,
            name,
            lambda *_args, **_kwargs: [],
        )
    monkeypatch.setattr(
        A,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )

    state = A.ScanBudgetState(
        coverage=A.ScanCoverage(files_discovered=1),
        recurring_index=A.RecurringRowIndex(budget=0),
        profile="review",
        evidence=False,
    )
    blocks = [(1, 7, 0, 2)]
    sheet = A.Sheet.from_rows(
        [["a", "b"]] + [[row + 0.125, row + 1.375] for row in range(6)]
    )

    result = A._analyze_numeric_blocks(
        sheet,
        file_name="dense.csv",
        sheet_name="dense",
        blocks=blocks,
        state=state,
    )

    assert offered == list(range(10_000))
    assert len(result[0]["relations"]) == 7
    assert result[0]["findings_omitted"] == 9_993


def test_ranked_buffer_keeps_late_best_and_stable_ties_lazily():
    calls = []
    buffer = A._BoundedRankedFindingBuffer(cap=2)

    def builder(name):
        def build():
            calls.append(name)
            return {"id": name, "severity": "high"}
        return build

    buffer.offer((1, -0.80), "medium", builder("early-medium"))
    buffer.offer((0, -0.90), "high", builder("early-high"))
    buffer.offer((0, -0.90), "high", builder("late-tie"))
    buffer.offer((0, -0.95), "high", builder("late-best"))
    findings, emit = A._finding_emitter("row_pairs", None)

    omitted = buffer.drain(emit)

    assert findings == [
        {"id": "late-best", "severity": "high"},
        {"id": "early-high", "severity": "high"},
    ]
    assert omitted == 2
    assert calls == ["late-best", "early-high"]


def test_row_pair_sink_preserves_ranked_local_cap(monkeypatch):
    header = [f"c{column}" for column in range(12)]
    base = [
        100 + column + (column + 1) / 100
        for column in range(12)
    ]
    rows = [
        header,
        base,
        [value + 10 for value in base],
        [value + 20 for value in base],
    ]
    sheet = A.Sheet.from_rows(rows)
    monkeypatch.setattr(A, "_ROW_PAIR_MAX_FINDINGS_PER_BLOCK", 1)
    baseline, baseline_meta = A.detect_row_pair_digit_coupling(
        sheet, 1, 4, 0, 12, header, with_coverage=True
    )
    collector = BoundedFindingCollector(
        BLOCK_FINDING_GROUPS,
        cap=None,
        severity_rank=A._SEVERITY_RANK,
    )

    local, sink_meta = A.detect_row_pair_digit_coupling(
        sheet,
        1,
        4,
        0,
        12,
        header,
        with_coverage=True,
        _finding_sink=collector,
    )

    assert local == []
    assert collector.materialize()["row_pairs"] == baseline
    assert sink_meta == baseline_meta == {"findings_omitted": 2}
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_findings_cap.py::test_bounded_collector_matches_post_materialization_oracle \
  tests/test_findings_cap.py::test_scan_path_never_retains_more_than_block_cap_during_detection \
  tests/test_findings_cap.py::test_ranked_buffer_keeps_late_best_and_stable_ties_lazily \
  tests/test_findings_cap.py::test_row_pair_sink_preserves_ranked_local_cap
```

Expected: the oracle test passes from Task 1; the other tests fail because the
ranked local buffer does not exist, `_analyze_numeric_blocks()` does not pass
`_finding_sink`, and row-pair findings still materialize before their local
cap.

- [ ] **Step 4: Add one emission adapter**

In `src/paperconan/_audit.py`, import the collector:

```python
from ._resources import BoundedFindingCollector
```

Add next to the detector helpers:

```python
def _finding_emitter(group, sink):
    local = []

    def emit(severity, builder):
        if sink is None:
            local.append(builder())
            return True
        return sink.offer(group, severity, builder)

    return local, emit


class _BoundedRankedFindingBuffer:
    def __init__(self, cap):
        self.cap = max(0, int(cap))
        self.offered = 0
        self._sequence = 0
        self._items = []

    def offer(self, sort_key, severity, builder):
        key = (*tuple(sort_key), self._sequence)
        self._sequence += 1
        self.offered += 1
        item = (key, severity, builder)
        if self.cap == 0:
            return False
        if len(self._items) < self.cap:
            self._items.append(item)
            return True
        worst_index = max(
            range(len(self._items)),
            key=lambda index: self._items[index][0],
        )
        if key >= self._items[worst_index][0]:
            return False
        self._items[worst_index] = item
        return True

    def drain(self, emit):
        omitted = self.offered - len(self._items)
        for _key, severity, builder in sorted(
            self._items, key=lambda item: item[0]
        ):
            emit(severity, builder)
        self._items.clear()
        return omitted
```

- [ ] **Step 5: Route every block detector through the adapter**

Add keyword-only `_finding_sink=None` to:

```python
detect_relations
detect_arithmetic_progression
detect_equal_pairs
detect_row_pair_digit_coupling
detect_within_column_patterns
detect_dispersed_repeats
detect_identical_after_rounding
detect_grim_grimmer
```

At each function entry, replace the local list with the correct adapter:

```python
findings, emit = _finding_emitter("relations", _finding_sink)
```

Use these exact group names:

```python
{
    "detect_relations": "relations",
    "detect_arithmetic_progression": "progressions",
    "detect_equal_pairs": "equal_pairs",
    "detect_row_pair_digit_coupling": "row_pairs",
    "detect_within_column_patterns": "within_col",
    "detect_dispersed_repeats": "within_col",
    "detect_identical_after_rounding": "identical_after_rounding",
    "detect_grim_grimmer": "grim",
}
```

Except for the ranked row-pair path described below, replace every
`findings.append` and `column_findings.append` call with a lazy offer that
preserves the existing dictionary byte-for-byte:

```python
emit(
    "high",
    lambda: dict(
        kind="identical_column",
        col_a=header[ci - c0],
        col_b=header[cj - c0],
        col_a_idx=ci,
        col_b_idx=cj,
        n=pair_stats.n,
        severity="high",
        col_a_sample=_sample_exact(pair_stats.sample_a),
        col_b_sample=_sample_exact(pair_stats.sample_b),
        rule=f"col[{cj}] == col[{ci}]",
    ),
)
```

Capture loop-mutated values in lambda defaults whenever the builder refers to
a mutable loop variable:

```python
emit(
    severity,
    lambda finding=finding: dict(finding),
)
```

Do not construct `examples`, `value_sample`, `unique_diffs`, or evidence-like
payload lists outside the builder when they are only needed by the finding.
The row-pair path may snapshot its already-computed fixed-size examples because
its local ranking key must be known before shared emission. In
`detect_within_column_patterns()`, remove the per-column
`column_findings` list and the final `findings.extend` call; call the shared
`emit` function directly from each of the four column rules.

For `detect_row_pair_digit_coupling()`, do not emit directly. Its existing
contract sorts qualifying pairs by quality before applying
`_ROW_PAIR_MAX_FINDINGS_PER_BLOCK`. Create:

```python
findings, emit = _finding_emitter("row_pairs", _finding_sink)
ranked = _BoundedRankedFindingBuffer(
    _ROW_PAIR_MAX_FINDINGS_PER_BLOCK
)
```

Add this factory next to the row-pair detector:

```python
def _row_pair_finding_builder(
    *,
    label_a,
    label_b,
    ra,
    rb,
    n,
    changed,
    same_decimal1,
    frac_decimal1,
    same_ones,
    same_ones_decimal1,
    frac_ones_decimal1,
    coarse_10_diff,
    frac_coarse_10,
    top_diffs,
    examples,
    severity,
):
    top_diffs = tuple(top_diffs)
    examples = tuple(dict(item) for item in examples)

    def build():
        return {
            "kind": "row_pair_digit_coupling",
            "row_a": label_a,
            "row_b": label_b,
            "row_a_idx": ra,
            "row_b_idx": rb,
            "n": n,
            "changed": changed,
            "same_decimal1": same_decimal1,
            "same_decimal1_frac": frac_decimal1,
            "same_ones": same_ones,
            "same_ones_decimal1": same_ones_decimal1,
            "same_ones_decimal1_frac": frac_ones_decimal1,
            "coarse_10_diff": coarse_10_diff,
            "coarse_10_diff_frac": frac_coarse_10,
            "top_diffs": [
                {"diff": float(diff), "count": int(count)}
                for diff, count in top_diffs
            ],
            "examples": [dict(item) for item in examples],
            "example_cells": (
                [(ra + 1, item["col"]) for item in examples[:4]]
                + [(rb + 1, item["col"]) for item in examples[:4]]
            ),
            "severity": severity,
            "rule": (
                f"rows {ra + 1} and {rb + 1}: first decimal digit "
                f"matches {same_decimal1}/{n}; ones+decimal matches "
                f"{same_ones_decimal1}/{n}; coarse 10-step "
                f"differences {coarse_10_diff}/{n}"
            ),
        }

    return build
```

At each qualifying pair, offer the existing sort key and a lazy builder:

```python
ranked.offer(
    (
        0 if severity == "high" else 1,
        -frac_decimal1,
        -frac_ones_decimal1,
        -frac_coarse_10,
        -n,
    ),
    severity,
    _row_pair_finding_builder(
        label_a=label_a,
        label_b=label_b,
        ra=ra,
        rb=rb,
        n=n,
        changed=changed,
        same_decimal1=same_decimal1,
        frac_decimal1=frac_decimal1,
        same_ones=same_ones,
        same_ones_decimal1=same_ones_decimal1,
        frac_ones_decimal1=frac_ones_decimal1,
        coarse_10_diff=coarse_10_diff,
        frac_coarse_10=frac_coarse_10,
        top_diffs=top_diffs,
        examples=examples,
        severity=severity,
    ),
)
```

After all row pairs:

```python
row_pair_omitted = ranked.drain(emit)
if with_coverage:
    return findings, {"findings_omitted": row_pair_omitted}
return findings
```

This retains at most 25 row-pair builders, preserves the original stable
quality order even when the best candidate appears late, and keeps local
omissions separate from the shared block-cap omissions.

- [ ] **Step 6: Create and finalize the collector in block orchestration**

In `_analyze_numeric_blocks()`, create the collector immediately after the
block header:

```python
block_cap = (
    _MAX_FINDINGS_PER_BLOCK
    if _MAX_FINDINGS_PER_BLOCK > 0
    else None
)
collector = BoundedFindingCollector(
    BLOCK_FINDING_GROUPS,
    cap=block_cap,
    severity_rank=_SEVERITY_RANK,
)
```

Pass `_finding_sink=collector` to every detector call. Replace the manually
assembled `groups` dictionary and `_cap_block_findings()` call with:

```python
groups = collector.materialize()
block_cap_omitted = collector.omitted
```

Keep row-pair local omission accounting separate, then set:

```python
report_block["findings_omitted"] = (
    row_pair_omitted + block_cap_omitted
)
```

The existing `_cap_block_findings()` helper remains unchanged for compatibility
and oracle tests.

- [ ] **Step 7: Run focused cap and coverage tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_resource_budget.py \
  tests/test_findings_cap.py \
  tests/test_detector_coverage.py -k \
  'finding or cap or bounded_collector or row_pair'
```

Expected: all selected tests pass.

- [ ] **Step 8: Run detector-output compatibility tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  tests/test_relations_flood.py \
  tests/test_progression_reuse.py \
  tests/test_within_col_prefilter.py \
  tests/test_grim.py
```

Expected: all tests pass with unchanged direct detector output.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/paperconan/_audit.py \
  tests/test_findings_cap.py tests/test_detector_coverage.py
git commit -m "fix: bound block findings during detection"
```

---

### Task 3: Move Dense State And Work Ownership Into Detectors

**Files:**

- Modify: `src/paperconan/_audit.py:1302-2385`
- Modify: `src/paperconan/_audit.py:5721-6052`
- Modify: `tests/test_resource_lifetime.py`
- Modify: `tests/test_relations_tolerance.py`
- Modify: `tests/test_detector_coverage.py`
- Modify: `tests/test_module_boundaries.py`

**Interfaces:**

- Consumes:
  - `StateBudget`, `StateLease`, and `state_units_for_nbytes` from Task 1.
  - `_finding_sink` detector integration from Task 2.
- Produces:
  - `_DenseFamilyResources`
  - `_DenseFamilyResult`
  - `_DenseCandidate`
  - `_DenseCandidateRejected`
  - `_DenseFamilyResources.begin(...)`
  - `_DenseFamilyResources.start_candidate(source_visits, emit)`
  - `_DenseFamilyResources.start_allocated_candidate(name, units,
    source_visits, emit, *, initial_reservations=()) -> tuple[
    _DenseCandidate | None, StateLease | None, tuple[StateLease, ...]]`
  - `_DenseCandidate.reserve(name, units) -> StateLease`
  - `_DenseCandidate.allocate(name, units, factory) -> tuple[T, StateLease]`
  - `_DenseCandidate.materialize(lease, factory, *,
    release_after=()) -> T`
  - `_DenseCandidate.release(lease) -> None`
  - `_DenseCandidate.offer(severity, builder) -> None`
  - `_DenseCandidate.rejected -> bool` (read-only)
  - Optional keyword-only `_resources=None` on dense detector functions.
  - Detector-owned `dense_block_detector_limit` metadata.
- Removes:
  - `_dense_detector_requirements`
  - `_dense_detector_admission`

- [ ] **Step 1: Add relation peak-state regressions**

Append to `tests/test_resource_lifetime.py`:

```python
RELATION_BRANCH_CASES = [
    (
        "offset",
        [[i + 0.125, i + 0.375] for i in range(40)],
        "relation_close_workspace",
    ),
    (
        "ratio",
        [[i + 0.125, 2 * (i + 0.125)] for i in range(40)],
        "ratio",
    ),
    (
        "sum",
        [[i + 0.125, 100 - (i + 0.125)] for i in range(40)],
        "sum_compare_workspace",
    ),
    (
        "linear",
        [[i + 0.125, 3 * (i + 0.125) + 7] for i in range(40)],
        "linear_fit_workspace",
    ),
    (
        "fractional-shift",
        [[
            i + 0.12345,
            i + 0.12345 + (10 if i % 2 else 20),
        ] for i in range(40)],
        "high_precision_unique_workspace",
    ),
    (
        "discrete-difference",
        [[
            i + 0.2,
            i + 0.2 + (0.1111 if i % 2 else 0.2222),
        ] for i in range(40)],
        "diff_unique_workspace",
    ),
]


@pytest.mark.parametrize(
    "rows,branch_state",
    [
        (rows, branch_state)
        for _case_id, rows, branch_state in RELATION_BRANCH_CASES
    ],
    ids=[case_id for case_id, _rows, _state in RELATION_BRANCH_CASES],
)
def test_relation_allocations_are_reserved_and_released(
    rows, branch_state, monkeypatch
):
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    baseline = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    observed_numpy_calls = _guard_numpy_workspaces(
        monkeypatch,
        resources,
        WORKSPACE_GUARDS["relations"],
    )
    for owner, function_name, required_variants in (
        (
            audit,
            "relation_close",
            (
                {"relation_close_workspace"},
                {"sum_compare_workspace"},
                {"fitted_relation_workspace"},
            ),
        ),
        (
            audit,
            "integer_shift_close",
            ({"integer_shift_workspace", "diff_is_int"},),
        ),
        (
            audit.stats,
            "linregress",
            ({"linear_fit_workspace"},),
        ),
    ):
        _guard_callable_workspace(
            monkeypatch,
            owner,
            function_name,
            resources,
            required_variants,
        )

    instrumented = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.peak_state_units > 0
    assert resources.state.live_units == 0
    assert {
        "mask",
        "mask_rhs_workspace",
        "filtered_values",
        "diff",
        branch_state,
    } <= resources.state.seen_names
    assert {
        ("isnan", "mask"),
        ("isnan", "mask_rhs"),
    } <= observed_numpy_calls
    if branch_state == "high_precision_unique_workspace":
        assert ("unique", "high_precision") in observed_numpy_calls


def test_relation_branch_inventory_reserves_every_declared_state(
    monkeypatch
):
    seen_names = set()
    allocation_names = set()
    original_allocate = audit._DenseCandidate.allocate

    def tracked_allocate(self, name, units, factory):
        allocation_names.add(name)
        return original_allocate(self, name, units, factory)

    monkeypatch.setattr(
        audit._DenseCandidate,
        "allocate",
        tracked_allocate,
    )

    for _case_id, rows, _branch_state in RELATION_BRANCH_CASES:
        sheet = Sheet.from_rows([["left", "right"], *rows])
        resources = audit._DenseFamilyResources(
            family="relations",
            max_rows=100,
            work_limit=100_000,
            state_limit=100_000,
        )
        audit.detect_relations(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )
        seen_names.update(resources.state.seen_names)
        assert resources.state.live_units == 0

    assert EXPECTED_DENSE_STATES["relations"] <= seen_names
    assert RELATION_ALLOCATED_STATES <= allocation_names


@pytest.mark.parametrize(
    "rows,expected_kinds",
    [
        (
            [[1.1, 7.3], [2.2, 4.8], [3.3, 9.1]],
            [],
        ),
        (
            [[i + 0.125, i + 0.125] for i in range(5)],
            ["identical_column"],
        ),
        (
            [[i, i + 5] for i in range(5)],
            ["constant_offset"],
        ),
        (
            [[10**400 + i, 10**400 + i * i] for i in range(5)],
            [],
        ),
        (
            [
                [1.1, 9.3],
                [2.4, 1.7],
                [3.9, 7.1],
                [5.8, 4.4],
                [8.2, 12.6],
                [11.7, 2.2],
            ],
            [],
        ),
        (
            [[i + 0.125, 2 * (i + 0.125)] for i in range(8)],
            ["constant_ratio"],
        ),
    ],
    ids=[
        "short",
        "identical",
        "integer-offset",
        "wide-integer",
        "normal-empty",
        "normal-finding",
    ],
)
def test_relation_normal_exits_complete_candidate_once(
    rows, expected_kinds
):
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )

    findings = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert [finding["kind"] for finding in findings] == expected_kinds
    assert result.candidates_total == 1
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.work_examined == 2 * len(rows)
    assert resources.state.live_units == 0


def test_relation_proportional_arrays_die_before_candidate_finalizer(
    monkeypatch
):
    rows = [
        [
            row + 0.125,
            2.75 * (row + 0.125) + 3.5,
            (row + 0.375) ** 2 + 0.625,
        ]
        for row in range(40)
    ]
    sheet = Sheet.from_rows([["a", "b", "c"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=1_000_000,
        state_limit=1_000_000,
    )
    refs_by_candidate = {}
    finalized = []
    original_allocate = audit._DenseCandidate.allocate
    original_exit = audit._DenseCandidate.__exit__

    def array_refs(value):
        if isinstance(value, np.ndarray):
            return [weakref.ref(value)]
        if isinstance(value, tuple):
            return [
                ref
                for item in value
                for ref in array_refs(item)
            ]
        return []

    def tracked_allocate(self, name, units, factory):
        value, lease = original_allocate(self, name, units, factory)
        refs_by_candidate.setdefault(id(self), []).extend(
            array_refs(value)
        )
        return value, lease

    def tracked_exit(self, exc_type, exc, traceback):
        refs = refs_by_candidate.get(id(self), ())
        assert refs
        assert all(ref() is None for ref in refs)
        finalized.append(id(self))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "allocate", tracked_allocate
    )
    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        3,
        ["a", "b", "c"],
        _resources=resources,
    )

    assert len(finalized) == 3
    assert resources.result().candidates_examined == 3
    assert resources.state.live_units == 0


def test_relation_later_state_rejection_keeps_completed_candidate():
    rows = [[i, i + 5, i + 0.125] for i in range(8)]
    sheet = Sheet.from_rows([["a", "b", "c"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=0,
    )

    findings = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        3,
        ["a", "b", "c"],
        _resources=resources,
    )
    result = resources.result()

    assert [finding["kind"] for finding in findings] == [
        "constant_offset"
    ]
    assert result.candidates_total == 3
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 2
    assert result.work_examined == 4 * len(rows)
    assert result.state_required_lower_bound > 0
    assert result.peak_state_units == 0
    assert result.limits_reached == ("state",)
    assert resources.state.live_units == 0


def test_relation_state_boundary_stops_before_rejected_allocation():
    rows = [[i + 0.125, 3 * (i + 0.125) + 7] for i in range(60)]
    sheet = Sheet.from_rows([["left", "right"], *rows])
    probe = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=probe,
    )
    required = probe.result().peak_state_units
    assert {
        "fitted_build_workspace",
        "fitted_relation_workspace",
    } <= probe.state.seen_names

    limited = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=required - 1,
    )
    findings = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=limited,
    )
    result = limited.result()

    assert findings == []
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert "state" in result.limits_reached
    assert result.peak_state_units <= required - 1
    assert limited.state.live_units == 0

    exact = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=required,
    )
    exact_findings = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=exact,
    )
    assert exact_findings == audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    assert exact.result().limits_reached == ()
    assert exact.state.live_units == 0


def test_dense_candidate_factory_runs_only_inside_entered_transaction():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    live_names_seen = []

    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None
    with pytest.raises(AssertionError):
        candidate.allocate(
            "too_early",
            1,
            lambda: pytest.fail("pre-transaction factory ran"),
        )
    with candidate:
        value, lease = candidate.allocate(
            "probe_array",
            1,
            lambda: (
                live_names_seen.append(resources.state.live_names),
                np.zeros(1, dtype=np.float64),
            )[1],
        )
        assert value.shape == (1,)
        assert lease is not None
        candidate.release(lease)
        assert candidate.live_lease_count == 0

    assert live_names_seen == [frozenset({"probe_array"})]
    assert resources.state.live_names == frozenset()

    blocked = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=0,
    )
    assert blocked.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = blocked.start_candidate(0, lambda *_args: None)
    assert candidate is not None
    with candidate:
        candidate.allocate(
            "blocked_array",
            1,
            lambda: pytest.fail("factory ran before state admission"),
        )
        pytest.fail("resource rejection did not unwind candidate")
    assert candidate.rejected is True
    assert candidate.closed is True
    assert blocked.result().candidates_examined == 0
    assert not hasattr(audit._DenseFamilyResources, "allocate")

    work_blocked = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=0,
        state_limit=1,
    )
    candidate, lease, initial_leases = (
        work_blocked.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()
    assert work_blocked.work_examined == 0
    assert work_blocked.state.live_units == 0

    state_blocked_candidate = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=0,
    )
    candidate, lease, initial_leases = (
        state_blocked_candidate.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()
    assert state_blocked_candidate.candidates_started == 0
    assert state_blocked_candidate.work_examined == 0
    assert state_blocked_candidate.state.live_units == 0


def test_dense_candidate_registry_tracks_only_live_leases():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None

    with candidate:
        for _ in range(5_000):
            _value, lease = candidate.allocate(
                "group_probe",
                1,
                lambda: np.zeros(1, dtype=np.float64),
            )
            assert candidate.live_lease_count == 1
            del _value
            candidate.release(lease)
            assert candidate.live_lease_count == 0
        assert candidate.peak_lease_count == 1

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


def test_dense_candidate_scoped_helper_drops_array_before_release():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None

    def run_candidate_body():
        value, lease = candidate.allocate(
            "scoped_array",
            1,
            lambda: np.zeros(1, dtype=np.float64),
        )
        return weakref.ref(value), lease

    with candidate:
        value_ref, lease = run_candidate_body()
        assert value_ref() is None
        candidate.release(lease)

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


def test_dense_source_factory_exception_uses_candidate_finalizer(
    monkeypatch
):
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1
    exit_snapshots = []
    original_exit = audit._DenseCandidate.__exit__

    def tracked_exit(self, exc_type, exc, traceback):
        exit_snapshots.append((
            self.live_lease_count,
            resources.state.live_names,
        ))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    with pytest.raises(RuntimeError, match="source factory"):
        with candidate:
            candidate.materialize(
                source_lease,
                lambda: (_ for _ in ()).throw(
                    RuntimeError("source factory")
                ),
                release_after=initial_leases,
            )

    result = resources.result()
    assert exit_snapshots == [(
        2,
        frozenset({"candidate_workspace", "candidate_array"}),
    )]
    assert candidate.closed is True
    assert candidate.live_lease_count == 0
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


def test_dense_source_validation_exception_uses_candidate_finalizer(
    monkeypatch
):
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1
    exit_snapshots = []
    original_exit = audit._DenseCandidate.__exit__

    def tracked_exit(self, exc_type, exc, traceback):
        exit_snapshots.append((
            self.live_lease_count,
            resources.state.live_names,
        ))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    with pytest.raises(
        AssertionError, match="used 2 units but reserved 1"
    ):
        with candidate:
            candidate.materialize(
                source_lease,
                lambda: np.zeros(2, dtype=np.float64),
                release_after=initial_leases,
            )

    assert exit_snapshots == [(
        2,
        frozenset({"candidate_workspace", "candidate_array"}),
    )]
    assert candidate.closed is True
    assert candidate.live_lease_count == 0
    result = resources.result()
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


def test_dense_first_materialization_releases_initial_workspace():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    live_names_during_factory = []
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1

    with candidate:
        value = candidate.materialize(
            source_lease,
            lambda: (
                live_names_during_factory.append(
                    resources.state.live_names
                ),
                np.zeros(1, dtype=np.float64),
            )[1],
            release_after=initial_leases,
        )
        assert live_names_during_factory == [frozenset({
            "candidate_workspace",
            "candidate_array",
        })]
        assert resources.state.live_names == frozenset({
            "candidate_array"
        })
        assert candidate.live_lease_count == 1
        del value
        candidate.release(source_lease)

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


ARRAY_SOURCE_CASES = (
    (
        "arithmetic_progression",
        "detect_arithmetic_progression",
        audit,
        "col_array",
        {"column"},
    ),
    (
        "within_column",
        "detect_within_column_patterns",
        audit,
        "col_array",
        {"column"},
    ),
    (
        "dispersed_repeats",
        "detect_dispersed_repeats",
        audit.np,
        "isnan",
        {"numeric_mask"},
    ),
    (
        "identical_after_rounding",
        "detect_identical_after_rounding",
        audit.np,
        "isnan",
        {"candidate_workspace", "candidate_mask"},
    ),
)


@pytest.mark.parametrize(
    "family,detector_name,owner,source_name,expected_leases",
    ARRAY_SOURCE_CASES,
    ids=[case[0] for case in ARRAY_SOURCE_CASES],
)
def test_array_family_work_rejection_precedes_source_factory(
    family,
    detector_name,
    owner,
    source_name,
    expected_leases,
    monkeypatch,
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=0,
        state_limit=100_000,
    )

    monkeypatch.setattr(
        owner,
        source_name,
        lambda *_args, **_kwargs: pytest.fail(
            "source factory ran before work admission"
        ),
    )
    findings = getattr(audit, detector_name)(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )

    result = resources.result()
    assert findings == []
    assert resources.candidates_started == 0
    assert result.candidates_examined == 0
    assert result.work_examined == 0
    assert expected_leases <= resources.state.seen_names
    assert resources.state.live_names == frozenset()


@pytest.mark.parametrize(
    "family,detector_name,owner,source_name,expected_leases",
    ARRAY_SOURCE_CASES,
    ids=[case[0] for case in ARRAY_SOURCE_CASES],
)
def test_array_family_source_exception_uses_candidate_finalizer(
    family,
    detector_name,
    owner,
    source_name,
    expected_leases,
    monkeypatch,
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )

    def fail_source_factory(*_args, **_kwargs):
        raise RuntimeError(f"{family} source factory")

    monkeypatch.setattr(owner, source_name, fail_source_factory)
    with pytest.raises(RuntimeError, match=f"{family} source factory"):
        getattr(audit, detector_name)(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )

    result = resources.result()
    assert resources.candidates_started == 1
    assert result.candidates_examined == 0
    assert expected_leases <= resources.state.seen_names
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


GROUP_REJECTION_CASES = (
    (
        "dispersed-group-rows",
        "dispersed_repeats",
        "detect_dispersed_repeats",
        Sheet.from_rows(
            [["value"]]
            + [
                [1000.1234567 + (row % 60) * 0.7312345]
                for row in range(120)
            ]
        ),
        (1, 121, 0, 1, ["value"]),
        "group_rows",
        {"group_diffs", "group_gaps", "sample_rounded"},
    ),
    (
        "dispersed-group-diffs",
        "dispersed_repeats",
        "detect_dispersed_repeats",
        Sheet.from_rows(
            [["value"]]
            + [
                [1000.1234567 + (row % 60) * 0.7312345]
                for row in range(120)
            ]
        ),
        (1, 121, 0, 1, ["value"]),
        "group_diffs",
        {"group_gaps", "sample_rounded"},
    ),
    (
        "dispersed-group-gaps",
        "dispersed_repeats",
        "detect_dispersed_repeats",
        Sheet.from_rows(
            [["value"]]
            + [
                [1000.1234567 + (row % 60) * 0.7312345]
                for row in range(120)
            ]
        ),
        (1, 121, 0, 1, ["value"]),
        "group_gaps",
        {"sample_rounded"},
    ),
    (
        "rounding-group-values",
        "identical_after_rounding",
        "detect_identical_after_rounding",
        Sheet.from_rows(
            [["left", "right"]]
            + [
                [
                    1.001 + (row % 20) * 0.0021,
                    2.001 + (row % 20) * 0.0021,
                ]
                for row in range(60)
            ]
        ),
        (1, 61, 0, 2, ["left", "right"]),
        "group_values",
        {"precise_rounded", "precise_values"},
    ),
)


@pytest.mark.parametrize(
    (
        "case_id",
        "family",
        "detector_name",
        "sheet",
        "bounds",
        "reject_name",
        "forbidden_later",
    ),
    GROUP_REJECTION_CASES,
    ids=[case[0] for case in GROUP_REJECTION_CASES],
)
def test_dense_group_rejection_unwinds_complete_candidate(
    case_id,
    family,
    detector_name,
    sheet,
    bounds,
    reject_name,
    forbidden_later,
    monkeypatch,
):
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=10_000_000,
    )
    attempts = []
    candidates = []
    original_try_reserve = audit.StateBudget.try_reserve
    original_start = resources.start_allocated_candidate

    def reject_selected(state, name, units):
        if state is resources.state:
            attempts.append(name)
            if name == reject_name:
                return None
        return original_try_reserve(state, name, units)

    def tracked_start(*args, **kwargs):
        candidate, lease, initial_leases = original_start(
            *args, **kwargs
        )
        if candidate is not None:
            candidates.append(candidate)
        return candidate, lease, initial_leases

    monkeypatch.setattr(
        audit.StateBudget, "try_reserve", reject_selected
    )
    monkeypatch.setattr(
        resources, "start_allocated_candidate", tracked_start
    )

    findings = getattr(audit, detector_name)(
        sheet, *bounds, _resources=resources
    )
    result = resources.result()

    assert findings == [], case_id
    assert reject_name in attempts
    assert forbidden_later.isdisjoint(attempts)
    assert len(candidates) == 1
    assert candidates[0].rejected is True
    assert candidates[0].closed is True
    assert candidates[0].live_lease_count == 0
    assert result.candidates_examined == 0
    assert "state" in result.limits_reached
    assert resources.state.live_units == 0


SCALAR_PAIR_SOURCE_CASES = (
    ("relations", "detect_relations"),
    ("equal_pairs", "detect_equal_pairs"),
)


@pytest.mark.parametrize(
    "family,detector_name",
    SCALAR_PAIR_SOURCE_CASES,
    ids=[case[0] for case in SCALAR_PAIR_SOURCE_CASES],
)
def test_scalar_pair_work_rejection_precedes_source_scan(
    family, detector_name, monkeypatch
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=0,
        state_limit=100_000,
    )
    monkeypatch.setattr(
        audit,
        "_numeric_pair_stats",
        lambda *_args, **_kwargs: pytest.fail(
            "scalar pair source ran before work admission"
        ),
    )

    findings = getattr(audit, detector_name)(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )

    result = resources.result()
    assert findings == []
    assert resources.candidates_started == 0
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 0
    assert resources.state.live_names == frozenset()


@pytest.mark.parametrize(
    "family,detector_name",
    SCALAR_PAIR_SOURCE_CASES,
    ids=[case[0] for case in SCALAR_PAIR_SOURCE_CASES],
)
def test_scalar_pair_source_exception_uses_entered_finalizer(
    family, detector_name, monkeypatch
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    candidates = []
    original_start = resources.start_candidate

    def tracked_start(source_visits, emit):
        candidate = original_start(source_visits, emit)
        if candidate is not None:
            candidates.append(candidate)
        return candidate

    monkeypatch.setattr(resources, "start_candidate", tracked_start)

    def fail_source(*_args, **_kwargs):
        raise RuntimeError(f"{family} scalar source")

    monkeypatch.setattr(audit, "_numeric_pair_stats", fail_source)
    with pytest.raises(RuntimeError, match=f"{family} scalar source"):
        getattr(audit, detector_name)(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )

    result = resources.result()
    assert len(candidates) == 1
    assert candidates[0].entered is True
    assert candidates[0].closed is True
    assert resources.candidates_started == 1
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 80
    assert resources.state.live_names == frozenset()


def test_declared_state_requirement_survives_every_rejection_path():
    row_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=0,
        work_limit=10,
        state_limit=10,
    )
    assert not row_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )

    work_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=2,
        state_limit=10,
    )
    assert work_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )
    assert work_limited.start_candidate(
        3, lambda *_args: None
    ) is None

    state_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=1,
    )
    assert state_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )
    candidate, lease, initial_leases = (
        state_limited.start_allocated_candidate(
            "rejected",
            2,
            0,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()

    results = [
        row_limited.result(),
        work_limited.result(),
        state_limited.result(),
    ]
    assert [result.state_required for result in results] == [7, 7, 7]
    assert [
        result.state_required_lower_bound for result in results
    ] == [0, 0, 2]
    assert [result.peak_state_units for result in results] == [0, 0, 0]
    assert [result.limits_reached for result in results] == [
        ("row",),
        ("work",),
        ("state",),
    ]


def test_dense_candidate_finalizer_commits_or_discards_atomically():
    emitted = []
    completed = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=1,
    )
    assert completed.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=1,
    )
    candidate = completed.start_candidate(
        1,
        lambda severity, builder: emitted.append(
            (severity, builder())
        ),
    )
    assert candidate is not None
    with candidate:
        candidate.offer("high", lambda: {"id": "kept"})

    assert emitted == [("high", {"id": "kept"})]
    assert candidate.closed is True
    assert completed.result().candidates_examined == 1
    assert completed.state.live_units == 0

    rejected_calls = []
    rejected = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=0,
    )
    assert rejected.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=1,
    )
    candidate = rejected.start_candidate(
        1,
        lambda severity, builder: rejected_calls.append(
            (severity, builder())
        ),
    )
    assert candidate is not None
    with candidate:
        candidate.offer(
            "high",
            lambda: pytest.fail("rejected candidate was materialized"),
        )
        candidate.allocate(
            "blocked",
            1,
            lambda: pytest.fail("factory ran before reservation"),
        )
        pytest.fail("resource rejection did not unwind candidate")

    result = rejected.result()
    assert candidate.rejected is True
    assert candidate.closed is True
    assert rejected_calls == []
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert rejected.state.live_units == 0
```

- [ ] **Step 2: Replace estimator-boundary tests with detector-owned tests**

In `tests/test_resource_lifetime.py`, replace
`test_dense_detector_declared_state_bounds_cover_actual_live_arrays`,
`test_within_column_declared_state_covers_actual_live_arrays`, and
`test_within_column_state_admission_boundary_is_deterministic`. Keep their
existing fixtures, but use these complete expected-name sets:

```python
EXPECTED_DENSE_STATES = {
    "relations": {
        "mask",
        "mask_rhs_workspace",
        "filtered_values",
        "abs_scale_workspace",
        "diff",
        "nonzero_workspace",
        "relation_close_workspace",
        "ratio",
        "ratio_stats_workspace",
        "sum",
        "sum_compare_workspace",
        "linear_fit_workspace",
        "fitted",
        "fitted_build_workspace",
        "fitted_relation_workspace",
        "integer_shift_workspace",
        "diff_is_int",
        "fractional_workspace",
        "frac_x",
        "hp_rows",
        "high_precision_unique_workspace",
        "high_precision_unique",
        "integer_diff_round_workspace",
        "int_diff_rounded",
        "integer_diff_unique_workspace",
        "int_diffs",
        "diff_rounded",
        "diff_unique_workspace",
        "unique_diffs",
    },
    "arithmetic_progression": {
        "column",
        "numeric_mask",
        "values",
        "diffs",
        "progression_abs_workspace",
        "progression_close_workspace",
    },
    "within_column": {
        "column",
        "numeric_mask",
        "values",
        "rounded",
        "frequency_workspace",
        "unique",
        "counts",
        "order",
        "integer_workspace",
    },
    "dispersed_repeats": {
        "numeric_mask",
        "rows",
        "values",
        "integer_gate_workspace",
        "rounded",
        "frequency_workspace",
        "unique_all",
        "counts_all",
        "order_all",
        "core_mask",
        "core_rows",
        "core_values",
        "decimal_places",
        "precision_gate",
        "rounded_core",
        "unique_workspace",
        "unique_core",
        "first_core",
        "inverse",
        "counts",
        "partition_workspace",
        "sort_workspace",
        "sorted_positions",
        "group_start_workspace",
        "group_starts",
        "group_rows",
        "group_diffs",
        "group_gaps",
        "sample_rounded",
        "sample_frequency_workspace",
        "sample_unique",
        "sample_counts",
        "sample_order",
    },
    "identical_after_rounding": {
        "candidate_workspace",
        "candidate_mask",
        "bucket_workspace",
        "bucket_mask",
        "flat_indices",
        "values",
        "rounded",
        "unique_workspace",
        "rounded_values",
        "first_indices",
        "inverse",
        "counts",
        "sort_workspace",
        "sorted_positions",
        "group_start_workspace",
        "group_starts",
        "group_values",
        "precise_rounded",
        "precise_unique_workspace",
        "precise_values",
    },
}

RELATION_ALLOCATED_STATES = {
    "mask",
    "mask_rhs_workspace",
    "filtered_values",
    "diff",
    "ratio",
    "sum",
    "fitted",
    "diff_is_int",
    "frac_x",
    "hp_rows",
    "high_precision_unique",
    "int_diff_rounded",
    "int_diffs",
    "diff_rounded",
    "unique_diffs",
}
```

Add guards that require an explicit required/forbidden lease contract for each
NumPy call. A workspace alone is never sufficient when the operation also
creates proportional outputs, and a call site is not inferred solely because
its required names are a subset of all currently live names:

```python
def _guard_numpy_workspaces(monkeypatch, resources, guards):
    observed = set()
    for function_name, variants in guards.items():
        original = getattr(audit.np, function_name)
        normalized = []
        for variant in variants:
            label, required, *optional_forbidden = variant
            forbidden = (
                optional_forbidden[0]
                if optional_forbidden
                else ()
            )
            normalized.append((
                label,
                frozenset(required),
                frozenset(forbidden),
            ))

        def guarded(
            *args,
            _original=original,
            _variants=tuple(normalized),
            _function_name=function_name,
            **kwargs,
        ):
            live_names = resources.state.live_names
            matches = [
                label
                for label, required, forbidden in _variants
                if (
                    required <= live_names
                    and forbidden.isdisjoint(live_names)
                )
            ]
            assert len(matches) == 1, (
                f"{_function_name} required exactly one explicit "
                f"required/forbidden lease contract, got {matches}; "
                f"live={live_names}"
            )
            observed.add((_function_name, matches[0]))
            return _original(*args, **kwargs)

        monkeypatch.setattr(audit.np, function_name, guarded)
    return observed


def _guard_callable_workspace(
    monkeypatch, owner, function_name, resources, required_variants
):
    original = getattr(owner, function_name)
    variants = tuple(
        frozenset(required) for required in required_variants
    )

    def guarded(*args, **kwargs):
        matches = [
            required
            for required in variants
            if required <= resources.state.live_names
        ]
        assert len(matches) == 1, (
            f"{function_name} required one complete lease variant, "
            f"got {matches}"
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, function_name, guarded)


WORKSPACE_GUARDS = {
    "relations": {
        "isnan": (
            (
                "mask",
                {"mask"},
                {"mask_rhs_workspace", "linear_fit_workspace"},
            ),
            (
                "mask_rhs",
                {"mask", "mask_rhs_workspace"},
                {"linear_fit_workspace"},
            ),
            (
                "linear_fit",
                {"linear_fit_workspace"},
                {"mask", "mask_rhs_workspace"},
            ),
        ),
        "logical_not": (
            ("mask", {"mask"}, {"mask_rhs_workspace"}),
            ("mask_rhs", {"mask", "mask_rhs_workspace"}),
        ),
        "logical_and": (
            ("mask", {"mask", "mask_rhs_workspace"}),
        ),
        "abs": (
            ("abs_scale", {"abs_scale_workspace"}),
            ("fractional", {"frac_x", "hp_rows", "fractional_workspace"}),
            ("fitted_build", {"fitted", "fitted_build_workspace"}),
            ("integer_shift", {"diff_is_int", "integer_shift_workspace"}),
            ("linear_fit", {"linear_fit_workspace"}),
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
        ),
        "all": (
            ("nonzero", {"nonzero_workspace"}),
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
        ),
        "std": (
            ("ratio_stats", {"ratio", "ratio_stats_workspace"}),
            ("fitted_build", {"fitted", "fitted_build_workspace"}),
            ("linear_fit", {"linear_fit_workspace"}),
        ),
        "full_like": (
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
        ),
        "round": (
            ("fractional", {"frac_x", "fractional_workspace"}),
            (
                "integer_diff",
                {"int_diff_rounded", "integer_diff_round_workspace"},
            ),
            ("diff", {"diff_rounded"}),
        ),
        "unique": (
            (
                "high_precision",
                {
                    "frac_x",
                    "high_precision_unique_workspace",
                    "high_precision_unique",
                },
            ),
            (
                "integer_diff",
                {
                    "int_diff_rounded",
                    "integer_diff_unique_workspace",
                    "int_diffs",
                },
            ),
            (
                "diff",
                {
                    "diff_rounded",
                    "diff_unique_workspace",
                    "unique_diffs",
                },
            ),
        ),
    },
    "arithmetic_progression": {
        "isnan": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "abs": (
            ("scale", {"values", "progression_abs_workspace"}),
            ("close", {"diffs", "progression_close_workspace"}),
        ),
        "allclose": (
            ("close", {"diffs", "progression_close_workspace"}),
        ),
    },
    "within_column": {
        "isnan": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "unique": (
            (
                "frequency",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique",
                    "counts",
                    "order",
                },
            ),
        ),
        "lexsort": (
            (
                "frequency",
                {
                    "frequency_workspace",
                    "unique",
                    "counts",
                    "order",
                },
            ),
        ),
    },
    "dispersed_repeats": {
        "isnan": (
            ("numeric_mask", {"numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"numeric_mask"}),
        ),
        "flatnonzero": (
            ("rows", {"numeric_mask", "rows"}),
        ),
        "unique": (
            (
                "frequency",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "core",
                {
                    "rounded_core",
                    "unique_workspace",
                    "unique_core",
                    "first_core",
                    "inverse",
                    "counts",
                },
            ),
            (
                "sample",
                {
                    "sample_rounded",
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
        ),
        "lexsort": (
            (
                "frequency",
                {
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "sample",
                {
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
        ),
        "partition": (
            ("median", {"decimal_places", "partition_workspace"}),
        ),
        "greater_equal": (
            (
                "precision_gate",
                {"decimal_places", "precision_gate"},
            ),
        ),
        "argsort": (
            ("groups", {"inverse", "sort_workspace", "sorted_positions"}),
        ),
        "concatenate": (
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
            ),
        ),
        "cumsum": (
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
            ),
        ),
        "diff": (
            ("group_diffs", {"group_rows", "group_diffs"}),
        ),
        "greater": (
            (
                "group_gaps",
                {"group_diffs", "group_gaps"},
            ),
        ),
    },
    "identical_after_rounding": {
        "isnan": (
            (
                "candidate_mask",
                {
                    "candidate_workspace",
                    "candidate_mask",
                },
            ),
        ),
        "flatnonzero": (
            (
                "flat_indices",
                {"bucket_mask", "flat_indices"},
                {"candidate_workspace"},
            ),
        ),
        "unique": (
            (
                "rounded",
                {
                    "rounded",
                    "unique_workspace",
                    "rounded_values",
                    "first_indices",
                    "inverse",
                    "counts",
                },
                {"candidate_workspace"},
            ),
            (
                "precise",
                {
                    "precise_rounded",
                    "precise_unique_workspace",
                    "precise_values",
                },
                {"candidate_workspace"},
            ),
        ),
        "argsort": (
            (
                "groups",
                {"inverse", "sort_workspace", "sorted_positions"},
                {"candidate_workspace"},
            ),
        ),
        "concatenate": (
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
                {"candidate_workspace"},
            ),
        ),
        "cumsum": (
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
                {"candidate_workspace"},
            ),
        ),
    },
}

EXPECTED_MULTI_OUTPUT_CALLS = {
    "within_column": {
        ("unique", "frequency"),
        ("lexsort", "frequency"),
    },
    "dispersed_repeats": {
        ("unique", "frequency"),
        ("lexsort", "frequency"),
        ("greater_equal", "precision_gate"),
        ("unique", "core"),
        ("argsort", "groups"),
        ("concatenate", "group_starts"),
        ("cumsum", "group_starts"),
        ("diff", "group_diffs"),
        ("greater", "group_gaps"),
        ("unique", "sample"),
        ("lexsort", "sample"),
    },
    "identical_after_rounding": {
        ("unique", "rounded"),
        ("argsort", "groups"),
        ("concatenate", "group_starts"),
        ("cumsum", "group_starts"),
        ("unique", "precise"),
    },
}
```

The relation parameterized test from Step 1 must invoke both guard helpers
before its instrumented detector call, exactly as shown there. This covers
NumPy calls plus `relation_close`, `integer_shift_close`, `stats.linregress`,
and the exact high-precision `np.unique` output/workspace pair. Each
array-family fixture must assert the expected observed labels, including both
`unique` and `lexsort` for every `_numpy_frequency_summary()`, every direct
`unique`, `argsort`, and both `concatenate` and `cumsum` for group starts. This
prevents a call from passing because an unrelated live lease happens to share
the same operation.

Add this case to the existing parameter table:

```python
(
    "arithmetic_progression",
    "detect_arithmetic_progression",
    Sheet.from_rows(
        [["value"]]
        + [[1.125 + row * 0.375] for row in range(40)]
    ),
    (1, 41, 0, 1, ["value"]),
    EXPECTED_DENSE_STATES["arithmetic_progression"],
),
```

For the dispersed/rounding parameterized test and both all-distinct and
high-duplication within-column cases, run the baseline first, then:

```python
resources = audit._DenseFamilyResources(
    family=family,
    max_rows=10_000,
    work_limit=10_000_000,
    state_limit=10_000_000,
)
observed_numpy_calls = _guard_numpy_workspaces(
    monkeypatch,
    resources,
    WORKSPACE_GUARDS[family],
)
instrumented = detector(sheet, *bounds, _resources=resources)
result = resources.result()

assert instrumented == baseline
assert result.candidates_examined == result.candidates_total
assert result.candidates_skipped == 0
assert result.peak_state_units <= resources.state.limit_units
assert resources.state.live_units == 0
assert EXPECTED_DENSE_STATES[family] <= resources.state.seen_names
assert (
    EXPECTED_MULTI_OUTPUT_CALLS.get(family, set())
    <= observed_numpy_calls
)
```

Add a scalar-state regression for equal pairs:

```python
def test_equal_pairs_consumes_work_without_allocating_dense_state():
    rows = [[row + 0.125, row + 0.125] for row in range(20)]
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="equal_pairs",
        max_rows=100,
        work_limit=100,
        state_limit=0,
    )
    baseline = audit.detect_equal_pairs(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )

    instrumented = audit.detect_equal_pairs(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.work_examined == 40
    assert result.peak_state_units == 0
    assert resources.state.live_units == 0
```

Parameterize the existing state-admission boundary test over both the
within-column fixture and the dispersed-repeat fixture used above. For each
family, first run with an ample limit to obtain
`required = probe.result().peak_state_units`, then run at `required - 1` and
`required`. Assert the lower run emits no partial candidate, reports `"state"`,
and releases all leases; assert the exact-boundary run matches the unlimited
baseline. For `dispersed_repeats`, also assert that `precision_gate`,
`group_diffs`, and `group_gaps` are in `probe.state.seen_names`, so the
boundary is measured after all three newly explicit outputs have executed.

- [ ] **Step 3: Replace caller-preflight coverage and add scan assertions**

Replace
`test_dense_detector_limits_preflight_every_named_family` in
`tests/test_detector_coverage.py`; do not retain its expectation that detector
functions are skipped. The replacement exercises every real detector session
and proves row rejection happens before source scans or allocation:

```python
def test_dense_row_limit_rejects_inside_every_real_detector(
    monkeypatch
):
    sheet = Sheet.from_rows([
        [float(row * 10 + col) + 0.125 for col in range(4)]
        for row in range(12)
    ])
    called = []
    detector_names = (
        "detect_relations",
        "detect_equal_pairs",
        "detect_arithmetic_progression",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
    )
    expected_totals = {
        "relations": 6,
        "equal_pairs": 6,
        "arithmetic_progression": 4,
        "within_column": 4,
        "dispersed_repeats": 4,
        "identical_after_rounding": 1,
    }

    for detector_name in detector_names:
        original = getattr(audit, detector_name)

        def wrapped(
            *args,
            _original=original,
            **kwargs,
        ):
            resources = kwargs["_resources"]
            called.append(resources.family)
            return _original(*args, **kwargs)

        monkeypatch.setattr(audit, detector_name, wrapped)

    def fail_source_or_allocation(*_args, **_kwargs):
        pytest.fail("row-limited detector performed source/allocation work")

    monkeypatch.setattr(
        audit, "_numeric_pair_stats", fail_source_or_allocation
    )
    monkeypatch.setattr(audit, "col_array", fail_source_or_allocation)
    monkeypatch.setattr(audit.np, "isnan", fail_source_or_allocation)
    for method_name in (
        "_reserve",
        "start_allocated_candidate",
    ):
        monkeypatch.setattr(
            audit._DenseFamilyResources,
            method_name,
            fail_source_or_allocation,
        )
    monkeypatch.setattr(
        audit, "detect_grim_grimmer", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(audit, "_DENSE_BLOCK_MAX_ROWS", 8)
    monkeypatch.setattr(audit, "_DENSE_BLOCK_CELL_WORK_LIMIT", 1_000_000)
    monkeypatch.setattr(audit, "_DENSE_BLOCK_STATE_CELL_LIMIT", 1_000_000)
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=False,
    )

    audit._analyze_numeric_blocks(
        sheet,
        file_name="large.csv",
        sheet_name="large",
        blocks=[(0, sheet.nrows, 0, sheet.ncols)],
        state=state,
    )

    assert sorted(called) == sorted(expected_totals)
    limitation = next(
        item for item in state.coverage.limitations
        if item["reason"] == "dense_block_detector_limit"
    )
    detectors = {
        item["family"]: item for item in limitation["detectors"]
    }
    assert set(detectors) == set(expected_totals)
    for family, candidates_total in expected_totals.items():
        result = detectors[family]
        assert result["candidates_total"] == candidates_total
        assert result["candidates_examined"] == 0
        assert result["candidates_skipped"] == candidates_total
        assert result["work_examined"] == 0
        assert result["work_skipped"] == result["work_required"]
        assert result["state_required_lower_bound"] == 0
        assert result["peak_state_units"] == 0
        assert result["limits_reached"] == ["row"]
    assert detectors["equal_pairs"]["state_required"] == 0
    assert all(
        item["state_required"] > 0
        for family, item in detectors.items()
        if family != "equal_pairs"
    )
    assert state.findings_omitted_is_lower_bound is True


def test_dense_resource_exhaustion_reports_detector_owned_counters(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "values.csv").write_text(
        "left,right\n"
        + "\n".join(
            f"{i + 0.125},{3 * (i + 0.125) + 7}"
            for i in range(40)
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "_DENSE_BLOCK_STATE_CELL_LIMIT", 1)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    limitation = next(
        item for item in scan["coverage"]["limitations"]
        if item["reason"] == "dense_block_detector_limit"
    )

    relation = next(
        item for item in limitation["detectors"]
        if item["family"] == "relations"
    )
    assert relation["candidates_examined"] == 0
    assert relation["candidates_skipped"] == 1
    assert relation["state_required"] > 1
    assert relation["state_required_lower_bound"] > 1
    assert relation["peak_state_units"] <= 1
    assert relation["limits_reached"] == ["state"]
    assert scan["scan_status"] == "partial"
    assert scan["findings_omitted_is_lower_bound"] is True
```

Append this source-boundary regression to `tests/test_module_boundaries.py`
before the RED run:

```python
def test_dense_resource_ownership_has_no_pretransaction_factory_escape():
    import ast
    from collections import Counter
    import inspect
    import textwrap

    import paperconan._audit as audit
    import pytest

    assert "_dense_detector_requirements" not in vars(audit)
    assert "_dense_detector_admission" not in vars(audit)
    assert "_run_factory" not in vars(audit._DenseFamilyResources)
    for unsafe_name in (
        "allocate",
        "allocate_candidate",
        "begin_candidate",
        "candidate",
        "complete_candidate",
        "reserve",
    ):
        assert not hasattr(audit._DenseFamilyResources, unsafe_name)

    expected_calls = {
        "detect_relations": Counter({
            "begin": 1,
            "start_candidate": 1,
        }),
        "detect_equal_pairs": Counter({
            "begin": 1,
            "start_candidate": 1,
        }),
        "detect_arithmetic_progression": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_within_column_patterns": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_dispersed_repeats": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
        "detect_identical_after_rounding": Counter({
            "begin": 1,
            "start_allocated_candidate": 1,
        }),
    }
    allowed_candidate_methods = {
        "allocate",
        "materialize",
        "offer",
        "release",
        "reserve",
    }
    allowed_candidate_properties = {"rejected"}
    scoped_helper_families = {
        "detect_relations",
        "detect_arithmetic_progression",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
    }

    def audit_source(source, name, expected):
        tree = ast.parse(source)
        root = tree.body[0]
        assert isinstance(root, ast.FunctionDef)
        assert ".complete_candidate(" not in source
        assert "_CandidateFindingBuffer(" not in source

        parents = {}
        calls = []
        called_attributes = set()
        resource_method_attributes = []
        candidate_names = set()
        admission_stores = set()
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
            if (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Attribute)
            ):
                called_attributes.add(id(parent.func))
                if (
                    isinstance(parent.func.value, ast.Name)
                    and parent.func.value.id == "resources"
                ):
                    calls.append(parent)
                    resource_method_attributes.append(parent.func)
                    if parent.func.attr in {
                        "start_candidate",
                        "start_allocated_candidate",
                    }:
                        assignment = parents[id(parent)]
                        assert isinstance(assignment, ast.Assign)
                        assert len(assignment.targets) == 1
                        target = assignment.targets[0]
                        if parent.func.attr == "start_candidate":
                            assert isinstance(target, ast.Name)
                            candidate_names.add(target.id)
                            admission_stores.add(id(target))
                        else:
                            assert isinstance(target, ast.Tuple)
                            assert len(target.elts) == 3
                            assert isinstance(target.elts[0], ast.Name)
                            candidate_names.add(target.elts[0].id)
                            admission_stores.add(id(target.elts[0]))

        resource_stores = [
            node
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Name)
                and node.id == "resources"
                and isinstance(node.ctx, ast.Store)
            )
        ]
        assert len(resource_stores) == 1
        resource_store = resource_stores[0]
        resource_assignment = parents[id(resource_store)]
        assert isinstance(resource_assignment, ast.Assign)
        assert resource_assignment.targets == [resource_store]

        assert len(candidate_names) == 1
        candidate_name = next(iter(candidate_names))
        candidate_method_attributes = []
        candidate_with_nodes = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Name):
                continue
            if node.id == "resources":
                if isinstance(node.ctx, ast.Store):
                    assert node is resource_store
                    continue
                assert isinstance(node.ctx, ast.Load)
                parent = parents[id(node)]
                assert isinstance(parent, ast.Attribute)
                assert parent.value is node
                assert isinstance(parent.ctx, ast.Load)
                assert id(parent) in called_attributes
                assert parent.attr in expected
            if node.id == "_resources" and isinstance(node.ctx, ast.Load):
                parent = parents[id(node)]
                assert isinstance(parent, ast.BoolOp)
                assignment = parents[id(parent)]
                assert assignment is resource_assignment
            if (
                node.id == candidate_name
            ):
                if isinstance(node.ctx, ast.Store):
                    assert id(node) in admission_stores
                    continue
                assert isinstance(node.ctx, ast.Load)
                parent = parents[id(node)]
                if isinstance(parent, ast.Compare):
                    assert parent.left is node
                    assert len(parent.ops) == 1
                    assert isinstance(
                        parent.ops[0], (ast.Is, ast.IsNot)
                    )
                    assert len(parent.comparators) == 1
                    comparator = parent.comparators[0]
                    assert (
                        isinstance(comparator, ast.Constant)
                        and comparator.value is None
                    )
                    continue
                if isinstance(parent, ast.withitem):
                    assert parent.context_expr is node
                    assert parent.optional_vars is None
                    with_node = parents[id(parent)]
                    assert isinstance(with_node, ast.With)
                    candidate_with_nodes.append(with_node)
                    continue
                assert isinstance(parent, ast.Attribute)
                assert parent.value is node
                assert isinstance(parent.ctx, ast.Load)
                if id(parent) in called_attributes:
                    assert parent.attr in allowed_candidate_methods
                    candidate_method_attributes.append(parent)
                else:
                    assert parent.attr in allowed_candidate_properties
                assert not parent.attr.startswith("_")

        scoped_helpers = []
        for helper in ast.walk(root):
            if helper is root or not isinstance(helper, ast.FunctionDef):
                continue
            if not any(
                isinstance(descendant, ast.Name)
                and descendant.id == candidate_name
                and isinstance(descendant.ctx, ast.Load)
                for descendant in ast.walk(helper)
            ):
                continue
            args = helper.args
            assert not args.posonlyargs
            assert not args.args
            assert args.vararg is None
            assert not args.kwonlyargs
            assert args.kwarg is None
            scoped_helpers.append(helper)

        if name in scoped_helper_families:
            assert len(scoped_helpers) == 1
            scoped_helper = scoped_helpers[0]
            assert scoped_helper.decorator_list == []
            assert scoped_helper.name not in {
                "resources",
                candidate_name,
            }
            assert not any(
                isinstance(
                    descendant,
                    (ast.Yield, ast.YieldFrom, ast.Await),
                )
                for descendant in ast.walk(scoped_helper)
            )
        else:
            assert scoped_helpers == []
            scoped_helper = root

        assert len(candidate_with_nodes) == 1
        candidate_with = candidate_with_nodes[0]
        assert len(candidate_with.items) == 1
        protected_names = {"resources", candidate_name}
        if scoped_helper is not root:
            protected_names.add(scoped_helper.name)

        for declaration in ast.walk(root):
            if isinstance(declaration, (ast.Global, ast.Nonlocal)):
                assert protected_names.isdisjoint(declaration.names)
            if isinstance(
                declaration,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                if declaration.name in protected_names:
                    assert declaration is scoped_helper
            if isinstance(declaration, ast.arg):
                assert declaration.arg not in protected_names
            if isinstance(declaration, (ast.Import, ast.ImportFrom)):
                for alias in declaration.names:
                    bound_name = (
                        alias.asname
                        or alias.name.split(".", 1)[0]
                    )
                    assert bound_name not in protected_names
            if isinstance(declaration, ast.ExceptHandler):
                assert declaration.name not in protected_names
            if isinstance(declaration, (ast.MatchAs, ast.MatchStar)):
                if declaration.name is not None:
                    assert declaration.name not in protected_names
            if isinstance(declaration, ast.MatchMapping):
                if declaration.rest is not None:
                    assert declaration.rest not in protected_names

        for deferred_node in ast.walk(root):
            if not isinstance(
                deferred_node,
                (ast.Lambda, ast.GeneratorExp, ast.AsyncFunctionDef),
            ):
                continue
            assert not any(
                isinstance(descendant, ast.Name)
                and descendant.id in protected_names
                for descendant in ast.walk(deferred_node)
            )

        for comprehension in ast.walk(root):
            if not isinstance(
                comprehension,
                (
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                continue
            assert not any(
                isinstance(descendant, ast.Name)
                and descendant.id in protected_names
                for descendant in ast.walk(comprehension)
            )

        def nearest_function(node):
            current = node
            while id(current) in parents:
                current = parents[id(current)]
                if isinstance(
                    current, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    return current
            raise AssertionError("candidate call has no function owner")

        assert nearest_function(candidate_with) is root
        if scoped_helper is not root:
            helper_references = [
                node
                for node in ast.walk(root)
                if (
                    isinstance(node, ast.Name)
                    and node.id == scoped_helper.name
                )
            ]
            assert len(helper_references) == 1
            helper_reference = helper_references[0]
            assert isinstance(helper_reference.ctx, ast.Load)
            helper_call = parents[id(helper_reference)]
            assert isinstance(helper_call, ast.Call)
            assert helper_call.func is helper_reference
            assert helper_call.args == []
            assert helper_call.keywords == []
            helper_statement = parents[id(helper_call)]
            assert isinstance(helper_statement, ast.Expr)
            assert helper_statement in candidate_with.body

        for attribute in candidate_method_attributes:
            owner = nearest_function(attribute)
            assert owner is scoped_helper
        for attribute in resource_method_attributes:
            owner = nearest_function(attribute)
            assert owner is root

        assert Counter(call.func.attr for call in calls) == expected
        assert all(
            isinstance(call.func.value, ast.Name)
            and call.func.value.id == "resources"
            for call in calls
        )

        if name == "detect_identical_after_rounding":
            start_call = next(
                call for call in calls
                if call.func.attr == "start_allocated_candidate"
            )
            initial = next(
                keyword.value
                for keyword in start_call.keywords
                if keyword.arg == "initial_reservations"
            )
            assert isinstance(initial, ast.Tuple)
            assert isinstance(initial.elts[0], ast.Tuple)
            reservation_name = initial.elts[0].elts[0]
            assert isinstance(reservation_name, ast.Constant)
            assert reservation_name.value == "candidate_workspace"

    for name, expected in expected_calls.items():
        detector = getattr(audit, name)
        parameters = inspect.signature(detector).parameters
        assert "_resources" in parameters
        assert "_state_tracker" not in parameters
        source = textwrap.dedent(inspect.getsource(detector))
        audit_source(source, name, expected)

    invalid_sources = (
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            candidate.offer("low", builder)
            def run_candidate():
                return candidate.rejected
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer(
                    "low", lambda: candidate.rejected
                )
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            candidate = replacement
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate as alias:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                return (
                    candidate.offer("low", builder)
                    for _item in values
                )
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
                yield None
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            resources = replacement
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            del resources
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = alias = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            def begin_resources():
                resources.begin()
            begin_resources()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            begin_resources = lambda: resources.begin()
            begin_resources()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            candidate.rejected = False
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            del candidate.rejected
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            saved = run_candidate
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            run_candidate()
            with candidate:
                pass
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate(candidate)
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                pass
            return run_candidate
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            [
                resources.begin()
                for _item in range(2)
            ]
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                [
                    candidate.offer("low", builder)
                    for _item in values
                ]
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case resources:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case [*candidate]:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            match value:
                case {**run_candidate}:
                    pass
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            global resources
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                nonlocal candidate
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
        """
        def detect_relations(_resources=None):
            global run_candidate
            resources = _resources or fallback
            resources.begin()
            candidate = resources.start_candidate(0, emit)
            if candidate is None:
                return []
            def run_candidate():
                candidate.offer("low", builder)
            with candidate:
                run_candidate()
        """,
    )
    for invalid_source in invalid_sources:
        with pytest.raises(AssertionError):
            audit_source(
                textwrap.dedent(invalid_source),
                "detect_relations",
                expected_calls["detect_relations"],
            )
```

- [ ] **Step 4: Run new dense tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_resource_lifetime.py \
  tests/test_detector_coverage.py \
  tests/test_module_boundaries.py -k \
  'relation_allocations or relation_branch_inventory or relation_normal_exits or relation_proportional_arrays or relation_later_state_rejection or relation_state_boundary or dense_candidate_factory or dense_candidate_registry or dense_candidate_scoped_helper or dense_candidate_finalizer or dense_source_factory_exception or dense_source_validation_exception or dense_first_materialization or dense_group_rejection or array_family_work_rejection or array_family_source_exception or scalar_pair_work_rejection or scalar_pair_source_exception or declared_state_requirement or dense_row_limit or equal_pairs or dense_resource_exhaustion or dense_resource_ownership'
```

Expected: failures because `_DenseFamilyResources` and detector-owned
`_resources` do not exist.

- [ ] **Step 5: Add detector-family resource result types**

In `src/paperconan/_audit.py`, import:

```python
from ._resources import StateBudget, state_units_for_nbytes
```

Add before the dense detectors:

```python
@dataclass(frozen=True)
class _DenseFamilyResult:
    family: str
    candidates_total: int
    candidates_examined: int
    candidates_skipped: int
    work_required: int
    work_examined: int
    work_skipped: int
    work_skipped_lower_bound: int
    state_required: int
    state_required_lower_bound: int
    peak_state_units: int
    limits_reached: tuple[str, ...]


class _DenseFamilyResources:
    def __init__(
        self,
        *,
        family,
        max_rows,
        work_limit,
        state_limit,
    ):
        self.family = family
        self.max_rows = (
            None if max_rows is None else max(0, int(max_rows))
        )
        self.work_limit = (
            None if work_limit is None else max(0, int(work_limit))
        )
        self._state = StateBudget(
            None
            if state_limit is None
            else max(0, int(state_limit))
        )
        self.candidates_total = 0
        self.candidates_started = 0
        self.candidates_examined = 0
        self.work_examined = 0
        self.minimum_candidate_work = 0
        self.state_required = 0
        self._limits_reached = set()
        self._stopped = False

    @classmethod
    def unlimited(cls, family):
        return cls(
            family=family,
            max_rows=None,
            work_limit=None,
            state_limit=None,
        )

    def begin(
        self,
        *,
        row_count,
        candidates_total,
        minimum_candidate_work,
        state_required,
    ):
        self.candidates_total = max(0, int(candidates_total))
        self.minimum_candidate_work = max(
            0, int(minimum_candidate_work)
        )
        self.state_required = max(0, int(state_required))
        if self.max_rows is not None and row_count > self.max_rows:
            self._limits_reached.add("row")
            self._stopped = True
            return False
        return True

    def _begin_candidate(self, source_visits):
        if self._stopped:
            return False
        source_visits = max(0, int(source_visits))
        if (
            self.work_limit is not None
            and self.work_examined + source_visits > self.work_limit
        ):
            self._limits_reached.add("work")
            self._stopped = True
            return False
        self.candidates_started += 1
        self.work_examined += source_visits
        return True

    def _reserve(self, name, units):
        lease = self._state.try_reserve(name, units)
        if lease is None:
            self._limits_reached.add("state")
            self._stopped = True
        return lease

    @staticmethod
    def _release_leases(leases):
        for lease in reversed(tuple(leases)):
            lease.release()

    def start_allocated_candidate(
        self,
        name,
        units,
        source_visits,
        emit,
        *,
        initial_reservations=(),
    ):
        leases = []
        for initial_name, initial_units in initial_reservations:
            initial_lease = self._reserve(
                initial_name, initial_units
            )
            if initial_lease is None:
                self._release_leases(leases)
                return None, None, ()
            leases.append(initial_lease)
        lease = self._reserve(name, units)
        if lease is None:
            self._release_leases(leases)
            return None, None, ()
        leases.append(lease)
        if not self._begin_candidate(source_visits):
            self._release_leases(leases)
            return None, None, ()
        try:
            candidate = self._candidate(emit, *leases)
        except BaseException:
            self._release_leases(leases)
            raise
        return candidate, lease, tuple(leases[:-1])

    def _candidate(self, emit, *initial_leases):
        return _DenseCandidate(
            self,
            emit,
            initial_leases=initial_leases,
        )

    def start_candidate(self, source_visits, emit):
        if not self._begin_candidate(source_visits):
            return None
        return self._candidate(emit)

    def _complete_candidate(self):
        self.candidates_examined += 1

    @property
    def state(self):
        return self._state

    @property
    def stopped(self):
        return self._stopped

    def result(self):
        unstarted = self.candidates_total - self.candidates_started
        work_required = (
            self.candidates_total * self.minimum_candidate_work
        )
        work_skipped = max(0, work_required - self.work_examined)
        state_required_lower_bound = (
            self._state.required_peak_units
        )
        assert state_required_lower_bound <= self.state_required
        return _DenseFamilyResult(
            family=self.family,
            candidates_total=self.candidates_total,
            candidates_examined=self.candidates_examined,
            candidates_skipped=(
                self.candidates_total - self.candidates_examined
            ),
            work_required=work_required,
            work_examined=self.work_examined,
            work_skipped=work_skipped,
            work_skipped_lower_bound=(
                max(0, unstarted) * self.minimum_candidate_work
            ),
            state_required=self.state_required,
            state_required_lower_bound=state_required_lower_bound,
            peak_state_units=self._state.peak_units,
            limits_reached=tuple(
                name for name in ("row", "work", "state")
                if name in self._limits_reached
            ),
        )
```

- [ ] **Step 6: Make relation allocations reservation-first**

Add optional `_resources=None` to `detect_relations()`. Create an unlimited
session only for direct compatibility calls:

```python
resources = _resources or _DenseFamilyResources(
    family="relations",
    max_rows=None,
    work_limit=None,
    state_limit=None,
)
row_count = r1 - r0
bool_units = state_units_for_nbytes(row_count)
relation_state_upper_bounds = {
    "mask": bool_units,
    "mask_rhs_workspace": bool_units,
    "filtered_values": 2 * row_count,
    "abs_scale_workspace": 2 * row_count,
    "diff": row_count,
    "nonzero_workspace": bool_units,
    "relation_close_workspace": 12 * row_count,
    "ratio": row_count,
    "ratio_stats_workspace": 2 * row_count,
    "sum": row_count,
    "sum_compare_workspace": 13 * row_count,
    "linear_fit_workspace": 12 * row_count,
    "fitted": row_count,
    "fitted_build_workspace": 2 * row_count,
    "fitted_relation_workspace": 12 * row_count,
    "integer_shift_workspace": 8 * row_count,
    "diff_is_int": bool_units,
    "fractional_workspace": row_count,
    "frac_x": row_count,
    "hp_rows": bool_units,
    "high_precision_unique_workspace": 4 * row_count,
    "high_precision_unique": row_count,
    "integer_diff_round_workspace": row_count,
    "int_diff_rounded": row_count,
    "integer_diff_unique_workspace": 4 * row_count,
    "int_diffs": row_count,
    "diff_rounded": row_count,
    "diff_unique_workspace": 4 * row_count,
    "unique_diffs": row_count,
}
pair_count = (c1 - c0) * (c1 - c0 - 1) // 2
if not resources.begin(
    row_count=row_count,
    candidates_total=pair_count,
    minimum_candidate_work=2 * row_count,
    state_required=sum(relation_state_upper_bounds.values()),
):
    return findings
```

Use `_DenseFamilyResources.unlimited("relations")` in the final code; the
expanded constructor above shows the exact unlimited values. Use the
corresponding family name for every other direct detector call.

Add a candidate-local lazy buffer so a state failure cannot emit part of one
column-pair candidate:

```python
class _CandidateFindingBuffer:
    def __init__(self):
        self._items = []

    def offer(self, severity, builder):
        self._items.append((severity, builder))

    def commit(self, emit):
        for severity, builder in self._items:
            emit(severity, builder)
        self._items.clear()

    def discard(self):
        self._items.clear()


class _DenseCandidateRejected(RuntimeError):
    pass


class _DenseCandidate:
    def __init__(
        self,
        resources,
        emit,
        *,
        initial_leases=(),
    ):
        self._resources = resources
        self.emit = emit
        self.findings = _CandidateFindingBuffer()
        self._leases = {}
        self._peak_lease_count = 0
        self._rejected = False
        self.entered = False
        self.closed = False
        for lease in initial_leases:
            self._adopt(lease)

    def _adopt(self, lease):
        key = id(lease)
        assert key not in self._leases
        assert not lease.released
        self._leases[key] = lease
        self._peak_lease_count = max(
            self._peak_lease_count, len(self._leases)
        )

    def __enter__(self):
        assert not self.closed
        assert not self.entered
        self.entered = True
        return self

    def reserve(self, name, units):
        assert self.entered
        assert not self.closed
        if self._rejected:
            raise _DenseCandidateRejected
        lease = self._resources._reserve(name, units)
        if lease is None:
            self._rejected = True
            raise _DenseCandidateRejected
        self._adopt(lease)
        return lease

    def allocate(self, name, units, factory):
        assert self.entered
        assert not self.closed
        lease = self.reserve(name, units)
        return self.materialize(lease, factory), lease

    def materialize(self, lease, factory, *, release_after=()):
        assert self.entered
        assert not self.closed
        assert id(lease) in self._leases
        assert not lease.released
        release_after = tuple(release_after)
        assert all(item is not lease for item in release_after)
        try:
            value = factory()
            values = value if isinstance(value, tuple) else (value,)
            lease.validate_nbytes(*(
                item.nbytes
                for item in values
                if hasattr(item, "nbytes")
            ))
            for transient_lease in release_after:
                self.release(transient_lease)
        except BaseException:
            self._rejected = True
            raise
        return value

    def release(self, lease):
        assert self.entered
        assert not self.closed
        tracked = self._leases.pop(id(lease))
        assert tracked is lease
        assert not lease.released
        lease.release()

    def offer(self, severity, builder):
        assert self.entered
        assert not self.closed
        self.findings.offer(severity, builder)

    @property
    def rejected(self):
        return self._rejected

    @property
    def live_lease_count(self):
        return len(self._leases)

    @property
    def peak_lease_count(self):
        return self._peak_lease_count

    def __exit__(self, exc_type, _exc, _traceback):
        assert not self.closed
        assert self.entered
        resource_rejection = exc_type is _DenseCandidateRejected
        try:
            if exc_type is None and not self._rejected:
                self.findings.commit(self.emit)
                self._resources._complete_candidate()
            else:
                self.findings.discard()
        finally:
            for lease in reversed(tuple(self._leases.values())):
                assert not lease.released
                lease.release()
            self._leases.clear()
            self.closed = True
        return resource_rejection
```

Put all proportional pair-local variables in a nested no-argument
`run_pair_candidate()` closure. It captures `candidate`, `ci`, and `cj` from the
pair-loop scope; never pass or alias the candidate object. Its normal branch
exits use `return` where the old loop used `continue`; candidate findings are
already buffered through `candidate.offer()`. The helper returns before
`__exit__()` releases remaining leases, so no prior-pair array remains bound
while the next pair allocates. For each pair, call:

```python
candidate = resources.start_candidate(
    2 * row_count,
    emit,
)
if candidate is None:
    break
with candidate:
    run_pair_candidate()
if candidate.rejected:
    break
```

Keep the existing branch order inside `run_pair_candidate()`. Lazy finding
builders may capture only scalars, immutable bounded samples, and other
non-proportional values; they must not close over NumPy arrays or sheet views.

Reserve before each proportional allocation. The mask and filtered arrays use:

```python
mask, mask_lease = candidate.allocate(
    "mask",
    relation_state_upper_bounds["mask"],
    lambda: np.isnan(ai),
)
np.logical_not(mask, out=mask)
mask_rhs, mask_rhs_lease = candidate.allocate(
    "mask_rhs_workspace",
    relation_state_upper_bounds["mask_rhs_workspace"],
    lambda: np.isnan(aj),
)
np.logical_not(mask_rhs, out=mask_rhs)
np.logical_and(mask, mask_rhs, out=mask)
del mask_rhs
candidate.release(mask_rhs_lease)
(x, y), filtered_lease = candidate.allocate(
    "filtered_values",
    2 * row_count,
    lambda: (ai[mask], aj[mask]),
)
del mask
candidate.release(mask_lease)
```

Use `candidate.allocate()` for each explicit result and
`candidate.reserve()` for each hidden workspace:

```text
mask                         state_units_for_nbytes(row_count)
mask_rhs_workspace           state_units_for_nbytes(row_count)
filtered_values              2 * row_count
abs_scale_workspace          2 * n
diff                         n
nonzero_workspace            state_units_for_nbytes(n)
relation_close_workspace     12 * n
ratio                        n
ratio_stats_workspace        2 * n
sum                          n
sum_compare_workspace        13 * n
linear_fit_workspace         12 * n
fitted                       n
fitted_build_workspace       2 * n
fitted_relation_workspace    12 * n
integer_shift_workspace      8 * n
diff_is_int                  state_units_for_nbytes(n)
fractional_workspace         n
frac_x                       n
hp_rows                      state_units_for_nbytes(n)
high_precision_unique_workspace 4 * n
high_precision_unique        n
integer_diff_round_workspace n
int_diff_rounded             n
integer_diff_unique_workspace 4 * n
int_diffs                    n
diff_rounded                 n
diff_unique_workspace        4 * n
unique_diffs                 n
```

Replace the intermediate `hp_fracs` list and Python set with in-place
compaction into the already reserved `frac_x` array:

```python
def _compact_high_precision_fractions(frac_x, hp_rows):
    count = 0
    for index, selected in enumerate(hp_rows):
        value = frac_x[index]
        if selected and _sig_frac_digits(value) >= 4:
            frac_x[count] = round(float(value), 6)
            count += 1
    return count
```

Compute `n_real_frac` before compaction. Then reserve
`high_precision_unique_workspace` and the `high_precision_unique` output at
the full `n` upper bound before calling `np.unique(frac_x[:count])`; validate
the actual output bytes, read `distinct_hp = len(high_precision_unique)`, and
release both leases immediately. This preserves the previous scalar
`round(float(value), 6)` semantics without retaining one Python object per
eligible row:

```python
high_precision_count = _compact_high_precision_fractions(
    frac_x, hp_rows
)
high_precision_workspace = candidate.reserve(
    "high_precision_unique_workspace",
    4 * n,
)
high_precision_unique, high_precision_unique_lease = (
    candidate.allocate(
        "high_precision_unique",
        n,
        lambda: np.unique(frac_x[:high_precision_count]),
    )
)
distinct_hp = len(high_precision_unique)
del high_precision_unique
candidate.release(high_precision_unique_lease)
candidate.release(high_precision_workspace)
```

Hold
`integer_shift_workspace` while `integer_shift_close()` constructs its
temporaries, and reserve `diff_is_int` for that returned boolean array before
the call. Reserve unique outputs and their workspaces before every `np.unique`
call. Reuse `relation_close_workspace` only after releasing the previous
call's lease.

For the affine branch, hold `fitted_build_workspace` only while constructing
the reserved `fitted` array, then release it through `candidate.release()`.
Before calling `relation_close(y, fitted)`, separately reserve the full
`fitted_relation_workspace = 12 * n`; do not reuse the smaller construction
workspace for comparison. Release the relation workspace immediately after
the scalar comparison result is known.

Reserve each workspace immediately before the NumPy/SciPy operation, release it
immediately after the operation, and release no-longer-needed arrays early.
Route pair findings to `candidate.offer()`. Replace each existing normal
pair-loop `continue` with `return` from `run_pair_candidate()`:
`__exit__()` then commits the buffered findings, increments
`candidates_examined` exactly once, and releases every remaining lease after
the helper's proportional locals are gone.

If `candidate.reserve()` or `candidate.allocate()` cannot reserve state, it
marks the candidate rejected and raises `_DenseCandidateRejected`. The context
finalizer suppresses only that private exception, discards buffered findings,
does not increment `candidates_examined`, and releases every remaining live
lease. Immediately after the `with`, use `if candidate.rejected: break` to stop
the outer pair loop. Every early successful release goes through
`candidate.release(lease)` after deleting the corresponding array and all of
its views, so the live registry and `StateBudget` change together. Do not add
manual `commit()`, `discard()`, `complete_candidate()`, or lease-finalizer
paths around individual branches.

Do not alter the existing identity, offset, ratio, sum, affine, partial-offset,
integer-fraction, or discrete-difference expressions or their finding fields.

- [ ] **Step 7: Migrate the other dense families**

Add `_resources=None` and one `_DenseCandidate` transaction per candidate to:

```python
detect_equal_pairs
detect_arithmetic_progression
detect_within_column_patterns
detect_dispersed_repeats
detect_identical_after_rounding
```

Use these exact source-visit units:

```python
{
    "equal_pairs": 2 * row_count,
    "arithmetic_progression": row_count,
    "within_column": row_count,
    "dispersed_repeats": row_count,
    "identical_after_rounding": row_count * col_count,
}
```

Before row/work admission, each detector builds a local
`state_upper_bounds` dictionary from its complete inventory below and passes
`state_required=sum(state_upper_bounds.values())` to `resources.begin()`.
Use `row_count` as the upper bound for `n`, `m`, `distinct_count`, and
`group_count`; use `cell_count` as the upper bound for `value_count`,
`distinct_count`, and `group_count` in the block-wide rounding detector.
`equal_pairs` passes `state_required=0` because its exact-value scan is
scalar and state-free. These dictionaries live inside their owning detectors,
not in the orchestrator.

For `equal_pairs`, call `start_candidate()` immediately before
`_numeric_pair_stats()` and keep every normal early `continue` inside its
context. For each array-based family, put every proportional local in a nested
no-argument candidate-scoped closure. It may capture the current candidate and
source indexes, but it must never accept, pass, return, or alias the candidate
or resource session. Use
`start_allocated_candidate()` for the first source-reading allocation so state
is reserved first and work is admitted second:

```text
arithmetic_progression  column
within_column           column
dispersed_repeats       numeric_mask
identical_after_rounding candidate_mask
```

After successful state/work admission, destructure the returned candidate,
first-source lease, and ordered tuple of initial workspace leases. Enter the
candidate before calling the scoped helper, which starts with
`candidate.materialize(first_lease, source_factory)`. The transaction therefore
owns every accepted lease before the source factory can raise. If the candidate
is `None`, stop that detector loop; the admission helper has already released
every pre-reserved lease and returned an empty initial-lease tuple. Replace
every later explicit allocation with `candidate.allocate()`, every workspace
reservation with `candidate.reserve()`, and every normal no-result path with
`return` from the scoped helper. The helper must return before candidate
finalization, and lazy finding builders must not retain proportional arrays.

For `identical_after_rounding`, pass
`initial_reservations=(("candidate_workspace", 3 * cell_count),)` to
`start_allocated_candidate()` for `candidate_mask`. The admission helper
reserves both entries without running a factory and releases them on
source-state or work rejection; after admission the candidate owns both leases
before `candidate.materialize()` invokes the complete candidate-mask factory.
Pass the returned initial-lease tuple as `release_after=initial_leases`, so a
successful materialization releases and unregisters `candidate_workspace`
before returning the mask, while a factory or validation exception leaves both
leases live for the candidate finalizer. This ordering ensures a state
rejection records no unperformed source work, every accepted source pass is
included in `work_examined`, and the candidate remains the sole owner of every
accepted lease.

Inside every candidate transaction, use `candidate.allocate()` for every
explicit proportional array so its factory cannot run before reservation, and
use `candidate.reserve()` for hidden NumPy workspaces. Dense detectors must
never call a family-level reserve, work-admission, candidate-builder, or
completion primitive; those methods are private. Factory execution and output
validation are candidate-owned.
`start_allocated_candidate()` reserves any declared initial workspace and the
first source lease without running a factory. No source factory may run in
that pre-transaction interval. The complete operation inventory is:

**`equal_pairs` — one column-pair candidate**

`_numeric_pair_stats()` is a scalar exact-value pass with only two fixed
eight-value samples. It consumes `2 * row_count` work and needs no proportional
state lease. Do not introduce NumPy arrays solely for accounting.

**`arithmetic_progression` — one column candidate**

```text
column                       row_count
numeric_mask                 state_units_for_nbytes(row_count)
values                       row_count
diffs                        max(0, n - 1)
progression_abs_workspace    n
progression_close_workspace  4 * n
```

Reserve `column` before `col_array`; allocate `numeric_mask` with
`np.isnan(column)` and invert it in place with
`np.logical_not(numeric_mask, out=numeric_mask)`; reserve `values` before
boolean indexing and `diffs` before `np.diff`. Hold the two workspace leases
around `np.max(np.abs(values))` and `np.allclose` respectively.

**`within_column` — one column candidate**

```text
column                row_count
numeric_mask          state_units_for_nbytes(row_count)
values                row_count
rounded               n
frequency_workspace   8 * n
unique                n
counts                n
order                 n
integer_workspace     3 * n
```

Reserve `unique`, `counts`, and `order` at their input-size upper bounds before
`_numpy_frequency_summary()`, then validate actual output bytes. The
`frequency_workspace` lease is simultaneously live around `np.unique` and
`np.lexsort`. As in arithmetic progression, allocate `numeric_mask` with
`np.isnan(column)` and invert that output in place before boolean indexing.

**`dispersed_repeats` — one column candidate**

```text
numeric_mask                state_units_for_nbytes(row_count)
rows                        row_count
values                      row_count
integer_gate_workspace      3 * n
rounded                     n
frequency_workspace         8 * n
unique_all                  n
counts_all                  n
order_all                   n
core_mask                   state_units_for_nbytes(n)
core_rows                   n
core_values                 n
decimal_places              state_units_for_nbytes(m)
precision_gate              state_units_for_nbytes(m)
rounded_core                m
unique_workspace            10 * m
unique_core                 m
first_core                  m
inverse                     m
counts                      m
partition_workspace         m
sort_workspace              3 * m
sorted_positions            m
group_start_workspace       2 * distinct_count
group_starts                distinct_count
group_rows                  group_count
group_diffs                 group_count
group_gaps                  state_units_for_nbytes(group_count)
sample_rounded              m
sample_frequency_workspace  8 * m
sample_unique               m
sample_counts               m
sample_order                m
```

Here `n` is the numeric count, `m` is the post-boundary core count,
`distinct_count = len(counts)`, and `group_count` is the current duplicate
group size. Reserve all three first frequency outputs before the first
`_numpy_frequency_summary()`, all four core unique outputs before the second
`np.unique`, and all three sample outputs before the final frequency summary.
Allocate `precision_gate` before `np.greater_equal(decimal_places, 2)`.
Reserve `group_rows` before advanced indexing, allocate `group_diffs` before
`np.diff(group_rows)`, and allocate `group_gaps` before
`np.greater(group_diffs, 1)`. Release each gate/difference output immediately
after its scalar count or `np.any` result is known.

Build the initial mask and row indexes without proportional temporaries:

```python
candidate, numeric_mask_lease, initial_leases = (
    resources.start_allocated_candidate(
        "numeric_mask",
        state_units_for_nbytes(row_count),
        row_count,
        emit,
    )
)
if candidate is None:
    break
assert initial_leases == ()

def run_column_candidate():
    numeric_mask = candidate.materialize(
        numeric_mask_lease,
        lambda: np.isnan(column),
    )
    np.logical_not(numeric_mask, out=numeric_mask)
    rows, rows_lease = candidate.allocate(
        "rows",
        row_count,
        lambda: np.flatnonzero(numeric_mask),
    )
    rows += r0
    values, values_lease = candidate.allocate(
        "values",
        row_count,
        lambda: column[numeric_mask],
    )

with candidate:
    run_column_candidate()
if candidate.rejected:
    break
```

Build both comparison masks as explicit candidate-owned outputs:

```python
precision_gate, precision_gate_lease = candidate.allocate(
    "precision_gate",
    state_units_for_nbytes(m),
    lambda: np.greater_equal(decimal_places, 2),
)
frac_hi_prec = float(np.count_nonzero(precision_gate)) / m
del precision_gate
candidate.release(precision_gate_lease)

group_diffs, group_diffs_lease = candidate.allocate(
    "group_diffs",
    group_count,
    lambda: np.diff(group_rows),
)
group_gaps, group_gaps_lease = candidate.allocate(
    "group_gaps",
    state_units_for_nbytes(group_count),
    lambda: np.greater(group_diffs, 1),
)
non_adjacent = bool(np.any(group_gaps))
del group_gaps
candidate.release(group_gaps_lease)
del group_diffs
candidate.release(group_diffs_lease)
```

The in-place offset is required; do not use
`np.flatnonzero(numeric_mask) + r0`, which has two simultaneous integer
arrays.

For column-loop families, a normal no-result path uses `return` inside the
scoped helper; after it returns, the `with` body exits normally and the outer
loop continues. A failed `candidate.*` call raises `_DenseCandidateRejected`,
unwinds all nested group loops, and is suppressed only by the candidate
finalizer; `if candidate.rejected: break` immediately after the context then
stops the outer column loop. For the single block-wide rounding candidate, the
same post-context check returns the already completed findings. A normal early
`return findings` from its scoped helper still counts the candidate as
completed. No detector uses `None` to represent both outcomes.

**`identical_after_rounding` — one block candidate**

```text
candidate_workspace        3 * cell_count
candidate_mask             state_units_for_nbytes(cell_count)
bucket_workspace           2 * cell_count
bucket_mask                state_units_for_nbytes(cell_count)
flat_indices               cell_count
values                     cell_count
rounded                    value_count
unique_workspace           10 * value_count
rounded_values             value_count
first_indices              value_count
inverse                    value_count
counts                     value_count
sort_workspace             3 * value_count
sorted_positions           value_count
group_start_workspace      2 * distinct_count
group_starts               distinct_count
group_values               group_count
precise_rounded            group_count
precise_unique_workspace   4 * group_count
precise_values             group_count
```

Start the candidate and atomically retire its initial workspace after the
complete candidate-mask expression succeeds:

```python
candidate, candidate_mask_lease, initial_leases = (
    resources.start_allocated_candidate(
        "candidate_mask",
        state_units_for_nbytes(cell_count),
        cell_count,
        emit,
        initial_reservations=(
            ("candidate_workspace", 3 * cell_count),
        ),
    )
)
if candidate is None:
    return findings
```

The first statement in the no-argument `run_rounding_candidate()` closure is:

```python
candidate_mask = candidate.materialize(
    candidate_mask_lease,
    lambda: (
        ~np.isnan(block) & (np.abs(block) > 1e-9)
    ),
    release_after=initial_leases,
)
```

Migrate the remainder of the existing detector body into that closure using the
complete inventory below, then invoke it through the transaction:

```python
with candidate:
    run_rounding_candidate()
if candidate.rejected:
    return findings
```

Reserve the four `np.unique` outputs before the first call, `sorted_positions`
before `np.argsort`, `flat_indices` before `np.flatnonzero`, and
`group_starts` before
`np.concatenate`/`np.cumsum`. Per-finding copies remain bounded by the existing
five-group and six-example constants, so they are not proportional to the
input.

For every family, route lazy findings through `candidate.offer()`. A normal
empty result and a normal finding both leave the context without setting
`candidate.rejected`, so the finalizer counts the candidate exactly once.
Reservation failure marks rejection and discards the candidate without
overloading `None` as both “no finding” and “resource rejection”; the private
exception exits nested loops immediately. Delete
detector-local `_state_tracker` parameters after the new tests no longer use
them; `_DenseStateTracker` itself remains only for the separate wide-integer
index path.

- [ ] **Step 8: Replace orchestration admission with detector sessions**

Delete `_dense_detector_requirements()` and
`_dense_detector_admission()`.

In `_analyze_numeric_blocks()`, create one session per dense family:

```python
def dense_resources(family):
    return _DenseFamilyResources(
        family=family,
        max_rows=_DENSE_BLOCK_MAX_ROWS,
        work_limit=_DENSE_BLOCK_CELL_WORK_LIMIT,
        state_limit=_DENSE_BLOCK_STATE_CELL_LIMIT,
    )
```

Pass the matching session to each detector. After detection:

```python
dense_results = []
for session in dense_sessions.values():
    result = session.result()
    if result.limits_reached:
        dense_results.append(result)
```

Serialize each result as:

```python
{
    "family": result.family,
    "candidates_total": result.candidates_total,
    "candidates_examined": result.candidates_examined,
    "candidates_skipped": result.candidates_skipped,
    "work_required": result.work_required,
    "work_examined": result.work_examined,
    "work_skipped": result.work_skipped,
    "work_skipped_lower_bound": result.work_skipped_lower_bound,
    "state_required": result.state_required,
    "state_required_lower_bound": result.state_required_lower_bound,
    "peak_state_units": result.peak_state_units,
    "limits_reached": list(result.limits_reached),
}
```

Keep the existing block-level `max_rows`, `cell_work_limit`, and
`state_cell_limit` fields. Preserve detector-level `work_required`,
`work_skipped`, and `state_required` for archived-consumer compatibility;
`state_required` remains the complete detector-declared upper bound even when
row or work admission stops before allocation. `work_skipped_lower_bound`,
`state_required_lower_bound`, and `peak_state_units` are additive. The state
lower bound is the largest accepted-or-rejected simultaneous reservation
attempt, while peak state counts accepted live reservations only. Mark
top-level omission semantics as a lower bound when any family is incomplete.

- [ ] **Step 9: Confirm the RED module-boundary test is now GREEN**

Do not add a second source-inspection test here. The AST regression introduced
before Step 4 must now pass unchanged, proving the implementation removed the
superseded estimators and the family-level factory escape, keeps raw
reserve/work/candidate/completion primitives private, rejects nested state
access plus resource/candidate aliases, later stores or deletes, writable
candidate status, lambda/generator/async deferrals, yielding helpers, and
`with ... as`, protected-name comprehensions, `global`/`nonlocal`, and helper
escape or repetition; requires every resource method call to execute
synchronously in the detector root, every candidate method call to live inside
the unique synchronous no-argument scoped helper, and exactly one direct helper
call inside the unique root-owned candidate context; permits only the documented
candidate methods plus the read-only `candidate.rejected`; and allows only the
exact `begin` plus scalar/array candidate-admission calls documented for each
detector.

- [ ] **Step 10: Run dense tests under strict warnings**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_resource_lifetime.py \
  tests/test_relations_tolerance.py \
  tests/test_detector_coverage.py \
  tests/test_module_boundaries.py -k \
  'dense or relation or equal_pairs or within_column or dispersed or rounding or resource'
```

Expected: all selected tests pass.

- [ ] **Step 11: Run all affected detector suites**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_sheet.py \
  tests/test_relations_tolerance.py \
  tests/test_relations_flood.py \
  tests/test_progression_reuse.py \
  tests/test_within_col_prefilter.py \
  tests/test_detection_recall_e2e.py
```

Expected: all tests pass with unchanged finding substance.

- [ ] **Step 12: Commit Task 3**

```bash
git add src/paperconan/_audit.py \
  tests/test_resource_lifetime.py \
  tests/test_relations_tolerance.py \
  tests/test_detector_coverage.py \
  tests/test_module_boundaries.py
git commit -m "fix: move dense budgets into detectors"
```

---

### Task 4: Make Pair And Axis Detectors Own Exact Feasible Work

**Files:**

- Modify: `src/paperconan/_audit.py:2729-3135`
- Modify: `src/paperconan/_audit.py:3437-3475`
- Modify: `src/paperconan/_audit.py:3550-3835`
- Modify: `src/paperconan/_audit.py:4687-4820`
- Modify: `tests/test_collisions.py`
- Modify: `tests/test_detector_coverage.py`
- Modify: `tests/test_module_boundaries.py`

**Interfaces:**

- Consumes:
  - `StateBudget` and `state_units_for_nbytes`.
  - Existing `CrossSheetWorkBudget`.
- Produces:
  - `_cross_sheet_pair_stats(..., budget=None, with_coverage=False)` with
    detector-owned pair/value admission.
  - `_detect_decimal_tail_reuse_for_pair(...)` with detector-owned pair/value
    admission and exact early-exit accounting.
  - `_axis_columns(grids, recur_min=3, *, position_keys=None,
    budget=None, with_coverage=False, _state_limit=None)`
  - Exact `axis_loading_visits`, `axis_grouping_visits`,
    `axis_progression_visits`, `axis_fingerprint_visits`,
    `axis_recurrence_order_visits`, `axis_recurrence_group_visits`,
    `axis_recurrence_comparison_visits`, `axis_recurrence_mark_visits`, and
    `axis_output_visits`.
  - `axis_work_skipped_lower_bound` and
    `axis_work_skipped_is_lower_bound`.
  - `participating_*` position-family counts and
    `recurrence_support_*` support counts in direct coverage.
  - `axis_state_unit_limit` and `axis_peak_state_units` coverage.

- [ ] **Step 1: Add exact-pass, pair-ownership, and irrelevant-grid regressions**

Append to `tests/test_collisions.py`:

```python
class _CountingGrid(dict):
    def __init__(self, values):
        super().__init__(values)
        self.item_visits = 0

    def items(self):
        for item in super().items():
            self.item_visits += 1
            yield item


def test_axis_work_matches_concrete_passes_for_feasible_grids_only():
    irrelevant = _CountingGrid({
        (row, 0): row + 0.125 for row in range(3)
    })
    recurrence_support = _CountingGrid({
        (row, 0): row + 20.625 for row in range(5)
    })
    left = _CountingGrid({
        (row, 0): row + 0.125 for row in range(8)
    })
    right = _CountingGrid({
        (row, 0): row + 10.375 for row in range(8)
    })
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        {
            ("irrelevant.csv", "small"): irrelevant,
            ("support.csv", "support"): recurrence_support,
            ("left.csv", "Figure 1"): left,
            ("right.csv", "Figure 2"): right,
        },
        budget=budget,
        with_coverage=True,
    )

    assert irrelevant.item_visits == 0
    assert recurrence_support.item_visits == len(
        recurrence_support
    )
    assert left.item_visits == len(left)
    assert right.item_visits == len(right)
    assert {
        name: coverage[name]
        for name in (
            "participating_summaries",
            "participating_cells",
            "recurrence_support_summaries",
            "recurrence_support_cells",
            "axis_loading_visits",
            "axis_grouping_visits",
            "axis_progression_visits",
            "axis_fingerprint_visits",
            "axis_recurrence_order_visits",
            "axis_recurrence_group_visits",
            "axis_recurrence_comparison_visits",
            "axis_recurrence_mark_visits",
            "axis_output_visits",
            "axis_value_visits",
            "axis_context_available",
        )
    } == {
        "participating_summaries": 2,
        "participating_cells": 16,
        "recurrence_support_summaries": 3,
        "recurrence_support_cells": 21,
        "axis_loading_visits": 21,
        "axis_grouping_visits": 21,
        "axis_progression_visits": 16,
        "axis_fingerprint_visits": 21,
        "axis_recurrence_order_visits": 3,
        "axis_recurrence_group_visits": 3,
        "axis_recurrence_comparison_visits": 3,
        "axis_recurrence_mark_visits": 0,
        "axis_output_visits": 3,
        "axis_value_visits": 91,
        "axis_context_available": True,
    }
    assert coverage["axis_state_unit_limit"] == (
        audit._AXIS_STATE_UNITS_PER_CELL * 21
    )
    assert 0 < coverage["axis_peak_state_units"] <= (
        coverage["axis_state_unit_limit"]
    )
    assert budget.values_examined == 91
    assert set(axis) == {
        ("left.csv", "Figure 1"),
        ("right.csv", "Figure 2"),
    }


def test_axis_fingerprint_preserves_signed_zero_set_equivalence():
    def build_grids(zeros):
        return {
            (f"sheet-{index}.csv", f"Figure {index}"):
                _CountingGrid({
                    (row, 0): value
                    for row, value in enumerate((
                        zero,
                        2.25,
                        7.5,
                        4.125,
                        2.25,
                        7.5,
                    ))
                })
            for index, zero in enumerate(zeros)
        }

    baseline_grids = build_grids((0.0, 0.0, 0.0))
    signed_grids = build_grids((0.0, -0.0, 0.0))
    assert len({
        frozenset(grid.values())
        for grid in signed_grids.values()
    }) == 1

    baseline_axis, baseline_coverage = audit._axis_columns(
        baseline_grids, with_coverage=True
    )
    signed_axis, signed_coverage = audit._axis_columns(
        signed_grids, with_coverage=True
    )

    expected_axis = {key: {0} for key in signed_grids}
    assert baseline_axis == expected_axis
    assert signed_axis == expected_axis
    assert signed_coverage == baseline_coverage
    assert signed_coverage["axis_recurrence_mark_visits"] == 3
    assert signed_coverage["axis_context_available"] is True
    for grids in (baseline_grids, signed_grids):
        assert all(
            grid.item_visits == len(grid)
            for grid in grids.values()
        )


def _axis_finalization_grids():
    axis_values = (
        1.125,
        4.875,
        2.250,
        7.625,
        1.125,
        4.875,
    )
    support = {
        (row, 0): value
        for row, value in enumerate(axis_values[:4])
    }
    left = {}
    right = {}
    for row, value in enumerate(axis_values):
        left[(row, 0)] = value
        right[(row, 0)] = value
        left[(row, 1)] = 100 + row + 0.125
        right[(row, 1)] = 200 + row + 0.375
    return {
        ("support.csv", "support"): support,
        ("left.csv", "Figure 1"): left,
        ("right.csv", "Figure 2"): right,
    }


def test_axis_compact_finalization_matches_concrete_processing(
    monkeypatch
):
    comparison_calls = 0
    progression_cells = 0
    original_equal = audit._axis_payload_equal
    original_progression = audit._is_axis_progression_arrays

    def tracked_equal(*args, **kwargs):
        nonlocal comparison_calls
        comparison_calls += 1
        return original_equal(*args, **kwargs)

    def tracked_progression(rows, values, **kwargs):
        nonlocal progression_cells
        progression_cells += len(values)
        return original_progression(rows, values, **kwargs)

    monkeypatch.setattr(
        audit, "_axis_payload_equal", tracked_equal
    )
    monkeypatch.setattr(
        audit,
        "_is_axis_progression_arrays",
        tracked_progression,
    )

    _axis, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )

    assert progression_cells == coverage[
        "axis_progression_visits"
    ]
    assert comparison_calls == coverage[
        "axis_recurrence_comparison_visits"
    ]
    assert coverage["axis_recurrence_order_visits"] == 5
    assert coverage["axis_recurrence_group_visits"] == 5
    assert coverage["axis_recurrence_comparison_visits"] == 5
    assert coverage["axis_recurrence_mark_visits"] == 3
    assert coverage["axis_output_visits"] == 5
    assert coverage["axis_work_skipped_lower_bound"] == 0
    assert coverage["axis_work_skipped_is_lower_bound"] is False


def test_axis_comparison_budget_stops_before_uncharged_compare(
    monkeypatch
):
    baseline, baseline_coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )
    comparison_calls = 0
    original_equal = audit._axis_payload_equal

    def tracked_equal(*args, **kwargs):
        nonlocal comparison_calls
        comparison_calls += 1
        return original_equal(*args, **kwargs)

    monkeypatch.setattr(
        audit, "_axis_payload_equal", tracked_equal
    )
    before_comparisons = (
        baseline_coverage["axis_value_visits"]
        - baseline_coverage["axis_recurrence_comparison_visits"]
        - baseline_coverage["axis_recurrence_mark_visits"]
        - baseline_coverage["axis_output_visits"]
    )
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=before_comparisons + 2,
        tail_match_limit=100,
        finding_limit=100,
    )

    limited, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        budget=budget,
        with_coverage=True,
    )

    assert baseline
    assert limited == {}
    assert comparison_calls == 2
    assert coverage["axis_recurrence_comparison_visits"] == 2
    assert coverage["axis_recurrence_mark_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_work_skipped_lower_bound"] > 0
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == before_comparisons + 2


def test_axis_output_budget_rejects_before_mapping_traversal():
    _baseline, baseline_coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )
    output_visits = baseline_coverage["axis_output_visits"]
    before_output = (
        baseline_coverage["axis_value_visits"] - output_visits
    )
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=before_output + output_visits - 1,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        budget=budget,
        with_coverage=True,
    )

    assert axis == {}
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_work_skipped_lower_bound"] == output_visits
    assert coverage["axis_work_skipped_is_lower_bound"] is False
    assert budget.values_examined == before_output


def test_axis_fingerprint_keeps_existing_four_unique_value_floor():
    grids = {
        (f"{index}.csv", f"Figure {index}"): {
            (0, 0): 1.125,
            (2, 0): 4.875,
            (5, 0): 2.250,
            (0, 1): 10.125 + index,
            (2, 1): 14.875 + index,
            (5, 1): 12.250 + index,
        }
        for index in range(3)
    }

    axis = audit._axis_columns(grids)

    assert all(0 not in columns for columns in axis.values())


def test_four_cell_recurrence_support_preserves_axis_downgrade():
    axis_values = (
        1.125,
        4.875,
        2.250,
        7.625,
        1.125,
        4.875,
    )
    support = _CountingGrid({
        (row, 0): value
        for row, value in enumerate(axis_values[:4])
    })
    left = {}
    right = {}
    for row, value in enumerate(axis_values):
        left[(row, 0)] = value
        right[(row, 0)] = value
        left[(row, 1)] = 100 + row + 0.125
        right[(row, 1)] = 200 + row + 0.375
    grids = {
        ("support.csv", "support"): support,
        ("left.csv", "Figure 1"): left,
        ("right.csv", "Figure 2"): right,
    }

    findings = detect_collisions(grids)
    finding = _find(
        findings,
        "cross_sheet_position_identical",
    )

    assert finding is not None
    assert {
        finding["file_a"],
        finding["file_b"],
    } == {"left.csv", "right.csv"}
    assert finding["axis_overlap"] is True
    assert finding["severity"] == "low"
    assert support.item_visits == len(support)


@pytest.mark.parametrize(
    "helper_name",
    [
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ],
)
@pytest.mark.parametrize("blocked_limit", ["pair", "value"])
def test_pair_helpers_reject_before_source_grid_access(
    helper_name, blocked_limit
):
    left = _VisitGrid(_sized_grid(9))
    right = _VisitGrid(_sized_grid(8, 100))
    candidate_value_count = len(left) + len(right)
    budget = CrossSheetWorkBudget(
        pair_limit=0 if blocked_limit == "pair" else 1,
        value_limit=(
            candidate_value_count - 1
            if blocked_limit == "value"
            else candidate_value_count
        ),
        tail_match_limit=100,
        finding_limit=100,
    )

    result, coverage = getattr(audit, helper_name)(
        left,
        right,
        budget=budget,
        with_coverage=True,
    )

    assert result is None
    assert coverage["pair_admitted"] is False
    assert coverage["candidate_value_count"] == candidate_value_count
    assert coverage["value_visits"] == 0
    assert left.value_visits == 0
    assert right.value_visits == 0
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 0
    assert metadata["pairs_skipped"] == 1
    assert metadata["values_examined"] == 0
    assert metadata["values_skipped"] == candidate_value_count
    assert metadata["limits_reached"] == [blocked_limit]


@pytest.mark.parametrize(
    "helper_name",
    [
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ],
)
def test_pair_helpers_own_exact_completed_work(helper_name):
    left = _VisitGrid(_sized_grid(9))
    right = _VisitGrid(_sized_grid(8, 100))
    candidate_value_count = len(left) + len(right)
    budget = CrossSheetWorkBudget(
        pair_limit=1,
        value_limit=candidate_value_count,
        tail_match_limit=100,
        finding_limit=100,
    )

    _result, coverage = getattr(audit, helper_name)(
        left,
        right,
        budget=budget,
        with_coverage=True,
    )

    assert coverage["pair_admitted"] is True
    assert coverage["candidate_value_count"] == candidate_value_count
    assert coverage["value_visits"] == candidate_value_count
    assert left.value_visits == len(left)
    assert right.value_visits == len(right)
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 0
    assert metadata["values_examined"] == candidate_value_count
    assert metadata["values_skipped"] == 0
```

In `test_decimal_tail_match_state_stops_before_limit_is_exceeded()`, wrap the
completed input dictionaries before constructing the budget:

```python
ga = _VisitGrid(ga)
gb = _VisitGrid(gb)
```

Then append:

```python
assert metadata["pairs_examined"] == 1
assert metadata["pairs_skipped"] == 0
assert metadata["values_examined"] == (
    ga.value_visits + gb.value_visits
)
assert metadata["values_skipped"] == (
    len(ga) + len(gb) - metadata["values_examined"]
)
```

This covers the decimal-tail early exit caused by the retained-match cap: only
source cells actually visited are examined work, and the unvisited part of the
already admitted candidate is exact skipped work.

Append to `tests/test_module_boundaries.py`:

```python
def test_cross_sheet_pair_budget_is_owned_by_pair_helpers():
    import inspect

    import paperconan._audit as audit

    caller_source = inspect.getsource(audit.detect_collisions)
    assert ".begin_pair(" not in caller_source
    assert ".record_values(" not in caller_source
    assert "collision_value_work" not in caller_source
    assert "tail_value_work" not in caller_source

    for name in (
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ):
        helper_source = inspect.getsource(getattr(audit, name))
        assert ".begin_pair(" in helper_source
        assert ".record_values(" in helper_source


def test_axis_compact_passes_have_detector_owned_admission():
    import inspect

    import paperconan._audit as audit

    source = inspect.getsource(audit._axis_columns)
    for stage in (
        "recurrence_order",
        "recurrence_group",
        "output",
    ):
        assert f'admit_stage("{stage}"' in source
    for stage in (
        "recurrence_comparison",
        "recurrence_mark",
    ):
        assert f'admit_dynamic_stage("{stage}"' in source

    progression_source = inspect.getsource(
        audit._is_axis_progression_arrays
    )
    assert progression_source.count(
        "for index in range(len(values)):"
    ) == 1
```

- [ ] **Step 2: Add stage-boundary regressions**

Append to `tests/test_collisions.py`:

```python
@pytest.mark.parametrize(
    (
        "value_limit",
        "expected_stage_visits",
        "expected_grid_visits",
        "expected_examined",
        "expected_skipped",
    ),
    [
        (7, (0, 0, 0, 0), 0, 0, 48),
        (63, (16, 16, 16, 8), 16, 56, 14),
    ],
)
def test_axis_work_stops_before_the_rejected_stage(
    value_limit,
    expected_stage_visits,
    expected_grid_visits,
    expected_examined,
    expected_skipped,
):
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=value_limit,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        grids, budget=budget, with_coverage=True
    )

    assert axis == {}
    assert sum(grid.item_visits for grid in grids.values()) == (
        expected_grid_visits
    )
    assert coverage["axis_context_available"] is False
    assert coverage["axis_state_unit_limit"] == (
        audit._AXIS_STATE_UNITS_PER_CELL * 16
    )
    assert coverage["axis_peak_state_units"] <= (
        coverage["axis_state_unit_limit"]
    )
    assert (
        coverage["axis_loading_visits"],
        coverage["axis_grouping_visits"],
        coverage["axis_progression_visits"],
        coverage["axis_fingerprint_visits"],
    ) == expected_stage_visits
    assert coverage["axis_recurrence_order_visits"] == 0
    assert coverage["axis_recurrence_group_visits"] == 0
    assert coverage["axis_recurrence_comparison_visits"] == 0
    assert coverage["axis_recurrence_mark_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_value_visits"] == sum(expected_stage_visits)
    assert coverage["axis_work_skipped_lower_bound"] == (
        expected_skipped
    )
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == expected_examined
    assert budget.values_skipped == expected_skipped


def test_axis_state_rejection_at_fingerprint_after_grouping_counts_known_work(
    monkeypatch,
):
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )
    original_try_reserve = audit.StateBudget.try_reserve
    rejected_names = []

    def reject_fingerprint_state(state, name, units):
        if name == "axis_unique_values":
            rejected_names.append(name)
            return None
        return original_try_reserve(state, name, units)

    monkeypatch.setattr(
        audit.StateBudget,
        "try_reserve",
        reject_fingerprint_state,
    )

    axis, coverage = audit._axis_columns(
        grids,
        budget=budget,
        with_coverage=True,
    )

    assert axis == {}
    assert rejected_names == ["axis_unique_values"]
    assert sum(grid.item_visits for grid in grids.values()) == 8
    assert coverage["axis_context_available"] is False
    assert (
        coverage["axis_loading_visits"],
        coverage["axis_grouping_visits"],
        coverage["axis_progression_visits"],
        coverage["axis_fingerprint_visits"],
    ) == (8, 8, 8, 0)
    assert coverage["axis_recurrence_order_visits"] == 0
    assert coverage["axis_recurrence_group_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_value_visits"] == 24
    assert coverage["axis_work_skipped_lower_bound"] == 35
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == 24
    assert budget.values_skipped == 35


def test_axis_zero_support_has_zero_state_budget():
    grids = {
        ("a.csv", "Figure 1"): {
            (row, 0): row + 0.125 for row in range(3)
        },
        ("b.csv", "Figure 2"): {
            (row, 0): row + 10.375 for row in range(3)
        },
    }

    axis, coverage = audit._axis_columns(
        grids,
        with_coverage=True,
    )

    assert axis == {}
    assert coverage["recurrence_support_cells"] == 0
    assert coverage["axis_value_visits"] == 0
    assert coverage["axis_state_unit_limit"] == 0
    assert coverage["axis_peak_state_units"] == 0


def test_axis_state_rejection_precedes_grid_loading():
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        grids,
        budget=budget,
        with_coverage=True,
        _state_limit=0,
    )

    assert axis == {}
    assert sum(grid.item_visits for grid in grids.values()) == 0
    assert coverage["axis_context_available"] is False
    assert coverage["axis_value_visits"] == 0
    assert coverage["axis_state_unit_limit"] == 0
    assert coverage["axis_peak_state_units"] == 0
    assert coverage["axis_work_skipped_lower_bound"] == 48
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == 0
    assert budget.values_skipped == 48
    assert budget.limitation_metadata()["limits_reached"] == [
        "axis"
    ]


def test_axis_state_multiplier_covers_many_column_worst_case(
    monkeypatch
):
    grids = {
        (f"{sheet}.csv", f"Figure {sheet}"): {
            (row, column): row + 0.125
            for row in range(4)
            for column in range(64)
        }
        for sheet in range(3)
    }
    expected_names = {
        "axis_column_table",
        "axis_fingerprint_payloads",
        "axis_records",
        "axis_order",
        "axis_sort_workspace",
        "axis_ordered_records",
        "axis_unique_workspace",
        "axis_unique_values",
        "axis_canonical_values",
        "axis_fingerprint_temp",
        "axis_fingerprint_order",
        "axis_fingerprint_order_workspace",
        "axis_output_capacity",
    }
    seen_names = set()
    states = []
    original = audit.StateBudget.try_reserve

    def tracked_reserve(state, name, units):
        if state not in states:
            states.append(state)
        seen_names.add(name.split(":", 1)[0])
        return original(state, name, units)

    monkeypatch.setattr(
        audit.StateBudget, "try_reserve", tracked_reserve
    )
    baseline, baseline_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=10_000_000,
    )
    required = baseline_coverage["axis_peak_state_units"]
    cell_count = sum(len(grid) for grid in grids.values())
    default_limit = audit._AXIS_STATE_UNITS_PER_CELL * cell_count

    assert expected_names <= seen_names
    assert baseline_coverage["axis_state_unit_limit"] == 10_000_000
    assert 0 < required <= default_limit
    assert all(state.live_units == 0 for state in states)

    limited, limited_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=required - 1,
    )
    assert limited == {}
    assert limited_coverage["axis_context_available"] is False
    assert limited_coverage["axis_state_unit_limit"] == required - 1
    assert limited_coverage["axis_peak_state_units"] <= required - 1

    exact, exact_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=required,
    )
    assert exact == baseline
    assert exact_coverage["axis_context_available"] is True
    assert exact_coverage["axis_state_unit_limit"] == required
    assert exact_coverage["axis_peak_state_units"] == required

    default, default_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
    )
    assert default == baseline
    assert default_coverage["axis_context_available"] is True
    assert default_coverage["axis_state_unit_limit"] == default_limit
    assert default_coverage["axis_peak_state_units"] == required
    assert all(state.live_units == 0 for state in states)
```

Update the five existing work-accounting tests that monkeypatch
`_axis_columns()` at lines 230, 258, 297, 329, and 368. Delete those
monkeypatches so the tests exercise the real detector-owned axis passes.
Remove the now-unused `monkeypatch` parameter from
`test_cross_sheet_value_work_counts_known_passes_exactly`,
`test_pair_value_budget_stops_before_any_uncharged_pass`,
`test_pair_stop_reports_exact_remaining_family_and_value_work`, and
`test_impossible_families_do_not_displace_later_viable_pair`;
`test_pair_setup_is_linear_and_remaining_work_is_exact` still uses it for
`combinations`.

In `test_cross_sheet_value_work_counts_known_passes_exactly`, include the four
single-record finalization passes for each of its two column records:

```python
assert metadata["values_examined"] == (
    4 * (9 + 8)
    + 4 * 2
    + (9 + 8)
    + (9 + 8)
)
```

In `test_pair_value_budget_stops_before_any_uncharged_pass`, replace the two
source-visit assertions with:

```python
assert left.value_visits == len(left)
assert right.value_visits == len(right)
```

Those visits are the admitted axis-loading pass; the rejected pair family adds
none. Set that test's axis work to:

```python
axis_work = 4 * (len(left) + len(right)) + 4 * 2
```

The additional term is ordering, recurrence grouping, one exact singleton
comparison, and output traversal for each of two compact column records.

In `test_pair_stop_reports_exact_remaining_family_and_value_work`, replace the
examined-work assertion with:

```python
assert metadata["values_examined"] == 4 * 23 + 4 * 3 + 14
```

In `test_impossible_families_do_not_displace_later_viable_pair`, replace the
old all-grid axis expectation with:

```python
assert metadata["values_examined"] == (
    3 * (5 + 5 + 2 * len(viable))
    + 2 * len(viable)
    + 4 * 4
    + 16
)
```

The two five-cell grids cannot form a positional pair, but each can still
provide a four-value recurrence fingerprint. They therefore consume loading,
grouping, and fingerprint work but no progression work or pair work. Keep the
other exact-work assertions unchanged.

In `test_pair_setup_is_linear_and_remaining_work_is_exact`, include four
compact finalization visits per one-column summary:

```python
assert metadata["values_examined"] == (
    4 * summary_count * grid_size
    + 4 * summary_count
    + examined_work
)
```

- [ ] **Step 3: Run the pair/axis ownership tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_collisions.py tests/test_module_boundaries.py -k \
  'axis_work_matches or axis_fingerprint_preserves_signed_zero or axis_compact or axis_comparison_budget or axis_output_budget or axis_work_stops or axis_zero_support or axis_state_rejection or axis_state_multiplier or four_unique_value_floor or recurrence_support or pair_helpers or pair_budget_is_owned or cross_sheet_value_work_counts_known_passes_exactly or pair_value_budget_stops_before_any_uncharged_pass or pair_stop_reports_exact_remaining_family_and_value_work or impossible_families_do_not_displace_later_viable_pair or pair_setup_is_linear_and_remaining_work_is_exact'
```

Expected: failures because `_axis_columns()` has no budget/coverage interface,
does not distinguish irrelevant sub-four-cell grids from four/five-cell
recurrence support, and the caller still charges a fixed multiplier. Compact
progression/finalization passes have no admission or counters. Pair helpers do
not yet own pair/value admission or concrete visit recording, and
`detect_collisions()` still contains the caller-side predictions. The
four-unique-value test is initially green and guards the compact replacement
against changing existing classification behavior.

- [ ] **Step 4: Add explicit feasible-key selection**

In `src/paperconan/_audit.py`, add:

```python
def _position_family_keys(grids):
    keys = tuple(
        key for key, grid in grids.items()
        if len(grid) >= _POSITION_VALUE_MIN_CELLS
    )
    return keys if len(keys) >= 2 else ()
```

Compute this tuple once in `detect_collisions()`, use it directly for
positional/value candidate generation, and pass it to `_axis_columns()` as
`position_keys`. Direct `_axis_columns()` calls derive the same tuple when that
argument is omitted. Inside axis classification, separately derive
`support_keys`: when at least two position keys exist, include every retained
grid with at least `_AXIS_FINGERPRINT_MIN_UNIQUE` cells. A four- or five-cell
grid cannot form a positional pair but can preserve the existing third-sheet
recurrence count. Do not build an intermediate `dict` of either key family.
Keep decimal-tail eligibility separate at `_DECIMAL_TAIL_MIN_CELLS`.

Replace the current `keys` list and caller-side `axis_value_count` precharge
with:

```python
position_keys = _position_family_keys(grids)
sizes = tuple(len(grids[key]) for key in position_keys)
candidate_ledger = _CrossSheetCandidateLedger.from_sizes(sizes)
axis_cols = _axis_columns(
    grids,
    position_keys=position_keys,
    budget=budget,
)
```

Change the existing pair loop iterable from `combinations(keys, 2)` to
`combinations(position_keys, 2)`. Step 7 replaces the caller-side work
prediction inside that loop; no caller `begin_pair()` or `record_values()` call
may remain after that migration.

- [ ] **Step 5: Replace Python cell retention with compact axis records**

Add:

```python
_AXIS_RECORD_DTYPE = np.dtype([
    ("column", np.int64),
    ("row", np.int64),
    ("value", np.float64),
])
_AXIS_COLUMN_DTYPE = np.dtype([
    ("summary_index", np.int64),
    ("column", np.int64),
    ("cell_count", np.int64),
    ("is_output", np.bool_),
    ("is_progression", np.bool_),
    ("fingerprint_offset", np.int64),
    ("fingerprint_nbytes", np.int64),
    ("fingerprint_hash", "S32"),
    ("is_recurring", np.bool_),
])
_AXIS_PROGRESSION_MIN_CELLS = 4
_AXIS_FINGERPRINT_MIN_UNIQUE = 4
_AXIS_OUTPUT_COLUMN_UNITS = 20
_AXIS_OUTPUT_SUMMARY_UNITS = 32
_AXIS_STATE_UNITS_PER_CELL = 64
```

The fixed multiplier has explicit headroom. Per recurrence-support cell, the
cross-summary table, payload, and output reservations consume at most
`10 + 1 + 20 + 32 / 6 < 37` units. Position summaries have at least six cells,
and support cells are a superset of position cells, so the per-output-summary
term remains bounded by `32 / 6`. The largest per-summary transient is at most
11 units per cell for either records/order/sort/ordered state or
ordered/unique/workspace/canonical/bytes state. Recurrence ordering needs at
most `1 + 4 = 5` units per column, and column count cannot exceed cell count.
Therefore every simultaneous peak is below 48 units per support cell;
64 leaves deterministic margin without weakening the existing retained-grid
limit.

After `position_keys`, `support_keys`, `position_cell_count`, and
`support_cell_count` are known, reserve all cross-summary column metadata,
canonical fingerprint payload bytes, and final Python output capacity before
any per-column result grows:

```python
column_table_units = state_units_for_nbytes(
    support_cell_count * _AXIS_COLUMN_DTYPE.itemsize
)
column_table_lease = reserve_axis_state(
    "axis_column_table",
    column_table_units,
)
fingerprint_payload_lease = reserve_axis_state(
    "axis_fingerprint_payloads",
    support_cell_count,
)
output_capacity_lease = reserve_axis_state(
    "axis_output_capacity",
    (
        _AXIS_OUTPUT_COLUMN_UNITS * support_cell_count
        + _AXIS_OUTPUT_SUMMARY_UNITS * len(position_keys)
    ),
)
if any(
    lease is None
    for lease in (
        column_table_lease,
        fingerprint_payload_lease,
        output_capacity_lease,
    )
):
    return unavailable_result()

column_table = np.zeros(
    support_cell_count,
    dtype=_AXIS_COLUMN_DTYPE,
)
column_table_lease.validate_nbytes(column_table.nbytes)
fingerprint_payload = bytearray(
    support_cell_count * np.dtype("<f8").itemsize
)
fingerprint_payload_lease.validate_nbytes(
    len(fingerprint_payload)
)
column_count = 0
fingerprint_bytes_used = 0
```

`column_table` replaces the proportional `progression` and `fingerprints`
dictionaries. The single payload lease replaces `fingerprint_leases`; the
sum of canonical fingerprint byte lengths cannot exceed
`8 * support_cell_count`
because columns partition each support summary's retained cells.

For each recurrence-support summary, reserve the complete loading allocation
before admitting loading work, then reserve every grouping allocation before
admitting grouping work:

```python
summary_cells = len(grid)
record_units = state_units_for_nbytes(
    summary_cells * _AXIS_RECORD_DTYPE.itemsize
)
record_lease = reserve_axis_state(
    "axis_records",
    record_units,
)
if record_lease is None:
    return unavailable_result()
if not admit_stage("loading", summary_cells):
    return unavailable_result()

records = np.empty(summary_cells, dtype=_AXIS_RECORD_DTYPE)
record_lease.validate_nbytes(records.nbytes)
for index, ((row, column), value) in enumerate(grid.items()):
    canonical_value = 0.0 if value == 0.0 else value
    records[index] = (column, row, canonical_value)

order_lease = reserve_axis_state(
    "axis_order",
    summary_cells,
)
workspace_lease = reserve_axis_state(
    "axis_sort_workspace",
    4 * summary_cells,
)
ordered_lease = reserve_axis_state(
    "axis_ordered_records",
    record_units,
)
leases = [order_lease, workspace_lease, ordered_lease]
if any(lease is None for lease in leases):
    return unavailable_result()
if not admit_stage("grouping", summary_cells):
    return unavailable_result()

order = np.lexsort((records["row"], records["column"]))
order_lease.validate_nbytes(order.nbytes)
ordered = records[order]
ordered_lease.validate_nbytes(ordered.nbytes)
release_axis_state(record_lease)
release_axis_state(order_lease)
release_axis_state(workspace_lease)

column_entry_start = column_count
summary_progression_cells = 0
start = 0
while start < len(ordered):
    column = int(ordered["column"][start])
    stop = start + 1
    while (
        stop < len(ordered)
        and int(ordered["column"][stop]) == column
    ):
        stop += 1
    if column_count >= len(column_table):
        raise AssertionError("axis column table overflow")
    cell_count = stop - start
    column_table["summary_index"][column_count] = summary_index
    column_table["column"][column_count] = column
    column_table["cell_count"][column_count] = cell_count
    column_table["is_output"][column_count] = (
        key in position_key_set
    )
    if (
        key in position_key_set
        and cell_count >= _AXIS_PROGRESSION_MIN_CELLS
    ):
        summary_progression_cells += cell_count
    column_count += 1
    start = stop
column_entry_stop = column_count
summary_column_count = column_entry_stop - column_entry_start
add_fixed_work(3 * summary_column_count)
```

Validate actual array bytes, keep only `ordered_lease` through the progression
and fingerprint stages, and release it before the next summary. A state
rejection reaches `unavailable_result()` before the corresponding stage is
admitted, so reported work never includes an allocation-blocked pass. Reuse
the same fixed reservation names after release; do not append summary or column
identifiers, which would make `StateBudget.seen_names` grow with the corpus.

Use a compact array progression helper without constructing Python
`(row, value)` tuples:

```python
def _is_axis_progression_arrays(
    rows,
    values,
    *,
    min_n=4,
    rel_tol=1e-4,
    geo_tol=1e-3,
    with_coverage=False,
):
    value_visits = 0

    def finish(result):
        coverage = {"value_visits": value_visits}
        return (result, coverage) if with_coverage else result

    if len(values) < min_n:
        return finish(False)
    first_row = int(rows[0])
    last_row = int(rows[-1])
    first_value = float(values[0])
    last_value = float(values[-1])
    span = last_row - first_row
    if span <= 0:
        return finish(False)

    scale = 0.0
    max_arithmetic_error = 0.0
    max_geometric_error = 0.0
    first_positive = first_value > 0
    step = (last_value - first_value) / span
    geometric = (
        first_value != 0
        and last_value != 0
        and (last_value > 0) == first_positive
    )
    if geometric:
        log_first = math.log(abs(first_value))
        log_step = (
            math.log(abs(last_value)) - log_first
        ) / span
    else:
        log_first = 0.0
        log_step = 0.0

    for index in range(len(values)):
        value_visits += 1
        row = int(rows[index])
        value = float(values[index])
        scale = max(scale, abs(value))
        max_arithmetic_error = max(
            max_arithmetic_error,
            abs(
                value - (
                    first_value + step * (row - first_row)
                )
            )
        )
        if geometric:
            if value == 0 or (value > 0) != first_positive:
                geometric = False
            else:
                max_geometric_error = max(
                    max_geometric_error,
                    abs(
                        math.log(abs(value))
                        - (
                            log_first
                            + log_step * (row - first_row)
                        )
                    ),
                )
    arithmetic = (
        abs(step) > 1e-12
        and max_arithmetic_error <= rel_tol * (scale or 1.0)
    )
    geometric = (
        geometric
        and abs(log_step) > 1e-9
        and max_geometric_error <= geo_tol
    )
    return finish(arithmetic or geometric)
```

This is the scalar logic from `_is_axis_progression_cells()` applied to compact
array views. It performs one complete loop over each eligible compact column
instead of the old scale pass followed by a fit pass. The caller admits the
sum of cells in columns with at least `min_n` values, calls the helper with
`with_coverage=True`, and asserts that the returned visits equal that admitted
count. It intentionally avoids proportional NumPy temporaries so normal
boundary behavior remains unchanged and no progression workspace lease is
needed.

For recurring value sets, write exact canonical float bytes into the
preallocated payload rather than retaining one Python `bytes` or `frozenset`
per column:

```python
def store_axis_fingerprint(
    column_entry,
    values,
    *,
    unique_lease,
    canonical_lease,
    temp_lease,
):
    nonlocal fingerprint_bytes_used
    unique = np.unique(values)
    unique_lease.validate_nbytes(unique.nbytes)
    if len(unique) < _AXIS_FINGERPRINT_MIN_UNIQUE:
        return
    canonical = unique.astype("<f8", copy=False)
    canonical_lease.validate_nbytes(canonical.nbytes)
    fingerprint = canonical.tobytes()
    temp_lease.validate_nbytes(len(fingerprint))
    stop = fingerprint_bytes_used + len(fingerprint)
    if stop > len(fingerprint_payload):
        raise AssertionError("axis fingerprint payload overflow")
    fingerprint_payload[fingerprint_bytes_used:stop] = fingerprint
    column_table["fingerprint_offset"][
        column_entry
    ] = fingerprint_bytes_used
    column_table["fingerprint_nbytes"][
        column_entry
    ] = len(fingerprint)
    column_table["fingerprint_hash"][
        column_entry
    ] = hashlib.sha256(fingerprint).digest()
    fingerprint_bytes_used = stop
```

Reserve unique/sort workspace before `np.unique`. Loading canonicalizes every
zero to positive `0.0` in the existing cell-copy pass, so `-0.0` and `0.0`
retain the legacy `frozenset` equality semantics without an extra proportional
pass or mask. This preserves exact order-insensitive set equality for finite
collision-grid floats without retaining one Python object per cell. Preserve
the existing minimum of four unique values. Both the canonical array and
temporary `tobytes()` payload are reserved at the current summary's full cell
upper bound before the fingerprint stage is admitted. The four leases are
reused across columns and released after that summary's fingerprint pass; only
copied bytes, offset, length, and SHA-256 digest remain in the preallocated
cross-summary tables.

The grouping scan creates compact metadata for every recurrence-support
column. Use separate progression and fingerprint loops so each work stage can
be admitted immediately before its own pass. Run progression only for position
summaries, but fingerprint every support summary:

```python
if key in position_key_set:
    add_fixed_work(summary_progression_cells)
    if not admit_stage(
        "progression", summary_progression_cells
    ):
        return unavailable_result()
    observed_progression_visits = 0
    column_entry = column_entry_start
    start = 0
    while start < len(ordered):
        column = int(ordered["column"][start])
        stop = start + 1
        while (
            stop < len(ordered)
            and int(ordered["column"][stop]) == column
        ):
            stop += 1
        rows = ordered["row"][start:stop]
        values = ordered["value"][start:stop]
        if len(values) >= _AXIS_PROGRESSION_MIN_CELLS:
            (
                is_progression,
                progression_coverage,
            ) = _is_axis_progression_arrays(
                rows,
                values,
                with_coverage=True,
            )
            observed_progression_visits += (
                progression_coverage["value_visits"]
            )
            column_table["is_progression"][column_entry] = (
                is_progression
            )
        column_entry += 1
        start = stop
    if column_entry != column_entry_stop:
        raise AssertionError("axis progression columns diverged")
    if observed_progression_visits != summary_progression_cells:
        raise AssertionError("axis progression work diverged")

unique_lease = reserve_axis_state(
    "axis_unique_values",
    summary_cells,
)
unique_workspace = reserve_axis_state(
    "axis_unique_workspace",
    5 * summary_cells,
)
canonical_lease = reserve_axis_state(
    "axis_canonical_values",
    summary_cells,
)
temp_lease = reserve_axis_state(
    "axis_fingerprint_temp",
    summary_cells,
)
fingerprint_stage_leases = (
    unique_lease,
    unique_workspace,
    canonical_lease,
    temp_lease,
)
if any(lease is None for lease in fingerprint_stage_leases):
    return unavailable_result()
if not admit_stage("fingerprint", summary_cells):
    return unavailable_result()

column_entry = column_entry_start
start = 0
while start < len(ordered):
    column = int(ordered["column"][start])
    stop = start + 1
    while (
        stop < len(ordered)
        and int(ordered["column"][stop]) == column
    ):
        stop += 1
    values = ordered["value"][start:stop]
    store_axis_fingerprint(
        column_entry,
        values,
        unique_lease=unique_lease,
        canonical_lease=canonical_lease,
        temp_lease=temp_lease,
    )
    column_entry += 1
    start = stop

if column_entry != column_entry_stop:
    raise AssertionError("axis fingerprint columns diverged")
for lease in reversed(fingerprint_stage_leases):
    release_axis_state(lease)
release_axis_state(ordered_lease)
```

Add this module-level helper before `_axis_columns()`:

```python
def _axis_payload_equal(
    payload_view,
    table,
    left_index,
    right_index,
):
    fingerprint_nbytes = int(
        table["fingerprint_nbytes"][left_index]
    )
    if (
        int(table["fingerprint_nbytes"][right_index])
        != fingerprint_nbytes
    ):
        return False
    left_offset = int(
        table["fingerprint_offset"][left_index]
    )
    right_offset = int(
        table["fingerprint_offset"][right_index]
    )
    return (
        payload_view[
            left_offset:left_offset + fingerprint_nbytes
        ]
        == payload_view[
            right_offset:right_offset + fingerprint_nbytes
        ]
    )
```

After all summaries:

```python
fingerprint_order_lease = reserve_axis_state(
    "axis_fingerprint_order",
    column_count,
)
fingerprint_order_workspace = reserve_axis_state(
    "axis_fingerprint_order_workspace",
    4 * column_count,
)
if any(
    lease is None
    for lease in (
        fingerprint_order_lease,
        fingerprint_order_workspace,
    )
):
    return unavailable_result()

table = column_table[:column_count]
if not admit_stage("recurrence_order", column_count):
    return unavailable_result()
fingerprint_order = np.lexsort((
    table["fingerprint_hash"],
    table["fingerprint_nbytes"],
))
fingerprint_order_lease.validate_nbytes(
    fingerprint_order.nbytes
)
release_axis_state(fingerprint_order_workspace)
payload_view = memoryview(fingerprint_payload)

if not admit_stage("recurrence_group", column_count):
    return unavailable_result()
position = 0
while position < column_count:
    first_index = int(fingerprint_order[position])
    fingerprint_nbytes = int(
        table["fingerprint_nbytes"][first_index]
    )
    fingerprint_hash = table["fingerprint_hash"][first_index]
    stop = position + 1
    while (
        stop < column_count
        and int(table["fingerprint_nbytes"][
            int(fingerprint_order[stop])
        ]) == fingerprint_nbytes
        and table["fingerprint_hash"][
            int(fingerprint_order[stop])
        ] == fingerprint_hash
    ):
        stop += 1
    if fingerprint_nbytes == 0:
        position = stop
        continue

    class_start = position
    while class_start < stop:
        base_index = int(fingerprint_order[class_start])
        match_stop = class_start
        for candidate_position in range(class_start, stop):
            candidate_index = int(
                fingerprint_order[candidate_position]
            )
            if not admit_dynamic_stage(
                "recurrence_comparison", 1
            ):
                return unavailable_result()
            if _axis_payload_equal(
                payload_view,
                table,
                base_index,
                candidate_index,
            ):
                displaced_index = int(
                    fingerprint_order[match_stop]
                )
                fingerprint_order[match_stop] = candidate_index
                fingerprint_order[candidate_position] = (
                    displaced_index
                )
                match_stop += 1
        match_count = match_stop - class_start
        if match_count <= 0:
            raise AssertionError(
                "axis fingerprint class made no progress"
            )
        if match_count >= recur_min:
            if not admit_dynamic_stage(
                "recurrence_mark", match_count
            ):
                return unavailable_result()
            for match_position in range(
                class_start, match_stop
            ):
                candidate_index = int(
                    fingerprint_order[match_position]
                )
                table["is_recurring"][candidate_index] = True
        class_start = match_stop
    position = stop

dynamic_finalization_complete = True
if not admit_stage("output", column_count):
    return unavailable_result()
axis = {key: set() for key in position_keys}
for index in range(column_count):
    if (
        bool(table["is_output"][index])
        and (
            bool(table["is_progression"][index])
            or bool(table["is_recurring"][index])
        )
    ):
        summary_index = int(table["summary_index"][index])
        axis[support_keys[summary_index]].add(
            int(table["column"][index])
        )

release_axis_state(fingerprint_order_lease)
release_axis_state(output_capacity_lease)
release_axis_state(fingerprint_payload_lease)
release_axis_state(column_table_lease)
```

`np.lexsort` groups fixed-size length/hash records; every ordering/grouping pass
is admitted for `column_count` compact records. `_axis_payload_equal()` is
called only after one dynamic comparison unit is admitted. Each exact class is
partitioned in place at the front of its length/hash group, so every inner-loop
candidate visit performs one accounted payload comparison and recurrence flags
need no second comparison pass. SHA-256 is therefore only an ordering
accelerator and cannot change equality semantics. The complete output traversal
is admitted before the first `dict`/`set` insertion, while the pre-reserved
output-capacity token stays live until the mapping is complete. No per-column
Python `dict`, `Counter`, recurring `set`, or lease list remains.

- [ ] **Step 6: Admit each concrete axis stage inside `_axis_columns()`**

At `_axis_columns()` entry:

```python
if position_keys is None:
    position_keys = _position_family_keys(grids)
else:
    position_keys = tuple(position_keys)
    if len(position_keys) < 2:
        position_keys = ()
position_key_set = frozenset(position_keys)
support_keys = (
    tuple(
        key for key, grid in grids.items()
        if (
            key in position_key_set
            or len(grid) >= _AXIS_FINGERPRINT_MIN_UNIQUE
        )
    )
    if len(position_keys) >= 2
    else ()
)
position_cell_count = sum(
    len(grids[key]) for key in position_keys
)
support_cell_count = sum(
    len(grids[key]) for key in support_keys
)
remaining_fixed_visits = 3 * support_cell_count
axis_work_skipped_lower_bound = 0
dynamic_finalization_complete = not bool(support_keys)
stage_visits = {
    "loading": 0,
    "grouping": 0,
    "progression": 0,
    "fingerprint": 0,
    "recurrence_order": 0,
    "recurrence_group": 0,
    "recurrence_comparison": 0,
    "recurrence_mark": 0,
    "output": 0,
}
default_axis_state_limit = (
    _AXIS_STATE_UNITS_PER_CELL
    * support_cell_count
)
axis_state_limit = (
    default_axis_state_limit
    if _state_limit is None
    else max(0, int(_state_limit))
)
state = StateBudget(axis_state_limit)
live_leases = []


def add_fixed_work(count):
    nonlocal remaining_fixed_visits
    remaining_fixed_visits += max(0, int(count))


def record_skipped_work(count, *, budget_recorded=False):
    nonlocal axis_work_skipped_lower_bound
    count = max(0, int(count))
    axis_work_skipped_lower_bound += count
    if budget is not None and not budget_recorded:
        budget.skip_values(count)


def admit_stage(stage, count):
    nonlocal remaining_fixed_visits
    count = max(0, int(count))
    if count > remaining_fixed_visits:
        raise AssertionError("axis work ledger underflow")
    remaining_fixed_visits -= count
    if budget is not None and not budget.consume_values(count):
        record_skipped_work(count, budget_recorded=True)
        return False
    stage_visits[stage] += count
    return True


def admit_dynamic_stage(stage, count):
    count = max(0, int(count))
    if budget is not None and not budget.consume_values(count):
        record_skipped_work(count, budget_recorded=True)
        return False
    stage_visits[stage] += count
    return True


def skip_remaining_axis_work():
    nonlocal remaining_fixed_visits
    record_skipped_work(remaining_fixed_visits)
    remaining_fixed_visits = 0


def release_all_axis_leases():
    for lease in reversed(live_leases):
        if not lease.released:
            lease.release()
    live_leases.clear()


def reserve_axis_state(name, units):
    lease = state.try_reserve(name, units)
    if lease is not None:
        live_leases.append(lease)
    return lease


def release_axis_state(lease):
    lease.release()
    live_leases.remove(lease)


def finish_result(result, *, available):
    if not available:
        skip_remaining_axis_work()
    coverage = {
        "participating_summaries": len(position_keys),
        "participating_cells": position_cell_count,
        "recurrence_support_summaries": len(support_keys),
        "recurrence_support_cells": support_cell_count,
        "axis_loading_visits": stage_visits["loading"],
        "axis_grouping_visits": stage_visits["grouping"],
        "axis_progression_visits": stage_visits["progression"],
        "axis_fingerprint_visits": stage_visits["fingerprint"],
        "axis_recurrence_order_visits": (
            stage_visits["recurrence_order"]
        ),
        "axis_recurrence_group_visits": (
            stage_visits["recurrence_group"]
        ),
        "axis_recurrence_comparison_visits": (
            stage_visits["recurrence_comparison"]
        ),
        "axis_recurrence_mark_visits": (
            stage_visits["recurrence_mark"]
        ),
        "axis_output_visits": stage_visits["output"],
        "axis_value_visits": sum(stage_visits.values()),
        "axis_work_skipped_lower_bound": (
            axis_work_skipped_lower_bound
        ),
        "axis_work_skipped_is_lower_bound": (
            not available and not dynamic_finalization_complete
        ),
        "axis_context_available": bool(available),
        "axis_state_unit_limit": axis_state_limit,
        "axis_peak_state_units": state.peak_units,
    }
    if budget is not None:
        budget.record_axis_coverage(
            available=available,
            loading_visits=stage_visits["loading"],
            grouping_visits=stage_visits["grouping"],
            progression_visits=stage_visits["progression"],
            fingerprint_visits=stage_visits["fingerprint"],
            recurrence_order_visits=stage_visits[
                "recurrence_order"
            ],
            recurrence_group_visits=stage_visits[
                "recurrence_group"
            ],
            recurrence_comparison_visits=stage_visits[
                "recurrence_comparison"
            ],
            recurrence_mark_visits=stage_visits[
                "recurrence_mark"
            ],
            output_visits=stage_visits["output"],
            work_skipped_lower_bound=(
                axis_work_skipped_lower_bound
            ),
            work_skipped_is_lower_bound=(
                not available
                and not dynamic_finalization_complete
            ),
            state_unit_limit=axis_state_limit,
            peak_state_units=state.peak_units,
        )
    return (result, coverage) if with_coverage else result


def unavailable_result():
    return finish_result({}, available=False)
```

Use `reserve_axis_state()` and `release_axis_state()` for every lease shown in
Step 5. Put the entire classification body in one `try/finally`, and within its
deterministic per-summary loop reserve the next stage's complete state before
calling `admit_stage()`. Iterate without an intermediate mapping:

```python
for summary_index, key in enumerate(support_keys):
    grid = grids[key]
    summary_cells = len(grid)
```

On a failed `consume_values()`, that method already records the rejected
fixed or dynamic unit. The local helpers mirror it into
`axis_work_skipped_lower_bound` without double-adding it to the budget.
`unavailable_result()` then records every still-known fixed pass. Progression
cardinality becomes known after grouping and is added with `add_fixed_work()`;
the recurrence-order, recurrence-group, and output cardinalities for each
summary become known in that same grouping scan and are added immediately as
`3 * summary_column_count`. A later fingerprint state or work rejection
therefore reports those already known fixed visits as skipped. Exact payload
comparisons and recurrence marks are outcome-dependent and admitted immediately
before they execute. If stopping prevents their remaining cardinality from
being known, the lower-bound flag is true. No compact-array loop or mapping
insertion runs after its admission fails.

A state failure reaches `unavailable_result()` before the affected stage is
admitted. It returns `{}` rather than allocating an unreserved per-summary
empty mapping. Partial progression/fingerprint/finalization results are
discarded, and the single `finally` releases every reservation on success,
resource rejection, and unexpected errors.

Indent the complete reservation and classification body from Step 5 under one
`try:`. Close that block with:

```python
try:
    if remaining_fixed_visits != 0:
        raise AssertionError("axis work ledger did not close")
    if not dynamic_finalization_complete:
        raise AssertionError("axis finalization did not close")
    return finish_result(axis, available=True)
finally:
    release_all_axis_leases()
```

The displayed `try` body is the tail of the larger classification `try`; do
not create a nested second `try`.

On success, loading, grouping, and fingerprint construction each visit every
recurrence-support cell once. Progression visits one complete compact pass over
eligible columns in position summaries. Recurrence ordering/grouping and
output each visit every compact column record once; payload comparisons and
recurrence marks report their concrete counts. The stage-counter sum is the
exact axis contribution to `values_examined`. Remove the caller-side
axis-value precharge block from `detect_collisions()`.

The production state limit and test-only override are:

```python
default_axis_state_limit = (
    _AXIS_STATE_UNITS_PER_CELL
    * support_cell_count
)
axis_state_limit = (
    default_axis_state_limit
    if _state_limit is None
    else max(0, int(_state_limit))
)
```

This is a documented fixed multiplier of already bounded retained grid cells,
not a new public control. With zero support cells, the derived limit is exactly
zero. `_state_limit` is private test injection only.

- [ ] **Step 7: Move pair admission and visit accounting into the helpers**

First make rejection of the current candidate detector-owned. Replace
`CrossSheetWorkBudget.begin_pair()` with:

```python
def begin_pair(self, planned_value_count):
    planned_value_count = max(0, int(planned_value_count))
    blocked_by = None
    if self.pairs_examined >= self.pair_limit:
        blocked_by = "pair"
    elif (
        self.values_examined + planned_value_count
        > self.value_limit
    ):
        blocked_by = "value"
    if blocked_by is not None:
        self._limits_reached.add(blocked_by)
        self.pairs_skipped += 1
        self.values_skipped += planned_value_count
        return False
    self.pairs_examined += 1
    return True
```

The rejected current candidate is now counted exactly once by the helper that
attempted it. `skip_pairs()` is reserved for later feasible candidates that
the outer loop never enters.

Add `budget=None` to `_cross_sheet_pair_stats()`. At the top of both
`_cross_sheet_pair_stats()` and
`_detect_decimal_tail_reuse_for_pair()`, before creating proportional
containers or calling either grid's `.values()`/`.items()`, establish:

```python
candidate_value_count = len(ga) + len(gb)
value_visits = 0


def finish_pair_result(result, **extra_coverage):
    if value_visits > candidate_value_count:
        raise AssertionError("pair value work exceeded its candidate")
    if budget is not None:
        budget.record_values(value_visits)
        budget.skip_values(candidate_value_count - value_visits)
    coverage = {
        "pair_admitted": True,
        "candidate_value_count": candidate_value_count,
        "value_visits": value_visits,
        **extra_coverage,
    }
    return (result, coverage) if with_coverage else result


if budget is not None and not budget.begin_pair(
    candidate_value_count
):
    coverage = {
        "pair_admitted": False,
        "candidate_value_count": candidate_value_count,
        "value_visits": 0,
    }
    return (None, coverage) if with_coverage else None
```

Each helper computes its own exact complete-pass upper bound from the two
grids. The preflight happens before the first source-grid visit. On admission,
increment `value_visits` in the existing concrete loops and route every normal
return through `finish_pair_result()`. For `_cross_sheet_pair_stats()`, pass
its existing `retained_value_counts` field as extra coverage. For the
decimal-tail helper, this includes the retained-match-cap early return, so it
records the visited prefix as examined and the unvisited candidate suffix as
skipped. Preserve all result payloads and no-budget direct-call behavior.

In `detect_collisions()`, add this ledger bridge after
`candidate_ledger` is created:

```python
def resolve_pair_family(coverage):
    candidate_ledger.resolve(
        coverage["candidate_value_count"]
    )
    if coverage["pair_admitted"]:
        return True
    if budget is None:
        raise AssertionError(
            "unbudgeted pair helper rejected candidate"
        )
    pairs_remaining, values_remaining = (
        candidate_ledger.remaining()
    )
    budget.skip_pairs(
        pairs_remaining,
        values=values_remaining,
    )
    return False
```

Pass `budget=budget` to `_cross_sheet_pair_stats()`, then resolve the helper's
coverage before reading its result:

```python
pair_stats, pair_coverage = _cross_sheet_pair_stats(
    ga,
    gb,
    budget=budget,
    with_coverage=True,
)
if not resolve_pair_family(pair_coverage):
    break
```

Do the same immediately after
`_detect_decimal_tail_reuse_for_pair()`. Remove `collision_value_work`,
`tail_value_work`, both caller-side `begin_pair()` blocks, the caller-side
`record_values()` calls, the pair-stats divergence check, and the caller-side
decimal-tail `skip_values()` call. The helper coverage is the only source for
resolving an attempted candidate. `_CrossSheetCandidateLedger.from_sizes()`
remains a linear eligibility aggregate and is used only to count later
pair/value work that was never entered.

`detect_cross_sheet_column_duplicates()` also uses `consume_pair(0)`, whose
failed `begin_pair()` now records the rejected current candidate. Change its
outer skip to later candidates only:

```python
budget.skip_pairs(
    max(0, total_pairs - candidate_index - 1)
)
```

Do not increment `candidate_index` for the rejected candidate. Keep
`test_column_duplicate_comparisons_share_pair_and_finding_budget()` unchanged:
its six candidates with a pair limit of two must still report exactly two
examined and four skipped, not five.

- [ ] **Step 8: Feed unavailable axis context into coverage**

Extend `CrossSheetWorkBudget.limitation_metadata()` with additive fields:

```python
{
    "axis_context_available": self.axis_context_available,
    "axis_loading_visits": self.axis_loading_visits,
    "axis_grouping_visits": self.axis_grouping_visits,
    "axis_progression_visits": self.axis_progression_visits,
    "axis_fingerprint_visits": self.axis_fingerprint_visits,
    "axis_recurrence_order_visits": (
        self.axis_recurrence_order_visits
    ),
    "axis_recurrence_group_visits": (
        self.axis_recurrence_group_visits
    ),
    "axis_recurrence_comparison_visits": (
        self.axis_recurrence_comparison_visits
    ),
    "axis_recurrence_mark_visits": (
        self.axis_recurrence_mark_visits
    ),
    "axis_output_visits": self.axis_output_visits,
    "axis_work_skipped_lower_bound": (
        self.axis_work_skipped_lower_bound
    ),
    "axis_work_skipped_is_lower_bound": (
        self.axis_work_skipped_is_lower_bound
    ),
    "axis_state_unit_limit": self.axis_state_unit_limit,
    "axis_peak_state_units": self.axis_peak_state_units,
}
```

Add these dataclass fields:

```python
axis_context_available: bool = True
axis_loading_visits: int = 0
axis_grouping_visits: int = 0
axis_progression_visits: int = 0
axis_fingerprint_visits: int = 0
axis_recurrence_order_visits: int = 0
axis_recurrence_group_visits: int = 0
axis_recurrence_comparison_visits: int = 0
axis_recurrence_mark_visits: int = 0
axis_output_visits: int = 0
axis_work_skipped_lower_bound: int = 0
axis_work_skipped_is_lower_bound: bool = False
axis_state_unit_limit: int = 0
axis_peak_state_units: int = 0
```

Add:

```python
def record_axis_coverage(
    self,
    *,
    available,
    loading_visits,
    grouping_visits,
    progression_visits,
    fingerprint_visits,
    recurrence_order_visits,
    recurrence_group_visits,
    recurrence_comparison_visits,
    recurrence_mark_visits,
    output_visits,
    work_skipped_lower_bound,
    work_skipped_is_lower_bound,
    state_unit_limit,
    peak_state_units,
):
    self.axis_context_available = (
        self.axis_context_available and bool(available)
    )
    self.axis_loading_visits += max(0, int(loading_visits))
    self.axis_grouping_visits += max(0, int(grouping_visits))
    self.axis_progression_visits += max(0, int(progression_visits))
    self.axis_fingerprint_visits += max(0, int(fingerprint_visits))
    self.axis_recurrence_order_visits += max(
        0, int(recurrence_order_visits)
    )
    self.axis_recurrence_group_visits += max(
        0, int(recurrence_group_visits)
    )
    self.axis_recurrence_comparison_visits += max(
        0, int(recurrence_comparison_visits)
    )
    self.axis_recurrence_mark_visits += max(
        0, int(recurrence_mark_visits)
    )
    self.axis_output_visits += max(0, int(output_visits))
    self.axis_work_skipped_lower_bound += max(
        0, int(work_skipped_lower_bound)
    )
    self.axis_work_skipped_is_lower_bound = (
        self.axis_work_skipped_is_lower_bound
        or bool(work_skipped_is_lower_bound)
    )
    self.axis_state_unit_limit = max(
        self.axis_state_unit_limit,
        max(0, int(state_unit_limit)),
    )
    self.axis_peak_state_units = max(
        self.axis_peak_state_units,
        max(0, int(peak_state_units)),
    )
    if self.axis_peak_state_units > self.axis_state_unit_limit:
        raise AssertionError("axis peak exceeded state limit")
    if not available:
        self._limits_reached.add("axis")
```

Append to `tests/test_detector_coverage.py`:

```python
def test_cross_sheet_budget_reports_axis_work_and_state_coverage():
    budget = audit.CrossSheetWorkBudget(
        pair_limit=10,
        value_limit=100,
        tail_match_limit=10,
        finding_limit=10,
    )

    budget.record_axis_coverage(
        available=False,
        loading_visits=8,
        grouping_visits=8,
        progression_visits=0,
        fingerprint_visits=0,
        recurrence_order_visits=0,
        recurrence_group_visits=0,
        recurrence_comparison_visits=0,
        recurrence_mark_visits=0,
        output_visits=0,
        work_skipped_lower_bound=16,
        work_skipped_is_lower_bound=True,
        state_unit_limit=512,
        peak_state_units=384,
    )
    metadata = budget.limitation_metadata()

    assert metadata["axis_context_available"] is False
    assert metadata["axis_loading_visits"] == 8
    assert metadata["axis_grouping_visits"] == 8
    assert metadata["axis_progression_visits"] == 0
    assert metadata["axis_fingerprint_visits"] == 0
    assert metadata["axis_recurrence_order_visits"] == 0
    assert metadata["axis_recurrence_group_visits"] == 0
    assert metadata["axis_recurrence_comparison_visits"] == 0
    assert metadata["axis_recurrence_mark_visits"] == 0
    assert metadata["axis_output_visits"] == 0
    assert metadata["axis_work_skipped_lower_bound"] == 16
    assert metadata["axis_work_skipped_is_lower_bound"] is True
    assert metadata["axis_state_unit_limit"] == 512
    assert metadata["axis_peak_state_units"] == 384
    assert metadata["limits_reached"] == ["axis"]
```

Insert `"axis"` after `"value"` in limitation ordering. When axis admission
fails, continue collision detection with empty axis sets and retain the
existing conservative severity behavior. The rejected fixed/dynamic unit is
already recorded by `consume_values()`; add only known later fixed work with
`skip_values()`. Expose the explicit lower-bound flag whenever unexecuted
outcome-dependent comparison or mark counts cannot be known without violating
the stop.

- [ ] **Step 9: Run collision and coverage tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_collisions.py \
  tests/test_detector_coverage.py \
  tests/test_module_boundaries.py -k \
  'axis or cross_sheet_work or cross_sheet_value_work_counts or impossible or budget or pair_stop or pair_setup or pair_helpers or decimal_tail_match_state'
```

Expected: all selected tests pass and actual grid, compact-column, comparison,
mark, and output processing match metadata.

- [ ] **Step 10: Commit Task 4**

```bash
git add src/paperconan/_audit.py \
  tests/test_collisions.py tests/test_detector_coverage.py \
  tests/test_module_boundaries.py
git commit -m "fix: account cross-sheet work in detectors"
```

---

### Task 5: Reserve Fingerprint Capacity Before Candidate Construction

**Files:**

- Modify: `src/paperconan/_audit.py:4207-4684`
- Modify: `tests/test_cross_sheet_summaries.py`
- Modify: `tests/test_resource_lifetime.py`
- Modify: `tests/test_detector_coverage.py`

**Interfaces:**

- Produces:
  - `_selected_fingerprint_columns(blocks, column_limit)`
  - `CrossSheetSummaryReservation`
  - `CrossSheetSummaryBudget.start_summary()`
  - `CrossSheetSummaryReservation.reserve_fingerprint_candidates(count)`
  - `CrossSheetSummaryReservation.validate_metrics(metrics)`
  - `CrossSheetSummaryReservation.commit(metrics)`
  - `CrossSheetSummaryReservation.reject(dimensions)`
  - `CrossSheetSummaryReservation.rollback()`
  - `CrossSheetSummaryReservation.closed`
- Replaces:
  - `CrossSheetSummaryBudget.begin_summary()`
  - post-construction-only `try_retain()` flow.

- [ ] **Step 1: Add a zero-capacity no-construction regression**

Append to `tests/test_resource_lifetime.py`:

```python
def test_fingerprint_capacity_is_checked_before_source_rows_are_touched():
    class GuardedSource:
        nrows = 40
        ncols = 2
        _text = {}

        def cell(self, row, col):
            return None

        def exact_numeric(self, row, col):
            raise AssertionError(
                "fingerprint candidate started without capacity"
            )

    budget = audit.CrossSheetSummaryBudget(
        summary_limit=10,
        grid_cell_limit=100,
        label_cell_limit=100,
        label_byte_limit=100,
        column_fingerprint_limit=0,
    )

    summary, limitations = audit.build_cross_sheet_summary(
        "wide.csv",
        "Figure 1",
        GuardedSource(),
        blocks=[(0, 40, 0, 2)],
        budget=budget,
    )

    assert summary is None
    assert limitations == []
    metadata = budget.limitation_metadata()
    assert metadata["exhausted_dimensions"] == [
        "column_fingerprints"
    ]
    assert metadata["dimensions"]["column_fingerprints"][
        "candidate_columns_skipped"
    ] == 2
    assert metadata["dimensions"]["column_fingerprints"][
        "skipped_items"
    ] == 2
```

- [ ] **Step 2: Add commit and rollback regressions**

Append to `tests/test_cross_sheet_summaries.py`:

```python
def test_summary_reservation_commits_actual_fingerprints_not_upper_bound():
    budget = audit.CrossSheetSummaryBudget(
        summary_limit=10,
        grid_cell_limit=100_000,
        label_cell_limit=100_000,
        label_byte_limit=100_000,
        column_fingerprint_limit=10,
    )

    summary, _ = build_cross_sheet_summary(
        "a.xlsx", "Figure 1", _sheet(), budget=budget
    )

    assert summary is not None
    assert budget.retained_metadata()["column_fingerprints"] == len(
        summary.columns
    )
    assert budget.reserved_metadata() == {
        "summaries": 0,
        "grid_cells": 0,
        "label_cells": 0,
        "label_bytes": 0,
        "column_fingerprints": 0,
    }


def test_summary_reservation_rejection_precedes_final_object_construction(
    monkeypatch
):
    budget = audit.CrossSheetSummaryBudget(
        summary_limit=10,
        grid_cell_limit=1,
        label_cell_limit=100_000,
        label_byte_limit=100_000,
        column_fingerprint_limit=10,
    )
    monkeypatch.setattr(
        audit,
        "CrossSheetSummary",
        lambda **_kwargs: pytest.fail(
            "rejected summary reached final object construction"
        ),
    )

    summary, _ = build_cross_sheet_summary(
        "a.xlsx", "Figure 1", _sheet(), budget=budget
    )

    assert summary is None
    assert budget.retained_metadata()["column_fingerprints"] == 0
    assert budget.reserved_metadata() == {
        "summaries": 0,
        "grid_cells": 0,
        "label_cells": 0,
        "label_bytes": 0,
        "column_fingerprints": 0,
    }


@pytest.mark.parametrize(
    "failure_point",
    [
        "find_numeric_blocks",
        "_grid_from_rows",
        "CrossSheetSummary",
    ],
)
def test_summary_reservation_rolls_back_after_any_builder_exception(
    monkeypatch,
    failure_point,
):
    budget = audit.CrossSheetSummaryBudget(
        summary_limit=10,
        grid_cell_limit=100_000,
        label_cell_limit=100_000,
        label_byte_limit=100_000,
        column_fingerprint_limit=10,
    )
    original = getattr(audit, failure_point)

    def raise_after_reservation(*_args, **_kwargs):
        raise RuntimeError(
            f"synthetic {failure_point} failure"
        )

    monkeypatch.setattr(
        audit, failure_point, raise_after_reservation
    )
    with pytest.raises(
        RuntimeError, match=f"synthetic {failure_point} failure"
    ):
        build_cross_sheet_summary(
            "a.xlsx", "Figure 1", _sheet(), budget=budget
        )

    assert budget.reserved_metadata() == {
        "summaries": 0,
        "grid_cells": 0,
        "label_cells": 0,
        "label_bytes": 0,
        "column_fingerprints": 0,
    }
    assert budget.retained_metadata() == {
        "summaries": 0,
        "grid_cells": 0,
        "label_cells": 0,
        "label_bytes": 0,
        "column_fingerprints": 0,
    }

    monkeypatch.setattr(audit, failure_point, original)
    summary, _ = build_cross_sheet_summary(
        "b.xlsx", "Figure 2", _sheet(), budget=budget
    )
    assert summary is not None
    assert budget.reserved_metadata() == {
        "summaries": 0,
        "grid_cells": 0,
        "label_cells": 0,
        "label_bytes": 0,
        "column_fingerprints": 0,
    }
```

- [ ] **Step 3: Run new summary tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_resource_lifetime.py \
  tests/test_cross_sheet_summaries.py -k \
  'fingerprint_capacity_is_checked or summary_reservation'
```

Expected: the guarded source is touched and the reservation APIs do not exist.

- [ ] **Step 4: Extract deterministic candidate-column selection**

Refactor the first half of `_column_fingerprints()` into:

```python
def _selected_fingerprint_columns(blocks, column_limit):
    column_limit = max(0, int(column_limit))
    selected = []
    columns_total = 0
    for start, stop in _iter_merged_column_intervals(blocks):
        interval_size = stop - start
        remaining = column_limit - len(selected)
        if remaining > 0:
            selected.extend(
                range(start, start + min(interval_size, remaining))
            )
        columns_total += interval_size
    return tuple(selected), columns_total
```

Preserve its existing positional parameters and add keyword-only inputs:

```python
def _column_fingerprints(
    file,
    sheet,
    source,
    blocks,
    min_column_length,
    distinct_limit=None,
    column_limit=None,
    retained_limit=None,
    *,
    selected_columns=None,
    columns_total=None,
    with_metrics=False,
):
    if distinct_limit is None:
        distinct_limit = _COLUMN_FINGERPRINT_DISTINCT_LIMIT
    if column_limit is None:
        column_limit = _COLUMN_FINGERPRINT_MAX_COLUMNS
    if (selected_columns is None) != (columns_total is None):
        raise AssertionError(
            "selected columns and total must be supplied together"
        )
    if selected_columns is None:
        selected_columns, columns_total = (
            _selected_fingerprint_columns(
                blocks,
                column_limit,
            )
        )
    else:
        selected_columns = tuple(selected_columns)
        columns_total = max(0, int(columns_total))
        if columns_total < len(selected_columns):
            raise AssertionError(
                "selected columns exceed declared total"
            )
    columns_used = len(selected_columns)
```

Continue with the existing fingerprint loop from `columns_used`. Supplied
columns are used exactly as given; the function neither recomputes nor enlarges
that candidate set.

- [ ] **Step 5: Add a reservation transaction**

Add `CrossSheetSummaryReservation` next to
`CrossSheetSummaryBudget`:

```python
class CrossSheetSummaryReservation:
    def __init__(self, budget):
        self.budget = budget
        self._reserved = {
            dimension: 0 for dimension in _SUMMARY_DIMENSIONS
        }
        self._closed = False
        self._validated_metrics = None

    @property
    def closed(self):
        return self._closed

    def reserve_capacity(self, dimension, count, *, rejection=None):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        count = max(0, int(count))
        available = self.budget.available_metadata()[dimension]
        if count > available:
            details = {
                "skipped_items": max(1, count),
            }
            if rejection:
                details.update(rejection)
            self.reject({dimension: details})
            return False
        self._reserved[dimension] += count
        self.budget._reserved[dimension] += count
        return True

    def reserve_fingerprint_candidates(self, count):
        count = max(0, int(count))
        return self.reserve_capacity(
            "column_fingerprints",
            count,
            rejection={
                "candidate_columns_skipped": count,
                "candidate_columns_may_qualify": True,
            },
        )

    def amount(self, dimension):
        return self._reserved[dimension]

    @staticmethod
    def _normalize_metrics(metrics):
        return {
            dimension: (
                1
                if dimension == "summaries"
                else max(0, int(metrics[dimension]))
            )
            for dimension in _SUMMARY_DIMENSIONS
        }

    def validate_metrics(self, metrics):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        actual_metrics = self._normalize_metrics(metrics)
        exceeded = {}
        for dimension in _SUMMARY_DIMENSIONS:
            actual = actual_metrics[dimension]
            reserved = self._reserved[dimension]
            if actual > reserved:
                exceeded[dimension] = {
                    "skipped_items": max(1, actual),
                }
        if exceeded:
            self.reject(exceeded)
            return False
        self._validated_metrics = actual_metrics
        return True

    def commit(self, metrics):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        actual_metrics = self._normalize_metrics(metrics)
        if actual_metrics != self._validated_metrics:
            raise AssertionError(
                "summary metrics changed after validation"
            )
        self.budget._commit_reservation(
            self,
            actual_metrics,
        )
        self._closed = True
        return True

    def reject(self, dimensions):
        if self._closed:
            return
        self.budget._reject_reservation(self, dimensions)
        self._validated_metrics = None
        self._closed = True

    def rollback(self):
        if self._closed:
            return
        self.budget._release_reservation(self)
        self._validated_metrics = None
        self._closed = True
```

Initialize `CrossSheetSummaryBudget._reserved` in `__post_init__`:

```python
self._reserved = {
    dimension: 0 for dimension in _SUMMARY_DIMENSIONS
}
```

Add:

```python
def reserved_metadata(self):
    return dict(self._reserved)

def available_metadata(self):
    retained = self.retained_metadata()
    limits = self._limits()
    return {
        dimension: max(
            0,
            limits[dimension]
            - retained[dimension]
            - self._reserved[dimension],
        )
        for dimension in _SUMMARY_DIMENSIONS
    }

def start_summary(self):
    self.summaries_considered += 1
    if self.available_metadata()["summaries"] < 1:
        self._record_rejection({"summaries": {"skipped_items": 1}})
        return None
    reservation = CrossSheetSummaryReservation(self)
    if not reservation.reserve_capacity("summaries", 1):
        raise AssertionError("summary slot reservation diverged")
    return reservation

def _release_reservation(self, reservation):
    for dimension, count in reservation._reserved.items():
        if count > self._reserved[dimension]:
            raise AssertionError("summary reservation underflow")
        self._reserved[dimension] -= count
        reservation._reserved[dimension] = 0

def _commit_reservation(self, reservation, metrics):
    self._release_reservation(reservation)
    self.summaries_retained += 1
    self.grid_cells_retained += int(metrics["grid_cells"])
    self.label_cells_retained += int(metrics["label_cells"])
    self.label_bytes_retained += int(metrics["label_bytes"])
    self.column_fingerprints_retained += int(
        metrics["column_fingerprints"]
    )

def _reject_reservation(self, reservation, dimensions):
    self._release_reservation(reservation)
    self._record_rejection(dimensions)
```

Update `_record_rejection()` to accept either an integer or a detail mapping:

```python
self.summaries_skipped += 1
for dimension, raw in dimensions.items():
    details = (
        {"skipped_items": raw}
        if isinstance(raw, int)
        else dict(raw)
    )
    skipped_items = max(1, int(details.pop("skipped_items", 1)))
    item = self._exhausted.setdefault(dimension, {
        "limit": limits[dimension],
        "retained": retained[dimension],
        "skipped_sheets": 0,
        "skipped_items": 0,
    })
    item["retained"] = retained[dimension]
    item["skipped_sheets"] += 1
    item["skipped_items"] += skipped_items
    for key, value in details.items():
        if key.endswith("_skipped") and isinstance(value, int):
            item[key] = item.get(key, 0) + value
        else:
            item[key] = value
```

Remove `begin_summary()` and `try_retain()`. Change `remaining_metadata()` to
return `available_metadata()`.

Keep limitation ordering from `_SUMMARY_DIMENSIONS`.

- [ ] **Step 6: Reserve every component before its builder grows**

Replace the body of `build_cross_sheet_summary()` with the transaction below.
The reservation wraps block discovery through the fully constructed return.
Actual metrics are validated before final object construction; commit occurs
only after `CrossSheetSummary` and every limitation exist:

```python
def build_cross_sheet_summary(
    file,
    sheet,
    source,
    *,
    blocks=None,
    collision_max_rows=200,
    collision_max_cells=200000,
    min_column_length=12,
    budget=None,
) -> tuple[CrossSheetSummary | None, list[InputLimitation]]:
    reservation = (
        budget.start_summary() if budget is not None else None
    )
    if budget is not None and reservation is None:
        return None, []

    try:
        if blocks is None:
            blocks = find_numeric_blocks(source)
        selected_columns, columns_total = (
            _selected_fingerprint_columns(
                blocks,
                _COLUMN_FINGERPRINT_MAX_COLUMNS,
            )
        )
        if reservation is not None:
            available = budget.available_metadata()
            for dimension in (
                "grid_cells",
                "label_cells",
                "label_bytes",
            ):
                if not reservation.reserve_capacity(
                    dimension, available[dimension]
                ):
                    raise AssertionError(
                        f"{dimension} reservation diverged"
                    )
            if not reservation.reserve_fingerprint_candidates(
                len(selected_columns)
            ):
                return None, []

        grid, grid_meta = _grid_from_rows(
            source,
            max_rows=collision_max_rows,
            max_cells=collision_max_cells,
            retained_cell_limit=(
                reservation.amount("grid_cells")
                if reservation is not None else None
            ),
            with_coverage=True,
        )
        label_row_limit = min(
            source.nrows, collision_max_rows + 3
        )
        labels, label_metrics = _bounded_sparse_label_context(
            source,
            row_limit=label_row_limit,
            retained_cell_limit=(
                reservation.amount("label_cells")
                if reservation is not None else None
            ),
            retained_byte_limit=(
                reservation.amount("label_bytes")
                if reservation is not None else None
            ),
        )
        (
            columns,
            column_limitations,
            column_metrics,
        ) = _column_fingerprints(
            file,
            sheet,
            source,
            blocks,
            min_column_length,
            selected_columns=selected_columns,
            columns_total=columns_total,
            retained_limit=(
                reservation.amount("column_fingerprints")
                if reservation is not None else None
            ),
            with_metrics=True,
        )
        metrics = {
            "grid_cells": grid_meta["cells_used"],
            **label_metrics,
            **column_metrics,
        }
        if (
            reservation is not None
            and not reservation.validate_metrics(metrics)
        ):
            return None, []
        summary = CrossSheetSummary(
            file=file,
            sheet=sheet,
            grid=grid,
            labels=labels,
            columns=columns,
        )
        limitations = list(column_limitations)
        if grid_meta["row_limited"]:
            limitations.append(InputLimitation(
                scope="sheet",
                reason="collision_row_limit",
                sheet=sheet,
                details={
                    "rows_total": grid_meta["rows_total"],
                    "rows_used": grid_meta["rows_used"],
                },
            ))
        if grid_meta["cell_limited"]:
            limitations.append(InputLimitation(
                scope="sheet",
                reason="collision_cell_limit",
                sheet=sheet,
                details={
                    "cells_used": grid_meta["cells_used"],
                    "max_cells": max(
                        0, int(collision_max_cells)
                    ),
                },
            ))
        if reservation is not None:
            if not reservation.commit(metrics):
                raise AssertionError(
                    "validated summary commit was rejected"
                )
        return summary, limitations
    finally:
        if reservation is not None and not reservation.closed:
            reservation.rollback()
```

Only after all five reservations succeed may grid, label, or fingerprint
builders scan and retain proportional state. Commit only actual retained
fingerprint count and release every unused provisional slot.

- [ ] **Step 7: Preserve truthful limitation metadata**

For fingerprint preconstruction rejection, include:

```python
{
    "skipped_items": candidate_count,
    "candidate_columns_skipped": candidate_count,
    "candidate_columns_may_qualify": True,
}
```

Do not report those columns as known omitted fingerprints. Keep
`omitted_findings_lower_bound: 0` and top-level
`findings_omitted_is_lower_bound: true`.

- [ ] **Step 8: Run summary and coverage tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_cross_sheet_summaries.py \
  tests/test_resource_lifetime.py \
  tests/test_detector_coverage.py -k \
  'summary or fingerprint or cross_sheet'
```

Expected: all selected tests pass; zero-capacity sources are not touched and
every reservation returns to zero.

- [ ] **Step 9: Commit Task 5**

```bash
git add src/paperconan/_audit.py \
  tests/test_cross_sheet_summaries.py \
  tests/test_resource_lifetime.py \
  tests/test_detector_coverage.py
git commit -m "fix: reserve summary capacity before construction"
```

---

### Task 6: Document Semantics, Verify, And Re-Review

**Files:**

- Modify: `docs/cli.md:88-102`
- Modify: `skills/paperconan/references/output-schema.md:153-183`
- Modify: `tests/test_skill_docs.py:230-307`
- Modify: `tests/test_packaging.py`
- Modify locally only:
  - `.superpowers/sdd/final-review-fix-wave-4-report.md`
  - `.superpowers/sdd/progress.md`

**Interfaces:**

- Consumes: final limitation fields from Tasks 3-5.
- Produces: public documentation matching implemented units and lower-bound
  semantics.

- [ ] **Step 1: Tighten documentation governance tests**

Update `tests/test_skill_docs.py` assertions to require:

```python
assert "detector-owned" in cli_text
assert "allocation 前" in cli_text
assert "detector-owned source-grid loops" in cli_text
assert "axis loading / grouping / progression / fingerprint" in cli_text
assert "recurrence order / group / comparison / mark / output" in cli_text
assert "candidate_columns_skipped" in schema_text
assert "work_skipped_lower_bound" in schema_text
assert "state_required_lower_bound" in schema_text
assert "axis_context_available" in schema_text
assert "axis_recurrence_comparison_visits" in schema_text
assert "axis_work_skipped_is_lower_bound" in schema_text
assert "axis_state_unit_limit" in schema_text
assert "axis_peak_state_units" in schema_text
assert "axis classification 固定每值 4 次" not in cli_text
```

- [ ] **Step 2: Run governance tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_skill_docs.py::test_cli_documents_wave4_detector_and_cross_table_budgets \
  tests/test_skill_docs.py::test_output_schema_documents_consolidated_wave4_resource_units
```

Expected: failures because the current docs still describe caller-side complete
family estimates and do not describe detector-owned pair loops or
feasible-summary/finalization stages.

- [ ] **Step 3: Update public documentation and add an archive verifier**

In `docs/cli.md`, document:

- dense row/work/state checks are owned by each detector;
- state reservations occur before arrays/workspaces are allocated;
- `work_skipped_lower_bound` is used when skipped branch-dependent work cannot
  be known without execution;
- `state_required` is the complete detector-declared upper bound,
  `state_required_lower_bound` is the largest attempted simultaneous
  reservation, and `peak_state_units` is the accepted live peak;
- the block finding cap is enforced during detection while preserving
  severity/stable-order selection;
- positional/value and decimal-tail helpers admit their own complete pair/value
  upper bound immediately before their detector-owned source-grid loops, record
  concrete visits at normal exits, and leave only later never-entered
  candidates to the linear remaining-work ledger;
- axis work includes loading, grouping, and recurrence-fingerprint passes over
  recurrence-support summaries; one compact progression pass over eligible
  position columns; and recurrence order / group / comparison / mark / output
  passes over compact column records;
- every compact finalization pass is admitted before it runs; exact payload
  comparisons are admitted individually, and
  `axis_work_skipped_is_lower_bound` marks stops where remaining
  outcome-dependent work cannot be known;
- four- and five-cell support grids preserve legacy recurrence context but
  never enter pair comparison or the final axis mapping;
- `axis_state_unit_limit` is the fixed-multiplier private workspace cap and
  `axis_peak_state_units` is the accepted live axis-classification peak;
- column fingerprint candidate capacity is reserved before source rows are
  scanned;
- no new environment control was added.

In `skills/paperconan/references/output-schema.md`, document the additive
fields:

```text
peak_state_units
work_skipped_lower_bound
state_required_lower_bound
axis_context_available
axis_loading_visits
axis_grouping_visits
axis_progression_visits
axis_fingerprint_visits
axis_recurrence_order_visits
axis_recurrence_group_visits
axis_recurrence_comparison_visits
axis_recurrence_mark_visits
axis_output_visits
axis_work_skipped_lower_bound
axis_work_skipped_is_lower_bound
axis_state_unit_limit
axis_peak_state_units
candidate_columns_skipped
candidate_columns_may_qualify
```

Keep exact-vs-lower-bound language explicit.

In `tests/test_packaging.py`, import `base64`, `csv`, `hashlib`, `io`, `stat`,
`warnings`, and `zipfile`. Add a fixed wheel metadata allowlist and a callable
verifier for the archives actually present in `dist/`:

```python
WHEEL_DIST_INFO_MEMBERS = {
    "METADATA",
    "WHEEL",
    "entry_points.txt",
    "licenses/LICENSE",
    "top_level.txt",
    "RECORD",
}


def _validated_archive_member(name, seen):
    assert name
    assert "\\" not in name
    normalized = name[:-1] if name.endswith("/") else name
    assert normalized
    path = PurePosixPath(normalized)
    assert not path.is_absolute()
    assert path.as_posix() == normalized
    assert all(part not in {"", ".", ".."} for part in path.parts)
    assert normalized not in seen, f"duplicate archive member: {normalized}"
    seen.add(normalized)
    return path.parts


def _archive_parent_directories(paths):
    directories = set()
    for name in paths:
        parts = PurePosixPath(name).parts
        for stop in range(1, len(parts)):
            directories.add(PurePosixPath(*parts[:stop]).as_posix())
    return directories


def _sdist_payloads(path, expected_files):
    expected_root = f"paperconan-{__version__}"
    allowed_directories = _archive_parent_directories(expected_files)
    seen = set()
    roots = set()
    payloads = {}
    root_directory_seen = False

    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = _validated_archive_member(member.name, seen)
            roots.add(parts[0])
            assert member.isfile() or member.isdir(), member.name
            if len(parts) == 1:
                assert member.isdir(), member.name
                root_directory_seen = True
                continue
            assert parts[0] == expected_root, member.name
            relative = PurePosixPath(*parts[1:]).as_posix()
            if member.isdir():
                assert relative in allowed_directories, member.name
                continue
            assert relative not in payloads
            stream = archive.extractfile(member)
            assert stream is not None
            payloads[relative] = stream.read()

    assert root_directory_seen
    assert roots == {expected_root}
    return payloads


def _wheel_payloads(path):
    seen = set()
    payloads = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            parts = _validated_archive_member(member.filename, seen)
            assert not member.is_dir(), member.filename
            mode = member.external_attr >> 16
            assert mode == 0 or stat.S_ISREG(mode), member.filename
            normalized = PurePosixPath(*parts).as_posix()
            payloads[normalized] = archive.read(member)
    return payloads


def _record_hash(payload):
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _assert_wheel_record(payloads, record_name):
    rows = csv.reader(
        io.StringIO(payloads[record_name].decode("utf-8"), newline="")
    )
    records = {}
    record_seen = set()
    for row in rows:
        assert len(row) == 3, row
        name, digest, size = row
        _validated_archive_member(name, record_seen)
        records[name] = (digest, size)

    assert set(records) == set(payloads)
    for name, payload in payloads.items():
        digest, size = records[name]
        if name == record_name:
            assert digest == ""
            assert size == ""
            continue
        assert digest == f"sha256={_record_hash(payload)}"
        assert size == str(len(payload))


def _assert_exact_wheel_members(payloads, expected_wheel):
    dist_info_root = f"paperconan-{__version__}.dist-info"
    expected_metadata = {
        f"{dist_info_root}/{relative}"
        for relative in WHEEL_DIST_INFO_MEMBERS
    }
    assert set(payloads) == set(expected_wheel) | expected_metadata
    return dist_info_root


def _assert_built_release_archives(dist=ROOT / "dist"):
    sdists = sorted(dist.glob("paperconan-*.tar.gz"))
    wheels = sorted(dist.glob("paperconan-*.whl"))
    assert len(sdists) == 1, sdists
    assert len(wheels) == 1, wheels

    expected_sdist = _sdist_allowlist()
    expected_sdist_files = expected_sdist | SDIST_GENERATED_METADATA
    sdist_payloads = _sdist_payloads(
        sdists[0],
        expected_sdist_files,
    )
    assert set(sdist_payloads) == expected_sdist_files
    for relative in expected_sdist:
        assert sdist_payloads[relative] == (ROOT / relative).read_bytes()

    expected_wheel = {
        relative.removeprefix("src/"): (ROOT / relative).read_bytes()
        for relative in expected_sdist
        if relative.startswith("src/paperconan/")
    }
    wheel_payloads = _wheel_payloads(wheels[0])
    package_payloads = {
        name: payload
        for name, payload in wheel_payloads.items()
        if name.startswith("paperconan/")
    }
    assert package_payloads == expected_wheel

    dist_info_root = _assert_exact_wheel_members(
        wheel_payloads,
        expected_wheel,
    )
    license_name = f"{dist_info_root}/licenses/LICENSE"
    assert wheel_payloads[license_name] == (ROOT / "LICENSE").read_bytes()
    record_name = f"{dist_info_root}/RECORD"
    _assert_wheel_record(wheel_payloads, record_name)


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "../escape",
        "root/../escape",
        "./root/file",
        "root//file",
        r"root\file",
    ],
)
def test_archive_member_validation_rejects_unsafe_paths(name):
    with pytest.raises(AssertionError):
        _validated_archive_member(name, set())


def test_archive_member_validation_rejects_duplicates():
    seen = set()
    assert _validated_archive_member("root/file", seen) == (
        "root",
        "file",
    )
    with pytest.raises(AssertionError, match="duplicate"):
        _validated_archive_member("root/file", seen)


def _add_tar_directory(archive, name):
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    archive.addfile(member)


def _add_tar_file(archive, name, payload=b"x"):
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


@pytest.mark.parametrize(
    "malformation",
    ["multiple-root", "link", "extra-directory"],
)
def test_sdist_reader_rejects_structural_extras(
    tmp_path, malformation
):
    root = f"paperconan-{__version__}"
    path = tmp_path / f"{malformation}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_directory(archive, f"{root}/")
        _add_tar_file(archive, f"{root}/README.md")
        if malformation == "multiple-root":
            _add_tar_directory(archive, "other/")
        elif malformation == "link":
            member = tarfile.TarInfo(f"{root}/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "README.md"
            archive.addfile(member)
        else:
            _add_tar_directory(archive, f"{root}/unexpected/")

    with pytest.raises(AssertionError):
        _sdist_payloads(path, {"README.md"})


@pytest.mark.parametrize(
    ("malformation", "expected_message"),
    [
        pytest.param(
            "duplicate",
            "duplicate archive member",
            id="duplicate",
        ),
        pytest.param("directory", None, id="directory"),
        pytest.param("link", None, id="link"),
    ],
)
def test_wheel_reader_rejects_non_regular_or_duplicate_members(
    tmp_path, malformation, expected_message
):
    path = tmp_path / f"{malformation}.whl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            if malformation == "duplicate":
                for payload in (b"first", b"second"):
                    member = zipfile.ZipInfo("paperconan/module.py")
                    member.create_system = 3
                    member.external_attr = (
                        stat.S_IFREG | 0o644
                    ) << 16
                    archive.writestr(member, payload)
            elif malformation == "directory":
                archive.writestr("paperconan/", b"")
            else:
                member = zipfile.ZipInfo("paperconan/link")
                member.create_system = 3
                member.external_attr = (
                    stat.S_IFLNK | 0o777
                ) << 16
                archive.writestr(member, "target")

    with pytest.raises(AssertionError, match=expected_message):
        _wheel_payloads(path)


def test_exact_wheel_members_rejects_extra_metadata():
    dist_info_root = f"paperconan-{__version__}.dist-info"
    expected_wheel = {"paperconan/module.py": b"source"}
    payloads = {
        **expected_wheel,
        **{
            f"{dist_info_root}/{name}": b""
            for name in WHEEL_DIST_INFO_MEMBERS
        },
        f"{dist_info_root}/unexpected.json": b"extra",
    }

    with pytest.raises(AssertionError):
        _assert_exact_wheel_members(payloads, expected_wheel)


@pytest.mark.parametrize(
    "malformation",
    ["bad-hash", "bad-size", "duplicate"],
)
def test_wheel_record_rejects_invalid_rows(malformation):
    record_name = (
        f"paperconan-{__version__}.dist-info/RECORD"
    )
    if malformation == "bad-hash":
        rows = [
            "paperconan/module.py,sha256=incorrect,6",
            f"{record_name},,",
        ]
    elif malformation == "bad-size":
        digest = _record_hash(b"source")
        rows = [
            f"paperconan/module.py,sha256={digest},7",
            f"{record_name},,",
        ]
    else:
        digest = _record_hash(b"source")
        rows = [
            f"paperconan/module.py,sha256={digest},6",
            f"paperconan/module.py,sha256={digest},6",
            f"{record_name},,",
        ]
    record = ("\n".join(rows) + "\n").encode("utf-8")
    payloads = {
        "paperconan/module.py": b"source",
        record_name: record,
    }

    with pytest.raises(AssertionError):
        _assert_wheel_record(payloads, record_name)
```

The sdist reader validates every member before filtering, rejects duplicate or
multiple-root archives, unexpected directories, and every
non-file/non-directory member; allows only the explicit generated metadata
set; and compares every allowlisted member byte-for-byte with the working-tree
file. The wheel reader rejects unsafe, duplicate, directory, and non-regular
members; requires exactly the tracked `src/paperconan/**` payload plus the
fixed `.dist-info` allowlist; verifies the license bytes; and validates every
`RECORD` hash and size. Synthetic tar/zip tests exercise each rejection path
without depending on a pre-existing `dist/`.

- [ ] **Step 4: Run all focused tests under strict warnings**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_resource_budget.py \
  tests/test_findings_cap.py \
  tests/test_resource_lifetime.py \
  tests/test_relations_tolerance.py \
  tests/test_detector_coverage.py \
  tests/test_collisions.py \
  tests/test_cross_sheet_summaries.py \
  tests/test_module_boundaries.py \
  tests/test_skill_docs.py
.venv/bin/python -m pytest -W error -q tests/test_packaging.py -k \
  'archive_member_validation or sdist_reader or wheel_reader or exact_wheel_members or wheel_record'
```

Expected: all focused tests and every synthetic archive rejection test pass
with no warnings.

- [ ] **Step 5: Commit documentation and governance tests**

```bash
git add docs/cli.md \
  skills/paperconan/references/output-schema.md \
  tests/test_skill_docs.py tests/test_packaging.py
git commit -m "docs: explain detector-owned resource limits"
```

- [ ] **Step 6: Run both complete test entry points**

Run:

```bash
.venv/bin/python -m pytest -q
uv run --frozen pytest -q
```

Expected: both suites pass with one intentional live-network skip.

- [ ] **Step 7: Run lock, build, and archive gates**

Run:

```bash
set -e
uv lock --check
rm -rf dist
build_output="$(uv build 2>&1)"
printf '%s\n' "$build_output"
if printf '%s\n' "$build_output" | grep -qi 'warning:'; then
  printf 'uv build emitted warning output\n' >&2
  exit 1
fi
.venv/bin/python -c \
  'from tests.test_packaging import _assert_built_release_archives; _assert_built_release_archives()'
./build_skill_zip.sh /tmp/paperconan-skill-final.zip
unzip -t /tmp/paperconan-skill-final.zip
```

Expected:

- lock is current;
- build exits zero without warnings;
- the actual `dist/` sdist and wheel contain only safe, unique, exact allowed
  members; every source/package payload is byte-identical to the working-tree
  file; and wheel `RECORD` hashes and sizes are valid;
- Skill ZIP has one safe root and passes integrity.

- [ ] **Step 8: Run repository hygiene gates**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_packaging.py
git diff --check
git status --short --untracked-files=all
```

Remove only generated artifacts created by this plan. Do not remove unrelated
user files. Expected tracked status: clean after committed changes; ignored
`.pytest_cache/` may remain.

- [ ] **Step 9: Append the execution record**

Append the following evidence to the ignored local report:

```text
- each focused RED command and its intended failure;
- each focused GREEN command and pass count;
- both complete-suite pass counts;
- lock/build/archive/hygiene results;
- commit list for Tasks 1-6;
- exact task-review and whole-branch review findings/fixes.
```

Update `.superpowers/sdd/progress.md` with the final Wave 4 state. Verify both
files remain ignored:

```bash
git check-ignore -v \
  .superpowers/sdd/final-review-fix-wave-4-report.md \
  .superpowers/sdd/progress.md
```

- [ ] **Step 10: Request an independent task review**

Generate a fresh package:

```bash
git diff --binary 28cacb2..HEAD \
  > .superpowers/sdd/review-28cacb2..HEAD.diff
```

Ask a fresh reviewer to inspect the task diff for Critical, Important, and
Minor findings. Fix every severity with a focused RED/GREEN cycle, append the
record, and repeat with a fresh reviewer until all three severities are zero.

- [ ] **Step 11: Request a whole-branch review**

Generate:

```bash
git diff --binary 6bbcb00..HEAD \
  > .superpowers/sdd/review-6bbcb00..HEAD.diff
```

Ask the whole-branch reviewer to verify the complete hardening branch against
the approved project-hardening design and repository rules. Fix every severity
and repeat until the whole-branch review is clear.

- [ ] **Step 12: Re-run final verification after the last review fix**

Run exactly:

```bash
set -e
.venv/bin/python -m pytest -q
uv run --frozen pytest -q
uv lock --check
rm -rf dist
build_output="$(uv build 2>&1)"
printf '%s\n' "$build_output"
if printf '%s\n' "$build_output" | grep -qi 'warning:'; then
  printf 'uv build emitted warning output\n' >&2
  exit 1
fi
.venv/bin/python -c \
  'from tests.test_packaging import _assert_built_release_archives; _assert_built_release_archives()'
./build_skill_zip.sh /tmp/paperconan-skill-final.zip
unzip -t /tmp/paperconan-skill-final.zip
git diff --check
git status --short --untracked-files=all
```

Expected: both suites pass with one intentional network skip, all release
gates pass, generated tracked artifacts are absent, and the branch contains
only intentional committed changes.
