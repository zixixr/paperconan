# Adaptive Image Diagnostics Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an adaptive image-review path that inventories lawful/local image assets, emits optional deterministic similarity signals, lets an external multimodal Agent adjudicate any registered image region, and renders numeric plus image findings in the existing unified report.

**Architecture:** Extend the current `scan_dir`, `paperconan.fetch`, `scan.json`, `verdict.json findings[]`, and report renderers rather than creating parallel pipelines. Image assets are content-addressed and bounded; deterministic diagnostics are optional, non-gating hints; semantic review stays in the user's multimodal Agent and is represented by `image_refs` plus `image_review` coverage.

**Tech Stack:** Python >=3.10, existing stdlib fetch stack, `TypedDict`, Pillow >=12, pypdfium2 >=5, OpenCV headless >=4.10, pytest, existing self-contained HTML renderers.

**Spec:** `docs/superpowers/specs/2026-07-10-adaptive-image-diagnostics-design.md`

## Global Constraints

- PaperConan never calls or configures a model provider; all multimodal judgments come from the external Agent.
- Image findings must use the existing `scan.json`, `verdict.json findings[]`, and unified adjudicated HTML report.
- Extend `src/paperconan/fetch/`; do not add a second acquisition framework.
- Prefer lawful public online resources, then user-authorized local files; stop at authentication, access-control, or challenge pages.
- Deterministic image diagnostics are non-gating and must never remove an asset from `image_assets[]`.
- Unknown image finding statuses normalize to `unresolved`; unknown coverage status normalizes to `partial`.
- Native assets and model crops retain original pixels; report previews are separate bounded derivatives.
- Renderer file reads are limited to paths registered in `scan.json image_assets[]` and rooted under the scan artifact directory.
- All user-facing and committed language must remain neutral: statistical signal, data inconsistency, unresolved image similarity, or request for clarification.
- Existing numeric-only CLI, JSON, report appearance, fetch defaults, and library calls remain backward compatible.
- New resource limits are `PAPERCONAN_MAX_IMAGE_MB`, `PAPERCONAN_MAX_IMAGE_PIXELS`,
  `PAPERCONAN_MAX_IMAGE_ASSETS`, `PAPERCONAN_MAX_IMAGE_TOTAL_MB`,
  `PAPERCONAN_MAX_IMAGE_FINDINGS`, `PAPERCONAN_MAX_IMAGE_COMPARISONS`, and
  `PAPERCONAN_MAX_IMAGE_EVIDENCE_MB`. The total artifact budget defaults to `1500` MiB and covers
  native copies, previews, PDF render staging, diagnostic native crops, and montage evidence. The
  scan-wide attempted-comparison ceiling defaults to `100000`.

---

## File Structure

- Create `src/paperconan/image/__init__.py` - internal image capability boundary and lazy dependency error.
- Create `src/paperconan/image/_evidence.py` - safe registered-path resolution, data-URI previews, native crops, bounded evidence montage.
- Create `src/paperconan/image/_assets.py` - local image discovery, hashing, native copies, previews, PDF page rendering, manifest records, resource caps.
- Create `src/paperconan/image/_diagnostics.py` - optional panel proposals and transform-robust similarity signals; never filters assets.
- Modify `src/paperconan/schema.py` - `ImageAsset`, `ImageRegion`, `ImageFinding`, and `ImageReview` `TypedDict` contracts.
- Modify `src/paperconan/_audit.py` - add `images=False` and `image_diagnostics=False` to `scan_dir`, support image-only scans, emit unified scan output, and add CLI flags.
- Modify `src/paperconan/_html.py` - add the `image` finding scope to the deterministic evidence browser.
- Modify `src/paperconan/_adjudicated_html.py` - image refs, coverage, status normalization, neutral-text validation, safe preview rendering, and no numeric fallback for image-only findings.
- Modify `src/paperconan/fetch/_files.py` - image/document classification without changing `is_tabular()`.
- Modify `src/paperconan/fetch/_sources.py` - normalized `image_files`.
- Modify `src/paperconan/fetch/_nature.py` - public Nature figure link discovery through the existing HTTP helper.
- Modify `src/paperconan/fetch/_download.py` - `include_images`, archive member selection, enriched provenance.
- Modify `src/paperconan/fetch/_cli.py` - additive `--images` flag.
- Modify `pyproject.toml` and `uv.lock` - optional image extra and test/dev coverage.
- Create `tests/test_image_report.py` - unified schema/report behavior and renderer boundaries.
- Create `tests/test_image_assets.py` - asset preparation, PDF pages, limits, deterministic IDs.
- Create `tests/test_image_diagnostics.py` - non-gating deterministic signals and native crop fidelity.
- Create `tests/test_image_workflow.py` - mixed numeric/image end-to-end workflow.
- Modify `tests/fetch/test_files.py`, `tests/fetch/test_download.py`, `tests/fetch/test_cli.py`, and `tests/test_fetch_nature.py`.
- Modify `tests/test_module_boundaries.py`, `tests/test_packaging.py`, and `tests/test_skill_docs.py`.
- Modify `skills/paperconan/SKILL.md`, `skills/paperconan/references/output-schema.md`, `skills/paperconan/references/report-templates.md`.
- Modify `README.md`, `docs/cli.md`, and `docs/reports.md`.

## Shared Interfaces

```python
# src/paperconan/schema.py
class ImageRegion(TypedDict):
    asset_id: str
    box: list[int]  # [x0, y0, x1, y1], native-pixel coordinates


class ImageAsset(TypedDict, total=False):
    asset_id: str
    file: str
    source_files: list[str]
    path: str
    preview_path: str
    preview_mime: str
    source_type: Literal["local_image", "pdf_page", "fetched_image"]
    source_url: str | None
    parent_file: str | None
    page: int | None
    render_dpi: int | None
    figure_label: str | None
    sha256: str
    width: int
    height: int
    exif_orientation: int
    mime: str


class ImageFinding(TypedDict, total=False):
    finding_id: str
    kind: str
    severity: str
    rule: str
    asset_ids: list[str]
    regions: list[ImageRegion]
    method: str
    score: float
    transform: str
    evidence: dict[str, str]
    profile_action: ProfileAction


class ImageReview(TypedDict, total=False):
    status: Literal["completed", "partial", "unavailable_no_multimodal", "not_requested"]
    reviewed_asset_ids: list[str]
    unresolved_asset_ids: list[str]
    unreadable_asset_ids: list[str]
    deferred_asset_ids: list[str]
    note: str
```

- `scan_dir(in_dir, out_dir, *, write_md=False, write_html=True, paper=None, profile="review", write_json=True, evidence=True, images=False, image_diagnostics=False)`
- `render_adjudicated_report(scan, verdict, *, artifact_dir=None) -> str`
- `write_adjudicated_report(scan, verdict, out_path, *, artifact_dir=None) -> None`
- `prepare_image_assets(in_dir: str, out_dir: str, *, provenance: dict | None = None, render_pdf: bool = True) -> tuple[list[ImageAsset], list[dict]]`
- `diagnose_image_assets(assets: list[ImageAsset], artifact_dir: str) -> tuple[list[ImageFinding], list[dict]]`
- `download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX, archive_max=_ARCHIVE_MAX, include_images=False)`

---

### Task 1: Unified Image Schema And Existing Report Integration

**Files:**
- Create: `src/paperconan/image/__init__.py`
- Create: `src/paperconan/image/_evidence.py`
- Modify: `src/paperconan/schema.py`
- Modify: `src/paperconan/_html.py`
- Modify: `src/paperconan/_adjudicated_html.py`
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_image_report.py`
- Modify: `tests/test_module_boundaries.py`
- Modify: `tests/test_adjudicated_report.py`

**Interfaces:**
- Produces: the shared `Image*` `TypedDict` names.
- Produces: `_normalize_image_review_status(value: object) -> str`.
- Produces: `_normalize_image_review(review: object, known_asset_ids: set[str]) -> dict`.
- Produces: `_validate_neutral_verdict(verdict: dict) -> None`.
- Produces: `registered_preview_data_uri(asset, artifact_dir, budget) -> str | None`.
- Extends: `_all_findings(scan)` with `scope == "image"`.
- Extends: `_finding_matches_ref()` with exact `finding_id`.
- Extends: report functions with keyword-only `artifact_dir`.
- Preserves: numeric legacy fallback when the verdict finding is not an image finding.

- [ ] **Step 1: Write failing schema and deterministic report tests**

Create `tests/test_image_report.py` with these fixtures and tests:

```python
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from paperconan._adjudicated_html import render_adjudicated_report
from paperconan._html import _all_findings
from paperconan.schema import ImageAsset, ImageFinding, ImageReview


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _scan(tmp_path: Path) -> dict:
    preview = tmp_path / "images" / "preview" / "img-a.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(PNG_1X1)
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
            "sha256": "a" * 64,
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
```

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
uv run python -m pytest tests/test_image_report.py::test_schema_types_are_importable tests/test_image_report.py::test_all_findings_includes_image_scope -q
```

