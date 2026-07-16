# PR 32 Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge current `main` into PR 32 without losing either branch's behavior, then eliminate every confirmed review issue with regression coverage.

**Architecture:** Use one integration merge because `main` and the PR have 58 and 146 unique commits respectively, with 48 overlapping files. Keep the PR's explicit coverage, resource, input, sidecar, and report-status architecture; forward-port `main`'s current detector, image, fetch, report, version, and runtime language-validation contracts. Each reported issue receives a failing test before production changes.

**Tech Stack:** Python 3.10+, NumPy, openpyxl, python-calamine, pdfplumber, pytest, uv, Bash 3.2-compatible release tooling.

## Global Constraints

- All user-facing and contributor-facing wording must describe statistical signals, data inconsistencies, unresolved evidence, or requests for clarification.
- Preserve deterministic output for identical input.
- Preserve `PAPERCONAN_MAX_FILE_MB`, `PAPERCONAN_MAX_CELLS`, sparse-cell, evidence, and detector resource ceilings.
- Do not add paper source data, DOI values, verdicts, credentials, `recheck/`, or `batches/` content.
- Preserve Python 3.10 compatibility and macOS Bash 3.2 compatibility.
- `scan.json` coverage/status fields and verdict evidence binding remain backward compatible.

---

### Task 1: Integrate Current Main Once

**Files:**
- Resolve: `README.md`
- Resolve: `docs/**`
- Resolve: `skills/paperconan/**`
- Resolve: `examples/**`
- Resolve: `pyproject.toml`
- Resolve: `uv.lock`
- Resolve: `src/paperconan/**`
- Resolve: `tests/**`

**Interfaces:**
- Consumes: `origin/main@a77ec9831e37` and PR head `abfc2ed1b20f647286fcfaf707784e8ee8f890a4`.
- Produces: one merge result containing both histories and the union of test files.

- [ ] **Step 1: Confirm the exact integration inputs**

Run:
```bash
git fetch origin main codex/project-hardening
git rev-list --left-right --count origin/main...HEAD
git status -sb
```

Expected: `58 146` (or a documented newer main count) and a clean worktree except this plan.

- [ ] **Step 2: Commit this implementation plan**

Run:
```bash
git add docs/superpowers/plans/2026-07-16-pr32-review-remediation.md
git commit -m "docs: plan PR review remediation"
```

- [ ] **Step 3: Merge main without rebasing 146 commits**

Run:
```bash
git merge --no-ff --no-commit origin/main
```

Expected: one merge operation with the known overlapping files; do not rebase.

- [ ] **Step 4: Resolve by subsystem, not by choosing one tree wholesale**

Preserve these PR modules and their callers:
```text
src/paperconan/_coverage.py
src/paperconan/_input.py
src/paperconan/_numeric.py
src/paperconan/_resources.py
src/paperconan/_source_sidecar.py
src/paperconan/_summaries.py
```

Preserve these main additions:
```text
src/paperconan/_neutral_language.py
src/paperconan/image/__init__.py
src/paperconan/image/_assets.py
src/paperconan/image/_budget.py
src/paperconan/image/_dependencies.py
src/paperconan/image/_diagnostics.py
src/paperconan/image/_evidence.py
```

For every conflict, remove markers and retain both sets of public behavior. Regenerate examples only after the merged scanner is stable.

- [ ] **Step 5: Verify the union test tree collects**

Run:
```bash
uv sync --all-extras
uv run pytest --collect-only -q
```

Expected: no import or collection errors. Runtime test failures are handled in later tasks.

