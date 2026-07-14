# Relation Model Ambiguity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve raw high-precision affine coefficients and explicitly mark
findings when stored float values cannot distinguish a proportional model from
a nonzero-intercept affine model.

**Architecture:** Add a scalar-only intercept assessment to `_numeric.py`.
`detect_relations()` keeps its centered fit and existing candidate/resource
transaction, evaluates ratios with finite scalar division and normalized
fallback statistics, and evaluates affine compatibility with radius-scaled
scalar correlation. Finite legacy NumPy mean and standard-deviation results
remain authoritative for ordinary binary64 output; bounded reductions run
only when those results are non-finite. Affine work stops when intercept
assessment is unrepresentable. The shared relation comparator treats exact
values as equal and conservatively rejects non-exact comparisons whose
residual or tolerance range is not finite. The detector emits one ratio-first
finding with additive ambiguity metadata when both models remain compatible.
Schema, compact packet, and public output docs preserve the metadata without
changing verdict selector rules.

**Tech Stack:** Python 3.10-3.14, NumPy, SciPy, pytest, uv, setuptools.

## Global Constraints

- Work only in
  `/Users/xiaotong/Dev/paperconan/.worktrees/project-hardening`.
- Do not access the main checkout, `recheck/`, `batches/`, or private data.
- Use neutral statistical-signal and data-inconsistency language everywhere.
- Do not decimal-round a coefficient used by detection.
- Preserve `constant_ratio` and `exact_linear`; do not add a new finding kind.
- Preserve identifiable finding severity, order, evidence, and profile
  behavior.
- Add ambiguity fields only when both models are compatible.
- Do not increment `schema_version`.
- Preserve archived `scan.json` and legacy/current verdict readability.
- Preserve deterministic output for identical input.
- Keep direct detector calls and budgeted detector calls behaviorally equal.
- Do not allocate new row-proportional state for this change.
- Keep Python 3.10-3.14 support.
- Do not add a public environment variable.
- Use strict RED/GREEN TDD for every production change.
- Do not commit `.superpowers/`, generated archives, caches, or source data.

## Pre-Execution Gate

The approved specification, this plan, and their sorted `MANIFEST.in` entries
must be committed before Task 1.

Run:

```bash
git status --short
.venv/bin/python -m pytest -q \
  tests/test_packaging.py::test_sdist_allowlist_matches_tracked_public_files
```

Expected: clean tracked status and `1 passed`.

## File Structure

**Modify**

- `src/paperconan/_numeric.py`: scalar intercept uncertainty and model-state
  assessment.
- `src/paperconan/_audit.py`: raw centered coefficient use, independent ratio
  compatibility, ambiguity metadata, and ratio-first deduplication.
- `src/paperconan/schema.py`: additive finding-field documentation.
- `src/paperconan/packet.py`: preserve ambiguity fields in compact review
  findings.
- `tests/test_relations_tolerance.py`: numeric boundaries, coefficient
  fidelity, identifiable and ambiguous relation behavior.
- `tests/test_resource_lifetime.py`: direct/budgeted parity and unchanged
  resource contract for an ambiguous relation.
- `tests/test_packet.py`: compact packet propagation and absence on
  identifiable findings.
- `skills/paperconan/references/output-schema.md`: public meaning and
  scan/verdict compatibility.
- `tests/test_skill_docs.py`: governance check for the documented fields.
- `.superpowers/sdd/final-review-fix-wave-6-report.md`: ignored local RED/GREEN
  and verification record; never commit.
- `.superpowers/sdd/progress.md`: ignored local progress record; never commit.

---

### Task 1: Add Scalar Intercept Model Assessment

**Files:**

- Modify: `src/paperconan/_numeric.py`
- Test: `tests/test_relations_tolerance.py`

**Interfaces:**

- Produces:
  - `RelationInterceptAssessment`
  - `assess_relation_intercept(*, slope, intercept, x_center,
    centered_radius, centered_residual, transformed_span, anchor_y,
    intercept_product) -> RelationInterceptAssessment | None`
