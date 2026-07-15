# Detector-Owned Resource Budgets Design

**Date:** 2026-07-13

**Status:** Approved

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
   `_axis_columns()` or distinguish positional participants, legacy
   recurrence-support grids, and truly irrelevant grids.
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
4. completes or rejects the current pair as one atomic candidate through a
   candidate transaction; and
5. releases all pair-local state before moving to the next pair.

The tracked state includes, where applicable:

- numeric mask;
- filtered `x` and `y`;
- difference, ratio, sum, fitted, rounded, and fractional arrays;
- boolean masks and unique-value outputs;
- linear-fit and unique/sort workspace reserves; and
- the unique output and workspace used to count distinct high-precision
  fractions.

Compound boolean masks are built into already reserved outputs, with any
second operand separately reserved, so expressions such as two `isnan`
results plus an `and` result do not create an uncharged peak. The
high-precision-fraction path compacts rounded eligible values into the
already-reserved `frac_x` array, then runs `np.unique` only while the complete
unique output and hidden-workspace reservations are live. It does not
construct a proportional Python list or set.

Arrays should be reused or released early when that lowers the real peak
without changing numeric behavior.

The existing `_dense_detector_requirements()` and
`_dense_detector_admission()` must no longer be the source of truth for
relation state. Compatibility metadata may still expose a declared required
peak, but it must be produced from the detector-owned session and verified
against its observed peak.

The existing `state_required` field keeps its complete-declaration meaning: a
detector supplies a conservative full-candidate upper bound before row, work,
or state admission. `state_required_lower_bound` separately reports the
largest simultaneous reservation attempted before stopping, while
`peak_state_units` reports the largest accepted live state. A scalar,
state-free detector declares `state_required = 0` explicitly rather than
using zero to represent an unknown value.

Other dense families follow the same reservation-first rule. In particular,
row-index arrays are reserved before `np.flatnonzero`; any row offset is then
applied in place so `np.flatnonzero(mask) + r0` cannot create an unreserved
second integer array.

### Candidate transaction

Every admitted dense candidate owns one finalizer. A normal exit includes
early `continue` paths for short inputs, scalar equality/offset findings, wide
integers, and candidates that correctly produce no finding. The finalizer
commits any candidate-local findings, increments `candidates_examined`
exactly once, and releases all leases.

A failed reservation marks the transaction rejected before the caller exits
the candidate body. The same finalizer then discards candidate-local findings,
does not increment `candidates_examined`, and releases all leases. Work
admission failure occurs before a transaction exists. Array-based families
reserve their first source-reading allocation before work admission, so a
state rejection cannot report source work that never ran.

After work admission succeeds, the candidate transaction adopts every
pre-reserved lease and is entered before the first source factory runs. A
factory exception therefore reaches the same finalizer as every other
candidate exit, including the rounding detector's earlier workspace lease.
The family session never releases a lease after candidate construction; until
the finalizer runs, every accepted lease remains both live and registered to
that candidate.

Candidate allocation and materialization methods assert that the transaction
has already been entered. Dense detectors cannot call a family-level
allocation or reservation primitive directly. The array-candidate admission
helper accepts bounded initial-reservation specifications, so the rounding
detector's first workspace and source lease are reserved together and adopted
by the candidate before its source factory runs. It returns the ordered initial
lease tuple with the candidate and source lease. Candidate materialization may
atomically release that tuple only after the source factory and output-size
validation succeed; an exception leaves every lease for the finalizer.
Family-level reserve, candidate-construction, work-admission, and completion
primitives stay private; detectors use only `begin()`, `start_candidate()`, and
`start_allocated_candidate()`, while factory execution is candidate-owned.

The candidate stores its family session in a private attribute and tracks only
currently live leases. Detector code releases state through
`candidate.release(lease)`, which releases and unregisters the lease together;
repeated per-group allocations therefore cannot grow a historical lease list.
Rejection state is private and exposed to detector code through a read-only
`candidate.rejected` property.
A candidate's proportional local variables live inside a no-argument,
candidate-scoped closure invoked by the `with candidate:` body. The closure may
capture the current candidate and source indexes, but never accepts, passes,
returns, or aliases the candidate or resource session. It returns before the
finalizer releases any remaining leases, so prior-candidate arrays cannot stay
bound in the detector frame while the next candidate allocates. Lazy finding
builders capture only scalars, immutable bounded samples, and other
non-proportional values.
A private resource-rejection exception unwinds every nested detector loop to
the candidate context boundary. The finalizer suppresses only that private
exception, discards the candidate, releases its remaining live leases, and
lets the detector stop its outer candidate loop. Unexpected source or numeric
exceptions still propagate after the same cleanup.

