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
- **典型命中**：col_b 是 col_a 加了 k 后生成的"实验组"。
- **常见误报**：测量受到固定偏置（如温度补偿）— 但通常文章里会说明。

### `constant_ratio`
- **原理**：col_b / col_a 在所有行上为同一比例（非 1）。
- **典型命中**：col_b 是 col_a 乘了 k 倍后生成的"处理组"。
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

### `partial_constant_offset`
- **原理**：col_b - col_a 在一段**连续行**上为同一非零常数（整列都成立时会先命中 `constant_offset`）。用 scale-relative 容差，段长 ≥ max(20, 半列)，所以任意数量级（含 ~1e-14 的小磁场）都能检出。
- **典型命中**：作者复制一列后在前一段整体加/减固定值充当另一组，后半段才发散（如前 40 行 Lactate = Control − 0.3）。
- **常见误报**：**低精度数据上的整数偏移（B = A + 5）是常见良性情形，已排除**（除非两列本身高精度）；分段量表 / 阶梯刻度测量。
- **解读时**：看 `run_length` / `offset` 和 `col_a_sample` / `col_b_sample`，确认这一段确实是两组独立测量而非派生列或同批的换算。

### `integer_diff_shared_fraction`
- **原理**：两列在多数行上**共享完全相同的小数尾**（高精度），但整数部分相差**整数**、且整数差不止一个值（copy-then-shift 指纹）。精度要求让它能从 n≥5 触发而不像 `small_diff_set` 那样宽。
- **典型命中**：col_b 由 col_a 每行加减一个整数得到，小数位一模一样（`178.7615` vs `112.7615`、`169.8687` vs `115.8687`）。
- **常见误报**：极少——要求高精度小数尾且 ≥3 种不同的高精度小数。单位换算 / 派生列若碰巧保留小数尾要人工确认。
- **解读时**：`n_shared_fraction` 报的是**真正共享非零小数**的行数（不含整数对整数的行）；`n_high_precision` 是不同高精度小数的种类数。

### `round_shift_shared_fraction`
- **原理**：`integer_diff_shared_fraction` 的低精度版。两列逐行**共享相同小数尾**、整数部分差为**非零的 10 的整数倍**时触发。高精度版要求 ≥4 位小数，本条只要求"差全是 10 的倍数"作为额外结构约束，从而在 2–3 位小数的常见台式读数上也能抓。门槛：既是整十差又带真小数尾的行 ≥ max(5, 0.7n)、所有非零差都是 10 的倍数、且共享小数尾 ≥3 种。
- **典型命中**：作者把一组数按整十"微调"成另一组、保留小数（两组逐行差 60, −10, −20, 20…全是 10 的倍数，`.34/.58/.86` 尾数全同）。
- **常见误报**：整数列按整十平移（无真小数尾）已被排除；恒定的整十偏移会先命中 `constant_offset`。
- **解读时**：`n_shared_fraction` 是共享真小数尾的行数；确认两列确是独立测量而非派生。

### `constant_ratio_row` / `identical_row`
- **原理**：**同一 block 内两行**（而非两列）在最长连续列段上成精确比值（`row_b = row_a × k`, k≠1）或逐值完全相同。针对"实验条件在行、逐格测量在列"的布局——这类关系不落在任何列对上，列向 `detect_relations` 完全看不到。比值容差 1e-3（吸收 2–6 位有效数字舍入），段长 ≥12 列，且段内 ≥6 个不同值。
- **典型命中**：两条**不同实验条件**的行逐列恰好差一个固定比例（`shUSP15-2+shPARP1-2` = `shUSP15-2+pPARP1` × 1.14，78 列全中）；或不同标签下一整行数据完全相同。
- **常见误报**：整十/整百等 power-of-ten 比值（单位换算/百分比互换）标 `likely_benign`；命名含单位/归一等派生词的行按 review 降权（`derived_or_unit_conversion`）。
- **解读时**：看 `ratio`/`run_length`/`row_a`/`row_b`；确认两行确是独立条件而非派生/换算。