### Task 2: Restore Main Detector and Scan Contracts

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/detectors.py`
- Modify: `src/paperconan/_html.py`
- Modify: `src/paperconan/packet.py`
- Modify: `src/paperconan/schema.py`
- Test: `tests/test_decimal_tail_clustering.py`
- Test: `tests/test_offset_row_reuse.py`
- Test: `tests/test_round_shift_shared_fraction.py`
- Test: `tests/test_row_pair_shared_fraction.py`
- Test: `tests/test_row_relations.py`
- Test: `tests/test_scaled_row_reuse.py`
- Test: `tests/test_short_row_reuse.py`
- Test: `tests/test_within_row_shared_fraction.py`

**Interfaces:**
- Produces: `detect_decimal_tail_clustering`, `detect_row_relations`, `detect_scaled_row_reuse`, `detect_short_row_reuse`, `detect_row_pair_shared_fraction`, and `detect_within_row_shared_fraction`.
- Produces: `round_shift_shared_fraction`, `offset_row_reuse`, and `row_relations` finding groups.
- Preserves: resource leases, finding caps, coverage limitations, deferred evidence, and deterministic ordering.

- [ ] **Step 1: Run the main detector suite and record the failing APIs**

Run:
```bash
uv run pytest -q tests/test_decimal_tail_clustering.py tests/test_offset_row_reuse.py tests/test_round_shift_shared_fraction.py tests/test_row_pair_shared_fraction.py tests/test_row_relations.py tests/test_scaled_row_reuse.py tests/test_short_row_reuse.py tests/test_within_row_shared_fraction.py
```

Expected before implementation: failures proving each missing or incompatible detector path.

- [ ] **Step 2: Port detector implementations into the hardened orchestration**

Add the main detector functions to `_audit.py`, but route allocations and retained findings through the existing budget objects. Extend:

```python
BLOCK_FINDING_GROUPS = (
    # existing groups...
    "row_relations",
)
```

Expose the public subset from `paperconan.detectors.__all__`.

- [ ] **Step 3: Preserve combined scan output**

Keep coverage/status/runtime fields and add the current detector outputs:

```python
out["decimal_tail_clusters"] = decimal_tail_clusters
```

Ensure packet distillation and HTML rendering recognize the new groups without weakening explicit evidence binding.

- [ ] **Step 4: Run detector and hardened-resource regression suites**

Run:
```bash
uv run pytest -q tests/test_decimal_tail_clustering.py tests/test_offset_row_reuse.py tests/test_round_shift_shared_fraction.py tests/test_row_pair_shared_fraction.py tests/test_row_relations.py tests/test_scaled_row_reuse.py tests/test_short_row_reuse.py tests/test_within_row_shared_fraction.py tests/test_detector_coverage.py tests/test_resource_budget.py tests/test_resource_lifetime.py
```

Expected: all pass.

### Task 3: Restore Image and Adjudicated-Report Contracts

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_adjudicated_html.py`
- Modify: `src/paperconan/_html.py`
- Modify: `src/paperconan/schema.py`
- Modify: `src/paperconan/fetch/_cli.py`
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `src/paperconan/fetch/_files.py`
- Modify: `pyproject.toml`
- Test: `tests/test_adjudicated_report.py`
- Test: `tests/test_adjudicated_report_unified.py`
- Test: `tests/test_image_assets.py`
- Test: `tests/test_image_dependencies.py`
- Test: `tests/test_image_diagnostics.py`
- Test: `tests/test_image_report.py`
- Test: `tests/test_image_workflow.py`
- Test: `tests/test_skill_docs.py`

**Interfaces:**
- Produces: `scan_dir(..., images=False, image_diagnostics=False)`.
- Produces: `image_assets`, `image_findings`, and optional `image_review`.
- Preserves: `render_adjudicated_report(..., artifact_dir=None)` and exact verdict evidence binding.

- [ ] **Step 1: Run image/report tests to establish the failing baseline**

Run:
```bash
uv run pytest -q tests/test_adjudicated_report.py tests/test_adjudicated_report_unified.py tests/test_image_assets.py tests/test_image_dependencies.py tests/test_image_diagnostics.py tests/test_image_report.py tests/test_image_workflow.py tests/test_skill_docs.py
```

- [ ] **Step 2: Merge report normalization before rendering**

Use a copied, validated verdict:

