from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, DecimalException
import os
from pathlib import Path
import stat
import sys

try:
    import fcntl
except ImportError:  # pragma: no cover - supported production targets are POSIX
    fcntl = None


_BUDGET_NAME = "PAPERCONAN_MAX_IMAGE_TOTAL_MB"
_DEFAULT_MAX_IMAGE_TOTAL_MB = Decimal("1500")
_EVIDENCE_BUDGET_NAME = "PAPERCONAN_MAX_IMAGE_EVIDENCE_MB"
_DEFAULT_MAX_IMAGE_EVIDENCE_MB = Decimal("20")
_MEBIBYTE = Decimal(1024 * 1024)
_PUBLICATION_LOCK_NAME = ".paperconan-image-publication.lock"


class ImageArtifactBudgetExceeded(ValueError):
    pass


def _budget_exhausted_error() -> ImageArtifactBudgetExceeded:
    return ImageArtifactBudgetExceeded(
        "image artifact budget exhausted "
        f"({_BUDGET_NAME})"
    )


def _mebibyte_limit_bytes(name: str, default: Decimal) -> int:
    raw = os.environ.get(name, str(default))
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


def _max_image_total_bytes() -> int:
    return _mebibyte_limit_bytes(
        _BUDGET_NAME,
        _DEFAULT_MAX_IMAGE_TOTAL_MB,
    )


def report_image_evidence_bytes() -> int:
    try:
        return _mebibyte_limit_bytes(
            _EVIDENCE_BUDGET_NAME,
            _DEFAULT_MAX_IMAGE_EVIDENCE_MB,
        )
    except ValueError:
        return 0


def regular_file_size(directory_fd: int, name: str) -> int:
    try:
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return 0
    return current.st_size if stat.S_ISREG(current.st_mode) else 0


def _regular_tree_size(
    directory_fd: int,
    *,
    path_parts: tuple[str, ...] = (),
    excluded_entries: dict[tuple[str, ...], tuple[int, int]] | None = None,
) -> int:
    total = 0
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name in os.listdir(directory_fd):
        relative_parts = (*path_parts, name)
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if stat.S_ISREG(current.st_mode):
            expected_identity = (excluded_entries or {}).get(relative_parts)
            if expected_identity == (current.st_dev, current.st_ino):
                continue
            total += current.st_size
        elif stat.S_ISDIR(current.st_mode):
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                total += _regular_tree_size(
                    child_fd,
                    path_parts=relative_parts,
                    excluded_entries=excluded_entries,
                )
            finally:
                os.close(child_fd)
        if total > sys.maxsize:
            raise ValueError("image artifact tree is too large to budget")
    return total


def _verify_publication_lock_entry(root_fd: int, lock_fd: int) -> None:
    opened = os.fstat(lock_fd)
    current = os.stat(
        _PUBLICATION_LOCK_NAME,
        dir_fd=root_fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError("image artifact publication lock changed")


@contextmanager
def image_publication_lock(root_fd: int):
    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("image artifact publication locking is unavailable")
    lock_fd = -1
    try:
        try:
            lock_fd = os.open(
                _PUBLICATION_LOCK_NAME,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _verify_publication_lock_entry(root_fd, lock_fd)
        except (OSError, TypeError, NotImplementedError) as exc:
            raise ValueError(
                "image artifact publication locking is unavailable"
            ) from exc
        active_error = False
        try:
            yield
        except BaseException:
            active_error = True
            raise
        finally:
            try:
                _verify_publication_lock_entry(root_fd, lock_fd)
            except (OSError, ValueError):
                if not active_error:
                    raise
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


class ImageArtifactBudget:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.used_bytes = 0
        self.temporary_bytes = 0
        self._initialized = False

    @classmethod
    def from_environment(cls) -> ImageArtifactBudget:
        return cls(_max_image_total_bytes())

    def initialize_from_images_fd(self, images_fd: int) -> None:
        if self._initialized:
            return
        self.used_bytes = _regular_tree_size(images_fd)
        self._initialized = True

    def resynchronize_from_images_fd(
        self,
        images_fd: int,
        *,
        excluded_entries: dict[tuple[str, ...], tuple[int, int]] | None = None,
    ) -> None:
        self.used_bytes = _regular_tree_size(
            images_fd,
            excluded_entries=excluded_entries,
        )
        self._initialized = True

    def ensure_within_limit(self) -> None:
        if self.used_bytes + self.temporary_bytes > self.max_bytes:
            raise _budget_exhausted_error()

    def initialize_from_root(self, root: Path) -> None:
        if self._initialized:
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = images_fd = None
        try:
            root_fd = os.open(root, flags)
            try:
                images_fd = os.open(
                    "images",
                    flags,
                    dir_fd=root_fd,
                )
            except FileNotFoundError:
                self.used_bytes = 0
                self._initialized = True
                return
            self.initialize_from_images_fd(images_fd)
        except (OSError, TypeError, NotImplementedError) as exc:
            raise ValueError(
                "image artifact budget cannot inspect artifact root"
            ) from exc
        finally:
            if images_fd is not None:
                os.close(images_fd)
            if root_fd is not None:
                os.close(root_fd)

    def reserve_temporary(self, size: int) -> None:
        if size < 0:
            raise ValueError("image artifact size cannot be negative")
        if self.used_bytes + self.temporary_bytes + size > self.max_bytes:
            raise _budget_exhausted_error()
        self.temporary_bytes += size

    def release_temporary(self, size: int) -> None:
        self.temporary_bytes = max(0, self.temporary_bytes - max(0, size))

    def require_replacement(
        self,
        *,
        existing_size: int,
        staged_size: int,
    ) -> None:
        if existing_size < 0 or staged_size < 0:
            raise ValueError("image artifact size cannot be negative")
        committed_without_existing = max(0, self.used_bytes - existing_size)
        projected = (
            committed_without_existing
            + self.temporary_bytes
            + staged_size
        )
        if projected > self.max_bytes:
            raise _budget_exhausted_error()

    def commit_replacement(
        self,
        *,
        existing_size: int,
        staged_size: int,
    ) -> None:
        self.used_bytes = max(0, self.used_bytes - existing_size) + staged_size
