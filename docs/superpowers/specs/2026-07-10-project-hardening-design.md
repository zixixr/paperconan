# Paperconan Project Hardening Design

**Date:** 2026-07-10

**Status:** Approved

## Objective

Resolve the confirmed correctness, coverage, evidence-binding, resource-budget,
download-integrity, packaging, and governance gaps without breaking archived
`scan.json` files or legacy verdict files.

The project continues to report statistical signals and data inconsistencies
that require human review. All public text, metadata, code comments, tests, and
documentation must use neutral language.

## Compatibility Contract

The repair is additive wherever possible:

- Existing `scan.json` keys remain readable.
- New scans add explicit schema, completion, and coverage fields.
- Archived scans without the new fields still render, but the renderer labels
  their detailed coverage state as unavailable.
- Both verdict shapes remain accepted:
  - the legacy flat object with `report_md` and optional `finding_refs`;
  - the primary paper object with a `findings` array and one `finding_ref` per
    finding.
- A verdict that omits references may retain the legacy automatic evidence
  selection behavior.
- A verdict that supplies a reference which matches no scan finding must show
  an explicit unmatched-reference message and must not display evidence from a
  different finding.
- The CLI returns zero for `complete` and `partial` scans. It returns nonzero
  when no supported input is found or when no discovered input reaches numeric
  scanning.

## Alternatives Considered

### Local patches

Fixing each reproduction in place would minimize the immediate diff, but would
leave tolerance rules, coverage accounting, and resource limits distributed
through the orchestration module. Similar defects would remain difficult to
prevent.

### Staged hardening with compatibility

Introduce small internal boundaries for numeric comparison, scan coverage, and
compact cross-sheet summaries while retaining public JSON and detector
interfaces. This is the selected approach because it addresses root causes
without replacing the complete engine.

### Scanner rewrite

A new event-driven streaming engine could provide the cleanest long-term
architecture, but it would invalidate too many golden assumptions at once and
create unnecessary migration risk.

## Component 1: Numeric Correctness

### Comparison semantics

The engine must distinguish three operations:

1. **Stored-value identity** compares the parsed source values directly. It
   must not use a magnitude-relative tolerance.
2. **Deterministic transformation checks** compare residuals using a tolerance
   based on floating-point ULPs and the variation of the transformed data, not
   the absolute baseline magnitude.
3. **Statistical fits** may use a documented relative tolerance, but fixed
   magnitude gates such as `1e-12` must not exclude valid tiny-scale data.

This prevents high-baseline values with meaningful differences from being
reported as identical while retaining support for very small measurements.

### Integer fidelity

`Sheet` keeps exact Python integers that cannot be represented uniquely by
`float64`. Exact-value accessors and relation checks use those values. Float
arrays may still be used for vectorized statistics, but unsafe integer cells
must never be silently merged into the same stored value or used to claim an
exact match.

### Numeric block discovery

A candidate region is marked visited only after it satisfies the minimum block
requirements. A short seed column therefore cannot hide a valid neighboring
block.

### GRIM and GRIMMER

GRIMMER adds an exact integer-sample feasibility check for practical state
sizes, including the two-observation closed form. Search that exceeds a
documented safety budget remains conservative and does not create a finding.
Header grouping evaluates every qualifying mean group and pairs it with the
best matching sample-size and SD columns rather than stopping at the first
group.

## Component 2: Scan Status, Coverage, and Profiles

New scans add:

```json
{
  "schema_version": 2,
  "scan_status": "complete",
  "coverage": {
    "files_discovered": 1,
    "files_succeeded": 1,
    "files_failed": 0,
    "sheets_succeeded": 2,
    "sheets_skipped": 0,
    "blocks_analyzed": 3,
    "blocks_skipped": 0,
    "truncated": false,
    "limitations": []
  }
}
```

`scan_status` is:

- `complete` when all discovered inputs and applicable detector paths finish
  without a coverage limitation;
- `partial` when at least one sheet is analyzed but another input, sheet,
  detector path, or output path is skipped or capped;
- `failed` when no sheet reaches numeric scanning.

Every existing cap must either count omitted findings or add a structured
coverage limitation. This includes file and cell limits, wide-block detector
skips, row-pair bounds, collision-grid row bounds, report-block limits, global
finding limits, optional extractor availability, and formula-cache gaps.

The HTML and Markdown reports display completion state before findings. A
failed scan never renders the same empty-state wording as a complete scan with
no findings.

The `forensic` profile preserves detector severity and
`profile_action="kept"` through all later sheet-level demotion passes.

## Component 3: Resource and Input Fidelity

### Bounded lifetime

