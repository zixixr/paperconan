from __future__ import annotations

import base64
from collections import UserDict
import json
import subprocess
import sys

import pytest

from build_fixture import build

from paperconan import _adjudicated_html, scan_dir, write_adjudicated_report
from paperconan._adjudicated_html import _render_md, render_adjudicated_report


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _DictionarySubclass(dict):
    pass


class _StatefulVerdictSubclass(dict):
    def __init__(self) -> None:
        super().__init__(
            verdict="NEEDS_HUMAN",
            report_md="The signal requires contextual review.",
        )
        self.title_reads = 0

    def get(self, key, default=None):
        if key == "title":
            self.title_reads += 1
            if self.title_reads == 1:
                return "Contextual review"
            return "mis" + "conduct"
        return super().get(key, default)


def _verdict() -> dict:
    return {
        "verdict": "KEEP",
        "suspicion_tier": 1,
        "impact_scope": "supporting",
        "tier_why": "synthetic independent columns are identical",
        "drop_reason": None,
        "innocent_explanation": "source-data assembly error remains possible",
        "needs_author_data": "raw values and figure mapping",
        "review_status": "confirmed",
        "report_md": (
            "## Synthetic paper\n\n"
            "### 论文主结论\n"
            "This synthetic fixture tests report rendering.\n\n"
            "### 异常位置\n"
            "`ED_Fig1.xlsx` Sheet1 has an identical numeric column pair.\n\n"
            "### 标签含义\n"
            "The fixture labels two columns as separate measurements.\n\n"
            "### 为什么这是问题\n"
            "If independent, identical values need clarification.\n\n"
            "### 影响判断\n"
            "This is supporting evidence in a synthetic test.\n\n"
            "### 无辜解释的层次\n"
            "A duplicate export remains possible.\n\n"
            "### 需要作者澄清\n"
            "Please provide the raw source mapping.\n\n"
            "### 证据\n"
            "paperconan synthetic fixture, identical_column finding.\n"
        ),
    }


def test_write_adjudicated_report_renders_verdict_and_scan_evidence(tmp_path):
    data = tmp_path / "data"
    build(str(data))
    audit = tmp_path / "audit"
    scan = scan_dir(str(data), str(audit), write_html=False)
    out = tmp_path / "adjudication.html"

    write_adjudicated_report(scan, _verdict(), str(out))

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "KEEP" in html
    assert "Tier 1" in html
    assert "supporting" in html
    assert "confirmed" in html
    assert "论文主结论" in html
    assert "异常位置" in html
    assert "identical_column" in html
    assert "ED_Fig1.xlsx" in html
    assert "signal, not verdict" in html
    assert "fabri" + "cation" not in html.lower()
    assert "mis" + "conduct" not in html.lower()


def _image_scan(audit) -> dict:
    preview = audit / "images" / "preview" / "img-a.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(PNG_1X1)
    return {
        "tool_version": "0.test",
        "profile": "review",
        "input_dir": str(audit.parent / "input"),
        "relations_blocks": [],
        "cross_sheet_findings": [],
        "image_assets": [{
            "asset_id": "img:a",
            "file": "Fig1.png",
            "preview_path": "images/preview/img-a.png",
            "mime": "image/png",
        }],
        "image_findings": [],
    }


def _image_verdict() -> dict:
    return {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Registered image reference",
            "image_refs": [{"asset_id": "img:a"}],
            "review_status": "needs_human",
            "report_md": "The registered image requires contextual review.",
        }],
    }


def test_write_adjudicated_report_accepts_artifact_dir(tmp_path):
    audit = tmp_path / "audit"
    scan = _image_scan(audit)
    out = tmp_path / "adjudication.html"

    write_adjudicated_report(scan, _image_verdict(), str(out), artifact_dir=str(audit))

    assert "data:image/png;base64," in out.read_text(encoding="utf-8")


