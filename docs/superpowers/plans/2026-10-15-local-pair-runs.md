# 局部段关系检测（local pair runs）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一遍检测，报出同一数值块内两行或两列只在一段连续位置上成恒定比例、恒定差值的统计信号（`local_ratio_run` / `local_offset_run`）。

**Architecture:** 全部放在 `src/paperconan/_audit.py`，作为独立的一遍：不读写现有检测器的候选池、频次表、预算或上限。块循环里在 `detect_relations` / `detect_row_relations` 之后调用；列方向的结果并入 `relations` 组，行方向的并入 `row_relations` 组，下游（证据、良性说明、profile、降权）沿用现有路径。设计见 `docs/superpowers/specs/2026-10-15-local-pair-runs-design.md`。

**Tech Stack:** Python ≥ 3.10、numpy、pytest；复用 `_audit.py` 里的 `_effective_row_quantums`、`_sigfigs_and_frac`、`_ratio_prediction_bits`、`_interval_holds_a_power_of_ten`、`_AXIS_CONTEXT_LABEL_RE`、`_row_label`、`_sample`、`_note_detector_cap`。

**纪律（来自 CLAUDE.md 与本仓库的教训）：**
- 只用合成数据；不写任何论文的数据、编号或判定。输出与注释用中性措辞（统计信号、数据不一致）。
- 每条新断言都要确认：删掉它守护的代码，它会变红。本计划在 Task 3、4、5、6、9 里给出了逐条的“关掉即变红”核对。
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
# blank cells, cells that confirm nothing (in the ratio arm, zero against zero) and the group's
# own positions, the first cell with data must depart from the constant by at least this many
# of its own rounding steps beyond rounding. A rounding plateau on a smooth trend, and the
# noise exceptions of a relation holding approximately everywhere, depart by less.
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
    _LOCAL_RUN_DEPARTURE_CAP, _best_local_group, _edge_steps, _effective_line_quantums, _effective_row_quantums,
    _group_constant_runs, _identity_steps, _local_group_bits, _local_line_usable, _outside_departure,
    _ratio_prediction_bits, _scan_interval_runs, _segments, _symmetric_offset_interval,
    _symmetric_ratio_interval, _zero_source_miss, detect_local_pair_runs,
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
    assert (run["start"], run["end"]) == (0, 2)
    assert _scan_interval_runs(spans, [1, 2, 3], min_informative=3, min_distinct_source=2) == []


def test_intervals_that_only_touch_share_a_point():
    (run,) = _scan_interval_runs([(1.0, 2.0), (2.0, 3.0), (2.0, 2.5)], [1, 2, 3])
    assert (run["start"], run["end"], run["lo"], run["hi"]) == (0, 2, 2.0, 2.0)


def test_a_run_needs_distinct_source_values_among_its_informative_cells():
    spans = [(1.9, 2.1)] * 4
    assert _scan_interval_runs(spans, [5.0, 5.0, 6.0, 6.0]) == []
    assert len(_scan_interval_runs(spans, [5.0, 5.0, 6.0, 7.0])) == 1
    assert _scan_interval_runs([(1.9, 2.1), (-math.inf, math.inf), (1.9, 2.1), (1.9, 2.1)], [5.0, 7.0, 6.0, 6.0]) == []


def test_a_run_starting_inside_another_can_reach_further():
    spans = [(1.0, 2.0), (1.5, 2.5), (2.2, 3.0), (2.6, 3.5)]
    runs = _scan_interval_runs(spans, [1, 2, 3, 4], min_distinct_source=2)
    assert [(r["start"], r["end"], r["lo"], r["hi"]) for r in runs] == [
        (0, 1, 1.5, 2.0), (1, 2, 2.2, 2.5), (2, 3, 2.6, 3.0)]


def test_runs_of_one_constant_form_one_group_with_the_lone_cells_between():
    spans = [(1.9, 2.1)] * 3 + [(5.0, 6.0), (1.95, 2.05), (7.0, 8.0)] + [(1.9, 2.1)] * 3
    runs = _scan_interval_runs(spans, list(range(9)))
    (group, *others) = _group_constant_runs(runs, spans)
    assert others == []
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


def test_a_run_joins_the_longest_group_its_constant_still_meets():
    sep = (9.0, 9.5)
    spans = [(2.3, 2.5)] * 4 + [sep] + [(2.05, 2.35)] * 3 + [sep] + [(1.9, 2.1)] * 6
    first, second = _group_constant_runs(_scan_interval_runs(spans, list(range(len(spans)))), spans)
    assert (first["lo"], first["hi"]) == (2.05, 2.1) and first["positions"] == [5, 6, 7, 9, 10, 11, 12, 13, 14]
    assert (second["lo"], second["hi"]) == (2.3, 2.5) and second["positions"] == [0, 1, 2, 3, 5, 6, 7]


def test_a_cell_that_confirms_nothing_sits_inside_a_stretch_but_never_extends_one():
    every = (-math.inf, math.inf)
    spans = [every] * 4 + [(1.9, 2.1), (1.95, 2.05), every, (1.92, 2.08), every, every, (5.0, 6.0)]
    (group,) = _group_constant_runs(_scan_interval_runs(spans, list(range(len(spans)))), spans)
    assert group["positions"] == [4, 5, 6, 7] and group["segments"] == [[4, 7]]
    assert group["informative"] == [4, 5, 7]
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**（`_audit.py` 顶部的 `from collections import Counter, defaultdict` 加上 `deque`）

