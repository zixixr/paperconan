# 审计：所有 detector 的硬门槛 —— 样本量 floor vs 有效性门槛

- 日期：2026-07-19
- 缘起：`detect_block_value_duplication`（PR #39）确立的原则——**用显著性过滤，不用硬样本量门槛**。
  已知两个真实漏检因硬门槛而起：S4D（行比率被 `_ROW_REL_MIN_COLS=12` 挡）、3A（局部尾数复用被
  `_TAIL_CLUSTER_MIN_N=100` + 整-sheet 粒度挡）。
- 目的：把每个 detector 的硬门槛盘一遍，分类，定出该改哪些、保留哪些，再复测。

> ⚠️ 中立措辞红线：detector 产出的是**统计信号 / 数据不一致 / 待作者澄清**，不构成对任何人的指控。

---

## 分类框架（关键判据）

一个 floor 该不该改，取决于它守的是"数据够不够"还是"统计有没有意义"，而这又取决于**检验的类型**：

| 类 | 含义 | 处置 |
|---|---|---|
| **A. 重复/衝突/关系类的样本量 floor** | 检验的是**精确巧合**（重复值、相同列、行比率/偏移）。这类信号靠 **birthday 逻辑**——少数几个高精度精确巧合本身就极不可能，**不需要大 N**。此处的 N-floor 是**错的**，会误杀小样本/少量复制的真信号。 | ✅ **审查目标**：改成显著性（泊松 birthday，同 block_value_duplication），或降 floor + 交 prefilter。 |
| **B. 分布/频率检验的检定力 floor** | 检验的是**分布形状**（末位数均匀性、小数末两位频率、位分布 χ²）。这类**天然需要 N** 才有检定力；N 太小时检验本就无意义。 | 🔵 **保留**（可选：换成直接报 p 值的检验，让小 N 自然不显著，去掉突兀的硬 floor）。 |
| **C. 有效性门槛** | "统计到底成不成立"：birthday support（`N_eff≥K·m`）、反量化（full 分数须多样）、定义一条线/比率至少要 ≥3 点、共享分数至少要 ≥K 位小数才有意义。 | 🔵 **保留**。 |
| **D. 计算/内存预算上限** | `MAX_*`、O(n²) 检测器的行/列上限、evidence 截断。与信号无关，纯护栏。 | 🔵 **保留**（但 CLAUDE.md：截断要如实记 coverage，不静默）。 |

**一句话判据**：*这个检验能不能在小样本上给出可信信号？* 精确重复/巧合类 → 能（改）；分布/频率类 → 不能（留）。

---

## 逐 detector 盘点

### 上游（影响所有 detector）

| 位置 | 门槛 | 值 | 类 | 判定 / 已知漏检 |
|---|---|---|---|---|
| `find_numeric_blocks:385` | `min_rows` | 3 | **A** | 丢弃 2 行块 → **3A（INS1/INS2）整块被丢**。改：降到 2 或按信号类型放行。波及全 golden，需谨慎。 |

### 重复 / 衝突 / 关系类（A —— 审查目标）

| detector | 门槛 | 值 | 判定 / 已知漏检 |
|---|---|---|---|
| `detect_row_relations:1236` | `_ROW_REL_MIN_COLS` | **12** | **铁靶子**：S4D 的 5 列固定比率被挡（实测完美 5 列比率返回 `[]`）。改：降门槛 + 支持**部分列**比率 + 用显著性判断“k 一致的列数远超偶然”。 |
| `detect_decimal_tail_clustering:1906/1907` | `_TAIL_CLUSTER_MIN_N` / `SHARE` | **100 / 0.40** | **铁靶子**：3A 40 个值 <100 直接出局；且按整-sheet 跑，局部强尾数复用被稀释到 40% 以下。改：**放到 block/panel 粒度** + 泊松显著性（尾数碰撞 vs 期望）替 100/40% 硬门槛。 |
| `detect_dispersed_repeats:1474/1517/1546` | `min_n` / `distinct<50` / `≥10 组` | **30 / 50 / 10** | 已知：误杀小样本/少量复制。改：迁到泊松 birthday（`_birthday_grid`/`_poisson_sf` 已抽出可复用）。 |
| `detect_within_column_patterns:1349…` | `min_n` / 末两位 `≥8` / 末位 `≥10` / 缺位 `≥20` | 6 / 8 / 10 / 20 | value_duplication 与 decimal_repetition 是**重复类**（A，可显著性化）；末位 0/5 频率与缺位是**频率类**（B）。混在一个函数里，需拆判。3A 每列仅 2 值 → 全数落空。 |
| `detect_equal_pairs:2005` | `n` | **6** | 5 行完全相同的列对不发（重复类，A）。降门槛 + 显著性。 |
| `detect_identical_after_rounding:1753/1764` | `len(cells)` / bucket `≥4` | 20 / 4 | 整块 <20 个数不判（A/边界）。`≥4 且 ≥3 distinct` 是信号定义（留）。 |
| `detect_cross_sheet_column_duplicates:2770` | `min_len` | **12** | 跨 sheet 复制列 <12 行不算（A，同 row_relations 病）。 |
| `detect_row_pair_digit_coupling:991` | `min_n`(cols) | 10 | 行对位耦合需 ≥10 列（A/B 混：位耦合偏频率，但列数是样本量）。 |
| `detect_within_sheet_fraction_reuse:3064` | `min_cells` | 10 | 共享分数复用 <10 格不算（A）。 |
| `detect_short_row_reuse:3431` (`_SHORT_ROW_MIN_COLS=3`,`MIN_SIGFIGS=5`,`MAX_VALUE_FREQ=8`) | 见常量 | 3/5/8 | MIN_SIGFIGS=5 是**有效性**（短低精度行复用无意义，C）；MIN_COLS=3 是样本量（A）。 |
| `detect_within_row_shared_fraction:3606` / `detect_row_pair_shared_fraction:3680` | `_..._FRAC_MIN_DIGITS` / `_ROW_PAIR_MIN_RUN` | 6/4 / 3 | MIN_DIGITS 是**有效性**（分数要够长才算共享，C）；MIN_RUN=3 是样本量（A）。 |
| `detect_scaled_row_reuse:3201` | `min_k`(周期) 等 | — | 待细读；缩放行复用的周期/长度门槛，多为 A。 |