- `RelationInterceptAssessment.state` is one of `"proportional"`,
  `"affine"`, or `"ambiguous"`.
- Invalid or degenerate scalar inputs return `None`.

- [ ] **Step 1: Write failing assessment tests**

Update the `_numeric` import in `tests/test_relations_tolerance.py`:

```python
from paperconan._numeric import (
    assess_relation_intercept,
    integer_shift_close,
)
```

Add:

```python
def _assess_intercept(**overrides):
    values = {
        "slope": 2.0,
        "intercept": 0.0,
        "x_center": 0.0,
        "centered_radius": 1.0,
        "centered_residual": 0.0,
        "transformed_span": 1.0,
        "anchor_y": 0.0,
        "intercept_product": 0.0,
    }
    values.update(overrides)
    return assess_relation_intercept(**values)


def test_relation_intercept_assessment_distinguishes_three_states():
    proportional = _assess_intercept()
    affine = _assess_intercept(intercept=1e-6, anchor_y=1e-6)
    ambiguous = _assess_intercept(
        x_center=100.0,
        centered_residual=1e-8,
    )

    assert proportional is not None
    assert proportional.state == "proportional"
    assert proportional.uncertainty <= proportional.zero_tolerance

    assert affine is not None
    assert affine.state == "affine"
    assert affine.intercept_lower > affine.zero_tolerance

    assert ambiguous is not None
    assert ambiguous.state == "ambiguous"
    assert ambiguous.intercept_lower < ambiguous.zero_tolerance
    assert ambiguous.intercept_upper > -ambiguous.zero_tolerance


def test_relation_intercept_assessment_rejects_degenerate_inputs():
    assert _assess_intercept(centered_radius=0.0) is None
    assert _assess_intercept(centered_residual=float("inf")) is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  -k 'relation_intercept_assessment'
```

Expected: collection fails because `assess_relation_intercept` does not exist.

- [ ] **Step 3: Implement the scalar assessment**

Add these imports to `src/paperconan/_numeric.py`:

```python
from typing import Literal, NamedTuple
```

Add after `scalar_ulp_tolerance()`:

```python
class RelationInterceptAssessment(NamedTuple):
    state: Literal["proportional", "affine", "ambiguous"]
    zero_tolerance: float
    uncertainty: float
    intercept_lower: float
    intercept_upper: float


def assess_relation_intercept(
    *,
    slope: float,
    intercept: float,
    x_center: float,
    centered_radius: float,
    centered_residual: float,
    transformed_span: float,
    anchor_y: float,
    intercept_product: float,
) -> RelationInterceptAssessment | None:
    scalars = (
        slope,
        intercept,
        x_center,
        centered_radius,
        centered_residual,
        transformed_span,
        anchor_y,
        intercept_product,
    )
    if (
        centered_radius <= 0
        or centered_residual < 0
        or transformed_span < 0
        or not all(math.isfinite(value) for value in scalars)
    ):
        return None

    propagated_roundoff = (
        scalar_ulp_tolerance(anchor_y, intercept_product)
        + abs(x_center) * scalar_ulp_tolerance(slope)
    )
    zero_tolerance = (
        propagated_roundoff
        + 1e-9
        * max(
            transformed_span,
            np.finfo(float).smallest_subnormal,
        )
    )
    uncertainty = (
        propagated_roundoff
        + centered_residual
        + abs(x_center)
        * centered_residual
        / centered_radius
    )
    intercept_lower = intercept - uncertainty
    intercept_upper = intercept + uncertainty

    if (
        intercept_lower >= -zero_tolerance
        and intercept_upper <= zero_tolerance
    ):
        state = "proportional"
    elif (
        intercept_lower > zero_tolerance
        or intercept_upper < -zero_tolerance
    ):
        state = "affine"
    else:
        state = "ambiguous"

    return RelationInterceptAssessment(
        state=state,
        zero_tolerance=zero_tolerance,
        uncertainty=uncertainty,
        intercept_lower=intercept_lower,
        intercept_upper=intercept_upper,
    )
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  -k 'relation_intercept_assessment'
```

