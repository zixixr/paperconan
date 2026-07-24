# 设计：Agent 默认的自适应短样本信号工作流

- 日期：2026-07-25
- 状态：待用户书面复核
- 范围：PaperConan 数值检测、Agent 编排、候选展开、报告链

> PaperConan 输出的是统计信号、数据不一致和待解释异常，不是对作者意图的判断。
> 最终解释仍需原始记录、图例、Methods、作者说明以及期刊或机构复核。

## 1. 背景

PaperConan 当前主要采用一次性流程：

```text
确定性扫描 → profile 过滤 → review packet → Agent 判定
```

为了控制假阳性数量和 Agent 上下文压力，若干 detector 使用较严格的最小长度、
完整覆盖或高精度门槛。这会产生两类覆盖缺口：

1. 只有 3–4 个生物学重复的短样本关系在进入 Agent 之前被跳过；
2. 单个 finding 只达到 medium/low，但同一面板内多个弱信号组合后已值得深查。

已确认需要通用覆盖的数值结构包括：

- 3–4 个配对值保留相同高精度小数尾；
- 5/6 等局部精确比例，允许一个缺失、例外或量级异常；
- 长度 3–8 的完整短向量重复；
- 多个独立实体行中反复出现非基线高精度组内碰撞；
- 上述模式的行列转置、跨 block 和跨 sheet 版本。

同时必须避免把累计堆叠边界、固定分母计数频率、归一化锚点、检测限、
低基数量化网格和有证明的技术重复直接升级为高优先级。

## 2. 设计目标

1. PaperConan 与 Agent 搭配时，默认使用自适应级联流程。
2. 裸 CLI 保留一次性、无模型的确定性扫描。
3. Agent 不在 `review`、`triage`、`forensic`、`adaptive` 之间自由选模式。
4. 第一阶段扩大召回，但只向 Agent 提供压缩候选簇。
5. Agent 只决定是否分配深查预算，不决定数学计算结果。
6. 深查由注册的确定性 recipe 执行，可缓存、可重放、有预算上限。
7. 三个通用数值原语同时支持 seed 和 confirm，避免两套实现漂移。
8. 最终只向用户交付 Agent 判定后的统一报告。
9. 所有真实论文数据、DOI 和判定继续保持本地且不进入 git。

## 3. 非目标

- PaperConan 不管理模型密钥、模型 SDK 或模型提供商。
- 不让 Agent 动态编写 detector、修改阈值或执行任意代码。
- 不尝试仅凭数值模式判断作者意图。
- 不把只有两个支持点的关系自动升级为 high。
- 不在本次设计中解决只有图片而没有可读源数据的图表数字化。
- 不将真实论文工作簿或具体数值写入测试 fixture。

## 4. 两种产品入口

### 4.1 裸 CLI

```bash
paperconan data/
```

行为保持一次性：

- 不调用模型；
- `review`、`triage`、`forensic` 仍是人类可选的显示 profile；
- 产出 `scan.json` 和确定性证据浏览器；
- 报告明确提示仍需人工或 Agent 复核。

### 4.2 Agent 工作流

用户通过 PaperConan skill 请求论文检查时，默认进入固定的自适应流程。
Agent 不向用户询问 profile，也不得将裸 CLI 的原始报告直接作为最终结论。

PaperConan 提供确定性工作流命令，skill 负责模型调用和编排：

```bash
paperconan workflow start data/ --out audit/agent/
paperconan workflow expand audit/agent/ --request expansion_request.json
paperconan workflow status audit/agent/
paperconan report audit/agent/scan.json --verdict verdict.json --out adjudicated.html
```

`workflow` 子命令不调用模型。

## 5. 固定状态机

Agent 工作流只有一条状态机：

```text
DISCOVER
   ↓
ROUTE
   ↓
EXPAND（可选，最多两轮）
   ↓
ADJUDICATE
   ↓
COMPLETE
```

### 5.1 状态职责

| 状态 | PaperConan | Agent |
|---|---|---|
| `DISCOVER` | 宽召回、候选聚类、压缩 packet | 不作最终判断 |
| `ROUTE` | 校验请求 schema、recipe 和预算 | 选择展开、结构解释、补充上下文或暂缓 |
| `EXPAND` | 执行注册的确定性深查 recipe | 阅读结果，必要时申请第二轮 |
| `ADJUDICATE` | 提供原始信号、展开结果、阴性检查和 coverage | 形成谨慎判定 |
| `COMPLETE` | 验证 verdict、生成统一报告 | 向用户交付结果 |

