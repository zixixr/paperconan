# Numeric Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact-value and deterministic-relation detectors correct across
large baselines, tiny measurements, wide integers, ragged blocks, and practical
GRIMMER cases.

**Architecture:** Add a focused numeric-policy module for ULP-aware residual
checks and integer-shift checks. Preserve unsafe integers sparsely in `Sheet`,
use exact source values for identity/equality decisions, and keep vectorized
float arrays for statistical work that is safe to approximate.

**Tech Stack:** Python 3.10+, NumPy, SciPy, pytest.

## Global Constraints

- Use only neutral statistical-signal and data-inconsistency language.
- Existing detector kinds and public function signatures remain available.
- Exact stored-value identity never uses a magnitude-relative tolerance.
- A conservative unknown result from bounded GRIMMER search is treated as
  consistent, so incomplete search cannot create a new finding.
- Every production change follows a verified red-green test cycle.
- Do not modify `recheck/` or `batches/`.

---

### Task 1: Lock Numeric Edge Cases With Failing Tests

**Files:**
- Modify: `tests/test_relations_tolerance.py`
- Modify: `tests/test_sheet.py`
- Modify: `tests/test_columnar_loader.py`
- Create: `tests/test_numeric_blocks.py`
- Modify: `tests/test_fraction_reuse.py`

**Interfaces:**
- Consumes: current `Sheet`, `detect_relations`, `detect_equal_pairs`,
  `find_numeric_blocks`, and `detect_within_sheet_fraction_reuse`.
- Produces: regression expectations used by Tasks 2-4.

- [ ] **Step 1: Add the high-baseline and tiny-transform tests**

Append to `tests/test_relations_tolerance.py`:

```python
def test_high_baseline_half_unit_difference_is_not_identical():
    x = [1_000_000_000.0 + i for i in range(6)]
    y = [v + 0.5 for v in x]
    findings = _kinds(x, y)
    assert not any(f["kind"] == "identical_column" for f in findings)
    assert any(f["kind"] == "constant_offset" for f in findings)


def test_tiny_nonzero_pure_scaling_is_detected():
    x = [1e-14, 2e-14, 3e-14, 4e-14, 5e-14, 6e-14]
    y = [2 * v for v in x]
    findings = _kinds(x, y)
    assert any(f["kind"] == "constant_ratio" for f in findings)
    assert not any(f["kind"] == "identical_column" for f in findings)


def test_high_baseline_near_values_are_not_many_equal_pairs():
    x = [1_000_000_000.0 + i for i in range(8)]
    y = [v + 0.5 for v in x]
    sheet = _sheet([x, y])
    assert detect_equal_pairs(sheet, 1, 9, 0, 2, ["x", "y"]) == []
```

- [ ] **Step 2: Add exact wide-integer tests**

Append to `tests/test_sheet.py`:

```python
def test_wide_adjacent_integers_roundtrip_without_merging():
    left = 2**53
    right = left + 1
    sheet = Sheet.from_rows([[left, right]])
    assert sheet.cell(0, 0) == left
    assert sheet.cell(0, 1) == right
    assert sheet.cell(0, 0) != sheet.cell(0, 1)
    assert left in sheet.numeric_values()
    assert right in sheet.numeric_values()


def test_wide_integer_cells_remain_numeric_in_mask():
    sheet = Sheet.from_rows([[2**53], [2**53 + 1], [2**53 + 2]])
    assert sheet.numeric_mask()[:, 0].tolist() == [True, True, True]
```

Append to `tests/test_relations_tolerance.py`:

```python
def test_adjacent_wide_integer_columns_are_not_identical_or_equal_pairs():
    x = [2**53 + i * 2 for i in range(8)]
    y = [v + 1 for v in x]
    sheet = _sheet([x, y])
    findings = detect_relations(sheet, 1, 9, 0, 2, ["x", "y"])
    assert not any(f["kind"] == "identical_column" for f in findings)
    assert any(f["kind"] == "constant_offset" for f in findings)
    assert detect_equal_pairs(sheet, 1, 9, 0, 2, ["x", "y"]) == []
```

