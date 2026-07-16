# paperconan detectors reference

每个检测器：**原理** · **典型命中** · **常见误报**。Agent 在向用户解读 finding 之前应该 skim 一遍对应条目。

---

## 跨列关系类 (block-level relation detectors)

### `identical_column`
- **原理**：同一 block 内两列每一行数值完全一致（atol=1e-9）。
- **典型命中**：两列被标注为独立指标，但逐行数值完全一致；需澄清是否为共享对照、重复展示或其他关系。
- **常见误报**：先核对两列 header 是否实际指向同一指标，或是否为跨图共享对照；这两种情况可以解释逐行一致。

### `constant_offset`
- **原理**：col_b - col_a 在所有行上为同一非零常数。
- **典型命中**：col_b 与 col_a 呈现固定偏移，但被标注为独立"实验组"，属于需要澄清的数据不一致。
- **常见误报**：先检查 Methods 或 legend 是否说明温度补偿等固定偏置。

### `constant_ratio`
- **原理**：col_b / col_a 在所有行上为同一比例（非 1）。
- **典型命中**：两列逐行保持同一非 1 比例，却被标注为独立"处理组"；需核对单位换算或设计关系。
- **常见误报**：单位换算（mg → ng × 1000）；剂量梯度时间轴。

### `sum_constant`
- **原理**：col_a + col_b 在所有行上为同一常数 K。
- **典型命中**：两个被标注为不同测量的百分比列逐行严格互补（如合计为 100），但标签未说明互补关系。
- **常见误报**：分配比例等设计上互补的指标。

### `exact_linear`
- **原理**：col_b = slope × col_a + intercept，残差 ~0，r > 0.99，且非 identical/offset/ratio。
- **典型命中**：两列呈近乎零残差的精确线性关系，且标签未说明标准曲线或派生关系。
- **常见误报**：标准曲线等按定义呈严格线性关系的量（如吸光度 vs 浓度）。

### `small_diff_set`
- **原理**：col_b - col_a 只取 2-6 个离散值。
- **典型命中**：两列被标注为独立实验，但逐行差值只落在 2-6 个离散值中。
- **常见误报**：定量分级 / 离散刻度测量。

### `partial_constant_offset`
- **原理**：col_b - col_a 在一段**连续行**上为同一非零常数（整列都成立时会先命中 `constant_offset`）。用 scale-relative 容差，段长 ≥ max(20, 半列)，所以任意数量级（含 ~1e-14 的小磁场）都能检出。
- **典型命中**：两列在前一段保持固定非零偏移、后半段发散，却被标注为不同组（如前 40 行 Lactate = Control − 0.3）。
- **常见误报**：**低精度数据上的整数偏移（B = A + 5）是常见良性情形，已排除**（除非两列本身高精度）；分段量表 / 阶梯刻度测量。
- **解读时**：看 `run_length` / `offset` 和 `col_a_sample` / `col_b_sample`，核对这一段是否标注为两组独立测量，并排除派生列或同批换算。

### `integer_diff_shared_fraction`
- **原理**：两列在多数行上**共享完全相同的小数尾**（高精度），但整数部分相差**整数**、且整数差不止一个值。精度要求让它能从 n≥5 触发而不像 `small_diff_set` 那样宽。
- **典型命中**：两列逐行整数差变化而小数尾一致（`178.7615` vs `112.7615`、`169.8687` vs `115.8687`）。
- **常见误报**：单位换算或派生列也可能保留小数尾；结合 ≥3 种不同高精度小数的门槛，仍需人工确认列关系。
- **解读时**：`n_shared_fraction` 报的是**真正共享非零小数**的行数（不含整数对整数的行）；`n_high_precision` 是不同高精度小数的种类数。

### `many_equal_pairs`
- **原理**：两列 ≥ 50% 行 byte-identical，但不是完全相同（仅少量行不同）。
- **典型命中**："10 行中 9 行完全一致，仅 1 行不同" 的数据不一致模式。
- **常见误报**：肿瘤长宽可能因取值范围或测量分辨率而频繁相同；结合 figure legend 核查。

