# Phase 0a Block Duplication HTML Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make existing `block_value_duplication` findings visible in the default HTML report without changing detector output, severity, profile behavior, or other report groups.

**Architecture:** P0a is a default-HTML-only opt-in. Preserve the shared legacy `_PER_BLOCK_GROUPS` default so consumers of `_iter_block_findings()` and `_all_findings()`—including adjudicated rendering—retain their existing behavior. Define an ordered immutable default-HTML group tuple that adds `block_dups`, and have only `write_html_report()` select it. P0b, a possible unified finding registry, remains a separate project.

**Tech Stack:** Python 3.10–3.12, pytest, PaperConan's existing dictionary scan schema and self-contained HTML renderer.

## Global Constraints

- Use neutral language: describe statistical signals, data inconsistencies, and requests for clarification; do not make author-intent claims.
- Use only synthetic inline scan data; do not add real paper data, DOI, judgment, or source workbook.
- P0a changes only default HTML visibility. Do not change detector math, `severity`, profile projection, `scan.json`, Markdown output, packet behavior, or adjudicated-report behavior.
- Do not implement the P0b unified finding registry in this change.
- Preserve deterministic output and all existing public finding kinds and fields.
- Follow strict TDD: observe the default HTML regression RED before production code. The adjudicated boundary regression was added after review exposed coupling through the shared helper; use it to prevent that coupling from returning.

---

### Task 1: Establish default HTML and adjudicated-rendering boundaries

**Files:**

- Modify: `tests/test_block_value_duplication.py`
- Modify: `tests/test_adjudicated_report.py`

**Interfaces/results:**

- `relations_blocks[*].block_dups` items use the normal finding fields (`kind`, `severity`, `rule`, and optional `evidence`).
- `write_html_report(scan, out_path)` must render those items in the default HTML report.
- Plain `_all_findings(scan)` retains its legacy per-block groups and does **not** yield `block_dups`; adjudicated rendering therefore remains unchanged and a `block_value_duplication` reference remains unmatched there.

- [ ] **Step 1: Add the default HTML regression and verify RED**

In `tests/test_block_value_duplication.py`, import `write_html_report` and add a synthetic scan with one `relations_blocks` entry containing one `block_dups` item. Write it with `write_html_report()` and assert that its `kind` and synthetic `rule` occur in the HTML.

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py::test_default_html_includes_block_value_duplication \
  -q
```

Expected: the assertion is RED before the renderer opts in to `block_dups`.

- [ ] **Step 2: Record the discovered adjudicated boundary regression**

After review exposes that a naive change to the shared tuple affects `_all_findings()` consumers, add a synthetic test in `tests/test_adjudicated_report.py`. Give the scan a `block_dups` item and the verdict a `finding_ref` with `{"kind": "block_value_duplication"}`. Assert the adjudicated HTML does not render the finding kind or synthetic rule and instead shows its existing unmatched-evidence message.

With the initially naive implementation that adds `block_dups` directly to `_PER_BLOCK_GROUPS`, run:

```bash
.venv/bin/python -m pytest \
  tests/test_adjudicated_report.py::test_modern_block_duplication_ref_remains_unmatched_in_adjudicated_report \
  -q
```

Expected: RED, demonstrating the shared-helper coupling. This regression is intentionally added after review exposed that boundary; it is not evidence that adjudicated behavior should expand in P0a.

---

### Task 2: Scope the renderer opt-in to default HTML

**Files:**

- Modify: `src/paperconan/_html.py`

- [ ] **Step 3: Implement the scoped group selection**

Keep the existing ordered immutable legacy tuple unchanged. Define a second immutable tuple for default HTML by appending `"block_dups"`. Let both helper APIs take a keyword-only `per_block_groups` selector that defaults to the legacy tuple, and pass that selector through from `_all_findings()` to `_iter_block_findings()`. Make only `write_html_report()` call `_all_findings()` with the default-HTML tuple.

The intended shape is:

```python
_PER_BLOCK_GROUPS = (...legacy groups...)
_DEFAULT_HTML_PER_BLOCK_GROUPS = _PER_BLOCK_GROUPS + ("block_dups",)

def _iter_block_findings(scan, *, per_block_groups=_PER_BLOCK_GROUPS):
    ...

def _all_findings(scan, *, per_block_groups=_PER_BLOCK_GROUPS):
    for blk, finding in _iter_block_findings(
        scan, per_block_groups=per_block_groups
    ):
        ...

def write_html_report(scan, out_path):
    findings = _all_findings(
        scan, per_block_groups=_DEFAULT_HTML_PER_BLOCK_GROUPS
    )
```

Do not add `block_dups` directly to shared `_PER_BLOCK_GROUPS`, add a second renderer, alter the scan schema, or make an adjudicated renderer opt in.

- [ ] **Step 4: Verify both boundaries are GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py::test_default_html_includes_block_value_duplication \
  tests/test_adjudicated_report.py::test_modern_block_duplication_ref_remains_unmatched_in_adjudicated_report \
  -q
```

Expected: both assertions pass: default HTML contains the synthetic finding, while the adjudicated report still treats its reference as unmatched.

---

### Task 3: Run focused verification and commit the scoped implementation

- [ ] **Step 5: Run focused report and policy regressions**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_block_value_duplication.py \
  tests/test_adjudicated_report.py \
  tests/test_smoke.py \
  tests/test_skill_docs.py \
  -q
```

Expected: selected tests pass, including neutral-language policy checks. Do not hard-code aggregate test totals if they are brittle.

- [ ] **Step 6: Run the full suite and inspect the implementation patch**

Run:

```bash
.venv/bin/python -m pytest
git diff --check
git diff -- src/paperconan/_html.py tests/test_block_value_duplication.py tests/test_adjudicated_report.py
```

Expected: the implementation touches exactly the renderer and the two focused test files; the renderer preserves legacy helper defaults and scopes `block_dups` to default HTML.

- [ ] **Step 7: Commit P0a implementation in its actual reviewable slices**

The implementation may be committed as two code commits: first the default HTML test and initial renderer visibility change, then the renderer-boundary correction and adjudicated regression. Do not falsely require a single code commit. Keep this corrective plan update as its own documentation commit.

Suggested commands:

```bash
git add src/paperconan/_html.py tests/test_block_value_duplication.py
git commit -m "fix: include block duplication signals in html"

git add src/paperconan/_html.py tests/test_adjudicated_report.py
git commit -m "fix: scope block duplication to default html"
```

Expected: across the two implementation commits, only three runtime/test files change. The final behavior is default-HTML visibility with no detector, severity, profile, scan, Markdown, packet, or adjudicated-report behavior change.
