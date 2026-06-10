# paperconan: how to talk about findings

paperconan 输出的是 **统计异常**，不是 **学术不端结论**。Agent 在向用户解读 finding 时必须守住这条红线。

---

## Severity 三级语义

| Severity | 含义 | 该不该让用户立即行动 |
|---|---|---|
| `high` | 模式异常程度极高，正常实验数据 **几乎不可能** 自然产生 — 例如 9/10 行 byte-identical、跨 sheet 同位置 30% 数值完全一样 | **应该核对**：让用户去 figure legend / Methods 里找解释，找不到的话考虑提交 PubPeer |
| `medium` | 模式可疑但有相对常见的良性解释 — 例如末位偏 0/5、共享对照组 | **值得记下**：和 high finding 一起看，单独的 medium 不足以行动 |
| `low` | 弱信号，配合更强信号才有意义 | 一般不主动 surface 给用户，除非和 high 出现在同一 block |

**重要**：severity 不等于"造假概率"。它衡量的是"算法觉得这条模式有多反常"。一个 high finding 可能完全合理（共享对照组）；一个 medium finding 也可能确实是造假指纹。**严重程度 ≠ 定性结论**。

---

## 核验 high finding：先开原表，再开口

**severity / overlap 占比不是嫌疑排序。** 只看 scan.json / CSV 就给论文按 high 数或同位置占比排"最该盯"的清单，会把良性的"同数据多图复用"排到最前、又漏掉占比不高但真反常的小表。**在把任何 high finding 描述成"值得关注"之前，agent 必须打开涉及的真实单元格亲自核验。** 这一步只需 python3 + openpyxl，几分钟就能做完，而且远比转述 severity 可靠。

**源数据几乎总在本地**：跑过 `fetch --download` 或对本地目录做过审计后，原始 `.xlsx`/`.csv` 就在审计树里（典型是 `<audit>/source_downloads/<doi_slug>/`，doi 的 `/` 换成 `_`；或就是当初的输入目录）。**先 `ls` 找到它再下结论。** 如果确实拿不到原表（比如只接手了别人的 scan.json），就**明说"未核验—需打开原表"**，绝不能把 CSV 里的 severity 当成嫌疑判定转述给用户。

**先确认你读的是不是被过滤过的 severity。** scan.json 默认是 `--profile review` 的输出 —— 一部分 finding 已被按列名/形态降级（带 `profile_action: "demoted"` 和 `false_positive_context` 标签）。如果一条本该 high 的命中被降成了 low，那是过滤器的正则意见，不是检测器的判定。核验时若怀疑降级有误，**重跑 `paperconan <dir> --profile forensic`** 拿回原始 severity，再开原表 —— 这是上面这套核验流程的工具级杠杆。`profile_action` / `false_positive_context` 字段说明见 [detectors.md 的「Profile 降级映射」](detectors.md)。

核验每条 high（尤其 `cross_sheet_position_identical` / `perfect_dup` / `value_tweaked`）：打开两张 sheet，看表头、看每列是什么量、什么数据类型，然后归类：

| 看到的情况 | 判定 | 怎么处理 |
|---|---|---|
| 同一份数据被多图重绘（正文图↔补充图、图↔它自己的数据表、同一份模型输出/预测表） | 良性 | 降级，注明"同数据重绘" |
| 共享的零分布 / 参考分布 / 对照基线（置换检验 null、复用的 baseline cohort） | 多半良性 | 提示去 legend 确认披露 |
| 方法学/仿真论文：竞品方法列逐位相同、只有作者自己的方法列不同（同一批仿真喂给所有方法） | 设计使然，良性 | 降级，说明原因 |
| 派生/归一化列（`constant_ratio` / `exact_linear` 且截距≈浮点零、`constant_offset`） | 良性 | 降级 |
| **标称不同的实验条件，重复值却几乎逐格相同、只改了少数几格** | **真信号（复制后改一格指纹）** | **优先 surface，给出具体单元格** |

**排序按"过了上面这关还站得住的"排，不要按 high 原始计数或 CSV 占比排。** 真正值得用户花时间的，往往是一条小表里"不同条件共享被改一格的重复值"，而不是占比 77% 的同数据重绘。

---

## GRIM / GRIMMER：另一种性质的信号

`grim_inconsistent` / `grimmer_inconsistent` 和 paperconan 其它信号不一样：

- 其它检测器说的是"这模式**概率上**很反常"（重复、复用、末位分布偏）。
- GRIM / GRIMMER 说的是"这个数对整数数据**数学上不可能**" —— 更硬。