### `many_equal_pairs`
- **原理**：两列 ≥ 50% 行 byte-identical，但不是完全相同（有少量手改痕迹）。
- **典型命中**："9/10 完全一致只改 1 格" 的 copy-then-edit 数据不一致。
- **常见误报**：肿瘤长宽常常相近但本来就独立测量 — 看 figure legend。

### `row_pair_digit_coupling`
- **原理**：同一 block 内两行按列配对后，大量 cell 对在数值已改变的同时保留第一位小数，且常常连个位+第一位小数也一起保留；差值还频繁落在 10 的粗步长上。
- **典型命中**：两条声称代表不同实验组/处理组的 row，出现类似 `197.2 → 167.2`、`165.5 → 155.5` 这种高位改动但低位数字异常保留的成串关系。
- **常见误报**：低基数整数评分/分类码、axis/time/dose 行、公式派生的网格数据、同一原始 row 的合法重标定。工具已跳过低基数整数样式和 axis-like 行名，但仍要确认这些 row 是独立原始测量而不是同一数据的合法换算或排版辅助行。
- **解读时**：优先看 `row_a`/`row_b`、`same_decimal1`、`same_ones_decimal1`、`coarse_10_diff`、`top_diffs` 和 `examples`。这比整 sheet 末位分布更局部、也更接近人工改数指纹，但仍只能说"值得核查"，不能直接判定意图。

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

### `within_col_dispersed_repeats`
- **原理**：一列高精度连续测量里，**多个不同数值各自跨"散布的行"精确重复**（与 `within_col_value_duplication` 的"单个值高频"互补——后者要求单值占列 ≥ 一半，抓不到"许多不同值各重复几次"这种指纹）。剥离主导封顶/删失值后，只统计通过离散度闸门（重复实例散布在表的不同区域、非相邻）且有效取值支撑远大于样本量（碰撞期望≈0）的重复组。
- **典型命中**：连续潜伏期/测量（精度 0.01 等）里，几十个不同精确值各在多行、跨不同区域反复出现，且这些行不是相邻填充或同一对象的技术重复。
- **常见误报**：相邻技术重复 / 填充（离散度闸门排除）、小整数或低基数比率列（连续性/支撑门排除）、封顶/删失值主导（先剥离）、派生/公式列。severity=medium（统计信号，非结论）。
- **解读时**：确认这些重复的行确为独立样本/实体后再重点报告；仍需作者澄清这些相同数值如何各自独立产生。

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

## 整块模式类 (block-level pattern detectors)

### `block_value_duplication`
- **原理**：把一个 block 内**所有** ≥2 位小数的高精度 cell 汇聚起来（不分行列），统计"多个不同值各自精确重复"的碰撞对数，用**泊松 birthday 显著性**判定（λ = C(m,2)/N_eff，p < 1e-4 才报）而非硬样本量门槛。列级检测器对"重复散布在不同行**和**不同列"的指纹结构性失明（如 5×10 replicate 面板，每行 10 个"独立重复"其实是 5 个值各出现 2 次——没有任何单列在重复）。闸门：≥12 个高精度 cell、≥2 种不同重复值且 ≥2 个碰撞对、N_eff ≥ 20m（粗精度/窄区间/聚簇数据的天然碰撞超出均匀模型，拒判而非误报）；整列结构性拷贝先剔除（归 `identical_column`）、占比 >25% 的主导边界/删失值先剥离（归 `within_col_value_duplication`）、>50 万 cell 的巨块跳过。
- **典型命中**：整个面板打乱重排后复用（dup_fraction 高 → high）；或大表里只粘贴了几个高精度值——dup_fraction 很小但 p 近零，severity=low 仍报出（"只复制几个数"不漏）。
- **常见误报**：2 位小数窄区间数据的自然碰撞（support 闸门排除）、检出限 floor / 填充值（剥离）、全整数块（不参与）。合成连续块 Monte-Carlo FP 0/600。
- **解读时**：看 `n_repeated_values` / `pairs` / `p_value` / `dup_fraction`（≥0.5 high、≥0.2 medium、否则 low）、`repeated_values_sample`（各值及次数）、`example_cells`。finding 在 `relations_blocks[].block_dups` 组里，不受 within_col flood 降级影响；仍需确认这些 cell 确是独立测量而非共享对照/技术重复。

---

