from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "paperconan"
REF_DIR = SKILL_DIR / "references"


PUBLIC_REFS = [
    "output-schema.md",
    "detectors.md",
    "judgment-rubric.md",
    "interpretation.md",
    "adjudication-tiers.md",
    "report-templates.md",
    "adversarial-review.md",
    "batch-workflow.md",
    "case-patterns.md",
]

WORKED_EXAMPLE_FILES = [
    "make_demo_data.py",
    "report-preview.png",
    "README.md",
    "demo_paper/ED_Fig2_tumor_volume.xlsx",
    "demo_paper/ED_Fig4_qPCR.xlsx",
    "demo_paper/audit/report.html",
    "demo_paper/audit/scan.json",
]


def _local_markdown_links(text: str | None = None) -> list[str]:
    if text is None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    links = set()
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = match.group(1).strip()
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path.endswith(".md"):
            continue
        links.add(parsed.path)
    return sorted(links)


def _is_forbidden_zip_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        path.is_absolute()
        or ".." in path.parts
        or "__pycache__" in path.parts
        or any(part == ".cache" for part in path.parts)
        or any(part.startswith(".") and part.endswith("_cache") for part in path.parts)
        or bool(re.search(r"\.py[cod]$", name))
        or path.name == ".DS_Store"
    )


def _publishable_skill_files(skill_dir: Path) -> set[str]:
    files = set()
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        archive_name = f"paperconan/{path.relative_to(skill_dir).as_posix()}"
        if not _is_forbidden_zip_member(archive_name):
            files.add(archive_name)
    return files


def _skill_zip_sources(script=ROOT / "build_skill_zip.sh") -> set[str]:
    text = script.read_text(encoding="utf-8")
    match = re.search(
        r"^SKILL_ZIP_SOURCES=\(\n(?P<body>.*?)^\)\n",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    sources = set()
    for line in match.group("body").splitlines():
        tokens = shlex.split(line, comments=True)
        if tokens:
            assert len(tokens) == 1
            sources.add(tokens[0])
    return sources


def _skill_zip_members(script=ROOT / "build_skill_zip.sh") -> set[str]:
    members = set()
    for source in _skill_zip_sources(script):
        if source.startswith("skills/paperconan/"):
            relative = source.removeprefix("skills/paperconan/")
        else:
            assert source.startswith("examples/")
            relative = source
        members.add(f"paperconan/{relative}")
    return members


def _tracked_skill_sources() -> set[str] | None:
    repository = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    if (
        repository.returncode != 0
        or Path(repository.stdout.strip()).resolve() != ROOT.resolve()
    ):
        return None
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "skills/paperconan"],
        check=True,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )
    return {name for name in result.stdout.split("\0") if name}


def test_local_markdown_links_strip_suffixes_and_skip_external_urls() -> None:
    text = """
    [local](references/local.md#section)
    [query](references/query.md?mode=full#section)
    [external](https://example.test/reference.md)
    [protocol-relative](//example.test/reference.md)
    [anchor](#section)
    """

    assert _local_markdown_links(text) == [
        "references/local.md",
        "references/query.md",
    ]


def test_publishable_skill_files_exclude_generated_files(tmp_path) -> None:
    skill_dir = tmp_path / "paperconan"
    files = {
        "guide.md": "publishable",
        ".pytest_cache/state": "cache",
        ".cache/state": "cache",
        ".tool_cache/state": "cache",
        "__pycache__/module.pyc": "bytecode",
        "module.pyo": "bytecode",
        ".DS_Store": "metadata",
    }
    for relative, contents in files.items():
        path = skill_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    assert _publishable_skill_files(skill_dir) == {"paperconan/guide.md"}


def test_skill_zip_allowlist_matches_tracked_skill_and_demo_sources() -> None:
    tracked = _tracked_skill_sources()
    if tracked is None:
        return

    expected = tracked | {
        f"examples/{relative}"
        for relative in WORKED_EXAMPLE_FILES
    }
    assert _skill_zip_sources() == expected


def test_skill_routes_all_public_references() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    for name in PUBLIC_REFS:
        assert (REF_DIR / name).exists(), f"missing reference file: {name}"
        assert f"references/{name}" in skill, f"SKILL.md does not route {name}"


