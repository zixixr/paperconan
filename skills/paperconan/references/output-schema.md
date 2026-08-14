# paperconan output schema (scan.json)

Full structure of the `scan.json` the agent parses. The SKILL.md keeps only the
essentials; this file is the complete reference (it travels in the skill bundle).

## scan.json top-level schema

```json
{
  "tool": "paperconan",
  "tool_version": "0.8.4",        // matches the pyproject version; provenance for archived reports
  "schema_version": 2,            // scan.json shape version; sidecar packets carry it too
  "scan_status": "complete",      // "complete" | "partial" | "failed" — check BEFORE adjudicating (see below)
  "coverage": {...},              // explicit scan-coverage accounting (see below)
  "scanned_at": "2026-05-29T02:08:53+00:00",  // null unless --runtime-metadata (scan.json stays byte-reproducible by default)
  "profile": "review",            // which FP profile ran (review|forensic|triage) — severities are post-filter unless "forensic"
  "input_dir": "...",             // absolute path
  "paper": {"doi": "10.1038/...", "title": "..."},  // provenance, or null (see below)
  "n_files": 3,
  "n_blocks_with_findings": 8,
  "findings_omitted": 0,          // total findings dropped by the per-block/global finding caps, all blocks
  "scan_errors": [                // files that failed to parse — surface these, don't imply a clean scan
    {"file": "broken.xlsx", "error": "..."}
  ],
  "scan_stats": {                 // per-file / per-sheet sizing + timing (files[], sheets[], elapsed_ms);
    "files": [...], "sheets": [...], "elapsed_ms": 412.5   // elapsed_* are null without --runtime-metadata
  },
  "n_image_source_files": 2,
  "n_image_assets": 3,
  "relations_blocks": [
    {
      "file": "ED_Fig8b.xlsx",
      "sheet": "Sheet1",
      "block": {"rows": "6-15", "cols": "1-30", "header": [...]},
      "findings_omitted": 0,           // findings this block dropped to stay under the finding caps
                                       // (PAPERCONAN_MAX_FINDINGS_PER_BLOCK / PAPERCONAN_MAX_TOTAL_FINDINGS;
                                       // highest severity kept first — never a silent truncation)
      "relations": [...],              // cross-column relations
      "progressions": [...],           // arithmetic progressions
      "equal_pairs": [...],            // pairs of columns with many equal rows
      "row_pairs": [...],              // pairs of rows with suspicious low-digit coupling
      "row_relations": [...],          // within-block row-to-row relations (constant_ratio_row / identical_row)
      "within_col": [...],             // within-column anomalies
      "identical_after_rounding": [...], // cells matching after rounding
      "grim": [...],                   // GRIM/GRIMMER: reported mean/SD inconsistent for integer data
      "block_dups": [...]              // block-level distributed exact-repeat signal (block_value_duplication, see below)
    }
  ],
  // per-sheet last-digit χ². Each: {label, n, chi2, p, p_adj, fdr_significant, counts, top}
  // Filter on fdr_significant (BH-FDR q ≤ 0.05), NOT raw p — dozens of sheets are tested.
  "digit_distribution": [...],
  // per-sheet two-decimal ending counts. Each: {label, n, n_unique, top}
  "decimal_endings": [...],
  // per-sheet 3-decimal tail clustering (detectors.md `decimal_tail_clustering`).
  // Each: {label, n, top, top_share, complementary_pairs, n_distinct_fraction,
  //        collision_pairs, expected_pairs, p_value, ...}
  "decimal_tail_clusters": [...],
  // bit-identical / value-overlap across sheets (same file OR cross-file). See fields below.
  "cross_sheet_findings": [...],
  // complete registered inventory when --images is enabled
  "image_assets": [...],
  // optional deterministic, non-gating hints when --image-diagnostics is enabled
  "image_findings": [...]
}
```

`paper` provenance is populated from a `paperconan_source.json` sidecar that
`paperconan fetch --download/--auto` writes alongside the data, or from
`paperconan <dir> --doi <DOI> --title <T>`. It is `null` when neither is present
(a bare directory audit) — never read `null` as "no paper".

## `scan_status` and `coverage`

A scan can legitimately leave work undone (oversized file skipped, unreadable
workbook, detector compute budget spent). Without this record a *partial* scan
reads as a *clean* one. `scan_status` is derived from `coverage`:

- `"complete"` — everything discovered was analyzed, no limitations recorded
- `"partial"` — any file failed, sheet/block skipped, or limitation recorded;
  a quiet detector here is NOT a negative result for the capped region
- `"failed"` — files were discovered but none succeeded

```json
{
  "files_discovered": 2, "files_succeeded": 2, "files_failed": 0,
  "sheets_succeeded": 3, "sheets_skipped": 0,
  "blocks_analyzed": 3, "blocks_skipped": 0,
  "truncated": false,       // true when blocks were skipped or any *_limit limitation fired
  "limitations": [          // bounded, deduplicated event list; why coverage fell short
    {"scope": "detector", "reason": "detector_compute_budget_limit",
     "detector": "detect_row_pair_shared_fraction"}
    // scope: "file" | "sheet" | "detector" | ... ; file/sheet entries carry file/sheet keys
  ],
  "limitations_omitted": 3  // only present when the list itself hit PAPERCONAN_MAX_LIMITATIONS
}
```

The record only says what the scanner reached — never a judgement about the
data or its authors.

## `image_assets[]`

`paperconan <input-dir> --images` registers every admitted local/fetched image
and rendered PDF page. A complete asset record has this shape:

```json
{
  "asset_id": "img:0123456789abcdef0123",
  "file": "Fig3.png",
  "source_files": ["Fig3.png"],
  "path": "images/native/img-0123456789abcdef0123.png",
  "preview_path": "images/preview/img-0123456789abcdef0123.jpg",
  "preview_mime": "image/jpeg",
  "source_type": "local_image",
  "source_url": null,
  "parent_file": null,
  "page": null,
  "render_dpi": null,
  "figure_label": null,
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "width": 2480,
  "height": 1760,
  "exif_orientation": 1,
  "mime": "image/png"
}
```

- `asset_id` is content-derived and deterministic. Duplicate bytes share one
  asset and list all names in `source_files`.
- `path` is the native-pixel asset or lossless copy used for close review.
  `preview_path` is a bounded JPEG used for browsing and report embedding.
- `source_type` is `local_image`, `fetched_image`, or `pdf_page`. PDF page
  assets also carry `parent_file`, one-based `page`, and `render_dpi`.
- Full image bytes are not stored in `scan.json`. Review status belongs in the
  external Agent verdict, not in this deterministic inventory.

## `image_findings[]`

`paperconan <input-dir> --images --image-diagnostics` may add deterministic
image hints:

```json
{
  "finding_id": "image:pair:stable-id",
  "kind": "image_pair_similarity_signal",
  "severity": "medium",
  "rule": "two registered image regions retain high structural similarity under flip",
  "asset_ids": ["img:a"],
  "regions": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610]},
    {"asset_id": "img:a", "box": [820, 80, 1440, 610]}
  ],
  "method": "panel_pair_similarity",
  "score": 0.94,
  "transform": "flip",
  "evidence": {
    "crop_a_path": "images/evidence/image-pair-stable-id-a.png",
    "crop_b_path": "images/evidence/image-pair-stable-id-b.png",
    "preview_path": "images/evidence/image-pair-stable-id-preview.jpg"
  },
  "profile_action": "kept"
}
```

The current deterministic helper compares two native-coordinate regions within
one registered asset. It does not emit cross-asset comparisons; those belong
to the external multimodal Agent.

The proposal boxes in `regions` remain native coordinates. Low-information
margins are trimmed only in comparison preprocessing, where normalized
intensity and edge agreement are scored across all eight dihedral variants.
`evidence` may be `null` when the scored source is still stable but evidence
publication or its artifact budget is unavailable; the corresponding reason is
recorded in `scan_errors`. If the registered source identity changes after
scoring, the deterministic finding is suppressed instead.

For image hints, `profile_action: "kept"` is informational. The numeric
prefilter does not demote or hide `image_findings`.

These are optional, non-gating statistical signals. They are hints, not the
complete image review set. An empty `image_findings` list means only that the
optional helper emitted no registered hint; it does not resolve the
`image_assets` inventory and must not reduce Agent coverage.

## `verdict.json` image contract

The external Agent writes image and numeric conclusions into the same
paper-level `findings[]`, and all findings remain in the unified report. An
image entry can refer to a deterministic hint and registered image regions:

