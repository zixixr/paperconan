# Ratio Structure M2.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove offline that one precision-agnostic structure layer can fold block-wide proportional families while retaining an isolated planted ratio, without changing production detector output.

**Architecture:** M1 continues to reconstruct directed quantized runs and M2 continues to score them. M2.5 consumes only qualifying relations plus their complete numeric block, builds an undirected row graph, and uses block/component rank-1 residuals to distinguish `proportional_family` from `isolated_ratio`. The prototype reports both raw edges and final isolated relations; it does not claim to recognize a curve or infer a cause.

**Tech Stack:** Python 3.10+, numpy, pytest, existing synthetic bench in `tests/test_curve_bench_baseline.py` and `recheck/sigplan/bench.py`.

## Global Constraints

- All output uses neutral statistical-signal language and never attributes intent or a person-level judgement.
- Real supplementary data, paths, candidate identities, and review judgements stay under gitignored `recheck/`.
- The production `detect_short_row_reuse` call graph remains unchanged throughout M2.5.
- Structure classification never branches on decimal count; precision is consumed only by reconstruction and scoring.
- Every behavior change follows red-green-refactor, with the failing test observed before implementation.

---

### Task 1: Score Only Informative Columns

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_quantized_ratio_core.py`
- Modify: `tests/test_ratio_prediction_bits.py`
- Modify: `recheck/sigplan/m2_threshold.py`

**Interfaces:**
- Produces: each `_scan_quantized_ratio_runs(...)` result includes `informative_columns: list[int]`.
- Produces: `_ratio_prediction_bits(target_values, quantums, n_tests, *, informative=None, max_quantum_ratio=None)`; `informative` is an optional Boolean sequence aligned with the two input sequences.
- Consumes: M2 runner converts each run's absolute informative column indexes into a slice-local Boolean mask.

- [ ] Add a failing scanner test asserting a `0 -> 0` column appears in the run span but not in `informative_columns`.
- [ ] Run that test and observe failure because the field is absent.
- [ ] Add `informative_columns` to the scanner result and rerun the scanner tests.
- [ ] Add a failing score test asserting an uninformative leading column neither anchors nor confirms the ratio.
- [ ] Run that test and observe the current scorer over-counting it.
- [ ] Filter the score input by the optional mask before anchor/confirmation accounting.
- [ ] Pass the run mask in `m2_threshold.py`; rerun M1/M2 tests and the frozen threshold measurement.

### Task 2: Classify Block-Wide Proportional Families

**Files:**
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_ratio_structure_context.py`

**Interfaces:**
- Produces: `_rank1_relative_residual(matrix) -> float`.
- Produces: `_classify_ratio_structure(rows, relations, *, max_rank1_residual=0.02, min_family_rows=3) -> dict`.
- Relation inputs contain integer `row_a`, `row_b`, `start`, and `end`; output copies each relation and adds `context_class` equal to `proportional_family` or `isolated_ratio`.
- Output also contains `block_rank1_residual`, `families`, and `isolated_relations` so the offline runner never infers classification from private state.

- [ ] Add a failing test where four exact multiples form one family and every edge becomes `proportional_family`.
- [ ] Run it and observe import/function failure.
- [ ] Implement finite rectangular rank-1 residual plus undirected components and make the family test pass.
- [ ] Add a failing test where one planted proportional pair sits inside an otherwise independent block and stays `isolated_ratio`.
- [ ] Implement component/block classification narrowly enough to retain that pair.
- [ ] Add malformed/NaN/zero-matrix boundary tests; return `inf` rather than granting a family classification when the residual cannot be estimated.
- [ ] Run the focused structure tests and the existing M1/M2 suite.

### Task 3: Run the Frozen M2.5 Acceptance Bench

**Files:**
- Create: `recheck/sigplan/m25_structure.py` (gitignored local measurement)
- Create: `tests/test_ratio_structure_bench.py`
- Modify: `docs/superpowers/specs/2026-07-30-short-row-significance-gate.md`

**Interfaces:**
- The local runner emits aggregate JSON only: raw runs, qualifying directed runs, unique row edges, family summaries, and final isolated relations by shape/precision.
- The committed test imports the frozen synthetic generator from `tests/test_curve_bench_baseline.py`; it contains no real data.

- [ ] Add a failing committed acceptance test for one frozen curve sheet at 1, 2, and 3 decimals: final isolated count must be zero.
- [ ] Add a failing committed test for an isolated planted two-row ratio in an independent block: exactly one unique isolated row pair remains.
- [ ] Adjust only the structure classifier until both tests pass; do not add a precision branch or raise the bits threshold.
- [ ] Run all 40 frozen sheets for continuous and paneled curve streams plus independent shapes; save aggregate results under `recheck/sigplan/`.
- [ ] Add at least one affine/shifted curve-family stress bench and one quantized common-pool bench; record unsupported shapes instead of weakening the hard gate silently.
- [ ] Write measured component/family/final-isolated counts back into the M2.5 section of the design.

### Task 4: Verify the Offline Boundary

**Files:**
- Modify only files required by failures found during verification.

**Interfaces:**
- No production detector integration is permitted.

- [ ] Run `pytest` for row quantum, quantized core, prediction bits, curve baseline, and both new structure test files.
- [ ] Run the M2 and M2.5 local measurement commands twice and compare JSON outputs byte-for-byte.
- [ ] Run the full test suite.
- [ ] Inspect `git diff --check`, `git status --short`, and the production detector call graph to confirm M2.5 remains offline.
- [ ] Record remaining unsupported curve/common-pool shapes and keep M3–M5 blocked unless every hard acceptance passes.