Expected: `2 passed`.

- [ ] **Step 5: Run the complete numeric helper file**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_numeric.py tests/test_relations_tolerance.py
git commit -m "fix: assess relation intercept uncertainty"
```

---

### Task 2: Integrate Raw Coefficients and Ambiguous Relation Output

**Files:**

- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_relations_tolerance.py`
- Modify: `tests/test_resource_lifetime.py`

**Interfaces:**

- Consumes:
  - `assess_relation_intercept(...)`
  - `RelationInterceptAssessment.state`
- Produces ambiguous relation fields:
  - `relation_model_ambiguous: true`
  - `relation_model_alternatives:
    ["constant_ratio", "exact_linear"]`
- Preserves one primary finding with ratio-first precedence.

- [ ] **Step 1: Write failing coefficient and ambiguity tests**

In `tests/test_relations_tolerance.py`, add:

```python
HIGH_PRECISION_SLOPE = 1.2345678901
RELATION_X = [
    467.61905, 453.14286, 404.38095, 364.0,
    598.66667, 538.47619, 532.38095, 510.28571,
    544.57143, 375.42857, 619.2381, 715.2381,
]


def _primary_linear_relation(findings):
    return [
        finding
        for finding in findings
        if finding["kind"] in {"constant_ratio", "exact_linear"}
    ]


def test_high_precision_affine_coefficient_is_not_decimal_canonicalized():
    y = [
        HIGH_PRECISION_SLOPE * value + 0.25
        for value in RELATION_X
    ]
    relations = _primary_linear_relation(_kinds(RELATION_X, y))

    assert len(relations) == 1
    finding = relations[0]
    assert finding["kind"] == "exact_linear"
    assert abs(finding["slope"] - HIGH_PRECISION_SLOPE) < 1e-12
    assert finding["slope"] != 1.23456789
    assert abs(finding["intercept"] - 0.25) < 1e-9
    assert "1.235" in finding["rule"]
    assert "relation_model_ambiguous" not in finding


@pytest.mark.parametrize("source_intercept", [0.0, 0.25])
def test_translated_high_precision_relation_exposes_model_ambiguity(
    source_intercept,
):
    x = [value + 1e9 for value in RELATION_X]
    y = [
        HIGH_PRECISION_SLOPE * value
        + source_intercept
        + HIGH_PRECISION_SLOPE * 1e9
        for value in RELATION_X
    ]
    relations = _primary_linear_relation(_kinds(x, y))

    assert len(relations) == 1
    finding = relations[0]
    assert finding["kind"] == "constant_ratio"
    assert finding["relation_model_ambiguous"] is True
    assert finding["relation_model_alternatives"] == [
        "constant_ratio",
        "exact_linear",
    ]
```

Add `import pytest` at the top of the test file.

Replace
`test_nonbinary_affine_classification_is_inverse_translation_invariant`
with:

```python
def test_nonbinary_affine_translation_exposes_model_ambiguity():
    x = RELATION_X
    y = [2.39 * value + 0.25 for value in x]

    ordinary = _primary_linear_relation(_kinds(x, y))
    translated = _primary_linear_relation(_kinds(
        [value + 1e9 for value in x],
        [value + 2.39e9 for value in y],
    ))

    assert [finding["kind"] for finding in ordinary] == [
        "exact_linear"
    ]
    assert "relation_model_ambiguous" not in ordinary[0]
    assert [finding["kind"] for finding in translated] == [
        "constant_ratio"
    ]
    assert translated[0]["relation_model_ambiguous"] is True
```

Extend `test_affine_linear_classification_is_translation_invariant` so both
binary-slope findings also assert:

```python
assert "relation_model_ambiguous" not in finding
```

In `tests/test_resource_lifetime.py`, add:

```python
def test_relation_model_ambiguity_preserves_dense_resource_contract():
    slope = 1.2345678901
    source = [
        467.61905, 453.14286, 404.38095, 364.0,
        598.66667, 538.47619, 532.38095, 510.28571,
        544.57143, 375.42857, 619.2381, 715.2381,
    ]
    rows = [
        [value + 1e9, slope * value + 0.25 + slope * 1e9]
        for value in source
    ]
    sheet = Sheet.from_rows([["left", "right"], *rows])
    baseline = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )

    instrumented = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert instrumented == baseline
    relation = next(
        finding for finding in instrumented
        if finding["kind"] in {"constant_ratio", "exact_linear"}
    )
    assert relation["relation_model_ambiguous"] is True
    assert result.candidates_examined == 1
    assert result.work_examined == 2 * len(rows)
    assert resources.state.live_units == 0
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  tests/test_resource_lifetime.py \
  -k 'high_precision_affine_coefficient or translated_high_precision_relation or nonbinary_affine_translation or relation_model_ambiguity_preserves'
```

Expected: failures show the coefficient is shortened and ambiguity fields are
absent.

- [ ] **Step 3: Import the scalar assessment**

Change the `_numeric` import in `src/paperconan/_audit.py` to include:

```python
from ._numeric import (
    assess_relation_intercept,
    integer_shift_close,
    relation_close,
    scalar_ulp_tolerance,
)
```

- [ ] **Step 4: Replace slope canonicalization with raw assessment**

In the varying-`x` block of `detect_relations()`, keep the centered fit and
replace the decimal simplification and old intercept Boolean with:

```python
slope = centered_xy / centered_xx
centered_radius = 0.0
centered_residual = 0.0
intercept_x = float(x[0])
intercept_y = float(y[0])
y_min = intercept_y
y_max = intercept_y
for x_value, y_value in zip(x, y):
    x_scalar = float(x_value)
    y_scalar = float(y_value)
    centered_x = x_scalar - x_center
    centered_y = y_scalar - y_center
    centered_radius = max(centered_radius, abs(centered_x))
    centered_residual = max(
        centered_residual,
        abs(centered_y - slope * centered_x),
    )
    y_min = min(y_min, y_scalar)
    y_max = max(y_max, y_scalar)
    if abs(x_scalar) < abs(intercept_x):
        intercept_x = x_scalar
        intercept_y = y_scalar

intercept_product = slope * intercept_x
intercept = intercept_y - intercept_product
intercept_assessment = assess_relation_intercept(
    slope=slope,
    intercept=intercept,
    x_center=x_center,
    centered_radius=centered_radius,
    centered_residual=centered_residual,
    transformed_span=max(
        abs(slope * float(dx)),
        abs(y_max - y_min),
    ),
    anchor_y=intercept_y,
    intercept_product=intercept_product,
)
relation_intercept_state = (
    None
    if intercept_assessment is None
    else intercept_assessment.state
)
```

For non-varying `x`, set:

```python
slope = 0.0
intercept = 0.0
relation_intercept_state = None
```

Delete `slope_tolerance`, the significant-digit loop, and
`intercept_is_zero`.

- [ ] **Step 5: Evaluate the ratio predicate independently**

Keep the existing `ratio` lease, but allocate an empty buffer and fill it with
Python scalar `float(y_value) / float(x_value)` operations. Reject any
non-finite result before ratio statistics. Preserve finite NumPy `mean` and
population `std` results exactly under a local floating-point warning scope.
Only when either legacy reduction is non-finite, compute the corresponding
bounded fallback after dividing each finite ratio by the maximum absolute
ratio. Validate all fallback results before classification. Reuse the leased
ratio buffer for fitted proportional values, filling it with checked scalar
multiplication before calling `relation_close()`.

