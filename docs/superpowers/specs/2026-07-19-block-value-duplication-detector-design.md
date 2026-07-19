# 设计：block/panel 级「高精度数值重复指纹」detector

- 日期：2026-07-19
- 触发案例：JCI179845（天津医大 刘铭 Trapα），**Fig. 2B**（源数据 sheet `F2`）
- 定位：补上「大量高精度连续值散落在本该独立的格子里精确重复」这类**分布式重复指纹**的检测缺口。与既有的
  `detect_within_column_patterns`（单列内部重复）互补，是纯**加法**改动。

> ⚠️ 中立措辞红线：本 detector 产出的是**统计信号 / 数据不一致 / 待作者澄清 / data inconsistency**，
> 不构成对任何人的指控。原始记录、图注、Methods、作者答复与期刊/机构复核才能定性。文档、代码、变量名、
> 报告文案一律遵守此红线。

---

## 1. 问题：现有引擎为什么漏掉 Fig 2B

`Fig 2B` 是 5 个周龄 × 10 个"独立生物学重复"的体重相关比值。取 Male-Con 那 5×10=50 个值：

| 指标 | 值 |
|---|---|
| 高精度值（小数位 ≥2）单元格 | 50 |
| 不同值 | 只有 23 |
| 出现 ≥2 次的值（`n_repeated_values`） | 15 |
| 涉及单元格 | 42 / 50 |
| 超额拷贝 `excess = Σ(count−1)` | 27 |
| 每行 10 个重复 → 不同值 | 第17行仅 **5**（后 5 列是前 5 列的乱序重排） |

对真正独立的多位小数连续测量，任意两个精确相等的概率≈0；50 个值出现 27 个超额精确重复，几乎不可能是偶然。

**漏检机理（已在 `_audit.py` 逐块 trace 确认）：**

1. **重复是分布式的，不是列内堆叠。** 逐列看（`within_col_value_duplication` 的视角）几乎为空——
   只有个别列碰巧有一个值出现两次。重复沿**行方向（10 个重复列）和跨行**铺开，列指向检测器结构性看不见。
2. **该块本身就是一个完整 block**（`find_numeric_blocks` 给出 `r15-20, c0-21`，跨 Con|KO 两组），
   所以对 2B 而言**不涉及**空列分割问题——唯一缺的就是"没人统计 block 内部跨行跨列的精确重复"。
3. 少数被空列切成两个块的兄弟组（如 2B 的 age-20 Female 三元组 `0.4687/0.3872/0.5195` 在 Con 与 βKO 整组复用）
   需要 **panel 级**（跨兄弟块）才能覆盖——见 §4。

这与既有记忆条目 `detector-gap-repeated-continuous-across-entities`（Laskowski 蜘蛛案）是**同一类缺口**；本
detector 建成通用 block/panel 级检测器后可一并补上该 pubpeer-loop 已知漏检。

---

## 2. 检测目标（scope）

**In scope：** 一个 block 内、以及一个 panel 内（跨被空列/空行切开的兄弟块），**多个不同的高精度值各自精确重复**
构成的"复制指纹"。

**Out of scope（另立 detector，不在本 spec）：**
- 组间**定数偏移**（3A：Con→KO 严格 +5）、**比率**（S4D：B≈1.13·A、论文24：×100）——属"组对关系"，
  应走 `detect_relations` 的跨组扩展，与本 spec 正交。
- 行内**小数尾复用**（论文23 尾数高频）已有 `within_col_decimal_repetition` 覆盖。
- `find_numeric_blocks` 的 `min_rows` 床值调整（会波及全 golden，另议）。

---

## 2.5 设计原则：用显著性过滤，不用硬样本量门槛

（在实现中与用户讨论后确立，同时修正 §3 的初版 `dup_fraction≥0.30` 触发闸门。）

姊妹 detector `detect_dispersed_repeats` 用了一批**硬砍**：`≥30 行`、`distinct≥50`、`≥10 组`。
这些"够不够大"的门槛会**误杀两类真信号**：①样本本来就少的测量；②只复制了其中几个数的情况。它们
也违背本仓库原则（README「误报靠 profile/prefilter 控，不靠削弱 detector」）。

要区分门槛的**性质**：
- **样本量/量级门槛**（行数、distinct、组数）——应改为**随 n 自适应 + 交给 prefilter 降权**，不该不发。
- **统计有效性门槛**（精确相等到底有没有意义）——保留，但做成**连续显著性**而非硬阈值。

本 detector 用一个**泊松显著性**统一处理，天然自适应，无任何硬样本量 floor：

