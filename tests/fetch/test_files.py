from paperconan.fetch import _files


def test_ext_of_lowercases_and_strips_dot():
    assert _files.ext_of("Data Sheet 1.XLSX") == "xlsx"
    assert _files.ext_of("table.csv") == "csv"
    assert _files.ext_of("readme") == ""


def test_is_tabular():
    assert _files.is_tabular("a.xlsx")
    assert _files.is_tabular("b.CSV")
    assert _files.is_tabular("c.tsv")
    assert not _files.is_tabular("d.zip")
    assert not _files.is_tabular("e.inp")


def test_make_fileref():
    ref = _files.make_fileref("t.csv", 1234, "https://x/t.csv")
    assert ref == {"name": "t.csv", "ext": "csv", "size": 1234,
                   "download_url": "https://x/t.csv", "label": None}


def test_image_and_document_classification_does_not_change_tabular_behavior():
    assert _files.is_image("Fig1.PNG")
    assert _files.is_image("panel.tiff")
    assert _files.asset_type("panel.webp") == "image"
    assert _files.asset_type("supplement.pdf") == "document"
    assert _files.asset_type("table.csv") == "tabular"
    assert _files.asset_type("movie.mp4") == "other"
    assert not _files.is_tabular("Fig1.PNG")
