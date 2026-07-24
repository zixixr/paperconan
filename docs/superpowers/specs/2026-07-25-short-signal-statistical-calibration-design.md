# 统计校准 companion spec：短信号自动优先级

- 日期：2026-07-25
- 状态：待用户书面复核（v1，独立 companion）
- 依赖：
  [Agent 默认的分阶段短信号工作流](2026-07-25-adaptive-short-signal-workflow-design.md)
- 范围：`feature_status=experimental` 的 canonical 短信号获得 detector 自动优先级的统计门

> 本规范校准的是统计信号的自动优先级，不是对作者意图的判断。
> 未通过本规范不会隐藏候选；候选仍可由 Agent 展开、复核并作为待解释异常报告。

## 1. 目的和独立交付边界

产品主规范已经允许以下 canonical family 在 Agent workflow 中作为
experimental feature 和 review-ready evidence 出现：

- `short_pair_relation`
- `repeated_short_vector`
- `recurrent_group_collision`

本 companion 只回答一个问题：

> 某个已注册的 family + mode + applicability class，在什么统计和工程证据下，可以自动获得
> `direct_confirm` 或 detector 默认 `high` 的资格？

它不定义：

- matcher、向量 iterator 或 collision event collector；
- context reducer、canonical owner 或 dependency component；
- Agent 路由、预算、profile 或最终 verdict；
- 既有 FDR、GRIM/GRIMMER 或其他 detector 的追溯重写；
- 真实论文的最终解释。

产品 Phase 0–4 均可在没有任何 enabled calibration entry 时完成。某个 slot 校准失败，
只使该 feature 保持 experimental，不阻塞 Agent 深查、Agent 默认入口或其他 slot。

## 2. Fail-closed 启用模型

### 2.1 Unit 与最小启用 slot

```text
calibration_unit = canonical family + registered mode
calibration_slot = calibration_unit + applicability_class
```

示例：

```text
short_pair_relation:partial_identity
short_pair_relation:ratio
short_pair_relation:offset
short_pair_relation:shared_tail
repeated_short_vector:exact_ordered
recurrent_group_collision:exact_grouped
```

不同 mode 的 statistic 不要求可比，不能因为同属一个 canonical family 就强行放进
同一个异构 maxT。`calibration_unit` 用于组织共享 matcher/statistic；真正独立校准、
计入多重性预算和启用的最小单位是 `calibration_slot`。

同一 mode 的不同 precision、missingness、layout 或 dependence applicability class
各占一个 slot。v1 不构造跨 class 的 joint null；这避免一个 unit 的多个 class 分别
选择最有利结果，却只占一个多重性名额。

### 2.2 Registry 状态

每个 slot 的 registry entry 只能是：

```text
disabled | enabled | revoked
```

Runtime 只有在以下条件全部成立时，才可设置 `promotion_eligible=true`：

```text
entry.status == enabled
and all runtime versions exactly match
and current calibration_runtime_class is allowed
and applicability predicate passes
and structural/support/context gates pass
and eligible-universe coverage is complete
and scan_adjusted_tail < scope_alpha
```

缺 entry、状态 disabled/revoked、版本不符、输入字段缺失或 coverage 不完整，都必须
fail closed。Agent、profile 和 packet top-K 不能覆盖此规则。Runtime 统一输出
`registry_status = missing | disabled | enabled | revoked`；`experimental` 只用于
产品层 `feature_status`，不能作为 registry 状态。

## 3. 冻结的输入和输出接口

### 3.1 Runtime 输入

统计输入由产品主规范的确定性 Phase 3 core 产生，并在 profile、candidate strength、
packet top-K 和 Agent 选择之前固定。

每次扫描至少提供：

```text
source_manifest_digest
analysis_scope_id
eligible_universe_digest
coverage_complete
enumeration_version
matcher_version
context_version
dependency_version
ownership_version
numeric_canonicalization_version
calibration_scope_version
calibration_runtime_class / version
```

每个 candidate 至少提供：

```text
canonical_finding_id
calibration_unit
calibration_slot
scope / footprint
statistic and integer/exact sufficient statistics
support and applicability tags
dependency_component_refs
structural/context/support eligibility
source_finding_refs
```

以下字段禁止参与统计输入：

- Agent reason、decision 或 verdict；
- display profile 或既有 severity；
- packet 是否收录、排序名次或 token 预算；
- 事后人工标签；
- 本论文中已经观察到的最终解释。

