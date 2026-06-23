# paperconan detectors reference

每个检测器：**原理** · **典型命中** · **常见误报**。Agent 在向用户解读 finding 之前应该 skim 一遍对应条目。

---

## 跨列关系类 (block-level relation detectors)

### `identical_column`
- **原理**：同一 block 内两列每一行数值完全一致（atol=1e-9）。
- **典型命中**：作者用同一列数据填了两次声称独立的列。
- **常见误报**：极少。如果两列 header 写的都是同一指标（如同一对照组在两张图重复使用），可能合理。

### `constant_offset`
- **原理**：col_b - col_a 在所有行上为同一非零常数。
- **典型命中**：col_b 是 col_a 加了 k 后捏造出来的"实验组"。
- **常见误报**：测量受到固定偏置（如温度补偿）— 但通常文章里会说明。

### `constant_ratio`
- **原理**：col_b / col_a 在所有行上为同一比例（非 1）。
- **典型命中**：col_b 是 col_a 乘了 k 倍后伪造的"处理组"。
- **常见误报**：单位换算（mg → ng × 1000）；剂量梯度时间轴。

### `sum_constant`
- **原理**：col_a + col_b 在所有行上为同一常数 K。
- **典型命中**：百分比对（前/后 = 100）；两组互补造数。
- **常见误报**：真实互补关系如分配比例（合理共存）。

### `exact_linear`
- **原理**：col_b = slope × col_a + intercept，残差 ~0，r > 0.99，且非 identical/offset/ratio。
- **典型命中**：用线性公式从一列推出另一列。
- **常见误报**：物理学/化学上确有严格线性关系的量（吸光度 vs 浓度的标准曲线）。

### `small_diff_set`
- **原理**：col_b - col_a 只取 2-6 个离散值。
- **典型命中**：作者从一组 base 数据派生小幅度扰动得到"独立"实验。
- **常见误报**：定量分级 / 离散刻度测量。

### `many_equal_pairs`
- **原理**：两列 ≥ 50% 行 byte-identical，但不是完全相同（有少量手改痕迹）。
- **典型命中**："9/10 完全一致只改 1 格" 的造假指纹。
- **常见误报**：肿瘤长宽常常相近但本来就独立测量 — 看 figure legend。

---

## 单列模式类 (within-column detectors)

### `arithmetic_progression`
- **原理**：整列等差（diff 恒定，且非 0）。
- **典型命中**：理论 / 模拟生成的对照组被误标为实验组（1, 2, 3, … 整数）。
- **常见误报**：剂量梯度、时间轴、index 列。Agent 看到这条要先确认列名。

### `within_col_value_duplication`
- **原理**：同列内某个具体数值重复出现 ≥ 一半的行数（且不是全相同）。
- **典型命中**：非圆整连续测量值（如 `0.208975`）在多行独立样本里反复出现，且这些行不是技术重复、共享对照或同一条件重复读数。
- **常见误报**：检出限以下截断（LOD）、饱和上限、背景扣除后的固定值、零/一/100 等边界值、缺失/默认填充值、人工评分等级、四舍五入网格、技术重复、共享 batch control。
- **解读时**：高门槛。只有在重复值是非圆整、非阈值、非填充值的连续测量，且行与行确认为独立样本时，才值得重点报告。否则按 `likely benign` 或 `needs human context` 处理。

### `within_col_decimal_repetition`
- **原理**：同列中 ≥ 2/3 数值末两位完全一致（如 `.25` / `.75`）。
- **典型命中**：一列原始独立测量的不同取值大量共享末两位，且不能由固定分母、公式派生、归一化或显示精度解释。
- **常见误报**：细胞计数 / 4 视野平均天然落在 0.25 步长；百分比、ratio、proportion、normalized/log/fold-change、p/q-value、AUC、coverage、model output、Excel 公式、标准化后四舍五入都可能生成固定小数尾。
- **解读时**：必须做 fixed-denominator 思路：对样本值测试 `N=2..500`（至少 2..200），看 `value * N` 或百分比列的 `value / 100 * N` 是否接近整数。若大多能被同一个小分母解释，按良性固定分母/rounding grid 处理。

