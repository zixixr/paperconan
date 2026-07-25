# Phase 0a Block Duplication HTML Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make existing `block_value_duplication` findings visible in the default HTML report without changing detector output, severity, profile behavior, or other report groups.

**Architecture:** Keep P0a as a one-line compatibility repair in the existing per-block HTML iterator. Add a real rendering regression test first, then add `block_dups` to `_PER_BLOCK_GROUPS`; the unified finding registry remains a separate P0b project.

**Tech Stack:** Python 3.10–3.12, pytest, PaperConan's existing dictionary scan schema and self-contained HTML renderer.

## Global Constraints

- Use neutral language: describe statistical signals, data inconsistencies, and requests for clarification; do not make author-intent claims.
- Use only synthetic inline scan data; do not add real paper data, DOI, judgment, or source workbook.
- P0a changes only default HTML visibility. Do not change detector math, `severity`, profile projection, `scan.json`, Markdown output, packet behavior, or adjudicated-report behavior.
- Do not implement the P0b unified finding registry in this change.
- Preserve deterministic output and all existing public finding kinds and fields.
- Follow strict TDD: observe the new regression test fail for the missing HTML finding before editing production code.

---

### Task 1: Include `block_dups` in the existing default HTML finding iterator

**Files:**
- Modify: `tests/test_block_value_duplication.py`
- Modify: `src/paperconan/_html.py:52-60`

**Interfaces:**
- Consumes: existing per-block scan field `relations_blocks[*].block_dups`, whose items use the normal finding fields `kind`, `severity`, `rule`, and optional `evidence`.
- Produces: `_iter_block_findings(scan)` yields `block_dups` items, so existing `_all_findings(scan)` and `write_html_report(scan, out_path)` render them without a special renderer.

- [ ] **Step 1: Add the real HTML regression test**

Add the HTML writer import beside the existing detector imports:

```python
from paperconan._html import write_html_report
```

Append this test to `tests/test_block_value_duplication.py`:

```python
def test_default_html_includes_block_value_duplication(tmp_path):
    scan = {
        "tool_version": "0.test",
        "profile": "review",
        "input_dir": "synthetic",
        "n_files": 1,
        "relations_blocks": [{
            "file": "synthetic.xlsx",
            "sheet": "Panel",
            "block": {
                "rows": "2-6",
                "cols": "1-3",
                "header": ["a", "b", "c"],
            },
            "block_dups": [{
                "kind": "block_value_duplication",
                "severity": "medium",
                "rule": "synthetic distributed exact-value collision signal",
                "profile_action": "kept",
            }],
        }],
        "cross_sheet_findings": [],
        "digit_distribution": [],
        "decimal_endings": [],
        "decimal_tail_clusters": [],
    }
    report = tmp_path / "report.html"

    write_html_report(scan, str(report))

    html = report.read_text(encoding="utf-8")
    assert "block_value_duplication" in html
    assert "synthetic distributed exact-value collision signal" in html
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py::test_default_html_includes_block_value_duplication \
  -q
```

Expected: one assertion fails because `_PER_BLOCK_GROUPS` does not yet contain `block_dups`, so the rendered HTML does not contain `block_value_duplication`.

- [ ] **Step 3: Make the minimal production change**

In `src/paperconan/_html.py`, change the existing tuple to:

```python
_PER_BLOCK_GROUPS = (
    "relations",
    "progressions",
    "equal_pairs",
    "row_pairs",
    "row_relations",
    "within_col",
    "identical_after_rounding",
    "grim",
    "block_dups",
)
```

Do not add a second iteration path or special-case rendering logic.

- [ ] **Step 4: Run the regression test and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py::test_default_html_includes_block_value_duplication \
  -q
```

Expected: `1 passed`.

- [ ] **Step 5: Run focused report and policy regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py \
  tests/test_smoke.py \
  tests/test_skill_docs.py \
  -q
```

Expected: all selected tests pass, including the neutral-language policy.

- [ ] **Step 6: Run the full suite and inspect the patch**

Run:

```bash
.venv/bin/python -m pytest
git diff --check
git diff -- src/paperconan/_html.py tests/test_block_value_duplication.py
```

Expected: the full suite passes with only the existing live-network skip; the code diff contains one test import, one regression test, and the `block_dups` tuple entry.

- [ ] **Step 7: Commit P0a only**

Run:

```bash
git add src/paperconan/_html.py tests/test_block_value_duplication.py
git commit -m "fix: include block duplication signals in html"
```

Expected: one commit containing only the two P0a files. The implementation-plan document may be committed separately before execution, but it must not be folded into the P0a code commit.
