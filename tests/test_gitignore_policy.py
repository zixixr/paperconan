from pathlib import Path
import shutil
import subprocess

import pytest

from paperconan._input import SUPPORTED_INPUT_EXTS


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_repo(tmp_path):
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.ignorecase", "false"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.excludesFile", "/dev/null"],
        cwd=tmp_path,
        check=True,
    )
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    return tmp_path


def _ignored(repo, path):
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path],
        cwd=repo,
        check=False,
    )
    assert proc.returncode in (0, 1), path
    return proc.returncode == 0


def _extension_spellings(ext):
    return (ext, ext.upper(), ext[0].upper() + ext[1:])


def test_supported_local_inputs_are_ignored_case_insensitively(isolated_repo):
    for ext in SUPPORTED_INPUT_EXTS:
        for spelling in _extension_spellings(ext):
            path = f"local/source.{spelling}"
            assert _ignored(isolated_repo, path), path


def test_local_scan_and_review_artifacts_are_ignored(isolated_repo):
    for path in (
        "local/audit/scan.json",
        "local/scan.json",
        "local/verdict.json",
        "local/REPORT.md",
        "local/report.html",
        "local/paperconan_source.json",
        "local/adjudicated-report.html",
        "local/adjudication.html",
        "local/review.json",
        "local/.paperconan_source.json.worker.part",
        "local/.PAPERCONAN_SOURCE.JSON.worker.PART",
    ):
        assert _ignored(isolated_repo, path), path


def test_public_test_and_example_paths_are_not_ignored(isolated_repo):
    for public_root in ("tests/fixtures", "examples/demo_paper"):
        for ext in SUPPORTED_INPUT_EXTS:
            path = f"{public_root}/source.{ext.upper()}"
            assert not _ignored(isolated_repo, path), path
        for artifact in (
            "audit/scan.json",
            "scan.json",
            "verdict.json",
            "REPORT.md",
            "report.html",
            "adjudicated-report.html",
            "adjudication.html",
            "review.json",
            "paperconan_source.json",
            ".paperconan_source.json.worker.part",
        ):
            path = f"{public_root}/{artifact}"
            assert not _ignored(isolated_repo, path), path


def test_existing_private_and_generated_rules_remain_effective(isolated_repo):
    for path in (
        "recheck/local/source.csv",
        "batches/local/review.json",
        ".worktrees/feature/source.xlsx",
        ".venv/bin/python",
        "venv/bin/python",
        "env/bin/python",
        "src/__pycache__/module.cpython-312.pyc",
        ".pytest_cache/v/cache/nodeids",
        ".mypy_cache/3.12/cache.json",
        ".ruff_cache/content",
        ".idea/workspace.xml",
        ".vscode/settings.json",
        ".DS_Store",
        "paperconan-skill.zip",
        "tests/__pycache__/test_policy.cpython-312.pyc",
        "tests/.pytest_cache/v/cache/nodeids",
        "tests/.venv/bin/python",
        "tests/.idea/workspace.xml",
        "tests/.DS_Store",
        "examples/demo_paper/__pycache__/build.cpython-312.pyc",
        "examples/demo_paper/.ruff_cache/content",
        "examples/demo_paper/paperconan-skill.zip",
    ):
        assert _ignored(isolated_repo, path), path
