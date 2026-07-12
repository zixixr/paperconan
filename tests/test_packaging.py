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


def test_uv_default_dev_group_and_pytest_scope():
    with open("pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    assert "pytest>=8" in pyproject["dependency-groups"]["dev"]
    assert pyproject["tool"]["uv"]["default-groups"] == ["dev"]
    pytest_config = pyproject["tool"]["pytest"]["ini_options"]
    assert pytest_config["testpaths"] == ["tests"]
    assert "src" in pytest_config["pythonpath"]


def test_supported_python_classifiers_cover_310_through_314():
    with open("pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    classifiers = set(pyproject["project"]["classifiers"])
    for minor in range(10, 15):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers


def test_lock_project_version_matches_package():
    with open("uv.lock", "rb") as fh:
        lock = tomllib.load(fh)
    project = next(
        item for item in lock["package"]
        if item["name"] == "paperconan"
    )
    assert project["version"] == __version__


def test_ci_uses_uv_with_matrix_python():
    with open(".github/workflows/tests.yml", encoding="utf-8") as fh:
        workflow = fh.read()

    setup_uv = (
        "      - uses: astral-sh/setup-uv@v6\n"
        "        with:\n"
        "          python-version: ${{ matrix.python-version }}"
    )
    assert setup_uv in workflow
    assert "      - run: uv sync --frozen" in workflow
    assert "      - run: uv run --frozen pytest -q" in workflow