```python
verdict = _normalized_verdict_copy(
    scan,
    verdict,
    scan_findings=scan_findings,
)
```

Retain the PR's exact atomic/relation evidence selectors. Add image references and evidence budgets without reintroducing automatic ambiguous selection.

- [ ] **Step 3: Add runtime neutral-language validation**

Validate all rendered verdict text through:

```python
from ._neutral_language import contains_blocked_language

if contains_blocked_language(visible_text):
    raise ValueError("verdict text violates the neutral-language policy")
```

Validation must cover Markdown-visible text, modern and legacy verdict fields, image labels, image review notes, and visible unmatched selectors.

- [ ] **Step 4: Keep report writes atomic**

Render and validate before creating a temporary sibling file, `fsync` it, then replace the destination with `os.replace`.

- [ ] **Step 5: Combine packaging metadata**

Set version `0.8.3`; retain the PR's build/test dependencies and dependency group; add `image` and expanded `all`, `test`, and `dev` extras from main.

- [ ] **Step 6: Run image/report/language suites**

Run:
```bash
uv run pytest -q tests/test_adjudicated_report.py tests/test_adjudicated_report_unified.py tests/test_image_assets.py tests/test_image_dependencies.py tests/test_image_diagnostics.py tests/test_image_report.py tests/test_image_workflow.py tests/test_language_policy.py tests/test_skill_docs.py
```

Expected: all pass.

### Task 4: Fix Mixed-Scale Relation Tolerance

**Files:**
- Modify: `src/paperconan/_numeric.py`
- Modify: `src/paperconan/_audit.py`
- Test: `tests/test_relations_tolerance.py`

**Interfaces:**
- Consumes: `x`, `y`, and the already-computed `diff = y - x`.
- Produces: a constant-offset decision based on difference-space residuals plus operand roundoff.

- [ ] **Step 1: Add failing mixed-scale regression**

```python
def test_mixed_scale_outlier_is_not_constant_offset():
    x = [1.0, 1e12, 2e12, 3e12]
    y = [101.0, 1e12, 2e12, 3e12]
    findings = _kinds(x, y)
    assert not any(item["kind"] == "constant_offset" for item in findings)
```

- [ ] **Step 2: Verify it fails for the reported reason**

Run:
```bash
uv run pytest tests/test_relations_tolerance.py::test_mixed_scale_outlier_is_not_constant_offset -q
```

Expected: failure showing `constant_offset` with offset `25`.

- [ ] **Step 3: Add translated true-offset coverage**

Add a test whose decimal values are translated by `1e12` and still retain one real fractional offset.

- [ ] **Step 4: Validate offsets in difference space**

Compare each `y - x` value with `mean_diff`, using:

```python
residual = abs(diff - mean_diff)
tolerance = (
    ulp_tolerance(diff, mean_diff)
    + ulp_tolerance(x, y)
    + rtol * _local_variation(diff)
)
```

Use bounded arrays already covered by `relation_close_workspace`; do not derive row tolerance from full-column `x` or `y` spread.

- [ ] **Step 5: Run all relation tests**

Run:
```bash
uv run pytest tests/test_relations_tolerance.py -q
```

Expected: all pass.

### Task 5: Count Completed Cross-Table Analysis in Scan Status