Append to `tests/test_columnar_loader.py`:

```python
def test_streaming_loader_preserves_adjacent_wide_integers(tmp_path):
    import paperconan._audit as audit

    p = tmp_path / "wide.xlsx"
    _write_xlsx(p, [["a", "b"], [2**53, 2**53 + 1]])
    sheet = audit._load_workbook_openpyxl(str(p))["S1"]
    assert sheet.cell(1, 0) == 2**53
    assert sheet.cell(1, 1) == 2**53 + 1
    assert sheet.cell(1, 0) != sheet.cell(1, 1)
```

- [ ] **Step 3: Add the ragged-block regression**

Create `tests/test_numeric_blocks.py`:

```python
from paperconan._audit import find_numeric_blocks
from paperconan._sheet import Sheet


def test_short_seed_column_does_not_hide_neighboring_valid_block():
    sheet = Sheet.from_rows([
        [1, 10],
        [2, 11],
        [None, 12],
    ])
    assert find_numeric_blocks(sheet) == [(0, 3, 1, 2)]
```

- [ ] **Step 4: Add a high-baseline fraction-reuse rejection**

Append to `tests/test_fraction_reuse.py`:

```python
def test_b3_no_flag_when_high_baseline_differences_are_not_integers():
    a = [[1_000_000_000.125 + r * 10 + c for c in range(5)]
         for r in range(5)]
    b = [[v + 0.5 + ((r + c) % 3) * 0.125 for c, v in enumerate(row)]
         for r, row in enumerate(a)]
    findings = detect_within_sheet_fraction_reuse(
        _grid_sheets_two_blocks(a, b)
    )
    assert not any(f["kind"] == "within_table_fraction_reuse"
                   for f in findings)
```

- [ ] **Step 5: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_relations_tolerance.py \
  tests/test_sheet.py \
  tests/test_columnar_loader.py \
  tests/test_numeric_blocks.py \
  tests/test_fraction_reuse.py -q
```

Expected: failures for high-baseline identity/equality, tiny ratio, wide integer
round-trip/mask, ragged block discovery, and high-baseline fraction reuse.

- [ ] **Step 6: Commit the regression tests**

```bash
git add tests/test_relations_tolerance.py tests/test_sheet.py \
  tests/test_columnar_loader.py tests/test_numeric_blocks.py \
  tests/test_fraction_reuse.py
git commit -m "test: cover numeric precision boundaries"
```

---

### Task 2: Add Central Numeric Comparison Policies

**Files:**
- Create: `src/paperconan/_numeric.py`
- Modify: `src/paperconan/_audit.py`
- Test: `tests/test_relations_tolerance.py`
- Test: `tests/test_fraction_reuse.py`

**Interfaces:**
- Produces:
  - `ulp_tolerance(actual, expected, *, ulps=16) -> np.ndarray`
  - `relation_close(actual, expected, *, rtol=1e-10, ulps=16) -> np.ndarray`
  - `integer_shift_close(left, right, *, ulps=16) -> np.ndarray`
- Consumes: finite NumPy arrays of the same shape.

- [ ] **Step 1: Create the numeric policy module**

Create `src/paperconan/_numeric.py`:

```python
from __future__ import annotations

import numpy as np


def ulp_tolerance(actual, expected, *, ulps=16):
    actual = np.asarray(actual, dtype=float)
    expected = np.asarray(expected, dtype=float)
    spacing = np.maximum(
        np.abs(np.spacing(actual)),
        np.abs(np.spacing(expected)),
    )
    floor = np.full_like(spacing, np.finfo(float).smallest_subnormal)
    return ulps * np.maximum(spacing, floor)


def _local_variation(values):
    values = np.asarray(values, dtype=float)
    if values.size <= 1:
        return np.zeros_like(values)
    center = float(np.median(values))
    centered = np.abs(values - center)
    ordered = np.sort(values)
    steps = np.abs(np.diff(ordered))
    positive = steps[steps > 0]
    step = float(np.median(positive)) if positive.size else 0.0
    return np.maximum(centered, step)


