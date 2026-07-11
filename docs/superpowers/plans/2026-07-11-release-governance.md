# Release Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make default artifacts deterministic, enforce neutral public
language, align project metadata and locks, and verify complete source and
skill distributions.

**Architecture:** Separate optional runtime metadata from scan substance, add
repository policy tests driven by tracked files, and make packaging tests build
and inspect the real artifacts. Development setup, CI, sdist, ignore rules, and
the skill ZIP derive from explicit configuration rather than hand-maintained
partial lists.

**Tech Stack:** Python 3.10+, pytest, uv, setuptools, tarfile, zipfile, GitHub
Actions, shell.

## Global Constraints

- Use only neutral statistical-signal and data-inconsistency language.
- Store prohibited-language test tokens only in encoded form.
- Default `scan.json` retains existing runtime keys with `null` values.
- Runtime values appear only through an explicit library or CLI option.
- Paths in scan statistics are relative to the input directory.
- Keep `pip install -e ".[dev]"` compatible while adding uv's default dev group.
- Support Python 3.10 through 3.14.
- Every production change follows a verified red-green cycle.
- Do not inspect, modify, package, or scan `recheck/` or `batches/`.

---

### Task 1: Make Default Scan Artifacts Deterministic

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_html.py`
- Modify: `tests/test_smoke.py`
- Create: `tests/test_runtime_metadata.py`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `docs/cli.md`

**Interfaces:**
- `scan_dir(in_dir, out_dir, *, write_md=False, write_html=True, paper=None,
  profile="review", write_json=True, evidence=True,
  diagnostic_on_empty=False, include_runtime=False)`
- `_elapsed_ms(start) -> float | None`
- CLI option `--runtime-metadata`.

- [ ] **Step 1: Write deterministic-output tests**

Create `tests/test_runtime_metadata.py`:

```python
import json
import os
import subprocess
import sys

from paperconan._audit import scan_dir


def _data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "t.csv").write_text(
        "a,b\n1.1,2.2\n2.2,3.3\n3.3,4.4\n",
        encoding="utf-8",
    )
    return data


def test_default_scan_json_is_byte_deterministic(tmp_path):
    data = _data(tmp_path)
    out = tmp_path / "out"
    scan_dir(str(data), str(out), write_html=False)
    first = (out / "scan.json").read_bytes()
    scan_dir(str(data), str(out), write_html=False)
    second = (out / "scan.json").read_bytes()
    assert second == first
    scan = json.loads(first)
    assert scan["scanned_at"] is None
    assert scan["scan_stats"]["elapsed_ms"] is None
    assert all(item["elapsed_ms"] is None for item in scan["scan_stats"]["files"])
    assert all(item["elapsed_ms"] is None for item in scan["scan_stats"]["sheets"])
    assert all(not os.path.isabs(item["path"])
               for item in scan["scan_stats"]["files"])


def test_runtime_metadata_is_opt_in(tmp_path):
    data = _data(tmp_path)
    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        include_runtime=True,
    )
    assert isinstance(scan["scanned_at"], str)
    assert scan["scan_stats"]["elapsed_ms"] >= 0


def test_cli_runtime_metadata_switch(tmp_path):
    data = _data(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            str(data),
            "--no-html",
            "--runtime-metadata",
        ],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert isinstance(scan["scanned_at"], str)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_runtime_metadata.py -q
```

Expected: repeated JSON differs and `include_runtime` is not accepted.

- [ ] **Step 3: Isolate runtime collection**

Add:

```python
def _elapsed_ms(start):
    if start is None:
        return None
    return round((time.perf_counter() - start) * 1000, 3)
