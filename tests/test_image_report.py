from __future__ import annotations

import base64
import copy
import hashlib
import io
from pathlib import Path

import pytest

from paperconan._adjudicated_html import (
    _VisibleTextParser,
    _normalized_verdict_copy,
    render_adjudicated_report,
)
from paperconan._html import _all_findings, _render_finding_card, write_html_report
from paperconan.image import _evidence
from paperconan.image._evidence import (
    EvidenceBudget,
    registered_preview_data_uri,
)
from paperconan.schema import ImageAsset, ImageFinding, ImageReview

Image = pytest.importorskip("PIL.Image")


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _scan(tmp_path: Path) -> dict:
    preview = tmp_path / "images" / "preview" / "img-a.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(PNG_1X1)
    native = tmp_path / "images" / "native" / "img-a.png"
    native.parent.mkdir(parents=True)
    native.write_bytes(PNG_1X1)
    return {
        "tool_version": "0.test",
        "profile": "review",
        "input_dir": str(tmp_path / "input"),
        "relations_blocks": [{
            "file": "data.csv",
            "sheet": "data.csv",
            "block": {"rows": "2-4", "cols": "1-2", "header": ["a", "b"]},
            "relations": [{
                "kind": "constant_offset",
                "severity": "medium",
                "rule": "b = a + 1",
                "profile_action": "kept",
                "evidence": {"headers": ["a", "b"], "rows": []},
            }],
        }],
        "cross_sheet_findings": [],
        "image_assets": [{
            "asset_id": "img:a",
            "file": "Fig1.png",
            "path": "images/native/img-a.png",
            "preview_path": "images/preview/img-a.png",
            "source_type": "local_image",
            "source_url": None,
            "parent_file": None,
            "page": None,
            "figure_label": "Fig. 1",
            "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
            "width": 1,
            "height": 1,
            "mime": "image/png",
        }],
        "image_findings": [{
            "finding_id": "image:pair:1",
            "kind": "image_pair_similarity_signal",
            "severity": "medium",
            "rule": "two registered regions retain high structural similarity",
            "asset_ids": ["img:a"],
            "regions": [{"asset_id": "img:a", "box": [0, 0, 1, 1]}],
            "method": "panel_pair_similarity",
            "score": 0.97,
            "transform": "flip",
            "profile_action": "kept",
        }],
    }


def test_schema_types_are_importable():
    asset: ImageAsset = {"asset_id": "img:a", "file": "Fig1.png"}
    finding: ImageFinding = {"finding_id": "image:pair:1"}
    review: ImageReview = {"status": "partial"}
    assert asset["asset_id"] == "img:a"
    assert finding["finding_id"] == "image:pair:1"
    assert review["status"] == "partial"


def test_all_findings_includes_image_scope(tmp_path):
    items = _all_findings(_scan(tmp_path))
    image = [item for item in items if item["scope"] == "image"]
    assert len(image) == 1
    assert image[0]["finding"]["finding_id"] == "image:pair:1"


def test_image_finding_card_renders_registered_regions(tmp_path):
    item = {
        "scope": "image",
        "file": "Fig1.png",
        "sheet": "image",
        "block_rows": "native pixels",
        "block_cols": "native pixels",
        "header": [],
        "finding": _scan(tmp_path)["image_findings"][0],
    }
    html = _render_finding_card(item)
    assert "img:a" in html
    assert "[0, 0, 1, 1]" in html
    assert "score=0.97" in html
    assert "transform=flip" in html


def test_standard_report_rejects_in_root_registered_native_symlink(tmp_path):
    native_dir = tmp_path / "images" / "native"
    native_dir.mkdir(parents=True)
    target = native_dir / "target.png"
    Image.new("RGB", (8, 8), (30, 90, 150)).save(target)
    registered = native_dir / "registered.png"
    registered.symlink_to(target.name)
    scan = {
        "input_dir": str(tmp_path / "input"),
        "relations_blocks": [],
        "cross_sheet_findings": [],
        "image_assets": [
            {
                "asset_id": "img:registered",
                "file": "registered.png",
                "path": registered.relative_to(tmp_path).as_posix(),
            },
            {
                "asset_id": "img:target",
                "file": "target.png",
                "path": target.relative_to(tmp_path).as_posix(),
            },
        ],
        "image_findings": [{
            "finding_id": "image:pair:symlink",
            "kind": "image_pair_similarity_signal",
            "severity": "medium",
            "rule": "registered regions require contextual review",
            "asset_ids": ["img:registered", "img:target"],
            "regions": [
                {"asset_id": "img:registered", "box": [0, 0, 8, 8]},
                {"asset_id": "img:target", "box": [0, 0, 8, 8]},
            ],
            "score": 0.99,
            "transform": "identity",
            "profile_action": "kept",
        }],
    }
    out = tmp_path / "report.html"

    write_html_report(scan, str(out))

    assert "data:image/jpeg;base64," not in out.read_text(encoding="utf-8")