def relation_close(actual, expected, *, rtol=1e-10, ulps=16):
    actual = np.asarray(actual, dtype=float)
    expected = np.asarray(expected, dtype=float)
    scale = np.maximum(_local_variation(actual), _local_variation(expected))
    tolerance = ulp_tolerance(actual, expected, ulps=ulps) + rtol * scale
    return np.abs(actual - expected) <= tolerance


def integer_shift_close(left, right, *, ulps=16):
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    diff = right - left
    nearest = np.rint(diff)
    arithmetic_noise = (
        ulp_tolerance(left, left, ulps=ulps)
        + ulp_tolerance(right, right, ulps=ulps)
        + ulp_tolerance(diff, nearest, ulps=ulps)
    )
    return np.abs(diff - nearest) <= arithmetic_noise
```

- [ ] **Step 2: Replace relation identity and transform checks**

In `src/paperconan/_audit.py`:

```python
from ._numeric import integer_shift_close, relation_close
```

Replace `_isclose_rowwise` with:

```python
def _isclose_rowwise(actual, expected, rtol=1e-10):
    return relation_close(actual, expected, rtol=rtol)
```

In `detect_relations`:

- perform identical-column detection with exact source values supplied by
  `Sheet` in Task 3;
- change the nonzero ratio guard from `abs(x) > 1e-12` to `x != 0`;
- use `relation_close` for constant offset, constant ratio, sum constant, and
  affine-fit residuals;
- replace `np.ptp(x) > 1e-12` with `np.ptp(x) > 0`;
- compute affine slope from the two rows with the largest x separation, then
  calculate the intercept from those points before residual validation;
- replace integer-difference relative tolerances with
  `integer_shift_close(x, y)`.

The affine candidate is:

```python
lo = int(np.argmin(x))
hi = int(np.argmax(x))
dx = x[hi] - x[lo]
if dx != 0:
    slope = (y[hi] - y[lo]) / dx
    intercept = y[lo] - slope * x[lo]
    fitted = slope * x + intercept
```

Keep `stats.linregress` only for the correlation coefficient and guard it
against `ValueError`.

- [ ] **Step 3: Tighten fraction-reuse integer shifts**

In `detect_within_sheet_fraction_reuse`, replace the per-cell
`1e-6 * magnitude` integer test with:

```python
same_fraction = bool(integer_shift_close([x], [y])[0])
if same_fraction:
    shared += 1
    rounded_diff = round(y - x)
    if abs(rounded_diff) >= 1:
        int_diffs += 1
        diffset.add(rounded_diff)
    if _sig_frac_digits(x) >= 3:
        hp += 1
        fracs.add(round(x - round(x), 6))
```

The detector must still require the existing high-precision, coverage,
nonzero-shift, distinct-shift, and distinct-fraction gates.

- [ ] **Step 4: Run focused tests and verify GREEN except wide integers**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_relations_tolerance.py \
  tests/test_fraction_reuse.py -q
```

Expected: high-baseline, tiny-scale, mixed-scale, B4, B5, and B3 tests pass.
Wide-integer tests may remain red until Task 3.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_numeric.py src/paperconan/_audit.py \
  tests/test_relations_tolerance.py tests/test_fraction_reuse.py
git commit -m "fix: separate numeric comparison semantics"
```

---

### Task 3: Preserve Wide Integers and Exact Equality

**Files:**
- Modify: `src/paperconan/_sheet.py`
- Modify: `src/paperconan/_audit.py`
- Test: `tests/test_sheet.py`
- Test: `tests/test_relations_tolerance.py`
- Test: `tests/test_columnar_loader.py`

**Interfaces:**
- `Sheet.numeric_mask() -> np.ndarray`
- `Sheet.exact_numeric(r, c) -> int | float | None`
- `Sheet.numeric_values() -> list[int | float]`
- `Sheet.__init__(nrows, ncols, numeric, text, ints, wide_ints=None)`

- [ ] **Step 1: Store wide integers sparsely**

Change `Sheet.__slots__` and constructor to:

```python
__slots__ = ("nrows", "ncols", "numeric", "_text", "_ints", "_wide_ints")

