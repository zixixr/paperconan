# 设计：Agent 默认的分阶段短信号工作流（产品交付规范）

- 日期：2026-07-25
- 状态：待用户书面复核（v3，产品规范）
- 范围：PaperConan 数值检测、Agent 编排、候选展开、报告链
- 统计 companion：
  [短信号自动优先级统计校准规范](2026-07-25-short-signal-statistical-calibration-design.md)

> PaperConan 输出的是统计信号、数据不一致和待解释异常，不是对作者意图的判断。
> 最终解释仍需原始记录、图例、Methods、作者说明以及期刊或机构复核。

## 1. 本次修订的产品决策

PaperConan 当前主要采用一次性流程：

```text
确定性扫描 → profile 过滤 → review packet → Agent 判定
```

为控制假阳性和 Agent 上下文压力，若干 detector 使用较高的最小样本数或完整覆盖
门槛。这使只有 3–4 个重复、5/6 局部关系或短向量复用等真实数据不一致，可能在进入
Agent 前消失。

本设计采用“确定性宽召回 + Agent 选择性展开 + 确定性复算 + Agent 谨慎判定”：

1. 裸 CLI 继续提供一次性、无模型、确定性的扫描；
2. PaperConan skill 默认使用固定 Agent workflow，不让 Agent 选择 detector profile；
3. 新短信号在 Phase 2 即可以 experimental feature、review-ready evidence 进入 Agent
   和最终报告；
4. Agent 可以决定是否分配展开预算，但不能修改阈值、数学结果、所有权或依赖关系；
5. detector 自动赋予 `direct_confirm` 或默认 `high`，必须另行通过统计 companion；
6. 统计校准不阻塞工作流交付、Agent 复核或 Agent 默认入口切换；
7. 每个 Phase 独立 PR，后续 PR 从已合并主干开始，不积累跨 Phase 大分支。

这将核心价值从统计研究工程中解耦：短样本信号先变得可见、可展开、可判定；自动
优先级再按 calibration slot 逐个验证和启用。

## 2. 目标、边界和权威来源

### 2.1 目标

- 连续覆盖 span 3–11 的短信号，不在 3–4、5–8 或 9–11 形成新空档；
- 覆盖行、列、转置、跨 block 和在语义允许时的跨 sheet 布局；
- 把完整向量恒等、局部/近似关系和剩余组内碰撞分配给唯一 canonical owner；
- 把共享轴、固定分母、归一化锚点、累计边界、检测限、技术重复和公式来源纳入
  确定性上下文；
- 防止相关弱 seed 被当成多份独立支持；
- 让固定输入和固定 Agent 请求可重放，报告证据可追溯；
- 保持既有裸 CLI、public finding kind 和 `scan.json` 消费者兼容。

### 2.2 非目标

- 不让 PaperConan 管理模型密钥、模型 SDK 或模型提供商；
- 不让 Agent 动态编写 detector、传入阈值或执行任意代码；
- 不仅凭数值模式判断作者意图；
- 不把只有两个支持点的关系自动升级；
- 不在本设计中完成只有图片而无可读源数据的数字化；
- 不将真实论文数据、DOI、判定或大规模校准明细提交到 git；
- 不在一个 PR 中同时改 workflow、legacy activation floor 和统计启用状态；
- 不规定统计 null、概率阈值和 Monte Carlo 实现；这些属于 companion。

### 2.3 三份规范的权威边界

| 决策 | 权威来源 |
|---|---|
| Agent 产品入口、状态机、artifact、3–11 canonical owner、上下文和报告 | 本产品规范 |
| legacy detector 的 activation floor 是否降低 | 独立、被 git 跟踪的 hard-threshold audit/PR；本规范不授权降低 |
| 某 calibration slot 能否产生 detector 默认 high | 统计 companion |

三种状态必须分别记录，不得互相推导：

```text
workflow_covered
core_adapter_migrated
hard_floor_resolved
```

例如，workflow 已能发现 span 5 的关系，不表示裸 CLI 的旧 floor 已改变；旧 detector
已迁移到共享 core，也不表示该 floor 已完成统计复核。

当前本地草案 `2026-07-19-detector-hard-threshold-audit.md` 若要作为实施依据，必须
先被单独复核、纳入版本控制并增加对本规范的交叉引用。其中“降低
`_ROW_REL_MIN_COLS`”只决定 legacy activation；与局部 pair relation 重叠的 matcher
需求由本规范的共享 core 实现，不再新增第二套检测器。两份规范意见冲突时，按上表
的权威边界处理。

## 3. 两种产品入口

### 3.1 裸 CLI

```bash
paperconan data/
```

裸 CLI：

- 不调用 Agent；
- 一次性扫描并写出 `scan.json` 和报告；
- `review`、`triage`、`forensic` 仍是人类显式选择的显示/过滤 profile；
- 普通默认 `scan.json` 的 finding 集合不包含 workflow-only experimental candidates；
- 已稳定发布且通过 calibration 的新 detector 行为须另按版本启用；
- 报告明确提示所有 finding 仍需人工或 Agent 复核。

### 3.2 Agent 工作流

通过 PaperConan skill 检查论文时，默认进入固定 workflow。Workflow 是产品入口，
不是第四个 profile；Agent 不向用户询问应选哪个 profile。

