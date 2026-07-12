from __future__ import annotations

import re
from pathlib import Path


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
