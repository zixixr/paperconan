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
