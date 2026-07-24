# 设计：Agent 默认的分阶段选择性展开工作流

- 日期：2026-07-25
- 状态：待用户书面复核（v2）
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
- 长度 3–8 的完整短向量重复，并让共享 core 连续覆盖 3–11，避免新的长度空档；
- 多个布局互异的实体行中反复出现非基线高精度组内碰撞；
- 上述模式的行列转置、跨 block 和跨 sheet 版本。

同时必须避免把累计堆叠边界、固定分母计数频率、归一化锚点、检测限、
低基数量化网格和有证明的技术重复直接升级为高优先级。

## 2. 设计目标

1. PaperConan 与 Agent 搭配时，默认使用分阶段选择性展开流程。
2. 裸 CLI 保留一次性、无模型的确定性扫描。
3. Agent workflow 是独立工作流入口，不是第四个 profile；Agent 不选择 profile。
4. DISCOVER 扩大召回，但只向 Agent 提供压缩候选簇。
5. Agent 在 ROUTE 只决定注册预算与暂缓处置，在 ADJUDICATE 形成谨慎解释；
   两个阶段都不决定数学计算结果。
6. 深查由注册的确定性 recipe 执行，可缓存、可重放、有预算上限。
7. 三个 canonical abstraction 的数学内核同时服务 seed、confirm 和旧 detector
   兼容适配层，避免新旧实现漂移。
8. 最终只向用户交付 Agent 判定后的统一报告。
9. 所有真实论文数据、DOI 和判定继续保持本地且不进入 git。
10. shared-axis、固定分母、归一化锚点和公式来源等相关结构不得被重复计算为
    多份独立支持。

## 3. 非目标

- PaperConan 不管理模型密钥、模型 SDK 或模型提供商。
- 不让 Agent 动态编写 detector、修改阈值或执行任意代码。
- 不尝试仅凭数值模式判断作者意图。
- 不把只有两个支持点的关系自动升级为 high。
- 不在本次设计中解决只有图片而没有可读源数据的图表数字化。
- 不将真实论文工作簿或具体数值写入测试 fixture。
- 不在一个版本内删除既有 public finding kind 或破坏裸 CLI 的 scan.json 消费者。
- 不把 workflow 已覆盖等同于旧 detector 的硬门槛迁移已经完成。

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

用户通过 PaperConan skill 请求论文检查时，默认进入固定的选择性展开流程。
Agent 不向用户询问 profile，也不得将裸 CLI 的原始报告直接作为最终结论。

PaperConan 提供确定性工作流命令，skill 负责模型调用和编排：

```bash
paperconan workflow start data/ --out audit/agent/
paperconan workflow route audit/agent/ \
  --request audit/agent/steps/t000/routing_request.json
paperconan workflow finalize audit/agent/ \
  --verdict audit/agent/verdict.json \
  --out audit/agent/adjudicated.html
paperconan workflow status audit/agent/
paperconan report audit/agent/scan.json \
  --expanded audit/agent/expanded_findings.json \
  --verdict audit/agent/verdict.json \
  --out audit/agent/adjudicated.html
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
| `EXPAND` | 执行本轮已注册的确定性深查 recipe | 阅读结果；工具完成后回到 ROUTE 或进入 ADJUDICATE |
| `ADJUDICATE` | 提供原始信号、展开结果、阴性检查和 coverage | 形成谨慎判定 |
| `COMPLETE` | verdict 已验证且统一报告已原子生成 | 向用户交付结果 |

### 5.2 Agent 不选择 profile

- `DISCOVER` 固定保留 raw signals；
- `EXPAND` 固定运行候选对应的 confirm 逻辑；
- `ADJUDICATE` 固定应用 review 上下文；
- `review`、`triage`、`forensic` 只属于裸 CLI 和报告展示。

每次工作流操作写出：

```json
{
  "workflow_stage": "ROUTE",
  "next_action": "write_routing_request",
  "next_artifact_path": "steps/t000/routing_request.json",
  "allowed_decisions": ["expand", "explained", "needs_context", "defer"],
  "allowed_recipes": ["partial_pair_relation", "shared_fraction_check"],
  "route_step": 0,
  "max_route_steps": 5,
  "expansion_round": 0,
  "max_expansion_rounds": 2,
  "budget_remaining": {
    "clusters": 8,
    "evidence_cells": 2000,
    "context_requests": 4,
    "context_asset_bytes": 50000000,
    "render_pixels": 100000000
  },
  "allowed_context_requests": ["load_figure_context", "render_image_context"]
}
```

CLI 拒绝非法状态转换、未注册 recipe、超预算请求和第三轮展开。

`workflow route` 接收一整个 route step 的 envelope，而不是一次只处理一个 cluster：

```json
{
  "schema_version": 1,
  "workflow_id": "wf:synthetic:01",
  "parent_packet_sha256": "sha256:...",
  "route_step": 0,
  "expansion_round": 0,
  "decisions": [
    {
      "cluster_id": "sheet:synthetic:ratio:1",
      "decision": "expand",
      "recipes": ["partial_pair_relation"],
      "context_requests": [],
      "context_refs": [],
      "reason": "5/6 aligned values support a precise non-unit ratio"
    }
  ],
  "proceed_to_adjudicate": false
}
```

语义固定为：

- 当前 packet 中每个 actionable cluster 必须恰好出现一次，cluster ID 不得重复；
- 每次合法 `workflow route` 都令 `route_step += 1`，无论是否执行 numeric expansion；
- 同一 envelope 中多个 `expand` 合并为一个 expansion round；零个 `expand` 不增加轮次；
- `needs_context` 必须列出注册的 context request，只消耗 context 预算；
- `explained` 和 `defer` 只记录路由处置，不改变 finding；
- 空 `decisions[]` 只在没有 actionable cluster 且 `proceed_to_adjudicate=true` 时合法；
- 仍有展开或上下文动作时，工具执行后回到 ROUTE 并生成新的 packet；
- `proceed_to_adjudicate=true` 时不得同时请求 expand/context，且不能遗留未处置候选；
- 本 step 没有 expand/context 时必须 `proceed_to_adjudicate=true`，false 请求非法；
- 合法显式结束、所有可用预算耗尽或达到 step 上限时进入 ADJUDICATE；
- 达到 `max_route_steps` 时记录未完成 coverage 并进入 ADJUDICATE，不得无限请求 context；
- `workflow finalize` 只接受 ADJUDICATE，验证 verdict 和全部 lineage，原子写出报告并
  将状态置为 COMPLETE；COMPLETE 状态下只允许同 digest 的幂等 finalize，其他请求拒绝；
  单独运行 `paperconan report` 只渲染，不修改 workflow state。

JSON Schema 必须用 `if/then/not` 明确编码：任一 decision 含 `expand` 或
`needs_context` 时 `proceed_to_adjudicate` 必须为 false；不存在这两类动作时必须为
true。不能只依赖运行时约定。

## 6. 选择性展开数据流

```text
宽召回 raw event / seed
  → raw core match + provisional raw owner（审计）
  → 确定性 context evaluation
  → explained-cell removal
  → residual core 重算 + final residual owner
  → residual exact-vector cells 从 collision ledger 扣除
  → residual source-event 去重
  → dependency graph / union
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
- medium/low 可以与其他 dependency components 聚合后触发展开。

示例结构：

```json
{
  "seed_id": "sheet:synthetic:ratio:1",
  "kind": "partial_ratio_seed",
  "sheet": "Synthetic panel",
  "support": 5,
  "total": 6,
  "ratio": 0.73184261,
  "max_residual": 2e-8,
  "seed_tier": "direct_confirm",
  "direct_confirm_reason": "partial_ratio_support_and_information_gate_v1",
  "routing_rule_version": 1,
  "calibration_id": "calibration:short-pair:v1",
  "candidate_strength": 0.94,
  "cells": ["F3:F8", "H3:H8"],
  "evidence_unit_id": "f.xlsx:S:panel-1:entity-3:group-a",
  "dependency_key_version": 1,
  "dependency_keys": [
    "axis:4c9d...",
    "normalization_anchor:none",
    "formula_source:none"
  ],
  "source_finding_refs": ["legacy:constant_ratio_row:17"]
}
```