def test_standard_report_rejects_same_dimension_native_content_replacement(
    tmp_path,
):
    scan = _scan(tmp_path)
    native = tmp_path / scan["image_assets"][0]["path"]
    Image.new("RGB", (4, 2), (10, 20, 30)).save(native)
    scan["image_assets"][0].update(
        width=4,
        height=2,
        sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
    )
    scan["image_findings"][0]["regions"] = [
        {"asset_id": "img:a", "box": [0, 0, 2, 2]},
        {"asset_id": "img:a", "box": [2, 0, 4, 2]},
    ]
    Image.new("RGB", (4, 2), (200, 30, 40)).save(native)
    out = tmp_path / "report.html"

    write_html_report(scan, str(out))

    assert "data:image/" not in out.read_text(encoding="utf-8")


def test_standard_report_requires_every_supplied_region_to_validate(tmp_path):
    scan = _scan(tmp_path)
    native = tmp_path / scan["image_assets"][0]["path"]
    Image.new("RGB", (4, 2), (10, 20, 30)).save(native)
    scan["image_assets"][0].update(
        width=4,
        height=2,
        sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
    )
    scan["image_findings"][0]["regions"] = [
        {"asset_id": "img:a", "box": [0, 0, 2, 2]},
        {"asset_id": "img:a", "box": [2, 0, 4, 2]},
        {"asset_id": "img:a", "box": [0, 0, 5, 2]},
    ]
    out = tmp_path / "report.html"

    write_html_report(scan, str(out))

    assert "data:image/" not in out.read_text(encoding="utf-8")


def test_standard_report_single_region_uses_digest_bound_native_crop(tmp_path):
    scan = _scan(tmp_path)
    native = tmp_path / scan["image_assets"][0]["path"]
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    source = Image.new("RGB", (4, 2), (0, 0, 255))
    for x in range(2):
        for y in range(2):
            source.putpixel((x, y), (255, 0, 0))
    source.save(native)
    Image.new("RGB", (4, 2), (0, 255, 0)).save(preview)
    scan["image_assets"][0].update(
        width=4,
        height=2,
        sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
    )
    scan["image_findings"][0]["regions"] = [
        {"asset_id": "img:a", "box": [0, 0, 2, 2]},
    ]
    out = tmp_path / "report.html"

    write_html_report(scan, str(out))

    html = out.read_text(encoding="utf-8")
    marker = "data:image/jpeg;base64,"
    encoded = html.split(marker, 1)[1].split('"', 1)[0]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
        red, green, blue = crop.convert("RGB").getpixel((1, 1))
    assert red > 200
    assert green < 80
    assert blue < 80
    assert "data:image/png;base64," not in html


def test_standard_report_without_regions_may_use_registered_full_preview(
    tmp_path,
):
    scan = _scan(tmp_path)
    scan["image_findings"][0]["regions"] = []
    out = tmp_path / "report.html"

    write_html_report(scan, str(out))

    assert "data:image/png;base64," in out.read_text(encoding="utf-8")


def test_mixed_numeric_and_agent_only_image_findings_share_one_report(tmp_path):
    scan = _scan(tmp_path)
    verdict = {
        "title": "Synthetic mixed review",
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "Numeric and image evidence require contextual review.",
        "findings": [
            {
                "finding_type": "numeric",
                "title": "Numeric relation",
                "finding_ref": {"kind": "constant_offset"},
                "review_status": "needs_human",
                "impact_scope": "supporting",
                "report_md": "A numeric relation requires clarification.",
            },
            {
                "finding_type": "image",
                "title": "Image region pair",
                "image_refs": [{
                    "asset_id": "img:a",
                    "box": [0, 0, 1, 1],
                    "label": "A",
                }],
                "review_status": "unexpected-model-token",
                "impact_scope": "supporting",
                "report_md": "The registered region is unresolved at the available scale.",
            },
        ],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": ["img:a"],
            "unresolved_asset_ids": [],
            "unreadable_asset_ids": [],
            "deferred_asset_ids": [],
            "note": "all registered assets reviewed",
        },
    }
    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    assert html.count('class="finding-block"') == 2
    assert "constant_offset" in html
    assert "Image region pair" in html
    assert "data:image/png;base64," in html
    assert '<span class="badge review">unresolved</span>' in html
    assert "unexpected-model-token" not in html
    assert "completed" in html


def test_repeated_image_refs_render_once_and_preserve_first_order(tmp_path):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Registered image references",
            "image_refs": [
                {
                    "asset_id": "img:a",
                    "label": "First label",
                    "path": "ignored-first",
                },
                {
                    "asset_id": "img:a",
                    "label": "First label",
                    "path": "ignored-second",
                },
                {
                    "asset_id": "img:a",
                    "label": "Second label",
                },
            ],
            "report_md": "The registered images require contextual review.",
        }],
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert html.count('class="image-evidence"') == 2
    assert html.index("First label") < html.index("Second label")


