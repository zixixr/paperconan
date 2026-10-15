# 局部段关系检测（local pair runs）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一遍检测，报出同一数值块内两行或两列只在一段连续位置上成恒定比例、恒定差值的统计信号（`local_ratio_run` / `local_offset_run`）。

**Architecture:** 全部放在 `src/paperconan/_audit.py`，作为独立的一遍：不读写现有检测器的候选池、频次表、预算或上限。块循环里在 `detect_relations` / `detect_row_relations` 之后调用；列方向的结果并入 `relations` 组，行方向的并入 `row_relations` 组，下游（证据、良性说明、profile、降权）沿用现有路径。设计见 `docs/superpowers/specs/2026-10-15-local-pair-runs-design.md`。

**Tech Stack:** Python ≥ 3.10、numpy、pytest；复用 `_audit.py` 里的 `_effective_row_quantums`、`_sigfigs_and_frac`、`_ratio_prediction_bits`、`_interval_holds_a_power_of_ten`、`_AXIS_CONTEXT_LABEL_RE`、`_row_label`、`_sample`、`_note_detector_cap`。

**纪律（来自 CLAUDE.md 与本仓库的教训）：**
- 只用合成数据；不写任何论文的数据、编号或判定。输出与注释用中性措辞（统计信号、数据不一致）。
- 每条新断言都要确认：删掉它守护的代码，它会变红。本计划在 Task 5、Task 9 里给出了逐条的“关掉即变红”核对。
- 本地测试用 `.venv/bin/python -m pytest tests`（`uv run pytest` 在 main 上收集 e2e 报错；管道 `| tail` 会吞掉退出码，报结果前读汇总行）。
- 只 `git add` 列出的文件，绝不 `-A`。提交信息结尾加 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/paperconan/_audit.py` | 修改 | 新增一节“local pair runs”（常数、步长推断、对称区间、段与组、判据、驱动）；块循环里接入；`benign_reason` 加两种类型 |
| `src/paperconan/_profiles.py` | 修改 | `_is_derived_relation` 认新类型（行方向套同名行规则）；`_relation_prefilter` 对列方向按别名接入 |
| `tests/test_local_pair_runs.py` | 新建 | 单元测试与端到端测试（合成数据） |
| `tests/test_local_run_bench_baseline.py` | 新建 | 台架：在两个现有台架的良性数据上跑本检测器，另有已知代价层与真阳性层，冻结档位 |
| `skills/paperconan/references/detectors.md` | 修改 | 两种类型的说明 |
| `skills/paperconan/references/output-schema.md` | 修改 | `relations` / `row_relations` 组的注释与字段 |

新代码在 `_audit.py` 里的位置：紧接 `_ratio_prediction_bits` 之后。锚点是该函数的最后一行（全文唯一）：

```python
            "confirming": len(per_cell), "per_cell": per_cell}
```

每个任务把新代码接在上一个任务加的代码之后。

---
### Task 1: 常数与按整条线推断的记录步长

**Files:**
- Modify: `src/paperconan/_audit.py`（锚点之后）
- Create: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 建测试文件，写步长的测试**

`tests/test_local_pair_runs.py` 的开头与第一组测试（含三轮评审后补入的测试，见提交 95f3051、a33a677、957d72d）：

```python
"""Local pair runs: two lines of one block related as B = k*A or B = A + c over part of the
line only (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md). Synthetic data only."""
import math

import numpy as np
import pytest

from paperconan._sheet import Sheet
from paperconan._audit import _effective_line_quantums, _effective_row_quantums

A10 = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333]


def _arr(xs):
    return np.array(xs, dtype=float)


# --- steps --------------------------------------------------------------------------------

def test_fixed_decimals_line_reads_like_the_row_inference():
    line = [1.25, 3.5, 2.75, 4.0, 0.0, math.nan]
    got, want = _effective_line_quantums(line), _effective_row_quantums(line)
    assert [g if not math.isnan(g) else "nan" for g in got] == [w if not math.isnan(w) else "nan" for w in want]


def test_fixed_significant_figures_line_reads_each_cell_at_its_magnitude():
    line = [123.4567, 234.5678, 45.67891, 56.78912, 67.89123]
    assert _effective_line_quantums(line) == pytest.approx([1e-4, 1e-4, 1e-5, 1e-5, 1e-5])
    # the row inference reads the three-digit cells ten times too finely
    assert _effective_row_quantums(line)[0] == pytest.approx(1e-5)


def test_a_cell_that_lost_trailing_zeros_keeps_the_lines_step():
    line = [123.4, 234.5678, 45.67891, 56.78912, 67.89123]   # 123.4 was 123.4000
    assert _effective_line_quantums(line)[0] == pytest.approx(1e-4)


def test_a_zero_reads_at_the_lines_step_and_non_finite_cells_are_nan():
    q = _effective_line_quantums([0.0, 234.5678, 45.67891, 56.78912, 67.89123, math.nan])
    assert q[0] == pytest.approx(1e-5)
    assert math.isnan(q[-1])


def test_a_cell_with_more_digits_than_the_mode_keeps_them():
    q = _effective_line_quantums([123.4567, 234.5678, 45.67891, 56.78912, 67.89123, 1 / 3])
    assert q[-1] == pytest.approx(1e-10)


def test_a_tie_keeps_fixed_decimals_where_the_readings_differ():
    assert _effective_line_quantums([12.34, 56.78, 1.234, 4.567, 8.91, 2.34]) == \
        pytest.approx([1e-2, 1e-2, 1e-3, 1e-3, 1e-2, 1e-2])


def test_whole_numbers_do_not_vote_for_significant_figures():
    assert _effective_line_quantums([50.0, 25.0, 12.5, 6.3, 3.1, 1.6]) == pytest.approx([0.1] * 6)


def test_zeros_do_not_vote():
    assert _effective_line_quantums([0, 0, 0, 0, 123.4, 234.5678, 45.67891])[4] == pytest.approx(1e-4)


def test_equally_common_figure_counts_take_the_finer_reading():
    q = _effective_line_quantums([123.4567, 45.67891, 6.789123, 12.3456, 1.23456, 345.678])
    assert q[3] == pytest.approx(1e-5)


def test_a_mostly_zero_line_reads_its_zeros_at_its_finest_step():
    q = _effective_line_quantums([0, 0, 0, 0, 0, 0.001234, 0.01234, 0.002345, 0.1234])
    assert q[:5] == pytest.approx([1e-6] * 5)
    q = _effective_line_quantums([0, 0, 0, 0, 1.23, 4.56, 7.89])          # fixed decimals
    assert q == pytest.approx([0.01] * 7)


def test_a_value_too_small_to_read_takes_the_lines_finest_step():
    assert _effective_line_quantums([0.0123, 0.000234, 3.45e-5, 1.2e-12])[3] == pytest.approx(1e-7)


def test_the_magnitude_is_taken_at_the_read_precision():
    q = _effective_line_quantums([123.4567, 234.5678, 45.67891, 56.78912, 99.99999999999])
    assert q[-1] == pytest.approx(1e-4)


def test_a_line_with_no_non_zero_cell_keeps_the_row_step():
    assert _effective_line_quantums([0.0, 0.0, math.nan])[:2] == [1.0, 1.0]


def test_a_blank_cell_does_not_set_a_zeros_step():
    assert _effective_line_quantums([math.nan, 0.0, 234.5678, 45.67891, 56.78912])[1] == pytest.approx(1e-5)
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 收集阶段 ImportError（`_effective_line_quantums` 等尚未定义）。

- [ ] **Step 3: 在锚点之后加入常数与 `_effective_line_quantums`**