`seed_tier` 由版本化注册规则确定，只能是 `direct_confirm`、`medium` 或 `low`；
`direct_confirm` 还必须带注册的 `direct_confirm_reason`。Agent 不得重分类。
`candidate_strength` 只是在同一 tier 内的确定性排序分数，
不是概率、p 值或最终 severity。

### 6.2 候选聚合

送入 Agent 前必须：

- 按论文、figure、sheet、panel 和物理单元格聚类；
- 合并重叠 numeric block；
- 合并同一数值结构的多个 detector；
- 标记 shared-axis、累计、固定分母、基线、边界、公式来源和量化上下文；
- 采用 finding family 多样性配额，防止一种噪声占满 top-K；
- 保存未进入 packet 的候选数量和原因。

#### 6.2.1 Evidence unit

规范中的“独立”默认只表示布局上互不重叠，不表示已经证明生物学独立。
确定性的布局证据单元定义为：

```text
distinct_evidence_unit =
  (file, sheet, panel_id, orientation, entity_span_or_id, replicate_group_id)
```

只有图例、Methods 或明确表头支持时，Agent 才可另外记录
`semantic_independence` 或 `biological_independence`。PaperConan 不自行推断；
这些语义字段只供 ADJUDICATE 参考，不改变确定性路由计数或数学结果。

这些 ID 来自版本化的 deterministic layout segmentation：

- `panel_id` 优先使用显式 block/sub-block 和表头层级；无可用标签时使用最小
  canonical block footprint 的稳定 hash；
- `orientation` 明确为 `row` 或 `column`；
- `entity_span_or_id` 使用绝对物理行/列 span，不使用可随排序变化的局部序号；
- `replicate_group_id` 使用有序 replicate span、missing mask 和表头路径；无法识别时
  写为该 scope 专属的 `unknown:<hash>`，不得把所有 unknown 合并为一组；
- 分组存在多个同样合理的解释时写 `grouping_unknown=true`，只可出 seed，不得自动 high。

#### 6.2.2 依赖合并

Context evaluator 先标记并剔除 explained cells，再对 residual events 建图。以下任一
关系成立的 seeds 必须经 union-find 合并为同一 dependency component：

- 共享同一个 residual `source_event_id`；仅处于重叠 block 不算；
- 同一或等价 X 轴/坐标序列本身仍属于两个 finding 的 residual evidence；
- 有可追踪证据表明来自同一固定分母或同一个 count/n 来源；
- 公式或导出结构证明来自同一归一化锚点，而不只是同处一个 normalized panel；
- 公式直接或间接引用同一上游单元格；
- 同一数值关系被多个 detector 或多个重叠窗口重复表达。

Union 的传递闭包用于保守计票，但必须保存每条 edge 的 typed reason，避免把“同一
sheet/panel”当作依赖。强 shared-axis、固定分母、边界、锚点或公式解释的单元格写入
`explained_cells`，从聚合和 p 值证据中剔除；raw seed 仍保留。

`evidence_unit_id`、`dependency_keys` 和 `dependency_key_version` 必须由 PaperConan
按注册规则生成；Agent 只能读取，不能新增、删除或改写依赖边。依赖规则版本进入
candidate packet、缓存键和 replay 输入。

#### 6.2.3 确定性展开下限

所有弱信号门槛按不同 dependency component 计票；evidence unit 只描述 component
内部的布局支持度。每个 component 按 canonical ownership 得到一个
`primary_family`，同时保留审计用 `component_family_set`；supporting family 不参与
family diversity 投票。

单个 `direct_confirm` component 可以单独申请展开。仅依靠 medium/low 时，必须满足：

- 至少两个不同 component 的 `primary_family` 不同，且至少一个为 medium；或
- 同一 `primary_family` 至少三个不同 component，且 residual evidence 覆盖至少
  三个不同 evidence units；或
- 全部为 low 时，至少三个不同 component 且覆盖至少两个 `primary_family`。

同一 dependency component 内无论有多少 detector、family 或 evidence unit 命中，
对路由都最多贡献一票。输出同时包含 `n_distinct_evidence_units`、
`n_distinct_dependency_components`、`primary_family` 和 `component_family_set`。
这些是保守的初始路由下限；shadow/null 校准只能统一调整注册配置，Agent 不能调整。

### 6.3 Agent 路由

`routing_request.json` 中的每个 decision 只允许：

```json
{
  "cluster_id": "sheet:synthetic:ratio:1",
  "decision": "expand",
  "recipes": [
    "partial_pair_relation",
    "transpose_check"
  ],
  "context_requests": ["load_figure_context"],
  "context_refs": [],
  "reason": "5/6 aligned values support a precise non-unit ratio"
}
```

允许的 decision：

- `expand`
- `explained`
- `needs_context`
- `defer`

这些 decision 只控制预算和下一状态。`explained` 是待 ADJUDICATE 复核的路由记录，
必须附上下文引用；它不能改写 raw/review severity、dependency、数值参数或 finding。
PaperConan 只接受达到注册路由下限的 `expand` 请求。Agent 不能通过拆分 envelope、
重复 cluster 或修改轮次来绕过预算。

`explained.context_refs[]` 必须唯一指向 context artifact digest、已注册 context finding
或图例/Methods 片段 ID，并通过当前 context asset manifest 校验。Context requests、
加载字节和渲染像素分别受预算约束；超预算与未知引用均拒绝。

`context_refs[]` item 固定为：

```json
{
  "context_artifact_id": "context:t001:0",
  "asset_id": "asset:figure:0",
  "region_id": "page-1:region-2",
  "digest": "sha256:..."
}
```

Decision schema 使用条件约束：

| decision | recipes | context_requests | context_refs |
|---|---|---|---|
| `expand` | 至少 1 个 | 可选 | 可选既有引用 |
| `needs_context` | 空 | 至少 1 个 | 可选既有引用 |
| `explained` | 空 | 空 | 至少 1 个 |
| `defer` | 空 | 空 | 空 |

默认 context budget 的单位是：注册动作数 `context_requests=4`、实际加载资产 bytes
`context_asset_bytes=50,000,000`、实际渲染像素 `render_pixels=100,000,000`。
任一维度耗尽后，state 清空相应 allowed action 并记录 coverage；仍可用的 numeric
action 不受影响，全部动作耗尽或达到 step 上限时进入 ADJUDICATE。

首批注册 recipe：

- `partial_pair_relation`
- `shared_fraction_check`
- `repeated_vector_check`
- `group_collision_aggregation`
- `transpose_check`
- `merge_sibling_blocks`
- `shared_axis_check`
- `cumulative_boundary_check`
- `fixed_denominator_check`
- `formula_provenance_check`

上下文请求与数值 recipe 分开注册：

- `load_figure_context`
- `render_image_context`

上下文请求只负责确定性地定位、加载或渲染图例、Methods 和图片区域；
图片与文字的解释仍由 Agent 完成，不写入 detector 的数学结论。

Agent 不能传入自定义代码、阈值或未注册的上下文动作。

## 7. Canonical abstraction 与既有 detector 的迁移契约

三个名字是 workflow 中的 canonical abstraction，不是三套与旧 detector 平行运行的
新扫描器。唯一数学内核为：

```text
iter_numeric_vectors()
match_pair_relation()
vector_information()
collect_exact_collision_events()
collision_stats()
finding_footprint()
shared_axis_context()
cumulative_boundary_context()
```

共享数学函数本身支持 legacy 入口需要的任意 span；“3–11”只限定本设计新增的
short canonical eligibility。扫描对行向和列向对称，保存绝对 Excel 坐标。旧 detector
继续提供裸 CLI/API 兼容入口，但必须调用上述共享内核，再由 adapter 施加旧 gate；
不得复制一份阈值、容差或尾数算法。

### 7.1 Legacy migration matrix

| Canonical owner / shared core | 既有入口 | 迁移方式 |
|---|---|---|
| `short_pair_relation` / `match_pair_relation()` | `detect_relations` 的 identity/ratio/offset/shared-tail 分支、`detect_row_relations`、`detect_equal_pairs`、`detect_short_row_reuse`、`detect_scaled_row_reuse`、`detect_row_pair_shared_fraction` | 保留旧函数和旧 kind；把任意 span 的匹配、support mask 和容差计算改为共享 core，旧 finding 作为 adapter 输出 |
| `repeated_short_vector` / `iter_numeric_vectors()` | `detect_recurring_row_vectors`、`within_row_repeated_segment`、上述 relation detector 的 exact identity 分支 | 使用同一双轴向量索引；workflow 聚成一个 `occurrences[]`，旧 identity kind 仅作 supporting ref |
| `recurrent_group_collision` / `collect_exact_collision_events()` | `detect_block_value_duplication`、`detect_dispersed_repeats` 和精确重复 detector 的碰撞来源 | 先产出未经过 summary gate 的 raw events，再由各消费者施加各自统计和上下文规则；对 dispersed path 这里只代表 event extraction 迁移 |