def test_adjudicated_report_rejects_in_root_registered_preview_symlink(tmp_path):
    audit = tmp_path / "audit"
    scan = _image_scan(audit)
    registered = audit / scan["image_assets"][0]["preview_path"]
    target = registered.with_name("target.png")
    registered.replace(target)
    registered.symlink_to(target.name)

    html = render_adjudicated_report(
        scan,
        _image_verdict(),
        artifact_dir=str(audit),
    )

    assert "data:image/png;base64," not in html
    assert "preview unavailable" in html


@pytest.mark.parametrize(
    "value",
    ["not-a-number", "inf", "-1", "1e10000"],
    ids=["malformed", "non-finite", "negative", "overflow"],
)
def test_adjudicated_report_invalid_image_evidence_limit_suppresses_images(
    tmp_path,
    monkeypatch,
    value,
):
    audit = tmp_path / "audit"
    scan = _image_scan(audit)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_EVIDENCE_MB", value)

    html = render_adjudicated_report(
        scan,
        _image_verdict(),
        artifact_dir=str(audit),
    )

    assert "data:image/" not in html
    assert "preview unavailable" in html


def test_adjudicated_numeric_only_report_ignores_invalid_image_evidence_limit(
    monkeypatch,
):
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_EVIDENCE_MB",
        "not-a-number",
    )

    html = render_adjudicated_report(
        {
            "relations_blocks": [],
            "cross_sheet_findings": [],
            "image_assets": [],
            "image_findings": [],
        },
        {
            "verdict": "NEEDS_HUMAN",
            "report_md": "Numeric evidence remains available for review.",
        },
    )

    assert "Numeric evidence remains available for review." in html


def test_write_adjudicated_report_validation_failure_preserves_existing_output(
    tmp_path,
):
    out = tmp_path / "adjudication.html"
    out.write_text("existing-report", encoding="utf-8")
    blocked = "mis" + "conduct"

    with pytest.raises(ValueError, match="neutral-language policy"):
        write_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            {"verdict": "NEEDS_HUMAN", "report_md": blocked},
            str(out),
        )

    assert out.read_text(encoding="utf-8") == "existing-report"


@pytest.mark.parametrize(
    "verdict",
    [
        UserDict({
            "verdict": "NEEDS_HUMAN",
            "report_md": "The signal requires contextual review.",
        }),
        _DictionarySubclass(
            verdict="NEEDS_HUMAN",
            report_md="The signal requires contextual review.",
        ),
    ],
    ids=["user-dictionary", "dictionary-subclass"],
)
def test_top_level_verdict_requires_a_concrete_json_object(verdict):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            verdict,
        )

    assert str(exc.value) == "verdict must be a concrete JSON object"


def test_stateful_top_level_verdict_is_rejected_before_any_report_work(
    monkeypatch,
):
    verdict = _StatefulVerdictSubclass()

    def reject_late_work(*args, **kwargs):
        raise AssertionError("report work must not start")

    monkeypatch.setattr(
        _adjudicated_html,
        "_visible_scan_findings",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html.copy,
        "deepcopy",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html,
        "_validate_neutral_verdict",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html,
        "_scan_title",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html,
        "_render_unified",
        reject_late_work,
    )

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            verdict,
        )

    assert str(exc.value) == "verdict must be a concrete JSON object"
    assert verdict.title_reads == 0


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (
            {
                "verdict": "NEEDS_HUMAN",
                "paper_conclusion": "Paper-level context.",
                "findings": [{"report_md": "Modern finding context."}],
            },
            "Modern finding context.",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "Legacy finding context.",
            },
            "Legacy finding context.",
        ),
    ],
    ids=["modern", "legacy"],
)
def test_concrete_top_level_verdict_preserves_modern_and_legacy_behavior(
    verdict,
    expected,
):
    html = render_adjudicated_report(
        {"relations_blocks": [], "cross_sheet_findings": []},
        verdict,
    )

    assert expected in html