def __init__(self, nrows, ncols, numeric, text, ints, wide_ints=None):
    self.nrows = nrows
    self.ncols = ncols
    self.numeric = numeric
    self._text = text
    self._ints = ints
    self._wide_ints = wide_ints or {}
```

Define:

```python
_MAX_EXACT_FLOAT_INT = 2**53
```

During `from_rows`, initialize `wide_ints = {}` and replace the numeric branch
with:

```python
if _is_num(v):
    if isinstance(v, int) and abs(v) > _MAX_EXACT_FLOAT_INT:
        wide_ints[(r, c)] = v
    else:
        numeric[r, c] = float(v)
        if isinstance(v, int):
            ints.add((r, c))
```

Return `cls(nrows, ncols, numeric, text, ints, wide_ints)`.

Add:

```python
def numeric_mask(self):
    mask = ~np.isnan(self.numeric)
    for r, c in self._wide_ints:
        mask[r, c] = True
    return mask


def exact_numeric(self, r, c):
    if (r, c) in self._wide_ints:
        return self._wide_ints[(r, c)]
    value = self.cell(r, c)
    return value if _is_num(value) else None


def numeric_values(self):
    values = []
    for r in range(self.nrows):
        for c in range(self.ncols):
            value = self.exact_numeric(r, c)
            if value is not None:
                values.append(value)
    return values
```

Update `cell()` to return `_wide_ints[(r, c)]` before inspecting the dense
array; the row-major `numeric_values()` implementation above replaces the old
dense-only flattening.

In `_fill_sheet_from_rows`, initialize `wide_ints = {}`, apply the same
wide-integer branch before assigning to the dense array, filter `wide_ints`
alongside `_text` and `_ints`, and construct:

```python
return Sheet(
    numeric.shape[0],
    numeric.shape[1],
    numeric,
    text,
    ints,
    wide_ints,
), cells
```

- [ ] **Step 2: Make block discovery use the numeric mask**

In `find_numeric_blocks`, replace:

```python
num = ~np.isnan(sheet.numeric)
```

with:

```python
num = sheet.numeric_mask()
```

Mark `visited` only after the candidate passes `min_rows` and `min_cols`:

```python
if (i1 - i0) >= min_rows and (j1 - j) >= min_cols:
    visited[i0:i1, j:j1] = True
    blocks.append((i0, i1, j, j1))
```

- [ ] **Step 3: Add exact relation extraction**

Add to `_audit.py`:

```python
def _numeric_pairs(sheet, r0, r1, ca, cb):
    pairs = []
    for row in range(r0, r1):
        left = sheet.exact_numeric(row, ca)
        right = sheet.exact_numeric(row, cb)
        if left is not None and right is not None:
            pairs.append((row, left, right))
    return pairs


def _sample_exact(values, k=8):
    out = []
    for value in values[:k]:
        if isinstance(value, int):
            out.append(value)
        else:
            out.append(round(float(value), 6))
    return out
```

In `detect_relations`, use these pairs before float conversion:

```python
pairs = _numeric_pairs(sheet, r0, r1, ci, cj)
if len(pairs) < 4:
    continue
exact_x = [p[1] for p in pairs]
exact_y = [p[2] for p in pairs]
if all(a == b for a, b in zip(exact_x, exact_y)):
    findings.append(dict(
        kind="identical_column",
        col_a=header[ci - c0],
        col_b=header[cj - c0],
        col_a_idx=ci,
        col_b_idx=cj,
        n=len(pairs),
        severity="high",
        col_a_sample=_sample_exact(exact_x),
        col_b_sample=_sample_exact(exact_y),
        rule=f"col[{cj}] == col[{ci}]",
    ))
    continue