以下能力只复用局部 helper，不被三个 abstraction 整体取代：

- `detect_within_row_shared_fraction` 复用可靠尾数提取，但保留其行内拓扑；
- `detect_within_sheet_fraction_reuse` 保留 block-to-block 拓扑；
- 长度至少 12 的跨 sheet 列复用和长关系 finding 继续由既有 owner 负责，但 matcher
  仍复用通用 core；
- `sum_constant`、`exact_linear`、分布/位数类 detector 不属于本次三个 abstraction。

裸 CLI 的 legacy kind、字段和默认行为在迁移期保持兼容。Workflow 的 canonical
finding 写入 `expanded_findings.json`，并通过 `supporting_kinds` 和
`source_finding_refs` 关联旧结果。不得在同一 scope 上先跑旧 matcher、再跑一遍新 matcher。

### 7.2 与硬门槛审计的关系

配套的 `2026-07-19-detector-hard-threshold-audit.md` 与本设计共用
“精确关系靠信息量和显著性控制，不靠任意大样本 floor”的原则，但二者状态分开记录：

```text
workflow_covered = workflow 已能发现并展开
core_adapter_migrated = 裸 CLI 旧 detector 已切到共享 core
hard_floor_resolved = 裸 CLI 的对应硬门槛已经校准并迁移
```

三种状态互不推导。`workflow_covered` 或 `core_adapter_migrated` 均不能自动关闭
`hard_floor_resolved`；`detect_dispersed_repeats` 即使完成 event extraction adapter，
其原有 floor 仍单独保持 open。行关系、equal-pair、short reuse 和 shared-tail 的
重叠条目由本迁移矩阵记录；decimal-tail clustering、dispersed repeats、
identical-after-rounding、长列跨 sheet 和 block fraction reuse 等硬门槛工作继续留在
审计中。

为保持兼容，legacy 广义 detector 在 adapter 阶段保留其既有有效性 gate；workflow
允许的 3 点关系走单独的 short eligibility，且必须通过更强的信息量、量化 null 和
完整 scan 校准。两者在 `hard_floor_resolved` 前不得被描述为同一默认行为。

新 iterator 必须 sheet-scoped，或显式支持只有两个数值行的布局；不能在
`find_numeric_blocks(min_rows=3)` 丢弃后才启动。

### 7.3 `short_pair_relation`

通用 matcher 支持任意 span；`short_pair_relation` 只接收 `span_length=3–11` 的
对齐行对或列对。3–8 是首批验收重点，9–11 保证与当前 short/long 分界连续。关系包括：

- exact identity；
- partial identity；
- approximate identity，容差与 support mask 由版本化规则记录；
- constant ratio；
- constant offset；
- shared high-precision fractional tail；
- `k/n` 局部支持；
- 一个缺失、例外或量级异常。

完整 exact identity 可以由 matcher 识别，但 short scope 的 canonical finding 由
`repeated_short_vector` 所有。Partial/approximate identity 仍由
`short_pair_relation` 所有。

长度字段固定为：

```text
span_length = 对齐物理 span 中的位置数，包含 missing 位置
n_observed_pairs = 两侧同时有可比 numeric token 的位置数
n_match = observed pairs 中通过 relation rule 的位置数
missing_indices / outlier_indices = 相对于 span 的索引
```

Eligibility 使用 `span_length`；coverage 使用 `n_match / n_observed_pairs`，同时必须
满足各 relation 的最小 observed-pair 数。Exact vector 的 missing token 只有在两侧
missing mask 相同且规则显式允许时才进入 signature。

输出至少包含：

```json
{
  "relation_type": "ratio",
  "axis": "column",
  "span_length": 6,
  "n_observed_pairs": 6,
  "n_match": 5,
  "coverage": 0.8333333333,
  "matched_indices": [0, 1, 2, 4, 5],
  "outlier_indices": [3],
  "parameter": 0.73184261,
  "max_residual": 2e-8,
  "structural_gate_passed": true,
  "support_valid": null,
  "raw_model_p_value": null,
  "adjusted_model_p_value": null,
  "registered_alpha": null,
  "null_model": "exact_relation_information_gate_v1",
  "calibration_id": null
}
```

#### High 规则

以下只是结构下限；最终 high 还必须满足 §7.6 的 `support_valid` 与 full-scan
`adjusted_model_p_value` gate：

- 完整 3 点关系：3/3 严格成立、信息量足够并通过相应量化 null；
- 局部关系：`span_length >= 4`、`n_observed_pairs >= 3`、`n_match >= 3`、
  覆盖率至少 75%，最多一个例外；
- 局部精确比例：至少 4 个支持点、至少 3 个不同基准值、比例不是 `10^k`；
- shared tail：至少 3 个不同尾数，每个尾数至少 4 位；允许一对完整值相同，
  但必须至少有两个非零整数差且整数差不全相同；
- 只剩 2 个支持点不得 high。

量级异常只记录为 `possible_scale_entry_inconsistency`。工具不得自动改写原值；
可以报告某个 `10^k` 缩放候选是否会恢复关系。

### 7.4 `repeated_short_vector`

通用 iterator 支持任意 span；`repeated_short_vector` 只接收长度 3–11、
顺序敏感的行向量或列向量。3–8 是首批验收重点：

- 同一等价类一次输出整个 `occurrences[]`；
- 支持同块、跨块、跨 sheet、文本间隔和转置；
- exact 关系由本 abstraction 所有，不再同时输出 canonical identity ratio；
- 单侧缺失或单侧异常不算 exact，转给 `short_pair_relation`。

以下任一条件满足结构 eligibility；最终 high 仍须通过 §7.6 的校准 gate：

- 一个长度至少 4 的高信息向量完整重复；
- 同一 panel 至少出现两种不同的重复三元组；
- 一个高信息三元组跨明确不同的 block 或 figure 重复。

归一化锚点只从证据中剔除。若剔除锚点后仍有多个非基线匹配，
不得因为表中存在 `1` 而整体降级。

### 7.5 `recurrent_group_collision`

`collect_exact_collision_events()` 在 `min_hp`、结构列剔除、dominant boundary、
severity 和 summary 截断之前收集带完整坐标的 raw events。现有
`block_value_duplication` 和本 abstraction 分别消费同一事件流，不互相消费对方的
最终 finding。

Collector 采用可重放 iterator 或两遍扫描，只物化重复 group；重叠 block 按
`source_event_id` 去重。每个 raw event 至少包含：

```text
source_event_id
absolute_cell
raw_numeric_token / canonical_decimal
display_precision
collision_key / collision_key_version
source_block_ids
orientation / header_path
panel_id / evidence_unit_id / replicate_group_id
measurement_family_key / measurement_family_rule_version
formula_source_ids
```

“Exact”只比较版本化 `canonical_decimal`；按显示精度相同是另一种明确标记的
rounded relation，不得混入 exact collision。只有
`coverage_complete_for_scope=true` 时才能计算可升级的概率值；cache 被截断时，
confirm 必须从源文件重建完整 scope，不能从 examples 外推。

聚合顺序固定为：

1. 合并重叠 numeric block 和重复事件，保存 provisional raw owner；
2. 剔除强上下文解释的单元格；
3. 在 residual footprint 上重跑 canonical core 并确定 final residual owner；
4. 由 residual `repeated_short_vector` 认领完整有序向量；
5. 从 collision ledger 扣除该 residual exact-vector cells；
6. 对剩余事件去重，并按 measurement family 和 evidence unit 聚合；
7. 重新计算支持度与 birthday/Poisson null。

`measurement_family_key` 必须在观察哪些值发生碰撞之前由 deterministic layout
segmentation 生成，至少综合 orientation、规范化单位、measurement/assay 表头路径、
数量级 band、显示精度和 replicate-group layout。不得按已碰撞的值事后拆 family。
无法唯一分组时写 `grouping_unknown=true`，只出 seed。