Normal empty results and resource rejection therefore never share one
ambiguous return value.

### Exhaustion behavior

A failed reservation occurs before the rejected allocation. The current
candidate is not emitted partially. The detector returns:

- completed findings;
- candidates completed and skipped;
- work completed and a truthful skipped-work value or lower bound;
- complete declared state, attempted-state lower bound, and peak accepted
  state;
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
- stable group order from `BLOCK_FINDING_GROUPS`;
- per-group emission sequence;
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

Candidate-local findings commit as one atomic batch. A finite collector
therefore reserves one additional `cap` of temporary payload capacity while
the original retained set remains available for rollback. Before each
replacement builder runs, the current working entry is removed; the rollback
snapshot remains its sole owner. The exact live-payload bound during an atomic
batch is therefore `2 * cap`, while the post-commit retained bound remains
`cap`. Outside an atomic batch, evicted payloads are released immediately.

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
- axis output: only summaries participating in a feasible positional family
  pair, because axis context is used only by that result;
- recurrence support: when a feasible positional pair exists, every retained
  summary with at least four cells remains eligible to contribute its legacy
  four-unique-value fingerprint, even when it has only four or five cells and
  cannot form a positional pair itself.

Impossible pair families consume no pair work. Grids with fewer than four
cells consume no axis work; four- and five-cell grids consume only the
loading, grouping, and recurrence-fingerprint work required to preserve
existing classification behavior.

### Axis classification

Move work consumption into `_axis_columns()` or its replacement. Each concrete
grid-cell pass consumes its actual number of value visits immediately before
the pass. The caller no longer charges `4 * total_grid_cells`.

Axis accounting exposes stage counters, such as:

- loading visits;
- grouping visits;
- progression visits;
- recurrence-fingerprint construction visits;
- recurrence ordering and group-scan visits;
- exact fingerprint-payload comparison visits;
- recurrence-mark visits; and
- final output-table visits.

Their sum equals the existing aggregate `values_examined` contribution.
The classifier processes one recurrence-support summary at a time and uses
compact arrays/signatures instead of retaining Python cell tuples for the
complete corpus. Loading, grouping, and recurrence-fingerprint stages cover
all support cells; progression covers only cells in eligible columns of
positional participants and is implemented as one complete compact-array pass.
Ordering, recurrence grouping,
exact payload comparison, recurrence marking, and output materialization
separately account every compact column-record pass or comparison. Exact
fingerprint classes are partitioned in place inside the reserved ordering
array, avoiding a second comparison pass or another proportional match list.
Temporary grouped cells, ordering arrays, and recurrence fingerprints reserve
capacity before construction and release it as soon as their stage completes.

Per-column counts, progression flags, fingerprint offsets/lengths/hashes, and
recurrence flags live in preallocated NumPy tables bounded by the
recurrence-support cell count. Canonical fingerprint bytes live in one
preallocated payload buffer. The implementation does not grow per-column Python
`dict`/`Counter`/`set` structures or a list of fingerprint leases.
The existing loading pass canonicalizes every signed zero to positive `0.0`
before bytes are stored, preserving the old `frozenset` equality of `-0.0` and
`0.0` without another proportional pass or mask.

The required public axis mapping is the only proportional Python container in
the result path. Before constructing it, the classifier reserves conservative
per-summary and per-column object-slot capacity. All compact tables,
fingerprint payloads, sort/unique workspaces, and final output capacity are
included in the fixed multiplier and named in state coverage tests. The
position and recurrence-support key tuples plus the position-key membership
set remain separately bounded by the existing scan-wide summary-count limit.

Each stage reserves its complete proportional state before work admission, then
is admitted immediately before its pass. If state or work admission fails, the
classifier discards partial axis context, reports the stages actually
completed, and records every known unperformed feasible stage as skipped.
Outcome-dependent exact-payload comparisons are admitted before each
comparison; when stopping prevents the remaining comparison/mark cardinality
from being known, `axis_work_skipped_lower_bound` and
`axis_work_skipped_is_lower_bound` disclose that boundary. No compact-array
pass, comparison, mark, or output insertion continues after its admission
fails. The unavailable path returns an empty mapping rather than allocating
unreserved per-summary empty sets.

