"""Pure helpers for classifying downloadable files by extension."""
from __future__ import annotations
import os

TABULAR_EXTS = {"xlsx", "csv", "tsv"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "webp"}
DOCUMENT_EXTS = {"pdf"}


def ext_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def is_tabular(name: str) -> bool:
    return ext_of(name) in TABULAR_EXTS


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


def make_fileref(name: str, size, download_url: str, label: str | None = None) -> dict:
    """`label` is whatever the source page called this file, verbatim and unparsed.

    A supplementary file's own name is usually an accession string, so the only thing that
    says WHICH figure it holds is the text the publisher put beside the link. Sources that
    have it should pass it through; interpreting it belongs to whoever consumes it.
    """
    return {"name": name, "ext": ext_of(name),
            "size": int(size) if isinstance(size, (int, float)) else None,
            "download_url": download_url, "label": label}