```python
def _scan_interval_runs(spans, source, min_informative=2, min_distinct_source=None):
    """Maximal contiguous runs whose intervals share a point.

    The run logic of `_scan_quantized_ratio_runs`, taking the intervals ready-made so the
    ratio and the offset arm share it: `None` breaks a run, (-inf, inf) joins one without
    informing it, a run needs `min_informative` informative cells and `min_distinct_source`
    distinct source values among them (at the defaults the first never binds), and
    a run inside another is dropped. Returns [{"start", "end", "lo", "hi"}] in order of start.
    The distinct-source floor defaults to `_LOCAL_RUN_MIN_DISTINCT_SOURCE`, read at call time.

    Linear in the line's length. What can join a run starting at s can join one starting
    later, so the run's end only moves forward as its start does: one window slides along the
    line, its bounds kept by two monotone queues. A run is then inside another exactly when the
    run one start earlier reaches the same end.
    """
    if min_distinct_source is None:
        min_distinct_source = _LOCAL_RUN_MIN_DISTINCT_SOURCE

    def informing(span):
        return math.isfinite(span[0]) and math.isfinite(span[1])

    n = len(spans)
    # indices whose lower (upper) bound can still be the window's largest (smallest)
    los, his = deque(), deque()
    values, informative = Counter(), 0
    end, prev_end, out = -1, None, []
    for start in range(n):
        if start - 1 <= end and start > 0 and spans[start - 1] is not None and informing(spans[start - 1]):
            informative -= 1
            v = float(source[start - 1])
            values[v] -= 1
            if not values[v]:
                del values[v]
        end = max(end, start - 1)
        for q in (los, his):
            while q and q[0] < start:
                q.popleft()
        while end + 1 < n and spans[end + 1] is not None:
            lo_j, hi_j = spans[end + 1]
            lo = spans[los[0]][0] if los else -math.inf
            hi = spans[his[0]][1] if his else math.inf
            if max(lo, lo_j) > min(hi, hi_j):
                break
            end += 1
            while los and spans[los[-1]][0] < lo_j:
                los.pop()
            los.append(end)
            while his and spans[his[-1]][1] > hi_j:
                his.pop()
            his.append(end)
            if informing(spans[end]):
                informative += 1
                values[float(source[end])] += 1
        contained, prev_end = prev_end == end, end
        if end < start or contained or informative < min_informative or len(values) < min_distinct_source:
            continue
        out.append({"start": start, "end": end,
                    "lo": spans[los[0]][0] if los else -math.inf,
                    "hi": spans[his[0]][1] if his else math.inf})
    return out


def _segments(positions):
    """[[start, end], ...] of the contiguous stretches in a strictly increasing list of positions."""
    out = []
    for p in positions:
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return out


def _confirms_nothing(span):
    """A cell whose interval is unbounded: in practice zero against zero in the ratio arm,
    which every constant writes."""
    return span is not None and not (math.isfinite(span[0]) and math.isfinite(span[1]))


def _group_constant_runs(runs, spans, admit=None):
    """Gather the runs one constant accounts for.

    Longest first, a run joins the first group whose interval it still intersects; the
    group's interval narrows to the intersection. (Of runs `_scan_interval_runs` found over
    these same spans, two that overlap in position never share a point -- the earlier would
    have extended -- so a run never joins a group holding one it overlaps.) A group's
    POSITIONS are then the cells whose interval meets the group's, in a run or not -- a lone
    consistent cell between two runs is the same relation, not its outside -- less those
    trimmed below. A cell with no interval (None) joins only if `admit(i, lo, hi)` says some
    constant of the group writes it -- a source read as zero, which one interval cannot
    describe. A cell that confirms nothing (`_confirms_nothing`) may sit inside a stretch but
    never extends one: it is trimmed off both ends of every segment. Returns
    [{"lo", "hi", "positions", "informative", "segments"}].
    """
    groups = []
    for r in sorted(runs, key=lambda r: (-(r["end"] - r["start"]), r["start"])):
        for g in groups:
            lo, hi = max(g["lo"], r["lo"]), min(g["hi"], r["hi"])
            if lo <= hi:
                g["lo"], g["hi"] = lo, hi
                break
        else:
            groups.append({"lo": r["lo"], "hi": r["hi"]})
    out = []
    for g in groups:
        met = [i for i, s in enumerate(spans)
               if (s is not None and s[0] <= g["hi"] and s[1] >= g["lo"])
               or (s is None and admit is not None and admit(i, g["lo"], g["hi"]))]
        positions = []
        for start, end in _segments(met):
            while start <= end and _confirms_nothing(spans[start]):
                start += 1
            while end >= start and _confirms_nothing(spans[end]):
                end -= 1
            positions.extend(range(start, end + 1))
        # an admitted cell without an interval says only that |k| clears a threshold: not informative
        informative = [i for i in positions if spans[i] is not None and not _confirms_nothing(spans[i])]
        out.append({"lo": g["lo"], "hi": g["hi"], "positions": positions,
                    "informative": informative, "segments": _segments(positions)})
    return out
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 30 passed.

- [ ] **Step 4b: 逐条核对“删掉即变红”**

每一项在磁盘上改动后跑一次测试文件，确认点名的测试失败，再从备份恢复并用 `cmp` 确认与备份逐字节相同。每次运行都设 `PYTHONDONTWRITEBYTECODE=1`。替换只在新函数体内做（旧的 `_scan_quantized_ratio_runs` 里有相似的片段）。

| 改动 | 必须失败的测试 |
|---|---|
| `_scan_interval_runs`：`max(lo, lo_j) > min(hi, hi_j)` 改为 `>=` | `test_intervals_that_only_touch_share_a_point` |
| `_scan_interval_runs`：`contained` 恒为 `False` | `test_scan_interval_runs_breaks_on_none_and_drops_sub_runs` |
| `_scan_interval_runs`：窗口的界过期滞后一格（`q[0] < start - 1`） | `test_a_run_starting_inside_another_can_reach_further` |
| `_scan_interval_runs`：不同源值按全部格计，而不只按有信息的格 | `test_a_run_needs_distinct_source_values_among_its_informative_cells` |
| `_group_constant_runs`：组的区间不随加入的段收窄 | `test_a_run_joins_the_longest_group_its_constant_still_meets` |
| `_group_constant_runs`：按段长升序加入 | `test_a_run_joins_the_longest_group_its_constant_still_meets` |
| `_group_constant_runs`：去掉 `admit` 一支 | `test_a_cell_without_an_interval_joins_a_group_only_when_admitted` |
| `_group_constant_runs`：不从段首（或段尾）剪掉不提供信息的格 | `test_a_cell_that_confirms_nothing_sits_inside_a_stretch_but_never_extends_one` |
| `_group_constant_runs`：不提供信息的格算作有信息 | `test_a_cell_that_confirms_nothing_sits_inside_a_stretch_but_never_extends_one` |

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

- [ ] **Step 1: 写测试**（导入行加入 `_LOCAL_RUN_DEPARTURE_CAP, _edge_steps, _identity_steps, _local_line_usable, _outside_departure`）

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


def test_outside_departure_of_an_offset_is_its_gap_over_the_constant():
    a, b = _arr([1.0, 2.0, 3.0, 4.0, 5.0]), _arr([4.0, 5.0, 6.0, 7.5, 7.6])
    q = [0.1] * 5
    spans = [_symmetric_offset_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2]}
    # c = 3 against [3.4, 3.6] below it and [2.5, 2.7] above it: gaps 0.4 and 0.3 over |c|, their median
    assert _outside_departure(a, b, q, q, group, spans, "offset") == pytest.approx((0.4 + 0.3) / 2 / 3.0)


def test_outside_departure_of_a_ratio_is_read_against_the_lines_scale():
    a, b = _arr([10.0, 20.0, 30.0, 0.5]), _arr([20.0, 40.0, 60.0, 1.3])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # the small cell admits k from 1.25/0.55; that gap as an amount of B, over the line's median |b|
    # of 30 -- not over the cell's own 1.3, which would read a small additive error as a large one
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx((1.25 / 0.55 - 2.0) * 0.5 / 30.0)


def test_outside_departure_reads_its_scale_from_cells_that_confirm_something():
    a, b = _arr([10.0, 20.0, 30.0, 0.5] + [0.0] * 4), _arr([20.0, 40.0, 60.0, 1.3] + [0.0] * 4)
    q = [0.1] * 8
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # four zeros against zeros: they neither set the median |b|, still 30, nor count as departures
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx((1.25 / 0.55 - 2.0) * 0.5 / 30.0)


def test_outside_departure_reads_its_scale_over_the_pair_only():
    # B against a blank in A is not part of the pair: it does not pull the median |b| to 0
    a = _arr([10.0, 20.0, 30.0, 0.0] + [math.nan] * 5)
    b = _arr([20.0, 40.0, 60.0, 5.0] + [0.0] * 5)
    q_a, q_b = [0.1] * 4 + [math.nan] * 5, [0.1] * 9
    spans = [_symmetric_ratio_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # the source read as zero misses 5 by 4.95 - 2 * 0.05; the pair's median |b| is that of 20, 40, 60, 5
    assert _outside_departure(a, b, q_a, q_b, group, spans, "ratio") == pytest.approx(4.85 / 30.0)


def test_outside_departure_is_the_median_of_the_outside_not_its_mean():
    a, b = _arr([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), _arr([4.0, 5.0, 6.0, 7.5, 7.6, 20.0])
    q = [0.1] * 6
    spans = [_symmetric_offset_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2]}
    # gaps 0.4, 0.3 and 10.9 over |c|: the median is the first, the mean far above it
    assert _outside_departure(a, b, q, q, group, spans, "offset") == pytest.approx(0.4 / 3.0)


def test_outside_departure_is_capped_when_the_lines_scale_is_zero():
    a = _arr([10.0, 20.0, 30.0, 11.0, 12.0, 13.0, 14.0])
    b = _arr([20.0, 40.0, 60.0, 0.0, 0.0, 0.0, 0.0])
    q = [0.1] * 7
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == _LOCAL_RUN_DEPARTURE_CAP


def test_a_source_read_as_zero_departs_at_the_cap_when_the_pairs_scale_is_zero():
    # inside the stretch k * a falls below half B's step more often than not: B reads 0 there,
    # those cells are part of the pair, and the pair's median |b| is 0
    a = _arr([20.0, 40.0, 60.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.0])
    b = _arr([1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0])
    q = [0.1] * 9
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    assert all(s is not None and s[0] <= 0.05 <= s[1] for s in spans[:8])     # k = 0.05 writes every inside cell
    group = {"lo": 0.049, "hi": 0.051, "positions": list(range(8))}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == _LOCAL_RUN_DEPARTURE_CAP


def test_outside_departure_refuses_a_constant_of_zero():
    a, b = _arr([10.0, 20.0, 30.0, 5.0]), _arr([0.0, 0.0, 0.0, 13.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": -0.001, "hi": 0.001, "positions": [0, 1, 2]}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") is None


@pytest.mark.parametrize("relation", ["ratio", "offset"])
@pytest.mark.parametrize("blank", ["a", "b"])
def test_a_cell_blank_on_one_side_is_neither_a_departure_nor_an_edge(relation, blank):
    a = [10.0, 20.0, 30.0, 40.0, 5.0]
    b = [20.0, 40.0, 60.0, 80.0, 13.0] if relation == "ratio" else [20.0, 30.0, 40.0, 50.0, 13.0]
    (a if blank == "a" else b)[3] = math.nan
    a, b = _arr(a), _arr(b)
    q_a = [0.1 if math.isfinite(x) else math.nan for x in a]
    q_b = [0.1 if math.isfinite(x) else math.nan for x in b]
    interval = _symmetric_ratio_interval if relation == "ratio" else _symmetric_offset_interval
    spans = [interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    lo, hi = (1.99, 2.01) if relation == "ratio" else (9.9, 10.1)
    group = {"lo": lo, "hi": hi, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # the edge is read past the half-blank cell, at (5, 13)
    want = (12.95 / 5.05 - 2.0) * 5.0 / 0.1 if relation == "ratio" else (10.0 - 8.1) / 0.1
    assert _edge_steps(a, b, q_a, q_b, group, spans, relation) == pytest.approx([want])
    # with (5, 13) in the group, the half-blank cell is all that lies outside: nothing to measure
    group = {"lo": lo, "hi": hi, "positions": [0, 1, 2, 4], "segments": [[0, 2], [4, 4]]}
    assert _outside_departure(a, b, q_a, q_b, group, spans, relation) is None


@pytest.mark.filterwarnings("error")
def test_identity_steps_with_no_informative_cell_refuse_nothing():
    assert _identity_steps([(-math.inf, math.inf)] * 3, [], 2.0, "ratio") == math.inf


def test_identity_steps_are_read_in_the_groups_half_widths():
    spans = [(1.9, 2.1), (1.8, 2.2), (1.95, 2.05), (-math.inf, math.inf)]
    # half-widths 0.1, 0.2 and 0.05 over the informative cells: a median of 0.1
    assert _identity_steps(spans, [0, 1, 2], 2.0, "ratio") == pytest.approx(10.0)
    assert _identity_steps(spans, [0, 1, 2], 0.3, "offset") == pytest.approx(3.0)        # counted from 0, not 1


def test_edge_steps_count_a_ratio_gap_in_the_targets_steps():
    a, b = _arr([10.0, 20.0, 30.0, 5.0]), _arr([20.0, 40.0, 60.0, 13.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # nothing precedes the stretch; past its end the cell admits k from 12.95/5.05, a gap that is
    # (12.95/5.05 - 2) * 5 as an amount of B, counted in B's steps
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_walk_past_a_blank_and_the_groups_own_positions():
    a = _arr([10.0, 20.0, 30.0, math.nan, 15.0, 5.0])
    b = _arr([20.0, 40.0, 60.0, math.nan, 30.0, 13.0])
    q = [0.1] * 6
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 4], "segments": [[0, 2], [4, 4]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_count_an_offset_gap_in_the_pairs_combined_step():
    a, b = _arr([1.0, 2.0, 3.0, 4.0]), _arr([4.0, 5.0, 6.0, 7.5])
    q_a, q_b = [0.1] * 4, [0.01] * 4
    spans = [_symmetric_offset_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # the last cell admits c in 3.5 +/- 0.055: a gap of 0.445, in steps of (0.1 + 0.01) / 2
    assert _edge_steps(a, b, q_a, q_b, group, spans, "offset") == pytest.approx([0.445 / 0.055])


def test_edge_steps_walk_past_a_cell_that_confirms_nothing():
    a, b = _arr([10.0, 20.0, 30.0, 0.0, 5.0]), _arr([20.0, 40.0, 60.0, 0.0, 13.0])
    q = [0.1] * 5
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2], "segments": [[0, 2]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_read_the_segment_with_the_most_informative_cells():
    a = _arr([10.0, 0.0, 0.0, 0.0, 20.0, 30.0, 5.0, 7.0, 11.0, 12.0, 13.0, 14.0, 9.0])
    b = _arr([20.0, 0.0, 0.0, 0.0, 40.0, 60.0, 13.0, 30.0, 22.0, 24.0, 26.0, 28.0, 40.0])
    q = [0.1] * 13
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
             "segments": [[0, 5], [8, 11]]}
    # [0, 5] spans six positions but informs at three; [8, 11] informs at four: its edges are judged
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx(
        [(29.95 / 7.05 - 2.0) * 7.0 / 0.1, (39.95 / 9.05 - 2.0) * 9.0 / 0.1])


def test_edge_steps_take_the_earlier_of_two_equally_long_segments():
    a = _arr([10.0, 20.0, 30.0, 5.0, 8.0, 7.0, 11.0, 12.0, 13.0, 9.0])
    b = _arr([20.0, 40.0, 60.0, 13.0, 3.0, 30.0, 22.0, 24.0, 26.0, 40.0])
    q = [0.1] * 10
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 6, 7, 8], "segments": [[0, 2], [6, 8]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_count_a_zero_source_in_the_targets_steps():
    a, b = _arr([3.42, 1.87, 2.95, 0.0]), _arr([8.6, 4.7, 7.4, 0.3])
    q_a, q_b = [0.01] * 4, [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 2.49, "hi": 2.51, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # B's box starts at 0.25; k = 2.5 writes 0 +/- 0.005 as at most 0.0125: a miss of 0.2375, in B's steps
    assert _edge_steps(a, b, q_a, q_b, group, spans, "ratio") == pytest.approx([0.2375 / 0.1])


def test_a_line_is_usable_with_enough_cells_and_no_axis_shape():
    assert _local_line_usable(_arr([1.2, 3.4, math.nan, 2.7, 5.1]))
    assert not _local_line_usable(_arr([1.2, 3.4, math.nan, 2.7]))           # three finite cells
    assert not _local_line_usable(_arr([1.2, 3.4, 2.7, 5.1]), "Time (h)")      # an axis by its label
    assert not _local_line_usable(_arr([0.5, 1.0, math.nan, 1.5, 2.0]))       # a dose or time grid


def test_a_line_that_is_itself_a_grid_within_rounding_is_not_compared():
    assert not _local_line_usable(_arr([0.1, 0.2, 0.3, 0.4, 0.5]))                         # float error in a typed grid
    assert not _local_line_usable(_arr([round(i / 3, 2) for i in range(1, 9)]))           # thirds, at 2 dp
    assert not _local_line_usable(_arr([0.0, 1.0, 2.0, 3.0]))                              # a time axis from 0
    assert not _local_line_usable(_arr([100 / 2 ** i for i in range(8)] + [0.0]))          # a dilution and its vehicle
    assert not _local_line_usable(_arr([float(f"{50 / 3 ** i:.3g}") for i in range(7)]))   # 3-fold, at 3 figures
    assert not _local_line_usable(_arr([4.2] * 6))                                        # a constant line
    ladder = [100 / 2 ** i for i in range(6)]
    assert not _local_line_usable(_arr([x for x in ladder for _ in range(3)]))            # each dose, once per replicate
    assert not _local_line_usable(_arr(ladder + [0.0] + ladder + [0.0]))                  # the ladder listed twice
    assert _local_line_usable(_arr([0.0, 0.0, 3.2, 6.4, 0.0]))                            # two ratios are not a ladder
    assert _local_line_usable(_arr([10.01, 11.02, 12.08, 13.01, 14.02]))                  # off any grid by more than rounding
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**（五个函数一起加入，各有直接测试）

```python
def _outside_departure(a_line, b_line, q_a, q_b, group, spans, relation):
    """Median departure from the group's constant at the line's other positions, beyond
    what each cell's own rounding explains.

    The gap is from the constant to the cell's interval, so rounding alone never counts.
    Ratio: that gap as an amount of B (gap * |a|) over the pair's median |b| -- measured
    against the line's scale, not the cell's, so additive noise on a near-zero cell is
    not read as a departure (curve families leak through their smallest cell otherwise).
    Offset: the gap over |c|. A source read as zero (no interval; ratio arm only, since offset
    intervals always exist) departs by what the constant falls short of writing it
    (`_zero_source_miss`), over the pair's median |b|. That median is read over the pair's
    cells: those where both lines have values and that confirm something -- B against a blank
    in A is not part of the pair, and zeros against zeros filling most of a line would
    otherwise make it 0. When the median is 0 all the same -- B zero against non-zero A
    across most of the pair's cells, inside the stretch or out -- every ratio departure is
    recorded at the cap. Departures are capped at `_LOCAL_RUN_DEPARTURE_CAP`. None when
    nothing outside the group can be measured -- the relation covers the whole line, or what
    lies outside confirms nothing -- and for a constant of 0 or one that is not finite: a
    stretch of zeros in B is not a ratio this pass looks for, and an offset of 0 is the
    identity.
    """
    k = (group["lo"] + group["hi"]) / 2.0
    if k == 0.0 or not math.isfinite(k):
        return None
    inside = set(group["positions"])
    finite_b = [abs(float(b_line[i])) for i, s in enumerate(spans)
                if math.isfinite(float(a_line[i])) and math.isfinite(float(b_line[i]))
                and not _confirms_nothing(s)]
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
    informative cells). Read off the intervals, so no cell value is divided by. With no
    informative cell there is no width to count in, and the constant is not refused here."""
    if not informative:
        return math.inf
    half = float(np.median([(spans[i][1] - spans[i][0]) / 2.0 for i in informative]))
    identity = 1.0 if relation == "ratio" else 0.0
    return abs(constant - identity) / half if half > 0 else math.inf


def _edge_steps(a_line, b_line, q_a, q_b, group, spans, relation):
    """Departure, in the cell's own rounding steps and beyond rounding, of the first cell
    past each end of the group's longest segment -- walking outward over blank cells (a
    panel separator), cells that confirm nothing and the group's own positions, so the cell
    judged is the data that actually adjoins the stretch. The longest segment is the one the
    contiguity test calls longest, by informative cells; of two equally long, the earlier.
    [] when nothing lies outside on either side. A source read as zero counts by what the
    constant falls short of writing it, in B's steps."""
    inside = set(group["positions"])
    informing = {i for i in inside if spans[i] is not None and not _confirms_nothing(spans[i])}
    start, end = max(group["segments"],
                     key=lambda se: (sum(i in informing for i in range(se[0], se[1] + 1)), -se[0]))
    k = (group["lo"] + group["hi"]) / 2.0
    out = []
    for i, stride in ((start - 1, -1), (end + 1, 1)):
        while 0 <= i < len(spans) and (i in inside or _confirms_nothing(spans[i]) or not (
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
        gap = lo - k if k < lo else (k - hi if k > hi else 0.0)
        if relation == "ratio":
            out.append(abs(gap) * abs(float(a_line[i])) / q_b[i] if q_b[i] > 0 else math.inf)
        else:
            step = (q_a[i] + q_b[i]) / 2.0
            out.append(abs(gap) / step if step > 0 else math.inf)
    return out


def _line_is_grid(values):
    """Does one difference, or one ratio, write every step between consecutive cells with
    values, within their rounding -- a time or dose axis, a dilution series, a constant line?

    A line that repeats values is read a second time with each value at its first appearance
    only, so a dose listed once per replicate, or a whole ladder listed twice, still reads as
    its ladder. Zeros are left out of the ratio reading: a dilution series ends in its vehicle
    at 0. The difference reading also takes a data line that is straight within its rounding;
    a stretch copied into such a line is then not looked for -- the cost of keeping the
    parallel, then diverging, straight trends of benign data out.
    """
    cells = [(float(v), q) for v, q in zip(values, _effective_line_quantums(values))
             if math.isfinite(float(v))]
    seen, first = set(), []
    for c in cells:
        if c[0] not in seen:
            seen.add(c[0])
            first.append(c)
    for line in ([cells, first] if len(first) < len(cells) else [cells]):
        for interval, steps in ((_symmetric_offset_interval, line),
                                (_symmetric_ratio_interval, [c for c in line if c[0] != 0.0])):
            if len(steps) < _LOCAL_RUN_MIN_FINITE:
                continue
            lo, hi = -math.inf, math.inf
            for (x, q_x), (y, q_y) in zip(steps, steps[1:]):
                span = interval(x, y, q_x, q_y)
                if span is None:
                    break
                lo, hi = max(lo, span[0]), min(hi, span[1])
                if lo > hi:
                    break
            else:
                return True
    return False


def _local_line_usable(values, label=None):
    """Enough finite cells for a stretch and an outside, not an axis by its row label, and
    not itself a grid (`_line_is_grid`)."""
    finite = values[np.isfinite(values)]
    if finite.size < _LOCAL_RUN_MIN_FINITE:
        return False
    if label and _AXIS_CONTEXT_LABEL_RE.search(label):
        return False
    return not _line_is_grid(values)
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 55 passed.

- [ ] **Step 4b: 逐条核对“删掉即变红”**

每一项在磁盘上改动后跑一次测试文件，确认点名的测试失败，再从备份恢复并用 `cmp` 确认与备份逐字节相同。每次运行都设 `PYTHONDONTWRITEBYTECODE=1`。替换只在对应函数体内做（`gap = ...` 一行在两个函数里各出现一次）。

| 改动 | 必须失败的测试 |
|---|---|
| `_outside_departure`：比例臂的尺度 `scale` 换成该格自身的 `abs(b)` | `test_outside_departure_of_a_ratio_is_read_against_the_lines_scale` |
| `_outside_departure`：差值臂 `abs(gap / k)` 改为 `abs(gap)` | `test_outside_departure_of_an_offset_is_its_gap_over_the_constant` |
| `_outside_departure`：`gap` 去掉 `k > hi` 分支 | `test_outside_departure_of_an_offset_is_its_gap_over_the_constant` |
| `_outside_departure`：源读作 0 的格改为直接记上限 | `test_outside_departure_measures_a_source_read_as_zero_by_its_miss` |
| `_outside_departure`：尺度取全部有值的格（含两侧都读作 0 的格） | `test_outside_departure_reads_its_scale_from_cells_that_confirm_something` |
| `_outside_departure`：不提供信息的组外格记为偏离 0 | `test_outside_departure_reads_its_scale_from_cells_that_confirm_something` |
| `_identity_steps`：半宽改为全宽 | `test_identity_steps_are_read_in_the_groups_half_widths` |
| `_edge_steps`：比例臂去掉 `abs(float(a_line[i]))` 因子 | `test_edge_steps_count_a_ratio_gap_in_the_targets_steps` |
| `_edge_steps`：差值臂步长改为 `q_b[i]` | `test_edge_steps_count_an_offset_gap_in_the_pairs_combined_step` |
| `_edge_steps`：向外走时不跳过组内位置 | `test_edge_steps_walk_past_a_blank_and_the_groups_own_positions` |
| `_edge_steps`：向外走时不跳过空格 | `test_edge_steps_walk_past_a_blank_and_the_groups_own_positions` |
| `_edge_steps`：向外走时不跳过不提供信息的格 | `test_edge_steps_walk_past_a_cell_that_confirms_nothing` |
| `_local_line_usable`：最少有限格数、行标签两条规则各关掉一次 | `test_a_line_is_usable_with_enough_cells_and_no_axis_shape` |
| `_local_line_usable`：不问 `_line_is_grid` | `test_a_line_that_is_itself_a_grid_within_rounding_is_not_compared` |
| `_line_is_grid`：去掉差值或比例的读法之一；比例读法不去 0；差值读法也去 0 | `test_a_line_that_is_itself_a_grid_within_rounding_is_not_compared` |
| `_line_is_grid`：不按首次出现再读一遍；每种读法的格数下限改成 2 | `test_a_line_that_is_itself_a_grid_within_rounding_is_not_compared` |
| `_outside_departure`：尺度也取 A 为空的格 | `test_outside_departure_reads_its_scale_over_the_pair_only` |
| `_outside_departure`：源读作 0 的格在尺度为 0 时记 0 而不记上限 | `test_a_source_read_as_zero_departs_at_the_cap_when_the_pairs_scale_is_zero` |
| `_outside_departure`：只查 a 是否有值 | `test_a_cell_blank_on_one_side_is_neither_a_departure_nor_an_edge` |
| `_outside_departure`：中位数换成均值 | `test_outside_departure_is_the_median_of_the_outside_not_its_mean` |
| `_outside_departure`：尺度为 0 时记 0 而不记上限 | `test_outside_departure_is_capped_when_the_lines_scale_is_zero` |
| `_outside_departure`：去掉 `k == 0` 的拒绝 | `test_outside_departure_refuses_a_constant_of_zero` |
| `_identity_steps`：差值也从 1 算起 | `test_identity_steps_are_read_in_the_groups_half_widths` |
| `_identity_steps`：去掉空输入的判断（numpy 警告当作错误） | `test_identity_steps_with_no_informative_cell_refuse_nothing` |
| `_edge_steps`：按跨度而不按有信息的格数选最长一段；取最短一段；并列取靠后的 | `test_edge_steps_read_the_segment_with_the_most_informative_cells`、`test_edge_steps_take_the_earlier_of_two_equally_long_segments` |
| `_edge_steps`：向外走时只查 a（或只查 b）是否有值 | `test_a_cell_blank_on_one_side_is_neither_a_departure_nor_an_edge` |
| `_edge_steps`：源读作 0 的格按 A 的步长计 | `test_edge_steps_count_a_zero_source_in_the_targets_steps` |

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

- [ ] **Step 1: 写测试**（导入行加入 `_best_local_group, _local_group_bits, _ratio_prediction_bits`；先加一个辅助函数）

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


def test_cells_that_confirm_nothing_do_not_make_scattered_consistency_a_stretch():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    assert _best([0.0] * 6 + a, [0.0] * 6 + b) is None          # zero against zero, six times


def test_a_stretch_is_reported_without_the_shared_zeros_beside_it():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best([0.0] * 4 + A10, [0.0] * 4 + b)
    assert relation == "ratio" and group["positions"] == list(range(4, 10))


def test_a_pair_pays_a_pass_over_the_line_for_each_run_before_grouping():
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    budget = [100]
    assert _best_local_group(a, b, q_a, q_b, 4, budget) is not None
    assert budget == [100 - len(A10)]          # one ratio run, no offset run
    budget = [len(A10) - 1]
    assert _best_local_group(a, b, q_a, q_b, 4, budget) is None and budget[0] < 0     # abandoned


def test_cells_that_confirm_nothing_inside_a_stretch_do_not_carry_scattered_consistency():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    for at in (1, 5, 9, 13):
        assert _best(a[:at] + [0.0] * 6 + a[at:], b[:at] + [0.0] * 6 + b[at:]) is None
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best(A10[:3] + [0.0] * 3 + A10[3:], b[:3] + [0.0] * 3 + b[3:])
    assert relation == "ratio" and group["segments"] == [[0, 8]]


def test_cells_that_confirm_nothing_set_neither_the_scale_nor_the_outside():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [round(x * 1.137 * 1.02, 3) for x in A10[6:]]
    assert _best(A10 + [0.0] * 12, b + [0.0] * 12) is None
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best(A10 + [0.0] * 12, b + [0.0] * 12)
    assert relation == "ratio" and group["segments"] == [[0, 5]]


def test_a_groups_bits_are_summed_over_its_segments_each_against_its_own_spread():
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    whole = {"segments": [[0, 9]], "informative": list(range(10))}
    split = {"segments": [[0, 4], [5, 9]], "informative": list(range(10))}
    lone = {"segments": [[0, 0], [4, 4], [9, 9]], "informative": [0, 4, 9]}
    assert _local_group_bits(a, b, q_a, q_b, lone, 1) == 0.0          # each cell only anchors

    def by_segment(line, q):        # each segment against its own spread, as the docstring says
        return sum(_ratio_prediction_bits([line[i] for i in range(s, e + 1)], [q[i] for i in range(s, e + 1)], 1)
                   ["raw_bits"] for s, e in split["segments"])

    assert _local_group_bits(a, b, q_a, q_b, split, 1) == pytest.approx(min(by_segment(b, q_b), by_segment(a, q_a)))
    assert _local_group_bits(a, b, q_a, q_b, split, 1) < _local_group_bits(a, b, q_a, q_b, whole, 1)
    assert _local_group_bits(a, b, q_a, q_b, split, 8) == pytest.approx(_local_group_bits(a, b, q_a, q_b, split, 1) - 3)


def test_a_groups_bits_are_those_of_its_less_precise_side():
    a, coarse = _arr(A10), _arr([round(x * 1.137, 1) for x in A10])
    q_a, q_c = _effective_line_quantums(a), _effective_line_quantums(coarse)
    group = {"segments": [[0, 9]], "informative": list(range(10))}
    coarse_side = _ratio_prediction_bits(list(coarse), q_c, 1)["raw_bits"]
    assert _local_group_bits(a, coarse, q_a, q_c, group, 1) == pytest.approx(coarse_side)
    assert _local_group_bits(coarse, a, q_c, q_a, group, 1) == pytest.approx(coarse_side)


def test_a_source_read_as_zero_does_not_carry_scattered_consistency():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.447, 13.103, 11.774]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    for at in (1, 5, 9, 13):        # six sources read as zero, each target one step above it
        assert _best(a[:at] + [0.0] * 6 + a[at:], b[:at] + [0.001] * 6 + b[at:]) is None


def test_a_groups_bits_score_only_its_informative_cells():
    a = _arr(A10[:4] + [0.0] + A10[4:])
    b = _arr([round(x * 1.137, 3) for x in A10[:4]] + [0.0] + [round(x * 1.137, 3) for x in A10[4:]])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    keep = [i for i in range(11) if i != 4]                   # the zero against zero informs nothing
    group = {"segments": [[0, 10]], "positions": list(range(11)), "informative": keep}
    expected = min(_ratio_prediction_bits([b[i] for i in keep], [q_b[i] for i in keep], 1)["raw_bits"],
                   _ratio_prediction_bits([a[i] for i in keep], [q_a[i] for i in keep], 1)["raw_bits"])
    assert _local_group_bits(a, b, q_a, q_b, group, 1) == pytest.approx(expected)


def test_a_pair_that_cannot_pay_for_its_runs_is_abandoned_before_grouping(monkeypatch):
    import paperconan._audit as audit_module

    def grouped(*args, **kwargs):
        raise AssertionError("grouped before paying for the runs")

    monkeypatch.setattr(audit_module, "_group_constant_runs", grouped)
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2])
    budget = [len(A10) - 1]
    assert _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), 4, budget) is None
    assert budget[0] < 0