Expected: collection fails because the `Image*` types do not exist; after only those types are added, the second test still fails because `_all_findings()` has no image scope.

- [ ] **Step 3: Add schema contracts and image finding extraction**

Add the shared `ImageRegion`, `ImageAsset`, `ImageFinding`, and `ImageReview` definitions shown above to `src/paperconan/schema.py`.

Append this loop to `src/paperconan/_html.py::_all_findings()` after cross-sheet findings:

```python
    assets = {
        str(asset.get("asset_id")): asset
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }
    for image_finding in scan.get("image_findings", []) or []:
        asset_ids = [str(x) for x in image_finding.get("asset_ids", []) or []]
        files = [assets[x].get("file", x) for x in asset_ids if x in assets]
        out.append({
            "scope": "image",
            "file": " / ".join(files) or "registered image asset",
            "sheet": "image",
            "block_rows": "native pixels",
            "block_cols": "native pixels",
            "header": [],
            "finding": image_finding,
        })
```

Add an `image` branch to `_render_finding_card()`:

```python
    if item["scope"] == "image":
        regions = f.get("regions") or []
        chips = "".join(
            f'<span class="val-chip">{_esc(r.get("asset_id"))} '
            f'{_esc(r.get("box"))}</span>'
            for r in regions
        )
        evidence_html = (
            f'<div class="shared-values">{chips}</div>'
            if chips else '<p class="no-evidence">no registered image region</p>'
        )
        loc = _esc(file_)
        extra_meta = (
            f' · score={_esc(f.get("score"))}'
            f' · transform={_esc(f.get("transform"))}'
        )
    elif item["scope"] == "cross_sheet":
```

Keep the existing cross-sheet and block branches unchanged below it.

- [ ] **Step 4: Run the extraction tests and verify green**

Run:

```bash
uv run python -m pytest tests/test_image_report.py::test_schema_types_are_importable tests/test_image_report.py::test_all_findings_includes_image_scope -q
```

Expected: `2 passed`.

- [ ] **Step 5: Add failing tests for mixed verdicts, status normalization, and coverage**

Append:

```python
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
    assert "unresolved" in html
    assert "completed" in html


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
```

- [ ] **Step 6: Run the mixed report tests and verify red**

Run:

```bash
uv run python -m pytest tests/test_image_report.py -q
```

Expected: the mixed and agent-only tests fail because image status, coverage, preview rendering, and image-specific fallback behavior are not implemented.

- [ ] **Step 7: Implement safe preview resolution**

Create `src/paperconan/image/__init__.py`:

```python
"""Internal image asset, evidence, and diagnostic helpers."""


class ImageDependencyError(RuntimeError):
    """Raised when an explicitly requested image operation lacks its optional extra."""
```

Create `src/paperconan/image/_evidence.py`:

```python
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path


class EvidenceBudget:
    def __init__(self, max_bytes: int):
        self.max_bytes = max(0, int(max_bytes))
        self.used_bytes = 0

    def consume(self, size: int) -> bool:
        if size < 0 or self.used_bytes + size > self.max_bytes:
            return False
        self.used_bytes += size
        return True


def resolve_registered_path(artifact_dir: str | None, relative_path: object) -> Path | None:
    if not artifact_dir or not isinstance(relative_path, str) or not relative_path:
        return None
    root = Path(artifact_dir).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def registered_preview_data_uri(
    asset: dict,
    artifact_dir: str | None,
    budget: EvidenceBudget,
) -> str | None:
    path = resolve_registered_path(artifact_dir, asset.get("preview_path"))
    if path is None:
        return None
    size = path.stat().st_size
    if not budget.consume(size):
        return None
    mime = asset.get("preview_mime") or mimetypes.guess_type(path.name)[0] or "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"
```

- [ ] **Step 8: Implement verdict normalization and neutral-text validation**

In `src/paperconan/_adjudicated_html.py`, add:

```python
import copy

from .image._evidence import EvidenceBudget, registered_preview_data_uri


_IMAGE_FINDING_STATUSES = {"needs_human", "explained", "different", "unresolved"}
_IMAGE_REVIEW_STATUSES = {
    "completed", "partial", "unavailable_no_multimodal", "not_requested",
}
_REPORT_TERM_HEX = (
    "6672617564",
    "6661627269636174696f6e",
    "66616b6564",
    "66616c736966696564",
    "6d6973636f6e64756374",
    "6775696c7479",
    "e980a0e58187",
)
_REPORT_TERMS = tuple(bytes.fromhex(value).decode("utf-8") for value in _REPORT_TERM_HEX)


def _is_image_verdict_finding(finding: dict[str, Any]) -> bool:
    return finding.get("finding_type") == "image" or bool(finding.get("image_refs"))


def _normalize_image_review_status(value: object) -> str:
    status = str(value or "").strip().lower()
    return status if status in _IMAGE_FINDING_STATUSES else "unresolved"


def _normalize_image_review(review: object, known_asset_ids: set[str]) -> dict[str, Any]:
    source = review if isinstance(review, dict) else {}
    status = str(source.get("status") or "").strip().lower()
    if status not in _IMAGE_REVIEW_STATUSES:
        status = "partial"
    result = {"status": status}
    assigned: set[str] = set()
    for key in (
        "reviewed_asset_ids",
        "unresolved_asset_ids",
        "unreadable_asset_ids",
        "deferred_asset_ids",
    ):
        values = source.get(key) if isinstance(source.get(key), list) else []
        normalized = sorted({
            str(x) for x in values
            if str(x) in known_asset_ids and str(x) not in assigned
        })
        result[key] = normalized
        assigned.update(normalized)
    missing = sorted(known_asset_ids - assigned)
    if missing:
        result["deferred_asset_ids"] = sorted(
            set(result["deferred_asset_ids"] + missing)
        )
        if status == "completed":
            result["status"] = "partial"
    if source.get("note"):
        result["note"] = str(source["note"])
    return result


def _iter_verdict_text(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_verdict_text(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_verdict_text(nested)


def _validate_neutral_verdict(verdict: dict[str, Any]) -> None:
    text = "\n".join(_iter_verdict_text(verdict)).casefold()
    if any(term.casefold() in text for term in _REPORT_TERMS):
        raise ValueError(
            "verdict text violates the neutral-language policy; rewrite it as a "
            "statistical signal, data inconsistency, unresolved similarity, or "
            "request for clarification"
        )


def _normalized_verdict_copy(scan: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(verdict)
    known = {
        str(asset.get("asset_id"))
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }
    for finding in normalized.get("findings", []) or []:
        if _is_image_verdict_finding(finding):
            finding["review_status"] = _normalize_image_review_status(
                finding.get("review_status")
            )
    if "image_review" in normalized:
        normalized["image_review"] = _normalize_image_review(
            normalized.get("image_review"), known
        )
    _validate_neutral_verdict(normalized)
    return normalized
```

At the start of `render_adjudicated_report()`:

```python
    verdict = _normalized_verdict_copy(scan, verdict)
```

- [ ] **Step 9: Match image finding IDs and render registered image evidence**

Extend `_finding_matches_ref()`:

```python
    if ref.get("finding_id"):
        checks.append(str(ref["finding_id"]) == str(f.get("finding_id")))
```

Add:

```python
def _image_asset_map(scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(asset["asset_id"]): asset
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }


def _render_image_refs(
    scan: dict[str, Any],
    finding: dict[str, Any],
    artifact_dir: str | None,
    budget: EvidenceBudget,
) -> str:
    assets = _image_asset_map(scan)
    cards = []
    for ref in finding.get("image_refs", []) or []:
        asset = assets.get(str(ref.get("asset_id")))
        if asset is None:
            continue
        uri = registered_preview_data_uri(asset, artifact_dir, budget)
        img = (
            f'<img class="image-preview" src="{uri}" alt="{_esc(asset.get("file"))}">'
            if uri else '<div class="image-unavailable">preview unavailable</div>'
        )
        cards.append(
            '<figure class="image-evidence">'
            f'{img}<figcaption>{_esc(ref.get("label") or asset.get("file"))} '
            f'· {_esc(ref.get("box") or "full image")}</figcaption></figure>'
        )
    if not cards:
        return '<p class="no-evidence">图像证据引用未命中</p>'
    return '<div class="image-grid">' + "".join(cards) + "</div>"


def _render_image_review(review: dict[str, Any] | None) -> str:
    if not review:
        return ""
    return (
        '<section class="panel image-review">'
        '<h2>图像语义复核覆盖</h2>'
        f'<p><strong>{_esc(review.get("status"))}</strong></p>'
        f'<p>{_esc(review.get("note"))}</p>'
        f'<p>reviewed={len(review.get("reviewed_asset_ids") or [])} · '
        f'unresolved={len(review.get("unresolved_asset_ids") or [])} · '
        f'unreadable={len(review.get("unreadable_asset_ids") or [])} · '
        f'deferred={len(review.get("deferred_asset_ids") or [])}</p>'
        "</section>"
    )
```