### `rounded_to_half_or_int`
- **原理**：整列 ≥ 70% 末位是 0 或 5。
- **典型命中**：人工随手凑数。
- **常见误报**：量表测量、Likert scale、按 0.5 刻度记录。

### `missing_last_digits`
- **原理**：≥ 20 个数据中，某些末位数字（如 3, 7）从未出现。
- **典型命中**：编造者倾向于写"漂亮"的尾数（避免 3 / 7）。
- **常见误报**：极少。本检测器只在样本量充足时触发。

### `identical_after_rounding`
- **原理**：≥ 4 个 cell 共享同一 1 位小数舍入值，但精确值 ≥ 3 种不同。
- **典型命中**：先写概数再"反向"补全精度的伪精确数据。
- **常见误报**：测量天然在某区间聚集。

---

## 整 sheet 末位/末两位类 (sheet-level digit detectors)

### `last_digit_chi_square`
- **原理**：整 sheet 数值末位数字（1-9）做 χ² 均匀性检验，flag p < 1e-6。
- **典型命中**：编造者末位偏好特定数字（5、0、2 等）。
- **常见误报**：测量受刻度量化（仪器精度有限），并非造假。
- **解读时**：必须配合 `top` 字段看哪个末位被偏向了 — 给用户具体证据。

### `repeated_two_decimal_endings`
- **原理**：整 sheet 末两位高度集中（top 末两位占比 > 5%）。
- **典型命中**：批量编造数字的指纹。
- **常见误报**：单位换算 / 公式派生导致天然出现 `.00` / `.50`。

---

## 统计自洽性类 (summary-statistics consistency detectors)

针对 **summary 表**（每行一个组的 `均值 ± SD (n)`）而非原始数据列。和其它检测器性质不同：别的是"概率上反常"，这一类是"对整数数据**数学上不可能**" —— 信号更硬，但**前提是该量确实是整数粒度数据**（计数 / Likert / 评分）。

### `grim_inconsistent`
- **原理**：报告均值在该 n 与小数位下，无法由"整数和 ÷ n"得到（GRIM 检验）。
- **典型命中**：summary 表 "n=10 的细胞计数均值 = 3.45" —— 10 个整数的均值只能是 x.x0，给不出 3.45。
- **常见误报**：**只对整数数据有效**。工具已设防——整数关键词必须出现在**均值列名**、对 %/ratio/index/proportion 等连续量直接跳过、`n ≥ 10^小数位`（无区分力）跳过。仍可能漏网：均值列名碰巧含计数词但其实是连续测量。按 `likely_benign` 提示让用户先确认该量是不是整数计数/评分，**别当复用类信号那样硬下结论**。

### `grimmer_inconsistent`
- **原理**：报告 SD 在该均值、n 下，无法由任何整数样本产生（GRIMMER：整数平方和的奇偶性 + 回代检验）。均值先过 GRIM 才查 SD。
- **典型命中**：均值 / n 自洽，但这个 SD 没有任何整数数据集能给出。
- **常见误报**：同 `grim_inconsistent`。额外注意：**只在真正的 SD 列上跑，SEM / 标准误被刻意排除**（GRIMMER 对标准误无定义）—— 如果用户的"SD"列其实是 SEM，这条不会触发，但那**不等于**"没问题"。

---

## 跨表类 (cross-table detectors) — **最高优先级**

检测范围是**全局**的：每个 (文件, sheet) 网格两两比对，所以既能抓同一 xlsx 文件内的两张 sheet，也能抓**两个独立文件**（如两份 CSV）之间的数据复用。finding 里 `same_file` 标记是哪种，`file_a` / `file_b` 给出涉及的文件。kind 名沿用历史的 `cross_sheet_*`。