### 5.2 Agent 不选择 profile

- `DISCOVER` 固定保留 raw signals；
- `EXPAND` 固定运行候选对应的 confirm 逻辑；
- `ADJUDICATE` 固定应用 review 上下文；
- `review`、`triage`、`forensic` 只属于裸 CLI 和报告展示。

每次工作流操作写出：

```json
{
  "workflow_stage": "ROUTE",
  "next_action": "write_expansion_request",
  "allowed_decisions": ["expand", "explained", "needs_context", "defer"],
  "allowed_recipes": ["partial_pair_relation", "shared_fraction_check"],
  "expansion_round": 0,
  "max_expansion_rounds": 2,
  "budget_remaining": {
    "clusters": 8,
    "evidence_cells": 2000
  },
  "allowed_context_requests": ["load_figure_context", "render_image_context"]
}
```

CLI 拒绝非法状态转换、未注册 recipe、超预算请求和第三轮展开。

## 6. 自适应数据流

```text
宽召回 seed
  → 物理证据去重
  → 候选簇聚合
  → 紧凑 candidate packet
  → Agent 路由
  → 定向 confirm recipe / 上下文请求
  → 确定性 expanded findings
  → Agent 最终判定
  → adjudicated report
```

### 6.1 Seed 层

Seed 层门槛较宽，只产生紧凑候选：

- 不嵌入完整证据表；
- 不把 seed strength 当成最终 severity；
- 保存支持点、覆盖率、参数、残差、物理位置和可疑上下文；
- medium/low 可以与其他独立 seed 聚合后触发展开。

示例结构：

```json
{
  "seed_id": "sheet:synthetic:ratio:1",
  "kind": "partial_ratio_seed",
  "sheet": "Synthetic panel",
  "support": 5,
  "total": 6,
  "ratio": 0.73523447,
  "max_residual": 2e-8,
  "candidate_strength": 0.94,
  "cells": ["F3:F8", "H3:H8"]
}
```

`candidate_strength` 只是确定性的路由排序分数，不是概率、p 值或最终 severity。

### 6.2 候选聚合

送入 Agent 前必须：

- 按论文、figure、sheet、panel 和物理单元格聚类；
- 合并重叠 numeric block；
- 合并同一数值结构的多个 detector；
- 标记累计、固定分母、基线、边界和量化上下文；
- 采用 finding family 多样性配额，防止一种噪声占满 top-K；
- 保存未进入 packet 的候选数量和原因。

### 6.3 Agent 路由

`expansion_request.json` 只允许：

```json
{
  "cluster_id": "sheet:synthetic:ratio:1",
  "decision": "expand",
  "recipes": [
    "partial_pair_relation",
    "transpose_check"
  ],
  "context_requests": ["load_figure_context"],
  "reason": "5/6 aligned values support one precise arbitrary ratio"
}
```

允许的 decision：

- `expand`
- `explained`
- `needs_context`
- `defer`

首批注册 recipe：

- `partial_pair_relation`
- `shared_fraction_check`
- `repeated_vector_check`
- `group_collision_aggregation`
- `transpose_check`
- `merge_sibling_blocks`
- `cumulative_boundary_check`
- `fixed_denominator_check`
- `formula_provenance_check`

上下文请求与数值 recipe 分开注册：

- `load_figure_context`
- `render_image_context`

上下文请求只负责确定性地定位、加载或渲染图例、Methods 和图片区域；
图片与文字的解释仍由 Agent 完成，不写入 detector 的数学结论。

Agent 不能传入自定义代码、阈值或未注册的上下文动作。

## 7. 三个通用数值原语

三个原语采用共享底层函数：

```text
iter_numeric_vectors()
match_pair_relation()
vector_information()
collision_stats()
finding_footprint()
cumulative_boundary_context()
```

扫描必须对行向和列向对称，并保存绝对 Excel 坐标。

### 7.1 `short_pair_relation`

适用长度为 3–8 的对齐行对或列对，支持：

- exact identity；
- constant ratio；
- constant offset；
- shared high-precision fractional tail；
- `k/n` 局部支持；
- 一个缺失、例外或量级异常。