Thread `scan`, `artifact_dir`, and one shared `EvidenceBudget` through `_render_unified()` and `_render_finding_block()`. In `_render_finding_block()` use:

```python
    is_image = _is_image_verdict_finding(finding)
    if is_image:
        evidence = _render_image_refs(scan, finding, artifact_dir, image_budget)
        if matched is not None:
            evidence += _render_key_finding(matched, idx)
    else:
        if matched is None and scan_findings:
            matched = scan_findings[0]
        evidence = (
            _render_key_finding(matched, idx)
            if matched is not None
            else '<p class="no-evidence">无匹配证据（finding_ref 未命中扫描结果）</p>'
        )
```

Add coverage to `_render_unified()`:

```python
    coverage = _render_image_review(verdict.get("image_review"))
    main_html = f"""<section class="panel">
    <h2>论文主结论</h2>
    {conclusion}
    {summary_html}
    {index_html}
  </section>
  {coverage}
  {blocks}
  <section class="panel" style="margin-top:18px">
    <h2>方法与背景</h2>
    {note}<div class="kv">{kv_html}</div>
  </section>"""
```

Add CSS:

```css
.image-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:12px; margin:12px 0; }
.image-evidence { margin:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.image-preview { display:block; width:100%; height:auto; max-height:420px; object-fit:contain;
  background:#f8fafc; }
.image-evidence figcaption { padding:8px 10px; color:var(--muted); font-size:12px; }
.image-unavailable { padding:32px 12px; color:var(--muted); text-align:center; }
```

Use:

```python
max_image_bytes = int(
    float(os.environ.get("PAPERCONAN_MAX_IMAGE_EVIDENCE_MB", "20")) * 1024 * 1024
)
image_budget = EvidenceBudget(max_image_bytes)
```

- [ ] **Step 10: Pass the scan artifact directory from CLI and writer**

Change `render_adjudicated_report(scan, verdict)` to `render_adjudicated_report(scan, verdict, *, artifact_dir=None)` and change `write_adjudicated_report(scan, verdict, out_path)` to `write_adjudicated_report(scan, verdict, out_path, *, artifact_dir=None)`. Retain their existing bodies and thread `artifact_dir` through the image-rendering calls added in Steps 8-9.

In `src/paperconan/_audit.py::main()`:

```python
        write_adjudicated_report(
            scan,
            verdict,
            rargs.out,
            artifact_dir=os.path.dirname(os.path.abspath(rargs.scan_json)),
        )
```

Existing library calls remain valid because `artifact_dir` is optional.

- [ ] **Step 11: Add failing safety tests**

Append:

```python
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
```

- [ ] **Step 12: Run report and compatibility tests**

Run:

```bash
uv run python -m pytest tests/test_image_report.py tests/test_adjudicated_report.py tests/test_adjudicated_report_unified.py tests/test_module_boundaries.py -q
```

Expected: all tests pass. Existing numeric fallback tests remain green; image-only findings never inherit unrelated numeric evidence.

- [ ] **Step 13: Commit Task 1**

```bash
git add src/paperconan/schema.py src/paperconan/image/__init__.py src/paperconan/image/_evidence.py src/paperconan/_html.py src/paperconan/_adjudicated_html.py src/paperconan/_audit.py tests/test_image_report.py tests/test_module_boundaries.py tests/test_adjudicated_report.py
git commit -m "feat(report): integrate image findings into unified adjudication"
```

---

### Task 2: Extend Existing Fetch And Prepare Image Assets

**Files:**
- Create: `src/paperconan/image/_assets.py`
- Modify: `src/paperconan/image/_evidence.py`
- Modify: `src/paperconan/fetch/_files.py`
- Modify: `src/paperconan/fetch/_sources.py`
- Modify: `src/paperconan/fetch/_nature.py`
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `src/paperconan/fetch/_cli.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/test_image_assets.py`
- Modify: `tests/fetch/test_files.py`
- Modify: `tests/fetch/test_download.py`
- Modify: `tests/fetch/test_cli.py`
- Modify: `tests/test_fetch_nature.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: `IMAGE_EXTS`, `DOCUMENT_EXTS`, `is_image(name)`, `asset_type(name)`.
- Produces: `prepare_image_assets(in_dir, out_dir, *, provenance=None, render_pdf=True) -> (assets, errors)`.
- Produces: `paperconan_source.json.downloads[]` entries with `file`, `source_url`, `content_type`, `asset_type`, and `size`.
- Extends: `download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX, archive_max=_ARCHIVE_MAX, include_images=False)` while preserving `tabular_only`.
- Extends: `scan_dir(in_dir, out_dir, *, write_md=False, write_html=True, paper=None, profile="review", write_json=True, evidence=True, images=False, image_diagnostics=False)`.
- CLI: `paperconan fetch QUERY --images`; `paperconan DIR --images`.

- [ ] **Step 1: Write failing file-classification and fetch selection tests**

Append to `tests/fetch/test_files.py`:

```python
def test_image_and_document_classification_does_not_change_tabular_behavior():
    assert _files.is_image("Fig1.PNG")
    assert _files.is_image("panel.tiff")
    assert _files.asset_type("panel.webp") == "image"
    assert _files.asset_type("supplement.pdf") == "document"
    assert _files.asset_type("table.csv") == "tabular"
    assert _files.asset_type("movie.mp4") == "other"
    assert not _files.is_tabular("Fig1.PNG")
```

Append to `tests/fetch/test_download.py`:

```python
def test_download_candidate_images_are_additive_and_default_stays_tabular(monkeypatch, tmp_path):
    calls = []

    def stub_download(url, dest, **kwargs):
        open(dest, "wb").write(b"x")
        calls.append(dest)
        return {
            "ok": True,
            "path": dest,
            "size": 1,
            "content_type": "application/octet-stream",
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{"name": "data.csv", "download_url": "https://x/data.csv"}],
        "image_files": [{"name": "Fig1.png", "download_url": "https://x/Fig1.png"}],
        "all_files": [
            {"name": "data.csv", "download_url": "https://x/data.csv"},
            {"name": "Fig1.png", "download_url": "https://x/Fig1.png"},
        ],
    }

    default_dir = tmp_path / "default"
    default = _download.download_candidate(cand, str(default_dir))
    assert [Path(p).name for p in default["downloaded"]] == ["data.csv"]

    image_dir = tmp_path / "images"
    image = _download.download_candidate(cand, str(image_dir), include_images=True)
    assert sorted(Path(p).name for p in image["downloaded"]) == ["Fig1.png", "data.csv"]


def test_image_archive_same_basenames_do_not_overwrite(monkeypatch, tmp_path):
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("figures/Fig1.png", b"first-image")
        archive.writestr("supplement/Fig1.png", b"second-image")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload.getvalue())
        return {"ok": True, "path": dest, "size": len(payload.getvalue())}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "tabular_files": [],
        "image_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles",
            "name": "supplementary.zip",
        },
    }
    summary = _download.download_candidate(
        candidate,
        str(tmp_path),
        include_images=True,
    )
    names = sorted(Path(path).name for path in summary["downloaded"])
    assert len(names) == 2
    assert names[0] == "Fig1.png"
    assert names[1].startswith("Fig1-")
```

- [ ] **Step 2: Run classification/fetch tests and verify red**

Run:

```bash
uv run python -m pytest tests/fetch/test_files.py tests/fetch/test_download.py::test_download_candidate_images_are_additive_and_default_stays_tabular -q
```

Expected: failures because image classification and `include_images` do not exist.

- [ ] **Step 3: Implement classification and normalized candidate image files**

Replace the constants and helpers in `src/paperconan/fetch/_files.py` with:

```python
TABULAR_EXTS = {"xlsx", "csv", "tsv"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "webp"}
DOCUMENT_EXTS = {"pdf"}


def is_image(name: str) -> bool:
    return ext_of(name) in IMAGE_EXTS


def asset_type(name: str) -> str:
    ext = ext_of(name)
    if ext in TABULAR_EXTS:
        return "tabular"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOCUMENT_EXTS:
        return "document"
    return "other"
```

Keep `ext_of()`, `is_tabular()`, and `make_fileref()` backward compatible.

In `src/paperconan/fetch/_sources.py::_candidate()`:

```python
    tabular = [f for f in all_files if f["ext"] in TABULAR_EXTS]
    images = [f for f in all_files if is_image(f.get("name") or "")]
    return {
        "cand_id": f"{source}:{cid}",
        "source": source,
        "id": str(cid),
        "doi": doi,
        "title": title or "",
        "authors": authors or [],
        "published": published,
        "tabular_files": tabular,
        "image_files": images,
        "all_files": all_files,
        "all_files_count": len(all_files),
        "related_dois": related or [],
        "match_signals": None,
    }