def test_an_offset_exactly_ten_steps_from_the_identity_is_reported():
    rng = random.Random(20261018)
    for _ in range(20):           # 1.0 over a half-width of 0.1: on the lattice, so float error must not decide
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(10)]
        b = [round(x + 1.0, 1) for x in a[:8]] + [round(a[8] + 3.7, 1), round(a[9] - 4.4, 1)]
        assert _best(a, b) is not None


def test_an_offset_nine_and_a_half_steps_from_the_identity_is_not_reported():
    rng = random.Random(20261021)
    for _ in range(20):           # the tolerance only takes float error: 0.95 over 0.1 is not ten steps
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(10)]
        b = [round(x + d, 1) for x, d in zip(a[:8], [1.0, 0.9] * 4)] + [round(a[8] + 3.7, 1), round(a[9] - 4.4, 1)]
        assert _best(a, b) is None


def test_prediction_bits_exactly_at_the_threshold_are_enough():
    a = [13.6, 10.4, 11.5, 13.6, 10.4, 9.9, 19.6, 7.3, 9.4, 20.9]
    b = [37.639, 28.783, 31.827, 37.639, 28.783, 23.289, 49.875, 10.533, 13.05, 43.372]
    # a segment spreading over 32 of A's steps: four confirming cells at 5 bits each, exactly 20
    assert _best(a, b, n_tests=1) is not None