默认在单个 sheet 内聚合。跨 sheet 只在规范化表头、单位和 replicate layout 明确
兼容时启用；每个 file/sheet 仍保留为单独 stratum，不能直接池化背景格点。跨 sheet
语义不明确时，分别输出 sheet-level seed。

#### 7.5.1 Collision statistic

对预先确定的 dependency component `d`、其中的 evidence unit `u`、family stratum
`s` 和 canonical value `v`：

```text
c_suv = unit u 内值 v 的出现次数
m_su = unit u 内 eligible residual observations 数
pairs_obs,d = Σ_s Σ_(u∈d) Σ_v C(c_suv, 2)
N_eff,su = 1 / Σ_v p_su(v)^2
lambda_d = Σ_s Σ_(u∈d) C(m_su, 2) / N_eff,su
```

只计算同一 evidence unit/replicate-group 内规则允许比较的 pair；跨实体 pair 不得进入
observed 或 expected。`p_su(v)` 是保留 unit location、scale、quantization 和组内相关
结构的 hierarchical/conditional occupancy；不得用跨实体池化的宽背景代替。
已知固定格点用 exact occupancy；连续/显示精度模型使用候选 scope 之外的同类 units，
采用 leave-one-panel-out 或等价 cross-fit 的保守估计。背景不足时
`support_valid=false`。

若一个 component 只含一个 unit，或有证据支持其中 units 条件独立，且每个 stratum
满足注册的稀疏占用条件（v1 同时要求
`N_eff,su >= 20*m_su`、`max_v p_su(v) <= 1/(20*m_su)` 和已注册的近似误差上界），
使用：

```text
raw_model_p_value,d = P(Poisson(lambda_d) >= pairs_obs,d)
```

否则使用 component-level joint exact/empirical null，保留 unit random effect、
paired/serial correlation 和共同漂移。只有不同 dependency components 被模型证明
条件独立时才卷积分布；相关 components/families 使用联合经验 null。禁止选择最小
p 值。该值仍是条件模型结果，必须另外经过完整扫描流程的经验校准。

`support_valid=true` 至少要求：scope coverage 完整、layout grouping 唯一、family key
在碰撞前固定、residual evidence 覆盖至少 3 个 dependency components、每个 component
有 exact/经验 occupancy model 或满足稀疏近似条件，并且 calibration ID 与当前规则
版本一致。

High 规则：

- 至少 3 个不同的非边界高精度值发生精确碰撞；
- 分布于至少 3 个不同 dependency components，且每个 component 至少含一个 residual
  collision；同一 component 内多个 units 只构成一个复合观测；
- 碰撞发生在同一 replicate group 布局内，但不把这一点表述成生物学独立；
- `adjusted_model_p_value < registered_alpha` 且 `support_valid=true`；
  v1 `registered_alpha` 不高于 `1e-4`；
- 支持 n=3–11、混合组宽、缺失值和变化的重复位置。

输出必须包含：

```text
n_distinct_evidence_units
n_distinct_dependency_components
evidence_unit_definition
measurement_family_key
raw_model_p_value
adjusted_model_p_value
registered_alpha
null_model
calibration_id
support_valid
structural_gate_passed
explained_cells_removed
coverage_complete_for_scope
```

默认以一个 panel/sheet 聚合 finding 输出，不为每个实体行单独生成 high；只有满足
上述跨 sheet compatibility contract 时，才可输出保留 sheet strata 的 workbook-level
finding。

### 7.6 Selection adjustment

`raw_model_p_value` 只描述预注册局部 null；detector 搜索的窗口、方向、relation family
和参数都会产生选择效应。Agent 又可只展开合法候选的任意子集，因此 adjustment 不能
依赖某一次 Agent 路由。

`eligible universe` 必须先由版本化 structural enumeration 固定，再计算 local p、
seed tier、top-K 或 Agent 选择。每个 `calibration_scope_id` 内登记可比较的
`statistic_order`，先把异构 local statistic 转换为同一标尺的经验尾概率
`p_local`，再定义：

```text
tail_score = -log10(p_local)
max_tail_score(null workbook) =
  max over all eligible candidates × registered recipes × calibration scopes

adjusted_model_p_value =
  (1 + #{null workbooks: max_tail_score >= observed tail_score})
  / (1 + total_null_workbooks)
```

不得直接比较 pair tuple、vector tuple 和 collision occupancy statistic。上述是
paper-wide maxT/Westfall–Young 式 adjustment；改变 packet top-K 或 Agent 选择子集
不得改变 eligible universe 或 adjusted p。

生成 null workbook 的 `p_local` 时使用 analytic/exact tail、独立校准 split，或
leave-one-workbook-out rank；同一本 null workbook 不能同时拟合自己的 local tail 又
评估 maxT。Calibration manifest 固定 split/cross-fit 方案和随机种子。

若一个结构适用于多个预注册 null family，取其中最保守的 adjusted tail probability，
不得用任意混合权重平均。这样 Agent 之后选择任何子集都不会扩大已校准的检验集合。

实现一个版本化 family calibration registry。每个 relation type、每种 repeated-vector
结构和 collision mode 必须登记：

```text
structural_gate
local_statistic
raw_null_generator
dependence_model_set
full_scan_adjustment
registered_alpha
required_output_fields
calibration_id
calibration_scope_id
statistic_order
cross_scope_adjustment
```

v1 的局部统计量为：

- pair relation：`(n_match, information_bits, -max_residual)`；
- repeated vector：`(occurrence_count, span_length, information_bits, cross_scope_count)`；
- recurrent collision：component-level `pairs_obs` 及其 joint occupancy statistic。

Pair/vector 的 conditional null 必须保留 display precision、magnitude band、
missing mask、paired correlation、时间/序列自相关、unit random intercept/slope、
共同漂移和 layout。使用保持这些结构的条件置换，或对 IID、exchangeable、AR(1)、
random-effect 和 shared-drift 等预注册模型分别校准并取最保守尾概率。经验尾概率使用
`(1 + extreme_trials) / (1 + total_trials)`。Null generator、依赖模型、trial 数、
随机种子和 score version 都进入 `calibration_id`。

所有 canonical output 必须含 `structural_gate_passed`、`support_valid`、
`raw_model_p_value`、`adjusted_model_p_value`、`registered_alpha` 和 `calibration_id`。
同时记录 `calibration_scope_id` 与 `cross_scope_adjustment`。
统一 high 判据为：

```text
structural_gate_passed
and support_valid
and adjusted_model_p_value < registered_alpha
```

校准完成前只能输出 experimental raw result，不得赋 `direct_confirm` 或默认 high。

## 8. 上下文守卫

### 8.1 Legacy context migration contract

上下文守卫不是与 `_prefilter.py` / `_profiles.py` 平行运行的第二套分级系统。
实现一个纯函数 evaluator：

```text
evaluate_contexts(finding, context_bundle) -> contexts[]
```

`context_bundle` 是 typed、版本化且可哈希的输入，至少包含 paper/workbook/sheet scope、
layout segmentation、sibling panel/sheet index、规范化表头与单位、公式引用 DAG、
显示精度和 coverage。Context detection 在 DISCOVER 的 dependency/p-value 计算之前
执行；ADJUDICATE 只把已经确定的 contexts 投影成 review view，不在此时重新猜结构。

它允许多个 context 同时存在，每项统一返回：

```json
{
  "context": "cumulative_or_ranked_boundaries",
  "strength": "strong",
  "explained_cells": ["C3:C5", "D3:D5"],
  "explained_source_event_ids": ["event:..."],
  "applies_to_families": ["repeated_short_vector", "recurrent_group_collision"],
  "metrics": {}
}
```

Deterministic reducer 顺序固定：

1. 收集全部 contexts；只有 `strength=strong` 且 family 适用的 event 可自动解释；
2. 对 explained source events/cells 取 union，并保留每个 context 的 provenance；
3. 从 raw footprint 中剔除该 union；
4. 在 residual footprint 上调用同一 canonical core 重算 support 并重定 final owner；
5. 扣除 residual exact-vector cells 后，再计算 dependency、collision null 和 severity；
6. residual 仍通过规则则保留相应 review severity，否则按注册 mapper 降级或隐藏。

