# Ratio table-context stress implementation plan

**Goal:** Extend the offline M2.5 prototype with the table-level evidence a human
would inspect next: repeated proportional mappings in the same column window and
layout. Keep the production ratio detector unchanged.

**Product rule:** A relation supported by another aligned relation with the same
quantization-compatible scale is not an isolated row pair. Two pairs are retained as
an ambiguous table transform; three or more become one proportional table-transform
summary. A single unsupported pair remains available for human re-check.

**Boundary:** `k ~= 1` belongs to the identical-row path and is not reintroduced as a
ratio finding. Labels and formulas may later explain a derived percentage, but the
numeric context layer must not guess that explanation from one pair alone.

## Task 1: Pin the stress shapes

**Files:**
- Add: `tests/test_ratio_table_context.py`
- Modify: `tests/test_ratio_structure_bench.py`

- [x] A proportional family restricted to a local column window folds even when the
      surrounding columns are unrelated.
- [x] Three count/percentage row pairs sharing one denominator fold into one table
      transform.
- [x] Two aligned cross-panel pairs become ambiguous table context, not two isolated
      outputs.
- [x] Three aligned cross-panel pairs become one stable table transform; a fourth pair
      at a different scale remains isolated.
- [x] One derived-looking numeric pair without labels/formulas remains isolated.
- [x] An exact shared-control pair (`k ~= 1`) produces no ratio relation upstream.

## Task 2: Implement the pure table fold

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_ratio_table_context.py`

- [x] Canonicalize relation direction by table layout and invert its ratio interval
      when needed.
- [x] Deduplicate reciprocal directions without counting them as independent support.
- [x] Require every observed direction of a row pair to admit one common interval;
      contradictory directions keep the whole pair isolated.
- [x] Group only relations with the same block pair, relative row offset, and column
      window whose ratio intervals admit one common constant.
- [x] Mark two-pair groups `ambiguous_table_transform`; summarize groups of at least
      three as `proportional_table_transform`.
- [x] Preserve every input relation in the annotated result and exclude folded groups
      from `isolated_relations`.
- [x] Keep the helper offline with zero production callers.

## Task 3: Measure, document, verify

**Files:**
- Modify: `docs/superpowers/specs/2026-07-30-short-row-significance-gate.md`
- Modify: this plan

- [x] Run the focused ratio/curve/short-row suite and the new stress tests
      (`120 passed`).
- [x] Run aggregate fixtures twice and compare deterministic output.
- [x] Run `git diff --check` and the full suite with the repository root on
      `PYTHONPATH` (`1701 passed, 1 skipped`).
- [x] Record the observed destinations, known limits, and continued M3-M5 block.
- [x] Commit only the tracked implementation, tests, and documentation.
