from __future__ import annotations

import ast
import base64
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import warnings
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 lacks the stdlib tomllib
    import tomli as tomllib

import pytest

from paperconan import __version__

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ARCHIVE_ROOTS = (
    ".github",
    "docs",
    "examples",
    "scripts",
    "skills",
    "src",
    "tests",
    ".gitignore",
    ".python-version",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "build_skill_zip.sh",
    "pyproject.toml",
    "uv.lock",
)
SDIST_GENERATED_METADATA = {
    "PKG-INFO",
    "setup.cfg",
    "src/paperconan.egg-info/SOURCES.txt",
}
WHEEL_DIST_INFO_MEMBERS = {
    "METADATA",
    "WHEEL",
    "entry_points.txt",
    "licenses/LICENSE",
    "top_level.txt",
    "RECORD",
}


def _copied_source_build_command(dist):
    return [
        sys.executable,
        "-m",
        "build",
        "--no-isolation",
        "--sdist",
        "--outdir",
        str(dist),
    ]


EXPECTED_DEV_GROUP = [
    "pytest>=8",
    "build>=1.2",
    "setuptools>=77",
    "wheel",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "xlwt>=1.3",
    "tomli>=2; python_version < '3.11'",
]
EXPECTED_PIP_EXTRA = [
    "pytest>=8",
    "build>=1.2",
    "setuptools>=77",
    "wheel",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "xlwt>=1.3",
    "tomli>=2; python_version < '3.11'",
]
EXPECTED_LOCK_PIP_EXTRA_DEPENDENCIES = [
    {"name": "build"},
    {"name": "pdfplumber"},
    {"name": "pytest"},
    {"name": "python-docx"},
    {"name": "setuptools"},
    {"name": "tomli", "marker": "python_full_version < '3.11'"},
    {"name": "wheel"},
    {"name": "xlwt"},
]
EXPECTED_LOCK_DEV_DEPENDENCIES = [
    {"name": "build"},
    {"name": "pdfplumber"},
    {"name": "pytest"},
    {"name": "python-docx"},
    {"name": "setuptools"},
    {"name": "tomli", "marker": "python_full_version < '3.11'"},
    {"name": "wheel"},
    {"name": "xlwt"},
]
EXPECTED_LOCK_DEV_METADATA = [
    {"name": "build", "specifier": ">=1.2"},
    {"name": "pdfplumber", "specifier": ">=0.11"},
    {"name": "pytest", "specifier": ">=8"},
    {"name": "python-docx", "specifier": ">=1.1"},
    {"name": "setuptools", "specifier": ">=77"},
    {
        "name": "tomli",
        "marker": "python_full_version < '3.11'",
        "specifier": ">=2",
    },
    {"name": "wheel"},
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
SDIST_CHECKOUT_STEP = """\
    - name: Checkout source
      uses: actions/checkout@v4
      with:
        fetch-depth: "1"
"""
SDIST_SETUP_STEP = """\
    - name: Configure uv
      uses: astral-sh/setup-uv@v6
      with:
        python-version: "3.14"
        enable-cache: "true"
"""
SDIST_BUILD_STEP = """\
    - name: Build source distribution
      run: |
        uv   build   --sdist
"""
SDIST_EXTRACT_STEP = """\
    - name: Extract source distribution
      run: >
        archive=$(find dist -maxdepth 1 -type f
        -name 'paperconan-*.tar.gz' -print -quit);
        test -n "$archive";
        mkdir sdist-root;
        tar -xzf "$archive" -C sdist-root --strip-components=1
"""
SDIST_VENV_STEP = """\
    - name: Create isolated environment
      working-directory: sdist-root
      run: |
        uv venv --python 3.14 .venv
"""
SDIST_INSTALL_STEP = """\
    - name: Install unpacked tests
      run: >
        uv pip install --python .venv/bin/python
        ".[test]"
      working-directory: sdist-root
"""
SDIST_TEST_STEP = """\
    - name: Test unpacked source
      working-directory: sdist-root
      run: |
        .venv/bin/python    -m pytest -q
"""
SDIST_POST_TEST_STEP = """\
    - name: Publish summary
      run: echo "sdist verification complete"
"""
SDIST_POST_TEST_COPY_OUT_STEP = """\
    - name: Copy test result for upload
      run: cp sdist-root/test-results.xml artifact/test-results.xml
"""
SDIST_POST_TEST_ARTIFACT_STEP = """\
    - name: Upload test result
      uses: actions/upload-artifact@v4
      with:
        name: sdist-test-result
        path: artifact/test-results.xml
"""
SDIST_POST_TEST_WORKSPACE_COPY_OUT_STEP = """\
    - name: Copy workspace-relative result for upload
      working-directory: ${{ github.workspace }}
      run: cp sdist-root/test-results.xml artifact/workspace-test-results.xml
"""
SDIST_POST_TEST_OTHER_CHECKOUT_STEP = """\
    - name: Checkout artifact metadata
      uses: actions/checkout@v3
      with:
        path: artifact-source
"""
EQUIVALENT_SDIST_JOB = (
    "sdist:\n"
    "  runs-on: ubuntu-latest\n"
    "  steps:\n"
    + SDIST_CHECKOUT_STEP
    + SDIST_SETUP_STEP
    + SDIST_BUILD_STEP
    + SDIST_EXTRACT_STEP
    + SDIST_VENV_STEP
    + SDIST_INSTALL_STEP
    + SDIST_TEST_STEP
    + SDIST_POST_TEST_STEP
    + SDIST_POST_TEST_COPY_OUT_STEP
    + SDIST_POST_TEST_ARTIFACT_STEP
    + SDIST_POST_TEST_WORKSPACE_COPY_OUT_STEP
    + SDIST_POST_TEST_OTHER_CHECKOUT_STEP
)


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


def _expected_lock_optional_metadata(extra):
    return [
        {
            "name": "build",
            "marker": f"extra == '{extra}'",
            "specifier": ">=1.2",
        },
        {
            "name": "pdfplumber",
            "marker": f"extra == '{extra}'",
            "specifier": ">=0.11",
        },
        {
            "name": "pytest",
            "marker": f"extra == '{extra}'",
            "specifier": ">=8",
        },
        {
            "name": "python-docx",
            "marker": f"extra == '{extra}'",
            "specifier": ">=1.1",
        },
        {
            "name": "setuptools",
            "marker": f"extra == '{extra}'",
            "specifier": ">=77",
        },
        {
            "name": "tomli",
            "marker": (
                "python_full_version < '3.11' "
                f"and extra == '{extra}'"
            ),
            "specifier": ">=2",
        },
        {
            "name": "wheel",
            "marker": f"extra == '{extra}'",
        },
        {
            "name": "xlwt",
            "marker": f"extra == '{extra}'",
            "specifier": ">=1.3",
        },
    ]


def _tracked_public_files(root=ROOT):
    try:
        repository = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            cwd=root,
            text=True,
        )
    except OSError:
        return None
    if repository.returncode != 0:
        return None
    if Path(repository.stdout.strip()).resolve() != root.resolve():
        return None

    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", *PUBLIC_ARCHIVE_ROOTS],
        check=True,
        capture_output=True,
        cwd=root,
        text=True,
    )
    return {
        name
        for name in tracked.stdout.split("\0")
        if name
    }