def test_agent_only_image_finding_never_falls_back_to_numeric_evidence(tmp_path):
    scan = _scan(tmp_path)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Agent-only image observation",
            "image_refs": [{"asset_id": "missing", "box": [0, 0, 1, 1]}],
            "review_status": "needs_human",
            "report_md": "The image reference did not resolve.",
        }],
    }
    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    assert "图像证据引用未命中" in html
    assert "constant_offset" not in html


def test_numeric_legacy_fallback_remains_compatible(tmp_path):
    html = render_adjudicated_report(
        _scan(tmp_path),
        {"verdict": "NEEDS_HUMAN", "report_md": "Numeric review."},
        artifact_dir=str(tmp_path),
    )
    assert "constant_offset" in html


def test_completed_coverage_with_missing_assets_becomes_partial(tmp_path):
    scan = _scan(tmp_path)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": [],
            "unresolved_asset_ids": [],
            "unreadable_asset_ids": [],
            "deferred_asset_ids": [],
        },
    }
    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    assert "partial" in html
    assert "completed" not in html


def test_empty_modern_findings_render_no_blocks_or_unrelated_numeric_evidence(
    tmp_path,
):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "Coverage was recorded without adjudicated findings.",
        "findings": [],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": ["img:a"],
        },
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert 'class="finding-block"' not in html
    assert "constant_offset" not in html
    assert "Coverage was recorded" in html


@pytest.mark.parametrize("findings", [None, {}, "not-a-list"])
def test_modern_findings_key_requires_a_list(tmp_path, findings):
    with pytest.raises(ValueError, match="findings.*list"):
        render_adjudicated_report(
            _scan(tmp_path),
            {"verdict": "NEEDS_HUMAN", "findings": findings},
            artifact_dir=str(tmp_path),
        )


def test_modern_numeric_finding_without_match_does_not_use_legacy_fallback(
    tmp_path,
):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "The selected evidence was unavailable.",
        "findings": [{
            "finding_type": "numeric",
            "title": "Unmatched numeric observation",
            "finding_ref": {"sheet": "missing"},
            "report_md": "The selected evidence could not be matched.",
        }],
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert "无匹配证据" in html
    assert "constant_offset" not in html


def test_boxed_image_reference_renders_native_region_not_full_preview(tmp_path):
    scan = _scan(tmp_path)
    native = tmp_path / scan["image_assets"][0]["path"]
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    Image.new("RGB", (8, 4), (0, 255, 0)).save(preview)
    source = Image.new("RGB", (8, 4), (0, 0, 255))
    for x in range(4):
        for y in range(4):
            source.putpixel((x, y), (255, 0, 0))
    source.save(native)
    scan["image_assets"][0].update(
        width=8,
        height=4,
        sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
    )
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Native region",
            "image_refs": [{"asset_id": "img:a", "box": [0, 0, 4, 4]}],
            "report_md": "The native-pixel region requires contextual review.",
        }],
    }

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    marker = "data:image/png;base64,"
    encoded = html.split(marker, 1)[1].split('"', 1)[0]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
        assert crop.size == (4, 4)
        assert crop.convert("RGB").getpixel((2, 2)) == (255, 0, 0)
    assert "data:image/jpeg;base64," not in html


def test_boxed_reference_rejects_same_dimension_native_content_replacement(
    tmp_path,
):
    scan = _scan(tmp_path)
    native = tmp_path / scan["image_assets"][0]["path"]
    Image.new("RGB", (1, 1), (255, 0, 0)).save(native)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Replaced native region",
            "image_refs": [{"asset_id": "img:a", "box": [0, 0, 1, 1]}],
            "report_md": "The requested native region is unavailable.",
        }],
    }

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert "requested native-pixel region unavailable" in html
    assert "data:image/" not in html


@pytest.mark.parametrize(
    "registered_digest",
    [None, "", "a" * 63, "g" * 64],
    ids=["missing", "empty", "short", "non-hex"],
)
def test_registered_native_crop_rejects_missing_or_malformed_digest(
    tmp_path,
    registered_digest,
):
    scan = _scan(tmp_path)
    asset = scan["image_assets"][0]
    if registered_digest is None:
        asset.pop("sha256")
    else:
        asset["sha256"] = registered_digest
    budget = EvidenceBudget(1024)

    uri = _evidence.registered_native_crop_data_uri(
        asset,
        [0, 0, 1, 1],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


@pytest.mark.parametrize(
    "box",
    [
        [0, 0, 9, 4],
        [0, 0, 0, 4],
    ],
)
def test_invalid_box_never_falls_back_to_full_preview(tmp_path, box):
    scan = _scan(tmp_path)
    scan["image_assets"][0].update(width=1, height=1)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Invalid native region",
            "image_refs": [{"asset_id": "img:a", "box": box}],
            "report_md": "The requested native region is unavailable.",
        }],
    }

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert "requested native-pixel region unavailable" in html
    assert "data:image/" not in html


