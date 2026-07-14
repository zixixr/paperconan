# Relation Model Ambiguity Design

**Date:** 2026-07-14

**Status:** Approved

## Objective

Repair affine-relation classification without changing legitimate
high-precision coefficients.

The detector must distinguish these outcomes:

1. the data identify a proportional model (`constant_ratio`);
2. the data identify a nonzero-intercept affine model (`exact_linear`); or
3. finite-precision representation makes both models compatible with the
   observed values.

The third outcome keeps an existing relation `kind` for compatibility and adds
explicit model-ambiguity metadata. It does not create a new detector kind or
turn a statistical signal into a stronger conclusion.

## Confirmed Defect

Commit `a810246` rounds the centered-fit slope to the shortest decimal value
within a relative tolerance before calculating the intercept. That operation
changes the fitted model rather than only its display.

For a legitimate slope such as `1.2345678901`, the rounded value can become
`1.23456789`. At a translated baseline near `1e9`, the discarded slope digits
are multiplied by the baseline when the intercept is reconstructed. A pure
scaling relation can therefore acquire an apparent intercept near `0.1`, and
an affine relation with intercept `0.25` can acquire an apparent intercept
near `0.35`.

The root problem is not JSON formatting. Detection, classification, and
serialization currently share the mutated coefficient. Display-oriented
canonicalization therefore changes relation substance.

## Alternatives Considered

### Force a concise decimal slope

Keeping decimal canonicalization can make selected translated examples retain
the same `kind`, but it changes valid high-precision coefficients and can
manufacture a different intercept. This approach is rejected.

### Force one model whenever the intercept is uncertain

Always choosing either `constant_ratio` or `exact_linear` would keep a single
simple output shape, but it would hide cases where the stored float values do
not identify which model generated the source values. This approach is
rejected.

### Preserve coefficients and expose ambiguity

Keep the raw centered fit, derive an intercept uncertainty interval, and mark
the finding when both proportional and affine models remain compatible. This
is the selected approach because it preserves numeric evidence and states the
limit of what the stored values support.

## Compatibility Contract

- The detector continues to emit `constant_ratio` and `exact_linear`; no new
  `kind` is introduced.
- Existing identifiable relations preserve their finding substance, severity,
  order, evidence, and profile behavior.
- `slope` and `intercept` values used by detection are never decimal-rounded.
  Rule text may format them for readability without feeding formatted values
  back into detection.
- Ambiguous findings add:

  ```json
  {
    "relation_model_ambiguous": true,
    "relation_model_alternatives": [
      "constant_ratio",
      "exact_linear"
    ]
  }
  ```

- The ambiguity keys are omitted when the model is identifiable. A serialized
  `false` value is not required.
- The compact review packet preserves both ambiguity keys so downstream review
  does not lose this qualification.
- The change is additive and does not require a `schema_version` increment.
  Archived scans remain readable, and archived verdict formats remain
  accepted.
- Verdict evidence binding remains based on the primary finding fields such as
  `file`, `sheet`, `rows`, `kind`, and `rule`. The new ambiguity keys do not
  change selector matching.
- A verdict is bound to the archived scan from which its selector was created.
  As before, rerunning a changed detector can produce a different primary
  `kind`; this does not alter compatibility for an existing
  `scan.json`/verdict pair.

## Component 1: Unmodified Centered Fit

Keep the existing one-pass centered least-squares calculation:

- `x_center` and `y_center` are running means;
- `centered_xx` and `centered_xy` are centered sums;
- `slope = centered_xy / centered_xx`.

The computed slope is the detector coefficient. It is not replaced by a
shorter decimal approximation.

The intercept estimate is derived from the raw slope and the existing
low-cancellation anchor strategy. Selecting the observed `x` with the smallest
absolute magnitude remains deterministic and avoids unnecessary cancellation
when the data include values near zero:

```text
intercept = anchor_y - slope * anchor_x
```

Formatting in `rule` remains presentation-only. The serialized numeric
coefficient retains the raw fit value.

## Component 2: Intercept Uncertainty

Classification uses two separate quantities:

1. a practical-zero band for intercepts that are negligible at the observed
   transformed-data scale; and
2. an uncertainty interval describing how far the reconstructed intercept can
   move because the fitted line must be extrapolated from the observed data to
   `x = 0`.

For a varying input column, define:

```text
R = max(abs(x_i - x_center))
E = max(abs((y_i - y_center) - slope * (x_i - x_center)))
S = max(abs(slope * (max(x) - min(x))), abs(max(y) - min(y)))
```

`R` is the centered data radius, `E` is the maximum centered residual, and `S`
is the transformed-value span.

First derive a shared roundoff term:

```text
propagated_roundoff =
    propagated_intercept_ulp
    + abs(x_center) * slope_ulp
```

`slope_ulp` is the operation-level ULP bound for the raw fitted slope.
Including this term in both comparisons avoids treating a coefficient's own
binary representation limit as evidence for one model over the other.