建议命令面：

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

这些命令只执行确定性工作；模型调用由 skill 编排。

## 4. 固定状态机和职责

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

| 状态 | PaperConan | Agent |
|---|---|---|
| `DISCOVER` | 宽召回、上下文预标记、聚类、压缩 packet | 不作最终判断 |
| `ROUTE` | 校验请求、recipe、状态和预算 | 决定展开、补上下文、暂缓或进入判定 |
| `EXPAND` | 执行注册的确定性 recipe | 阅读结果，不修改计算 |
| `ADJUDICATE` | 提供原始信号、展开结果、阴性检查和 coverage | 形成谨慎 verdict |
| `COMPLETE` | 验证 verdict 并原子生成统一报告 | 向用户交付 |

### 4.1 Agent 的权限边界

Agent 只允许：

- 在工具给出的 `allowed_recipes` 中分配展开预算；
- 请求注册的文字、图片或公式来源上下文；
- 将候选暂缓，或在上下文支持下说明可能的良性数据结构；
- 在 ADJUDICATE 给出独立于 detector severity 的复核优先级和解释。

Agent 不允许：

- 自定义 matcher、统计量、阈值、容差或 recipe；
- 新增、删除或改写 dependency key；
- 修改 finding 的数值参数、support mask 或 canonical owner；
- 把未校准候选改写成 detector `direct_confirm` 或默认 `high`；
- 通过拆分请求、重复 cluster 或传入与状态不一致的轮次绕过预算。

Agent verdict 可以把确定性证据评为“强待解释异常”，但这是审阅结论，不会回写
detector 的 `severity` 或 registry 状态。

### 4.2 路由协议

每个 packet 必须明确：

```text
workflow_stage
next_action
next_artifact_path
allowed_decisions
allowed_recipes
route_step / max_route_steps
expansion_round / max_expansion_rounds
budget_remaining
coverage
```

每个 actionable cluster 在一次 route envelope 中恰好出现一次。Decision 只有：

- `expand`：至少一个注册 recipe；
- `needs_context`：至少一个注册 context request；
- `explained`：必须引用已加载上下文，仅记录路由处置；
- `defer`：不执行动作，留给 ADJUDICATE。

若还有展开或上下文动作，工具完成后回到 ROUTE；无动作时必须显式进入 ADJUDICATE。
达到轮次或预算上限时也进入 ADJUDICATE，并记录未覆盖部分，不能循环等待。

最小转换契约：

- 同一 envelope 不能同时请求 expand/context 和进入 ADJUDICATE；
- context-only step 增加 `route_step`，不增加 `expansion_round`；
- 同一 envelope 的多个 numeric recipes 合并为一轮 expansion；
- `explained` 只是带 context ref 的路由记录，仍必须进入最终 ADJUDICATE；
- `finalize` 只接受 ADJUDICATE；相同 input digest 可幂等重放，不同 digest 必须拒绝；
- 达到上限时未处理 cluster 以 coverage limitation 进入 verdict/report。

## 5. 最小 artifact 契约

工作流使用以下逻辑产物：

```text
scan.json
candidate_packet.json
routing_request.json
expanded_findings.json
verdict.json
adjudicated.html / adjudicated.md
```

每个 JSON artifact 至少包含：

```text
schema_version
run_id
artifact_id
parent_refs
config_digest
source_finding_refs
coverage
created_by_stage
```

约束：

1. 原始证据不可被上下文或 Agent verdict 覆盖；
2. 展开结果必须引用来源 finding、recipe 版本和输入 digest；
3. Agent request/verdict 是可审计输入，不参与 detector 数学；
4. 上下文资产至少记录来源、定位和 SHA-256 引用；
5. 具体目录布局、是否物理拆分 raw/residual、缓存实现留给实施计划；
6. schema 不兼容时必须显式拒绝，不得静默猜测字段；
7. 固定 source、配置、request 和 verdict 时，工具产物与报告必须可重放。

Agent 自由文本本身不要求两次采样完全一致。端到端确定性测试使用固定或 mock 的
`routing_request.json` 和 `verdict.json`。

## 6. 产品数据流

```text
deterministic enumeration
  → raw event / raw match
  → provisional canonical owner
  → deterministic context evaluation
  → explained event 标记
  → residual evidence 重算
  → final canonical owner
  → source-event 去重与 dependency components
  → compact candidate packet
  → Agent ROUTE
  → deterministic expansion
  → Agent ADJUDICATE
  → unified report
```

规范只要求每一步可追溯、顺序无关且确定；不强制某一种图算法或物理存储模型。

Workflow 的逻辑 raw stream 必须在 legacy `_cap_block_findings` 和
`apply_profile_to_findings` 之前冻结，并记录 `workflow_policy_version`。它不等价于
暗中选择 `review`、`triage` 或 `forensic`：

- profile 对 severity/profile_action 的原地修改不能回流到 workflow raw evidence；
- CLI per-block/global cap 只能影响 CLI 展示，不能让 workflow candidate 无记录消失；
- 为满足内存上限，raw stream 可用 streaming index、去重摘要或受控 spill，而不要求
  全部 materialize；