def test_top_level_verdict_validation_preserves_existing_output(tmp_path):
    out = tmp_path / "adjudication.html"
    out.write_text("existing-report", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        write_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            UserDict({
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
            }),
            str(out),
        )

    assert str(exc.value) == "verdict must be a concrete JSON object"
    assert out.read_text(encoding="utf-8") == "existing-report"


@pytest.mark.parametrize(
    "blocked",
    [
        "fr" + "audulent",
        "fabri" + "cated",
        "fa" + "king",
        "fal" + "sification",
        "mis" + "conducted",
        "guil" + "t",
        "造" + "假",
        "伪" + "造",
        "捏" + "造",
        "作" + "假",
        "fr" + "audster",
        "de" + "fr" + "auder",
    ],
)
def test_adjudicated_report_rejects_blocked_language_families_without_echo(
    tmp_path,
    blocked,
):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Registered signal",
            "report_md": f"The text makes a {blocked} conclusion.",
        }],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            verdict,
            artifact_dir=str(tmp_path),
        )

    assert blocked.casefold() not in str(exc.value).casefold()
    assert str(exc.value) == (
        "verdict text violates the neutral-language policy; rewrite it as a "
        "statistical signal, data inconsistency, unresolved similarity, or "
        "request for clarification; quoted or negated blocked language must "
        "also be rewritten"
    )


def test_adjudicated_report_rejects_identifier_style_blocked_language(
    tmp_path,
):
    blocked = "sample" + "Fa" + "keDownload"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "report_md": f"The visible label is {blocked}.",
    }

    with pytest.raises(ValueError, match="neutral-language policy"):
        render_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            verdict,
            artifact_dir=str(tmp_path),
        )


def test_write_adjudicated_report_publication_failure_preserves_existing_output(
    tmp_path,
    monkeypatch,
):
    out = tmp_path / "adjudication.html"
    out.write_text("existing-report", encoding="utf-8")

    def reject_publication(*args, **kwargs):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(_adjudicated_html.os, "replace", reject_publication)

    with pytest.raises(OSError, match="synthetic publication failure"):
        write_adjudicated_report(
            {"relations_blocks": [], "cross_sheet_findings": []},
            {"verdict": "NEEDS_HUMAN", "report_md": "Contextual review required."},
            str(out),
        )

    assert out.read_text(encoding="utf-8") == "existing-report"
    assert not list(tmp_path.glob(".paperconan-adjudicated-*"))


