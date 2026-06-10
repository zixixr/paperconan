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
    """Raised when the input directory has no supported tabular files."""


class Finding(TypedDict, total=False):
    kind: str
    severity: str
    rule: str
    profile_action: ProfileAction
    false_positive_context: list[str]
    likely_benign: str

