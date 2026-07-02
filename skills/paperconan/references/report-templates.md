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
These are numerical signals, not misconduct conclusions.

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
以上是可复核的数据模式问题，不构成对作者意图或学术不端的判断。
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

For a paper with more than one distinct finding, use a paper-level object with
a `findings` array (each entry has its own tier/status and `finding_ref`); see
[adjudication-tiers.md](adjudication-tiers.md) › "Multiple Findings In One
Paper". `paperconan report` then renders one self-contained block per finding.

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