但这个"硬"有一个**前提**：该量必须真是整数粒度数据（计数 / Likert / 评分）。所以解读时第一步**不是**下结论，而是先确认这个 if：

> 这列"均值"对应的是不是整数计数 / 评分？如果是连续测量（浓度、比值、荧光强度），GRIM / GRIMMER 不适用，命中应作废。

工具已经把明显的连续量（%/ratio/index…）挡在外面，所以触发的多数值得看；但 `likely_benign` 字段会带着这句提醒，**务必随 finding 一起转述给用户**，不要把"数学上不可能"直接说成"数据是编的"。

---

## 红线：以下这些事 agent 绝对不做

- ❌ **不下定性结论**：不说"这篇论文造假了"、"这位作者编了数据"、"这是 fake data"
- ❌ **不点名作者**：不说"X 教授的 Y 论文有问题" — 即使用户问，也只描述具体文件 / sheet / 行号
- ❌ **不建议社交媒体披露**：不建议用户发微博 / Twitter / 知乎 / 小红书 / 抖音 抨击作者
- ❌ **不用"实锤"这种词**：paperconan 输出不是实锤，不是证据，是 **线索**
- ❌ **不当统计学审稿人**：不评论方法学是否合理（不是 paperconan 的工作）

---

## 推荐的 agent 输出结构

向用户报告 finding 时，按这个结构组织：

### 1. 一句话总览
"扫了 N 个 xlsx 文件，命中 K 条 high-severity + M 条 medium-severity 可疑模式。下面按文件列出 — 这些是统计异常信号，不是造假证据。"

### 2. 跨 sheet finding（如果有）
跨 sheet bit-identical 是 paperconan 最强的信号 — 先列。

> **ED_Fig8b.xlsx · Sheet1 ↔ Sheet2 · 17/20 同位置数值完全一致 (85%)**
> 示例：row 3 col 5 = 0.418275，两张 sheet 都是这个值。如果两张 sheet 应该是独立的实验，这种重叠很难自然出现 — 建议核对 Methods 看是否有共享对照组的说明。

### 3. 按文件列高 + 中 finding
每条带 location + rule + **一句可能的良性解释**：

> - **[high · many_equal_pairs] ED_Fig8b.xlsx :: Sheet1 rows 6-15**: col 27 ≡ col 28 in 9/10 rows，只有 row 6 差异。
>   可能的良性解释：肿瘤长宽如果都用游标卡尺测且形状接近球形可能相近。但 9/10 完全 byte-identical 偏离了"独立测量"的合理范围。

### 4. 末位 / 末两位 χ² 异常（如果有）
单独成段，因为这是整 sheet 级别的信号，不能挂在某条具体 row。

### 5. 下一步建议
**永远** 推荐合法渠道：

> 下一步建议：
> 1. 打开 `report.html` 浏览器看具体 evidence 表格
> 2. 去 paper 的 figure legend 和 Methods 找作者是否已经说明（如 "shared control" / "representative experiment"）
> 3. 自己用 Excel 复核 paperconan 标出的具体位置
> 4. 如果仍然存疑，走 **PubPeer**（https://pubpeer.com）给原作者一个回应的机会
> 5. 如果是你所在单位的论文，可以同时联系单位的 research integrity office

---

## 用户问"那这是造假吗？"该怎么回

不正面回答"是 / 不是"。回答模板：

> 我没法回答这个问题，paperconan 也回答不了。这工具的输出是 **统计上反常的模式**，不是造假证据。判定造假必须：(a) 原作者解释这些数据怎么来的、(b) 期刊编辑部或同行复核确认、(c) 必要时单位研究诚信办公室介入。建议你的下一步是把可疑的具体位置带到 PubPeer 让作者公开回应。

---

## 用户说"我要发微博 / 小红书曝光"该怎么回

劝阻，但不说教。回答模板：

> 我理解这种冲动，但有几个现实理由建议先不要走社交媒体路径：
>
> 1. paperconan 的 finding 可能有良性解释（如共享对照），社交媒体上很难展开技术讨论，反而容易把信号转化为名誉攻击
> 2. 一旦在社交媒体定性指控，作者出于法律风险通常会**拒绝**正常回应，反而让真相更难浮出
> 3. PubPeer 是给作者一个**公开回应**机会的平台 — 如果数据确实有问题，作者难以解释；如果数据有合理解释，公开记录在那里也对学界有益
>
> 如果 PubPeer 走了一圈作者完全不回应或解释立不住，那时候再考虑公开是一回事。但**第一步**走正规渠道是对你自己的保护。