def _sdist_allowlist(root=ROOT):
    return {
        line.removeprefix("include ").strip()
        for line in (root / "MANIFEST.in").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.startswith("include ")
    }


def _validated_archive_member(name, seen):
    assert name
    assert "\\" not in name
    normalized = name[:-1] if name.endswith("/") else name
    assert normalized
    path = PurePosixPath(normalized)
    assert not path.is_absolute()
    assert path.as_posix() == normalized
    assert all(part not in {"", ".", ".."} for part in path.parts)
    assert normalized not in seen, (
        f"duplicate archive member: {normalized}"
    )
    seen.add(normalized)
    return path.parts


def _archive_parent_directories(paths):
    directories = set()
    for name in paths:
        parts = PurePosixPath(name).parts
        for stop in range(1, len(parts)):
            directories.add(
                PurePosixPath(*parts[:stop]).as_posix()
            )
    return directories


def _read_expected_payload(stream, expected_size):
    payload = stream.read(expected_size + 1)
    assert len(payload) == expected_size
    return payload


def _sdist_payloads(
    path, expected_payloads, generated_metadata=()
):
    if hasattr(expected_payloads, "items"):
        expected_payloads = dict(expected_payloads)
    else:
        expected_payloads = {
            name: None for name in expected_payloads
        }
    generated_metadata = set(generated_metadata)
    expected_files = set(expected_payloads) | generated_metadata
    expected_root = f"paperconan-{__version__}"
    allowed_directories = _archive_parent_directories(expected_files)
    seen = set()
    roots = set()
    payloads = {}
    file_members = {}
    root_directory_seen = False

    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = _validated_archive_member(member.name, seen)
            roots.add(parts[0])
            assert member.isfile() or member.isdir(), member.name
            if len(parts) == 1:
                assert member.isdir(), member.name
                root_directory_seen = True
                continue
            assert parts[0] == expected_root, member.name
            relative = PurePosixPath(*parts[1:]).as_posix()
            if member.isdir():
                assert relative in allowed_directories, member.name
                continue
            assert relative not in file_members
            file_members[relative] = member

        assert root_directory_seen
        assert roots == {expected_root}
        assert set(file_members) == expected_files

        for relative, member in file_members.items():
            expected = expected_payloads.get(relative)
            if expected is None:
                if relative in generated_metadata:
                    payloads[relative] = None
                    continue
                expected_size = member.size
            else:
                expected_size = len(expected)
                assert member.size == expected_size, member.name
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                payload = _read_expected_payload(
                    stream, expected_size
                )
            if expected is not None:
                assert payload == expected
            payloads[relative] = payload
    return payloads


def _wheel_payloads(path, expected_payloads=None):
    expected_payloads = (
        None
        if expected_payloads is None
        else dict(expected_payloads)
    )
    seen = set()
    payloads = {}
    file_members = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            parts = _validated_archive_member(
                member.filename,
                seen,
            )
            assert not member.is_dir(), member.filename
            mode = member.external_attr >> 16
            assert mode == 0 or stat.S_ISREG(mode), member.filename
            normalized = PurePosixPath(*parts).as_posix()
            file_members[normalized] = member
        if expected_payloads is not None:
            assert set(file_members) == set(expected_payloads)
        for normalized, member in file_members.items():
            expected = (
                None
                if expected_payloads is None
                else expected_payloads[normalized]
            )
            expected_size = (
                member.file_size
                if expected is None
                else len(expected)
            )
            if expected is not None:
                assert member.file_size == expected_size, normalized
            with archive.open(member) as stream:
                payload = _read_expected_payload(
                    stream, expected_size
                )
            if expected is not None:
                assert payload == expected
            payloads[normalized] = payload
    return payloads


def _record_hash(payload):
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode(
        "ascii"
    )