def test_report_subcommand_writes_adjudicated_html(tmp_path):
    data = tmp_path / "data"
    build(str(data))
    audit = tmp_path / "audit"
    scan_dir(str(data), str(audit), write_html=False)
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(_verdict(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "adjudication.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            "report",
            str(audit / "scan.json"),
            "--verdict",
            str(verdict_path),
            "--out",
            str(out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Synthetic paper" in html
    assert 'class="finding-block"' in html
    assert "identical_column" in html
    assert str(out) in proc.stdout


def test_report_subcommand_rejects_non_neutral_verdict_without_traceback(
    tmp_path,
):
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(
        json.dumps({"relations_blocks": [], "cross_sheet_findings": []}),
        encoding="utf-8",
    )
    blocked = "mis" + "conduct"
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(
        json.dumps({
            "verdict": "NEEDS_HUMAN",
            "report_md": f"There is no evidence of {blocked}.",
        }),
        encoding="utf-8",
    )
    out = tmp_path / "adjudication.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            "report",
            str(scan_path),
            "--verdict",
            str(verdict_path),
            "--out",
            str(out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert blocked not in proc.stderr.casefold()
    assert "neutral-language policy" in proc.stderr
    assert "quoted or negated" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1
    assert not out.exists()


def test_report_subcommand_rejects_malformed_verdict_without_traceback(
    tmp_path,
):
    scan_path = tmp_path / "scan.json"
    scan_path.write_text(
        json.dumps({"relations_blocks": [], "cross_sheet_findings": []}),
        encoding="utf-8",
    )
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text("{", encoding="utf-8")
    out = tmp_path / "adjudication.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            "report",
            str(scan_path),
            "--verdict",
            str(verdict_path),
            "--out",
            str(out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1
    assert not out.exists()


def test_report_subcommand_resolves_preview_relative_to_scan_json(tmp_path):
    audit = tmp_path / "audit"
    scan = _image_scan(audit)
    scan_path = audit / "scan.json"
    scan_path.write_text(json.dumps(scan), encoding="utf-8")
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(_image_verdict()), encoding="utf-8")
    out = tmp_path / "adjudication.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            "report",
            str(scan_path),
            "--verdict",
            str(verdict_path),
            "--out",
            str(out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "data:image/png;base64," in out.read_text(encoding="utf-8")


def test_render_md_sections_are_balanced_not_nested():
    md = (
        "## Title\n\n### 论文主结论\nA.\n\n### 异常位置\n- one\n- two\n\n"
        "### 证据\nC.\n"
    )
    out = _render_md(md)
    # every opened report section must be closed, so sections are siblings
    assert out.count("<section") == out.count("</section>") == 3


def test_profile_hidden_findings_do_not_surface_as_key_evidence():
    scan = {
        "relations_blocks": [
            {
                "file": "F.xlsx",
                "sheet": "S1",
                "block": {"rows": "1-4", "cols": "A-B", "header": ["a", "b"]},
                "within_col": [
                    {
                        "kind": "within_col_value_duplication",
                        "severity": "high",
                        "rule": "r",
                        "profile_action": "hidden",
                        "evidence": {"rows": [{"row_idx": 1, "values": [1]}], "headers": ["a"]},
                    }
                ],
            }
        ],
        "cross_sheet_findings": [],
    }
    html = render_adjudicated_report(scan, {"verdict": "DROP", "report_md": "## x"})
    assert "within_col_value_duplication" not in html


def _scan_two_findings() -> dict:
    return {
        "relations_blocks": [
            {
                "file": "A.xlsx",
                "sheet": "Alpha",
                "block": {"rows": "5-39", "cols": "A-B", "header": ["x", "y"]},
                "relations": [
                    {
                        "kind": "constant_offset",
                        "severity": "high",
                        "rule": "col[1] = col[2] + 0.3",
                        "n": 35,
                        "evidence": {"headers": ["x", "y"], "rows": [{"row_idx": 5, "values": [1, 2]}]},
                    }
                ],
            },
            {
                "file": "B.xlsx",
                "sheet": "Beta",
                "block": {"rows": "1-9", "cols": "C-D", "header": ["p", "q"]},
                "within_col": [
                    {
                        "kind": "within_col_value_duplication",
                        "severity": "medium",
                        "rule": "dup",
                        "n": 9,
                        "evidence": {"headers": ["p"], "rows": [{"row_idx": 1, "values": [9]}]},
                    }
                ],
            },
        ],
        "cross_sheet_findings": [],
    }


def test_finding_refs_scope_key_evidence_to_the_selected_finding():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "suspicion_tier": 1,
        "report_md": "## t",
        "finding_refs": [{"sheet": "Alpha", "kind": "constant_offset"}],
    }
    html = render_adjudicated_report(scan, verdict)
    # only the selected finding is rendered as a full evidence card
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html
    # the other signal is not presented as part of the verdict's evidence
    assert "within_col_value_duplication" not in html


def test_without_finding_refs_falls_back_to_strongest_finding():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, {"verdict": "KEEP", "report_md": "## t"})
    # no finding_ref -> evidence falls back to the single strongest scan signal
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html


def test_finding_refs_with_no_match_falls_back_to_strongest_finding():
    scan = _scan_two_findings()
    verdict = {"verdict": "KEEP", "report_md": "## t", "finding_refs": [{"sheet": "Nonexistent"}]}
    html = render_adjudicated_report(scan, verdict)
    # an unmatched ref falls back to the single strongest scan signal
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html


def test_unmatched_ref_fallback_evidence_is_labeled_as_a_fallback():
    """An unmatched ref still falls back, but the card must say so.

    The fallback keeps a legacy single-finding report useful, yet the narrative
    in report_md describes the cited signal while the rendered evidence is the
    strongest scan signal. Without a label a reader cannot tell the two apart.
    """
    scan = _scan_two_findings()
    verdict = {"verdict": "KEEP", "report_md": "## t", "finding_refs": [{"sheet": "Nonexistent"}]}

    html = render_adjudicated_report(scan, verdict)

    assert 'class="fallback-evidence"' in html
    # the intended fallback behaviour is unchanged
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html


def test_matched_ref_evidence_is_not_labeled_as_a_fallback():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "report_md": "## t",
        "finding_refs": [{"sheet": "Alpha", "kind": "constant_offset"}],
    }

    html = render_adjudicated_report(scan, verdict)

    assert 'class="fallback-evidence"' not in html


def test_legacy_null_finding_refs_preserves_strongest_finding_fallback():
    html = render_adjudicated_report(
        _scan_two_findings(),
        {
            "verdict": "KEEP",
            "report_md": "## t",
            "finding_refs": None,
        },
    )

    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html


@pytest.mark.parametrize("value", ["not-a-list", {}, 7])
def test_legacy_finding_refs_must_be_list_or_null(value):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": value,
            },
        )

    assert str(exc.value) == "verdict finding_refs must be a list or null"


