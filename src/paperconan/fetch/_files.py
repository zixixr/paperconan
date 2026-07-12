"""Pure helpers for classifying downloadable files by extension."""
from __future__ import annotations

from paperconan._input import (
    SUPPORTED_INPUT_EXTS,
    ext_of,
    is_supported_input,
)

TABULAR_EXTS = set(SUPPORTED_INPUT_EXTS)
is_tabular = is_supported_input


def make_fileref(name: str, size, download_url: str) -> dict:
    return {"name": name, "ext": ext_of(name),
            "size": int(size) if isinstance(size, (int, float)) else None,
            "download_url": download_url}
