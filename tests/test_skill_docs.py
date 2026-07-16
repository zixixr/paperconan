from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

from paperconan._neutral_language import contains_blocked_language


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "paperconan"
REF_DIR = SKILL_DIR / "references"
_FALLBACK_SURFACE_ROOTS = (
    "src",
    "tests",
    "skills",
    "docs",
    "examples",
)


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
    assert "review priority labels" in text
    assert "not author-intent conclusions" in text


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

    assert "不是作者意图判断" in readme


def _python_comments_and_docstrings(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    tree = ast.parse(source, filename=str(path))
    docstrings = [
        value
        for node in ast.walk(tree)
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        )
        if (value := ast.get_docstring(node, clean=False)) is not None
    ]
    return "\n".join([*comments, *docstrings])


def _python_product_text(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    comments_and_docstrings = _python_comments_and_docstrings(path)
    tree = ast.parse(source, filename=str(path))
    runtime_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    return "\n".join([comments_and_docstrings, *runtime_strings])


def _python_identifier_text(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return "\n".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    )


def _tracked_surface_text(relative: str, path: Path) -> str | None:
    if relative in {"README.md", "pyproject.toml"} or relative.startswith(
        ("docs/", "examples/", "skills/")
    ):
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
    if relative.endswith(".py") and relative.startswith("src/"):
        return "\n".join([
            _python_product_text(path),
            _python_identifier_text(path),
        ])
    if relative.endswith(".py") and relative.startswith("tests/"):
        return "\n".join([
            _python_comments_and_docstrings(path),
            _python_identifier_text(path),
        ])
    return None


def _tracked_product_surfaces(root: Path = ROOT) -> list[str]:
    repository = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        cwd=root,
        text=True,
    )
    if (
        repository.returncode == 0
        and repository.stdout.strip()
        and Path(repository.stdout.strip()).resolve() == root.resolve()
    ):
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            check=True,
            capture_output=True,
            cwd=root,
            text=True,
        )
        return sorted(
            relative
            for relative in tracked.stdout.split("\0")
            if relative
        )

    paths = [
        path.relative_to(root).as_posix()
        for name in _FALLBACK_SURFACE_ROOTS
        for path in (root / name).rglob("*")
        if path.is_file()
    ]
    paths.extend(
        name
        for name in ("README.md", "pyproject.toml")
        if (root / name).is_file()
    )
    return sorted(set(paths))


def test_tracked_product_surface_fallback_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    included = (
        "src/package.py",
        "tests/test_example.py",
        "skills/paperconan/SKILL.md",
        "docs/guide.md",
        "examples/README.md",
        "README.md",
        "pyproject.toml",
    )
    excluded = (
        "recheck/private.md",
        "batches/private.md",
        "private/other.md",
    )
    for relative in included + excluded:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=128,
            stdout="",
        ),
    )

    assert _tracked_product_surfaces(tmp_path) == sorted(included)


def test_tracked_product_surfaces_follow_neutral_language_policy() -> None:
    violations = []
    for relative in _tracked_product_surfaces():
        path = ROOT / relative
        text = _tracked_surface_text(relative, path)
        if text is None:
            continue
        if contains_blocked_language(text):
            violations.append(relative)
    assert violations == []


@pytest.mark.parametrize(
    "text",
    [
        "fr" + "aud",
        "fr" + "audulent",
        "de" + "fr" + "auded",
        "fabri" + "cate",
        "fabri" + "cated",
        "fabri" + "cation",
        "fa" + "ke",
        "fa" + "ked",
        "fa" + "king",
        "fal" + "sify",
        "fal" + "sified",
        "fal" + "sification",
        "mis" + "conduct",
        "mis" + "conducted",
        "guil" + "t",
        "guil" + "ty",
        "造" + "假",
        "伪" + "造",
        "捏" + "造",
        "作" + "假",
        "实" + "锤",
        "fr" + "audster",
        "de" + "fr" + "auder",
    ],
)
def test_neutral_language_matcher_blocks_expression_families(text: str) -> None:
    assert contains_blocked_language(f"prefix {text} suffix")