- 有效格点数 `N_eff = (max−min) × 10^median_decimals`（birthday 模型的可分辨值个数）。
- 独立假设下的偶然碰撞对数期望 `λ = C(m, 2) / N_eff`（m = 高精度值单元格数）。
- 观测碰撞对数 `pairs = Σ C(count_v, 2)`（每个重复值贡献 C(k,2) 对）。
- 显著性 `p = P(Poisson(λ) ≥ pairs)`；`p < α` 才触发。

**实测（400–600 次 Monte-Carlo）：** α=1e-4 时随机连续块 FP = **0/600**；同时能抓到
「2B 整块排列复制」(p≈2e-16) 和「200 格大表里只复制 2–3 个高精度值」(p≈1e-12, dup_fraction 仅 0.03–0.05——
旧的 fraction 硬闸门会漏)。`dup_fraction` 因此**降级为 severity 输入**，不再当触发闸门。

> **同技术复用到 `detect_dispersed_repeats`（推荐 fast-follow，待用户确认是否本次一起做）：** 用同一泊松核
> 替换它的 `≥30 行 / distinct≥50 / ≥10 组` 硬砍，把"漏掉小样本/少量复制"的问题一并解决。会触及其现有
> golden，故单列。

---

## 3. block 级 detector：`detect_block_value_duplication`

### 3.1 接口与集成点

签名与摆位对齐既有 `detect_within_column_patterns(sheet, r0, r1, c0, c1, header, min_n=6)`，挂在
`_audit.py` 同一个逐块循环里，输出并入该块的 `within_col`（或新键 `block_dups`，见 §6）。**不改**
`find_numeric_blocks`、不改任何 golden 覆盖的列/行关系逻辑。

```
detect_block_value_duplication(sheet, r0, r1, c0, c1, header, min_hp=12, alpha=1e-4):
    1. 收集 block 内全部有限数值 (r,c,v)
    2. 高精度过滤：只保留小数位 >= 2 的值（HIGH_PRECISION_MIN_DECIMALS = 2）
       —— 排除整数（索引/周龄/计数）、x.0、1 位小数（多为 1dp 百分比/剂量梯/归一化到 1.0）
       m = 高精度单元格数；m < min_hp(=12) -> 不判
    3. birthday 模型 + 有效性闸门：
         med_dp = 高精度值小数位中位数
         N_eff  = (max - min) * 10^med_dp            # 可分辨值个数
         若 N_eff < SUPPORT_K(=20) * m -> 不判        # 均匀 birthday 模型仅在网格够细时可信；
                                                      # 粗粒度/窄域/聚集数据(如 2 位小数肿瘤体积 [0,2])
                                                      # 的自然碰撞超过均匀预期，无法与复制区分 -> 宁可不发
         λ      = C(m, 2) / N_eff                     # 独立假设下偶然碰撞对数期望
    4. 量化 key = round(v, QUANT_DECIMALS=6)，按值聚位置；dup = {key: cells | count>=2}
         pairs  = Σ C(count_v, 2)                     # 观测碰撞对数
         n_repeated_values = len(dup);  excess = Σ(count-1)
         dup_fraction = (Σ count over dup) / m        # 仅用于 severity
    5. 触发（AND）：
         pairs >= MIN_PAIRS(=2)                       # 防单个偶然对（fill-down）
         p = PoissonSF(pairs, λ) < alpha(=1e-4)       # 自适应显著性，无硬样本量 floor
    6. severity（由"复制了多少"决定，都要发）：
         dup_fraction >= 0.50 -> high    (2B 整块排列复制)
         dup_fraction >= 0.20 -> medium
         否则                 -> low     (大表里只复制几个数：真信号但量小)
    7. 证据：把共享同一重复值的格子成组高亮（按值分组），复现文章那张黄色高亮图；
       走既有 _attach_evidence（highlight_rows/cols），受 PAPERCONAN_MAX_EVIDENCE_* 约束。
```

### 3.2 常量（集中定义，便于 profile 调参）

| 常量 | 值 | 依据 |
|---|---|---|
| `HIGH_PRECISION_MIN_DECIMALS` | 2 | 用户选定：放宽召回；有效性由 birthday/泊松显著性兜住，非削弱判据 |
| `QUANT_DECIMALS` | 6 | 吸收 xlsx 浮点噪声，同引擎既有量化粒度一致 |
| `MIN_HIGH_PRECISION_CELLS` | 12 | 唯一的样本量 floor：块内至少要有一点数据可判 |
| `MIN_PAIRS` | 2 | 单个偶然碰撞对（可能是重复测量/fill-down）不单独触发 |
| `SUPPORT_K` | 20 | 有效性闸门 `N_eff >= K·m`：均匀 birthday 模型只在网格够细时可信；粗粒度/聚集数据挡下（与 `detect_dispersed_repeats` 一致）|
| `SIGNIFICANCE_ALPHA` | 1e-4 | 见 §5：随机连续块 Monte-Carlo FP = 0/600 |