**Files:**
- Modify: `src/paperconan/_coverage.py`
- Modify: `src/paperconan/_audit.py`
- Test: `tests/test_scan_status.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: a private completed-non-block-analysis marker or equivalent counter.
- Preserves: public `coverage.blocks_analyzed` semantics and existing JSON key order.

- [ ] **Step 1: Add failing scan and CLI regressions**

Create two identical CSV files with two rows and three high-precision decimal columns. Assert:

```python
assert result["cross_sheet_findings"][0]["kind"] == "cross_sheet_position_identical"
assert result["scan_status"] == "partial"
assert cli_return_code == 0
```

- [ ] **Step 2: Verify current failure**

Run:
```bash
uv run pytest tests/test_scan_status.py -k cross_sheet_only -q
```

Expected: `scan_status == "failed"` and nonzero CLI status.

- [ ] **Step 3: Mark completed cross-table work**

After `detect_collisions`, mark non-block analysis complete when `CrossSheetWorkBudget.pairs_examined > 0`. Do not infer completion from retained findings.

Update `ScanCoverage.status` so failure requires both zero analyzed blocks and zero completed non-block analyses. Existing limitations keep the example `partial`.

- [ ] **Step 4: Verify status boundaries**

Run:
```bash
uv run pytest tests/test_scan_status.py tests/test_smoke.py::test_csv_cross_file_collision -q
```

Expected: all pass; a single short table with no pair remains `failed`.

### Task 6: Bind Managed-Output Ownership to Content and Pin the Output Root

**Files:**
- Modify: `src/paperconan/_source_sidecar.py`
- Modify: `src/paperconan/fetch/_download.py`
- Test: `tests/fetch/test_managed_output.py`
- Test: `tests/fetch/test_download.py`

**Interfaces:**
- Sidecar format: each managed name maps to `{size: int, sha256: str}`.
- Mutation rule: overwrite or cleanup is allowed only after no-follow regular-file verification and fingerprint match.
- Legacy name-only sidecars: read for provenance but never authorize mutation.

- [ ] **Step 1: Add failing ownership tests**

Cover:

```text
modified same-name output -> preserve original and use collision name
modified stale output -> preserve and relinquish management
equal-size edit -> detected by SHA-256
legacy sidecar -> no overwrite or deletion authority
matching fingerprint -> refresh and stale cleanup remain allowed
malformed fingerprint -> fail closed
```

- [ ] **Step 2: Add failing symlink-root tests**

Assert a final symlink output directory is rejected before download, its target is untouched, and replacing the root with a symlink during a run cannot redirect publication.

- [ ] **Step 3: Persist bounded fingerprints**

Encode:

```json
{
  "managed_files": {
    "table.csv": {
      "size": 123,
      "sha256": "64 lowercase hexadecimal characters"
    }
  }
}
```

Count fingerprint metadata toward sidecar byte/name/entry caps.

- [ ] **Step 4: Verify before every destructive operation**

Open with `O_NOFOLLOW`, require a stable regular file, stream exactly `size` bytes into SHA-256, re-stat the descriptor/path, and compare the digest. On any mismatch, preserve the path and allocate a collision name.

- [ ] **Step 5: Pin the output directory**

Port main's directory-descriptor pattern:

```python
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
fd = os.open(out_dir, flags)
```

Verify device/inode identity before each publication or cleanup and use `dir_fd`-relative operations. If no-follow directory operations are unavailable, reject destructive refresh/cleanup rather than following links.

- [ ] **Step 6: Run managed-output and fetch tests**

Run:
```bash
uv run pytest -q tests/fetch/test_managed_output.py tests/fetch/test_download.py tests/test_fetch_download.py
```

Expected: all pass.

### Task 7: Bound OOXML Formula Inspection and PDF Extraction

**Files:**
- Modify: `src/paperconan/_input.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_extract.py`
- Modify: `src/paperconan/_sheet.py`
- Test: `tests/test_formula_cache.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- `inspect_ooxml_formula_cache(..., accepted_sheets=None)` streams metadata and only opens accepted worksheets.
- PDF adapters provide declared rows/columns before lazy extraction.

- [ ] **Step 1: Add failing OOXML tests**

Add tests that reject any unbounded `read(-1)` for workbook metadata, skip rejected worksheets, and do not run formula inspection when no sheet was accepted.

- [ ] **Step 2: Stream OOXML metadata**

Replace `zf.read()` plus `ET.fromstring()` with `zf.open()` plus detached `ET.iterparse()`. Keep only relationship IDs/targets and sheet names required for resolution.

- [ ] **Step 3: Avoid repeated formula inspection**

