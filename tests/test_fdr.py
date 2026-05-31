"""Tests for Benjamini-Hochberg FDR correction across the per-sheet last-digit
χ² tests. Dozens of sheets are tested at once, so a raw p<1e-6 cutoff with no
multiple-testing control over-reports; BH gives each sheet a q-value instead.
"""
from __future__ import annotations

from paperconan._audit import benjamini_hochberg, scan_dir


def test_bh_adjusts_pvalues_step_up():
    """Known three-test case, hand-computed BH adjusted p-values."""
    adj, sig = benjamini_hochberg([0.001, 0.5, 0.9], alpha=0.05)
    assert abs(adj[0] - 0.003) < 1e-9   # 0.001 * 3/1
    assert abs(adj[1] - 0.75) < 1e-9    # 0.5   * 3/2
    assert abs(adj[2] - 0.9) < 1e-9     # 0.9   * 3/3
    assert sig == [True, False, False]


def test_bh_is_monotone_and_never_below_raw():
    pvals = [0.04, 0.01, 0.03, 0.005, 0.2]
    adj, _ = benjamini_hochberg(pvals, alpha=0.05)
    for p, a in zip(pvals, adj):
        assert a >= p - 1e-12, "adjusted p must never fall below the raw p"
        assert a <= 1.0


def test_bh_empty_is_safe():
    assert benjamini_hochberg([], alpha=0.05) == ([], [])


def _digit_rich_csv(seed_offset=0):
    rows = ["a,b,c"]
    v = 10.0 + seed_offset
    for i in range(60):
        # 4-decimal values with varied non-zero last digits
        rows.append(f"{v + i*1.137:.4f},{v + i*2.713:.4f},{v + i*0.917:.4f}")
    return "\n".join(rows) + "\n"


def test_scan_attaches_fdr_fields_to_digit_distribution(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    (data / "t1.csv").write_text(_digit_rich_csv(0), encoding="utf-8")
    (data / "t2.csv").write_text(_digit_rich_csv(5), encoding="utf-8")

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    dd = res["digit_distribution"]
    assert dd, "expected at least one per-sheet digit report"
    for d in dd:
        assert "p_adj" in d, "every digit report should carry a BH-adjusted q-value"
        assert "fdr_significant" in d
        assert d["p_adj"] >= d["p"] - 1e-12