@pytest.mark.parametrize("entry", [None, [], "not-a-dictionary"])
def test_legacy_finding_ref_entries_must_be_dictionaries(entry):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": [entry],
            },
        )

    assert str(exc.value) == "verdict finding_refs entries must be dictionaries"


def test_legacy_finding_refs_limit_is_deterministic():
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": [{} for _ in range(5001)],
            },
        )

    assert str(exc.value) == (
        "verdict finding_refs must contain at most 5000 entries"
    )


def test_legacy_shape_validation_precedes_neutral_text_inspection():
    blocked = "mis" + "conduct"

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": [blocked],
            },
        )

    assert str(exc.value) == "verdict finding_refs entries must be dictionaries"
    assert blocked not in str(exc.value).casefold()


def _multi_finding_verdict() -> dict:
    return {
        "title": "Paper X",
        "verdict": "KEEP",
        "paper_conclusion": "Main claim under review.",
        "overall_impact": "core",
        "findings": [
            {
                "title": "Finding one",
                "finding_ref": {"sheet": "Alpha", "kind": "constant_offset"},
                "suspicion_tier": 3,
                "impact_scope": "core",
                "review_status": "confirmed",
                "report_md": "**位置** alpha loc.",
            },
            {
                "title": "Finding two",
                "finding_ref": {"sheet": "Beta", "kind": "within_col_value_duplication"},
                "suspicion_tier": 2,
                "impact_scope": "supporting",
                "review_status": "needs_human",
                "report_md": "**位置** beta loc.",
            },
        ],
    }


def test_findings_array_renders_per_finding_blocks_with_own_evidence():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, _multi_finding_verdict())
    # each finding is its own self-contained block
    assert html.count('class="finding-block"') == 2
    assert "Finding one" in html and "Finding two" in html
    # each block carries its own status badge
    assert "confirmed" in html and "needs_human" in html
    # each finding's evidence table is rendered adjacent to its block
    assert html.count('class="ev"') == 2
    # a findings index summarises them
    assert "findings-index" in html


def test_adjudicated_report_resolves_a_block_duplication_finding_ref():
    """A verdict may cite any canonical per-block group, block_dups included.

    P0a deliberately scoped its fix to default HTML and left this reference
    unmatched. P0b makes every consumer read the same canonical group set, so
    the adjudicated report — the deliverable — can now render the cited card.
    """
    scan = {
        "relations_blocks": [{
            "file": "synthetic.xlsx",
            "sheet": "Panel",
            "block": {"rows": "2-6", "cols": "A-C", "header": ["a", "b", "c"]},
            "block_dups": [{
                "kind": "block_value_duplication",
                "severity": "medium",
                "rule": "synthetic block duplication signal",
            }],
        }],
        "cross_sheet_findings": [],
    }
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Cited synthetic signal",
            "finding_ref": {"kind": "block_value_duplication"},
            "report_md": "This signal requires contextual review.",
        }],
    }

    html = render_adjudicated_report(scan, verdict)

    assert "block_value_duplication" in html
    assert "synthetic block duplication signal" in html
    assert "无匹配证据（finding_ref 未命中扫描结果）" not in html


