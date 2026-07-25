# Phase 1 实施计划：opt-in workflow 骨架

- 日期：2026-07-26
- 依据规范：[分阶段短信号工作流（产品交付规范）](../specs/2026-07-25-adaptive-short-signal-workflow-design.md) §14.3
- 前置：Phase 0 已闭环（#40 / #41 / #42 合并，main 全量 1319 passed）

> PaperConan 输出的是统计信号、数据不一致和待解释异常，不是对作者意图的判断。

## 目标与不做的事

**交付**（spec §14.3）：状态机、最小 artifact envelope 与 lineage；`route/expand/finalize/status`；
在 cap/profile 前冻结 raw stream 并据此构造 seed；expanded finding 与 verdict 的统一报告；
固定 request/verdict 的 replay；明确的预算与 coverage；fail-closed calibration registry reader。

**本阶段不做**：任何新 detector 数学；任何阈值/floor 改动；把 workflow 切成生产默认。
新 canonical 短信号留到 Phase 2。

**退出条件**（逐条对应 spec §14.3）：

1. 裸 CLI 默认行为不变；
2. detector golden 零变化；
3. workflow 仅 opt-in；
4. workflow seed 不随 CLI profile 改写或 packet cap 消失；
5. 不要求任何 enabled calibration。

## 已定决策

**CLI 分派**：先用一个独立机械 PR 把 `fetch` / `report` 从手写 `sys.argv[1]` 统一迁移到
`argparse.add_subparsers`，`workflow` 再自然接入。spec §14.3 禁止 workflow 成为第三种混合分派。
`paperconan <dir>` 的默认扫描行为必须保持不变。

## PR 切分

每个 PR 独立可验证、从已合并主干开始、全量 pytest + golden 绿（spec §14.1）。

| PR | 内容 | 行为变化 |
|---|---|---|
| P1a | 在 profile 改写前冻结 `raw_severity` | 只增字段，无既有行为变化 |
| P1b | CLI 机械迁移到 `add_subparsers` | 纯结构，无功能变化 |
| P1c | artifact envelope + `workflow start` / `status` | 新 opt-in 命令 |
| P1d | `workflow route`（含 expand 执行与预算） | 新 opt-in 命令 |
| P1e | `workflow finalize` + `report --expanded` | 新 opt-in 命令 + report 新可选参数 |
| P1f | fail-closed calibration registry reader | 空 registry 下行为不变 |

---

### P1a：在 profile 改写前冻结 `raw_severity`

**为什么先做**：`_profiles._demote_or_hide()` 原地覆写 `f["severity"] = "low"`，原始 severity
就此丢失。workflow seed 必须来自 profile 改写前的 raw stream（spec §14.3 退出条件 4、§8.1
"`raw_severity` 一经产出便不可覆盖"）。这是后续所有 PR 的地基，且本身零行为变化。

**改动点**：`_profiles.initialize_profile_fields()`（`_audit.py` 已在所有 demote 之前、且在
forensic 早退之前调用它，是唯一统一入口）。

```python
def initialize_profile_fields(findings):
    for f in findings:
        f.setdefault("raw_severity", f.get("severity"))   # 冻结，幂等
        f.setdefault("profile_action", "kept")
        f.setdefault("false_positive_context", [])
```

**测试**：
- 被 demote 的 finding：`severity == "low"` 而 `raw_severity == "high"`；
- forensic 早退路径同样带 `raw_severity`；
- 重复调用 `apply_profile_to_findings` 不会把已降级的 low 覆盖进 `raw_severity`（幂等）；
- triage 的 `profile_action == "hidden"` 下 `raw_severity` 仍保留；
- 全量 golden：确认 scan.json 只多出 `raw_severity` 字段，无其它 diff。

**风险**：scan.json 新增字段可能触发 golden 更新。必须逐条确认 diff 只有 `raw_severity`。

---

### P1b：CLI 机械迁移到 `add_subparsers`

**改动点**：`_audit.main()`。把 `fetch`、`report` 的手写 `sys.argv[1]` 分支改为子命令，
保留 `paperconan <dir>` 的默认扫描（无子命令时走默认 parser）。

**测试**：
- `paperconan <dir>`、`paperconan report ...`、`paperconan fetch ...` 行为与迁移前逐字节一致；
- `--help` 列出子命令；
- `python -m paperconan` 与 console script 两个入口都覆盖；
- 未知子命令给出明确错误而非静默当成目录。