- 若安全上限确实阻止完整 enumeration，必须记录 omitted count、scope 和
  `coverage_complete=false`，不能静默截断；
- packet top-K 发生在 raw ledger/digest 之后，只限制 Agent 上下文，不改变 eligible
  universe。

### 6.1 Seed 与候选阶段

新短信号使用以下产品状态：

```text
evidence_stage = discovered | expanded | review_ready
feature_status = experimental | stable
routing_tier = standalone_review | medium | low | direct_confirm
registry_status = missing | disabled | enabled | revoked
```

- `discovered` 表示 raw matcher/event 已记录；
- `expanded` 表示至少一个注册 numeric/context recipe 已完成；
- `review_ready` 表示进入 ADJUDICATE 的 packet 已冻结；预算耗尽时仍可进入，但必须
  带 coverage limitation；
- `feature_status=experimental` 表示该 detector feature 尚未作为稳定默认行为发布；
- UI 的 `review_candidate` 是 `evidence_stage=review_ready` 的展示标签，不是校准状态。

`candidate_strength` 只用于同一 mode 内确定性排序，不是概率、p 值或最终 severity。
`standalone_review` 表示已通过版本化结构门、可以单独分配一次展开预算，但不表示
概率或 high；未校准新短信号可以使用它。只有 enabled calibration entry 可以产生
`direct_confirm`。Evidence stage、feature status、routing tier 和 registry status
相互正交；同一个 review-ready evidence 可以同时具有任何 registry status。

有限 packet 的合并不能全局比较 `candidate_strength`。确定性规则是：

1. 先按 routing tier 分层；
2. 在每层按 primary family/mode 做 round-robin；
3. 每个含 `direct_confirm` 或 `standalone_review` 的非空 primary family 先保留一个；
4. `candidate_strength` 只在同一 family/mode partition 内排序；
5. packet capacity 必须不小于已注册 primary family 数，否则配置拒绝；
6. 每个 partition 的 omitted count 和最高 omitted tier 写入 coverage。

这样噪声较多的 family 不能仅因候选数量或不可比的 strength 挤掉另一类单个短信号。

### 6.2 Evidence unit 与 dependency component

布局 evidence unit 至少由以下字段稳定定义：

```text
(file, sheet, panel, orientation, entity span, replicate-group span)
```

“不同 evidence unit”只表示物理布局不同，不自动等于生物学独立。语义独立性只有在
图例、Methods 或明确表头支持时，才由 Agent 在 verdict 中说明，不改变数学计票。

以下任一关系成立的 seeds 必须合并到同一个 dependency component：

- 共享 residual `source_event_id`；
- 同一或等价坐标/X 轴本身参与两个 finding；
- 有证据来自同一固定分母或同一 count/n；
- 来自同一归一化锚点或同一公式上游；
- 同一物理数值关系被多个 detector、方向或重叠窗口重复表达。

“同一 sheet/panel”本身不构成 dependency edge。每条 edge 保存 typed reason。
`dependency_component_id` 和规则版本由工具生成；Agent 只读。

### 6.3 弱 seed 聚合下限

同一 dependency component 无论有多少 detector 或 supporting kind，最多贡献一票。
单个 `standalone_review` 或已校准的 `direct_confirm` component 可以申请展开。
仅依靠 medium/low seed 申请展开必须满足：

- 至少两个 component，且 primary family 不同、至少一个为 medium；或
- 同一 primary family 至少三个 component，覆盖至少三个 evidence units；或
- 全部为 low 时，至少三个 component且覆盖至少两个 primary families。

共享轴、固定分母、锚点、公式来源或同一关系形成的相关 seeds 不能相互凑足门槛。
Packet 同时输出 component 数、evidence-unit 数、primary family 和 supporting families。
这些是版本化、确定性的保守下限；Agent 不得放宽。

Phase 2 至少用 physical footprint、source-event 和重叠 relation 建立保守的
`provisional_dependency_component`，保证同一物理证据最多展开一次。Phase 3 再加入
共享轴、分母、锚点和公式来源等语义 edge，并重新生成 final component。§6.3 只约束
多个弱 seed 聚合；它不阻止单个 `standalone_review` 在全局预算内执行一次有上限的
confirm/context recipe。

在相关 family 的 final semantic dependency rules 尚未启用时，所有 medium/low seed
必须设置 `aggregation_eligible=false`：provisional component 只能去重，不能证明
多个弱 seed 相互独立，也不能触发上述聚合门。此时只有单个 `standalone_review`
可以执行 bounded expansion；final dependency 完成后才可打开 medium/low 聚合。

## 7. 三个 canonical signal

三个 abstraction 是 workflow 的 canonical owner，不是和旧 detector 平行运行的三套
扫描器。共享 core 支持任意 span，short ownership 只覆盖 3–11：

```text
iter_numeric_vectors()
match_pair_relation()
collect_exact_collision_events()
evaluate_context()
finding_footprint()
```

旧 detector 继续提供 public kind，但逐步改为调用共享 core，再由 adapter 施加旧 gate。
同一 scope 不得先运行旧 matcher，再复制运行一份新 matcher。

共享 core 必须显式输出：

```text
match_mode =
  canonical_exact | strict_numeric | rounded | approximate | partial
```

