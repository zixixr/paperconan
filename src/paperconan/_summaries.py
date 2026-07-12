from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import heapq
import math
from typing import Any

from ._profiles import apply_profile_to_findings


@dataclass(frozen=True)
class SparseLabelContext:
    nrows: int
    ncols: int
    text: dict[tuple[int, int], str]

    def cell(self, row, col):
        if 0 <= row < self.nrows and 0 <= col < self.ncols:
            return self.text.get((row, col))
        return None


@dataclass(frozen=True)
class ColumnFingerprint:
    file: str
    sheet: str
    col_idx: int
    label: str
    length: int
    digest: str
    all_int: bool
    distinct: int
    sample: tuple[int | float, ...]


@dataclass(frozen=True)
class CrossSheetSummary:
    file: str
    sheet: str
    grid: dict[tuple[int, int], float]
    labels: SparseLabelContext
    columns: tuple[ColumnFingerprint, ...]


def _canonical_vector_value(value):
    if isinstance(value, int):
        return value
    numeric = float(value)
    if math.isfinite(numeric) and numeric.is_integer():
        return int(numeric)
    return round(numeric, 6)


def _vector_is_patterned(vec):
    vec = tuple(_canonical_vector_value(value) for value in vec)
    if len(set(vec)) < 3:
        return True
    differences = [vec[i + 1] - vec[i] for i in range(len(vec) - 1)]
    if all(abs(value - differences[0]) < 1e-9 for value in differences):
        return True
    nonzero = [value for value in vec if abs(value) > 1e-12]
    if len(nonzero) == len(vec):
        if all(isinstance(value, int) for value in vec):
            ratios = [
                Fraction(vec[i + 1], vec[i])
                for i in range(len(vec) - 1)
            ]
            same_ratio = all(value == ratios[0] for value in ratios)
        else:
            ratios = [
                vec[i + 1] / vec[i]
                for i in range(len(vec) - 1)
            ]
            same_ratio = all(
                abs(value - ratios[0]) < 1e-9
                for value in ratios
            )
        if same_ratio:
            return True
    return all(
        (
            value % 10 == 0
            if isinstance(value, int)
            else abs(value - round(value / 10.0) * 10.0) < 1e-9
        )
        for value in vec
    )