```python
# ---------- local pair runs ----------
# docs/superpowers/specs/2026-10-15-local-pair-runs-design.md
#
# Two lines of one block -- two rows or two columns -- related as B = k*A or B = A + c on
# part of the line and departing from that constant elsewhere. A separate pass: it reads the
# sheet and no detector's candidate pools, counters or budgets. It does share the short-row
# detector's score bar (`_LOCAL_RUN_MIN_BITS` defaults to `_SHORT_ROW_MIN_PREDICTION_BITS`)
# and, through `_ratio_prediction_bits`, its per-cell cap, so the two score alike.
_LOCAL_RUN_MIN_BITS = float(os.environ.get(
    "PAPERCONAN_LOCAL_RUN_MIN_BITS", str(_SHORT_ROW_MIN_PREDICTION_BITS)))
# The median departure outside a stretch must exceed this: for a ratio as a share of the line's
# median |b|, for an offset as a share of |c|. A relation holding across the whole line has no
# outside and is left to the whole-line detectors.
_LOCAL_RUN_OUT_DEV = float(os.environ.get("PAPERCONAN_LOCAL_RUN_OUT_DEV", "0.05"))
# Cell operations per block; when spent the pass stops for that block and says so in coverage.
_LOCAL_RUN_BUDGET = int(os.environ.get("PAPERCONAN_LOCAL_RUN_BUDGET", "4000000"))
# Outside departures are capped here so a median stays finite and JSON-safe.
_LOCAL_RUN_DEPARTURE_CAP = 1e3
# Fewer distinct source values than this cannot tell one constant from a coincidence of rounding.
_LOCAL_RUN_MIN_DISTINCT_SOURCE = 3
# A STRETCH: the longest segment must hold at least this share of the positions one constant
# accounts for. Consistency scattered along the line is what noise makes of a relation that
# holds approximately everywhere (a curve family's proportional columns), not a copied stretch.
_LOCAL_RUN_MIN_CONTIGUOUS_SHARE = 0.5
# The constant must clear the identity -- an offset of 0, a ratio of 1 -- by this many rounding
# steps. Within a few steps, rounding a slowly varying quantity makes a stretch "constant" that
# no copy produced; and a small offset reads as a ratio near 1, so both arms ask it.
_LOCAL_RUN_MIN_IDENTITY_STEPS = 10
# A copied stretch ends abruptly. Walking outward from each end of the longest segment, past
# blank cells and the group's own positions, the first cell with data must depart from the
# constant by at least this many of its own rounding steps beyond rounding; a side whose
# first such cell confirms nothing (in the ratio arm, zero against zero) is not judged. A
# rounding plateau on a smooth trend, and the noise exceptions of a relation holding
# approximately everywhere, depart by less.
_LOCAL_RUN_MIN_EDGE_STEPS = float(os.environ.get("PAPERCONAN_LOCAL_RUN_MIN_EDGE_STEPS", "3"))
# A line needs the distinct source values a stretch asks for, and one finite cell outside it.
_LOCAL_RUN_MIN_FINITE = _LOCAL_RUN_MIN_DISTINCT_SOURCE + 1
# example_cells lists both sides of each position; bounded like every other evidence list.
_LOCAL_RUN_MAX_EXAMPLE_POSITIONS = 64


def _effective_line_quantums(values):
    """The step each cell of a line was RECORDED at, deciding per line between a
    fixed-decimals export and a fixed-significant-figures one.

    `_effective_row_quantums` assumes fixed decimals: a line's commonest decimal count is
    every cell's floor. An export at fixed significant figures breaks that -- at seven
    figures 123.4567 carries four decimals and 45.67891 five, and reading the first at
    1e-5 makes its rounding interval ten times too narrow. So the line is read both ways
    and the mode more cells match exactly decides; a tie keeps fixed decimals. Under fixed
    decimals every cell that reads as non-zero gets what `_effective_row_quantums` gives it.

    Only cells with a fractional part vote. A zero has no magnitude, and a whole number's
    figure count is its digit count whatever it was written to (50 reads as two figures),
    so neither is evidence for either reading. Between two equally common figure counts the
    larger, i.e. the finer reading, is taken.

    Under fixed significant figures a cell's step comes from its magnitude and is never
    COARSER than the cell's own decimals, since a cell carrying more digits than the mode
    has real extra precision (a whole number with trailing zeros is the exception: 98770 at
    four figures is read at 1, not 10, which is the stricter direction). Magnitudes and
    digit counts are both taken at `_MAX_READ_DECIMALS` read precision.

    A cell that reads as zero at that precision -- a zero, or a value too small to register --
    gets the finest step on its line, under either reading: at least as fine as its neighbours',
    the stricter reading. Rounding to n figures never yields 0, and an export at fixed decimals
    wrote its 0 no coarser than its neighbours; `_effective_row_quantums` instead lets zeros
    vote, so a mostly-zero line reads them at 1, a rounding interval wide enough to pass for any
    constant. Only when no cell of the line reads as non-zero does a zero keep the row
    inference's step. Non-finite cells get NaN.
    """
    by_decimals = _effective_row_quantums(values)
    cells = []
    for v in values:
        fv = float(v)
        if not math.isfinite(fv) or fv == 0.0:
            continue
        sig, dec = _sigfigs_and_frac(fv)
        dec = min(dec, _MAX_READ_DECIMALS)
        if dec == 0:
            continue
        cells.append((sig, dec))
    use_figures = False
    if cells:
        decimals = Counter(d for _, d in cells)
        sigfigs = Counter(s for s, _ in cells)
        sig_mode, sig_top = max(sigfigs.items(), key=lambda kv: (kv[1], kv[0]))
        use_figures = sig_top > max(decimals.values())
    out, unread = [], []
    for i, (v, q) in enumerate(zip(values, by_decimals)):
        fv = float(v)
        if not math.isfinite(fv):
            out.append(math.nan)
            continue
        read = round(abs(fv), _MAX_READ_DECIMALS)
        if read == 0.0:
            out.append(math.nan)
            unread.append(i)
            continue
        if use_figures:
            own = 10.0 ** -min(_sigfigs_and_frac(fv)[1], _MAX_READ_DECIMALS)
            out.append(min(own, 10.0 ** (math.floor(math.log10(read)) - sig_mode + 1)))
        else:
            out.append(q)
    steps = [x for x in out if not math.isnan(x)]      # unread cells are still NaN here
    finest = min(steps) if steps else None
    for i in unread:
        out[i] = finest if finest is not None else by_decimals[i]
    return out
```

测试文件的导入行在每个 Task 里只列出当时已经定义的名字，后续 Task 逐步追加；到 Task 6 结束时，导入行为：

```python
from paperconan._audit import (
    _best_local_group, _effective_line_quantums, _effective_row_quantums,
    _group_constant_runs, _outside_departure, _scan_interval_runs, _segments,
    _symmetric_offset_interval, _symmetric_ratio_interval, _zero_source_miss, detect_local_pair_runs,
)
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 14 passed.

- [ ] **Step 5: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): read a line's recorded step whether it was exported at fixed decimals or fixed figures"
```

---
### Task 2: 两侧都舍入的比例、差值区间

**Files:**
- Modify: `src/paperconan/_audit.py`（接在 `_effective_line_quantums` 之后）
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（追加到测试文件；导入行加入 `_symmetric_ratio_interval, _symmetric_offset_interval, _zero_source_miss`；文件顶部 `import math` 之后加 `import random`）

```python
# --- intervals ----------------------------------------------------------------------------

def test_symmetric_ratio_interval_spans_both_roundings():
    lo, hi = _symmetric_ratio_interval(2.0, 3.0, 0.1, 0.1)
    assert lo == pytest.approx(2.95 / 2.05) and hi == pytest.approx(3.05 / 1.95)


def test_symmetric_ratio_interval_with_a_negative_source_is_ordered():
    lo, hi = _symmetric_ratio_interval(-2.0, 3.0, 0.1, 0.1)
    assert lo <= hi < 0


def test_zero_source_confirms_nothing_against_zero_and_breaks_a_run_otherwise():
    assert _symmetric_ratio_interval(0.0, 0.0, 0.1, 0.1) == (-math.inf, math.inf)
    assert _symmetric_ratio_interval(0.0, 1.0, 0.1, 0.1) is None
    assert _symmetric_ratio_interval(math.nan, 1.0, 0.1, 0.1) is None
    assert _symmetric_ratio_interval(2.0, 3.0, math.nan, 0.1) is None


def test_symmetric_ratio_interval_sign_cases():
    assert _symmetric_ratio_interval(-2.0, 3.0, 0.1, 0.1) == pytest.approx((3.05 / -1.95, 2.95 / -2.05))
    assert _symmetric_ratio_interval(-2.0, -3.0, 0.1, 0.1) == pytest.approx((2.95 / 2.05, 3.05 / 1.95))
    assert _symmetric_ratio_interval(5.0, 0.0, 0.1, 0.1) == pytest.approx((-0.05 / 4.95, 0.05 / 4.95))


def test_symmetric_ratio_interval_matches_a_brute_force_oracle():
    rng = random.Random(20261016)
    for _ in range(300):
        a = rng.choice((-1.0, 1.0)) * rng.uniform(0.2, 9.0)
        b = rng.uniform(-9.0, 9.0)
        qa, qb = rng.choice((0.1, 0.01)), rng.choice((0.1, 0.01))
        lo, hi = _symmetric_ratio_interval(a, b, qa, qb)
        grid = [(b + qb * (j / 20 - 0.5)) / (a + qa * (i / 20 - 0.5)) for i in range(21) for j in range(21)]
        assert lo == pytest.approx(min(grid), rel=1e-12) and hi == pytest.approx(max(grid), rel=1e-12)


def test_a_source_read_as_zero_is_written_only_by_a_large_enough_constant():
    assert _zero_source_miss(0.1, 0.1, 0.1, 2.0) == pytest.approx(0.0)
    assert _zero_source_miss(0.3, 0.1, 0.1, 2.0) == pytest.approx(0.25 - 0.1)
    assert _zero_source_miss(-0.2, 0.1, 0.1, 3.0) == pytest.approx(0.0)
    assert _zero_source_miss(0.04, 0.1, 0.1, 0.0) == pytest.approx(0.0)      # target box holds 0
    assert _zero_source_miss(0.3, 0.1, 0.1, -2.0) == pytest.approx(0.15)     # either sign of k
    for args in ((math.nan, 0.1, 0.1, 2.0), (0.3, math.nan, 0.1, 2.0), (0.3, 0.1, math.nan, 2.0), (0.3, 0.1, 0.1, math.nan)):
        assert _zero_source_miss(*args) == math.inf


def test_symmetric_offset_interval():
    lo, hi = _symmetric_offset_interval(2.0, 5.0, 0.1, 0.01)
    assert lo == pytest.approx(3.0 - 0.055) and hi == pytest.approx(3.0 + 0.055)
    assert _symmetric_offset_interval(2.0, math.nan, 0.1, 0.1) is None
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError（区间函数尚未定义）。

- [ ] **Step 3: 实现**

```python
def _symmetric_ratio_interval(a, b, q_a, q_b):
    """Which k could have written both cells, when EACH was rounded to its own step?

    `_directed_ratio_interval` takes the source as exact. Two lines rounded separately
    from one higher-precision original break that: the source's rounding, multiplied
    by k, can exceed half the target's step. Here both cells admit their rounding
    interval and k ranges over the ratios of the corners.

    Returns (lo, hi); (-inf, inf) when both cells are indistinguishable from zero --
    compatible with every k, so it confirms nothing; None when a value or a step is not
    finite, or when the source is indistinguishable from zero and the target is not: then
    only a large enough |k| writes the pair, two rays that no interval holds (see
    `_zero_source_miss`). None breaks a run.
    """
    av, bv, qa, qb = float(a), float(b), float(q_a), float(q_b)
    if not all(math.isfinite(x) for x in (av, bv, qa, qb)):
        return None
    a_lo, a_hi = av - qa / 2.0, av + qa / 2.0
    b_lo, b_hi = bv - qb / 2.0, bv + qb / 2.0
    if a_lo <= 0.0 <= a_hi:
        return (-math.inf, math.inf) if b_lo <= 0.0 <= b_hi else None
    corners = (b_lo / a_lo, b_lo / a_hi, b_hi / a_lo, b_hi / a_hi)
    return (min(corners), max(corners))


def _symmetric_offset_interval(a, b, q_a, q_b):
    """Which c could have written both cells as B = A + c, each rounded to its own step.
    None when a value is not finite."""
    av, bv, qa, qb = float(a), float(b), float(q_a), float(q_b)
    if not all(math.isfinite(x) for x in (av, bv, qa, qb)):
        return None
    half = (qa + qb) / 2.0
    return (bv - av - half, bv - av + half)


def _zero_source_miss(b, q_a, q_b, k):
    """For a source that reads as zero -- its value lies in [-q_a/2, q_a/2] -- by how much, in
    units of B, the constant k falls short of writing `b`: 0 once |k|*q_a/2 reaches B's own
    rounding interval. Such a pair is written by every k beyond a threshold in either
    direction, two rays rather than an interval, so `_symmetric_ratio_interval` returns None
    for it and the decision asks this instead, against a constant already found. A value,
    step or constant that is not finite gives inf: a miss that cannot be measured never
    reads as written."""
    bv, qa, qb, kv = float(b), float(q_a), float(q_b), float(k)
    if not all(math.isfinite(x) for x in (bv, qa, qb, kv)):
        return math.inf
    b_lo, b_hi = bv - qb / 2.0, bv + qb / 2.0
    distance = 0.0 if b_lo <= 0.0 <= b_hi else min(abs(b_lo), abs(b_hi))
    return max(0.0, distance - abs(kv) * qa / 2.0)
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 21 passed.