def _assert_wheel_record(payloads, record_name):
    rows = csv.reader(
        io.StringIO(
            payloads[record_name].decode("utf-8"),
            newline="",
        )
    )
    records = {}
    record_seen = set()
    for row in rows:
        assert len(row) == 3, row
        name, digest, size = row
        _validated_archive_member(name, record_seen)
        records[name] = (digest, size)

    assert set(records) == set(payloads)
    for name, payload in payloads.items():
        digest, size = records[name]
        if name == record_name:
            assert digest == ""
            assert size == ""
            continue
        assert digest == f"sha256={_record_hash(payload)}"
        assert size == str(len(payload))


def _assert_exact_wheel_members(payloads, expected_wheel):
    dist_info_root = f"paperconan-{__version__}.dist-info"
    expected_metadata = {
        f"{dist_info_root}/{relative}"
        for relative in WHEEL_DIST_INFO_MEMBERS
    }
    assert set(payloads) == set(expected_wheel) | expected_metadata
    return dist_info_root


def _assert_built_release_archives(dist=ROOT / "dist"):
    sdists = sorted(dist.glob("paperconan-*.tar.gz"))
    wheels = sorted(dist.glob("paperconan-*.whl"))
    assert len(sdists) == 1, sdists
    assert len(wheels) == 1, wheels

    expected_sdist = _sdist_allowlist()
    expected_sdist_payloads = {
        relative: (ROOT / relative).read_bytes()
        for relative in expected_sdist
    }
    sdist_payloads = _sdist_payloads(
        sdists[0],
        expected_sdist_payloads,
        generated_metadata=SDIST_GENERATED_METADATA,
    )
    assert set(sdist_payloads) == (
        expected_sdist | SDIST_GENERATED_METADATA
    )
    for relative in expected_sdist:
        assert sdist_payloads[relative] == expected_sdist_payloads[
            relative
        ]

    expected_wheel = {
        relative.removeprefix("src/"): (
            ROOT / relative
        ).read_bytes()
        for relative in expected_sdist
        if relative.startswith("src/paperconan/")
    }
    dist_info_root = f"paperconan-{__version__}.dist-info"
    license_name = f"{dist_info_root}/licenses/LICENSE"
    wheel_expected_payloads = {
        **expected_wheel,
        **{
            f"{dist_info_root}/{relative}": None
            for relative in WHEEL_DIST_INFO_MEMBERS
        },
        license_name: (ROOT / "LICENSE").read_bytes(),
    }
    wheel_payloads = _wheel_payloads(
        wheels[0], wheel_expected_payloads
    )
    package_payloads = {
        name: payload
        for name, payload in wheel_payloads.items()
        if name.startswith("paperconan/")
    }
    assert package_payloads == expected_wheel

    assert _assert_exact_wheel_members(
        wheel_payloads,
        expected_wheel,
    ) == dist_info_root
    assert (
        wheel_payloads[license_name]
        == (ROOT / "LICENSE").read_bytes()
    )
    record_name = f"{dist_info_root}/RECORD"
    _assert_wheel_record(wheel_payloads, record_name)


def _replace_sdist_job(old, new=""):
    assert old in EQUIVALENT_SDIST_JOB
    return EQUIVALENT_SDIST_JOB.replace(old, new, 1)


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

    return [_workflow_step(block) for block in blocks]


def _workflow_step(block):
    list_indent = len(block[0]) - len(block[0].lstrip())
    key_indent = list_indent + 2
    lines = [
        (" " * key_indent) + block[0].lstrip()[2:],
        *block[1:],
    ]
    step = {}
    section = None
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent == key_indent:
            key, value = line.strip().split(":", 1)
            value = value.strip()
            if re.fullmatch(r"[|>][+-]?", value):
                step[key], index = _workflow_block_scalar(
                    lines,
                    index + 1,
                    key_indent,
                    value,
                )
                section = None
                continue
            if value:
                step[key] = _yaml_scalar(value)
                section = None
            else:
                step[key] = {}
                section = key
            index += 1
            continue
        if indent == key_indent + 2 and section is not None:
            key, value = line.strip().split(":", 1)
            step[section][key] = _yaml_scalar(value.strip())
            index += 1
            continue
        raise AssertionError("unsupported workflow step presentation")

    step.pop("name", None)
    return step


def _workflow_block_scalar(lines, index, parent_indent, style):
    content = []
    content_indent = None

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            content.append("")
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        if content_indent is None:
            content_indent = indent
        assert indent >= content_indent
        content.append(line[content_indent:])
        index += 1

    if style.startswith(">"):
        value = " ".join(item.strip() for item in content if item.strip())
    else:
        value = "\n".join(content).strip()
    return value, index


def _normalize_shell(command):
    lines = [
        " ".join(line.split())
        for line in command.splitlines()
        if line.strip()
    ]
    return " ; ".join(lines)


def _shell_tokens(command):
    try:
        return shlex.split(command)
    except ValueError as exc:
        raise AssertionError(f"invalid shell command: {command}") from exc


def _without_option(tokens, option, expected_value):
    tokens = list(tokens)
    assert tokens.count(option) == 1
    index = tokens.index(option)
    assert index + 1 < len(tokens)
    assert tokens[index + 1] == expected_value
    del tokens[index:index + 2]
    return tokens


def _assert_safe_pretest_command(command):
    assert not re.search(
        r"(^|[;&|]\s*)(?:sudo\s+)?(?:cp|rsync)\s",
        command,
    ), "checkout-copy command before pytest"
    forbidden_sources = (
        "../",
        "$GITHUB_WORKSPACE",
        "${{ github.workspace }}",
        "/github/workspace",
    )
    assert not any(source in command for source in forbidden_sources), (
        "parent or workspace source before pytest"
    )