`triage/review/forensic` profile 可分别放宽/收紧 `SIGNIFICANCE_ALPHA`（沿用 `_profiles.py` 机制）。
注意：**不再有 `MIN_DUP_FRACTION` / `MIN_REPEATED_VALUES` 触发闸门**——它们会误杀"只复制几个数"的真信号
（见 §2.5）；fraction 只进 severity。

---

## 4. panel 级扩展（block 级落地后独立分阶段；见 §4.1 实测发现）

### 4.1 实测发现：panel 级有真价值，但需额外的共享轴 FP 守卫（block 级完成后处理）

在 10 篇批次上原型验证 panel 级（合并行区间重叠的兄弟块再打指纹）：**多抓到 13 处 block 级漏掉的真信号**，
最强的是 p19 `Fig. 7A and 7C`（跨 5 块、m=1217、p=1e-16，正是文章指认的同队列复制）。**但同时重新暴露了
共享轴 FP**：p19 `Fig. 2B, 2C`/`2E`/`4E` 的 `Fa`（combination-index 的 fraction-affected 剂量轴）在子面板间
复用属良性，panel union 会把它当重复报。

结论：panel 级的 FP 控制**不能只沿用 block 级**——必须先做**跨兄弟块的"同一列=共享轴"抑制**（复用既有
`cross_sheet_findings` 的 axis-like 判定 / `_demote_reused_progressions` 思路）。因此 panel 级独立于 block 级
落地，待共享轴守卫设计确定后再实现。block 级已单独覆盖用户指定的 Fig 2B 目标。

### 4.2 panel 定义与打分（实现时）

**panel** = 一个子表（如标签行 `2B` 之下、到下一个标签行 `Age(weeks)/2C/...` 之前），可能被空列（Con|gutter|KO）
或空行切成多个兄弟 block。

- **分段规则**：在一张 sheet 内，以"标签行"（首格是短 alphanumeric 面板号 `2B/3A/S4D`，或组表头行如
  `Age(weeks)`）为边界，把边界之间的所有数值 block 归为同一 panel。实现为一个轻量 `segment_panels(sheet)`，
  只读文本布局，不动 `find_numeric_blocks`。
- **panel 级指纹**：对同一 panel 下所有 block 的数值取并集，跑 §3.1 的同一套指标与闸门。
- **去重**：若某 block 单独已在 block 级触发，panel 级对同一批格子不重复报（按 highlight_cells 交集抑制）；
  panel 级只在"跨兄弟块才显现"的重复上额外加信号（如 age-20 Female 三元组跨 Con/KO）。
- **小 panel 限制（明确写出，不静默）**：触发用 §9 的泊松显著性 + `n_repeated_values >= 2` 定义性门槛。
  像 age-20 那种"整组三元组跨 Con/KO 相同"更贴近"整组跨空列相同"，也可由 `detect_relations` 的跨 gutter
  identical-group 扩展覆盖（out of scope，§2）；panel 级与之如何分工，待共享轴守卫设计时一并确定。

---

## 5. 标定数据（阈值依据）

**Monte-Carlo（随机连续块，nr∈[4,25]、nc∈[4,16]、精度 2–4 位、值域 1–600）：**
`α=1e-4` 时 FP = **0/600**；`α=1e-3` 时 FP = 0/600。

**召回（两类真信号，泊松显著性判定）：**

| 用例 | pairs | λ | p | dup_fraction | severity |
|---|---|---|---|---|---|
| 2B 整块排列复制（5×10，每行 5 值各 2 次） | 25 | 0.39 | 2e-16 | ~0.9 | high |
| 200 格大表里只复制 3 个高精度值 | 9 | 0.03 | 1e-16 | 0.045 | low |
| 只复制 2 个值 | 6 | 0.03 | 2e-12 | 0.03 | low |
| 2A 粗粒度体重（2dec、窄域） | — | — | — | — | ✗ N_eff 太小 |

关键点：泊松显著性同时拿下"整块复制"和"只复制几个数"（后者 fraction 极低、旧硬闸门必漏），而对粗粒度良性块
（N_eff 小、λ 大）自动不显著。唯一样本量 floor 是 `min_hp=12`；`N_eff >= 20·m` 是**有效性**闸门（非样本量）。