Medium context 只供排序和 ADJUDICATE 解释，不自动剔除单元格。Reducer、layout 和
context rule version 均进入缓存与 artifact lineage。

Provisional raw owner 和 raw finding 只用于审计；candidate、dependency 投票、最终
canonical report item 和 review severity 均以 final residual owner 为准。Workflow
不得把两者建模成一个会跨 owner 原地变化的 finding，而是保存两个 linked records：

```text
raw_finding_record:
  raw_finding_id / provisional_owner / raw_footprint
  raw_parameters / raw_null / raw_severity
residual_finding_record:
  canonical_finding_id / final_residual_owner / residual_footprint
  residual_parameters / residual_null / review_severity / profile_action
  derived_from_raw_ids[] / contexts[]
```

若 residual 为空，仍写一个 `residual_status=fully_explained` 的 canonical audit
record，但不生成可投票 evidence。Context 导致 owner 改变时，raw record 不变；
不得把 residual null、support、severity 或 footprint 写回 raw record。

为兼容现有 schema，view adapter 可临时 materialize 扁平 `raw_severity`、
`review_severity`、`effective_severity` 和 `profile_action`，但它们只是由两个 records
投影的 view，不是持久化 source of truth。

`raw_severity` 一经 detector/confirm 产出便不可覆盖。Profile mapper 是唯一写
residual record 中 `review_severity` 和 `profile_action` 的层：

- `DISCOVER` 和 forensic 读取 raw records；
- `ADJUDICATE` 和 review 读取 residual records；
- triage 使用 review view，再通过 `profile_action=hidden` 改变可见性；
- 旧 `_prefilter.py` 规则经 adapter 产出 context，不得再独立重复降级；
- 裸 CLI 和 workflow 使用同一个 evaluator。

迁移对应关系：

- shared-axis 复用并扩展现有 progression/axis helpers；
- 固定分母统一现有 within-column 和 decimal-tail helpers；
- 检测限/边界扩展现有 boundary context；
- normalization 标签只提供中等上下文，新的 anchor guard 才能解释具体单元格；
- ordinary `replicate` 不再自动等同 technical repeat；
- 现有 OOXML formula-cache 检查继续只报告 coverage；公式来源模板是新增元数据能力。

守卫不得删除 raw finding。Review 根据尚未被解释的证据重新计算
`review_severity`。

兼容字段真值表：

| 产物/视图 | `effective_severity` | legacy `severity` | 可见性 |
|---|---|---|---|
| 裸 CLI forensic | `raw_severity` | mirror effective | visible |
| 裸 CLI review | `review_severity` | mirror effective | visible/demoted |
| 裸 CLI triage | `review_severity` | mirror effective；旧 adapter 可保持既有 low 投影 | visible/hidden 由 `profile_action` 决定 |
| workflow `scan.json` / DISCOVER packet | `raw_severity` | mirror effective | raw routing coverage |
| `expanded_findings.json` canonical residual records | `review_severity` | mirror effective | 尚未最终渲染 |
| adjudicated report model | `review_severity` | renderer 只读 effective | 由 verdict + profile action 决定 |

`severity` 仅为兼容镜像，canonical reducer、路由、packet 和新 renderer 均不得把它
当 source of truth。Phase 1 必须在现有 profile mutation 前冻结 `raw_severity`。

### 8.2 Shared axis / coordinate

该守卫在行列两个方向对称运行，识别：

- 等差 progression；
- 等比 progression，包括序列稀释轴；
- 同一 ordered sequence/vector 在 sibling block、panel 或 sheet 的稳定轴位置重复；
- 明确的 time、dose、concentration、wavelength、coordinate 等轴标签。

一次性等差/等比形状只记 medium context，不能单凭形状认定为 axis。强命中需要：

- 数值序列在至少 3 个 sibling scope 的稳定轴位置复用；或
- 明确 axis 标签、稳定 layout role 和 progression/ordered-sequence 结构同时成立。

任意 measurement vector 即使重复三次，也不能仅凭重复认定为 axis；必须满足稳定轴
位置或明确 coordinate role。命中后遵循：

- 只自动解释非完整重复；完整表或完整 measurement vector 不因同时含共享轴而整体降级；
- 80% axis coverage 和 residual cell 数只作为 context metrics，不能直接决定降级；
- 始终只移除 axis cells，对剩余 measurement cells 重算 relation、collision 和 severity；
- 只有 residual evidence 不再通过任何 confirm/aggregation 规则时才可整体降级；
- axis cells 必须在 birthday/Poisson p 值计算前移除；
- 若轴外仍有高信息 measurement 重复，finding 必须保留。

### 8.3 累计或排序边界

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
- `review`：先只移除累计/排序 context 解释的 events；residual 不再通过规则时才
  low + `profile_action=demoted`，否则沿用 residual reducer 的 severity；
- `triage`：同样先重算 residual；只有不再通过时 hidden。

### 8.4 固定分母或计数网格

- 不得只用候选的 3–4 点拟合分母；
- 必须使用同一 trace 至少 8 个不同上下文值；
- 至少 90% 值在显示半个 ULP 内落在共同零锚定格点；
- 格点至少比显示量化步长粗 4 倍，排除平凡的 `10^d` 解；
- 至少 6 个不同整数 index；
- 奇偶位置交叉验证均成立；
- frequency、percent、count、read、UMI、clonotype 语义或伴随 n 列可增强置信。

该守卫只解释单点碰撞。整段向量重复必须在量化 null 下重算，
不能仅因固定分母而整体降级。

现有小分母 helper 仅作为 legacy adapter；共享实现必须支持大分母、显示精度和
交叉验证，不得继续维护多套不同上限。

### 8.5 归一化锚点

- 同一位置在至少 3 组、至少 80% 组中精确为 1；或
- 同时具有 control/reference/baseline 与 normalized/relative/fold 语义。

只剔除锚点 cell。其他非基线高精度匹配继续参与分级。

### 8.6 检测限和边界

强解释需要：

- LOD、LLOQ、ULOQ、ND、BDL、floor、ceiling、saturation 等明确语义，
  且值位于局部最小或最大；或
- 同一边界跨至少 3 组并占 block 至少 25%。

`0`、`1`、`-1`、`100` 本身只算中等上下文，不能整条降级。

### 8.7 技术重复

只有同时存在明确 technical/re-read/duplicate-injection 语义和共同上游观测证明时，
才能作为强解释。普通 `replicate` 或相邻列不够。

`biological replicate`、mouse、patient、sample、`n=` 是自动降级阻断项。

### 8.8 低基数或量化网格

- 至少 95% 值落在规则格点；
- 潜在格点数 `G <= max(8, 2m)`，或整数/ordinal 且 distinct 不超过 8；
- 在相应量化 birthday null 下碰撞并不罕见时降为 low；
- 若碰撞低于注册的 low-cardinality override alpha，不得仅靠低基数标签降级。
  v1 可取 `1e-6`，但只有 exact enumeration、importance sampling 或足以解析该量级的
  校准可用时才启用；否则该 override 保持 disabled，不能用 30,000 次试验外推。

### 8.9 公式蕴含关系

只有公式逻辑本身必然产生关系时才降级，例如：

- 两格引用同一源格；
- 对应公式是同一平移模板；
- 显式 `source*k`、`source+c`、`x/x`；
- 累计公式 `left + increment`。

仅仅“这些格子含公式”或标签含 normalized、mean、densitometry 不够。
实现该守卫需要扩展现有 OOXML 公式检查，保存公式坐标、引用来源和规范化模板；
不得把“缺少 cached value”的 coverage limitation 当成公式关系证明。

## 9. 去重和 finding 所有权

每条 finding 规范化为：

```text
(file, sheet, physical cells, relation family)
```

所有权分 raw 与 residual 两阶段，不使用会吞掉 exact vector 的静态 severity 优先级：

```text
raw core match
  → provisional raw owner（只供审计）
  → context explained-event union
  → residual core rematch
residual exact ordered vector
  → repeated_short_vector
  → 从 collision event ledger 扣除 residual cells
  → 对 residual events 重算 recurrent_group_collision
residual partial / missing / scale relation
  → short_pair_relation
remaining distributed collision without group structure
  → block_value_duplication
```

Legacy kind 的 canonical owner：