```

When every pair is integer-valued, check constant offset exactly:

```python
if all(isinstance(v, int) for v in exact_x + exact_y):
    offsets = {b - a for a, b in zip(exact_x, exact_y)}
    if len(offsets) == 1 and next(iter(offsets)) != 0:
        offset = next(iter(offsets))
        findings.append(dict(
            kind="constant_offset",
            col_a=header[ci - c0],
            col_b=header[cj - c0],
            col_a_idx=ci,
            col_b_idx=cj,
            n=len(pairs),
            offset=offset,
            severity="high",
            col_a_sample=_sample_exact(exact_x),
            col_b_sample=_sample_exact(exact_y),
            rule=f"col[{cj}] = col[{ci}] + {offset}",
        ))
        continue
```

Only rows whose values are representable in the dense float arrays participate
in remaining float-based transform checks.

- [ ] **Step 4: Make many-equal-pairs exact**

Rewrite `detect_equal_pairs` to count source-value equality through
`Sheet.exact_numeric`:

```python
pairs = _numeric_pairs(sheet, r0, r1, c0 + i, c0 + j)
n = len(pairs)
equal_rows = [row for row, left, right in pairs if left == right]
eq = len(equal_rows)
all_equal = eq == n
```

Preserve existing sample, severity, and minimum-count behavior. Do not emit
`many_equal_pairs` when every row is equal because `identical_column` owns that
case.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_sheet.py \
  tests/test_numeric_blocks.py \
  tests/test_relations_tolerance.py \
  tests/test_columnar_accessors.py \
  tests/test_columnar_loader.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_sheet.py src/paperconan/_audit.py \
  tests/test_sheet.py tests/test_numeric_blocks.py \
  tests/test_relations_tolerance.py
git commit -m "fix: preserve exact wide integers"
```

---

### Task 4: Add Exact Practical GRIMMER Feasibility

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_grim.py`

**Interfaces:**
- `_integer_moments_reachable(total, sum_squares, n, *, max_states=200000)
  -> bool | None`
- `_candidate_integer_totals(mean, n, decimals) -> list[int]`
- `_candidate_sum_squares(total, sd, n, decimals, ddof) -> list[int]`
- Existing
  `grimmer_consistent(mean, sd, n, mean_decimals, sd_decimals) -> bool`
  remains public.

- [ ] **Step 1: Add failing feasibility tests**

Append to `tests/test_grim.py`:

```python
def test_grimmer_rejects_parity_only_false_consistency():
    assert grimmer_consistent(0.5, 1.12, 2, 1, 2) is False


def test_grimmer_two_value_closed_form():
    assert grimmer_consistent(0.5, 0.71, 2, 1, 2) is True
    assert grimmer_consistent(0.5, 0.50, 2, 1, 2) is True


def test_grimmer_stays_conservative_when_search_budget_is_exceeded(monkeypatch):
    import paperconan._audit as audit

    assert audit._integer_moments_reachable(
        total=3,
        sum_squares=101,
        n=5,
        max_states=1,
    ) is None
    monkeypatch.setattr(audit, "_GRIMMER_MAX_STATES", 1)
    assert audit.grimmer_consistent(0.6, 4.6, 5, 1, 1) is True
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_grim.py::test_grimmer_rejects_parity_only_false_consistency \
  tests/test_grim.py::test_grimmer_two_value_closed_form \
  tests/test_grim.py::test_grimmer_stays_conservative_when_search_budget_is_exceeded -q
```

Expected: at least the parity-only case fails.

- [ ] **Step 3: Implement bounded exact moment search**

Add:

```python
_GRIMMER_MAX_STATES = 200_000


def _candidate_integer_totals(mean, n, decimals):
    scale = 10 ** decimals
    target = round(mean * scale)
    lo = (target - 0.5) / scale
    hi = (target + 0.5) / scale
    return [
        total for total in range(math.floor(lo * n) - 1,
                                 math.ceil(hi * n) + 2)
        if round((total / n) * scale) == target
    ]