@pytest.mark.parametrize("selector", ["sheet", "file", "rows", "kind"])
def test_multi_finding_unmatched_visible_selector_is_neutral_validated(selector):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "The signals require contextual review.",
        "findings": [
            {
                "title": "Unmatched signal",
                "finding_ref": {selector: blocked},
                "report_md": "The signal requires contextual review.",
            },
            {
                "title": "Matched signal",
                "finding_ref": {"sheet": "Alpha", "kind": "constant_offset"},
                "report_md": "The signal requires contextual review.",
            },
        ],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert blocked not in str(exc.value).casefold()
    assert "neutral-language policy" in str(exc.value)


def test_multi_finding_non_rendered_selector_metadata_is_ignored():
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "The signals require contextual review.",
        "findings": [
            {
                "title": "Unmatched signal",
                "finding_ref": {
                    "sheet": "Unmatched",
                    "file": blocked,
                    "finding_id": blocked,
                    "rule": blocked,
                    "private_note": blocked,
                },
                "report_md": "The signal requires contextual review.",
            },
            {
                "title": "Matched signal",
                "finding_ref": {"sheet": "Alpha", "kind": "constant_offset"},
                "report_md": "The signal requires contextual review.",
            },
        ],
    }

    html = render_adjudicated_report(_scan_two_findings(), verdict)

    assert blocked not in html.casefold()
    assert "Unmatched" in html


@pytest.mark.parametrize("entry", [None, [], "not-a-dictionary"])
def test_modern_finding_entries_must_be_dictionaries(entry):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {"verdict": "NEEDS_HUMAN", "findings": [entry]},
        )

    assert str(exc.value) == "verdict finding entries must be dictionaries"


@pytest.mark.parametrize("value", [[], "not-a-dictionary", 7])
def test_modern_finding_ref_must_be_dictionary_or_null(value):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{"finding_ref": value}],
            },
        )

    assert str(exc.value) == "verdict finding_ref must be a dictionary or null"


@pytest.mark.parametrize("field", ["extra_refs", "image_refs"])
@pytest.mark.parametrize("value", [None, {}, "not-a-list"])
def test_modern_reference_collections_must_be_lists(field, value):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{field: value}],
            },
        )

    assert str(exc.value) == f"verdict {field} must be a list"


@pytest.mark.parametrize("field", ["extra_refs", "image_refs"])
@pytest.mark.parametrize("entry", [None, [], "not-a-dictionary"])
def test_modern_reference_entries_must_be_dictionaries(field, entry):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{field: [entry]}],
            },
        )

    assert str(exc.value) == f"verdict {field} entries must be dictionaries"


@pytest.mark.parametrize(
    ("verdict", "message"),
    [
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [UserDict({
                    "title": "mis" + "conduct",
                    "report_md": "The signal requires contextual review.",
                })],
            },
            "verdict finding entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "finding_ref": UserDict({"sheet": "mis" + "conduct"}),
                }],
            },
            "verdict finding_ref must be a dictionary or null",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "extra_refs": [UserDict({"sheet": "mis" + "conduct"})],
                }],
            },
            "verdict extra_refs entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "image_refs": [UserDict({
                        "asset_id": "img:a",
                        "label": "mis" + "conduct",
                    })],
                }],
            },
            "verdict image_refs entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": [UserDict({"sheet": "mis" + "conduct"})],
            },
            "verdict finding_refs entries must be dictionaries",
        ),
    ],
    ids=[
        "modern-finding",
        "modern-finding-ref",
        "modern-extra-ref",
        "modern-image-ref",
        "legacy-finding-ref",
    ],
)
def test_non_dictionary_verdict_objects_are_rejected_without_echo(
    verdict,
    message,
):
    blocked = "mis" + "conduct"

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert str(exc.value) == message
    assert blocked not in str(exc.value).casefold()