```

Import `is_image` from `_files`.

- [ ] **Step 4: Implement additive image download selection and provenance**

Update `download_file()` success payload:

```python
                return {
                    "ok": True,
                    "path": dest_path,
                    "size": total,
                    "content_type": ctype.split(";", 1)[0].strip(),
                    "source_url": url,
                }
```

Add:

```python
def _selected_files(cand, *, tabular_only: bool, include_images: bool) -> list[dict]:
    if not tabular_only:
        return list(cand.get("all_files") or cand.get("tabular_files") or [])
    selected = list(cand.get("tabular_files") or [])
    if include_images:
        selected.extend(cand.get("image_files") or [])
        selected.extend(
            f for f in cand.get("all_files") or []
            if asset_type(f.get("name") or "") == "document"
        )
    out, seen = [], set()
    for ref in selected:
        key = (ref.get("download_url"), ref.get("name"))
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out
```

Change the signature:

```python
def download_candidate(
    cand,
    out_dir,
    tabular_only=True,
    max_bytes=_DEFAULT_MAX,
    archive_max=_ARCHIVE_MAX,
    include_images=False,
):
```

Use `_selected_files(cand, tabular_only=tabular_only, include_images=include_images)`. Track successful downloads:

```python
    provenance_files = []
    files = _selected_files(
        cand,
        tabular_only=tabular_only,
        include_images=include_images,
    )
    os.makedirs(out_dir, exist_ok=True)
    downloaded, skipped = [], []
    for f in files:
        dest = os.path.join(out_dir, os.path.basename(f["name"]))
        res = download_file(f["download_url"], dest, max_bytes=max_bytes)
        if res.get("ok"):
            downloaded.append(res["path"])
            provenance_files.append({
                "file": os.path.basename(res["path"]),
                "source_url": res.get("source_url") or f.get("download_url"),
                "content_type": res.get("content_type"),
                "asset_type": asset_type(f.get("name") or ""),
                "size": res.get("size"),
            })
```

Change `_write_source_sidecar(cand, out_dir, downloads=None)` so the existing keys remain and:

```python
    prov["downloads"] = sorted(downloads or [], key=lambda x: x["file"])
```

Write the final sidecar after downloads and archive extraction, not before.

Before storing a source URL, remove query strings and fragments so signed access parameters never enter committed or archived provenance:

```python
def _safe_source_url(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
```

Apply `_safe_source_url()` to every `downloads[].source_url`.

- [ ] **Step 5: Generalize safe archive extraction**

Replace `_extract_tabular_zip()` with:

```python
def _extract_selected_zip(
    zip_bytes,
    out_dir,
    *,
    include_images=False,
    max_member_bytes=_DEFAULT_MAX,
):
    extracted = []
    written = _dir_size(out_dir)
    allowed = {"tabular"}
    if include_images:
        allowed.update({"image", "document"})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if written > _MAX_PAPER_BYTES:
                break
            if info.is_dir():
                continue
            name = os.path.basename(info.filename)
            if (
                not name
                or asset_type(name) not in allowed
                or info.file_size > max_member_bytes
            ):
                continue
            with zf.open(info) as src:
                data = src.read(max_member_bytes + 1)
                if len(data) > max_member_bytes:
                    continue
            dest = _write_collision_safe(out_dir, name, data)
            written += len(data)
            extracted.append(dest)
    return extracted
```

Add `import hashlib` and `import urllib.parse`, then add the collision-safe writer and use it for both zip and tar extraction:

```python
def _write_collision_safe(out_dir: str, name: str, data: bytes) -> str:
    stem, suffix = os.path.splitext(os.path.basename(name))
    candidate = os.path.join(out_dir, stem + suffix)
    if os.path.exists(candidate):
        with open(candidate, "rb") as fh:
            if fh.read() == data:
                return candidate
        digest = hashlib.sha256(data).hexdigest()[:10]
        candidate = os.path.join(out_dir, f"{stem}-{digest}{suffix}")
    with open(candidate, "wb") as fh:
        fh.write(data)
    return candidate
```

Apply the same `allowed` set to the tar extractor. Pass `include_images` through `_download_oa_package()` and `_download_supplementary_archive()`. Archive extraction must run when the requested asset class has no direct downloads; it must not be gated on `not downloaded` when tables were downloaded but requested images are only inside the archive.

- [ ] **Step 6: Run fetch tests and verify green**

Run:

```bash
uv run python -m pytest tests/fetch/test_files.py tests/fetch/test_download.py -q
```

Expected: all fetch unit tests pass, including unchanged default tabular extraction.

- [ ] **Step 7: Add failing Nature public-figure tests**

Extend `tests/test_fetch_nature.py`:

```python
FIGURE_HTML = '''
<a href="/articles/s41467-022-28338-0/figures/1">Fig. 1</a>
<img src="https://media.springernature.com/full/springer-static/image/art%3A10.1038%2Fs41467-022-28338-0/MediaObjects/41467_2022_28338_Fig1_HTML.png">
'''


def test_parse_nature_public_figure_links():
    refs = nat.parse_nature_figure_links(
        FIXTURE_HTML,
        "https://www.nature.com/articles/s41467-022-28338-0",
    )
    assert refs == [
        "https://www.nature.com/articles/s41467-022-28338-0/figures/1"
    ]


def test_search_nature_esm_adds_public_full_figure(monkeypatch):
    def stub_get_text(url, **kwargs):
        return FIGURE_HTML if url.endswith("/figures/1") else FIXTURE_HTML

    monkeypatch.setattr(nat._http, "get_text", stub_get_text)
    candidate = nat.search_nature_esm("10.1038/s41467-022-28338-0")[0]
    assert [f["ext"] for f in candidate["image_files"]] == ["png"]
    assert candidate["image_files"][0]["download_url"].startswith(
        "https://media.springernature.com/full/"
    )
```

- [ ] **Step 8: Implement Nature figure discovery through existing HTTP**

Add to `src/paperconan/fetch/_nature.py`:

```python
_FIGURE_HREF = re.compile(r'href="([^"]+/figures/\d+)"', re.I)
_FULL_IMAGE_SRC = re.compile(
    r'(https://media\.springernature\.com/full/[^"\']+\.(?:png|jpe?g|tiff?))',
    re.I,
)


def parse_nature_figure_links(html: str, article_url: str) -> list[str]:
    return sorted({
        urllib.parse.urljoin(article_url, href.replace("&amp;", "&"))
        for href in _FIGURE_HREF.findall(html or "")
    })


def parse_nature_full_image(html: str) -> dict | None:
    match = _FULL_IMAGE_SRC.search(html or "")
    if not match:
        return None
    url = match.group(1).replace("&amp;", "&")
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    return make_fileref(name, None, url)
```

Inside `search_nature_esm()`:

```python
    all_files = parse_nature_esm_links(html)
    for figure_url in parse_nature_figure_links(html, url):
        try:
            ref = parse_nature_full_image(_http.get_text(figure_url, timeout=60))
        except Exception:
            ref = None
        if ref is not None:
            all_files.append(ref)
```

No new HTTP client, browser automation, challenge handling, or authenticated fetch path is added.

- [ ] **Step 9: Add failing local asset tests**

Create `tests/test_image_assets.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from paperconan.image import _assets


def _image(path: Path, size=(80, 60), color=(20, 90, 180)):
    PIL.new("RGB", size, color).save(path)


def test_prepare_image_assets_preserves_native_pixels_and_stable_ids(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png", size=(80, 60))
    _image(source / "FigB.png", size=(40, 30), color=(180, 60, 20))
    out = tmp_path / "audit"

    first, errors = _assets.prepare_image_assets(str(source), str(out))
    second, errors2 = _assets.prepare_image_assets(str(source), str(out))

    assert errors == errors2 == []
    assert [a["asset_id"] for a in first] == [a["asset_id"] for a in second]
    assert [a["file"] for a in first] == ["FigA.png", "FigB.png"]
    native = PIL.open(out / first[0]["path"])
    assert native.size == (80, 60)
    assert first[0]["width"] == 80 and first[0]["height"] == 60
    assert first[0]["path"] != first[0]["preview_path"]


def test_exact_duplicate_files_are_one_asset_with_all_source_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "A.png")
    (source / "B.png").write_bytes((source / "A.png").read_bytes())
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert errors == []
    assert len(assets) == 1
    assert assets[0]["source_files"] == ["A.png", "B.png"]


def test_prepare_image_assets_renders_pdf_pages(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pdf = Path("tests/fixtures/supp_table.pdf")
    (source / "supp.pdf").write_bytes(pdf.read_bytes())
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    pages = [a for a in assets if a["source_type"] == "pdf_page"]
    assert errors == []
    assert pages
    assert pages[0]["parent_file"] == "supp.pdf"
    assert pages[0]["page"] == 1
    assert pages[0]["render_dpi"] == 200


def test_image_asset_limit_is_explicit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "A.png")
    _image(source / "B.png", color=(1, 2, 3))
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 1)
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert len(assets) == 1
    assert any("PAPERCONAN_MAX_IMAGE_ASSETS" in e["error"] for e in errors)