def _candidate_sum_squares(total, sd, n, decimals, ddof):
    denom = n - ddof
    if denom <= 0:
        return []
    scale = 10 ** decimals
    target = round(sd * scale)
    lo_sd = max(0.0, (target - 0.5) / scale)
    hi_sd = (target + 0.5) / scale
    correction = (total * total) / n
    first = max(0, math.floor(lo_sd * lo_sd * denom + correction) - 2)
    last = math.ceil(hi_sd * hi_sd * denom + correction) + 2
    out = []
    for sum_squares in range(first, last + 1):
        variance = (sum_squares - correction) / denom
        if variance < -1e-12:
            continue
        candidate_sd = math.sqrt(max(0.0, variance))
        if round(candidate_sd * scale) == target:
            out.append(sum_squares)
    return out


def _integer_moments_reachable(total, sum_squares, n, *,
                               max_states=None):
    if max_states is None:
        max_states = _GRIMMER_MAX_STATES
    if n <= 0 or sum_squares < 0:
        return False
    if total * total > n * sum_squares:
        return False
    if (sum_squares - total) % 2:
        return False

    base = math.floor(total / n)
    shifted_sum = total - n * base
    shifted_squares = (
        sum_squares - 2 * base * total + n * base * base
    )
    if shifted_squares < 0:
        return False

    if n == 1:
        return shifted_squares == shifted_sum * shifted_sum
    if n == 2:
        discriminant = 2 * shifted_squares - shifted_sum * shifted_sum
        if discriminant < 0:
            return False
        root = math.isqrt(discriminant)
        return (
            root * root == discriminant
            and (shifted_sum + root) % 2 == 0
        )

    radius = math.isqrt(shifted_squares)
    values = range(-radius, radius + 1)
    states = {(0, 0)}
    for used in range(n):
        remaining = n - used - 1
        next_states = set()
        for partial_sum, partial_sq in states:
            for value in values:
                new_sum = partial_sum + value
                new_sq = partial_sq + value * value
                if new_sq > shifted_squares:
                    continue
                sum_left = shifted_sum - new_sum
                sq_left = shifted_squares - new_sq
                if remaining == 0:
                    if sum_left == 0 and sq_left == 0:
                        return True
                    continue
                if sum_left * sum_left > remaining * sq_left:
                    continue
                next_states.add((new_sum, new_sq))
                if len(next_states) > max_states:
                    return None
        states = next_states
        if not states:
            return False
    return False
```

Replace the existing parity-only body with:

```python
def grimmer_consistent(mean, sd, n, mean_decimals, sd_decimals):
    if n <= 1 or sd < 0:
        return True
    unknown = False
    for total in _candidate_integer_totals(mean, n, mean_decimals):
        for ddof in (1, 0):
            for sum_squares in _candidate_sum_squares(
                total, sd, n, sd_decimals, ddof
            ):
                reachable = _integer_moments_reachable(
                    total, sum_squares, n
                )
                if reachable is True:
                    return True
                if reachable is None:
                    unknown = True
    return True if unknown else False
```

- [ ] **Step 4: Run brute-force oracle tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_grim.py -q
```

Expected: all pass, including every achievable brute-force sample.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_grim.py
git commit -m "fix: strengthen integer SD feasibility checks"
```

---

### Task 5: Evaluate Every GRIM Header Group

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_grim.py`
- Modify: `tests/test_grim_e2e.py`

**Interfaces:**
- `_grim_column_groups(header) -> list[tuple[int, int, int | None]]`
- `detect_grim_grimmer` emits findings independently for each matched group.

- [ ] **Step 1: Add multi-group detector tests**

Append to `tests/test_grim.py`:

```python
def test_detector_checks_multiple_labeled_mean_groups():
    rows = [
        ["group", "score mean A", "sd A", "n A",
         "score mean B", "sd B", "n B"],
        ["x", 3.40, 1.0, 10, 2.25, 1.0, 10],
        ["y", 3.45, 1.0, 10, 2.20, 1.0, 10],
    ]
    findings = detect_grim_grimmer(*_block(rows))
    mean_columns = {f["mean_col"] for f in findings}
    assert "score mean A" in mean_columns
    assert "score mean B" in mean_columns


def test_detector_may_share_one_global_n_column():
    rows = [
        ["group", "score mean A", "sd A",
         "score mean B", "sd B", "n"],
        ["x", 3.45, 1.0, 2.25, 1.0, 10],
    ]
    findings = detect_grim_grimmer(*_block(rows))
    assert {f["n_col"] for f in findings} == {"n"}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_grim.py::test_detector_checks_multiple_labeled_mean_groups \
  tests/test_grim.py::test_detector_may_share_one_global_n_column -q
```