`canonical_exact` 要求两侧版本化 canonical decimal/token 与 missing mask 逐位置完全
相同，不使用 rtol 或先行 rounding。`strict_numeric` 表示 canonical token 不同，但
通过注册的 tight numeric tolerance；`rounded` 表示只在明确的 decimal/sig-fig
rounding rule 后相同。

Owner 根据物理 evidence、span 和 `match_mode` 决定，不能根据 legacy kind 名决定。
现有某些 `identical_*` 使用数值容差，某些 recurring vector 使用 round-6 signature；
只有 `canonical_exact` 的完整有序向量进入 `repeated_short_vector`。其余模式保留 legacy
兼容输出，并按 pair/rounded relation 语义确定 workflow owner。

### 7.1 `short_pair_relation`

接收 span 3–11 的对齐行对或列对：

- partial identity；
- approximate identity；
- constant ratio；
- constant offset；
- shared high-precision fractional tail；
- `k/n` 局部支持；
- 一个缺失、例外或可能的量级录入不一致。

完整严格恒等可由 matcher 识别，但 canonical owner 是
`repeated_short_vector`。只有部分恒等、近似恒等或带例外的 identity 归
`short_pair_relation`。

至少输出：

```text
relation_type
axis
span_length
n_observed_pairs
n_match
matched / missing / outlier indices
parameter
residual summary
precision summary
structural_gate_passed
source_finding_refs
```

`span_length` 包含物理 span 中的 missing 位置；coverage 使用
`n_match / n_observed_pairs`。两个支持点永远不能进入 registry promotion。

P2 的 `standalone_review` 结构下限是：至少 3 个 observed match、coverage 至少 75%、
最多一个 missing/outlier，并通过 relation-specific 的信息量和非平凡参数 gate。
完整 3 点关系必须 3/3 严格成立。局部比例、近似 identity 和 shared-tail 可以有更强
的 mode-specific 下限，但不能比本下限更宽；这些规则进入 routing-rule version，
只控制是否值得展开，不声称已通过统计校准。

量级候选只能命名为 `possible_scale_entry_inconsistency`。工具不得改写原值，只能
报告某个 `10^k` 候选是否恢复关系。

### 7.2 `repeated_short_vector`

接收长度 3–11、顺序敏感的严格完整行向量或列向量：

- 同一等价类一次输出整个 `occurrences[]`；
- 支持同块、跨块、跨 sheet 和转置；
- signature 包含版本化 numeric token 与 missing mask；
- 单侧 missing、单侧例外或仅近似相等转给 `short_pair_relation`；
- 归一化锚点只剔除对应位置，不因存在一个基线值而丢弃其余非基线匹配。

严格完整 vector identity 先于 collision family 认领相关 cells，保证一个物理证据只有
一个 primary finding。

同一 occurrence pair 命中多个嵌套窗口时，只输出不能再向两侧扩展的 maximal span
作为 primary；其严格子窗口折叠为 alias/supporting evidence。不得让一个长度 8 的
重复产生长度 3–7 的多个 primary findings。

P2 的 `standalone_review` 结构下限是以下之一：一个长度至少 4 的高信息向量完整
重复；同一 panel 出现至少两种不同的重复三元组；或一个高信息三元组跨明确不同的
block/sheet scope 重复。该门只分配 Agent 展开预算，不产生 detector high。

### 7.3 `recurrent_group_collision`

从完整坐标的 exact-collision raw event 流中识别多个布局互异实体中的组内碰撞。
Collector 必须在现有 summary gate、severity 截断和 top-K 之前运行，并至少保存：

```text
source_event_id
absolute cell
canonical decimal and display precision
collision-key version
panel / orientation / header path
evidence unit / replicate group
measurement-family key and version
formula-source refs
coverage completeness
```

聚合顺序：

1. 去除重叠 block 对同一 source event 的重复表达；
2. 标记确定性上下文解释的 events；
3. 在 residual evidence 上重算 owner；
4. 由 `repeated_short_vector` 先认领完整有序向量；
5. 从 collision ledger 扣除这些 exact-vector cells；
6. 对剩余 events 按预先确定的 measurement family 和 evidence unit 聚合。

Measurement family 必须在查看哪些值发生碰撞前由布局、单位、表头、数量级、
显示精度和 replicate layout 确定。分组不唯一时只保留
`grouping_unknown=true` 的 experimental candidate。

本产品规范只要求输出 exact sufficient statistics、support、coverage 和结构上下文。
碰撞概率模型、occupancy oracle 和自动 high 条件全部由 companion 定义。
P2 的 `standalone_review` 至少要求 residual collisions 覆盖 3 个 dependency
components，并包含 3 个不同的非边界高精度 collision values；否则只作为
medium/low seed 参与 §6.3 的保守聚合。

## 8. Legacy adapter、所有权和 floor 解耦

### 8.1 Ownership 常量

```text
SHORT_CANONICAL_MIN_SPAN = 3
SHORT_CANONICAL_MAX_SPAN = 11
```

这是 schema-versioned canonical ownership boundary，不是 detector activation floor，
也不从环境变量或 `_ROW_REL_MIN_COLS` 推导。

