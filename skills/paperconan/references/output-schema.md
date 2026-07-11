# paperconan output schema (scan.json)

Full structure of the `scan.json` the agent parses. The SKILL.md keeps only the
essentials; this file is the complete reference (it travels in the skill bundle).

## scan.json top-level schema

```json
{
  "schema_version": 2,
  "tool": "paperconan",
  "tool_version": "0.8.2",        // matches the pyproject version; provenance for archived reports
  "scanned_at": "2026-05-29T02:08:53+00:00",
  "profile": "review",            // which FP profile ran (review|forensic|triage) — severities are post-filter unless "forensic"
  "input_dir": "...",
  "paper": {"doi": "10.1038/...", "title": "..."},  // provenance, or null (see below)
  "scan_status": "complete",      // complete | partial | failed
  "coverage": {
    "files_discovered": 3,
    "files_succeeded": 3,
    "files_failed": 0,
    "sheets_succeeded": 8,
    "sheets_skipped": 0,
    "blocks_analyzed": 12,
    "blocks_skipped": 0,
    "truncated": false,
    "limitations": []
  },
  "n_files": 3,
  "n_blocks_with_findings": 8,
  "scan_errors": [                // files that failed to parse — surface these, don't imply a clean scan
    {"file": "broken.xlsx", "error": "..."}
  ],
  "scan_stats": {                 // per-file / per-sheet sizing + timing (files[], sheets[], elapsed_ms)
    "files": [...], "sheets": [...], "elapsed_ms": 412.5
  },
  "relations_blocks": [
    {
      "file": "ED_Fig8b.xlsx",
      "sheet": "Sheet1",
      "block": {"rows": "6-15", "cols": "1-30", "header": [...]},
      "relations": [...],              // cross-column relations
      "progressions": [...],           // arithmetic progressions
      "equal_pairs": [...],            // pairs of columns with many equal rows
      "row_pairs": [...],              // pairs of rows with suspicious low-digit coupling
      "within_col": [...],             // within-column anomalies
      "identical_after_rounding": [...], // cells matching after rounding
      "grim": [...]                    // GRIM/GRIMMER: reported mean/SD impossible for integer data
    }
  ],
  // per-sheet last-digit χ². Each: {label, n, chi2, p, p_adj, fdr_significant, counts, top}
  // Filter on fdr_significant (BH-FDR q ≤ 0.05), NOT raw p — dozens of sheets are tested.
  "digit_distribution": [...],
  // per-sheet two-decimal ending counts. Each: {label, n, n_unique, top}
  "decimal_endings": [...],
  // bit-identical / value-overlap across sheets (same file OR cross-file). See fields below.
  "cross_sheet_findings": [...]
}
```

## Scan completion and coverage

Schema version 2 adds `scan_status` and `coverage` so an empty finding list can
be interpreted together with the amount of input that actually reached numeric
scanning:

- `complete`: at least one sheet reached numeric scanning and no coverage
  limitation was recorded.
- `partial`: some input reached numeric scanning, but one or more files, sheets,
  detector paths, rows, blocks, or retained findings were limited.
- `failed`: no sheet reached numeric scanning. The CLI still writes diagnostic
  `scan.json` and requested HTML/Markdown reports, then exits nonzero.

Coverage counters are cumulative for the scan:

- `files_discovered`, `files_succeeded`, `files_failed`
- `sheets_succeeded`, `sheets_skipped`
- `blocks_analyzed`, `blocks_skipped`
- `truncated`: `true` when a configured row/block/finding limit reduced coverage
  or retained output
- `limitations`: deterministic limitation objects in scan order

Every limitation object has `scope` and `reason`. Location and threshold fields
depend on the limitation:

```json
{
  "scope": "block",
  "reason": "wide_block_detector_limit",
  "file": "table.xlsx",
  "sheet": "Data",
  "rows": "2-80",
  "cols": "1-240",
  "detectors": ["relations", "equal_pairs", "row_pairs"],
  "max_cols": 120
}
```

Other optional fields include `count`, `limit`, `omitted_findings`,
`rows_total`, `rows_used`, `max_rows`, `max_bytes`, and `max_cells`. Reports
list these limitations before findings. A partial report keeps all retained
findings; a failed report never presents an empty finding list as a completed
scan.

Archived schema-version-1 scans may omit `schema_version`, `scan_status`, and
`coverage`. Both HTML and Markdown renderers accept that shape and label it as a
legacy scan whose detailed coverage status is unavailable.

`paper` provenance is populated from a `paperconan_source.json` sidecar that
`paperconan fetch --download/--auto` writes alongside the data, or from
`paperconan <dir> --doi <DOI> --title <T>`. It is `null` when neither is present
(a bare directory audit) — never read `null` as "no paper".