### 3.2 Offline calibration manifest

每个 slot 的 calibration manifest 至少冻结：

```text
calibration_unit
calibration_slot
all runtime rule versions
statistic definition and weak ordering
applicability predicate
null-generator IDs and versions
alternative-generator IDs and versions
seed schedule
calibration-scope version
ordered slot ID list and digest
allowed calibration-runtime classes/versions
scope_alpha
FP confidence target
power target
adjustment method
numeric canonicalization version
```

真实论文和人工判定不得作为 calibration ground truth。它们只可用于不提交 git 的
shadow 观察，不能决定 null、阈值或 enable 状态。

### 3.3 Runtime 输出

候选的 calibration view 至少包含：

```text
calibration_unit
calibration_slot
calibration_id
registry_status
version_match
runtime_compatible
applicability_class
local_tail_diagnostic
slot_max_adjusted_tail
scan_adjusted_tail
scope_alpha
support_valid
coverage_complete
promotion_eligible
```

经验结果还必须保存 exact `extreme_count` 和 `trial_count`。Local tail 是诊断字段，
不能绕过 full-scan 和 scan-wide adjustment 单独触发升级。

## 4. 派生数值规范化

### 4.1 Canonical float v1

只规范化派生浮点统计量，不修改：

- 源单元格值；
- 原始 numeric token；
- Excel 显示值或公式；
- evidence 中为了复核而保存的原数值。

`numeric_canonicalization_version=1` 的规则：

1. 先拒绝 NaN 和无穷值，并令 support invalid；
2. 若字段是概率，先应用 `CANONICAL_PROBABILITY_EPS=1e-15`：只在
   `[-eps, 0)` 或 `(1, 1+eps]` 内夹到边界，超出即为计算错误；
3. 再对 binary float 执行 `format(value, ".12g")`；
4. 指数统一为小写 `e`，删除指数中的 `+` 和多余前导零；
5. `-0`、`-0.0` 和等价指数形式统一为 token `"0"`；
6. 新 calibration 浮点字段以 canonical decimal token 字符串持久化，不能转回 binary
   float 后再次序列化；
7. `scope_alpha` 也用相同 token，门槛通过 decimal token 的十进制值比较；
8. canonical token 用于 artifact、排序、阈值、缓存和任何包含该字段的 digest；
9. candidate identity 不依赖 p 值；calibration-result identity 包含 calibration 版本；
10. 比较使用严格 `< scope_alpha`，等于阈值不升级。

同一计算中的门槛判断和落盘值必须使用同一个 canonical token，不能用未规范值判定、
再把另一个值写入报告。

### 4.2 概率和尾部计算

- 经验尾概率保存整数 sufficient statistics，并用预注册公式推导；
- 默认经验尾使用 `(1 + extreme_count) / (1 + trial_count)`；
- analytic tail 优先使用 exact 方法、`sf`、`logsf` 或有证明的保守界；
- 不使用容易消减的 `1 - cdf`；
- 数值下溢且没有保守上界时，support invalid，不能把 `0` 当作升级证据；
- 多个适用 null 的结果取最保守尾概率或最保守置信上界，不能选择最小 p。

Helper/oracle 单元测试可以按明确容差比较；持久化 artifact/golden 必须精确比较
canonical JSON。Authoritative calibration 只在一个 pinned build environment 生成，
manifest 冻结 Python、依赖锁 digest、OS/架构和 PRNG 算法/版本，该 provenance 进入
calibration artifact/digest。

产品 CI 的 Ubuntu + Python 3.10–3.12 矩阵只验证固定 artifact 的 serializer、reader、
version gate 和 runtime 消费，不要求三种环境重新生成相同 artifact。若以后允许多个
calibration build environment，需定义不含 environment provenance 的
`mathematical_payload`，跨环境只比较该 payload；完整 artifact digest 仍可不同。
未列入 registry 的 runtime class 必须 fail closed。

## 5. Slot 内完整扫描校准

### 5.1 Statistic 契约

每个 calibration slot 只登记一个预注册 statistic、方向和 weak ordering。
Statistic 必须由 Phase 3 输出的 sufficient statistics 计算，不使用 Agent 或 top-K。
统计尾部对 ties 使用 inclusive `>=`。位置、ID 等 deterministic tie-break 只能用于
稳定展示，不得参与显著性，除非其交换性和 FP oracle 另行证明。

