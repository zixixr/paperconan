# Scan Coverage and Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scan completion, coverage limits, CLI status, report messaging,
and profile behavior explicit and internally consistent.

**Architecture:** Add a small `ScanCoverage` state object owned by `scan_dir`.
All loaders and detector-budget decisions record structured limitations through
that object. JSON, CLI, HTML, and Markdown derive their status from the same
serialized fields.

**Tech Stack:** Python 3.10+, dataclasses, pytest, existing HTML renderer.

## Global Constraints

- Use neutral statistical-signal and data-inconsistency language.
- Preserve all existing `scan.json` fields and add `schema_version`,
  `scan_status`, and `coverage`.
- Archived scans without new fields continue to render.
- `partial` scans return CLI status zero; `failed` scans write diagnostics and
  return nonzero.
- `forensic` keeps original severity and `profile_action="kept"` through every
  later demotion pass.
- Every production change follows a verified red-green cycle.

---

### Task 1: Define Scan Coverage State

**Files:**
- Create: `src/paperconan/_coverage.py`
- Create: `tests/test_scan_coverage.py`

**Interfaces:**
- `ScanCoverage(files_discovered: int)`
- `mark_file_succeeded() -> None`
- `mark_file_failed(file: str, reason: str, **details: Any) -> None`
- `mark_sheet_succeeded() -> None`
- `mark_sheet_skipped(file: str, sheet: str, reason: str,
  **details: Any) -> None`
- `mark_block_analyzed(count: int = 1) -> None`
- `mark_blocks_skipped(count: int, *, scope: str, reason: str,
  **details: Any) -> None`
- `add_limitation(scope: str, reason: str, **details: Any) -> None`
- `status -> Literal["complete", "partial", "failed"]`
- `to_dict() -> dict`

- [ ] **Step 1: Write failing state tests**

Create `tests/test_scan_coverage.py`:

```python
from paperconan._coverage import ScanCoverage


def test_complete_coverage_has_no_limitations():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_block_analyzed()
    assert coverage.status == "complete"
    assert coverage.to_dict()["truncated"] is False


def test_partial_coverage_requires_some_success_and_a_limitation():
    coverage = ScanCoverage(files_discovered=2)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_file_failed("bad.xlsx", "parse_error")
    assert coverage.status == "partial"
    out = coverage.to_dict()
    assert out["files_failed"] == 1
    assert out["limitations"][0]["reason"] == "parse_error"


def test_failed_coverage_has_no_successful_sheet():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_failed("bad.xlsx", "parse_error")
    assert coverage.status == "failed"


def test_skipped_blocks_are_counted_and_mark_truncation():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_blocks_skipped(
        4, scope="sheet", reason="report_block_limit", file="a.csv", sheet="S"
    )
    out = coverage.to_dict()
    assert out["blocks_skipped"] == 4
    assert out["truncated"] is True
    assert coverage.status == "partial"
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_scan_coverage.py -q
```

Expected: import failure because `_coverage.py` does not exist.

- [ ] **Step 3: Implement the coverage object**

Create `src/paperconan/_coverage.py`:

```python
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

    def mark_sheet_skipped(self, file: str, sheet: str,
                           reason: str, **details: Any) -> None:
        self.sheets_skipped += 1
        self.add_limitation(
            "sheet", reason, file=file, sheet=sheet, **details
        )

    def mark_block_analyzed(self, count: int = 1) -> None:
        self.blocks_analyzed += count

    def mark_blocks_skipped(self, count: int, *, scope: str,
                            reason: str, **details: Any) -> None:
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
```

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_scan_coverage.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_coverage.py tests/test_scan_coverage.py
git commit -m "feat: model scan coverage explicitly"
```

---

### Task 2: Integrate Coverage Into `scan_dir`

**Files:**
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_scan_status.py`
- Modify: `tests/test_findings_cap.py`
- Modify: `tests/test_oversized_guard.py`
- Modify: `tests/test_cell_guard.py`

**Interfaces:**
- New top-level output:
  - `"schema_version": 2`
  - `"scan_status": coverage.status`
  - `"coverage": coverage.to_dict()`
- Existing `scan_errors` and `findings_omitted` remain.

- [ ] **Step 1: Write complete, partial, and failed scan tests**

Create `tests/test_scan_status.py`:

```python
import json
import subprocess
import sys

from paperconan._audit import scan_dir


def _write_good_csv(path):
    path.write_text("a,b\n1,2\n2,3\n3,4\n4,5\n", encoding="utf-8")


def test_complete_scan_status(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["schema_version"] == 2
    assert scan["scan_status"] == "complete"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["sheets_succeeded"] == 1


def test_partial_scan_status_when_one_file_fails(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 1


def test_failed_scan_status_when_every_file_fails(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["sheets_succeeded"] == 0


def test_cli_writes_failed_scan_then_returns_nonzero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "failed"


def test_cli_empty_input_writes_failed_scan_then_returns_nonzero(tmp_path):
    data = tmp_path / "empty"
    data.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["files_discovered"] == 0
```

- [ ] **Step 2: Add cap accounting tests**

Extend `tests/test_findings_cap.py` with a scan containing at least two finding
blocks, monkeypatch `_MAX_REPORT_BLOCKS = 1`, and assert:

```python
assert scan["scan_status"] == "partial"
assert scan["coverage"]["blocks_skipped"] >= 1
assert any(
    item["reason"] == "report_block_limit"
    for item in scan["coverage"]["limitations"]
)
```

In the existing dense-block test, also assert:

```python
assert scan["scan_status"] == "partial"
assert any(
    item["reason"] == "finding_limit"
    and item.get("omitted_findings", 0) > 0
    for item in scan["coverage"]["limitations"]
)
```

Extend oversized and cell-guard tests to assert their existing error also
appears as `file_size_limit` or `cell_limit` in `coverage.limitations`.

- [ ] **Step 3: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_scan_status.py \
  tests/test_findings_cap.py \
  tests/test_oversized_guard.py \
  tests/test_cell_guard.py -q
```

Expected: missing status and coverage keys; failed CLI still returns zero.

- [ ] **Step 4: Wire `ScanCoverage` through scanning**

Add an opt-in compatibility switch:

```python
def scan_dir(
    in_dir,
    out_dir,
    *,
    write_md=False,
    write_html=True,
    paper=None,
    profile="review",
    write_json=True,
    evidence=True,
    diagnostic_on_empty=False,
):
```

The default preserves the current library behavior and raises
`PaperconanInputError` for an empty directory. The CLI passes
`diagnostic_on_empty=True`; that path creates a normal schema-v2 failed result
with zero discovered files, writes the requested outputs, and returns it.

After file discovery:

```python
coverage = ScanCoverage(files_discovered=len(files))
```

Update branches:

- file byte limit:

```python
coverage.mark_file_failed(
    os.path.basename(f), "file_size_limit", max_bytes=_MAX_FILE_BYTES
)
```

- loader exception:

```python
coverage.mark_file_failed(os.path.basename(f), "parse_error")
```

- successful `load_table`:

```python
coverage.mark_file_succeeded()
```

- skipped oversized sheet:

```python
coverage.mark_sheet_skipped(
    os.path.basename(f), sn, "cell_limit", max_cells=_MAX_CELLS
)
```

- processable sheet:

```python
coverage.mark_sheet_succeeded()
```

For the block loop use an index. Before each analyzed block:

```python
coverage.mark_block_analyzed()
```

When `_MAX_REPORT_BLOCKS` or `_MAX_TOTAL_FINDINGS` stops collection, calculate
the number of remaining blocks, record it once for that sheet, and call:

```python
coverage.mark_blocks_skipped(
    len(blocks) - block_index,
    scope="sheet",
    reason="report_block_limit",
    file=os.path.basename(f),
    sheet=sn,
)
```

Use `finding_limit` for the global finding-budget reason.

After `_cap_block_findings`, record per-block omission without counting the
whole block as skipped:

```python
if omitted:
    coverage.add_limitation(
        "block",
        "finding_limit",
        file=os.path.basename(f),
        sheet=sn,
        rows=f"{r0 + 1}-{r1}",
        cols=f"{c0 + 1}-{c1}",
        omitted_findings=omitted,
        limit=block_cap,
    )
```

Add output keys:

```python
schema_version=2,
scan_status=coverage.status,
coverage=coverage.to_dict(),
```

- [ ] **Step 5: Return nonzero for a failed CLI scan**

Pass `diagnostic_on_empty=True` from `main`. After `scan_dir` writes outputs and
before success messaging:

```python
if res["scan_status"] == "failed":
    print("scan failed: no input table reached numeric scanning", file=sys.stderr)
    raise SystemExit(1)