```text
span <= SHORT_CANONICAL_MAX_SPAN  → short canonical ownership
span >  SHORT_CANONICAL_MAX_SPAN  → legacy long ownership
```

`_ROW_REL_MIN_COLS` 只表达 legacy detector 是否激活。当前代码若用同一常量同时控制
legacy activation 和 short-path 上界，Phase 2 必须先拆开。以后即使 legacy floor
从 12 降到 5，span 5–11 仍由 short canonical owner 负责；legacy finding 只能成为
supporting ref，span 12+ 仍归 long owner。

### 8.2 明确的 canonical owner

| 物理证据 | Primary canonical owner | Legacy / supporting 处理 |
|---|---|---|
| span 3–11 `canonical_exact` 完整向量恒等 | `repeated_short_vector` | legacy identity kind 仅在语义相符时 supporting |
| span 3–11 部分或近似 identity | `short_pair_relation` | relation/equal-pair kind 仅 supporting |
| span 3–11 aligned pair 的 strict-numeric/rounded identity | `short_pair_relation` | 不从 `identical_*` kind 名升级为 exact vector |
| span 3–11 多 occurrence rounded vector recurrence | 既有 rounded/recurring owner | 不进入 `repeated_short_vector`，不生成 pair owner |
| span 3–11 ratio、offset、shared-tail | `short_pair_relation` | 对应 legacy kind 仅 supporting |
| `many_equal_pairs` 且 physical span 3–11 | `short_pair_relation` | 不得仅凭 kind 名或 jointly-finite `n` 推断 strict identity/span |
| `many_equal_pairs` 且 physical span 12+ | legacy long/equal-pair owner | matcher 可共享，不生成 short primary |
| 完整向量已经认领的 exact cells | `repeated_short_vector` | collision ledger 扣除 |
| 剩余的分组 recurring exact collision | `recurrent_group_collision` | block/dispersed kind 仅 supporting |
| 其他 block-level duplication | `block_value_duplication` | 保留现有 owner |
| span 12+ 的关系或完整向量 | legacy long owner | matcher 可共享，所有权不变 |
| `sum_constant`、`exact_linear`、分布/位数类 | 既有 owner | 不纳入三个 abstraction |

`many_equal_pairs` 的短 span 归属基于现有语义：它可以只在部分行近似相等，并排除
严格全列恒等；因此归 pair relation，而不是 repeated vector。现有字段 `n` 是两列
jointly-finite 的数量，不是 physical span；adapter 必须从绝对 footprint/missing mask
计算 `span_length` 再分 owner。若未来该 legacy kind 的语义改变，必须先改 adapter
parity fixture 和 ownership version。

### 8.3 Legacy migration matrix

| Shared core | 既有入口 | 迁移契约 |
|---|---|---|
| pair matcher | row/column relation、equal-pair、shared-fraction、short scaled reuse | core 计算 support mask/容差；adapter 保留旧 kind、字段和 gate |
| vector iterator | recurring row/column vector、short-row reuse、within-row segment | workflow 输出一个 canonical equivalence class；旧输出只作兼容 |
| collision event collector | block duplication、dispersed repeats、exact repeat 来源 | 共享 raw event extraction；各 consumer 保留自己的 summary gate |

`detect_within_row_shared_fraction`、block-to-block reuse 等具有不同拓扑的 detector 只复用
局部 helper，不被强制改成新 owner。

迁移顺序固定为：

```text
shared core + parity tests
→ owner/floor constants split
→ legacy adapter
→ workflow canonical output
```

不能先降低旧 floor，再依赖尚未完成的 dedup 修复双 finding。

## 9. 确定性上下文和 residual owner

必须支持以下 context class：

| Context | 产品处置 |
|---|---|
| shared axis / coordinate | 坐标序列本身不作为独立进展关系；依赖 finding 合并 |
| cumulative / sorted boundary | 标记由累计、堆叠或排序定义的边界 |
| fixed denominator / count grid | 记录可还原的分母、count/n 与量化步长 |
| normalization anchor | 只解释锚点 cells，不整块清除非锚点证据 |
| LOD / boundary / saturation | 标记检测限、0/1 边界或截断平台 |
| technical repeats | 仅在表头、图例、Methods 或公式来源有证据时标记 |
| low-cardinality / quantization | 记录显示精度和有效格点，不把舍入碰撞当连续值 |
| formula provenance | 来源格式/reader 可提供公式时追踪模板、共同上游和派生列；否则 `unknown` |

上下文规则必须：

1. 复用或扩展既有 `_prefilter.py` / `_profiles.py` 语义，不能复制两套互不一致的逻辑；
2. 输出 typed context finding 和作用 cell/event 范围；
3. 原始 numeric match 始终可追溯；
4. 只从 residual evidence 排除被解释的 cell/event；
5. 排除后重新运行 owner 和 dependency 计算；
6. 结果与 detector 遍历顺序无关；
7. 无充分上下文时保留 `unknown`，交给 Agent，不假定良性或异常解释。

本规范不要求所有文件格式都重建完整公式依赖图。Legacy/cached-value reader 不暴露
公式时，formula provenance 是 unavailable/unknown，不得据此清除 evidence，也不得
阻塞其他 context 或 workflow 发布。

