# paperconan adjudication tiers

Use this reference when a user asks for ranking, batch review, a KEEP/DROP
decision, or a formal report. These tiers are **review priority labels**, not
misconduct probabilities. They describe how hard it is to explain a numerical
signal innocently after checking the source table, labels, figure legend, and
Methods.

Public wording:

- `Tier 1`: strongest follow-up priority; innocent explanations are difficult.
- `Tier 2`: concrete anomaly; likely needs author/journal clarification.
- `Tier 3`: weak or peripheral anomaly; preserve context, do not overstate.
- `NEEDS_HUMAN`: key context is missing.
- `DROP`: likely benign or not meaningful after context review.

Do not say `fraud`, `fake`, `fabricated`, `造假`, or `实锤`. Say "data
inconsistency", "unexplained numerical pattern", or "needs clarification".

## Before Assigning Any Tier

Assign a tier only after these premises have been checked as far as the
available materials allow:

1. Open the original table or extracted source table, not just `scan.json`.
2. Identify file, sheet, row/column range, labels, detector, rule, `n`, and a
   small value sample.
3. Read available figure legend and Methods context when the data maps to a
   paper figure or main claim.
4. Decide whether the compared rows/columns are supposed to be independent
   measurements rather than formulas, axes, re-plots, pooled tables, or derived
   statistics.
5. List plausible benign explanations and mark which ones are supported,
   contradicted, or still unknown.

If a key premise cannot be checked, use `NEEDS_HUMAN` instead of forcing a
tier.

## Tier 1

Use `Tier 1` only when the signal is concrete, reproducible from the source
table, and hard to explain as ordinary data handling.

Typical Tier 1 patterns:

- Cross-group, cross-animal, cross-patient, or cross-treatment values are
  byte-identical when the paper presents them as independent measurements.
- Cross-figure or cross-file data show perfect reuse, strict offset/ratio, or
  copy-then-tweak behavior with no disclosed shared-control or re-plot
  explanation.
- Two columns presented as independent raw measurements have a strict transform
  such as constant offset, constant ratio, or exact linear relation across many
  rows.
- The same apparent data support different statistics, sample sizes, or p
  values in different places.
- Multiple independent anomalies in one paper point to separate data-integrity
  questions, not one obvious duplicated upload.

Tier 1 is still not a misconduct accusation. It means the case is a high
priority for author clarification or formal human review.

## Tier 2

Use `Tier 2` when the anomaly is real and visible in the source data, but an
ordinary source-data assembly, labeling, re-plot, or export mistake remains a
plausible explanation.

Typical Tier 2 patterns:

- A single block is duplicated across different labels, conditions, analytes,
  or figures, but there is no copy-then-tweak signal.
- A cross-sheet or cross-file overlap is substantial and undisclosed, but the
  affected data are supporting rather than central.
- A strict relation is present, but labels leave open a derived-column,
  normalization, unit conversion, or summary-statistic explanation.
- The source data do not clearly match the figure or legend, but the paper's
  main conclusion does not depend entirely on that panel.

Tier 2 usually deserves a clear report and a request for clarification. It does
not justify stronger language than the evidence supports.

## Tier 3

Use `Tier 3` when the signal should be preserved for transparency but is weak,
peripheral, or heavily context-dependent.

Typical Tier 3 patterns:

- Same-label, same-group, or same-figure duplicates that may be duplicate
  upload, repeated display, technical replicate export, or shared control.
- Within-column repetition after excluding obvious false positives, where row
  independence or raw-measurement status remains uncertain.
- An anomaly in a peripheral supplementary table with limited impact on the
  paper's conclusions.
- Small-`n` patterns that are odd but not strong enough for a formal concern.

Tier 3 should be brief and cautious. It often becomes `NEEDS_HUMAN` if the
source data or paper context is incomplete.

## NEEDS_HUMAN

Use `NEEDS_HUMAN` when a signal could matter but the available materials cannot
establish the premise.

Common reasons:

- The original table, figure legend, Methods, or source-data provenance is
  missing.
- It is unclear whether rows are independent samples or repeated technical
  reads.
- It is unclear whether columns are raw measurements, normalized values,
  formulas, model outputs, or statistical summaries.
- The signal depends on field-specific experimental conventions.
- The detector hit is serious-looking but comes from a false-positive-heavy
  class such as within-column repetition.

Write what exact material would resolve the ambiguity.

## DROP

Use `DROP` when a benign explanation fits the table context.

Common drop reasons:

- `axis_or_index`: time, dose, rank, ID, category, well, plate, or coordinate.
- `derived_or_formula`: normalized value, ratio, fold change, cumulative value,
  log transform, model output, p/q value, or other formula-derived column.
