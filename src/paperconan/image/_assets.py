from __future__ import annotations

from decimal import Decimal, DecimalException
import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

from . import ImageDependencyError
from ._budget import (
    ImageArtifactBudget,
    ImageArtifactBudgetExceeded,
    image_publication_lock,
    regular_file_size,
)
from ._dependencies import preflight_image_dependencies


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
_MAX_IMAGE_MB = 100.0
_MAX_IMAGE_BYTES = int(_MAX_IMAGE_MB * 1024 * 1024)
_MAX_IMAGE_PIXELS = 100_000_000
_MAX_IMAGE_ASSETS = 1000
_MAX_SOURCE_PROVENANCE_BYTES = 8 * 1024 * 1024
_SOURCE_PROVENANCE_NAME = "paperconan_source.json"
_SOURCE_PROVENANCE_READ_BYTES = 64 * 1024
_PDF_DPI = 200
_MEBIBYTE = Decimal(1024 * 1024)


class _AssetPublicationRecoveryError(RuntimeError):
    pass


def _max_image_bytes() -> int:
    name = "PAPERCONAN_MAX_IMAGE_MB"
    raw = os.environ.get(name)
    if raw is None:
        return _MAX_IMAGE_BYTES
    try:
        value = Decimal(raw)
        byte_value = value * _MEBIBYTE
    except (DecimalException, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name} limit") from exc
    if (
        not value.is_finite()
        or value < 0
        or not byte_value.is_finite()
        or byte_value > sys.maxsize
    ):
        raise ValueError(f"invalid {name} limit")
    return int(byte_value)


def _max_image_pixels() -> int:
    return _non_negative_int_limit(
        "PAPERCONAN_MAX_IMAGE_PIXELS",
        _MAX_IMAGE_PIXELS,
    )


def _max_image_assets() -> int:
    return _non_negative_int_limit(
        "PAPERCONAN_MAX_IMAGE_ASSETS",
        _MAX_IMAGE_ASSETS,
    )