```

Append `include_runtime=False` to the existing keyword-only `scan_dir`
signature shown in the interface block. Initialize timers as:

```python
scan_start = time.perf_counter() if include_runtime else None
file_start = time.perf_counter() if include_runtime else None
sheet_start = time.perf_counter() if include_runtime else None
```

Retain every existing `elapsed_ms` key but assign `_elapsed_ms(start)`.
Assign:

```python
scanned_at = (
    datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )
    if include_runtime
    else None
)
file_stat["path"] = os.path.relpath(f, start=in_dir)
```

- [ ] **Step 4: Add CLI and renderer behavior**

Add:

```python
ap.add_argument(
    "--runtime-metadata",
    action="store_true",
    help="Record wall-clock timestamp and elapsed times",
)
```

Pass `include_runtime=args.runtime_metadata`. HTML and Markdown include
timestamp or elapsed fields only when the value is not `None`.

- [ ] **Step 5: Update tests and docs**

Change the old smoke assertion that required a timestamp to expect `None` by
default and a string under `include_runtime=True`. Document the deterministic
default and explicit option in the output schema and CLI guide.

- [ ] **Step 6: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_runtime_metadata.py \
  tests/test_smoke.py \
  tests/test_golden_columnar.py \
  tests/test_report_status.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/paperconan/_audit.py src/paperconan/_html.py \
  tests/test_runtime_metadata.py tests/test_smoke.py \
  skills/paperconan/references/output-schema.md docs/cli.md
git commit -m "feat: make runtime metadata opt in"
```

---

### Task 2: Enforce Neutral Language Across Public Files

**Files:**
- Create: `tests/test_language_policy.py`
- Modify: `README.md`
- Modify: `docs/batch-workflow.md`
- Modify: `docs/detectors.md`
- Modify: `docs/faq.md`
- Modify: `docs/superpowers/plans/2026-05-28-paperconan-data-fetch.md`
- Modify: `docs/superpowers/specs/2026-06-03-grim-grimmer-detector-design.md`
- Modify: `docs/superpowers/specs/2026-06-13-skillhub-upload-design.md`
- Modify: `docs/superpowers/specs/2026-07-08-report-fidelity-and-dispersed-repeat-detector-design.md`
- Modify: `examples/README.md`
- Modify: `examples/make_demo_data.py`
- Modify: `examples/demo_paper/audit/scan.json`
- Modify: `examples/demo_paper/audit/report.html`
- Modify: `pyproject.toml`
- Modify: `skills/paperconan/SKILL.md`
- Modify: `skills/paperconan/references/adjudication-tiers.md`
- Modify: `skills/paperconan/references/adversarial-review.md`
- Modify: `skills/paperconan/references/detectors.md`
- Modify: `skills/paperconan/references/interpretation.md`
- Modify: `skills/paperconan/references/judgment-rubric.md`
- Modify: `skills/paperconan/references/report-templates.md`
- Modify: `src/paperconan/_adjudicated_html.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_extract.py`
- Modify: `src/paperconan/_profiles.py`
- Modify: `src/paperconan/packet.py`
- Modify: affected tracked tests under `tests/` and `tests/fetch/`

**Interfaces:**
- `_tracked_public_text_files(root) -> list[Path]`
- `_policy_hits(path) -> list[tuple[int, int]]`
- Token IDs are encoded and never decoded in failure output.

- [ ] **Step 1: Write the policy test**

Create `tests/test_language_policy.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
_TOKEN_HEX = (
    "6672617564",
    "6661627269636174",
    "66616b65",
    "6d6973636f6e64756374",
    "6775696c7479",
    "e980a0e58187",
    "e4bcaae980a0",
    "e5ada6e69cafe4b88de7abaf",
)
_TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".yml", ".yaml",
    ".json", ".html", ".sh", ".txt",
}
_FALLBACK_ROOTS = (
    "src", "tests", "skills", "docs", "examples", ".github",
)


def _tokens():
    return [
        bytes.fromhex(value).decode("utf-8").casefold()
        for value in _TOKEN_HEX
    ]


def _allowed_path(path):
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith(("recheck/", "batches/")):
        return False
    return path.name == ".gitignore" or path.suffix.lower() in _TEXT_SUFFIXES


def _tracked_public_text_files(root=ROOT):
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout:
        paths = [
            root / value.decode("utf-8")
            for value in proc.stdout.split(b"\0")
            if value
        ]
    else:
        paths = [
            path
            for name in _FALLBACK_ROOTS
            for path in (root / name).rglob("*")
            if path.is_file()
        ]
        paths.extend(
            root / name
            for name in ("README.md", "pyproject.toml", ".gitignore")
            if (root / name).is_file()
        )
    return sorted(path for path in paths if _allowed_path(path))


def _policy_hits(path):
    hits = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return hits
    for line_number, line in enumerate(lines, 1):
        folded = line.casefold()
        for token_number, token in enumerate(_tokens(), 1):
            if token in folded:
                hits.append((line_number, token_number))
    return hits


def test_public_files_use_neutral_language():
    hits = [
        (
            path.relative_to(ROOT).as_posix(),
            line_number,
            token_number,
        )
        for path in _tracked_public_text_files()
        for line_number, token_number in _policy_hits(path)
    ]
    assert not hits, "\n".join(
        f"{path}:{line}:T{token}"
        for path, line, token in hits
    )
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_language_policy.py -q
```