实施计划必须先列出现有 prefilter/profile 能力矩阵，再决定“复用、提升为 shared
helper 或新增”。禁止同一个 fixed-denominator 或 normalization 规则分别维护在
workflow 与 legacy 两处。

## 10. Calibration fail-closed 接口

产品层只消费统计 companion 产生的版本化 registry，不定义其概率模型。
Fail-closed reader、空 registry 行为、版本校验和 synthetic enabled fixture 由本产品
规范拥有；companion 只拥有 calibration artifact、entry 内容和 enable/revoke 决策。

Runtime 至少暴露：

```text
calibration_unit = canonical family + registered mode
calibration_slot = calibration_unit + applicability class
calibration_id
registry_status = missing | disabled | enabled | revoked
calibration_runtime_class / version
version_match
promotion_eligible
```

新的 detector 默认 high 必须满足：

```text
structural eligibility
and context/support eligibility
and coverage complete
and registry_status == enabled
and current runtime is allowed by calibration_runtime_class
and version_match
and promotion_eligible
```

任何字段缺失、registry 不存在、版本不一致、coverage 不完整或 entry 被撤销，都
fail closed：evidence/feature 状态保持原值，routing tier 不得成为 `direct_confirm`，
detector severity 不得因该 calibration 升为 high。Agent 不能覆盖此门。

映射唯一为：

| Registry/runtime 结果 | Evidence/feature status | Routing tier | Detector severity |
|---|---|---|---|
| missing/disabled/revoked/version mismatch | 不改变 | `standalone_review` / medium / low | 不因新校准升级 |
| enabled，但 `promotion_eligible=false` | 不改变 | 不得 `direct_confirm` | 不因新校准升级 |
| enabled 且 `promotion_eligible=true` | 不改变 | 必须 `direct_confirm` | 该 canonical finding 必须升 high |
| 任意状态下的 Agent verdict | 不改变 | 不改变 | 不改变 |

v1 不提供可选 `promotion_action`：同一个 true 同时授权 canonical finding 的
`routing_tier=direct_confirm` 和 detector `severity=high`。Legacy supporting refs
不因该映射各自再升一次 high。若未来需要 `direct_confirm_only`，必须新增 schema 和
mapping version，不能复用 v1 calibration ID。

Workflow 必须能在 registry 为空时正常到达 COMPLETE。某个 calibration slot 未通过，
不阻塞其他 slot，也不阻塞 Agent 默认工作流发布。

## 11. 报告链一致性

### 11.1 Finding registry

HTML、Markdown、packet 和 adjudicated report 必须使用统一、显式排序的 finding
registry。当前 `block_dups` 已写入 per-block scan 结构并进入 Markdown 汇总，但
`_html.py::_PER_BLOCK_GROUPS` 未包含它，因此默认 HTML 会遗漏。Phase 0 先独立修复
这个现存报告链缺口。

Registry 至少定义：

```text
storage path
canonical family
display group
sort key
packet inclusion
HTML inclusion
Markdown inclusion
supporting-only rule
```

新增 storage key 没有 registry entry 时测试失败，不能静默从报告消失。

### 11.2 统一最终报告

最终报告合并：

- 原始 `scan.json`；
- `expanded_findings.json`；
- Agent `verdict.json`；
- calibration slot、registry 状态与 version match；
- coverage、预算耗尽和未展开候选；
- raw finding 与 canonical finding 的 alias/supporting refs。

报告必须区分：

```text
detector severity
evidence stage / feature status
registry status
Agent adjudication
```

不得把 Agent 的复核优先级显示成 detector 自动 high。

## 12. 预算、确定性和数值序列化

### 12.1 预算

预算至少覆盖：

- 候选 cluster 数；
- evidence cell 数；
- expansion round 和 route step；
- 上下文请求数、加载 bytes 和渲染 pixels；
- 单文件大小、总 cells 和证据行列上限。

截断、采样或缓存不完整必须显式进入 `coverage`。不完整 scope 可以进入 Agent 复核，
但不能进入 registry promotion。

### 12.2 确定性边界

保证确定性的层：

- enumeration、matcher、context、owner、dependency 和排序；
- 给定 request 的 expansion；
- artifact schema、stable IDs、digests 和报告渲染；
- 给定 verdict 的 finalize。

不保证确定性的层：

- Agent 如何从允许动作中选择；
- Agent 的自由文本。

因此 end-to-end replay 测试必须固定/mock request 与 verdict。

### 12.3 派生浮点最小契约

为避免跨平台 golden 抖动，产品 artifact 对派生浮点统一使用
`numeric_canonicalization_version`：

- 只规范化派生统计量，不改源单元格、原始 token 或证据值；
- v1 使用 12 位有效数字，不使用固定小数位；
- `-0` 规范为 `0`，拒绝 NaN 和无穷值；
- 同一 canonical 值用于排序、阈值、stable ID/cache 和 JSON/golden；
- 概率、经验计数和数值尾部的更完整规则由 companion 定义。

若实现发现既有 public artifact 不能立即改为 canonical float，必须以 schema-versioned
新字段并存迁移，不能静默改变旧字段。

## 13. 产品测试矩阵

全部 fixture 使用合成数据，不提交真实论文工作簿、DOI 或判断。