```json
{
  "finding_type": "image",
  "title": "Fig. 3 panel pair requires clarification",
  "finding_ref": {"finding_id": "image:pair:stable-id"},
  "image_refs": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610], "label": "A"},
    {"asset_id": "img:a", "box": [820, 80, 1440, 610], "label": "B"}
  ],
  "review_status": "needs_human",
  "impact_scope": "supporting",
  "report_md": "The registered regions retain high similarity and require source context."
}
```

`finding_ref` is optional for Agent-only observations. When deterministic
`image_findings` is empty, the Agent may still create an image entry using only
registered `image_refs`. Image `review_status` accepts `needs_human`,
`explained`, `different`, or `unresolved`; missing or unknown values become
`unresolved`.

An external Agent may also create a cross-asset observation that is not
deterministic `image_findings` output. Such an Agent-created entry omits
`finding_ref` when no deterministic finding matches:

```json
{
  "finding_type": "image",
  "title": "Cross-asset regions require clarification",
  "image_refs": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610], "label": "Fig. 3A"},
    {"asset_id": "img:b", "box": [40, 55, 660, 585], "label": "Fig. 4B"}
  ],
  "review_status": "needs_human",
  "impact_scope": "supporting",
  "report_md": "The Agent-registered comparison requires source context."
}
```

Coverage is recorded once at verdict top level:

```json
{
  "image_review": {
    "status": "completed",
    "reviewed_asset_ids": [],
    "unresolved_asset_ids": ["img:a", "img:b"],
    "unreadable_asset_ids": [],
    "deferred_asset_ids": [],
    "note": "coverage accounting completed by a multimodal Agent"
  }
}
```

Every registered asset must appear in exactly one coverage list:
`reviewed_asset_ids`, `unresolved_asset_ids`, `unreadable_asset_ids`, or
`deferred_asset_ids`. `status: "completed"` means that coverage accounting is
complete; it does not mean every image question was explained. Valid top-level
statuses are `completed`, `partial`, `unavailable_no_multimodal`, and
`not_requested`. Unknown `image_review.status` values normalize to `partial`,
while unknown image finding `review_status` values normalize to `unresolved`.

A complete mixed verdict still has one `findings[]` and produces one report:

```json
{
  "title": "Synthetic mixed review",
  "verdict": "NEEDS_HUMAN",
  "paper_conclusion": "The numeric and image material require contextual review.",
  "findings": [
    {
      "finding_type": "numeric",
      "title": "Numeric relation",
      "finding_ref": {"kind": "constant_offset"},
      "review_status": "needs_human",
      "impact_scope": "supporting",
      "report_md": "The numeric relation requires source context."
    },
    {
      "finding_type": "image",
      "title": "Image region",
      "image_refs": [{"asset_id": "img:a", "label": "Fig. 3"}],
      "review_status": "unresolved",
      "impact_scope": "supporting",
      "report_md": "The available image context is insufficient to explain the region."
    }
  ],
  "image_review": {
    "status": "completed",
    "reviewed_asset_ids": [],
    "unresolved_asset_ids": ["img:a"],
    "unreadable_asset_ids": [],
    "deferred_asset_ids": [],
    "note": "coverage accounting completed by a multimodal Agent"
  }
}
```

## Every finding has

- `kind`: detector name (see [detectors.md](detectors.md))
- `severity`: `"high"` | `"medium"` | `"low"` — the post-profile value
- `raw_severity`: the severity the detector originally assigned, frozen before
  any profile demotion rewrites `severity` in place. When `profile_action` is
  `"demoted"`/`"hidden"`, compare the two to see what the filter changed
  without re-running `--profile forensic`
