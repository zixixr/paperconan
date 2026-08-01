# Ratio Context Invariants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make offline ratio-context classification direction- and unit-invariant, preserve indistinguishable relations in table-level summaries, and validate its isolation threshold on independent synthetic data without changing production output.

**Architecture:** M1/M2 remain responsible for quantized reconstruction and prediction scoring. M2.5 receives qualifying relations and complete numeric rows, canonicalizes reciprocal relations by undirected row pair plus column window, and compares row shapes with a symmetric dimensionless miss that does not read decimal precision. A broad proportional context folds ordinary relations; context-supported but non-separable relations receive `ambiguous_within_context`; only relations clearly tighter than their peers remain `isolated_ratio`.

**Tech Stack:** Python 3.10+, numpy, pytest, existing offline ratio bench.

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-01-ratio-context-decision.md`.
- Keep all language neutral: this is a statistical signal for human re-check, never a person-level judgement.
- Do not call `_effective_row_quantums` from M2.5 or branch on decimal count.
- Do not wire M2.5 into `detect_short_row_reuse` and do not change `_same_band`.
- Preserve every input relation in the returned evidence object.
- Use red-green-refactor for every behavior change.

---

### Task 1: Canonical, Symmetric Pair Evidence

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_ratio_structure_context.py`

**Interfaces:**
- Produce: `_ratio_context_key(relation) -> (min_row, max_row, start, end)`.
- Replace directional `_ratio_pair_relative_miss` with `_symmetric_ratio_miss(row_a, row_b, start, end) -> float`.
- `_symmetric_ratio_miss` rescales each finite vector by its own maximum absolute value before normalization, then returns the sine of the angle between the two row vectors. It returns `math.inf` for incompatible shapes, fewer than two shared finite coordinates, or an all-zero vector. It has no absolute value cutoff.
- `_ratio_peer_excess` consumes only rows, scope, canonical row pair and window. It must not consume quantums.

- [ ] Add a failing end-to-end test containing both directions of the existing `2.19x` fixture; assert identical context classes for the same canonical key.
- [ ] Run the test and observe `isolated_ratio` in one direction and `proportional_family` in the other.
- [ ] Add a failing scale-invariance test for factors `1e-14`, `1`, and `1e14`; assert the same class at every scale.
- [ ] Run it and observe the current `1e-12` cutoff changing the result.
- [ ] Implement `_ratio_context_key` and `_symmetric_ratio_miss`; remove `_ratio_grid_resolution` and quantum reconstruction from `_classify_ratio_structure`.
- [ ] Compute one decision per canonical key and copy it to every directed relation carrying that key.
- [ ] Run `tests/test_ratio_structure_context.py` and commit.

### Task 2: Separate Structural Context From Isolation Decision

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_ratio_structure_context.py`
- Modify: `tests/test_ratio_structure_bench.py`

**Interfaces:**
- A canonical relation can be `proportional_family`, `proportional_series`, `ambiguous_within_context`, or `isolated_ratio`.
- `ambiguous_within_context` is included in a family/series summary and excluded from `isolated_relations`.
- `_ratio_peer_excess` returns `0.0` when the candidate and the peer reference quantile are both numerically exact; returns `math.inf` when no peer context exists.
- `min_peer_excess=7.0` remains provisional: excess above it is isolated only when a dense qualifying-relation component does not already make the pair non-separable. Excess in `(1.0, 7.0]`, or excess above the bar inside a qualifying component spanning at least three rows, is `ambiguous_within_context`.

- [ ] Add a failing test that an ordinary exact multi-row family produces one family summary and no isolated relation after removal of the quantum floor.
- [ ] Add a failing test that the one-decimal planted relation inside a dense smooth block is present, classified `ambiguous_within_context`, absent from `isolated_relations`, and counted by a table-level summary.
- [ ] Add a failing test that the two-decimal planted relation which is clearly tighter than sparse structural peers remains `isolated_ratio` in both directions.
- [ ] Implement the tri-state isolation decision once per canonical key and attach ambiguous relations to the relevant summary.
- [ ] Assert family/series summaries expose `ambiguous_relation_count` and retain raw `relation_count`.
- [ ] Run both ratio-structure test files and commit.

### Task 3: Independent Holdout Bench

**Files:**
- Modify: `tests/test_ratio_structure_bench.py`
- Modify: `recheck/sigplan/m25_structure.py` (gitignored aggregate runner)
- Create: `recheck/sigplan/m25_holdout.py` (gitignored aggregate runner)

**Interfaces:**
- Calibration continues to use the frozen 40-sheet generator.
- Holdout uses disjoint seeds and at least three new shape families: sigmoid with a non-zero baseline, saturating response with varying half-max, and exponential decay with varying amplitude/rate.
- Holdout output contains aggregate counts only and reports classes by shape, precision and threshold; it contains no real supplementary identities.

- [ ] Add failing presence-first holdout tests: each null family must generate at least one qualifying relation across its sweep before zero isolated output is accepted.
- [ ] Add planted sweeps over multiple constants, row positions and scales; separable planted relations must remain isolated while dense-context planted relations must fold as ambiguous/table-level structure.
- [ ] Run thresholds `5`, `7`, and `9` on calibration and holdout; record the stable interval rather than selecting a value from the acceptance set.
- [ ] If no shared stable interval exists, keep the threshold unset and stop before Task 4 instead of tuning to one fixture.
- [ ] Run both aggregate scripts twice and compare their JSON byte-for-byte.
- [ ] Commit only synthetic tests; keep local aggregate scripts/results gitignored.

### Task 4: Documentation and Offline Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-30-short-row-significance-gate.md`
- Modify: `docs/superpowers/plans/2026-08-01-ratio-context-invariants.md`

**Interfaces:**
- The main design records the approved product rule, measured calibration/holdout counts, known ambiguity boundary, and an explicit M3–M5 go/no-go result.

- [ ] Replace the contradictory unconditional planted acceptance with the approved separability rule.
- [ ] Remove the claim that a one-decimal miss is an information-theoretic limit; describe only what the measured evidence can and cannot separate.
- [ ] Correct the full-suite verification note and record the exact command used.
- [ ] Run focused ratio tests, `git diff --check`, and the full test suite.
- [ ] Inspect the call graph and assert `_classify_ratio_structure` still has zero production callers.
- [ ] Profile the offline classifier at the 400-row candidate cap and record runtime/peak-memory evidence; keep M3–M5 blocked if it violates repository resource limits or any hard acceptance gate.
- [ ] Commit the documentation and verification record.