## 整 sheet 末位/末两位类 (sheet-level digit detectors)

### `last_digit_chi_square`
- **原理**：整 sheet 数值末位数字（1-9）做 χ² 均匀性检验，flag p < 1e-6。
- **典型命中**：编造者末位偏好特定数字（5、0、2 等）。
- **常见误报**：测量受刻度量化（仪器精度有限），不代表作者意图。
- **解读时**：必须配合 `top` 字段看哪个末位被偏向了 — 给用户具体证据。

### `repeated_two_decimal_endings`
- **原理**：整 sheet 末两位高度集中（top 末两位占比 > 5%）。
- **典型命中**：批量编造数字的指纹。
- **常见误报**：单位换算 / 公式派生导致天然出现 `.00` / `.50`。

### `decimal_tail_clustering`
- **原理**：在大量**不同**高精度值里，少数几个**3 位小数尾数**高频集中——数值取自一小撮固定小数部分（拷贝/派生）而非独立测量的指纹，常见互补对（尾数相加 = 1000）。只取 ≥3 位小数的值、需 ≥12 个、top-6 尾数覆盖 ≥40%（均匀分布下约 0.7%），**且集中程度需在泊松碰撞零假设下不可能**（这才是取代旧 ≥100 硬门槛的判据，小样本时份额是算术必然、显著性才是全部证据），**完整小数部分需大多不同**——否则量化/公分母列（如 k/7、eighths）会平凡地共享尾数造成误报。|v|≥1e7 的值跳过（读精度噪声）。与 `within_col_value_duplication`（整值重复）、`repeated_two_decimal_endings`（2 位、无集中度检验）不同。
- **典型命中**：一张 568 个数的表里,尾数 714 出现 86 次、286 出现 81 次…6 个尾数占 81%（互补对 714+286=1000）。
- **常见误报**：量化 / 公分母数据（少数几个小数）已被"完整小数大多不同"闸门排除；仍要确认这批高精度值确是独立测量。
- **解读时**：看 `top`（各尾数及次数）、`top_share`、`complementary_pairs`、`n_distinct_fraction`，以及 `collision_pairs` / `expected_pairs` / `p_value`——**n 小于约 20 时 `top_share` 是算术必然，只有 p 值有信息量**。
- **仍需人工排除的良性形态**：d 个读数的**均值**，尾数会被钉在 1/d 的余数上。读数记到 ≤3 位小数时检测器会自行剔除；但读数记到 **4 位小数**且样本量小（约 20-60）时**仍可能报出**——这是为保住单尾复用召回（285/300 vs 150/300）而接受的代价。判断方法：若各值乘以某个小整数 d 后都落在很短的小数上，即为均值伪影，应判为良性。

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
- **典型命中**：池化 + 重新洗牌后作为独立实验呈现。
- **常见误报**：共享样本量集合 / 同一仪器输出范围。

### `cross_sheet_decimal_tail_reuse`
- **原理**：两张表（同文件 sheet 或跨文件）在**同一个 (row, col) 平移偏移**下，大量 cell 的**数值已不同**、但跳过第 1 位小数后的 **≥5 位小数尾完全相同**（copy-then-edit 只改高位、留长尾的指纹，如 `0.808902488 → 0.908902488` 保留 `08902488`）。按 offset 分组对齐，所以整块被粘到低几行/几列也能对上；同一 offset 的尾巴匹配数 ≥ max(8, min(20, 小表 3%)) 才报。全表出现 >20 次的同一尾巴（量化伪影）不参与匹配；单一数字重复的填充尾（00000 / 99999 等）排除。
- **典型命中**：B 表复制 A 表后整体上移两行、逐格只改第 1 位小数——精确值重叠为零，但 36 个长尾在同一 offset 全部对齐。
- **常见误报**：两表间恒定加/乘变换、固定分母比率（k/n）、逐列恒定 offset/ratio——这三类在检测器内直接降 low 并写 `tail_benign_reason`（constant_transform / fixed_denominator:1/n / per_column_constant）；axis 数列、单一尾巴主导、log/稀释整数位移只加注记**不降级**（同字段）。同图号 (`same_figure`) 降 low。
- **解读时**：看 `tail_match_count` / `offset_rows` / `offset_cols` / `examples`（value_a、value_b、decimal_tail）和 `tail_benign_reason`。tail_match ≥12 或占小表 ≥10% 为 high，否则 medium。下游 packet 里它保留 `cross_sheet:decimal_tail_reuse` 身份、受 prefilter 保护——共享长小数尾是近零概率的数据不一致，不会被当成普通 partial overlap 淡化。

