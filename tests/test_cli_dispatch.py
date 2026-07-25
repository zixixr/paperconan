"""CLI dispatch: one subcommand tree, with `paperconan <dir>` still the default.

`main()` used to branch on `sys.argv[1]` once per subcommand, so every new
command added another special case and `--help` never mentioned any of them.
The commands now live in a single argparse subparser tree under one dispatch
rule: an argument that is not a known subcommand is the scan target.

That rule is what keeps `paperconan data/` working, so it is pinned here
alongside the ambiguity it creates — a directory literally named after a
subcommand.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from paperconan._audit import SUBCOMMANDS


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, "-m", "paperconan", *args],
        text=True, capture_output=True, **kw
    )


def _tiny_csv(d):
    d.mkdir(parents=True, exist_ok=True)
    rows = ["a,b"] + [f"{i},{i}" for i in range(6)]
    (d / "t.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return d


# ---------- the default path must not change ----------

def test_bare_directory_still_scans(tmp_path):
    data = _tiny_csv(tmp_path / "data")

    res = _run([str(data), "--out", str(tmp_path / "out"), "--no-html"])

    assert res.returncode == 0, res.stderr
    assert (tmp_path / "out" / "scan.json").exists()


def test_flags_may_precede_the_directory(tmp_path):
    """`paperconan --no-html data/` — the target is not argv[0] here."""
    data = _tiny_csv(tmp_path / "data")

    res = _run(["--no-html", str(data), "--out", str(tmp_path / "out")])

    assert res.returncode == 0, res.stderr
    assert (tmp_path / "out" / "scan.json").exists()


def test_version_still_works():
    res = _run(["--version"])

    assert res.returncode == 0, res.stderr
    assert "paperconan" in res.stdout


# ---------- the subcommand tree ----------

def test_help_lists_every_subcommand():
    res = _run(["--help"])

    assert res.returncode == 0, res.stderr
    for name in SUBCOMMANDS:
        assert name in res.stdout, f"{name} missing from --help"


# `paperconan <dir>` is the documented default invocation, so the bare help has
# to keep documenting its flags. Asserting only on subcommand names passed even
# when every scan flag had vanished from the help.
SCAN_FLAGS = ("--out", "--profile", "--md", "--no-html", "--doi", "--images")


@pytest.mark.parametrize("flag", SCAN_FLAGS)
def test_bare_help_still_documents_the_scan_flags(flag):
    res = _run(["--help"])

    assert res.returncode == 0, res.stderr
    assert flag in res.stdout, f"{flag} is undiscoverable from `paperconan --help`"


@pytest.mark.parametrize("flag", SCAN_FLAGS)
def test_scan_subcommand_help_documents_the_scan_flags(flag):
    res = _run(["scan", "--help"])

    assert res.returncode == 0, res.stderr
    assert flag in res.stdout


def test_scan_usage_errors_point_at_the_scan_parser(tmp_path):
    """`--image-diagnostics requires --images` must show scan usage, not the top level."""
    data = _tiny_csv(tmp_path / "data")

    res = _run([str(data), "--image-diagnostics"])

    assert res.returncode != 0
    combined = res.stderr + res.stdout
    assert "--image-diagnostics requires --images" in combined
    assert "{scan,fetch,report,workflow}" not in combined, (
        "error reported against the top-level parser instead of the scan parser"
    )


def test_scan_is_addressable_explicitly(tmp_path):
    """The explicit form disambiguates a directory named like a subcommand."""
    data = _tiny_csv(tmp_path / "data")

    res = _run(["scan", str(data), "--out", str(tmp_path / "out"), "--no-html"])

    assert res.returncode == 0, res.stderr
    assert (tmp_path / "out" / "scan.json").exists()


def test_a_directory_named_like_a_subcommand_resolves_to_the_subcommand(tmp_path):
    """Documents the ambiguity: the subcommand wins, `scan` is the way out."""
    _tiny_csv(tmp_path / "report")

    res = _run(["report"], cwd=str(tmp_path))

    # parsed as `report` (which then complains about its own required args),
    # never as a scan of ./report
    assert res.returncode != 0
    assert not (tmp_path / "report" / "audit").exists()


def test_unknown_subcommand_like_argument_is_treated_as_a_scan_target(tmp_path):
    res = _run(["definitely-not-a-command"], cwd=str(tmp_path))

    assert res.returncode != 0
    # it failed as a missing scan input, not as an argparse "invalid choice"
    assert "invalid choice" not in (res.stderr + res.stdout)


# ---------- existing subcommands keep working ----------

def test_report_subcommand_still_renders(tmp_path):
    scan = {"relations_blocks": [], "cross_sheet_findings": []}
    verdict = {"verdict": "NEEDS_HUMAN", "report_md": "Requires contextual review."}
    (tmp_path / "scan.json").write_text(json.dumps(scan), encoding="utf-8")
    (tmp_path / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    out = tmp_path / "adjudicated.html"

    res = _run([
        "report", str(tmp_path / "scan.json"),
        "--verdict", str(tmp_path / "verdict.json"),
        "--out", str(out),
    ])

    assert res.returncode == 0, res.stderr
    assert out.exists()


@pytest.mark.parametrize("name", ["fetch", "report"])
def test_each_subcommand_has_its_own_help(name):
    res = _run([name, "--help"])

    assert res.returncode == 0, res.stderr