def test_an_edge_exactly_three_steps_off_is_abrupt_enough():
    rng = random.Random(20261019)
    for _ in range(20):           # 3.4 against 3.0 at 1 dp: 0.3 beyond rounding, in steps of 0.1
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(11)]
        b = ([round(x + 3.0, 1) for x in a[:8]]
             + [round(a[8] + 3.4, 1), round(a[9] + 9.7, 1), round(a[10] - 4.2, 1)])
        assert _best(a, b) is not None


def test_an_outside_departing_by_exactly_the_threshold_is_not_local():
    rng = random.Random(20261020)
    for _ in range(20):           # four of six outside cells off by 0.2 over |c| = 4: a median of exactly 0.05
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(14)]
        b = [round(x + o, 1) for x, o in zip(a, [4.0] * 8 + [9.1] + [4.3] * 4 + [11.3])]
        assert _best(a, b) is None


def test_an_offset_interval_holding_zero_is_the_identity():
    # integer cells pin the offset to [0, 1]; finer cells whose differences fall anywhere in it
    # would otherwise be scored at their own step
    a = [40.0, 48.0, 23.0, 60.0, 44.0, 34.0, 31.0, 71.0, 29.0, 34.788, 60.158, 68.552, 78.372, 80.404,
         21.0, 18.0, 12.0, 61.0, 80.0, 47.0]
    b = [17.0, 38.0, 76.0, 78.0, 44.0, 35.0, 32.0, 56.0, 45.0, 34.916, 60.753, 69.207, 78.877, 80.614,
         32.0, 23.0, 43.0, 37.0, 13.0, 43.0]
    assert _best(a, b) is None


