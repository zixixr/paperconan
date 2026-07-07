# 判定报告统一高保真 + 散落精确重复检测器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `within_col_dispersed_repeats` 检测器（抓高精度连续列里散落的精确重复），并把判定报告渲染器统一成"永远高保真"（删除老派单条布局）。

**Architecture:** 检测器是一个 row-order 的 within-column sibling detector（`detect_dispersed_repeats`），在现有编排点并入 block 的 `within_col` 列表，通过 `col_idx` + `example_cells` 复用 `_attach_evidence` 出证据热力表；渲染器新增 `_normalize_verdict` 把新旧两种 verdict schema 折成统一的 findings 列表，永远走 `_render_multi` 富布局。

**Tech Stack:** Python 3.10+，numpy，pytest，uv。检测器在 [`src/paperconan/_audit.py`](../../../src/paperconan/_audit.py)，渲染器在 [`src/paperconan/_adjudicated_html.py`](../../../src/paperconan/_adjudicated_html.py)，prefilter 在 [`src/paperconan/_prefilter.py`](../../../src/paperconan/_prefilter.py)。

参考设计：[`docs/superpowers/specs/2026-07-08-report-fidelity-and-dispersed-repeat-detector-design.md`](../specs/2026-07-08-report-fidelity-and-dispersed-repeat-detector-design.md)

---

## File Structure

- **Create** `tests/test_dispersed_repeats.py` — 检测器正例 + FP 负例 + 蒙特卡洛 oracle。
- **Modify** `src/paperconan/_audit.py` — 新增 `detect_dispersed_repeats(...)`；在编排点（`~2599`）把它的输出并入 `within_col`；`detectors.py` 导出。
- **Modify** `src/paperconan/detectors.py` — 从 `_audit` 导出 `detect_dispersed_repeats`。
- **Modify** `src/paperconan/_prefilter.py` — 把 `within_col_dispersed_repeats` 加入 `prefilter_within_col` 白名单。
- **Create** `tests/test_adjudicated_report_unified.py` — 渲染器统一路径测试。
- **Modify** `src/paperconan/_adjudicated_html.py` — 新增 `_normalize_verdict`；改 `render_adjudicated_report` 永远走富渲染；删 `_render_single`；index 单条隐藏；判定摘要块；证据回退。
- **Modify** docs：`docs/reports.md`、`docs/detectors.md`、`skills/paperconan/references/report-templates.md`、`skills/paperconan/references/detectors.md`、`skills/paperconan/references/adjudication-tiers.md`。

运行测试：`uv run pytest <file> -q`。

---

## Task 1: 检测器正例（failing test）

**Files:**
- Create: `tests/test_dispersed_repeats.py`

- [ ] **Step 1: Write the failing test**

```python
"""within_col_dispersed_repeats: many DISTINCT high-precision values each
repeated across DISPERSED rows (Laskowski/Pruitt fingerprint), with FP guards."""
import numpy as np
import pytest
from paperconan._sheet import Sheet
from paperconan._audit import detect_dispersed_repeats


def _sheet(col, header="boldness"):
    rows = [[header]] + [[v] for v in col]
    return Sheet.from_rows(rows)


def _detect(col, header="boldness"):
    s = _sheet(col, header)
    return detect_dispersed_repeats(s, 1, len(col) + 1, 0, 1, [header])


def test_dispersed_high_precision_repeats_fire():
    # 60-row continuous-looking column (2-decimal latencies). Inject 12 distinct
    # values, each appearing 3x at DISPERSED (non-adjacent, wide-span) rows.
    rng = np.random.default_rng(7)
    col = [round(float(rng.uniform(1, 599)), 2) for _ in range(120)]
    injected = [round(float(rng.uniform(1, 599)), 2) for _ in range(12)]
    # scatter each injected value across the column at spread positions
    for i, val in enumerate(injected):
        for slot in (i, 40 + i, 80 + i):   # spans > half the column, non-adjacent
            col[slot] = val
    out = _detect(col)
    hits = [f for f in out if f["kind"] == "within_col_dispersed_repeats"]
    assert hits, "expected a dispersed-repeats finding"
    f = hits[0]
    assert f["col_idx"] == 0
    assert f["severity"] == "medium"
    assert f["n_repeat_groups"] >= 10
    # example_cells (1-based row,col) must point at injected duplicate rows
    assert f["example_cells"], "expected example_cells for the evidence heatmap"
    assert all(c == 1 for _, c in f["example_cells"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dispersed_repeats.py::test_dispersed_high_precision_repeats_fire -q`