Expected: failures report only file paths, line numbers, and token IDs.

- [ ] **Step 3: Apply the neutral replacement map**

Replace every reported token according to context:

```text
T1 -> "research-integrity concern" or "data inconsistency"
T2 -> "statistical-signal pattern" or "synthetic"
T3 -> "stub", "fixture", or "synthetic"
T4 -> "research-integrity review"
T5 -> "responsible"
T6 -> "数据不一致"
T7 -> "待解释异常"
T8 -> "研究完整性问题"
```

Rename helper variables, functions, comments, test names, metadata keywords,
CLI copy, report copy, historical plan text, and synthetic-demo labels as
needed. Do not weaken detector names or machine-readable schema fields that do
not contain a policy token.

For generated demo artifacts, regenerate after source copy is clean:

```bash
.venv/bin/python examples/make_demo_data.py examples/demo_paper
.venv/bin/paperconan examples/demo_paper --out examples/demo_paper/audit
```

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_language_policy.py \
  tests/test_skill_docs.py \
  tests/test_adjudicated_report.py \
  tests/test_decimal_tail_gate.py \
  tests/test_grim.py \
  tests/test_progression_reuse.py \
  tests/test_relations_flood.py \
  tests/fetch -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -u -- README.md docs examples pyproject.toml skills src tests
git add -- tests/test_language_policy.py
git commit -m "docs: enforce neutral statistical-signal language"
```

Before committing, inspect `git status --short` and stage only tracked public
files plus `tests/test_language_policy.py`; do not stage local instruction files
or ignored working data.

---

### Task 3: Align uv, Project Metadata, CI, and Direct pytest

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.github/workflows/tests.yml`
- Create: `tests/__init__.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Default uv dependency group: `dev`.
- Direct `.venv/bin/pytest` collects only `tests/`.
- CI matrix: Python 3.10, 3.11, 3.12, 3.13, 3.14.

- [ ] **Step 1: Add packaging-configuration tests**

Append to `tests/test_packaging.py`:

```python
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
```

- [ ] **Step 2: Run current failures**

```bash
uv lock --check
.venv/bin/pytest --collect-only -q -p no:cacheprovider
.venv/bin/python -m pytest tests/test_packaging.py -q
```

Expected: the lock is stale, direct collection imports fail, and new metadata
assertions fail.

- [ ] **Step 3: Update `pyproject.toml`**

Use:

```toml
[build-system]
requires = ["setuptools>=77", "wheel"]
build-backend = "setuptools.build_meta"

[project]
license = "MIT"
license-files = ["LICENSE"]

[dependency-groups]
dev = [
    "pytest>=8",
    "build>=1.2",
    "pdfplumber>=0.11",
    "python-docx>=1.1",
    "xlwt>=1.3",
    "tomli>=2; python_version < '3.11'",
]

