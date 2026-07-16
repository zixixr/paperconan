"""Pure helpers for classifying downloadable files by extension."""
from __future__ import annotations

from paperconan._input import (
    SUPPORTED_INPUT_EXTS,
    ext_of,
    is_supported_input,
)


TABULAR_EXTS = set(SUPPORTED_INPUT_EXTS)
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "webp"}
DOCUMENT_EXTS = {"pdf", "docx"}


def is_tabular(name: str) -> bool:
    return is_supported_input(name)


def is_image(name: str) -> bool:
    return ext_of(name) in IMAGE_EXTS


def asset_type(name: str) -> str:
    ext = ext_of(name)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOCUMENT_EXTS:
        return "document"
    if ext in TABULAR_EXTS:
        return "tabular"
    return "other"


def make_fileref(name: str, size, download_url: str) -> dict:
    return {"name": name, "ext": ext_of(name),
            "size": int(size) if isinstance(size, (int, float)) else None,
            "download_url": download_url}
