from __future__ import annotations

import importlib

from . import ImageDependencyError


_import_module = importlib.import_module
_INSTALL_GUIDANCE = 'pip install "paperconan[image]"'


def _require(module_name: str, message: str) -> None:
    try:
        _import_module(module_name)
    except ImportError as exc:
        raise ImageDependencyError(
            f"{message} requires `{_INSTALL_GUIDANCE}`"
        ) from exc


def preflight_image_dependencies(
    *,
    render_pdf: bool,
    diagnostics: bool,
) -> None:
    _require("PIL.Image", "image support")
    _require("PIL.ImageOps", "image support")
    if render_pdf:
        _require("pypdfium2", "PDF image rendering")
    if diagnostics:
        _require("cv2", "image diagnostics")
