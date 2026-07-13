from __future__ import annotations

import pytest

from paperconan import scan_dir
from paperconan import _audit as audit
from paperconan._audit import detect_row_pair_digit_coupling
from paperconan._sheet import Sheet


def _coupled_values():
    left = [100 + i + ((i * 7) % 10) / 10 for i in range(40)]
    right = []
    for i, v in enumerate(left):
        if i < 25:
            right.append(v - 30)
        else:
            right.append(v - (11 + (i % 7)))
    return left, right


def test_detects_row_pair_preserving_decimal_and_ones_digits():
    left, right = _coupled_values()
    rows = [
        ["group", *[f"m{i}" for i in range(40)]],
        ["Hydrogel-mEGF", *left],
        ["NanoFLUID-mEGF", *right],
    ]
    sheet = Sheet.from_rows(rows)

    findings = detect_row_pair_digit_coupling(sheet, 1, 3, 1, 41, rows[0][1:])

    assert findings, "expected a row-pair digit-coupling finding"
    finding = findings[0]
    assert finding["kind"] == "row_pair_digit_coupling"
    assert finding["row_a"] == "Hydrogel-mEGF"
    assert finding["row_b"] == "NanoFLUID-mEGF"
    assert finding["n"] == 40
    assert finding["same_decimal1"] == 40
    assert finding["same_ones_decimal1"] == 25
    assert finding["coarse_10_diff"] == 25
    assert finding["severity"] == "high"


def test_low_cardinality_integer_score_rows_are_not_flagged():
    rows = [
        ["score", *[f"judge{i}" for i in range(12)]],
        ["Score A", 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3],
        ["Score B", 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1],
    ]
    sheet = Sheet.from_rows(rows)

    assert detect_row_pair_digit_coupling(sheet, 1, 3, 1, 13, rows[0][1:]) == []


@pytest.mark.parametrize(
    ("n_rows", "n_cols"),
    [
        (1, 10),
        (2, 9),
        (audit._ROW_PAIR_MAX_ROWS + 1, 10),
        (2, audit._ROW_PAIR_MAX_COLS + 1),
    ],
    ids=[
        "short-rows",
        "short-columns",
        "oversized-rows",
        "oversized-columns",
    ],
)
def test_dimension_exits_keep_coverage_return_shape(n_rows, n_cols):
    class NoAccessSheet:
        def cell(self, *_args):
            raise AssertionError("dimension exit accessed source cells")

        @property
        def numeric(self):
            raise AssertionError("dimension exit accessed numeric data")

    header = [f"c{column}" for column in range(n_cols)]
    sheet = NoAccessSheet()

    assert detect_row_pair_digit_coupling(
        sheet, 0, n_rows, 0, n_cols, header
    ) == []
    findings, metadata = detect_row_pair_digit_coupling(
        sheet,
        0,
        n_rows,
        0,
        n_cols,
        header,
        with_coverage=True,
    )

    assert findings == []
    assert metadata == {"findings_omitted": 0}


def test_scan_dir_surfaces_row_pair_digit_coupling_and_html(tmp_path):
    left, right = _coupled_values()
    neutral = [200 + i * 1.37 for i in range(40)]
    header = ["group", *[f"m{i}" for i in range(40)]]
    rows = [
        header,
        ["Hydrogel-mEGF", *left],
        ["NanoFLUID-mEGF", *right],
        ["Vehicle", *neutral],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "source.csv").write_text(
        "\n".join(",".join(str(v) for v in row) for row in rows) + "\n",
        encoding="utf-8",
    )

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)

    row_pairs = [
        f
        for blk in res.get("relations_blocks", []) or []
        for f in blk.get("row_pairs", []) or []
    ]
    assert any(f["kind"] == "row_pair_digit_coupling" for f in row_pairs)
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "row_pair_digit_coupling" in html