Expected: FAIL — `ImportError: cannot import name 'detect_dispersed_repeats'`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_dispersed_repeats.py
git commit -m "test(detector): failing positive test for within_col_dispersed_repeats"
```

---

## Task 2: 实现 `detect_dispersed_repeats`

**Files:**
- Modify: `src/paperconan/_audit.py` (新增函数，紧邻 `detect_within_column_patterns` 之后，约 `1191` 行)
- Modify: `src/paperconan/detectors.py` (导出)

- [ ] **Step 1: Implement the detector**

在 `src/paperconan/_audit.py` 中 `detect_within_column_patterns` 之后新增（复用文件顶部已导入的 `Counter`、`defaultdict`、`np`、`is_num`；若 `math` 未导入则用 `np.isnan`）：

```python
def detect_dispersed_repeats(sheet, r0, r1, c0, c1, header, min_n=30):
    """Many DISTINCT high-precision values each repeated across DISPERSED rows.

    Complements within_col_value_duplication (single dominant value). Targets a
    continuous, high-precision column whose exact-duplicate mass far exceeds the
    near-zero birthday expectation, where repeats are scattered across the table
    (not adjacent fill-down / technical replicates). Thresholds are conservative
    defaults pinned by tests; not env-tunable.
    """
    findings = []

    def _dec_places(v):
        s = f"{v:.10f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    for c in range(c0, c1):
        rows_vals = []
        for r in range(r0, r1):
            v = sheet.cell(r, c)
            if is_num(v) and not (isinstance(v, float) and np.isnan(v)):
                rows_vals.append((r, float(v)))
        n = len(rows_vals)
        if n < min_n:
            continue
        vals = [v for _, v in rows_vals]

        # quick reject: pure-integer columns (counts / codes)
        if all(abs(v - round(v)) < 1e-9 for v in vals):
            continue

        # Strip a dominant boundary/censor value FIRST (e.g. 600s ceiling), so it
        # neither drags down the precision fraction nor counts as a "repeat".
        cnt_all = Counter(round(v, 6) for v in vals)
        top_v, top_c = cnt_all.most_common(1)[0]
        boundary = top_v if top_c > 0.25 * n else None
        core = [(r, v) for (r, v) in rows_vals
                if boundary is None or round(v, 6) != boundary]
        m = len(core)
        if m < min_n:
            continue
        core_vals = [v for _, v in core]

        # Gate 1 — continuity / high precision (computed on core)
        frac_hi_prec = sum(1 for v in core_vals if _dec_places(v) >= 2) / m
        if frac_hi_prec < 0.6:
            continue
        distinct = len({round(v, 6) for v in core_vals})
        if distinct < 50 or distinct / m < 0.3:
            continue

        # Gate 1b — birthday / effective-support gate: the recording precision must
        # be fine ENOUGH relative to the value range that exact collisions are
        # near-zero-expected. A coarse column (e.g. 2 decimals over [0,1] -> only
        # ~100 possible values) collides naturally and must NOT fire.
        dps = sorted(_dec_places(v) for v in core_vals)
        med_dp = dps[len(dps) // 2]
        support = (max(core_vals) - min(core_vals)) * (10 ** med_dp)
        if support < 20 * m:
            continue

        # Gate 2 + 3 — dispersed exact-duplicate groups
        positions = defaultdict(list)
        for r, v in core:
            positions[round(v, 6)].append(r)
        block_h = r1 - r0
        dispersed = []
        dup_cells = 0
        for val, rs in positions.items():
            if len(rs) < 2:
                continue
            rs_sorted = sorted(rs)
            span = rs_sorted[-1] - rs_sorted[0]
            non_adjacent = any(b - a > 1 for a, b in zip(rs_sorted, rs_sorted[1:]))
            if span >= 0.5 * block_h and non_adjacent:
                dispersed.append((val, rs_sorted))
                dup_cells += len(rs_sorted)

        if len(dispersed) >= 10 and dup_cells >= 0.15 * m:
            dispersed.sort(key=lambda kv: -len(kv[1]))
            example_cells = []
            for _, rs in dispersed[:3]:
                for rr in rs[:8]:
                    example_cells.append((rr + 1, c + 1))
            col_name = header[c - c0] if c - c0 < len(header) else f"col{c}"
            core_arr = np.round(np.array([v for _, v in core]), 4)
            counts = Counter(core_arr.tolist())
            findings.append(dict(
                kind="within_col_dispersed_repeats",
                col=col_name, col_idx=c, n=m,
                n_repeat_groups=len(dispersed), dup_cells=dup_cells,
                frac_repeat=dup_cells / m,
                n_distinct=int(len(counts)), all_integer=False,
                value_sample=[float(v) for v, _ in counts.most_common(8)],
                example_cells=example_cells,
                severity="medium",
                rule=(f"col[{c}]: {len(dispersed)} distinct high-precision values "
                      f"each recur across dispersed rows ({dup_cells}/{m} cells)")))
    return findings
```

- [ ] **Step 2: Export it from `detectors.py`**

在 `src/paperconan/detectors.py` 的 `from ._audit import (...)` 块加入 `detect_dispersed_repeats,`，并加进 `__all__`。

- [ ] **Step 3: Run the positive test**

Run: `uv run pytest tests/test_dispersed_repeats.py::test_dispersed_high_precision_repeats_fire -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/paperconan/_audit.py src/paperconan/detectors.py
git commit -m "feat(detector): add within_col_dispersed_repeats"
```

---

## Task 3: FP 负例（必须不命中）

**Files:**
- Modify: `tests/test_dispersed_repeats.py`

- [ ] **Step 1: Add the FP tests**

```python
def test_adjacent_technical_replicates_do_not_fire():
    # 60 distinct values (passes the distinct/support gates) but each repeated in
    # ADJACENT rows (fill-down / technical replicate) -> dispersion guard must reject.
    col = []
    rng = np.random.default_rng(1)
    for _ in range(60):
        v = round(float(rng.uniform(1, 599)), 2)
        col += [v, v, v]   # 3 adjacent copies
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_censored_ceiling_does_not_fire():
    # a continuous column dominated by a 600s ceiling, rest unique -> benign
    rng = np.random.default_rng(2)
    col = [600.0] * 90 + [round(float(rng.uniform(1, 599)), 2) for _ in range(60)]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_small_integer_column_does_not_fire():
    rng = np.random.default_rng(3)
    col = [int(rng.integers(0, 8)) for _ in range(150)]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_low_cardinality_ratio_column_does_not_fire():
    # ratios of small integers -> few distinct, naturally collide -> benign
    denoms = [2, 3, 4, 5, 6]
    rng = np.random.default_rng(4)
    col = [round(int(rng.integers(0, d + 1)) / d, 4) for d in
           (denoms[int(rng.integers(0, 5))] for _ in range(200))]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]