### `cross_sheet_position_identical`
- **原理**：两张表（同文件 sheet 或跨文件）在 ≥ 15% 同位置上数值 bit-identical（≥3 位小数）。
- **典型命中**：作者复制一整张表然后改了少量值充当"独立"实验；或把同一份数据塞进两个号称独立的 CSV / 数据集。
- **常见误报**：合理的共享对照组（但 source data 应该明确标注）。
- **怎么解读**：这是 paperconan 最强的信号 — 通常意味着两张表之间确实有派生关系。`same_file=false` 的跨文件命中尤其值得追。

### `cross_sheet_value_overlap`
- **原理**：两张表共享 ≥ 40% 的小数值（不要求位置匹配）。
- **典型命中**：池化 + 重新洗牌伪造独立实验。
- **常见误报**：共享样本量集合 / 同一仪器输出范围。

---

## Profile 降级映射 (`false_positive_context` → 检测器)

`--profile review`（默认）和 `triage` 会按列名/finding 形态把疑似误报降级。每条被降级的 finding 带一个 `profile_action`（`demoted`/`hidden`）和一组 `false_positive_context` 标签。下表把标签反查回它针对的检测器和良性理由 —— agent 看到标签时用它解释"为什么被降级"，并判断这个降级是否成立（名字正则会误判）。

| `false_positive_context` | 命中的检测器 kind | 降级理由 | 怎么核 |
|---|---|---|---|
| `axis_or_scan_column` | `arithmetic_progression` | step 是整数，或列名像 day/time/dose/index/2θ 等扫描轴 | 确认这列确实是自变量轴而非测量值 |
| `censoring_or_boundary_value` | `within_col_value_duplication` | 重复值是 0/1/-1/100 等边界（或 p 值列里的 1） | 边界值天然重复（截断/饱和/缺失计数/校正 p），但若重复的是普通测量值则降级不成立 |
| `derived_or_unit_conversion` | `constant_ratio` / `exact_linear` / `sum_constant` | 列名含单位/比例/均值/归一等派生词 | 派生列本就和源列严格相关，合理；但要确认它确实是派生而非两次"独立"测量 |
| `same_data_replot_or_duplicate_upload` | `cross_sheet_position_identical` / `cross_sheet_value_overlap`（仅 `delta.pattern == perfect_dup`） | 同图号，或表名像 source data / 补充表 | 同一份数据多图重绘属预期；**注意只对 `perfect_dup` 生效——`value_tweaked` 不会被降级，那才是改一格指纹** |
| `omics_or_large_matrix_boundary_flood` | `within_col_value_duplication` / `within_col_decimal_repetition` | sheet/列名像 gene/protein/padj/logFC 等大矩阵 | omics 大表里 0/1/padj/logFC 边界值海量重复属常态 |

`prefilter_reason` / `prefilter_flags` 是更早的确定性 triage 信息，尤其常见于 `within_col_*`。它们不是最终结论，但能提示为什么某条看似高 severity 的单列模式可能只是结构性误报：低基数、边界值、整数/类别编码、比例或归一化列、固定分母、模型/统计表、floor/ceiling、默认填充值、或每 sheet 大量同类命中。详细判读流程见 [judgment-rubric.md](judgment-rubric.md)。

`--profile forensic` 下本表全部不生效，所有 finding `profile_action: "kept"`、保留原始 severity。**当默认 profile 把一条你觉得该看的 high 降成了 low，重跑 `--profile forensic` 看原始严重度，再开原表核。**

---

## 在 evidence 里高亮的列怎么对照

每条 finding 的 `evidence.highlight_cols` 是 0-based 绝对列下标（不是 block 内偏移）。配合 `evidence.col_offset` 推断出 evidence 表里的相对位置：

```
local_idx = abs_col - evidence.col_offset
```

HTML 报告已经处理好高亮渲染 — 这段信息是给 agent 想直接引用具体单元格时用的。
