# 判定报告统一高保真 + 散落精确重复检测器 — 设计文档

日期：2026-07-08
状态：已通过设计评审，待写实现

## 背景与问题

为 paperconan 做介绍视频时，用一个**已公开撤稿**的案例（Laskowski, Montiglio & Pruitt 2016, *American Naturalist*；数据经 `paperconan fetch` 从公开 Zenodo 镜像下载）跑标准流程，暴露了两个真实缺口：

1. **检测器缺口。** 该文核心测量 `boldness` 潜伏期（连续量，精度 0.01 秒）里，312 个不同非封顶数值各自跨多个不同个体精确重复，约 77% 非封顶测量卷入——正是撤稿通知所述"过量的精确重复响应时间"。但标准扫描没有把这条作为 finding 冒出来：现有 [`detect_within_column_patterns`](../../../src/paperconan/_audit.py) 的 `within_col_value_duplication` 只在**单个最高频值占列 ≥ 一半**时触发（`top_count >= max(4, n//2)`），而这里的指纹是**许多不同值各重复几次**，结构上落在覆盖空白里。

2. **渲染器一致性缺口。** README 首图（[`docs/images/adjudication-report.png`](../../images/adjudication-report.png)）展示的高保真报告，其实就是标准 `paperconan report` 的**多发现**渲染路径（论文头 + 发现清单 + 每条 finding 卡片配证据热力表）。但 [`_adjudicated_html.py`](../../../src/paperconan/_adjudicated_html.py) 目前是**分叉**的：多发现 `findings[]` 走 `_render_multi`（高保真），单条 `report_md` 走 `_render_single`（老派两栏，朴素）。[2026-07-02 多发现报告 spec](2026-07-02-multi-finding-adjudicated-report-design.md) 当时刻意"旧单发现格式零改动继续可用"，结果就是**呈现质量由 finding 数量决定**：单条→老派、多条→高保真。README/reports.md 指向的复现文档又只教单条 schema，外部用户照着走只会得到老派版，复现不出首图。

**结论**：呈现质量不应依赖 finding 数量。修法不是"教大家改用多发现模式"，而是让渲染器**永远**输出高保真那套；同时补上检测器空白，让"散落精确重复"成为原生 finding、直接在证据热力表高亮。

## 目标

- **组件一**：新增一个 FP 安全、可复用的 within-column 检测器，捕捉"高精度连续列里大量不同值各自跨表散落精确重复"的指纹，产出可在证据热力表精确高亮的 `example_cells`。
- **组件二**：统一判定报告渲染器——任何 verdict（单条或多条、新旧 schema）都走高保真布局，删除老派分支。现存单条 verdict 重渲后自动变好看，向后兼容。
- 文档收敛到一套统一说明；README 首图无需重生成。

## 非目标（YAGNI）

- 不做 CLI 脚手架 / verdict 生成器 / 校验器（呈现已无"走错模式"的可能，无需引导）。
- 新检测器**不做**环境变量可调阈值——保守默认写死、靠测试锁行为。
- 不做 ID/分组列识别（用列无关的离散度启发替代）。
- 不动 `recheck/` 下 batch2 内部高保真管线。
- 不重生成 README 首图。

---

## 组件一：散落精确重复检测器 `within_col_dispersed_repeats`

### 定位

现有 `within_col_value_duplication` 抓"单个值高频重复"（如二元/编码列 `0.0 × 172`）。新检测器抓互补指纹：**一列高精度连续测量里，许多不同值各自重复少数几次、且这些重复散布在表的不同区域**——对真连续测量而言精确碰撞期望≈0，出现一批即异常。

### 三道门（全过才报，保守默认；最终数值由测试锁定）

**门 1 — 连续/高精度门槛**（排除小整数、比率、编码、分类、低基数列）：
- 非空数值 `n ≥ 30`。
- 非全整数；且相当比例的值带真实小数（如中位小数位数 ≥ 2 / ≥60% 的值有 ≥2 位有效小数）。
- 有效取值支撑大：非边界不同值数 ≥ 50 且 distinct/n 达一定比例——即"理论上几乎不该精确碰撞"。

**门 2 — 超额精确重复**：
- 先**剥离主导边界值**：若单个值占列 > 25%（多为封顶/删失值，如潜伏期 600 秒），从本检测的统计中排除、只看其余。
- 在剩余值上统计"重复组"（出现 ≥2 次的不同值）。真连续模型下期望碰撞≈0，故触发条件为：通过门 3 的**散落重复组数 ≥ K**（初值如 8–10）且被卷入单元格占非边界比例 ≥ 一定阈值（初值如 15–20%），并设小样本绝对下限。

**门 3 — 离散度闸门**（列无关，防技术重复/填充）：
- 逐个重复组检查其多次出现的**行位置分布**：相邻/连续出现（填充、同对象技术重复块）**不计**；只有出现**散布在不同区域**（如行跨度 ≥ 0.5×块高，或落在 ≥3 个不同区段）的重复组，才计入门 2 的计数。
- 门 2 与门 3 互锁：门 2 数的是"通过离散度的重复组"。

### 证据输出

发 `col_idx` + `example_cells`（取散落最狠的前若干个重复值的散落单元格，1-based (row,col)，条数受 `PAPERCONAN_MAX_EVIDENCE_ROWS/_COLS` 约束）。[`_attach_evidence`](../../../src/paperconan/_audit.py) 已能据此在证据热力表精确高亮——铁证由此"原生命中、直接上图"。

### 严重度与接线