输出至少包含：

```json
{
  "relation_type": "ratio",
  "axis": "column",
  "n_total": 6,
  "n_match": 5,
  "coverage": 0.8333333333,
  "matched_indices": [0, 1, 2, 4, 5],
  "outlier_indices": [3],
  "parameter": 0.73523447,
  "max_residual": 2e-8
}
```

#### High 规则

- 完整 3 点关系：3/3 严格成立且信息量足够；
- 局部关系：`n_total >= 4`、`n_match >= 3`、覆盖率至少 75%，最多一个例外；
- 局部精确比例：至少 4 个支持点、至少 3 个不同基准值、比例不是 `10^k`；
- shared tail：至少 3 个不同尾数，每个尾数至少 4 位；允许一对完整值相同，
  但必须至少有两个非零整数差且整数差不全相同；
- 只剩 2 个支持点不得 high。

量级异常只记录为 `possible_scale_entry_inconsistency`。工具不得自动改写原值；
可以报告某个 `10^k` 缩放候选是否会恢复关系。

### 7.2 `repeated_short_vector`

适用长度为 3–8、顺序敏感的行向量或列向量：

- 同一等价类一次输出整个 `occurrences[]`；
- 支持同块、跨块、跨 sheet、文本间隔和转置；
- exact 关系由本原语所有，不再同时输出 identity ratio；
- 单侧缺失或单侧异常不算 exact，转给 `short_pair_relation`。

以下任一条件为 high：

- 一个长度至少 4 的高信息向量完整重复；
- 同一 panel 至少出现两种不同的重复三元组；
- 一个高信息三元组跨明确不同的 block 或 figure 重复。

归一化锚点只从证据中剔除。若剔除锚点后仍有多个非基线匹配，
不得因为表中存在 `1` 而整体降级。

### 7.3 `recurrent_group_collision`

本原语消费前两个原语和 `block_value_duplication` 的原始碰撞事件，不重新扫描表格。

High 规则：

- 至少 3 个不同的非边界高精度值发生精确碰撞；
- 分布于至少 3 个独立实体行；
- 碰撞发生在同一 replicate group 内；
- birthday/Poisson `p < 1e-4`；
- 支持 n=3–8、混合组宽、缺失值和变化的重复位置。

以一个 panel/sheet 聚合 finding 输出，不为每个实体行单独生成 high。

## 8. 上下文守卫

守卫不得删除 raw finding。统一返回：

```json
{
  "context": "cumulative_or_ranked_boundaries",
  "strength": "strong",
  "explained_cells": ["C3:C5", "D3:D5"],
  "metrics": {}
}
```

Review 根据尚未被解释的证据重新分级；forensic 保留 raw severity。

### 8.1 累计或排序边界

仅自动降级 equality、repeated vector 和 collision，不自动压低 ratio 或 shared tail。

强命中条件：

- 对行列两个方向都评估；
- 候选子区间至少 8 个边界、至少 2 条 trace、合计至少 16 个相邻差；
- 每条 trace 至少 95%、整体至少 98% 的相邻差同方向；
- 重复短向量在该轴连续相邻，且等值对应零差平台；
- 至少两个分离平台，或一个长度至少 3 的平台；
- 参与轴不能明确标为 biological replicate、mouse、patient、sample 或 well。

守卫寻找覆盖至少 85% 候选宽度的共同最长单调子区间，也可用上层非空表头分段。
不能要求整个 block 完全单调，因为末尾可能存在独立 summary 指标。

只有单调或只有 `frequency` 标签不能降级。显式
`cumulative`、`stacked`、`cumsum`、`upper boundary`、`running total`
可以把平台数量要求放宽为一个。

命中后：

- `forensic`：保留 raw high；
- `review`：low + `profile_action=demoted`；
- `triage`：hidden。

### 8.2 固定分母或计数网格

- 不得只用候选的 3–4 点拟合分母；
- 必须使用同一 trace 至少 8 个不同上下文值；
- 至少 90% 值在显示半个 ULP 内落在共同零锚定格点；
- 格点至少比显示量化步长粗 4 倍，排除平凡的 `10^d` 解；
- 至少 6 个不同整数 index；
- 奇偶位置交叉验证均成立；
- frequency、percent、count、read、UMI、clonotype 语义或伴随 n 列可增强置信。