| Legacy family | Canonical owner |
|---|---|
| span 3–11 的 `identical_column`、`identical_row`、short exact reuse、`recurring_row_vector`、`within_row_repeated_segment` | `repeated_short_vector` |
| span 3–11 的 ratio、offset、partial/approximate identity、partial relation、row-pair shared fraction | `short_pair_relation` |
| span ≥12 的 identity/relation | 对应 legacy long owner，经 shared matcher adapter |
| 跨至少 3 个 dependency components 的残余组内碰撞 | `recurrent_group_collision` |
| 其他 block 内分布式精确碰撞 | `block_value_duplication` |

较弱/兼容 finding 的 residual event-set 被主 finding 完全包含，或
`intersection / min(size_a, size_b) >= 0.8` 时不再作为独立 candidate，写入主 finding。
只共享一个 source event 不足以做 finding dedup，但该 event 仍会在 dependency graph
中阻止双方被当成独立投票：

```json
{
  "supporting_kinds": ["block_value_duplication"],
  "source_finding_refs": ["..."]
}
```

全比例与局部比例同时出现时，保留覆盖范围较大的一个。所有被合并 finding 的
原始坐标、规则和 severity 仍能通过 `source_finding_refs` 追溯。

## 10. 工作流产物

Agent 工作目录包含：

- `scan.json`：既有确定性原始扫描，加 bounded `short_signal_seeds[]`；
- `states/sNNN.json`：不可变的状态、下一动作、预算和 coverage 历史；
- `workflow_state.json`：只作为指向最新 state digest/path 的可变 index，不作 lineage parent；
- `steps/tNNN/candidate_packet.json`：该 route step 不可变的压缩候选簇；
- `steps/tNNN/routing_request.json`：Agent 对该 step 所有候选的不可变路由决定；
- `steps/tNNN/numeric_results.json`：该 step 的不可变 linked raw/residual numeric
  records 与阴性结果，可为空；
- `steps/tNNN/context_results.json`：该 step 的不可变 context artifact/asset refs、digest
  和 coverage，可为空；
- `expanded_findings.json`：进入 ADJUDICATE 时生成的一次性 cumulative manifest，
  顶层含 canonical `findings[]`、阴性 `results[]` 和全部 step child digests；
- `verdict.json`：Agent 判定；
- `adjudicated.html`：唯一用户交付报告。

后续 route step 不得覆盖早期文件。`route_step` 每次递增，`expansion_round` 只在
numeric expand 时递增并独立限制为最多两轮。所有 parent digest 只指向不可变
per-step/state artifact；
顶层 current index 即使更新，也不参与数学 cache 或 replay identity。

所有 workflow JSON 使用共同 envelope：

```text
schema_version
workflow_id
artifact_type
producer.paperconan_version
source_manifest_sha256
context_asset_manifest_sha256
config_digest
dependency_key_version
context_rule_version
parent_artifact_digests[]
```

Source manifest 使用逻辑根下规范化相对路径、每个数值源文件的 bytes SHA-256 和
资产类型；不把机器绝对路径放入语义 hash。后续加载的图例/图片进入 append-only
context asset set；每次变化都写不可变、content-addressed 的
`context/manifests/<sha256>.json`，资产 bytes 同样按 digest 保留。可变 current manifest
只作 index，每个 child artifact 绑定并可独立读取当时的 manifest digest。
`routing_request.json` 绑定 parent packet digest；`expanded_findings.json` 还包含
`source_scan_sha256`、`recipe_versions`、`findings[]` 和阴性 `results[]`；
`verdict.json` 同时绑定 scan、expanded 与 context asset digests。
任何 stale 或来源不匹配的 request、expanded artifact 或 verdict 都必须拒绝。

`workflow_id` 是语义 ID，由 source manifest、config digest、PaperConan/rule versions
确定生成，因此固定输入可重放为相同 bytes。若实现另需随机运行实例标识，只能写
`workflow_instance_id` runtime audit field，并排除出 stable ID、cache、semantic hash
和 canonical byte-equality 模式。

Raw collision events 由 recipe 从源数据确定性重建，或写入受内存/证据上限约束的本地
cache；不得把截断的 `example_cells` 当作完整事件源。发生截断时必须记录 coverage。

最终 report 先构建统一 `report_model`：

```text
scan legacy findings
  + expanded_findings canonical findings
  + verdict finding references
  → canonical dedup
  → adjudicated.html
```

所有 scan/expanded finding 都分配命名空间稳定 ID。ID 分两层：

- `raw_finding_id`（例如 `scan:<hash>`）基于 immutable 原始物理 footprint/scope、
  detector/rule version 和数学参数；没有逐 cell footprint 的 legacy/image/distribution
  kind 使用注册的 scope-fingerprint fallback；
- `canonical_finding_id`（例如 `expanded:<hash>`）基于 residual source-event footprint、
  canonical/context rule versions 和 residual 数学参数。

Context 重定 owner 时不得改写 `raw_finding_id`；它创建新的 canonical ID，并通过
`derived_from_raw_ids[]` 连接来源。Canonical dedup 输出
`alias_id -> canonical_id`；verdict 可以
引用主 ID 或 alias，但必须唯一解析，未知、歧义、重复冲突的 ref 均报错，不得静默
匹配零条或多条。传统裸 CLI 的
`paperconan report scan.json --verdict ...` 保持兼容；workflow 最终报告必须显式传入
`--expanded`，即使它是合法的空 artifact。不得把 canonical finding 塞入
`cross_sheet_findings` 来绕过接口。

两类 fingerprint 都包含 source manifest、规范化相对文件名和 sheet；不得包含 profile
projection、自由文本 reason、时间戳或模型审计字段。

原始 `report.html` 仍是中间证据浏览器，不是最终结论。

如果 Agent 或工作流中途失败，状态必须为 `workflow_incomplete`，
并记录停止阶段、未完成候选和 coverage limitation。不得静默退回裸 CLI 后宣称完成。

## 11. 报告链一致性

当前 `BLOCK_FINDING_GROUPS` 已包含 `block_dups`，packet 可以处理，
但 HTML 的 per-block group 列表没有使用这份 canonical 定义。

实现必须：

- Phase 0 先修复这一现存缺口，并加只有 `block_value_duplication` 的回归 fixture；
- 将 canonical finding-group 常量移到无循环依赖的共享模块；
- 让 HTML、Markdown 和 packet 全部从同一集合派生；
- 保留新 finding 的 support、outlier、坐标和上下文字段；
- 确保被 shared-axis、累计或其他强守卫降级的 finding 不进入 high-only review packet；
- 确保 scan.json 中的 high finding 不会因消费者漏 group 而不可见；
- 确保 `expanded_findings.json` 通过统一 report model 和
  `paperconan report --expanded` 到达 adjudicated HTML。

“一致”分成三个不变量：所有消费者识别同一 registered group keys；各消费者再显式
执行自己的 eligibility filter（例如 high-only packet）；所有 eligible canonical item
都有 renderer，未知 finding kind 使用中立的通用 fallback。新增 synthetic group key
与新增 unknown kind 分别测试，不能把 group key 和 kind 混为一谈。

## 12. 预算、确定性和安全

- 默认最多两轮 EXPAND；
- 每轮限制候选簇数、证据单元格数和 detector 计算预算；
- 同一 source manifest + scan digest + recipe + canonical scope + recipe version +
  数值/context/dependency 配置采用稳定 ID 并缓存；
- 自由文本 `reason` 不进入稳定 ID 或数学缓存键；
- 候选排序、聚类、去重和输出顺序必须确定；
- 预算耗尽必须写 coverage，不得静默截断；
- routing request 和 verdict 使用 JSON Schema 及 lineage 验证；
- Agent reason 作为审计记录，不参与数学计算；
- 阴性深查结果与支持结果同样保存；
- 选择性深查产生的概率值标记为条件性结果；
- 所有路径继续遵守现有内存上限。

### 12.1 确定性边界

Agent 的 request、reason 和 verdict 可能随模型运行变化，不属于 PaperConan 的确定性
保证。确定性只在以下输入同时固定时成立：

```text
numeric source manifest: logical root + normalized relative paths + file bytes
context asset manifest and referenced asset bytes
PaperConan version
numeric/configuration values
recipe, dependency, context and renderer versions
runtime metadata disabled, or runtime-only fields excluded from comparison
schema-normalized routing_request.json
schema-normalized verdict.json
```

具体保证：

