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


from paperconan._audit import _attach_evidence


def test_finding_gets_evidence_with_highlighted_cells():
    s = _block_sheet(_fig2b_like())
    out = detect_block_value_duplication(s, 1, 6, 0, 10, [f"c{j}" for j in range(10)])
    _attach_evidence(out, s, 1, 6, 0, 10, [f"c{j}" for j in range(10)])
    ev = out[0]["evidence"]
    assert ev.get("highlight_rows") or ev.get("highlight_cols"), "expected highlighted cells"
