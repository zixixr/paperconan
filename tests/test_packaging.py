from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 lacks the stdlib tomllib
    import tomli as tomllib

import pytest

from paperconan import __version__

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DEV_GROUP = [
    "pytest>=8",
    "build>=1.2",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "xlwt>=1.3",
    "tomli>=2; python_version < '3.11'",
]
EXPECTED_PIP_EXTRA = [
    "pytest>=8",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "xlwt>=1.3",
    "tomli>=2; python_version < '3.11'",
]
EXPECTED_LOCK_DEV_DEPENDENCIES = [
    {"name": "build"},
    {"name": "pdfplumber"},
    {"name": "pytest"},
    {"name": "python-docx"},
    {"name": "tomli", "marker": "python_full_version < '3.11'"},
    {"name": "xlwt"},
]
EXPECTED_LOCK_DEV_METADATA = [
    {"name": "build", "specifier": ">=1.2"},
    {"name": "pdfplumber", "specifier": ">=0.11"},
    {"name": "pytest", "specifier": ">=8"},
    {"name": "python-docx", "specifier": ">=1.1"},
    {
        "name": "tomli",
        "marker": "python_full_version < '3.11'",
        "specifier": ">=2",
    },
    {"name": "xlwt", "specifier": ">=1.3"},
]
EXPECTED_PYTHON_CLASSIFIERS = {
    f"Programming Language :: Python :: 3.{minor}"
    for minor in range(10, 15)
}
DEPRECATED_LICENSE_CLASSIFIER = "License :: OSI Approved :: MIT License"
EXPECTED_CI_PYTEST_STEPS = [
    {"uses": "actions/checkout@v4"},
    {
        "uses": "astral-sh/setup-uv@v6",
        "with": {
            "python-version": "${{ matrix.python-version }}",
        },
    },
    {"run": "uv sync --frozen"},
    {"run": "uv run --frozen pytest -q"},
]
INLINE_MATRIX_JOB = """
pytest:
  strategy:
    matrix:
      python-version: ["3.10", "3.11"]
"""
BLOCK_MATRIX_JOB = """
pytest:
  strategy:
    matrix:
      python-version:
        - "3.10"
        - "3.11"
"""
NAMED_PYTEST_JOB = """
pytest:
  steps:
    - name: Checkout
      uses: actions/checkout@v4
    - name: Set up uv
      uses: astral-sh/setup-uv@v6
      with:
        python-version: ${{ matrix.python-version }}
    - name: Sync
      run: uv sync --frozen
    - name: Test
      run: uv run --frozen pytest -q
"""


def _load_toml(relative_path):
    with (ROOT / relative_path).open("rb") as fh:
        return tomllib.load(fh)


def _project_lock_entry():
    lock = _load_toml("uv.lock")
    return next(
        item for item in lock["package"]
        if item["name"] == "paperconan"
    )


def _semantic_identity(value):
    if isinstance(value, dict):
        return tuple(
            sorted(
                (key, _semantic_identity(item))
                for key, item in value.items()
            )
        )
    if isinstance(value, list):
        return tuple(_semantic_identity(item) for item in value)
    return value


def _assert_semantic_members(actual, expected):
    assert Counter(map(_semantic_identity, actual)) == Counter(
        map(_semantic_identity, expected)
    )


def _workflow_job(workflow, job_name):
    lines = workflow.splitlines()
    jobs_index = next(
        index for index, line in enumerate(lines)
        if line.strip() == "jobs:"
    )
    jobs_indent = len(lines[jobs_index]) - len(lines[jobs_index].lstrip())
    job_index = next(
        index for index in range(jobs_index + 1, len(lines))
        if (
            len(lines[index]) - len(lines[index].lstrip())
            == jobs_indent + 2
            and lines[index].strip() == f"{job_name}:"
        )
    )
    job_indent = len(lines[job_index]) - len(lines[job_index].lstrip())
    job_lines = [lines[job_index]]
    for line in lines[job_index + 1:]:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            if indent <= job_indent:
                break
        job_lines.append(line)
    return "\n".join(job_lines)


def _workflow_steps(job):
    lines = job.splitlines()
    steps_index = next(
        index for index, line in enumerate(lines)
        if line.strip() == "steps:"
    )
    steps_indent = len(lines[steps_index]) - len(lines[steps_index].lstrip())
    blocks = []
    current = []

    for line in lines[steps_index + 1:]:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            if indent <= steps_indent:
                break
            if indent == steps_indent + 2 and line.lstrip().startswith("- "):
                if current:
                    blocks.append(current)
                current = [line]
                continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)

    steps = []
    for block in blocks:
        list_indent = len(block[0]) - len(block[0].lstrip())
        first_key, first_value = block[0].lstrip()[2:].split(":", 1)
        step = {first_key: first_value.strip()}
        section = None
        for line in block[1:]:
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            key, value = line.strip().split(":", 1)
            value = value.strip()
            if indent == list_indent + 2:
                if value:
                    step[key] = value
                    section = None
                else:
                    step[key] = {}
                    section = key
            elif indent == list_indent + 4 and section is not None:
                step[section][key] = value
        step.pop("name", None)
        steps.append(step)
    return steps