### 13.1 Canonical signal

| 场景 | 必须断言 |
|---|---|
| 3/3、4/4、5/6 relation | span、support mask、missing/outlier、转置一致 |
| ratio/offset/shared-tail/partial identity | relation type、参数、精度和 residual |
| 量级候选 | 只报告候选，不改原值 |
| 长度 3–11 vector | equivalence class、occurrences、转置和跨 block |
| group collision | event 去重、预先分 family、完整 coverage |
| 只有两个 numeric rows 的 sheet | 不被 block `min_rows=3` 前置丢弃 |

### 13.2 Ownership 和 legacy parity

至少参数化 span 3–13，并在 legacy floor=5、12 和大于 12 的配置运行：

1. strict full identity，span 3–11 → `repeated_short_vector`；
2. 6/8 partial identity → `short_pair_relation`；
3. 8/8 仅在 loose tolerance 相等、但 strict identity 不成立 →
   `short_pair_relation`；
4. `many_equal_pairs` physical span 3–11 → pair owner，legacy kind 只 supporting；
5. physical span 12–13（包括 missing 使 `n < span_length`）→ legacy long owner；
6. vector cells 不再形成第二个 collision primary finding；
7. missing/exception 会从 vector owner 转给 pair owner；
8. legacy near-equal `identical_*` 与 round-6 recurring vector 不被误标为
   `canonical_exact`；
9. 长向量只输出 maximal primary，嵌套短窗口只作 supporting；
10. adapter 对旧 kind、字段、顺序、gate、evidence 和 profile 投影保持 parity；
11. floor 改变时 workflow ownership 不出现空档或两个 primary finding。

Legacy floor 大于 12 时，裸 CLI 是否激活仍由独立 hard-threshold audit 决定；
workflow 的 generic enumeration 仍按 physical span 把 12+ 交给 long owner，不能把
activation floor 当 ownership 边界。

### 13.3 Context 和 dependency

- shared-axis、固定分母、归一化锚点、累计边界、LOD、技术重复、量化和公式 fixture；
- context 只影响命中的 cells/events；
- residual owner 重算与输入顺序无关；
- 同一 dependency component 最多一票；
- shared-axis 等相关 signals 不能聚合触发展开；
- final semantic dependency 前，provisional components 的 medium/low
  `aggregation_eligible=false`；
- 两个真正不相交 component 可以按 §6.3 规则触发展开；
- grouping unknown 只能进入 experimental。

### 13.4 Workflow 和报告

- 非法状态转换、未知 recipe、超预算和第三轮 expansion 被拒绝；
- 无 registry 时，单个 `standalone_review` 完成 bounded route/expand/adjudicate；
- 不可比 family/mode 用分层 round-robin 进入 packet，不能全局比较 strength；
- 固定 request/verdict 可重放，Agent 文本不作重复采样断言；
- registry 为空时 workflow 仍可 COMPLETE；
- `experimental` finding 能进入 packet、expanded artifact 和最终报告；
- report 区分 detector severity、evidence/feature status、registry 与 Agent verdict；
- stable feature + missing/disabled registry 的组合可序列化且不升级；
- `block_dups` 和未来新增 group 均受 registry 完整性测试保护；
- 裸 CLI 的默认 detector golden 在 Phase 2 前后不增加 high。

### 13.5 Companion 接口

- 未登记、disabled/revoked、runtime class/version 不符、coverage 不完整一律 fail closed；
- enabled entry 只能影响其注册 applicability；
- `promotion_eligible=true` 同时映射 canonical direct_confirm/high，supporting refs 不重复升级；
- canonical serializer/reader fixture 在支持的产品 runtime 稳定；校准重建使用
  companion 冻结的 authoritative environment；
- 统计 oracle、full-scan selection、FP/power 和 registry activation 测试引用 companion，
  不在产品测试中复制。

## 14. PR 纪律和分期

### 14.1 所有 PR 的共同门禁

- 一个 PR 不跨 Phase；Phase 大时继续拆成更小 PR；
- 每个 PR 从前一 PR 已合并的主干开始；
- 每个 PR 只引入一个可独立验证的行为变化；
- 全量 pytest 和 golden 通过；
- finding/report 数量差异有机器可读或书面 diff；
- 新 family 在校准启用前保持关闭或 experimental；
- schema 变化兼容旧 artifact，或显式拒绝不兼容版本；
- 不提交真实论文数据、DOI、判定或敏感本地产物；
- 不顺带降低 hard-threshold audit 的 activation floor；
- detector 数学、calibration registry enable 和 Agent 默认切换分别审查。

### 14.2 Phase 0：报告链缺口

拆成两个独立 PR：

- **P0a**：只修复 `block_dups` 默认 HTML 可见性并添加定向回归测试；
- **P0b**：建立统一 finding registry，再添加 HTML、Markdown、packet 完整性测试。

退出条件：

- detector 结论不变；
- 只有预期报告 fixture 变化；
- 全量测试和 golden 通过。

### 14.3 Phase 1：opt-in workflow 骨架

交付：

