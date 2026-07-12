"""Decimal-tail clustering (class E): a few multi-digit fractional tails dominate a set.

A reported fingerprint: across hundreds of DIFFERENT high-precision values, the last 3
fractional digits are drawn from a tiny set (e.g. 6 tails accounting for ~80% of the
numbers, often in complementary pairs summing to 1000). Independently measured
high-precision data has ~uniform fractional tails, so this concentration is a
data-inconsistency signal worth an author's explanation. detect_last_digit tests only a
single last digit; detect_repeated_decimals lists 2-digit endings without a concentration
test; within_col_value_duplication counts repeated whole values, not shared tails.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import detect_decimal_tail_clustering


def _clustered(n, tails):
    # n high-precision values whose last-3 fractional digits are drawn from `tails`
    out = []
    for i in range(n):
        lead = f"{(i * 137 + 11) % 1000:03d}"          # varied leading fraction digits
        tail = tails[i % len(tails)]
        out.append(10 + i + int(lead + tail) / 1_000_000)
    return out


def _diffuse(n):
    # n high-precision values with well-spread fractional tails (no clustering)
    return [10 + i + ((i * 616157 + 7919) % 1_000_000) / 1_000_000 for i in range(n)]


def test_detects_dominant_tail_cluster():
    vals = _clustered(160, ["714", "286", "572", "428", "143", "857"])  # 6 tails = 100%
    r = detect_decimal_tail_clustering(vals, "Fig 4L")
    assert r is not None, "expected a tail-clustering finding"
    assert r["severity"] == "high"
    assert r["top_share"] >= 0.9
    assert r["complementary_pairs"] >= 1        # 714+286, 572+428, 143+857


def test_no_false_positive_on_diffuse_tails():
    assert detect_decimal_tail_clustering(_diffuse(300), "Fig X") is None


def test_partial_cluster_below_threshold_does_not_fire():
    # only ~30% clustered, rest diffuse — below the concentration threshold
    vals = _clustered(60, ["714", "286"]) + _diffuse(140)
    assert detect_decimal_tail_clustering(vals, "Fig Y") is None


def test_low_precision_values_are_ignored():
    # 2-decimal data has no 3-digit fractional tail → not enough high-precision values
    vals = [round(10 + i * 0.37, 2) for i in range(300)]
    assert detect_decimal_tail_clustering(vals, "Fig Z") is None


def test_small_n_does_not_fire():
    vals = _clustered(40, ["714", "286", "572", "428", "143", "857"])
    assert detect_decimal_tail_clustering(vals, "Fig S") is None


def test_common_denominator_column_is_not_flagged():
    # values = integer + k/7 : all DIFFERENT numbers, but only ~7 distinct fractional parts.
    # This shares 3-digit tails trivially and must NOT fire (H1 regression).
    vals = [10 + i + (i % 7) / 7 for i in range(300)]
    assert detect_decimal_tail_clustering(vals, "Fig /7") is None


def test_eighths_column_is_not_flagged():
    vals = [5 + i + (i % 8) / 8 for i in range(300)]
    assert detect_decimal_tail_clustering(vals, "Fig /8") is None


def test_large_magnitude_values_are_skipped():
    # tails above ~1e7 are read-precision noise; a clustered column there must not fire
    vals = _clustered(160, ["714", "286", "572", "428", "143", "857"])
    vals = [v + 1e8 for v in vals]
    assert detect_decimal_tail_clustering(vals, "Fig big") is None


def test_negative_values_counted_by_magnitude():
    vals = [-v for v in _clustered(160, ["714", "286", "572", "428", "143", "857"])]
    r = detect_decimal_tail_clustering(vals, "Fig neg")
    assert r is not None and r["top_share"] >= 0.9


def test_min_n_boundary():
    tails = ["714", "286", "572", "428", "143", "857"]
    assert detect_decimal_tail_clustering(_clustered(99, tails), "a") is None
    assert detect_decimal_tail_clustering(_clustered(100, tails), "b") is not None


def test_tail_cluster_flows_into_review_packet(tmp_path):
    from paperconan.packet import distill_findings_for_review
    vals = _clustered(160, ["714", "286", "572", "428", "143", "857"])
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text("\n".join(["m"] + [f"{v:.6f}" for v in vals]) + "\n", encoding="utf-8")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    distilled = distill_findings_for_review(scan)
    assert any(d.get("kind") == "decimal_tail_clustering" for d in distilled)


def test_scan_dir_surfaces_tail_clustering_and_html(tmp_path):
    vals = _clustered(160, ["714", "286", "572", "428", "143", "857"])
    data = tmp_path / "data"
    data.mkdir()
    # one column of high-precision clustered values
    lines = ["measure"] + [f"{v:.6f}" for v in vals]
    (data / "s.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)
    clusters = res.get("decimal_tail_clusters") or []
    assert any(c["severity"] == "high" for c in clusters)
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "fractional tails" in html