def test_multiframe_image_is_recorded_in_errors_not_silently_truncated(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = PIL.new("RGB", (20, 20), (1, 2, 3))
    second = PIL.new("RGB", (20, 20), (4, 5, 6))
    first.save(source / "stack.tiff", save_all=True, append_images=[second])
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert assets == []
    assert any("multi-frame images are not silently truncated" in e["error"] for e in errors)
```

- [ ] **Step 10: Run asset tests and verify red**

Run:

```bash
uv run python -m pytest tests/test_image_assets.py -q
```

Expected: collection fails because `_assets` does not exist.

- [ ] **Step 11: Implement asset preparation**

Create `src/paperconan/image/_assets.py` with these exact public helpers and limits:

```python
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from pathlib import Path

from . import ImageDependencyError


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
_MAX_IMAGE_MB = float(os.environ.get("PAPERCONAN_MAX_IMAGE_MB", "100"))
_MAX_IMAGE_BYTES = int(_MAX_IMAGE_MB * 1024 * 1024)
_MAX_IMAGE_PIXELS = int(os.environ.get("PAPERCONAN_MAX_IMAGE_PIXELS", "100000000"))
_MAX_IMAGE_ASSETS = int(os.environ.get("PAPERCONAN_MAX_IMAGE_ASSETS", "1000"))
_PDF_DPI = 200


def _load_pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ImageDependencyError(
            'image support requires `pip install "paperconan[image]"`'
        ) from exc
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    return Image, ImageOps


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_id(digest: str) -> str:
    return f"img:{digest[:20]}"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _source_provenance(in_dir: Path) -> dict[str, dict]:
    sidecar = in_dir / "paperconan_source.json"
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        str(item.get("file")): item
        for item in data.get("downloads", []) or []
        if item.get("file")
    }


def _write_preview(image, path: Path, max_side: int = 1400) -> None:
    preview = image.copy()
    preview.thumbnail((max_side, max_side))
    if preview.mode not in ("RGB", "L"):
        preview = preview.convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path, format="JPEG", quality=86, optimize=True)


def _record_image(
    source: Path,
    output_root: Path,
    *,
    source_type: str,
    source_url: str | None,
    parent_file: str | None = None,
    page: int | None = None,
    render_dpi: int | None = None,
) -> dict:
    Image, ImageOps = _load_pillow()
    if source.stat().st_size > _MAX_IMAGE_BYTES:
        raise ValueError(
            f"{source.name}: exceeds PAPERCONAN_MAX_IMAGE_MB={_MAX_IMAGE_MB:g}"
        )
    digest = _sha256(source)
    asset_id = _asset_id(digest)
    suffix = source.suffix.lower() or ".png"
    native = output_root / "images" / "native" / f"{asset_id.replace(':', '-')}{suffix}"
    native.parent.mkdir(parents=True, exist_ok=True)
    if not native.exists():
        shutil.copyfile(source, native)
    with Image.open(native) as image:
        frame_count = int(getattr(image, "n_frames", 1))
        if frame_count != 1:
            raise ValueError(
                f"{source.name}: multi-frame images are not silently truncated; "
                "export each frame as a separate image"
            )
        exif_orientation = int(image.getexif().get(274, 1) or 1)
        width, height = image.size
        if width * height > _MAX_IMAGE_PIXELS:
            raise ValueError(
                f"{source.name}: exceeds PAPERCONAN_MAX_IMAGE_PIXELS={_MAX_IMAGE_PIXELS}"
            )
        display_image = ImageOps.exif_transpose(image)
        preview = (
            output_root / "images" / "preview"
            / f"{asset_id.replace(':', '-')}.jpg"
        )
        _write_preview(display_image, preview)
        mime = Image.MIME.get(image.format) or mimetypes.guess_type(native.name)[0]
    return {
        "asset_id": asset_id,
        "file": source.name,
        "source_files": [source.name],
        "path": _relative(native, output_root),
        "preview_path": _relative(preview, output_root),
        "preview_mime": "image/jpeg",
        "source_type": source_type,
        "source_url": source_url,
        "parent_file": parent_file,
        "page": page,
        "render_dpi": render_dpi,
        "figure_label": None,
        "sha256": digest,
        "width": width,
        "height": height,
        "exif_orientation": exif_orientation,
        "mime": mime or "application/octet-stream",
    }
```

The native file is copied byte-for-byte; EXIF orientation is applied only to the display preview and recorded explicitly. Multi-frame TIFF/WebP input becomes a `scan_errors` entry instead of silently using frame zero.

Add PDF rendering:

```python
def _render_pdf_pages(pdf_path: Path, temp_dir: Path) -> list[Path]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImageDependencyError(
            'PDF image rendering requires `pip install "paperconan[image]"`'
        ) from exc
    doc = pdfium.PdfDocument(str(pdf_path))
    rendered = []
    scale = _PDF_DPI / 72.0
    for index in range(len(doc)):
        page = doc[index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        dest = temp_dir / f"{pdf_path.stem}.p{index + 1}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, format="PNG")
        rendered.append(dest)
        page.close()
    doc.close()
    return rendered
```

Implement `prepare_image_assets()`:

```python
def prepare_image_assets(
    in_dir: str,
    out_dir: str,
    *,
    provenance: dict | None = None,
    render_pdf: bool = True,
) -> tuple[list[dict], list[dict]]:
    source_root = Path(in_dir)
    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    downloads = _source_provenance(source_root)
    candidates = sorted(
        [
            path for path in source_root.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        ],
        key=lambda path: path.name.casefold(),
    )
    pdfs = sorted(source_root.glob("*.pdf"), key=lambda path: path.name.casefold())
    assets_by_digest: dict[str, dict] = {}
    errors: list[dict] = []

    def add(path: Path, **metadata):
        if len(assets_by_digest) >= _MAX_IMAGE_ASSETS:
            errors.append({
                "file": path.name,
                "error": (
                    "image asset limit reached; set "
                    "PAPERCONAN_MAX_IMAGE_ASSETS to raise"
                ),
            })
            return
        try:
            asset = _record_image(path, output_root, **metadata)
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})
            return
        existing = assets_by_digest.get(asset["sha256"])
        if existing is None:
            assets_by_digest[asset["sha256"]] = asset
        else:
            existing["source_files"] = sorted(
                set(existing["source_files"] + asset["source_files"])
            )

    for path in candidates:
        prov = downloads.get(path.name) or {}
        add(
            path,
            source_type="fetched_image" if prov.get("source_url") else "local_image",
            source_url=prov.get("source_url"),
        )
    if render_pdf:
        temp_dir = output_root / "images" / ".rendered"
        for pdf in pdfs:
            try:
                pages = _render_pdf_pages(pdf, temp_dir)
            except Exception as exc:
                errors.append({"file": pdf.name, "error": str(exc)})
                continue
            for page_number, page_path in enumerate(pages, 1):
                add(
                    page_path,
                    source_type="pdf_page",
                    source_url=(downloads.get(pdf.name) or {}).get("source_url"),
                    parent_file=pdf.name,
                    page=page_number,
                    render_dpi=_PDF_DPI,
                )
        shutil.rmtree(temp_dir, ignore_errors=True)
    assets = sorted(
        assets_by_digest.values(),
        key=lambda asset: (asset["asset_id"], asset["file"]),
    )
    return assets, errors
```

- [ ] **Step 12: Wire image assets into scan and support image-only input**

Change `scan_dir()` signature to the shared signature. Separate table files from image files:

```python
    table_files = sorted({
        p for pattern in (
            "*.xlsx", "*.xls", "*.xlsm", "*.xlsb",
            "*.csv", "*.tsv", "*.pdf", "*.docx",
        )
        for p in glob.glob(os.path.join(in_dir, pattern))
    })
    local_images = sorted({
        p for pattern in ("*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.webp")
        for p in glob.glob(os.path.join(in_dir, pattern))
    })
    if not table_files and not (images and local_images):
        raise PaperconanInputError(
            f"no supported table/document files"
            f"{' or image files' if images else ''} in {in_dir}"
        )
```

Keep the numeric loop over `table_files`. Before constructing `out`:

```python
    image_assets = []
    image_findings = []
    if images:
        from .image._assets import prepare_image_assets
        image_assets, image_errors = prepare_image_assets(in_dir, out_dir)
        scan_errors.extend(image_errors)
```

Add to `out`:

```python
               image_assets=image_assets,
               image_findings=image_findings,
```

Preserve `n_files=len(table_files)` for backward compatibility. Add:

```python
               n_image_source_files=len(local_images),
               n_image_assets=len(image_assets),
```

An image-only scan therefore reports `n_files == 0` and a positive `n_image_assets`, rather than changing the historical meaning of `n_files`.

Add CLI flags:

```python
    ap.add_argument(
        "--images",
        action="store_true",
        help="inventory local/fetched images and render PDF pages into scan.json image_assets",
    )
    ap.add_argument(
        "--image-diagnostics",
        action="store_true",
        help="also run optional non-gating deterministic image similarity helpers",
    )
