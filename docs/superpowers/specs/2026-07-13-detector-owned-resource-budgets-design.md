# Detector-Owned Resource Budgets Design

**Date:** 2026-07-13

**Status:** Architecture approved; written specification pending review

## Objective

Close the remaining Wave 4 resource-accounting gaps by making the code that
allocates proportional state, performs proportional work, and emits findings
own the corresponding budget checks.

The change must preserve detector output for inputs that fit the configured
limits. Inputs that do not fit must stop before the rejected state or work
grows, return deterministic retained output, and mark the scan `partial` with
truthful structured limitations.

## Confirmed Gaps

The current implementation has four related defects:

1. Relation-detector peak state is estimated outside the detector. The estimate
   does not cover every simultaneously live array and NumPy workspace.
2. Block detectors build complete finding lists before the 150-item block cap
   is applied. The final output is bounded, but peak retained finding state is
   not.
3. Axis-classification work is charged through a fixed multiplier in the
   caller. That multiplier does not describe the concrete passes in
   `_axis_columns()` and includes grids that cannot participate in a relevant
   collision family.
4. Cross-sheet column fingerprint capacity is checked after fingerprint
   candidates have already been constructed.

These are instances of the same ownership problem: a caller predicts resources
that are actually consumed inside another function.

## Design Decision

Use detector-owned reservations and bounded output sinks.

- A proportional allocation must be preceded by a successful state
  reservation in the function that performs the allocation.
- A proportional pass must consume work in the function that performs the
  pass.
- A finding must pass through a bounded collector at the point where it would
  otherwise be appended.
- A summary component must reserve scan-wide capacity before candidate
  construction begins.

The orchestration layer supplies configured limits and aggregates coverage. It
does not duplicate detector allocation formulas.

## Compatibility Contract

- Direct detector calls without a supplied budget or collector remain
  unlimited and keep their existing list-returning behavior.
- Within configured limits, finding substance, severity, order, evidence,
  profile handling, and serialized output remain unchanged.
- `_cap_block_findings()` remains available as a compatibility helper and as
  an oracle for bounded-collector tests.
- `PAPERCONAN_MAX_FINDINGS_PER_BLOCK=0` continues to disable the block cap.
- Existing environment variable names and defaults remain unchanged.
- Existing `scan.json` and verdict shapes remain readable. New limitation
  details are additive and do not require a schema-version change.
- Stable input order remains the deterministic tie-breaker everywhere.

## Component 1: Allocation-Owned State Reservations

Add a small internal resource primitive in
`src/paperconan/_resources.py`, with no detector dependencies:

- `StateBudget(limit_units)` tracks live and peak float64-equivalent 8-byte
  units.
- `try_reserve(name, units)` returns a lease only when the allocation fits.
- A lease is released explicitly or by a context manager as soon as the
  corresponding state is no longer live.
- Reusing a reservation name without release, releasing an unknown lease, or
  exceeding the reserved units is an internal invariant error.
- Allocation factories are invoked only after reservation succeeds.

Detector-specific code remains responsible for calculating the units of the
allocation it is about to perform. Hidden NumPy sort, unique, partition, and
linear-fit workspaces receive explicit conservative reservations immediately
before the operation.

### Relation detector

`detect_relations()` receives an optional dense-family resource session. For
each column pair it:

1. consumes the work for the exact-value pair pass;
2. reserves the mask and filtered arrays before creating them;
3. reserves and releases each derived array or workspace around the operation
   that needs it;
4. completes or rejects the current pair as one atomic candidate; and
5. releases all pair-local state before moving to the next pair.

The tracked state includes, where applicable:

- numeric mask;
- filtered `x` and `y`;
- difference, ratio, sum, fitted, rounded, and fractional arrays;
- boolean masks and unique-value outputs;
- linear-fit and unique/sort workspace reserves.

Arrays should be reused or released early when that lowers the real peak
without changing numeric behavior.

The existing `_dense_detector_requirements()` and
`_dense_detector_admission()` must no longer be the source of truth for
relation state. Compatibility metadata may still expose a declared required
peak, but it must be produced from the detector-owned session and verified
against its observed peak.

### Exhaustion behavior

A failed reservation occurs before the rejected allocation. The current
candidate is not emitted partially. The detector returns:

- completed findings;
- candidates completed and skipped;
- work completed and a truthful skipped-work value or lower bound;
- peak state observed;
- the state/work limit reached.