- 严重度默认 `medium`（signal-not-verdict，与同族一致），极端情形可升 `high`。
- 实现为 `detect_within_column_patterns` 内新增一支（复用列迭代与 `enrich` 描述符；若函数过大则抽 helper），受现有 `_demote_within_col_flood` 每-sheet 泛滥闸门约束。
- 走现有 prefilter/profile：注册新 `kind`，让 `axis_or_scan_column` / `derived_or_unit_conversion` 等 `false_positive_context` 能对它降级；给 `benign_reason` 出口。
- rule 串走中性语言（"统计信号 / 精确重复"，不含指控词）。

### 测试（TDD，项目铁律）

- **golden 正例（合成）**：构造一张注入"散落高精度重复"的表 → 必须命中，`example_cells` 命中注入位置。合成数据符合"只有 `examples/**` 能进 git"，不提交真论文数据。
- **FP 负例（必须不命中）**：相邻技术重复块；censored 封顶值 `600 × N` 主导；小整数列；比率/低基数列；派生/公式列。
- **蒙特卡洛对拗 oracle**：大量生成真连续列（各种精度/分布/n），验证误报率≈0——正确性硬杠杆。
- 现存检测器 golden 全绿；确定性不变（同输入同输出）。

---

## 组件二：判定报告渲染器统一

### 现状

[`render_adjudicated_report`](../../../src/paperconan/_adjudicated_html.py) 分叉：有 `verdict["findings"]` → `_render_multi`（论文头 + 发现清单 + 每条卡片 + 证据热力表，高保真）；否则 → `_render_single`（两栏 report + 侧栏 kv + 关键证据面板，老派）。

### 归一化（两种 schema → 统一 findings 列表，向后兼容）

新增归一化步骤，把任何 verdict 折成 `(论文级字段, findings 列表)`：
- **多发现形态**：原样。
- **单条 `report_md` 形态** → 合成 1 条 finding：`title`=verdict.title；`report_md`=八段式；`finding_ref`=`finding_refs[0]`（若有）；`suspicion_tier`/`impact_scope`/`review_status` 取 verdict 顶层。
- **顶层判定字段**（`tier_why` / `innocent_explanation` / `needs_author_data`）不丢：在高保真页保留一个紧凑"判定摘要"块承载。

### 统一布局与呈现细节

- 永远走高保真路径：抬头（eyebrow + 标题 + 论文级徽章）→ [发现清单]→ 每条 finding 富卡片（徽章 + 散文 + 证据热力表）→ 方法与背景。
- **发现清单表**：`len(findings) == 1` 时隐藏（避免单行表），≥2 才显示。
- **证据热力表**：finding 有 `finding_ref` → 高亮该 finding；无 → 退回取扫描里**最强的一条** finding 出热力表，保证永远有图。
- **DROP / NEEDS_HUMAN**（常无 report_md/findings）：照样能渲（抬头 + 简短说明 + 最强信号证据），不崩、字段不丢。
- **删除 `_render_single`**，单一渲染路径（归一化 → 富渲染），彻底去掉老派两栏分支。

### 测试

- 单条 `report_md` verdict → 断言产出 `finding-block` + 证据热力表，且**不再**有老派两栏（`panel report` + `panel side` grid）。
- 多发现 verdict → 布局不回归。
- DROP / NEEDS_HUMAN / 无 finding_ref → 优雅渲染、不崩。
- 顶层判定字段在统一布局中仍呈现。

---

## 文档改动

- [`docs/reports.md`](../../reports.md) § 判定后 HTML 报告：改成描述**一套统一流程**（agent 写判断 → `paperconan report` → 永远高保真）；说明 `findings[]` 是主形态、单条只是"列了一条"、旧 `report_md` 兼容且现在也富渲染；点明 README 首图就是此输出、无特殊管线。
- [`skills/paperconan/references/report-templates.md`](../../../skills/paperconan/references/report-templates.md)：主例子改为**主推 `findings[]` 形态**，澄清单/多只是数量、与观感无关；保留 DROP/NEEDS_HUMAN 短形态。
- [`skills/paperconan/references/adjudication-tiers.md`](../../../skills/paperconan/references/adjudication-tiers.md)：多发现 schema 已在此，做轻微交叉引用整理。
- [`docs/detectors.md`](../../detectors.md) + [`skills/paperconan/references/detectors.md`](../../../skills/paperconan/references/detectors.md)：新检测器进反查表（kind、抓什么、常见 FP 与 gating）。

## 数据合规 / 确定性 / 中性语言

- golden/fixture 一律**合成**，不提交真论文数据（`.gitignore` 只放行 `examples/**`）。
- 检测器与渲染器对同一输入产出完全一致（golden 依赖）。
- 新检测器 rule 串、文档、命名一律中性（统计信号 / 数据不一致 / 请作者澄清），不含 "fraud/fabrication/造假/伪造" 等指控词。

## 验收标准

落地后用 `paperconan` 重扫 + 重渲 Am Nat 案例：
1. `within_col_dispersed_repeats` 在 `Pre.boldness` / `Post.boldness` 命中，`example_cells` 指向跨行散落的重复潜伏期（如 `143.37` 的 8 处）。
2. `paperconan report`（哪怕 verdict 只有 1 条 finding）产出高保真报告——论文头 + finding 卡片 + 证据热力表高亮重复值，与 README 首图同款观感。
3. 现存全部 golden/单测绿；确定性不变。

## 范围收口（YAGNI — 明确不做）

CLI 脚手架 / verdict 生成器 / 校验器、新检测器的环境变量可调项、ID/分组列识别、动 batch2 内部管线、重生成 README 首图——本次都不做。
