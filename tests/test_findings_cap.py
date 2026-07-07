"""Output-size guard: a single dense, highly-correlated block must not emit an
unbounded number of findings.

Regression for GitHub issue #15: a modestly sized but dense sheet (many mutually
proportional columns) makes the O(col^2) pairwise detectors emit thousands of
findings, each carrying an embedded evidence snippet, so scan.json / report.html
balloon to > 1 GB and the browser cannot open them. scan_dir must cap the number
of findings retained per block (keeping the highest-severity ones) and record how
many were omitted, so the report stays bounded and honest about the truncation.
"""
from __future__ import annotations

import csv

from paperconan._audit import (
    BLOCK_FINDING_GROUPS,
    _MAX_FINDINGS_PER_BLOCK,
    _cap_block_findings,
    scan_dir,
)


def _write_dense_csv(path, n_rows=40, n_cols=60):
    """A block where every column is a fixed scalar multiple of the first, so the
    linear/ratio/equal-pair detectors fire on ~every one of the O(col^2) pairs."""
    base = [round(1.0 + i * 0.7, 4) for i in range(n_rows)]
    header = [f"c{c}" for c in range(n_cols)]
    rows = []
    for r in range(n_rows):
        rows.append([round(base[r] * (c + 1), 4) for c in range(n_cols)])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _block_finding_count(blk):
    return sum(len(blk.get(g) or []) for g in BLOCK_FINDING_GROUPS)


def test_dense_block_findings_are_capped(tmp_path):
    data = tmp_path / "dense"
    data.mkdir()
    _write_dense_csv(str(data / "dense.csv"))

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    blocks = scan.get("relations_blocks") or []
    assert blocks, "expected the dense block to produce findings"
    for blk in blocks:
        n = _block_finding_count(blk)
        assert n <= _MAX_FINDINGS_PER_BLOCK, (
            f"block {blk['file']}::{blk['sheet']} kept {n} findings, "
            f"exceeds cap {_MAX_FINDINGS_PER_BLOCK}"
        )

    # The truncation must be recorded, not silent: this dense fixture generates far
    # more than the cap, so at least one block reports omitted findings.
    total_omitted = sum(int(blk.get("findings_omitted") or 0) for blk in blocks)
    assert total_omitted > 0, "dense block exceeded the cap but omission was not recorded"


def test_cap_keeps_highest_severity_first():
    """`_cap_block_findings` must drop the LEAST-severe findings first, so no dropped
    finding outranks a kept one. Tested directly on the helper because the e2e path runs
    `_demote_dense_sheets` afterwards, which flattens a dense sheet's severities to 'low'
    and would mask whether the cap itself selected by severity."""
    groups = {
        "relations": [{"severity": "low", "i": i} for i in range(100)]
                     + [{"severity": "high", "i": i} for i in range(20)],
        "grim": [{"severity": "medium", "i": i} for i in range(30)],
    }
    omitted = _cap_block_findings(groups, 40)

    kept = [f for lst in groups.values() for f in lst]
    assert len(kept) == 40
    assert omitted == 110
    counts = {s: sum(1 for f in kept if f["severity"] == s) for s in ("high", "medium", "low")}
    # 20 high + 30 medium = 50 > cap 40, so all 40 kept are high/medium and NO low survives.
    assert counts == {"high": 20, "medium": 20, "low": 0}, counts


def test_cap_is_deterministic_and_stable():
    """Ties within a severity band keep original order, so two identical inputs cap to the
    same findings (the scan output must stay byte-identical across runs)."""
    def fresh():
        return {"relations": [{"severity": "high", "i": i} for i in range(10)],
                "grim": [{"severity": "high", "i": i} for i in range(10)]}
    a, b = fresh(), fresh()
    _cap_block_findings(a, 5)
    _cap_block_findings(b, 5)
    assert a == b
    # Stable = the first-emitted findings win the ties.
    assert [f["i"] for f in a["relations"]] == [0, 1, 2, 3, 4]
    assert a["grim"] == []


def test_cap_none_is_unlimited():
    groups = {"relations": [{"severity": "low"}] * 500}
    assert _cap_block_findings(groups, None) == 0
    assert len(groups["relations"]) == 500