- [ ] **Step 5: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): bound a ratio and an offset when both cells were rounded"
```

---
### Task 3: 区间求段、分段与按常数分组

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（导入行加入 `_scan_interval_runs, _segments, _group_constant_runs`）

```python
# --- runs and groups ----------------------------------------------------------------------

def test_scan_interval_runs_breaks_on_none_and_drops_sub_runs():
    spans = [(1.0, 2.0), (1.5, 3.0), None, (4.0, 5.0), (4.5, 6.0), (4.8, 4.9)]
    runs = _scan_interval_runs(spans, [1, 2, 3, 4, 5, 6], min_distinct_source=2)
    assert [(r["start"], r["end"]) for r in runs] == [(0, 1), (3, 5)]
    assert runs[1]["lo"] == pytest.approx(4.8) and runs[1]["hi"] == pytest.approx(4.9)


def test_an_unbounded_span_joins_a_run_without_informing_it():
    spans = [(1.0, 2.0), (-math.inf, math.inf), (1.5, 2.5)]
    (run,) = _scan_interval_runs(spans, [1, 2, 3], min_distinct_source=2)
    assert (run["start"], run["end"]) == (0, 2) and run["informative"] == [0, 2]


def test_runs_of_one_constant_form_one_group_with_the_lone_cells_between():
    spans = [(1.9, 2.1)] * 3 + [(5.0, 6.0), (1.95, 2.05), (7.0, 8.0)] + [(1.9, 2.1)] * 3
    runs = _scan_interval_runs(spans, list(range(9)))
    (group, *others) = _group_constant_runs(runs, spans)
    assert group["positions"] == [0, 1, 2, 4, 6, 7, 8]
    assert group["segments"] == [[0, 2], [4, 4], [6, 8]]
    assert _segments([0, 1, 3]) == [[0, 1], [3, 3]]


def test_a_cell_without_an_interval_joins_a_group_only_when_admitted():
    spans = [(1.9, 2.1)] * 3 + [None] + [(1.9, 2.1)] * 2
    runs = _scan_interval_runs(spans, list(range(6)), min_distinct_source=2)
    (group,) = _group_constant_runs(runs, spans)
    assert group["positions"] == [0, 1, 2, 4, 5]
    (group,) = _group_constant_runs(runs, spans, admit=lambda i, lo, hi: i == 3 and lo <= 2.0 <= hi)
    assert group["positions"] == [0, 1, 2, 3, 4, 5] and group["segments"] == [[0, 5]]
    assert 3 not in group["informative"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**

```python
def _scan_interval_runs(spans, source, min_informative=2, min_distinct_source=None):
    """Maximal contiguous runs whose intervals share a point.

    The run logic of `_scan_quantized_ratio_runs`, taking the intervals ready-made so the
    ratio and the offset arm share it: `None` breaks a run, (-inf, inf) joins one without
    informing it, a run needs `min_informative` informative cells and
    `min_distinct_source` distinct source values, and a run inside another is dropped.
    Returns [{"start", "end", "lo", "hi", "informative"}] in order of start. The distinct-source
    floor defaults to `_LOCAL_RUN_MIN_DISTINCT_SOURCE`, read at call time.
    """
    if min_distinct_source is None:
        min_distinct_source = _LOCAL_RUN_MIN_DISTINCT_SOURCE
    n = len(spans)
    found = []
    for start in range(n):
        lo, hi = -math.inf, math.inf
        informative, seen = [], set()
        end = start - 1
        for j in range(start, n):
            span = spans[j]
            if span is None:
                break
            new_lo, new_hi = max(lo, span[0]), min(hi, span[1])
            if new_lo > new_hi:
                break
            lo, hi, end = new_lo, new_hi, j
            if math.isfinite(span[0]) and math.isfinite(span[1]):
                informative.append(j)
                seen.add(float(source[j]))
        if end < start or len(informative) < min_informative or len(seen) < min_distinct_source:
            continue
        found.append({"start": start, "end": end, "lo": lo, "hi": hi, "informative": informative})
    maximal = []
    for r in found:
        if any(o is not r and o["start"] <= r["start"] and o["end"] >= r["end"]
               and (o["start"], o["end"]) != (r["start"], r["end"]) for o in found):
            continue
        if any((m["start"], m["end"]) == (r["start"], r["end"]) for m in maximal):
            continue
        maximal.append(r)
    return maximal


def _segments(positions):
    """[[start, end], ...] of the contiguous stretches in a sorted list of positions."""
    out = []
    for p in positions:
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return out


def _group_constant_runs(runs, spans, admit=None):
    """Gather the runs one constant accounts for.

    Longest first, a run joins the first group whose interval it still intersects and
    none of whose runs it overlaps; the group's interval narrows to the intersection. A
    group's POSITIONS are then every cell whose interval admits that constant, in a run
    or not -- a lone consistent cell between two runs is the same relation, not its
    outside. A cell with no interval (None) joins only if `admit(i, lo, hi)` says the constant
    writes it -- a source read as zero, which one interval cannot describe. Returns
    [{"lo", "hi", "positions", "informative", "segments"}].
    """
    groups = []
    for r in sorted(runs, key=lambda r: (-(r["end"] - r["start"]), r["start"])):
        for g in groups:
            lo, hi = max(g["lo"], r["lo"]), min(g["hi"], r["hi"])
            if lo <= hi and all(r["end"] < s["start"] or r["start"] > s["end"] for s in g["runs"]):
                g["lo"], g["hi"] = lo, hi
                g["runs"].append(r)
                break
        else:
            groups.append({"lo": r["lo"], "hi": r["hi"], "runs": [r]})
    out = []
    for g in groups:
        positions = [i for i, s in enumerate(spans)
                     if (s is not None and s[0] <= g["hi"] and s[1] >= g["lo"])
                     or (s is None and admit is not None and admit(i, g["lo"], g["hi"]))]
        # an admitted cell without an interval says only that |k| clears a threshold: not informative
        informative = [i for i in positions if spans[i] is not None
                       and math.isfinite(spans[i][0]) and math.isfinite(spans[i][1])]
        out.append({"lo": g["lo"], "hi": g["hi"], "positions": positions,
                    "informative": informative, "segments": _segments(positions)})
    return out
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 25 passed.

- [ ] **Step 5: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): gather the stretches one constant accounts for"
```

---
### Task 4: 组外偏离、离开恒等、边缘与线的可用性

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（导入行加入 `_outside_departure`）

```python
# --- outside departure ---------------------------------------------------------------------

def test_outside_departure_measures_a_source_read_as_zero_by_its_miss():
    a, b = _arr([1.0, 2.0, 3.0, 0.0]), _arr([2.0, 4.0, 6.0, 5.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # k = 2 writes 0 +/- 0.05 as at most 0.1; B's box starts at 4.95: a miss of 4.85 over median |b| 4.5
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx(4.85 / 4.5)


def test_outside_departure_is_none_for_a_whole_line_relation():
    a, b = _arr([1.0, 2.0, 3.0]), _arr([2.0, 4.0, 6.0])
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    q = [0.1] * 3
    assert _outside_departure(a, b, q, q, {"lo": 1.9, "hi": 2.1, "positions": [0, 1, 2]}, spans, "ratio") is None
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**（四个函数一起加入；后三个由 Task 5 的判定测试覆盖）

```python
def _outside_departure(a_line, b_line, q_a, q_b, group, spans, relation):
    """Median departure from the group's constant at the line's other positions, beyond
    what each cell's own rounding explains.

    The gap is from the constant to the cell's interval, so rounding alone never counts.
    Ratio: that gap as an amount of B (gap * |a|) over the line's median |b| -- measured
    against the line's scale, not the cell's, so additive noise on a near-zero cell is
    not read as a departure (curve families leak through their smallest cell otherwise).
    Offset: the gap over |c|. A source read as zero (no interval) departs by what the constant
    falls short of writing it (`_zero_source_miss`), over the same scale. Departures are capped
    at `_LOCAL_RUN_DEPARTURE_CAP`. None when the line has no finite position outside the
    group: the relation covers the whole line.
    """
    k = (group["lo"] + group["hi"]) / 2.0
    if k == 0.0 or not math.isfinite(k):
        return None
    inside = set(group["positions"])
    finite_b = [abs(float(v)) for v in b_line if math.isfinite(float(v))]
    scale = float(np.median(finite_b)) if finite_b else 0.0
    departures = []
    for i, span in enumerate(spans):
        if i in inside:
            continue
        a, b = float(a_line[i]), float(b_line[i])
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        if span is None:                      # a source read as zero (ratio arm)
            miss = _zero_source_miss(b, q_a[i], q_b[i], k)
            departures.append(min(_LOCAL_RUN_DEPARTURE_CAP, miss / scale) if scale > 0
                              else _LOCAL_RUN_DEPARTURE_CAP)
            continue
        lo, hi = span
        if not (math.isfinite(lo) and math.isfinite(hi)):
            continue
        gap = lo - k if k < lo else (k - hi if k > hi else 0.0)
        if relation == "ratio":
            d = abs(gap) * abs(a) / scale if scale > 0 else _LOCAL_RUN_DEPARTURE_CAP
        else:
            d = abs(gap / k)
        departures.append(min(_LOCAL_RUN_DEPARTURE_CAP, d))
    return float(np.median(departures)) if departures else None


def _identity_steps(spans, informative, constant, relation):
    """How many rounding steps the constant lies from the identity -- 1 for a ratio, 0 for an
    offset -- measured in the half-width of the group's own intervals (their median over the
    informative cells). Read off the intervals, so no cell value is divided by."""
    half = float(np.median([(spans[i][1] - spans[i][0]) / 2.0 for i in informative]))
    identity = 1.0 if relation == "ratio" else 0.0
    return abs(constant - identity) / half if half > 0 else math.inf


def _edge_steps(a_line, b_line, q_a, q_b, group, spans, relation):
    """Departure, in the cell's own rounding steps and beyond rounding, of the first cell
    past each end of the group's longest segment -- walking outward over blank cells (a
    panel separator) and over the group's own positions, so the cell judged is the data
    that actually adjoins the stretch. [] when nothing lies outside on either side. A source
    read as zero counts by what the constant falls short of writing it, in B's steps."""
    start, end = max(group["segments"], key=lambda se: (se[1] - se[0], -se[0]))
    inside = set(group["positions"])
    k = (group["lo"] + group["hi"]) / 2.0
    out = []
    for i, stride in ((start - 1, -1), (end + 1, 1)):
        while 0 <= i < len(spans) and (i in inside or not (
                math.isfinite(float(a_line[i])) and math.isfinite(float(b_line[i])))):
            i += stride
        if not 0 <= i < len(spans):
            continue
        span = spans[i]
        if span is None:                          # a source read as zero (ratio arm)
            miss = _zero_source_miss(b_line[i], q_a[i], q_b[i], k)
            out.append(miss / q_b[i] if q_b[i] > 0 else math.inf)
            continue
        lo, hi = span
        if not (math.isfinite(lo) and math.isfinite(hi)):
            continue
        gap = lo - k if k < lo else (k - hi if k > hi else 0.0)
        if relation == "ratio":
            out.append(abs(gap) * abs(float(a_line[i])) / q_b[i] if q_b[i] > 0 else math.inf)
        else:
            step = (q_a[i] + q_b[i]) / 2.0
            out.append(abs(gap) / step if step > 0 else math.inf)
    return out


def _local_line_usable(values, label=None):
    """Enough finite cells for a stretch and an outside, not an axis by its row label, and
    not itself an arithmetic progression (a time or dose grid)."""
    finite = values[np.isfinite(values)]
    if finite.size < _LOCAL_RUN_MIN_FINITE:
        return False
    if label and _AXIS_CONTEXT_LABEL_RE.search(label):
        return False
    steps = np.diff(finite)
    if steps[0] != 0.0 and np.allclose(steps, steps[0], rtol=1e-9, atol=0.0):
        return False
    return True
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 27 passed.

- [ ] **Step 5: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): measure how a stretch departs outside itself, beyond rounding"
```

---
### Task 5: 判定：一对线上得分最高的合格组

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（导入行加入 `_best_local_group`；先加一个辅助函数）

```python
# --- the decision -------------------------------------------------------------------------