@pytest.mark.parametrize(
    "box",
    [
        [0, 0, 4],
        [0, 0, 4, 4.0],
        [True, 0, 4, 4],
        (0, 0, 4, 4),
    ],
    ids=["short", "float", "boolean", "non-list"],
)
def test_modern_image_region_box_requires_four_non_boolean_integers(
    tmp_path,
    box,
):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Invalid native region",
            "image_refs": [{"asset_id": "img:a", "box": box}],
            "report_md": "The requested native region is unavailable.",
        }],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan(tmp_path),
            verdict,
            artifact_dir=str(tmp_path),
        )

    assert str(exc.value) == (
        "verdict image_refs box must contain exactly four non-boolean integers"
    )


def test_invalid_region_metadata_is_rejected_before_text_inspection(tmp_path):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Invalid native region",
            "image_refs": [{
                "asset_id": "img:a",
                "box": [0, 0, 1, blocked],
            }],
            "report_md": "The requested native region is unavailable.",
        }],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(
            _scan(tmp_path),
            verdict,
            artifact_dir=str(tmp_path),
        )

    assert str(exc.value) == (
        "verdict image_refs box must contain exactly four non-boolean integers"
    )
    assert blocked not in str(exc.value).casefold()


def test_boxed_reference_rejects_registered_native_path_escape(tmp_path):
    scan = _scan(tmp_path)
    outside = tmp_path.parent / "outside-native.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(outside)
    scan["image_assets"][0].update(
        path="../outside-native.png",
        width=2,
        height=2,
    )
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Escaped native region",
            "image_refs": [{"asset_id": "img:a", "box": [0, 0, 1, 1]}],
            "report_md": "The requested native region is unavailable.",
        }],
    }

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert "requested native-pixel region unavailable" in html
    assert "data:image/" not in html


def test_missing_image_decoder_during_box_render_is_non_gating(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Native region",
            "image_refs": [{"asset_id": "img:a", "box": [0, 0, 1, 1]}],
            "report_md": "The requested native region is unavailable.",
        }],
    }
    real_import = __import__

    def missing_image(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PIL" and "Image" in fromlist:
            raise ImportError("synthetic missing image decoder")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", missing_image)

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert "requested native-pixel region unavailable" in html
    assert "Native region" in html


def test_public_renderer_missing_image_review_does_not_mutate_verdict(tmp_path):
    scan = _scan(tmp_path)
    scan["image_assets"].append({
        **scan["image_assets"][0],
        "asset_id": "img:0",
        "file": "Fig0.png",
        "sha256": "0" * 64,
    })
    verdict = {"verdict": "NEEDS_HUMAN", "findings": []}
    original = copy.deepcopy(verdict)

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert "<strong>partial</strong>" in html
    assert "deferred=2" in html
    assert verdict == original


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        (" NEEDS_HUMAN ", "needs_human"),
        ("unexpected-model-token", "unresolved"),
    ],
    ids=["case-normalized", "unknown"],
)
def test_image_finding_review_status_is_normalized(tmp_path, supplied, expected):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Image reference",
            "image_refs": [{"asset_id": "img:a"}],
            "review_status": supplied,
            "report_md": "The registered signal requires contextual review.",
        }],
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert f'<span class="badge review">{expected}</span>' in html
    assert f'<span class="badge review">{supplied.strip()}</span>' not in html


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        (" COMPLETED ", "completed"),
        ("unexpected-model-token", "partial"),
    ],
    ids=["case-normalized", "unknown"],
)
def test_image_review_status_is_normalized(tmp_path, supplied, expected):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [],
        "image_review": {
            "status": supplied,
            "reviewed_asset_ids": ["img:a"],
        },
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert f"<strong>{expected}</strong>" in html
    assert f"<strong>{supplied.strip()}</strong>" not in html


def test_duplicate_coverage_assignments_become_unresolved_and_partial(tmp_path):
    scan = _scan(tmp_path)
    scan["image_assets"].append({
        **scan["image_assets"][0],
        "asset_id": "img:b",
        "file": "Fig2.png",
        "sha256": "b" * 64,
    })
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": ["img:b", "img:a"],
            "unresolved_asset_ids": ["img:a"],
            "unreadable_asset_ids": ["img:a"],
            "deferred_asset_ids": ["img:a", "img:b"],
        },
    }
    original = copy.deepcopy(verdict)

    normalized = _normalized_verdict_copy(scan, verdict)

    assert normalized["image_review"] == {
        "status": "partial",
        "reviewed_asset_ids": [],
        "unresolved_asset_ids": ["img:a", "img:b"],
        "unreadable_asset_ids": [],
        "deferred_asset_ids": [],
    }
    assert verdict == original