**风险**：`paperconan <dir>` 与子命令名冲突（若有目录恰好叫 `report`）。测试需覆盖该歧义并
固定优先级。

---

### P1c：artifact envelope + `workflow start` / `status`

**交付**：`_workflow/` 子包；共同 envelope；`DISCOVER` 落地。

envelope 最小字段（spec §5）：

```text
schema_version / run_id / artifact_id / parent_refs
config_digest / source_finding_refs / coverage / created_by_stage
```

`workflow start <in_dir> --out <dir>` 产出：`scan.json`（复用现有 `scan_dir`）、
`states/s000.json`、`steps/t000/candidate_packet.json`。
`workflow status <dir>` 只读打印当前 stage、next_action、预算余额与 coverage。

**seed 构造**：本阶段不含新 detector 数学，seed 由既有 finding 的 raw stream 构造
（用 P1a 的 `raw_severity`，不读被 profile 改写的 `severity`），并受 bounded cap 约束，
截断如实写入 `coverage`。

**测试**：固定输入两次 `start` 产物一致；envelope 字段齐全；schema 版本不兼容显式拒绝；
seed 不因 `--profile triage` 而消失（退出条件 4 的直接回归）。

---

### P1d：`workflow route`

**交付**：ROUTE/EXPAND 状态机、JSON Schema 校验、预算、不可变 step artifact。

- 每个 actionable cluster 在一次 envelope 中恰好出现一次；
- 四类 decision（`expand` / `needs_context` / `explained` / `defer`）的条件矩阵；
- `route_step` 每次递增；`expansion_round` 只在 numeric expand 时递增，上限 2；
- 同一 envelope 不得既请求动作又 `proceed_to_adjudicate=true`；
- 预算耗尽写 coverage 后进入 ADJUDICATE，不静默截断。

Phase 1 的 recipe 注册表可以只含最小集合（甚至只做 schema 与状态流转，不含新数学），
但未注册 recipe 必须被拒绝。

**测试**：非法状态转换、未注册 recipe、第三轮展开、超预算、重复/遗漏 cluster 全部被拒；
context-only step 只递增 route_step；固定 request replay 两次产物一致。

---

### P1e：`workflow finalize` + `report --expanded`

**交付**：ADJUDICATE→COMPLETE；verdict 与 lineage 校验；统一 report model。

- `finalize` 只接受 ADJUDICATE 状态；相同 digest 幂等重放，不同 digest 拒绝；
- `paperconan report` 新增可选 `--expanded`，把 `expanded_findings.json` 合入统一 report model；
- 裸 CLI 的 `report scan.json --verdict` 不传 `--expanded` 时行为完全不变（向后兼容）；
- 中途失败写 `workflow_incomplete` 与 coverage limitation，不得静默宣称完成。

**测试**：固定 verdict replay 一致；stale digest 被拒；未知/歧义 finding ref 报错而非静默匹配零条；
legacy `report` 路径零变化。

---

### P1f：fail-closed calibration registry reader

**交付**：产品侧只读 reader（registry 内容由 companion 规范拥有）。

`registry_status = missing | disabled | enabled | revoked`；缺 entry / 版本不符 / coverage
不完整一律 fail closed。**registry 为空时 workflow 必须能正常走到 COMPLETE**（spec §10）。

**测试**：四种状态 + 版本不符均 fail closed；空 registry 全流程通过；synthetic enabled
compatibility fixture 能被正确读取；Agent verdict 不能改变 promotion。

---

## 全局约束

- 合成 fixture only；不提交真实论文数据、DOI、判定（CLAUDE.md 红线）。
- 中立措辞贯穿代码注释、报告文案与 commit message。
- 不顺带降低 hard-threshold audit 的 activation floor（spec §2.3 权威边界）。
- 派生浮点按 spec §12.3 走 `numeric_canonicalization_version`（12 位有效数字，`-0`→`0`，
  拒绝 NaN/inf），避免跨平台 golden 抖动。
- 每个 PR 走 TDD：先看到 RED，再最小实现。

## 当前进度

- [ ] P1a  - [ ] P1b  - [ ] P1c  - [ ] P1d  - [ ] P1e  - [ ] P1f