### `row_pair_digit_coupling`
- **原理**：同一 block 内两行按列配对后，大量 cell 对在数值已改变的同时保留第一位小数，且常常连个位+第一位小数也一起保留；差值还频繁落在 10 的粗步长上。
- **典型命中**：两条被标注为不同实验组/处理组的 row，出现类似 `197.2 → 167.2`、`165.5 → 155.5` 这种高位改动但低位数字异常保留的成串关系。
- **常见误报**：低基数整数评分/分类码、axis/time/dose 行、公式派生的网格数据、同一原始 row 的合法重标定。工具已跳过低基数整数样式和 axis-like 行名，但仍要确认这些 row 是独立原始测量而不是同一数据的合法换算或排版辅助行。
- **解读时**：优先看 `row_a`/`row_b`、`same_decimal1`、`same_ones_decimal1`、`coarse_10_diff`、`top_diffs` 和 `examples`。这比整 sheet 末位分布更局部，是需要核查的数据不一致信号；仍需结合原表、Methods 和上下文解释。

---

## 单列模式类 (within-column detectors)

### `arithmetic_progression`
- **原理**：整列等差（diff 恒定，且非 0）。
- **典型命中**：等差整数序列出现在标注为实验测量的列中（1, 2, 3, … 整数）。
- **常见误报**：剂量梯度、时间轴、index 列。Agent 看到这条要先确认列名。

### `within_col_value_duplication`
- **原理**：同列内某个具体数值重复出现 ≥ 一半的行数（且不是全相同）。
- **典型命中**：非圆整连续测量值（如 `0.208975`）在多行独立样本里反复出现，且这些行不是技术重复、共享对照或同一条件重复读数。
- **常见误报**：检出限以下截断（LOD）、饱和上限、背景扣除后的固定值、零/一/100 等边界值、缺失/默认填充值、人工评分等级、四舍五入网格、技术重复、共享 batch control。
- **解读时**：高门槛。只有在重复值是非圆整、非阈值、非填充值的连续测量，且行与行确认为独立样本时，才值得重点报告。否则按 `likely benign` 或 `needs human context` 处理。

### `within_col_dispersed_repeats`
- **原理**：一列高精度连续测量里，**多个不同数值各自跨"散布的行"精确重复**（与 `within_col_value_duplication` 的"单个值高频"互补——后者要求单值占列 ≥ 一半，抓不到"许多不同值各重复几次"这种分布）。剥离主导封顶/删失值后，只统计通过离散度闸门（重复实例散布在表的不同区域、非相邻）且有效取值支撑远大于样本量（碰撞期望≈0）的重复组。
- **典型命中**：连续潜伏期/测量（精度 0.01 等）里，几十个不同精确值各在多行、跨不同区域反复出现，且这些行不是相邻填充或同一对象的技术重复。
- **常见误报**：相邻技术重复 / 填充（离散度闸门排除）、小整数或低基数比率列（连续性/支撑门排除）、封顶/删失值主导（先剥离）、派生/公式列。severity=medium（统计信号，非结论）。
- **解读时**：确认这些重复的行确为独立样本/实体后再重点报告；仍需作者澄清这些相同数值如何各自独立产生。

### `within_col_decimal_repetition`
- **原理**：同列中 ≥ 2/3 数值末两位完全一致（如 `.25` / `.75`）。
- **典型命中**：一列原始独立测量的不同取值大量共享末两位，且不能由固定分母、公式派生、归一化或显示精度解释。
- **常见误报**：细胞计数 / 4 视野平均可落在 0.25 步长；百分比、ratio、proportion、normalized/log/fold-change、p/q-value、AUC、coverage、model output、Excel 公式、标准化后四舍五入都可能生成固定小数尾。
- **解读时**：必须做 fixed-denominator 思路：对样本值测试 `N=2..500`（至少 2..200），看 `value * N` 或百分比列的 `value / 100 * N` 是否接近整数。若大多能被同一个小分母解释，按良性固定分母/rounding grid 处理。