```

The library function continues to return the diagnostic object.

- [ ] **Step 6: Run focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_scan_coverage.py \
  tests/test_scan_status.py \
  tests/test_findings_cap.py \
  tests/test_oversized_guard.py \
  tests/test_cell_guard.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/paperconan/_audit.py tests/test_scan_status.py \
  tests/test_findings_cap.py tests/test_oversized_guard.py \
  tests/test_cell_guard.py
git commit -m "feat: expose scan completion and coverage"
```

---

### Task 3: Record Detector-Path Limitations

**Files:**
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_detector_coverage.py`

**Interfaces:**
- Coverage limitation reasons:
  - `wide_block_detector_limit`
  - `row_pair_dimension_limit`
  - `row_pair_finding_limit`
  - `collision_row_limit`
  - `finding_limit`
  - `report_block_limit`

- [ ] **Step 1: Add wide-block and row-pair tests**

Create `tests/test_detector_coverage.py`:

```python
import paperconan._audit as audit
from paperconan._sheet import Sheet


def _reasons(scan):
    return {item["reason"] for item in scan["coverage"]["limitations"]}


def test_wide_block_detector_skip_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_MAX_BLOCK_COLS", 2)
    data = tmp_path / "data"
    data.mkdir()
    (data / "wide.csv").write_text(
        "a,b,c\n1,2,3\n2,3,4\n3,4,5\n4,5,6\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "partial"
    assert "wide_block_detector_limit" in _reasons(scan)


def test_row_pair_dimension_skip_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_ROW_PAIR_MAX_ROWS", 3)
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "a,b\n1.1,2.1\n2.2,3.2\n3.3,4.3\n4.4,5.4\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert "row_pair_dimension_limit" in _reasons(scan)


def test_row_pair_finding_cap_is_disclosed(tmp_path, monkeypatch):
    original = audit.detect_row_pair_digit_coupling

    def capped(*args, **kwargs):
        if kwargs.get("with_coverage"):
            return (
                [{
                    "kind": "row_pair_digit_coupling",
                    "severity": "high",
                    "rule": "synthetic capped row pair",
                    "n": 3,
                    "row_a_idx": 1,
                    "row_b_idx": 2,
                    "example_cells": [],
                }],
                {"findings_omitted": 3},
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, "detect_row_pair_digit_coupling", capped)
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "a,b,c\n1.1,2.1,3.1\n2.2,3.2,4.2\n3.3,4.3,5.3\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert "row_pair_finding_limit" in _reasons(scan)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_detector_coverage.py -q
```

Expected: limitation reasons are absent.

- [ ] **Step 3: Register detector skips before calls**

When `wide` is true, add one block limitation containing:

```python
coverage.add_limitation(
    "block",
    "wide_block_detector_limit",
    file=os.path.basename(f),
    sheet=sn,
    rows=f"{r0 + 1}-{r1}",
    cols=f"{c0 + 1}-{c1}",
    detectors=["relations", "equal_pairs", "row_pairs"],
    max_cols=_MAX_BLOCK_COLS,
)
```

When the block exceeds row-pair dimensions but is not already wide, add:

```python
coverage.add_limitation(
    "block",
    "row_pair_dimension_limit",
    file=os.path.basename(f),
    sheet=sn,
    rows=r1 - r0,
    cols=c1 - c0,
    max_rows=_ROW_PAIR_MAX_ROWS,
    max_cols=_ROW_PAIR_MAX_COLS,
)
```

Do not count detector-path limitations as skipped blocks because other
detectors still analyze the block. They still make the overall status partial.

- [ ] **Step 4: Expose row-pair finding and collision-row truncation**

Add `with_coverage=False` as a keyword-only parameter to the existing function
signature. Keep the detector body unchanged through its existing
`findings.sort` call, then replace the final slice return with:

```python
omitted = max(0, len(findings) - _ROW_PAIR_MAX_FINDINGS_PER_BLOCK)
kept = findings[:_ROW_PAIR_MAX_FINDINGS_PER_BLOCK]
if with_coverage:
    return kept, {"findings_omitted": omitted}
return kept
```

`scan_dir` calls it with `with_coverage=True`. When `findings_omitted > 0`, add
`row_pair_finding_limit` with the block location, cap, and omitted count.

Add `with_coverage=False` as a keyword-only parameter to `_grid_from_rows`.
Keep the current grid-building body, then replace its final `return grid` with:

```python
meta = {
    "rows_total": sheet.nrows,
    "rows_used": rmax,
    "row_limited": sheet.nrows > rmax,
}
return (grid, meta) if with_coverage else grid
```

`scan_dir` calls it with `with_coverage=True`. When `row_limited`, add a
`collision_row_limit` limitation with total/used rows. Existing direct callers
keep receiving the grid dictionary.

- [ ] **Step 5: Run focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_detector_coverage.py \
  tests/test_collisions.py \
  tests/test_decimal_tail_gate.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_audit.py tests/test_detector_coverage.py
git commit -m "feat: disclose detector coverage limits"
```

---

### Task 4: Make Reports Render Completion State

**Files:**
- Modify: `src/paperconan/_html.py`
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_report_status.py`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `docs/reports.md`

**Interfaces:**
- `_render_scan_status(scan) -> str`
- Legacy scan without `scan_status` renders a coverage-unavailable notice.

- [ ] **Step 1: Add report-status tests**

Create `tests/test_report_status.py`:

```python
from paperconan._html import write_html_report


def _scan(status, limitations=None):
    return {
        "tool": "paperconan",
        "tool_version": "test",
        "profile": "review",
        "input_dir": "data",
        "n_files": 1,
        "n_blocks_with_findings": 0,
        "findings_omitted": 0,
        "scan_errors": [],
        "scan_stats": {"files": [], "sheets": [], "elapsed_ms": None},
        "relations_blocks": [],
        "digit_distribution": [],
        "decimal_endings": [],
        "cross_sheet_findings": [],
        "scan_status": status,
        "coverage": {
            "truncated": bool(limitations),
            "limitations": limitations or [],
        },
    }


def _render(tmp_path, scan):
    out = tmp_path / "report.html"
    write_html_report(scan, str(out))
    return out.read_text(encoding="utf-8")


def test_failed_report_does_not_claim_no_findings(tmp_path):
    html = _render(tmp_path, _scan("failed"))
    assert "scan failed" in html.lower()
    assert "nothing flagged in this dataset" not in html


def test_partial_report_lists_coverage_limit(tmp_path):
    html = _render(tmp_path, _scan(
        "partial",
        [{"scope": "file", "reason": "parse_error", "file": "bad.xlsx"}],
    ))
    assert "partial" in html.lower()
    assert "parse_error" in html


def test_legacy_scan_reports_unknown_detailed_coverage(tmp_path):
    scan = _scan("complete")
    scan.pop("scan_status")
    scan.pop("coverage")
    html = _render(tmp_path, scan)
    assert "legacy" in html.lower()
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_report_status.py -q
```

Expected: status banners are absent.

- [ ] **Step 3: Add HTML status rendering**

Implement `_render_scan_status`:

```python
def _render_scan_status(scan):
    status = scan.get("scan_status")
    coverage = scan.get("coverage") or {}
    if status is None:
        return (
            '<div class="warn status-legacy">'
            'Legacy scan: detailed coverage status is unavailable.</div>'
        )
    if status == "complete":
        return '<div class="status status-complete">scan complete</div>'
    limitations = coverage.get("limitations") or []
    items = "".join(
        f"<li>{_esc(item.get('reason'))}: "
        f"{_esc(item.get('file') or item.get('sheet') or item.get('scope'))}</li>"
        for item in limitations
    )
    return (
        f'<div class="warn status-{_esc(status)}">'
        f"scan { _esc(status) }<ul>{items}</ul></div>"
    )
```

Place the banner before findings. For `failed`, replace the normal empty state
with a message that no input reached numeric scanning. For `partial`, keep
findings but display limitations first.

- [ ] **Step 4: Add Markdown status**

At the beginning of `write_markdown_report`, include status and each coverage
limitation. A failed scan uses a dedicated section and does not describe the
empty finding list as a completed clean result.

- [ ] **Step 5: Update schema and report docs**

Document `schema_version`, `scan_status`, coverage counters, limitation objects,
CLI status behavior, and legacy handling.

- [ ] **Step 6: Run report tests**

```bash
.venv/bin/python -m pytest \
  tests/test_report_status.py \
  tests/test_smoke.py \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/paperconan/_html.py src/paperconan/_audit.py \
  tests/test_report_status.py skills/paperconan/references/output-schema.md \
  docs/reports.md
git commit -m "feat: surface scan completion in reports"
```

---

### Task 5: Preserve the `forensic` Profile End to End

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_profiles.py`
- Modify: `tests/test_progression_reuse.py`
- Modify: `tests/test_relations_flood.py`

**Interfaces:**
- `_demote_dense_sheets(
  report_blocks, cap=RELATION_FLOOD_CAP, profile="review")`
- `_demote_reused_progressions(report_blocks, profile="review")`

- [ ] **Step 1: Add failing end-to-end profile tests**

Add a direct dense-sheet test in `tests/test_relations_flood.py`:

```python
def test_forensic_dense_sheet_keeps_original_high_severity():
    half = RELATION_FLOOD_CAP // 2 + 5
    def relations():
        return [
            {
                "kind": "identical_column",
                "severity": "high",
                "rule": f"synthetic relation {i}",
            }
            for i in range(half)
        ]
    blocks = [
        {"file": "f.xlsx", "sheet": "S1",
         "relations": relations(), "equal_pairs": [],
         "within_col": []},
        {"file": "f.xlsx", "sheet": "S1",
         "relations": relations(), "equal_pairs": [],
         "within_col": []},
    ]
    _demote_dense_sheets(blocks, profile="forensic")
    findings = [f for block in blocks for f in block["relations"]]
    assert all(f["severity"] == "high" for f in findings)
    assert all(f.get("dense_block") is not True for f in findings)
```

Add a direct progression test in `tests/test_progression_reuse.py`:

```python
def test_forensic_reused_progression_remains_kept():
    blocks = [_block(f"Fig {i}", [_prog(0.5, 30, 1.25)]) for i in range(2)]
    for block in blocks:
        block["progressions"][0]["profile_action"] = "kept"
    _demote_reused_progressions(blocks, profile="forensic")
    findings = [block["progressions"][0] for block in blocks]
    assert all(f["severity"] == "high" for f in findings)
    assert all(f["profile_action"] == "kept" for f in findings)
    assert all(f.get("prefilter") != "drop" for f in findings)
```

Add to `tests/test_profiles.py`:

```python
def test_forensic_within_column_flood_remains_kept():
    import paperconan._audit as audit

    findings = [
        {
            "kind": "within_col_value_duplication",
            "severity": "high",
            "profile_action": "kept",
        }
        for _ in range(audit.WITHIN_COL_SHEET_CAP + 1)
    ]
    blocks = [{
        "file": "f.csv",
        "sheet": "S",
        "relations": [],
        "equal_pairs": [],
        "within_col": findings,
    }]
    audit._demote_dense_sheets(blocks, profile="forensic")
    assert all(f["severity"] == "high" for f in findings)
    assert all(f["profile_action"] == "kept" for f in findings)
    assert all(f.get("prefilter") != "drop" for f in findings)
```

Keep the existing review-profile tests to prove the demotions still apply
outside forensic mode.

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_profiles.py \
  tests/test_progression_reuse.py \
  tests/test_relations_flood.py -q
```

Expected: forensic findings are demoted by the later sheet-level passes.

- [ ] **Step 3: Gate every late demotion**

Change both sheet-level signatures exactly as shown and insert the guard as the
first executable statement; the existing function bodies follow the guard
unchanged:

```python
def _demote_dense_sheets(
    report_blocks, cap=RELATION_FLOOD_CAP, profile="review"
):
    if normalize_profile(profile) == "forensic":
        return report_blocks


def _demote_reused_progressions(report_blocks, profile="review"):
    if normalize_profile(profile) == "forensic":
        return report_blocks
```

Call them as:

```python
_demote_dense_sheets(report_blocks, profile=profile)
_demote_reused_progressions(report_blocks, profile=profile)
```

Ensure no later code assigns low severity, `prefilter="drop"`, or a non-kept
`profile_action` under forensic.

- [ ] **Step 4: Run profile tests**

```bash
.venv/bin/python -m pytest \
  tests/test_profiles.py \
  tests/test_progression_reuse.py \
  tests/test_relations_flood.py \
  tests/test_within_col_prefilter.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_profiles.py \
  tests/test_progression_reuse.py tests/test_relations_flood.py
git commit -m "fix: preserve forensic profile severity"
```

---

### Task 6: Coverage Regression Gate

**Files:**
- Modify only to address regressions in this component.

- [ ] **Step 1: Run all focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_scan_coverage.py \
  tests/test_scan_status.py \
  tests/test_detector_coverage.py \
  tests/test_report_status.py \
  tests/test_findings_cap.py \
  tests/test_oversized_guard.py \
  tests/test_cell_guard.py \
  tests/test_profiles.py \
  tests/test_progression_reuse.py \
  tests/test_relations_flood.py -q
```

Expected: all pass.

- [ ] **Step 2: Run the complete suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with only the intentional live-network skip.