@pytest.mark.parametrize("mirrored", [False, True])
def test_a_stretch_needs_an_abrupt_edge_at_both_ends(mirrored):
    a = [10.0, 11.3, 12.5, 13.6, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, 22.6, 23.8]
    d = [5.0, 9.1, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.6, 1.7]      # abrupt on one side only
    if mirrored:
        a, d = a[::-1], d[::-1]
    assert _best(a, [round(x + e, 1) for x, e in zip(a, d)]) is None


def test_the_group_with_the_most_bits_is_chosen_over_the_longest():
    a = [10.412, 10.087, 10.733, 10.291, 10.958, 10.164, 10.645, 10.379, 3.1, 47.0, 22.9, 8.8, 39.4, 15.2,
         5.0, 42.0, 17.3]
    b = [round(x * 2.0, 3) for x in a[:8]] + [round(x * 0.6, 3) for x in a[8:14]] + [33.3, 1.7, 40.2]
    relation, group, _bits, _dep = _best(a, b)
    # eight cells of k = 2 over a narrow range carry less than six of k = 0.6 over a wide one
    assert relation == "ratio" and group["positions"] == list(range(8, 14))


def test_a_tie_between_the_arms_goes_to_the_ratio():
    a = [1000.067, 1000.424, 1000.382, 1000.128, 1000.248, 1000.225, 1000.326, 1000.394, 918.772, 905.669,
         1067.153, 986.553, 1052.456, 900.421]
    b = [1000.567, 1000.924, 1000.882, 1000.628, 1000.748, 1000.725, 1000.826, 1000.894, 989.077, 1044.308,
         945.752, 1089.054, 1080.285, 906.118]
    # both arms hold cells 0..7 and score the same cells, so the same bits
    assert _best(a, b)[0] == "ratio"


def test_a_pair_pays_once_for_every_run():
    a = _arr(A10 + [10.051, 12.884, 9.447, 13.103, 11.774, 8.25])
    b = _arr([round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1, 1.2, 15.0]
             + [round(x * 0.6, 3) for x in a[11:]])
    budget = [1000]
    _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), 4, budget)
    assert budget == [1000 - 2 * len(a)]          # two ratio runs, no offset run


def test_a_stretch_holding_exactly_half_of_its_positions_is_contiguous_enough():
    a = A10 + [10.051, 12.884, 9.447, 13.103, 11.774, 8.25]
    b = [round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1, 1.2, 15.0] + [round(x * 0.6, 3) for x in a[11:]]
    relation, group, _bits, _dep = _best(a, b)
    assert relation == "ratio" and group["segments"] == [[0, 4], [11, 15]]


def test_a_source_read_as_zero_is_admitted_by_any_constant_of_the_group():
    a = [7.7, 7.6, 1.4, 1.6, 6.8, 6.2, 5.7, 0.0, 3.2, 5.2, 5.2]
    b = [19.17, 18.92, 3.49, 3.98, 16.93, 15.44, 14.19, 0.13, 12.46, 4.85, 9.75]
    # 0.13 needs |k| of at least 2.5; the group's interval reaches it, though not its middle
    relation, group, _bits, _dep = _best(a, b, n_tests=1)
    assert relation == "ratio" and group["positions"] == list(range(8))


def test_two_independently_sorted_lines_are_not_a_stretch():
    rng = random.Random(20261017)
    a = sorted(round(rng.uniform(5.0, 20.0), 3) for _ in range(1000))
    b = sorted(round(rng.uniform(5.0, 20.0), 3) for _ in range(1000))
    assert _best(a, b, n_tests=2 * 1000) is None


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
# Fixed-decimal steps put counts on a lattice: in the offset arm the identity and edge steps fall
# on halves when both sides share a step (on a finer rational lattice otherwise), the outside
# departure on round values, and prediction bits on whole numbers when the steps are powers of
# two apart. At a lattice point float error, not the data, decided which side of a threshold a
# count fell, so thresholds are compared within this relative tolerance.
_LOCAL_RUN_THRESHOLD_RTOL = 1e-9


