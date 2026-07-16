from __future__ import annotations

import json

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 lacks the stdlib tomllib
    import tomli as tomllib

from paperconan import __version__


def test_test_extra_contains_pytest_and_table_extractors():
    with open("pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)

    extras = pyproject["project"]["optional-dependencies"]
    test_extra = " ".join(extras["test"])
    assert "pytest" in test_extra
    assert "pdfplumber" in test_extra
    assert "python-docx" in test_extra


def test_committed_demo_scan_version_matches_package():
    with open("examples/demo_paper/audit/scan.json", encoding="utf-8") as fh:
        scan = json.load(fh)

    assert scan["tool_version"] == __version__


def test_pyproject_version_matches_package():
    with open("pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)

    assert pyproject["project"]["version"] == __version__


def test_image_extra_is_optional_but_included_in_all_and_test():
    with open("pyproject.toml", "rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    assert {"pillow>=12", "pypdfium2>=5", "opencv-python-headless>=4.10"} <= set(
        extras["image"]
    )
    for name in ("all", "test", "dev"):
        joined = " ".join(extras[name])
        assert "pillow" in joined
        assert "pypdfium2" in joined
        assert "opencv-python-headless" in joined