### `cross_sheet_column_duplicate`
- **原理**：两个 panel（跨 sheet / 跨文件）某列**逐值顺序完全一致**（对齐到 6 位小数），列长 ≥ 12。补齐 `cross_sheet_position_identical` 因只 grid ≥3 位小数而漏掉的**整数 / 一位小数列**。
- **典型命中**：一张图的"No IR"基线列在另一张声称独立的图里 60 个值全同。
- **常见误报**：共享的轴 / 索引 / 剂量列（等差**和等比 / 系列稀释**都已排除）；同图号 panel（自动降为 low）；低基数 / 全整数列（要求更长且高基数）。
- **解读时**：rule 说的是"match to 6 decimal places"（不是逐比特相同）；`same_file=false` 的跨文件命中最值得追。

### `within_table_fraction_reuse`
- **原理**：**同一 sheet** 内两个数值矩阵块，逐格对应位置**共享高精度小数位**、整数部分只差整数（≥80% 格 + ≥5 种不同小数）。（这条虽然进 `cross_sheet_findings`，但 `same_file=true`、`figure_a/b` 为 null——两个块在同一 sheet 内。）
- **典型命中**：两个剂量-反应矩阵（如 PDO#4 / PDO#5）48/49 格小数位相同、只整数偏移。
- **常见误报**：`.0` / `.5` / 三分之一网格等低精度共享（已排除）；派生 / 归一化后的矩阵。

### `within_row_shared_fraction`
- **原理**：**同一行**内 ≥2 个 cell 共享一条长高精度小数尾、整数部分不同（`20.316768` vs `102.316768`）——copy-then-integer-shift 落在单行的列间时，列对（`integer_diff_shared_fraction`）和块对（`within_table_fraction_reuse`）检测器都看不到。尾长门槛 `PAPERCONAN_WITHIN_ROW_FRAC_MIN_DIGITS`（默认 6 位，~1e-6 巧合率）；尾巴按数值量级截断取位（float64 只有 ~15 位有效数字，大整数部分下的低位是表示噪声）、|v| ≥ 1e7 直接跳过；共享小数是 p/q（q ≤ 128）的**小分母值**不算（三复孔均值 .333/.667、k/13、1/128 dyadic 都是除法伪影）。
- **典型命中**：一行 15 个测量里 `20.316768/102.316768`、`162.14990133/163.14990133`、`132.81763667/138.81763667` 三组尾巴各自成对——一段 3 格片段被改整数位后在同行复用。
- **常见误报**：固定分母派生值（已排除）；同整数+同尾=同值重复（归 within_col / identical 类，这里要求整数不同）；剩余面较小，但仍需确认两处 cell 声称是独立测量。
- **解读时**：`n_groups`（= `n` / `same_position_count`）是共享尾**家族**数，不是 cell 数；看 `examples[].tail` / `values` 与 `row`。severity=high，进 `cross_sheet_findings`（`delta.pattern = shared_fraction`、`same_sheet=true`）。

### `shared_fraction_row_pair`
- **原理**：**两行**在 ≥3 个（`PAPERCONAN_ROW_PAIR_MIN_RUN`）对齐列的**连续段**上逐列共享同一小数尾（≥4 位，`PAPERCONAN_ROW_PAIR_FRAC_MIN_DIGITS`）、整数部分不同——`integer_diff_shared_fraction` 的行向孪生（那条只比两列）。段内还要求 ≥3 种**非小分母**的不同尾巴、且整数差 ≥2 种（恒定整数差 B = A + k 留给 `constant_offset` 家族）；所有 maximal run 逐一判定，最长的良性 run 不会掩盖更短的真信号。3 个独立尾巴同时对齐的巧合率约 (1e-4)³。
- **典型命中**：20 nM 与 100 nM 两个浓度行在 3 列上共享 `.27037/.85351/.86076`，整数 95/85、90/88、91/87——一行改整数位后当另一浓度复用。
- **常见误报**：小分母尾（已排除）、恒定整数偏移（已排除）、两行完全相同（归 identical 类——这里要求整数不同）。
- **解读时**：看 `run_length`、`row_a`/`row_b`、`examples`。候选行数有每 sheet 上限（`PAPERCONAN_ROW_PAIR_MAX_ROWS`）、比较预算 `PAPERCONAN_ROW_PAIR_FRAC_BUDGET`——超限记入 `coverage.limitations` 并把 `scan_status` 翻成 partial，不静默截断。severity=high。