def _local_group_bits(a_line, b_line, q_a, q_b, group, n_tests):
    """A group's prediction bits: each side scored as the target, keeping the lower, since
    neither side is privileged when both were rounded. Only informative cells are scored.

    Each segment is scored against its own spread, the segments' bits summed and the search
    penalty, log2(n_tests), taken once. Scored over the whole group at once, positions
    scattered along a sorted or smooth line would be credited with the whole group's spread,
    up to the line's whole range, though each is predictable from its neighbours to within the
    local spacing; two independently sorted columns then read as a stretch. Within a segment
    the spread is local, and a cell alone in its segment only anchors it and earns nothing.
    """
    inside = set(group["informative"])
    cells = [[i for i in range(s, e + 1) if i in inside] for s, e in group["segments"]]
    penalty = math.log2(n_tests) if n_tests > 1 else 0.0

    def side(line, q):
        return sum(_ratio_prediction_bits([line[i] for i in c], [q[i] for i in c], 1)["raw_bits"]
                   for c in cells if c) - penalty

    return min(side(b_line, q_b), side(a_line, q_a))


def _best_local_group(a_line, b_line, q_a, q_b, n_tests, budget=None):
    """The highest-scoring group on one pair of lines that passes the design's pairwise tests
    (section 4; which lines take part, and which pairs are already reported, are the
    caller's), as (relation, group, bits, departure), or None. Highest by bits; a tie keeps
    the first found -- the ratio arm before the offset, and within an arm the group seeded by
    the longest run, of equally long runs the earlier.

    Each group costs a pass over the line, and a slowly drifting relation can hold many of
    them. So when `budget` (a one-element list of remaining cell operations) is given, each
    arm pays one line's length per run before grouping them -- the runs bound the groups --
    and when it cannot, the pair is abandoned: None, with budget[0] below zero."""
    rtol = _LOCAL_RUN_THRESHOLD_RTOL
    best = None
    for relation, interval in (("ratio", _symmetric_ratio_interval),
                               ("offset", _symmetric_offset_interval)):
        spans = [interval(a, b, x, y) for a, b, x, y in zip(a_line, b_line, q_a, q_b)]
        admit = None
        if relation == "ratio":
            def admit(i, lo, hi):           # a non-finite b or step misses by inf; a non-finite a
                # is read at a NaN step (`_effective_line_quantums`), so it misses too
                return _zero_source_miss(b_line[i], q_a[i], q_b[i], max(abs(lo), abs(hi))) == 0.0
        runs = _scan_interval_runs(spans, a_line)
        if budget is not None:
            budget[0] -= len(spans) * len(runs)
            if budget[0] < 0:
                return None
        for g in _group_constant_runs(runs, spans, admit):
            lo, hi = g["lo"], g["hi"]
            # an interval holding the identity 1 holds 10^0: a unit restatement or the same values
            if relation == "ratio" and _interval_holds_a_power_of_ten(lo, hi):
                continue
            if relation == "offset" and lo <= 0.0 <= hi:
                continue
            # every group holds a run, and `_scan_interval_runs` asked that run for
            # `_LOCAL_RUN_MIN_DISTINCT_SOURCE` distinct source values among its informative cells
            inf = g["informative"]
            informing = set(inf)
            # counted in informative cells: zeros against zeros, and sources read as zero that a
            # large enough constant writes, neither lengthen a stretch nor add to what it accounts for
            told = [sum(i in informing for i in range(s, e + 1)) for s, e in g["segments"]]
            if max(told) < _LOCAL_RUN_MIN_CONTIGUOUS_SHARE * sum(told):
                continue
            if (_identity_steps(spans, inf, (lo + hi) / 2.0, relation)
                    < _LOCAL_RUN_MIN_IDENTITY_STEPS * (1.0 - rtol)):
                continue
            bits = _local_group_bits(a_line, b_line, q_a, q_b, g, n_tests)
            if bits < _LOCAL_RUN_MIN_BITS * (1.0 - rtol):
                continue
            departure = _outside_departure(a_line, b_line, q_a, q_b, g, spans, relation)
            if departure is None or departure <= _LOCAL_RUN_OUT_DEV * (1.0 + rtol):
                continue
            if any(e < _LOCAL_RUN_MIN_EDGE_STEPS * (1.0 - rtol)
                   for e in _edge_steps(a_line, b_line, q_a, q_b, g, spans, relation)):
                continue
            if best is None or bits > best[0]:
                best = (bits, relation, g, bits, departure)
    return None if best is None else best[1:]
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 99 passed.

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

- [ ] **Step 5b: 本任务其余判据的“删掉即变红”**

做法同 Task 3 的 Step 4b。

| 改动 | 必须失败的测试 |
|---|---|
| 成段比例按位置数计，而不按有信息的格数 | `test_cells_that_confirm_nothing_inside_a_stretch_do_not_carry_scattered_consistency` |
| 成段比例把源读作 0 而被写出的格也算作有信息 | `test_a_source_read_as_zero_does_not_carry_scattered_consistency` |
| `_local_group_bits`：把组内无信息的位置也拿来计分 | `test_a_groups_bits_score_only_its_informative_cells` |
| `_local_group_bits`：整组一起打分，不分段 | `test_two_independently_sorted_lines_are_not_a_stretch` |
| `_local_group_bits`：每段各减一次搜索惩罚 | `test_a_groups_bits_are_summed_over_its_segments_each_against_its_own_spread` |
| `_local_group_bits`：只取一侧，或取较大的一侧 | `test_a_groups_bits_are_those_of_its_less_precise_side` |
| 分组前不扣预算，或扣不起时不放弃 | `test_a_pair_pays_a_pass_over_the_line_for_each_run_before_grouping` |
| 分组之后才按组数扣预算 | `test_a_pair_that_cannot_pay_for_its_runs_is_abandoned_before_grouping` |
| `_identity_steps` 传入组内全部位置，而不是有信息的格 | `test_a_source_read_as_zero_inside_a_stretch_joins_it_when_the_constant_writes_it` |
| 离开恒等、组外偏离、边缘、bits 四处比较去掉 `_LOCAL_RUN_THRESHOLD_RTOL`（各一次） | `test_an_offset_exactly_ten_steps_from_the_identity_is_reported`、`test_an_outside_departing_by_exactly_the_threshold_is_not_local`、`test_an_edge_exactly_three_steps_off_is_abrupt_enough`、`test_prediction_bits_exactly_at_the_threshold_are_enough` |
| `_LOCAL_RUN_THRESHOLD_RTOL` 放大到 0.06 | `test_an_offset_nine_and_a_half_steps_from_the_identity_is_not_reported` |
| 去掉差值臂“区间含 0”的拒绝 | `test_an_offset_interval_holding_zero_is_the_identity` |
| 边缘判据 `any` 改成 `all` | `test_a_stretch_needs_an_abrupt_edge_at_both_ends` |
| 合格组之间取第一个，或取得分较低的 | `test_the_group_with_the_most_bits_is_chosen_over_the_longest` |
| 两臂的顺序对调（差值在前） | `test_a_tie_between_the_arms_goes_to_the_ratio` |
| 按段扣预算的乘数封顶为 1 | `test_a_pair_pays_once_for_every_run` |
| 成段比例的比较写成 `<=` | `test_a_stretch_holding_exactly_half_of_its_positions_is_contiguous_enough` |
| `admit` 用组区间的中点或最小 \|k\| | `test_a_source_read_as_zero_is_admitted_by_any_constant_of_the_group` |
| 预算恰好用到 0 就放弃（`<= 0`） | Task 6 的 `test_a_pair_that_spends_the_budget_exactly_keeps_its_finding` |

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


def test_a_findings_n_counts_the_cells_the_relation_was_tested_on():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10[:3] + [0.0] * 3 + A10[3:], b[:3] + [0.0] * 3 + b[3:]])
    (finding,) = detect_local_pair_runs(sheet, 1, 14, 0, 2, header)
    # three zeros against zeros sit inside the stretch and confirm nothing
    assert finding["run_length"] == 9 and finding["n"] == 6