```

- [ ] **Step 2: Run the FP tests**

Run: `uv run pytest tests/test_dispersed_repeats.py -q`
Expected: PASS (all). 若某条误触发，收紧对应门槛（如 `distinct/m`、`frac_hi_prec`、`0.5*block_h`）直到负例全绿且 Task 1 正例仍绿——阈值以测试锁定。

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispersed_repeats.py
git commit -m "test(detector): FP negatives for dispersed_repeats (adjacent/ceiling/int/ratio)"
```

---

## Task 4: 蒙特卡洛对拗 oracle（误报率≈0）

**Files:**
- Modify: `tests/test_dispersed_repeats.py`

- [ ] **Step 1: Add the oracle test**

```python
def test_monte_carlo_continuous_columns_have_near_zero_fp():
    # Genuinely continuous columns (varied precision/scale/n) must almost never fire.
    fp = 0
    trials = 200
    for seed in range(trials):
        rng = np.random.default_rng(1000 + seed)
        n = int(rng.integers(40, 400))
        scale = float(rng.choice([1.0, 10.0, 100.0, 600.0]))
        decimals = int(rng.choice([2, 3, 4]))
        col = [round(float(rng.uniform(0, scale)), decimals) for _ in range(n)]
        out = _detect(col)
        if [f for f in out if f["kind"] == "within_col_dispersed_repeats"]:
            fp += 1
    assert fp <= 2, f"false-positive rate too high: {fp}/{trials}"
```

