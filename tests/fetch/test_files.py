from pathlib import Path

import pytest

from paperconan import _input
from paperconan._input import SUPPORTED_INPUT_EXTS
from paperconan.fetch import _files
from paperconan.schema import PaperconanInputError


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


@pytest.mark.parametrize(
    ("path_kind", "message"),
    [
        ("missing", "input directory does not exist"),
        ("file", "input path is not a directory"),
    ],
)
def test_discover_supported_inputs_rejects_invalid_directory_paths(
    tmp_path, path_kind, message
):
    path = tmp_path / path_kind
    if path_kind == "file":
        path.write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(PaperconanInputError, match=message):
        _input.discover_supported_inputs(path)


def test_discover_supported_inputs_translates_enumeration_error(
    tmp_path, monkeypatch
):
    error = PermissionError("enumeration denied")
    original_iterdir = Path.iterdir

    def fail_iterdir(path):
        if path == tmp_path:
            raise error
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(
        PaperconanInputError,
        match="could not enumerate input directory",
    ) as caught:
        _input.discover_supported_inputs(tmp_path)

    assert str(tmp_path) in str(caught.value)
    assert caught.value.__cause__ is error


def test_make_fileref():
    ref = _files.make_fileref("t.csv", 1234, "https://x/t.csv")
    assert ref == {"name": "t.csv", "ext": "csv", "size": 1234, "download_url": "https://x/t.csv"}


def test_image_and_document_classification_does_not_change_tabular_behavior():
    assert _files.is_image("Fig1.PNG")
    assert _files.is_image("panel.tiff")
    assert _files.asset_type("panel.webp") == "image"
    assert _files.asset_type("supplement.pdf") == "document"
    assert _files.asset_type("table.csv") == "tabular"
    assert _files.asset_type("movie.mp4") == "other"
    assert not _files.is_tabular("Fig1.PNG")
