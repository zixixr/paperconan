import numpy as np
import pytest

import paperconan._audit as audit
from paperconan._audit import load_table, load_table_result
from paperconan._input import (
    ExtractedTableResult,
    InputLimitation,
    TableLoadResult,
)
from paperconan._sheet import Sheet


def _scan_with_limitation(tmp_path, monkeypatch, limitation):
    data = tmp_path / "data"
    data.mkdir()
    (data / "source.csv").write_text("a\n1\n", encoding="utf-8")
    result = TableLoadResult(
        sheets={"Stats": Sheet.from_rows([["a"], [1], [2], [3]])},
        limitations=[limitation],
    )
    monkeypatch.setattr(audit, "load_table_result", lambda _path: result)
    return audit.scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )


def test_table_load_result_keeps_compatibility_dict(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,2.5\n3,note\n", encoding="utf-8")

    legacy = load_table(str(path))
    result = load_table_result(str(path))

    assert isinstance(result, TableLoadResult)
    assert result.limitations == []
    assert legacy.keys() == result.sheets.keys()
    for name in legacy:
        legacy_sheet = legacy[name]
        result_sheet = result.sheets[name]
        assert type(legacy_sheet) is type(result_sheet)
        assert isinstance(result_sheet, Sheet)
        assert legacy_sheet.nrows == result_sheet.nrows
        assert legacy_sheet.ncols == result_sheet.ncols
        assert np.array_equal(
            legacy_sheet.numeric,
            result_sheet.numeric,
            equal_nan=True,
        )
        assert legacy_sheet._text == result_sheet._text
        assert legacy_sheet._ints == result_sheet._ints
        assert legacy_sheet._wide_ints == result_sheet._wide_ints


def test_input_limitation_serializes_deterministically():
    item = InputLimitation(
        scope="sheet",
        reason="cell_limit",
        sheet="S",
        details={"max_cells": 10, "cells": 12},
    )
    assert item.to_dict() == {
        "scope": "sheet",
        "reason": "cell_limit",
        "sheet": "S",
        "cells": 12,
        "max_cells": 10,
    }


@pytest.mark.parametrize("reserved", ["scope", "reason", "sheet"])
def test_input_limitation_rejects_reserved_detail_keys(reserved):
    with pytest.raises(
        ValueError,
        match=f"details contains reserved key: {reserved}",
    ):
        InputLimitation(
            scope="sheet",
            reason="cell_limit",
            sheet="S",
            details={reserved: "replacement"},
        )


@pytest.mark.parametrize("reserved", ["scope", "reason", "sheet"])
def test_scan_revalidates_mutated_reserved_limitation_details(
    tmp_path, monkeypatch, reserved
):
    limitation = InputLimitation(
        scope="sheet",
        reason="formula_cache_missing",
        sheet="Stats",
        details={"count": 1, "cells": ["A3"]},
    )
    limitation.details[reserved] = "replacement"

    with pytest.raises(
        ValueError,
        match=f"details contains reserved key: {reserved}",
    ):
        _scan_with_limitation(tmp_path, monkeypatch, limitation)


def test_scan_uses_actual_source_filename_for_input_limitation(
    tmp_path, monkeypatch
):
    limitation = InputLimitation(
        scope="sheet",
        reason="formula_cache_missing",
        sheet="Stats",
        details={"count": 1, "cells": ["A3"]},
    )
    limitation.details["file"] = "replacement.xlsx"

    scan = _scan_with_limitation(tmp_path, monkeypatch, limitation)

    assert scan["coverage"]["limitations"] == [{
        "scope": "sheet",
        "reason": "formula_cache_missing",
        "sheet": "Stats",
        "cells": ["A3"],
        "count": 1,
        "file": "source.csv",
    }]


def test_extracted_table_result_defaults_to_no_limitations():
    result = ExtractedTableResult(tables={"Table 1": [[1, 2]]})

    assert result.tables == {"Table 1": [[1, 2]]}
    assert result.limitations == []