[tool.uv]
default-groups = ["dev"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src"]
markers = ["network: live network test, skipped unless PAPERCONAN_LIVE=1"]
```

Keep optional `test` and `dev` extras for pip compatibility. Remove the
deprecated license classifier and add Python 3.13 and 3.14 classifiers.

- [ ] **Step 4: Fix direct test imports**

Create an empty `tests/__init__.py`. Keep `tests/test_detection_recall_e2e.py`
using `from tests import build_nbs1_regression`.

- [ ] **Step 5: Update CI**

Use a matrix of:

```yaml
python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
```

Install uv with `astral-sh/setup-uv`, set up the matrix Python, then run:

```yaml
- run: uv sync --frozen
- run: uv run --frozen pytest -q
```

- [ ] **Step 6: Refresh and verify the lock**

```bash
uv lock
uv lock --check
uv sync --frozen
.venv/bin/pytest --collect-only -q -p no:cacheprovider
.venv/bin/python -m pytest tests/test_packaging.py -q
```

Expected: lock check exits zero, direct collection succeeds, and packaging
tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/tests.yml \
  tests/__init__.py tests/test_packaging.py
git commit -m "build: align development and CI environments"
```

---

### Task 4: Package the Complete Test and Documentation Closure

**Files:**
- Create: `MANIFEST.in`
- Modify: `tests/test_packaging.py`
- Modify: `.github/workflows/tests.yml`

**Interfaces:**
- sdist contains nested tests, helpers, fixtures, golden files, skills,
  examples, lock/config files, and the skill-build script.

- [ ] **Step 1: Write the sdist closure test**

Append to `tests/test_packaging.py`:

```python
from pathlib import Path
import subprocess
import sys
import tarfile


def test_sdist_contains_test_and_skill_closure(tmp_path):
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(dist)],
        check=True,
    )
    archive = next(dist.glob("paperconan-*.tar.gz"))
    with tarfile.open(archive, "r:gz") as tf:
        names = {
            name.split("/", 1)[1]
            for name in tf.getnames()
            if "/" in name
        }
    required = {
        "tests/__init__.py",
        "tests/build_fixture.py",
        "tests/fetch/test_download.py",
        "tests/fetch/fixtures/dryad_files.json",
        "tests/fixtures/supp_table.pdf",
        "tests/golden/tiny_paper.json",
        "skills/paperconan/SKILL.md",
        "examples/demo_paper/audit/scan.json",
        "build_skill_zip.sh",
        "uv.lock",
        ".gitignore",
    }
    assert required <= names
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_packaging.py::test_sdist_contains_test_and_skill_closure -q
```

Expected: nested tests, fixtures, and skills are absent.

- [ ] **Step 3: Create `MANIFEST.in`**

```text
include LICENSE README.md pyproject.toml uv.lock .gitignore build_skill_zip.sh
recursive-include .github *.yml *.yaml
graft tests
graft skills
graft examples
global-exclude __pycache__ *.py[cod] .DS_Store
prune recheck
prune batches
prune .worktrees
```

- [ ] **Step 4: Add an sdist CI job**

After the matrix test job, build an sdist, unpack it, install
`".[test]"` from the unpacked root, and run:

```bash
python -m pytest -q
```

Do not copy any file from the checkout into the unpacked tree.

- [ ] **Step 5: Run and verify GREEN**

```bash
rm -rf dist build
uv build
.venv/bin/python -m pytest tests/test_packaging.py -q
```

Expected: warning-free build and all packaging tests pass.

- [ ] **Step 6: Commit**

```bash
git add MANIFEST.in tests/test_packaging.py .github/workflows/tests.yml
git commit -m "build: include the complete source test closure"
```

---

### Task 5: Ignore Local Inputs and Review Artifacts Safely

**Files:**
- Modify: `.gitignore`
- Create: `tests/test_gitignore_policy.py`

**Interfaces:**
- Every supported local input and review artifact is ignored by default.
- `tests/**` and `examples/**` remain explicit exceptions.

- [ ] **Step 1: Write ignore-policy tests**

Create `tests/test_gitignore_policy.py`:

```python
import subprocess


def _ignored(path):
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path],
        check=False,
    )
    return proc.returncode == 0


def test_local_input_and_review_artifacts_are_ignored():
    for path in (
        "local/a.xlsx", "local/a.xls", "local/a.xlsm", "local/a.xlsb",
        "local/a.csv", "local/a.tsv", "local/a.pdf", "local/a.docx",
        "local/audit/scan.json", "local/scan.json", "local/verdict.json",
        "local/REPORT.md", "local/report.html",
        "local/adjudicated-report.html", "local/paperconan_source.json",
    ):
        assert _ignored(path), path


def test_test_fixtures_and_examples_are_not_ignored():
    for path in (
        "tests/fixtures/supp_table.pdf",
        "tests/fetch/fixtures/source.csv",
        "examples/demo_paper/source.xlsx",
        "examples/demo_paper/audit/scan.json",
        "examples/demo_paper/audit/report.html",
    ):
        assert not _ignored(path), path
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_gitignore_policy.py -q
```

Expected: most formats and review artifacts are not ignored.

- [ ] **Step 3: Update `.gitignore`**

Use:

```gitignore
# Local source inputs
*.xlsx
*.xls
*.xlsm
*.xlsb
*.csv
*.tsv
*.pdf
*.docx

# Local scan and review outputs
audit/
scan.json
verdict.json
REPORT.md
report.html
*adjudicated*.html
paperconan_source.json

# Explicit public test and example exceptions
!tests/**
!examples/**
```

Keep the existing Python, environment, editor, local working-directory, and
skill-ZIP rules.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_gitignore_policy.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add .gitignore tests/test_gitignore_policy.py
git commit -m "chore: ignore local scan inputs and outputs"
```

---

### Task 6: Build the Skill ZIP From Its Full Reference Tree

**Files:**
- Modify: `build_skill_zip.sh`
- Modify: `tests/test_skill_docs.py`

**Interfaces:**
- `./build_skill_zip.sh [output-path]`
- Every local Markdown link in `SKILL.md` exists in the ZIP.

- [ ] **Step 1: Add ZIP closure tests**

Append to `tests/test_skill_docs.py`:

```python
import subprocess
import zipfile


def _local_markdown_links():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    return sorted(set(
        match.group(1)
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", text)
        if "://" not in match.group(1)
    ))


def test_skill_zip_contains_every_local_markdown_reference(tmp_path):
    out = tmp_path / "paperconan-skill.zip"
    subprocess.run(
        [str(ROOT / "build_skill_zip.sh"), str(out)],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    for relative in _local_markdown_links():
        assert f"paperconan/{relative}" in names
    assert all(not name.startswith("/") for name in names)
    assert all(".." not in name.split("/") for name in names)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_skill_docs.py::test_skill_zip_contains_every_local_markdown_reference -q
```

Expected: five referenced Markdown files are absent.

- [ ] **Step 3: Replace the reference whitelist**

In `build_skill_zip.sh`:

```bash
OUT="${1:-paperconan-skill.zip}"
OUT_DIR="$(dirname "$OUT")"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
OUT="$OUT_DIR/$(basename "$OUT")"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/paperconan"
mkdir -p "$ROOT/examples/demo_paper/audit"
cp -R skills/paperconan/. "$ROOT/"
```

Keep the existing example files. The absolute `OUT` above must be passed
unchanged to the subshell that runs `zip`.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_docs.py -q
./build_skill_zip.sh /tmp/paperconan-skill.zip
unzip -t /tmp/paperconan-skill.zip
```

Expected: tests pass and `unzip -t` reports no errors.

- [ ] **Step 5: Commit**

```bash
git add build_skill_zip.sh tests/test_skill_docs.py
git commit -m "build: package the complete paperconan skill"
```

---

### Task 7: Release Governance Regression Gate

**Files:**
- Modify only to address regressions in this component.

- [ ] **Step 1: Run policy and deterministic tests**

```bash
.venv/bin/python -m pytest \
  tests/test_language_policy.py \
  tests/test_runtime_metadata.py \
  tests/test_gitignore_policy.py \
  tests/test_skill_docs.py \
  tests/test_packaging.py -q
```

Expected: all pass.

- [ ] **Step 2: Verify both pytest entry points**

```bash
.venv/bin/python -m pytest -q
uv run --frozen pytest -q
```

Expected: both commands pass with only the intentional live-network skip.

- [ ] **Step 3: Verify lock and distributions**

```bash
uv lock --check
rm -rf dist build
uv build
./build_skill_zip.sh /tmp/paperconan-skill.zip
unzip -t /tmp/paperconan-skill.zip
```

Expected: every command exits zero and the build emits no warnings.

- [ ] **Step 4: Verify repository cleanliness**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional branch changes and the
user-owned untracked instruction files are present.
