# Ratio Context Decision

**Status:** approved on 2026-08-01

## Product rule

When a proportional relation cannot be distinguished from the broader structure of
its numeric block, it is not emitted as an isolated row pair. It is folded into one
table-level proportional-structure summary. Only a relation that is materially more
exceptional than its surrounding structure remains available as an isolated row-pair
statistical signal for human re-check.

This is an evidence rule, not a decimal-place eligibility gate:

- a one-decimal relation may remain isolated when layout or relational evidence makes
  it distinguishable;
- a higher-precision relation is folded when the surrounding structure explains it;
- an indistinguishable relation is retained inside the table-level summary rather than
  silently discarded.

## Context classes

- `isolated_ratio`: the relation is materially more exceptional than its structural
  peers and remains a row-pair candidate.
- `proportional_family` / `proportional_series`: the relation is an ordinary member of
  a broadly supported table-level structure.
- `ambiguous_within_context`: the block provides a proportional context but the
  available evidence cannot separate this relation from that context. It is folded
  into the table-level summary and is never emitted as an isolated row pair.

All original relations remain in the offline evidence object. Folding changes their
context and presentation, not the stored arithmetic evidence.

## Evidence ownership

M1 reconstructs directed quantized runs. M2 scores them and owns any use of inferred
recording resolution. M2.5 does not call `_effective_row_quantums` and does not infer
decimal precision again. It consumes complete numeric rows plus evidence already
attached to qualifying relations.

Structural comparison is canonical and symmetric for a fixed row pair and column
window. Reversing `row_a` and `row_b` must not change its context class. Multiplying a
whole block by a finite non-zero unit-conversion factor must not change its context
class. The classifier may use row order to recognize a series, but block-family
classification must otherwise be invariant to row permutation.

## Acceptance gates

M2.5 remains offline and M3–M5 remain blocked until all of these pass:

1. Every reciprocal relation over the same row pair and column window receives the
   same context class.
2. Unit conversion does not change family, ambiguous, or isolated classification.
3. Frozen curve benches end with zero isolated row pairs and produce non-empty
   table-level summaries whenever qualifying relations entered M2.5.
4. A planted relation that is demonstrably tighter than its surrounding structure
   remains `isolated_ratio`.
5. A planted relation that is not separable from a dense proportional context becomes
   `ambiguous_within_context` and appears in a table-level summary.
6. Multi-row proportional families produce one summary rather than many isolated
   row-pair outputs.
7. Thresholds are calibrated on one deterministic dataset and accepted on independent
   seeds and shape parameters. Mutation tests do not substitute for holdout evidence.
8. Full-suite, deterministic-run, runtime, and memory checks pass before production
   integration is considered.

## Non-goals for this stage

- Do not wire M2.5 into `detect_short_row_reuse`.
- Do not delete or weaken `_same_band`.
- Do not claim that the classifier recognizes a curve or infers a data-generating
  cause.
- Do not restore a per-decimal eligibility branch.

