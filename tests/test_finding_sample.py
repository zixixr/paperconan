"""detect_relations emits a compact value peek (col_a_sample/col_b_sample) on each
relation finding so downstream LLM triage can SEE the actual data, not just indices.
The samples must be small (<=8 floats each) so they never reintroduce evidence bloat."""
from paperconan._sheet import Sheet
from paperconan._audit import detect_relations, detect_equal_pairs


def _sheet(cols):
    rows = [[f"c{j}" for j in range(len(cols))]]
    for k in range(len(cols[0])):
        rows.append([cols[j][k] for j in range(len(cols))])
    return Sheet.from_rows(rows)


def test_identical_column_carries_value_samples():
    col = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]
    s = _sheet([col, list(col)])
    findings = detect_relations(s, 1, 8, 0, 2, ["c0", "c1"])
    ident = [f for f in findings if f["kind"] == "identical_column"]
    assert ident, "expected an identical_column finding on two identical columns"
    f = ident[0]
    # samples are present, non-empty lists of <=8 floats matching the data
    assert isinstance(f["col_a_sample"], list) and isinstance(f["col_b_sample"], list)
    assert 0 < len(f["col_a_sample"]) <= 8
    assert 0 < len(f["col_b_sample"]) <= 8
    assert all(isinstance(v, float) for v in f["col_a_sample"])
    assert f["col_a_sample"] == col[:8] == col  # first <=8 of the column
    assert f["col_b_sample"] == col[:8] == col


def test_samples_bounded_to_eight():
    # a 20-row column must still only peek the first 8 values (no whole-column dump)
    col = [round(0.1 * i + 0.5, 6) for i in range(20)]
    s = _sheet([col, list(col)])
    f = next(x for x in detect_relations(s, 1, 21, 0, 2, ["c0", "c1"])
             if x["kind"] == "identical_column")
    assert len(f["col_a_sample"]) == 8
    assert len(f["col_b_sample"]) == 8
    assert f["col_a_sample"] == col[:8]


def test_equal_pairs_carry_value_samples():
    a = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.0, 8.0]
    b = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 9.0, 1.0]  # mostly equal -> many_equal_pairs
    s = _sheet([a, b])
    findings = detect_equal_pairs(s, 1, 9, 0, 2, ["c0", "c1"])
    assert findings, "expected a many_equal_pairs finding"
    f = findings[0]
    assert 0 < len(f["col_a_sample"]) <= 8
    assert 0 < len(f["col_b_sample"]) <= 8
    assert f["col_a_sample"] == a[:8]
    assert f["col_b_sample"] == b[:8]
