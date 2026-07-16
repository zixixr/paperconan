"""Shared scan schema helpers for paperconan.

The project intentionally emits plain JSON-compatible dictionaries, because
scan.json is part of the public artifact users archive and inspect. These
TypedDicts document the stable shape without forcing a runtime model layer.
"""
from __future__ import annotations

from typing import Literal, TypedDict


Profile = Literal["review", "forensic", "triage"]
ProfileAction = Literal["kept", "demoted", "hidden"]

VALID_PROFILES: tuple[Profile, ...] = ("review", "forensic", "triage")


class PaperconanInputError(ValueError):
    """Raised when the input path cannot supply supported source data."""


class Finding(TypedDict, total=False):
    kind: str
    severity: str
    rule: str
    relation_model_ambiguous: bool
    relation_model_alternatives: list[str]
    profile_action: ProfileAction
    false_positive_context: list[str]
    likely_benign: str


class ImageRegion(TypedDict):
    asset_id: str
    box: list[int]


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
    evidence: dict[str, str] | None
    profile_action: ProfileAction


class ImageReview(TypedDict, total=False):
    status: Literal["completed", "partial", "unavailable_no_multimodal", "not_requested"]
    reviewed_asset_ids: list[str]
    unresolved_asset_ids: list[str]
    unreadable_asset_ids: list[str]
    deferred_asset_ids: list[str]
    note: str