The scan loop retains a complete `Sheet` only while processing its source file.
Per-sheet digit reports and within-sheet checks run immediately. Cross-sheet
checks receive compact summaries:

- bounded decimal grids and sparse label context;
- exact column fingerprints plus bounded evidence samples;
- recurring-row-vector aggregates;
- per-sheet metadata required for location reporting.

Full `Sheet` objects and full Python lists of every number are not retained
across the directory.

### Cell budgets

CSV cell accounting uses `row_count * maximum_width` before dense allocation,
so ragged input cannot bypass the cap. PDF and DOCX table normalization uses the
same cumulative cell budget and returns an explicit skipped-table limitation
when exceeded.

### Formulas

Spreadsheet formulas without cached values are not interpreted as empty source
cells. The affected file or sheet is marked partial with a structured
limitation identifying the formula-cache gap. Paperconan does not attempt to
evaluate spreadsheet formulas.

### DOCX merged cells

Repeated XML cells produced by horizontal or vertical merges contribute their
text once. Synthetic repeated values created by the document adapter are
replaced by empty cells before numeric detection.

## Component 4: Downloads and Report Evidence

### Atomic downloads

Downloads stream into a temporary file in the destination directory. Success
uses `os.replace`; every failure removes the temporary path and leaves any
previous completed destination untouched.

### Managed output

The provenance sidecar records the exact files managed by the current fetch.
Before a later fetch into the same directory, only files from the previous
managed manifest are removed. User-created files are never deleted.

Archive members with the same basename receive deterministic collision-safe
names derived from their internal paths. Archive extraction streams from the
archive file rather than reading the complete archive into memory.

The fetch classifier and archive extractors recognize the same input extensions
as the scanner: `.xlsx`, `.xls`, `.xlsm`, `.xlsb`, `.csv`, `.tsv`, `.pdf`, and
`.docx`.

### Evidence binding

An omitted reference and an unmatched explicit reference are separate states.
Only the omitted-reference state may use legacy automatic evidence selection.
HTML visibly identifies automatic selection. Explicit unmatched references
render no evidence table and identify the failed selector.

## Component 5: Release and Governance

### Neutral-language enforcement

A repository test scans tracked public text and source files for the prohibited
accusatory vocabulary defined by project policy. The test stores its search
tokens in a non-public encoded form so the test itself does not introduce the
vocabulary into public source text.

All current metadata, CLI help, reports, comments, documentation, examples, and
skill references are rewritten using statistical-signal and data-inconsistency
language.

### Deterministic artifacts

Default `scan.json` retains the existing timestamp and elapsed-time keys with
deterministic `null` values while preserving stable sizing statistics. Runtime
metadata populates those keys only through an explicit CLI/library option.
HTML and Markdown omit absent runtime values. Paths stored in scan statistics
are relative to the input directory.

### Packaging and development

- Refresh `uv.lock` to the project version.
- Configure the default uv development group so `uv sync` installs test
  dependencies.
- Use current license metadata syntax and require a warning-free build.
- Add Python 3.13 and 3.14 to CI.
- Make the direct `pytest` entry point work from the repository root.
- Include test helpers and fixtures required to run the sdist test suite.
- Ignore every supported local source-data format and local verdict/report
  artifacts, while preserving explicit test and example exemptions.
- Build the distributable skill from every referenced Markdown file and verify
  that the archive contains them all.

## Testing Strategy

Every production change follows a red-green cycle. New regression coverage
includes:

- high-baseline non-identical columns;
- tiny-scale constant ratios and linear relations;
- adjacent integers above the exact `float64` range;
- ragged numeric block discovery;
- exact GRIMMER feasibility and multiple header groups;
- `forensic` profile preservation;
- failed, partial, complete, and capped scan coverage;
- HTML status banners and explicit unmatched evidence references;
- formula-cache limitations and merged DOCX cells;
- ragged CSV and extracted-table cell limits;
- atomic download cleanup, basename collisions, managed stale files, and
  extension parity;
- deterministic repeated scans;
- neutral-language policy;
- Skill ZIP completeness, lock consistency, direct pytest collection, CI
  versions, and sdist fixture completeness.

The final verification set is:

```bash
.venv/bin/python -m pytest -q
uv run --frozen pytest -q
uv lock --check
uv build
./build_skill_zip.sh
git diff --check
```

The previously reproduced edge cases are also rerun as direct smoke checks.

## Delivery Sequence

1. Numeric correctness and exact-value storage.
2. Scan coverage, CLI status, report status, and profile semantics.
3. Resource lifetime, extractor budgets, formulas, and merged cells.
4. Download integrity and verdict evidence binding.
5. Language, deterministic output, packaging, Skill, CI, and documentation.

Each stage ends with focused tests and a review before the next stage begins.