### `recurring_row_vector`
- **原理**：一个固定的高信息数值元组（长 4-12）作为**连续行片段**在 **≥3 处、跨 ≥2 个图命名空间**反复出现。**这是最易误报的一类，护栏最严。**
- **典型命中**：同一个 6 值向量 `[220,188,122,166,128,166]` 出现在 Fig 4b/4c 和 ED 2a——6 只独立小鼠不可能在多组给出同一向量。
- **常见误报**：等差 / 等比 / round-number 阶梯（已排除）；**合法复用的标准曲线 / 参考向量跨图展示**（这是本类固有的误报面）；同一图内复发（预期的 replicate 结构，已要求跨 ≥2 图）。
- **解读时**：看 `vector`、`n_occurrences`、`n_figures` 和各出现位置；因固有误报面较大，**优先确认这几张图之间是否有正当理由共享同一向量**，别当复用铁证那样直接下结论。

### `within_row_repeated_segment`
- **原理**：`recurring_row_vector` 的**行内**成员：同一段 4–8 个值的高信息数值片段（round-6 量化）在**一行**的 ≥2 个**不重叠**列位置上完全重复。逐行直接扫描而不经 block 索引，所以 grid 分块从不覆盖的稀疏子面板也看得到。同族闸门：≥3 种不同值、非等差/等比阶梯、全整数段要求更长；另有**量化池闸门**——段内某值在整行的出现次数超过拷贝数 2 倍时不报（k/19 网格、剂量平台的重复值不是复制指纹）。扫描量受 `PAPERCONAN_WITHIN_ROW_VEC_BUDGET` / `PAPERCONAN_WR_MAX_ROW_CELLS` 约束，超限记入 `coverage.limitations`。
- **典型命中**：一行里同一个 5 值 tuple（`3.238866, 1.724138, 3.418803, …`）同时出现在两个声称独立 cohort 的列段下——两组独立个体不可能给出同一高精度片段。
- **常见误报**：量化网格 / 平台期值（闸门排除）、低信息整数段（排除）；同一组数据在同行的合法重复排版需人工确认。
- **解读时**：看 `vector`、`row`、`occurrences[]`（每次出现的 Excel 列区间 `range`，含非连续列）、`n_occurrences`。severity=high，进 `cross_sheet_findings`（`delta.pattern = within_row_repeat`、`same_sheet=true`）。