The practical-zero tolerance and intercept uncertainty are:

```text
zero_tolerance =
    propagated_roundoff
    + 1e-9 * max(S, smallest_subnormal)

intercept_uncertainty =
    propagated_roundoff
    + E
    + abs(x_center) * E / R
```

`propagated_intercept_ulp` covers the floating-point subtraction used to
reconstruct the intercept, including the anchor value and
`slope * anchor_x`. The final `E / R` term converts the observed centered
residual into slope uncertainty; multiplying it by `abs(x_center)` accounts
for extrapolation from the observed baseline to zero.

This is deliberately baseline-aware only for uncertainty propagation. The
practical-zero band itself remains based on ULP representation limits and
transformed-data variation, not on the absolute source baseline.

The plausible intercept interval is:

```text
[intercept - intercept_uncertainty,
 intercept + intercept_uncertainty]
```

It is classified as:

- **proportional identified** when the complete plausible interval is inside
  `[-zero_tolerance, zero_tolerance]`;
- **affine identified** when the complete plausible interval is outside that
  band on one side; or
- **model ambiguous** when the plausible interval overlaps the band boundary.

This gives stable behavior at the useful boundaries:

- ordinary-scale pure scaling has a narrow interval contained in the
  practical-zero band;
- an ordinary-scale material intercept such as `0.25` has an interval
  separated from zero;
- a large translation can widen the interval enough that both models remain
  compatible, which is reported explicitly rather than resolved by changing
  the slope.

## Component 3: Finding Selection

The ratio predicate and affine-fit predicate are evaluated independently of a
single `intercept_is_zero` Boolean.

- If the proportional model is identified and the ratio predicate passes,
  emit `constant_ratio` and suppress the redundant `exact_linear` finding.
- If the affine model is identified and the fitted-line predicate passes,
  emit `exact_linear`.
- If both predicates are compatible and the intercept state is ambiguous,
  retain the detector's established ratio-first deduplication precedence:
  emit one `constant_ratio` finding and attach the two ambiguity keys.
- If the ratio predicate cannot run, such as when the input contains zero, the
  existing `exact_linear` fallback remains the sole finding. Ambiguity metadata
  is added only when both model predicates are actually compatible.
- Identity and constant-offset suppression remain unchanged.

The alternatives list has a fixed order,
`["constant_ratio", "exact_linear"]`, independent of the primary `kind`.
This keeps serialized output deterministic.

## Component 4: Data Flow and Resource Accounting

For each admitted column pair:

1. calculate the raw centered fit;
2. collect `R`, `E`, the anchor, and scalar span values in the existing scalar
   traversal;
3. derive the zero band and uncertainty interval;
4. evaluate ratio and affine compatibility;
5. offer at most one deduplicated relation finding with optional ambiguity
   metadata.

The implementation must not allocate a new row-proportional Python collection
or NumPy array. Scalar accumulators should reuse the existing fit/residual
passes. If an additional proportional pass is unavoidable, detector work
accounting must charge it before the pass and resource tests must be updated.
The existing candidate transaction and bounded finding collector continue to
own rejection, cleanup, and emission.

## Degenerate and Error Cases

- A non-varying `x` column keeps the existing non-linear path; no uncertainty
  division is attempted.
- Filtered non-finite values remain excluded by the existing numeric mask.
- A non-finite slope, radius, tolerance, or uncertainty cannot produce an
  ambiguity claim. The detector follows its existing conservative no-finding
  behavior for an invalid fit.
- Resource rejection remains atomic: the current candidate emits no partial
  finding or partial ambiguity metadata.

## Testing Strategy

Implementation follows strict RED/GREEN development.

Focused regressions cover:

- an identifiable ordinary-scale pure ratio;
- an identifiable ordinary-scale nonzero intercept;
- a legitimate high-precision slope such as `1.2345678901`, proving the
  serialized coefficient is not shortened;
- translated pure-ratio and affine examples whose stored floats cannot
  distinguish the two models, proving one primary finding carries the fixed
  alternatives list;
- cases immediately inside, outside, and overlapping the practical-zero
  boundary;
- the zero-containing input fallback;
- deterministic rule formatting without coefficient feedback;
- compact-packet preservation of the ambiguity keys;
- archived scan and both verdict shapes remaining readable;
- unchanged finding order and severity for identifiable cases; and
- unchanged dense state declarations, plus truthful work accounting for every
  proportional pass.

The focused relation, packet, report, profile, detector-coverage, and resource
tests run under `-W error`, followed by both complete test suites and release
verification already required by the project-hardening branch.

## Out of Scope

- changing the global relation tolerances outside this ratio/affine decision;
- adding a new public environment variable;
- introducing a new public finding `kind`;
- changing verdict tiers or making an automated final judgment; and
- migrating archived verdict selectors to findings from a newly rerun scan.
