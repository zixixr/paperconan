# 它能找出什么

paperconan 跑一组数值取证检测器，把"值得人工复核的位置"找出来。下表是概览；每个检测器的原理、典型命中、常见误报见面向 agent 的深入文档 [`references/detectors.md`](../skills/paperconan/references/detectors.md)。

| 检测器 | 寻找的模式 | 典型证据形态 |
|--------|-----------|------------|
| `identical_column` / `constant_offset` / `constant_ratio` / `exact_linear` | 同一 block 内两列存在精确数值关系 | `col B = col A + 2.13` 出现在所有 10 行 |
| `sum_constant` / complementary relations | 两列或两类比例严格相加成常数 | 两个百分比列逐行加和为 100 |
| `small_diff_set` | 两列差值只取少数离散值 | col_b − col_a 只在 2–6 个值里跳 |
| `partial_constant_offset` | 两列在一段**连续行**里严格相差固定值（整列的情况是 `constant_offset`） | 前 40 行 Lactate = Control − 0.3，其后发散 |
| `integer_diff_shared_fraction` | 两列**共享高精度小数尾**、只相差整数（copy-then-shift 指纹） | `178.7615` vs `112.7615`、`169.8687` vs `115.8687` |
| `round_shift_shared_fraction` | 两列逐行**小数尾完全相同、整数部分只差非零的 10 的整数倍**（B5 的低精度版，2 位小数也能抓） | 两组逐行差 60, −10, −20, 20…全是 10 的倍数、`.34/.58/.86` 尾数全同 |
| `constant_ratio_row` / `identical_row` | **同一 block 内两行**（不同实验组）在最长连续列段上成精确比值 / 完全相同——针对"条件在行、测量在列"的布局，列向 `detect_relations` 看不到 | `shUSP15-2+shPARP1-2` 行 = 另一条件行 × 1.14 逐列 |
| `decimal_tail_clustering` | 大量**不同**高精度值里，少数几个 3 位小数尾数高频集中（≠ 单值重复） | 6 个尾数覆盖 80% 的值，常见互补对（尾数相加 = 1000） |
| `arithmetic_progression` | 整列等差 / 等比 | 一列完美 0, 3, 6, 9... |
| `within_col_value_duplication` | 单列里同一个高精度值反复出现 | `0.208975` 在独立样本里出现 8 次 |
| `within_col_dispersed_repeats` | 单列里**多个不同**高精度连续值各自跨散布的行精确重复（与上一条"单值高频"互补） | 46 个不同潜伏期各在多个不同行/区域重复出现 |
| `within_col_decimal_repetition` | 同一列末两位高度重复 | 大量值都以 `.37` 结尾 |
| `rounded_to_half_or_int` | 整列被舍入到固定刻度 | 全部落在整数、0.5 或 0.25 网格 |
| `identical_after_rounding` | 多个 cell 舍到 1 位小数后相同但精确值不同 | 先写概数再反向补精度 |
| `missing_last_digits` | 某些末位数字从不出现 | 编造者偏好"漂亮"尾数 |
| `many_equal_pairs` | 两个本该独立的列里大量 byte-identical | 9/10 一致，只手改一格 |
| `row_pair_digit_coupling` | 两行之间高位改变但小数/个位异常保留 | `197.2 → 167.2`、`165.5 → 155.5` 成串出现 |
| `cross_sheet_position_identical` | 两张 sheet 同位置数值完全一样 | 同一份样本被复制到另一张表 |
| `cross_sheet_value_overlap` | 两张表共享大量小数值（不要求同位置） | 池化后重新洗牌伪造独立实验 |
| `cross_sheet_decimal_tail_reuse` | 跨 sheet 多个值保留长小数尾、只改前导数字 | `14.70300997 → 6.70300997` 成串出现 |
| `cross_sheet_column_duplicate` | 跨 sheet / 跨文件**整列逐值重复**（含 `cross_sheet_*` 位置检测漏掉的整数 / 一位小数列） | 一张图的"No IR"基线列在另一张图里 60 个值全同 |
| `within_table_fraction_reuse` | 同一 sheet 两个矩阵块**逐格共享小数位**、只差整数 | 两个剂量-反应矩阵 48/49 格小数位相同 |
| `recurring_row_vector` | 一个固定高信息行向量在 **≥2 个图**之间反复出现 | `[220,188,122,166,128,166]` 在 Fig 1/Fig 4/ED 2 都出现 |
| `scaled_row_reuse` / `identical_row_reuse` | **跨 block / 跨 sheet** 某条件行是另一行的标量倍（k≠1）或逐值完全相同——`constant_ratio_row` 的跨块版 | DMSO 组某行 = MMS 组同条件 × 1.05 逐列 204 格 |
| `grim_inconsistent` / `grimmer_inconsistent` | 报告的均值 / SD 对整数数据不可能 | 计数均值或 SD 与 n 不自洽 |
| `last_digit_chi_square` | 末位数字偏离均匀分布（χ² 检验） | 整张 sheet 的末位数字集中 |
| `repeated_two_decimal_endings` | 末两位高度集中 | 批量编造数字的尾数指纹 |

每条 finding 都带 `severity`、文件、sheet、block 行列范围、规则字符串和 evidence。**跨表类（`cross_sheet_*`）优先级最高** —— 既能抓同一文件内两张 sheet，也能抓两个独立文件之间的数据复用。

`scan.json` 的完整字段结构见 [`references/output-schema.md`](../skills/paperconan/references/output-schema.md)。