def _workspace_relative_path(path):
    workspace_roots = (
        "${{ github.workspace }}",
        "$GITHUB_WORKSPACE",
        "/github/workspace",
    )
    for root in workspace_roots:
        if path in (root, f"{root}/"):
            return ".", True
        prefix = f"{root}/"
        if path.startswith(prefix):
            return path[len(prefix):], True
    return path, False


def _targets_sdist_root(path, working_directory=None):
    working_directory, _ = _workspace_relative_path(
        working_directory or "."
    )
    path, workspace_relative = _workspace_relative_path(path)
    path = posixpath.normpath(
        posixpath.join(
            "." if workspace_relative else working_directory,
            path,
        )
    )
    return path == "sdist-root" or path.startswith("sdist-root/")


def _assert_no_copy_into_sdist_root(command, working_directory=None):
    for segment in re.split(r"\s*(?:;|&&|\|\|)\s*", command):
        if not segment:
            continue
        tokens = _shell_tokens(segment)
        if tokens[:1] == ["sudo"]:
            tokens = tokens[1:]
        if tokens[:1] not in (["cp"], ["rsync"]):
            continue
        assert len(tokens) >= 3
        assert not _targets_sdist_root(tokens[-1], working_directory), (
            "copy destination inside sdist-root"
        )


def _checkout_path(step):
    config = step.get("with", {})
    assert isinstance(config, dict)
    return config.get("path")


def _assert_safe_posttest_step(step):
    action = step.get("uses", "")
    if action.startswith("actions/checkout@"):
        path = _checkout_path(step)
        assert path is None or not _targets_sdist_root(path), (
            "checkout destination inside sdist-root"
        )
    if "run" in step:
        _assert_no_copy_into_sdist_root(
            _normalize_shell(step["run"]),
            step.get("working-directory"),
        )


def _sdist_run_operation(step):
    assert set(step) <= {"run", "working-directory"}
    command = _normalize_shell(step["run"])
    working_directory = step.get("working-directory")
    _assert_safe_pretest_command(command)
    tokens = _shell_tokens(command)

    if tokens[:2] == ["uv", "build"]:
        assert working_directory in (None, ".")
        assert tokens[2:] == ["--sdist"]
        return "build"

    if command.startswith("archive=$("):
        assert working_directory in (None, ".")
        segments = [
            segment.strip()
            for segment in re.split(r"\s*(?:;|&&)\s*", command)
            if segment.strip()
        ]
        assert len(segments) == 4
        assert segments[0].startswith("archive=$(find dist ")
        assert "paperconan-*.tar.gz" in segments[0]
        assert "-print -quit)" in segments[0]
        assert _shell_tokens(segments[1]) == ["test", "-n", "$archive"]
        assert _shell_tokens(segments[2]) in (
            ["mkdir", "sdist-root"],
            ["mkdir", "-p", "sdist-root"],
        )
        tar_tokens = _shell_tokens(segments[3])
        assert tar_tokens[:5] == [
            "tar",
            "-xzf",
            "$archive",
            "-C",
            "sdist-root",
        ]
        assert tar_tokens[5:] in (
            ["--strip-components=1"],
            ["--strip-components", "1"],
        )
        return "extract"

    if tokens[:2] == ["uv", "venv"]:
        assert working_directory == "sdist-root"
        remaining = _without_option(tokens[2:], "--python", "3.14")
        assert remaining == [".venv"]
        return "venv"

    if tokens[:3] == ["uv", "pip", "install"]:
        assert working_directory == "sdist-root"
        remaining = _without_option(
            tokens[3:],
            "--python",
            ".venv/bin/python",
        )
        assert remaining == [".[test]"]
        return "install"

    if "pytest" in tokens:
        assert working_directory == "sdist-root"
        assert tokens == [".venv/bin/python", "-m", "pytest", "-q"]
        return "test"

    return None


def _assert_sdist_job_invariants(job):
    operations = {}
    test_complete = False

    for index, step in enumerate(_workflow_steps(job)):
        if test_complete:
            _assert_safe_posttest_step(step)
            continue

        action = step.get("uses")
        if action is not None:
            assert set(step) <= {"uses", "with"}
            config = step.get("with", {})
            if action == "actions/checkout@v4":
                assert _checkout_path(step) in (None, ".", "./")
                operation = "checkout"
            elif action == "astral-sh/setup-uv@v6":
                assert config.get("python-version") == "3.14"
                operation = "setup"
            else:
                raise AssertionError("unknown action before pytest")
        else:
            assert "run" in step, "unknown step before pytest"
            operation = _sdist_run_operation(step)
            assert operation is not None, "unknown command before pytest"

        assert operation not in operations, f"duplicate {operation}"
        operations[operation] = index
        if operation == "test":
            test_complete = True

    required = (
        "checkout",
        "setup",
        "build",
        "extract",
        "venv",
        "install",
        "test",
    )
    assert set(required) <= operations.keys()
    assert [operations[name] for name in required] == sorted(
        operations[name]
        for name in required
    )


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


@pytest.mark.parametrize("extra", ["test", "dev"])
def test_lock_optional_extra_resolution_matches_pyproject(extra):
    project = _project_lock_entry()

    _assert_semantic_members(
        project["optional-dependencies"][extra],
        EXPECTED_LOCK_PIP_EXTRA_DEPENDENCIES,
    )

    lock = _load_toml("uv.lock")
    locked_names = {package["name"] for package in lock["package"]}
    assert {
        dependency["name"]
        for dependency in EXPECTED_LOCK_PIP_EXTRA_DEPENDENCIES
    } <= locked_names


