# paperconan judgment rubric

Use this reference when deciding how strongly to surface a paperconan finding or when drafting a PubPeer/research-integrity note. The public vocabulary is:

- `reportable concern`: a concrete anomaly that remains hard to explain after checking table context and common benign mechanisms.
- `likely benign`: the pattern is explained by table structure, formulas, shared data, rounding, bounded values, or another ordinary workflow.
- `needs human context`: the finding could matter, but the agent cannot confirm a key premise from the available data.

Do not use internal batch-review labels in user-facing output.

## First-Pass Ordering

1. Surface `scan_errors` before findings. A failed parse means the audit is incomplete.
2. Review cross-sheet and cross-file reuse first. `cross_sheet_position_identical` with `delta.pattern: "value_tweaked"` is much stronger than a single-column digit pattern.
3. Review cross-column transforms next: `identical_column`, `constant_offset`, `constant_ratio`, `exact_linear`, `sum_constant`, and `many_equal_pairs`.
4. Review summary-stat consistency separately. GRIM/GRIMMER can be hard evidence only when the measured quantity is truly integer-granular.
5. Review `within_col_*` last. These are false-positive-heavy and need more context than the detector output alone.

## General Decision Rules

Classify as `reportable concern` only when the central premise survives:

- the rows or columns are supposed to be independent measurements;
- the suspicious values are raw measurements or clearly reported primary data, not formulas/model outputs;
- table labels and Methods do not disclose a shared control, re-plot, unit conversion, normalization, or bounded scale;
- the evidence can be named concretely by file, sheet, row/column, detector, and value examples.

Classify as `likely benign` when a common structural explanation fits the table:

- axis/index/time/dose/category/cluster/score-level columns;
- percent, ratio, normalized, log, fold-change, abundance, p/q-value, AUC, coverage, or model-output columns;
- shared controls, repeated technical reads, duplicate uploads, combined-vs-individual re-plots, or simulation inputs reused across methods;
- detection floors/ceilings, zero/non-detect values, default fills, manual scoring levels, Likert scales, or coarse rounding grids.

Classify as `needs human context` when the answer depends on missing information:

- the original table or highlighted cells cannot be opened;
- row independence is unclear;
- column definitions, units, Methods, or figure legends are missing;
- a formula explanation is plausible but not confirmable;
- the finding is serious-looking but based on a single weak class such as within-column repetition.

## Cross-Sheet And Transform Findings

Strong reuse signals still require context:

- `perfect_dup`: often a clean re-plot or duplicate upload. Report only if the paper claims independence or the duplicate is undisclosed.
- `superset`: may be a legitimate combined table. Ask whether one sheet aggregates the other.
- `value_tweaked`: highest priority; copy-then-edit patterns are harder to explain innocently.
- strict transforms: usually benign if labels indicate unit conversion, normalization, cumulative percentages, or model-derived columns. Suspicious only if both columns are presented as independent raw measurements.

For every serious cross-sheet or transform finding, quote a small number of concrete matching values and say what independence assumption would make the pattern concerning.

## Within-Column Conservative Baseline

`within_col_value_duplication` and `within_col_decimal_repetition` are false-positive-heavy. Treat them as `likely benign` or `needs human context` unless the raw-data premise is positively established.

Never make a strong report from within-column evidence when any of these apply:

- `n < 10`;
- the label looks like rank, ID, index, bin, cluster, class, category, group, type, score level, grade, stage, timepoint, day, dose, concentration, channel, well, or plate;
- the column is a percentage, ratio, proportion, abundance, normalized value, log/log2 transform, z-score, fold change, rate, coverage, AUC, p/q-value, statistic, or model output;
- repeated values are integers, half-integers, one-decimal values, common thresholds, floors/ceilings, zeros/ones/100s, missing-value fills, or manual scores;
- many similar hits come from one statistical/model-output table, suggesting a pipeline artifact rather than independent events.