No new public state control is added. Axis temporary state is bounded by a
documented fixed multiplier of the already configured scan-wide retained-grid
cell limit. The detector-owned `StateBudget` verifies that multiplier against
the concrete compact allocations and the reserved Python output capacity.
This turns the existing grid-cell limit into a defensible hard bound for both
retained grids and axis-classification workspace.
When no recurrence-support cell is eligible, the derived axis state limit and
observed peak are both zero.

If axis classification cannot fit, collision detection may continue without
axis-based downgrading, but coverage records that axis context was unavailable
and the scan becomes `partial`.

Axis coverage includes `axis_state_unit_limit` and
`axis_peak_state_units`. The first is the active fixed-multiplier cap (or the
private test override), and the second is the largest accepted simultaneous
reservation. The peak never exceeds the reported limit.

### Pair families

The positional/value and decimal-tail functions consume work inside their
actual source-grid loops. Remaining feasible pair and value work is derived
from linear-size eligibility aggregates; no all-pairs setup pass is allowed.

`CrossSheetWorkBudget` remains the scan-wide aggregate, but its counters are
updated by detector-owned operations rather than caller predictions.
Each pair helper admits its own complete pair/value upper bound before the
first source-grid access, records its concrete visits at every normal exit,
and records the current candidate as skipped when admission fails. The linear
candidate ledger accounts only later feasible candidates that were never
entered.

## Component 4: Transactional Summary Capacity

Replace the current `begin_summary()` followed by post-construction
`try_retain()` pattern with a summary reservation transaction.

The transaction:

1. records the summary as considered;
2. reserves the summary slot;
3. reserves each proportional component before its builder grows;
4. validates actual metrics and rejects before constructing the final summary
   object when any reservation was insufficient;
5. commits the already validated metrics only after the final summary and its
   limitations are complete; and
6. releases all provisional reservations on rejection or error.

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

1. Derive feasible pair families, position keys, and recurrence-support keys.
2. Run budget-owned axis classification on those exact support roles.
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
- Guard relation, row-index, unique, sort, concatenate/cumsum, and linear-fit
  operations at runtime. Each invocation must match one explicit
  required/forbidden lease contract, including all proportional outputs and
  hidden workspace, rather than inferring its call site from any live-name
  intersection.
- Keep the fitted-array construction workspace separate from the full
  conservative relation-comparison workspace used by `relation_close()`.
- Verify observed peak state never exceeds the detector-owned declared peak.
- Test deterministic admission at `required - 1` and `required`.
- Test row, work, and state rejection paths separately so
  `state_required`, `state_required_lower_bound`, and `peak_state_units` keep
  distinct meanings.
- Exercise short data, identical columns, integer offsets, wide integers,
  normal empty results, normal findings, and a later-candidate rejection;
  verify each normal path completes exactly once and each rejected path
  completes zero times.
- Verify all leases are released after success, early continue, and
  exhaustion.
- Make the dispersed-repeat precision gate, per-group integer differences, and
  per-group boolean gap mask explicit reserved arrays; guard each producing
  NumPy call with its complete input/output lease subset.
- Exercise thousands of allocate/release cycles and assert the candidate's live
  lease registry returns to its baseline after every cycle and has constant
  peak cardinality.
- Hold weak references to candidate-local arrays and verify the scoped helper
  releases them before the candidate finalizer and before the next candidate
  factory runs. Exercise this against the production relation detector, not
  only a synthetic candidate body.
- Reject the dispersed-repeat candidate at `group_rows`, `group_diffs`, and
  `group_gaps`, plus a rounding candidate during group refinement; verify no
  later group or sample operation runs.
- For every array-based dense family, reject work before the first source
  factory and verify the factory is not called. Then force that factory to
  raise after admission and verify the candidate finalizer releases its source
  lease and, for rounding, the pre-reserved workspace. Separately make source
  output validation fail and verify the source plus initial leases remain live
  and registered until the same finalizer releases them.
- For both scalar pair families, reject work before `_numeric_pair_stats()` and
  verify no source access. Then force that source pass to raise after admission
  and verify the entered candidate finalizer closes without completing the
  candidate.