首批候选结构可以包括：

| Unit | Statistic 所需结构 |
|---|---|
| pair relation mode | support、information、residual、precision、missing/outlier mask |
| exact ordered vector | occurrence count、span、information、跨 scope 数 |
| exact grouped collision | residual collision pairs/components、occupancy sufficient stats |

具体公式和 applicability predicate 必须在对应 slot 的校准 PR 中冻结，不能在总规范
中一次性假定三个 family 使用同一尺度。

### 5.2 Eligible universe

统计保证的基本范围是：

```text
analysis_scope = 一个 source_manifest / 一次完整目录扫描
```

PaperConan 不能确认目录只对应一篇论文时，对外称 `scan-wide`，不称 paper-wide。
对 analysis scope 内每个 workbook、sheet 和 panel 必须运行完整 production 流程：

```text
enumeration
→ matcher/event collection
→ context
→ residual owner
→ dependency
→ all structurally eligible candidates for this slot
```

然后才允许：

```text
candidate strength
→ packet top-K
→ Agent selection
```

Eligible universe、源文件数量与布局、枚举方向、窗口、参数候选和适用 recipe 全部
进入 digest。任何文件遗漏、截断或采样导致 universe 不完整时，当前 candidate 设置
`coverage_complete=false`、`promotion_eligible=false`；静态 registry status 和
feature maturity 均不被单次扫描改写。

### 5.3 Scope-wide slot-max adjustment

每个 synthetic null replicate 必须生成与一个完整 source manifest 相同层级的
analysis scope，并对该 slot 在所有 workbook/sheet/panel 中的 eligible candidates
取最大 statistic：

```text
slot_max(null source manifest) =
  max statistic over every eligible candidate
  across all workbooks/sheets/panels in the analysis scope
```

Observed candidate 由 `slot_max` null 分布得到：

```text
p_slot_max
```

因此 slot 内的文件、sheet、窗口、方向、位置和候选参数选择均已计入。Agent 之后
只展开其中一个候选，不会改变 `p_slot_max`。

若一个 slot 有多个预注册 null model，分别计算 slot-max tail，并使用
最保守结果。不得看见 observed 结果后选择更宽松的 model 或权重。

Synthetic generator 若不能复现 observed source manifest 的文件数、sheet 数、布局和
枚举规模，必须使用对这些维度单调保守的 envelope；没有验证过的匹配或 envelope 时
当前 candidate 设置 `support_valid=false`、`promotion_eligible=false`。即使 registry
entry 全局 enabled，也不能在不适用的单次扫描中升级；entry 状态本身保持 enabled。

## 6. 跨 slot 的 scan-wide 控制

v1 不实现异构 global maxT。它采用固定 scope budget 的 Bonferroni：

```text
p_scan = min(1, K_scope * p_slot_max)
```

其中：

- calibration-scope manifest 冻结有序 `slot_ids[]` 及其 digest；
- `K_scope = len(slot_ids)`，不能只保存一个脱离列表的整数；
- disabled slot 也计入 `K_scope`；
- 新增 family、mode 或 applicability class 必须提升 `calibration_scope_version`
  并重新验证相关 entries；
- 如果 v1 恰有三个 slot，则 `K_scope=3`；规范不把 3 写死为长期常量；
- 可以登记更保守的解析 union bound，不能临时采用更宽松 adjustment；
- candidate universe 或 Agent 展开子集不能改变 `K_scope`。

这使每个 slot 可以独立启用，同时避免维护一套跨异构 statistic 的统一研究型 maxT。
未来若替换为 Holm、joint maxT 或其他方法，必须新建 adjustment version、重跑 oracle、
FP/power 和 shadow，不得原位改变既有 calibration ID。

## 7. Null、oracle 和 applicability

### 7.1 Null applicability

Applicability class 必须在观察该 candidate 的极端程度前，由布局/context tags 决定。
Null 至少保留该 mode 声称支持的：

- display precision 和 numeric grid；
- magnitude band；
- missing mask；
- pair/serial correlation；
- unit random effect 或 shared drift；
- replicate-group layout；
- dependency component 结构；
- workbook/sheet/panel 的枚举规模。

如果需要从当前 scan 拟合复杂 null，v1 默认保持 disabled。第一批 enabled slot
优先选择有 exact/analytic 保守保证，或能由外部合成生成器充分校准的 applicability
class，不在首次交付中引入 cross-fit 研究系统。

