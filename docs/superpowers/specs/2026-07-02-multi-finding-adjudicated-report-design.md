# 多发现判定报告 — 设计文档

日期：2026-07-02
状态：已通过设计评审，待写实现

## 背景与问题

`paperconan report` 渲染的判定报告（[`_adjudicated_html.py`](../../../src/paperconan/_adjudicated_html.py)）目前基于**单个发现**的 8 段模板（论文主结论 / 异常位置 / 标签含义 / 为什么这是问题 / 影响判断 / 无辜解释 / 需要作者澄清 / 证据）。

当一篇论文有**多个**数据问题时（例如 Nature HDAC6 论文同时有 Fig.4c 的 `constant_offset`（confirmed）和 ED Fig.6c 的 `small_diff_set`（needs_human）），现结构表达不佳：

- 主发现说完直接接 `n=35, severity=high`，第二个发现被塞进末尾的「关联发现」小节，两者层级不对等、边界模糊。
- 底部「关键证据」面板机械混列，证据表与它所属发现的推理相分离。
- 每个发现无法各自携带 tier / status。

## 目标

让一篇论文的多个发现各自成为**自包含区块**：各带 tier/status 徽章、各带位置/标签/为什么/无辜解释/需澄清、证据表紧跟其后。顶部给论文级信息 + 发现清单一览。旧的单发现格式零改动继续可用。

## 方案（已选：论文头 + 每发现独立区块）

### 1. verdict.json schema 演进（向后兼容）

新增可选 `findings` 数组。**旧的单发现格式（`report_md` + `finding_refs`）继续照常渲染。**

```jsonc
{
  "title": "…",
  "verdict": "KEEP",                 // 论文级
  "paper_conclusion": "论文主结论…",   // 论文级（原第 1 段）
  "overall_impact": "core",          // 论文级，可选
  "review_note": "…",                // 论文级，可选
  "findings": [                      // ← 新增，每个发现一个对象
    {
      "title": "Fig.4c shHDAC6 组 VR 两列恒定 +0.3",
      "finding_ref": {"file": "MOESM7", "sheet": "Source Data Fig.4",
                      "rows": "5-39", "kind": "constant_offset"},
      "suspicion_tier": 1,
      "impact_scope": "core",
      "review_status": "confirmed",
      "report_md": "**位置**…\n\n**标签含义**…\n\n**为什么是问题**…\n\n**无辜解释**…\n\n**需要作者澄清**…"
    },
    {
      "title": "ED Fig.6c 跨条件列多行相同",
      "finding_ref": {"file": "MOESM13", "sheet": "Source Data ED Fig.6",
                      "rows": "5-39", "kind": "small_diff_set", "rule": "col[5] - col[3]"},
      "suspicion_tier": null,
      "impact_scope": "core",
      "review_status": "needs_human",
      "report_md": "…"
    }
  ]
}
```

每个 finding 携带：
- `finding_ref` — 定位并渲染它自己的证据表（复用现有 `_finding_matches_ref`）。
- `suspicion_tier` / `impact_scope` / `review_status` — 徽章。
- `title` — 发现清单与区块标题。
- `report_md` — 该发现的散文（小 markdown，复用 `_render_md`）。

论文级字段：`title`、`verdict`、`paper_conclusion`、`overall_impact`（可选）、`review_note`（可选）。

**决策确认：**
- (a) 论文级 `verdict` 保留显式（不从 findings 自动推）；hero 的 Tier 徽章显示所有发现里的最高级（数值最小的 tier）。
- (b) 每发现用 `report_md` 装散文，而非拆成 why/labels/... 多字段，保持 agent 写作灵活。
- (c) 保留旧单发现格式兼容路径。

### 2. 渲染布局

```
Hero:  论文标题 + [论文级 verdict] + [最高 Tier] + [overall impact]
─ 论文主结论 (paper_conclusion)
─ 发现清单表:  # | 位置 | detector | tier | status
──────────────────────────────
▸ 发现 1 · <title>   [Tier1 · confirmed · core]
    <report_md 渲染>
    ┌ 证据表(finding_ref 命中，列高亮)┐
▸ 发现 2 · <title>   [needs_human · core]
    <report_md 渲染>
    ┌ 证据表 ┐
──────────────────────────────
─ 方法/版本/背景 (review_note + 论文级证据)
```

关键点：**证据表紧跟在它所属发现的推理下面**；每发现有自己的 status 徽章，边界清晰；发现清单表让「这篇有几个问题、各什么级别」一眼可见。

### 3. 兼容与实现

- `render_adjudicated_report` 先检测 `verdict.get("findings")`：
  - 有 → 走新的多发现布局。
  - 没有 → 走现有单发现路径（`report_md` + `finding_refs`），旧报告零改动。
- 复用 `_finding_matches_ref`、`_render_key_finding`、`_render_md`；新增 per-finding 区块渲染 + 发现清单表渲染 + hero 最高 tier 计算。
- 未命中的 `finding_ref`：该发现区块仍渲染（用 report_md + 徽章），证据表位置显示「无匹配证据」占位（不静默吞掉）。

## 测试（TDD）

先写失败测试，再实现：

1. `findings` 两发现 → 渲染两个独立区块，各带自己的 tier/status 徽章。
2. 每个发现的证据表紧邻其区块（证据表数量 == 有 finding_ref 且命中的发现数），且高亮列正确。
3. 发现清单表含每个发现一行。
4. hero Tier 徽章显示最高级（两发现分别 Tier 1 与无 tier → 显示 Tier 1）。
5. 旧单发现格式（无 `findings`，只有 `report_md` + `finding_refs`）仍按现有布局渲染（回归保护）。

## 文档同步

`adjudication-tiers.md`、`report-templates.md`、`SKILL.md` 增加 `findings` 数组的说明与示例；标明旧单发现格式仍受支持。

## 非目标（YAGNI）

- 不自动从 findings 推导论文级 verdict/tier（保持显式）。
- 不把 per-finding 散文拆成结构化子字段。
- 不改确定性 `audit/report.html`。
- 不引入数据库、私有依赖或跨论文汇总。