@pytest.mark.parametrize("extra", ["test", "dev"])
def test_lock_optional_extra_metadata_matches_pyproject(extra):
    project = _project_lock_entry()
    marker = f"extra == '{extra}'"
    metadata = [
        item
        for item in project["metadata"]["requires-dist"]
        if marker in item.get("marker", "")
    ]

    _assert_semantic_members(
        metadata,
        _expected_lock_optional_metadata(extra),
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


def test_ci_sdist_steps_build_and_test_only_from_unpacked_root():
    with (ROOT / ".github/workflows/tests.yml").open(encoding="utf-8") as fh:
        workflow = fh.read()

    sdist_job = _workflow_job(workflow, "sdist")
    _assert_sdist_job_invariants(sdist_job)


def test_sdist_job_invariants_accept_equivalent_block_commands():
    _assert_sdist_job_invariants(EQUIVALENT_SDIST_JOB)


@pytest.mark.parametrize(
    ("case", "job"),
    [
        ("missing checkout", _replace_sdist_job(SDIST_CHECKOUT_STEP)),
        ("missing setup", _replace_sdist_job(SDIST_SETUP_STEP)),
        (
            "checkout into sdist root",
            _replace_sdist_job(
                '        fetch-depth: "1"\n',
                (
                    '        fetch-depth: "1"\n'
                    "        path: sdist-root\n"
                ),
            ),
        ),
        (
            "setup missing python version",
            _replace_sdist_job(
                '        python-version: "3.14"\n',
                "",
            ),
        ),
        (
            "setup wrong python version",
            _replace_sdist_job(
                '        python-version: "3.14"\n',
                '        python-version: "3.13"\n',
            ),
        ),
        ("missing build", _replace_sdist_job(SDIST_BUILD_STEP)),
        ("missing extraction", _replace_sdist_job(SDIST_EXTRACT_STEP)),
        ("missing venv", _replace_sdist_job(SDIST_VENV_STEP)),
        ("missing install", _replace_sdist_job(SDIST_INSTALL_STEP)),
        ("missing test", _replace_sdist_job(SDIST_TEST_STEP)),
        (
            "install outside root",
            _replace_sdist_job(
                SDIST_INSTALL_STEP,
                SDIST_INSTALL_STEP.replace(
                    "      working-directory: sdist-root\n",
                    "",
                ),
            ),
        ),
        (
            "pytest outside root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                SDIST_TEST_STEP.replace(
                    "working-directory: sdist-root",
                    "working-directory: .",
                ),
            ),
        ),
        (
            "install from parent",
            _replace_sdist_job('        ".[test]"\n', '        "../.[test]"\n'),
        ),
        (
            "copy checkout into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    "    - name: Copy checkout file\n"
                    "      run: cp README.md sdist-root/README.md\n"
                    + SDIST_TEST_STEP
                ),
            ),
        ),
        (
            "wrong test extra",
            _replace_sdist_job('        ".[test]"\n', '        ".[dev]"\n'),
        ),
        (
            "wrong install interpreter",
            _replace_sdist_job(
                SDIST_INSTALL_STEP,
                SDIST_INSTALL_STEP.replace(
                    ".venv/bin/python",
                    ".venv/bin/python3",
                ),
            ),
        ),
        (
            "wrong pytest interpreter",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                SDIST_TEST_STEP.replace(
                    ".venv/bin/python",
                    "python",
                ),
            ),
        ),
        (
            "unknown pre-test command",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    "    - name: Unverified preparation\n"
                    "      run: echo preparing\n"
                    + SDIST_TEST_STEP
                ),
            ),
        ),
        (
            "operations out of order",
            _replace_sdist_job(
                SDIST_VENV_STEP + SDIST_INSTALL_STEP,
                SDIST_INSTALL_STEP + SDIST_VENV_STEP,
            ),
        ),
        (
            "post-test copy into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Replace unpacked README\n"
                    + "      run: cp README.md sdist-root/README.md\n"
                ),
            ),
        ),
        (
            "post-test rsync into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Sync checkout file into root\n"
                    + "      run: rsync README.md sdist-root/\n"
                ),
            ),
        ),
        (
            "post-test checkout into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Checkout over unpacked root\n"
                    + "      uses: actions/checkout@v4\n"
                    + "      with:\n"
                    + "        path: sdist-root\n"
                ),
            ),
        ),
        (
            "post-test expression workspace copy into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Copy from expression workspace\n"
                    + "      working-directory: ${{ github.workspace }}\n"
                    + "      run: cp README.md sdist-root/README.md\n"
                ),
            ),
        ),
        (
            "post-test environment workspace copy into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Copy from environment workspace\n"
                    + "      working-directory: $GITHUB_WORKSPACE\n"
                    + "      run: cp README.md sdist-root/README.md\n"
                ),
            ),
        ),
        (
            "post-test runner workspace copy into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Copy from runner workspace\n"
                    + "      working-directory: /github/workspace\n"
                    + "      run: cp README.md sdist-root/README.md\n"
                ),
            ),
        ),
        (
            "post-test checkout v3 into root",
            _replace_sdist_job(
                SDIST_TEST_STEP,
                (
                    SDIST_TEST_STEP
                    + "    - name: Checkout v3 over unpacked root\n"
                    + "      uses: actions/checkout@v3\n"
                    + "      with:\n"
                    + "        path: sdist-root\n"
                ),
            ),
        ),
    ],
    ids=[
        "missing-checkout",
        "missing-setup",
        "checkout-into-sdist-root",
        "setup-missing-python-version",
        "setup-wrong-python-version",
        "missing-build",
        "missing-extraction",
        "missing-venv",
        "missing-install",
        "missing-test",
        "install-outside-root",
        "pytest-outside-root",
        "install-from-parent",
        "copy-checkout-into-root",
        "wrong-test-extra",
        "wrong-install-interpreter",
        "wrong-pytest-interpreter",
        "unknown-pre-test-command",
        "operations-out-of-order",
        "post-test-copy-into-root",
        "post-test-rsync-into-root",
        "post-test-checkout-into-root",
        "post-test-expression-workspace-copy-into-root",
        "post-test-environment-workspace-copy-into-root",
        "post-test-runner-workspace-copy-into-root",
        "post-test-checkout-v3-into-root",
    ],
)
def test_sdist_job_invariants_reject_contract_mutations(case, job):
    with pytest.raises(AssertionError, match="."):
        _assert_sdist_job_invariants(job)


