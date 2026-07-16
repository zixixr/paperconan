# paperconan report templates

Use this reference when the user asks for a report, PubPeer draft,
research-integrity note, or batch verdict. Normal scan summaries should stay
short. Formal reports are for findings that survive source-table and context
checks.

Every report must preserve the `signal-not-verdict` boundary: describe the
data pattern, not author intent.

## Short Single-Paper Summary

Use this for normal interactive audits.

```text
I scanned <input>. <N> files were read; <M> files failed to parse.
These are numerical signals, not research-integrity conclusions.

Highest-priority finding:
- Location: <file> :: <sheet>, rows <range>, columns <labels>
- Detector: <kind>, rule=<rule>, n=<n>
- Evidence: <small value sample>
- Why it matters: <independence premise + numerical pattern>
- Plausible benign explanations: <shared control / re-plot / unit conversion / formula / fixed denominator / boundary value / technical replicate>
- What would resolve it: <specific author data, legend, Methods, or raw-value mapping>

See <audit/report.html> for highlighted table context.
```

If no finding survives context review, say that paperconan found only likely
benign or context-dependent signals. Do not say the paper is clean.

## Formal Eight-Section Report

Use this for Tier 1 or Tier 2 `KEEP`, PubPeer-style drafting, or formal
research-integrity notes. Keep the language neutral and question-based.

### 1. 论文主结论

State what paper claim the affected data support. Use one or two sentences.
Do not evaluate author intent.

### 2. 异常位置

List file, sheet, figure/panel if known, row/column range, detector, `rule`,
`n`, and representative values. For within-column findings, include repeat
counts, distinct-value count, and repeated value or repeated decimal tail.

### 3. 标签含义

Explain what the labels appear to mean: groups, conditions, units, samples,
timepoints, analytes, statistical outputs, or normalization status. If the
labels cannot be interpreted without the paper, say so.

### 4. 为什么这是问题

State the independence premise. Example:

```text
如果这些列代表不同处理组的独立原始测量，那么逐行完全相同或严格固定变换不容易由普通实验波动产生。这里的重点不是判断作者意图，而是需要说明这些数值如何从原始测量得到。
```

### 5. 影响判断

Set `impact_scope` to `core`, `supporting`, or `peripheral`. Explain how the
affected data relate to the paper's main conclusion. Do not inflate a
supplementary side table into a core conclusion.

### 6. 无辜解释的层次

Use three-part reasoning for each plausible benign explanation:

```text
- 解释: <shared control / re-plot / unit conversion / formula / normalization / fixed denominator / boundary value / technical replicate / model output>
  支持它的证据: <what points toward this explanation>
  反驳它的证据: <what makes it insufficient>
  仍缺什么: <specific missing source, legend, Methods, or author clarification>
  当前判断: <fits / partly fits / does not fit / unresolved>
```

### 7. 需要作者澄清

Ask answerable questions:

- Are these rows/columns independent samples or repeated displays of the same
  measurements?
- Are the values raw measurements or formula-derived outputs?
- Is there a disclosed shared control, common baseline, unit conversion, or
  normalization step?
- Can the authors provide the raw values or corrected source-data mapping for
  the affected figure?

### 8. 证据

List reproducibility details:

- paperconan version and profile.
- Input source-data file path or public supplementary-data source.
- `scan.json` and `report.html` path if available.
- Finding kind, rule, `n`, row/column range, and small value sample.
- Whether original table, figure legend, Methods, and main text were opened.

Close with:

```text
以上是可复核的数据模式问题，不构成对作者意图或研究完整性问题的判断。
```

## Adaptive Numeric And Image Report

Use one `findings[]` for numeric and image entries and one `image_review`
coverage object. PaperConan does not configure model APIs, keys, or provider
SDKs.

An optional deterministic `image_findings[]` hint has this shape:

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

The deterministic helper compares two native-coordinate regions within one
registered asset. It does not emit cross-asset comparisons. `image_findings`
are optional hints, not the complete review set. Review every registered asset
even when this list is empty. Start with the whole image, then use a
native-pixel crop for small panels or unresolved detail.

An Agent-created image entry uses registered `image_refs`:

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
  "impact_scope": "supporting",
  "report_md": "The registered regions require figure and Methods context."
}
```

Every asset must appear in exactly one `image_review` coverage list:
`reviewed_asset_ids`, `unresolved_asset_ids`, `unreadable_asset_ids`, or
`deferred_asset_ids`. `image_review.status: "completed"` means coverage
accounting completed, not that every image question was explained. Use
`partial` when review is deferred, `unavailable_no_multimodal` when the Agent
cannot open local images, and `not_requested` only when image review was not
requested. Unknown `image_review.status` values normalize to `partial`, while
unknown image finding `review_status` values normalize to `unresolved`.

Render numeric and image entries as a single unified report:

```bash
paperconan report audit/scan.json --verdict verdict.json --out adjudication.html
```

## Batch Verdict Record

For batch work, one paper can be summarized as JSON. This schema is advisory;
it does not require a database or remote service.

```json
{
  "verdict": "KEEP",
  "suspicion_tier": 1,
  "impact_scope": "core",
  "tier_why": "strict transform across columns presented as independent raw measurements",
  "drop_reason": null,
  "innocent_explanation": "unit conversion checked and does not fit the labels",
  "needs_author_data": "raw source data and figure-panel mapping",
  "report_md": "### 1. 论文主结论\n...",
  "review_status": "unreviewed"
}
```

Use `null` for fields that do not apply. Do not include author names or
speculation about intent.

**The primary shape for a rendered adjudicated report is the paper-level object
with a `findings` array** (each entry has its own tier/status and `finding_ref`);
see [adjudication-tiers.md](adjudication-tiers.md) › "Multiple Findings In One
Paper". A single finding is just a one-element `findings` list — `paperconan
report` renders it in the same high-fidelity layout (paper header + per-finding
card + evidence heatmap), so single vs multiple is only a matter of how many
findings you list, not of presentation. The flat single-verdict schema above
stays valid and now renders in that same rich layout too.

## DROP Note

DROP records should be short:

```json
{
  "verdict": "DROP",
  "suspicion_tier": null,
  "impact_scope": null,
  "tier_why": "",
  "drop_reason": "fixed_denominator",
  "innocent_explanation": "values are percentages generated from a common small denominator",
  "needs_author_data": null,
  "report_md": null,
  "review_status": "unreviewed"
}
```

## NEEDS_HUMAN Note

NEEDS_HUMAN records should say exactly what is missing:

```json
{
  "verdict": "NEEDS_HUMAN",
  "suspicion_tier": null,
  "impact_scope": null,
  "tier_why": "source table does not identify whether rows are independent samples or technical repeats",
  "drop_reason": null,
  "innocent_explanation": "technical-repeat export remains plausible",
  "needs_author_data": "row-level sample provenance and raw instrument export",
  "report_md": null,
  "review_status": "unreviewed"
}
```
