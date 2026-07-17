"""Explicit scan-coverage accounting.

A scan can legitimately leave work undone — an oversized file skipped to bound
memory, an unreadable workbook, an oversized sheet, or output/finding budgets
that truncate block analysis. Without an explicit record, a *partial* scan reads
as a *clean* one, which silently understates what was and was not examined.

``ScanCoverage`` accumulates those events from the orchestration control flow and
serialises a compact, bounded summary into ``scan.json`` so reports (and the
downstream corpus pipeline) can tell ``complete`` from ``partial`` from
``failed``. It records only what the scanner reached — never a judgement about
the data or its authors.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal


ScanStatus = Literal["complete", "partial", "failed"]


def _limitations_cap() -> int:
    """Upper bound on retained limitation records (env-tunable, deterministic).

    Bounds ``scan.json`` / report size on pathological inputs (tens of thousands
    of skipped sheets), consistent with the other ``PAPERCONAN_MAX_*`` guards.
    """
    raw = os.environ.get("PAPERCONAN_MAX_LIMITATIONS", "500")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 500
    return value if value > 0 else 500


@dataclass
class ScanCoverage:
    files_discovered: int
    files_succeeded: int = 0
    files_failed: int = 0
    sheets_succeeded: int = 0
    sheets_skipped: int = 0
    blocks_analyzed: int = 0
    blocks_skipped: int = 0
    limitations: list[dict[str, Any]] = field(default_factory=list)
    _limitation_keys: set[tuple] = field(default_factory=set, repr=False)
    _limitations_dropped: int = field(default=0, repr=False)

    def add_limitation(self, scope: str, reason: str, **details: Any) -> None:
        item = {"scope": scope, "reason": reason}
        item.update({k: v for k, v in details.items() if v is not None})
        # Deduplicate identical (scope, reason, file, sheet) events so a repeated
        # cause records once, and cap the retained list so a pathological input
        # cannot balloon the report (GH#15-class size guard).
        key = (scope, reason, item.get("file"), item.get("sheet"))
        if key in self._limitation_keys:
            return
        if len(self.limitations) >= _limitations_cap():
            self._limitations_dropped += 1
            return
        self._limitation_keys.add(key)
        self.limitations.append(item)

    def mark_file_succeeded(self) -> None:
        self.files_succeeded += 1

    def mark_file_failed(self, file: str, reason: str, **details: Any) -> None:
        self.files_failed += 1
        self.add_limitation("file", reason, file=file, **details)

    def mark_sheet_succeeded(self) -> None:
        self.sheets_succeeded += 1

    def mark_sheet_skipped(
        self, file: str, sheet: str, reason: str, **details: Any
    ) -> None:
        self.sheets_skipped += 1
        self.add_limitation("sheet", reason, file=file, sheet=sheet, **details)

    def mark_block_analyzed(self, count: int = 1) -> None:
        if count > 0:
            self.blocks_analyzed += count

    def mark_blocks_skipped(
        self, count: int, *, scope: str, reason: str, **details: Any
    ) -> None:
        if count <= 0:
            return
        self.blocks_skipped += count
        self.add_limitation(scope, reason, count=count, **details)

    @property
    def status(self) -> ScanStatus:
        if self.files_discovered and self.files_succeeded == 0:
            return "failed"
        if (
            self.files_failed
            or self.sheets_skipped
            or self.blocks_skipped
            or self.limitations
        ):
            return "partial"
        return "complete"

    def to_dict(self) -> dict[str, Any]:
        truncated = bool(
            self.blocks_skipped
            or any(
                str(item.get("reason") or "").endswith("_limit")
                for item in self.limitations
            )
        )
        payload = {
            "files_discovered": self.files_discovered,
            "files_succeeded": self.files_succeeded,
            "files_failed": self.files_failed,
            "sheets_succeeded": self.sheets_succeeded,
            "sheets_skipped": self.sheets_skipped,
            "blocks_analyzed": self.blocks_analyzed,
            "blocks_skipped": self.blocks_skipped,
            "truncated": truncated,
            "limitations": list(self.limitations),
        }
        if self._limitations_dropped:
            payload["limitations_omitted"] = self._limitations_dropped
        return payload
