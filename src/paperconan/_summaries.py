from __future__ import annotations

from dataclasses import dataclass
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


def _vector_is_patterned(vec):
    if len({round(v, 6) for v in vec}) < 3:
        return True
    differences = [vec[i + 1] - vec[i] for i in range(len(vec) - 1)]
    if all(abs(value - differences[0]) < 1e-9 for value in differences):
        return True
    nonzero = [value for value in vec if abs(value) > 1e-12]
    if len(nonzero) == len(vec):
        ratios = [vec[i + 1] / vec[i] for i in range(len(vec) - 1)]
        if all(abs(value - ratios[0]) < 1e-9 for value in ratios):
            return True
    return all(
        abs(value - round(value / 10.0) * 10.0) < 1e-9
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


class RecurringRowIndex:
    def __init__(self, budget=3_000_000):
        self._budget = max(0, int(budget))
        self._vectors: dict[tuple[float, ...], dict[str, Any]] = {}

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
        windows_skipped = 0
        for r0, r1, c0, c1 in blocks:
            for row_idx in range(r0, min(r1, r0 + max_rows)):
                row = [
                    _numeric_value(source.cell(row_idx, col_idx))
                    for col_idx in range(c0, c1)
                ]
                for start in range(len(row)):
                    for width in range(min_k, max_k + 1):
                        window = row[start:start + width]
                        if (
                            len(window) < width
                            or any(value is None for value in window)
                        ):
                            continue
                        if self._budget <= 0:
                            windows_skipped += 1
                            continue
                        self._budget -= 1
                        vector = tuple(round(float(value), 6) for value in window)
                        site = (file, sheet, row_idx, c0 + start)
                        record = self._vectors.setdefault(
                            vector,
                            {
                                "vector": vector,
                                "site_count": 0,
                                "sites": set(),
                                "figures": set(),
                            },
                        )
                        record["site_count"] += 1
                        if len(record["sites"]) < 16:
                            record["sites"].add(site)
                        if figure_id is not None:
                            record["figures"].add(figure_id)
        return {
            "budget_exhausted": windows_skipped > 0,
            "windows_skipped": windows_skipped,
        }

    def findings(
        self, profile="review", max_findings=20
    ) -> tuple[list[dict], dict[str, int]]:
        candidates = []
        for vector, record in self._vectors.items():
            site_count = record["site_count"]
            if site_count < 3 or _vector_is_patterned(vector):
                continue
            figures = record["figures"]
            if len(figures) < 2:
                continue
            all_int = all(abs(value - round(value)) < 1e-9 for value in vector)
            if all_int and (
                len(vector) < 5
                or len({round(value, 6) for value in vector}) < 4
            ):
                continue
            sites = record["sites"]
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
                -candidate[1]["site_count"],
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
            sites = record["sites"]
            sheets_hit = sorted({site[1] for site in sites})
            files_hit = sorted({site[0] for site in sites})
            site_count = record["site_count"]
            figures = record["figures"]
            location = "; ".join(sheets_hit[:6])
            findings.append(dict(
                kind="recurring_row_vector",
                file="; ".join(files_hit)[:120],
                file_a=files_hit[0],
                file_b=files_hit[-1],
                same_file=len(files_hit) == 1,
                sheet="; ".join(sheets_hit)[:120],
                sheet_a=sheets_hit[0],
                sheet_b=sheets_hit[-1],
                vector=[float(value) for value in vector],
                size_a=site_count,
                size_b=site_count,
                same_position_count=site_count,
                fraction_of_smaller=1.0,
                n_occurrences=site_count,
                n_figures=len(figures),
                same_figure=False,
                delta={"pattern": "recurring_row_vector"},
                pattern="recurring_row_vector",
                examples=[{"value": float(value)} for value in vector],
                severity=(
                    "high"
                    if len(vector) >= 5 and site_count >= 3
                    else "medium"
                ),
                rule=(
                    f"the {len(vector)}-value vector {list(vector)} recurs at "
                    f"{site_count} places across {len(figures)} figures "
                    f"({location})"
                ),
            ))

        limit = max(0, int(max_findings))
        omitted = max(0, len(findings) - limit)
        findings = findings[:limit]
        apply_profile_to_findings(findings, profile)
        return findings, {"findings_omitted": omitted}