Do not offer the ratio finding yet. Preserve the scalar `mean_ratio` and
`ratio_compatible` values until the fitted-line predicate has run. This keeps
the existing ratio finding position without claiming ambiguity before the
standalone `exact_linear` predicate is available and compatible.

- [ ] **Step 6: Evaluate affine compatibility before selection**

Run the fitted-line predicate only for `n >= 5`, a valid centered fit, and a
non-`None` intercept assessment, preserving the standalone `exact_linear`
minimum-sample contract. Compute Pearson correlation from centered values
scaled by their finite radii instead of calling `scipy.stats.linregress`.
Preserve the existing linear-fit and fitted-array leases; fill fitted values
with checked scalar operations and conservatively reject non-finite results.
Record compatibility before offering either primary relation:

```python
affine_compatible = (
    fitted_close
    and abs(r) > 0.99
)
ratio_selected = (
    ratio_compatible
    and (
        relation_intercept_state == "proportional"
        or (
            relation_intercept_state == "ambiguous"
            and affine_compatible
        )
    )
)
if ratio_selected:
    relation_model_ambiguous = (
        relation_intercept_state == "ambiguous"
    )
    candidate.offer(
        "high",
        lambda: {
            "kind": "constant_ratio",
            **(
                {
                    "relation_model_ambiguous": True,
                    "relation_model_alternatives": [
                        "constant_ratio",
                        "exact_linear",
                    ],
                }
                if relation_model_ambiguous
                else {}
            ),
            # Existing columns, samples, severity, ratio, and rule.
        },
    )
    ratio_emitted = True

if affine_compatible:
    is_identity = (
        abs(slope - 1) < 1e-9
        and relation_intercept_state == "proportional"
    )
    redundant_scaling = ratio_emitted
    if not (is_identity or redundant_scaling):
        candidate.offer(
            "high",
            lambda ci=ci, cj=cj, n=n,
            slope=slope,
            intercept=intercept,
            x_sample=x_sample,
            y_sample=y_sample: dict(
                kind="exact_linear",
                col_a=header[ci - c0],
                col_b=header[cj - c0],
                col_a_idx=ci,
                col_b_idx=cj,
                n=n,
                slope=float(slope),
                intercept=float(intercept),
                severity="high",
                col_a_sample=list(x_sample),
                col_b_sample=list(y_sample),
                rule=(
                    f"col[{cj}] = "
                    f"{slope:.4g} * "
                    f"col[{ci}] + "
                    f"{intercept:.4g}"
                ),
            ),
        )
```

Do not add ambiguity metadata to the zero-containing fallback because the
ratio predicate did not run.

- [ ] **Step 7: Run focused GREEN tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  tests/test_resource_lifetime.py \
  -k 'relation_intercept_assessment or pure_scaling or affine_linear or high_precision_affine_coefficient or translated_high_precision_relation or nonbinary_affine_translation or ratio_emitted_flag or relation_model_ambiguity_preserves'
```

Expected: all selected tests pass.

- [ ] **Step 8: Run affected detector suites**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  tests/test_profiles.py \
  tests/test_detector_coverage.py \
  tests/test_resource_lifetime.py
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add \
  src/paperconan/_audit.py \
  tests/test_relations_tolerance.py \
  tests/test_resource_lifetime.py
git commit -m "fix: preserve ambiguous relation models"
```

---

### Task 3: Preserve Ambiguity Metadata Through Public Boundaries

**Files:**

- Modify: `src/paperconan/schema.py`
- Modify: `src/paperconan/packet.py`
- Modify: `tests/test_packet.py`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**

- Consumes relation finding fields from Task 2.
- Produces the same optional fields in `Finding` and compact review findings.
- Existing findings omit both fields.
- Verdict selectors continue to use their existing fields.

- [ ] **Step 1: Write failing packet and documentation tests**

Add to `tests/test_packet.py`:

```python
def test_distill_relations_preserves_optional_model_ambiguity():
    scan = {
        "cross_sheet_findings": [],
        "relations_blocks": [{
            "file": "source.csv",
            "sheet": "Data",
            "relations": [
                {
                    "kind": "constant_ratio",
                    "severity": "high",
                    "col_a": "x",
                    "col_b": "y",
                    "n": 12,
                    "ratio": 1.23456789035,
                    "rule": "col[1] = col[0] * 1.23457",
                    "col_a_sample": [1.0, 2.0],
                    "col_b_sample": [1.2, 2.4],
                    "relation_model_ambiguous": True,
                    "relation_model_alternatives": [
                        "constant_ratio",
                        "exact_linear",
                    ],
                },
                {
                    "kind": "exact_linear",
                    "severity": "high",
                    "col_a": "a",
                    "col_b": "b",
                    "n": 12,
                    "slope": 2.0,
                    "intercept": 0.25,
                    "rule": "col[3] = 2 * col[2] + 0.25",
                    "col_a_sample": [1.0, 2.0],
                    "col_b_sample": [2.25, 4.25],
                },
            ],
            "equal_pairs": [],
            "within_col": [],
        }],
    }

    findings = distill_findings_for_review(scan)

    assert findings[0]["relation_model_ambiguous"] is True
    assert findings[0]["relation_model_alternatives"] == [
        "constant_ratio",
        "exact_linear",
    ]
    assert "relation_model_ambiguous" not in findings[1]
    assert "relation_model_alternatives" not in findings[1]
```

Add to `tests/test_skill_docs.py`:

```python
def test_output_schema_documents_relation_model_ambiguity():
    text = (REF_DIR / "output-schema.md").read_text(
        encoding="utf-8"
    )
    assert "relation_model_ambiguous" in text
    assert "relation_model_alternatives" in text
    assert "constant_ratio" in text
    assert "exact_linear" in text
    assert "schema_version" in text
    assert "finding_ref" in text
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_packet.py::test_distill_relations_preserves_optional_model_ambiguity \
  tests/test_skill_docs.py::test_output_schema_documents_relation_model_ambiguity
```

Expected: packet fields are absent and the output schema does not document
them.

- [ ] **Step 3: Add optional schema fields**

Add to `schema.Finding`:

```python
relation_model_ambiguous: bool
relation_model_alternatives: list[str]
```

- [ ] **Step 4: Preserve only present packet metadata**

In `_distill_relations()` replace the direct append body with:

```python
relation_extra = {
    "sheet": block.get("sheet"),
    "file": block.get("file"),
    "figure_label": block.get("figure_label"),
    "headers": (r.get("evidence") or {}).get("headers"),
    "slope": r.get("slope"),
    "intercept": r.get("intercept"),
}
if r.get("relation_model_ambiguous") is True:
    relation_extra["relation_model_ambiguous"] = True
    relation_extra["relation_model_alternatives"] = list(
        r.get("relation_model_alternatives") or []
    )
findings.append(_relation_finding(
    r.get("kind"),
    r.get("col_a"),
    r.get("col_b"),
    int(r.get("n") or 0),
    1.0,
    r.get("rule"),
    r.get("col_a_sample"),
    r.get("col_b_sample"),
    **relation_extra,
))
```

Do not pass the optional keys with `None` or `False`.

- [ ] **Step 5: Document scan and verdict semantics**

In the `Every finding has` section of
`skills/paperconan/references/output-schema.md`, add:

```markdown
- `relation_model_ambiguous` (optional): `true` when float representation and
  intercept uncertainty leave both a proportional and nonzero-intercept
  affine model compatible with the stored values. This qualifies the
  statistical signal; it is not a final judgment.
- `relation_model_alternatives` (optional): deterministic compatible kinds,
  currently `["constant_ratio", "exact_linear"]`. It appears only with
  `relation_model_ambiguous: true`.
```

In the verdict evidence-binding section, add:

```markdown
These additive relation-model fields do not change `finding_ref` matching and
do not require a `schema_version` increment. An archived verdict remains bound
to its archived scan; rerunning a changed detector can still produce a
different primary `kind`.
```

- [ ] **Step 6: Run focused GREEN tests**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_packet.py::test_distill_relations_preserves_optional_model_ambiguity \
  tests/test_skill_docs.py::test_output_schema_documents_relation_model_ambiguity
```

Expected: `2 passed`.

- [ ] **Step 7: Run boundary suites**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_packet.py \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py \
  tests/test_skill_docs.py \
  tests/test_packaging.py::test_sdist_allowlist_matches_tracked_public_files
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add \
  src/paperconan/schema.py \
  src/paperconan/packet.py \
  tests/test_packet.py \
  skills/paperconan/references/output-schema.md \
  tests/test_skill_docs.py
git commit -m "docs: expose relation model ambiguity"
```

---

### Task 4: Review, Verification, and Branch Completion

**Files:**

- Modify locally only:
  - `.superpowers/sdd/final-review-fix-wave-6-report.md`
  - `.superpowers/sdd/progress.md`
- Do not commit either file.

**Interfaces:**

- Consumes all Task 1-3 commits.
- Produces a clean reviewed branch ready for local integration.

- [ ] **Step 1: Record RED/GREEN evidence**

Append every actual RED command/result, GREEN command/result, implementation
commit, and self-review correction to:

```text
.superpowers/sdd/final-review-fix-wave-6-report.md
```

Update the Wave 6 entry in:

```text
.superpowers/sdd/progress.md
```

- [ ] **Step 2: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest -W error -q \
  tests/test_relations_tolerance.py \
  tests/test_packet.py \
  tests/test_profiles.py \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py \
  tests/test_detector_coverage.py \
  tests/test_resource_lifetime.py \
  tests/test_skill_docs.py \
  tests/test_packaging.py
```

Expected: all pass with no warnings.

- [ ] **Step 3: Request task review**

Use `superpowers:requesting-code-review`. The reviewer must check:

- raw high-precision coefficient preservation;
- correct proportional/affine/ambiguous boundaries;
- ratio-first single-finding behavior;
- metadata absence on identifiable findings;
- compact packet and verdict compatibility;
- deterministic output; and
- unchanged detector resource contracts.

Repeat implementation and review until Critical, Important, and Minor findings
are all zero.

- [ ] **Step 4: Repeat detector-owned and whole-branch reviews**

Re-run the detector-owned reviewer and the whole-branch reviewer against the
new HEAD. Address technically valid findings one at a time with focused
RED/GREEN tests. Repeat both reviews until all severities are zero.

- [ ] **Step 5: Run both complete test entry points**

Run independently:

```bash
.venv/bin/python -m pytest -q
uv run --frozen pytest -q
```

Expected: both complete suites pass with the same pass/skip totals.

- [ ] **Step 6: Verify lock and build artifacts**

Run:

```bash
uv lock --check
rm -rf dist build src/paperconan.egg-info
.venv/bin/python -m build --no-isolation
.venv/bin/python -m pytest -q tests/test_packaging.py
rm -f /tmp/paperconan-skill-final.zip
./build_skill_zip.sh /tmp/paperconan-skill-final.zip
unzip -t /tmp/paperconan-skill-final.zip
```

Expected: lock check, wheel/sdist build, packaging tests, and ZIP integrity all
pass.

- [ ] **Step 7: Verify repository cleanliness**

Run:

```bash
git diff --check
git status --short
```

Remove generated `dist/`, `build/`, `src/paperconan.egg-info/`, and
`/tmp/paperconan-skill-final.zip`, then run both commands again.

Expected: `git diff --check` is silent and tracked status is clean.

- [ ] **Step 8: Finish the branch**

Use `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. The user previously selected
local integration option 1, so merge `codex/project-hardening` into its base
branch locally only after every review and verification gate is green.