@pytest.mark.parametrize(
    ("verdict", "message"),
    [
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [_DictionarySubclass()],
            },
            "verdict finding entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "finding_ref": _DictionarySubclass(),
                }],
            },
            "verdict finding_ref must be a dictionary or null",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "extra_refs": [_DictionarySubclass()],
                }],
            },
            "verdict extra_refs entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    "image_refs": [_DictionarySubclass()],
                }],
            },
            "verdict image_refs entries must be dictionaries",
        ),
        (
            {
                "verdict": "NEEDS_HUMAN",
                "report_md": "The signal requires contextual review.",
                "finding_refs": [_DictionarySubclass()],
            },
            "verdict finding_refs entries must be dictionaries",
        ),
    ],
    ids=[
        "modern-finding",
        "modern-finding-ref",
        "modern-extra-ref",
        "modern-image-ref",
        "legacy-finding-ref",
    ],
)
def test_dictionary_subclasses_are_rejected_at_verdict_ingress(
    verdict,
    message,
):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert str(exc.value) == message


def test_modern_findings_limit_is_deterministic():
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{} for _ in range(5001)],
            },
        )

    assert str(exc.value) == (
        "verdict findings must contain at most 5000 entries"
    )


@pytest.mark.parametrize("field", ["extra_refs", "image_refs"])
def test_modern_reference_limit_is_deterministic(field):
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [{
                    field: [{} for _ in range(1001)],
                }],
            },
        )

    assert str(exc.value) == (
        f"verdict {field} must contain at most 1000 entries"
    )


def test_verdict_wide_reference_limit_precedes_matching_and_rendering(
    monkeypatch,
):
    def reject_late_work(*args, **kwargs):
        raise AssertionError("late report work must not start")

    monkeypatch.setattr(_adjudicated_html, "_match_finding", reject_late_work)
    monkeypatch.setattr(
        _adjudicated_html,
        "registered_preview_data_uri",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html.copy,
        "deepcopy",
        reject_late_work,
    )
    repeated = [{"asset_id": "img:a"} for _ in range(1000)]
    findings = [{"image_refs": list(repeated)} for _ in range(5)]
    findings.append({"finding_ref": {}})

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": findings,
            },
        )

    assert str(exc.value) == (
        "verdict references must contain at most 5000 entries"
    )


def test_repeated_extra_refs_render_once_by_selector_key():
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Combined numeric evidence",
            "finding_ref": {"sheet": "Alpha", "kind": "constant_offset"},
            "extra_refs": [
                {
                    "sheet": "Beta",
                    "kind": "within_col_value_duplication",
                    "private_note": "first",
                },
                {
                    "sheet": "Beta",
                    "kind": "within_col_value_duplication",
                    "private_note": "second",
                },
            ],
            "report_md": "The selected signals require contextual review.",
        }],
    }

    html = render_adjudicated_report(_scan_two_findings(), verdict)

    assert html.count('class="finding-card"') == 2
    assert html.count("within_col_value_duplication") == 1


def test_unique_multi_finding_report_structure_is_unchanged():
    html = render_adjudicated_report(
        _scan_two_findings(),
        _multi_finding_verdict(),
    )

    assert html.count('class="finding-block"') == 2
    assert html.count('class="finding-card"') == 2
    assert "findings-index" in html