Expected: current first-match implementation misses the later group.

- [ ] **Step 3: Implement header-role grouping**

Add:

```python
_GRIM_SE_RE = re.compile(r"\b(?:sem|s\.?e\.?|standard error)\b", re.I)
_GRIM_ROLE_WORDS = {
    "mean", "average", "avg", "sd", "std", "stdev",
    "n", "sample", "size",
}


def _grim_role_tokens(label):
    words = re.findall(r"[a-z0-9]+", str(label or "").lower())
    return {word for word in words if word not in _GRIM_ROLE_WORDS}


def _grim_best_partner(mean_i, candidates, header):
    if not candidates:
        return None
    mean_tokens = _grim_role_tokens(header[mean_i])
    ranked = sorted(
        candidates,
        key=lambda idx: (
            -len(mean_tokens & _grim_role_tokens(header[idx])),
            abs(idx - mean_i),
            idx,
        ),
    )
    best = ranked[0]
    overlap = len(mean_tokens & _grim_role_tokens(header[best]))
    if overlap == 0 and len(candidates) == 1:
        return candidates[0]
    return best


def _grim_column_groups(header):
    mean_cols = [
        i for i, value in enumerate(header)
        if _GRIM_MEAN_RE.search(str(value or ""))
        and _GRIM_INT_RE.search(str(value or ""))
        and not _GRIM_RATIO_RE.search(str(value or ""))
    ]
    sd_cols = [
        i for i, value in enumerate(header)
        if _GRIM_SD_RE.search(str(value or ""))
        and not _GRIM_SE_RE.search(str(value or ""))
        and i not in mean_cols
    ]
    n_cols = [
        i for i, value in enumerate(header)
        if _GRIM_N_RE.search(str(value or ""))
        and i not in mean_cols
        and i not in sd_cols
    ]
    groups = []
    for mean_i in mean_cols:
        n_i = _grim_best_partner(mean_i, n_cols, header)
        if n_i is None:
            continue
        sd_i = _grim_best_partner(mean_i, sd_cols, header)
        groups.append((mean_i, n_i, sd_i))
    return groups
```

The public detector loops over `_grim_column_groups(header)` and runs the
existing row checks once per group. Findings keep their group-specific column
names and indices.

- [ ] **Step 4: Run GRIM unit and end-to-end tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_grim.py tests/test_grim_e2e.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_grim.py tests/test_grim_e2e.py
git commit -m "fix: inspect every integer-summary column group"
```

---

### Task 6: Numeric Regression Gate

**Files:**
- Modify only if a regression is found in numeric code or its tests.

- [ ] **Step 1: Run all detector-focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_relations_tolerance.py \
  tests/test_fraction_reuse.py \
  tests/test_grim.py \
  tests/test_grim_e2e.py \
  tests/test_numeric_blocks.py \
  tests/test_sheet.py \
  tests/test_collisions.py \
  tests/test_decimal_tail_gate.py \
  tests/test_fp_complement_and_tweak.py \
  tests/test_row_pair_digit_coupling.py -q
```

Expected: all pass.

- [ ] **Step 2: Run the complete suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with only the intentional live-network skip.

- [ ] **Step 3: Re-run direct reproductions**

Use a temporary-directory smoke script to assert:

```python
assert "identical_column" not in high_baseline_kinds
assert "constant_ratio" in tiny_scale_kinds
assert wide_integer_cells[0] != wide_integer_cells[1]
assert ragged_blocks == [(0, 3, 1, 2)]
assert grimmer_consistent(0.5, 1.12, 2, 1, 2) is False
```

Expected: all assertions pass.
