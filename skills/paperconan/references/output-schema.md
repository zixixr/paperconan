# paperconan output schema (scan.json)

Full structure of the `scan.json` the agent parses. The SKILL.md keeps only the
essentials; this file is the complete reference (it travels in the skill bundle).

## scan.json top-level schema

```json
{
  "schema_version": 2,
  "tool": "paperconan",
  "tool_version": "0.8.3",        // matches the pyproject version; provenance for archived reports
  "scanned_at": null,             // deterministic default; timestamp only when runtime metadata is requested
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
  "findings_omitted": 0,
  // present and true when findings_omitted is only a proved lower bound
  "findings_omitted_is_lower_bound": true,
  "scan_errors": [                // files that failed to parse — surface these, don't imply a clean scan
    {"file": "broken.xlsx", "error": "..."}
  ],
  "scan_stats": {                 // per-file / per-sheet sizing + optional timing
    "files": [...], "sheets": [...], "elapsed_ms": null
  },
  "n_image_source_files": 2,
  "n_image_assets": 3,
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
  // cross-table statistical signals (same file OR cross-file): position/value overlap,
  // decimal-tail reuse, repeated columns/vectors, and within-table fraction reuse.
  "cross_sheet_findings": [...],
  // complete registered inventory when --images is enabled
  "image_assets": [...],
  // optional deterministic, non-gating hints when --image-diagnostics is enabled
  "image_findings": [...]
}
```

Default scans are deterministic: repeated scans of identical input write
byte-identical `scan.json` files. The existing runtime keys remain present, but
`scanned_at` and every scan/file/sheet `elapsed_ms` value are `null`. File
entries under `scan_stats.files` use paths relative to `input_dir`.

Runtime metadata is opt-in through library
`scan_dir(..., include_runtime=True)` or CLI `--runtime-metadata`. Archived
scans containing timestamp and elapsed values remain valid and renderable.
HTML and Markdown reports omit runtime metadata when the values are `null`.

## Scan completion and coverage

Schema version 2 adds `scan_status` and `coverage` so an empty finding list can
be interpreted together with the amount of input that actually reached numeric
scanning:

- `complete`: at least one sheet reached numeric scanning and no coverage
  limitation was recorded. The CLI exits zero.
- `partial`: some input reached numeric scanning, but one or more files, sheets,
  detector paths, rows, blocks, or retained findings were limited. The CLI
  exits zero.
- `failed`: no sheet reached numeric scanning. The CLI first writes diagnostic
  `scan.json` and requested HTML/Markdown outputs, then exits nonzero.

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

Recurring-row source-window exhaustion uses:

```json
{
  "scope": "sheet",
  "reason": "recurring_row_vector_budget",
  "file": "table.xlsx",
  "sheet": "Data",
  "windows_skipped": 22,
  "windows_skipped_is_lower_bound": true,
  "limit": 3
}
```

`windows_skipped` is exact when every candidate row was inspected. When the
budget stops later source-row reads, `windows_skipped_is_lower_bound: true`
states that the serialized count is only a lower-bound: it includes exact
skipped windows from inspected rows, while unread rows may contain more.
This limitation makes scan coverage partial even when the known lower bound is
zero.

Within-row repeated-segment work exhaustion uses the same lower-bound rule:

```json
{
  "scope": "sheet",
  "reason": "within_row_repeated_segment_budget",
  "file": "table.xlsx",
  "sheet": "Data",
  "limit": 2000000,
  "windows_skipped": 0,
  "windows_skipped_is_lower_bound": true
}
```

The detector has three independent retained-state/output controls:

- `within_row_repeated_segment_unique_vector_limit` is sheet-scoped and reports
  `limit`, `max_unique_vectors_retained`, `skipped_new_windows`, and
  `omitted_findings_lower_bound`. The unique-vector map is bounded per row;
  already-retained vectors continue to receive later occurrences.
- `within_row_repeated_segment_row_cell_limit` is sheet-scoped and reports
  `limit`, `rows_limited`, `numeric_cells_skipped_lower_bound`, and
  `omitted_findings_lower_bound`.