def _non_negative_int_limit(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid {name} limit") from exc
    if value < 0 or value > sys.maxsize:
        raise ValueError(f"invalid {name} limit")
    return value


def _image_mb_limit_label() -> str:
    raw = os.environ.get("PAPERCONAN_MAX_IMAGE_MB")
    return raw if raw is not None else f"{_MAX_IMAGE_MB:g}"


def _load_pillow(max_image_pixels: int):
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ImageDependencyError(
            'image support requires `pip install "paperconan[image]"`'
        ) from exc
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


def _same_entry_identity(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
    )


def _stable_provenance_file_state(
    initial: os.stat_result,
    opened: os.stat_result,
    current: os.stat_result,
) -> bool:
    return (
        stat.S_ISREG(initial.st_mode)
        and stat.S_ISREG(opened.st_mode)
        and stat.S_ISREG(current.st_mode)
        and _same_entry_identity(initial, opened)
        and _same_entry_identity(initial, current)
        and opened.st_size == initial.st_size
        and current.st_size == initial.st_size
        and opened.st_mtime_ns == initial.st_mtime_ns
        and current.st_mtime_ns == initial.st_mtime_ns
        and opened.st_ctime_ns == initial.st_ctime_ns
        and current.st_ctime_ns == initial.st_ctime_ns
    )


def _read_exact_provenance_fd(fd: int, size: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = size
    chunks = []
    while remaining:
        chunk = os.read(
            fd,
            min(_SOURCE_PROVENANCE_READ_BYTES, remaining),
        )
        if not chunk:
            raise ValueError("provenance sidecar changed during bounded read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise ValueError("provenance sidecar changed during bounded read")
    return b"".join(chunks)


def _source_provenance(in_dir: Path) -> dict[str, dict]:
    root = Path(os.path.abspath(in_dir))
    root_fd = sidecar_fd = -1
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened_root = os.fstat(root_fd)
        current_root = os.stat(root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or not stat.S_ISDIR(current_root.st_mode)
            or not _same_entry_identity(opened_root, current_root)
        ):
            return {}
        sidecar_fd = os.open(
            _SOURCE_PROVENANCE_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        initial = os.fstat(sidecar_fd)
        current = os.stat(
            _SOURCE_PROVENANCE_NAME,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if (
            not _stable_provenance_file_state(initial, initial, current)
            or initial.st_size > _MAX_SOURCE_PROVENANCE_BYTES
        ):
            return {}
        payload = _read_exact_provenance_fd(sidecar_fd, initial.st_size)
        opened = os.fstat(sidecar_fd)
        current = os.stat(
            _SOURCE_PROVENANCE_NAME,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if not _stable_provenance_file_state(initial, opened, current):
            return {}
        if hashlib.sha256(
            _read_exact_provenance_fd(sidecar_fd, initial.st_size)
        ).digest() != hashlib.sha256(payload).digest():
            return {}
        opened = os.fstat(sidecar_fd)
        current = os.stat(
            _SOURCE_PROVENANCE_NAME,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        current_root = os.stat(root, follow_symlinks=False)
        if (
            not _stable_provenance_file_state(initial, opened, current)
            or not _same_entry_identity(opened_root, current_root)
        ):
            return {}
        data = json.loads(payload.decode("utf-8"))
    except (AttributeError, OSError, TypeError, ValueError):
        return {}
    finally:
        if sidecar_fd >= 0:
            os.close(sidecar_fd)
        if root_fd >= 0:
            os.close(root_fd)
    if not isinstance(data, dict):
        return {}
    downloads = data.get("downloads")
    if (
        not isinstance(downloads, list)
        or not all(isinstance(item, dict) for item in downloads)
    ):
        return {}
    return {
        str(item.get("file")): item
        for item in downloads
        if item.get("file")
    }


def _provided_provenance(provenance: dict | None) -> dict[str, dict]:
    if not isinstance(provenance, dict):
        return {}
    downloads = provenance.get("downloads")
    if isinstance(downloads, list):
        return {
            str(item.get("file")): item
            for item in downloads
            if isinstance(item, dict) and item.get("file")
        }
    return {
        str(name): item
        for name, item in provenance.items()
        if isinstance(item, dict)
    }


def _write_preview(image, destination, max_side: int = 1400) -> None:
    preview = image.copy()
    try:
        preview.thumbnail((max_side, max_side))
        if preview.mode not in ("RGB", "L"):
            converted = preview.convert("RGB")
            preview.close()
            preview = converted
        if isinstance(destination, (str, os.PathLike)):
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            destination = path
        preview.save(destination, format="JPEG", quality=86, optimize=True)
    finally:
        preview.close()


def _secure_dirfd_capability_error() -> str | None:
    missing = []
    for name in ("O_DIRECTORY", "O_NOFOLLOW"):
        if not hasattr(os, name):
            missing.append(f"os.{name}")
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    for name in ("link", "open", "mkdir", "rmdir", "stat", "unlink"):
        function = getattr(os, name, None)
        if function is None or function not in supports_dir_fd:
            missing.append(f"os.{name}(dir_fd=...)")
    supports_follow_symlinks = getattr(
        os,
        "supports_follow_symlinks",
        frozenset(),
    )
    if getattr(os, "stat", None) not in supports_follow_symlinks:
        missing.append("os.stat(follow_symlinks=False)")
    if not missing:
        return None
    return ", ".join(missing)


def _require_secure_dirfd_publication() -> None:
    missing = _secure_dirfd_capability_error()
    if missing is not None:
        raise ValueError(
            "secure image asset publication is unavailable on this platform; "
            f"required dir_fd/no-follow capabilities missing: {missing}"
        )


def _secure_dirfd_runtime_error(exc: Exception) -> ValueError:
    detail = _sanitized_exception_context(exc)
    return ValueError(
        "secure image asset publication is unavailable on this platform; "
        f"required dir_fd/no-follow operation failed ({detail})"
    )


def _sanitized_exception_context(exc: Exception) -> str:
    message = str(exc) or "no detail"
    message = re.sub(
        r"(?i)\b(?:token|password|secret|signature|key)=[^\s,;]+",
        "[credential]",
        message,
    )
    message = re.sub(r"https?://[^\s,;]+", "[url]", message)
    message = re.sub(
        r"(?<!\w)(?:[A-Za-z]:)?[/\\][^\s,;]+",
        "[path]",
        message,
    )
    message = " ".join(message.split())
    if len(message) > 180:
        message = message[:177] + "..."
    return f"{exc.__class__.__name__}: {message}"


def _probe_link_dirfd(directory_fd: int) -> None:
    token = secrets.token_hex(8)
    source_name = f".paperconan-dirfd-probe-{token}.src"
    destination_name = f".paperconan-dirfd-probe-{token}.dst"
    probe_fd = None
    try:
        probe_fd = os.open(
            source_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.close(probe_fd)
        probe_fd = None
        os.link(
            source_name,
            destination_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        source = os.stat(
            source_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        current = os.stat(
            destination_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(source.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (source.st_dev, source.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise ValueError(
                "secure image asset publication dir_fd link probe was not regular"
            )
    except (AttributeError, NotImplementedError, TypeError) as exc:
        raise _secure_dirfd_runtime_error(exc) from exc
    finally:
        if probe_fd is not None:
            os.close(probe_fd)
        _unlink_at(source_name, directory_fd)
        _unlink_at(destination_name, directory_fd)


def _open_child_directory(parent_fd: int, name: str, display_path: Path) -> int:
    try:
        os.mkdir(name, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError(
            f"{display_path}: resolves outside artifact root or is not a directory"
        ) from exc


@contextmanager
def _asset_output_directories(output_root: Path):
    _require_secure_dirfd_publication()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(output_root, flags)
    except (AttributeError, NotImplementedError, TypeError) as exc:
        raise _secure_dirfd_runtime_error(exc) from exc
    except OSError as exc:
        raise ValueError(
            "image artifact root is not a stable no-follow directory"
        ) from exc
    images_fd = native_fd = preview_fd = None
    try:
        images_fd = _open_child_directory(
            root_fd,
            "images",
            output_root / "images",
        )
        _probe_link_dirfd(images_fd)
        native_fd = _open_child_directory(
            images_fd,
            "native",
            output_root / "images" / "native",
        )
        preview_fd = _open_child_directory(
            images_fd,
            "preview",
            output_root / "images" / "preview",
        )
        yield root_fd, images_fd, native_fd, preview_fd
    finally:
        for fd in (preview_fd, native_fd, images_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _verify_directory_entry(
    parent_fd: int,
    name: str,
    child_fd: int,
    display_path: Path,
) -> None:
    opened = os.fstat(child_fd)
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError(
            f"{display_path}: changed during asset publication"
        ) from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
    ):
        raise ValueError(f"{display_path}: changed during asset publication")


def _verify_asset_directories(
    output_root: Path,
    root_fd: int,
    images_fd: int,
    native_fd: int,
    preview_fd: int,
) -> None:
    opened_root = os.fstat(root_fd)
    try:
        current_root = os.stat(output_root, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("image artifact root changed during asset publication") from exc
    if (
        not stat.S_ISDIR(opened_root.st_mode)
        or not stat.S_ISDIR(current_root.st_mode)
        or current_root.st_dev != opened_root.st_dev
        or current_root.st_ino != opened_root.st_ino
    ):
        raise ValueError("image artifact root changed during asset publication")
    images = output_root / "images"
    _verify_directory_entry(root_fd, "images", images_fd, images)
    _verify_directory_entry(images_fd, "native", native_fd, images / "native")
    _verify_directory_entry(images_fd, "preview", preview_fd, images / "preview")


def _verify_regular_file_entry(
    directory_fd: int,
    name: str,
    file_fd: int,
    display_path: Path,
) -> None:
    opened = os.fstat(file_fd)
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError(
            f"{display_path}: changed during asset publication"
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
    ):
        raise ValueError(f"{display_path}: changed during asset publication")


def _asset_temp_path(directory_fd: int, *, suffix: str) -> tuple[str, int]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(128):
        name = f".paperconan-image-{secrets.token_hex(8)}{suffix}"
        try:
            fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        return name, fd
    raise FileExistsError("could not allocate image staging file")


@contextmanager
def _pdf_staging_directory(root_fd: int):
    name = f".paperconan-rendered-{secrets.token_hex(8)}"
    directory_fd = None
    os.mkdir(name, 0o700, dir_fd=root_fd)
    try:
        directory_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        yield directory_fd
    finally:
        if directory_fd is not None:
            for entry in os.listdir(directory_fd):
                try:
                    os.unlink(entry, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            os.close(directory_fd)
        try:
            os.rmdir(name, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def _unlink_at(name: str | None, directory_fd: int) -> None:
    if name is None:
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _copy_source_to_fd(
    source: Path,
    destination_fd: int,
    *,
    source_fd: int | None = None,
) -> None:
    os.lseek(destination_fd, 0, os.SEEK_SET)
    if source_fd is None:
        source_context = source.open("rb")
    else:
        source_context = os.fdopen(os.dup(source_fd), "rb")
    with source_context as source_fh:
        source_fh.seek(0)
        with os.fdopen(os.dup(destination_fd), "wb") as destination_fh:
            shutil.copyfileobj(source_fh, destination_fh, length=1024 * 1024)


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    with os.fdopen(os.dup(fd), "rb") as fh:
        fh.seek(0)
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _open_stable_source_regular(path: Path):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("secure image source opening is unavailable")
    fd = -1
    try:
        try:
            fd = os.open(path, os.O_RDONLY | nofollow)
            opened = os.fstat(fd)
            current = os.stat(path, follow_symlinks=False)
        except (OSError, TypeError, NotImplementedError) as exc:
            raise ValueError(
                f"{path.name}: image source is not a stable no-follow regular file"
            ) from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise ValueError(
                f"{path.name}: image source is not a stable no-follow regular file"
            )
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            yield fh
    finally:
        if fd >= 0:
            os.close(fd)


def _existing_asset_matches_staged(
    directory_fd: int,
    final_name: str,
    staged_fd: int,
    relative_name: str,
) -> bool:
    try:
        current = os.stat(
            final_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(current.st_mode):
        raise _AssetPublicationRecoveryError(
            "image asset publication retained existing visible entry because "
            f"it is not a regular file: {relative_name}"
        )
    existing_fd = -1
    try:
        existing_fd = os.open(
            final_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        opened = os.fstat(existing_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise _AssetPublicationRecoveryError(
                "image asset publication retained existing visible entry "
                f"because it changed during verification: {relative_name}"
            )
        staged = os.fstat(staged_fd)
        matches = (
            opened.st_size == staged.st_size
            and _sha256_fd(existing_fd) == _sha256_fd(staged_fd)
        )
        final_opened = os.fstat(existing_fd)
        final_current = os.stat(
            final_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_opened.st_mode)
            or not stat.S_ISREG(final_current.st_mode)
            or final_opened.st_size != opened.st_size
            or final_opened.st_mtime_ns != opened.st_mtime_ns
            or final_opened.st_ctime_ns != opened.st_ctime_ns
            or (final_opened.st_dev, final_opened.st_ino)
            != (opened.st_dev, opened.st_ino)
            or (final_current.st_dev, final_current.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise _AssetPublicationRecoveryError(
                "image asset publication retained existing visible entry "
                f"because it changed during verification: {relative_name}"
            )
        if not matches:
            if relative_name.startswith("images/preview/"):
                raise _AssetPublicationRecoveryError(
                    f"delete {relative_name} and rescan if the native asset "
                    "still matches the source; image asset publication retained "
                    "the existing preview because it differs from prepared output"
                )
            raise _AssetPublicationRecoveryError(
                "image asset publication retained existing visible entry "
                f"because it differs from prepared output: {relative_name}"
            )
        return True
    finally:
        if existing_fd >= 0:
            os.close(existing_fd)


def _install_or_reuse_asset(
    directory_fd: int,
    temp_name: str,
    temp_fd: int,
    final_name: str,
    relative_name: str,
) -> bool:
    if _existing_asset_matches_staged(
        directory_fd,
        final_name,
        temp_fd,
        relative_name,
    ):
        return False
    try:
        os.link(
            temp_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except FileExistsError as exc:
        raise _AssetPublicationRecoveryError(
            "image asset publication retained existing visible entry created "
            f"during publication: {relative_name}"
        ) from exc
    return True


def _publish_asset_pair(
    *,
    output_root: Path,
    root_fd: int,
    images_fd: int,
    native_fd: int,
    preview_fd: int,
    native_temp_name: str,
    preview_temp_name: str,
    native_temp_fd: int,
    preview_temp_fd: int,
    native_name: str,
    preview_name: str,
) -> None:
    native = output_root / "images" / "native" / native_name
    preview = output_root / "images" / "preview" / preview_name
    entries = [
        (
            native_fd,
            native_temp_name,
            native_temp_fd,
            native_name,
            native,
            f"images/native/{native_name}",
        ),
        (
            preview_fd,
            preview_temp_name,
            preview_temp_fd,
            preview_name,
            preview,
            f"images/preview/{preview_name}",
        ),
    ]
    installed: list[str] = []
    try:
        _verify_asset_directories(
            output_root,
            root_fd,
            images_fd,
            native_fd,
            preview_fd,
        )
        for directory_fd, temp_name, temp_fd, _, final_path, _ in entries:
            _verify_regular_file_entry(
                directory_fd,
                temp_name,
                temp_fd,
                final_path,
            )
        _verify_asset_directories(
            output_root,
            root_fd,
            images_fd,
            native_fd,
            preview_fd,
        )
        for entry in entries:
            directory_fd, temp_name, temp_fd, final_name, final_path, _ = entry
            _verify_regular_file_entry(
                directory_fd,
                temp_name,
                temp_fd,
                final_path,
            )
            was_installed = _install_or_reuse_asset(
                directory_fd,
                temp_name,
                temp_fd,
                final_name,
                entry[5],
            )
            if was_installed:
                installed.append(entry[5])
                _verify_regular_file_entry(
                    directory_fd,
                    final_name,
                    temp_fd,
                    final_path,
                )
            else:
                _existing_asset_matches_staged(
                    directory_fd,
                    final_name,
                    temp_fd,
                    entry[5],
                )
        _verify_asset_directories(
            output_root,
            root_fd,
            images_fd,
            native_fd,
            preview_fd,
        )
    except Exception as publication_error:
        context = ["image asset publication incomplete"]
        if installed:
            context.append(
                "retained uncertain visible entry or entries: "
                + ", ".join(installed)
            )
        context.append(
            "publication cause: "
            + _sanitized_exception_context(publication_error)
        )
        raise _AssetPublicationRecoveryError(
            "; ".join(context)
        ) from publication_error


def _record_image(
    source: Path,
    output_root: Path,
    *,
    source_type: str,
    source_url: str | None,
    parent_file: str | None = None,
    page: int | None = None,
    render_dpi: int | None = None,
    digest: str | None = None,
    directory_fds: tuple[int, int, int, int] | None = None,
    artifact_budget: ImageArtifactBudget | None = None,
    source_fd: int | None = None,
    max_image_bytes: int | None = None,
    max_image_pixels: int | None = None,
) -> dict:
    if max_image_bytes is None:
        max_image_bytes = _max_image_bytes()
    if max_image_pixels is None:
        max_image_pixels = _max_image_pixels()
    if directory_fds is None:
        budget = artifact_budget or ImageArtifactBudget.from_environment()
        with _asset_output_directories(output_root) as opened_directories:
            budget.initialize_from_images_fd(opened_directories[1])
            return _record_image(
                source,
                output_root,
                source_type=source_type,
                source_url=source_url,
                parent_file=parent_file,
                page=page,
                render_dpi=render_dpi,
                digest=digest,
                directory_fds=opened_directories,
                artifact_budget=budget,
                source_fd=source_fd,
                max_image_bytes=max_image_bytes,
                max_image_pixels=max_image_pixels,
            )
    if artifact_budget is None:
        raise ValueError("image artifact budget is required")
    Image, ImageOps = _load_pillow(max_image_pixels)
    source_stat = os.fstat(source_fd) if source_fd is not None else source.stat()
    if source_stat.st_size > max_image_bytes:
        raise ValueError(
            f"{source.name}: exceeds "
            f"PAPERCONAN_MAX_IMAGE_MB={_image_mb_limit_label()}"
        )
    digest = digest or (
        _sha256_fd(source_fd) if source_fd is not None else _sha256(source)
    )
    asset_id = _asset_id(digest)
    suffix = source.suffix.lower() or ".png"
    stem = asset_id.replace(":", "-")
    native = output_root / "images" / "native" / f"{stem}{suffix}"
    preview = output_root / "images" / "preview" / f"{stem}.jpg"
    native_name = native.name
    preview_name = preview.name
    root_fd, images_fd, native_fd, preview_fd = directory_fds
    native_temp_name = preview_temp_name = None
    native_temp_fd = preview_temp_fd = None
    with image_publication_lock(root_fd):
        try:
            _verify_asset_directories(
                output_root,
                root_fd,
                images_fd,
                native_fd,
                preview_fd,
            )
            native_temp_name, native_temp_fd = _asset_temp_path(
                native_fd,
                suffix=suffix,
            )
            preview_temp_name, preview_temp_fd = _asset_temp_path(
                preview_fd,
                suffix=".jpg",
            )
            _copy_source_to_fd(
                source,
                native_temp_fd,
                source_fd=source_fd,
            )
            if _sha256_fd(native_temp_fd) != digest:
                raise ValueError(
                    f"{source.name}: source changed while preparing image asset"
                )
            os.lseek(native_temp_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(native_temp_fd), "rb") as source_fh:
                with Image.open(source_fh) as image:
                    frame_count = int(getattr(image, "n_frames", 1))
                    if frame_count != 1:
                        raise ValueError(
                            f"{source.name}: multi-frame images are not silently "
                            "truncated; export each frame as a separate image"
                        )
                    exif_orientation = int(image.getexif().get(274, 1) or 1)
                    width, height = image.size
                    if width * height > max_image_pixels:
                        raise ValueError(
                            f"{source.name}: exceeds "
                            f"PAPERCONAN_MAX_IMAGE_PIXELS={max_image_pixels}"
                        )
                    display_image = ImageOps.exif_transpose(image)
                    try:
                        os.lseek(preview_temp_fd, 0, os.SEEK_SET)
                        with os.fdopen(os.dup(preview_temp_fd), "wb") as preview_fh:
                            _write_preview(display_image, preview_fh)
                    finally:
                        if display_image is not image:
                            display_image.close()
                    mime = (
                        Image.MIME.get(image.format)
                        or mimetypes.guess_type(native.name)[0]
                    )
            native_temp_state = os.fstat(native_temp_fd)
            preview_temp_state = os.fstat(preview_temp_fd)
            artifact_budget.resynchronize_from_images_fd(
                images_fd,
                excluded_entries={
                    ("native", native_temp_name): (
                        native_temp_state.st_dev,
                        native_temp_state.st_ino,
                    ),
                    ("preview", preview_temp_name): (
                        preview_temp_state.st_dev,
                        preview_temp_state.st_ino,
                    ),
                },
            )
            existing_size = (
                regular_file_size(native_fd, native_name)
                + regular_file_size(preview_fd, preview_name)
            )
            staged_size = (
                os.fstat(native_temp_fd).st_size
                + os.fstat(preview_temp_fd).st_size
            )
            artifact_budget.require_replacement(
                existing_size=existing_size,
                staged_size=staged_size,
            )
            _publish_asset_pair(
                output_root=output_root,
                root_fd=root_fd,
                images_fd=images_fd,
                native_fd=native_fd,
                preview_fd=preview_fd,
                native_temp_name=native_temp_name,
                preview_temp_name=preview_temp_name,
                native_temp_fd=native_temp_fd,
                preview_temp_fd=preview_temp_fd,
                native_name=native_name,
                preview_name=preview_name,
            )
            artifact_budget.commit_replacement(
                existing_size=existing_size,
                staged_size=staged_size,
            )
        finally:
            active_error = sys.exc_info()[0] is not None
            if native_temp_fd is not None:
                os.close(native_temp_fd)
            if preview_temp_fd is not None:
                os.close(preview_temp_fd)
            _unlink_at(native_temp_name, native_fd)
            _unlink_at(preview_temp_name, preview_fd)
            try:
                artifact_budget.resynchronize_from_images_fd(images_fd)
                if not active_error:
                    artifact_budget.ensure_within_limit()
            except Exception:
                if not active_error:
                    raise
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


def _exception_text(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def _pdf_renderer_error(exc: Exception) -> str:
    detail = " ".join(_exception_text(exc).split())
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return f"PDF image rendering unavailable: {detail}"


def _pdf_page_render_bound(pixel_width: int, pixel_height: int) -> int:
    raw_bytes = pixel_width * pixel_height * 4
    overhead = pixel_height + max(64 * 1024, raw_bytes // 100)
    bound = raw_bytes + overhead
    if bound < 0 or bound > sys.maxsize:
        raise ValueError("PDF page render bound exceeds platform capacity")
    return bound


def _render_pdf_pages(
    pdf_path: Path,
    pdf_fh,
    temp_dir_fd: int,
    artifact_budget: ImageArtifactBudget,
    *,
    max_image_pixels: int | None = None,
):
    if max_image_pixels is None:
        max_image_pixels = _max_image_pixels()
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImageDependencyError(
            'PDF image rendering requires `pip install "paperconan[image]"`'
        ) from exc
    pdf_fh.seek(0)
    doc = pdfium.PdfDocument(pdf_fh)
    scale = _PDF_DPI / 72.0
    try:
        page_count = len(doc)
        for index in range(page_count):
            page = None
            bitmap = None
            image = None
            dest_name = None
            dest_fd = None
            page_error = None
            reserved_size = 0
            try:
                page = doc[index]
                width, height = page.get_size()
                pixel_width = math.ceil(width * scale)
                pixel_height = math.ceil(height * scale)
                if pixel_width * pixel_height > max_image_pixels:
                    raise ValueError(
                        f"{pdf_path.name} page {index + 1}: exceeds "
                        f"PAPERCONAN_MAX_IMAGE_PIXELS={max_image_pixels}"
                    )
                reserved_size = _pdf_page_render_bound(
                    pixel_width,
                    pixel_height,
                )
                artifact_budget.reserve_temporary(reserved_size)
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                dest_name = f"{pdf_path.stem}.p{index + 1}.png"
                dest_fd = os.open(
                    dest_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=temp_dir_fd,
                )
                with os.fdopen(os.dup(dest_fd), "wb") as destination:
                    image.save(destination, format="PNG")
                    destination.flush()
                if os.fstat(dest_fd).st_size > reserved_size:
                    raise ImageArtifactBudgetExceeded(
                        "rendered PDF page exceeds reserved image artifact bound"
                    )
            except Exception as exc:
                page_error = _exception_text(exc)
            finally:
                for resource in (image, bitmap, page):
                    if resource is None:
                        continue
                    try:
                        resource.close()
                    except Exception as exc:
                        if page_error is None:
                            page_error = _exception_text(exc)
                if page_error is not None and dest_name is not None:
                    try:
                        if dest_fd is not None:
                            os.close(dest_fd)
                            dest_fd = None
                        _unlink_at(dest_name, temp_dir_fd)
                    except Exception as exc:
                        cleanup_error = _exception_text(exc)
                        page_error = (
                            f"{page_error}; partial page cleanup failed: "
                            f"{cleanup_error}"
                        )
            if page_error is not None:
                artifact_budget.release_temporary(reserved_size)
                yield index + 1, page_count, None, None, page_error
                continue
            try:
                yield (
                    index + 1,
                    page_count,
                    Path(dest_name),
                    dest_fd,
                    None,
                )
            finally:
                artifact_budget.release_temporary(reserved_size)
                os.close(dest_fd)
                _unlink_at(dest_name, temp_dir_fd)
    finally:
        doc.close()


def _stable_name_key(name: str) -> tuple[str, str]:
    return name.casefold(), name


def _asset_limit_error() -> str:
    return (
        "image asset limit reached; set "
        "PAPERCONAN_MAX_IMAGE_ASSETS to raise"
    )


def prepare_image_assets(
    in_dir: str,
    out_dir: str,
    *,
    provenance: dict | None = None,
    render_pdf: bool = True,
    artifact_budget: ImageArtifactBudget | None = None,
) -> tuple[list[dict], list[dict]]:
    source_root = Path(in_dir)
    output_root = Path(out_dir)
    try:
        budget = artifact_budget or ImageArtifactBudget.from_environment()
        max_image_bytes = _max_image_bytes()
        max_image_pixels = _max_image_pixels()
        max_image_assets = _max_image_assets()
    except ValueError as exc:
        return [], [{"error": str(exc)}]
    downloads = _source_provenance(source_root)
    downloads.update(_provided_provenance(provenance))
    candidates = []
    pdfs = []
    for path in source_root.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            candidates.append(path)
        elif suffix == ".pdf":
            pdfs.append(path)
    candidates.sort(key=lambda path: _stable_name_key(path.name))
    pdfs.sort(key=lambda path: _stable_name_key(path.name))
    if candidates or (render_pdf and pdfs):
        preflight_image_dependencies(
            render_pdf=False,
            diagnostics=False,
        )
    output_root.mkdir(parents=True, exist_ok=True)
    assets_by_digest: dict[str, dict] = {}
    errors: list[dict] = []
    pdf_page_attempts = 0
    output_context = _asset_output_directories(output_root)
    try:
        directory_fds = output_context.__enter__()
    except ImageDependencyError:
        raise
    except Exception as exc:
        return [], [{"error": str(exc)}]
    try:
        budget.initialize_from_images_fd(directory_fds[1])
    except ValueError as exc:
        output_context.__exit__(None, None, None)
        return [], [{"error": str(exc)}]

    def add(path: Path, *, source_fd: int | None = None, **metadata):
        try:
            source_stat = (
                os.fstat(source_fd)
                if source_fd is not None
                else path.stat()
            )
            if source_stat.st_size > max_image_bytes:
                raise ValueError(
                    f"{path.name}: exceeds "
                    f"PAPERCONAN_MAX_IMAGE_MB={_image_mb_limit_label()}"
                )
            digest = (
                _sha256_fd(source_fd)
                if source_fd is not None
                else _sha256(path)
            )
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})
            return "error"
        existing = assets_by_digest.get(digest)
        if existing is not None:
            existing["source_files"] = sorted(
                set(existing["source_files"] + [path.name]),
                key=_stable_name_key,
            )
            return "duplicate"
        if len(assets_by_digest) >= max_image_assets:
            errors.append({"file": path.name, "error": _asset_limit_error()})
            return "limit"
        try:
            asset = _record_image(
                path,
                output_root,
                digest=digest,
                directory_fds=directory_fds,
                artifact_budget=budget,
                source_fd=source_fd,
                max_image_bytes=max_image_bytes,
                max_image_pixels=max_image_pixels,
                **metadata,
            )
        except ImageDependencyError:
            raise
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})
            return "error"
        assets_by_digest[digest] = asset
        return "added"

    try:
        for path in candidates:
            prov = downloads.get(path.name) or {}
            try:
                with _open_stable_source_regular(path) as source_fh:
                    add(
                        path,
                        source_fd=source_fh.fileno(),
                        source_type=(
                            "fetched_image"
                            if prov.get("source_url")
                            else "local_image"
                        ),
                        source_url=prov.get("source_url"),
                    )
            except ImageDependencyError:
                raise
            except Exception as exc:
                errors.append({"file": path.name, "error": str(exc)})
        pdf_renderer_available = True
        if render_pdf and pdfs:
            try:
                preflight_image_dependencies(
                    render_pdf=True,
                    diagnostics=False,
                )
            except ImageDependencyError as exc:
                pdf_renderer_available = False
                error = _pdf_renderer_error(exc)
                errors.extend(
                    {"file": pdf.name, "error": error}
                    for pdf in pdfs
                )
        if render_pdf and pdf_renderer_available:
            with _pdf_staging_directory(directory_fds[0]) as temp_dir_fd:
                for pdf in pdfs:
                    try:
                        with _open_stable_source_regular(pdf) as pdf_fh:
                            if os.fstat(pdf_fh.fileno()).st_size > max_image_bytes:
                                raise ValueError(
                                    f"{pdf.name}: exceeds "
                                    "PAPERCONAN_MAX_IMAGE_MB="
                                    f"{_image_mb_limit_label()}"
                                )
                            if pdf_page_attempts >= max_image_assets:
                                errors.append({
                                    "file": pdf.name,
                                    "page": 1,
                                    "error": _asset_limit_error(),
                                })
                                continue
                            if len(assets_by_digest) >= max_image_assets:
                                errors.append({
                                    "file": pdf.name,
                                    "error": _asset_limit_error(),
                                })
                                continue
                            pages = _render_pdf_pages(
                                pdf,
                                pdf_fh,
                                temp_dir_fd,
                                budget,
                                max_image_pixels=max_image_pixels,
                            )
                            try:
                                for (
                                    page_number,
                                    page_count,
                                    page_path,
                                    page_fd,
                                    page_error,
                                ) in pages:
                                    pdf_page_attempts += 1
                                    if page_error is not None:
                                        errors.append({
                                            "file": pdf.name,
                                            "page": page_number,
                                            "error": page_error,
                                        })
                                    else:
                                        add(
                                            page_path,
                                            source_fd=page_fd,
                                            source_type="pdf_page",
                                            source_url=(
                                                downloads.get(pdf.name) or {}
                                            ).get("source_url"),
                                            parent_file=pdf.name,
                                            page=page_number,
                                            render_dpi=_PDF_DPI,
                                        )
                                    if (
                                        (
                                            pdf_page_attempts >= max_image_assets
                                            or len(assets_by_digest)
                                            >= max_image_assets
                                        )
                                        and page_number < page_count
                                    ):
                                        errors.append({
                                            "file": pdf.name,
                                            "page": page_number + 1,
                                            "error": _asset_limit_error(),
                                        })
                                        break
                            except ImageDependencyError:
                                raise
                            except Exception as exc:
                                errors.append({
                                    "file": pdf.name,
                                    "error": str(exc),
                                })
                            finally:
                                pages.close()
                    except ImageDependencyError:
                        raise
                    except Exception as exc:
                        errors.append({
                            "file": pdf.name,
                            "error": str(exc),
                        })
        try:
            _verify_asset_directories(
                output_root,
                *directory_fds,
            )
        except Exception as exc:
            errors.append({"error": str(exc)})
    finally:
        output_context.__exit__(None, None, None)
    assets = sorted(
        assets_by_digest.values(),
        key=lambda asset: (
            _stable_name_key(asset["file"]),
            asset["asset_id"],
        ),
    )
    return assets, errors
