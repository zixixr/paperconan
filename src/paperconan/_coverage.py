from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ScanStatus = Literal["complete", "partial", "failed"]


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

    def add_limitation(self, scope: str, reason: str, **details: Any) -> None:
        item = {"scope": scope, "reason": reason}
        item.update({k: v for k, v in details.items() if v is not None})
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
        if self.sheets_succeeded == 0:
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
        return {
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