- 状态机、最小 artifact envelope 和 lineage；
- route/expand/finalize/status；
- 在 cap/profile 前冻结 workflow raw stream，并从既有 finding 构造 seed；
- expanded finding 与 verdict 的统一报告；
- 固定 request/verdict replay；
- 明确预算和 coverage；
- 产品侧 fail-closed registry reader、空 registry 和 synthetic enabled compatibility fixture。

当前 `main()` 对 `fetch`、`report` 使用手写 `sys.argv[1]` 分派，而不是完整
subparser 树。Phase 1 必须在实施计划中明确是延续这一兼容模式，还是用独立机械 PR
统一迁移到 `add_subparsers`；不能只让 `workflow` 使用第三种混合分派。

退出条件：

- 裸 CLI 默认行为不变；
- detector golden 零变化；
- workflow 仅 opt-in；
- workflow seed 不随 CLI profile 改写或 packet cap 消失；
- 不要求任何 enabled calibration。

### 14.4 Phase 2：短信号 experimental 价值出口

Phase 2 按纵向价值切片，不等待三个 family 同时完成：

1. **P2a pair**：shared pair core、legacy parity、owner/floor 解耦、Agent/report 端到端；
2. **P2b vector**：vector iterator、maximal containment、legacy parity、Agent/report；
3. **P2c collision**：raw event collector、vector-cell 扣除、分组、Agent/report。

每个切片都是独立 PR 序列；P2a 通过即可交付 pair 的 experimental 用户价值，不等待
P2b/P2c。P2c 可以在 P3 的 final dependency 完成前先输出保守 candidate，但其
`standalone_review` 必须等所需 dependency 语义可用。

用户价值出口：

> 在 opt-in Agent workflow 中，3–4 个重复、5/6 局部关系和短向量复用可以进入
> 候选、执行确定性展开并出现在最终报告中。它们不再在 Agent 之前消失，但不会在
> 未校准时获得 detector 默认 high。

宽召回的 experimental candidates 只写入 workflow namespace 的 packet/expanded
artifacts；普通裸 CLI 的默认 `scan.json` schema、finding 集合和 high 数量保持不变。
未来若要让裸 CLI 显示这些候选，必须另设显式 opt-in/schema 版本，不能静默改变旧
消费者。

退出条件：

- span 3–13 在 legacy floor=5、12 和大于 12 时均无 workflow owner 空档、无双 primary；
- `many_equal_pairs` short/long owner、missing span 和 adapter parity 有测试；
- 本切片的 experimental finding 可完整走通 workflow；
- 裸 CLI 默认 high 数量不增加。

### 14.5 Phase 3：context、dependency 和 final owner

交付：

- 按 family 需要逐步交付 context taxonomy 与既有 prefilter/profile 的共享 helper；
- explained event 范围与 residual owner；
- dependency component 单次计票；
- raw evidence 和 supporting ref 可追溯；
- 相关 seed 聚合保护。

退出条件：

- shared-axis、固定分母、归一化等合成反例不会被重复聚合；
- context removal 只影响明确范围；
- residual owner 与遍历顺序无关；
- 未校准 slot 仍不产生默认 high。

### 14.6 Phase 4：shadow 与 Agent 默认入口

交付：

- 先以“既有 findings + 已完成 family feature flags”在本地 corpus shadow，比较候选量、
  展开率、coverage、token 和运行时间；
- 通过 shadow 的最小 workflow 即可让 skill 默认切换，裸 CLI 仍为一次性入口；
- 后续 family/context 分别 shadow 后逐个打开 feature flag；
- registry 为空和部分 slot enabled 两种路径均可运行；
- per-slot revoke/rollback 和 workflow fallback。

退出条件：

- P0、P1 以及本次默认启用 family 所需的 P2/P3 slice 已通过；
- 当前 feature set 的 shadow 指标满足预注册产品 go/no-go；
- Agent 不需要选择 profile 或 calibration mode；
- signal 明确显示 evidence stage、feature status 和 registry status；stable feature
  可以保持 registry missing/disabled；
- 默认切换有回滚路径。

统计 companion 的 C0、纵向 slot 校准和启用 PR 可与产品 Phase 独立推进。
某个 calibration slot 未通过，不能阻塞 Phase 2 价值出口或 Phase 4 Agent 默认入口。

## 15. 产品验收

本规范完成的必要条件：

1. Agent workflow 与裸 CLI 的入口和语义不混淆；
2. span 3–11 连续覆盖，3–4 个重复和局部关系可进入 Agent；
3. `many_equal_pairs`、strict identity、long owner 和 collision owner 无真空或重叠；
4. canonical ownership 与 legacy activation floor 明确解耦；
5. 相关 seed 不会因 shared axis、分母、锚点、公式或重复 detector 被多次计票；
6. Agent 只控制预算和 verdict，不控制数学结果；
7. 无 enabled calibration 时 workflow 仍能完成和报告；
8. 新短信号在未校准时不会自动赋 detector high；
9. 固定 request/verdict 的确定性边界可测试；
10. 报告包含 expanded finding、coverage、registry 状态和 Agent adjudication；
11. 每个 Phase 可作为独立 PR 交付、回滚和复核；
12. 全部 detector、artifact、golden 和报告测试通过。

本规范获得书面批准后，先为 Phase 0 编写独立实施计划；统计 companion 另行批准、
另行计划，不与产品 Phase 合并实施。