经验公式 `(1+e)/(1+n)` 只有在 observed scope max 与 null-replicate scope max 在冻结
条件下可交换时有效。若 null 参数、layout bucket 或保守 envelope 从当前 source
manifest 拟合，必须提供独立 split、条件化/交换性证明或保守上界；否则 fail closed。

### 7.2 独立 oracle

每个 slot 的校准实现必须有独立验证路径：

- 小域使用 brute-force 或 exact oracle；
- 生产近似对 oracle 验证保守性、单调性和 threshold neighborhood；
- randomized tests 使用固定 seed schedule，但断言统计性质而非某个 BLAS 尾数；
- 多个 null 同时适用时断言取最保守结果；
- null 不适用、背景不足或分组不唯一时断言 fail closed。
- empirical `extreme_count` 使用 inclusive `null_max >= observed`，并覆盖 ties。

Oracle 不得调用被测试的同一个 production helper 来生成“期望答案”。

## 8. FP、power 和启用门

### 8.1 False-positive gate

每个 enabled calibration slot 必须满足：

- exact/analytic guarantee 或独立 empirical calibration；
- 完整 production full-scan 流程下的 FP 评估；
- `scope_alpha <= 1e-4`；
- 预注册 FP 目标及其置信上界；
- scope-wide slot-max 与 `K_scope` adjustment 已应用；
- calibration 和 evaluation split 独立，或有等价保守保证。

若有 exact/analytic 保守界，Monte Carlo 只需验证完整 pipeline 和实现错误，不强制
机械运行固定的 30,000 次。若主要依赖 empirical calibration，trial 数、importance
sampling 或 exact enumeration 规模必须由目标 FP 率和置信上界反推。

特别地，30,000 次普通 null trial 不能证明 `1e-6` 量级尾部；需要 exact 方法、
有验证的 rare-event/importance sampling 或数量级相符的试验规模。门槛不能只因
golden 通过就视为已校准。

### 8.2 Power gate

每个 calibration slot 预注册：

```text
alternative generator
effect range
evaluation split
minimum power target
confidence lower bound target
```

Power 必须在独立 evaluation split 上评估。未达标的 slot 保持 disabled，
不拖住同一 unit 中其他已验证 slot。

测试替代模式使用通用结构描述，例如：

- 共享高精度尾且整数部分变化；
- 局部固定比例并含一个例外；
- 多组短向量完整复用；
- 多个实体中的 residual exact group collision。

不使用真实论文名称、DOI 或原始值作为 fixture 标签。

### 8.3 Enable、失效和撤销

Registry enable PR 必须是独立 PR，只改变经过审查的 slot entry，不顺带改变 matcher、
context、dependency 或 statistic。

以下任一版本变化使 entry 在 runtime `version_match=false` 并 fail closed；registry
历史状态本身不被静默改写：

```text
enumeration
matcher/statistic
context
ownership
dependency
numeric canonicalization
null generator
calibration scope / slot list / K_scope
```

发现 oracle、coverage、数值稳定性或适用范围问题时，可将 entry 标记 `revoked`。
撤销不删除历史 artifact；旧报告保留当时 calibration ID 和状态，新运行 fail closed。

## 9. Calibration artifact

每个 slot 的可审查 summary 至少包含：

```text
calibration_id and all version digests
calibration unit / slot / applicability predicate
statistic and adjustment method
null/alternative generator versions
seed schedule
exact-oracle coverage
trial counts or analytic guarantee
FP estimate and confidence upper bound
power estimate and confidence lower bound
scope alpha
ordered slot list digest / K_scope
known limitations
status recommendation
```

提交 git 的内容只包括合成 generator、manifest、小型 oracle fixture 和 summary。
不提交大规模 trial 明细、真实论文数据、DOI、人工判定或本地 shadow corpus。

Calibration result 必须可由固定 manifest 和支持环境重建。若不同平台只在未规范化
中间浮点上有微小差异，最终 canonical artifact 必须一致；否则 entry 不能 enabled。

## 10. 测试矩阵

### 10.1 Numeric canonicalization

- 12 位有效数字覆盖极小概率、大 statistic、阈值邻域和负零；
- NaN、inf、明显越界概率被拒绝；
- canonical token 同时驱动比较和 JSON；
- `p == alpha` 不升级；
- exact count 可重新推导经验 p；
- artifact golden 跨支持 Python 版本稳定。