### 分布 / 频率检验（B —— 检定力 floor，保留）

| detector | 门槛 | 值 | 判定 |
|---|---|---|---|
| `detect_last_digit:1882` | `len(digits)` | 40 | χ²(df=8) 末位均匀性——**<40 无检定力**。保留；可选：只报 p 值。 |
| `detect_repeated_decimals:1898` | `len(endings)` | 60 | 末两位频率表——需 N。保留。 |
| `detect_decimal_tail_clustering` 的 `SHARE=0.40` | 集中度 | 0.40 | 集中度阈值（半有效性）；建议随泊松显著性一起重构。 |

### 有效性门槛（C —— 保留）

- `detect_block_value_duplication`：`N_eff≥20·m`（birthday support）、`len(dup)≥2`（分布式定义）、`pairs≥2`。**参考范式**。
- `detect_dispersed_repeats`：同 support 门（已抽成 `_birthday_grid`）；`len(set(full))<max(50,n//2)` 反量化。
- `detect_relations`：确认一条线/和/比至少要 ≥4–5 点（`n<4`、`n>=5`）——定义性有效，保留。
- `detect_grim_grimmer`：GRIM 内在需要 mean/sd/n，属有效性。

### 计算 / 内存预算上限（D —— 保留）

`_MAX_CELLS`(10M)、`_MAX_FILE_MB`(200)、`_MAX_BLOCK_COLS`(120)、`_MAX_REPORT_BLOCKS`(2000)、`_MAX_EVIDENCE_*`、
`_MAX_FINDINGS_*`、`_ROW_REL_MAX_ROWS`(60)、`_ROW_PAIR_MAX_ROWS/COLS`、`_WR_MAX_ROW_CELLS`(20000)、
`BLOCK_DUP_MAX_CELLS`(500k)。—— 与信号无关，保留；只需保证截断如实记 coverage。

---

## 复测计划（改前先立“会漏”的回归证据）

对每个 A 类目标，先写一个**当前会漏、改后应中**的最小固定用例（真实布局，不含 DOI/敏感信息），再动刀：

1. **`_ROW_REL_MIN_COLS=12` → S4D**：5 列 `行B = k·行A`（部分列比率，k 为非 `10^n` 常数）。
   断言当前 `[]`、目标命中。**合成数据**，不使用原始值。
2. **`_TAIL_CLUSTER_MIN_N=100` + 粒度 → 3A**：约 40 个值的局部尾数复用（若干高精度值各重复 3 次）。
   断言当前 sheet 级 `None`、目标 block/panel 级命中。**合成数据**，不使用原始值。
3. **`detect_dispersed_repeats` `min_n=30`**：一个 ~15 行、少量高精度精确重复的列。
4. **`detect_equal_pairs` `n=6`**：5 行完全相同的列对。

改造统一走 `_poisson_sf` + `_birthday_grid` 显著性核（已在库中）。每步保持全 golden 绿 + Monte-Carlo FP 复核。

---

## 优先级建议

1. **S4D**（`detect_row_relations` 列门槛 + 部分列比率）—— 最干净、影响面小、有铁证。
2. **3A 尾数**（`detect_decimal_tail_clustering` 下沉到 block/panel + 显著性）—— 复用刚建的核。
3. **`detect_dispersed_repeats` 显著性化** —— 把它的三个硬 floor 一起换掉（会动其 golden，单列）。
4. 其余 A 类（equal_pairs / cross_sheet_column_duplicates / within_sheet_fraction_reuse）批量跟进。