The orchestrator converts this result into the existing
`dense_block_detector_limit` coverage family. If exact future work cannot be
known without running skipped candidates, the field is explicitly named and
documented as a lower bound.

## Component 2: Bounded Block Finding Collector

Introduce a `BoundedFindingCollector` shared by every finding-producing path
within one numeric block.

The collector owns:

- the configured block cap;
- global emission sequence within the block;
- group name from `BLOCK_FINDING_GROUPS`;
- severity rank;
- at most `cap` retained finding payloads;
- exact offered, retained, evicted, and omitted counts.

### Selection semantics

The retained set must be identical to applying `_cap_block_findings()` after
full materialization:

1. higher severity is retained before lower severity;
2. ties retain earlier detector/emission order;
3. final findings remain in their original group and original emission order.

The collector maintains only the current best `cap` entries. When full:

- a later candidate that cannot outrank the current worst retained entry is
  counted but its payload is not built;
- a candidate that outranks the current worst entry replaces it and releases
  the evicted payload immediately.

Detectors offer a severity plus a lazy payload factory, rather than appending a
constructed dictionary directly. This bounds both the retained list and
payload construction.

### Detector integration

All block finding families use the same collector in scan execution:

- relations;
- progressions;
- equal pairs;
- row-pair findings;
- within-column findings;
- dispersed repeats;
- identical-after-rounding findings;
- GRIM/GRIMMER findings.

Existing detector APIs use an unlimited list sink when no collector is
provided. Existing detector-local caps, such as the row-pair cap, remain in
force and feed their exact omissions into the block result.

After detection, the collector materializes the existing group dictionary.
Evidence attachment, benign-context enrichment, profile application, and
report serialization continue unchanged. `findings_omitted` is the exact sum
of collector omissions and existing detector-local omissions.

## Component 3: Detector-Owned Cross-Sheet Work

Cross-sheet family eligibility is computed once from retained summary sizes:

- positional/value family: only pairs for which at least one of its two
  finding rules can reach its minimum threshold;
- decimal-tail family: only pairs whose smaller grid can reach the minimum
  tail-match threshold;
- axis classification: only summaries participating in a feasible
  positional family pair, because axis context is used only by that result.

Impossible families consume no pair, value, or axis work and do not appear in
remaining-work totals.

### Axis classification

Move work consumption into `_axis_columns()` or its replacement. Each concrete
grid-cell pass consumes its actual number of value visits immediately before
the pass. The caller no longer charges `4 * total_grid_cells`.

Axis accounting exposes stage counters, such as:

- grouping visits;
- progression visits;
- recurrence-fingerprint visits.

Their sum equals the existing aggregate `values_examined` contribution.
The classifier processes one feasible summary at a time and uses compact
arrays/signatures instead of retaining Python cell tuples for the complete
corpus. Temporary grouped cells, ordering arrays, and recurrence fingerprints
reserve state before construction and release it as soon as their stage
completes.

No new public state control is added. Axis temporary state is bounded by a
documented fixed multiplier of the already configured scan-wide retained-grid
cell limit. The detector-owned `StateBudget` verifies that multiplier against
the concrete compact allocations. This turns the existing grid-cell limit into
a defensible hard bound for both retained grids and axis-classification
workspace.

If axis classification cannot fit, collision detection may continue without
axis-based downgrading, but coverage records that axis context was unavailable
and the scan becomes `partial`.

### Pair families

The positional/value and decimal-tail functions consume work inside their
actual source-grid loops. Remaining feasible pair and value work is derived
from linear-size eligibility aggregates; no all-pairs setup pass is allowed.

`CrossSheetWorkBudget` remains the scan-wide aggregate, but its counters are
updated by detector-owned operations rather than caller predictions.

## Component 4: Transactional Summary Capacity

Replace the current `begin_summary()` followed by post-construction
`try_retain()` pattern with a summary reservation transaction.

The transaction:

1. records the summary as considered;
2. reserves the summary slot;
3. reserves each proportional component before its builder grows;
4. commits actual retained metrics only after the complete summary succeeds;
5. releases all provisional reservations on rejection or error.

### Column fingerprints

Before `_stream_column_fingerprint()` is called for a physical column,
fingerprint capacity must already be reserved.

`build_cross_sheet_summary()` first derives the deterministic bounded set of
candidate physical columns from the numeric block intervals. It then requests
a provisional reservation for that candidate upper bound.

