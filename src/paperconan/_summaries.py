from __future__ import annotations

from collections import Counter
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


def _recurring_candidate_qualifies(vector, record):
    site_count = record.site_count
    if site_count < 3 or _vector_is_patterned(vector):
        return False
    if len(record.figures) < 2:
        return False
    all_int = all(
        isinstance(value, int)
        or abs(value - round(value)) < 1e-9
        for value in vector
    )
    if all_int and (
        len(vector) < 5
        or len(set(vector)) < 4
    ):
        return False
    return True


def _recurring_candidate_cells(vector, record):
    return frozenset(
        (file, sheet, row, start_col + offset)
        for file, sheet, row, start_col in record.sites
        for offset in range(len(vector))
    )


def _iter_indexed_candidate_ids(cells, cell_index):
    pending = []
    postings = []
    for cell in sorted(cells):
        matches = cell_index.get(cell)
        if not matches:
            continue
        posting_index = len(postings)
        postings.append(matches)
        heapq.heappush(
            pending,
            (matches[0], posting_index, 0),
        )
    while pending:
        candidate_id = pending[0][0]
        while pending and pending[0][0] == candidate_id:
            _candidate_id, posting_index, offset = heapq.heappop(
                pending
            )
            next_offset = offset + 1
            matches = postings[posting_index]
            if next_offset < len(matches):
                heapq.heappush(
                    pending,
                    (
                        matches[next_offset],
                        posting_index,
                        next_offset,
                    ),
                )
        yield candidate_id


def _recurring_finding(vector, record):
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
    return finding


class RecurringRowIndex:
    def __init__(
        self,
        budget=3_000_000,
        unique_budget=100_000,
        finalization_candidate_budget=10_000,
        finalization_pair_budget=200_000,
        finalization_cell_budget=1_000_000,
    ):
        self._initial_budget = max(0, int(budget))
        self._budget = self._initial_budget
        self._initial_unique_budget = max(0, int(unique_budget))
        self._finalization_candidate_budget = max(
            0, int(finalization_candidate_budget)
        )
        self._finalization_pair_budget = max(
            0, int(finalization_pair_budget)
        )
        self._finalization_cell_budget = max(
            0, int(finalization_cell_budget)
        )
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
        windows_skipped_is_lower_bound = False
        row_specs = iter(_iter_block_rows(blocks, max_rows))
        while True:
            try:
                row_idx, c0, c1 = next(row_specs)
            except StopIteration:
                break
            if c1 - c0 < min_k:
                continue
            if self._budget <= 0:
                windows_skipped_is_lower_bound = True
                break
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
        metadata = {
            "budget_exhausted": (
                windows_skipped > 0
                or windows_skipped_is_lower_bound
            ),
            "windows_skipped": windows_skipped,
        }
        if windows_skipped_is_lower_bound:
            metadata["windows_skipped_is_lower_bound"] = True
        return metadata

    def findings(
        self, profile="review", max_findings=20
    ) -> tuple[list[dict], dict[str, Any]]:
        candidate_heap = []
        qualifying_candidates = 0
        candidate_limit = self._finalization_candidate_budget
        for order, (vector, record) in enumerate(
            self._vectors.items()
        ):
            if not _recurring_candidate_qualifies(vector, record):
                continue
            qualifying_candidates += 1
            if candidate_limit <= 0:
                continue
            quality = (
                record.site_count,
                len(vector),
                -order,
            )
            candidate = (quality, order, vector, record)
            if len(candidate_heap) < candidate_limit:
                heapq.heappush(candidate_heap, candidate)
            elif quality > candidate_heap[0][0]:
                heapq.heapreplace(candidate_heap, candidate)

        candidates = [
            (vector, record, order)
            for _quality, order, vector, record in candidate_heap
        ]
        candidates.sort(
            key=lambda candidate: (
                -candidate[1].site_count,
                -len(candidate[0]),
                candidate[2],
            )
        )
        candidates_omitted = (
            qualifying_candidates - len(candidates)
        )
        limits_reached = []
        if candidates_omitted:
            limits_reached.append("candidate")

        cell_index = {}
        kept_cells = []
        findings = []
        limit = max(0, int(max_findings))
        findings_omitted = 0
        definite_omissions = 0
        pair_comparisons = 0
        cell_references_retained = 0
        candidates_processed = 0
        finalization_stopped = False
        for vector, record, _order in candidates:
            cells = _recurring_candidate_cells(vector, record)
            overlaps_prior = False
            for candidate_id in _iter_indexed_candidate_ids(
                cells, cell_index
            ):
                if (
                    pair_comparisons
                    >= self._finalization_pair_budget
                ):
                    if "pair" not in limits_reached:
                        limits_reached.append("pair")
                    finalization_stopped = True
                    break
                pair_comparisons += 1
                prior_cells = kept_cells[candidate_id]
                if (
                    len(cells & prior_cells)
                    >= 0.5 * min(len(cells), len(prior_cells))
                ):
                    overlaps_prior = True
                    break
            if finalization_stopped:
                break
            if overlaps_prior:
                candidates_processed += 1
                continue
            if (
                cell_references_retained + len(cells)
                > self._finalization_cell_budget
            ):
                if "cell" not in limits_reached:
                    limits_reached.append("cell")
                definite_omissions += 1
                finalization_stopped = True
                break

            candidate_id = len(kept_cells)
            kept_cells.append(cells)
            for cell in cells:
                cell_index.setdefault(cell, []).append(candidate_id)
            cell_references_retained += len(cells)
            candidates_processed += 1
            if len(findings) < limit:
                findings.append(
                    _recurring_finding(vector, record)
                )
            else:
                findings_omitted += 1

        findings_omitted += definite_omissions
        apply_profile_to_findings(findings, profile)
        if not limits_reached:
            return findings, {
                "findings_omitted": findings_omitted
            }
        return findings, {
            "findings_omitted": findings_omitted,
            "findings_omitted_is_lower_bound": True,
            "finalization_limitation": {
                "candidate_limit": (
                    self._finalization_candidate_budget
                ),
                "pair_limit": self._finalization_pair_budget,
                "cell_limit": self._finalization_cell_budget,
                "qualifying_candidates": qualifying_candidates,
                "candidates_retained": len(candidates),
                "candidates_omitted": candidates_omitted,
                "candidates_processed": candidates_processed,
                "pair_comparisons": pair_comparisons,
                "cell_references_retained": (
                    cell_references_retained
                ),
                "limits_reached": limits_reached,
                "omitted_findings_lower_bound": findings_omitted,
            },
        }