@pytest.mark.parametrize(
    "text",
    [
        "sample_" + "fa" + "ke_download",
        "sample" + "Fa" + "keDownload",
        "sample_" + "fa" + "ke2_download",
        "sample_2" + "fa" + "ke_download",
    ],
    ids=["underscore", "camel-case", "letter-to-digit", "digit-to-letter"],
)
def test_neutral_language_matcher_normalizes_identifier_boundaries(text: str) -> None:
    assert contains_blocked_language(text)


def test_tracked_surface_identifier_and_code_block_text_is_inspected(
    tmp_path: Path,
) -> None:
    identifier = "fa" + "ke_download"
    source = tmp_path / "sample.py"
    source.write_text(f"def {identifier}():\n    return None\n", encoding="utf-8")
    assert contains_blocked_language(_python_identifier_text(source))

    numeric_identifier = "fa" + "ke2_download"
    source.write_text(
        f"def {numeric_identifier}():\n    return None\n",
        encoding="utf-8",
    )
    assert contains_blocked_language(_python_identifier_text(source))

    document = tmp_path / "sample.md"
    document.write_text(
        f"```python\ndef {identifier}():\n    return None\n```\n",
        encoding="utf-8",
    )
    text = _tracked_surface_text("docs/sample.md", document)
    assert text is not None
    assert contains_blocked_language(text)


@pytest.mark.parametrize(
    "text",
    [
        "statistical signal",
        "data inconsistency",
        "request for clarification",
        "fabric",
        "micro" + "fabri" + "cation",
        "falsifiable hypothesis",
        "fa" + "ke" + "root package",
        "mis" + "conductance",
        "guiltless",
    ],
)
def test_neutral_language_matcher_allows_unrelated_words(text: str) -> None:
    assert not contains_blocked_language(text)


def test_image_budget_lock_scope_is_documented() -> None:
    cli = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")

    assert "PaperConan writers" in cli
    assert "external writers that ignore the lock" in cli
    assert "observed external changes" in cli


def test_verdict_reference_ceiling_is_documented() -> None:
    reports = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")

    assert "5,000 raw verdict references" in reports


def test_verdict_ingress_schema_contract_is_documented() -> None:
    reports = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")

    assert (
        "The top-level verdict and all nested verdict objects must be "
        "concrete JSON objects."
    ) in reports
    assert "concrete JSON objects" in reports
    assert "Markdown-rendered verdict fields must be strings or `null`" in reports


def test_skill_routes_adaptive_image_review() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    required = [
        "paperconan <input-dir> --images",
        "unavailable_no_multimodal",
        "image_assets",
        "image_findings",
        "image_refs",
        "deferred_asset_ids",
        "whole image",
        "native-pixel crop",
        "single unified report",
    ]
    for phrase in required:
        assert phrase in skill


def test_output_schema_and_report_template_document_image_contracts() -> None:
    output = (REF_DIR / "output-schema.md").read_text(encoding="utf-8")
    template = (REF_DIR / "report-templates.md").read_text(encoding="utf-8")
    for phrase in ("image_assets", "image_findings", "image_review"):
        assert phrase in output
    for phrase in ("finding_type", "image_refs", "review_status"):
        assert phrase in template


def test_deterministic_image_examples_use_two_regions_in_one_asset() -> None:
    for path in (
        REF_DIR / "output-schema.md",
        REF_DIR / "report-templates.md",
    ):
        text = path.read_text(encoding="utf-8")
        blocks = re.findall(r"```json\n(.*?)\n```", text, flags=re.DOTALL)
        examples = [
            json.loads(block)
            for block in blocks
            if '"kind": "image_pair_similarity_signal"' in block
        ]
        assert examples, f"missing deterministic image example in {path.name}"
        for example in examples:
            assert example["asset_ids"] == ["img:a"]
            assert len(example["regions"]) == 2
            assert {
                region["asset_id"] for region in example["regions"]
            } == {"img:a"}


def test_image_coverage_status_normalization_is_documented() -> None:
    pattern = re.compile(
        r"unknown `image_review\.status`[^.\n]*`partial`",
        flags=re.IGNORECASE,
    )
    for path in (
        REF_DIR / "output-schema.md",
        REF_DIR / "report-templates.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert pattern.search(text), (
            f"{path.name} must document unknown coverage status normalization"
        )
