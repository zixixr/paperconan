# paperconan output schema (scan.json)

Full structure of the `scan.json` the agent parses. The SKILL.md keeps only the
essentials; this file is the complete reference (it travels in the skill bundle).

## scan.json top-level schema

```json
{
  "tool": "paperconan",
  "tool_version": "0.8.0",        // matches the pyproject version; provenance for archived reports
  "scanned_at": "2026-05-29T02:08:53+00:00",
  "profile": "review",            // which FP profile ran (review|forensic|triage) — severities are post-filter unless "forensic"
  "input_dir": "...",
  "paper": {"doi": "10.1038/...", "title": "..."},  // provenance, or null (see below)
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
- `dense_block` (optional, column-relation / equal-pair findings): `true` means this finding comes from a sheet that floods with pairwise column relations (a dense / correlated matrix — correlation tables, normalized replicate panels). Such findings are auto-demoted to `low` severity because identical/linear columns there are expected by construction, not a duplication red flag — don't treat them as high-severity signal

## cross_sheet_findings fields

- `same_file`: whether the two sheets live in one workbook or span two files
- `figure_a` / `figure_b` / `same_figure`: parsed figure identity (e.g. `main:5`, `ext:6`). When `same_figure` is true the overlap is a combined-vs-individual re-plot of one display item — it is **downgraded to `low`** and carries a `context` note. Cross-figure / cross-file overlaps keep `high`/`medium` and are the ones worth checking against the legend.
- `delta`: how the two near-duplicate tables differ — `{pattern, modified_cells, shared_values, only_in_a, only_in_b}`. `pattern` is one of:
  - `perfect_dup` — identical value multiset (clean re-plot)
  - `superset` — one side strictly contains the other (e.g. an extra replicate column, n=5 vs n=6)
  - `value_tweaked` — cells changed in place (copy-then-tweak fingerprint; most worth investigating)
  - `value_divergent` — both sides hold values the other lacks
