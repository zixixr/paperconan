# 批量扫描推荐工作流

下面这套是我们扫 Nature 系列 / 多期刊回填时沉淀出来的公开版流水线。核心思路是 **确定性的归确定性、判断的归 agent**：paperconan 负责拉取/初筛/过滤这些可重复、可审计的步骤；AI agent 只在已经收窄的候选集上做语义判定，并且每一步都要能被反向质疑。完整可复用协议见 [`references/batch-workflow.md`](../skills/paperconan/references/batch-workflow.md)。

```
① 拉取        ② 初筛        ③ 过滤            ④ 立卷           ⑤ 子 agent 判定      ⑥ 分级           ⑦ 对抗
fetch    →    scan    →    profile/     →   保存问题文档   →   subagent 写        →   tier-1/2/3   →   红队反向
(DOI)        (检测器)      prefilter        全量资料+发现       详细审核报告           needs-human       refute 验证
                                                                                    drop
─────── paperconan(确定性) ───────         ──────────────── AI agent 编排 ────────────────
```

## ① 拉取 — `paperconan fetch`

对一批 DOI / 标题，用 `fetch` 找开放源（Zenodo / Figshare / Europe PMC / Dryad / nature.com ESM）并下载到各自目录。`--auto` 只在 DOI 命中或标题高度一致时才下载，弱匹配会被标出来拒绝自动下载 —— 这一步要诚实对待"没找到数据 ≠ 论文干净"。

```bash
paperconan fetch "<DOI>" --auto --out runs/<paper-id>/data/
```

## ② 初筛 — `paperconan <dir>`

逐篇跑检测器，产出 `scan.json`。批量时建议 `evidence=False`（库调用）或只读 metadata，先拿到"哪些篇有信号"。

## ③ 过滤 — profile / prefilter

`--profile triage` 直接隐藏疑似误报，拿到最短候选清单；拿不准时对单篇重跑 `--profile forensic` 看原始 severity。这一步把"结构性误报海洋"砍掉，留下值得人工/agent 看的少数篇。**到这里为止都是 paperconan 自己的确定性输出，可复现、可 diff。**（profile 细节见 [报告与调参](reports.md#误报控制profiles-和-prefilter)。）

## ④ 立卷 — 保存问题文档全量资料

对每一篇过滤后仍有信号的论文，整理成一个**独立卷宗目录**，把判定要用的东西一次性备齐：

- 原始表（`.xlsx` / `.csv` / 抽出来的 PDF/Word 表）
- 该篇所有 finding，按检测器和位置**列清楚**：kind、文件/sheet、行列范围、`rule`、`n`、`col_a_sample` / `col_b_sample` 或 value sample
- `report.html` 链接，方便人随时回看高亮

立卷的意义：判定子 agent 只看卷宗就能工作，不必反复回到大目录，也便于事后审计"当时基于什么材料下的结论"。

## ⑤ 子 agent 判定 — 每篇一份详细审核报告

主 agent 把卷宗分发给子 agent（一篇/一组 finding 一个），每个子 agent **写一份详细的审核报告**，而不是只丢一个标签。报告里要回答：

- 这条信号在原表里到底长什么样（引用具体 cell）
- 最可能的良性解释（shared control、单位换算、公式派生、归一化、固定分母、边界值洪泛……）有没有被排除
- 还缺哪些人工背景（行是否独立样本、是不是原始测量、Methods/legend 怎么说）

判定纪律见 [`references/judgment-rubric.md`](../skills/paperconan/references/judgment-rubric.md) 和 [`references/interpretation.md`](../skills/paperconan/references/interpretation.md)：`within_col_*` 默认按高误报处理；优先看跨表/跨列；拿不准一律 `needs human context`，**宁可保守也不要把 severity 当成不端结论**。

## ⑥ 分级 — Tier 1 → Tier 3 / NEEDS_HUMAN / DROP

子 agent 的报告汇总后定级：

- **Tier 1**：最高复核优先级；无辜解释很难，但仍不是学术不端结论
- **Tier 2 / Tier 3**：信号真实但影响范围、上下文或良性解释空间不同
- **NEEDS_HUMAN**：必须有领域专家、原始数据、figure legend 或 Methods 才能判
- **DROP**：经判定为良性 / 误报（要写明良性理由，便于回归）

具体阶梯、`impact_scope` 和 JSON verdict 字段见 [`references/adjudication-tiers.md`](../skills/paperconan/references/adjudication-tiers.md)；正式报告格式见 [`references/report-templates.md`](../skills/paperconan/references/report-templates.md)。

## ⑦ 对抗 — 红队反向判定

对每一条进入 Tier 1 / Tier 2 或准备公开提问的结论，再派**独立的红队 agent 专门去 refute**：默认假设"它其实是误报"，去找能解释掉信号的良性机制。只有扛得住反向质疑的才保留等级，被驳倒的降级或 drop。这一步是把"看起来对、其实站不住"的结论挡在外面的关键闸门 —— 我们的经验是单向判定很容易出现 default-FP 或 default-KEEP 偏置，对抗一遍才稳。红队 checklist 见 [`references/adversarial-review.md`](../skills/paperconan/references/adversarial-review.md)。

> **为什么要这么分工**：拉取/初筛/过滤交给确定性工具，保证可复现、可回归；判定/分级/对抗交给 agent，处理工具处理不了的语义和上下文。两边都留痕，整条链路可被第三方复核 —— 这正是 paperconan 的 signal-not-verdict 立场在批量场景下的落地方式。

公开仓库只提供抽象模式和合成示例，见 [`references/case-patterns.md`](../skills/paperconan/references/case-patterns.md)。真实论文校准、下载的 source data、PDF、截图、运行报告和私有队列结果不应提交到公开 GitHub。