def _numeric_value(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return None


def _numeric_run_lengths(row):
    remaining = [0] * len(row)
    run_length = 0
    for index in range(len(row) - 1, -1, -1):
        if row[index] is None:
            run_length = 0
        else:
            run_length += 1
        remaining[index] = run_length
    return remaining


def _valid_window_count(run_lengths, min_k, max_k):
    total = 0
    for start, available in enumerate(run_lengths):
        if available < min_k:
            continue
        total += min(max_k, available) - min_k + 1
    return total


def _iter_valid_window_specs(run_lengths, min_k, max_k, limit):
    emitted = 0
    for start, available in enumerate(run_lengths):
        for width in range(min_k, min(max_k, available) + 1):
            if emitted >= limit:
                return
            emitted += 1
            yield start, width


def _materialize_window(row, start, width):
    return tuple(
        _canonical_vector_value(value)
        for value in row[start:start + width]
    )


def _iter_block_rows(blocks, max_rows):
    pending = []
    for block_index, (r0, r1, c0, c1) in enumerate(blocks):
        row_end = min(r1, r0 + max_rows)
        if r0 < row_end:
            heapq.heappush(
                pending,
                (r0, block_index, row_end, c0, c1),
            )
    while pending:
        row_idx, block_index, row_end, c0, c1 = heapq.heappop(
            pending
        )
        yield row_idx, c0, c1
        next_row = row_idx + 1
        if next_row < row_end:
            heapq.heappush(
                pending,
                (next_row, block_index, row_end, c0, c1),
            )


def _add_bounded_representative(values, value, limit=6):
    values.add(value)
    if len(values) > limit:
        values.remove(max(values))


@dataclass(slots=True)
class _RecurringVectorRecord:
    site_count: int
    file_min: str
    file_max: str
    sheet_min: str
    sheet_max: str
    last_row_token: tuple[int, int] | None = None
    sites: list[tuple[str, str, int, int]] = field(
        default_factory=list
    )
    figures: set[str] = field(default_factory=set)
    figures_lower_bound: bool = False
    file_representatives: set[str] = field(default_factory=set)
    sheet_representatives: set[str] = field(default_factory=set)


def _record_location(record, file, sheet):
    record.file_min = min(record.file_min, file)
    record.file_max = max(record.file_max, file)
    record.sheet_min = min(record.sheet_min, sheet)
    record.sheet_max = max(record.sheet_max, sheet)
    _add_bounded_representative(record.file_representatives, file)
    _add_bounded_representative(record.sheet_representatives, sheet)


def _record_figure(record, figure_id, limit=16):
    if figure_id in record.figures:
        return
    if len(record.figures) < limit:
        record.figures.add(figure_id)
    else:
        record.figures_lower_bound = True


def _location_names(record, prefix):
    return sorted({
        *getattr(record, f"{prefix}_representatives"),
        getattr(record, f"{prefix}_min"),
        getattr(record, f"{prefix}_max"),
    })


class RecurringRowIndex:
    def __init__(self, budget=3_000_000, unique_budget=100_000):
        self._initial_budget = max(0, int(budget))
        self._budget = self._initial_budget
        self._initial_unique_budget = max(0, int(unique_budget))
        self._vectors: dict[
            tuple[int | float, ...], _RecurringVectorRecord
        ] = {}
        self._sheet_sequence = 0
        self._skipped_new_vector_windows = 0

    @property
    def initial_budget(self):
        return self._initial_budget

    @property
    def initial_unique_budget(self):
        return self._initial_unique_budget

    def unique_budget_metadata(self):
        exhausted = self._skipped_new_vector_windows > 0
        return {
            "budget_exhausted": exhausted,
            "limit": self._initial_unique_budget,
            "vectors_retained": len(self._vectors),
            "skipped_new_vector_windows": (
                self._skipped_new_vector_windows
            ),
            "skipped_new_vectors_lower_bound": (
                1 if exhausted else 0
            ),
        }

    def add_sheet(
        self,
        file,
        sheet,
        source,
        *,
        blocks,
        figure_id,
        min_k=4,
        max_k=8,
        max_rows=300,
    ) -> dict[str, int | bool]:
        if figure_id is None:
            return {
                "budget_exhausted": False,
                "windows_skipped": 0,
            }
        self._sheet_sequence += 1
        sheet_sequence = self._sheet_sequence
        windows_skipped = 0
        for row_idx, c0, c1 in _iter_block_rows(
            blocks, max_rows
        ):
            row = [
                _numeric_value(source.cell(row_idx, col_idx))
                for col_idx in range(c0, c1)
            ]
            run_lengths = _numeric_run_lengths(row)
            valid_windows = _valid_window_count(
                run_lengths,
                min_k,
                max_k,
            )
            accepted = min(self._budget, valid_windows)
            windows_skipped += valid_windows - accepted
            if accepted == 0:
                continue
            for start, width in _iter_valid_window_specs(
                run_lengths,
                min_k,
                max_k,
                accepted,
            ):
                self._budget -= 1
                vector = _materialize_window(row, start, width)
                site = (file, sheet, row_idx, c0 + start)
                record = self._vectors.get(vector)
                if record is None:
                    if (
                        len(self._vectors)
                        >= self._initial_unique_budget
                    ):
                        self._skipped_new_vector_windows += 1
                        continue
                    record = _RecurringVectorRecord(
                        site_count=0,
                        file_min=file,
                        file_max=file,
                        sheet_min=sheet,
                        sheet_max=sheet,
                    )
                    self._vectors[vector] = record
                row_token = (sheet_sequence, row_idx)
                if record.last_row_token != row_token:
                    record.last_row_token = row_token
                    record.site_count += 1
                    _record_location(record, file, sheet)
                    _record_figure(record, figure_id)
                if len(record.sites) < 16 and site not in record.sites:
                    record.sites.append(site)
        return {
            "budget_exhausted": windows_skipped > 0,
            "windows_skipped": windows_skipped,
        }

    def findings(
        self, profile="review", max_findings=20
    ) -> tuple[list[dict], dict[str, int]]:
        candidates = []
        for vector, record in self._vectors.items():
            site_count = record.site_count
            if site_count < 3 or _vector_is_patterned(vector):
                continue
            figures = record.figures
            if len(figures) < 2:
                continue
            all_int = all(
                isinstance(value, int)
                or abs(value - round(value)) < 1e-9
                for value in vector
            )
            if all_int and (
                len(vector) < 5
                or len(set(vector)) < 4
            ):
                continue
            sites = record.sites
            if site_count < 3:
                continue
            cells = {
                (file, sheet, row, start_col + offset)
                for file, sheet, row, start_col in sites
                for offset in range(len(vector))
            }
            candidates.append((vector, record, cells))

        candidates.sort(
            key=lambda candidate: (
                -candidate[1].site_count,
                -len(candidate[0]),
            )
        )
        kept = []
        for candidate in candidates:
            cells = candidate[2]
            if any(
                len(cells & prior[2])
                >= 0.5 * min(len(cells), len(prior[2]))
                for prior in kept
            ):
                continue
            kept.append(candidate)

        findings = []
        for vector, record, _cells in kept:
            sheets_hit = _location_names(record, "sheet")
            files_hit = _location_names(record, "file")
            site_count = record.site_count
            figures = record.figures
            location = "; ".join(sheets_hit[:6])
            figure_count = len(figures)
            figure_text = (
                f"at least {figure_count}"
                if record.figures_lower_bound
                else str(figure_count)
            )
            finding = dict(
                kind="recurring_row_vector",
                file="; ".join(files_hit)[:120],
                file_a=record.file_min,
                file_b=record.file_max,
                same_file=record.file_min == record.file_max,
                sheet="; ".join(sheets_hit)[:120],
                sheet_a=record.sheet_min,
                sheet_b=record.sheet_max,
                vector=list(vector),
                size_a=site_count,
                size_b=site_count,
                same_position_count=site_count,
                fraction_of_smaller=1.0,
                n_occurrences=site_count,
                n_figures=figure_count,
                same_figure=False,
                delta={"pattern": "recurring_row_vector"},
                pattern="recurring_row_vector",
                examples=[{"value": value} for value in vector],
                severity=(
                    "high"
                    if len(vector) >= 5 and site_count >= 3
                    else "medium"
                ),
                rule=(
                    f"the {len(vector)}-value vector {list(vector)} recurs at "
                    f"{site_count} places across {figure_text} figures "
                    f"({location})"
                ),
            )
            if record.figures_lower_bound:
                finding["n_figures_lower_bound"] = True
            findings.append(finding)

        limit = max(0, int(max_findings))
        omitted = max(0, len(findings) - limit)
        findings = findings[:limit]
        apply_profile_to_findings(findings, profile)
        return findings, {"findings_omitted": omitted}