def test_new_judgment_docs_keep_signal_not_verdict_boundary() -> None:
    docs = [
        REF_DIR / "adjudication-tiers.md",
        REF_DIR / "report-templates.md",
        REF_DIR / "adversarial-review.md",
        REF_DIR / "batch-workflow.md",
        REF_DIR / "case-patterns.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    assert "signal-not-verdict" in text
    assert re.search(r"not\s+research-integrity probabilities", text)
    assert "does not establish a research-integrity finding" in text


def test_case_patterns_do_not_publish_real_paper_identifiers() -> None:
    text = (REF_DIR / "case-patterns.md").read_text(encoding="utf-8")

    doi_pattern = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
    assert not doi_pattern.search(text)
    assert "Nature" not in text
    assert "s414" not in text
    assert "s415" not in text


def test_readme_points_to_public_adjudication_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for name in [
        "adjudication-tiers.md",
        "report-templates.md",
        "adversarial-review.md",
        "batch-workflow.md",
        "case-patterns.md",
    ]:
        assert f"skills/paperconan/references/{name}" in readme

    assert "不是研究完整性问题概率" in readme


def test_skill_zip_contains_complete_path_safe_skill_tree(tmp_path) -> None:
    caller_cwd = tmp_path / "caller cwd"
    caller_cwd.mkdir()
    out = tmp_path / "nested output" / "with spaces" / "paperconan skill.zip"

    subprocess.run(
        [str(ROOT / "build_skill_zip.sh"), str(out.resolve())],
        cwd=caller_cwd,
        check=True,
    )

    assert out.is_file()
    assert list(tmp_path.rglob("*.zip")) == [out]

    with zipfile.ZipFile(out) as zf:
        names = {
            info.filename
            for info in zf.infolist()
            if not info.is_dir()
        }

    local_links = _local_markdown_links()
    for relative in local_links:
        assert (SKILL_DIR / relative).is_file(), (
            f"missing local Markdown reference: {relative}"
        )
        assert f"paperconan/{relative}" in names

    assert names == _skill_zip_members()

    forbidden = {
        name
        for name in names
        if _is_forbidden_zip_member(name)
    }
    assert not forbidden
    assert all(name == "paperconan/" or name.startswith("paperconan/") for name in names)


def test_skill_zip_replaces_stale_output_inside_copied_skill_tree(tmp_path) -> None:
    project = tmp_path / "copied project"
    project.mkdir()
    shutil.copy2(ROOT / "build_skill_zip.sh", project / "build_skill_zip.sh")
    shutil.copytree(SKILL_DIR, project / "skills" / "paperconan")
    for relative in WORKED_EXAMPLE_FILES:
        source = ROOT / "examples" / relative
        destination = project / "examples" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    cache_dir = project / "skills" / "paperconan" / ".pytest_cache"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "state").write_text("cache", encoding="utf-8")
    generic_cache_dir = project / "skills" / "paperconan" / ".cache"
    generic_cache_dir.mkdir(exist_ok=True)
    (generic_cache_dir / "state").write_text("cache", encoding="utf-8")
    bytecode_dir = project / "skills" / "paperconan" / "__pycache__"
    bytecode_dir.mkdir(exist_ok=True)
    (bytecode_dir / "module.pyc").write_bytes(b"bytecode")
    (project / "skills" / "paperconan" / ".DS_Store").write_bytes(b"metadata")
    (project / "skills" / "paperconan" / "local-note.md").write_text(
        "local only",
        encoding="utf-8",
    )
    (project / "examples" / "local-note.py").write_text(
        "local_only = True\n",
        encoding="utf-8",
    )

    out = project / "skills" / "paperconan" / "stale output.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("stale-marker.txt", "old archive")

    subprocess.run(
        [str(project / "build_skill_zip.sh"), str(out)],
        cwd=tmp_path,
        check=True,
    )

    with zipfile.ZipFile(out) as zf:
        names = {
            info.filename
            for info in zf.infolist()
            if not info.is_dir()
        }

    assert names == _skill_zip_members(project / "build_skill_zip.sh")
