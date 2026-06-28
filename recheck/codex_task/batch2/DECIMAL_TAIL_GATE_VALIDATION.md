# decimal-tail gate v2 validation

Run date: 2026-06-28

Code under test: local `src/paperconan` copied to w1 `/tmp/paperconan_gate_src`, using
`scan_dir(..., profile="forensic")`.

Remote output: `/tmp/dt_gate_rescan_full_1782661937`

## Scope

- Actual Nature Communications decimal-tail queue on w1: 41 papers, all already judged.
- Extra anchors added to the rescan: `10.1038/s41467-023-38842-6`,
  `10.1038/s41467-021-25561-z`.
- The earlier expected 86-candidate set was not present in `batch2`; current query returns 41.

## Rollup

Queue-only results:

- 41 papers scanned, 0 source scan errors.
- Old packet decimal-tail findings: 213.
- New forensic decimal-tail findings: 326.
- New severities: 154 high, 12 medium, 160 low.
- Paper-level: 22 papers still have high/medium decimal-tail; 19 are all-low or no longer have
  decimal-tail findings.

All 43 rows including anchors:

- New severities: 155 high, 12 medium, 160 low.
- 1 old packet read issue (`packet is not an object`), but source scan succeeded.

## KEEP / Anchor Outcomes

Existing KEEP rows survived:

- `10.1038/s41467-025-67467-0`: 3 high, 3 low. `axis_progression` appears only as a note on 2
  high findings.
- `10.1038/s41467-023-41807-4`: 2 high with
  `log_or_dilution_integer_shift_candidate` notes; not downgraded.
- `10.1038/s41467-024-45548-w`: 2 high, no benign note.
- `10.1038/s41467-024-52344-z`: 1 high, 1 medium, 1 low.

Anchor checks:

- `10.1038/s41467-023-38842-6`: 1 high, no benign note. Good hard anchor.
- `10.1038/s41467-021-25561-z`: 0 old decimal-tail findings and 0 new decimal-tail findings.
  This is not usable as a decimal-tail hard anchor unless the intended pair is re-extracted from a
  different source path or detector.

## Gate Sampling

M1 fixed denominator looks valid as an automatic low rule:

- `10.1038/s41467-025-56908-5`: all 42 new findings are low with `fixed_denominator:1/180`.
  Examples include `0.555555556`, `0.538888889`, `0.827777778`, consistent with k/180-style
  proportions or accuracies.

M4 per-column/global transforms look valid as automatic low rules:

- `10.1038/s41467-025-66361-z`: 2 low findings with per-column offsets such as repeated
  `+0.2` / `-0.2` and `-18.7`.
- `10.1038/s41467-024-47486-z`: low findings include per-column shifts across columns and
  constant transforms.

M2/M3/M5 should remain note-only for now:

- M2 `axis_progression` appears on a KEEP (`10.1038/s41467-025-67467-0`), so promoting it to low
  would create a false negative risk.
- M3 `constant_fraction_tail` appears as notes on high findings in
  `10.1038/s41467-025-59149-8`; keep it note-only until a separate manual pass confirms these are
  all benign.
- M5 behaves as intended: `10.1038/s41467-023-41807-4` remains high with log/dilution notes.

## Recommendation

Proceed with stage-1 rollout exactly as implemented:

- Auto-downgrade only M1 fixed denominator, M4 per-column transform, and the existing constant
  transform.
- Keep M2 axis progression, M3 few-tail dominance, and M5 log/dilution integer shifts as notes only.
- Replace `25561-z` as a decimal-tail hard anchor, or keep it outside decimal-tail regression until
  the intended finding is identified.

Verification:

- `.venv/bin/pytest -q tests/test_decimal_tail_gate.py tests/test_collisions.py tests/test_packet.py`
  -> 33 passed.
- `.venv/bin/pytest -q` -> 349 passed, 3 skipped.