- Parse every dense detector with `ast` and reject direct family allocation
  or reservation primitives, nested `resources.state` access, and aliases of
  the resource session. Permit exactly one root-scope resource-session
  initialization target, reject later resource stores/deletes, require every
  resource method call to execute synchronously in the detector root, and
  reject deferred resource captures. Derive candidate variable names from the
  admission-call assignment target; reject `with ... as` aliases, candidate
  passing or renaming, later stores, writable/deletable rejection state, and
  deferred lambda/generator/async captures; and reject yielding/awaiting
  candidate helpers. Require the proportional candidate helper to be a
  synchronous no-argument closure with no decorators, yielding, awaiting, or
  comprehension scope. Require every candidate method call to be owned by that
  helper, and require exactly one direct no-argument helper call as a statement
  in the unique root-owned `with candidate:` body. The helper cannot be saved,
  returned, passed, repeated, globalized, or declared nonlocal. Treat
  comprehensions as separate scopes and reject protected resource, candidate,
  or helper names within them. Recursively reject protected names bound by
  structural-pattern captures, sequence stars, or mapping rests, whose AST
  fields are strings rather than `Name(Store)` nodes. Permit only the
  documented transaction methods plus read-only `candidate.rejected`, and
  enforce the exact public admission-method whitelist for each detector
  family. Exercise the audit with synthetic outer-call, deferred-capture,
  reassignment, deletion, chained alias, status-write,
  helper-escape/repetition, comprehension, structural-pattern binding,
  `global`/`nonlocal`, `with ... as`, and generator-helper bypass attempts.

### Finding collector

- Compare collector output with `_cap_block_findings()` across mixed groups,
  severities, caps, and stable ties.
- Use a large synthetic finding stream and assert retained payload count never
  exceeds the cap.
- Assert lazy factories are not called for candidates that cannot be retained.
- Verify exact omission counts and unchanged end-to-end dense-block output.

### Axis accounting

- Instrument grid iteration, the compact progression helper, exact payload
  comparison, recurrence marking, and final output traversal; assert reported
  visits equal actual processing.
- Prove sub-four-cell grids are never touched, while four/five-cell recurrence
  support is retained without entering pair comparison or final axis output.
- Regress two positional grids plus one four-cell support grid so the legacy
  recurrence-based severity downgrade is unchanged.
- Regress otherwise identical recurring value sets that differ only by
  `-0.0` versus `0.0`; they must remain one fingerprint class with identical
  stage coverage, total work, and one source traversal per grid.
- Test zero, boundary, and sufficient work budgets.
- Use many-column worst cases to verify every compact table, fingerprint
  payload, recurrence workspace, and final output slot is reserved and the
  observed peak stays within the fixed multiplier.
- Test axis state at `required - 1`, `required`, and the default multiplier.
- Verify a pre-loading state rejection touches no grid cells, records every
  known unperformed pass, and marks the skipped-work count as a lower bound
  while outcome-dependent finalization cardinality remains unknown.
- As soon as each summary grouping determines its column count, register its
  recurrence-order, recurrence-group, and output passes. Reject both
  fingerprint work and fingerprint state after grouping and verify those
  already known fixed passes are included in skipped-work accounting.
- Stop immediately before recurrence comparison and output traversal and prove
  no unadmitted compact processing occurs.
- Verify ample-budget findings are byte-equivalent to current expected output.

### Fingerprint reservation

- With zero or insufficient remaining capacity, assert
  `source.exact_numeric()` is never called for a fingerprint candidate.
- Verify unused provisional slots are released for unqualified columns.
- Verify rejection in another summary dimension rolls back fingerprint
  reservations and occurs before final summary-object construction.
- Verify normal summaries and column-duplicate findings remain unchanged.

### Regression and release gates

- Run focused tests under `-W error`.
- Run both complete pytest entry points.
- Run lock, warning-free build, exact archive membership, Skill ZIP integrity,
  whitespace, generated-artifact cleanup, and tracked-status gates.
- Reject unsafe, duplicate, multi-root, unexpected-directory, or link archive
  members; verify the fixed wheel metadata allowlist, wheel `RECORD`, and
  every packaged source byte with synthetic negative cases plus the real
  build artifacts.
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
