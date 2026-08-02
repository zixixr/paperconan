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

- [x] Add a failing end-to-end test containing both directions of the existing `2.19x` fixture; assert identical context classes for the same canonical key.
- [x] Run the test and observe `isolated_ratio` in one direction and `proportional_family` in the other.
- [x] Add a failing scale-invariance test for factors `1e-14`, `1`, and `1e14`; assert the same class at every scale.
- [x] Run it and observe the current `1e-12` cutoff changing the result.
- [x] Implement `_ratio_context_key` and `_symmetric_ratio_miss`; remove `_ratio_grid_resolution` and quantum reconstruction from `_classify_ratio_structure`.
- [x] Compute one decision per canonical key and copy it to every directed relation carrying that key.
- [x] Run `tests/test_ratio_structure_context.py` and commit together with Task 2 because removing the quantum floor intentionally exposed the ambiguity cases Task 2 owns.

### Task 2: Separate Structural Context From Isolation Decision

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_ratio_structure_context.py`
- Modify: `tests/test_ratio_structure_bench.py`

**Interfaces:**
- A canonical relation can be `proportional_family`, `proportional_series`, `ambiguous_within_context`, or `isolated_ratio`.
- `ambiguous_within_context` is included in a family/series summary and excluded from `isolated_relations`.
- `_ratio_peer_excess` returns `0.0` when the candidate and the peer reference quantile are both numerically exact; returns `math.inf` when no peer context exists.
- Structural peers are local to either endpoint of the candidate relation. The second-nearest endpoint peer is the reference miss, so a three-row panel remains visible inside a much larger block while one accidental neighbour cannot explain the candidate by itself.
- `min_peer_excess=7.0` and `min_peer_improvement=0.005` remain provisional. A relation is isolated only when both thresholds pass. Excess above `1.0` which fails either isolation threshold is `ambiguous_within_context`; using both a ratio and an absolute dimensionless improvement prevents a tiny denominator from promoting negligible differences.

- [x] Run the existing exact multi-row family test after removal of the quantum floor and confirm it still produces one family summary with no isolated relation.
- [x] Add a failing test that the one-decimal planted relation inside a dense smooth block is present, classified `ambiguous_within_context`, absent from `isolated_relations`, and counted by a table-level summary.
- [x] Strengthen the existing two-decimal planted test to require `isolated_ratio` in both directions; confirm the Task 1 symmetric implementation already satisfies it.
- [x] Implement the tri-state isolation decision once per canonical key and attach ambiguous relations to the relevant summary.
- [x] Assert family/series summaries expose `ambiguous_relation_count` and retain raw `relation_count`.
- [x] Run both ratio-structure test files and commit.

### Task 3: Independent Holdout Bench

**Files:**
- Create: `tests/test_ratio_structure_holdout.py`
- Modify: `recheck/sigplan/m25_structure.py` (gitignored aggregate runner)
- Create: `recheck/sigplan/m25_holdout.py` (gitignored aggregate runner)

**Interfaces:**
- Calibration continues to use the frozen 40-sheet generator.
- Holdout uses disjoint seeds and at least three new shape families: sigmoid with a non-zero baseline, saturating response with varying half-max, and exponential decay with varying amplitude/rate.
- Holdout output contains aggregate counts only and reports classes by shape, precision and threshold; it contains no real supplementary identities.

- [x] Add failing presence-first holdout tests: each null family must generate at least one qualifying relation at every tested precision before zero isolated output is accepted.
- [x] Add planted sweeps over multiple constants, row positions and structural contexts; separable planted relations must remain isolated while context-supported planted relations must fold as ambiguous/table-level structure. Unit-scale invariance remains covered by Task 1 because M1's float-precision inference is intentionally outside this context-only holdout.
- [x] Run thresholds `5`, `7`, and `9` on calibration and holdout; all three are stable after endpoint-local peer comparison, so `7` remains the provisional midpoint rather than a value selected at an acceptance boundary.
- [x] Confirm a shared stable interval exists; otherwise this task would have stopped before Task 4.
- [x] Run both aggregate scripts twice and compare their JSON byte-for-byte.
- [x] Commit only synthetic tests; keep local aggregate scripts/results gitignored.

### Task 4: Documentation and Offline Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-30-short-row-significance-gate.md`
- Modify: `docs/superpowers/plans/2026-08-01-ratio-context-invariants.md`
- Modify: `docs/superpowers/plans/2026-08-01-ratio-structure-m25.md`

**Interfaces:**
- The main design records the approved product rule, measured calibration/holdout counts, known ambiguity boundary, and an explicit M3–M5 go/no-go result.

- [x] Replace the contradictory unconditional planted acceptance with the approved separability rule.
- [x] Remove the claim that a one-decimal miss is an information-theoretic limit; describe only what the measured evidence can and cannot separate.
- [x] Correct the full-suite verification note and record the exact command used.
- [x] Run focused ratio tests (`83 passed`), `git diff --check`, and the full test suite (`1685 passed, 1 skipped`).
- [x] Inspect the call graph and assert `_classify_ratio_structure` still has zero production callers and no `_effective_row_quantums` dependency.
- [x] Profile the offline classifier at the 400-row candidate cap. Fix the observed `O(E*n^2)` rescan and repeated-scope cache first; record the resulting ~0.71 s runtime, ~285 MB process peak, and ~123 MB traced classifier allocation peak. M3–M5 remain blocked on the still-missing evidence gates rather than on this synthetic performance upper bound.
- [x] Commit the documentation and verification record.