### 10.2 Selection control

- source-manifest-wide eligible universe 在 top-K/Agent 前冻结；
- 改变 packet size 或 Agent request 不改变 `p_slot_max` / `p_scan`；
- 增加另一个文件或更极端 null candidate 会按预期改变 slot max；
- incomplete coverage 必须 fail closed；
- disabled slot 仍计入 `K_scope`；
- `K_scope` 与有序 slot 列表长度一致；
- scope version 改变使旧 entry 失效。

### 10.3 Oracle 和统计门

- 小域 brute-force 与生产结果一致或生产界更保守；
- statistic/tail 单调；
- threshold 两侧行为正确；
- 多 null 取最保守；
- FP 置信上界和 power 置信下界按预注册公式计算；
- empirical trial 规模不足时不能 enable；
- 每个 enabled slot 都有独立 power 证据。

### 10.4 Runtime registry

- missing/disabled/revoked/version-mismatch 均 fail closed；
- runtime class/version 不在 allowlist 时 fail closed；
- enabled entry 只影响自己的 slot；
- 单次 coverage/support 不足只令 promotion false，不改写 registry/feature 状态；
- Agent verdict 不能改变 promotion；
- registry 为空时产品 workflow 正常 COMPLETE；
- calibration artifact、runtime output 和报告使用同一 calibration ID。

## 11. 独立 PR 序列

每个 PR 全量 pytest/golden 通过，并提供行为或 artifact diff。

### C0：接口和 canonical serializer

- numeric canonicalization helper；
- calibration manifest/registry schema；
- 产品 reader 使用的 compatibility fixture；
- 所有 slot 仍 disabled，feature 保持 experimental；
- 零 detector high 行为变化。

若产品 Phase 已先提供 canonical serializer，C0 必须复用并补充概率规则，不得实现
第二套 formatter。

### C1：第一个纵向 slot

不先造覆盖全部 family 的通用研究框架。选择最窄、最有保守 null 的 slot：

1. **C1a**：冻结该 slot 的 statistic、applicability 和合成 generator；
2. **C1b**：只为该 slot 打通 source-manifest-wide max、scope adjustment 和独立
   oracle，状态仍 disabled；
3. **C1c**：FP/power 门通过后，用独立 PR enable 该 slot。

第二个 slot 出现后，才从两个已工作的纵向切片提炼共享 harness/helper。

### C2+：逐 slot 校准

后续每个 slot 至少拆成两个 PR：

1. **Calibration PR**：实现 statistic/null/oracle，提交 summary，状态仍 disabled；
2. **Enable PR**：只有门槛通过后，单独加入 enabled slot entry。

建议顺序：

```text
short_pair_relation 的一个可保守校准 mode
→ repeated_short_vector 的一个 mode
→ recurrent_group_collision 的一个 mode
→ 其他 mode 逐个扩展
```

不能在同一 PR 同时启用多个未经独立 summary 的 slot。

### C-final：首次 enabled slot 的集成 shadow

- 产品 reader 已由主规范实现；本步骤不新增第二个 reader；
- 本地 shadow 比较该 slot 启用前后候选和 detector high 变化；
- 该 slot 有独立 revoke/rollback；
- Agent 默认入口仍不要求用户选择 calibration mode。

产品主规范的 Phase 2 和 Phase 4 不等待 C2+。C-final 只影响已经 enabled 的 slot。

## 12. 验收标准

一个 calibration slot 只有在以下条件全部满足时才可 enabled：

1. 输入 universe 在 Agent/profile/top-K 前固定且 coverage 完整；
2. statistic、applicability、null、scope、numeric 和 runtime class 版本全部冻结；
3. analysis scope 内所有文件的 selection 已由 slot-max 控制；
4. 跨 slot 使用预注册有序 slot 列表、`K_scope` 或更保守 adjustment；
5. brute-force/exact oracle 或独立保守验证通过；
6. FP 目标及置信上界通过；
7. 预注册 alternative 的 power 及置信下界通过；
8. canonical artifact 跨支持环境稳定；
9. enable 是独立 PR，可单独撤销；
10. 未启用 mode 仍可在 Agent workflow 中完整发现、展开和报告。

本 companion 获得书面批准后，从 C0 编写独立实施计划。任何 slot 的具体 statistic、
null 和门槛在对应 Calibration PR 前仍需单独复核，不能把本总规范视为一次性批准
所有 mode/applicability class。