- [ ] **Step 2: Run the oracle**

Run: `uv run pytest tests/test_dispersed_repeats.py::test_monte_carlo_continuous_columns_have_near_zero_fp -q`
Expected: PASS. 若超阈值，收紧门槛并重跑 Task 1/3/4 全绿。

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispersed_repeats.py
git commit -m "test(detector): monte-carlo oracle pins dispersed_repeats FP rate ~0"
```

---

## Task 5: 接线（编排 + prefilter 白名单）

**Files:**
- Modify: `src/paperconan/_audit.py:~2599`（编排点）
- Modify: `src/paperconan/_prefilter.py:~909`（`prefilter_within_col` 白名单）

- [ ] **Step 1: Merge detector output into the block within_col list**

在 `_audit.py` 编排点，紧跟 `wc = detect_within_column_patterns(sheet, r0, r1, c0, c1, header)` 之后一行加入：

```python
                wc = wc + detect_dispersed_repeats(sheet, r0, r1, c0, c1, header)
```

（`wc` 随后已进 `"within_col": wc` 并经 `_attach_evidence`，故 `col_idx` + `example_cells` 会自动生成证据热力表，无需额外接线。）

- [ ] **Step 2: Let name-based prefilter demotion apply to the new kind**

在 `_prefilter.py` 的 `prefilter_within_col` 中，把白名单从

```python
    if kind not in {"within_col_value_duplication", "within_col_decimal_repetition"}:
        return "keep", None
```

改为

```python
    if kind not in {"within_col_value_duplication", "within_col_decimal_repetition",
                    "within_col_dispersed_repeats"}:
        return "keep", None
```

- [ ] **Step 3: Add a wiring test (kind flows into scan + evidence attached)**

在 `tests/test_dispersed_repeats.py` 追加：

```python
from paperconan._audit import _attach_evidence

def test_finding_gets_evidence_with_highlighted_cells():
    rng = np.random.default_rng(7)
    col = [round(float(rng.uniform(1, 599)), 2) for _ in range(120)]
    injected = [round(float(rng.uniform(1, 599)), 2) for _ in range(12)]
    for i, val in enumerate(injected):
        for slot in (i, 40 + i, 80 + i):
            col[slot] = val
    s = _sheet(col)
    out = detect_dispersed_repeats(s, 1, len(col) + 1, 0, 1, ["boldness"])
    _attach_evidence(out, s, 1, len(col) + 1, 0, 1, ["boldness"])
    ev = out[0]["evidence"]
    assert ev["highlight_cols"] == [0]
    assert ev["highlight_rows"], "expected highlighted duplicate rows"
```

- [ ] **Step 4: Run detector + full suite**

Run: `uv run pytest tests/test_dispersed_repeats.py tests/test_within_col_prefilter.py -q`
Expected: PASS. Then `uv run pytest -q` — 全绿（确认没有回归现有 golden）。

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py src/paperconan/_prefilter.py tests/test_dispersed_repeats.py
git commit -m "feat(detector): wire dispersed_repeats into scan + prefilter whitelist"
```

---

## Task 6: 渲染器 — `_normalize_verdict` + 单条走富渲染（failing test）