def test_modern_shape_validation_precedes_neutral_text_inspection():
    attacker_text = "fabri" + "cated-input-sentinel"

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "findings": [attacker_text],
            },
        )

    assert str(exc.value) == "verdict finding entries must be dictionaries"
    assert attacker_text not in str(exc.value)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (
            "paper_conclusion",
            "verdict paper_conclusion must be a string or null",
        ),
        (
            "review_note",
            "verdict review_note must be a string or null",
        ),
    ],
)
@pytest.mark.parametrize(
    "value",
    [7, 1.5, False, [], {}, UserDict({"note": "value"})],
    ids=["integer", "float", "boolean", "list", "dictionary", "user-dictionary"],
)
def test_paper_markdown_fields_require_string_or_null(field, message, value):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [],
        field: value,
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert str(exc.value) == message


@pytest.mark.parametrize(
    "value",
    [7, 1.5, False, [], {}, UserDict({"note": "value"})],
    ids=["integer", "float", "boolean", "list", "dictionary", "user-dictionary"],
)
def test_modern_finding_report_md_requires_string_or_null(value):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{"report_md": value}],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert str(exc.value) == (
        "verdict finding report_md must be a string or null"
    )


@pytest.mark.parametrize(
    "value",
    [7, 1.5, False, [], {}, UserDict({"note": "value"})],
    ids=["integer", "float", "boolean", "list", "dictionary", "user-dictionary"],
)
def test_legacy_report_md_requires_string_or_null(value):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "report_md": value,
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan_two_findings(), verdict)

    assert str(exc.value) == "verdict report_md must be a string or null"


def test_markdown_schema_validation_precedes_copy_and_neutral_inspection(
    monkeypatch,
):
    blocked = "mis" + "conduct"

    def reject_late_work(*args, **kwargs):
        raise AssertionError("late report work must not start")

    monkeypatch.setattr(
        _adjudicated_html.copy,
        "deepcopy",
        reject_late_work,
    )
    monkeypatch.setattr(
        _adjudicated_html,
        "_validate_neutral_verdict",
        reject_late_work,
    )

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan_two_findings(),
            {
                "verdict": "NEEDS_HUMAN",
                "paper_conclusion": UserDict({"text": blocked}),
                "findings": [],
            },
        )

    assert str(exc.value) == (
        "verdict paper_conclusion must be a string or null"
    )
    assert blocked not in str(exc.value).casefold()


def test_markdown_fields_preserve_absent_null_and_string_behavior():
    for verdict in (
        {
            "verdict": "NEEDS_HUMAN",
            "findings": [{}],
        },
        {
            "verdict": "NEEDS_HUMAN",
            "paper_conclusion": None,
            "review_note": None,
            "findings": [{"report_md": None}],
        },
        {
            "verdict": "NEEDS_HUMAN",
            "review_note": None,
            "report_md": None,
        },
    ):
        render_adjudicated_report(_scan_two_findings(), verdict)

    modern = render_adjudicated_report(
        _scan_two_findings(),
        {
            "verdict": "NEEDS_HUMAN",
            "paper_conclusion": "Paper-level context.",
            "review_note": "Review context.",
            "findings": [{"report_md": "Finding context."}],
        },
    )
    legacy = render_adjudicated_report(
        _scan_two_findings(),
        {
            "verdict": "NEEDS_HUMAN",
            "review_note": "Legacy review context.",
            "report_md": "Legacy finding context.",
        },
    )

    assert "Paper-level context." in modern
    assert "Review context." in modern
    assert "Finding context." in modern
    assert "Legacy review context." in legacy
    assert "Legacy finding context." in legacy


def test_hero_shows_highest_tier_across_findings():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, _multi_finding_verdict())  # tiers 3 and 2
    hero = html.split("</section>")[0]  # the hero is the first <section>
    assert "Tier 2" in hero  # highest severity across findings
    assert "Tier 3" not in hero


def test_legacy_single_finding_format_now_renders_rich():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "report_md": "## t",
        "finding_refs": [{"sheet": "Alpha", "kind": "constant_offset"}],
    }
    html = render_adjudicated_report(scan, verdict)
    # legacy single verdicts now render in the same rich per-finding layout
    assert 'class="finding-block"' in html
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html