def test_tracked_public_files_requires_exact_repository_root():
    assert _tracked_public_files(ROOT / "tests") is None


def test_sdist_allowlist_matches_tracked_public_files():
    tracked_public = _tracked_public_files()
    if tracked_public is None:
        return
    assert _sdist_allowlist() == tracked_public


def test_sdist_includes_shared_sidecar_module():
    assert (
        "src/paperconan/_source_sidecar.py"
        in _sdist_allowlist()
    )


def test_sdist_includes_detector_resource_module():
    assert "src/paperconan/_resources.py" in _sdist_allowlist()


def test_copied_source_build_command_disables_isolation(tmp_path):
    command = _copied_source_build_command(tmp_path / "dist")

    assert command[:3] == [sys.executable, "-m", "build"]
    assert "--no-isolation" in command


def test_sdist_contains_test_and_skill_closure(tmp_path):
    dist = tmp_path / "dist"
    probes = [
        ROOT / "docs" / "untracked-sdist-probe.md",
        ROOT / "examples" / "untracked-sdist-probe.py",
        ROOT / "skills" / "paperconan" / "untracked-sdist-probe.md",
        ROOT / "src" / "paperconan" / "untracked_sdist_probe.py",
        ROOT / "tests" / "untracked_sdist_probe.py",
    ]
    try:
        for probe in probes:
            probe.write_text("local-only probe\n", encoding="utf-8")
        result = subprocess.run(
            _copied_source_build_command(dist),
            check=True,
            capture_output=True,
            cwd=ROOT,
            text=True,
        )
    finally:
        for probe in probes:
            probe.unlink(missing_ok=True)
    warning_lines = [
        line
        for line in (*result.stdout.splitlines(), *result.stderr.splitlines())
        if "warning:" in line.lower()
    ]
    assert not warning_lines, "\n".join(warning_lines)

    archive = next(dist.glob("paperconan-*.tar.gz"))
    with tarfile.open(archive, "r:gz") as tf:
        names = {
            member.name.split("/", 1)[1]
            for member in tf.getmembers()
            if member.isfile() and "/" in member.name
        }

    assert names == _sdist_allowlist() | SDIST_GENERATED_METADATA

    assert not {probe.relative_to(ROOT).as_posix() for probe in probes} & names


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "../escape",
        "root/../escape",
        "./root/file",
        "root//file",
        r"root\file",
    ],
)
def test_archive_member_validation_rejects_unsafe_paths(name):
    with pytest.raises(AssertionError):
        _validated_archive_member(name, set())


def test_archive_member_validation_rejects_duplicates():
    seen = set()
    assert _validated_archive_member("root/file", seen) == (
        "root",
        "file",
    )
    with pytest.raises(AssertionError, match="duplicate"):
        _validated_archive_member("root/file", seen)


def _add_tar_directory(archive, name):
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    archive.addfile(member)


def _add_tar_file(archive, name, payload=b"x"):
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