- `workflow start`：固定源文件、版本和配置时，scan、seed、candidate packet 和初始 state 一致；
- `workflow route`：再固定 routing request 时，expanded findings 和下一 state 一致；
- `workflow finalize`：再固定 verdict 时，报告和 COMPLETE state 一致；
- `paperconan report`：再固定 expanded findings 和 verdict 时，HTML 一致；
- 状态转换校验始终确定。

数学 cache/ID 使用排除自由文本 reason 与 runtime audit fields 的 schema-normalized JSON，
不使用原始请求字节。若关闭 runtime metadata 并采用 canonical serialization，可断言
整个 artifact bytes 相等；否则只断言数学字段、稳定 ID 和规范化语义内容相等。

Skill 在可获得时记录 model、prompt/skill revision 和运行标识，作为审计元数据；
这些字段不参与数学计算。不得宣称“同一论文让 Agent 运行两次会得到相同 request
或 verdict”。

## 13. 测试设计

新增或扩展九个完全合成的测试文件：

1. `tests/test_short_pair_relation.py`
2. `tests/test_repeated_short_vector.py`
3. `tests/test_recurrent_group_collision.py`
4. `tests/test_short_signal_context.py`
5. `tests/test_seed_dependency_aggregation.py`
6. `tests/test_short_signal_statistics.py`
7. `tests/test_workflow_replay.py`
8. `tests/test_short_signal_recall_e2e.py`
9. `tests/test_finding_group_report_chain.py`

### 13.1 `short_pair_relation`

- n=3–11 × row/column，3–8 全组合重点覆盖；
- exact/partial/approximate identity、ratio、offset、shared tail；
- n−1 局部支持和一个例外；
- 缺失一个值；
- 一个量级异常；
- 整体缩放 `1e-6`、`1`、`1e6` 后关系不变；
- `/3`、`/13` 小分母尾数；
- `0/1/100` 边界、整数 ID、`10^k` 单位换算；
- 误差刚超过容差；
- `span_length`、`n_observed_pairs`、missing/outlier mask 的边界；
- sheet 只有两个数值行时仍可扫描；
- legacy adapter 与 canonical core 对相同 scope 给出一致数学结果；
- 通用 matcher 对 >11 span 可用，但 short owner 不吞掉 legacy long finding。

### 13.2 `repeated_short_vector`

- 长度 3–11 × row/column，3–8 全组合重点覆盖；
- 同一块两种不同重复向量；
- 跨 block、跨 sheet、文本间隔、稀疏物理列；
- 相同 missing mask；
- 单侧缺失、单侧异常和顺序置换负例；
- same missing mask 进入 signature，different mask 不进入；
- 低基数、整数编码和量化网格；
- exact 关系的跨原语和 legacy kind 去重；
- 9–11 长度不落入 short/long 空档。

### 13.3 `recurrent_group_collision`

- 12–20 个布局互异的实体行、n=3–11；
- 每行一对不同的非基线高精度重复；
- 重复位置变化，避免退化为 identical column；
- 转置、混合组宽、缺失和一个异常；
- 全 1 基线但其他值重复；
- 只有 1–2 行碰撞、LOD、边界、小整数、固定分母和随机连续负例；
- 重叠 numeric block 不得重复计数；
- raw event 在旧 `min_hp` / boundary / summary gate 前仍可供 confirm 使用；
- raw event schema、collision-key version、重叠 block event ID 去重；
- cache 截断时 `coverage_complete_for_scope=false`，回源重建前不能 high；
- exact vector 认领后，只有 residual events 参与 recurrent 统计；
- 三个 units 属于同一 dependency component 时不能 high，至少三个 components 才可；
- measurement family 在碰撞前确定，不同 family 不放入同一个局部 null；
- grouping ambiguous 只出 `grouping_unknown` seed；
- 跨 sheet 兼容 family 保留 sheet strata，不兼容时分别输出。

### 13.4 上下文

- 等差轴、等比稀释轴、跨 3 个 sibling panel 的不规则重复 ordered sequence；
- 一次性等差/等比 measurement 只记 medium，不自动降级；
- 非 axis 位置的三次高信息 measurement vector 重复不降级；
- shared-axis 的转置版本；
- 完整表重复不得仅因含共享轴降级；
- 轴外仍有 measurement 重复时，只剔除轴并保留残余 finding；
- 轴外恰好 3 个 `direct_confirm` measurement cells 仍保留；
- axis coverage 79%/80% 与 residual 3/4 的边界只影响 metrics，不直接决定降级；
- 3×36 累计边界前缀 + 末尾独立 summary；
- 转置和反向累计；
- 非单调重复向量不得被累计守卫降级；
- 只有一个单调 trace 或一个短平台不足以降级；
- 合成 `N=37`、`181`、`73,117` 的固定分母；
- 基线 1 只解释 anchor；
- biological replicate 阻止技术重复降级；
- normalized 标签不能压低局部精确比例；
- 同一 finding 同时命中多个 context；
- 多 context 的 explained-event union 和 residual core 重算顺序固定；
- raw exact-vector owner 在剔除 context 后变成 residual pair owner 时，两个 records、
  两组 stats/footprints/IDs 和 `derived_from_raw_ids[]` 均保留；
- 累计区外的独立 summary residual 仍通过规则时，review/triage 不得整体降级；
- `raw_severity` 不变，review/triage 只改变 effective review view；
- forensic/review/triage/workflow scan/expanded/report 的 severity 真值表逐项覆盖；
- Phase 1 从既有 detector 输出建立 seed 时，在任何现有 profile mutation 之前冻结
  `raw_severity`，后续 context migration 不得改变它。

### 13.5 Seed dependency aggregation

- 重叠窗口和同一 relation 的多个 detector 只产生一个 dependency component；
- A–B、B–C dependency edge 产生一个可审计的传递 component；
- 已被解释并剔除的共同 anchor 不建立 residual overlap edge；
- 同轴但 residual measurement 互异的 seeds 不因“同一 panel”被过度合并；
- 有来源证明的同一固定分母、锚点或 formula source 只能贡献一个 component；
- 两个不同 primary family 的 component 且至少一个 medium 可以申请展开；
- 同一 primary family 的三个 component 且跨三个 evidence units 可以申请展开；
- 两个 low 或三个同 component low 不得申请展开；
- 单个非 `direct_confirm` seed 不得绕过聚合下限；
- 改变 `candidate_strength` 只影响同 tier 排序，不改变展开资格；
- Agent 不能覆盖 seed tier、evidence unit、dependency keys 或 component family；
- orientation/span 与 unknown grouping fallback 的 ID 稳定；
- `n_distinct_evidence_units` 不得被命名为 biological independence。

### 13.6 统计 oracle 与 null 校准

- `_poisson_sf` 在覆盖 `p≈1e-4` 的 `(k, lambda)` 网格上与
  `scipy.stats.poisson.sf` 比较，并验证 p 随 k 增大不增、随 lambda 增大不减；
- 小格点使用穷举或 occupancy DP 计算 collision-pair 真分布；
- `pairs_obs,d`、unit-conditioned `lambda_d` 和允许的 component convolution 用手算小例验证；
- 跨 unit 池化背景导致 `N_eff` 过大的反例走 hierarchical/empirical null；
- 非均匀 occupancy 不满足 max-probability/误差界时禁止 Poisson shortcut；
- 相关 components 不做独立卷积；
- pair/vector matcher 使用独立实现的 brute-force reference oracle；
- CI 中运行固定种子的快速 Monte-Carlo smoke test；
- 离线校准分别覆盖 iid、clustered/correlated、单调曲线、shared-axis、固定分母、
  normalization、LOD、missingness、混合精度和转置；
- 每个 trial 是完整 synthetic workbook，并对全部合法 eligible candidates × recipes
  取最大统计量；离线校准统计
  family-wise candidate、expansion、post-confirm high 和 review-high rate，
  不只测单个 detector；
- IID、exchangeable、AR(1)、random-effect、shared-drift 和条件置换 null 均预注册；
- family calibration registry 对每种 pair/vector/collision mode 的 statistic、null、
  adjustment、alpha 和 required outputs 做 schema/golden 检查；
- 异构 statistic 不能直接比较；先转成 `p_local`/tail score，再对全部 scope 做
  paper-wide maxT adjustment；