class _Coverage:
    def __init__(self):
        self.calls = []

    def add_limitation(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_an_exhausted_budget_is_recorded(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    for budget in (5, 15):          # the scan cannot be paid for; the scan can, its one run cannot
        monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", budget)
        cov = _Coverage()
        assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov) == []
        assert cov.calls and cov.calls[0][1]["detector"] == "detect_local_pair_runs"


def test_the_run_work_of_a_pair_is_charged_to_the_blocks_budget(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([A10, b, c])
    # three column pairs pay 10 each for the scan; the first pair's one run pays 10 more
    for budget, exhausted in ((41, False), (31, True)):
        monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", budget)
        cov = _Coverage()
        assert len(detect_local_pair_runs(sheet, 1, 11, 0, 3, header, coverage=cov)) == 1
        assert bool(cov.calls) is exhausted


def test_a_pair_that_spends_the_budget_exactly_keeps_its_finding(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 20)     # 10 for the scan, 10 for its one run
    cov = _Coverage()
    assert len(detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov)) == 1 and not cov.calls


def test_a_pair_whose_scan_spends_the_budget_exactly_is_scanned(monkeypatch):
    import paperconan._audit as audit_module
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([A10, c])
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 10)     # the scan, and no run to pay for
    cov = _Coverage()
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov) == [] and not cov.calls
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
        segments=segments, run_length=len(positions), n=len(group["informative"]), line_length=paired,
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
    budget = [_LOCAL_RUN_BUDGET]

    def exhausted():
        print(f"[paperconan] detect_local_pair_runs: budget exhausted on a "
              f"{r1 - r0}x{c1 - c0} block — coverage bounded", file=sys.stderr)
        _note_detector_cap(coverage, "detect_local_pair_runs", "detector_compute_budget_limit")
        return findings

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
                budget[0] -= length              # the scan; the call pays for its runs
                if budget[0] < 0:
                    return exhausted()
                if (orientation, ia, ib) in already:
                    continue
                hit = _best_local_group(lines[ia], lines[ib], quantums[ia], quantums[ib],
                                        n_tests, budget)
                if budget[0] < 0:                # abandoned part-way
                    return exhausted()
                if hit is not None:
                    findings.append(_local_run_finding(orientation, ia, ib, labels, first,
                                                       lines[ia], lines[ib], *hit))
    return findings
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_local_pair_runs.py -q`
Expected: 109 passed.

- [ ] **Step 4b: 预算的“删掉即变红”**

做法同 Task 3 的 Step 4b。

| 改动 | 必须失败的测试 |
|---|---|
| 调用 `_best_local_group` 时不传 `budget` | `test_the_run_work_of_a_pair_is_charged_to_the_blocks_budget` |
| 调用后不检查 `budget[0] < 0`（中途放弃） | `test_an_exhausted_budget_is_recorded` |
| 调用后的检查写成 `<= 0` | `test_a_pair_that_spends_the_budget_exactly_keeps_its_finding` |
| 付扫描之后的检查写成 `<= 0` | `test_a_pair_whose_scan_spends_the_budget_exactly_is_scanned` |
| `n` 取组内全部位置数 | `test_a_findings_n_counts_the_cells_the_relation_was_tested_on` |

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
Expected: 110 passed.

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
Expected: 114 passed.

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
  MONOTONE     two lines drawn independently, each sorted (side-by-side ranked lists) or
               each a smooth curve with noise. Each moves little from one cell to the next,
               so a constant fits a few neighbouring cells by coincidence; scored against the
               whole line's range such a stretch once read as significant.
  DOSE         two dilution series with different top concentrations, one ending in its
               vehicle at 0 where the other has one dilution more: a whole-line ratio whose
               last cell departs. Listed once per dose, once per replicate, or twice over.
               Each line is itself a grid, which is what keeps it out.
  PARALLEL     two straight trends, noisy within about a rounding step, running parallel and
               then diverging. At the cell level this is a stretch of one line shifted onto
               the other; frozen as measured, a recorded cost rather than a target.
  MIXED        a line recorded in whole numbers that turns to three decimals partway, against
               another doing the same, the decimal part a benign shift plus noise. A coarse group
               interval met by fine cells credits them at their own step; frozen as measured.
  DECAY        two proportional decays with a near-zero tail: a whole-line relation that
               locality should refuse, but the tail makes the line's median |b| -- the scale
               the ratio arm reads departures against -- small. Frozen as measured.
  PLANTED      one stretch (4 or 6 cells) copied from a line, then scaled or shifted, in
               columns and in rows. A bench of things that must not fire is passed perfectly
               by a detector that never fires; this is the side that gives it teeth.
  PLANTED_MONOTONE  a 6-cell stretch copied into one of two MONOTONE lines: the cost side of
               scoring by segment, since within a sorted or smooth line a segment's spread is
               local and a copied stretch there carries less than among unrelated values.
  PLANTED_STRAIGHT  8 cells of a straight line with noise under a rounding step, shifted onto
               another: the cost side of reading a line straight within rounding as a grid,
               which leaves it out of the comparison (only some draws come out that straight).

Verdicts, not counts (CLAUDE.md "Freeze a verdict, not a count"): each stratum is the band
of _verdict -- silent / partial / pervasive -- over its draws, using the column-pair bench's
own banding so the three benches read alike.

Regenerate with `python -m tests.test_local_run_bench_baseline` from the repository root.
No real data: every value comes from `random.Random` seeded below.
"""
from __future__ import annotations

import functools
import math
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


# --- benign: two independent monotone lines --------------------------------------------------

MONOTONE = (("sorted", 100, 2), ("sorted", 300, 2), ("sorted", 1000, 3), ("smooth", 100, 2), ("smooth", 100, 3))


def _monotone_pair(rng, shape, n, decimals):
    if shape == "sorted":
        return [sorted(round(rng.uniform(5.0, 20.0), decimals) for _ in range(n)) for _ in range(2)]

    def curve(top, base):
        mid = rng.uniform(0.3, 0.7)
        return [round(top / (1.0 + math.exp(-8.0 * (i / (n - 1) - mid))) + base + rng.gauss(0.0, 0.02), decimals)
                for i in range(n)]

    return [curve(10.0, 1.0), curve(12.0, 2.0)]


@functools.lru_cache(maxsize=None)
def _measure_monotone(shape, n, decimals):
    rng = random.Random(SEED * 19 + n * 109 + decimals * 7 + (shape == "smooth"))
    return CP._verdict(_local(_pair_sheet(*_monotone_pair(rng, shape, n, decimals)))
                       for _ in range(REPEATS))


# --- benign: two dilution series ---------------------------------------------------------------

# (fold, significant figures with 0 = as computed, layout)
DOSES = tuple((fold, figures, layout) for fold in (2, 3) for figures in (0, 3)
              for layout in ("once", "per_replicate", "twice"))


def _dose_pair(rng, fold, figures, layout):
    def series(top, n):
        values = [top / fold ** i for i in range(n)]
        return [float(f"{v:.{figures}g}") for v in values] if figures else values
    top = round(rng.uniform(5.0, 100.0), 1)
    a, b = series(top, 9), series(top * rng.choice((0.2, 0.25, 0.5, 2.0, 3.0, 4.0)), 8) + [0.0]
    if layout == "per_replicate":
        return [x for x in a for _ in range(3)], [x for x in b for _ in range(3)]
    return (a * 2, b * 2) if layout == "twice" else (a, b)


@functools.lru_cache(maxsize=None)
def _measure_doses(fold, figures, layout):
    rng = random.Random(SEED * 31 + fold * 131 + figures + ("once", "per_replicate", "twice").index(layout) * 17)
    return CP._verdict(_local(_pair_sheet(*_dose_pair(rng, fold, figures, layout))) for _ in range(REPEATS))


# --- benign: two straight trends, parallel and then diverging ----------------------------------

PARALLEL = ((1, 0.05), (0, 0.5), (0, 1.0))          # (decimals, noise sd)


def _straight(rng, slope, base, noise, decimals, length=12):
    return [round(base + slope * t + rng.gauss(0.0, noise), decimals) for t in range(length)]


@functools.lru_cache(maxsize=None)
def _measure_parallel(decimals, noise):
    rng = random.Random(SEED * 37 + decimals * 137 + int(noise * 100))
    draws = []
    for _ in range(REPEATS):
        slope = rng.uniform(0.3, 1.0) if decimals else rng.uniform(3.0, 15.0)
        base, shift = rng.uniform(10.0, 40.0), rng.uniform(2.0, 8.0) * (1 if decimals else 6)
        bend = rng.choice((-1, 1)) * slope * rng.uniform(0.5, 1.5)
        a = _straight(rng, slope, base, noise, decimals)
        b = [round(base + shift + slope * t + (bend * (t - 7) if t >= 8 else 0.0) + rng.gauss(0.0, noise), decimals)
             for t in range(12)]
        draws.append(_local(_pair_sheet(a, b)))
    return CP._verdict(draws)


# --- benign: a line whose precision changes partway -------------------------------------------

MIXED = ((20, 8), (40, 8), (60, 12))          # (whole-number cells, three-decimal cells)


@functools.lru_cache(maxsize=None)
def _measure_mixed(whole, fine):
    rng = random.Random(SEED * 43 + whole * 149 + fine)
    draws = []
    for _ in range(REPEATS):
        a = [float(rng.randint(0, 20)) for _ in range(whole)]
        b = [float(rng.randint(0, 20)) for _ in range(whole)]
        tail = [round(rng.uniform(10.0, 30.0), 3) for _ in range(fine)]
        draws.append(_local(_pair_sheet(a + tail, b + [round(x + 1.5 + rng.gauss(0.0, 0.25), 3) for x in tail])))
    return CP._verdict(draws)


# --- benign: two proportional decays with a near-zero tail ------------------------------------

DECAYS = ((24, 2.0, 0.02, 2), (30, 3.0, 0.002, 3))


def _decay_pair(rng, n, rate, noise, decimals):
    k = rng.uniform(1.2, 2.0)
    base = [10.0 * math.exp(-t / rate) for t in range(n)]
    return ([round(v + rng.gauss(0.0, noise), decimals) for v in base],
            [round(k * v + rng.gauss(0.0, noise), decimals) for v in base])


@functools.lru_cache(maxsize=None)
def _measure_decays(n, rate, noise, decimals):
    rng = random.Random(SEED * 23 + n * 113 + decimals)
    return CP._verdict(_local(_pair_sheet(*_decay_pair(rng, n, rate, noise, decimals)))
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


PLANTED_MONOTONE = tuple((shape, decimals, relation)
                         for shape, decimals in (("sorted", 2), ("smooth", 2), ("smooth", 3))
                         for relation in ("ratio", "offset"))


@functools.lru_cache(maxsize=None)
def _measure_planted_monotone(shape, decimals, relation):
    rng = random.Random(SEED * 29 + decimals * 127 + (shape == "smooth") * 3 + (relation == "offset"))
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for _ in range(REPEATS):
        a, b = _monotone_pair(rng, shape, 100, decimals)
        start = rng.randrange(10, 100 - 16)
        k = rng.uniform(1.2, 2.4) if relation == "ratio" else round(rng.uniform(0.5, 3.0), decimals)
        for i in range(start, start + 6):
            b[i] = round(a[i] * k if relation == "ratio" else a[i] + k, decimals)
        draws.append(_found(_local(_pair_sheet(a, b)), kind, start + 2))    # header row, 1-based
    return CP._verdict(draws)


PLANTED_STRAIGHT = ((1, 0.05), (0, 0.5))          # (decimals, noise sd)


@functools.lru_cache(maxsize=None)
def _measure_planted_straight(decimals, noise):
    rng = random.Random(SEED * 41 + decimals * 139 + int(noise * 100))
    draws = []
    for _ in range(REPEATS):
        slope = rng.uniform(0.3, 1.0) if decimals else rng.uniform(3.0, 15.0)
        a = _straight(rng, slope, rng.uniform(10.0, 40.0), noise, decimals)
        shift = round(rng.uniform(2.0, 8.0) * (1 if decimals else 6), decimals)
        b = [round(x + shift, decimals) for x in a[:8]] + [round(rng.uniform(10.0, 60.0), decimals) for _ in range(4)]
        draws.append(_found(_local(_pair_sheet(a, b)), "local_offset_run", 2))     # the stretch starts at row 1
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
MONOTONE_BASELINE = {
    ('sorted', 100, 2): ('silent', {}),
    ('sorted', 300, 2): ('silent', {}),
    ('sorted', 1000, 3): ('silent', {}),
    ('smooth', 100, 2): ('silent', {}),
    ('smooth', 100, 3): ('silent', {}),
}

DOSE_BASELINE = {
    (2, 0, 'once'): ('silent', {}),
    (2, 0, 'per_replicate'): ('silent', {}),
    (2, 0, 'twice'): ('silent', {}),
    (2, 3, 'once'): ('silent', {}),
    (2, 3, 'per_replicate'): ('silent', {}),
    (2, 3, 'twice'): ('silent', {}),
    (3, 0, 'once'): ('silent', {}),
    (3, 0, 'per_replicate'): ('silent', {}),
    (3, 0, 'twice'): ('silent', {}),
    (3, 3, 'once'): ('silent', {}),
    (3, 3, 'per_replicate'): ('silent', {}),
    (3, 3, 'twice'): ('silent', {}),
}

PARALLEL_BASELINE = {
    (1, 0.05): ('partial', {'local_offset_run': 'partial'}),
    (0, 0.5): ('partial', {'local_offset_run': 'partial'}),
    (0, 1.0): ('partial', {'local_offset_run': 'partial'}),
}

PLANTED_STRAIGHT_BASELINE = {
    (1, 0.05): ('partial', {'local_offset_run': 'partial'}),
    (0, 0.5): ('partial', {'local_offset_run': 'partial'}),
}

MIXED_BASELINE = {
    (20, 8): ('silent', {}),
    (40, 8): ('silent', {}),
    (60, 12): ('silent', {}),
}

DECAY_BASELINE = {
    (24, 2.0, 0.02, 2): ('silent', {}),
    (30, 3.0, 0.002, 3): ('silent', {}),
}

PLANTED_MONOTONE_BASELINE = {
    ('sorted', 2, 'ratio'): ('partial', {'local_ratio_run': 'partial'}),
    ('sorted', 2, 'offset'): ('partial', {'local_offset_run': 'partial'}),
    ('smooth', 2, 'ratio'): ('partial', {'local_ratio_run': 'partial'}),
    ('smooth', 2, 'offset'): ('partial', {'local_offset_run': 'partial'}),
    ('smooth', 3, 'ratio'): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('smooth', 3, 'offset'): ('pervasive', {'local_offset_run': 'pervasive'}),
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
        "MONOTONE_BASELINE": {m: _measure_monotone(*m) for m in MONOTONE},
        "DOSE_BASELINE": {d: _measure_doses(*d) for d in DOSES},
        "PARALLEL_BASELINE": {p: _measure_parallel(*p) for p in PARALLEL},
        "MIXED_BASELINE": {m: _measure_mixed(*m) for m in MIXED},
        "DECAY_BASELINE": {d: _measure_decays(*d) for d in DECAYS},
        "KNOWN_COST_BASELINE": {(d, s): _measure_known_cost(d, s) for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_COLUMNS_BASELINE": {(r, d, s): _measure_planted_columns(r, d, s)
                                     for r in ("ratio", "offset") for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_ROWS_BASELINE": {(r, d, s): _measure_planted_rows(r, d, s)
                                  for r in ("ratio", "offset") for d in CV._DECIMALS for s in SEGMENTS},
        "PLANTED_MONOTONE_BASELINE": {m: _measure_planted_monotone(*m) for m in PLANTED_MONOTONE},
        "PLANTED_STRAIGHT_BASELINE": {p: _measure_planted_straight(*p) for p in PLANTED_STRAIGHT},
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


@pytest.mark.parametrize("stratum", sorted(MONOTONE_BASELINE))
def test_independent_monotone_lines_hold_their_frozen_verdict(stratum):
    _check(MONOTONE_BASELINE, _measure_monotone, stratum, "local runs on two independent monotone lines")


@pytest.mark.parametrize("stratum", sorted(DOSE_BASELINE))
def test_dilution_series_hold_their_frozen_verdict(stratum):
    _check(DOSE_BASELINE, _measure_doses, stratum, "local runs on two dilution series")


@pytest.mark.parametrize("stratum", sorted(PARALLEL_BASELINE))
def test_parallel_then_diverging_trends_hold_their_frozen_verdict(stratum):
    _check(PARALLEL_BASELINE, _measure_parallel, stratum, "local runs on parallel, then diverging, trends")


@pytest.mark.parametrize("stratum", sorted(PLANTED_STRAIGHT_BASELINE))
def test_stretches_planted_in_straight_lines_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_STRAIGHT_BASELINE, _measure_planted_straight, stratum, "recall of a stretch planted in a straight line")


@pytest.mark.parametrize("stratum", sorted(MIXED_BASELINE))
def test_lines_changing_precision_hold_their_frozen_verdict(stratum):
    _check(MIXED_BASELINE, _measure_mixed, stratum, "local runs on lines that change precision partway")


@pytest.mark.parametrize("stratum", sorted(DECAY_BASELINE))
def test_proportional_decays_hold_their_frozen_verdict(stratum):
    _check(DECAY_BASELINE, _measure_decays, stratum, "local runs on two proportional decays")


@pytest.mark.parametrize("stratum", sorted(PLANTED_MONOTONE_BASELINE))
def test_stretches_planted_in_monotone_lines_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_MONOTONE_BASELINE, _measure_planted_monotone, stratum, "recall of a stretch planted in a monotone line")


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
    assert set(MONOTONE_BASELINE) == set(MONOTONE)
    assert set(DOSE_BASELINE) == set(DOSES)
    assert set(PARALLEL_BASELINE) == set(PARALLEL)
    assert set(PLANTED_STRAIGHT_BASELINE) == set(PLANTED_STRAIGHT)
    assert set(MIXED_BASELINE) == set(MIXED)
    assert set(DECAY_BASELINE) == set(DECAYS)
    assert set(PLANTED_MONOTONE_BASELINE) == set(PLANTED_MONOTONE)
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
Expected: 167 passed（约 15 秒）。若有档位不同，说明实现与本计划有出入：先找出出入在哪里，确认是有意的，再用 `python -m tests.test_local_run_bench_baseline` 重新生成表并粘贴，提交信息里写明哪一层、往哪个方向变了、为什么。

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

用仓库自带的中性措辞检查（它扫描所有入库文件，词表在测试里，不要在文档里另抄一份）：

Run: `.venv/bin/python -m pytest tests/test_skill_docs.py -q -p no:cacheprovider -k neutral_language`
Expected: 汇总行无 failed。

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

另记两项（Task 5 审查要求）：

- **预算用尽**：从各篇 `scan.json` 的 coverage 限制记录里，数 `detect_local_pair_runs` 的 `detector_compute_budget_limit` 出现在多少块、多少篇，并记下这些块的行列数。按段扣预算在段远多于组的粗粒度高表上会把工作量高估许多倍，块会在比完所有线对之前停下。若这样的块数量可观，把分组拆成两步（先按区间归组，对组区间先过只看区间的三道闸，再为留下的组预付一条线长），重跑 Step 3–4。
- **已知代价形态**：首屏上不落在参考卡目标上的新类型 finding，逐条归入设计 §9 记下的形态（部分换算、先平行后分开的直线趋势、精度中途变化的线、尾部近 0 的衰减），归不进去的单列。这决定 §9 的打分问题要不要在合并前处理。

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