@dataclass(slots=True)
class _WithinRowWindowRecord:
    order: int
    non_overlapping_count: int = 0
    last_end: int = -1
    starts: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _WithinRowCandidate:
    vector: tuple[int | float, ...]
    file: str
    sheet: str
    figure_id: str | None
    row_idx: int
    occurrence_count: int
    cells: frozenset[int]
    start_cols: tuple[int, ...]
    end_cols: tuple[int, ...]
    order: int


def _within_row_candidate_qualifies(
    vector,
    record,
    row_frequency,
):
    if (
        record.non_overlapping_count < 2
        or len(set(vector)) < 3
        or _vector_is_patterned(vector)
    ):
        return False
    all_int = all(isinstance(value, int) for value in vector)
    if all_int and (
        len(vector) < 5
        or len(set(vector)) < 4
    ):
        return False
    vector_values = set(vector)
    if (
        len(vector_values) <= 4
        and len(row_frequency) <= len(vector_values) + 1
        and min(
            row_frequency[value] for value in vector_values
        ) >= 3
    ):
        return False
    return max(
        row_frequency[value] for value in vector
    ) <= 2 * record.non_overlapping_count


def _within_row_finding(candidate):
    vector = candidate.vector
    occurrence_count = candidate.occurrence_count
    matched_cells = len(vector) * occurrence_count
    figure_id = candidate.figure_id
    start_cols = [
        col_idx + 1 for col_idx in candidate.start_cols
    ]
    end_cols = [
        col_idx + 1 for col_idx in candidate.end_cols
    ]
    column_text = ", ".join(str(value) for value in start_cols)
    return dict(
        kind="within_row_repeated_segment",
        file=candidate.file,
        file_a=candidate.file,
        file_b=candidate.file,
        same_file=True,
        sheet=candidate.sheet,
        sheet_a=candidate.sheet,
        sheet_b=candidate.sheet,
        same_sheet=True,
        vector=list(vector),
        row=candidate.row_idx + 1,
        start_cols=start_cols,
        end_cols=end_cols,
        occurrences=[
            {
                "row": candidate.row_idx + 1,
                "start_col": start_col + 1,
                "end_col": end_col + 1,
            }
            for start_col, end_col in zip(
                candidate.start_cols,
                candidate.end_cols,
            )
        ],
        size_a=matched_cells,
        size_b=matched_cells,
        same_position_count=matched_cells,
        fraction_of_smaller=1.0,
        n_occurrences=occurrence_count,
        figure_a=figure_id,
        figure_b=figure_id,
        same_figure=figure_id is not None,
        delta={"pattern": "within_row_repeat"},
        pattern="within_row_repeat",
        examples=[{"value": value} for value in vector],
        severity="high",
        rule=(
            f"the {len(vector)}-value segment {list(vector)} appears at "
            f"{occurrence_count} non-overlapping positions within row "
            f"{candidate.row_idx + 1} of {candidate.sheet}, starting at "
            f"columns {column_text}"
        ),
        **(
            {"occurrence_positions_truncated": True}
            if occurrence_count > len(candidate.start_cols)
            else {}
        ),
    )