该守卫只解释单点碰撞。整段向量重复必须在量化 null 下重算，
不能仅因固定分母而整体降级。

### 8.3 归一化锚点

- 同一位置在至少 3 组、至少 80% 组中精确为 1；或
- 同时具有 control/reference/baseline 与 normalized/relative/fold 语义。

只剔除锚点 cell。其他非基线高精度匹配继续参与分级。

### 8.4 检测限和边界

强解释需要：

- LOD、LLOQ、ULOQ、ND、BDL、floor、ceiling、saturation 等明确语义，
  且值位于局部最小或最大；或
- 同一边界跨至少 3 组并占 block 至少 25%。

`0`、`1`、`-1`、`100` 本身只算中等上下文，不能整条降级。

### 8.5 技术重复

只有同时存在明确 technical/re-read/duplicate-injection 语义和共同上游观测证明时，
才能作为强解释。普通 `replicate` 或相邻列不够。

`biological replicate`、mouse、patient、sample、`n=` 是自动降级阻断项。

### 8.6 低基数或量化网格

- 至少 95% 值落在规则格点；
- 潜在格点数 `G <= max(8, 2m)`，或整数/ordinal 且 distinct 不超过 8；
- 在相应量化 birthday null 下碰撞并不罕见时降为 low；
- 若碰撞 `p < 1e-6`，不得仅靠低基数标签降级。

### 8.7 公式蕴含关系

只有公式逻辑本身必然产生关系时才降级，例如：

- 两格引用同一源格；
- 对应公式是同一平移模板；
- 显式 `source*k`、`source+c`、`x/x`；
- 累计公式 `left + increment`。

仅仅“这些格子含公式”或标签含 normalized、mean、densitometry 不够。
实现该守卫需要扩展现有 OOXML 公式检查，保存公式坐标和规范化模板。

## 9. 去重和 finding 所有权

每条 finding 规范化为：

```text
(file, sheet, physical cells, relation family)
```

同一物理证据重叠至少 80% 时，只保留解释范围最大的主 finding：

```text
recurrent_group_collision
  > repeated_short_vector
  > short_pair_relation
  > block_value_duplication
```

较弱 finding 写入：

```json
{
  "supporting_kinds": ["block_value_duplication"],
  "source_finding_refs": ["..."]
}
```

完全相同向量由 `repeated_short_vector` 所有。全比例与局部比例同时出现时，
保留覆盖范围较大的一个。

## 10. 工作流产物

Agent 工作目录包含：

- `scan.json`：确定性原始扫描和 raw seeds；
- `workflow_state.json`：状态、下一动作、预算和 coverage；
- `candidate_packet.json`：压缩候选簇；
- `expansion_request.json`：Agent 路由决定；
- `expanded_findings.json`：确定性深查结果；
- `verdict.json`：Agent 判定；
- `adjudicated.html`：唯一用户交付报告。

原始 `report.html` 仍是中间证据浏览器，不是最终结论。

如果 Agent 或工作流中途失败，状态必须为 `adaptive_review_incomplete`，
并记录停止阶段、未完成候选和 coverage limitation。不得静默退回裸 CLI 后宣称完成。

## 11. 报告链一致性

当前 `BLOCK_FINDING_GROUPS` 已包含 `block_dups`，packet 可以处理，
但 HTML 的 per-block group 列表没有使用这份 canonical 定义。

实现必须：

- 让 HTML 和 packet 从同一个 canonical finding group 集合派生；
- 保留新 finding 的 support、outlier、坐标和上下文字段；
- 确保被累计守卫降级的 finding 不进入 high-only review packet；
- 确保 scan.json 中的 high finding 不会因消费者漏 group 而不可见。

## 12. 预算、确定性和安全

- 默认最多两轮 EXPAND；
- 每轮限制候选簇数、证据单元格数和 detector 计算预算；
- 同一 recipe + scope 请求采用稳定 ID 并缓存；
- 候选排序、聚类、去重和输出顺序必须确定；
- 预算耗尽必须写 coverage，不得静默截断；
- expansion request 使用 JSON Schema 验证；
- Agent reason 作为审计记录，不参与数学计算；
- 阴性深查结果与支持结果同样保存；
- 选择性深查产生的概率值标记为条件性结果；
- 所有路径继续遵守现有内存上限。