def _yaml_scalar(value):
    if value.startswith(("[", '"', "'")):
        return ast.literal_eval(value)
    return value


def _workflow_matrix(job):
    lines = job.splitlines()
    matrix_index = next(
        index for index, line in enumerate(lines)
        if line.strip() == "matrix:"
    )
    matrix_indent = (
        len(lines[matrix_index]) - len(lines[matrix_index].lstrip())
    )
    matrix = {}
    for index, line in enumerate(
        lines[matrix_index + 1:],
        start=matrix_index + 1,
    ):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= matrix_indent:
            break
        if indent != matrix_indent + 2:
            continue
        key, value = line.strip().split(":", 1)
        value = value.strip()
        if value:
            matrix[key] = _yaml_scalar(value)
            continue

        items = []
        for nested in lines[index + 1:]:
            if not nested.strip():
                continue
            nested_indent = len(nested) - len(nested.lstrip())
            if nested_indent <= indent:
                break
            if (
                nested_indent != indent + 2
                or not nested.lstrip().startswith("- ")
            ):
                raise AssertionError("unsupported matrix presentation")
            items.append(_yaml_scalar(nested.lstrip()[2:].strip()))
        matrix[key] = items
    return matrix


def test_semantic_dependency_contract_ignores_harmless_reordering():
    _assert_semantic_members(
        list(reversed(EXPECTED_PIP_EXTRA)),
        EXPECTED_PIP_EXTRA,
    )
    _assert_semantic_members(
        list(reversed(EXPECTED_LOCK_DEV_METADATA)),
        EXPECTED_LOCK_DEV_METADATA,
    )


def test_semantic_dependency_contract_rejects_identity_changes():
    changed_marker = [
        dict(item)
        for item in EXPECTED_LOCK_DEV_METADATA
    ]
    changed_marker[4]["marker"] = "python_version < '3.11'"
    changed_specifier = [
        dict(item)
        for item in EXPECTED_LOCK_DEV_METADATA
    ]
    changed_specifier[0]["specifier"] = ">=1.1"

    changed_contracts = [
        EXPECTED_PIP_EXTRA[:-1],
        [*EXPECTED_PIP_EXTRA, "coverage>=7"],
        ["pytest>=9", *EXPECTED_PIP_EXTRA[1:]],
        [*EXPECTED_PIP_EXTRA, EXPECTED_PIP_EXTRA[0]],
        EXPECTED_LOCK_DEV_METADATA[:-1],
        [
            *EXPECTED_LOCK_DEV_METADATA,
            {"name": "coverage", "specifier": ">=7"},
        ],
        [
            *EXPECTED_LOCK_DEV_METADATA,
            EXPECTED_LOCK_DEV_METADATA[0],
        ],
        changed_marker,
        changed_specifier,
    ]
    expected_contracts = [
        EXPECTED_PIP_EXTRA,
        EXPECTED_PIP_EXTRA,
        EXPECTED_PIP_EXTRA,
        EXPECTED_PIP_EXTRA,
        EXPECTED_LOCK_DEV_METADATA,
        EXPECTED_LOCK_DEV_METADATA,
        EXPECTED_LOCK_DEV_METADATA,
        EXPECTED_LOCK_DEV_METADATA,
        EXPECTED_LOCK_DEV_METADATA,
    ]

    for actual, expected in zip(changed_contracts, expected_contracts):
        with pytest.raises(AssertionError):
            _assert_semantic_members(actual, expected)


def test_workflow_matrix_accepts_inline_and_block_lists():
    expected = {"python-version": ["3.10", "3.11"]}

    assert _workflow_matrix(INLINE_MATRIX_JOB) == expected
    assert _workflow_matrix(BLOCK_MATRIX_JOB) == expected


def test_workflow_steps_ignore_optional_name_fields():
    assert _workflow_steps(NAMED_PYTEST_JOB) == EXPECTED_CI_PYTEST_STEPS


def test_workflow_steps_preserve_behavior_changing_fields_and_commands():
    extra_env = NAMED_PYTEST_JOB.replace(
        "      run: uv sync --frozen",
        (
            "      run: uv sync --frozen\n"
            "      env:\n"
            "        UV_NO_CACHE: \"1\""
        ),
    )
    changed_action = NAMED_PYTEST_JOB.replace(
        "astral-sh/setup-uv@v6",
        "astral-sh/setup-uv@v5",
    )
    changed_with = NAMED_PYTEST_JOB.replace(
        "python-version: ${{ matrix.python-version }}",
        "python-version: 3.14",
    )
    changed_run = NAMED_PYTEST_JOB.replace(
        "run: uv sync --frozen",
        "run: uv sync",
    )
    extra_command = (
        NAMED_PYTEST_JOB
        + "    - name: Extra\n"
        + "      run: echo extra\n"
    )

    for job in (
        extra_env,
        changed_action,
        changed_with,
        changed_run,
        extra_command,
    ):
        assert _workflow_steps(job) != EXPECTED_CI_PYTEST_STEPS