def _best(a, b, n_tests=4):
    a, b = _arr(a), _arr(b)
    return _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), n_tests)


def test_a_stretch_of_a_scaled_copy_is_reported():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, bits, departure = _best(A10, b)
    assert relation == "ratio" and group["positions"] == [0, 1, 2, 3, 4, 5]
    assert (group["lo"] + group["hi"]) / 2 == pytest.approx(1.137, rel=1e-4)
    assert departure > 0.05


def test_a_relation_over_the_whole_line_is_left_to_the_whole_line_detectors():
    assert _best(A10, [round(x * 1.137, 3) for x in A10]) is None


def test_a_stretch_of_a_shifted_copy_is_reported_at_one_decimal():
    w = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    shifted = [round(x + 3.0, 1) for x in w[:8]] + [22.0, 25.9]
    relation, group, _bits, _dep = _best(w, shifted)
    assert relation == "offset" and group["positions"] == list(range(8))


def test_two_stretches_of_one_constant_are_one_finding():
    a = A10 + [10.051, 12.884]
    b = ([round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1]
         + [round(x * 0.6, 3) for x in a[9:]])
    relation, group, _bits, _dep = _best(a, b)
    assert relation == "ratio" and group["segments"] == [[0, 4], [9, 11]]


def test_near_proportional_curve_rows_are_not_a_stretch():
    resp = [10 / (1 + math.exp(-(-3 + 6 * j / 7))) for j in range(8)]
    assert _best([round(1.2 * v, 2) for v in resp], [round(1.5 * v, 2) for v in resp], 400) is None


@pytest.mark.parametrize("k", [10.0, 1.0])
def test_a_unit_restatement_or_an_identity_is_not_reported(k):
    b = [round(x * k, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    assert _best(A10, b) is None


def test_a_short_coarse_stretch_does_not_carry_enough_information():
    a = [2.1, 3.4, 5.2, 4.4, 6.1, 3.9]
    assert _best(a, [round(x * 2.5, 2) for x in a[:3]] + [8.0, 9.3, 11.1]) is None


def test_consistency_scattered_along_the_line_is_not_a_stretch():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):           # three breaks, stretches of three
        b[i] = v
    assert _best(a, b) is None


def test_a_constant_a_few_rounding_steps_from_the_identity_is_not_reported():
    w = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    shifted = [round(x + 0.3, 1) for x in w[:8]] + [22.0, 25.9]
    assert _best(w, shifted) is None


def test_a_plateau_with_gradual_edges_is_not_reported():
    a = [10.0, 11.3, 12.5, 13.6, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, 22.6, 23.8]
    drift = [1.0, 1.1, 1.2, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.6]
    assert _best(a, [round(x + d, 1) for x, d in zip(a, drift)]) is None


def test_the_edge_is_read_past_a_blank_separator():
    a = [10.0, 11.3, 12.5, 13.6, math.nan, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, math.nan, 22.6, 23.8]
    drift = [1.0, 1.1, 1.2, 1.3, 0.0, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 0.0, 1.5, 1.6]
    b = [x + d if math.isfinite(x) else math.nan for x, d in zip(a, drift)]
    assert _best(a, [round(v, 1) if math.isfinite(v) else v for v in b]) is None


def test_a_stretch_whose_outside_barely_departs_is_not_reported():
    # the curve-family shape: the outside is off the constant by ~2%, far beyond rounding
    b = [round(x * 1.137, 3) for x in A10[:6]] + [round(x * 1.137 * 1.02, 3) for x in A10[6:]]
    assert _best(A10, b) is None


def test_a_formula_cached_cell_cannot_carry_the_score(monkeypatch):
    import paperconan._audit as audit_module
    a = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    b = [round(x + 3.0, 1) for x in a[:8]] + [22.0, 25.9]
    plain = _best(a, b)[2]
    a[3] += 1e-9          # one cell on each side stored at full float precision
    b[3] += 1e-9
    cap = audit_module._SHORT_ROW_MAX_QUANTUM_RATIO
    assert _best(a, b)[2] - plain <= math.log2(cap) + 1e-6
    monkeypatch.setattr(audit_module, "_SHORT_ROW_MAX_QUANTUM_RATIO", 1e12)
    assert _best(a, b)[2] - plain > math.log2(cap) + 1      # the cap is what bounds it


def test_a_whole_line_relation_with_a_source_rounded_to_zero_is_not_a_stretch():
    true = [0.04, 1.37, 2.81, 0.66, 1.92, 2.44, 0.83, 1.58, 2.17, 1.05, 2.69, 0.31]
    a, b = [round(x, 1) for x in true], [round(2.5 * x, 1) for x in true]
    assert a[0] == 0.0 and b[0] == 0.1
    assert _best(a, b) is None and _best(b, a) is None


def test_a_source_read_as_zero_that_the_constant_cannot_write_departs():
    a = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 0.0]
    b = [round(x * 0.96, 3) for x in a[:8]] + [21.6]
    relation, group, _bits, departure = _best(a, b)
    assert relation == "ratio" and group["positions"] == list(range(8)) and departure > 0.05


def test_a_source_read_as_zero_inside_a_stretch_joins_it_when_the_constant_writes_it():
    true = [3.42, 1.87, 0.04, 2.95, 4.11, 3.06, 2.58, 1.73]
    a = [round(x, 1) for x in true] + [3.9, 2.2, 4.6, 1.4]
    b = [round(2.5 * x, 2) for x in true] + [5.3, 9.1, 3.2, 6.6]
    assert a[2] == 0.0 and b[2] == 0.1
    relation, group, _bits, departure = _best(a, b)
    assert relation == "ratio" and group["positions"] == list(range(8)) and departure > 0.05


def test_a_source_read_as_zero_that_the_constant_barely_misses_is_not_an_abrupt_edge():
    true = [3.42, 1.87, 2.95, 4.11, 3.06, 2.58, 1.73, 3.37]
    a = [round(x, 1) for x in true] + [0.0, 3.9, 2.2, 4.6]
    b = [round(2.5 * x, 2) for x in true] + [0.15, 5.3, 9.1, 3.2]
    assert _best(a, b) is None