## 13. 测试设计

新增五个完全合成的测试文件：

1. `tests/test_short_pair_relation.py`
2. `tests/test_repeated_short_vector.py`
3. `tests/test_recurrent_group_collision.py`
4. `tests/test_short_signal_context.py`
5. `tests/test_short_signal_recall_e2e.py`

### 13.1 `short_pair_relation`

- n=3–8 × row/column；
- exact ratio、offset、shared tail；
- n−1 局部支持和一个例外；
- 缺失一个值；
- 一个量级异常；
- 整体缩放 `1e-6`、`1`、`1e6` 后关系不变；
- `/3`、`/13` 小分母尾数；
- `0/1/100` 边界、整数 ID、`10^k` 单位换算；
- 误差刚超过容差。

### 13.2 `repeated_short_vector`

- 长度 3–8 × row/column；
- 同一块两种不同重复向量；
- 跨 block、跨 sheet、文本间隔、稀疏物理列；
- 相同 missing mask；
- 单侧缺失、单侧异常和顺序置换负例；
- 低基数、整数编码和量化网格；
- exact 关系的跨原语去重。

### 13.3 `recurrent_group_collision`

- 12–20 个独立实体行、n=3–8；
- 每行一对不同的非基线高精度重复；
- 重复位置变化，避免退化为 identical column；
- 转置、混合组宽、缺失和一个异常；
- 全 1 基线但其他值重复；
- 只有 1–2 行碰撞、LOD、边界、小整数、固定分母和随机连续负例；
- 重叠 numeric block 不得重复计数。

### 13.4 上下文

- 3×36 累计边界前缀 + 末尾独立 summary；
- 转置和反向累计；
- 非单调重复向量不得被累计守卫降级；
- 只有一个单调 trace 或一个短平台不足以降级；
- `N=37`、`180`、`71,554` 的固定分母；
- 基线 1 只解释 anchor；
- biological replicate 阻止技术重复降级；
- normalized 标签不能压低局部精确比例。

### 13.5 端到端

一个多 sheet 合成工作簿同时包含：

- short pair；
- repeated vector；
- recurrent collision；
- cumulative boundaries。

验证：

- 前三类 confirm 后为 high；
- 累计类存在于 raw scan，但 review 为 low；
- forensic 保留 raw high；
- candidate packet、expansion request、expanded findings、packet 和 HTML 连通；
- 非法状态转换和未注册 recipe 被拒绝；
- 两轮预算和 coverage 生效；
- 同一输入重复运行的确定性输出一致；
- 全量 pytest 和 golden 通过。

## 14. 验收标准

1. 同类短样本关系不再因全局最小长度在 Agent 看到之前消失。
2. 多个 medium/low seed 可以按独立物理证据聚合并触发深查。
3. ETMR 型 shared-tail、5/6 局部比例、双重复三元组和多行组内碰撞的通用合成布局，
   在 confirm 后均为 high。
4. 累计堆叠布局先被检出，再在 review 中降为 low。
5. Agent 只遵循状态文件的 `next_action`，不选择 profile。
6. 裸 CLI 仍可独立完成一次性扫描。
7. 原始信号、路由理由、展开动作、阴性结果、最终判定和 coverage 均可重放。
8. 默认 HTML、review packet 与 scan.json 对 finding group 的覆盖一致。
9. 不提交任何真实论文数据、标识或判定。

## 15. 实施分期

### Phase 1：工作流骨架与报告链

- 状态机、artifact schema、请求验证和预算；
- canonical finding group 消费；
- candidate packet 与 expansion request；
- 不改变现有 detector 结论。

### Phase 2：三个通用原语

- seed/confirm 共用底层函数；
- 行列双向；
- footprint 去重；
- 通用合成回归。

### Phase 3：上下文守卫

- 累计边界；
- 固定分母、锚点、边界、量化；
- 公式蕴含关系；
- review/forensic/triage 行为。

### Phase 4：Agent skill 默认切换

- 更新 `skills/paperconan/SKILL.md`；
- Agent 审核默认进入 workflow；
- 裸 CLI 文档保持一次性定位；
- 对缓存语料做 shadow 运行，比较候选量、展开率、KEEP/DROP 原因和 Agent token 消耗，
  再启用默认自适应交付。