```

Reject `--image-diagnostics` without `--images` through:

```python
    if args.image_diagnostics and not args.images:
        ap.error("--image-diagnostics requires --images")
```

Pass both flags to `scan_dir()`.

- [ ] **Step 13: Add `--images` fetch CLI test and implement it**

Append to `tests/fetch/test_cli.py`:

```python
def test_fetch_images_passes_additive_option(monkeypatch, tmp_path):
    cands = [{
        "cand_id": "source:1",
        "source": "source",
        "title": "T",
        "all_files_count": 2,
        "match_signals": {"doi_in_related": True},
        "tabular_files": [{"name": "data.csv"}],
        "image_files": [{"name": "Fig1.png"}],
    }]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    captured = {}

    def stub_download(candidate, out_dir, **kwargs):
        captured.update(kwargs)
        return {"downloaded": [str(tmp_path / "Fig1.png")], "skipped": []}

    monkeypatch.setattr(_cli, "download_candidate", stub_download)
    rc = _cli.fetch_main([
        "10.x/paper", "--auto", "--images", "--out", str(tmp_path),
    ])
    assert rc == 0
    assert captured["include_images"] is True
```

In `fetch_main()`:

```python
    ap.add_argument(
        "--images",
        action="store_true",
        help="also download public image files and PDFs for image review",
    )
    summary = download_candidate(
        target,
        out_dir,
        tabular_only=not args.all,
        include_images=args.images,
    )
```

When downloads succeed, print `paperconan <out_dir> --images` if `args.images`, otherwise preserve the old command.

- [ ] **Step 14: Add optional dependencies and package tests**

In `pyproject.toml`:

```toml
image = [
  "pillow>=12",
  "pypdfium2>=5",
  "opencv-python-headless>=4.10",
]
all = [
  "pdfplumber>=0.11",
  "python-docx>=1.1",
  "pillow>=12",
  "pypdfium2>=5",
  "opencv-python-headless>=4.10",
]
```

Add the same three image dependencies to `test` and `dev`. Extend `tests/test_packaging.py`:

```python
def test_image_extra_is_optional_but_included_in_all_and_test():
    with open("pyproject.toml", "rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    assert {"pillow>=12", "pypdfium2>=5", "opencv-python-headless>=4.10"} <= set(
        extras["image"]
    )
    for name in ("all", "test", "dev"):
        joined = " ".join(extras[name])
        assert "pillow" in joined
        assert "pypdfium2" in joined
        assert "opencv-python-headless" in joined
```

Run:

```bash
uv lock
uv sync --extra dev
```

Expected: `uv.lock` changes only for the declared image packages and their resolved dependencies.

- [ ] **Step 15: Run Task 2 tests**

Run:

```bash
uv run python -m pytest tests/test_image_assets.py tests/fetch/test_files.py tests/fetch/test_download.py tests/fetch/test_cli.py tests/test_fetch_nature.py tests/test_packaging.py tests/test_smoke.py tests/test_profiles.py -q
```

Expected: all tests pass. Numeric-only scans and fetches retain previous behavior; image-only scans succeed only with `images=True`.

- [ ] **Step 16: Commit Task 2**

```bash
git add src/paperconan/image/_assets.py src/paperconan/image/_evidence.py src/paperconan/fetch/_files.py src/paperconan/fetch/_sources.py src/paperconan/fetch/_nature.py src/paperconan/fetch/_download.py src/paperconan/fetch/_cli.py src/paperconan/_audit.py pyproject.toml uv.lock tests/test_image_assets.py tests/fetch/test_files.py tests/fetch/test_download.py tests/fetch/test_cli.py tests/test_fetch_nature.py tests/test_packaging.py
git commit -m "feat(image): add image assets through existing fetch and scan flows"
```

---

### Task 3: Optional Non-Gating Deterministic Diagnostics

**Files:**
- Create: `src/paperconan/image/_diagnostics.py`
- Modify: `src/paperconan/image/_evidence.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_html.py`
- Create: `tests/test_image_diagnostics.py`

**Interfaces:**
- Produces: `propose_panels(image) -> list[tuple[int, int, int, int]]`.
- Produces: `transform_robust_similarity(a, b) -> tuple[float, str]`.
- Produces: `write_native_pair_evidence(image_path, box_a, box_b, output_root, evidence_id) -> dict`.
- Produces: `diagnose_image_assets(assets, artifact_dir) -> (findings, errors)`.
- Invariant: `scan["image_assets"]` is identical whether diagnostics are enabled or disabled.
- Invariant: every crop box is native-pixel `[x0, y0, x1, y1]`; individual crop images have exactly `(x1-x0, y1-y0)` pixels.

- [ ] **Step 1: Write failing diagnostic and crop-fidelity tests**

Create `tests/test_image_diagnostics.py`:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

Image = pytest.importorskip("PIL.Image")
pytest.importorskip("cv2")

from paperconan.image._assets import prepare_image_assets
from paperconan.image._diagnostics import diagnose_image_assets


def _two_panel(path: Path):
    left = np.zeros((120, 140, 3), dtype=np.uint8)
    yy, xx = np.indices(left.shape[:2])
    left[:, :, 0] = (xx * 3 + yy * 5) % 255
    left[:, :, 1] = (xx * 7) % 255
    left[:, :, 2] = (yy * 11) % 255
    right = np.fliplr(left)
    canvas = np.full((140, 310, 3), 255, dtype=np.uint8)
    canvas[10:130, 10:150] = left
    canvas[10:130, 160:300] = right
    Image.fromarray(canvas).save(path)


def test_diagnostics_find_transform_related_panels_and_keep_assets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    before = [dict(asset) for asset in assets]

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert errors == diagnostic_errors == []
    assert assets == before
    assert findings
    finding = findings[0]
    assert finding["kind"] == "image_pair_similarity_signal"
    assert finding["transform"] == "flip"
    assert finding["score"] >= 0.92
    assert len(finding["regions"]) == 2


def test_native_evidence_crops_are_not_resized(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    findings, _ = diagnose_image_assets(assets, str(out))
    evidence = findings[0]["evidence"]
    for region, key in zip(findings[0]["regions"], ("crop_a_path", "crop_b_path")):
        image = Image.open(out / evidence[key])
        x0, y0, x1, y1 = region["box"]
        assert image.size == (x1 - x0, y1 - y0)
    preview = Image.open(out / evidence["preview_path"])
    assert preview.width <= 1600


def test_diagnostic_finding_ids_and_order_are_deterministic(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    first, _ = diagnose_image_assets(assets, str(out))
    second, _ = diagnose_image_assets(assets, str(out))
    assert [f["finding_id"] for f in first] == [f["finding_id"] for f in second]
    assert first == second
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
uv run python -m pytest tests/test_image_diagnostics.py -q
```

Expected: collection fails because `_diagnostics` does not exist.

- [ ] **Step 3: Implement native crop and bounded preview evidence**

Append to `src/paperconan/image/_evidence.py`:

```python
def write_native_pair_evidence(
    image_path: str,
    box_a: tuple[int, int, int, int],
    box_b: tuple[int, int, int, int],
    output_root: str,
    evidence_id: str,
) -> dict[str, str]:
    from PIL import Image

    root = Path(output_root)
    out_dir = root / "images" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        crop_a = image.crop(box_a)
        crop_b = image.crop(box_b)
        crop_a_path = out_dir / f"{evidence_id}-a.png"
        crop_b_path = out_dir / f"{evidence_id}-b.png"
        crop_a.save(crop_a_path, format="PNG")
        crop_b.save(crop_b_path, format="PNG")

        preview_a = crop_a.copy()
        preview_b = crop_b.copy()
        preview_a.thumbnail((760, 760))
        preview_b.thumbnail((760, 760))
        height = max(preview_a.height, preview_b.height)
        canvas = Image.new(
            "RGB",
            (preview_a.width + preview_b.width + 20, height),
            "white",
        )
        canvas.paste(preview_a.convert("RGB"), (0, 0))
        canvas.paste(preview_b.convert("RGB"), (preview_a.width + 20, 0))
        preview_path = out_dir / f"{evidence_id}-preview.jpg"
        canvas.save(preview_path, format="JPEG", quality=88, optimize=True)
    return {
        "crop_a_path": _relative_path(crop_a_path, root),
        "crop_b_path": _relative_path(crop_b_path, root),
        "preview_path": _relative_path(preview_path, root),
    }


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
```

- [ ] **Step 4: Implement deterministic diagnostics**

Create `src/paperconan/image/_diagnostics.py`:

```python
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from ._evidence import write_native_pair_evidence


_MAX_IMAGE_FINDINGS = int(os.environ.get("PAPERCONAN_MAX_IMAGE_FINDINGS", "200"))
_MIN_PANEL_SIDE = 64
_SIMILARITY_THRESHOLD = 0.92


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        from . import ImageDependencyError
        raise ImageDependencyError(
            'image diagnostics require `pip install "paperconan[image]"`'
        ) from exc
    return cv2


def _uniform_runs(values: np.ndarray) -> list[tuple[int, int]]:
    mask = values < 2.0
    runs = []
    start = None
    for index, flag in enumerate(mask.tolist() + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if index - start >= 2:
                runs.append((start, index))
            start = None
    return runs


def propose_panels(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    cv2 = _cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    row_std = gray.std(axis=1)
    col_std = gray.std(axis=0)
    row_runs = _uniform_runs(row_std)
    col_runs = _uniform_runs(col_std)
    y_edges = [0] + [int((a + b) / 2) for a, b in row_runs] + [gray.shape[0]]
    x_edges = [0] + [int((a + b) / 2) for a, b in col_runs] + [gray.shape[1]]
    boxes = []
    for y0, y1 in zip(y_edges, y_edges[1:]):
        for x0, x1 in zip(x_edges, x_edges[1:]):
            if x1 - x0 < _MIN_PANEL_SIDE or y1 - y0 < _MIN_PANEL_SIDE:
                continue
            patch = gray[y0:y1, x0:x1]
            if patch.std() >= 8:
                boxes.append((x0, y0, x1, y1))
    return boxes or [(0, 0, gray.shape[1], gray.shape[0])]


def _normalized_gray(image: np.ndarray, size: int = 128) -> np.ndarray:
    cv2 = _cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (size, size)).astype(np.float32)
    gray -= gray.mean()
    std = gray.std()
    return gray / std if std > 1.0 else gray


def transform_robust_similarity(a: np.ndarray, b: np.ndarray) -> tuple[float, str]:
    cv2 = _cv2()
    left = _normalized_gray(a)
    variants = {
        "identity": b,
        "flip": cv2.flip(b, 1),
        "rotate90": cv2.rotate(b, cv2.ROTATE_90_CLOCKWISE),
        "rotate180": cv2.rotate(b, cv2.ROTATE_180),
        "rotate270": cv2.rotate(b, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }
    scored = [
        (float((left * _normalized_gray(value)).mean()), name)
        for name, value in variants.items()
    ]
    return max(scored, key=lambda item: (item[0], item[1]))


def _finding_id(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "image:pair:" + hashlib.sha256(raw).hexdigest()[:20]


def diagnose_image_assets(
    assets: list[dict],
    artifact_dir: str,
) -> tuple[list[dict], list[dict]]:
    cv2 = _cv2()
    root = Path(artifact_dir)
    findings, errors = [], []
    for asset in sorted(assets, key=lambda item: item["asset_id"]):
        native = (root / asset["path"]).resolve()
        try:
            native.relative_to(root.resolve())
        except ValueError:
            errors.append({"file": asset.get("file"), "error": "asset path escapes artifact root"})
            continue
        image = cv2.imread(str(native))
        if image is None:
            errors.append({"file": asset.get("file"), "error": "unable to decode registered image"})
            continue
        boxes = propose_panels(image)
        for left_index in range(len(boxes)):
            for right_index in range(left_index + 1, len(boxes)):
                box_a, box_b = boxes[left_index], boxes[right_index]
                a = image[box_a[1]:box_a[3], box_a[0]:box_a[2]]
                b = image[box_b[1]:box_b[3], box_b[0]:box_b[2]]
                score, transform = transform_robust_similarity(a, b)
                if score < _SIMILARITY_THRESHOLD:
                    continue
                identity = {
                    "asset_ids": [asset["asset_id"]],
                    "boxes": [list(box_a), list(box_b)],
                    "method": "panel_pair_similarity",
                    "transform": transform,
                }
                finding_id = _finding_id(identity)
                evidence_id = finding_id.replace(":", "-")
                evidence = write_native_pair_evidence(
                    str(native), box_a, box_b, artifact_dir, evidence_id
                )
                findings.append({
                    "finding_id": finding_id,
                    "kind": "image_pair_similarity_signal",
                    "severity": "medium",
                    "rule": (
                        "two registered image regions retain high structural "
                        f"similarity under {transform}"
                    ),
                    "asset_ids": [asset["asset_id"]],
                    "regions": [
                        {"asset_id": asset["asset_id"], "box": list(box_a)},
                        {"asset_id": asset["asset_id"], "box": list(box_b)},
                    ],
                    "method": "panel_pair_similarity",
                    "score": round(score, 6),
                    "transform": transform,
                    "evidence": evidence,
                    "profile_action": "kept",
                })
    findings.sort(key=lambda item: (-item["score"], item["finding_id"]))
    if len(findings) > _MAX_IMAGE_FINDINGS:
        errors.append({
            "error": (
                f"{len(findings) - _MAX_IMAGE_FINDINGS} image findings omitted; "
                "set PAPERCONAN_MAX_IMAGE_FINDINGS to raise"
            )
        })
        findings = findings[:_MAX_IMAGE_FINDINGS]
    return findings, errors
```

This deliberately small helper is not a completeness claim. It emits hints for separated, textured panels and retains every asset regardless of whether a signal is emitted.

- [ ] **Step 5: Wire diagnostics into `scan_dir()` without changing assets**

After `prepare_image_assets()`:

```python
        if image_diagnostics:
            from .image._diagnostics import diagnose_image_assets
            image_findings, diagnostic_errors = diagnose_image_assets(
                image_assets, out_dir
            )
            scan_errors.extend(diagnostic_errors)
```

Do not pass `image_findings` into a prefilter that can remove assets. `profile_action` may describe a signal only.

- [ ] **Step 6: Add scan-level non-gating regression**

Append to `tests/test_image_diagnostics.py`:

```python
def test_scan_diagnostics_never_change_asset_inventory(tmp_path):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    without = scan_dir(
        str(source),
        str(tmp_path / "without"),
        write_html=False,
        images=True,
        image_diagnostics=False,
    )
    with_hints = scan_dir(
        str(source),
        str(tmp_path / "with"),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )
    comparable = lambda asset: {
        key: value for key, value in asset.items()
        if key not in {"path", "preview_path"}
    }
    assert [comparable(a) for a in without["image_assets"]] == [
        comparable(a) for a in with_hints["image_assets"]
    ]
    assert without["image_findings"] == []
    assert with_hints["image_findings"]
```

- [ ] **Step 7: Render deterministic image evidence in the existing browser**

Thread the report output directory into `_render_finding_card()`:

```python
def _render_finding_card(
    item: dict,
    *,
    artifact_dir: str | None = None,
    image_budget: EvidenceBudget | None = None,
) -> str:
```

For `scope == "image"`, resolve only `finding["evidence"]["preview_path"]` under `artifact_dir`, consume the shared evidence budget, and embed it as a data URI. If no preview exists, retain the region chips from Task 1. In `write_html_report()`, use:

```python
artifact_dir = os.path.dirname(os.path.abspath(out_path))
image_budget = EvidenceBudget(
    int(float(os.environ.get("PAPERCONAN_MAX_IMAGE_EVIDENCE_MB", "20")) * 1024 * 1024)
)
```

Pass those values to every card. Add this assertion to `tests/test_image_diagnostics.py`:

```python
def test_deterministic_report_embeds_registered_image_evidence(tmp_path):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    scan_dir(
        str(source),
        str(out),
        write_html=True,
        images=True,
        image_diagnostics=True,
    )
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "image_pair_similarity_signal" in html
    assert "data:image/jpeg;base64," in html
```

- [ ] **Step 8: Run Task 3 tests**

Run:

```bash
uv run python -m pytest tests/test_image_diagnostics.py tests/test_image_assets.py tests/test_image_report.py -q
```

Expected: all tests pass; native crops retain exact dimensions; repeated runs produce identical IDs and ordering.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/paperconan/image/_diagnostics.py src/paperconan/image/_evidence.py src/paperconan/_audit.py src/paperconan/_html.py tests/test_image_diagnostics.py
git commit -m "feat(image): add non-gating deterministic image diagnostics"
```

---

### Task 4: Skill, Documentation, And End-To-End Adaptive Workflow

**Files:**
- Create: `tests/test_image_workflow.py`
- Modify: `tests/test_skill_docs.py`
- Modify: `skills/paperconan/SKILL.md`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `skills/paperconan/references/report-templates.md`
- Modify: `README.md`
- Modify: `docs/cli.md`
- Modify: `docs/reports.md`

**Interfaces:**
- Documents: capability check before semantic image review.
- Documents: whole-image first, native crop second, deterministic hints optional.
- Documents: every asset must end in reviewed, unresolved, unreadable, or deferred coverage.
- Documents: one unified verdict and report for numeric and image findings.
- Demonstrates: mixed directory -> one `scan.json` -> external Agent verdict -> one adjudicated HTML.

- [ ] **Step 1: Write the failing end-to-end workflow test**

Create `tests/test_image_workflow.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