def test_a_sign_flip_and_a_stretch_of_zeros_are_not_reported():
    # A sign-flipped copy: its interval holds -1, a power of ten, so it is left with the unit
    # restatements -- a recorded gap rather than a judgement. A stretch of zeros: its constant
    # is 0, which carries no information on the zero side and leaves no departure to measure;
    # not a relation this pass looks for, and three of its tests refuse it independently.
    assert _best(A10, [round(-x, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]) is None
    assert _best(A10, [0.0] * 6 + [11.9, 9.1, 18.7, 6.2]) is None


def test_a_zero_target_inside_a_stretch_is_scored_without_dividing_by_it():
    a = [100.37, 2.11, 85.42, 1.73, 63.18, 97.55, 2.46, 71.09, 55.31, 40.12, 33.87, 12.64]
    b = [round(0.002 * x, 2) for x in a[:9]] + [0.35, 0.91, 0.47]
    _best(a, b)          # must not raise
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**

```python
def _best_local_group(a_line, b_line, q_a, q_b, n_tests):
    """The highest-scoring group passing every test of the design's section 4, as
    (relation, group, bits, departure), or None."""
    best = None
    for relation, interval in (("ratio", _symmetric_ratio_interval),
                               ("offset", _symmetric_offset_interval)):
        spans = [interval(a, b, x, y) for a, b, x, y in zip(a_line, b_line, q_a, q_b)]
        admit = None
        if relation == "ratio":
            def admit(i, lo, hi):
                a, b = float(a_line[i]), float(b_line[i])
                return (math.isfinite(a) and math.isfinite(b)
                        and _zero_source_miss(b, q_a[i], q_b[i], max(abs(lo), abs(hi))) == 0.0)
        for g in _group_constant_runs(_scan_interval_runs(spans, a_line), spans, admit):
            lo, hi = g["lo"], g["hi"]
            if relation == "ratio" and (lo <= 1.0 <= hi or _interval_holds_a_power_of_ten(lo, hi)):
                continue
            if relation == "offset" and lo <= 0.0 <= hi:
                continue
            inf = g["informative"]
            if len({float(a_line[i]) for i in inf}) < _LOCAL_RUN_MIN_DISTINCT_SOURCE:
                continue
            longest = max(e - s + 1 for s, e in g["segments"])
            if longest < _LOCAL_RUN_MIN_CONTIGUOUS_SHARE * len(g["positions"]):
                continue
            if _identity_steps(spans, inf, (lo + hi) / 2.0, relation) < _LOCAL_RUN_MIN_IDENTITY_STEPS:
                continue
            # Scored with each side as the target, keeping the lower: neither side is
            # privileged when both were rounded.
            bits = min(
                _ratio_prediction_bits([b_line[i] for i in inf], [q_b[i] for i in inf], n_tests)["bits"],
                _ratio_prediction_bits([a_line[i] for i in inf], [q_a[i] for i in inf], n_tests)["bits"])
            if bits < _LOCAL_RUN_MIN_BITS:
                continue
            departure = _outside_departure(a_line, b_line, q_a, q_b, g, spans, relation)
            if departure is None or departure <= _LOCAL_RUN_OUT_DEV:
                continue
            if any(e < _LOCAL_RUN_MIN_EDGE_STEPS
                   for e in _edge_steps(a_line, b_line, q_a, q_b, g, spans, relation)):
                continue
            key = (bits, len(g["positions"]), relation == "ratio")
            if best is None or key > best[0]:
                best = (key, relation, g, bits, departure)
    return None if best is None else best[1:]
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 46 passed.

- [ ] **Step 5: 逐条核对“关掉即变红”**

每条判据关掉后，守它的那条测试必须失败。在仓库根目录逐条运行（每行一条命令，预期每条都输出 `1 failed`）：

```bash
for spec in "_LOCAL_RUN_MIN_CONTIGUOUS_SHARE=0:test_consistency_scattered_along_the_line_is_not_a_stretch" \
            "_LOCAL_RUN_MIN_IDENTITY_STEPS=0:test_a_constant_a_few_rounding_steps_from_the_identity_is_not_reported" \
            "_LOCAL_RUN_MIN_EDGE_STEPS=0:test_a_plateau_with_gradual_edges_is_not_reported" \
            "_LOCAL_RUN_MIN_EDGE_STEPS=0:test_the_edge_is_read_past_a_blank_separator" \
            "_LOCAL_RUN_OUT_DEV=0:test_a_stretch_whose_outside_barely_departs_is_not_reported" \
            "_LOCAL_RUN_MIN_BITS=0:test_a_short_coarse_stretch_does_not_carry_enough_information"; do
  c=${spec%%:*}; t=${spec#*:}
  .venv/bin/python -c "import sys, pytest, paperconan._audit as a; setattr(a, '${c%%=*}', float('${c#*=}')); sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_local_pair_runs.py::$t']))" | tail -1
done
```

Expected: 六行都是 `1 failed`。任何一行 `1 passed`，说明那条测试没有守住它的判据，先修测试再继续。

- [ ] **Step 6: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): decide whether a pair of lines holds a constant on one stretch only"
```

---
### Task 6: 检测器驱动与 finding

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（导入行加入 `detect_local_pair_runs`）

```python
# --- the detector -------------------------------------------------------------------------

def _block(cols, header=None):
    header = header or [f"Group {chr(65 + j)}" for j in range(len(cols))]
    rows = [header] + [[c[i] for c in cols] for i in range(len(cols[0]))]
    return Sheet.from_rows(rows), header


def test_detector_reports_a_column_pair_with_its_cells_and_orientation():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, header)
    assert f["kind"] == "local_ratio_run" and f["orientation"] == "cols"
    assert (f["col_a_idx"], f["col_b_idx"]) == (0, 1)
    assert f["segments"] == [[2, 7]]                       # sheet rows, 1-based
    assert sorted({r for r, _ in f["example_cells"]}) == [2, 3, 4, 5, 6, 7]
    assert f["n"] == f["run_length"] == 6 and f["line_length"] == 10
    assert f["severity"] == "high" and "Group B = Group A *" in f["rule"]


def test_detector_reports_a_row_pair():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = [["", *[f"t{j}" for j in range(10)]], ["ctrl", *A10], ["drug", *b]]
    sheet = Sheet.from_rows(rows)
    (f,) = detect_local_pair_runs(sheet, 1, 3, 1, 11, [f"t{j}" for j in range(10)])
    assert f["orientation"] == "rows" and (f["row_a_idx"], f["row_b_idx"]) == (1, 2)
    assert f["segments"] == [[2, 7]]                       # sheet columns, 1-based
    assert (f["row_a"], f["row_b"]) == ("ctrl", "drug")


def test_a_pair_already_reported_is_skipped():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, already={("cols", 0, 1)}) == []


def test_skip_columns_leaves_only_rows():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, skip_columns=True) == []


def test_an_axis_row_is_not_compared():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = [["", *[f"t{j}" for j in range(10)]], ["Time (h)", *A10], ["drug", *b]]
    sheet = Sheet.from_rows(rows)
    assert detect_local_pair_runs(sheet, 1, 3, 1, 11, [f"t{j}" for j in range(10)]) == []


class _Coverage:
    def __init__(self):
        self.calls = []

    def add_limitation(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_an_exhausted_budget_is_recorded(monkeypatch):
    import paperconan._audit as audit_module
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 5)
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    cov = _Coverage()
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov) == []
    assert cov.calls and cov.calls[0][1]["detector"] == "detect_local_pair_runs"
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**

```python
def _local_run_finding(orientation, ia, ib, labels, first, a_line, b_line,
                       relation, group, bits, departure):
    positions = group["positions"]
    shown = positions[:_LOCAL_RUN_MAX_EXAMPLE_POSITIONS]
    value = (group["lo"] + group["hi"]) / 2.0
    paired = int((np.isfinite(a_line) & np.isfinite(b_line)).sum())
    segments = [[first + s + 1, first + e + 1] for s, e in group["segments"]]
    if orientation == "cols":
        cells = [[first + p + 1, i + 1] for p in shown for i in (ia, ib)]
        pair = dict(col_a=labels[ia], col_b=labels[ib], col_a_idx=ia, col_b_idx=ib,
                    col_a_sample=_sample(a_line[positions]), col_b_sample=_sample(b_line[positions]))
        where = "rows"
    else:
        cells = [[i + 1, first + p + 1] for p in shown for i in (ia, ib)]
        pair = dict(row_a=labels[ia], row_b=labels[ib], row_a_idx=ia, row_b_idx=ib,
                    row_a_sample=_sample(a_line[positions]), row_b_sample=_sample(b_line[positions]))
        where = "columns"
    if relation == "ratio":
        kind = "local_ratio_run"
        op = "*"
        constant = dict(k=value, k_lo=group["lo"], k_hi=group["hi"])
    else:
        kind = "local_offset_run"
        op = "+"
        constant = dict(offset=value, offset_lo=group["lo"], offset_hi=group["hi"])
    stretch = ", ".join(f"{s}-{e}" if s != e else f"{s}" for s, e in segments)
    return dict(
        kind=kind, orientation=orientation, **pair, **constant,
        segments=segments, run_length=len(positions), n=len(positions), line_length=paired,
        outside_departure=round(departure, 6), prediction_bits=round(bits, 2),
        example_cells=cells, example_cells_truncated=len(shown) < len(positions),
        severity="high",
        rule=(f"{labels[ib]} = {labels[ia]} {op} {value:.6g} on {where} {stretch} "
              f"({len(positions)} of {paired}); elsewhere on the line it departs by a "
              f"median {departure:.1%} beyond rounding"))


def detect_local_pair_runs(sheet, r0, r1, c0, c1, header, already=frozenset(),
                           coverage=None, skip_columns=False):
    """Two rows or two columns of one block related as B = k*A or B = A + c over part of
    the line only. docs/superpowers/specs/2026-10-15-local-pair-runs-design.md.

    Signal, not verdict: a normalisation, a unit conversion or a shared control applied
    to part of a line has the same shape. `already` holds (orientation, a, b) pairs the
    whole-line detectors reported in this block; `skip_columns` follows the `wide` flag
    the column detectors honour.
    """
    findings = []
    budget = _LOCAL_RUN_BUDGET
    for orientation in (("rows",) if skip_columns else ("cols", "rows")):
        if orientation == "cols":
            idxs = list(range(c0, c1))
            lines = {c: sheet.numeric[r0:r1, c].astype(float) for c in idxs}
            labels = {c: str(header[c - c0]) if header[c - c0] not in (None, "") else f"col {c + 1}"
                      for c in idxs}
            first, length = r0, r1 - r0
            usable = [c for c in idxs if _local_line_usable(lines[c])]
        else:
            idxs = list(range(r0, r1))
            lines = {r: sheet.numeric[r, c0:c1].astype(float) for r in idxs}
            labels = {r: _row_label(sheet, r, c0) for r in idxs}
            first, length = c0, c1 - c0
            usable = [r for r in idxs if _local_line_usable(lines[r], labels[r])]
        n_tests = max(1, len(usable) * (len(usable) - 1) // 2 * 2 * length)
        quantums = {i: _effective_line_quantums(lines[i]) for i in usable}
        for x, ia in enumerate(usable):
            for ib in usable[x + 1:]:
                budget -= length
                if budget <= 0:
                    print(f"[paperconan] detect_local_pair_runs: budget exhausted on a "
                          f"{r1 - r0}x{c1 - c0} block — coverage bounded", file=sys.stderr)
                    _note_detector_cap(coverage, "detect_local_pair_runs",
                                       "detector_compute_budget_limit")
                    return findings
                if (orientation, ia, ib) in already:
                    continue
                hit = _best_local_group(lines[ia], lines[ib], quantums[ia], quantums[ib], n_tests)
                if hit is not None:
                    findings.append(_local_run_finding(orientation, ia, ib, labels, first,
                                                       lines[ia], lines[ib], *hit))
    return findings
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 52 passed.

- [ ] **Step 5: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): report a pair of rows or columns related on one stretch only"
```

---
### Task 7: 接入块循环（端到端）

**Files:**
- Modify: `src/paperconan/_audit.py`（块循环，`rr = detect_row_relations(...)` 之后）
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写端到端测试**（追加到测试文件末尾）

```python

def test_a_scan_reports_the_stretch_in_the_relations_group(tmp_path):
    import csv
    from paperconan._audit import scan_dir
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    with open(tmp_path / "panel.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Group A", "Group B"])
        writer.writerows(zip(A10, b))
    scan = scan_dir(str(tmp_path), str(tmp_path / "out"), write_md=False, write_html=False)
    found = [f for blk in scan["relations_blocks"] for f in blk.get("relations", [])
             if f["kind"] == "local_ratio_run"]
    assert len(found) == 1
    f = found[0]
    assert f["evidence"]["highlight_rows"] == [2, 3, 4, 5, 6, 7]
    assert f["severity"] == "high" and f.get("profile_action") == "kept"
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py::test_a_scan_reports_the_stretch_in_the_relations_group -q`
Expected: FAIL，`assert len(found) == 1`（0 条：块循环还没调用新检测器）。

- [ ] **Step 3: 接入**

在块循环里找到这一行（全文唯一）：

```python
                rr = detect_row_relations(sheet, r0, r1, c0, c1, header, coverage=coverage)
```

紧接其后加入：

```python
                # Pairs related on one stretch only; the whole-line detectors above report the
                # rest, so the pairs they already hold are passed in and skipped. Column pairs
                # follow `wide` like the column detectors; row pairs always run.
                already = {("cols", f["col_a_idx"], f["col_b_idx"]) for f in rel
                           if isinstance(f.get("col_a_idx"), int) and isinstance(f.get("col_b_idx"), int)}
                already |= {("rows", f["row_a_idx"], f["row_b_idx"]) for f in rr
                            if isinstance(f.get("row_a_idx"), int) and isinstance(f.get("row_b_idx"), int)}
                local = detect_local_pair_runs(sheet, r0, r1, c0, c1, header, already=already,
                                               coverage=coverage, skip_columns=bool(wide))
                rel = rel + [f for f in local if f["orientation"] == "cols"]
                rr = rr + [f for f in local if f["orientation"] == "rows"]
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 53 passed.

- [ ] **Step 5: 跑全套测试，看有没有别处被带动**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -15`
Expected: 只有新加的测试是新增通过。若 golden 或其它检测器的测试失败，**不要更新 golden**：先看失败的 finding 是不是新检测器在 golden 数据上报出的。是的话，在 Task 12 的评估里记下它（这是本检测器在真实形态数据上的行为，要逐条判断是否属于误报），再决定是否更新 golden；更新 golden 必须单独提交，并在提交信息里说明每一条新增 finding 的性质。

- [ ] **Step 6: 提交**

```bash
git add src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(detect): run the local-run pass on every block"
```

---
### Task 8: 良性控制：派生关系、表头分流与良性说明

**Files:**
- Modify: `src/paperconan/_profiles.py`
- Modify: `src/paperconan/_audit.py`（`benign_reason`）
- Modify: `tests/test_local_pair_runs.py`

- [ ] **Step 1: 写测试**（追加到测试文件末尾）

```python

def test_a_column_stretch_is_routed_like_a_whole_column_ratio():
    from paperconan._profiles import _relation_prefilter
    f = dict(kind="local_ratio_run", orientation="cols", col_a="Body weight (g)",
             col_b="Body weight (kg)", n=6, rule="r",
             col_a_sample=[21.3, 22.8, 20.9, 23.4], col_b_sample=[0.0213, 0.0228, 0.0209, 0.0234])
    g = dict(f, kind="constant_ratio")
    assert _relation_prefilter(dict(f)) == _relation_prefilter(dict(g))


def test_a_row_stretch_is_not_sent_to_the_column_prefilter():
    from paperconan._profiles import _relation_prefilter
    assert _relation_prefilter(dict(kind="local_ratio_run", orientation="rows", row_a="a", row_b="b", n=6)) is None


def test_a_row_stretch_between_same_named_rows_is_not_excused_as_derived():
    from paperconan._profiles import _is_derived_relation
    same = dict(kind="local_ratio_run", orientation="rows", row_a="TNF (pg/mL)", row_b="TNF (pg/mL)")
    assert _is_derived_relation(same) is False
    derived = dict(kind="local_ratio_run", orientation="rows", row_a="TNF (pg/mL)", row_b="TNF normalised")
    other = dict(derived, kind="constant_ratio_row")
    assert _is_derived_relation(derived) == _is_derived_relation(other)


def test_both_kinds_carry_a_benign_reason():
    from paperconan._audit import benign_reason
    for kind in ("local_ratio_run", "local_offset_run"):
        assert "legend" in benign_reason(dict(kind=kind))
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 后两条以外的若干条失败（别名未接入、类型未登记、`benign_reason` 返回 None）。

- [ ] **Step 3: `_profiles.py` 的 `_is_derived_relation`**

把开头的类型集合与同名行规则改为：

```python
_LOCAL_RUN_KINDS = {"local_ratio_run", "local_offset_run"}


def _is_derived_relation(f: dict) -> bool:
    if f.get("kind") not in {"constant_ratio", "exact_linear", "sum_constant",
                             "constant_ratio_row", "scaled_row_reuse", "offset_row_reuse",
                             *_LOCAL_RUN_KINDS}:
        return False
    # Two rows carrying the SAME label related by an arbitrary scalar or offset is a duplicate,
    # not a unit/normalization derivation: a real unit conversion or %-restatement RELABELS the
    # row (e.g. "ng/mL" -> "µg/mL"). A unit token in a shared label (e.g. "TNF-α (pg/mL)" in two
    # panels) must not be read as evidence of derivation and must not downgrade the finding.
    row_pair = (f.get("kind") in {"constant_ratio_row", "scaled_row_reuse", "offset_row_reuse"}
                or (f.get("kind") in _LOCAL_RUN_KINDS and f.get("orientation") == "rows"))
    if row_pair:
        a = " ".join(str(f.get("row_a") or "").split()).casefold()
        b = " ".join(str(f.get("row_b") or "").split()).casefold()
        if a and a == b:
            return False
```

函数其余部分（折叠矩形的判断与最后的 `_DERIVED_RE`）保持不变。

- [ ] **Step 4: `_profiles.py` 的 `_relation_prefilter`**

函数开头改为按别名取类型，并把后文用到类型的两处改成这个变量：

```python
# A column stretch is judged by the header rules its whole-column counterpart gets.
_LOCAL_RUN_PREFILTER_ALIAS = {"local_ratio_run": "constant_ratio", "local_offset_run": "constant_offset"}


def _relation_prefilter(f: dict) -> tuple[str, str | None] | None:
    kind = f.get("kind")
    if kind in _LOCAL_RUN_PREFILTER_ALIAS:
        if f.get("orientation") != "cols":
            return None
        kind = _LOCAL_RUN_PREFILTER_ALIAS[kind]
    if kind not in {
        "identical_column",
        "constant_offset",
        "constant_ratio",
        "exact_linear",
        "sum_constant",
        "many_equal_pairs",
    }:
        return None
```

再把 `pf = _make_relation_prefilter_finding(f.get("kind"), ...)` 的第一个参数改为 `kind`。`if f.get("kind") == "many_equal_pairs"` 那一行不用改（别名不会产生它）。

- [ ] **Step 5: `_audit.py` 的 `benign_reason`**

在函数末尾的 `return None` 之前加入：

```python
    if kind in ("local_ratio_run", "local_offset_run"):
        return ("part of a line computed from another -- a normalisation, a unit conversion or a "
                "shared control applied to some positions only -- has this same shape; check whether "
                "the legend or Methods says so for these positions")
```

- [ ] **Step 6: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 57 passed.

- [ ] **Step 7: 提交**

```bash
git add src/paperconan/_profiles.py src/paperconan/_audit.py tests/test_local_pair_runs.py
git commit -m "feat(profiles): judge a stretch by the rules its whole-line counterpart gets"
```

---
### Task 9: 台架

**Files:**
- Create: `tests/test_local_run_bench_baseline.py`

- [ ] **Step 1: 写台架**（全文如下；冻结表是用本计划的代码生成的，实现与本计划一致时应原样通过）

```python
"""A frozen behaviour bench for local pair runs (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md).

The two existing benches call their own detectors and never this one, so they cannot hold it.
This one runs THIS detector on THEIR benign data -- the curve families of
test_curve_bench_baseline.py and the column families of test_column_pair_bench_baseline.py,
imported rather than copied so the three benches stay about the same data -- plus:

  KNOWN_COST   part of a column computed from another (one stretch normalised). It has the
               shape this detector looks for and numbers cannot tell it apart; header labels
               are what route it, so it is FROZEN AS A COST, not as a target to silence.
  PLANTED      one stretch (4 or 6 cells) copied from a line, then scaled or shifted, in
               columns and in rows. A bench of things that must not fire is passed perfectly
               by a detector that never fires; this is the side that gives it teeth.

Verdicts, not counts (CLAUDE.md "Freeze a verdict, not a count"): each stratum is the band
of _verdict -- silent / partial / pervasive -- over its draws, using the column-pair bench's
own banding so the three benches read alike.

Regenerate with `python -m tests.test_local_run_bench_baseline` from the repository root.
No real data: every value comes from `random.Random` seeded below.
"""
from __future__ import annotations

import functools
import random

import pytest

from paperconan._audit import detect_local_pair_runs, find_numeric_blocks
from paperconan._sheet import Sheet
from tests import test_column_pair_bench_baseline as CP
from tests import test_curve_bench_baseline as CV

SEED = 20261015
REPEATS = CP.REPEATS
SEGMENTS = (4, 6)


def _local(sheet):
    """Every local-run finding the detector makes on a sheet, block by block."""
    out = []
    for r0, r1, c0, c1 in find_numeric_blocks(sheet):
        header = [sheet.cell(r0 - 1, c) if r0 > 0 else None for c in range(c0, c1)]
        out += detect_local_pair_runs(sheet, r0, r1, c0, c1, header)
    return out


def _pair_sheet(col_a, col_b):
    return Sheet.from_rows([["Group A", "Group B"]] + [[a, b] for a, b in zip(col_a, col_b)])


# --- benign: the curve families, every row pair of every sheet ------------------------------

@functools.lru_cache(maxsize=None)
def _measure_curves(layout, decimals):
    return CP._verdict(_local(CV._as_sheet(CV._curve_block(decimals, s), layout == "panelled"))
                       for s in range(CV.SHEETS))


# --- benign: the column families --------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _measure_column_family(family, digits):
    rng = random.Random(CP._seed(family, digits) + SEED)
    return CP._verdict(_local(_pair_sheet(*CP.BENIGN[family](rng, 1.0, digits)))
                       for _ in range(REPEATS))


# --- known cost: one stretch of a column computed from another --------------------------------

def _partial_computed(rng, digits, segment):
    a, b = CP._base(rng, 1.0, digits), CP._base(rng, 1.0, digits)
    start = rng.randrange(0, CP.ROWS - segment + 1)
    control = CP._sig(4.73, digits)
    for i in range(start, start + segment):
        b[i] = CP._sig(a[i] / control, digits)
    return a, b


@functools.lru_cache(maxsize=None)
def _measure_known_cost(digits, segment):
    rng = random.Random(SEED * 11 + digits * 101 + segment)
    return CP._verdict(_local(_pair_sheet(*_partial_computed(rng, digits, segment)))
                       for _ in range(REPEATS))


# --- planted: one stretch copied then scaled or shifted ---------------------------------------

def _planted_columns(rng, digits, segment, relation):
    a, b = CP._base(rng, 1.0, digits), CP._base(rng, 1.0, digits)
    start = rng.randrange(0, CP.ROWS - segment + 1)
    k = rng.uniform(1.05, 3.0) if relation == "ratio" else CP._sig(rng.uniform(0.5, 3.0), digits)
    for i in range(start, start + segment):
        b[i] = CP._sig(a[i] * k if relation == "ratio" else a[i] + k, digits)
    return a, b, start


def _found(findings, kind, row_1based):
    return [f for f in findings
            if f["kind"] == kind and any(s <= row_1based <= e for s, e in f["segments"])]


@functools.lru_cache(maxsize=None)
def _measure_planted_columns(relation, digits, segment):
    rng = random.Random(SEED * 13 + digits * 103 + segment * 7 + (relation == "offset"))
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for _ in range(REPEATS):
        a, b, start = _planted_columns(rng, digits, segment, relation)
        draws.append(_found(_local(_pair_sheet(a, b)), kind, start + 2))   # header row, 1-based
    return CP._verdict(draws)


def _planted_rows(decimals, sheet, segment, relation):
    block = CV._curve_block(decimals, sheet)
    rng = random.Random(SEED * 17 + decimals * 107 + sheet * 3 + segment + (relation == "offset"))
    src, dst = rng.sample(range(CV.ROWS), 2)
    start = rng.randrange(0, CV.COLS - segment + 1)
    k = rng.uniform(1.15, 2.4) if relation == "ratio" else round(rng.uniform(0.5, 3.0), decimals)
    for j in range(start, start + segment):
        v = block[src][j]
        block[dst][j] = round(v * k if relation == "ratio" else v + k, decimals)
    return block, src, dst, start


@functools.lru_cache(maxsize=None)
def _measure_planted_rows(relation, decimals, segment):
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for s in range(CV.SHEETS):
        block, src, dst, start = _planted_rows(decimals, s, segment, relation)
        sheet = CV._as_sheet(block, False)
        rows = {src + 1, dst + 1}                                # sheet rows of the pair, 0-based
        hits = [f for f in _found(_local(sheet), kind, start + 2)  # label column, 1-based
                if f["orientation"] == "rows" and {f["row_a_idx"], f["row_b_idx"]} == rows]
        draws.append(hits)
    return CP._verdict(draws)


# --- frozen tables ----------------------------------------------------------------------------
# RECORDED, NOT ENDORSED. Regenerate with `python -m tests.test_local_run_bench_baseline` and
# paste; never hand-edit a band. KNOWN_COST is pervasive by construction (see the docstring);
# PLANTED_ROWS silent strata are a recorded gap -- four coarse cells do not carry the information
# a stretch needs -- not a target.
CURVE_BASELINE = {
    ('contiguous', 1): ('silent', {}),
    ('contiguous', 2): ('silent', {}),
    ('contiguous', 3): ('silent', {}),
    ('panelled', 1): ('silent', {}),
    ('panelled', 2): ('silent', {}),
    ('panelled', 3): ('silent', {}),
}
COLUMN_BASELINE = {
    ('baseline_subtracted', 4): ('silent', {}),
    ('baseline_subtracted', 6): ('silent', {}),
    ('baseline_subtracted', 7): ('silent', {}),
    ('baseline_subtracted', 8): ('silent', {}),
    ('baseline_subtracted', 9): ('silent', {}),
    ('baseline_subtracted', 10): ('silent', {}),
    ('complementary_percentages', 4): ('silent', {}),
    ('complementary_percentages', 6): ('silent', {}),
    ('complementary_percentages', 7): ('silent', {}),
    ('complementary_percentages', 8): ('silent', {}),
    ('complementary_percentages', 9): ('silent', {}),
    ('complementary_percentages', 10): ('silent', {}),
    ('control_normalised', 4): ('silent', {}),
    ('control_normalised', 6): ('silent', {}),
    ('control_normalised', 7): ('silent', {}),
    ('control_normalised', 8): ('silent', {}),
    ('control_normalised', 9): ('silent', {}),
    ('control_normalised', 10): ('silent', {}),
    ('duplicated_measurement', 4): ('silent', {}),
    ('duplicated_measurement', 6): ('silent', {}),
    ('duplicated_measurement', 7): ('silent', {}),
    ('duplicated_measurement', 8): ('silent', {}),
    ('duplicated_measurement', 9): ('silent', {}),
    ('duplicated_measurement', 10): ('silent', {}),
    ('null_independent', 4): ('silent', {}),
    ('null_independent', 6): ('silent', {}),
    ('null_independent', 7): ('silent', {}),
    ('null_independent', 8): ('silent', {}),
    ('null_independent', 9): ('silent', {}),
    ('null_independent', 10): ('silent', {}),
    ('percent_of_total', 4): ('silent', {}),
    ('percent_of_total', 6): ('silent', {}),
    ('percent_of_total', 7): ('silent', {}),
    ('percent_of_total', 8): ('silent', {}),
    ('percent_of_total', 9): ('silent', {}),
    ('percent_of_total', 10): ('silent', {}),
    ('rate_per_thousand', 4): ('silent', {}),
    ('rate_per_thousand', 6): ('silent', {}),
    ('rate_per_thousand', 7): ('silent', {}),
    ('rate_per_thousand', 8): ('silent', {}),
    ('rate_per_thousand', 9): ('silent', {}),
    ('rate_per_thousand', 10): ('silent', {}),
    ('sd_sem', 4): ('silent', {}),
    ('sd_sem', 6): ('silent', {}),
    ('sd_sem', 7): ('silent', {}),
    ('sd_sem', 8): ('silent', {}),
    ('sd_sem', 9): ('silent', {}),
    ('sd_sem', 10): ('silent', {}),
    ('sparse_shared_support', 4): ('silent', {}),
    ('sparse_shared_support', 6): ('silent', {}),
    ('sparse_shared_support', 7): ('silent', {}),
    ('sparse_shared_support', 8): ('silent', {}),
    ('sparse_shared_support', 9): ('silent', {}),
    ('sparse_shared_support', 10): ('silent', {}),
    ('two_level_coded', 4): ('silent', {}),
    ('two_level_coded', 6): ('silent', {}),
    ('two_level_coded', 7): ('silent', {}),
    ('two_level_coded', 8): ('silent', {}),
    ('two_level_coded', 9): ('silent', {}),
    ('two_level_coded', 10): ('silent', {}),
    ('unit_decadic', 4): ('silent', {}),
    ('unit_decadic', 6): ('silent', {}),
    ('unit_decadic', 7): ('silent', {}),
    ('unit_decadic', 8): ('silent', {}),
    ('unit_decadic', 9): ('silent', {}),
    ('unit_decadic', 10): ('silent', {}),
    ('unit_non_decadic', 4): ('silent', {}),
    ('unit_non_decadic', 6): ('silent', {}),
    ('unit_non_decadic', 7): ('silent', {}),
    ('unit_non_decadic', 8): ('silent', {}),
    ('unit_non_decadic', 9): ('silent', {}),
    ('unit_non_decadic', 10): ('silent', {}),
    ('varying_fold_change', 4): ('silent', {}),
    ('varying_fold_change', 6): ('silent', {}),
    ('varying_fold_change', 7): ('silent', {}),
    ('varying_fold_change', 8): ('silent', {}),
    ('varying_fold_change', 9): ('silent', {}),
    ('varying_fold_change', 10): ('silent', {}),
}
KNOWN_COST_BASELINE = {
    (4, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (4, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (6, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (6, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (7, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (7, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (8, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (8, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (9, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (9, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (10, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (10, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
}
PLANTED_COLUMNS_BASELINE = {
    ('ratio', 4, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 4, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 6, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 6, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 7, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 7, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 8, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 8, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 9, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 9, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 10, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 10, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('offset', 4, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 4, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 6, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 6, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 7, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 7, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 8, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 8, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 9, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 9, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 10, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 10, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
}
PLANTED_ROWS_BASELINE = {
    ('ratio', 1, 4): ('silent', {}),
    ('ratio', 1, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 2, 4): ('silent', {}),
    ('ratio', 2, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 3, 4): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 3, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('offset', 1, 4): ('silent', {}),
    ('offset', 1, 6): ('partial', {'local_offset_run': 'partial'}),
    ('offset', 2, 4): ('silent', {}),
    ('offset', 2, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 3, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 3, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
}


def _tables():
    return {
        "CURVE_BASELINE": {(lay, d): _measure_curves(lay, d) for lay, d in CV._STRATA},
        "COLUMN_BASELINE": {(f, d): _measure_column_family(f, d) for f in sorted(CP.BENIGN) for d in CP.SIG_FIGURES},
        "KNOWN_COST_BASELINE": {(d, s): _measure_known_cost(d, s) for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_COLUMNS_BASELINE": {(r, d, s): _measure_planted_columns(r, d, s)
                                     for r in ("ratio", "offset") for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_ROWS_BASELINE": {(r, d, s): _measure_planted_rows(r, d, s)
                                  for r in ("ratio", "offset") for d in CV._DECIMALS for s in SEGMENTS},
    }


def _check(table, measure, stratum, what):
    observed, expected = measure(*stratum), table[stratum]
    assert observed == expected, (
        f"{what} moved on {stratum}: expected {expected}, got {observed} "
        f"({CP._direction(expected, observed)}). Regenerate the tables only as a deliberate "
        "decision, saying in the commit which way it moved and why.")


@pytest.mark.parametrize("stratum", sorted(CURVE_BASELINE))
def test_curve_families_hold_their_frozen_verdict(stratum):
    _check(CURVE_BASELINE, _measure_curves, stratum, "local runs on curve data")


@pytest.mark.parametrize("stratum", sorted(COLUMN_BASELINE))
def test_column_families_hold_their_frozen_verdict(stratum):
    _check(COLUMN_BASELINE, _measure_column_family, stratum, "local runs on a benign column family")


@pytest.mark.parametrize("stratum", sorted(KNOWN_COST_BASELINE))
def test_the_known_cost_holds_its_frozen_verdict(stratum):
    _check(KNOWN_COST_BASELINE, _measure_known_cost, stratum, "the partial-normalisation cost")


@pytest.mark.parametrize("stratum", sorted(PLANTED_COLUMNS_BASELINE))
def test_planted_column_stretches_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_COLUMNS_BASELINE, _measure_planted_columns, stratum, "recall of a planted column stretch")


@pytest.mark.parametrize("stratum", sorted(PLANTED_ROWS_BASELINE))
def test_planted_row_stretches_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_ROWS_BASELINE, _measure_planted_rows, stratum, "recall of a planted row stretch")


def test_every_stratum_the_generators_define_is_frozen():
    """A stratum added to a generator and not to a table would never be checked."""
    assert set(CURVE_BASELINE) == set(CV._STRATA)
    assert set(COLUMN_BASELINE) == {(f, d) for f in CP.BENIGN for d in CP.SIG_FIGURES}
    assert set(KNOWN_COST_BASELINE) == {(d, s) for d in CP.SIG_FIGURES for s in SEGMENTS}
    assert set(PLANTED_COLUMNS_BASELINE) == {(r, d, s) for r in ("ratio", "offset")
                                             for d in CP.SIG_FIGURES for s in SEGMENTS}
    assert set(PLANTED_ROWS_BASELINE) == {(r, d, s) for r in ("ratio", "offset")
                                          for d in CV._DECIMALS for s in SEGMENTS}


def test_the_bench_still_has_teeth():
    """A bench of things that must not fire is passed perfectly by a detector that never fires.
    Anchored on the planted side, and on the far negative control staying silent."""
    assert any(v[0] != "silent" for v in PLANTED_COLUMNS_BASELINE.values())
    assert any(v[0] != "silent" for v in PLANTED_ROWS_BASELINE.values())
    assert all(COLUMN_BASELINE[("null_independent", d)][0] == "silent" for d in CP.SIG_FIGURES)


if __name__ == "__main__":
    for name, table in _tables().items():
        print(f"{name} = {{")
        for key, verdict in table.items():
            print(f"    {key!r}: {verdict!r},")
        print("}")
```

- [ ] **Step 2: 运行**

Run: `.venv/bin/python -m pytest tests/test_local_run_bench_baseline.py -q`
Expected: 134 passed（约 10 秒）。若有档位不同，说明实现与本计划有出入：先找出出入在哪里，确认是有意的，再用 `python -m tests.test_local_run_bench_baseline` 重新生成表并粘贴，提交信息里写明哪一层、往哪个方向变了、为什么。

- [ ] **Step 3: 核对台架有牙齿**

```bash
for c in "_LOCAL_RUN_OUT_DEV=0" "_LOCAL_RUN_MIN_BITS=1e9" "_LOCAL_RUN_MIN_EDGE_STEPS=0"; do
  .venv/bin/python -c "import sys, pytest, paperconan._audit as a; setattr(a, '${c%%=*}', float('${c#*=}')); sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_local_run_bench_baseline.py']))" | tail -1
done
```

Expected: 三行都有 `failed`（分别是良性层变吵、真阳性层与“有牙齿”测试变红、良性层变吵）。

- [ ] **Step 4: 提交**

```bash
git add tests/test_local_run_bench_baseline.py
git commit -m "test(detect): a frozen bench for local runs on the other benches' benign data"
```

---
### Task 10: 文档

**Files:**
- Modify: `skills/paperconan/references/detectors.md`
- Modify: `skills/paperconan/references/output-schema.md`

- [ ] **Step 1: `detectors.md`**：在 `### \`constant_ratio_row\` / \`identical_row\`` 一节之前插入：

```markdown
### `local_ratio_run` / `local_offset_run`
- **原理**：**同一 block 内**两列（`orientation = "cols"`）或两行（`"rows"`）只在**一段连续位置**上成恒定比例（B = k·A）或恒定差值（B = A + c），段外不成立。两侧都按各自记录的舍入步长求区间，整条线按固定小数位或固定有效数字推断步长。要同时满足：一个常数预测这一段的压缩得分过门槛；段外偏离（扣除舍入）的中位数 > 5%；最长一段占该常数相容位置的一半以上；常数离开恒等（比例离 1、差值离 0）至少 10 个舍入步长；段两端外第一个有值的格偏离至少 3 个舍入步长。整条线都成立的关系不在这里报（交给 `constant_ratio` / `constant_ratio_row` 等）。
- **典型命中**：一组数中的一段由另一组复制后乘一个系数或加一个常数而来，其余位置是独立数据（如合成例：10 个值中前 6 个 B = A × 1.137，后 4 个无此关系）。
- **常见误报**：同一列里只有一段由另一列换算、归一或共用对照得来——数值形态与复制一段相同，靠表头与行标签（派生词、单位换算、同名行规则）和图注区分。
- **解读时**：看 `segments`（段在 sheet 上的行号或列号）、`k` / `offset`、`outside_departure`、`prediction_bits`；`example_cells` 列出段内的格。确认这一段在图注或 Methods 里是否说明为换算、归一或共用数据。
```

- [ ] **Step 2: `output-schema.md`**：把 `relations` 与 `row_relations` 两行注释改为：

```text
      "relations": [...],              // cross-column relations (incl. local_ratio_run / local_offset_run with orientation "cols")
      "row_relations": [...],          // within-block row-to-row relations (constant_ratio_row / identical_row; local_*_run with orientation "rows")
```

- [ ] **Step 3: 检查措辞**

Run: `grep -n -E "fraud|fabricat|造假|伪造|不端" skills/paperconan/references/detectors.md skills/paperconan/references/output-schema.md`
Expected: 无输出。

- [ ] **Step 4: 提交**

```bash
git add skills/paperconan/references/detectors.md skills/paperconan/references/output-schema.md
git commit -m "docs: describe the two local-run kinds"
```

---
### Task 11: 全套测试、golden 与示例扫描

**Files:**

- [ ] **Step 1: 全套测试**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -5`
Expected: 汇总行无 failed。读汇总行本身，不要信管道的退出码。

- [ ] **Step 2: 示例论文**

Run: `.venv/bin/paperconan scan examples/demo_paper --out /tmp/demo_local_runs --no-html && .venv/bin/python -c "import json; s=json.load(open('/tmp/demo_local_runs/scan.json')); print(sum(1 for b in s['relations_blocks'] for g in ('relations','row_relations') for f in b.get(g, []) if f['kind'].startswith('local_')))"`
Expected: 打印一个数。非 0 时逐条打开（`paperconan explain`）判断性质，记入 Task 12 的评估记录；示例的 `audit/scan.json` 是否需要重新生成，按 CLAUDE.md “Releasing” 一节的规定处理（只在检测输出确实改变时重生成，并保持相对路径）。

- [ ] **Step 3: 确定性**

Run: 对 `examples/demo_paper` 扫描两次，比较两份 `scan.json` 去掉时间字段后的内容。
Expected: 相同。

---
### Task 12: 本地评估集（不入库）

**Files:**
- Create（本地，gitignored）: `recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/build_scope_v2.py`
- Create（本地，gitignored）: `recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/match_scope_v2.py`

v1 已冻结（`FREEZE_v1.json`），不改它的任何文件。v2 只在家族表里加入两个新类型，目标卡不动。

- [ ] **Step 1: v2 命中范围**（`build_scope_v2.py`）

```python
"""Hit scope v2 = v1 plus the two local-run kinds in the ratio and difference families. Targets unchanged."""
import importlib.util, json, sys
from pathlib import Path
E = Path("recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope")
spec = importlib.util.spec_from_file_location("build_scope", E / "build_scope.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for op, fam in m.FAMILIES.items():
    if fam & m.RATIO:
        fam.add("local_ratio_run")
    if fam & m.DIFF:
        fam.add("local_offset_run")
out = m.payload()
(E / "eval_scope_v2.jsonl").write_text(out["eval_scope_v1.jsonl"])
(E / "build_report_v2.json").write_text(out["build_report.json"])
print(json.loads(out["build_report.json"])["counts"])
```

Run: `.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/build_scope_v2.py`
Expected: 计数与 v1 相同（130 张卡、264 个目标对）。再确认 `eval_scope_v2.jsonl` 与 v1 只差 `family` 字段。

- [ ] **Step 2: v2 匹配器**（`match_scope_v2.py`：读 v2 范围，并把两个新类型加入 `PARTIAL`——它们按定义只在部分位置成立）

```python
import importlib.util, sys
from pathlib import Path
E = Path("recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope")
spec = importlib.util.spec_from_file_location("match_scope", E / "match_scope.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.PARTIAL |= {"local_ratio_run", "local_offset_run"}
orig = Path.read_text
def read_text(self, *a, **k):
    if self == E / "eval_scope_v1.jsonl":
        self = E / "eval_scope_v2.jsonl"
    return orig(self, *a, **k)
Path.read_text = read_text
m.main()
```

- [ ] **Step 3: 用分支代码重扫 74 篇，两版各计分一次**

```bash
B=$(git rev-parse --short HEAD)
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/run_scans.py v1_localruns_$B --workers 3
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/match_scope.py v1_localruns_$B      # v1 口径
cp -r recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/runs/v1_localruns_$B recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/runs/v2_localruns_$B
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/match_scope_v2.py v2_localruns_$B   # v2 口径
```

基线也要按 v2 口径计一次（main 的扫描不变，只换范围）：

```bash
cp -r recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/runs/v1_610c276 recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/runs/v2_610c276
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/match_scope_v2.py v2_610c276
```

- [ ] **Step 4: 收益与代价**

```bash
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/compare_runs.py v2_610c276 v2_localruns_$B
.venv/bin/python recheck/benchmarks/paperconan_eval/curation_20260912/eval_scope/compare_runs.py v1_610c276 v1_localruns_$B
```

记录：两层（检测层、首屏）各补回、丢失哪些卡；首屏新进、挤出的 panel 按类型与逐篇分布，大头集中在哪几篇；新类型在首屏上出现的次数中，有多少落在参考卡目标上、多少不是（后者逐篇抽看，判断是否属于已知代价形态）；与 `detect_short_row_reuse` 对同一行对重复报出的次数。

- [ ] **Step 5: 合并门槛**（设计 §8）

三条都满足才进入 Task 13：两个台架良性层不动（Task 9 通过）；本地评估集首屏上补回的卡多于挤出的卡；审查无未处理的阻断项（Task 13）。首屏代价过高时，先把严重度改为 medium、重跑 Step 3–4 再判断，并把两次结果都记下。

---
### Task 13: 独立审查与 PR

**Files:**

- [ ] **Step 1: 独立审查**

派一个独立子代理审查本分支相对 main 的全部改动（代码、测试、台架、文档），要求：对照设计文档逐条核对判据是否实现一致；找能通过全部检查的错误实现（注入探针）；检查合成数据之外的输入（空块、单行块、全 NaN、负值、极大量级、有效数字与小数位混合导出）；检查新 finding 在 `paperconan drill` / `explain` / HTML 报告里的呈现。审查意见逐条处理，处理记录写进 PR 描述。

- [ ] **Step 2: 推送并开 PR**

```bash
git push -u origin feat/local-pair-runs
gh pr create --title "feat(detect): report a pair related on one stretch only" --body-file /tmp/pr-local-pair-runs.md
```

先把 PR 描述写进 `/tmp/pr-local-pair-runs.md`，内容包含：问题（为什么现有检测器按构造报不出）、做法、台架档位、本地评估集上两层的收益与代价（只写汇总数，不写论文编号或数据）、审查意见与处理、已知代价与已知缺口。结尾加 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`。

- [ ] **Step 3: 合并前确认**

CI 全绿；PR 合并前再过一次独立审查（本仓库规定）。

---