### `rounded_to_half_or_int`
- **原理**：整列 ≥ 70% 末位是 0 或 5。
- **典型命中**：一列大多数值集中在整数、0.5 或 0.25 网格上。
- **常见误报**：量表测量、Likert scale、按 0.5 刻度记录。

### `missing_last_digits`
- **原理**：≥ 20 个数据中，某些末位数字（如 3, 7）从未出现。
- **典型命中**：在样本量达到门槛时，3、7 等末位数字完全缺失。
- **常见误报**：先检查离散刻度、取整规则和样本量是否足以解释末位缺失。

### `identical_after_rounding`
- **原理**：≥ 4 个 cell 共享同一 1 位小数舍入值，但精确值 ≥ 3 种不同。
- **典型命中**：同一 1 位小数舍入值下聚集 ≥3 种不同精确值。
- **常见误报**：集中分布或仪器分辨率可以形成同一舍入区间内的多个精确值。

---

## 整 sheet 末位/末两位类 (sheet-level digit detectors)

### `last_digit_chi_square`
- **原理**：整 sheet 数值末位数字（1-9）做 χ² 均匀性检验，flag p < 1e-6。
- **典型命中**：末位数字显著集中于 5、0、2 等少数组合。
- **常见误报**：仪器精度或记录刻度造成的量化可以解释该分布。
- **解读时**：必须配合 `top` 字段查看哪个末位占比最高，并给用户具体证据。

### `repeated_two_decimal_endings`
- **原理**：整 sheet 末两位高度集中（top 末两位占比 > 5%）。
- **典型命中**：多个值集中在少数末两位组合。
- **常见误报**：单位换算 / 公式派生可以频繁产生 `.00` / `.50`。

---

## 统计自洽性类 (summary-statistics consistency detectors)

针对 **summary 表**（每行一个组的 `均值 ± SD (n)`）而非原始数据列。和其它检测器性质不同：别的是"概率上反常"；这一类在输入确为整数粒度数据（计数 / Likert / 评分）时，报告的是数学不一致。

### `grim_inconsistent`
- **原理**：报告均值在该 n 与小数位下，无法由"整数和 ÷ n"得到（GRIM 检验）。
- **典型命中**：summary 表 "n=10 的细胞计数均值 = 3.45" —— 10 个整数的均值只能是 x.x0，给不出 3.45。
- **常见误报**：**只对整数数据有效**。工具已设防——整数关键词必须出现在**均值列名**、对 %/ratio/index/proportion 等连续量直接跳过、`n ≥ 10^小数位`（无区分力）跳过。仍可能漏网：均值列名碰巧含计数词但其实是连续测量。按 `likely_benign` 提示让用户先确认该量是不是整数计数/评分；确认前按 `needs human context` 处理。

### `grimmer_inconsistent`
- **原理**：报告 SD 在该均值、n 下，无法由任何整数样本产生（GRIMMER：整数平方和的奇偶性 + 回代检验）。均值先过 GRIM 才查 SD。
- **典型命中**：均值 / n 自洽，但这个 SD 没有任何整数数据集能给出。
- **常见误报**：同 `grim_inconsistent`。额外注意：**只在真正的 SD 列上跑，SEM / 标准误不纳入检测**（GRIMMER 对标准误无定义）—— 如果用户的"SD"列其实是 SEM，这条不会触发，也不能据此判断该 SEM 是否自洽。

---

## 跨表类 (cross-table detectors) — **最高优先级**

检测范围是**全局**的：每个 (文件, sheet) 网格两两比对，所以既能抓同一 xlsx 文件内的两张 sheet，也能抓**两个独立文件**（如两份 CSV）之间的数值重复或重合。finding 里 `same_file` 标记是哪种，`file_a` / `file_b` 给出涉及的文件。kind 名沿用历史的 `cross_sheet_*`。