class WithinRowRepeatIndex:
    def __init__(
        self,
        budget=2_000_000,
        unique_budget=100_000,
        candidate_budget=10_000,
        row_cell_limit=20_000,
        finalization_pair_budget=200_000,
        finalization_cell_budget=1_000_000,
    ):
        self._initial_budget = max(0, int(budget))
        self._budget = self._initial_budget
        self._unique_budget = max(0, int(unique_budget))
        self._candidate_budget = max(0, int(candidate_budget))
        self._row_cell_limit = max(0, int(row_cell_limit))
        self._finalization_pair_budget = max(
            0, int(finalization_pair_budget)
        )
        self._finalization_cell_budget = max(
            0, int(finalization_cell_budget)
        )
        self._candidate_heap = []
        self._candidates_seen = 0
        self._candidate_sequence = 0
        self._candidate_limit_exhausted = False
        self._candidate_omissions_lower_bound = False
        self._finalization_limits_reached = []
        self._finalization_stopped = False
        self._pair_comparisons = 0
        self._cell_references_retained = 0

    @property
    def initial_budget(self):
        return self._initial_budget

    @property
    def unique_budget(self):
        return self._unique_budget

    @property
    def candidate_budget(self):
        return self._candidate_budget

    @property
    def row_cell_limit(self):
        return self._row_cell_limit

    def _candidate_quality(
        self,
        occurrence_count,
        vector_length,
        order,
    ):
        return (occurrence_count, vector_length, -order)

    def _candidate_would_be_retained(self, quality):
        return (
            len(self._candidate_heap) < self._candidate_budget
            or quality > self._candidate_heap[0][0]
        )

    def _retain_candidate(self, candidate, quality):
        item = (quality, candidate.order, candidate)
        if len(self._candidate_heap) < self._candidate_budget:
            heapq.heappush(self._candidate_heap, item)
        else:
            heapq.heapreplace(self._candidate_heap, item)

    def _stop_finalization(self, dimension):
        if dimension not in self._finalization_limits_reached:
            self._finalization_limits_reached.append(dimension)
        self._finalization_stopped = True
        self._candidate_omissions_lower_bound = True

    def _scan_row(
        self,
        file,
        sheet,
        figure_id,
        row_idx,
        sequence,
        *,
        min_k,
        max_k,
        accepted_windows,
    ):
        row_frequency = Counter(
            _canonical_vector_value(value)
            for _col_idx, value in sequence
        )
        windows = {}
        window_order = 0
        skipped_new_windows = 0
        run_lengths = [
            len(sequence) - start
            for start in range(len(sequence))
        ]
        for start, width in _iter_valid_window_specs(
            run_lengths,
            min_k,
            max_k,
            accepted_windows,
        ):
            vector = tuple(
                _canonical_vector_value(sequence[start + offset][1])
                for offset in range(width)
            )
            record = windows.get(vector)
            if record is None:
                if len(windows) >= self._unique_budget:
                    skipped_new_windows += 1
                    continue
                record = _WithinRowWindowRecord(order=window_order)
                window_order += 1
                windows[vector] = record
            if start >= record.last_end:
                record.non_overlapping_count += 1
                record.last_end = start + width
                if len(record.starts) < 16:
                    record.starts.append(start)

        qualifying = []
        for vector, record in windows.items():
            if not _within_row_candidate_qualifies(
                vector,
                record,
                row_frequency,
            ):
                continue
            qualifying.append((vector, record))

        qualifying.sort(
            key=lambda item: (
                -item[1].non_overlapping_count,
                -len(item[0]),
                item[1].order,
            )
        )
        row_metadata = {
            "skipped_new_windows": skipped_new_windows,
            "max_unique_vectors_retained": len(windows),
        }
        if not qualifying:
            return row_metadata
        if self._candidate_budget <= 0:
            self._candidate_limit_exhausted = True
            self._candidate_omissions_lower_bound = True
            return row_metadata
        if self._finalization_stopped:
            self._candidate_omissions_lower_bound = True
            return row_metadata
        kept_cells = []
        cell_index = {}
        for candidate_index, (vector, record) in enumerate(
            qualifying
        ):
            order = self._candidate_sequence
            self._candidate_sequence += 1
            quality = self._candidate_quality(
                record.non_overlapping_count,
                len(vector),
                order,
            )
            heap_full = (
                len(self._candidate_heap)
                >= self._candidate_budget
            )
            if (
                heap_full
                and not self._candidate_would_be_retained(quality)
            ):
                self._candidate_limit_exhausted = True
                if not kept_cells:
                    self._candidates_seen += 1
                if kept_cells or candidate_index + 1 < len(qualifying):
                    self._candidate_omissions_lower_bound = True
                break
            if (
                kept_cells
                and self._pair_comparisons
                >= self._finalization_pair_budget
            ):
                self._stop_finalization("pair")
                break
            candidate_cell_count = (
                len(vector) * len(record.starts)
            )
            if (
                self._cell_references_retained
                + candidate_cell_count
                > self._finalization_cell_budget
            ):
                if not kept_cells:
                    self._candidates_seen += 1
                self._stop_finalization("cell")
                break
            cells = frozenset(
                sequence[start + offset][0]
                for start in record.starts
                for offset in range(len(vector))
            )
            overlaps_prior = False
            for candidate_id in _iter_indexed_candidate_ids(
                cells,
                cell_index,
            ):
                if (
                    self._pair_comparisons
                    >= self._finalization_pair_budget
                ):
                    self._stop_finalization("pair")
                    break
                self._pair_comparisons += 1
                prior_cells = kept_cells[candidate_id]
                if (
                    len(cells & prior_cells)
                    >= 0.5 * min(len(cells), len(prior_cells))
                ):
                    overlaps_prior = True
                    break
            if self._finalization_stopped:
                break
            if overlaps_prior:
                continue
            self._candidates_seen += 1
            if heap_full:
                self._candidate_limit_exhausted = True
            start_cols = tuple(
                sequence[start][0] for start in record.starts
            )
            end_cols = tuple(
                sequence[start + len(vector) - 1][0]
                for start in record.starts
            )
            candidate = _WithinRowCandidate(
                vector=vector,
                file=file,
                sheet=sheet,
                figure_id=figure_id,
                row_idx=row_idx,
                occurrence_count=record.non_overlapping_count,
                cells=cells,
                start_cols=start_cols,
                end_cols=end_cols,
                order=order,
            )
            candidate_id = len(kept_cells)
            kept_cells.append(cells)
            for cell in cells:
                cell_index.setdefault(cell, []).append(candidate_id)
            self._cell_references_retained += len(cells)
            self._retain_candidate(candidate, quality)
        return row_metadata

    def add_sheet(
        self,
        file,
        sheet,
        source,
        *,
        figure_id,
        min_k=4,
        max_k=8,
    ) -> dict[str, int | bool]:
        min_k = max(1, int(min_k))
        max_k = max(min_k, int(max_k))
        windows_examined = 0
        windows_skipped = 0
        windows_skipped_is_lower_bound = False
        skipped_new_windows = 0
        max_unique_vectors_retained = 0
        rows_limited = 0
        numeric_cells_skipped_lower_bound = 0

        for row_idx in range(source.nrows):
            if self._budget <= 0:
                windows_skipped_is_lower_bound = (
                    source.nrows > row_idx
                    and source.ncols >= min_k
                )
                break
            sequence = []
            row_limited = False
            for col_idx in range(source.ncols):
                value = _numeric_value(source.cell(row_idx, col_idx))
                if value is None:
                    continue
                if len(sequence) >= self._row_cell_limit:
                    row_limited = True
                    numeric_cells_skipped_lower_bound += 1
                    break
                sequence.append((col_idx, value))
            if row_limited:
                rows_limited += 1
                continue
            if len(sequence) < min_k:
                continue

            run_lengths = [
                len(sequence) - start
                for start in range(len(sequence))
            ]
            valid_windows = _valid_window_count(
                run_lengths,
                min_k,
                max_k,
            )
            accepted = min(self._budget, valid_windows)
            windows_examined += accepted
            windows_skipped += valid_windows - accepted
            if accepted:
                row_metadata = self._scan_row(
                    file,
                    sheet,
                    figure_id,
                    row_idx,
                    sequence,
                    min_k=min_k,
                    max_k=max_k,
                    accepted_windows=accepted,
                )
                skipped_new_windows += row_metadata[
                    "skipped_new_windows"
                ]
                max_unique_vectors_retained = max(
                    max_unique_vectors_retained,
                    row_metadata["max_unique_vectors_retained"],
                )
            self._budget -= accepted
            if accepted < valid_windows:
                windows_skipped_is_lower_bound = True
                break

        budget_exhausted = (
            windows_skipped > 0
            or windows_skipped_is_lower_bound
        )
        return {
            "windows_examined": windows_examined,
            "budget_exhausted": budget_exhausted,
            "windows_skipped": windows_skipped,
            "windows_skipped_is_lower_bound": (
                windows_skipped_is_lower_bound
            ),
            "unique_budget_exhausted": skipped_new_windows > 0,
            "unique_limit": self._unique_budget,
            "max_unique_vectors_retained": (
                max_unique_vectors_retained
            ),
            "skipped_new_windows": skipped_new_windows,
            "row_cell_limit_exhausted": rows_limited > 0,
            "row_cell_limit": self._row_cell_limit,
            "rows_limited": rows_limited,
            "numeric_cells_skipped_lower_bound": (
                numeric_cells_skipped_lower_bound
            ),
        }

    def findings(
        self,
        profile="review",
        max_findings=20,
    ) -> tuple[list[dict], dict[str, Any]]:
        candidates = [
            candidate
            for _quality, _order, candidate in self._candidate_heap
        ]
        candidates.sort(
            key=lambda candidate: (
                -candidate.occurrence_count,
                -len(candidate.vector),
                candidate.order,
            )
        )
        output_limit = max(0, int(max_findings))
        retained = len(candidates)
        findings = [
            _within_row_finding(candidate)
            for candidate in candidates[:output_limit]
        ]
        apply_profile_to_findings(findings, profile)

        candidate_findings_omitted = (
            self._candidates_seen - retained
        )
        output_findings_omitted = max(
            0,
            retained - len(findings),
        )
        metadata = {
            "candidate_findings_omitted": (
                candidate_findings_omitted
            ),
            "output_findings_omitted": output_findings_omitted,
        }
        if self._candidate_limit_exhausted:
            metadata.update({
                "candidate_limit": self._candidate_budget,
                "candidates_seen": self._candidates_seen,
                "candidates_retained": retained,
            })
        elif candidate_findings_omitted:
            metadata.update({
                "candidates_seen": self._candidates_seen,
                "candidates_retained": retained,
            })
        if self._candidate_omissions_lower_bound:
            metadata[
                "candidate_findings_omitted_is_lower_bound"
            ] = True
        if self._finalization_limits_reached:
            metadata["finalization_limitation"] = {
                "pair_limit": self._finalization_pair_budget,
                "cell_limit": self._finalization_cell_budget,
                "candidates_seen": self._candidates_seen,
                "candidates_retained": retained,
                "pair_comparisons": self._pair_comparisons,
                "cell_references_retained": (
                    self._cell_references_retained
                ),
                "limits_reached": list(
                    self._finalization_limits_reached
                ),
                "omitted_findings_lower_bound": (
                    candidate_findings_omitted
                ),
            }
        if output_findings_omitted:
            metadata.update({
                "output_limit": output_limit,
                "candidates_retained": retained,
            })
        return findings, metadata