def test_optional_test_extra_remains_pip_compatible():
    pyproject = _load_toml("pyproject.toml")

    extras = pyproject["project"]["optional-dependencies"]
    _assert_semantic_members(extras["test"], EXPECTED_PIP_EXTRA)


def test_optional_dev_extra_remains_pip_compatible():
    pyproject = _load_toml("pyproject.toml")

    extras = pyproject["project"]["optional-dependencies"]
    _assert_semantic_members(extras["dev"], EXPECTED_PIP_EXTRA)


def test_committed_demo_scan_version_matches_package():
    with (ROOT / "examples/demo_paper/audit/scan.json").open(
        encoding="utf-8"
    ) as fh:
        scan = json.load(fh)

    assert scan["tool_version"] == __version__


def test_pyproject_version_matches_package():
    pyproject = _load_toml("pyproject.toml")

    assert pyproject["project"]["version"] == __version__


def test_uv_dev_dependency_group_is_exact():
    pyproject = _load_toml("pyproject.toml")

    _assert_semantic_members(
        pyproject["dependency-groups"]["dev"],
        EXPECTED_DEV_GROUP,
    )


def test_uv_default_group_is_exact():
    pyproject = _load_toml("pyproject.toml")

    assert pyproject["tool"]["uv"]["default-groups"] == ["dev"]


def test_pytest_scope_is_exact():
    pyproject = _load_toml("pyproject.toml")

    pytest_config = pyproject["tool"]["pytest"]["ini_options"]
    assert pytest_config["testpaths"] == ["tests"]


def test_pytest_import_paths_are_exact():
    pyproject = _load_toml("pyproject.toml")

    pytest_config = pyproject["tool"]["pytest"]["ini_options"]
    assert pytest_config["pythonpath"] == [".", "src"]


def test_pep639_license_expression_is_exact():
    pyproject = _load_toml("pyproject.toml")

    assert pyproject["project"]["license"] == "MIT"


def test_pep639_license_files_are_exact():
    pyproject = _load_toml("pyproject.toml")

    assert pyproject["project"]["license-files"] == ["LICENSE"]


def test_deprecated_license_classifier_is_absent():
    pyproject = _load_toml("pyproject.toml")

    assert (
        DEPRECATED_LICENSE_CLASSIFIER
        not in pyproject["project"]["classifiers"]
    )


def test_build_backend_is_exact():
    pyproject = _load_toml("pyproject.toml")

    assert (
        pyproject["build-system"]["build-backend"]
        == "setuptools.build_meta"
    )


def test_setuptools_build_requirement_floor_is_exact():
    pyproject = _load_toml("pyproject.toml")

    _assert_semantic_members(
        pyproject["build-system"]["requires"],
        ["setuptools>=77", "wheel"],
    )


def test_supported_python_classifiers_cover_310_through_314():
    pyproject = _load_toml("pyproject.toml")

    python_minor_classifiers = {
        classifier
        for classifier in pyproject["project"]["classifiers"]
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", classifier)
    }
    assert python_minor_classifiers == EXPECTED_PYTHON_CLASSIFIERS


def test_lock_project_version_matches_package():
    project = _project_lock_entry()

    assert project["version"] == __version__


def test_lock_dev_dependency_resolution_matches_pyproject():
    project = _project_lock_entry()

    _assert_semantic_members(
        project["dev-dependencies"]["dev"],
        EXPECTED_LOCK_DEV_DEPENDENCIES,
    )

    lock = _load_toml("uv.lock")
    locked_names = {package["name"] for package in lock["package"]}
    assert {
        dependency["name"]
        for dependency in EXPECTED_LOCK_DEV_DEPENDENCIES
    } <= locked_names


def test_lock_dev_dependency_metadata_matches_pyproject():
    project = _project_lock_entry()

    _assert_semantic_members(
        project["metadata"]["requires-dev"]["dev"],
        EXPECTED_LOCK_DEV_METADATA,
    )


def test_ci_python_matrix_is_exact():
    with (ROOT / ".github/workflows/tests.yml").open(encoding="utf-8") as fh:
        workflow = fh.read()

    pytest_job = _workflow_job(workflow, "pytest")
    assert _workflow_matrix(pytest_job) == {
        "python-version": [
            "3.10",
            "3.11",
            "3.12",
            "3.13",
            "3.14",
        ]
    }


def test_ci_pytest_steps_are_exact():
    with (ROOT / ".github/workflows/tests.yml").open(encoding="utf-8") as fh:
        workflow = fh.read()

    pytest_job = _workflow_job(workflow, "pytest")
    assert _workflow_steps(pytest_job) == EXPECTED_CI_PYTEST_STEPS
