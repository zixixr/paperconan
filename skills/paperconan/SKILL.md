---
name: paperconan
version: 0.8.5
description: Use when auditing paper source-data tables and registered image assets for statistical signals or data inconsistencies, interpreting paperconan scan.json/report.html, preparing cautious PubPeer or research-integrity notes, or finding open supplementary data from a DOI/title. Trigger on 论文数据检查, source data audit, paper data audit, suspicious numeric tables, figure review, multimodal image review, PubPeer prep, research integrity, DOI/title data fetch. Covers .xlsx/.csv/.tsv, tables in .pdf/.docx, and adaptive image review by an external multimodal Agent; not chart digitization or autonomous semantic judgment.
---

# paperconan

paperconan scans paper source-data tables and can register local image assets
for external Agent review. Treat every hit as **signal, not verdict**: report
locations and patterns, never intent or personal accusation. PaperConan does
not manage model keys or provider SDKs and does not perform autonomous semantic
judgment.

Tool repository: https://github.com/zixixr/paperconan

## Core Workflow

0. Ensure the CLI is available before scanning: run `paperconan --version`. If it is missing and pip works, install once with `pip install "paperconan[all]"` (ask first if a virtualenv or non-global install is preferred; if pip refuses with a PEP 668 `externally-managed-environment` error, use `uv tool install "paperconan[all]"` or `pipx install "paperconan[all]"` instead — see Install And Run). If Python/pip is unavailable, ask the user to install and run locally — never invent output.
1. Confirm what the user supplied:
   - Local source-data directory: run `paperconan <input-dir>`.
   - DOI or title: run `paperconan fetch "<DOI or title>"`, choose a matched tabular dataset, download it, then scan the downloaded directory.
   - Only an existing audit: read `audit/scan.json` and use `audit/report.html` as the evidence browser to triage — then give an adjudicated answer. Do not hand the raw `report.html` over as "the result" (see Report Positioning below).
2. Prefer the real CLI. Do not invent findings from eyeballing tables.
3. Read the scan in layers — start with `paperconan overview`, not by parsing the
   whole `scan.json` (see Reading A Scan In Layers below). Load the reference file
   needed for the task.
4. Open the original table when describing a serious finding as worth follow-up. If the original data is unavailable, say the finding is unverified.
5. Answer cautiously: explain the anomaly, plausible benign explanations, and what human context is needed.

## Reading A Scan In Layers

One paper's supplement routinely produces hundreds to thousands of findings and a
multi-megabyte `scan.json`. Reading all of it wastes the attention you need for
judgement, and the few signals worth acting on get buried among many routine ones.

Detectors deliberately run wide so real signals are not lost to a threshold, so
the narrowing happens here, in how you read — not in what was detected. Nothing is
filtered; you reach it in stages.

```bash
paperconan overview audit/scan.json                    # which locations carry signal
paperconan drill    audit/scan.json 2                  # that location, grouped by kind
paperconan drill    audit/scan.json 2 --kind identical_column
paperconan explain  audit/scan.json seed:17942ad206854a66
paperconan explain  audit/scan.json seed:17942ad206854a66 --full
```

Add `--json` to any of them when you need the structure rather than the text.

**Evidence windows are bounded, and say so.** The scan stores a window around
the highlighted cells, not the whole block — on a dense supplement the windows
are most of the file's bytes. A trimmed window carries its own scale, e.g.
`! this window is 20x30 of a 300x200 block, trimmed by the scan.` Read that as
"the anomaly is here, and there is more of this block than you are seeing" — not
as the block. When the exact cells matter (checking a value against the paper,
or judging whether a pattern runs the whole column), `explain --full` re-reads
the block from the source data.

