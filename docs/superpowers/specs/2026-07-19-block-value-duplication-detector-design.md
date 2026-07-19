# 设计：block/panel 级「高精度数值重复指纹」detector

- 日期：2026-07-19
- 触发案例：JCI179845（天津医大 刘铭 Trapα），**Fig. 2B**（源数据 sheet `F2`）
- 定位：补上「大量高精度连续值散落在本该独立的格子里精确重复」这类**分布式重复指纹**的检测缺口。与既有的
  `detect_within_column_patterns`（单列内部重复）互补，是纯**加法**改动。

> ⚠️ 中立措辞红线：本 detector 产出的是**统计信号 / 数据不一致 / 待作者澄清**，不是造假结论。证据、图注、
> Methods、作者答复与期刊/机构复核才能定性。文档、代码、变量名、报告文案一律遵守此红线。

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

## 3. block 级 detector：`detect_block_value_duplication`

### 3.1 接口与集成点

签名与摆位对齐既有 `detect_within_column_patterns(sheet, r0, r1, c0, c1, header, min_n=6)`，挂在
`_audit.py` 同一个逐块循环里，输出并入该块的 `within_col`（或新键 `block_dups`，见 §6）。**不改**
`find_numeric_blocks`、不改任何 golden 覆盖的列/行关系逻辑。

```
detect_block_value_duplication(sheet, r0, r1, c0, c1, header, min_hp=12):
    1. 收集 block 内全部有限数值（sheet.block(...) 去 NaN）
    2. 高精度过滤：只保留小数位 >= 2 的值（HIGH_PRECISION_MIN_DECIMALS = 2）
       —— 排除整数（索引/周龄/计数）、x.0、1 位小数（多为 1dp 百分比/剂量梯/归一化到 1.0）
    3. 量化：key = round(v, QUANT_DECIMALS=6)，吸收 float 噪声后按值计数
    4. dup = {key: count | count >= 2}
       n_repeated_values = len(dup)
       excess            = Σ(count - 1)
       dup_fraction      = (Σ count over dup) / len(high_precision_values)
    5. 触发闸门（AND）：
         len(high_precision_values) >= min_hp (=12)     # 小块不判，避免偶然
         n_repeated_values          >= 4                 # 必须是"很多值各重复"，非单值成模
         dup_fraction               >= 0.30
    6. severity：dup_fraction >= 0.60 -> high；>= 0.40 -> medium；否则 low
       （2B Male: frac 0.74 -> high）
    7. 证据：把共享同一重复值的格子成组高亮（highlight_cells 按值分组），
       复现文章那张黄色高亮图；evidence 走既有 _block_evidence，受
       PAPERCONAN_MAX_EVIDENCE_ROWS/_COLS 上限约束。
```

### 3.2 常量（集中定义，便于 profile 调参）

| 常量 | 值 | 依据 |
|---|---|---|
| `HIGH_PRECISION_MIN_DECIMALS` | 2 | 用户选定：放宽召回；FP 由占比闸门兜住 |
| `QUANT_DECIMALS` | 6 | 吸收 xlsx 浮点噪声，同引擎既有量化粒度一致 |
| `MIN_HIGH_PRECISION_CELLS` | 12 | 小块不判 |
| `MIN_REPEATED_VALUES` | 4 | "多个不同值各重复" 才算指纹 |
| `MIN_DUP_FRACTION` | 0.30 | 见 §5 标定：良性 ≤0.15、2B=0.74，干净分开 |

`triage/review/forensic` profile 可分别放宽/收紧 `MIN_DUP_FRACTION` 与 `MIN_REPEATED_VALUES`
（沿用 `_profiles.py` 现有机制）。

---

## 4. panel 级扩展

**panel** = 一个子表（如标签行 `2B` 之下、到下一个标签行 `Age(weeks)/2C/...` 之前），可能被空列（Con|gutter|KO）
或空行切成多个兄弟 block。

- **分段规则**：在一张 sheet 内，以"标签行"（首格是短 alphanumeric 面板号 `2B/3A/S4D`，或组表头行如
  `Age(weeks)`）为边界，把边界之间的所有数值 block 归为同一 panel。实现为一个轻量 `segment_panels(sheet)`，
  只读文本布局，不动 `find_numeric_blocks`。
- **panel 级指纹**：对同一 panel 下所有 block 的数值取并集，跑 §3.1 的同一套指标与闸门。
- **去重**：若某 block 单独已在 block 级触发，panel 级对同一批格子不重复报（按 highlight_cells 交集抑制）；
  panel 级只在"跨兄弟块才显现"的重复上额外加信号（如 age-20 Female 三元组跨 Con/KO）。
- **小 panel 限制（明确写出，不静默）**：`n_repeated_values` 门槛仍是 4；像 age-20 那种 3 值×2 的小复用
  （nrep=3）**不会**由本 detector 单独触发——它更贴近"整组跨空列相同"，由 `detect_relations` 的跨 gutter
  identical-group 扩展覆盖（out of scope，§2）。本 detector 不假装覆盖它，`log`/报告里如实标注。

---

## 5. 标定数据（阈值依据）

`find_numeric_blocks` 逐块，`HIGH_PRECISION_MIN_DECIMALS=2`：

| 面板 | 性质 | n_repeated | excess | dup_fraction | 判定 |
|---|---|---|---|---|---|
| p20 F2 `2B Male` (r15-20) | 记事指认 | 24 | 44 | **0.739** | ✅ 触发 (high) |
| p20 F2 `2A` (r8-13) | 良性体重 | 6 | 6 | 0.152 | ✗ (frac 闸门挡下) |
| p20 SF4 `GTT` 各块 | 良性 | 1–3 | 1–3 | 0.04–0.12 | ✗ |
| p19 Fig1B / Fig1E | 良性 | 0–1 | — | — | ✗ |

关键点：良性面板即使在 2 位小数下也会有几处偶然碰撞，但都是"少数值各撞一次"，`dup_fraction ≤ 0.15`；
复制指纹是"很多值各重复且占比高"。`dup_fraction ≥ 0.30` 是主判别量，`n_repeated_values ≥ 4` 防单值成模。

---

## 6. 输出 schema

沿用 scan.json 现有形态（`relations_blocks[].within_col[]` 或并列的新键）。每条 finding：

```json
{
  "kind": "block_value_duplication",     // panel 级则 "panel_value_duplication"
  "scope": "block" | "panel",
  "rows": "15-19", "cols": "1-20",
  "n_repeated_values": 24,
  "excess_copies": 44,
  "dup_fraction": 0.739,
  "severity": "high",
  "repeated_values_sample": [[0.2077,5],[0.4657,5],[0.2475,5]],  // 上限 <=8 条
  "evidence": { "highlight_cells": [...按值分组...] },
  "summary": "block 内 24 个高精度值各出现 >=2 次（74% 单元格卷入精确重复），远超独立连续测量的偶然水平——数据不一致，请作者澄清原始记录。"
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
