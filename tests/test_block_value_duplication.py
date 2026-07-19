"""block_value_duplication: many DISTINCT high-precision values each recurring
across different rows/columns of ONE block — a distributed copy fingerprint the
column-scoped detectors (within_col_value_duplication / within_col_dispersed_repeats)
are structurally blind to (JCI179845 Fig 2B). 统计信号, not a verdict.

FP control is a Poisson birthday-significance test (no hard sample-size floor),
so it fires on BOTH a whole-panel permuted copy (2B) and a big block where only a
few high-precision values were copied — while random continuous blocks stay quiet.
"""
import numpy as np
from paperconan._sheet import Sheet
from paperconan._audit import detect_block_value_duplication


def _block_sheet(matrix):
    ncol = max(len(r) for r in matrix)
    header = [f"c{j}" for j in range(ncol)]
    return Sheet.from_rows([header] + matrix)


def _detect(matrix, **kw):
    s = _block_sheet(matrix)
    ncol = max(len(r) for r in matrix)
    return detect_block_value_duplication(s, 1, len(matrix) + 1, 0, ncol,
                                          [f"c{j}" for j in range(ncol)], **kw)


def _fig2b_like(seed=7):
    """5 rows x 10 'independent replicates', each row = 5 distinct 4-decimal
    values, each appearing exactly twice in a shuffled order (mirrors 2B)."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(5):
        vals = [round(float(rng.uniform(0.2, 0.55)), 4) for _ in range(5)]
        row = vals + vals
        rng.shuffle(row)
        rows.append(row)
    return rows


def _few_copied(seed=1, nr=20, nc=10, k=3):
    """A big, genuinely independent 3-decimal block where only k values were
    pasted into 2 extra scattered cells each (the '只复制了几个数' case)."""
    rng = np.random.default_rng(seed)
    rows = [[round(float(rng.uniform(1, 600)), 3) for _ in range(nc)] for _ in range(nr)]
    for i in range(k):
        v = rows[i][0]
        rows[(i * 3 + 5) % nr][(i * 2 + 1) % nc] = v
        rows[(i * 3 + 11) % nr][(i * 2 + 4) % nc] = v
    return rows


def test_fig2b_style_permuted_replicates_fire_high():
    out = _detect(_fig2b_like())
    hits = [f for f in out if f["kind"] == "block_value_duplication"]
    assert hits, "expected a block_value_duplication finding"
    f = hits[0]
    assert f["scope"] == "block"
    assert f["severity"] == "high"          # whole-panel copy -> dup_fraction high
    assert f["pairs"] >= 4
    assert f["p_value"] < 1e-4
    assert f["example_cells"]
    assert all(isinstance(r, int) and isinstance(c, int) for r, c in f["example_cells"])


def test_few_values_copied_in_big_block_still_fire():
    # the '只复制了几个数' case: dup_fraction is tiny (~0.05) but exact repeats of
    # high-precision values in a continuous block are near-impossible by chance.
    out = _detect(_few_copied(k=3))
    hits = [f for f in out if f["kind"] == "block_value_duplication"]
    assert hits, "3 copied high-precision values in a 200-cell block must still fire"
    f = hits[0]
    assert f["p_value"] < 1e-4
    assert f["dup_fraction"] < 0.20         # low fraction ...
    assert f["severity"] == "low"           # ... so severity is low, but still reported


def test_two_values_copied_still_fire():
    out = _detect(_few_copied(k=2))
    assert [f for f in out if f["kind"] == "block_value_duplication"]


def test_independent_high_precision_block_does_not_fire():
    rng = np.random.default_rng(3)
    rows = [[round(float(rng.uniform(0.2, 0.55)), 4) for _ in range(10)] for _ in range(5)]
    assert not _detect(rows)


def test_coarse_2decimal_narrow_range_does_not_fire():
    # 2A-style body weights: 2 decimals over a narrow range -> small N_eff, natural
    # collisions are expected -> Poisson test not significant.
    rng = np.random.default_rng(11)
    rows = [[round(float(rng.uniform(15.0, 24.0)), 2) for _ in range(16)] for _ in range(5)]
    assert not _detect(rows)


def test_coarse_clustered_narrow_range_does_not_fire():
    # Real-world FP class: 2-decimal tumor-volume-like values, narrow range [0,2],
    # CLUSTERED (not uniform) -> natural collisions exceed the uniform birthday
    # model. The N_eff >= K*m validity gate must reject it (JCI186291 Figure 1).
    rng = np.random.default_rng(101)
    # clustered around a growth curve: many values land on the same 2-decimal ticks
    base = [0.1, 0.3, 0.5, 0.9, 1.2, 1.3, 1.48, 2.02]
    rows = [[round(float(b + rng.normal(0, 0.05)), 2) for b in base] for _ in range(8)]
    assert not _detect(rows)


def test_all_integer_block_does_not_fire():
    rng = np.random.default_rng(5)
    rows = [[int(rng.integers(0, 20)) for _ in range(10)] for _ in range(6)]
    assert not _detect(rows)


def test_small_block_below_min_hp_does_not_fire():
    rows = [[0.1234, 0.1234], [0.5678, 0.5678], [0.9876, 0.9876],
            [0.4321, 0.4321], [0.1111, 0.1111]]
    assert not _detect(rows)


def test_single_coincidental_pair_does_not_fire():
    # exactly one high-precision value appearing twice in an otherwise-independent
    # block is a single pair (min_pairs guard) -> not enough to fire alone.
    rng = np.random.default_rng(21)
    rows = [[round(float(rng.uniform(1, 600)), 3) for _ in range(10)] for _ in range(6)]
    rows[4][7] = rows[0][0]   # one duplicate pair
    assert not _detect(rows)


def test_monte_carlo_continuous_blocks_have_near_zero_fp():
    fp = 0
    trials = 400
    for seed in range(trials):
        rng = np.random.default_rng(2000 + seed)
        nr = int(rng.integers(4, 25))
        nc = int(rng.integers(4, 16))
        scale = float(rng.choice([1.0, 10.0, 100.0, 600.0]))
        dec = int(rng.choice([2, 3, 4]))
        rows = [[round(float(rng.uniform(0, scale)), dec) for _ in range(nc)]
                for _ in range(nr)]
        if [f for f in _detect(rows) if f["kind"] == "block_value_duplication"]:
            fp += 1
    assert fp <= 2, f"false-positive rate too high: {fp}/{trials}"


def test_mixed_precision_minority_copies_fire():
    # A block dominated by 2-decimal values with a MINORITY of 4-decimal copies:
    # the grid resolution must come from the duplicated values, not the block-wide
    # median, or the support gate would wash out the real high-precision fingerprint.
    rng = np.random.default_rng(2)
    rows = [[round(float(rng.uniform(1, 50)), 2) for _ in range(10)] for _ in range(8)]
    hp4 = [round(float(rng.uniform(1, 50)), 4) for _ in range(4)]
    for i, v in enumerate(hp4):
        rows[i][0] = v
        rows[(i + 3) % 8][(i + 5) % 10] = v
    assert [f for f in _detect(rows) if f["kind"] == "block_value_duplication"]


def test_single_dominant_value_alone_does_not_fire():
    # A single detection-limit floor repeated across many SCATTERED cells (not a
    # whole column, so the structural guard doesn't collapse it) is
    # within_col_value_duplication's job, not a distributed fingerprint: only one
    # distinct value recurs, so the >=2-distinct-value gate must reject it.
    rng = np.random.default_rng(31)
    rows = [[round(float(rng.uniform(1, 600)), 3) for _ in range(10)] for _ in range(20)]
    # scatter one floor value across ~30% of cells at varied (row, col) positions
    placed = 0
    for i in range(200):
        r, c = (i * 7) % 20, (i * 3) % 10
        if placed < 60 and c != 0:      # keep it off a single column
            rows[r][c] = 0.05
            placed += 1
    hits = [f for f in _detect(rows) if f["kind"] == "block_value_duplication"]
    assert not hits, f"single dominant value should not fire, got {hits}"


def test_poisson_sf_is_stable_and_bounded():
    from paperconan._audit import _poisson_sf
    # matches the naive CDF-complement for the small regime
    def naive(k, lam):
        cdf, term = 0.0, __import__("math").exp(-lam)
        for i in range(k):
            cdf += term
            term *= lam / (i + 1)
        return max(0.0, 1.0 - cdf)
    assert abs(_poisson_sf(5, 0.4) - naive(5, 0.4)) < 1e-12
    # huge k (would be a ~1e8-iteration loop) returns ~0 instantly, no hang
    assert _poisson_sf(10 ** 8, 0.5) == 0.0
    # large lam where exp(-lam) underflows: must NOT collapse to 1.0
    assert 0.0 < _poisson_sf(900, 745.0) < 1e-6


def test_pathologically_large_block_is_skipped():
    from paperconan._audit import detect_block_value_duplication, BLOCK_DUP_MAX_CELLS
    from paperconan._sheet import Sheet
    # a block whose area exceeds the cap is skipped without materializing it
    ncol = 1000
    nrow = BLOCK_DUP_MAX_CELLS // ncol + 5
    s = Sheet.from_rows([[0.12345] * ncol for _ in range(nrow)])
    assert detect_block_value_duplication(s, 0, nrow, 0, ncol, [f"c{j}" for j in range(ncol)]) == []


from paperconan._audit import _attach_evidence


def test_finding_gets_evidence_with_highlighted_cells():
    s = _block_sheet(_fig2b_like())
    out = detect_block_value_duplication(s, 1, 6, 0, 10, [f"c{j}" for j in range(10)])
    _attach_evidence(out, s, 1, 6, 0, 10, [f"c{j}" for j in range(10)])
    ev = out[0]["evidence"]
    assert ev.get("highlight_rows") or ev.get("highlight_cols"), "expected highlighted cells"