- `unit_conversion`: columns differ only by disclosed unit scaling.
- `shared_control_or_replot`: same data are disclosed or clearly used as shared
  control, combined-vs-individual display, or duplicate source-data export.
- `fixed_denominator`: repeated decimals are explained by `k/N` arithmetic.
- `boundary_or_fill`: zeros, ones, 100s, detection floors, saturation ceilings,
  missing-value fills, or bounded scoring levels.
- `not_independent`: rows or columns are repeated technical reads of the same
  sample or are otherwise not independent measurements.

DROP should still record the reason in batch work so future filters can learn
from repeated false-positive patterns.

## Impact Scope

When writing a formal verdict, set `impact_scope` separately from tier:

- `core`: the finding directly affects a main figure, primary quantitative
  claim, central mechanism, or headline result.
- `supporting`: the finding affects supporting evidence for the paper's
  conclusion but is not the only basis for the claim.
- `peripheral`: the finding is in a secondary supplementary analysis, quality
  control, or side result.

Tier answers "how hard is this to explain innocently"; impact answers "how
important is this data to the paper's conclusion." Keep the two axes separate.

## Verdict JSON

Batch or agent-to-agent workflows may emit one JSON object per paper:

```json
{
  "verdict": "KEEP",
  "suspicion_tier": 2,
  "impact_scope": "supporting",
  "tier_why": "cross-figure source-data block is identical under different condition labels",
  "drop_reason": null,
  "innocent_explanation": "source-data assembly or label error remains possible",
  "needs_author_data": "correct raw values and source-data mapping for the affected panel",
  "report_md": "## ...",
  "review_status": "unreviewed",
  "finding_refs": [
    {"file": "MOESM7", "sheet": "Source Data Fig.4", "rows": "5-39", "kind": "constant_offset"}
  ]
}
```

Rules:

- `KEEP` requires `suspicion_tier` in `1`, `2`, or `3`.
- Tier 1 and Tier 2 `KEEP` should include a full report.
- `DROP` requires `drop_reason` and should not include a long `report_md`.
- `NEEDS_HUMAN` requires `tier_why` explaining the missing premise.
- `review_status` is `unreviewed`, `confirmed`, or `rejected`.
- `finding_refs` is optional: a list of selectors naming which scan finding(s)
  the verdict actually adjudicated. Each selector may set any subset of
  `file` (substring), `sheet` (exact), `rows` (exact block range), `kind`
  (exact), `rule` (substring); a finding matches when every field it sets
  matches. `paperconan report` scopes the evidence panel to these findings and
  demotes the rest, so the report does not read as if every top scan signal
  were part of the verdict. Omit it to fall back to showing the top signals by
  severity.

## Multiple Findings In One Paper

When a paper has more than one distinct data-integrity signal, use a
paper-level object with a `findings` array instead of one flat verdict. Each
entry is adjudicated on its own — its own tier, impact, and review status — and
`paperconan report` renders one self-contained block per finding (badge +
reasoning + its own evidence table) under a paper header and a findings index.

```json
{
  "title": "...",
  "verdict": "KEEP",
  "paper_conclusion": "论文主结论 ...",
  "overall_impact": "core",
  "review_note": "方法 / 版本 / 背景 ...",
  "findings": [
    {
      "title": "Fig.4c shHDAC6 组 VR 两列恒定 +0.3",
      "finding_ref": {"file": "MOESM7", "sheet": "Source Data Fig.4", "rows": "5-39", "kind": "constant_offset"},
      "suspicion_tier": 1, "impact_scope": "core", "review_status": "confirmed",
      "report_md": "**位置** ...\n\n**为什么是问题** ...\n\n**无辜解释** ...\n\n**需要作者澄清** ..."
    },
    {
      "title": "ED Fig.6c 跨条件列多行相同",
      "finding_ref": {"file": "MOESM13", "sheet": "Source Data ED Fig.6", "rows": "5-39", "kind": "small_diff_set", "rule": "col[5] - col[3]"},
      "suspicion_tier": null, "impact_scope": "core", "review_status": "needs_human",
      "report_md": "..."
    }
  ]
}
```

Rules:

- Paper-level `verdict` stays explicit; the report hero shows the highest
  (numerically smallest) `suspicion_tier` across findings.
- Each finding carries its own `finding_ref`, `suspicion_tier`,
  `impact_scope`, `review_status`, `title`, and `report_md`.
- Each `report_md` here describes only that finding (position, labels, why,
  innocent explanation, questions) — the paper conclusion lives once at the top.
- The single flat form above (one `report_md` + `finding_refs`) is still valid
  and unchanged; use it when a paper has a single adjudicated finding.