### `cross_sheet_position_identical`
- **原理**：两张表（同文件 sheet 或跨文件）在 ≥ 15% 同位置上数值 bit-identical（≥3 位小数）。
- **典型命中**：两张标注为独立实验的表在大量对应位置数值相同，仅少量位置不同；需澄清两表的样本和处理关系。
- **常见误报**：共享对照组或同一数据的跨图展示；source data 和 legend 应说明这种关系。
- **怎么解读**：这是高优先级信号，因为它直接定位两张表的大量同位置重复值。先核对共享对照、跨图展示和派生关系；`same_file=false` 的跨文件命中优先核查。

### `cross_sheet_value_overlap`
- **原理**：两张表共享 ≥ 40% 的小数值（不要求位置匹配）。
- **典型命中**：两张标注为独立实验的表共享 ≥40% 小数值，但位置不一定相同；需澄清样本、池化或排序关系。
- **常见误报**：共享样本量集合 / 同一仪器输出范围。

### `cross_sheet_decimal_tail_reuse`
- **原理**：两张表在一致的行列偏移下出现一组数值不同、但长小数尾相同的 cell。
- **典型命中**：多个对应值的前导位不同，而 5 位以上小数尾重复；finding 会给出 A/B 坐标、两个值、偏移和小数尾。
- **常见误报**：固定分母比例、常数平移/缩放、按列换算、扫描轴或 log/dilution 阶梯。检测器会降级已识别的这些结构，但仍需结合列标签和 Methods 核对。

### `cross_sheet_column_duplicate`
- **原理**：两个 panel（跨 sheet / 跨文件）某列按顺序共享 **exact loader-preserved numeric identity**（加载器保留的精确数值身份），列长 ≥ 12。补齐 `cross_sheet_position_identical` 因只 grid ≥3 位小数而漏掉的**整数 / 一位小数列**。
- **典型命中**：一张图的"No IR"基线列在另一张标注为独立的图里 60 个值全同。
- **常见误报**：共享的轴 / 索引 / 剂量列（等差**和等比 / 系列稀释**都已排除）；同图号 panel（自动降为 low）；低基数 / 全整数列（要求更长且高基数）。
- **解读时**：rule 描述的是按加载器保留的数值身份逐值、按顺序精确一致；这是待人工复核的数据不一致统计信号。`same_file=false` 的跨文件命中优先核查。

### `within_table_fraction_reuse`
- **原理**：**同一 sheet** 内两个数值矩阵块，逐格对应位置**共享高精度小数位**、整数部分只差整数（≥80% 格 + ≥5 种不同小数）。（这条虽然进 `cross_sheet_findings`，但 `same_file=true`、`figure_a/b` 为 null——两个块在同一 sheet 内。）
- **典型命中**：两个剂量-反应矩阵（如 PDO#4 / PDO#5）48/49 格小数位相同、只整数偏移。
- **常见误报**：`.0` / `.5` / 三分之一网格等低精度共享（已排除）；派生 / 归一化后的矩阵。

### `recurring_row_vector`
- **原理**：一个固定的高信息数值元组（长 4-12）作为**连续行片段**在 **≥3 处、跨 ≥2 个图命名空间**反复出现。**这类信号的良性解释较多，因此护栏较多。**
- **典型命中**：同一个 6 值向量 `[220,188,122,166,128,166]` 出现在 Fig 4b/4c 和 ED 2a；若这些位置标注为不同独立动物组，需作者澄清相同向量如何重复出现。
- **常见误报**：等差 / 等比 / round-number 阶梯（已排除）；**跨图重复展示的标准曲线 / 参考向量**（需用 legend / Methods 核对）；同一图内复发（预期的 replicate 结构，已要求跨 ≥2 图）。
- **解读时**：看 `vector`、`n_occurrences`、`n_figures` 和各出现位置；因固有误报面较大，**优先核对这几张图之间是否有理由共享同一向量**，不得据此直接判断数据来源或意图。