`--full` refuses rather than guesses. The scan records each input's size and
timestamp, so a source that has moved, been edited, or lost the rows or columns
the finding covers returns the reason and no rows — reading it blind would hand
you a different table under this finding's heading. Two limits worth knowing:
a source rewritten to the same byte count with the timestamp restored is
undetectable (the check compares size, not content), and a
scan produced before this check existed falls back to comparing extents, which
catches a shrunk source but not an in-place edit. It also stays inside a cell
budget (`PAPERCONAN_MAX_FULL_EVIDENCE_CELLS`); a window bounded by it says so
and names that variable rather than telling you to re-run `--full`.

Work down, and stop at the shallowest layer that answers the question:

1. **overview** — which locations, how strong, what families. Often enough to see
   that a location is one derived relation restated many times.
2. **drill \<n\>** — the kinds at that location, with one concrete example each.
   A kind with a large `n` and a formulaic example is usually one structure
   expressed repeatedly; a kind with `n=1` may still be the strongest signal there.
3. **drill \<n\> --kind \<kind\>** — the individual findings, each with an id.
4. **explain \<id\>** — one finding with its parameters and evidence table. Open
   the original table before calling anything worth follow-up.

Three things to hold onto while reading:

- **`detector=high  displayed=low` means a display profile down-weighted it, not
  that it is benign.** `explain` prints the recorded reason. Judge that reason —
  a dense block or a repeated axis is often a fair call, but an exactly duplicated
  column can be demoted merely for sharing a block with many other relations.
  Do not skip a finding because its displayed severity is low.
- **Read the `!` lines — every layer has them.** `overview` and both `drill`
  forms carry a `coverage` block; `explain` states its own limits inline (an
  evidence window the scan trimmed, structured parameters only `--json` shows).
  They state what was not shown and why: locations beyond
  the listed count, findings beyond a listing limit, families this layer does not
  route (digit distributions, decimal endings, image findings), findings the scan's
  own caps dropped before the layers saw them, and detector-level caps that still
  reach no channel. Raise `--max-locations` / `--max-findings` to reach the
  remainder; the rest are limits of the scan, not of the view.
- **`scan:` lines carry scan-layer limitations, not just detector ones.** The
  shapes below are the ones you will meet most often, quoted verbatim; the code
  emits others in the same form (a sheet-scoped line appends the sheet name after
  the file). Whole-detector skips are the exception — they reach no channel and
  are never named here at all.
  - `scan: file too large in big.xlsx` — a file that was not read at all.
    `scan: unreadable in notes.xlsx` is the same class: nothing in that file was
    examined.
  - `scan: formula cache missing in m.xlsx Fig 3b (cells=['C4', 'C5', 'C6'], count=812)`
    — `count` is how many formula cells stored no cached value; `cells` is a
    short list of examples, not the whole set. paperconan never saw the numbers
    those cells compute, so they were **not** audited and the sheet is partly
    unread.
  - `scan: formula cache unreadable in m.xlsx` — the formula-cache inspection
    itself did not complete, so paperconan cannot say whether that file has
    unread formula cells. Absence of a `formula cache missing` line for it
    proves nothing. `formula metadata byte limit` and `formula metadata sheet
    limit` are the same class, naming which bound stopped the inspection.
  - `scan: report block limit in m.xlsx Fig 3b (count=3)` — block collection for
    that sheet stopped at an output budget, so later blocks were never analysed.
    Two budgets emit this same line: raise `PAPERCONAN_MAX_REPORT_BLOCKS`, and if
    that changes nothing raise `PAPERCONAN_MAX_TOTAL_FINDINGS`.
  - `scan: detector candidate pool limit in detect_short_row_reuse (limit=400)` —
    a detector stopped building candidates at its cap, so rows past it were never
    compared.
  - `scan: detector finding limit in detect_row_pair_digit_coupling (limit=25)` —
    a detector stopped emitting at its cap.
  - `scan: detector compute budget limit in detect_row_relations` — a detector ran
    out of its work budget.

  Any of these means `scan_status` is not `complete` (`partial`, or `failed` when
  nothing could be read at all) and the search was cut short, so a
  quiet result on that input is not evidence of a clean one. Detector lines name
  **no file or sheet**: the record does not carry one, so the line cannot tell you
  *where* the truncation bit — it may have been one block or many. Re-running
  unchanged proves nothing: the scan is deterministic, so it reproduces the same
  truncation byte for byte. Re-run with the matching `PAPERCONAN_*` cap raised
  (`PAPERCONAN_SHORT_ROW_MAX_ROWS`, `PAPERCONAN_ROW_PAIR_MAX_ROWS`, the
  `*_BUDGET` vars), or on a narrowed input. Some caps are bare defaults with no
  knob; for those, narrowing the input is the only remedy. A separate caveat covers whole-detector skips (a block too wide or too
  tall), which reach no channel at all and so are never named here.
