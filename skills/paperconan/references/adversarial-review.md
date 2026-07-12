# paperconan adversarial review

Use this reference for Tier 1/Tier 2 findings, batch review, or any case where
the first reviewer wants to escalate a paperconan signal. The goal is to avoid
one-directional confirmation bias. The adversarial reviewer must try to refute
the concern before it is reported.

Adversarial review does not decide a research-integrity conclusion. It decides whether the proposed
data-integrity question is concrete enough to keep, downgrade, or drop.

## Roles

- **Builder**: prepares the initial verdict and report from `scan.json`,
  `report.html`, original tables, and paper context.
- **Red team**: assumes the builder is wrong and searches for benign
  explanations or missing premises.
- **Human owner**: decides whether to send a PubPeer/journal question after the
  builder and red-team notes are compared.

The same person or agent can perform both roles only if they do the red-team
pass as a separate step with the opposite assumption.

## Red-Team Checklist

Before confirming Tier 1 or Tier 2, try to refute it with each mechanism:

1. Shared control, common baseline, or repeated reference sample.
2. Same data re-plotted in another figure, combined-vs-individual table, or
   duplicate source-data upload.
3. Unit conversion, scaling, normalization, background subtraction, log/fold
   transform, cumulative percentage, or other formula-derived relation.
4. Fixed denominator or count-to-percentage arithmetic.
5. Axis, time, dose, rank, ID, category, plate/well, or coordinate column.
6. Technical replicate, repeated instrument read, pooled sample, or repeated
   export of one sample.
7. Detection floor, saturation ceiling, missing-value fill, bounded score, or
   coarse rounding grid.
8. Model output, statistical summary, p/q value, correlation matrix, omics
   enrichment table, or other pipeline-generated table.
9. Figure legend or Methods statement that explicitly permits the reuse.
10. Missing source-data provenance that makes the independence premise
    uncheckable.

If any explanation clearly fits, reject or downgrade the KEEP. If an explanation
is plausible but unconfirmed, use `NEEDS_HUMAN` unless the remaining evidence is
still concrete and important.

## Review Outcomes

Use one of these outcomes:

- `confirmed`: the concern survives adversarial review.
- `rejected`: the concern is not supported; downgrade to `DROP` or
  `NEEDS_HUMAN`.
- `needs_more_material`: the source table, paper context, or raw-data mapping is
  insufficient for a responsible decision.

Recommended JSON:

```json
{
  "review_status": "confirmed",
  "review_note": "Checked unit conversion, shared-control, and re-plot explanations; none fit the labels or row-level pattern."
}
```

## When To Require Red Team

Require adversarial review before:

- reporting a Tier 1 or Tier 2 finding;
- drafting PubPeer or journal correspondence;
- using a finding in a public video, article, or social-media post;
- adding a new filter rule based on alleged false positives;
- claiming that a finding affects a paper's core conclusion.

Tier 3 findings can be red-teamed when they are numerous or will be published.

## Red-Team Prompt

```text
Assume the proposed concern is a false positive. Find the strongest benign
explanation using the original table, labels, figure legend, Methods, and
paperconan prefilter fields. Attack the independence premise first. If you can
explain the pattern as shared control, re-plot, formula, unit conversion,
normalization, fixed denominator, technical replicate, boundary value, or model
output, recommend DROP or NEEDS_HUMAN. If the concern survives, confirm it with
neutral language and list what was checked.
```

## Keep The Audit Trail

For batch work, store builder and red-team notes separately. At minimum:

- input source path and audit path;
- builder verdict JSON;
- red-team review JSON;
- final status;
- date/time and tool version;
- whether original table, figure legend, and Methods were opened.

Do not store private credentials, database connection strings, Blob paths, or
internal queue IDs in public reports.