### `within_row_repeated_segment`
- **原理**：同一物理行的数值序列中，一个长 4-8 的高信息片段出现在至少两个互不重叠位置。形成数值序列时会跳过 non-numeric cells，但 `row` / `start_cols` / `end_cols` 始终报告原 sheet 的 1-based 物理坐标。检测器排除常量、等差、等比、整十阶梯和低基数量化池，并保留加载器提供的宽整数精确身份。
- **典型命中**：同一行中两组标注为不同条件的列，分别出现完全相同的 5 值片段。
- **常见误报**：同一参考序列被重复展示、公式区块复用、成对技术重复、固定分母量化网格，或表格布局把同一组值并排展示。
- **解读时**：结合 `vector`、`n_occurrences`、行号、列标题、公式和 Methods 核对两段是否本来就应共享数值。它是待解释的数据不一致统计信号，不单独支持对数据来源或作者意图下结论。

---

## Profile 降级映射 (`false_positive_context` → 检测器)

`--profile review`（默认）和 `triage` 会按列名/finding 形态把疑似误报降级。每条被降级的 finding 带一个 `profile_action`（`demoted`/`hidden`）和一组 `false_positive_context` 标签。下表把标签反查回它针对的检测器和良性理由 —— agent 看到标签时用它解释"为什么被降级"，并判断这个降级是否成立（名字正则会误判）。

| `false_positive_context` | 命中的检测器 kind | 降级理由 | 怎么核 |
|---|---|---|---|
| `axis_or_scan_column` | `arithmetic_progression` | step 是整数，或列名像 day/time/dose/index/2θ 等扫描轴 | 核对这列是否为自变量轴而非测量值 |
| `censoring_or_boundary_value` | `within_col_value_duplication` | 重复值是 0/1/-1/100 等边界（或 p 值列里的 1） | 截断、饱和、缺失计数或校正 p 可造成边界值重复；若重复的是普通测量值则降级不成立 |
| `derived_or_unit_conversion` | `constant_ratio` / `exact_linear` / `sum_constant` | 列名含单位/比例/均值/归一等派生词 | 单位转换或派生定义可以解释严格关系；需核对是否确为派生列而非两次"独立"测量 |
| `same_data_replot_or_duplicate_upload` | `cross_sheet_position_identical` / `cross_sheet_value_overlap`（仅 `delta.pattern == perfect_dup`） | 同图号，或表名像 source data / 补充表 | 同一数据的跨图重绘可解释完全重复；**注意只对 `perfect_dup` 生效——`value_tweaked` 不会被降级，因为它表示高重合但非完全重复** |
| `omics_or_large_matrix_boundary_flood` | `within_col_value_duplication` / `within_col_decimal_repetition` | sheet/列名像 gene/protein/padj/logFC 等大矩阵 | omics 大表里的 0/1/padj/logFC 边界值可以频繁重复 |

`prefilter_reason` / `prefilter_flags` 是更早的确定性 triage 信息，尤其常见于 `within_col_*`。它们不是最终结论，但能提示为什么某条看似高 severity 的单列模式可能只是结构性误报：低基数、边界值、整数/类别编码、比例或归一化列、固定分母、模型/统计表、floor/ceiling、默认填充值、或每 sheet 大量同类命中。详细判读流程见 [judgment-rubric.md](judgment-rubric.md)。

`--profile forensic` 下本表全部不生效，所有 finding `profile_action: "kept"`、保留原始 severity。**当默认 profile 把一条你觉得该看的 high 降成了 low，重跑 `--profile forensic` 看原始严重度，再开原表核。**

---

## 在 evidence 里高亮的列怎么对照

每条 finding 的 `evidence.highlight_cols` 是 0-based 绝对列下标（不是 block 内偏移）。配合 `evidence.col_offset` 推断出 evidence 表里的相对位置：

```
local_idx = abs_col - evidence.col_offset
```

HTML 报告已经处理好高亮渲染 — 这段信息是给 agent 想直接引用具体单元格时用的。
