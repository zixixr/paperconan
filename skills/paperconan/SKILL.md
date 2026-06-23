---
name: paperconan
version: 0.8.0
description: Use when auditing paper source-data tables for numerical integrity signals, interpreting paperconan scan.json/report.html, preparing cautious PubPeer or research-integrity notes, or finding open supplementary data from a DOI/title. Trigger on 论文数据检查, source data audit, paper data audit, suspicious numeric tables, fabrication red flags, PubPeer prep, research integrity, DOI/title data fetch. Covers .xlsx/.csv/.tsv and tables in .pdf/.docx; not image forensics or chart digitization.
---

# paperconan

paperconan scans paper source-data tables for numerical anomaly signals. Treat every hit as **signal, not verdict**: report locations and patterns, never intent or misconduct.

Tool repository: https://github.com/zixixr/paperconan

## Core Workflow

1. Confirm what the user supplied:
   - Local source-data directory: run `paperconan <input-dir>`.
   - DOI or title: run `paperconan fetch "<DOI or title>"`, choose a matched tabular dataset, download it, then scan the downloaded directory.
   - Only an existing audit: read `audit/scan.json` and point the user to `audit/report.html`.
2. Prefer the real CLI. Do not invent findings from eyeballing tables.
3. Parse `scan.json`, then load the reference file needed for the task.
4. Open the original table when describing a serious finding as worth follow-up. If the original data is unavailable, say the finding is unverified.
5. Answer cautiously: explain the anomaly, plausible benign explanations, and what human context is needed.

## Install And Run

```bash
pip install paperconan
pip install "paperconan[all]"   # includes PDF / Word table extraction
paperconan --version
paperconan <input-dir>
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
```

If Python or package access is unavailable, tell the user to run the command locally. A manual review may be offered only as a non-authoritative hint and must not be presented as paperconan output.

## Fetching Data

Use fetch only when the user gives a DOI/title instead of local files:

```bash
paperconan fetch "<DOI or title>"
paperconan fetch "<DOI or title>" --json
paperconan fetch "<DOI>" --download <id> --out data/
paperconan data/
```

Prefer candidates with `doi_in_related: true`. Repository search can return unrelated deposits, so report weak matches honestly and do not imply "no data found" means "paper is clean". Do not bypass paywalls or scrape publisher sites.

## Profiles

`--profile {review,forensic,triage}` changes what you see in `scan.json`:

- `review` is the default. It keeps likely false positives visible but may demote them to `low`.
- `forensic` preserves raw detector severity. Use it before saying a concerning hit was only low severity under the raw detector.
- `triage` hides likely false positives.

When a finding has `profile_action: "demoted"` or `profile_action: "hidden"`, the active profile changed the visible severity. Use `prefilter_reason`, `prefilter_flags`, and `false_positive_context` to explain why, then decide whether the filter reason actually fits the table context.

## Reference Routing

Load references only when needed:

- [references/output-schema.md](references/output-schema.md): read before parsing `scan.json` or explaining fields such as `profile_action`, `prefilter_reason`, `col_a_sample`, or `cross_sheet_findings`.
- [references/detectors.md](references/detectors.md): read when interpreting a detector kind and its common false positives.
- [references/judgment-rubric.md](references/judgment-rubric.md): read before ranking findings, judging within-column signals, or drafting PubPeer/research-integrity language.
- [references/interpretation.md](references/interpretation.md): read when composing the final user-facing answer or handling requests to accuse, expose, or escalate.

## Judgment Discipline

- Never convert `severity` into a misconduct conclusion. Severity means anomaly strength after the active profile, not author intent.
- Inspect cross-sheet reuse and cross-column transforms before weaker single-column patterns.
- Prefer benign structural explanations first: shared controls, re-plots, unit conversions, formulas, indices, ratios, normalized values, model outputs, detection floors, and bounded scoring scales.
- Treat `within_col_*` findings as false-positive-heavy by default. Do not strongly report `n < 10`, categorical/index labels, derived columns, fixed-denominator ratios, rounded grids, floors/ceilings, or repeated fill values.
- Use "needs human context" when you cannot confirm row independence, raw measurement status, formula generation, Methods/legend meaning, or original-table provenance.
- For PubPeer-style writing, provide concrete file/sheet/column evidence and questions for the authors; do not say "fake", "fraud", "fabricated", "实锤", or name authors as wrongdoers.

## Output Shape

A normal scan summary should include:

1. What was scanned and whether any files failed to parse.
2. The highest-priority findings after manual/field-level triage, grouped by file.
3. Concrete evidence snippets: detector kind, location, `rule`, `n`, and a small value sample when useful.
4. Plausible benign explanations and what would resolve them.
5. A pointer to `report.html` for highlighted table context.

If the user asks "is this fraud?", answer that paperconan cannot determine that. The next step is to verify the original data and, if concerns remain, ask for clarification through PubPeer, the journal, or a research integrity office.