- `within_row_repeated_segment_candidate_limit` and
  `within_row_repeated_segment_finding_limit` are scan-scoped. They distinguish
  `candidate_findings_omitted` from `output_findings_omitted`; both also carry
  `omitted_findings` for the top-level additive count.

Work, unique-vector, or row-cell limits can leave additional finding omissions
unknown, so top-level `findings_omitted_is_lower_bound` is `true`. Candidate
and output omissions are exact for the candidates that reached finalization.

When recurring-row-vector finalization exhausts a retained-state or work
budget, `coverage.limitations` contains this complete shape:

```json
{
  "scope": "scan",
  "reason": "recurring_row_vector_finalization_limit",
  "candidate_limit": 10000,
  "pair_limit": 200000,
  "cell_limit": 1000000,
  "qualifying_candidates": 12000,
  "candidates_retained": 10000,
  "candidates_omitted": 2000,
  "candidates_processed": 8500,
  "pair_comparisons": 200000,
  "cell_references_retained": 740000,
  "limits_reached": ["pair"],
  "omitted_findings_lower_bound": 0
}
```

`candidate_limit`, `pair_limit`, and `cell_limit` are configured limits.
`candidates_processed`, `pair_comparisons`, and
`cell_references_retained` are the corresponding completed work/state
counters. `qualifying_candidates`, `candidates_retained`, and
`candidates_omitted` describe candidate coverage.

When this limitation is present, top-level
`findings_omitted_is_lower_bound` is `true`. Neither
`findings_omitted` nor `omitted_findings_lower_bound` may be interpreted as an
exact omitted total: a lower-bound value proves only that at least that many
findings were omitted, and additional omissions may be unknown.

Dense detector admission and cross-table budgets use additional structured
limitations:

- `dense_block_detector_limit`: one block-level object listing detector
  families whose detector-owned row/work/state checks stopped before a complete
  candidate could run. `work_examined` is concrete completed source work.
  `work_skipped_lower_bound` is the authoritative minimum when skipped
  branch-dependent work cannot be known without executing it; it must not be
  read as an exact skipped total. `state_required` is the complete
  detector-declared simultaneous-state upper bound,
  `state_required_lower_bound` is the largest simultaneous reservation actually
  attempted, and `peak_state_units` is the accepted live-state peak. State is
  measured in 8-byte float64-equivalent units and reserved before allocation.
- `wide_integer_block_index_limit`: one sheet-level object emitted before
  detector allocation when the compact wide-integer block range index cannot
  fit the same 8-byte state-unit limit. It reports the required/available
  units, zero peak retained state on rejection, skipped detector blocks, wide
  integer cells, and an explicit affected-block lower bound.
- `cross_sheet_summary_count_limit`, `cross_sheet_grid_cell_limit`,
  `cross_sheet_label_cell_limit`, `cross_sheet_label_byte_limit`, and
  `cross_sheet_column_fingerprint_limit`: one scan-level object per exhausted
  retained-summary dimension. Each reports the configured limit, retained
  amount, skipped sheets/items, and unavailable summary-pair count. A
  fingerprint-capacity rejection also reports `candidate_columns_skipped`
  before source rows are scanned and
  `candidate_columns_may_qualify: true`, because those unscanned columns may
  have qualified.
- `cross_sheet_work_limit`: one scan-level object for shared pair, value,
  decimal-tail tuple, and pre-cap finding budgets. It reports work performed,
  known skipped work/findings, and `limits_reached`. Detector-family pairs are
  counted independently, impossible detector families are excluded before
  admission, and positional/value work is one source-grid pass per side.
  Positional/value and decimal-tail helpers admit their complete pair/value
  upper bound immediately before their detector-owned source-grid loop and
  report concrete visits on normal exits. Only later never-entered candidates
  move to the linear remaining-work ledger.

`cross_sheet_work_limit` also exposes additive axis-classification coverage:

- `axis_context_available` says whether complete axis context was produced.
- `axis_loading_visits`, `axis_grouping_visits`,
  `axis_progression_visits`, and `axis_fingerprint_visits` are concrete
  completed visits over recurrence-support summaries and eligible position
  columns.
- `axis_recurrence_order_visits`, `axis_recurrence_group_visits`,
  `axis_recurrence_comparison_visits`, `axis_recurrence_mark_visits`, and
  `axis_output_visits` are concrete completed compact-finalization visits.
  Every pass is admitted before it runs, and exact payload comparisons are
  admitted individually.
- `axis_work_skipped_lower_bound` is the known minimum skipped axis work.
  When `axis_work_skipped_is_lower_bound` is `true`, remaining
  outcome-dependent comparisons/marks cannot be known without execution and
  the value is not exact. When it is `false`, all remaining work was knowable
  and the reported skipped value is exact.
- `axis_state_unit_limit` is the fixed-multiplier private-workspace cap in
  8-byte units; `axis_peak_state_units` is the accepted live
  axis-classification peak.

Four- and five-cell grids remain recurrence-support context for compatibility,
but never enter pair comparison or the final axis mapping. The finding cap is
enforced during detection. `bucket_findings_skipped` reports exact omissions
after the stable first ten column-duplicate findings in one fingerprint
bucket; those omissions are included once in `findings_skipped`.

Selection follows stable input order and stops before a configured limit is
exceeded. A rejected detector candidate or summary is not treated as complete.
Known finding omissions are counted exactly; pair/value/tail exhaustion can
make the total unknowable, so top-level
`findings_omitted_is_lower_bound: true` remains the authoritative disclosure.
These fields are additive: archived scans and verdicts that predate them remain
accepted through the existing compatibility path. No new public environment
control was added.

Archived schema-version-1 scans may omit `schema_version`, `scan_status`, and
`coverage`. Both HTML and Markdown renderers accept that shape and label it as a
legacy scan whose detailed coverage status is unavailable.

`paper` provenance is populated from a `paperconan_source.json` sidecar that
`paperconan fetch --download/--auto` writes alongside the data, or from
`paperconan <dir> --doi <DOI> --title <T>`. It is `null` when neither is present
(a bare directory audit) — never read `null` as "no paper".

## `image_assets[]`

`paperconan <input-dir> --images` registers every admitted local/fetched image
and rendered PDF page. A representative asset record is:

```json
{
  "asset_id": "img:a",
  "file": "Figure1.png",
  "path": "images/native/img-a.png",
  "preview_path": "images/preview/img-a.png",
  "source_type": "local_image",
  "parent_file": null,
  "page": null,
  "figure_label": "Fig. 1",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "width": 1600,
  "height": 900,
  "mime": "image/png"
}
```

The inventory is the coverage source of truth. Full image bytes are not stored
in `scan.json`; the paths refer to bounded artifacts rooted under the audit
directory. Agent review status belongs in the verdict rather than this
deterministic inventory.

## `image_findings[]`

`paperconan <input-dir> --images --image-diagnostics` may add optional,
non-gating deterministic hints. The current pair helper compares two regions
inside one registered asset:

```json
{
  "finding_id": "image:pair:1",
  "kind": "image_pair_similarity_signal",
  "severity": "medium",
  "rule": "two registered regions retain high structural similarity",
  "asset_ids": ["img:a"],
  "regions": [
    {"asset_id": "img:a", "box": [0, 0, 800, 900]},
    {"asset_id": "img:a", "box": [800, 0, 1600, 900]}
  ],
  "method": "panel_pair_similarity",
  "score": 0.97,
  "transform": "identity",
  "profile_action": "kept"
}
```

These hints never replace complete asset review. An empty `image_findings`
list means only that the optional helper emitted no registered hint.

## `verdict.json` image contract

An external multimodal Agent may add a finding even when no deterministic image
finding exists:

```json
{
  "finding_type": "image",
  "title": "Registered image regions require contextual review",
  "finding_ref": null,
  "image_refs": [
    {"asset_id": "img:a", "box": [0, 0, 800, 900], "label": "left region"},
    {"asset_id": "img:a", "box": [800, 0, 1600, 900], "label": "right region"}
  ],
  "review_status": "needs_human",
  "report_md": "The registered regions require figure and Methods context."
}
```