def test_image_finding_ref_matches_exact_finding_id(tmp_path):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Registered image signal",
            "finding_ref": {"finding_id": "image:pair:1"},
            "image_refs": [{"asset_id": "img:a"}],
            "review_status": "needs_human",
            "report_md": "The registered signal requires contextual review.",
        }],
    }
    html = render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))
    assert "image_pair_similarity_signal" in html


def test_verdict_cannot_supply_an_arbitrary_image_path(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("PRIVATE-SENTINEL", encoding="utf-8")
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Image reference",
            "image_refs": [{
                "asset_id": "missing",
                "box": [0, 0, 1, 1],
                "path": str(secret),
            }],
            "review_status": "needs_human",
            "report_md": "Registered evidence is unavailable.",
        }],
    }
    html = render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))
    assert "PRIVATE-SENTINEL" not in html
    assert str(secret) not in html


def test_registered_preview_cannot_escape_artifact_root(tmp_path):
    scan = _scan(tmp_path)
    scan["image_assets"][0]["preview_path"] = "../secret.png"
    (tmp_path.parent / "secret.png").write_bytes(PNG_1X1)
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Image reference",
            "image_refs": [{"asset_id": "img:a"}],
            "review_status": "needs_human",
            "report_md": "Registered evidence is unavailable.",
        }],
    }
    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    assert "data:image/png;base64," not in html


def test_registered_preview_mime_cannot_inject_html_attributes(tmp_path):
    scan = _scan(tmp_path)
    scan["image_assets"][0]["preview_mime"] = 'image/png" data-injected="yes'
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "finding_type": "image",
            "title": "Image reference",
            "image_refs": [{"asset_id": "img:a"}],
            "review_status": "needs_human",
            "report_md": "Registered evidence is available.",
        }],
    }
    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))
    assert "data-injected=" not in html
    assert 'src="data:image/png;base64,' in html


def test_report_shares_preview_budget_across_image_findings(tmp_path, monkeypatch):
    scan = _scan(tmp_path)
    second_preview = tmp_path / "images" / "preview" / "img-b.png"
    second_preview.write_bytes(PNG_1X1)
    scan["image_assets"].append({
        **scan["image_assets"][0],
        "asset_id": "img:b",
        "file": "Fig2.png",
        "preview_path": "images/preview/img-b.png",
        "sha256": "b" * 64,
    })
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [
            {
                "finding_type": "image",
                "title": "First image reference",
                "image_refs": [{"asset_id": "img:a"}],
                "review_status": "needs_human",
            },
            {
                "finding_type": "image",
                "title": "Second image reference",
                "image_refs": [{"asset_id": "img:b"}],
                "review_status": "needs_human",
            },
        ],
    }
    encoded_size = len(base64.b64encode(PNG_1X1))
    budget_mb = (encoded_size + 0.5) / (1024 * 1024)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_EVIDENCE_MB", str(budget_mb))

    html = render_adjudicated_report(scan, verdict, artifact_dir=str(tmp_path))

    assert html.count("data:image/png;base64,") == 1
    assert html.count('class="image-unavailable"') == 1
    assert "First image reference" in html
    assert "Second image reference" in html


@pytest.mark.parametrize(
    "value",
    ["not-a-number", "inf", "-1", "1e10000"],
    ids=["malformed", "non-finite", "negative", "overflow"],
)
def test_standard_report_invalid_image_evidence_limit_suppresses_only_images(
    tmp_path,
    monkeypatch,
    value,
):
    scan = _scan(tmp_path)
    out = tmp_path / "report.html"
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_EVIDENCE_MB", value)

    write_html_report(scan, str(out))

    html = out.read_text(encoding="utf-8")
    assert "constant_offset" in html
    assert "data:image/" not in html


def test_standard_numeric_only_report_ignores_invalid_image_evidence_limit(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    scan["image_assets"] = []
    scan["image_findings"] = []
    out = tmp_path / "report.html"
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_EVIDENCE_MB",
        "not-a-number",
    )

    write_html_report(scan, str(out))

    assert "constant_offset" in out.read_text(encoding="utf-8")


