# 局部段关系检测（local pair runs）设计

**状态**：设计已认可（2026-10-15），待写实施计划。
**一句话**：同一数值块内，两行或两列只在一段连续位置上成恒定比例或恒定差值、段外不成立时，报出一条统计信号。

paperconan 输出的是待解释的数据不一致，不是结论。本检测器报出的每一条，都要结合图注、Methods 和作者说明再判断。

---

## 1. 为什么做

评估集里有一类数据不一致，现有检测器按构造就报不出来：两条测量线（两个时间点、两组动物、两列重复）在一段连续位置上 B = k·A 或 B = A + c，段外不成立。

现有检测器漏掉它的原因各不相同：

- `detect_relations`（列对）：比例和差值只认整列成立；部分段差值要求 n ≥ 24、段长 ≥ max(20, n/2)；比例遇到除数列里一个 0 就整对跳过。
- `detect_row_relations`（同块行对）：只有相同和比例两个臂，没有差值臂；整块要 ≥ `_ROW_REL_MIN_COLS`（12）列。
- `detect_short_row_reuse`（同表短行）：段内每格要 ≥ `_SHORT_ROW_MIN_FRAC_DIGITS` 位小数，第一遍丢掉相邻行对。

**已经排除的路线**：

- **单纯放宽这些闸门**：在本地评估集（不入库）上逐道放宽重扫，只补回少数几张卡，还挤丢了已有的命中。
- **放宽比例容差**（分支 `fix/column-pair-tolerance-v2`）：列对台架上，良性的精确比例家族与真阳性在同一精度档一起翻转。

这两条结论各自的测量方法，见本地评估集 `eval_scope/` 下的 `gate_sweep.py` 与 `compare_runs.py`。容差分不开良性，所以本设计不靠容差，靠**局部性**：

- 曲线族、归一化、单位换算这类良性结构，关系覆盖整条线；
- 复制一段再缩放或平移，关系只在那一段成立，段外立刻散开。

此前一次测量支持这个判据，但测的是另一批数据，所以本项目必须在自己的台架上重新测量（见 §8）。

## 2. 范围

**做**：

- 同一个 `find_numeric_blocks` 块内的**行对**和**列对**；
- 两种关系：**恒定比例** B = k·A，**恒定差值** B = A + c；
- 关系只在一段连续位置上成立，段外不成立。

**不做**（另立项，本设计不为它们预留接口）：

- 整条线都成立的关系（留给现有检测器）；
- 差为整数、差为 10 的倍数；
- 组块对组块（一组多列整体等于另一组乘系数）；
- 行内逐列等差；
- 不在数值块内的两行小组；
- 跨块、跨表。

## 3. 组成

全部放在 `_audit.py`，作为**独立的一遍检测**：不读写任何现有检测器的候选池、频次表、预算或上限。

| 单元 | 作用 | 依赖 |
|---|---|---|
| `_directed_offset_interval(a, b, q_target)` | 由舍入推出常数 c 的可能区间：目标格按步长 q 记录，`c ∈ [b − q/2 − a, b + q/2 − a]`；源格按记录值使用 | 无 |
| `_scan_quantized_offset_runs(source, target, quantums, …)` | 区间逐格求交，求同一个 c 能写出的极大连续段。与现有 `_scan_quantized_ratio_runs` 同构；用 `contains_zero` 代替 `contains_one` | 上一项 |
| 复用 `_scan_quantized_ratio_runs` | 比例臂的段 | 现有 |
| 复用 `_effective_row_quantums` | 每格的记录步长 | 现有 |
| 复用 `_ratio_prediction_bits` | 段的压缩得分：按搜索次数扣减，单格贡献设上限。只看段内目标值与步长，与关系类型无关，两臂共用 | 现有 |
| `_out_of_run_deviation(source, target, run, relation)` | 同一条线上段外有效位置的偏离中位数。比例：`|B/A ÷ k − 1|`；差值：`|B − A − c| / |c|`。段外没有有效位置时返回 `None` | 无 |
| `detect_local_pair_runs(sheet, r0, r1, c0, c1, header, already, coverage)` | 驱动：枚举行对和列对、两个方向、两种关系，套用 §4 的判定，产出 finding | 以上各项 |

**调用点**：`_audit.py` 的块循环里，在 `detect_relations` 与 `detect_row_relations` 之后。`already` 是这两个检测器在本块已经报出的 (方向, 线对) 集合，用于去重。

## 4. 判定

对块内每一对线（行对或列对），以及 A→B、B→A 两个方向，求出比例臂和差值臂的极大段。一段同时满足以下各条，才产出 finding：

1. **显著**：`_ratio_prediction_bits(...)["bits"] ≥ PAPERCONAN_LOCAL_RUN_MIN_BITS`。
   - 默认值等于 `_SHORT_ROW_MIN_PREDICTION_BITS`（20），两处口径一致。
   - `n_tests` = 本块线对数 × 2 个方向 × 2 种关系 × 线长，即本块里搜索的起点总数。
   - 1 位小数、取值分布宽的线，靠格数多过关；高精度的线，短段也能过关。门槛不按小数位一刀切。