Add `inspect_formulas=False` to paths that discard limitation metadata, including `load_table()` and deferred evidence reload. In `load_table_result()`, pass only accepted sheet names and skip inspection when the set is empty.

- [ ] **Step 4: Add failing PDF preflight tests**

Use an oversized stub whose `extract()` raises if invoked. Also cover a second table that cannot fit after the first consumes the cumulative cap.

- [ ] **Step 5: Carry declared PDF geometry into the builder**

Derive row/column geometry from `table.cells`, wrap `table.extract()` lazily, and pass declared dimensions into `SheetBuilder`. Reject `loaded_cells + declared_rows * declared_cols > max_cells` before invoking extraction.

- [ ] **Step 6: Run formula and extraction tests**

Run:
```bash
uv run pytest -q tests/test_formula_cache.py tests/test_extract.py tests/test_cell_guard.py tests/test_resource_lifetime.py
```

Expected: all pass with bounded preflight behavior.

### Task 8: Make Skill ZIP Publication Non-Destructive

**Files:**
- Modify: `build_skill_zip.sh`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Optional output path remains supported.
- Existing output changes only after all sources validate and a complete replacement archive is ready.

- [ ] **Step 1: Add failing preservation tests**

Cover output equal to `skills/paperconan/SKILL.md`, missing source with existing output, and successful replacement of an old archive.

- [ ] **Step 2: Verify current destructive ordering**

Run:
```bash
uv run pytest tests/test_packaging.py -k skill_zip -q
```

Expected: new preservation tests fail.

- [ ] **Step 3: Validate before mutation**

Resolve the repository root first, validate every source, and reject aliases with Bash-compatible:

```bash
if [[ "$OUT" -ef "$SOURCE" ]]; then
  echo "output path aliases a Skill ZIP source: $SOURCE" >&2
  exit 1
fi
```

- [ ] **Step 4: Publish atomically**

Build and verify the archive under a temporary sibling directory in `OUT_DIR`, then replace the destination with `mv -f`. Do not remove the old output before successful validation/build.

- [ ] **Step 5: Run packaging tests**

Run:
```bash
uv run pytest tests/test_packaging.py -q
```

Expected: all pass.

### Task 9: Documentation, Examples, Lockfile, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/cli.md`
- Modify: `docs/detectors.md`
- Modify: `docs/reports.md`
- Modify: `skills/paperconan/SKILL.md`
- Modify: `skills/paperconan/references/detectors.md`
- Modify: `skills/paperconan/references/output-schema.md`
- Regenerate: `examples/demo_paper/audit/scan.json`
- Regenerate: `examples/demo_paper/audit/report.html`
- Regenerate: `uv.lock`

**Interfaces:**
- Documents version `0.8.3`, merged detector/image behavior, explicit scan coverage, and fingerprint-bound managed outputs.

- [ ] **Step 1: Update documentation and generated examples**

Document the combined CLI/options and report fields. Regenerate demo outputs from committed demo inputs only.

- [ ] **Step 2: Regenerate dependency lock**

Run:
```bash
uv lock
uv sync --all-extras
```

- [ ] **Step 3: Run focused integration suites**

Run:
```bash
uv run pytest -q tests/test_scan_coverage.py tests/test_scan_status.py tests/test_report_status.py tests/test_detector_coverage.py tests/test_resource_budget.py tests/test_resource_lifetime.py tests/test_language_policy.py tests/test_module_boundaries.py
```

- [ ] **Step 4: Run the complete workspace suite**

Run:
```bash
uv run pytest
```

Expected: zero failures; only opt-in live-network tests may skip.

- [ ] **Step 5: Build and test the installed source distribution**

Build a fresh sdist, install it in an isolated environment, and run the packaged test closure without importing the source checkout.

- [ ] **Step 6: Commit and push**

Run:
```bash
git status -sb
git diff --check
git push origin codex/project-hardening
```

Confirm PR 32 is no longer conflicting and targets the current `main`.
