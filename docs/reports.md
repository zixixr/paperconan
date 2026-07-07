# 报告与调参

## 报告怎么读

> **先分清两种报告。** `paperconan <dir>` 直接生成的 `audit/report.html` 是**确定性检测器的原始信号 / 人工复核工作台**——它按设计就含**大量 false positive**（共享对照、重绘坐标轴、单位换算、派生列、固定分母比值、四舍五入网格……多数命中都有完全良性的解释），而且**不代表任何结论**，不适合当作成品直接看或对外给出。
>
> **要得到一份正规、可读、经过判断的报告，请搭配 AI Agent + skill 使用**（见 [README › 快速开始](../README.md#快速开始推荐agent--skill)）：检测器只产出可复现的原始信号，Agent 在其上逐条判定（对照原表、图注、Methods，排除良性解释，再做对抗式复核），最后生成[判定后报告](#判定后-html-报告)。纯 CLI 拿不到这一步——判定本身需要一个会读上下文、会推理的 Agent 在环里。

`report.html`（分诊工作台）：顶部摘要 + "如何阅读本报告"说明 + 左侧 severity/detector/文件/关键词过滤 + finding 卡片 + last-digit histogram + cross-sheet 专段。为便于分诊，误报偏多的 **low 级信号默认折叠**（左侧一键展开），cross-sheet 等重点信号始终可见。建议顺序：

1. 先看 `scan_errors` —— 解析失败或超大文件被跳过时，不能解读成"没问题"。
2. 先看跨 sheet / 跨文件重复，再看列关系，最后才看 within-column。
3. 对降级为 low 的 finding，核 `likely_benign` / `false_positive_context` / `prefilter_reason` 是否成立。
4. 打开原始表，按 evidence 的文件、sheet、行列复核。
5. 再读 figure legend 和 Methods，确认 shared control / 重复展示 / 单位换算 / 派生指标。

（若某张密集/高相关表触发了海量成对信号，报告会按 severity 保留每个 block 的前若干条并在顶部提示省略数量，可用 `PAPERCONAN_MAX_FINDINGS_PER_BLOCK` 调整，见 [命令行与库参考 › 内存 / 输出保护](cli.md#内存--输出保护)。）

`scan.json` 完整结构见 [`references/output-schema.md`](../skills/paperconan/references/output-schema.md)。

## 误报控制：profiles 和 prefilter

检测器先产出原始 signal，`--profile` 再决定怎么处理常见误报。默认 `review`。

| profile | 行为 | 什么时候用 |
|---------|------|------------|
| `review` | 降级疑似误报，但保留可见 | 日常审计默认 |
| `forensic` | 不做降级，保留原始 severity | 怀疑默认过滤太保守、要复核原始信号 |
| `triage` | 同 `review` 判断，但把疑似误报隐藏 | 批量初筛、只想要最短清单 |

降级 / 隐藏的 finding 会带 `profile_action`（`kept` / `demoted` / `hidden`）、`false_positive_context`（如 `axis_or_scan_column`、`derived_or_unit_conversion`、`same_data_replot_or_duplicate_upload`、`omics_or_large_matrix_boundary_flood`）、`prefilter_reason` 和 `prefilter_flags`。

**重要：`review` 下的 low severity 可能是过滤器的意见，不是检测器原始判断。** 拿不准时重跑 `--profile forensic`。标签到检测器的反查表见 [`references/detectors.md`](../skills/paperconan/references/detectors.md)。

## 判定后 HTML 报告

默认的 `audit/report.html` 只展示确定性检测器输出；它不会替你判断论文。若你已经按 skill 的
[`references/adjudication-tiers.md`](../skills/paperconan/references/adjudication-tiers.md) 和
[`references/report-templates.md`](../skills/paperconan/references/report-templates.md) 写好了 `verdict.json`，可以再生成一份**判定后报告**：

```bash
paperconan report audit/scan.json --verdict verdict.json --out adjudication.html
```

`verdict.json` 使用公开 schema：`verdict`、`suspicion_tier`、`impact_scope`、`tier_why`、`drop_reason`、
`innocent_explanation`、`needs_author_data`、`report_md`、`review_status`。这份 HTML 会把 8 段式
`report_md`、Tier/impact/review 状态和 `scan.json` 的关键 evidence 放在一起，适合单篇论文复核或批量审计后的归档。

注意：`paperconan report` 是本地、公开、无私有依赖的渲染器；不读取 Postgres、Blob、云端队列或任何
`recheck/` 私有缓存。真实论文 PDF、截图、主图等材料若要展示，应由使用者在自己的审计目录中合法保存并另行归档。
