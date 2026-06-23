# paperconan: how to talk about findings

paperconan 输出的是 **统计异常**，不是 **学术不端结论**。本文件只管用户回复的措辞和红线；具体 detector 判读见 [detectors.md](detectors.md)，finding 分级和 PubPeer 草稿见 [judgment-rubric.md](judgment-rubric.md)。

## Severity Is Not Verdict

| Severity | 可说的含义 | 不可说的含义 |
|---|---|---|
| `high` | 算法认为模式非常反常，值得核对原表和论文说明 | 造假概率高 |
| `medium` | 有可疑模式，但常见良性解释更多 | 不严重、可以忽略 |
| `low` | 弱信号，或被 profile/prefilter 降级后的信号 | 已经排除问题 |

`profile_action: "demoted"` / `"hidden"`、`false_positive_context`、`prefilter_reason`、`prefilter_flags` 都要一起看。默认 `review` profile 下的 severity 已经是过滤后的读数；需要原始 detector 严重度时重跑 `--profile forensic`。

## Before Saying A Finding Matters

先做这几步，再对用户说"值得关注":

1. 打开原始 `.xlsx`/`.csv`/`.tsv` 或 PDF/Word 抽取后的表格，确认高亮值确实存在。
2. 看 file / sheet / column label、figure legend、Methods，判断是否是共享对照、同数据重绘、单位换算、公式派生、归一化、模型输出、检测限或评分等级。
3. 对 cross-sheet/cross-column 先判断两组数据是否声称独立。
4. 对 `within_col_*` 先按 [judgment-rubric.md](judgment-rubric.md) 的保守基线处理；无法确认 raw independent measurement 时说 `needs human context`。
5. 如果只有别人给的 `scan.json`，没有原表，就写明"未打开原表核验"。

## Red Lines

- 不说"这篇论文造假了"、"作者编了数据"、"fake/fraud/fabricated data"。
- 不点名作者为作假者；只描述文件、sheet、列、行和数值模式。
- 不建议微博 / Twitter / 知乎 / 小红书 / 抖音曝光。
- 不用"实锤"。
- 不把 paperconan 当统计学审稿工具；它只看数值模式。

## Normal Scan Summary Template

```text
我扫了 <N> 个文件；<M> 个文件解析失败/全部解析成功。下面这些是统计异常信号，不是造假结论。

优先核对：
1. <file> :: <sheet> — <kind>, <rule>, n=<n>
   证据：<少量具体值/列名/行列位置>
   为什么值得看：<独立性前提 + 模式反常点>
   可能的良性解释：<shared control / replot / formula / rounding / LOD / fixed denominator / model output>
   还需要确认：<legend/Methods/raw data/row independence>

建议打开 audit/report.html 看高亮表格；如果核对原文后仍解释不清，再考虑 PubPeer 或 journal/research integrity office。
```

## PubPeer / Formal Note

只有在用户明确要求写 PubPeer 草稿或正式报告时，才使用 [judgment-rubric.md](judgment-rubric.md) 的 8-section 中文模板。普通 scan 摘要不要默认写成长报告。

措辞要保持问题式：

- "这些数值模式需要作者说明。"
- "如果这些行代表独立测量，这种重复/变换关系不容易自然出现。"
- "一个可能的良性解释是 shared control / formula-derived output；需要 Methods 或原始数据确认。"

避免结论式：

- "这说明数据是编的。"
- "作者造假。"
- "已经实锤。"

## If The User Asks "Is This Fraud?"

```text
我不能这样定性，paperconan 也不能。它只能指出统计上反常、值得复核的模式；是否存在学术不端需要作者解释、同行或编辑部复核，必要时由研究诚信办公室调查。现在能做的是把具体文件、sheet、列、规则和样本值整理成可复核的问题。
```

## If The User Wants Social-Media Exposure

```text
不建议第一步走社交媒体。paperconan finding 可能有共享对照、公式派生、固定分母、检测限等良性解释；在社交媒体上直接定性容易变成名誉风险，也会让正常技术回应更难。更稳妥的路径是先用 PubPeer 或期刊渠道提出具体、可复核的问题，让作者公开解释。
```