- local-tail 拟合与 maxT 评估使用 independent split 或 leave-one-workbook-out，
  禁止同一 null workbook 自拟合自评估；
- 改变 packet top-K 或 Agent 选择子集不改变 eligible universe 与 adjusted p；
- 若用零事件上界验证 `1e-4`，每个关键 null family 至少约 30,000 次；
  否则使用精确枚举、重要性抽样或等价的置信界方法；
- `1e-6` override 只在 exact/importance method 或约数百万量级的相应分辨率校准下测试，
  否则断言其保持 disabled；
- 每个 null family 和 paper-level 总目标都在 calibration manifest 中预注册 95%
  置信上界；初始 family-wise review-high 目标不高于 `1e-4`，总目标单独给出；
- injected-alternative power 覆盖 n=3/4、一个缺失、转置、混合精度和
  residual-after-axis-removal；最低 power 在运行前预注册，不得事后改门槛；
- 校准脚本、随机种子、参数和汇总进入 git，真实论文数据和逐篇结果不进入 git。

`adjusted_model_p_value` 和 calibration gate 在上述校准完成前不得产生默认 high。

### 13.7 Workflow replay

- `workflow start` 在固定输入、版本、配置且关闭 runtime metadata 时运行两次，
  工具产物一致；
- 用冻结的多 decision / 零 expand `routing_request.json` replay 两次，
  expanded findings 和 state 一致；
- context-only step 递增 route step、不递增 expansion round，且不覆盖早期 artifact；
- 四类 decision、回到 ROUTE、无展开进入 ADJUDICATE 的转换均覆盖；
- 用冻结的 `verdict.json` finalize 两次，adjudicated HTML 和 COMPLETE state 一致；
- Agent 新生成的 request/verdict 不做跨运行相等断言；
- stable ID 不受自由文本 reason 影响；
- 修改输入 bytes、config、packet 或 expanded digest 后，旧 cache/request/verdict 被拒绝；
- 未知、重复、冲突 finding ref 被拒绝，dedup alias 可唯一重定向；
- `explained` 缺少上下文引用时被拒绝，合法记录也不改写 finding 数学字段；
- decision 的 recipes/context requests/context refs 条件矩阵和三维 context budget 均覆盖；
- 历史 content-addressed context manifests/assets 在追加后仍可独立 replay；
- 语义 `workflow_id` 固定；随机 instance audit ID 不影响 cache 或 byte-equality 模式；
- 重复/遗漏 actionable cluster、带 expand 的 `proceed_to_adjudicate=true` 被拒绝；
- 非法状态转换、未注册 recipe、第三轮展开和超预算请求被拒绝；
- runtime metadata 开启时测 semantic equality，关闭时测 canonical artifact byte equality；
- legacy report 可省略 `--expanded`，workflow finalize 要求合法的 expanded artifact；
- workflow 与 report 的 console script / `python -m paperconan` 入口均有分派测试。

### 13.8 端到端

一个多 sheet 合成工作簿同时包含：

- short pair；
- repeated vector；
- recurrent collision；
- shared axis；
- cumulative boundaries。

用固定的 routing request 和 verdict 验证：

- 前三类 confirm 后为 high；
- shared-axis 单元格从聚合和 p 值中移除；
- 累计类存在于 raw scan，但 review 为 low；
- forensic 保留 raw high；
- candidate packet、expanded findings、verdict 和 HTML 连通；
- 两轮预算和 coverage 生效；
- 全量 pytest 和 golden 通过。

### 13.9 报告链

- 只有 `block_value_duplication` 的 scan 在 HTML、Markdown 和 packet 中均可见；
- 新增 synthetic group key 后三个消费者均识别，再分别执行 eligibility filter；
- 未知 synthetic finding kind 走中立通用 renderer fallback；
- expanded finding 经 `--expanded` 进入统一 report model；
- supporting legacy finding 不重复显示为独立主 finding；
- finding ID 在 scan、expanded、verdict 和 HTML 间可追踪。

## 14. 验收标准

1. 同类短样本关系不再因全局最小长度在 Agent 看到之前消失。
2. 多个 medium/low seed 只有在通过 dependency 合并并满足确定性 component
   下限后才触发深查。
3. 完整 null/power 校准通过后，下列完全合成结构在 confirm 后均为 high：
   - 4 个配对值中至少 3 对保留各自至少 4 位且互不相同的小数尾，允许一对全值相同，
     但至少有两个非零且不全相同的整数差；
   - 5/6 局部精确比例；
   - 同一 panel 的两种不同重复三元组；
   - 至少 3 个 dependency components 的多值组内残余碰撞。
4. shared-axis 和累计布局先保留 raw signal，再只解释相应单元格；review 根据残余证据
   降级或保留。
5. Agent 只遵循状态文件的 `next_action`，不选择 profile。
6. 裸 CLI 仍可独立完成一次性扫描。
7. 固定 routing request/verdict 后，原始信号、展开动作、阴性结果、最终报告和 coverage
   均可重放；不要求 Agent 两次生成相同判断。
8. HTML、Markdown、review packet 对 registered group 的识别一致，并分别执行明确的
   eligibility filter；expanded finding 通过统一 report model 渲染。
9. 同一数学证据不会因 legacy/canonical 双路径产生两个主 finding。
10. 硬门槛审计分别记录 `workflow_covered`、`core_adapter_migrated` 与
    `hard_floor_resolved`。
11. 不提交任何真实论文数据、标识、数值或判定。

## 15. 实施分期

### Phase 0：现存报告链缺口

- 修复 `block_dups` 在默认 HTML 中不可见的问题；
- canonical finding-group 常量移到共享模块；
- HTML、Markdown、packet 消费同一集合；
- 加报告链回归，不改 detector 结论。

### Phase 1：工作流骨架

- 可执行的 route/finalize 状态机、共同 artifact envelope、lineage 验证和预算；
- candidate packet 与 routing request；
- route step / expansion round 分离、不可变 step/state artifacts 和 content-addressed
  context manifests；
- legacy/raw finding stable ID 与 scope-fingerprint fallback；
- 先从既有原始 finding/中间事件构造 bounded `short_signal_seeds[]`；允许 schema
  先落地而没有新 detector 数学，但不得把已被 profile 改写的 severity 当作 raw；
- `workflow_main(argv)` 接入现有手写 CLI 分派并保留 `paperconan <dir>`；
- report 明确接收并合并 `expanded_findings.json`；
- 固定 request/verdict 的 replay 测试；
- 不改变现有 detector 结论；
- 本阶段只能 opt-in/synthetic replay，不得切成生产默认 workflow。

### Phase 2：共享内核与 legacy adapters

- seed、confirm 和旧 detector adapter 共用底层函数；
- 行列双向；
- sheet-scoped 2-row 覆盖与 3–11 连续范围；
- raw collision event ledger；
- deterministic layout segmentation、measurement-family key 和 grouping-unknown fallback；
- footprint 去重；
- legacy migration matrix 和兼容字段；
- 通用合成回归；
- 新 canonical 结果保持 experimental/raw，不赋默认 high。

### Phase 3：统一上下文与 residual reducer

- shared-axis；
- 累计边界；
- 固定分母、锚点、边界、量化；
- 公式蕴含关系；
- typed context bundle、explained-event union 和 residual core 重算；
- linked raw/residual records、final owner 和 canonical ID/alias；
- `raw_severity` / `review_severity` / `effective_severity` 三视图；
- 既有 prefilter/profile adapter；
- review/forensic/triage 行为。

### Phase 4：Dependency aggregation 与统计校准

- distinct evidence unit、dependency component 和版本化 routing tier；
- shared measurement family 分层；
- brute-force/解析 oracle；
- selection-aware null、Monte-Carlo 和 injected-alternative power 校准；
- 在 Phase 3 contexts 生效后重跑完整流程；
- 只有校准通过的规则才能成为 `direct_confirm` 或默认 high。

### Phase 5：Agent skill 默认切换

- 先以 opt-in/shadow 方式更新 skill 编排，但不改变默认交付；
- 裸 CLI 文档保持一次性定位；
- 对缓存语料做 shadow 运行，比较候选量、dependency 合并率、shared-axis 解释率、
  展开率、最终保留/暂缓原因、校准指标和 Agent token 消耗；
- 预注册 go/no-go 指标通过后，才让 Agent 审核默认进入 workflow 并启用选择性展开交付。