**Files:**
- Create: `tests/test_adjudicated_report_unified.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unified adjudicated report: single-finding verdicts render in the SAME
high-fidelity layout (finding card + evidence heatmap) as multi-finding ones."""
from paperconan._adjudicated_html import render_adjudicated_report

SCAN = {
    "tool_version": "0.8.2", "profile": "review",
    "paper": {"title": "T", "doi": "10.0/x", "input_dir": "d"},
    "relations_blocks": [{
        "file": "f.xlsx", "sheet": "S", "block": {"rows": "2-40", "cols": "3-3", "header": ["v"]},
        "relations": [], "progressions": [], "equal_pairs": [], "row_pairs": [],
        "within_col": [{
            "kind": "within_col_dispersed_repeats", "col": "v", "col_idx": 2,
            "n": 30, "severity": "medium", "rule": "col[2]: dispersed repeats",
            "evidence": {"headers": ["v"], "col_offset": 2, "highlight_cols": [2],
                         "highlight_rows": [5, 12, 20],
                         "rows": [{"row_idx": 1, "is_context": True, "values": ["v"]},
                                  {"row_idx": 5, "is_context": False, "values": [1.23]}]},
        }],
        "identical_after_rounding": [], "grim": [], "findings_omitted": 0,
    }],
    "digit_distribution": [], "decimal_endings": [], "cross_sheet_findings": [],
}

SINGLE_VERDICT = {
    "title": "T", "verdict": "KEEP", "suspicion_tier": 1, "impact_scope": "core",
    "tier_why": "why-1", "innocent_explanation": "checked", "needs_author_data": "raw",
    "report_md": "### 1. 论文主结论\n结论。\n\n**为什么** …",
    "finding_refs": [{"file": "f.xlsx", "sheet": "S", "rows": "2-40",
                      "kind": "within_col_dispersed_repeats"}],
    "review_status": "confirmed",
}


def test_single_verdict_renders_rich_layout():
    html = render_adjudicated_report(SCAN, SINGLE_VERDICT)
    # rich per-finding card + evidence heatmap present
    assert "finding-block" in html
    assert "hi-col" in html            # evidence heatmap cells
    # old two-column plain layout is GONE
    assert 'class="panel side"' not in html
    assert 'class="panel report"' not in html
    # verdict-level judgment fields preserved somewhere in the page
    assert "why-1" in html and "raw" in html


def test_single_finding_hides_findings_index():
    html = render_adjudicated_report(SCAN, SINGLE_VERDICT)
    assert "findings-index" not in html   # 1 finding -> no silly 1-row index
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_adjudicated_report_unified.py -q`
Expected: FAIL — single verdict currently renders `_render_single` (`panel side` / `panel report` present, no `finding-block`).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_adjudicated_report_unified.py
git commit -m "test(report): failing test for unified rich single-verdict layout"
```

---

## Task 7: 渲染器 — 归一化 + 统一路径 + 删老派分支

**Files:**
- Modify: `src/paperconan/_adjudicated_html.py`

- [ ] **Step 1: Add `_normalize_verdict`**

在 `_adjudicated_html.py` `render_adjudicated_report` 之前加入：

```python
def _normalize_verdict(verdict: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Fold either verdict shape into (paper_fields, findings_list, summary).

    - multi shape: return its findings as-is.
    - legacy single shape (report_md + finding_refs): synthesize one finding and
      carry tier_why / innocent_explanation / needs_author_data as `summary`.
    """
    if verdict.get("findings"):
        paper = {"paper_conclusion": verdict.get("paper_conclusion"),
                 "review_note": verdict.get("review_note")}
        return paper, list(verdict["findings"]), {}
    refs = verdict.get("finding_refs") or []
    single = {
        "title": verdict.get("title") or "发现",
        "finding_ref": refs[0] if refs else None,
        "extra_refs": refs[1:],
        "suspicion_tier": verdict.get("suspicion_tier"),
        "impact_scope": verdict.get("impact_scope"),
        "review_status": verdict.get("review_status") or "unreviewed",
        "report_md": verdict.get("report_md"),
    }
    summary = {k: verdict.get(k) for k in
               ("tier_why", "innocent_explanation", "needs_author_data")
               if verdict.get(k)}
    paper = {"paper_conclusion": None, "review_note": verdict.get("review_note")}
    return paper, [single], summary
```

- [ ] **Step 2: Route `render_adjudicated_report` through the rich path only**

替换 `render_adjudicated_report` 的分叉尾部：

```python
def render_adjudicated_report(scan: dict[str, Any], verdict: dict[str, Any]) -> str:
    title = _scan_title(scan, verdict)
    visible = [item for item in _all_findings(scan)
               if str(item["finding"].get("profile_action") or "").lower() != "hidden"]
    scan_findings = sorted(visible, key=_finding_score)
    paper, findings, summary = _normalize_verdict(verdict)
    return _render_unified(scan, verdict, title, scan_findings, findings, paper, summary)
```

- [ ] **Step 3: Rename/adapt `_render_multi` → `_render_unified` with the three additions**

以现有 `_render_multi` 为基础改成 `_render_unified(scan, verdict, title, scan_findings, findings, paper, summary)`：
  1. `paper_conclusion` 从 `paper["paper_conclusion"]`（可能为 None → 显示 `—` 或省略该小节）。
  2. **index 隐藏**：`index = _render_findings_index(findings, scan_findings) if len(findings) > 1 else ""`。
  3. **判定摘要块**：`summary` 非空时渲染一个 `<div class="kv">`（复用现有 kv 样式）承载 `tier_why/innocent_explanation/needs_author_data`。
  4. `review_note` 从 `paper["review_note"]`。

`_render_finding_block` 增加**证据回退**：当 `finding.get("finding_ref")` 为空或未命中时，用 `scan_findings[0]`（最强信号）出证据表；并渲染 `extra_refs` 对应的附加证据表（如有）。

- [ ] **Step 4: Delete `_render_single`**

删除 `_render_single` 函数（不再被引用）。`grep -n "_render_single" src/paperconan/_adjudicated_html.py` 应为空。

- [ ] **Step 5: Run the unified tests + full suite**

Run: `uv run pytest tests/test_adjudicated_report_unified.py -q`
Expected: PASS.
Run: `uv run pytest -q`
Expected: 全绿（若有旧渲染 golden 断言老派 `panel side`，同步更新为富布局断言）。

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_adjudicated_html.py tests/test_adjudicated_report_unified.py
git commit -m "feat(report): unify adjudicated renderer; single verdicts render rich; drop _render_single"
```

---

## Task 8: 渲染器 — DROP / NEEDS_HUMAN 优雅渲染

**Files:**
- Modify: `tests/test_adjudicated_report_unified.py`

- [ ] **Step 1: Add graceful-degradation tests**

```python
def test_drop_verdict_renders_without_crash():
    v = {"title": "T", "verdict": "DROP", "drop_reason": "fixed_denominator",
         "innocent_explanation": "percentages from a common denominator",
         "report_md": None, "review_status": "unreviewed"}
    html = render_adjudicated_report(SCAN, v)
    assert "DROP" in html
    assert "finding-block" in html          # falls back to strongest scan finding evidence
    assert 'class="panel side"' not in html


def test_needs_human_verdict_renders_without_crash():
    v = {"title": "T", "verdict": "NEEDS_HUMAN",
         "tier_why": "sample provenance missing", "report_md": None,
         "review_status": "unreviewed"}
    html = render_adjudicated_report(SCAN, v)
    assert "NEEDS_HUMAN" in html
    assert "sample provenance missing" in html
```

- [ ] **Step 2: Run and fix if needed**

Run: `uv run pytest tests/test_adjudicated_report_unified.py -q`
Expected: PASS. （若 DROP 无 finding_ref 导致证据回退取 `scan_findings[0]`——Step 3 of Task 7 已覆盖；若 `scan_findings` 为空则证据块显示 `无匹配证据` 且不崩。）

- [ ] **Step 3: Commit**

```bash
git add tests/test_adjudicated_report_unified.py
git commit -m "test(report): DROP/NEEDS_HUMAN render gracefully in unified layout"
```

---

## Task 9: 文档更新

**Files:**
- Modify: `docs/reports.md`、`docs/detectors.md`
- Modify: `skills/paperconan/references/report-templates.md`、`skills/paperconan/references/detectors.md`、`skills/paperconan/references/adjudication-tiers.md`

- [ ] **Step 1: `docs/reports.md` § 判定后 HTML 报告**

改成描述一套统一流程：agent 写判断 → `paperconan report scan.json --verdict verdict.json --out …` → **永远**高保真（论文头 + 每条 finding 卡片 + 证据热力表）。说明 `findings[]` 是主形态、单条只是"列了一条 finding"、旧 `report_md` 形态兼容且现在也富渲染；点明 README 首图就是此输出、无特殊管线。删去"只展示 8 段式 report_md"的旧描述。

- [ ] **Step 2: `skills/paperconan/references/report-templates.md`**

把主 verdict 例子改为 `findings[]` 形态（复用 adjudication-tiers.md 的多发现例子），澄清"单/多只是 finding 数量、与观感无关"；保留 DROP/NEEDS_HUMAN 短形态。

- [ ] **Step 3: `skills/paperconan/references/adjudication-tiers.md`**

在 "Multiple Findings In One Paper" 顶部加一句：这是判定报告的**主**形态；单条 finding 也用它（一个元素的 `findings`），渲染同样高保真。

- [ ] **Step 4: 两处 `detectors.md` 加新检测器条目**

在 `docs/detectors.md` 和 `skills/paperconan/references/detectors.md` 的 within-column 检测器区，新增：

```
within_col_dispersed_repeats — 一列高精度连续测量里，多个不同值各自跨"散布的行"精确重复
（与 within_col_value_duplication 的"单值高频"互补）。剥离主导封顶值后统计通过离散度闸门
的重复组。常见良性：相邻技术重复/填充、小整数或低基数比率列、派生列——由离散度闸门与
连续性门槛排除，或经 prefilter 按列名降级。severity=medium（统计信号，非结论）。
```

- [ ] **Step 5: Commit**

```bash
git add docs/reports.md docs/detectors.md skills/paperconan/references/
git commit -m "docs: unified high-fidelity report flow + within_col_dispersed_repeats detector"
```

---

## Task 10: 验收 — 重扫 + 重渲 Am Nat 视频素材

**Files:** 无源码改动（仅生成到 gitignored `recheck/video_demo/`）。

- [ ] **Step 1: Re-scan the Am Nat file**

Run:
```bash
.venv/bin/paperconan recheck/video_demo/amnat2016_laskowski \
  --title "Individual and group performance suffers from social niche disruption" \
  --doi "10.1086/686220" --md
```
Expected: `scan.json` 现在含 `within_col_dispersed_repeats`（在 `Pre.boldness` / `Post.boldness`）。用
`grep -c within_col_dispersed_repeats recheck/video_demo/amnat2016_laskowski/audit/scan.json` 确认 ≥1。

- [ ] **Step 2: Point the multi-finding verdict's finding_ref at the new kind**

编辑 `recheck/video_demo/amnat2016_laskowski/verdict_multi.json`，把发现 1 的 `finding_ref.kind` 改为 `within_col_dispersed_repeats`（`sheet="individual measures"`，`rows` 用新 finding 的 `block_rows`），使证据热力表直接高亮重复潜伏期。

- [ ] **Step 3: Re-render + screenshot**

Run:
```bash
.venv/bin/paperconan report recheck/video_demo/amnat2016_laskowski/audit/scan.json \
  --verdict recheck/video_demo/amnat2016_laskowski/verdict_multi.json \
  --out recheck/video_demo/amnat2016_laskowski/adjudicated_report_multi.html
```
Expected: 报告高保真；证据热力表高亮 `143.37` 等重复值所在的散落行。

- [ ] **Step 4: Full suite green + determinism**

Run: `uv run pytest -q`
Expected: 全绿。再跑一次 Step 1 扫描，`diff` 两次 `scan.json` 应一致（确定性）。

- [ ] **Step 5: Final commit (source/docs only; no data)**

```bash
git add -A ':!recheck'
git commit -m "chore: acceptance run for dispersed_repeats + unified report (Am Nat)"
```

---

## Self-Review 注记

- Spec 两组件均有任务：检测器（T1–T5, T9, T10）、渲染器统一（T6–T8, T9, T10）。
- 无占位符：检测器与归一化均给出完整代码；测试均含真实断言与命令。
- 类型/命名一致：`detect_dispersed_repeats`、`within_col_dispersed_repeats`、`_normalize_verdict`、`_render_unified` 全程一致。
- 数据合规：所有 fixture 合成；Am Nat 产物仅落 gitignored `recheck/`，最终提交用 `':!recheck'` 排除。
- 阈值：保守默认写死于 Task 2，由 Task 3/4 负例+oracle 锁定（spec 要求）。