## Every finding has

- `kind`: detector name (see [detectors.md](detectors.md))
- `severity`: `"high"` | `"medium"` | `"low"`
- `rule`: human-readable rule string e.g. `col[27] ≡ col[28] in 9/10 rows`
- `n`: sample size for the rule
- `evidence`: block snippet `{headers, rows, highlight_cols, ...}` — used by report.html, but you can also surface a few highlighted values if useful
- `likely_benign` (optional): a common innocent explanation for this kind — surface it to the user alongside the finding so a signal is never reported as a verdict
- `profile_action`: `"kept"` | `"demoted"` | `"hidden"` — what the active profile did to this finding. `"demoted"`/`"hidden"` means the current `severity` is the **filter's** downgrade, not the detector's raw verdict (always `"kept"` under `--profile forensic`). See the Profiles section in SKILL.md.
- `false_positive_context` (list): machine tags for *why* it was demoted — e.g. `axis_or_scan_column`, `censoring_or_boundary_value`, `derived_or_unit_conversion`, `same_data_replot_or_duplicate_upload`, `omics_or_large_matrix_boundary_flood`. Map these back to the "常见误报" notes in [detectors.md](detectors.md).
- `prefilter_reason` (optional): deterministic triage explanation, especially for within-column findings. Treat it as a structured clue, not a final answer. It can explain why a pattern was kept, demoted, or considered a likely structural false positive.
- `prefilter_flags` (optional object): deterministic flags supporting the prefilter decision, such as axis/index-like labels, percentage/ratio/normalized/model-output context, low cardinality, boundary/floor/ceiling values, fixed-denominator hints, or repeated fill values. Use these with [judgment-rubric.md](judgment-rubric.md) before surfacing prefiltered hits.
- `dense_block` (optional, column-relation / equal-pair findings): `true` means this finding comes from a sheet that floods with pairwise column relations (a dense / correlated matrix — correlation tables, normalized replicate panels). Such findings are auto-demoted to `low` severity because identical/linear columns there are expected by construction, not a duplication red flag — don't treat them as high-severity signal
- `value_sample` (optional, within-column findings): small sample of distinct values from the column. Use it for repeated-value explanation, last-two-decimal checks, and fixed-denominator triage.
- `col_a_sample` / `col_b_sample` (optional, pairwise relation findings): small value samples from the relevant column(s), used as an evidence peek when the full table is large. These samples help explain cross-column transforms and relation prefilters, but they do not replace opening the original table when making a serious claim.

## row_pair_digit_coupling fields

- `row_a` / `row_b`: row labels inferred from text cells immediately left of the numeric block, or fallback row numbers.
- `row_a_idx` / `row_b_idx`: 0-based absolute row indices; evidence highlights these rows.
- `same_decimal1`: count of aligned numeric cell pairs sharing the first digit after the decimal point.
- `same_ones_decimal1`: count sharing both the ones digit and the first decimal digit.
- `coarse_10_diff`: count of changed pairs where `row_b - row_a` is a nonzero multiple of 10.
- `top_diffs`: most common paired differences, rounded for compact display.
- `examples`: small list of aligned columns with `a`, `b`, and `diff` values.

Treat this as a local row-pair anomaly. Confirm row independence and exclude formula-generated grids, low-cardinality scores, and legitimate transformations before escalating.

## cross_sheet_findings fields

- `same_file`: whether the two sheets live in one workbook or span two files
- `figure_a` / `figure_b` / `same_figure`: parsed figure identity (e.g. `main:5`, `ext:6`). When `same_figure` is true the overlap is a combined-vs-individual re-plot of one display item — it is **downgraded to `low`** and carries a `context` note. Cross-figure / cross-file overlaps keep `high`/`medium` and are the ones worth checking against the legend.
- `delta`: how the two near-duplicate tables differ — `{pattern, modified_cells, shared_values, only_in_a, only_in_b}`. `pattern` is one of:
  - `perfect_dup` — identical value multiset (clean re-plot)
  - `superset` — one side strictly contains the other (e.g. an extra replicate column, n=5 vs n=6)
  - `value_tweaked` — cells changed in place (copy-then-tweak fingerprint; most worth investigating)
  - `value_divergent` — both sides hold values the other lacks
  - `column_duplicate` — a full column repeats value-for-value across two panels (`cross_sheet_column_duplicate`; carries `col_a`/`col_b`)
  - `fraction_reuse` — two matrix blocks in ONE sheet share decimal fractions while integer parts differ (`within_table_fraction_reuse`; `same_file=true`, `figure_a/b=null`)
  - `recurring_row_vector` — a fixed row tuple recurs across ≥2 figures (`recurring_row_vector`; carries `vector`, `n_occurrences`, `n_figures`)