from paperconan import scan_dir, write_adjudicated_report


def test_mixed_numeric_and_image_workflow_produces_one_report(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.csv").write_text(
        "a,b\n1,2\n2,3\n3,4\n4,5\n",
        encoding="utf-8",
    )
    Image.new("RGB", (64, 48), (30, 100, 180)).save(source / "Fig1.png")
    audit = tmp_path / "audit"

    scan = scan_dir(
        str(source),
        str(audit),
        write_html=True,
        images=True,
        image_diagnostics=False,
    )
    assert (audit / "scan.json").exists()
    assert (audit / "report.html").exists()
    assert scan["relations_blocks"]
    assert len(scan["image_assets"]) == 1

    asset_id = scan["image_assets"][0]["asset_id"]
    verdict = {
        "title": "Synthetic mixed workflow",
        "verdict": "NEEDS_HUMAN",
        "paper_conclusion": "The numeric and image material were reviewed together.",
        "findings": [{
            "finding_type": "image",
            "title": "Image review",
            "image_refs": [{"asset_id": asset_id, "label": "Fig. 1"}],
            "review_status": "unresolved",
            "impact_scope": "supporting",
            "report_md": "The available image does not provide enough context for a conclusion.",
        }],
        "image_review": {
            "status": "completed",
            "reviewed_asset_ids": [asset_id],
            "unresolved_asset_ids": [asset_id],
            "unreadable_asset_ids": [],
            "deferred_asset_ids": [],
            "note": "reviewed with a multimodal Agent",
        },
    }
    verdict_path = audit / "verdict.json"
    verdict_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    report = audit / "adjudication.html"
    write_adjudicated_report(
        scan,
        verdict,
        str(report),
        artifact_dir=str(audit),
    )
    html = report.read_text(encoding="utf-8")
    assert "Image review" in html
    assert "图像语义复核覆盖" in html
    assert "data:image/jpeg;base64," in html
    assert html.count("<!DOCTYPE html>") == 1
```

- [ ] **Step 2: Run the workflow test**

Run:

```bash
uv run python -m pytest tests/test_image_workflow.py -q
```

Expected: pass only after Tasks 1-3 are complete. If it fails, fix the owning earlier interface rather than adding a second workflow.

- [ ] **Step 3: Add failing skill contract tests**

Append to `tests/test_skill_docs.py`:

```python
def test_skill_routes_adaptive_image_review():
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


def test_output_schema_and_report_template_document_image_contracts():
    output = (REF_DIR / "output-schema.md").read_text(encoding="utf-8")
    template = (REF_DIR / "report-templates.md").read_text(encoding="utf-8")
    for phrase in ("image_assets", "image_findings", "image_review"):
        assert phrase in output
    for phrase in ("finding_type", "image_refs", "review_status"):
        assert phrase in template
```

- [ ] **Step 4: Run skill tests and verify red**

Run:

```bash
uv run python -m pytest tests/test_skill_docs.py -q
```

Expected: new tests fail because the current skill explicitly excludes image review.

- [ ] **Step 5: Rewrite the Skill workflow**

Update the skill frontmatter description to include image diagnostics with an external multimodal Agent. Replace the current image exclusion with these operational rules:

```markdown
## Adaptive Image Review

1. Run `paperconan <input-dir> --images`; add `--image-diagnostics` only when deterministic hints are useful.
2. Read every entry in `image_assets`; deterministic `image_findings` are hints and never the complete review set.
3. Confirm the current Agent can open local images.
   - If yes, inspect the whole image first, then use a native-pixel crop for small panels or unresolved detail.
   - If no, set `image_review.status` to `unavailable_no_multimodal`, continue numeric review, and state that image semantic review was not completed.
4. For every asset, record exactly one coverage outcome: reviewed, unresolved, unreadable, or deferred.
5. Check figure labels, channels, processing steps, shared controls, insets, before/after layouts, and Methods before escalating an image similarity signal.
6. The Agent may create an image finding using `image_refs` even when `image_findings` is empty.
7. Put numeric and image findings in the same `verdict.json findings[]`, then generate a single unified report with `paperconan report`.
```

State explicitly that PaperConan does not manage model keys or provider SDKs.

- [ ] **Step 6: Document exact JSON contracts**

Add the spec's complete `image_assets[]`, `image_findings[]`, mixed finding, and `image_review` examples to `output-schema.md` and `report-templates.md`. Include:

```json
{
  "finding_type": "image",
  "title": "Fig. 3 panel pair requires clarification",
  "finding_ref": {"finding_id": "image:pair:stable-id"},
  "image_refs": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610], "label": "A"},
    {"asset_id": "img:b", "box": [40, 55, 660, 585], "label": "B"}
  ],
  "review_status": "needs_human",
  "impact_scope": "supporting",
  "report_md": "The registered regions retain high similarity and require source context."
}
```

Document that `finding_ref` is optional for Agent-only observations and that unknown image status becomes `unresolved`.

- [ ] **Step 7: Update CLI, report, and README documentation**

Document:

```bash
pip install "paperconan[image]"
paperconan fetch "<DOI or title>" --auto --images --out data/
paperconan data/ --images
paperconan data/ --images --image-diagnostics
paperconan report data/audit/scan.json --verdict verdict.json --out adjudication.html
```

Add the image resource-limit rows to `docs/cli.md`, including the total artifact budget and
scan-wide attempted-comparison ceiling. In `docs/reports.md`, state that image evidence is embedded
only from registered bounded previews and appears beside numeric evidence in the same finding list.
In `README.md`, add image review to the completed workflow without claiming autonomous semantic
judgment.

- [ ] **Step 8: Run documentation, workflow, and full regression tests**

Run:

```bash
uv run python -m pytest tests/test_skill_docs.py tests/test_image_workflow.py tests/test_image_report.py tests/test_image_assets.py tests/test_image_diagnostics.py -q
uv run python -m pytest -q
```

Expected: focused tests pass, then the complete suite passes with the existing single skip unchanged unless the local environment enables it.

- [ ] **Step 9: Manual CLI smoke test with synthetic local data**

Run:

```bash
tmp_dir="$(mktemp -d)"
mkdir -p "$tmp_dir/input"
printf 'a,b\n1,2\n2,3\n3,4\n4,5\n' > "$tmp_dir/input/data.csv"
uv run python - <<'PY' "$tmp_dir/input/Fig1.png"
from PIL import Image
import sys
Image.new("RGB", (96, 64), (40, 110, 170)).save(sys.argv[1])
PY
uv run paperconan "$tmp_dir/input" --images --out "$tmp_dir/audit"
uv run python - <<'PY' "$tmp_dir/audit/scan.json"
import json
import sys
scan = json.load(open(sys.argv[1], encoding="utf-8"))
assert scan["relations_blocks"]
assert len(scan["image_assets"]) == 1
assert scan["image_findings"] == []
print(scan["image_assets"][0]["asset_id"])
PY
```

Expected: one `scan.json`, one deterministic `report.html`, numeric findings present, one image asset present, and no deterministic image findings unless explicitly requested.

- [ ] **Step 10: Commit Task 4**

```bash
git add tests/test_image_workflow.py tests/test_skill_docs.py skills/paperconan/SKILL.md skills/paperconan/references/output-schema.md skills/paperconan/references/report-templates.md README.md docs/cli.md docs/reports.md
git commit -m "docs(skill): orchestrate adaptive multimodal image review"
```

---

## Final Verification

- [ ] Run `git status --short` and verify only intended files are present.
- [ ] Run `uv run python -m pytest -q`.
- [ ] Run `git log -4 --oneline` and verify these commit boundaries, in order:

```text
feat(report): integrate image findings into unified adjudication
feat(image): add image assets through existing fetch and scan flows
feat(image): add non-gating deterministic image diagnostics
docs(skill): orchestrate adaptive multimodal image review
```

- [ ] Inspect a generated mixed report and verify:
  - numeric and image findings share one report;
  - image-only findings do not show unrelated numeric evidence;
  - coverage is visible;
  - preview dimensions are bounded;
  - native crop dimensions equal their native boxes;
  - no image disappears when deterministic diagnostics emit no signal.

## Self-Review Record

- Spec coverage: acquisition, assets, scan schema, verdict schema, coverage, report integration, safe paths, status normalization, resource caps, optional diagnostics, skill capability check, and mixed end-to-end workflow are each assigned to a task.
- Naming consistency: `image_assets`, `image_findings`, `image_refs`, `image_review`, `artifact_dir`, `include_images`, and `image_diagnostics` use one spelling throughout.
- Compatibility: default fetch and scan behavior stays numeric/tabular; numeric report fallback stays unchanged; new arguments are optional and keyword-only where applicable.
- Ambiguity removed: model calls are explicitly outside PaperConan; diagnostics are explicitly optional and non-gating; public acquisition stops at access controls; report file reads are rooted under the scan artifact directory.