- `rule`: human-readable rule string e.g. `col[27] ≡ col[28] in 9/10 rows`
- `n`: sample size for the rule
- `evidence`: numeric block snippet `{headers, rows, highlight_cols, ...}`, or an image path block; deterministic image hints may use `null` when evidence publication fails while source identity remains stable
- `likely_benign` (optional): a common innocent explanation for this kind — surface it to the user alongside the finding so a signal is never reported as a verdict
- `profile_action`: `"kept"` | `"demoted"` | `"hidden"` — what the active profile did to a numeric finding. `"demoted"`/`"hidden"` means the current `severity` is the **filter's** downgrade, not the detector's raw verdict (always `"kept"` under `--profile forensic`). Image hints use informational `"kept"` and are not numeric-prefiltered. See the Profiles section in SKILL.md.
- `false_positive_context` (list): machine tags for *why* it was demoted — e.g. `axis_or_scan_column`, `censoring_or_boundary_value`, `derived_or_unit_conversion`, `same_data_replot_or_duplicate_upload`, `omics_or_large_matrix_boundary_flood`. Map these back to the "常见误报" notes in [detectors.md](detectors.md).
- `prefilter_reason` (optional): deterministic triage explanation, especially for within-column findings. Treat it as a structured clue, not a final answer. It can explain why a pattern was kept, demoted, or considered a likely structural false positive.
- `prefilter_flags` (optional object): deterministic flags supporting the prefilter decision, such as axis/index-like labels, percentage/ratio/normalized/model-output context, low cardinality, boundary/floor/ceiling values, fixed-denominator hints, or repeated fill values. Use these with [judgment-rubric.md](judgment-rubric.md) before surfacing prefiltered hits.
- `dense_block` (optional, column-relation / equal-pair findings): `true` means this finding comes from a sheet that floods with pairwise column relations (a dense / correlated matrix — correlation tables, normalized replicate panels). Such findings are auto-demoted to `low` severity because identical/linear columns there are expected by construction, not a duplication red flag — don't treat them as high-severity signal
- `value_sample` (optional, within-column findings): small sample of distinct values from the column. Use it for repeated-value explanation, last-two-decimal checks, and fixed-denominator triage.
- `col_a_sample` / `col_b_sample` (optional, pairwise relation findings): small value samples from the relevant column(s), used as an evidence peek when the full table is large. These samples help explain cross-column transforms and relation prefilters, but they do not replace opening the original table when making a serious claim.

## block_value_duplication fields (`relations_blocks[].block_dups`)

Block-level distributed exact-repeat signal — lives in its own `block_dups`
group, not `within_col` (so the within-col flood demotion never touches it):

```json
{
  "kind": "block_value_duplication",
  "scope": "block",
  "n": 32,                       // high-precision (>=2-decimal) cells judged
  "n_repeated_values": 7,        // distinct values that recur >=2x
  "pairs": 7,                    // observed exact-collision pairs
  "excess_copies": 7,            // cells beyond the first occurrence of each repeated value
  "lambda_": 0.0001,             // Poisson expectation of coincidental pairs (birthday model)
  "p_value": 0.0,                // Poisson upper tail; the detector fired because p < 1e-4
  "dup_fraction": 0.438,         // repeated cells / n — drives severity (>=0.5 high, >=0.2 medium, else low)
  "n_distinct": 25,
  "all_integer": false,
  "value_sample": [...],
  "repeated_values_sample": [[4.2156, 2], ...],   // [value, count] pairs
  "example_cells": [[2, 5], ...],                 // 1-based (row, col) of repeated cells
  "severity": "medium",
  "rule": "block has 7 distinct high-precision values each recurring >=2x (...)"
}
```

A tiny `dup_fraction` with a near-zero `p_value` is still a reportable signal
(a few pasted values inside a big block) — it arrives at `severity: "low"`, not
dropped.

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
  - `within_row_repeat` — the same segment repeats at ≥2 non-overlapping positions of ONE row (`within_row_repeated_segment`; carries `vector`, `row`, `occurrences[]` with Excel column `range`s)
  - `identical_row` / `scaled_row` — a row reused verbatim / at a constant ratio across blocks or sheets (`identical_row_reuse` / `scaled_row_reuse`)
  - `shared_fraction` — same decimal tail, different integer parts, within one row (`within_row_shared_fraction`; `n_groups` shared-tail families) or across an aligned row pair (`shared_fraction_row_pair`; carries `run_length`, `row_a`/`row_b`)
- `cross_sheet_decimal_tail_reuse` keeps the generic `delta` of its sheet pair but is its own `kind`: changed values whose long fractional tails still align at one (row, col) offset. Carries `tail_match_count`, `offset_rows`/`offset_cols`, `min_tail_digits`, `examples[]` (`value_a`/`value_b`/`decimal_tail`), and optional `tail_benign_reason` (e.g. `constant_transform`, `fixed_denominator:1/7`, `per_column_constant` — those arrive demoted to `low`; `axis_progression` / `constant_fraction_tail` / `log_or_dilution_integer_shift_candidate` are notes only)
