# GRIM / GRIMMER detector — design

**Date:** 2026-06-03
**Status:** approved, pre-implementation
**Component:** `src/paperconan/_audit.py` (new detector), `_html.py` + `write_markdown_report` (rendering), `tests/`

## Motivation

paperconan's existing detectors all operate on *raw-ish* data columns —
relations between columns, within-column repetition, cross-sheet collisions.
None of them validate **reported summary statistics**. But a large fraction of
fabricated supplementary tables are summary tables of the form
`group → mean ± SD (n)`, with no raw values to cross-check.

GRIM (Granularity-Related Inconsistency of Means, Brown & Heathers 2017) and
GRIMMER (its SD/variance extension, Anaya 2016) are cheap, deterministic,
well-validated forensic tests that catch impossible mean/SD values in exactly
this kind of table. They fit paperconan's engine style (per-block, pure
numeric, zero external deps) and fill a genuine capability gap.

Borrowed conceptually from the `geng-academic-fraud-detector` skill's second
"form" ("能否反推出三个合理的原始值?"), but implemented as deterministic code,
not LLM reasoning.

## Validity constraint (the crux)

GRIM/GRIMMER are mathematically valid **only for means of integer-granularity
data** (counts, # of cells/animals/colonies, Likert/score items). On genuinely
continuous measurements (concentrations, fluorescence) the underlying values
are not integers, so naive GRIM produces false positives. paperconan's whole
brand is low false-positive rate, so the design gates hard on integer evidence.

Two facts shape the gate:
- GRIM has *discriminating power* only when `n < 10^d` (where `d` = decimal
  places of the reported mean). For `n ≥ 10^d`, nearly every mean passes, so
  the test is vacuous and must be skipped.
- Cells are coerced to `int`/`float` on load (`_coerce_cell`), so displayed
  trailing-zero precision (`2.50`) is lost. We recover `d` from the float
  repr. This is *conservatively safe*: undercounting `d` only lowers power
  (fewer flags), never creates false positives.

## Detector

New function, same signature as siblings:

```python
def detect_grim_grimmer(rows, r0, r1, c0, c1, header) -> list[dict]:
```

### Step 1 — locate triple by header (case-insensitive, EN + 中文)

Within the block, match column headers:
- **mean**: `mean | average | avg | 均值 | 平均`
- **sd**:   `\bsd\b | std | s\.?d\.? | sem | s\.?e\.?m?\.? | 标准差 | 标准误`
- **n**:    `\bn\b | sample.?size | 样本量 | 例数`

Each data row is one group's `(mean, [sd], n)`. If no `n` column is found,
**skip the block** — never guess n. SD column is optional (drives GRIMMER only).
If multiple candidate columns match a role, take the first match left-to-right.

### Step 2 — integer-data gate (strict)

Proceed only if there is positive evidence the measured item is
integer-granular. Evidence = the **mean column header** OR the block's
left-label context matches a count/score lexicon:

```
count | counts | number | # | cells | foci | colonies | nuclei
| score | rating | likert | 个数 | 数目 | 计数 | 数量 | 评分
```

No keyword match → return `[]`. (Lower recall, near-zero FP — on brand.)

### Step 3 — per-row tests

For each data row with a valid numeric `(mean, n)` (`n` a positive integer ≥ 2):

1. `d` = number of decimal places in the mean (from float repr, capped at a
   sane max e.g. 6).
2. **Power gate:** skip the row if `n >= 10**d`.
3. **GRIM:** the mean is *consistent* iff some integer total
   `t ∈ {floor(mean·n), ceil(mean·n)}` satisfies
   `round(t / n, d) == mean` within a small epsilon (tolerant of round-half-up
   vs round-half-even). Otherwise → GRIM failure for this row; record the
   nearest consistent value `round(round(mean·n)/n, d)`.
4. **GRIMMER** (only when an sd value is present and the row passed GRIM):
   reconstruct the integer sum-of-squares `SS` implied by `(mean, sd, n)` using
   the sample-variance identity `var = (SS − T²/n)/(n−1)`, `T = round(mean·n)`.
   The sd is *consistent* iff the implied `SS` is (a) a non-negative integer
   (within epsilon), (b) parity-consistent with `T` for integer data, and
   (c) round-trips: the sd recomputed from the nearest integer `SS` rounds back
   to the reported sd at its own decimal precision `d_sd`. Otherwise → GRIMMER
   failure. A row that already failed GRIM is **not** double-reported here.

### Step 4 — output (aggregated, mirrors `detect_identical_after_rounding`)

Emit at most **one finding per kind per block** to avoid row-flooding:

```python
dict(kind="grim_inconsistent",        # or "grimmer_inconsistent"
     mean_col=<header>, sd_col=<header|None>, n_col=<header>,
     n_rows_checked=<int>, n_failed=<int>,
     failed_rows=[{row, mean, sd, n, decimals, nearest_consistent}, ...],  # capped (e.g. 8)
     severity="high",
     rule="<count> of <checked> rows report a mean impossible for integer "
          "data at the stated n (GRIM)")
```

`_attach_benign` adds a standing caveat string:
*"GRIM/GRIMMER assume the statistic is a mean of integer-valued items
(count/score); verify the measure before acting."*

### Step 5 — wiring

- Call `detect_grim_grimmer(...)` in `scan_dir`'s per-block loop.
- Add a new group key (e.g. `grim`) to the `report_blocks` dict and include it
  in the `if rel or ap or ...` guard and the `for group in (...)` evidence/benign
  loop. `_attach_evidence` highlights the mean/sd columns and failed rows.
- Render the `grim` group in `_html.write_html_report` and
  `write_markdown_report` (one card/section listing failed rows).

## Testing (TDD)

Correctness lever — an **independent brute-force oracle**:

- `tests/test_grim.py`: for small `n` (2..12) and a decimal precision, enumerate
  integer datasets (or, more cheaply, enumerate achievable totals `T` and
  sum-of-squares `SS`) to derive the ground-truth set of achievable
  `(mean, sd)` rounded targets, then assert the closed-form GRIM/GRIMMER helpers
  agree on a grid of candidate values. This validates the math independently of
  the implementation.
- Hand oracles:
  - GRIM: `mean=3.45, n=10` → inconsistent; `mean=3.40, n=10` → consistent;
    `mean=3.50, n=3` → inconsistent; `mean=3.33, n=3` → consistent.
  - GRIMMER: dataset `{1,2,3}` → `mean=2.00, sd=1.00, n=3` consistent;
    `mean=2.00, sd=1.05, n=3` inconsistent.
  - Power gate: `mean=3.456, n=1000` (`n ≥ 10^d`) → no finding.
- Fixture: extend the test fixture builder with a small summary table
  (integer-keyword header + an impossible mean) and assert `scan_dir` surfaces
  exactly the expected `grim_inconsistent` finding.
- **False-positive guard:** a continuous-data summary table (mean/sd/n columns
  but *no* integer keyword, or `n ≥ 10^d`) yields **zero** GRIM/GRIMMER findings.

## Scope boundaries (YAGNI — deferred)

- No packed `mean ± sd` single-cell parsing.
- No n-guessing when n is absent.
- No CLI flags, no configurable granularity / item-granularity override.
- No GRIM for non-integer granularities (e.g. 0.5-step scales).