- **A quiet overview is not a clean paper.** It means these detectors found
  nothing at these thresholds in the data that was supplied.

## Adaptive Image Review

Use this workflow when the user requests image review or the source directory
contains figures that should be reviewed with the numeric material:

1. Run `paperconan <input-dir> --images`; add `--image-diagnostics` only when
   deterministic hints are useful.
2. Read every entry in `image_assets`; deterministic `image_findings` are hints
   and never the complete review set. An empty `image_findings` list does not
   mean that every image question was resolved. Deterministic pair hints compare
   two regions within one registered asset only; they do not compare assets.
3. Confirm the current Agent can open local images.
   - If yes, inspect the whole image first, then use a native-pixel crop for
     small panels or unresolved detail.
   - If no, set `image_review.status` to
     `unavailable_no_multimodal`, continue numeric review, and state that image
     semantic review was not completed.
4. For every asset, record exactly one coverage outcome in
   `reviewed_asset_ids`, `unresolved_asset_ids`, `unreadable_asset_ids`, or
   `deferred_asset_ids`. `image_review.status: "completed"` means coverage
   accounting is complete; it does not mean every image question was explained.
5. Check figure labels, channels, processing steps, shared controls, insets,
   before/after layouts, figure legends, and Methods before escalating an image
   similarity signal.
6. The external multimodal Agent is responsible for cross-asset comparison and
   may create an image finding using `image_refs` even when `image_findings` is
   empty. Such Agent-only image findings belong in the verdict, not in
   deterministic `scan.json`.
7. Put numeric and image findings in the same `verdict.json findings[]`, then
   generate a single unified report with `paperconan report`.

PaperConan supplies registered local assets, bounded report previews, and
optional deterministic hints. The external multimodal Agent is responsible for
capability detection, semantic review, coverage accounting, and cautious
contextual interpretation.

## Review Modes

Choose the lightest mode that satisfies the user request:

- **Single-paper scan**: fetch/scan if needed, open the source table for serious
  findings, check labels/legend/Methods when available, then give a concise
  answer using [references/report-templates.md](references/report-templates.md)
  only if a report is requested.
- **Single-paper formal review**: after scan and source-table verification,
  load [references/adjudication-tiers.md](references/adjudication-tiers.md) and
  [references/report-templates.md](references/report-templates.md). Use Tier
  labels only as review priority / innocent-explanation difficulty, never as
  author-intent conclusions.
- **Batch review**: use [references/batch-workflow.md](references/batch-workflow.md).
  Keep deterministic paperconan output separate from agent judgment. Preserve
  DROP reasons because repeated false positives can guide future filters.
- **Adversarial review**: for Tier 1/Tier 2, PubPeer drafts, public-facing
  claims, or filter changes based on alleged false positives, load
  [references/adversarial-review.md](references/adversarial-review.md) and try
  to refute the concern before confirming it.

Do not write a full eight-section report for ordinary scan summaries. Use the
full report only for Tier 1/Tier 2 KEEP, PubPeer-style drafting, formal
research-integrity notes, or when the user explicitly asks for it.

## Report Positioning

The pipeline is **scan → agent triage/judgment → adjudicated report**. Keep the
two report artifacts distinct:

- `audit/report.html` (from the bare CLI) is a **deterministic detector /
  evidence browser** — a triage worklist. It is false-positive-heavy by design
  and represents **no judgment**. It is an intermediate artifact, not the
  user-facing deliverable. Never present it as "the audit result".
- The **user-facing deliverable is always agent-adjudicated**, produced only
  after you triage `scan.json`, open the source tables for serious findings, and
  weigh benign explanations: a short adjudicated summary for ordinary cases, or
  the eight-section report (`paperconan report scan.json --verdict verdict.json
  --out …`) for Tier 1/Tier 2 KEEP and formal/public writing.

So the raw `report.html` is what *you* read to triage; the adjudicated summary or
eight-section report is what the *user* receives. A plain CLI user who only runs
`paperconan <dir>` has no agent in the loop, so they see only the raw browser —
tell them the findings still need human/agent triage before they mean anything.

## Install And Run

```bash
pip install paperconan
pip install "paperconan[image]" # image assets, PDF page rendering, optional hints
pip install "paperconan[all]"   # includes PDF / Word table extraction
paperconan --version
paperconan <input-dir>
```

On systems where `pip install` is rejected with an `externally-managed-environment`
error (PEP 668 — e.g. Homebrew or Debian Python), install into an isolated
environment instead of forcing `--break-system-packages`:

```bash
uv tool install "paperconan[all]"   # or: pipx install "paperconan[all]"
# or a plain virtualenv:
python3 -m venv ~/.venvs/paperconan && ~/.venvs/paperconan/bin/pip install "paperconan[all]"
```

Default output:

```text
<input-dir>/audit/scan.json
<input-dir>/audit/report.html
```

Useful variants:

```bash
paperconan <input-dir> --out /tmp/audit-X
paperconan <input-dir> --md
paperconan <input-dir> --no-html
paperconan <input-dir> --profile forensic
paperconan <input-dir> --images
paperconan <input-dir> --images --image-diagnostics
paperconan report /tmp/audit-X/scan.json --verdict verdict.json --out adjudication.html
```

If Python or package access is unavailable, tell the user to run the command locally. A manual review may be offered only as a non-authoritative hint and must not be presented as paperconan output.

## Fetching Data

Use fetch only when the user gives a DOI/title instead of local files:

```bash
paperconan fetch "<DOI or title>"
paperconan fetch "<DOI or title>" --json
paperconan fetch "<DOI>" --download <id> --out data/
paperconan fetch "<DOI or title>" --auto --images --out data/
paperconan data/
paperconan data/ --images
```

Prefer candidates with `doi_in_related: true`. Repository search can return unrelated deposits, so report weak matches honestly and do not imply "no data found" means "paper is clean". Do not bypass paywalls or scrape publisher sites.

## Profiles

`--profile {review,forensic,triage}` changes what you see in `scan.json`:

- `review` is the default. It keeps likely false positives visible but may demote them to `low`.
- `forensic` preserves raw detector severity. Use it before saying a concerning hit was only low severity under the raw detector.
- `triage` hides likely false positives.

When a finding has `profile_action: "demoted"` or `profile_action: "hidden"`, the active profile changed the visible severity. Use `prefilter_reason`, `prefilter_flags`, and `false_positive_context` to explain why, then decide whether the filter reason actually fits the table context.

For deterministic image hints, `profile_action: "kept"` is informational.
Image hints do not pass through the numeric prefilter and are not demoted or
hidden by `review`, `forensic`, or `triage`.

## Reference Routing

Load references only when needed:

- [references/output-schema.md](references/output-schema.md): read before parsing `scan.json` or explaining fields such as `profile_action`, `prefilter_reason`, `value_sample`, `col_a_sample`, or `cross_sheet_findings`.
- [references/detectors.md](references/detectors.md): read when interpreting a detector kind and its common false positives.
- [references/judgment-rubric.md](references/judgment-rubric.md): read before ranking findings, judging within-column signals, or drafting PubPeer/research-integrity language.
- [references/interpretation.md](references/interpretation.md): read when composing the final user-facing answer or handling requests to accuse, expose, or escalate.
- [references/adjudication-tiers.md](references/adjudication-tiers.md): read before assigning `Tier 1/2/3`, `KEEP`, `DROP`, `NEEDS_HUMAN`, or `impact_scope`.
- [references/report-templates.md](references/report-templates.md): read before writing a formal report, PubPeer draft, research-integrity note, or batch verdict JSON.
- [references/adversarial-review.md](references/adversarial-review.md): read before confirming Tier 1/Tier 2, public-facing concerns, or proposed filter changes.
- [references/batch-workflow.md](references/batch-workflow.md): read when reviewing multiple papers or organizing candidate queues.
- [references/case-patterns.md](references/case-patterns.md): read for synthetic calibration patterns only; do not treat them as real case precedents.

## Judgment Discipline

- Never convert `severity` into an author-intent conclusion. Severity means anomaly strength after the active profile, not author intent.
- Never convert `Tier 1/2/3` into an author-intent conclusion. Tier means follow-up priority and difficulty of innocent explanation after context review.
- Inspect cross-sheet reuse and cross-column transforms before weaker single-column patterns.
- Prefer benign structural explanations first: shared controls, re-plots, unit conversions, formulas, indices, ratios, normalized values, model outputs, detection floors, and bounded scoring scales.
- Treat `within_col_*` findings as false-positive-heavy by default. Do not strongly report `n < 10`, categorical/index labels, derived columns, fixed-denominator ratios, rounded grids, floors/ceilings, or repeated fill values.
- Use "needs human context" when you cannot confirm row independence, raw measurement status, formula generation, Methods/legend meaning, or original-table provenance.
- For PubPeer-style writing, provide concrete file/sheet/column evidence and questions for the authors; do not accuse authors or state an intent conclusion.
- Do not use real papers as public calibration examples unless the user has
  explicitly asked to prepare a specific public note and the evidence has been
  checked against source data and paper context.

## Output Shape

A normal scan summary should include:

1. What was scanned and whether any files failed to parse.
2. The highest-priority findings after manual/field-level triage, grouped by file.
3. Concrete evidence snippets: detector kind, location, `rule`, `n`, and a small value sample when useful.
4. Plausible benign explanations and what would resolve them.
5. A pointer to `report.html` for highlighted table context.

For batch or agent-to-agent workflows, an optional verdict JSON may use:
`verdict`, `suspicion_tier`, `impact_scope`, `tier_why`, `drop_reason`,
`innocent_explanation`, `needs_author_data`, `report_md`, `review_status`, and
`finding_refs` (selectors naming which scan finding(s) the verdict adjudicated,
so the rendered report scopes its evidence panel to them). When a paper has
more than one distinct finding, use a paper-level object with a `findings`
array (each entry adjudicated on its own tier/status with its own
`finding_ref`); the report then renders one self-contained block per finding.
See [references/adjudication-tiers.md](references/adjudication-tiers.md) and
[references/report-templates.md](references/report-templates.md).

For adaptive image review, `scan.json image_assets[]` is the complete registered
asset inventory while `image_findings[]` contains only optional deterministic
hints. Add Agent conclusions as `finding_type: "image"` entries with
`image_refs`, and add top-level `image_review` coverage. Numeric and image
entries, including Agent-only cross-asset observations, all remain in the same
`findings[]` and the single unified report; do not create a separate image
verdict or a second user-facing report.

When a verdict JSON already exists, `paperconan report <scan.json> --verdict
<verdict.json> --out <html>` renders a separate adjudicated report. Do not
confuse this with the default deterministic `audit/report.html`; the
adjudicated report is only as reliable as the human/AI verdict and source
context behind it.

If the user asks for an author-intent conclusion, answer that paperconan cannot determine that. The next step is to verify the original data and, if concerns remain, ask for clarification through PubPeer, the journal, or a research integrity office.