def test_oversized_registered_preview_is_rejected_before_pillow_validation(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    budget = EvidenceBudget(1024)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_MB", "0")
    pillow_called = False
    original_open = Image.open

    def track_pillow_open(fp, *args, **kwargs):
        nonlocal pillow_called
        pillow_called = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", track_pillow_open)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not pillow_called
    assert budget.used_bytes == 0


def test_evidence_budget_can_consume_is_non_mutating():
    budget = EvidenceBudget(5)

    assert budget.can_consume(5)
    assert not budget.can_consume(6)
    assert budget.used_bytes == 0


def test_registered_preview_preflights_encoded_payload_budget_before_read(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    raw_size = preview.stat().st_size
    encoded_size = 4 * ((raw_size + 2) // 3)
    assert encoded_size > raw_size
    budget = EvidenceBudget(encoded_size - 1)
    original_fdopen = _evidence.os.fdopen
    payload_read = False

    class TrackingFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._fh.close()

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def read(self, requested=-1):
            nonlocal payload_read
            if requested == raw_size + 1:
                payload_read = True
            return self._fh.read(requested)

    monkeypatch.setattr(
        _evidence.os,
        "fdopen",
        lambda fd, *args, **kwargs: TrackingFile(
            original_fdopen(fd, *args, **kwargs)
        ),
    )

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not payload_read
    assert budget.used_bytes == 0


def test_registered_preview_charges_actual_base64_payload_length(tmp_path):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    encoded_size = 4 * ((preview.stat().st_size + 2) // 3)
    budget = EvidenceBudget(encoded_size)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is not None
    assert budget.used_bytes == len(uri.split(",", 1)[1]) == encoded_size


def test_zero_preview_budget_skips_pillow_and_payload_read(tmp_path, monkeypatch):
    scan = _scan(tmp_path)
    budget = EvidenceBudget(0)
    original_fdopen = _evidence.os.fdopen
    original_image_open = Image.open
    pillow_called = False
    payload_read = False

    class TrackingFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._fh.close()

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def read(self, *args, **kwargs):
            nonlocal payload_read
            payload_read = True
            return self._fh.read(*args, **kwargs)

    def track_fdopen(fd, *args, **kwargs):
        return TrackingFile(original_fdopen(fd, *args, **kwargs))

    def track_pillow_open(fp, *args, **kwargs):
        nonlocal pillow_called
        pillow_called = True
        return original_image_open(fp, *args, **kwargs)

    monkeypatch.setattr(_evidence.os, "fdopen", track_fdopen)
    monkeypatch.setattr(Image, "open", track_pillow_open)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not pillow_called
    assert not payload_read
    assert budget.used_bytes == 0


def test_native_crop_tiny_remaining_budget_skips_encoding(tmp_path, monkeypatch):
    scan = _scan(tmp_path)
    budget = EvidenceBudget(3)
    save_called = False

    def track_save(self, fp, *args, **kwargs):
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(Image.Image, "save", track_save)

    uri = _evidence.registered_native_crop_data_uri(
        scan["image_assets"][0],
        [0, 0, 1, 1],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not save_called
    assert budget.used_bytes == 0


def test_native_crop_writer_blocks_payload_above_remaining_budget(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    budget = EvidenceBudget(4)
    overflow_blocked = False

    def overflow_save(self, fp, *args, **kwargs):
        nonlocal overflow_blocked
        try:
            fp.write(b"1234")
        except ValueError:
            overflow_blocked = True
            raise

    monkeypatch.setattr(Image.Image, "save", overflow_save)

    uri = _evidence.registered_native_crop_data_uri(
        scan["image_assets"][0],
        [0, 0, 1, 1],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert overflow_blocked
    assert budget.used_bytes == 0


def test_registered_preview_rejects_oversized_dimensions_without_budget(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    Image.new("RGB", (11, 10), "white").save(preview)
    budget = EvidenceBudget(1024 * 1024)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "100")

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


def test_registered_preview_rejects_malformed_data_without_budget(tmp_path):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    preview.write_bytes(b"not an image")
    budget = EvidenceBudget(1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


def test_registered_preview_rejects_path_swap_after_secure_open(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    Image.new("RGB", (2, 2), (255, 0, 0)).save(preview)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (2, 2), (0, 0, 255)).save(outside)
    displaced = tmp_path / "displaced-preview.png"
    original_open = Image.open
    opened_from_file_object = False
    swapped = False

    def swap_during_header_open(fp, *args, **kwargs):
        nonlocal opened_from_file_object, swapped
        opened_from_file_object = hasattr(fp, "read")
        if opened_from_file_object and not swapped:
            preview.rename(displaced)
            preview.symlink_to(outside)
            swapped = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", swap_during_header_open)
    budget = EvidenceBudget(1024 * 1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert opened_from_file_object
    assert uri is None
    assert budget.used_bytes == 0


def test_registered_preview_short_read_does_not_consume_budget(
    tmp_path,
    monkeypatch,
):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    size = preview.stat().st_size
    original_fdopen = _evidence.os.fdopen
    short_read_triggered = False

    class ShortReadFile:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._fh.close()

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def read(self, requested=-1):
            nonlocal short_read_triggered
            if requested == size + 1:
                short_read_triggered = True
                return self._fh.read(max(0, size - 1))
            return self._fh.read(requested)

    def short_read_fdopen(fd, *args, **kwargs):
        return ShortReadFile(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(_evidence.os, "fdopen", short_read_fdopen)
    budget = EvidenceBudget(1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert short_read_triggered
    assert uri is None
    assert budget.used_bytes == 0


def test_registered_preview_rejects_preview_parent_swap(tmp_path, monkeypatch):
    scan = _scan(tmp_path)
    preview = tmp_path / scan["image_assets"][0]["preview_path"]
    Image.new("RGB", (2, 2), (255, 0, 0)).save(preview)
    preview_dir = preview.parent
    outside_dir = tmp_path / "outside-preview"
    outside_dir.mkdir()
    outside = outside_dir / preview.name
    Image.new("RGB", (2, 2), (0, 0, 255)).save(outside)
    displaced = tmp_path / "displaced-preview-dir"
    preview_path = str(preview.resolve())
    original_os_open = _evidence.os.open
    original_image_open = Image.open
    outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
    outside_consumed = False
    swapped = False

    def swap_parent_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        path_text = _evidence.os.fspath(path)
        if not swapped and (
            path_text == preview_path
            or (path_text == "preview" and kwargs.get("dir_fd") is not None)
        ):
            preview_dir.rename(displaced)
            preview_dir.symlink_to(outside_dir, target_is_directory=True)
            swapped = True
        return original_os_open(path, flags, *args, **kwargs)

    def track_image_open(fp, *args, **kwargs):
        nonlocal outside_consumed
        if hasattr(fp, "fileno"):
            opened = _evidence.os.fstat(fp.fileno())
            outside_consumed = (
                opened.st_dev,
                opened.st_ino,
            ) == outside_identity
        return original_image_open(fp, *args, **kwargs)

    monkeypatch.setattr(_evidence.os, "open", swap_parent_before_open)
    monkeypatch.setattr(Image, "open", track_image_open)
    budget = EvidenceBudget(1024 * 1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not outside_consumed
    assert budget.used_bytes == 0


def test_registered_preview_rejects_embedded_nul(tmp_path):
    scan = _scan(tmp_path)
    scan["image_assets"][0]["preview_path"] += "\x00outside"
    budget = EvidenceBudget(1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


@pytest.mark.parametrize(
    "path_kind",
    ["absolute", "empty", "dot", "empty-component", "parent-component"],
)
def test_registered_preview_rejects_unsafe_relative_components(
    tmp_path,
    path_kind,
):
    scan = _scan(tmp_path)
    preview = scan["image_assets"][0]["preview_path"]
    scan["image_assets"][0]["preview_path"] = {
        "absolute": str(tmp_path / preview),
        "empty": "",
        "dot": f"images/./preview/{Path(preview).name}",
        "empty-component": f"images//preview/{Path(preview).name}",
        "parent-component": f"images/preview/../preview/{Path(preview).name}",
    }[path_kind]
    budget = EvidenceBudget(1024 * 1024)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAPERCONAN_MAX_IMAGE_MB", "inf"),
        ("PAPERCONAN_MAX_IMAGE_MB", "not-a-number"),
        ("PAPERCONAN_MAX_IMAGE_MB", "1e10000"),
        ("PAPERCONAN_MAX_IMAGE_PIXELS", "inf"),
        ("PAPERCONAN_MAX_IMAGE_PIXELS", "not-a-number"),
        ("PAPERCONAN_MAX_IMAGE_PIXELS", "9" * 5000),
    ],
    ids=[
        "mb-non-finite",
        "mb-malformed",
        "mb-overflow",
        "pixels-non-finite",
        "pixels-malformed",
        "pixels-overflow",
    ],
)
def test_registered_preview_rejects_invalid_resource_limits(
    tmp_path,
    monkeypatch,
    name,
    value,
):
    scan = _scan(tmp_path)
    budget = EvidenceBudget(1024 * 1024)
    monkeypatch.setenv(name, value)

    uri = registered_preview_data_uri(
        scan["image_assets"][0],
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


def test_non_neutral_model_text_is_rejected_without_echo(tmp_path):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Image reference",
            "report_md": f"This text makes a {blocked} conclusion.",
        }],
    }
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))
    assert blocked not in str(exc.value).lower()
    assert "neutral-language policy" in str(exc.value)


def _assert_visible_text_is_rejected(tmp_path, verdict):
    blocked = "mis" + "conduct"
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))
    assert blocked not in str(exc.value).casefold()
    assert "neutral-language policy" in str(exc.value)


@pytest.mark.parametrize(
    "field",
    [
        "verdict",
        "paper_conclusion",
        "review_note",
        "title",
        "overall_impact",
        "finding.title",
        "finding.report_md",
        "finding.suspicion_tier",
        "finding.impact_scope",
        "finding.review_status",
        "finding.image_label",
        "image_review.note",
    ],
)
def test_multi_shape_visible_verdict_fields_are_validated(tmp_path, field):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "The signal requires contextual review.",
        "review_note": "Review note.",
        "title": "Review title",
        "overall_impact": "supporting",
        "findings": [{
            "finding_type": "numeric",
            "title": "Numeric relation",
            "report_md": "The signal requires contextual review.",
            "suspicion_tier": 2,
            "impact_scope": "supporting",
            "review_status": "needs_human",
        }],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": ["img:a"],
            "note": "Coverage note.",
        },
    }
    if field.startswith("finding."):
        key = field.removeprefix("finding.")
        if key == "image_label":
            verdict["findings"][0]["finding_type"] = "image"
            verdict["findings"][0]["image_refs"] = [{
                "asset_id": "img:a",
                "label": blocked,
            }]
        else:
            verdict["findings"][0][key] = blocked
    elif field == "image_review.note":
        verdict["image_review"]["note"] = blocked
    else:
        verdict[field] = blocked

    _assert_visible_text_is_rejected(tmp_path, verdict)


@pytest.mark.parametrize(
    "field",
    [
        "verdict",
        "review_note",
        "title",
        "overall_impact",
        "report_md",
        "suspicion_tier",
        "impact_scope",
        "review_status",
        "tier_why",
        "innocent_explanation",
        "needs_author_data",
    ],
)
def test_legacy_shape_visible_verdict_fields_are_validated(tmp_path, field):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "review_note": "Review note.",
        "title": "Review title",
        "overall_impact": "supporting",
        "report_md": "The signal requires contextual review.",
        "suspicion_tier": 2,
        "impact_scope": "supporting",
        "review_status": "needs_human",
        "tier_why": "The relation is unresolved.",
        "innocent_explanation": "A data assembly issue remains possible.",
        "needs_author_data": "Source values and mapping.",
    }
    verdict[field] = blocked

    _assert_visible_text_is_rejected(tmp_path, verdict)


@pytest.mark.parametrize("shape", ["multi", "legacy"])
def test_non_rendered_finding_id_does_not_trigger_visible_text_validation(
    tmp_path,
    shape,
):
    blocked = "mis" + "conduct"
    if shape == "multi":
        verdict = {
            "verdict": "NEEDS_HUMAN",
            "findings": [{
                "title": "Numeric relation",
                "finding_ref": {"finding_id": blocked},
                "report_md": "The signal requires contextual review.",
            }],
        }
    else:
        verdict = {
            "verdict": "NEEDS_HUMAN",
            "finding_refs": [{"finding_id": blocked}],
            "report_md": "The signal requires contextual review.",
        }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert blocked not in html.casefold()


def test_legacy_non_rendered_paper_conclusion_is_ignored(tmp_path):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": f"This {blocked} text is not rendered in legacy shape.",
        "report_md": "The signal requires contextual review.",
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert blocked not in html.casefold()


def test_markdown_delimiters_remain_visible_in_plain_title(tmp_path):
    blocked = "mis" + "conduct"
    split = blocked[:3] + "**" + blocked[3:6] + "**" + blocked[6:]
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": split,
            "report_md": "The signal requires contextual review.",
        }],
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert split in html
    assert blocked not in html.casefold()