**真实批次验证（10 篇 JCI sdval，`N_eff/m` 判别 + 支持闸门后）：** 本 detector 独立复现文章逐条指认——
p19 `Sup Fig.4B`(high)、p20 `Fig 2B`(high, N_eff/m=45)、p23 `Fig 7D/7G/7H`(high)、p24 `Fig 1M`(medium)、
p27 `Fig 6T`(high)；而对 p28 `Figure 1`（2 位小数肿瘤体积 [0,2]，N_eff/m≈1–4）从 27 个误报降到 0，
p25 从 7 个 high 降到 1。p22 正确地 0（其信号是 sum≈0 / 组间偏移的 relation 类，非重复指纹）。

---

## 6. 输出 schema

沿用 scan.json 现有形态（`relations_blocks[].within_col[]` 或并列的新键）。每条 finding：

```json
{
  "kind": "block_value_duplication",     // panel 级则 "panel_value_duplication"
  "scope": "block" | "panel",
  "n": 92,
  "n_repeated_values": 24,
  "pairs": 44,
  "excess_copies": 44,
  "lambda": 0.39,
  "p_value": 2.2e-16,
  "dup_fraction": 0.739,
  "severity": "high",
  "repeated_values_sample": [[0.2077,5],[0.4657,5],[0.2475,5]],  // 上限 <=8 条
  "example_cells": [[1,3],[1,9]],        // 1-based (row,col), <=24 条
  "severity": "high",
  "rule": "block 内 24 个高精度值各 >=2 次（44 个精确碰撞对，泊松期望 λ=0.39，p=2e-16）——数据不一致，请作者澄清原始记录。"
}
```

---

## 7. 测试计划

- **golden 正例**：`JCI179845 F2` 的 2B Male block（frac 0.74）必须触发 high；panel 级对该 panel 也触发。
  固定 fixture（截取该 block 的最小数值矩阵，不含 DOI/敏感信息）。
- **golden 负例**：2A（frac 0.15）、SF4 GTT 块、Fig1B/1E —— 必须**不**触发。
- **单元测试**：`n_repeated_values` / `excess` / `dup_fraction` 计算；高精度过滤把整数/1dp 排除；
  量化粒度对 `0.4657` vs `0.46570001` 归并正确。
- **确定性**：同输入同输出（golden 依赖）。
- 无需 brute-force oracle（指标是纯计数，非统计推断）。

---

## 8. 明确不做（YAGNI）

- 不做组间 offset/ratio（另立 detector）。
- 不动 `find_numeric_blocks` / `min_rows`。
- 不做跨 sheet 的重复指纹（已有 `cross_sheet_findings` 覆盖跨面板整列复制）。
- panel 分段只读布局、不做语义解析；分段失败时**退化为纯 block 级**（不报错、不静默吞信号）。

---

## 9. Code-review 加固（2026-07-19，high-effort workflow review，9 条 CONFIRMED 全修）

- **路由（关键）**：`block_value_duplication` 曾并入 `within_col` 组 → packet distiller 忽略该 kind、
  within_col-flood 还会降权，signal 到不了 review。改为**独立 group `block_dups`**（加入 BLOCK_FINDING_GROUPS），
  自动走 `_distill_block_findings` 的 HIGH 路径。已验证 2B HIGH 现在能进 review packet。
- **`_poisson_sf` 数值稳定**：旧实现 `for i in range(pairs)` 在 pairs~1e8 时挂起，且 `exp(-lam)` 在 lam>745
  下溢→对大 m 恒返回 1.0（静默不发）。改为小 λ 精确、大 k/大 λ 用连续性校正正态近似（O(1)、深尾正确）。
- **med_dp 用重复值精度**（修 FN）：网格分辨率取**被重复的值**的小数位（而非全块中位数），否则"多数 2 位小数 +
  少数高精度复制"的混合块会被 support 闸门误挡。范围仍用全块。
- **`n_repeated_values >= 2` 定义性门槛**：分布式指纹须 ≥2 个不同值重复；单一主导值（检测限地板/fill-down）是
  `within_col_value_duplication` 的活。非样本量硬砍。
- **dominant/censor 值剥离**：主导值占比 >25% 先剥离（镜像 `detect_dispersed_repeats`），防边界值虚增显著性。
- **块面积上限 `BLOCK_DUP_MAX_CELLS=500k`**：跳过基因组级大块的 O(cells) 物化（镜像 `wide` 跳过）。
- **去重/性能**：抽出共享 `_decimal_places` / `_birthday_grid` / `_poisson_sf`（两个 detector 共用，消除
  copy-paste 漂移）；结构列签名循环每格只读一次 `cell()`；输出字段从 `positions` 派生（去掉与 6dp 网格不一致的
  4dp Counter 二次遍历）。

全套 1161 passed；golden 无变化；批次结论不变（p19 S4B / p20 2B / p23 7D-7H / p24 1M / p27 2G·6T 仍独立复现，
p28 粗粒度仍 0 误报）。
