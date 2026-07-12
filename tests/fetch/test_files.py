from paperconan import _input
from paperconan._input import SUPPORTED_INPUT_EXTS
from paperconan.fetch import _files


def test_ext_of_lowercases_and_strips_dot():
    assert _files.ext_of("Data Sheet 1.XLSX") == "xlsx"
    assert _files.ext_of("table.csv") == "csv"
    assert _files.ext_of("readme") == ""


def test_fetch_extensions_match_scanner_extensions():
    assert SUPPORTED_INPUT_EXTS == (
        "xlsx", "xls", "xlsm", "xlsb",
        "csv", "tsv", "pdf", "docx",
    )
    assert _files.TABULAR_EXTS == set(SUPPORTED_INPUT_EXTS)


def test_supported_input_check_is_case_insensitive():
    for ext in SUPPORTED_INPUT_EXTS:
        assert _files.is_tabular(f"source.{ext}")
        assert _files.is_tabular(f"SOURCE.{ext.upper()}")
    assert not _files.is_tabular("notes.txt")


def test_discover_supported_inputs_is_sorted_and_case_insensitive(tmp_path):
    (tmp_path / "z.PDF").write_bytes(b"")
    (tmp_path / "a.xlsx").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    (tmp_path / "table.csv").mkdir()

    assert _input.discover_supported_inputs(tmp_path) == [
        str(tmp_path / "a.xlsx"),
        str(tmp_path / "z.PDF"),
    ]


def test_make_fileref():
    ref = _files.make_fileref("t.csv", 1234, "https://x/t.csv")
    assert ref == {"name": "t.csv", "ext": "csv", "size": 1234, "download_url": "https://x/t.csv"}