Every registered asset must appear in exactly one top-level coverage list:

```json
{
  "image_review": {
    "status": "completed",
    "reviewed_asset_ids": [],
    "unresolved_asset_ids": ["img:a"],
    "unreadable_asset_ids": [],
    "deferred_asset_ids": [],
    "note": "all registered assets received a coverage outcome"
  }
}
```

Valid statuses are `completed`, `partial`, `unavailable_no_multimodal`, and
`not_requested`. Unknown `image_review.status` values normalize to `partial`,
while unknown image finding `review_status` values normalize to `unresolved`.
Numeric and image findings share one `findings[]` and one unified report.

## Adjudicated verdict evidence binding

`paperconan report scan.json --verdict verdict.json` reads scan findings as
evidence and binds verdict selectors without modifying `scan.json`. Both the
primary `findings[].finding_ref` shape and the legacy top-level `finding_refs`
shape use these rules:

- An omitted or `null` numeric `finding_ref` may select the strongest visible
  numeric scan finding automatically. The HTML labels this as automatic
  evidence selection and does not cross into image evidence.
- Every explicit selector must resolve to exactly one visible scan finding.
  `kind` and `rows` match exactly, while `rule` retains substring matching.
- Canonical exact location identities have priority. Block selectors use exact
  `file` and `sheet`; cross-table generic `file` / `sheet` selectors match exact
  `file_a` / `sheet_a` or `file_b` / `sheet_b` endpoints, and a supplied file
  and sheet must belong to the same endpoint.
- Legacy file substring matching is attempted only when there are zero
  canonical exact candidates, and it binds only when the remaining visible
  candidate is unique. A supplied sheet still matches exactly.
- An unmatched or ambiguous explicit selector, including `{}`, shows the
  selector and does not substitute an unrelated evidence table.
- Every additional legacy `finding_refs` selector is bound independently, so
  matched and unmatched entries remain visible in their original order.

These additive relation-model fields do not change `finding_ref` matching and
do not require a `schema_version` increment. An archived verdict remains bound
to its archived scan; rerunning a changed detector can still produce a
different primary `kind`.

An explicit primary `"findings": []` remains an empty primary verdict. The
renderer does not synthesize a legacy finding from top-level `report_md` or
`finding_refs` in that case. Profile-hidden scan findings remain unavailable for
evidence binding, and visible findings retain deterministic strongest-first
ordering.

## Every finding has

- `kind`: detector name (see [detectors.md](detectors.md))
- `severity`: `"high"` | `"medium"` | `"low"`
- `rule`: human-readable rule string e.g. `col[27] ≡ col[28] in 9/10 rows`
- `n`: sample size for the rule
- `relation_model_ambiguous` (optional): `true` when float representation and
  intercept uncertainty leave both a proportional and nonzero-intercept
  affine model compatible with the stored values. This qualifies the
  statistical signal; it is not a final judgment.
- `relation_model_alternatives` (optional): deterministic compatible kinds,
  currently `["constant_ratio", "exact_linear"]`. It appears only with
  `relation_model_ambiguous: true`.
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

This array is the generic **cross-table statistical signals** family; the
historical key and `cross_sheet_*` kind names remain for schema compatibility.
It can contain:

- `cross_sheet_position_identical`
- `cross_sheet_value_overlap`
- `cross_sheet_decimal_tail_reuse`
- `cross_sheet_column_duplicate`
- `recurring_row_vector`
- `within_row_repeated_segment`
- `within_table_fraction_reuse`

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
  - `within_row_repeat` — one high-information segment appears at non-overlapping positions within one physical row (`within_row_repeated_segment`; carries `vector`, `row`, `start_cols`, `end_cols`, bounded `occurrences`, and `n_occurrences`). The detector compares the row's numeric sequence: non-numeric cells are omitted from vector construction, while reported columns remain physical 1-based sheet coordinates.