- If the reservation fails, the complete summary is rejected before any
  fingerprint candidate scans source rows.
- If it succeeds, unqualified columns release their provisional slots and only
  actual retained fingerprints are committed.
- If another summary dimension later rejects the summary, all fingerprint
  reservations are rolled back.

This is deliberately conservative when remaining capacity is smaller than the
candidate upper bound. It preserves the complete-summary contract and prevents
work or state from growing on a summary that cannot be guaranteed to fit.

The limitation reports candidate columns skipped before construction,
retained fingerprints, skipped summaries, and unavailable summary pairs. It
does not claim that every skipped candidate would have qualified.

## Data Flow

For each numeric block:

1. Create one bounded finding collector.
2. Invoke each detector with its own resource session and the shared collector.
3. Aggregate detector resource results into coverage.
4. Finalize collector groups and exact finding omissions.
5. Attach evidence and apply existing profile logic.

For each cross-sheet summary:

1. Open a scan-wide summary reservation transaction.
2. Reserve each component before construction.
3. Commit a complete compact summary or roll back the transaction.

For cross-sheet detection:

1. Derive feasible families and participating summaries.
2. Run budget-owned axis classification on only relevant summaries.
3. Run pair families with work consumption inside their loops.
4. Emit through the existing bounded cross-sheet finding path.
5. Aggregate one structured scan limitation when any shared budget is
   exhausted.

## Coverage Semantics

Normal inputs that fit all limits remain `complete`.

Any resource rejection makes the scan `partial` and identifies:

- scope, file, sheet, block, and detector family where applicable;
- configured limit;
- completed candidates/work;
- skipped candidates/work, distinguishing exact values from lower bounds;
- observed peak state;
- exact finding omissions known to bounded collectors;
- whether omitted finding totals remain a lower bound.

Resource exhaustion never means that the retained data contains no statistical
signal. Reports continue to present retained findings alongside the limitation.

## Testing Strategy

Implementation follows strict red-green cycles.

### State accounting

- Add relation cases that exercise offset, ratio, sum, linear-fit,
  fractional-shift, and discrete-difference branches.
- Verify every proportional allocation has a prior reservation.
- Verify observed peak state never exceeds the detector-owned declared peak.
- Test deterministic admission at `required - 1` and `required`.
- Verify all leases are released after success, early continue, and
  exhaustion.

### Finding collector

- Compare collector output with `_cap_block_findings()` across mixed groups,
  severities, caps, and stable ties.
- Use a large synthetic finding stream and assert retained payload count never
  exceeds the cap.
- Assert lazy factories are not called for candidates that cannot be retained.
- Verify exact omission counts and unchanged end-to-end dense-block output.

### Axis accounting

- Instrument grid iteration and assert reported visits equal actual visits.
- Prove undersized or otherwise infeasible grids are never touched by axis
  classification.
- Test zero, boundary, and sufficient work budgets.
- Verify ample-budget findings are byte-equivalent to current expected output.

### Fingerprint reservation

- With zero or insufficient remaining capacity, assert
  `source.exact_numeric()` is never called for a fingerprint candidate.
- Verify unused provisional slots are released for unqualified columns.
- Verify rejection in another summary dimension rolls back fingerprint
  reservations.
- Verify normal summaries and column-duplicate findings remain unchanged.

### Regression and release gates

- Run focused tests under `-W error`.
- Run both complete pytest entry points.
- Run lock, warning-free build, exact archive membership, Skill ZIP integrity,
  whitespace, generated-artifact cleanup, and tracked-status gates.
- Request an independent task review, then a complete branch review, and
  repeat until all severity levels are clear.

## Non-Goals

- No detector threshold or statistical interpretation changes.
- No change to archived scan/verdict compatibility.
- No new output family or user workflow.
- No broad detector-module rewrite beyond the resource and emission boundaries
  required here.
- No weakening of existing resource limits to make tests pass.

## Acceptance Criteria

The design is complete when:

1. no proportional detector state grows before its owning reservation;
2. no block retains more finding payloads than its configured cap during
   detection;
3. cross-sheet work counters match concrete passes over feasible inputs only;
4. fingerprint candidate construction never starts without scan-wide
   capacity;
5. normal-input output is unchanged and oversized-input coverage is truthful;
6. focused, full-suite, build, archive, and independent review gates pass.
