# Coverage Task 3 Report

## Result

Implemented detector-path limitation reporting without changing detector
decisions or legacy default return shapes.

## Files Changed

- `src/paperconan/_audit.py`
  - Added optional coverage metadata returns for row-pair findings and collision
    grids.
  - Added scan limitations for wide detector paths, row-pair dimension limits,
    row-pair finding truncation, and collision-grid row truncation.
- `tests/test_detector_coverage.py`
  - Added end-to-end limitation tests and direct return-shape compatibility
    coverage.
- `.superpowers/sdd/coverage-task-3-report.md`
  - Recorded implementation and verification evidence.

## RED Evidence

Command:

```text
.venv/bin/python -m pytest tests/test_detector_coverage.py -q
```

Observed before production changes:

```text
5 failed in 0.61s
```

The failures showed that the four new limitation reasons were absent and
`_grid_from_rows` did not accept `with_coverage`.

## GREEN Evidence

Required focused command:

```text
.venv/bin/python -m pytest \
  tests/test_detector_coverage.py \
  tests/test_collisions.py \
  tests/test_decimal_tail_gate.py -q
```

Result:

```text
43 passed in 0.60s
```

Complete suite:

```text
.venv/bin/python -m pytest -q
```

Result:

```text
457 passed, 1 skipped in 44.28s
```

Whitespace validation:

```text
git diff --check
```

Result: exit code 0 with no output.

## Self-Review

- `scan_dir` still emits schema version 2, scan status, coverage, and all
  existing scan fields.
- Wide blocks add one `wide_block_detector_limit` with file, sheet, block
  coordinates, detector names, and the configured column cap.
- Non-wide blocks over row-pair dimensions add
  `row_pair_dimension_limit` with dimensions and configured caps.
- Row-pair result truncation adds `row_pair_finding_limit` with location, cap,
  and omitted count.
- Collision-grid row truncation adds `collision_row_limit` with file, sheet,
  total rows, and used rows.
- Existing `finding_limit` and `report_block_limit` behavior remains intact.
- Detector-path limitations use `add_limitation`, so they make coverage
  partial without incrementing `blocks_skipped`.
- `detect_row_pair_digit_coupling` and `_grid_from_rows` retain their existing
  default list/dictionary return shapes; coverage metadata is opt-in through a
  keyword-only argument.
- Row-pair detector ordering, thresholds, finding content, and kept-finding
  slice are unchanged.
- Output ordering is deterministic because limitations are emitted during the
  existing sorted file, sheet, and block traversal.
- New text uses neutral statistical-signal and data-inconsistency terminology.
- Changes are confined to the three owned task files.

## Concerns

None.