### `scaled_row_reuse` / `identical_row_reuse`
- **原理**：`constant_ratio_row` / `identical_row` 的**跨块 / 跨 sheet** 版。把每张表按连续数据行切成 band（cohort 块），比较**不同 band 或不同 sheet** 的行对（只比不同 band，避免与同块的 `constant_ratio_row` 重复），找 `row_b = row_a × k`（k≠1，`scaled`）或逐值完全相同（k=1，`identical`）的最长连续列段。带候选/预算上限，超限走 stderr、不静默截断。
- **典型命中**：同一条件在两种处理下应独立，却是标量倍关系（DMSO 组 `shUSP15-2+pPARP1` = MMS 组同条件 × 1.05，逐列 204 格全中）；某队列的数据组原样出现在另一队列/图。
- **常见误报**：power-of-ten 比值标 benign；**同图号 + 不同 sheet** 且满足以下之一的 `identical_row_reuse` 视为跨面板共享对照（benign）——**所有**行对同名，或**所有**行都无名（位置标签）且匹配行数 ≥8。判据取自整个折叠矩形，不是某一对代表行。同 sheet 跨 block（如 DMSO↔MMS）和混有具名不同名行保持 HIGH。**注意一个例外**：同名行那条分支在代码里排在"仅限 k=1"的判断之前，所以两行**同名**且跨面板时，即使是任意常数比的 `scaled_row_reuse` 也会拿到共享对照注记——而跨面板的任意常数比正是本检测器最强的信号之一。看到 `scaled_row_reuse` 带 benign 注记时，请核对两行是否真的同名同物，注记不构成排除理由。
- **一条 finding 可能代表整个矩形**：两块共享前若干列时会逐行匹配，这些行**折叠成一条**。读 `distinct_rows_matched`（匹配的行数）、`rows_matched`（行对数，一行被复制多次时会大于前者）、`matched_row_pairs`（前几对行标签的样例）。`row_a`/`row_b` 只是**建起该矩形的第一对**，不代表整体——判断整体请看 `all_rows_unnamed` / `all_rows_same_named` / `row_labels`（`row_labels_complete=false` 表示标签未全部保留，此时不应据其下结论）。
- **解读时**：看 `ratio`/`run_length`/`same_sheet`/`same_figure`/`block_a`/`block_b` 与上面的折叠字段；`same_sheet=true` 的跨块关系（两处理臂之间）最值得看。
- **短段（3–11 列）由 `detect_short_row_reuse` 负责，它在检测器内部有多处静默抑制**——生效时报告里不留任何痕迹，所以「没报出来」不等于阴性。已知的有：同一连续数据 band 内的行对（视为拟合曲线的台阶）、power-of-ten 比值（单位换算）、在整张 sheet 里出现过多次的值（量化网格 / 平台期）、本身是等差/等比数列的行、看起来像坐标轴标签的行名，以及紧邻行对里覆盖整行（而非子区间）的比值。这些都不受 `--profile` 控制。**手上若正是一张归一化表或稀释系列而你确实怀疑某两行被复制过，请直接开原表按行比对。**
- **精度前提与已知盲区**：进入短段匹配的格子需记录到 ≥3 位小数（挡掉整数与粗网格数据的偶然巧合）。例外是「低精度除数」那条通路——它只要求**被除数**高精度，除数是非整数即可，所以 2 位小数的除数行仍可能命中。反过来，**两行都只有 2 位小数的面板不在覆盖范围内**——不只是因为闸门挡住，更因为按面板精度重新取整后，小数值的比值抖动本就会超出判定容差。这是盲区，不是阴性结论。另外注意一条容易误读的规则：一对**紧邻**行只有在「一侧精确到能钉住比值、另一侧钉不住」时才会被细判（子区间 vs 整行）；两行**同样精确**的紧邻行对一律不报（视为拟合曲线的台阶），两行**都不够精确**的也不报（没有任何一格能钉住那个比值）。

---

## Profile 降级映射 (`false_positive_context` → 检测器)

`--profile review`（默认）和 `triage` 会按列名/finding 形态把疑似误报降级。每条被降级的 finding 带一个 `profile_action`（`demoted`/`hidden`）和一组 `false_positive_context` 标签。下表把标签反查回它针对的检测器和良性理由 —— agent 看到标签时用它解释"为什么被降级"，并判断这个降级是否成立（名字正则会误判）。

| `false_positive_context` | 命中的检测器 kind | 降级理由 | 怎么核 |
|---|---|---|---|
| `axis_or_scan_column` | `arithmetic_progression` | step 是整数，或列名像 day/time/dose/index/2θ 等扫描轴 | 确认这列确实是自变量轴而非测量值 |
| `censoring_or_boundary_value` | `within_col_value_duplication` | 重复值是 0/1/-1/100 等边界（或 p 值列里的 1） | 边界值天然重复（截断/饱和/缺失计数/校正 p），但若重复的是普通测量值则降级不成立 |
| `derived_or_unit_conversion` | `constant_ratio` / `exact_linear` / `sum_constant` / `constant_ratio_row` / `scaled_row_reuse` | 列名（或行名）含单位/比例/均值/归一等派生词 | 派生列本就和源列严格相关，合理；但要确认它确实是派生而非两次"独立"测量 |
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