@pytest.mark.parametrize(
    "malformation",
    ["multiple-root", "link", "extra-directory"],
)
def test_sdist_reader_rejects_structural_extras(
    tmp_path, malformation
):
    root = f"paperconan-{__version__}"
    path = tmp_path / f"{malformation}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_directory(archive, f"{root}/")
        _add_tar_file(archive, f"{root}/README.md")
        if malformation == "multiple-root":
            _add_tar_directory(archive, "other/")
        elif malformation == "link":
            member = tarfile.TarInfo(f"{root}/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "README.md"
            archive.addfile(member)
        else:
            _add_tar_directory(archive, f"{root}/unexpected/")

    with pytest.raises(AssertionError):
        _sdist_payloads(path, {"README.md"})


@pytest.mark.parametrize(
    ("malformation", "expected_message"),
    [
        pytest.param(
            "duplicate",
            "duplicate archive member",
            id="duplicate",
        ),
        pytest.param("directory", None, id="directory"),
        pytest.param("link", None, id="link"),
    ],
)
def test_wheel_reader_rejects_non_regular_or_duplicate_members(
    tmp_path, malformation, expected_message
):
    path = tmp_path / f"{malformation}.whl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            if malformation == "duplicate":
                for payload in (b"first", b"second"):
                    member = zipfile.ZipInfo("paperconan/module.py")
                    member.create_system = 3
                    member.external_attr = (
                        stat.S_IFREG | 0o644
                    ) << 16
                    archive.writestr(member, payload)
            elif malformation == "directory":
                archive.writestr("paperconan/", b"")
            else:
                member = zipfile.ZipInfo("paperconan/link")
                member.create_system = 3
                member.external_attr = (
                    stat.S_IFLNK | 0o777
                ) << 16
                archive.writestr(member, "target")

    with pytest.raises(AssertionError, match=expected_message):
        _wheel_payloads(path)


def test_exact_wheel_members_rejects_extra_metadata():
    dist_info_root = f"paperconan-{__version__}.dist-info"
    expected_wheel = {"paperconan/module.py": b"source"}
    payloads = {
        **expected_wheel,
        **{
            f"{dist_info_root}/{name}": b""
            for name in WHEEL_DIST_INFO_MEMBERS
        },
        f"{dist_info_root}/unexpected.json": b"extra",
    }

    with pytest.raises(AssertionError):
        _assert_exact_wheel_members(payloads, expected_wheel)


@pytest.mark.parametrize(
    "malformation",
    ["bad-hash", "bad-size", "duplicate"],
)
def test_wheel_record_rejects_invalid_rows(malformation):
    record_name = (
        f"paperconan-{__version__}.dist-info/RECORD"
    )
    if malformation == "bad-hash":
        rows = [
            "paperconan/module.py,sha256=incorrect,6",
            f"{record_name},,",
        ]
    elif malformation == "bad-size":
        digest = _record_hash(b"source")
        rows = [
            f"paperconan/module.py,sha256={digest},7",
            f"{record_name},,",
        ]
    else:
        digest = _record_hash(b"source")
        rows = [
            f"paperconan/module.py,sha256={digest},6",
            f"paperconan/module.py,sha256={digest},6",
            f"{record_name},,",
        ]
    record = ("\n".join(rows) + "\n").encode("utf-8")
    payloads = {
        "paperconan/module.py": b"source",
        record_name: record,
    }

    with pytest.raises(AssertionError):
        _assert_wheel_record(payloads, record_name)


def _add_wheel_file(archive, name, payload):
    member = zipfile.ZipInfo(name)
    member.create_system = 3
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(member, payload)


def _write_test_release_archives(dist, *, mutation=None):
    known_mutations = {
        "sdist-source-bytes",
        "wheel-source-bytes",
        "wheel-license-bytes",
        "sdist-missing-member",
        "sdist-extra-member",
        "wheel-missing-member",
        "wheel-extra-member",
        "record-missing-self",
        "record-self-hash",
        "record-self-size",
        "record-bad-hash",
        "record-bad-size",
    }
    assert mutation is None or mutation in known_mutations
    dist.mkdir()

    expected_sdist = _sdist_allowlist()
    sdist_payloads = {
        relative: (ROOT / relative).read_bytes()
        for relative in expected_sdist
    }
    sdist_payloads.update({
        relative: b"generated metadata\n"
        for relative in SDIST_GENERATED_METADATA
    })
    if mutation == "sdist-source-bytes":
        sdist_payloads["README.md"] += b"changed\n"
    elif mutation == "sdist-missing-member":
        del sdist_payloads["README.md"]
    elif mutation == "sdist-extra-member":
        sdist_payloads["unexpected.txt"] = b"extra\n"

    root = f"paperconan-{__version__}"
    sdist = dist / f"{root}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        _add_tar_directory(archive, f"{root}/")
        for relative, payload in sorted(sdist_payloads.items()):
            _add_tar_file(
                archive,
                f"{root}/{relative}",
                payload,
            )

    expected_wheel = {
        relative.removeprefix("src/"): (
            ROOT / relative
        ).read_bytes()
        for relative in expected_sdist
        if relative.startswith("src/paperconan/")
    }
    dist_info_root = f"paperconan-{__version__}.dist-info"
    wheel_payloads = dict(expected_wheel)
    wheel_payloads.update({
        f"{dist_info_root}/METADATA": b"Metadata-Version: 2.4\n",
        f"{dist_info_root}/WHEEL": b"Wheel-Version: 1.0\n",
        f"{dist_info_root}/entry_points.txt": b"[console_scripts]\n",
        f"{dist_info_root}/licenses/LICENSE": (
            ROOT / "LICENSE"
        ).read_bytes(),
        f"{dist_info_root}/top_level.txt": b"paperconan\n",
    })
    package_member = "paperconan/__init__.py"
    license_member = f"{dist_info_root}/licenses/LICENSE"
    if mutation == "wheel-source-bytes":
        wheel_payloads[package_member] += b"changed\n"
    elif mutation == "wheel-license-bytes":
        wheel_payloads[license_member] += b"changed\n"
    elif mutation == "wheel-missing-member":
        del wheel_payloads[package_member]
    elif mutation == "wheel-extra-member":
        wheel_payloads[
            f"{dist_info_root}/unexpected.json"
        ] = b"{}\n"

    record_name = f"{dist_info_root}/RECORD"
    record_rows = [
        [
            name,
            f"sha256={_record_hash(payload)}",
            str(len(payload)),
        ]
        for name, payload in sorted(wheel_payloads.items())
    ]
    self_row = [record_name, "", ""]
    if mutation == "record-self-hash":
        self_row[1] = "sha256=incorrect"
    elif mutation == "record-self-size":
        self_row[2] = "0"
    if mutation != "record-missing-self":
        record_rows.append(self_row)

    if mutation in {"record-bad-hash", "record-bad-size"}:
        row = next(
            item for item in record_rows
            if item[0] == package_member
        )
        if mutation == "record-bad-hash":
            row[1] = "sha256=incorrect"
        else:
            row[2] = str(int(row[2]) + 1)

    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(record_rows)
    wheel_payloads[record_name] = record.getvalue().encode(
        "utf-8"
    )

    wheel = dist / f"{root}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in sorted(wheel_payloads.items()):
            _add_wheel_file(archive, name, payload)
    return sdist, wheel


def test_built_release_archives_integrated_accepts_temporary_artifacts(
    tmp_path,
):
    dist = tmp_path / "dist"
    _write_test_release_archives(dist)

    _assert_built_release_archives(dist)


@pytest.mark.parametrize(
    ("archive_kind", "cardinality"),
    [
        ("sdist", "missing"),
        ("sdist", "extra"),
        ("wheel", "missing"),
        ("wheel", "extra"),
    ],
)
def test_built_release_archives_integrated_requires_exactly_one_archive(
    tmp_path, archive_kind, cardinality
):
    dist = tmp_path / "dist"
    sdist, wheel = _write_test_release_archives(dist)
    archive = sdist if archive_kind == "sdist" else wheel
    if cardinality == "missing":
        archive.unlink()
    else:
        suffix = ".tar.gz" if archive_kind == "sdist" else ".whl"
        duplicate = dist / f"paperconan-duplicate{suffix}"
        duplicate.write_bytes(archive.read_bytes())

    with pytest.raises(AssertionError):
        _assert_built_release_archives(dist)


@pytest.mark.parametrize(
    "mutation",
    [
        "sdist-source-bytes",
        "wheel-source-bytes",
        "wheel-license-bytes",
        "sdist-missing-member",
        "sdist-extra-member",
        "wheel-missing-member",
        "wheel-extra-member",
    ],
)
def test_built_release_archives_integrated_rejects_payload_or_members(
    tmp_path, mutation
):
    dist = tmp_path / "dist"
    _write_test_release_archives(dist, mutation=mutation)

    with pytest.raises(AssertionError):
        _assert_built_release_archives(dist)


@pytest.mark.parametrize(
    ("archive_kind", "mutation", "guarded_member"),
    [
        (
            "sdist",
            "sdist-extra-member",
            "unexpected.txt",
        ),
        (
            "sdist",
            "sdist-source-bytes",
            "README.md",
        ),
        (
            "wheel",
            "wheel-extra-member",
            "unexpected.json",
        ),
        (
            "wheel",
            "wheel-source-bytes",
            "paperconan/__init__.py",
        ),
    ],
)
def test_release_verifier_rejects_members_before_payload_read(
    tmp_path, monkeypatch, archive_kind, mutation, guarded_member
):
    class UnexpectedPayloadRead(BaseException):
        pass

    dist = tmp_path / "dist"
    _write_test_release_archives(dist, mutation=mutation)

    if archive_kind == "sdist":
        real_extractfile = tarfile.TarFile.extractfile

        def guarded_extractfile(archive, member):
            if member.name.endswith(guarded_member):
                raise UnexpectedPayloadRead(member.name)
            return real_extractfile(archive, member)

        monkeypatch.setattr(
            tarfile.TarFile, "extractfile", guarded_extractfile
        )
    else:
        real_read = zipfile.ZipFile.read

        def guarded_read(archive, member, *args, **kwargs):
            name = (
                member.filename
                if isinstance(member, zipfile.ZipInfo)
                else member
            )
            if name.endswith(guarded_member):
                raise UnexpectedPayloadRead(name)
            return real_read(archive, member, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)

    with pytest.raises(AssertionError):
        _assert_built_release_archives(dist)


def test_sdist_expected_payload_read_is_bounded(
    tmp_path, monkeypatch
):
    dist = tmp_path / "dist"
    _write_test_release_archives(dist)
    target = "README.md"
    expected_size = (ROOT / target).stat().st_size
    read_sizes = []
    real_extractfile = tarfile.TarFile.extractfile

    class GuardedStream:
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            read_sizes.append(size)
            assert 0 <= size <= expected_size + 1
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._stream.close()

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def guarded_extractfile(archive, member):
        stream = real_extractfile(archive, member)
        if member.name.endswith(target):
            return GuardedStream(stream)
        return stream

    monkeypatch.setattr(
        tarfile.TarFile, "extractfile", guarded_extractfile
    )

    _assert_built_release_archives(dist)

    assert read_sizes


def test_wheel_expected_payload_read_is_bounded(
    tmp_path, monkeypatch
):
    dist = tmp_path / "dist"
    _write_test_release_archives(dist)
    target = "paperconan/__init__.py"
    expected_size = (ROOT / "src" / target).stat().st_size
    read_sizes = []
    real_open = zipfile.ZipFile.open

    class GuardedStream:
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            read_sizes.append(size)
            assert 0 <= size <= expected_size + 1
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._stream.close()

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def guarded_open(archive, member, *args, **kwargs):
        stream = real_open(archive, member, *args, **kwargs)
        name = (
            member.filename
            if isinstance(member, zipfile.ZipInfo)
            else member
        )
        if name == target:
            return GuardedStream(stream)
        return stream

    monkeypatch.setattr(zipfile.ZipFile, "open", guarded_open)

    _assert_built_release_archives(dist)

    assert read_sizes


@pytest.mark.parametrize(
    "mutation",
    [
        "record-missing-self",
        "record-self-hash",
        "record-self-size",
        "record-bad-hash",
        "record-bad-size",
    ],
)
def test_built_release_archives_integrated_rejects_record_mutations(
    tmp_path, mutation
):
    dist = tmp_path / "dist"
    _write_test_release_archives(dist, mutation=mutation)

    with pytest.raises(AssertionError):
        _assert_built_release_archives(dist)