### Fixed Denominator Check

Shared decimal endings such as `.33`, `.67`, `.25`, `.75`, `.1667`, and `.8333` are often `k/N` arithmetic. For `within_col_decimal_repetition`, run a fixed denominator check over the sampled values before treating the finding as meaningful.

Minimum test:

```text
For N=2..500:
  check whether value * N is near an integer for most sampled values.
For percentage-looking values:
  also check whether value / 100 * N is near an integer.
```

Use a tolerance around `1e-6` for stored precision or `1e-4` for coarse displayed values. If most sampled values are explained by one small denominator, classify as `likely benign` with reason `fixed denominator`.

### Decimal Repetition

Shared last-two digits are suspicious only if the column is a **raw independent measurement**. They are usually benign for:

- percentages and ratios;
- averages from a small fixed sample size;
- finite-precision model outputs;
- standardized or normalized values;
- Excel formulas and rounded displays.

If raw independent measurement status cannot be confirmed, use `needs human context`.

### Value Duplication

Single-column value duplication deserves emphasis only when all of the following are true:

- the repeated value is non-round, non-threshold, non-fill, non-default, and continuous;
- rows represent different independent samples, animals, patients, or conditions;
- the value is not a limit-of-detection floor, saturation ceiling, background subtraction artifact, reading-grid value, or rounded display;
- repeated technical replicates, shared batch controls, and repeated reads of the same sample have been ruled out.

If any premise is unresolved, classify as `needs human context`; if a structural explanation fits, classify as `likely benign`.

## GRIM And GRIMMER

`grim_inconsistent` and `grimmer_inconsistent` are different from pattern detectors. They can indicate a mathematical impossibility, but only for integer-granular data such as counts, Likert scores, or ratings.

Before reporting them as strong:

- confirm the mean/SD columns refer to integer data;
- confirm `SD` is not actually `SEM`;
- surface the `likely_benign` reminder if present;
- avoid saying the data was fabricated. Say the reported summary is inconsistent with the stated integer-data premise.

## PubPeer-Ready Chinese Template

Use this 8-section template only when the user explicitly asks for a PubPeer draft or formal research-integrity note. Normal scan summaries should be shorter.

### 1. 论文主结论

用一两句话说明这组数据支撑的论文结论。不要评价作者动机。

### 2. 异常位置

列出 file / sheet / figure / column / row range、detector、`rule`、`n`、关键值样本。within-column 情况要写明 `frac_repeat`、`n_distinct`、重复值或重复末两位。

### 3. 标签含义

解释列名、单位、组别、样本来源。若标签需要 Methods/legend 才能解释，写明"需要作者或原文进一步说明"。

### 4. 为什么这是问题

说明独立性前提。示例句：

> 如果这些是独立测量，数字末位本该像随机抽签一样分散；现在大量不同数值却共享同一末尾，更像它们是从同一个公式、分母或处理流程里生成的。所以重点不是断言造假，而是请作者说明这些数值如何从原始测量得到。

### 5. 影响判断

说明该数据对主结论是 core、supporting 还是 peripheral。不要夸大边缘补充表的影响。

### 6. 无辜解释的层次

按顺序写已经检查或仍需检查的解释：共享数据/重绘、固定分母、公式派生、归一化、四舍五入网格、检测限/饱和值、技术重复、模型输出。若做了 fixed denominator 检查，写明测试过的 `N=2..500` 范围和结果。

### 7. 需要作者澄清

提出可回答的问题，例如：这些行是否独立样本？该列是否由公式生成？原始仪器读数是什么？是否有共享对照或重绘说明？

### 8. 证据

列出 paperconan 版本、profile、文件路径或补充材料来源、sheet/figure、finding kind、`rule`、`n`、样本值、以及 `report.html` 可复核位置。

Close with a neutral sentence: "以上是可复核的数据模式问题，不构成对作者意图或学术不端的判断。"