def test_non_neutral_plain_title_is_rejected_without_echo(tmp_path):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": blocked,
            "report_md": "The signal requires contextual review.",
        }],
    }

    _assert_visible_text_is_rejected(tmp_path, verdict)


def test_non_neutral_markdown_split_text_is_rejected_without_echo(tmp_path):
    blocked = "mis" + "conduct"
    split = blocked[:3] + "**" + blocked[3:6] + "**" + blocked[6:]
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Image reference",
            "report_md": f"This text makes a {split} conclusion.",
        }],
    }
    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))
    assert blocked not in str(exc.value).lower()
    assert "neutral-language policy" in str(exc.value)


@pytest.mark.parametrize(
    "trusted_html",
    [
        "mis<script>hidden</script>conduct",
        "mis<style>hidden</style>conduct",
        "mis<!--hidden-->conduct",
    ],
    ids=["script", "style", "comment"],
)
def test_visible_text_ignores_hidden_html_without_breaking_continuity(trusted_html):
    parser = _VisibleTextParser()

    parser.feed(trusted_html)
    parser.close()

    assert parser.text() == "mis" + "conduct"


def test_non_neutral_inline_code_split_text_is_rejected_without_echo(tmp_path):
    blocked = "mis" + "conduct"
    split = blocked[:3] + "`" + blocked[3:6] + "`" + blocked[6:]
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Image reference",
            "report_md": f"This text makes a {split} conclusion.",
        }],
    }

    with pytest.raises(ValueError) as exc:
        render_adjudicated_report(_scan(tmp_path), verdict, artifact_dir=str(tmp_path))

    assert blocked not in str(exc.value).lower()
    assert "neutral-language policy" in str(exc.value)


def test_neutral_text_does_not_join_adjacent_list_items(tmp_path):
    blocked = "mis" + "conduct"
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Image reference",
            "report_md": f"- {blocked[:3]}\n- {blocked[3:]}",
        }],
    }

    html = render_adjudicated_report(
        _scan(tmp_path),
        verdict,
        artifact_dir=str(tmp_path),
    )

    assert "<li>mis</li><li>conduct</li>" in html