2. **局部**：`_out_of_run_deviation(...)` 不是 `None`，且大于 `PAPERCONAN_LOCAL_RUN_OUT_DEV`（默认 0.05）。整条线都成立的关系在这里被排除。
3. **常数不平凡**：
   - 比例：区间不含 1（相同关系由别处负责），也不含 10 的整数次幂（单位改写），沿用 `_scan_quantized_ratio_runs` 返回的两个标志；
   - 差值：区间不含 0。
4. **线本身不平凡**：
   - 段内源值至少有 3 个不同的数；
   - 行标签匹配 `_AXIS_CONTEXT_LABEL_RE` 的行不参与；
   - 整条线本身是等差数列（时间、剂量轴）的不参与。
5. **去重**：
   - 该线对已在 `already` 中，就跳过；
   - 同一线对两个方向、两种关系都过关时，只留得分最高的一条；
   - `detect_short_row_reuse` 在全表阶段运行，可能对同一行对再报一次。本设计不做跨阶段去重，重复的情况在评估时计数报告（§8）。

## 5. 输出

两个新的 finding 类型：

- `local_ratio_run`：B = k·A，只在段内成立；
- `local_offset_run`：B = A + c，只在段内成立。

字段如下：

- **线对**：列对给 `col_a/col_b/col_a_idx/col_b_idx`，行对给 `row_a/row_b/row_a_idx/row_b_idx`。索引沿用现有约定：0-based 的 sheet 绝对位置。
- **关系**：`direction`（`a_to_b` 或 `b_to_a`）；`k`（比例）或 `offset`（差值）；`k_lo/k_hi` 或 `offset_lo/offset_hi`。
- **段**：`run_start`、`run_end`，为线上 1-based 的 sheet 行号或列号；另有 `run_length`、`line_length`。
- **判据**：`out_of_run_deviation`、`prediction_bits`。
- **样本**：`col_a_sample/col_b_sample`（或 `row_*_sample`），沿用 `_sample`。
- **证据**：`evidence` 的 `highlight_rows/highlight_cols` 只标段内的格。匹配器和 HTML 报告据此定位到单元格。
- **其余**：`severity = "high"`；`rule` 用一句话写清关系和段。

严重度先定为 high，首屏代价实测后再定是否调整（§8）。

## 6. 良性控制（复用现有规则，不另起一套）

- **列方向**：接入 `_prefilter._relation_prefilter` 的表头标签分流，与 `constant_ratio/constant_offset` 同路。派生列、轴、计数、公式或单位换算 → 降权或丢弃。
- **行方向**：接入 `_profiles._is_derived_relation`，与 `constant_ratio_row/offset_row_reuse` 同路。同名行之间的关系不因单位词而豁免。
- **说明文字**：`_audit.py` 里按类型给出的良性说明，加一句“请核对图注是否说明这一段为换算、归一或共用数据”。
- **文档**：`skills/paperconan/references/detectors.md` 增加两个条目，`output-schema.md` 同步字段。

## 7. 预算与覆盖

- 每块设运算预算 `PAPERCONAN_LOCAL_RUN_BUDGET`，记法与 `_ROW_REL_BUDGET` 相同。
- 预算用尽就停止本块，在 coverage 里登记 `detect_local_pair_runs` / `detector_compute_budget_limit`，与现有检测器同一做法。
- 是否设行数、列数上限，以实测耗时为准。若设上限，超限的块同样登记，不静默跳过。

## 8. 评估与验收

按 CLAUDE.md 的台架规则：代价按 agent 读到的那一层计；冻结档位，不冻结计数；每条新断言都要确认删掉它守护的代码后会变红。

1. **单元测试**（合成数据，不放任何真实数据）：
   - 区间与段扫描：舍入边界、负源值、零值、方向；
   - 判定各条：去掉任意一条，对应的合成反例必须出现；
   - 去重与输出字段。
2. **台架**：
   - `tests/test_curve_bench_baseline.py`（短行）、`tests/test_column_pair_bench_baseline.py`（列对）的良性层必须不动；有变动就逐条说明，否则不合并；
   - 两个台架各加一个“复制一段后缩放或平移”的真阳性层，冻结它的档位；被检出的比例如实记录，哪怕很低。
3. **本地评估集**（不入库）：
   - 发 v2 命中范围：只在比例、差值两个家族里加入两个新类型，目标卡不动；v1、v2 都报数；
   - **收益**：`compare_runs.py` 对比当前 main 的扫描，按卡看检测层和首屏两层的升降；
   - **代价**：同一脚本统计每篇论文首屏新进和被挤出的 panel，按类型和逐篇分布报；新进和挤出两个方向都要报。
4. **全套测试与 golden** 保持通过；合并前过独立子代理审查。

**合并的门槛**：

- 两个台架的良性层不动；
- 本地评估集首屏上的收益大于挤出；
- 审查没有未处理的阻断项。

三条都满足才合并。

## 9. 风险

- **部分换算的良性列**：同一列里只有一段是计算出来的，比如只对子面板归一。它在形态上就是局部段。列方向靠表头标签分流兜底；行方向的风险要在台架上专门构造一个层来量。
- **记录步长推断**：`_effective_row_quantums` 可能把个别带公式缓存值的格读得过细。得分沿用 `max_quantum_ratio` 上限，用单元测试锁住这个上限对新检测器同样生效。
- **首屏挤出**：新的 high 级 finding 会和现有 panel 争首屏。按 §8 第 3 条量，代价过高就降为 medium，再量一次。
