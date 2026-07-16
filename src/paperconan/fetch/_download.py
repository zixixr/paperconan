"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
from bisect import bisect_right
import codecs
from collections import Counter
from contextlib import contextmanager, nullcontext
import ctypes
from dataclasses import dataclass
from enum import Enum
import errno
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import stat
import struct
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from paperconan._input import is_supported_input
from paperconan._source_sidecar import (
    SidecarLimitError,
    encode_sidecar,
    parse_sidecar_bytes,
    read_sidecar,
)
from . import _http
from ._files import asset_type

# Provenance sidecar written next to downloads; read back by scan_dir to stamp scan.json.
SOURCE_SIDECAR = "paperconan_source.json"
_RESERVED_SOURCE_SIDECAR_REASON = "reserved provenance sidecar basename"
_ZIP_MEMBER_READ_EXCEPTIONS = (
    zipfile.BadZipFile,
    zlib.error,
    RuntimeError,
    NotImplementedError,
)
_TAR_STREAM_READ_EXCEPTIONS = (
    gzip.BadGzipFile,
    EOFError,
    zlib.error,
)

_UA = "paperconan-fetch/0.6 (+https://github.com/zixixr/paperconan)"
_DEFAULT_MAX = 50 * 1024 * 1024     # 50 MB — per individual file / per extracted table
# A supplementary archive bundles ALL supplementary material (often 100MB+ of video/
# imaging) but we only keep its small tabular members, so it needs a much larger cap
# than a single file — otherwise big-but-tabular Europe PMC zips truncate and are lost.
_ARCHIVE_MAX = 250 * 1024 * 1024    # 250 MB — whole supplementary zip
# Per-PAPER total cap: a genomics supplement can hold hundreds of tabular files that extract
# to many GB and fill the worker disk before audit cleans up. Stop extracting/downloading once
# a paper's out_dir reaches this. Default 1.5 GB; raise PAPERCONAN_MAX_PAPER_MB on big disks.
_MAX_PAPER_MB = float(os.environ.get("PAPERCONAN_MAX_PAPER_MB", "1500"))
_MAX_PAPER_BYTES = int(_MAX_PAPER_MB * 1024 * 1024)
_ARCHIVE_MEMBER_LIMIT = int(
    os.environ.get("PAPERCONAN_ARCHIVE_MEMBER_LIMIT", "10000")
)
_ARCHIVE_MEMBER_NAME_BYTES = int(
    os.environ.get(
        "PAPERCONAN_ARCHIVE_MEMBER_NAME_BYTES",
        str(8 * 1024 * 1024),
    )
)
_ARCHIVE_METADATA_BYTES = int(
    os.environ.get(
        "PAPERCONAN_ARCHIVE_METADATA_BYTES",
        str(8 * 1024 * 1024),
    )
)
_ARCHIVE_SPARSE_ENTRY_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_ARCHIVE_SPARSE_ENTRY_LIMIT",
        "100000",
    )
)
_ARCHIVE_TAR_TRAVERSAL_BYTES = int(
    os.environ.get(
        "PAPERCONAN_ARCHIVE_TAR_TRAVERSAL_BYTES",
        str(1024 * 1024 * 1024),
    )
)
_ARCHIVE_OUTPUT_FILE_LIMIT = int(
    os.environ.get("PAPERCONAN_ARCHIVE_OUTPUT_FILE_LIMIT", "5000")
)
_SOURCE_SIDECAR_MAX_BYTES = int(
    os.environ.get(
        "PAPERCONAN_SOURCE_SIDECAR_MAX_BYTES",
        str(2 * 1024 * 1024),
    )
)
_MAX_SOURCE_SIDECAR_BYTES = _SOURCE_SIDECAR_MAX_BYTES
_SOURCE_SIDECAR_ENTRY_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_SOURCE_SIDECAR_ENTRY_LIMIT",
        "10000",
    )
)
_SOURCE_SIDECAR_NAME_BYTES = int(
    os.environ.get(
        "PAPERCONAN_SOURCE_SIDECAR_NAME_BYTES",
        str(1024 * 1024),
    )
)
_MANAGED_OUTPUT_NAME_BYTES = int(
    os.environ.get(
        "PAPERCONAN_MANAGED_OUTPUT_NAME_BYTES",
        "4096",
    )
)
_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT",
        "128",
    )
)
_TRANSIENT_CLEANUP_ATTEMPTS = 2
_INTERNAL_STATE_ENTRY_LIMIT = 4096
_INTERNAL_STATE_NAME_BYTES = 1024 * 1024
_INTERNAL_STATE_METADATA_LIMIT = 4096
_MAX_PUBLISHED_FILES_PER_CANDIDATE = 1000
_MAX_ARCHIVE_MEMBERS_PER_CANDIDATE = 1000
_MAX_RAW_ZIP_ENTRIES_PER_ARCHIVE = 4096
_MAX_RAW_TAR_MEMBERS_PER_ARCHIVE = 4096
_MAX_UNCOMPRESSED_TAR_BYTES_PER_ARCHIVE = 2 * _ARCHIVE_MAX
_FILE_COPY_CHUNK_BYTES = 64 * 1024
_URL_IN_ERROR = re.compile(r"https?://[^\s]+")
_URL_POLICY_SKIP_REASON = "download URL rejected by HTTP(S) policy"
_ZIP_UTF8_FILENAME_FLAG = 1 << 11
_ZIP64_EXTRA_FIELD = 0x0001
_ZIP_UNICODE_PATH_EXTRA_FIELD = 0x7075
_ZIP_EOCD = struct.Struct("<4s4H2IH")
_ZIP64_LOCATOR = struct.Struct("<4sIQI")
_ZIP64_EOCD = struct.Struct("<4sQ2H2I4Q")
_ZIP_CENTRAL_FILE_HEADER = struct.Struct("<4s6H3I5H2I")
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_MAX_COMMENT_BYTES = 0xFFFF
_ZIP16_SENTINEL = 0xFFFF
_ZIP32_SENTINEL = 0xFFFFFFFF
_ROLLBACK_NAME_HASH = hashlib.sha256


class _SizeLimitExceeded(ValueError):
    pass


class _UnstableRegularFileError(OSError):
    pass


class _IdentityBoundMutationUnavailableError(OSError):
    pass


class _ManagedOutputRecoveryRequiredError(_UnstableRegularFileError):
    def __init__(self, message, *, recovery_paths=()):
        self.recovery_paths = tuple(recovery_paths)
        super().__init__(message)


class _ManagedOutputPrepareError(_UnstableRegularFileError):
    pass


class _SourceSidecarLimitError(ValueError):
    pass


class _SourceSidecarPublicationError(ValueError):
    pass


class _SourceSidecarRecoveryRequiredError(
    _SourceSidecarPublicationError
):
    def __init__(
        self,
        message,
        *,
        recovery_paths,
        operation_error,
        rollback_error=None,
    ):
        self.recovery_paths = tuple(recovery_paths)
        self.operation_error = operation_error
        self.rollback_error = rollback_error
        details = ", ".join(self.recovery_paths)
        if details:
            message = (
                f"{message}; retained sidecar recovery path: {details}"
            )
        super().__init__(message)


class _ManagedOutputJournalState(Enum):
    OPEN = "OPEN"
    COMMIT_CLEANUP = "COMMIT_CLEANUP"
    COMMITTED = "COMMITTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class _PaperDataLimitError(ValueError):
    pass


class _ArchiveReadError(Exception):
    pass


class _PublicationRecoveryError(OSError):
    pass


@dataclass(frozen=True)
class _PublishedOutputFile:
    filename: str
    size: int
    identity: tuple[int, int]
    sha256: str
    created: bool
    cleanup_warning: str | None = None

    def display_path(self, output):
        return os.path.join(output.path, self.filename)


@dataclass
class _SourceSidecarWriteResult:
    pending_cleanup: tuple[object, ...] = ()
    cleanup_warning: str | None = None


class _ArchiveExtractionPaths(list):
    def __init__(self, paths=(), *, skipped=()):
        super().__init__(paths)
        self.skipped = list(skipped)


@dataclass
class _CandidateCardinality:
    max_published_files: int
    max_archive_members: int
    published_files: int = 0
    archive_members: int = 0

    def can_publish(self):
        return self.published_files < self.max_published_files

    def record_publication(self):
        self.published_files += 1

    def claim_archive_member(self):
        if self.archive_members >= self.max_archive_members:
            return False
        self.archive_members += 1
        return True


@dataclass(frozen=True)
class _ManagedFileState:
    name: str
    size: int
    sha256: str
    identity: tuple[int, int]
    created: bool = False
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    cleanup_warning: str | None = None


_PENDING_CLEANUP_WITHOUT_PATH = object()
# FreeBSD funlinkat atomically requires the name and descriptor to match.
_FREEBSD_AT_REMOVEDIR = 0x0800
_IDENTITY_BOUND_UNSUPPORTED_ERRNOS = frozenset({
    errno.ENOSYS,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
})
_IN_ROOT_TRANSACTION_PREFIXES = (
    ".paperconan-download-",
    ".paperconan-member-",
    ".paperconan-archive-",
    ".paperconan-zip-snapshot-",
    ".paperconan-publish-",
    f".{SOURCE_SIDECAR}.",
)
_SIBLING_ROLLBACK_PREFIXES = (
    ".paperconan-output-rollback-",
    ".paperconan-sidecar-rollback-",
)


def _load_funlinkat():
    if not sys.platform.startswith("freebsd"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        funlinkat = libc.funlinkat
        funlinkat.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
        )
        funlinkat.restype = ctypes.c_int
    except (AttributeError, OSError, TypeError):
        return None

    def remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        flags = _FREEBSD_AT_REMOVEDIR if is_directory else 0
        encoded_name = os.fsencode(name)
        ctypes.set_errno(0)
        if funlinkat(
            directory_fd,
            encoded_name,
            descriptor,
            flags,
        ) == 0:
            return
        error_number = ctypes.get_errno() or errno.EIO
        if error_number in _IDENTITY_BOUND_UNSUPPORTED_ERRNOS:
            raise _IdentityBoundMutationUnavailableError(
                error_number,
                "identity-bound cleanup is unavailable",
                os.fsdecode(encoded_name),
            )
        raise OSError(
            error_number,
            os.strerror(error_number),
            os.fsdecode(encoded_name),
        )

    return remove


_FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING = object()
_FUNLINKAT_RUNTIME_UNAVAILABLE = object()
_FUNLINKAT_STATE_LOCK = threading.RLock()
_FUNLINKAT_CANDIDATE_STATE = threading.local()
_FUNLINKAT = _load_funlinkat()


def _candidate_transaction_active():
    return (
        getattr(_FUNLINKAT_CANDIDATE_STATE, "depth", 0) > 0
    )


@contextmanager
def _candidate_transaction_admission():
    global _FUNLINKAT
    with _FUNLINKAT_STATE_LOCK:
        if (
            _FUNLINKAT
            is _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
            or _FUNLINKAT is _FUNLINKAT_RUNTIME_UNAVAILABLE
        ):
            yield False
            return
        previous_depth = getattr(
            _FUNLINKAT_CANDIDATE_STATE,
            "depth",
            0,
        )
        _FUNLINKAT_CANDIDATE_STATE.depth = previous_depth + 1
        try:
            yield True
        finally:
            if previous_depth:
                _FUNLINKAT_CANDIDATE_STATE.depth = previous_depth
            else:
                try:
                    del _FUNLINKAT_CANDIDATE_STATE.depth
                except AttributeError:
                    pass
                if (
                    _FUNLINKAT
                    is _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
                ):
                    _FUNLINKAT = _FUNLINKAT_RUNTIME_UNAVAILABLE


@contextmanager
def _transaction_state_allocation():
    with _FUNLINKAT_STATE_LOCK:
        if (
            _FUNLINKAT is _FUNLINKAT_RUNTIME_UNAVAILABLE
            or (
                _FUNLINKAT
                is _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
                and not _candidate_transaction_active()
            )
        ):
            raise _IdentityBoundMutationUnavailableError(
                "identity-bound cleanup is unavailable"
            )
        yield


def _identity_bound_mutation_available():
    with _FUNLINKAT_STATE_LOCK:
        return (
            _FUNLINKAT is not None
            and _FUNLINKAT
            is not _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
            and _FUNLINKAT is not _FUNLINKAT_RUNTIME_UNAVAILABLE
        )


def _require_identity_bound_mutation():
    if not _identity_bound_mutation_available():
        raise _IdentityBoundMutationUnavailableError(
            "identity-bound cleanup is unavailable"
        )


def _identity_bound_remove(
    directory_fd,
    name,
    descriptor,
    *,
    is_directory,
):
    global _FUNLINKAT
    with _FUNLINKAT_STATE_LOCK:
        if (
            _FUNLINKAT is None
            or _FUNLINKAT
            is _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
            or _FUNLINKAT is _FUNLINKAT_RUNTIME_UNAVAILABLE
        ):
            raise _IdentityBoundMutationUnavailableError(
                "identity-bound cleanup is unavailable"
            )
        remove = _FUNLINKAT
        try:
            remove(
                directory_fd,
                name,
                descriptor,
                is_directory=is_directory,
            )
        except OSError as error:
            if error.errno in _IDENTITY_BOUND_UNSUPPORTED_ERRNOS:
                if _candidate_transaction_active():
                    _FUNLINKAT = (
                        _FUNLINKAT_RUNTIME_UNAVAILABLE_PENDING
                    )
                else:
                    _FUNLINKAT = _FUNLINKAT_RUNTIME_UNAVAILABLE
            raise


def _rollback_directory_name(output, prefix):
    opened_output = os.fstat(output.fd)
    identity_seed = (
        f"{opened_output.st_dev}:{opened_output.st_ino}:{prefix}"
    ).encode("ascii")
    return (
        f"{prefix}"
        f"{_ROLLBACK_NAME_HASH(identity_seed).hexdigest()[:16]}"
    )


class _PinnedOutputDirectory:
    def __init__(self, path, fd):
        self.path = os.path.abspath(os.fspath(path))
        self.fd = fd
        self._opened = os.fstat(fd)

    def __fspath__(self):
        return self.path

    def verify(self):
        try:
            current = os.stat(self.path, follow_symlinks=False)
        except (OSError, TypeError, NotImplementedError) as error:
            raise ValueError(
                "fetch output directory changed during publication"
            ) from error
        if (
            not stat.S_ISDIR(self._opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or self._opened.st_dev != current.st_dev
            or self._opened.st_ino != current.st_ino
        ):
            raise ValueError(
                "fetch output directory changed during publication"
            )


class _DownloadStagingFile:
    def __init__(self, output, name, fd, logical_name=None):
        self.output = output
        self.name = name
        self.fd = fd
        self.logical_name = logical_name
        self._cleanup_attempted = False
        self._cleanup_result = None

    @property
    def display_path(self):
        return os.path.join(self.output.path, self.name)

    def __fspath__(self):
        if self.logical_name is not None:
            return os.path.join(self.output.path, self.logical_name)
        if os.path.isdir("/dev/fd"):
            return f"/dev/fd/{self.fd}"
        return f"/proc/self/fd/{self.fd}"


class _PrivateZipSnapshot:
    def __init__(self, stream, staging):
        self._stream = stream
        self.staging = staging

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _output_path(output):
    if isinstance(output, _PinnedOutputDirectory):
        return output.path
    return os.fspath(output)


@contextmanager
def _pinned_output_directory(path):
    absolute = os.path.abspath(os.fspath(path))
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ValueError("secure fetch output publication is unavailable")
    try:
        existing = os.stat(absolute, follow_symlinks=False)
    except FileNotFoundError:
        os.makedirs(absolute, exist_ok=False)
    except (OSError, TypeError, NotImplementedError) as error:
        raise ValueError(
            "fetch output directory is not a stable no-follow directory"
        ) from error
    else:
        if not stat.S_ISDIR(existing.st_mode):
            raise ValueError(
                "fetch output directory is not a stable no-follow directory"
            )
    try:
        fd = os.open(
            absolute,
            os.O_RDONLY | directory | nofollow,
        )
    except (OSError, TypeError, NotImplementedError) as error:
        raise ValueError(
            "fetch output directory is not a stable no-follow directory"
        ) from error
    try:
        output = _PinnedOutputDirectory(absolute, fd)
        output.verify()
        yield output
    finally:
        os.close(fd)


def _verify_staging_file(staging):
    opened = os.fstat(staging.fd)
    try:
        current = os.stat(
            staging.name,
            dir_fd=staging.output.fd,
            follow_symlinks=False,
        )
    except (OSError, TypeError, NotImplementedError) as error:
        raise _UnstableRegularFileError(
            "download staging entry is unavailable"
        ) from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or (opened.st_dev, opened.st_ino)
        != (current.st_dev, current.st_ino)
    ):
        raise _UnstableRegularFileError(
            "download staging entry is not a stable regular file"
        )


def _unlink_owned_regular_entry(
    directory_fd,
    name,
    descriptor,
    *,
    expected=None,
):
    try:
        opened = os.fstat(descriptor)
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    except (
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ) as error:
        raise _UnstableRegularFileError(
            "owned entry cleanup verification is unavailable"
        ) from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino)
        != (opened.st_dev, opened.st_ino)
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
        or (
            expected is not None
            and (
                (opened.st_dev, opened.st_ino) != expected.identity
                or opened.st_size != expected.size
                or (
                    expected.mtime_ns is not None
                    and opened.st_mtime_ns != expected.mtime_ns
                )
                or (
                    expected.ctime_ns is not None
                    and opened.st_ctime_ns != expected.ctime_ns
                )
            )
        )
    ):
        raise _UnstableRegularFileError(
            "owned entry changed before cleanup"
        )
    _identity_bound_remove(
        directory_fd,
        name,
        descriptor,
        is_directory=False,
    )
    return True


def _open_owned_regular_entry(
    directory_fd,
    name,
    *,
    expected=None,
    error_message="owned entry changed",
):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _UnstableRegularFileError(error_message)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        raise
    except (
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ) as error:
        raise _UnstableRegularFileError(error_message) from error
    try:
        opened = os.fstat(descriptor)
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
            or opened.st_size != current.st_size
            or opened.st_mtime_ns != current.st_mtime_ns
            or opened.st_ctime_ns != current.st_ctime_ns
            or (
                expected is not None
                and (
                    (opened.st_dev, opened.st_ino) != expected.identity
                    or opened.st_size != expected.size
                    or (
                        expected.mtime_ns is not None
                        and opened.st_mtime_ns != expected.mtime_ns
                    )
                    or (
                        expected.ctime_ns is not None
                        and opened.st_ctime_ns != expected.ctime_ns
                    )
                )
            )
        ):
            raise _UnstableRegularFileError(error_message)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _download_staging_file(
    output,
    *,
    prefix,
    suffix,
    logical_name=None,
):
    output.verify()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _UnstableRegularFileError(
            "no-follow file creation is unavailable"
        )
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow
    for _attempt in range(128):
        name = f"{prefix}{secrets.token_hex(8)}{suffix}"
        try:
            with _transaction_state_allocation():
                fd = os.open(
                    name,
                    flags,
                    0o600,
                    dir_fd=output.fd,
                )
        except FileExistsError:
            continue
        staging = _DownloadStagingFile(
            output,
            name,
            fd,
            logical_name=logical_name,
        )
        try:
            _verify_staging_file(staging)
            output.verify()
            return staging
        except BaseException as operation_error:
            cleanup_error = None
            try:
                _unlink_owned_regular_entry(
                    output.fd,
                    name,
                    fd,
                )
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                cleanup_error = _UnstableRegularFileError(
                    "download staging cleanup incomplete"
                )
                cleanup_error.__cause__ = error
                cleanup_error.__suppress_context__ = True
            try:
                os.close(fd)
            except OSError as error:
                if cleanup_error is None:
                    cleanup_error = _UnstableRegularFileError(
                        "download staging cleanup incomplete"
                    )
                    cleanup_error.__cause__ = error
                    cleanup_error.__suppress_context__ = True
            if cleanup_error is not None:
                _raise_transient_cleanup_error(
                    "<download staging>",
                    cleanup_error,
                    operation_error,
                )
            raise
    raise FileExistsError("could not allocate fetch download staging file")


def _cleanup_download_staging(staging):
    if staging is None:
        return None
    if staging._cleanup_attempted:
        return staging._cleanup_result
    staging._cleanup_attempted = True
    failures = []
    descriptor = staging.fd
    staging.fd = -1
    try:
        _unlink_owned_regular_entry(
            staging.output.fd,
            staging.name,
            descriptor,
        )
    except (
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ):
        failures.append("deletion failed")
    try:
        os.close(descriptor)
    except OSError:
        failures.append("descriptor close failed")
    if failures:
        staging._cleanup_result = (
            "download staging cleanup incomplete: "
            + ", ".join(failures)
        )
    return staging._cleanup_result


@contextmanager
def _open_download_staging(staging):
    try:
        staging.output.verify()
        _verify_staging_file(staging)
    except ValueError as error:
        raise _UnstableRegularFileError(str(error)) from error
    with os.fdopen(os.dup(staging.fd), "rb") as stream:
        stream.seek(0)
        yield stream
        _verify_staging_file(staging)
        try:
            staging.output.verify()
        except ValueError as error:
            raise _UnstableRegularFileError(str(error)) from error


def _hash_exact_fd(fd, size):
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = size
    while remaining:
        chunk = os.read(fd, min(_FILE_COPY_CHUNK_BYTES, remaining))
        if not chunk:
            raise _UnstableRegularFileError(
                "regular file changed during bounded read"
            )
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise _UnstableRegularFileError(
            "regular file changed during bounded read"
        )
    return digest.hexdigest()


def _read_verified_download_staging(staging, *, max_bytes):
    try:
        staging.output.verify()
        _verify_staging_file(staging)
    except ValueError as error:
        raise _UnstableRegularFileError(str(error)) from error
    initial = os.fstat(staging.fd)
    if not stat.S_ISREG(initial.st_mode):
        raise _UnstableRegularFileError(
            "download staging entry is not a stable regular file"
        )
    if initial.st_size > max_bytes:
        raise ValueError(
            "downloaded file exceeds max_bytes after staging verification "
            f"({max_bytes})"
        )
    with _open_download_staging(staging) as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(
            "downloaded file exceeds max_bytes after staging verification "
            f"({max_bytes})"
        )
    if len(data) != initial.st_size:
        raise _UnstableRegularFileError(
            "downloaded file size changed during bounded staging read"
        )
    expected_sha256 = hashlib.sha256(data).hexdigest()
    final = os.fstat(staging.fd)
    if (
        not stat.S_ISREG(final.st_mode)
        or final.st_size != initial.st_size
        or (final.st_dev, final.st_ino)
        != (initial.st_dev, initial.st_ino)
        or final.st_mtime_ns != initial.st_mtime_ns
        or final.st_ctime_ns != initial.st_ctime_ns
        or _hash_exact_fd(staging.fd, initial.st_size)
        != expected_sha256
    ):
        raise _UnstableRegularFileError(
            "downloaded file content changed during bounded staging read"
        )
    _verify_staging_file(staging)
    try:
        staging.output.verify()
    except ValueError as error:
        raise _UnstableRegularFileError(str(error)) from error
    return data


class _BoundedUncompressedReader:
    def __init__(
        self,
        source,
        *,
        max_bytes,
        max_members,
    ):
        self._source = source
        self._max_bytes = max(0, int(max_bytes))
        self._max_members = max(0, int(max_members))
        self._used_bytes = 0
        self._raw_members = 0
        self._scan_buffer = bytearray()
        self._payload_padding_remaining = 0
        self._archive_ended = False

    def readable(self):
        return True

    def read(self, size=-1):
        if size == 0:
            return b""
        remaining = self._max_bytes - self._used_bytes
        requested = remaining + 1 if size is None or size < 0 else size
        requested = min(max(1, requested), remaining + 1)
        if not self._archive_ended:
            parser_boundary = (
                self._payload_padding_remaining
                or tarfile.BLOCKSIZE - len(self._scan_buffer)
            )
            requested = min(requested, max(1, parser_boundary))
        data = self._source.read(requested)
        self._used_bytes += len(data)
        if self._used_bytes > self._max_bytes:
            raise ValueError(
                "decompressed TAR byte ceiling exceeded "
                f"({self._max_bytes})"
            )
        self._scan_raw_tar(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def _scan_raw_tar(self, data):
        if self._archive_ended or not data:
            return
        self._scan_buffer.extend(data)
        block_size = tarfile.BLOCKSIZE
        while True:
            if self._payload_padding_remaining:
                consumed = min(
                    len(self._scan_buffer),
                    self._payload_padding_remaining,
                )
                del self._scan_buffer[:consumed]
                self._payload_padding_remaining -= consumed
                if (
                    self._payload_padding_remaining
                    or not self._scan_buffer
                ):
                    return
            if len(self._scan_buffer) < block_size:
                return
            header = bytes(self._scan_buffer[:block_size])
            del self._scan_buffer[:block_size]
            if header == tarfile.NUL * block_size:
                self._archive_ended = True
                self._scan_buffer.clear()
                return
            self._raw_members += 1
            if self._raw_members > self._max_members:
                raise ValueError(
                    "raw TAR member count exceeds traversal ceiling "
                    f"({self._max_members})"
                )
            raw_info = tarfile.TarInfo.frombuf(
                header,
                tarfile.ENCODING,
                "surrogateescape",
            )
            self._payload_padding_remaining = (
                (raw_info.size + block_size - 1) // block_size
            ) * block_size


@contextmanager
def _private_zip_snapshot(
    source,
    *,
    max_bytes,
    output,
    cleanup_warnings,
):
    if not isinstance(output, _PinnedOutputDirectory):
        raise _UnstableRegularFileError(
            "private ZIP snapshot requires a pinned output directory"
        )
    staging = None
    snapshot_stream = None
    try:
        try:
            staging = _download_staging_file(
                output,
                prefix=".paperconan-zip-snapshot-",
                suffix=".zip",
            )
            source.seek(0, os.SEEK_SET)
            total = 0
            while True:
                chunk = source.read(_FILE_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        "private ZIP snapshot exceeds archive limit "
                        f"({max_bytes})"
                    )
                pending = memoryview(chunk)
                while pending:
                    written = os.write(staging.fd, pending)
                    if written <= 0:
                        raise OSError(
                            "private ZIP snapshot write failed"
                        )
                    pending = pending[written:]
            os.fsync(staging.fd)
            written_state = os.fstat(staging.fd)
            if (
                not stat.S_ISREG(written_state.st_mode)
                or written_state.st_size != total
            ):
                raise _UnstableRegularFileError(
                    "private ZIP snapshot is not a stable regular file"
                )
            stable = _stable_staging_state(staging)
            reader_fd = _open_owned_regular_entry(
                output.fd,
                staging.name,
                expected=stable,
                error_message=(
                    "private ZIP snapshot changed before read-only reopen"
                ),
            )
            writer_fd = staging.fd
            staging.fd = reader_fd
            os.close(writer_fd)
            snapshot_stream = os.fdopen(
                os.dup(staging.fd),
                "rb",
            )
        except (OSError, ValueError) as error:
            detail = str(error)
            if staging is not None:
                detail = detail.replace(
                    staging.display_path,
                    "<private ZIP snapshot>",
                ).replace(
                    staging.name,
                    "<private ZIP snapshot>",
                )
                cleanup_warning = _cleanup_download_staging(
                    staging
                )
                if cleanup_warning is not None:
                    detail = (
                        f"{detail}; private ZIP snapshot cleanup pending"
                    )
            raise _UnstableRegularFileError(
                f"private ZIP snapshot unavailable: {detail}"
            ) from error
        with snapshot_stream:
            yield _PrivateZipSnapshot(snapshot_stream, staging)
    finally:
        if staging is not None:
            cleanup_warning = _cleanup_download_staging(staging)
            if (
                cleanup_warning is not None
                and "private ZIP snapshot cleanup pending"
                not in cleanup_warnings
            ):
                cleanup_warnings.append(
                    "private ZIP snapshot cleanup pending"
                )


def _stable_managed_file(output, name, expected=None):
    output.verify()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _UnstableRegularFileError(
            "no-follow file opening is unavailable"
        )
    try:
        fd = os.open(
            name,
            os.O_RDONLY | nofollow,
            dir_fd=output.fd,
        )
    except (OSError, TypeError, NotImplementedError) as error:
        raise _UnstableRegularFileError(
            "managed output entry is unavailable"
        ) from error
    try:
        opened = os.fstat(fd)
        try:
            current = os.stat(
                name,
                dir_fd=output.fd,
                follow_symlinks=False,
            )
        except (OSError, TypeError, NotImplementedError) as error:
            raise _UnstableRegularFileError(
                "managed output entry is unavailable"
            ) from error
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise _UnstableRegularFileError(
                "managed output entry is not a stable regular file"
            )
        if expected is not None and opened.st_size != expected["size"]:
            raise _UnstableRegularFileError(
                "managed output fingerprint does not match"
            )
        digest = _hash_exact_fd(fd, opened.st_size)
        final_opened = os.fstat(fd)
        try:
            final_current = os.stat(
                name,
                dir_fd=output.fd,
                follow_symlinks=False,
            )
        except (OSError, TypeError, NotImplementedError) as error:
            raise _UnstableRegularFileError(
                "managed output entry is unavailable"
            ) from error
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
            or final_current.st_size != opened.st_size
            or final_current.st_mtime_ns != opened.st_mtime_ns
            or final_current.st_ctime_ns != opened.st_ctime_ns
        ):
            raise _UnstableRegularFileError(
                "managed output entry changed during verification"
            )
        if expected is not None and digest != expected["sha256"]:
            raise _UnstableRegularFileError(
                "managed output fingerprint does not match"
            )
        output.verify()
        return _ManagedFileState(
            name=name,
            size=opened.st_size,
            sha256=digest,
            identity=(opened.st_dev, opened.st_ino),
            mtime_ns=opened.st_mtime_ns,
            ctime_ns=opened.st_ctime_ns,
        )
    finally:
        os.close(fd)


def _stable_staging_state(staging):
    staging.output.verify()
    _verify_staging_file(staging)
    opened = os.fstat(staging.fd)
    if not stat.S_ISREG(opened.st_mode):
        raise _UnstableRegularFileError(
            "download staging entry is not a regular file"
        )
    digest = _hash_exact_fd(staging.fd, opened.st_size)
    final = os.fstat(staging.fd)
    current = os.stat(
        staging.name,
        dir_fd=staging.output.fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(final.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or final.st_size != opened.st_size
        or final.st_mtime_ns != opened.st_mtime_ns
        or final.st_ctime_ns != opened.st_ctime_ns
        or (final.st_dev, final.st_ino)
        != (opened.st_dev, opened.st_ino)
        or (current.st_dev, current.st_ino)
        != (opened.st_dev, opened.st_ino)
    ):
        raise _UnstableRegularFileError(
            "download staging entry changed during verification"
        )
    staging.output.verify()
    return _ManagedFileState(
        name=staging.name,
        size=opened.st_size,
        sha256=digest,
        identity=(opened.st_dev, opened.st_ino),
    )


class _TransientCleanupError(RuntimeError):
    def __init__(
        self,
        transient_path,
        cleanup_error,
        operation_error,
    ):
        self.transient_path = os.fspath(transient_path)
        self.cleanup_error = cleanup_error
        self.operation_error = operation_error
        self.rollback_errors = ()
        super().__init__("transient file cleanup failed")


def _dir_size_fd(directory_fd, excluded_names):
    total = 0
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for name in os.listdir(directory_fd):
        if name in excluded_names:
            continue
        try:
            current = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if stat.S_ISREG(current.st_mode):
                total += current.st_size
            elif stat.S_ISDIR(current.st_mode):
                child_fd = os.open(
                    name,
                    flags,
                    dir_fd=directory_fd,
                )
                try:
                    total += _dir_size_fd(child_fd, set())
                finally:
                    os.close(child_fd)
        except (OSError, TypeError, NotImplementedError):
            pass
    return total


def _excluded_entry_matches(current, identity):
    if len(identity) == 2:
        return identity == (current.st_dev, current.st_ino)
    return identity == (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
    )


def _dir_size_fd_excluding_root_entries(
    directory_fd,
    excluded_entries,
):
    total = 0
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for name in os.listdir(directory_fd):
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if stat.S_ISREG(current.st_mode):
            identity = excluded_entries.get(name)
            if (
                identity is not None
                and _excluded_entry_matches(current, identity)
            ):
                continue
            total += current.st_size
        elif stat.S_ISDIR(current.st_mode):
            child_fd = os.open(
                name,
                flags,
                dir_fd=directory_fd,
            )
            try:
                total += _dir_size_fd_excluding_root_entries(
                    child_fd, {}
                )
            finally:
                os.close(child_fd)
    return total


def _verified_source_sidecar_identity(output):
    fd = -1
    try:
        output.verify()
        current = os.stat(
            SOURCE_SIDECAR,
            dir_fd=output.fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(current.st_mode):
            return None
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            return None
        fd = os.open(
            SOURCE_SIDECAR,
            os.O_RDONLY | nofollow,
            dir_fd=output.fd,
        )
        opened = os.fstat(fd)
        final_current = os.stat(
            SOURCE_SIDECAR,
            dir_fd=output.fd,
            follow_symlinks=False,
        )
        final_opened = os.fstat(fd)
        output.verify()
    except (
        FileNotFoundError,
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ):
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(final_opened.st_mode)
        or not stat.S_ISREG(final_current.st_mode)
        or (opened.st_dev, opened.st_ino)
        != (current.st_dev, current.st_ino)
        or (final_opened.st_dev, final_opened.st_ino)
        != (current.st_dev, current.st_ino)
        or (final_current.st_dev, final_current.st_ino)
        != (current.st_dev, current.st_ino)
        or final_opened.st_size != opened.st_size
        or final_opened.st_mtime_ns != opened.st_mtime_ns
        or final_opened.st_ctime_ns != opened.st_ctime_ns
        or final_current.st_size != opened.st_size
        or final_current.st_mtime_ns != opened.st_mtime_ns
        or final_current.st_ctime_ns != opened.st_ctime_ns
    ):
        return None
    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )


def _paper_data_size(output, transient_files=()):
    excluded_entries = {}
    sidecar_identity = _verified_source_sidecar_identity(output)
    if sidecar_identity is not None:
        excluded_entries[SOURCE_SIDECAR] = sidecar_identity
    for staging in transient_files:
        if not isinstance(staging, _DownloadStagingFile):
            continue
        if staging.output.fd != output.fd:
            raise _UnstableRegularFileError(
                "download staging belongs to a different output directory"
            )
        _verify_staging_file(staging)
        current = os.fstat(staging.fd)
        excluded_entries[staging.name] = (
            current.st_dev,
            current.st_ino,
        )
    return _dir_size_fd_excluding_root_entries(
        output.fd,
        excluded_entries,
    )


def _dir_size(path, exclude_paths=()):
    if isinstance(path, _PinnedOutputDirectory):
        excluded_names = {
            item.name
            for item in exclude_paths
            if (
                isinstance(item, _DownloadStagingFile)
                and item.output.fd == path.fd
            )
        }
        return _dir_size_fd(path.fd, excluded_names)
    excluded = {
        os.path.abspath(os.fspath(excluded_path))
        for excluded_path in exclude_paths
    }
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            file_path = os.path.join(dp, f)
            if os.path.abspath(file_path) in excluded:
                continue
            try:
                total += os.path.getsize(file_path)
            except OSError:
                pass
    return total


def _copy_limited(src, dest, max_bytes):
    total = 0
    while True:
        chunk = src.read(65536)
        if not chunk:
            return total
        total += len(chunk)
        if total > max_bytes:
            raise _SizeLimitExceeded(f"file exceeds max_bytes ({max_bytes})")
        dest.write(chunk)


def _remove_transient_file(path):
    last_error = None
    for _attempt in range(_TRANSIENT_CLEANUP_ATTEMPTS):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
    raise last_error


def _raise_transient_cleanup_error(
    transient_path,
    cleanup_error,
    operation_error,
):
    cleanup_error.__cause__ = operation_error
    cleanup_error.__suppress_context__ = True
    error = _TransientCleanupError(
        transient_path,
        cleanup_error,
        operation_error,
    )
    raise error from cleanup_error


def _atomic_stream_write(src, dest_path, max_bytes):
    if isinstance(dest_path, _DownloadStagingFile):
        staging = dest_path
        staging.output.verify()
        _verify_staging_file(staging)
        os.ftruncate(staging.fd, 0)
        os.lseek(staging.fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(staging.fd), "wb") as dest:
            size = _copy_limited(src, dest, max_bytes)
            dest.flush()
            os.fsync(dest.fileno())
        _verify_staging_file(staging)
        current = os.fstat(staging.fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_size != size
        ):
            raise _UnstableRegularFileError(
                "download staging entry changed during write"
            )
        staging.output.verify()
        return size
    directory = os.path.dirname(os.path.abspath(dest_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(dest_path)}.",
        suffix=".part",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "wb") as dest:
            size = _copy_limited(src, dest, max_bytes)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(temp_path, dest_path)
        return size
    except BaseException as operation_error:
        try:
            _remove_transient_file(temp_path)
        except OSError as cleanup_error:
            _raise_transient_cleanup_error(
                temp_path,
                cleanup_error,
                operation_error,
            )
        raise


def _existing_destination_size(dest_path, reuses_old):
    if not reuses_old:
        return 0
    try:
        return os.path.getsize(dest_path)
    except OSError:
        return 0


def _remaining_final_size_allowance(
    current_size,
    dest_path,
    reuses_old,
    sidecar_delta=0,
):
    replacement_credit = _existing_destination_size(
        dest_path, reuses_old
    )
    return (
        (
            _MAX_PAPER_BYTES
            - current_size
            + replacement_credit
            - sidecar_delta
        ),
        replacement_credit,
    )


class _ManagedOutputRollbackError(RuntimeError):
    def __init__(self, failures, cleanup_failure=None):
        self.failures = tuple(failures)
        self.cleanup_failure = cleanup_failure
        messages = []
        if self.failures:
            count = len(self.failures)
            noun = "output" if count == 1 else "outputs"
            details = "; ".join(
                f"{dest_path}: {type(error).__name__}: {error}"
                for dest_path, error in self.failures
            )
            messages.append(
                f"could not restore {count} managed {noun}: {details}"
            )
        if cleanup_failure is not None:
            backup_dir, error = cleanup_failure
            if backup_dir is _PENDING_CLEANUP_WITHOUT_PATH:
                messages.append(
                    "managed-output cleanup remains pending: "
                    f"{type(error).__name__}: {error}"
                )
            else:
                messages.append(
                    "could not remove managed-output rollback directory "
                    f"{backup_dir}: {type(error).__name__}: {error}"
                )
        super().__init__("; ".join(messages))


class _ManagedOutputDirectoryCleanupError(OSError):
    def __init__(self, backup_dir, error):
        self.backup_dir = backup_dir
        self.error = error
        super().__init__(str(error))


class _ManagedOutputRestoreFailure(RuntimeError):
    def __init__(self, operation_error, rollback_error):
        self.operation_error = operation_error
        self.rollback_error = rollback_error
        super().__init__(str(rollback_error))


def _append_explicit_cause(error, cause):
    seen = set()
    tail = error
    while tail is not None and id(tail) not in seen:
        seen.add(id(tail))
        if tail.__cause__ is None:
            cause_cursor = cause
            while (
                cause_cursor is not None
                and id(cause_cursor) not in seen
            ):
                seen.add(id(cause_cursor))
                cause_cursor = cause_cursor.__cause__
            if cause_cursor is not None:
                return
            tail.__cause__ = cause
            tail.__suppress_context__ = True
            return
        tail = tail.__cause__


def _raise_operation_with_rollback_errors(
    operation_error,
    rollback_errors,
):
    rollback_errors = tuple(rollback_errors)
    if isinstance(operation_error, _TransientCleanupError):
        for rollback_error in rollback_errors:
            _append_explicit_cause(
                operation_error,
                rollback_error,
            )
        operation_error.rollback_errors += rollback_errors
        raise operation_error from operation_error.__cause__
    raise operation_error from rollback_errors[-1]


class _ManagedOutputJournal:
    def __init__(
        self,
        out_dir,
        *,
        internal_names=(),
        backup_prefix=".paperconan-output-rollback-",
        backup_entry_prefix="",
    ):
        if not isinstance(out_dir, _PinnedOutputDirectory):
            raise _UnstableRegularFileError(
                "managed-output journal requires a pinned output directory"
            )
        self._output = out_dir
        self._out_dir = os.path.abspath(_output_path(out_dir))
        self._parent = os.path.dirname(self._out_dir)
        self._backup_dir = None
        self._backup_parent_fd = -1
        self._backup_fd = -1
        self._backup_parent_identity = None
        self._backup_identity = None
        self._backup_name = None
        self._entries = {}
        self._detached_backup_paths = {}
        self._state = _ManagedOutputJournalState.OPEN
        self._recovery_error = None
        self._rollback_error = None
        self._verify_empty_root_on_commit = False
        self._commit_cleanup_descriptor = -1
        self._commit_cleanup_expected = None
        self._move_descriptor = -1
        self._move_expected = None
        self._internal_names = frozenset(internal_names)
        self._backup_prefix = str(backup_prefix)
        self._backup_entry_prefix = str(backup_entry_prefix)

    def _require_open(self):
        if self._state is not _ManagedOutputJournalState.OPEN:
            raise RuntimeError(
                "managed-output journal is not open "
                f"({self._state.value})"
            )

    def recovery_paths(self):
        paths = []
        if self._output is None:
            return ()
        for entry in self._entries.values():
            backup_state = entry["backup_state"]
            if backup_state is None:
                continue
            if entry.get("restored"):
                try:
                    canonical = _stable_managed_file(
                        self._output,
                        entry["name"],
                        expected={
                            "size": backup_state.size,
                            "sha256": backup_state.sha256,
                        },
                    )
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ):
                    pass
                else:
                    if (
                        canonical.identity == backup_state.identity
                        and self._output_lexical_path_matches()
                    ):
                        paths.append(os.path.abspath(
                            os.path.join(
                                self._out_dir,
                                entry["name"],
                            )
                        ))
                        continue
            backup_name = entry["backup"]
            if backup_name is not None and self._backup_copy_matches(
                backup_name,
                backup_state,
            ) and self._backup_lexical_path_matches():
                paths.append(os.path.abspath(
                    self._backup_path(backup_name)
                ))
        for backup_path, backup_state in (
            self._detached_backup_paths.items()
        ):
            if (
                backup_state is not None
                and self._detached_backup_copy_matches(
                    backup_path,
                    backup_state,
                )
                and self._backup_lexical_path_matches()
            ):
                paths.append(backup_path)
        return tuple(dict.fromkeys(paths))

    def _output_lexical_path_matches(self):
        try:
            self._output.verify()
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return False
        return True

    def _backup_copy_matches(self, backup_name, expected):
        try:
            actual = self._stable_backup_entry(
                backup_name,
                identity=expected.identity,
                size=expected.size,
                verify_output=False,
            )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return False
        return actual.sha256 == expected.sha256

    def _detached_backup_copy_matches(
        self,
        backup_path,
        expected,
    ):
        return (
            self._detached_backup_copy_status(
                backup_path,
                expected,
            )
            == "owned"
        )

    def _detached_backup_copy_status(
        self,
        backup_path,
        expected,
    ):
        if (
            self._backup_dir is None
            or os.path.dirname(backup_path) != self._backup_dir
        ):
            return "unavailable"
        backup_name = os.path.basename(backup_path)
        try:
            self._verify_backup_storage()
            current = os.stat(
                backup_name,
                dir_fd=self._backup_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return "missing"
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return "unavailable"
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != expected.identity
            or current.st_size != expected.size
        ):
            return "replaced"
        try:
            actual = self._stable_backup_entry(
                backup_name,
                identity=expected.identity,
                size=expected.size,
                verify_output=False,
            )
        except FileNotFoundError:
            return "missing"
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return "unavailable"
        if actual.sha256 == expected.sha256:
            return "owned"
        return "replaced"

    def _detached_backup_copy_is_absent(self, backup_path):
        expected = self._detached_backup_paths.get(backup_path)
        if expected is None:
            return False
        return (
            self._detached_backup_copy_status(
                backup_path,
                expected,
            )
            == "missing"
        )

    def _backup_directory_matches(self):
        try:
            self._verify_backup_storage()
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return False
        return self._backup_lexical_path_matches()

    def _enter_recovery_required(self, error):
        if not isinstance(
            error,
            _ManagedOutputRecoveryRequiredError,
        ):
            raise TypeError(
                "managed-output recovery state requires a recovery error"
            )
        if not error.recovery_paths:
            error.recovery_paths = self.recovery_paths()
        if self._state is not _ManagedOutputJournalState.RECOVERY_REQUIRED:
            self._recovery_error = error
            self._rollback_error = None
        self._state = _ManagedOutputJournalState.RECOVERY_REQUIRED
        return self._recovery_error

    def _raise_recovery_required(self, message, cause=None):
        error = _ManagedOutputRecoveryRequiredError(
            message,
            recovery_paths=self.recovery_paths(),
        )
        error = self._enter_recovery_required(error)
        if cause is None:
            raise error
        raise error from cause

    def _raise_pathless_recovery_required(
        self,
        message,
        cause=None,
    ):
        error = _ManagedOutputRecoveryRequiredError(
            message,
            recovery_paths=(),
        )
        if self._state is not _ManagedOutputJournalState.RECOVERY_REQUIRED:
            self._recovery_error = error
            self._rollback_error = None
        self._state = _ManagedOutputJournalState.RECOVERY_REQUIRED
        if cause is None:
            raise self._recovery_error
        raise self._recovery_error from cause

    def _recovery_rollback_failure(self):
        if self._rollback_error is None:
            recovery_error = self._recovery_error
            if recovery_error is None:
                recovery_error = _ManagedOutputRecoveryRequiredError(
                    "managed-output recovery is required",
                    recovery_paths=self.recovery_paths(),
                )
                self._recovery_error = recovery_error
            failures = [
                (dest_path, recovery_error)
                for dest_path in self._entries
            ]
            if not failures:
                failures = [(self._out_dir, recovery_error)]
            self._rollback_error = _ManagedOutputRollbackError(
                failures
            )
        return self._rollback_error

    def _pinned_name(self, dest_path):
        dest_path = os.path.abspath(os.fspath(dest_path))
        try:
            relative = os.path.relpath(dest_path, self._out_dir)
        except ValueError:
            return None
        if (
            relative not in self._internal_names
            and _safe_managed_name(relative) is None
        ):
            return None
        return relative

    def _ensure_backup_dir(self):
        if self._backup_dir is not None:
            self._verify_backup_dir()
            return self._backup_dir
        _require_identity_bound_mutation()
        self._output.verify()
        directory = getattr(os, "O_DIRECTORY", None)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if directory is None or nofollow is None:
            raise _UnstableRegularFileError(
                "secure managed-output rollback is unavailable"
            )
        flags = os.O_RDONLY | directory | nofollow
        parent_fd = os.open("..", flags, dir_fd=self._output.fd)
        backup_fd = -1
        backup_name = None
        try:
            parent = os.fstat(parent_fd)
            if not stat.S_ISDIR(parent.st_mode):
                raise _UnstableRegularFileError(
                    "managed-output rollback parent is not a directory"
                )
            backup_name = _rollback_directory_name(
                self._output,
                self._backup_prefix,
            )
            try:
                with _transaction_state_allocation():
                    os.mkdir(
                        backup_name,
                        0o700,
                        dir_fd=parent_fd,
                    )
            except FileExistsError as error:
                raise _UnstableRegularFileError(
                    "managed-output recovery state is already pending"
                ) from error
            backup_fd = os.open(
                backup_name,
                flags,
                dir_fd=parent_fd,
            )
            opened = os.fstat(backup_fd)
            visible = os.stat(
                backup_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(visible.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (visible.st_dev, visible.st_ino)
            ):
                raise _UnstableRegularFileError(
                    "managed-output rollback directory is unstable"
                )
            self._backup_parent_fd = parent_fd
            self._backup_fd = backup_fd
            self._backup_parent_identity = (
                parent.st_dev,
                parent.st_ino,
            )
            self._backup_identity = (
                opened.st_dev,
                opened.st_ino,
            )
            self._backup_name = backup_name
            self._backup_dir = os.path.join(
                self._parent,
                backup_name,
            )
            parent_fd = -1
            backup_fd = -1
        except BaseException:
            if backup_fd >= 0:
                try:
                    _identity_bound_remove(
                        parent_fd,
                        backup_name,
                        backup_fd,
                        is_directory=True,
                    )
                except OSError:
                    pass
                os.close(backup_fd)
            if parent_fd >= 0:
                os.close(parent_fd)
            raise
        return self._backup_dir

    def _verify_backup_dir(self):
        try:
            self._output.verify()
            self._verify_backup_storage()
            self._output.verify()
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "managed-output rollback directory changed"
            ) from error

    def _verify_backup_storage(self):
        if (
            self._backup_dir is None
            or self._backup_parent_fd < 0
            or self._backup_fd < 0
            or self._backup_name is None
        ):
            raise _UnstableRegularFileError(
                "managed-output rollback directory is unavailable"
            )
        try:
            parent = os.fstat(self._backup_parent_fd)
            opened = os.fstat(self._backup_fd)
            visible = os.stat(
                self._backup_name,
                dir_fd=self._backup_parent_fd,
                follow_symlinks=False,
            )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "managed-output rollback directory changed"
            ) from error
        if (
            not stat.S_ISDIR(parent.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (parent.st_dev, parent.st_ino)
            != self._backup_parent_identity
            or (opened.st_dev, opened.st_ino)
            != self._backup_identity
            or (visible.st_dev, visible.st_ino)
            != self._backup_identity
        ):
            raise _UnstableRegularFileError(
                "managed-output rollback directory changed"
            )

    def _backup_lexical_path_matches(self):
        if self._backup_dir is None or self._backup_fd < 0:
            return False
        try:
            opened = os.fstat(self._backup_fd)
            visible = os.stat(
                self._backup_dir,
                follow_symlinks=False,
            )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ):
            return False
        return (
            stat.S_ISDIR(opened.st_mode)
            and stat.S_ISDIR(visible.st_mode)
            and (opened.st_dev, opened.st_ino)
            == self._backup_identity
            and (visible.st_dev, visible.st_ino)
            == self._backup_identity
        )

    def _backup_path(self, backup_name):
        if backup_name is None:
            return None
        return os.path.join(self._backup_dir, backup_name)

    def _pending_backup_cleanup(self, path):
        path = os.path.abspath(os.fspath(path))
        if self._backup_lexical_path_matches():
            return path
        return _PENDING_CLEANUP_WITHOUT_PATH

    def _move_to_backup(self, output_name, backup_name):
        self._verify_backup_dir()
        descriptor = self._move_descriptor
        expected = self._move_expected
        if descriptor < 0 or expected is None:
            raise RuntimeError("managed-output move descriptor is unavailable")
        linked = False
        try:
            with _transaction_state_allocation():
                os.link(
                    output_name,
                    backup_name,
                    src_dir_fd=self._output.fd,
                    dst_dir_fd=self._backup_fd,
                    follow_symlinks=False,
                )
            linked = True
            self._verify_backup_entry(backup_name, expected)
        except BaseException as operation_error:
            if linked:
                try:
                    self._remove_backup_entry(backup_name)
                except BaseException as cleanup_error:
                    backup_path = os.path.abspath(
                        self._backup_path(backup_name)
                    )
                    self._detached_backup_paths[backup_path] = None
                    self._raise_pathless_recovery_required(
                        "managed-output cleanup remains pending",
                        cleanup_error,
                    )
            raise operation_error
        operation_error = None
        for _attempt in range(2):
            try:
                _identity_bound_remove(
                    self._output.fd,
                    output_name,
                    descriptor,
                    is_directory=False,
                )
                operation_error = None
                break
            except _IdentityBoundMutationUnavailableError:
                raise
            except OSError as error:
                operation_error = error
        if operation_error is not None:
            try:
                self._remove_backup_entry(backup_name, expected)
            except BaseException as cleanup_error:
                backup_path = os.path.abspath(
                    self._backup_path(backup_name)
                )
                self._detached_backup_paths[backup_path] = expected
                self._raise_pathless_recovery_required(
                    "managed-output cleanup remains pending",
                    cleanup_error,
                )
            raise _UnstableRegularFileError(
                "managed output changed before backup move"
            ) from operation_error

    def _stable_backup_entry(
        self,
        backup_name,
        *,
        identity=None,
        size=None,
        max_size=None,
        verify_output=True,
    ):
        if verify_output:
            self._verify_backup_dir()
        else:
            self._verify_backup_storage()
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise _UnstableRegularFileError(
                "no-follow rollback entry opening is unavailable"
            )
        fd = os.open(
            backup_name,
            os.O_RDONLY | nofollow,
            dir_fd=self._backup_fd,
        )
        try:
            opened = os.fstat(fd)
            visible = os.stat(
                backup_name,
                dir_fd=self._backup_fd,
                follow_symlinks=False,
            )
            opened_identity = (opened.st_dev, opened.st_ino)
            visible_identity = (visible.st_dev, visible.st_ino)
            actual_size = opened.st_size
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(visible.st_mode)
                or opened_identity != visible_identity
                or (
                    identity is not None
                    and opened_identity != tuple(identity)
                )
                or (
                    size is not None
                    and actual_size != size
                )
                or visible.st_size != actual_size
                or (
                    max_size is not None
                    and actual_size > max_size
                )
            ):
                raise _UnstableRegularFileError(
                    "managed-output rollback entry changed"
                )
            digest = _hash_exact_fd(fd, actual_size)
            final_opened = os.fstat(fd)
            final_visible = os.stat(
                backup_name,
                dir_fd=self._backup_fd,
                follow_symlinks=False,
            )
            final_opened_identity = (
                final_opened.st_dev,
                final_opened.st_ino,
            )
            final_visible_identity = (
                final_visible.st_dev,
                final_visible.st_ino,
            )
            if (
                not stat.S_ISREG(final_opened.st_mode)
                or not stat.S_ISREG(final_visible.st_mode)
                or final_opened_identity != opened_identity
                or final_visible_identity != opened_identity
                or final_opened.st_size != actual_size
                or final_visible.st_size != actual_size
                or final_opened.st_mtime_ns != opened.st_mtime_ns
                or final_opened.st_ctime_ns != opened.st_ctime_ns
                or final_visible.st_mtime_ns != opened.st_mtime_ns
                or final_visible.st_ctime_ns != opened.st_ctime_ns
            ):
                raise _UnstableRegularFileError(
                    "managed-output rollback entry changed"
                )
            if verify_output:
                self._verify_backup_dir()
            else:
                self._verify_backup_storage()
            return _ManagedFileState(
                name=backup_name,
                size=actual_size,
                sha256=digest,
                identity=opened_identity,
            )
        finally:
            os.close(fd)

    def _verify_backup_entry(
        self,
        backup_name,
        expected,
        *,
        verify_output=True,
    ):
        try:
            actual = self._stable_backup_entry(
                backup_name,
                identity=expected.identity,
                size=expected.size,
                verify_output=verify_output,
            )
        except _UnstableRegularFileError:
            raise
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "managed-output rollback entry changed"
            ) from error
        if actual.sha256 != expected.sha256:
            raise _UnstableRegularFileError(
                "managed-output rollback entry changed"
            )

    def _open_verified_backup_entry(self, backup_name, expected):
        self._verify_backup_dir()
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise _UnstableRegularFileError(
                "no-follow rollback entry opening is unavailable"
            )
        try:
            fd = os.open(
                backup_name,
                os.O_RDONLY | nofollow,
                dir_fd=self._backup_fd,
            )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "managed-output rollback entry changed"
            ) from error
        try:
            opened = os.fstat(fd)
            visible = os.stat(
                backup_name,
                dir_fd=self._backup_fd,
                follow_symlinks=False,
            )
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(visible.st_mode)
                or (visible.st_dev, visible.st_ino) != identity
                or visible.st_size != opened.st_size
                or visible.st_mtime_ns != opened.st_mtime_ns
                or visible.st_ctime_ns != opened.st_ctime_ns
                or (
                    expected is not None
                    and (
                        identity != expected.identity
                        or opened.st_size != expected.size
                    )
                )
            ):
                raise _UnstableRegularFileError(
                    "managed-output rollback entry changed"
                )
            digest = _hash_exact_fd(fd, opened.st_size)
            final_opened = os.fstat(fd)
            final_visible = os.stat(
                backup_name,
                dir_fd=self._backup_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(final_opened.st_mode)
                or not stat.S_ISREG(final_visible.st_mode)
                or (final_opened.st_dev, final_opened.st_ino) != identity
                or (final_visible.st_dev, final_visible.st_ino) != identity
                or final_opened.st_size != opened.st_size
                or final_visible.st_size != opened.st_size
                or final_opened.st_mtime_ns != opened.st_mtime_ns
                or final_opened.st_ctime_ns != opened.st_ctime_ns
                or final_visible.st_mtime_ns != opened.st_mtime_ns
                or final_visible.st_ctime_ns != opened.st_ctime_ns
                or (
                    expected is not None
                    and digest != expected.sha256
                )
            ):
                raise _UnstableRegularFileError(
                    "managed-output rollback entry changed"
                )
            self._verify_backup_dir()
            return fd, _ManagedFileState(
                name=backup_name,
                size=final_opened.st_size,
                sha256=digest,
                identity=identity,
                mtime_ns=final_opened.st_mtime_ns,
                ctime_ns=final_opened.st_ctime_ns,
            )
        except _UnstableRegularFileError:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            try:
                os.close(fd)
            except OSError:
                pass
            raise _UnstableRegularFileError(
                "managed-output rollback entry changed"
            ) from error
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def _restore_from_backup(self, backup_name, output_name):
        with _transaction_state_allocation():
            os.link(
                backup_name,
                output_name,
                src_dir_fd=self._backup_fd,
                dst_dir_fd=self._output.fd,
                follow_symlinks=False,
            )

    def _unlink_created_after_final_canonical_check(
        self,
        output_name,
        expected,
    ):
        descriptor = _open_owned_regular_entry(
            self._output.fd,
            output_name,
            expected=expected,
            error_message="managed output changed before rollback",
        )
        try:
            _unlink_owned_regular_entry(
                self._output.fd,
                output_name,
                descriptor,
                expected=expected,
            )
        finally:
            os.close(descriptor)

    def _restore_backup_over_published_after_final_check(
        self,
        backup_name,
        output_name,
        expected,
    ):
        self._unlink_created_after_final_canonical_check(
            output_name,
            expected,
        )
        self._restore_from_backup(backup_name, output_name)

    def _restore_backup_into_absent_path_after_final_check(
        self,
        backup_name,
        output_name,
    ):
        try:
            self._restore_from_backup(backup_name, output_name)
        except FileExistsError as error:
            raise _UnstableRegularFileError(
                "managed output changed before rollback"
            ) from error

    def _restore_anchor_name(self, backup_name):
        return f".restore-{backup_name}"

    def _ensure_restore_anchor(self, backup_name, expected):
        anchor_name = self._restore_anchor_name(backup_name)
        self._verify_backup_dir()
        created = False
        try:
            with _transaction_state_allocation():
                os.link(
                    backup_name,
                    anchor_name,
                    src_dir_fd=self._backup_fd,
                    dst_dir_fd=self._backup_fd,
                    follow_symlinks=False,
                )
            created = True
        except FileExistsError:
            pass
        try:
            self._verify_backup_entry(anchor_name, expected)
        except BaseException:
            if created:
                try:
                    self._remove_backup_entry(anchor_name)
                except BaseException as cleanup_error:
                    anchor_path = os.path.abspath(
                        self._backup_path(anchor_name)
                    )
                    self._detached_backup_paths[anchor_path] = None
                    self._raise_pathless_recovery_required(
                        "managed-output cleanup remains pending",
                        cleanup_error,
                    )
            raise
        return anchor_name

    def _cleanup_restore_anchor(self, anchor_name, expected):
        anchor_path = os.path.abspath(
            self._backup_path(anchor_name)
        )
        try:
            self._remove_backup_entry(anchor_name, expected)
        except _IdentityBoundMutationUnavailableError as error:
            raise _ManagedOutputDirectoryCleanupError(
                _PENDING_CLEANUP_WITHOUT_PATH,
                error,
            ) from error
        except _UnstableRegularFileError as error:
            try:
                os.stat(
                    anchor_name,
                    dir_fd=self._backup_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                self._detached_backup_paths.pop(anchor_path, None)
                return
            raise _ManagedOutputDirectoryCleanupError(
                anchor_path,
                error,
            ) from error
        except OSError as error:
            raise _ManagedOutputDirectoryCleanupError(
                anchor_path,
                error,
            ) from error
        self._detached_backup_paths.pop(anchor_path, None)

    def _remove_backup_entry(
        self,
        backup_name,
        expected=None,
        *,
        commit_entry=None,
    ):
        if expected is not None:
            self._verify_backup_entry(backup_name, expected)
        descriptor, verified = self._open_verified_backup_entry(
            backup_name,
            expected,
        )
        try:
            if commit_entry is None:
                _identity_bound_remove(
                    self._backup_fd,
                    backup_name,
                    descriptor,
                    is_directory=False,
                )
            else:
                self._commit_cleanup_descriptor = descriptor
                self._commit_cleanup_expected = verified
                try:
                    self._unlink_backup_after_visible_commit_check(
                        backup_name,
                        commit_entry,
                    )
                finally:
                    self._commit_cleanup_descriptor = -1
                    self._commit_cleanup_expected = None
        finally:
            os.close(descriptor)

    def _unlink_backup_after_visible_commit_check(
        self,
        backup_name,
        entry,
    ):
        descriptor = self._commit_cleanup_descriptor
        expected = self._commit_cleanup_expected
        if descriptor < 0 or expected is None:
            raise RuntimeError(
                "commit cleanup descriptor is unavailable"
            )
        name = entry["name"]
        identity = entry["published_identity"]
        try:
            self._output.verify()
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "published managed output changed"
            ) from error
        if identity is None:
            try:
                os.stat(
                    name,
                    dir_fd=self._output.fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                raise _UnstableRegularFileError(
                    "staged managed-output removal could not be verified"
                ) from error
            else:
                raise _UnstableRegularFileError(
                    "staged managed-output removal reappeared"
                )
        else:
            try:
                current = os.stat(
                    name,
                    dir_fd=self._output.fd,
                    follow_symlinks=False,
                )
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                raise _UnstableRegularFileError(
                    "published managed output changed"
                ) from error
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != identity
                or current.st_size != entry["published_size"]
                or current.st_mtime_ns != entry["commit_mtime_ns"]
                or current.st_ctime_ns != entry["commit_ctime_ns"]
            ):
                raise _UnstableRegularFileError(
                    "published managed output changed"
                )
        _identity_bound_remove(
            self._backup_fd,
            backup_name,
            descriptor,
            is_directory=False,
        )

    def _close_backup_handles(self):
        backup_fd = self._backup_fd
        backup_parent_fd = self._backup_parent_fd
        self._backup_parent_fd = -1
        self._backup_fd = -1
        self._backup_parent_identity = None
        self._backup_identity = None
        self._backup_name = None
        for fd in (backup_fd, backup_parent_fd):
            if fd < 0:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self):
        self._close_backup_handles()

    def _remove_pinned_backup_dir(self):
        self._verify_backup_dir()
        backup_name = self._backup_name
        try:
            parent = os.fstat(self._backup_parent_fd)
            opened = os.fstat(self._backup_fd)
            visible = os.stat(
                backup_name,
                dir_fd=self._backup_parent_fd,
                follow_symlinks=False,
            )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _UnstableRegularFileError(
                "managed-output rollback directory changed"
            ) from error
        if (
            not stat.S_ISDIR(parent.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (parent.st_dev, parent.st_ino)
            != self._backup_parent_identity
            or (opened.st_dev, opened.st_ino)
            != self._backup_identity
            or (visible.st_dev, visible.st_ino)
            != self._backup_identity
        ):
            raise _UnstableRegularFileError(
                "managed-output rollback directory changed"
            )
        _identity_bound_remove(
            self._backup_parent_fd,
            backup_name,
            self._backup_fd,
            is_directory=True,
        )
        self._account_removed_backup_dir()

    def _account_removed_backup_dir(self):
        self._backup_dir = None
        self._close_backup_handles()

    def prepare(
        self,
        dest_path,
        expected=None,
        *,
        recovery_max_bytes=None,
    ):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        if dest_path in self._entries:
            return True
        if self._output is not None:
            name = self._pinned_name(dest_path)
            if name is None:
                return False
            self._output.verify()
            if expected is None:
                try:
                    os.stat(
                        name,
                        dir_fd=self._output.fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    self._entries[dest_path] = {
                        "name": name,
                        "backup": None,
                        "backup_state": None,
                        "published_identity": None,
                        "published_size": None,
                        "published_sha256": None,
                    }
                    return True
                except (OSError, TypeError, NotImplementedError):
                    return False
                return False
            try:
                initial = _stable_managed_file(
                    self._output,
                    name,
                    expected=expected,
                )
            except (OSError, ValueError):
                return False
            expected_identity = (
                expected.get("identity")
                if type(expected) is dict
                else None
            )
            if (
                expected_identity is not None
                and initial.identity != tuple(expected_identity)
            ):
                return False
            self._ensure_backup_dir()
            self._output.verify()
            moved = False
            move_verified = False
            try:
                stable = _stable_managed_file(
                    self._output,
                    name,
                    expected=expected,
                )
                if (
                    expected_identity is not None
                    and stable.identity != tuple(expected_identity)
                ):
                    return False
                backup_name = (
                    f"{self._backup_entry_prefix}"
                    f"{len(self._entries)}"
                )
                self._verify_backup_dir()
                move_descriptor = _open_owned_regular_entry(
                    self._output.fd,
                    name,
                    expected=stable,
                    error_message=(
                        "managed output changed before backup move"
                    ),
                )
                self._move_descriptor = move_descriptor
                self._move_expected = stable
                try:
                    self._move_to_backup(
                        name,
                        backup_name,
                    )
                finally:
                    self._move_descriptor = -1
                    self._move_expected = None
                    os.close(move_descriptor)
                moved = True
                self._verify_backup_dir()
                move_verified = True
                self._verify_backup_entry(
                    backup_name,
                    stable,
                )
            except _ManagedOutputRecoveryRequiredError:
                raise
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                if moved:
                    try:
                        recovery_state = self._stable_backup_entry(
                            backup_name,
                            max_size=(
                                stable.size
                                if recovery_max_bytes is None
                                else recovery_max_bytes
                            ),
                        )
                    except (
                        OSError,
                        TypeError,
                        NotImplementedError,
                        ValueError,
                    ) as recovery_error:
                        self._entries[dest_path] = {
                            "name": name,
                            "backup": backup_name,
                            "backup_state": stable,
                            "published_identity": None,
                            "published_size": None,
                            "published_sha256": None,
                        }
                        _append_explicit_cause(
                            recovery_error,
                            error,
                        )
                        self._raise_recovery_required(
                            "managed-output rollback entry could not be "
                            "verified after prepare conflict",
                            recovery_error,
                        )
                    self._entries[dest_path] = {
                        "name": name,
                        "backup": backup_name,
                        "backup_state": recovery_state,
                        "published_identity": None,
                        "published_size": None,
                        "published_sha256": None,
                    }
                    try:
                        self.restore(dest_path)
                    except _ManagedOutputRecoveryRequiredError as recovery_error:
                        _append_explicit_cause(
                            recovery_error,
                            error,
                        )
                        raise
                    except (
                        OSError,
                        TypeError,
                        NotImplementedError,
                        ValueError,
                    ) as restore_error:
                        _append_explicit_cause(
                            restore_error,
                            error,
                        )
                        self._raise_recovery_required(
                            "managed-output rollback entry could not be "
                            "verified and restored after prepare conflict",
                            restore_error,
                        )
                    if not move_verified:
                        raise _ManagedOutputPrepareError(
                            "managed-output move could not be verified "
                            f"after prepare: {error}"
                        ) from error
                    return False
                return False
            self._entries[dest_path] = {
                "name": name,
                "backup": backup_name,
                "backup_state": stable,
                "published_identity": None,
                "published_size": None,
                "published_sha256": None,
            }
            return True
        raise RuntimeError("managed-output journal lost its pinned directory")

    def stage_removal(self, dest_path, expected=None):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        if dest_path in self._entries:
            return True
        if self._output is not None:
            if expected is None:
                return False
            return self.prepare(dest_path, expected=expected)
        raise RuntimeError("managed-output journal lost its pinned directory")

    def bind_published(
        self,
        dest_path,
        identity,
        size,
        sha256,
    ):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        if self._output is None:
            return
        entry = self._entries.get(dest_path)
        if entry is None:
            raise RuntimeError(
                "managed output was not prepared before publication"
            )
        published = _stable_managed_file(
            self._output,
            entry["name"],
            expected={"size": size, "sha256": sha256},
        )
        if published.identity != tuple(identity):
            raise _UnstableRegularFileError(
                "published output changed before journal binding"
            )
        entry["published_identity"] = tuple(identity)
        entry["published_size"] = int(size)
        entry["published_sha256"] = str(sha256)

    def record_created(
        self,
        dest_path,
        identity,
        size,
        sha256,
    ):
        self._require_open()
        if self._output is None:
            raise RuntimeError(
                "created output recording requires a pinned directory"
            )
        dest_path = os.path.abspath(dest_path)
        name = self._pinned_name(dest_path)
        if name is None or dest_path in self._entries:
            raise RuntimeError("managed output could not be recorded")
        published = _stable_managed_file(
            self._output,
            name,
            expected={"size": size, "sha256": sha256},
        )
        if published.identity != tuple(identity):
            raise _UnstableRegularFileError(
                "published output changed before journal recording"
            )
        self._entries[dest_path] = {
            "name": name,
            "backup": None,
            "backup_state": None,
            "published_identity": tuple(identity),
            "published_size": int(size),
            "published_sha256": str(sha256),
        }

    def abandon(self, dest_path):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        entry = self._entries.get(dest_path)
        if entry is None:
            return None
        if self._output is None:
            backup_path = entry
        else:
            backup_name = entry["backup"]
            backup_path = self._backup_path(backup_name)
            if backup_name is not None:
                backup_state = entry["backup_state"]
                if backup_state is None:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before abandon"
                    )
                try:
                    self._verify_backup_entry(
                        backup_name,
                        backup_state,
                    )
                except _UnstableRegularFileError as error:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before abandon",
                        error,
                    )
        self._entries.pop(dest_path)
        if backup_path is not None:
            backup_path = os.path.abspath(backup_path)
            if backup_path not in self._detached_backup_paths:
                self._detached_backup_paths[backup_path] = (
                    backup_state if self._output is not None else None
                )
        if backup_path is None:
            self._cleanup_backup_dir()
        return backup_path

    def restore(self, dest_path, *, cleanup=True):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        if dest_path not in self._entries:
            return
        if self._output is not None:
            entry = self._entries[dest_path]
            name = entry["name"]
            backup_path = entry["backup"]
            backup_state = entry["backup_state"]
            identity = entry["published_identity"]
            published_size = entry["published_size"]
            published_sha256 = entry["published_sha256"]
            if entry.get("restored"):
                if backup_path is None or backup_state is None:
                    self._raise_recovery_required(
                        "managed-output restore anchor is unavailable"
                    )
                try:
                    restored = _stable_managed_file(
                        self._output,
                        name,
                        expected={
                            "size": backup_state.size,
                            "sha256": backup_state.sha256,
                        },
                    )
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "during rollback",
                        error,
                    )
                if restored.identity != backup_state.identity:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "during rollback"
                    )
                self._cleanup_restore_anchor(
                    backup_path,
                    backup_state,
                )
                self._entries.pop(dest_path)
                if cleanup:
                    self._cleanup_backup_dir()
                return
            try:
                self._output.verify()
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                self._raise_recovery_required(
                    "managed output changed before rollback",
                    error,
                )
            if identity is not None:
                try:
                    current = os.stat(
                        name,
                        dir_fd=self._output.fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    current = None
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    self._raise_recovery_required(
                        "managed output changed before rollback",
                        error,
                    )
                if current is not None:
                    try:
                        published = _stable_managed_file(
                            self._output,
                            name,
                            expected={
                                "size": published_size,
                                "sha256": published_sha256,
                            },
                        )
                    except (
                        OSError,
                        TypeError,
                        NotImplementedError,
                        ValueError,
                    ) as error:
                        self._raise_recovery_required(
                            "managed output changed before rollback",
                            error,
                        )
                    if published.identity != identity:
                        self._raise_recovery_required(
                            "managed output changed before rollback"
                        )
            if backup_path is None:
                if identity is not None and current is not None:
                    try:
                        self._unlink_created_after_final_canonical_check(
                            name,
                            published,
                        )
                    except FileNotFoundError:
                        pass
                    except _IdentityBoundMutationUnavailableError as error:
                        self._raise_pathless_recovery_required(
                            "managed-output cleanup remains pending",
                            error,
                        )
                    except _UnstableRegularFileError as error:
                        self._raise_recovery_required(
                            "managed output changed before rollback",
                            error,
                        )
                self._entries.pop(dest_path)
                if cleanup:
                    self._cleanup_backup_dir()
                return
            else:
                if backup_state is None:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before rollback"
                    )
                try:
                    self._verify_backup_entry(
                        backup_path,
                        backup_state,
                    )
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before rollback",
                        error,
                    )
                if identity is None:
                    try:
                        os.stat(
                            name,
                            dir_fd=self._output.fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    except (
                        OSError,
                        TypeError,
                        NotImplementedError,
                        ValueError,
                    ) as error:
                        self._raise_recovery_required(
                            "managed output changed before rollback",
                            error,
                        )
                    else:
                        self._raise_recovery_required(
                            "managed output changed before rollback"
                        )
                try:
                    _require_identity_bound_mutation()
                except _IdentityBoundMutationUnavailableError as error:
                    self._raise_pathless_recovery_required(
                        "managed-output cleanup remains pending",
                        error,
                    )
                try:
                    anchor_name = self._ensure_restore_anchor(
                        backup_path,
                        backup_state,
                    )
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    self._raise_recovery_required(
                        "managed-output restore anchor could not be verified",
                        error,
                    )
                restore_accounted = False
                try:
                    self._verify_backup_dir()
                    if identity is not None and current is not None:
                        self._restore_backup_over_published_after_final_check(
                            backup_path,
                            name,
                            published,
                        )
                    else:
                        self._restore_backup_into_absent_path_after_final_check(
                            backup_path,
                            name,
                        )
                    try:
                        restored = _stable_managed_file(
                            self._output,
                            name,
                            expected={
                                "size": backup_state.size,
                                "sha256": backup_state.sha256,
                            },
                        )
                    except (
                        OSError,
                        TypeError,
                        NotImplementedError,
                        ValueError,
                    ) as error:
                        try:
                            descriptor = _open_owned_regular_entry(
                                self._output.fd,
                                name,
                                expected=backup_state,
                                error_message=(
                                    "managed output changed before rollback"
                                ),
                            )
                            try:
                                _unlink_owned_regular_entry(
                                    self._output.fd,
                                    name,
                                    descriptor,
                                    expected=backup_state,
                                )
                            finally:
                                os.close(descriptor)
                        except BaseException as cleanup_error:
                            _append_explicit_cause(
                                error,
                                cleanup_error,
                            )
                        self._raise_recovery_required(
                            "managed-output rollback entry changed "
                            "during rollback",
                            error,
                        )
                    if restored.identity != backup_state.identity:
                        self._raise_recovery_required(
                            "managed-output rollback entry changed "
                            "during rollback"
                        )
                    entry["backup"] = anchor_name
                    entry["backup_state"] = backup_state
                    entry["restored"] = True
                    entry.pop("recovery_path", None)
                    restore_accounted = True
                    original_backup_path = os.path.abspath(
                        self._backup_path(backup_path)
                    )
                    self._detached_backup_paths[
                        original_backup_path
                    ] = backup_state
                    try:
                        self._remove_backup_entry(
                            backup_path,
                            backup_state,
                        )
                    except _IdentityBoundMutationUnavailableError as error:
                        self._raise_pathless_recovery_required(
                            "managed-output cleanup remains pending",
                            error,
                        )
                    except _UnstableRegularFileError as error:
                        self._raise_recovery_required(
                            "managed-output rollback entry changed "
                            "during rollback",
                            error,
                        )
                    self._detached_backup_paths.pop(
                        original_backup_path,
                        None,
                    )
                except BaseException:
                    if restore_accounted:
                        raise
                    try:
                        self._cleanup_restore_anchor(
                            anchor_name,
                            backup_state,
                        )
                    except _ManagedOutputDirectoryCleanupError:
                        anchor_path = os.path.abspath(
                            self._backup_path(anchor_name)
                        )
                        if (
                            anchor_path
                            not in self._detached_backup_paths
                        ):
                            self._detached_backup_paths[anchor_path] = (
                                backup_state
                            )
                    raise
                post_move_error = None
                try:
                    self._verify_backup_dir()
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    post_move_error = error
                try:
                    restored = _stable_managed_file(
                        self._output,
                        name,
                        expected={
                            "size": backup_state.size,
                            "sha256": backup_state.sha256,
                        },
                    )
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ) as error:
                    if post_move_error is not None:
                        _append_explicit_cause(
                            error,
                            post_move_error,
                        )
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "during rollback",
                        error,
                    )
                if restored.identity != backup_state.identity:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "during rollback"
                    )
                self._cleanup_restore_anchor(
                    anchor_name,
                    backup_state,
                )
                self._entries.pop(dest_path)
                if cleanup:
                    self._cleanup_backup_dir()
                return
        raise RuntimeError("managed-output journal lost its pinned directory")

    def discard(self, dest_path):
        self._require_open()
        dest_path = os.path.abspath(dest_path)
        if dest_path not in self._entries:
            return
        if self._output is not None:
            entry = self._entries[dest_path]
            backup_path = entry["backup"]
            if backup_path is not None:
                backup_state = entry["backup_state"]
                if backup_state is None:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before discard"
                    )
                try:
                    self._remove_backup_entry(
                        backup_path,
                        backup_state,
                    )
                except _IdentityBoundMutationUnavailableError as error:
                    self._raise_pathless_recovery_required(
                        "managed-output cleanup remains pending",
                        error,
                    )
                except _UnstableRegularFileError as error:
                    self._raise_recovery_required(
                        "managed-output rollback entry changed "
                        "before discard",
                        error,
                    )
                self._entries.pop(dest_path)
            else:
                self._entries.pop(dest_path)
            self._cleanup_backup_dir()
            return
        raise RuntimeError("managed-output journal lost its pinned directory")

    def rollback(self):
        if self._state in {
            _ManagedOutputJournalState.COMMIT_CLEANUP,
            _ManagedOutputJournalState.COMMITTED,
        }:
            return set()
        if self._state is _ManagedOutputJournalState.RECOVERY_REQUIRED:
            rollback_error = self._recovery_rollback_failure()
            raise rollback_error from self._recovery_error
        paths = set(self._entries)
        failures = []
        recovery_failure = None
        for dest_path in reversed(tuple(self._entries)):
            try:
                self.restore(dest_path, cleanup=False)
            except _ManagedOutputRecoveryRequiredError as error:
                failures.append((dest_path, error))
                recovery_failure = error
                break
            except OSError as error:
                failures.append((dest_path, error))
        cleanup_failure = None
        if not self._entries:
            try:
                self._cleanup_backup_dir()
            except _ManagedOutputDirectoryCleanupError as error:
                cleanup_failure = (error.backup_dir, error.error)
        if failures or cleanup_failure is not None:
            rollback_error = _ManagedOutputRollbackError(
                failures,
                cleanup_failure=cleanup_failure,
            )
            if recovery_failure is not None:
                self._rollback_error = rollback_error
                raise rollback_error from recovery_failure
            raise rollback_error
        return paths

    def _verify_visible_commit_entry(self, entry):
        name = entry["name"]
        identity = entry["published_identity"]
        self._output.verify()
        if identity is None:
            try:
                os.stat(
                    name,
                    dir_fd=self._output.fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise _UnstableRegularFileError(
                    "staged managed-output removal reappeared"
                )
        else:
            published = _stable_managed_file(
                self._output,
                name,
                expected={
                    "size": entry["published_size"],
                    "sha256": entry["published_sha256"],
                },
            )
            if published.identity != identity:
                raise _UnstableRegularFileError(
                    "published managed output identity changed"
                )
            entry["commit_mtime_ns"] = published.mtime_ns
            entry["commit_ctime_ns"] = published.ctime_ns
        self._output.verify()

    def _verify_commit_entry(self, entry):
        backup_path = entry["backup"]
        backup_state = entry["backup_state"]
        try:
            self._verify_visible_commit_entry(entry)
            if (backup_path is None) != (backup_state is None):
                raise _UnstableRegularFileError(
                    "managed-output rollback entry is unavailable"
                )
            if backup_path is not None:
                self._verify_backup_entry(
                    backup_path,
                    backup_state,
                )
        except (
            OSError,
            TypeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise _ManagedOutputRecoveryRequiredError(
                "managed output changed before journal commit"
            ) from error

    def _raise_commit_recovery(self, error):
        if isinstance(
            error,
            _ManagedOutputRecoveryRequiredError,
        ):
            recovery_error = error
        else:
            recovery_error = _ManagedOutputRecoveryRequiredError(
                "managed output changed before journal commit",
                recovery_paths=self.recovery_paths(),
            )
        recovery_error = self._enter_recovery_required(
            recovery_error
        )
        if recovery_error is error:
            raise recovery_error
        raise recovery_error from error

    def commit(self):
        if self._state is _ManagedOutputJournalState.RECOVERY_REQUIRED:
            raise self._recovery_error
        if self._state is _ManagedOutputJournalState.COMMITTED:
            return []
        if self._state is _ManagedOutputJournalState.OPEN:
            if self._output is not None:
                if self._entries:
                    for entry in tuple(self._entries.values()):
                        try:
                            self._verify_commit_entry(entry)
                        except _ManagedOutputRecoveryRequiredError as error:
                            self._raise_commit_recovery(error)
                elif self._verify_empty_root_on_commit:
                    self._output.verify()
            self._state = _ManagedOutputJournalState.COMMIT_CLEANUP
        elif self._state is not _ManagedOutputJournalState.COMMIT_CLEANUP:
            raise RuntimeError(
                "managed-output journal cannot commit from "
                f"{self._state.value}"
            )
        if self._output is not None:
            pending = []
            for backup_path, backup_state in tuple(
                self._detached_backup_paths.items()
            ):
                status = (
                    self._detached_backup_copy_status(
                        backup_path,
                        backup_state,
                    )
                    if backup_state is not None
                    else "unavailable"
                )
                if status == "owned":
                    pending.append(
                        self._pending_backup_cleanup(backup_path)
                    )
                elif status == "unavailable":
                    pending.append(_PENDING_CLEANUP_WITHOUT_PATH)
                elif status in {"missing", "replaced"}:
                    self._detached_backup_paths.pop(
                        backup_path,
                        None,
                    )
            for dest_path, entry in tuple(self._entries.items()):
                backup_path = entry["backup"]
                if backup_path is None:
                    self._entries.pop(dest_path, None)
                    continue
                removed = False
                cleanup_unavailable = False
                for _attempt in range(2):
                    try:
                        self._remove_backup_entry(
                            backup_path,
                            entry["backup_state"],
                            commit_entry=entry,
                        )
                        self._entries.pop(dest_path, None)
                        removed = True
                        break
                    except _ManagedOutputRecoveryRequiredError as error:
                        self._raise_commit_recovery(error)
                    except _IdentityBoundMutationUnavailableError:
                        cleanup_unavailable = True
                        break
                    except _UnstableRegularFileError as error:
                        self._raise_commit_recovery(error)
                    except OSError:
                        continue
                if not removed:
                    if cleanup_unavailable:
                        pending.append(_PENDING_CLEANUP_WITHOUT_PATH)
                    elif self._backup_copy_matches(
                        backup_path,
                        entry["backup_state"],
                    ):
                        pending.append(
                            self._pending_backup_cleanup(
                                self._backup_path(backup_path)
                            )
                        )
                    else:
                        pending.append(_PENDING_CLEANUP_WITHOUT_PATH)
            if (
                not self._entries
                and not self._detached_backup_paths
                and self._backup_dir is not None
            ):
                backup_dir = self._backup_dir
                removed = False
                cleanup_unavailable = False
                for _attempt in range(2):
                    try:
                        self._remove_pinned_backup_dir()
                        removed = True
                        break
                    except _IdentityBoundMutationUnavailableError:
                        cleanup_unavailable = True
                        break
                    except OSError:
                        continue
                if not removed:
                    if cleanup_unavailable:
                        pending.append(
                            _PENDING_CLEANUP_WITHOUT_PATH
                        )
                    elif self._backup_directory_matches():
                        pending.append(backup_dir)
                    else:
                        pending.append(
                            _PENDING_CLEANUP_WITHOUT_PATH
                        )
            if (
                not self._entries
                and not self._detached_backup_paths
                and self._backup_dir is None
            ):
                self._state = _ManagedOutputJournalState.COMMITTED
            return list(dict.fromkeys(pending))
        raise RuntimeError("managed-output journal lost its pinned directory")

    def commit_after_sidecar(self):
        self._verify_empty_root_on_commit = True
        try:
            return self.commit()
        finally:
            self._verify_empty_root_on_commit = False

    def _cleanup_backup_dir(self):
        if (
            self._entries
            or self._detached_backup_paths
            or self._backup_dir is None
        ):
            return
        backup_dir = self._backup_dir
        try:
            self._remove_pinned_backup_dir()
        except _IdentityBoundMutationUnavailableError as error:
            raise _ManagedOutputDirectoryCleanupError(
                _PENDING_CLEANUP_WITHOUT_PATH,
                error,
            ) from error
        except FileNotFoundError:
            raise _ManagedOutputDirectoryCleanupError(
                backup_dir,
                _UnstableRegularFileError(
                    "managed-output rollback directory changed"
                ),
            )
        except OSError as error:
            raise _ManagedOutputDirectoryCleanupError(
                backup_dir,
                error,
            ) from error


def _append_post_commit_cleanup_records(records, paths):
    seen = {
        os.path.abspath(os.fspath(record["path"]))
        for record in records
        if (
            record.get("reason") == "post-commit cleanup pending"
            and record.get("path") is not None
        )
    }
    generic_seen = any(
        record.get("reason") == "post-commit cleanup pending"
        and record.get("path") is None
        for record in records
    )
    for path in paths:
        if path is _PENDING_CLEANUP_WITHOUT_PATH:
            if generic_seen:
                continue
            records.append({
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            })
            generic_seen = True
            continue
        path = os.path.abspath(os.fspath(path))
        if path in seen:
            continue
        records.append({
            "name": os.path.basename(path),
            "reason": "post-commit cleanup pending",
            "path": path,
        })
        seen.add(path)


def _restore_managed_output(
    output_journal,
    dest_path,
    operation_error=None,
):
    try:
        output_journal.restore(dest_path)
    except _ManagedOutputDirectoryCleanupError as cleanup_error:
        rollback_error = _ManagedOutputRollbackError(
            [],
            cleanup_failure=(
                cleanup_error.backup_dir,
                cleanup_error.error,
            ),
        )
        if operation_error is None:
            raise rollback_error from cleanup_error
        raise _ManagedOutputRestoreFailure(
            operation_error,
            rollback_error,
        ) from cleanup_error
    except OSError as restore_error:
        rollback_error = _ManagedOutputRollbackError(
            [(os.path.abspath(dest_path), restore_error)]
        )
        if operation_error is None:
            raise rollback_error from restore_error
        raise _ManagedOutputRestoreFailure(
            operation_error,
            rollback_error,
        ) from restore_error


def download_file(url, dest_path, timeout=180, max_bytes=_DEFAULT_MAX,
                  retries=3, backoff=2.0):
    """Download to disk with redirects, size cap, HTML sniffing, and retry/backoff.
    Streams the body in chunks (no whole-file buffering). Retries on timeout and
    HTTP 5xx; auth errors (401/403) and size/HTML rejections are terminal."""
    staging = (
        dest_path
        if isinstance(dest_path, _DownloadStagingFile)
        else None
    )
    result_path = (
        staging.display_path if staging is not None else dest_path
    )
    if not _http.is_valid_http_url(url):
        try:
            scheme = urllib.parse.urlsplit(url).scheme.lower()
        except (AttributeError, TypeError, ValueError):
            scheme = (
                url.split(":", 1)[0].lower()
                if isinstance(url, str) and ":" in url
                else ""
            )
        if scheme in {"http", "https"}:
            return {
                "ok": False,
                "path": result_path,
                "skipped_reason": "invalid download URL",
            }
        return {
            "ok": False,
            "path": result_path,
            "skipped_reason": f"unsupported URL scheme: {url!r}",
        }
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last_reason = "unknown error"
    for attempt in range(retries):
        try:
            with _http.open_http(req, timeout=timeout) as resp:
                final_url = _http.validated_response_url(resp)
                ctype = (resp.info().get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    return {"ok": False, "path": result_path,
                            "skipped_reason": f"server returned HTML ({ctype}), not a data file"}
                clen = resp.info().get("Content-Length")
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    return {"ok": False, "path": result_path,
                            "skipped_reason": f"file exceeds max_bytes ({max_bytes})"}
                try:
                    size = _atomic_stream_write(resp, dest_path, max_bytes)
                except _SizeLimitExceeded as e:
                    return {"ok": False, "path": result_path,
                            "skipped_reason": str(e)}
                result = {
                    "ok": True,
                    "path": result_path,
                    "size": size,
                    "content_type": ctype.split(";", 1)[0].strip(),
                    "source_url": final_url,
                }
                return result
        except _TransientCleanupError:
            raise
        except _http.URLPolicyError:
            return {
                "ok": False,
                "path": result_path,
                "skipped_reason": _URL_POLICY_SKIP_REASON,
            }
        except urllib.error.HTTPError as e:
            _http._close_http_response(e)
            if e.code in (401, 403):
                return {"ok": False, "path": result_path,
                        "skipped_reason": (f"requires authentication (HTTP {e.code}); "
                                           "download this file manually from the dataset page")}
            last_reason = f"HTTP {e.code}: {e.reason}"
            if not (500 <= e.code < 600):
                return {"ok": False, "path": result_path, "skipped_reason": last_reason}
        except Exception as e:
            last_reason = f"download error: {e}"
        if attempt < retries - 1:
            time.sleep(backoff * (2 ** attempt))
    return {
        "ok": False,
        "path": result_path,
        "skipped_reason": last_reason,
    }


def _write_collision_safe(
    out_dir,
    name,
    data,
    *,
    _return_entry=False,
    max_total_bytes=None,
    transient_files=(),
):
    if not isinstance(out_dir, _PinnedOutputDirectory):
        with _pinned_output_directory(out_dir) as output:
            return _write_collision_safe(
                output,
                name,
                data,
                _return_entry=_return_entry,
                max_total_bytes=max_total_bytes,
                transient_files=transient_files,
            )

    def regular_file_matches(filename):
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            return None
        try:
            fd = os.open(
                filename,
                os.O_RDONLY | nofollow,
                dir_fd=out_dir.fd,
            )
        except OSError:
            return None
        try:
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != len(data)
            ):
                return None
            offset = 0
            with os.fdopen(os.dup(fd), "rb") as fh:
                while offset < len(data):
                    chunk = fh.read(
                        min(1024 * 1024, len(data) - offset)
                    )
                    if (
                        not chunk
                        or chunk != data[offset:offset + len(chunk)]
                    ):
                        return None
                    offset += len(chunk)
            final_opened = os.fstat(fd)
            try:
                current = os.stat(
                    filename,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            if (
                stat.S_ISREG(current.st_mode)
                and stat.S_ISREG(final_opened.st_mode)
                and current.st_size == len(data)
                and final_opened.st_size == len(data)
                and current.st_dev == opened.st_dev
                and current.st_ino == opened.st_ino
                and final_opened.st_dev == opened.st_dev
                and final_opened.st_ino == opened.st_ino
            ):
                return current
            return None
        finally:
            os.close(fd)

    def publication(
        filename,
        *,
        size,
        identity,
        created,
    ):
        return _PublishedOutputFile(
            filename=filename,
            size=size,
            identity=identity,
            sha256=content_sha256,
            created=created,
        )

    def result(entry):
        if _return_entry:
            return entry
        return entry.display_path(out_dir)

    def require_projected_size(
        *,
        private_name,
        private_state,
        additional_size,
    ):
        if max_total_bytes is None:
            return
        excluded_entries = {
            private_name: (
                private_state.st_dev,
                private_state.st_ino,
            ),
        }
        sidecar_identity = _verified_source_sidecar_identity(out_dir)
        if sidecar_identity is not None:
            excluded_entries[SOURCE_SIDECAR] = sidecar_identity
        for staging in transient_files:
            if staging.output.fd != out_dir.fd:
                raise _UnstableRegularFileError(
                    "download staging belongs to a different output directory"
                )
            _verify_staging_file(staging)
            current = os.fstat(staging.fd)
            excluded_entries[staging.name] = (
                current.st_dev,
                current.st_ino,
            )
        out_dir.verify()
        projected = (
            _dir_size_fd_excluding_root_entries(
                out_dir.fd,
                excluded_entries,
            )
            + additional_size
        )
        if projected > max_total_bytes:
            raise _PaperDataLimitError(
                "publication skipped because projected paper data exceeds "
                "per-paper cap"
            )

    out_dir.verify()
    stem, suffix = os.path.splitext(os.path.basename(name))
    content_sha256 = hashlib.sha256(data).hexdigest()
    digest = content_sha256[:10]
    temp_name = None
    temp_fd = -1
    private_entry = None
    published_entry = None
    operation_error = None
    cleanup_warning = None
    try:
        for _attempt in range(128):
            temp_name = f".paperconan-publish-{secrets.token_hex(8)}"
            try:
                with _transaction_state_allocation():
                    temp_fd = os.open(
                        temp_name,
                        (
                            os.O_RDWR
                            | os.O_CREAT
                            | os.O_EXCL
                            | os.O_NOFOLLOW
                        ),
                        0o600,
                        dir_fd=out_dir.fd,
                    )
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(
                "could not allocate fetch publication staging file"
            )
        with os.fdopen(os.dup(temp_fd), "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        private = os.fstat(temp_fd)
        if (
            not stat.S_ISREG(private.st_mode)
            or private.st_size != len(data)
            or _hash_exact_fd(temp_fd, private.st_size)
            != content_sha256
        ):
            raise _UnstableRegularFileError(
                "publication staging is not a stable regular file"
            )
        collision_index = 0
        while True:
            if collision_index == 0:
                filename = stem + suffix
            elif collision_index == 1:
                filename = f"{stem}-{digest}{suffix}"
            else:
                filename = (
                    f"{stem}-{digest}-{collision_index}{suffix}"
                )
            private_entry = publication(
                filename,
                size=private.st_size,
                identity=(private.st_dev, private.st_ino),
                created=True,
            )
            matched = regular_file_matches(filename)
            if matched is not None:
                require_projected_size(
                    private_name=temp_name,
                    private_state=private,
                    additional_size=0,
                )
                out_dir.verify()
                published_entry = publication(
                    filename,
                    size=matched.st_size,
                    identity=(matched.st_dev, matched.st_ino),
                    created=False,
                )
                break
            require_projected_size(
                private_name=temp_name,
                private_state=private,
                additional_size=private.st_size,
            )
            try:
                with _transaction_state_allocation():
                    os.link(
                        temp_name,
                        filename,
                        src_dir_fd=out_dir.fd,
                        dst_dir_fd=out_dir.fd,
                        follow_symlinks=False,
                    )
            except FileExistsError:
                matched = regular_file_matches(filename)
                if matched is not None:
                    require_projected_size(
                        private_name=temp_name,
                        private_state=private,
                        additional_size=0,
                    )
                    out_dir.verify()
                    published_entry = publication(
                        filename,
                        size=matched.st_size,
                        identity=(
                            matched.st_dev,
                            matched.st_ino,
                        ),
                        created=False,
                    )
                    break
                collision_index += 1
                continue
            visible_fd = -1
            try:
                visible_fd = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=out_dir.fd,
                )
                opened = os.fstat(visible_fd)
                current = os.stat(
                    filename,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(current.st_mode)
                    or not stat.S_ISREG(opened.st_mode)
                    or current.st_size != private_entry.size
                    or opened.st_size != private_entry.size
                    or (current.st_dev, current.st_ino)
                    != private_entry.identity
                    or (opened.st_dev, opened.st_ino)
                    != private_entry.identity
                ):
                    raise _UnstableRegularFileError(
                        "published output entry is not a stable regular file"
                    )
                out_dir.verify()
            except Exception as exc:
                raise _PublicationRecoveryError(
                    f"{exc}; retained visible output for recovery: "
                    f"{filename}"
                ) from exc
            finally:
                if visible_fd >= 0:
                    os.close(visible_fd)
            published_entry = private_entry
            break
    except BaseException as error:
        operation_error = error
        raise
    finally:
        cleanup_error = None
        if temp_fd >= 0:
            try:
                _unlink_owned_regular_entry(
                    out_dir.fd,
                    temp_name,
                    temp_fd,
                )
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                cleanup_error = error
            try:
                os.close(temp_fd)
            except OSError as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            if operation_error is not None:
                _append_explicit_cause(
                    cleanup_error,
                    operation_error,
                )
                raise _PublicationRecoveryError(
                    "publication staging cleanup incomplete"
                ) from cleanup_error
            cleanup_warning = "publication staging cleanup incomplete"
    if cleanup_warning is not None:
        published_entry = _PublishedOutputFile(
            filename=published_entry.filename,
            size=published_entry.size,
            identity=published_entry.identity,
            sha256=published_entry.sha256,
            created=published_entry.created,
            cleanup_warning=cleanup_warning,
        )
    return result(published_entry)


def _publish_download_staging(
    output,
    staging,
    *,
    output_name,
    base,
    source_name,
    expected_old,
    output_journal,
):
    staged = _stable_staging_state(staging)
    if expected_old is None:
        data = _read_verified_download_staging(
            staging,
            max_bytes=staged.size,
        )
        published = _write_collision_safe(
            output,
            output_name,
            data,
            _return_entry=True,
        )
        dest = os.path.join(output.path, published.filename)
        if published.created:
            output_journal.record_created(
                dest,
                published.identity,
                published.size,
                published.sha256,
            )
        output.verify()
        return _ManagedFileState(
            name=published.filename,
            size=published.size,
            sha256=published.sha256,
            identity=published.identity,
            created=published.created,
            cleanup_warning=published.cleanup_warning,
        )
    candidate = output_name
    expected = expected_old
    while True:
        dest = os.path.join(output.path, candidate)
        if not output_journal.prepare(dest, expected=expected):
            candidate = _managed_output_name(
                output,
                base,
                source_name,
                (),
            )
            expected = None
            continue
        try:
            output.verify()
            with _transaction_state_allocation():
                os.link(
                    staging.name,
                    candidate,
                    src_dir_fd=output.fd,
                    dst_dir_fd=output.fd,
                    follow_symlinks=False,
                )
            output_journal.bind_published(
                dest,
                staged.identity,
                staged.size,
                staged.sha256,
            )
            published = _stable_managed_file(output, candidate)
            if (
                published.size != staged.size
                or published.sha256 != staged.sha256
                or published.identity != staged.identity
            ):
                raise _UnstableRegularFileError(
                    "published output does not match download staging"
                )
            output.verify()
            return published
        except FileExistsError:
            _restore_managed_output(output_journal, dest)
            candidate = _managed_output_name(
                output,
                base,
                source_name,
                (),
            )
            expected = None
        except BaseException as error:
            _restore_managed_output(
                output_journal,
                dest,
                operation_error=error,
            )
            raise


def _archive_staging_file(out_dir, suffix):
    return _download_staging_file(
        out_dir,
        prefix=".paperconan-archive-",
        suffix=suffix,
    )


def _published_file_limit_reason(cardinality):
    return (
        "published file cardinality ceiling reached "
        f"({cardinality.max_published_files}); remaining files were skipped"
    )


def _archive_member_limit_reason(cardinality):
    return (
        "archive member cardinality ceiling reached "
        f"({cardinality.max_archive_members}); "
        "remaining eligible members were skipped"
    )


def _append_limit_reason(reasons, reason):
    if reasons is not None and reason not in reasons:
        reasons.append(reason)


def _is_reserved_source_sidecar(name):
    try:
        basename = os.path.basename(os.fsdecode(name))
    except (TypeError, ValueError):
        return False
    return basename.casefold() == SOURCE_SIDECAR.casefold()


def _archive_blocking_reason(cardinality):
    if cardinality is None:
        return None
    if not cardinality.can_publish():
        return _published_file_limit_reason(cardinality)
    if cardinality.archive_members >= cardinality.max_archive_members:
        return _archive_member_limit_reason(cardinality)
    return None


def _read_exact_zip_range(source, offset, size, label):
    if offset < 0 or size < 0:
        raise ValueError(f"ZIP {label} position is invalid")
    source.seek(offset, os.SEEK_SET)
    data = source.read(size)
    if len(data) != size:
        raise ValueError(f"ZIP {label} is truncated")
    return data


def _validate_zip_central_directory(
    source,
    *,
    entry_count,
    directory_size,
    directory_offset,
    record_position,
    max_entries,
    prefix_adjustment=None,
):
    if directory_size > record_position:
        raise ValueError("ZIP central directory position is invalid")
    actual_offset = record_position - directory_size
    if actual_offset < directory_offset or actual_offset < 0:
        raise ValueError("ZIP central directory position is invalid")
    if (
        prefix_adjustment is not None
        and actual_offset - directory_offset != prefix_adjustment
    ):
        raise ValueError(
            "ZIP central directory position is inconsistent"
        )
    observed = 0
    position = actual_offset
    while position < record_position:
        remaining = record_position - position
        if remaining < _ZIP_CENTRAL_FILE_HEADER.size:
            raise ValueError(
                "ZIP central directory fixed header is truncated"
            )
        fixed = _read_exact_zip_range(
            source,
            position,
            _ZIP_CENTRAL_FILE_HEADER.size,
            "central directory fixed header",
        )
        fields = _ZIP_CENTRAL_FILE_HEADER.unpack(fixed)
        if fields[0] != _ZIP_CENTRAL_DIRECTORY_SIGNATURE:
            raise ValueError(
                "ZIP central directory signature is invalid"
            )
        filename_size, extra_size, comment_size = fields[10:13]
        disk_number = fields[13]
        if disk_number != 0:
            raise ValueError(
                "multi-disk ZIP archives are unavailable"
            )
        variable_size = filename_size + extra_size + comment_size
        record_size = (
            _ZIP_CENTRAL_FILE_HEADER.size + variable_size
        )
        record_end = position + record_size
        if (
            record_size < _ZIP_CENTRAL_FILE_HEADER.size
            or record_end <= position
            or record_end > record_position
        ):
            raise ValueError(
                "ZIP central directory record is truncated"
            )
        observed += 1
        if observed > max_entries:
            raise ValueError(
                f"observed ZIP entry count {observed} exceeds "
                f"preflight ceiling {max_entries}"
            )
        position = record_end
        source.seek(position, os.SEEK_SET)
    if position != record_position:
        raise ValueError(
            "ZIP central directory end is inconsistent"
        )
    if observed != entry_count:
        raise ValueError("ZIP entry counts are inconsistent")
    return observed


def _preflight_zip_entry_count(source, *, max_entries):
    if not isinstance(max_entries, int) or max_entries < 0:
        raise ValueError("ZIP entry ceiling is invalid")
    try:
        source.seek(0, os.SEEK_END)
        file_size = source.tell()
    except (AttributeError, OSError, ValueError) as error:
        raise ValueError("ZIP source is not seekable") from error
    if file_size < _ZIP_EOCD.size:
        raise ValueError(
            "ZIP EOCD record is missing or truncated"
        )

    tail_size = min(
        file_size,
        _ZIP_EOCD.size + _ZIP_MAX_COMMENT_BYTES,
    )
    tail_offset = file_size - tail_size
    tail = _read_exact_zip_range(
        source,
        tail_offset,
        tail_size,
        "EOCD tail",
    )
    candidates = []
    for relative_offset in range(
        tail_size - _ZIP_EOCD.size,
        -1,
        -1,
    ):
        fields = _ZIP_EOCD.unpack_from(tail, relative_offset)
        if fields[0] != _ZIP_EOCD_SIGNATURE:
            continue
        comment_size = fields[-1]
        record_position = tail_offset + relative_offset
        if (
            record_position
            + _ZIP_EOCD.size
            + comment_size
            == file_size
        ):
            candidates.append((record_position, fields))
    if not candidates:
        raise ValueError(
            "ZIP EOCD record is missing or truncated"
        )
    if len(candidates) != 1:
        raise ValueError("ZIP EOCD metadata is ambiguous")

    record_position, fields = candidates[0]
    (
        _,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        _,
    ) = fields
    needs_zip64 = (
        disk_number == _ZIP16_SENTINEL
        or central_directory_disk == _ZIP16_SENTINEL
        or entries_on_disk == _ZIP16_SENTINEL
        or total_entries == _ZIP16_SENTINEL
        or directory_size == _ZIP32_SENTINEL
        or directory_offset == _ZIP32_SENTINEL
    )

    if not needs_zip64:
        if disk_number != 0 or central_directory_disk != 0:
            raise ValueError(
                "multi-disk ZIP archives are unavailable"
            )
        if entries_on_disk != total_entries:
            raise ValueError(
                "ZIP entry counts are inconsistent"
            )
        if total_entries > max_entries:
            raise ValueError(
                f"raw ZIP entry count {total_entries} exceeds "
                f"preflight ceiling {max_entries}"
            )
        return _validate_zip_central_directory(
            source,
            entry_count=total_entries,
            directory_size=directory_size,
            directory_offset=directory_offset,
            record_position=record_position,
            max_entries=max_entries,
        )

    locator_position = (
        record_position - _ZIP64_LOCATOR.size
    )
    locator_data = _read_exact_zip_range(
        source,
        locator_position,
        _ZIP64_LOCATOR.size,
        "ZIP64 locator",
    )
    (
        locator_signature,
        zip64_disk,
        declared_zip64_offset,
        disk_count,
    ) = _ZIP64_LOCATOR.unpack(locator_data)
    if locator_signature != _ZIP64_LOCATOR_SIGNATURE:
        raise ValueError(
            "ZIP64 locator signature is invalid"
        )
    if zip64_disk != 0 or disk_count != 1:
        raise ValueError(
            "multi-disk ZIP64 archives are unavailable"
        )

    candidate_positions = [declared_zip64_offset]
    inferred_position = (
        locator_position - _ZIP64_EOCD.size
    )
    if inferred_position != declared_zip64_offset:
        candidate_positions.append(inferred_position)
    zip64_records = []
    for candidate_position in candidate_positions:
        if (
            candidate_position < 0
            or candidate_position + _ZIP64_EOCD.size
            > locator_position
        ):
            continue
        data = _read_exact_zip_range(
            source,
            candidate_position,
            _ZIP64_EOCD.size,
            "ZIP64 EOCD record",
        )
        values = _ZIP64_EOCD.unpack(data)
        if values[0] != _ZIP64_EOCD_SIGNATURE:
            continue
        record_size = values[1]
        if (
            record_size < _ZIP64_EOCD.size - 12
            or candidate_position + 12 + record_size
            != locator_position
        ):
            continue
        zip64_records.append(
            (candidate_position, values)
        )
    if len(zip64_records) != 1:
        raise ValueError(
            "ZIP64 EOCD record position or length is invalid"
        )

    zip64_position, values = zip64_records[0]
    (
        _,
        _,
        _,
        _,
        zip64_disk_number,
        zip64_directory_disk,
        zip64_entries_on_disk,
        zip64_total_entries,
        zip64_directory_size,
        zip64_directory_offset,
    ) = values
    if (
        zip64_disk_number != 0
        or zip64_directory_disk != 0
    ):
        raise ValueError(
            "multi-disk ZIP64 archives are unavailable"
        )
    if zip64_entries_on_disk != zip64_total_entries:
        raise ValueError(
            "ZIP64 entry counts are inconsistent"
        )

    classic_pairs = (
        (
            disk_number,
            _ZIP16_SENTINEL,
            zip64_disk_number,
        ),
        (
            central_directory_disk,
            _ZIP16_SENTINEL,
            zip64_directory_disk,
        ),
        (
            entries_on_disk,
            _ZIP16_SENTINEL,
            zip64_entries_on_disk,
        ),
        (
            total_entries,
            _ZIP16_SENTINEL,
            zip64_total_entries,
        ),
        (
            directory_size,
            _ZIP32_SENTINEL,
            zip64_directory_size,
        ),
        (
            directory_offset,
            _ZIP32_SENTINEL,
            zip64_directory_offset,
        ),
    )
    if any(
        classic_value != sentinel
        and classic_value != zip64_value
        for classic_value, sentinel, zip64_value
        in classic_pairs
    ):
        raise ValueError(
            "classic and ZIP64 metadata are inconsistent"
        )
    if zip64_total_entries > max_entries:
        raise ValueError(
            f"raw ZIP entry count {zip64_total_entries} exceeds "
            f"preflight ceiling {max_entries}"
        )
    prefix_adjustment = (
        zip64_position - declared_zip64_offset
    )
    if prefix_adjustment < 0:
        raise ValueError(
            "ZIP64 EOCD position is invalid"
        )
    return _validate_zip_central_directory(
        source,
        entry_count=zip64_total_entries,
        directory_size=zip64_directory_size,
        directory_offset=zip64_directory_offset,
        record_position=zip64_position,
        max_entries=max_entries,
        prefix_adjustment=prefix_adjustment,
    )


def _extract_selected_zip(
    zip_source,
    out_dir,
    *,
    include_images=False,
    max_member_bytes=_DEFAULT_MAX,
    return_entries=False,
    cardinality=None,
    limit_reasons=None,
    published_entries=None,
    pending_entries=None,
    transient_files=(),
):
    extracted = (
        published_entries
        if published_entries is not None
        else []
    )
    pending = (
        pending_entries
        if pending_entries is not None
        else []
    )
    allowed = {"tabular"}
    if include_images:
        allowed.update({"image", "document"})
    if isinstance(
        zip_source,
        (bytes, bytearray, memoryview),
    ):
        source = io.BytesIO(bytes(zip_source))
    elif all(
        hasattr(zip_source, name)
        for name in ("read", "seek", "tell")
    ):
        source = zip_source
    else:
        raise ValueError("ZIP source is not seekable")
    _preflight_zip_entry_count(
        source,
        max_entries=_MAX_RAW_ZIP_ENTRIES_PER_ARCHIVE,
    )
    source.seek(0, os.SEEK_SET)
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = os.path.basename(info.filename)
            if _is_reserved_source_sidecar(name):
                _append_limit_reason(
                    limit_reasons,
                    _RESERVED_SOURCE_SIDECAR_REASON,
                )
                continue
            publication_name = name
            if _is_reserved_managed_name(publication_name):
                publication_name = _managed_output_name(
                    out_dir,
                    publication_name,
                    info.filename,
                    (),
                )
            if (
                not name
                or asset_type(name) not in allowed
                or info.file_size > max_member_bytes
            ):
                continue
            if cardinality is not None:
                if not cardinality.can_publish():
                    _append_limit_reason(
                        limit_reasons,
                        _published_file_limit_reason(
                            cardinality
                        ),
                    )
                    break
                if not cardinality.claim_archive_member():
                    _append_limit_reason(
                        limit_reasons,
                        _archive_member_limit_reason(
                            cardinality
                        ),
                    )
                    break
            try:
                with archive.open(info) as source_member:
                    data = source_member.read(
                        max_member_bytes + 1
                    )
            except _ZIP_MEMBER_READ_EXCEPTIONS as error:
                raise _ArchiveReadError(str(error)) from error
            if len(data) > max_member_bytes:
                continue
            try:
                destination = _write_collision_safe(
                    out_dir,
                    publication_name,
                    data,
                    _return_entry=return_entries,
                    max_total_bytes=_MAX_PAPER_BYTES,
                    transient_files=transient_files,
                )
            except _PaperDataLimitError as error:
                _append_limit_reason(
                    limit_reasons,
                    str(error),
                )
                continue
            if return_entries:
                pending.append(destination)
            if cardinality is not None:
                cardinality.record_publication()
            if return_entries:
                _verify_published_output_file(
                    out_dir,
                    destination,
                )
                out_dir.verify()
                pending.remove(destination)
            extracted.append(destination)
    return extracted


def _extract_selected_tar(
    tar_source,
    out_dir,
    *,
    include_images=False,
    max_member_bytes=_DEFAULT_MAX,
    return_entries=False,
    cardinality=None,
    limit_reasons=None,
    published_entries=None,
    pending_entries=None,
    transient_files=(),
):
    extracted = (
        published_entries
        if published_entries is not None
        else []
    )
    pending = (
        pending_entries
        if pending_entries is not None
        else []
    )
    allowed = {"tabular"}
    if include_images:
        allowed.update({"image", "document"})
    compressed = (
        nullcontext(tar_source)
        if hasattr(tar_source, "read")
        else open(tar_source, "rb")
    )
    with compressed as compressed_source:
        with gzip.GzipFile(
            fileobj=compressed_source,
            mode="rb",
        ) as uncompressed:
            bounded = _BoundedUncompressedReader(
                uncompressed,
                max_bytes=(
                    _MAX_UNCOMPRESSED_TAR_BYTES_PER_ARCHIVE
                ),
                max_members=_MAX_RAW_TAR_MEMBERS_PER_ARCHIVE,
            )
            try:
                archive = tarfile.open(
                    fileobj=bounded,
                    mode="r|",
                )
            except _TAR_STREAM_READ_EXCEPTIONS as error:
                raise _ArchiveReadError(str(error)) from error
            with archive:
                members = iter(archive)
                while True:
                    try:
                        member = next(members)
                    except StopIteration:
                        break
                    except _TAR_STREAM_READ_EXCEPTIONS as error:
                        raise _ArchiveReadError(
                            str(error)
                        ) from error
                    if not member.isfile():
                        continue
                    name = os.path.basename(member.name)
                    if _is_reserved_source_sidecar(name):
                        _append_limit_reason(
                            limit_reasons,
                            _RESERVED_SOURCE_SIDECAR_REASON,
                        )
                        continue
                    publication_name = name
                    if _is_reserved_managed_name(
                        publication_name
                    ):
                        publication_name = _managed_output_name(
                            out_dir,
                            publication_name,
                            member.name,
                            (),
                        )
                    if (
                        not name
                        or asset_type(name) not in allowed
                        or member.size > max_member_bytes
                    ):
                        continue
                    if cardinality is not None:
                        if not cardinality.can_publish():
                            _append_limit_reason(
                                limit_reasons,
                                _published_file_limit_reason(
                                    cardinality
                                ),
                            )
                            break
                        if not cardinality.claim_archive_member():
                            _append_limit_reason(
                                limit_reasons,
                                _archive_member_limit_reason(
                                    cardinality
                                ),
                            )
                            break
                    try:
                        source_member = archive.extractfile(
                            member
                        )
                        if source_member is None:
                            continue
                        data = source_member.read(
                            max_member_bytes + 1
                        )
                    except _TAR_STREAM_READ_EXCEPTIONS as error:
                        raise _ArchiveReadError(
                            str(error)
                        ) from error
                    if len(data) > max_member_bytes:
                        continue
                    try:
                        destination = _write_collision_safe(
                            out_dir,
                            publication_name,
                            data,
                            _return_entry=return_entries,
                            max_total_bytes=_MAX_PAPER_BYTES,
                            transient_files=transient_files,
                        )
                    except _PaperDataLimitError as error:
                        _append_limit_reason(
                            limit_reasons,
                            str(error),
                        )
                        continue
                    if return_entries:
                        pending.append(destination)
                    if cardinality is not None:
                        cardinality.record_publication()
                    if return_entries:
                        _verify_published_output_file(
                            out_dir,
                            destination,
                        )
                        out_dir.verify()
                        pending.remove(destination)
                    extracted.append(destination)
    return extracted


def _is_reserved_managed_name(name):
    if not isinstance(name, str):
        return False
    folded = os.path.basename(name).casefold()
    sidecar = SOURCE_SIDECAR.casefold()
    return (
        folded == sidecar
        or folded.startswith(".paperconan-archive-")
        or folded.startswith(f".{sidecar}.")
    )


def _safe_managed_path(out_dir, relative):
    if (
        not isinstance(relative, str)
        or not relative
        or _is_reserved_managed_name(relative)
    ):
        return None
    try:
        if os.path.isabs(relative):
            return None
    except (OSError, TypeError, ValueError):
        return None
    parts = relative.split(os.sep)
    if os.altsep:
        parts = [
            part
            for component in parts
            for part in component.split(os.altsep)
        ]
    if any(part in ("", ".", "..") for part in parts):
        return None

    try:
        lexical_root = os.path.abspath(out_dir)
        lexical_path = os.path.abspath(os.path.join(lexical_root, relative))
        real_root = os.path.realpath(lexical_root)
        real_path = os.path.realpath(lexical_path)
    except (OSError, TypeError, ValueError):
        return None
    try:
        if (
            lexical_path == lexical_root
            or os.path.commonpath([lexical_root, lexical_path]) != lexical_root
        ):
            return None
    except ValueError:
        return None

    try:
        if (
            real_path == real_root
            or os.path.commonpath([real_root, real_path]) != real_root
        ):
            return None
    except ValueError:
        return None
    return lexical_path


def _safe_managed_name(relative):
    if (
        not isinstance(relative, str)
        or not relative
        or _is_reserved_managed_name(relative)
        or relative in (".", "..")
        or os.path.basename(relative) != relative
        or "\x00" in relative
    ):
        return None
    try:
        if os.path.isabs(relative):
            return None
    except (OSError, TypeError, ValueError):
        return None
    if os.altsep and os.altsep in relative:
        return None
    return relative


def _safe_managed_names(
    out_dir,
    managed_files,
    *,
    entry_limit=None,
    name_byte_limit=None,
):
    if isinstance(managed_files, str):
        entries = (managed_files,)
        known_entry_count = 1
    elif managed_files is None:
        entries = ()
        known_entry_count = 0
    else:
        try:
            entries = iter(managed_files)
        except TypeError:
            entries = ()
        known_entry_count = (
            len(managed_files)
            if type(managed_files)
            in (dict, list, tuple, set, frozenset)
            else None
        )

    lexical_root = os.path.abspath(_output_path(out_dir))
    safe = set()
    entries_inspected = 0
    retained_name_bytes = 0
    while True:
        if (
            entry_limit is not None
            and entries_inspected >= entry_limit
        ):
            if (
                known_entry_count is not None
                and entries_inspected >= known_entry_count
            ):
                break
            raise _SourceSidecarLimit(
                _source_sidecar_limit_record(
                    "source sidecar managed entry limit",
                    limit=entry_limit,
                    omitted_entries_lower_bound=(
                        known_entry_count
                        - entries_inspected
                        if known_entry_count is not None
                        else None
                    ),
                    iterator_exhaustion_unverified=(
                        known_entry_count is None
                    ),
                    managed_entries_inspected=entries_inspected,
                    managed_entries_retained=len(safe),
                    managed_name_bytes_retained=(
                        retained_name_bytes
                    ),
                )
            )
        try:
            relative = next(entries)
        except StopIteration:
            break
        entries_inspected += 1
        if not isinstance(relative, str):
            continue
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        normalized = os.path.relpath(path, lexical_root)
        if normalized in safe:
            continue
        name_bytes = len(
            normalized.encode("utf-8", errors="surrogatepass")
        )
        if (
            name_byte_limit is not None
            and retained_name_bytes + name_bytes
            > name_byte_limit
        ):
            raise _SourceSidecarLimit(
                _source_sidecar_limit_record(
                    "source sidecar managed name byte limit",
                    limit=name_byte_limit,
                    managed_entries_inspected=entries_inspected,
                    managed_entries_retained=len(safe),
                    managed_name_bytes_retained=(
                        retained_name_bytes
                    ),
                    requested_name_bytes=name_bytes,
                )
            )
        safe.add(normalized)
        retained_name_bytes += name_bytes
    return sorted(safe)


def _canonical_fingerprint(value):
    if (
        type(value) is not dict
        or set(value) != {"size", "sha256"}
        or type(value.get("size")) is not int
        or value["size"] < 0
        or type(value.get("sha256")) is not str
        or len(value["sha256"]) != 64
        or any(
            char not in "0123456789abcdef"
            for char in value["sha256"]
        )
    ):
        return None
    return {
        "size": value["size"],
        "sha256": value["sha256"],
    }


def _path_fingerprint(out_dir, relative):
    relative = _safe_managed_name(relative)
    if relative is None:
        return None
    if isinstance(out_dir, _PinnedOutputDirectory):
        try:
            state = _stable_managed_file(out_dir, relative)
        except (OSError, ValueError):
            return None
        return {"size": state.size, "sha256": state.sha256}
    path = os.path.join(_output_path(out_dir), relative)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError:
        return None
    try:
        opened = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            return None
        digest = _hash_exact_fd(fd, opened.st_size)
        final = os.fstat(fd)
        final_current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(final.st_mode)
            or not stat.S_ISREG(final_current.st_mode)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
            or final.st_ctime_ns != opened.st_ctime_ns
            or (final.st_dev, final.st_ino)
            != (opened.st_dev, opened.st_ino)
            or (final_current.st_dev, final_current.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            return None
        return {"size": opened.st_size, "sha256": digest}
    except OSError:
        return None
    finally:
        os.close(fd)


def _safe_managed_fingerprints(
    out_dir,
    managed_files,
    *,
    entry_limit,
    name_byte_limit,
):
    if type(managed_files) is dict:
        entries = iter(managed_files.items())
        known_entry_count = len(managed_files)
    else:
        names = _safe_managed_names(
            out_dir,
            managed_files,
            entry_limit=entry_limit,
            name_byte_limit=None,
        )
        entries = ((name, None) for name in names)
        known_entry_count = len(names)
    safe = {}
    entries_inspected = 0
    retained_name_bytes = 0
    while True:
        if entries_inspected >= entry_limit:
            if entries_inspected >= known_entry_count:
                break
            raise _SourceSidecarLimit(
                _source_sidecar_limit_record(
                    "source sidecar managed entry limit",
                    limit=entry_limit,
                    omitted_entries_lower_bound=(
                        known_entry_count - entries_inspected
                    ),
                    managed_entries_inspected=entries_inspected,
                    managed_entries_retained=len(safe),
                    managed_name_bytes_retained=retained_name_bytes,
                )
            )
        try:
            relative, supplied = next(entries)
        except StopIteration:
            break
        entries_inspected += 1
        normalized = _safe_managed_name(relative)
        if normalized is None or normalized in safe:
            continue
        fingerprint = (
            _canonical_fingerprint(supplied)
            if supplied is not None
            else _path_fingerprint(out_dir, normalized)
        )
        if fingerprint is None:
            continue
        requested_name_bytes = (
            len(
                normalized.encode(
                    "utf-8", errors="surrogatepass"
                )
            )
            + 64
        )
        if (
            retained_name_bytes + requested_name_bytes
            > name_byte_limit
        ):
            raise _SourceSidecarLimit(
                _source_sidecar_limit_record(
                    "source sidecar managed name byte limit",
                    limit=name_byte_limit,
                    managed_entries_inspected=entries_inspected,
                    managed_entries_retained=len(safe),
                    managed_name_bytes_retained=retained_name_bytes,
                    requested_name_bytes=requested_name_bytes,
                )
            )
        safe[normalized] = fingerprint
        retained_name_bytes += requested_name_bytes
    return {name: safe[name] for name in sorted(safe)}


class _SourceSidecarLimit(ValueError):
    def __init__(self, record):
        self.record = record
        super().__init__(record["reason"])


class _ManagedOutputNameLimit(ValueError):
    def __init__(
        self,
        reason,
        *,
        limit,
        field=None,
        observed_name_bytes_lower_bound=None,
        name="managed output",
        collision_probes=None,
    ):
        self.reason = reason
        self.limit = limit
        self.field = field
        self.observed_name_bytes_lower_bound = (
            observed_name_bytes_lower_bound
        )
        self.name = name
        self.collision_probes = collision_probes
        super().__init__(reason)

    def record(self, *, ownership_preserved=False):
        record = {
            "name": self.name,
            "reason": self.reason,
            "limit": self.limit,
        }
        if self.field is not None:
            record["field"] = self.field
        if self.observed_name_bytes_lower_bound is not None:
            record["observed_name_bytes_lower_bound"] = (
                self.observed_name_bytes_lower_bound
            )
        if self.collision_probes is not None:
            record["collision_probes"] = self.collision_probes
        if ownership_preserved:
            record["ownership_preserved"] = True
        return record


def _source_sidecar_limit_record(
    reason,
    *,
    limit,
    observed_bytes=None,
    observed_bytes_is_lower_bound=False,
    managed_entries_inspected=None,
    managed_entries_retained=None,
    managed_name_bytes_retained=None,
    requested_name_bytes=None,
    omitted_entries_lower_bound=1,
    iterator_exhaustion_unverified=False,
    retained_bytes=None,
    minimum_bytes_if_additional_entry=None,
    iterable_entries_retained=None,
    iterable_entries_remaining=None,
    ownership_preserved=False,
):
    record = {
        "name": SOURCE_SIDECAR,
        "reason": reason,
        "limit": limit,
    }
    if observed_bytes is not None:
        record["observed_bytes"] = observed_bytes
    if observed_bytes_is_lower_bound:
        record["observed_bytes_is_lower_bound"] = True
    if managed_entries_inspected is not None:
        record["managed_entries_inspected"] = (
            managed_entries_inspected
        )
    if managed_entries_retained is not None:
        record["managed_entries_retained"] = managed_entries_retained
    if managed_name_bytes_retained is not None:
        record["managed_name_bytes_retained"] = (
            managed_name_bytes_retained
        )
    if requested_name_bytes is not None:
        record["requested_name_bytes"] = requested_name_bytes
    if (
        reason != "source sidecar byte limit"
        and omitted_entries_lower_bound is not None
    ):
        record["omitted_entries_lower_bound"] = (
            omitted_entries_lower_bound
        )
    if iterator_exhaustion_unverified:
        record["iterator_exhaustion_unverified"] = True
    if retained_bytes is not None:
        record["retained_bytes"] = retained_bytes
    if minimum_bytes_if_additional_entry is not None:
        record["minimum_bytes_if_additional_entry"] = (
            minimum_bytes_if_additional_entry
        )
    if iterable_entries_retained is not None:
        record["iterable_entries_retained"] = (
            iterable_entries_retained
        )
    if iterable_entries_remaining is not None:
        record["iterable_entries_remaining"] = (
            iterable_entries_remaining
        )
    if ownership_preserved:
        record["ownership_preserved"] = True
    return record


def _read_source_sidecar(out_dir):
    path = os.path.join(_output_path(out_dir), SOURCE_SIDECAR)
    byte_limit = max(0, int(_SOURCE_SIDECAR_MAX_BYTES))
    entry_limit = max(0, int(_SOURCE_SIDECAR_ENTRY_LIMIT))
    name_byte_limit = max(
        0, int(_SOURCE_SIDECAR_NAME_BYTES)
    )
    def normalize_name(relative):
        return _safe_managed_name(relative)

    try:
        if isinstance(out_dir, _PinnedOutputDirectory):
            out_dir.verify()
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                raise _UnstableRegularFileError(
                    "no-follow sidecar opening is unavailable"
                )
            try:
                fd = os.open(
                    SOURCE_SIDECAR,
                    os.O_RDONLY | nofollow,
                    dir_fd=out_dir.fd,
                )
            except FileNotFoundError:
                return {}
            except (OSError, TypeError, NotImplementedError):
                return {}
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    return {}
                payload = bytearray()
                while len(payload) <= byte_limit:
                    chunk = os.read(
                        fd,
                        min(
                            65536,
                            byte_limit + 1 - len(payload),
                        ),
                    )
                    if not chunk:
                        break
                    payload.extend(chunk)
                if len(payload) > byte_limit:
                    raise SidecarLimitError(
                        "source sidecar byte limit",
                        observed_bytes=len(payload),
                        observed_bytes_is_lower_bound=True,
                    )
                final = os.fstat(fd)
                current = os.stat(
                    SOURCE_SIDECAR,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(final.st_mode)
                    or not stat.S_ISREG(current.st_mode)
                    or final.st_size != opened.st_size
                    or final.st_mtime_ns != opened.st_mtime_ns
                    or final.st_ctime_ns != opened.st_ctime_ns
                    or (final.st_dev, final.st_ino)
                    != (opened.st_dev, opened.st_ino)
                    or (current.st_dev, current.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    return {}
                out_dir.verify()
            finally:
                os.close(fd)
            data = parse_sidecar_bytes(
                bytes(payload),
                entry_limit=entry_limit,
                name_byte_limit=name_byte_limit,
                normalize_name=normalize_name,
            )
        else:
            data = read_sidecar(
                path,
                byte_limit=byte_limit,
                entry_limit=entry_limit,
                name_byte_limit=name_byte_limit,
                normalize_name=normalize_name,
            )
        return data if isinstance(data, dict) else {}
    except SidecarLimitError as error:
        limit = (
            byte_limit
            if error.reason == "source sidecar byte limit"
            else entry_limit
            if error.reason
            == "source sidecar managed entry limit"
            else name_byte_limit
        )
        raise _SourceSidecarLimit(
            _source_sidecar_limit_record(
                error.reason,
                limit=limit,
                ownership_preserved=True,
                **error.details,
            )
        ) from None


def _remove_managed_files(out_dir, managed_files):
    if type(managed_files) is not dict:
        return []
    if not isinstance(out_dir, _PinnedOutputDirectory):
        try:
            with _pinned_output_directory(out_dir) as output:
                return _remove_managed_files(
                    output,
                    managed_files,
                )
        except ValueError:
            return sorted(managed_files)
    failed = []
    for relative, expected in sorted(managed_files.items()):
        expected = _canonical_fingerprint(expected)
        if expected is None:
            continue
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        if isinstance(out_dir, _PinnedOutputDirectory):
            descriptor = -1
            try:
                authorized = _stable_managed_file(
                    out_dir,
                    relative,
                    expected=expected,
                )
                out_dir.verify()
                nofollow = getattr(os, "O_NOFOLLOW", None)
                if nofollow is None:
                    raise _UnstableRegularFileError(
                        "no-follow managed output cleanup is unavailable"
                    )
                descriptor = os.open(
                    relative,
                    os.O_RDONLY | nofollow,
                    dir_fd=out_dir.fd,
                )
                out_dir.verify()
                _unlink_owned_regular_entry(
                    out_dir.fd,
                    relative,
                    descriptor,
                    expected=authorized,
                )
            except FileNotFoundError:
                pass
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ):
                failed.append(relative)
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            continue
    return failed


def _stage_managed_file_cleanup(
    out_dir, managed_files, cleanup_journal
):
    failed = []
    absent = []
    if type(managed_files) is not dict:
        return failed, absent
    for relative, expected in sorted(managed_files.items()):
        expected = _canonical_fingerprint(expected)
        if expected is None:
            continue
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        if isinstance(out_dir, _PinnedOutputDirectory):
            try:
                _stable_managed_file(
                    out_dir,
                    relative,
                    expected=expected,
                )
            except (OSError, ValueError):
                absent.append(relative)
                continue
            try:
                staged = cleanup_journal.stage_removal(
                    path,
                    expected=expected,
                )
            except _ManagedOutputRecoveryRequiredError:
                raise
            except OSError:
                failed.append(relative)
                continue
            if not staged:
                failed.append(relative)
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            failed.append(relative)
            continue
        try:
            staged = cleanup_journal.stage_removal(path)
        except OSError:
            failed.append(relative)
            continue
        if not staged:
            absent.append(relative)
    return failed, absent


def _managed_output_text_bytes(value, *, field):
    limit = max(0, int(_MANAGED_OUTPUT_NAME_BYTES))
    total = 0
    for offset in range(0, len(value), 4096):
        total += len(
            value[offset:offset + 4096].encode(
                "utf-8", errors="surrogatepass"
            )
        )
        if total > limit:
            raise _ManagedOutputNameLimit(
                "managed output name byte limit",
                limit=limit,
                field=field,
                observed_name_bytes_lower_bound=limit + 1,
            )
    return total


def _managed_output_candidate(parts):
    limit = max(0, int(_MANAGED_OUTPUT_NAME_BYTES))
    total = 0
    for part in parts:
        total += _managed_output_text_bytes(
            part, field="candidate"
        )
        if total > limit:
            raise _ManagedOutputNameLimit(
                "managed output name byte limit",
                limit=limit,
                field="candidate",
                observed_name_bytes_lower_bound=limit + 1,
            )
    return "".join(parts)


def _managed_output_name(out_dir, base, source_name, reusable_names):
    reusable = reusable_names if reusable_names is not None else ()
    _managed_output_text_bytes(base, field="base")
    _managed_output_text_bytes(source_name, field="source")
    base = os.path.basename(base) or "download"
    if base in (".", ".."):
        base = "download"
    digest = hashlib.sha256(
        source_name.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    if _is_reserved_managed_name(base):
        _, suffix = os.path.splitext(base)
        base = _managed_output_candidate((
            "download--",
            digest[:10],
            suffix.lower(),
        ))

    probe_limit = max(
        0, int(_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT)
    )
    probes = 0

    def collision_limit():
        raise _ManagedOutputNameLimit(
            "managed output collision probe limit",
            limit=probe_limit,
            name=base,
            collision_probes=probes,
        )

    def available(name):
        nonlocal probes
        if _is_reserved_managed_name(name):
            return False
        if name in reusable:
            return True
        if probes >= probe_limit:
            collision_limit()
        probes += 1
        if isinstance(out_dir, _PinnedOutputDirectory):
            out_dir.verify()
            try:
                os.stat(
                    name,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return True
            except (OSError, TypeError, NotImplementedError):
                return False
            return False
        return not os.path.lexists(
            os.path.join(_output_path(out_dir), name)
        )

    if available(base):
        return base

    stem, suffix = os.path.splitext(base)
    for width in range(10, len(digest) + 1, 2):
        if probes >= probe_limit:
            collision_limit()
        candidate = _managed_output_candidate((
            stem,
            "--",
            digest[:width],
            suffix.lower(),
        ))
        if available(candidate):
            return candidate

    counter = 2
    while True:
        if probes >= probe_limit:
            collision_limit()
        candidate = _managed_output_candidate((
            stem,
            "--",
            digest,
            "-",
            str(counter),
            suffix.lower(),
        ))
        if available(candidate):
            return candidate
        counter += 1


def _archive_output_names(member_names):
    eligible = sorted(member_names)
    for member in eligible:
        _managed_output_text_bytes(member, field="source")
    counts = Counter(
        os.path.basename(name).casefold()
        for name in eligible
    )
    out = {}
    for member in eligible:
        base = os.path.basename(member)
        _managed_output_text_bytes(base, field="base")
        if counts[base.casefold()] == 1:
            out[member] = base
            continue
        stem, suffix = os.path.splitext(base)
        digest = hashlib.sha256(
            member.encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:10]
        out[member] = _managed_output_candidate((
            stem,
            "--",
            digest,
            suffix.lower(),
        ))
    return out


def _allocate_archive_output_names(preferred_names):
    used = set()
    allocated = []
    for preferred in preferred_names:
        _managed_output_text_bytes(
            preferred, field="candidate"
        )
        candidate = preferred
        stem, suffix = os.path.splitext(preferred)
        disambiguator = 2
        while candidate.casefold() in used:
            candidate = _managed_output_candidate((
                stem,
                "--",
                str(disambiguator),
                suffix,
            ))
            disambiguator += 1
        used.add(candidate.casefold())
        allocated.append(candidate)
    return allocated


def _archive_occurrence_output_names(member_names):
    bounded_names = []
    for member_name in member_names:
        _managed_output_text_bytes(
            member_name, field="source"
        )
        bounded_names.append(member_name)
    member_names = bounded_names
    output_names = _archive_output_names(member_names)
    totals = Counter(member_names)
    seen = Counter()
    out = []
    for member in member_names:
        name = output_names[member]
        if totals[member] == 1:
            out.append(name)
            continue
        seen[member] += 1
        stem, suffix = os.path.splitext(name)
        out.append(_managed_output_candidate((
            stem,
            "--",
            str(seen[member]),
            suffix,
        )))
    return _allocate_archive_output_names(out)


def _sanitize_zip_filename(filename):
    filename = filename.split("\0", 1)[0]
    if os.sep != "/" and os.sep in filename:
        filename = filename.replace(os.sep, "/")
    if os.altsep and os.altsep != "/" and os.altsep in filename:
        filename = filename.replace(os.altsep, "/")
    return filename


def _zip_extra_uint64(data, offset, field):
    if len(data) < offset + 8:
        raise zipfile.BadZipFile(
            f"Corrupt zip64 extra field. {field} not found."
        )
    return struct.unpack_from("<Q", data, offset)[0], offset + 8


def _decode_zip_extra(info, raw_filename):
    extra = info.extra
    offset = 0
    while len(extra) - offset >= 4:
        field_type, field_size = struct.unpack_from(
            "<HH", extra, offset
        )
        data_start = offset + 4
        data_end = data_start + field_size
        if data_end > len(extra):
            raise zipfile.BadZipFile(
                "Corrupt extra field %04x (size=%d)"
                % (field_type, field_size)
            )
        data = memoryview(extra)[data_start:data_end]
        if field_type == _ZIP64_EXTRA_FIELD:
            cursor = 0
            if info.file_size in (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF):
                info.file_size, cursor = _zip_extra_uint64(
                    data, cursor, "File size"
                )
            if info.compress_size == 0xFFFFFFFF:
                info.compress_size, cursor = _zip_extra_uint64(
                    data, cursor, "Compress size"
                )
            if info.header_offset == 0xFFFFFFFF:
                info.header_offset, cursor = _zip_extra_uint64(
                    data, cursor, "Header offset"
                )
        elif field_type == _ZIP_UNICODE_PATH_EXTRA_FIELD:
            if len(data) < 5:
                raise zipfile.BadZipFile(
                    "Corrupt unicode path extra field (0x7075)"
                )
            version, name_crc = struct.unpack_from("<BL", data, 0)
            if (
                version == 1
                and name_crc == zlib.crc32(raw_filename)
            ):
                try:
                    unicode_name = bytes(data[5:]).decode("utf-8")
                except UnicodeDecodeError as error:
                    raise zipfile.BadZipFile(
                        "Corrupt unicode path extra field (0x7075): "
                        "invalid utf-8 bytes"
                    ) from error
                if unicode_name:
                    info.filename = _sanitize_zip_filename(
                        unicode_name
                    )
                else:
                    import warnings

                    warnings.warn(
                        "Empty unicode path extra field (0x7075)",
                        stacklevel=2,
                    )
        offset = data_end


class _TarArchiveLimit(ValueError):
    def __init__(
        self,
        reason,
        state,
        *,
        requested_metadata_bytes=None,
        details=None,
    ):
        self.reason = reason
        self.state = dict(state)
        self.requested_metadata_bytes = requested_metadata_bytes
        self.details = dict(details or {})
        super().__init__(reason)

    def record(self, archive_name):
        state = self.state
        record = {
            "name": archive_name,
            "reason": self.reason,
            "limit": (
                state["member_limit"]
                if self.reason == "archive member count limit"
                else state["name_byte_limit"]
                if self.reason == "archive member name byte limit"
                else state["sparse_entry_limit"]
                if self.reason == "archive sparse entry limit"
                else state["traversal_byte_limit"]
                if self.reason
                == "archive decompressed traversal limit"
                else state["metadata_byte_limit"]
            ),
            "members_inspected": state["members_inspected"],
            "eligible_members_retained": state[
                "eligible_members_retained"
            ],
            "retained_members": state[
                "eligible_members_retained"
            ],
        }
        if self.reason == "archive member name byte limit":
            record["retained_name_bytes"] = state[
                "retained_name_bytes"
            ]
            if state["metadata_bytes_processed"]:
                record["metadata_bytes_processed"] = state[
                    "metadata_bytes_processed"
                ]
        elif self.reason == "archive metadata byte limit":
            record["metadata_bytes_processed"] = state[
                "metadata_bytes_processed"
            ]
            record["requested_metadata_bytes"] = (
                self.requested_metadata_bytes
            )
        elif self.reason == "archive sparse entry limit":
            record.update({
                "metadata_byte_limit": state[
                    "metadata_byte_limit"
                ],
                "metadata_bytes_processed": state[
                    "metadata_bytes_processed"
                ],
                "sparse_entries_retained": state[
                    "sparse_entries_retained"
                ],
                "sparse_extension_blocks_processed": state[
                    "sparse_extension_blocks_processed"
                ],
                "sparse_fields_processed": state[
                    "sparse_fields_processed"
                ],
            })
            record.update(self.details)
        elif self.reason == "archive decompressed traversal limit":
            record["decompressed_bytes_traversed"] = state[
                "decompressed_bytes_traversed"
            ]
            record.update(self.details)
        record.setdefault("omitted_members_lower_bound", 1)
        return record


class _TarTraversalFile:
    def __init__(self, inner, state):
        self._inner = inner
        self._state = state

    def _raise_limit(self, requested_bytes):
        raise _TarArchiveLimit(
            "archive decompressed traversal limit",
            self._state,
            details={
                "requested_traversal_bytes": requested_bytes,
                "omitted_members_lower_bound": 0,
            },
        )

    def _check_work(self, requested_bytes):
        if (
            self._state["decompressed_bytes_traversed"]
            + requested_bytes
            > self._state["traversal_byte_limit"]
        ):
            self._raise_limit(requested_bytes)

    def _record_work(self, traversed_bytes):
        self._state["decompressed_bytes_traversed"] += (
            traversed_bytes
        )

    def read(self, size=-1):
        current = self._inner.tell()
        if size is None or size < 0:
            self._raise_limit(
                self._state["traversal_byte_limit"] - current + 1
            )
        self._check_work(size)
        data = self._inner.read(size)
        self._record_work(len(data))
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def seek(self, offset, whence=os.SEEK_SET):
        current = self._inner.tell()
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = current + offset
        else:
            self._raise_limit(
                self._state["traversal_byte_limit"] - current + 1
            )
        if target > current:
            traversed = target - current
        elif target < current:
            traversed = max(0, target)
        else:
            traversed = 0
        self._check_work(traversed)
        result = self._inner.seek(offset, whence)
        if target > current:
            actual_traversed = max(0, result - current)
        elif target < current:
            actual_traversed = max(0, result)
        else:
            actual_traversed = 0
        self._record_work(actual_traversed)
        return result

    def tell(self):
        return self._inner.tell()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _tar_state(archive):
    return archive._paperconan_budget_state


def _raise_tar_limit(
    archive,
    reason,
    *,
    requested_metadata_bytes=None,
    details=None,
):
    raise _TarArchiveLimit(
        reason,
        _tar_state(archive),
        requested_metadata_bytes=requested_metadata_bytes,
        details=details,
    )


def _check_tar_extension_budget(info, archive):
    state = _tar_state(archive)
    if state["members_inspected"] >= state["member_limit"]:
        _raise_tar_limit(archive, "archive member count limit")
    state["members_inspected"] += 1
    if state["members_inspected"] >= state["member_limit"]:
        _raise_tar_limit(archive, "archive member count limit")
    metadata_size = max(0, int(info.size))
    if (
        state["metadata_bytes_processed"] + metadata_size
        > state["metadata_byte_limit"]
    ):
        _raise_tar_limit(
            archive,
            "archive metadata byte limit",
            requested_metadata_bytes=metadata_size,
        )
    return metadata_size


def _read_tar_extension_payload(info, archive, metadata_size):
    payload = archive.fileobj.read(info._block(info.size))
    _tar_state(archive)["metadata_bytes_processed"] += metadata_size
    return payload


class _RawTarText:
    __slots__ = (
        "raw",
        "encoding",
        "fallback_encoding",
        "fallback_errors",
    )

    def __init__(
        self,
        raw,
        encoding,
        fallback_encoding,
        fallback_errors,
    ):
        self.raw = raw
        self.encoding = encoding
        self.fallback_encoding = fallback_encoding
        self.fallback_errors = fallback_errors

    def decode(self):
        try:
            return self.raw.decode(self.encoding, "strict")
        except UnicodeDecodeError:
            return self.raw.decode(
                self.fallback_encoding,
                self.fallback_errors,
            )

    def utf8_length(self, *, trim_slashes=False, remove_slash=False):
        try:
            return _decoded_tar_text_utf8_length(
                self.raw,
                self.encoding,
                "strict",
                trim_slashes=trim_slashes,
                remove_slash=remove_slash,
            )
        except UnicodeDecodeError:
            return _decoded_tar_text_utf8_length(
                self.raw,
                self.fallback_encoding,
                self.fallback_errors,
                trim_slashes=trim_slashes,
                remove_slash=remove_slash,
            )


def _decoded_tar_text_utf8_length(
    raw,
    encoding,
    errors,
    *,
    trim_slashes,
    remove_slash,
):
    decoder = codecs.getincrementaldecoder(encoding)(errors)
    total = 0
    trailing_slashes = 0
    for offset in range(0, len(raw), 65536):
        chunk = decoder.decode(raw[offset:offset + 65536], final=False)
        total += len(chunk.encode("utf-8", errors="surrogatepass"))
        if chunk:
            if chunk.endswith("/"):
                trailing_slashes += len(chunk) - len(chunk.rstrip("/"))
            else:
                trailing_slashes = 0
    chunk = decoder.decode(b"", final=True)
    total += len(chunk.encode("utf-8", errors="surrogatepass"))
    if chunk:
        if chunk.endswith("/"):
            trailing_slashes += len(chunk) - len(chunk.rstrip("/"))
        else:
            trailing_slashes = 0
    if trim_slashes:
        return total - trailing_slashes
    if remove_slash and trailing_slashes:
        return total - 1
    return total


def _decode_tar_text(
    raw,
    encoding,
    fallback_encoding,
    fallback_errors,
):
    try:
        return raw.decode(encoding, "strict")
    except UnicodeDecodeError:
        return raw.decode(fallback_encoding, fallback_errors)


def _iter_pax_raw_records(payload):
    position = 0
    while position < len(payload) and payload[position] != 0:
        space = payload.find(b" ", position)
        if space < 0:
            raise tarfile.InvalidHeaderError("invalid header")
        try:
            length = int(payload[position:space])
        except ValueError:
            raise tarfile.InvalidHeaderError(
                "invalid header"
            ) from None
        if length < 5:
            raise tarfile.InvalidHeaderError("invalid header")
        record_end = position + length
        if record_end > len(payload):
            raise tarfile.InvalidHeaderError("invalid header")
        value_end = record_end - 1
        field = payload[space + 1:value_end]
        raw_keyword, equals, raw_value = field.partition(b"=")
        if (
            not raw_keyword
            or equals != b"="
            or payload[value_end] != 0x0A
        ):
            raise tarfile.InvalidHeaderError("invalid header")
        yield length, raw_keyword, raw_value
        position = record_end


def _parse_pax_records(payload, archive):
    name_encoding = None
    for _length, raw_keyword, raw_value in _iter_pax_raw_records(
        payload
    ):
        if raw_keyword == b"hdrcharset" and name_encoding is None:
            name_encoding = (
                archive.encoding
                if raw_value == b"BINARY"
                else "utf-8"
            )
    if name_encoding is None:
        name_encoding = "utf-8"

    updates = {}
    for _length, raw_keyword, raw_value in _iter_pax_raw_records(
        payload
    ):
        keyword = _decode_tar_text(
            raw_keyword,
            "utf-8",
            "utf-8",
            archive.errors,
        )
        if keyword in tarfile.PAX_NAME_FIELDS:
            value = _RawTarText(
                raw_value,
                name_encoding,
                archive.encoding,
                archive.errors,
            )
        elif keyword == "GNU.sparse.name":
            value = _RawTarText(
                raw_value,
                "utf-8",
                "utf-8",
                archive.errors,
            )
        else:
            value = _decode_tar_text(
                raw_value,
                "utf-8",
                "utf-8",
                archive.errors,
            )
        updates[keyword] = value
    return updates


def _sparse_budget_remaining(archive):
    state = _tar_state(archive)
    return max(
        0,
        state["sparse_entry_limit"]
        - state["sparse_entries_retained"],
    )


def _raise_sparse_limit(
    archive,
    *,
    omitted_sparse_entries=None,
    omitted_sparse_entries_lower_bound=None,
    requested_sparse_entries=None,
):
    details = {}
    if omitted_sparse_entries is not None:
        details["omitted_sparse_entries"] = (
            omitted_sparse_entries
        )
    if omitted_sparse_entries_lower_bound is not None:
        details["omitted_sparse_entries_lower_bound"] = (
            omitted_sparse_entries_lower_bound
        )
    if requested_sparse_entries is not None:
        details["requested_sparse_entries"] = (
            requested_sparse_entries
        )
    _raise_tar_limit(
        archive,
        "archive sparse entry limit",
        details=details,
    )


def _retain_sparse_entry(archive, entries, entry):
    state = _tar_state(archive)
    if state["sparse_entries_retained"] >= state[
        "sparse_entry_limit"
    ]:
        _raise_sparse_limit(
            archive,
            omitted_sparse_entries_lower_bound=1,
        )
    entries.append(entry)
    state["sparse_entries_retained"] += 1


def _read_sparse_metadata_block(archive):
    state = _tar_state(archive)
    block_size = tarfile.BLOCKSIZE
    if (
        state["metadata_bytes_processed"] + block_size
        > state["metadata_byte_limit"]
    ):
        _raise_tar_limit(
            archive,
            "archive metadata byte limit",
            requested_metadata_bytes=block_size,
        )
    block = archive.fileobj.read(block_size)
    state["metadata_bytes_processed"] += len(block)
    state["sparse_extension_blocks_processed"] += 1
    return block


class _SparseLineReader:
    def __init__(self, archive):
        self.archive = archive
        self.buffer = bytearray()
        self.position = 0
        self.search_position = 0

    def _record_work(self, byte_count):
        state = _tar_state(self.archive)
        state["sparse_line_bytes_processed"] = (
            state.get("sparse_line_bytes_processed", 0)
            + byte_count
        )

    def _append_block(self):
        block = _read_sparse_metadata_block(self.archive)
        if not block:
            raise tarfile.InvalidHeaderError("invalid header")
        self.buffer.extend(block)
        self._record_work(len(block))

    def readline(self):
        if self.position >= len(self.buffer):
            self._append_block()
        while True:
            newline = self.buffer.find(
                b"\n", self.search_position
            )
            if newline >= 0:
                self._record_work(
                    newline - self.search_position + 1
                )
                line = bytes(
                    self.buffer[self.position:newline]
                )
                self._record_work(len(line))
                self.position = newline + 1
                self.search_position = self.position
                return line
            self._record_work(
                len(self.buffer) - self.search_position
            )
            self.search_position = len(self.buffer)
            self._append_block()


def _parse_pax_sparse_00(payload, archive):
    offset_count = 0
    numbytes_count = 0
    for _length, keyword, _value in _iter_pax_raw_records(
        payload
    ):
        if keyword == b"GNU.sparse.offset":
            offset_count += 1
        elif keyword == b"GNU.sparse.numbytes":
            numbytes_count += 1
    entry_count = min(offset_count, numbytes_count)
    remaining = _sparse_budget_remaining(archive)
    if entry_count > remaining:
        _raise_sparse_limit(
            archive,
            omitted_sparse_entries=entry_count,
            requested_sparse_entries=entry_count,
        )

    offsets = []
    numbytes = []
    state = _tar_state(archive)
    for _length, keyword, value in _iter_pax_raw_records(
        payload
    ):
        if keyword not in (
            b"GNU.sparse.offset",
            b"GNU.sparse.numbytes",
        ):
            continue
        target = (
            offsets
            if keyword == b"GNU.sparse.offset"
            else numbytes
        )
        if len(target) >= entry_count:
            continue
        state["sparse_fields_processed"] += 1
        try:
            number = int(value.decode())
        except ValueError:
            raise tarfile.InvalidHeaderError(
                "invalid header"
            ) from None
        target.append(number)
    entries = list(zip(offsets, numbytes))
    state["sparse_entries_retained"] += len(entries)
    return entries


def _parse_pax_sparse_01(value, archive):
    token_count = value.count(",") + 1
    entry_count = token_count // 2
    remaining = _sparse_budget_remaining(archive)
    if entry_count > remaining:
        _raise_sparse_limit(
            archive,
            omitted_sparse_entries=entry_count,
            requested_sparse_entries=entry_count,
        )

    entries = []
    pending = None
    position = 0
    state = _tar_state(archive)
    values_needed = entry_count * 2
    values_read = 0
    while values_read < values_needed:
        comma = value.find(",", position)
        if comma < 0:
            comma = len(value)
        token = value[position:comma]
        try:
            number = int(token)
        except ValueError:
            raise tarfile.InvalidHeaderError(
                "invalid header"
            ) from None
        state["sparse_fields_processed"] += 1
        values_read += 1
        if pending is None:
            pending = number
        else:
            _retain_sparse_entry(
                archive, entries, (pending, number)
            )
            pending = None
        position = comma + 1
    return entries


def _parse_pax_sparse_10(next_info, archive):
    lines = _SparseLineReader(archive)
    fields_raw = lines.readline()
    try:
        fields = int(fields_raw)
    except (ValueError, TypeError):
        raise tarfile.InvalidHeaderError(
            "invalid header"
        ) from None
    if fields < 0:
        raise tarfile.InvalidHeaderError("invalid header")
    state = _tar_state(archive)
    state["sparse_fields_processed"] += 1
    remaining = _sparse_budget_remaining(archive)
    if fields > remaining:
        _raise_sparse_limit(
            archive,
            omitted_sparse_entries=fields,
            requested_sparse_entries=fields,
        )

    entries = []
    pending = None
    values_needed = fields * 2
    values_read = 0
    while values_read < values_needed:
        number_raw = lines.readline()
        try:
            number = int(number_raw)
        except ValueError:
            raise tarfile.InvalidHeaderError(
                "invalid header"
            ) from None
        state["sparse_fields_processed"] += 1
        values_read += 1
        if pending is None:
            pending = number
        else:
            _retain_sparse_entry(
                archive, entries, (pending, number)
            )
            pending = None
    next_info.offset_data = archive.fileobj.tell()
    next_info.sparse = entries


def _decoded_pax_metadata(pax_headers):
    return {
        keyword: (
            value.decode()
            if isinstance(value, _RawTarText)
            else value
        )
        for keyword, value in pax_headers.items()
        if keyword not in ("path", "GNU.sparse.name")
    }


def _decoded_pax_headers(
    pax_headers,
    archive,
    *,
    effective_name_field,
    effective_name_value,
):
    state = _tar_state(archive)
    remaining_name_bytes = (
        state["name_byte_limit"]
        - state["retained_name_bytes"]
    )
    decoded = {}
    for keyword, value in pax_headers.items():
        if not isinstance(value, _RawTarText):
            decoded[keyword] = value
        elif keyword == effective_name_field:
            decoded[keyword] = effective_name_value
        elif (
            keyword in ("path", "GNU.sparse.name")
            and _tar_name_utf8_length(value, None)
            > remaining_name_bytes
        ):
            decoded[keyword] = value
        else:
            decoded[keyword] = value.decode()
    return decoded


def _tar_name_utf8_length(value, transform):
    if isinstance(value, _RawTarText):
        return value.utf8_length(
            trim_slashes=transform == "rstrip",
            remove_slash=transform == "removesuffix",
        )
    if transform == "rstrip":
        value = value.rstrip("/")
    elif transform == "removesuffix":
        value = value.removesuffix("/")
    return len(value.encode("utf-8", errors="surrogatepass"))


def _decode_tar_name(value, transform):
    if isinstance(value, _RawTarText):
        value = value.decode()
    if transform == "rstrip":
        return value.rstrip("/")
    if transform == "removesuffix":
        return value.removesuffix("/")
    return value


def _preflight_tar_name(archive, value, transform):
    state = _tar_state(archive)
    remaining = (
        state["name_byte_limit"]
        - state["retained_name_bytes"]
    )
    if _tar_name_utf8_length(value, transform) > remaining:
        _raise_tar_limit(
            archive, "archive member name byte limit"
        )


def _apply_bounded_pax_info(
    info,
    pax_headers,
    archive,
    *,
    final_name_override=None,
    final_name_transform=None,
):
    effective_name = None
    effective_name_field = None
    effective_transform = None
    if hasattr(info, "_paperconan_pending_name"):
        effective_name = info._paperconan_pending_name
        effective_name_field = "pending"
        effective_transform = (
            info._paperconan_pending_name_transform
        )
    for keyword, value in pax_headers.items():
        if keyword == "GNU.sparse.name":
            effective_name = value
            effective_name_field = keyword
            effective_transform = None
        elif keyword == "path":
            effective_name = value
            effective_name_field = keyword
            effective_transform = "rstrip"
    if final_name_override is not None:
        effective_name = final_name_override
        effective_name_field = "final"
        effective_transform = final_name_transform
    decoded_name = None
    decoded_name_value = None
    if effective_name is not None:
        _preflight_tar_name(
            archive, effective_name, effective_transform
        )
        decoded_name_value = _decode_tar_name(
            effective_name, None
        )
        decoded_name = _decode_tar_name(
            decoded_name_value, effective_transform
        )

    if hasattr(info, "_paperconan_pending_name"):
        del info._paperconan_pending_name
        del info._paperconan_pending_name_transform

    decoded_headers = _decoded_pax_headers(
        pax_headers,
        archive,
        effective_name_field=effective_name_field,
        effective_name_value=decoded_name_value,
    )
    for keyword, value in decoded_headers.items():
        if (
            keyword in ("path", "GNU.sparse.name")
            and (
                keyword != effective_name_field
                or isinstance(value, _RawTarText)
            )
        ):
            continue
        if keyword == "GNU.sparse.name":
            info.path = value
        elif keyword == "GNU.sparse.size":
            info.size = int(value)
        elif keyword == "GNU.sparse.realsize":
            info.size = int(value)
        elif keyword in tarfile.PAX_FIELDS:
            if keyword in tarfile.PAX_NUMBER_FIELDS:
                try:
                    value = tarfile.PAX_NUMBER_FIELDS[keyword](value)
                except ValueError:
                    value = 0
            if keyword == "path":
                value = value.rstrip("/")
            setattr(info, keyword, value)
    if effective_name_field in ("pending", "final"):
        info.name = decoded_name
    info.pax_headers = decoded_headers.copy()


class _BoundedTarInfo(tarfile.TarInfo):
    def _proc_sparse(self, archive):
        structs, isextended, origsize = self._sparse_structs
        del self._sparse_structs
        state = _tar_state(archive)
        remaining = _sparse_budget_remaining(archive)
        if len(structs) > remaining:
            retained = structs[:remaining]
            state["sparse_entries_retained"] += len(retained)
            omitted = len(structs) - len(retained)
            details = {
                "requested_sparse_entries": len(structs),
            }
            if isextended:
                details[
                    "omitted_sparse_entries_lower_bound"
                ] = omitted
            else:
                details["omitted_sparse_entries"] = omitted
            _raise_sparse_limit(archive, **details)
        state["sparse_entries_retained"] += len(structs)

        while isextended:
            block = _read_sparse_metadata_block(archive)
            if len(block) < tarfile.BLOCKSIZE:
                raise tarfile.InvalidHeaderError(
                    "invalid header"
                )
            position = 0
            for _index in range(21):
                try:
                    offset = tarfile.nti(
                        block[position:position + 12]
                    )
                    numbytes = tarfile.nti(
                        block[position + 12:position + 24]
                    )
                except ValueError:
                    break
                state["sparse_fields_processed"] += 2
                if offset and numbytes:
                    _retain_sparse_entry(
                        archive,
                        structs,
                        (offset, numbytes),
                    )
                position += 24
            isextended = bool(block[504])
        self.sparse = structs
        self.offset_data = archive.fileobj.tell()
        archive.offset = (
            self.offset_data + self._block(self.size)
        )
        self.size = origsize
        return self

    def _proc_builtin(self, archive):
        self.offset_data = archive.fileobj.tell()
        offset = self.offset_data
        if self.isreg() or self.type not in tarfile.SUPPORTED_TYPES:
            offset += self._block(self.size)
        archive.offset = offset
        state = _tar_state(archive)
        if not state["suppress_global_pax"]:
            _apply_bounded_pax_info(
                self,
                state["global_pax_headers"],
                archive,
            )
        if self.isdir():
            self.name = self.name.rstrip("/")
        return self

    def _proc_pax(self, archive):
        metadata_size = _check_tar_extension_budget(self, archive)
        payload = _read_tar_extension_payload(
            self, archive, metadata_size
        )
        state = _tar_state(archive)
        updates = _parse_pax_records(payload, archive)
        if self.type == tarfile.XGLTYPE:
            pax_headers = state["global_pax_headers"]
            pax_headers.update(updates)
        else:
            pax_headers = state["global_pax_headers"].copy()
            pax_headers.update(updates)

        if self.type in (
            tarfile.XHDTYPE,
            tarfile.SOLARIS_XHDTYPE,
        ):
            state["suppress_global_pax"] += 1
        try:
            next_info = self.fromtarfile(archive)
        except tarfile.HeaderError as error:
            raise tarfile.SubsequentHeaderError(
                str(error)
            ) from None
        finally:
            if self.type in (
                tarfile.XHDTYPE,
                tarfile.SOLARIS_XHDTYPE,
            ):
                state["suppress_global_pax"] -= 1

        decoded_headers = _decoded_pax_metadata(pax_headers)
        if "GNU.sparse.map" in decoded_headers:
            next_info.sparse = _parse_pax_sparse_01(
                decoded_headers["GNU.sparse.map"],
                archive,
            )
        elif "GNU.sparse.size" in decoded_headers:
            next_info.sparse = _parse_pax_sparse_00(
                payload, archive
            )
        elif (
            decoded_headers.get("GNU.sparse.major") == "1"
            and decoded_headers.get("GNU.sparse.minor") == "0"
        ):
            _parse_pax_sparse_10(next_info, archive)

        if self.type in (
            tarfile.XHDTYPE,
            tarfile.SOLARIS_XHDTYPE,
        ):
            _apply_bounded_pax_info(
                next_info, pax_headers, archive
            )
            next_info.offset = self.offset
            if "size" in decoded_headers:
                offset = next_info.offset_data
                if (
                    next_info.isreg()
                    or next_info.type
                    not in tarfile.SUPPORTED_TYPES
                ):
                    offset += next_info._block(next_info.size)
                archive.offset = offset
        return next_info

    def _proc_gnulong(self, archive):
        metadata_size = _check_tar_extension_budget(self, archive)
        payload = _read_tar_extension_payload(
            self, archive, metadata_size
        )
        nul = payload.find(b"\0")
        raw_value = payload if nul < 0 else payload[:nul]
        value = _RawTarText(
            raw_value,
            archive.encoding,
            archive.encoding,
            archive.errors,
        )
        state = _tar_state(archive)
        already_suppressed = bool(state["suppress_global_pax"])
        state["suppress_global_pax"] += 1
        try:
            next_info = self.fromtarfile(archive)
        except tarfile.HeaderError as error:
            raise tarfile.SubsequentHeaderError(
                str(error)
            ) from None
        finally:
            state["suppress_global_pax"] -= 1

        next_info.offset = self.offset
        if self.type == tarfile.GNUTYPE_LONGNAME:
            transform = (
                "removesuffix" if next_info.isdir() else None
            )
            if already_suppressed:
                next_info._paperconan_pending_name = value
                next_info._paperconan_pending_name_transform = (
                    transform
                )
            else:
                _apply_bounded_pax_info(
                    next_info,
                    state["global_pax_headers"],
                    archive,
                    final_name_override=value,
                    final_name_transform=transform,
                )
        else:
            if not already_suppressed:
                _apply_bounded_pax_info(
                    next_info,
                    state["global_pax_headers"],
                    archive,
                )
            next_info.linkname = value.decode()
        return next_info


def _tar_has_unprocessed_header(archive):
    fileobj = archive.fileobj
    fileobj.seek(archive.offset)
    marker = fileobj.read(1)
    return bool(marker and marker != b"\0")


class _BoundedTarFile(tarfile.TarFile):
    def __init__(
        self,
        name=None,
        mode="r",
        fileobj=None,
        *args,
        include_images=False,
        **kwargs,
    ):
        self._paperconan_budget_state = {
            "member_limit": max(0, int(_ARCHIVE_MEMBER_LIMIT)),
            "name_byte_limit": max(
                0, int(_ARCHIVE_MEMBER_NAME_BYTES)
            ),
            "metadata_byte_limit": max(
                0, int(_ARCHIVE_METADATA_BYTES)
            ),
            "sparse_entry_limit": max(
                0, int(_ARCHIVE_SPARSE_ENTRY_LIMIT)
            ),
            "traversal_byte_limit": max(
                0, int(_ARCHIVE_TAR_TRAVERSAL_BYTES)
            ),
            "members_inspected": 0,
            "eligible_members_retained": 0,
            "retained_name_bytes": 0,
            "metadata_bytes_processed": 0,
            "sparse_entries_retained": 0,
            "sparse_extension_blocks_processed": 0,
            "sparse_fields_processed": 0,
            "sparse_line_bytes_processed": 0,
            "decompressed_bytes_traversed": 0,
            "global_pax_headers": {},
            "suppress_global_pax": 0,
            "include_images": bool(include_images),
        }
        if mode == "r" and fileobj is not None:
            fileobj = _TarTraversalFile(
                fileobj, self._paperconan_budget_state
            )
        super().__init__(
            name,
            mode,
            fileobj,
            *args,
            **kwargs,
        )

    def next(self):
        had_first_member = (
            getattr(self, "firstmember", None) is not None
        )
        state = _tar_state(self)
        if (
            not had_first_member
            and state["members_inspected"] >= state["member_limit"]
        ):
            if _tar_has_unprocessed_header(self):
                _raise_tar_limit(
                    self, "archive member count limit"
                )
            self._loaded = True
            return None

        member = super().next()
        if member is None or had_first_member:
            return member
        self.members.clear()
        state["members_inspected"] += 1
        name_bytes = len(
            member.name.encode("utf-8", errors="surrogatepass")
        )
        if (
            state["retained_name_bytes"] + name_bytes
            > state["name_byte_limit"]
        ):
            _raise_tar_limit(
                self, "archive member name byte limit"
            )
        state["retained_name_bytes"] += name_bytes
        if (
            member.isfile()
            and _is_selected_archive_member(
                member.name,
                include_images=state["include_images"],
            )
        ):
            state["eligible_members_retained"] += 1
        return member


class _BoundedZipFile(zipfile.ZipFile):
    """Read only budgeted central-directory metadata into ZipFile state."""

    def __init__(
        self,
        file,
        *,
        archive_name,
        include_images=False,
    ):
        self.archive_name = archive_name
        self.include_images = bool(include_images)
        self.selection_skipped = []
        self._member_limit = max(0, int(_ARCHIVE_MEMBER_LIMIT))
        self._name_byte_limit = max(
            0, int(_ARCHIVE_MEMBER_NAME_BYTES)
        )
        super().__init__(file, mode="r")

    def _RealGetContents(self):
        fp = self.fp
        try:
            endrec = zipfile._EndRecData(fp)
        except OSError:
            raise zipfile.BadZipFile("File is not a zip file")
        if not endrec:
            raise zipfile.BadZipFile("File is not a zip file")

        size_cd = endrec[zipfile._ECD_SIZE]
        offset_cd = endrec[zipfile._ECD_OFFSET]
        self._comment = endrec[zipfile._ECD_COMMENT]
        concat = (
            endrec[zipfile._ECD_LOCATION] - size_cd - offset_cd
        )
        self.start_dir = offset_cd + concat
        if self.start_dir < 0:
            raise zipfile.BadZipFile(
                "Bad offset for central directory"
            )

        fp.seek(self.start_dir)
        total = 0
        members_inspected = 0
        eligible_members_retained = 0
        retained_name_bytes = 0
        header_offsets = []
        while total < size_cd:
            if members_inspected >= self._member_limit:
                self.selection_skipped.append({
                    "name": self.archive_name,
                    "reason": "archive member count limit",
                    "limit": self._member_limit,
                    "members_inspected": members_inspected,
                    "eligible_members_retained": (
                        eligible_members_retained
                    ),
                    "retained_members": eligible_members_retained,
                    "omitted_members_lower_bound": 1,
                })
                break

            fixed = fp.read(zipfile.sizeCentralDir)
            if len(fixed) != zipfile.sizeCentralDir:
                raise zipfile.BadZipFile(
                    "Truncated central directory"
                )
            centdir = struct.unpack(
                zipfile.structCentralDir, fixed
            )
            if (
                centdir[zipfile._CD_SIGNATURE]
                != zipfile.stringCentralDir
            ):
                raise zipfile.BadZipFile(
                    "Bad magic number for central directory"
                )

            filename_length = centdir[
                zipfile._CD_FILENAME_LENGTH
            ]
            extra_length = centdir[
                zipfile._CD_EXTRA_FIELD_LENGTH
            ]
            comment_length = centdir[
                zipfile._CD_COMMENT_LENGTH
            ]
            entry_size = (
                zipfile.sizeCentralDir
                + filename_length
                + extra_length
                + comment_length
            )
            if total + entry_size > size_cd:
                raise zipfile.BadZipFile(
                    "Truncated central directory"
                )

            raw_filename = fp.read(filename_length)
            if len(raw_filename) != filename_length:
                raise zipfile.BadZipFile(
                    "Truncated central directory"
                )
            flags = centdir[zipfile._CD_FLAG_BITS]
            if flags & _ZIP_UTF8_FILENAME_FLAG:
                filename = raw_filename.decode("utf-8")
            else:
                filename = raw_filename.decode(
                    getattr(self, "metadata_encoding", None)
                    or "cp437"
                )

            extra = fp.read(extra_length)
            if len(extra) != extra_length:
                raise zipfile.BadZipFile(
                    "Truncated central directory"
                )
            fp.seek(comment_length, 1)

            info = zipfile.ZipInfo(filename)
            info.extra = extra
            info.header_offset = centdir[
                zipfile._CD_LOCAL_HEADER_OFFSET
            ]
            (
                info.create_version,
                info.create_system,
                info.extract_version,
                info.reserved,
                info.flag_bits,
                info.compress_type,
                raw_time,
                raw_date,
                info.CRC,
                info.compress_size,
                info.file_size,
            ) = centdir[1:12]
            if info.extract_version > zipfile.MAX_EXTRACT_VERSION:
                raise NotImplementedError(
                    "zip file version %.1f"
                    % (info.extract_version / 10)
                )
            (
                info.volume,
                info.internal_attr,
                info.external_attr,
            ) = centdir[15:18]
            info._raw_time = raw_time
            info.date_time = (
                (raw_date >> 9) + 1980,
                (raw_date >> 5) & 0xF,
                raw_date & 0x1F,
                raw_time >> 11,
                (raw_time >> 5) & 0x3F,
                (raw_time & 0x1F) * 2,
            )
            _decode_zip_extra(info, raw_filename)
            final_name_bytes = len(
                info.filename.encode(
                    "utf-8", errors="surrogatepass"
                )
            )
            members_inspected += 1
            if (
                retained_name_bytes + final_name_bytes
                > self._name_byte_limit
            ):
                self.selection_skipped.append({
                    "name": self.archive_name,
                    "reason": "archive member name byte limit",
                    "limit": self._name_byte_limit,
                    "members_inspected": members_inspected,
                    "eligible_members_retained": (
                        eligible_members_retained
                    ),
                    "retained_members": eligible_members_retained,
                    "retained_name_bytes": retained_name_bytes,
                    "omitted_members_lower_bound": 1,
                })
                break
            info.extra = b""
            info.header_offset += concat
            header_offsets.append(info.header_offset)

            retained_name_bytes += final_name_bytes
            total += entry_size
            if (
                not info.is_dir()
                and _is_selected_archive_member(
                    info.filename,
                    include_images=self.include_images,
                )
            ):
                eligible_members_retained += 1
                self.filelist.append(info)
                self.NameToInfo[info.filename] = info

        ordered_offsets = sorted(
            set(header_offsets + [self.start_dir])
        )
        for info in self.filelist:
            index = bisect_right(
                ordered_offsets, info.header_offset
            )
            info._end_offset = (
                ordered_offsets[index]
                if index < len(ordered_offsets)
                else self.start_dir
            )


def _is_tabular_archive_member(name):
    return is_supported_input(name)


def _is_selected_archive_member(name, *, include_images):
    if _is_tabular_archive_member(name):
        return True
    return (
        include_images
        and asset_type(name) in {"image", "document"}
    )


def _collect_bounded_tar_members(
    archive,
    archive_name,
    *,
    include_images=False,
):
    selected = []
    skipped = []
    while True:
        try:
            member = archive.next()
        except _TarArchiveLimit as error:
            skipped.append(error.record(archive_name))
            break
        if member is None:
            break
        archive.members.clear()
        if (
            member.isfile()
            and _is_selected_archive_member(
                member.name,
                include_images=include_images,
            )
        ):
            selected.append(member)
    return selected, skipped


def _extract_archive_members(
    out_dir,
    members,
    max_member_bytes,
    *,
    reusable_names,
    member_name,
    member_size,
    open_member,
    member_errors,
    transient_paths=(),
    managed_name_accounting=None,
    cap_state=None,
    output_journal=None,
    archive_name=None,
    initial_skipped=(),
):
    if not isinstance(out_dir, _PinnedOutputDirectory):
        with _pinned_output_directory(out_dir) as output:
            owns_journal = output_journal is None
            journal = output_journal or _ManagedOutputJournal(output)
            try:
                try:
                    result = _extract_archive_members(
                        output,
                        members,
                        max_member_bytes,
                        reusable_names=reusable_names,
                        member_name=member_name,
                        member_size=member_size,
                        open_member=open_member,
                        member_errors=member_errors,
                        transient_paths=transient_paths,
                        managed_name_accounting=managed_name_accounting,
                        cap_state=cap_state,
                        output_journal=journal,
                        archive_name=archive_name,
                        initial_skipped=initial_skipped,
                    )
                except BaseException:
                    if owns_journal:
                        journal.rollback()
                    raise
                if owns_journal:
                    pending_cleanup = journal.commit()
                    _append_post_commit_cleanup_records(
                        result[2],
                        pending_cleanup,
                    )
                return result
            finally:
                if owns_journal:
                    journal.close()
    extracted = []
    preserved = set()
    skipped = list(initial_skipped)
    coverage_limited = bool(skipped)
    written = _paper_data_size(out_dir, transient_paths)
    max_member_bytes = max(0, int(max_member_bytes))
    output_file_limit = max(0, int(_ARCHIVE_OUTPUT_FILE_LIMIT))
    try:
        preferred_names = _archive_occurrence_output_names(
            member_name(member) for member in members
        )
    except _ManagedOutputNameLimit as error:
        skipped.append(error.record())
        if cap_state is not None:
            cap_state["exceeded"] = True
            cap_state["ownership_blocked"] = True
        return extracted, preserved, skipped
    if type(reusable_names) is dict:
        reusable_files = {
            name: fingerprint
            for name, fingerprint in reusable_names.items()
            if (
                _safe_managed_name(name) is not None
                and _canonical_fingerprint(fingerprint) is not None
            )
        }
    else:
        reusable_files = {}
        for name in _safe_managed_names(out_dir, reusable_names):
            fingerprint = _path_fingerprint(out_dir, name)
            if fingerprint is not None:
                reusable_files[name] = fingerprint
    reusable = set(reusable_files)
    for index, (member, preferred) in enumerate(
        zip(members, preferred_names)
    ):
        source_name = member_name(member)
        if len(extracted) >= output_file_limit:
            coverage_limited = True
            omitted = members[index:]
            for omitted_member in omitted:
                skipped.append({
                    "name": member_name(omitted_member),
                    "reason": "archive output file limit",
                    "limit": output_file_limit,
                })
            skipped.append({
                "name": archive_name,
                "reason": "archive output file limit",
                "limit": output_file_limit,
                "retained_outputs": len(extracted),
                "omitted_members": len(omitted),
            })
            if cap_state is not None:
                cap_state["exceeded"] = True
            break
        try:
            name = _managed_output_name(
                out_dir, preferred, source_name, reusable
            )
        except _ManagedOutputNameLimit as error:
            skipped.append(error.record())
            coverage_limited = True
            if cap_state is not None:
                cap_state["exceeded"] = True
                cap_state["ownership_blocked"] = True
            break
        reuses_old = name in reusable
        expected_old = reusable_files.get(name)
        if managed_name_accounting is not None:
            sidecar_limitation = (
                managed_name_accounting.limitation_for(name)
            )
            if sidecar_limitation is not None:
                coverage_limited = True
                sidecar_limitation["name"] = source_name
                skipped.append(sidecar_limitation)
                if cap_state is not None:
                    cap_state["exceeded"] = True
                if reuses_old:
                    preserved.add(name)
                continue
        reusable.discard(name)
        dest = os.path.join(out_dir.path, name)
        replacement_credit = (
            expected_old["size"] if reuses_old else 0
        )
        remaining = (
            _MAX_PAPER_BYTES
            - written
            + replacement_credit
        )
        write_limit = min(max_member_bytes, remaining)
        declared_size = member_size(member)
        if max_member_bytes <= 0 or declared_size > max_member_bytes:
            skipped.append({
                "name": source_name,
                "reason": "archive member exceeds per-member cap",
                "limit": max_member_bytes,
                "declared_size": declared_size,
            })
            if reuses_old:
                preserved.add(name)
            continue
        if remaining <= 0 or declared_size > remaining:
            skipped.append({
                "name": source_name,
                "reason": "archive member exceeds per-paper cap",
                "limit": _MAX_PAPER_BYTES,
                "remaining_bytes": max(0, remaining),
                "declared_size": declared_size,
            })
            if cap_state is not None:
                cap_state["exceeded"] = True
            if reuses_old:
                preserved.add(name)
            continue
        staging = _download_staging_file(
            out_dir,
            prefix=".paperconan-member-",
            suffix=os.path.splitext(name)[1],
            logical_name=name,
        )
        committed = False
        published = None
        fingerprint = None
        try:
            src = open_member(member)
            if src is None:
                raise OSError("could not open archive member")
            with src:
                size = _atomic_stream_write(
                    src, staging, write_limit
                )
                staged = _stable_staging_state(staging)
                fingerprint = {
                    "size": staged.size,
                    "sha256": staged.sha256,
                }
                if managed_name_accounting is not None:
                    sidecar_limitation = (
                        managed_name_accounting.limitation_for(
                            name,
                            fingerprint,
                        )
                    )
                    if sidecar_limitation is not None:
                        sidecar_limitation["name"] = source_name
                        skipped.append(sidecar_limitation)
                        if cap_state is not None:
                            cap_state["exceeded"] = True
                        if reuses_old:
                            preserved.add(name)
                        continue
                published = _publish_download_staging(
                    out_dir,
                    staging,
                    output_name=name,
                    base=preferred,
                    source_name=source_name,
                    expected_old=expected_old,
                    output_journal=output_journal,
                )
                if published.cleanup_warning is not None:
                    skipped.append({
                        "name": source_name,
                        "reason": published.cleanup_warning,
                    })
                fingerprint = {
                    "size": published.size,
                    "sha256": published.sha256,
                }
                projected = _paper_data_size(
                    out_dir,
                    tuple(transient_paths) + (staging,),
                )
                if managed_name_accounting is not None:
                    sidecar_limitation = (
                        managed_name_accounting.limitation_for(
                            published.name,
                            fingerprint,
                        )
                    )
                else:
                    sidecar_limitation = None
                if (
                    sidecar_limitation is not None
                    or projected > _MAX_PAPER_BYTES
                ):
                    _restore_managed_output(
                        output_journal,
                        os.path.join(
                            out_dir.path, published.name
                        ),
                    )
                    published = None
                    if sidecar_limitation is not None:
                        sidecar_limitation["name"] = source_name
                        skipped.append(sidecar_limitation)
                    else:
                        skipped.append({
                            "name": source_name,
                            "reason": (
                                "archive member exceeds per-paper cap"
                            ),
                            "limit": _MAX_PAPER_BYTES,
                            "remaining_bytes": max(0, remaining),
                            "declared_size": declared_size,
                        })
                    if cap_state is not None:
                        cap_state["exceeded"] = True
                    if reuses_old:
                        preserved.add(name)
                    continue
                committed = True
        except _TransientCleanupError:
            raise
        except _ManagedOutputRecoveryRequiredError:
            raise
        except _TarArchiveLimit as error:
            if reuses_old:
                preserved.add(name)
            record = error.record(archive_name)
            if (
                error.reason
                == "archive decompressed traversal limit"
            ):
                record["omitted_members_lower_bound"] = 1
            skipped.append(record)
            coverage_limited = True
            if cap_state is not None:
                cap_state["exceeded"] = True
            break
        except _SizeLimitExceeded:
            if reuses_old:
                preserved.add(name)
            if remaining < max_member_bytes:
                skipped.append({
                    "name": source_name,
                    "reason": (
                        "archive member exceeds per-paper cap "
                        "while streaming"
                    ),
                    "limit": _MAX_PAPER_BYTES,
                    "remaining_bytes": max(0, remaining),
                })
                if cap_state is not None:
                    cap_state["exceeded"] = True
            else:
                skipped.append({
                    "name": source_name,
                    "reason": (
                        "archive member exceeds per-member cap "
                        "while streaming"
                    ),
                    "limit": max_member_bytes,
                })
            continue
        except member_errors as e:
            if committed:
                written = _paper_data_size(
                    out_dir,
                    tuple(transient_paths) + (staging,),
                )
                final_path = os.path.join(
                    out_dir.path, published.name
                )
                extracted.append(final_path)
                if managed_name_accounting is not None:
                    managed_name_accounting.add(
                        published.name,
                        fingerprint,
                    )
                skipped.append({
                    "name": source_name,
                    "reason": (
                        "archive member close failed after commit: "
                        f"{e}"
                    ),
                })
                continue
            if reuses_old:
                preserved.add(name)
            skipped.append({
                "name": source_name,
                "reason": f"archive member failed: {e}",
            })
            continue
        finally:
            cleanup_context = _cleanup_download_staging(staging)
            if cleanup_context is not None:
                skipped.append({
                    "name": source_name,
                    "reason": cleanup_context,
                })
        if committed:
            written = _paper_data_size(
                out_dir,
                transient_paths,
            )
            extracted.append(
                os.path.join(out_dir.path, published.name)
            )
            if managed_name_accounting is not None:
                managed_name_accounting.add(
                    published.name,
                    fingerprint,
                )
    if coverage_limited:
        for name in reusable:
            try:
                _stable_managed_file(
                    out_dir,
                    name,
                    expected=reusable_files[name],
                )
            except (OSError, ValueError):
                continue
            else:
                preserved.add(name)
    return extracted, preserved, skipped


def _extract_tabular_zip(
    zip_path,
    out_dir,
    max_member_bytes=_DEFAULT_MAX,
    *,
    reusable_names=(),
    include_images=False,
):
    """Extract scanner-supported inputs from a supplementary zip into
    out_dir, flattening internal paths to the basename (no path traversal) and
    capping per-member size. Returns the list of extracted file paths."""
    extracted, _, skipped = _extract_tabular_zip_managed(
        zip_path,
        out_dir,
        max_member_bytes,
        reusable_names=reusable_names,
        include_images=include_images,
    )
    return _ArchiveExtractionPaths(
        extracted,
        skipped=skipped,
    )


def _extract_tabular_zip_managed(
    zip_path,
    out_dir,
    max_member_bytes,
    *,
    reusable_names,
    managed_name_accounting=None,
    cap_state=None,
    output_journal=None,
    archive_name=None,
    include_images=False,
):
    stable_archive_name = archive_name or os.path.basename(zip_path)
    with _open_archive_source(zip_path) as source:
        with _BoundedZipFile(
            source,
            archive_name=stable_archive_name,
            include_images=include_images,
        ) as zf:
            infos = zf.filelist
            selection_skipped = zf.selection_skipped
            if selection_skipped and cap_state is not None:
                cap_state["exceeded"] = True
            return _extract_archive_members(
                out_dir,
                infos,
                max_member_bytes,
                reusable_names=reusable_names,
                member_name=lambda info: info.filename,
                member_size=lambda info: info.file_size,
                open_member=zf.open,
                member_errors=(
                    OSError,
                    EOFError,
                    RuntimeError,
                    zipfile.BadZipFile,
                ),
                transient_paths=(zip_path,),
                managed_name_accounting=managed_name_accounting,
                cap_state=cap_state,
                output_journal=output_journal,
                archive_name=stable_archive_name,
                initial_skipped=selection_skipped,
            )


def _extract_tabular_tar(
    tar_path,
    out_dir,
    max_member_bytes=_DEFAULT_MAX,
    *,
    reusable_names=(),
    include_images=False,
):
    """Extract scanner-supported inputs from a .tar.gz into out_dir,
    flattening internal paths to the basename and capping per-member size.
    Returns the list of extracted file paths."""
    extracted, _, skipped = _extract_tabular_tar_managed(
        tar_path,
        out_dir,
        max_member_bytes,
        reusable_names=reusable_names,
        include_images=include_images,
    )
    return _ArchiveExtractionPaths(
        extracted,
        skipped=skipped,
    )


def _extract_tabular_tar_managed(
    tar_path,
    out_dir,
    max_member_bytes,
    *,
    reusable_names,
    managed_name_accounting=None,
    cap_state=None,
    output_journal=None,
    archive_name=None,
    include_images=False,
):
    stable_archive_name = (
        archive_name or os.path.basename(tar_path)
    )
    with _open_archive_source(tar_path) as source:
        try:
            if isinstance(source, str):
                tf = _BoundedTarFile.open(
                    source,
                    "r:gz",
                    tarinfo=_BoundedTarInfo,
                    include_images=include_images,
                )
            else:
                tf = _BoundedTarFile.open(
                    fileobj=source,
                    mode="r:gz",
                    tarinfo=_BoundedTarInfo,
                    include_images=include_images,
                )
        except _TarArchiveLimit as error:
            selection_skipped = [error.record(stable_archive_name)]
            if selection_skipped and cap_state is not None:
                cap_state["exceeded"] = True
            return _extract_archive_members(
                out_dir,
                [],
                max_member_bytes,
                reusable_names=reusable_names,
                member_name=lambda member: member.name,
                member_size=lambda member: member.size,
                open_member=lambda member: None,
                member_errors=(
                    OSError,
                    EOFError,
                    tarfile.TarError,
                ),
                transient_paths=(tar_path,),
                managed_name_accounting=managed_name_accounting,
                cap_state=cap_state,
                output_journal=output_journal,
                archive_name=stable_archive_name,
                initial_skipped=selection_skipped,
            )
        with tf:
            members, selection_skipped = (
                _collect_bounded_tar_members(
                    tf,
                    stable_archive_name,
                    include_images=include_images,
                )
            )
            if selection_skipped and cap_state is not None:
                cap_state["exceeded"] = True
            return _extract_archive_members(
                out_dir,
                members,
                max_member_bytes,
                reusable_names=reusable_names,
                member_name=lambda member: member.name,
                member_size=lambda member: member.size,
                open_member=tf.extractfile,
                member_errors=(
                    OSError,
                    EOFError,
                    tarfile.TarError,
                ),
                transient_paths=(tar_path,),
                managed_name_accounting=managed_name_accounting,
                cap_state=cap_state,
                output_journal=output_journal,
                archive_name=stable_archive_name,
                initial_skipped=selection_skipped,
            )


@contextmanager
def _open_archive_source(path):
    if not isinstance(path, _DownloadStagingFile):
        yield os.fspath(path)
        return
    before = _stable_staging_state(path)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _UnstableRegularFileError(
            "no-follow archive opening is unavailable"
        )
    fd = os.open(
        path.name,
        os.O_RDONLY | nofollow,
        dir_fd=path.output.fd,
    )
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != before.identity
            or opened.st_size != before.size
        ):
            raise _UnstableRegularFileError(
                "downloaded archive is not a stable regular file"
            )
        with os.fdopen(fd, "rb") as source:
            fd = -1
            yield source
        after = _stable_staging_state(path)
        if (
            after.identity != before.identity
            or after.size != before.size
            or after.sha256 != before.sha256
        ):
            raise _UnstableRegularFileError(
                "downloaded archive changed during extraction"
            )
    finally:
        if fd >= 0:
            os.close(fd)


def _temporary_archive_path(out_dir, suffix):
    if isinstance(out_dir, _PinnedOutputDirectory):
        return _download_staging_file(
            out_dir,
            prefix=".paperconan-archive-",
            suffix=suffix,
        )
    fd, path = tempfile.mkstemp(
        prefix=".paperconan-archive-",
        suffix=suffix,
        dir=out_dir,
    )
    os.close(fd)
    return path


def _cleanup_transient_archive(
    path,
    archive_name,
    skipped,
    operation_error,
):
    if path is None:
        return
    try:
        if isinstance(path, _DownloadStagingFile):
            cleanup_context = _cleanup_download_staging(path)
            if cleanup_context is not None:
                skipped.append({
                    "name": archive_name,
                    "reason": cleanup_context,
                })
                return
        else:
            _remove_transient_file(path)
    except OSError as cleanup_error:
        if operation_error is not None:
            _raise_transient_cleanup_error(
                (
                    path.display_path
                    if isinstance(path, _DownloadStagingFile)
                    else path
                ),
                cleanup_error,
                operation_error,
            )
        skipped.append({
            "name": archive_name,
            "reason": "transient archive cleanup pending",
        })


def _accept_archive_publications(
    out_dir,
    entries,
    downloaded,
    skipped,
    *,
    archive_name,
    managed_name_accounting,
    cap_state,
    output_journal,
    published_outputs,
):
    accepted = []
    for entry in entries:
        if entry.cleanup_warning is not None:
            skipped.append({
                "name": archive_name,
                "reason": entry.cleanup_warning,
            })
        fingerprint = {
            "size": entry.size,
            "sha256": entry.sha256,
        }
        limitation = (
            managed_name_accounting.limitation_for(
                entry.filename,
                fingerprint,
            )
            if managed_name_accounting is not None
            else None
        )
        path = entry.display_path(out_dir)
        if limitation is not None:
            limitation["name"] = archive_name
            skipped.append(limitation)
            if cap_state is not None:
                cap_state["exceeded"] = True
            if entry.created and output_journal is not None:
                output_journal.record_created(
                    path,
                    entry.identity,
                    entry.size,
                    entry.sha256,
                )
                _restore_managed_output(
                    output_journal,
                    path,
                )
            continue
        if entry.created and output_journal is not None:
            output_journal.record_created(
                path,
                entry.identity,
                entry.size,
                entry.sha256,
            )
        if managed_name_accounting is not None:
            managed_name_accounting.add(
                entry.filename,
                fingerprint,
            )
        downloaded.append(path)
        accepted.append(entry)
        if published_outputs is not None:
            published_outputs.append(entry)
    return accepted


def _download_oa_package(
    pkg,
    out_dir,
    downloaded,
    skipped,
    max_bytes,
    archive_max=_ARCHIVE_MAX,
    *,
    reusable_names=(),
    managed_name_accounting=None,
    cap_state=None,
    output_journal=None,
    include_images=False,
    cardinality=None,
    published_outputs=None,
):
    """Download the static PMC OA tar.gz, extract its tabular members, drop the tarball."""
    if not reusable_names:
        blocking_reason = _archive_blocking_reason(cardinality)
        if blocking_reason is not None:
            skipped.append({
                "name": pkg.get("name"),
                "reason": blocking_reason,
            })
            return False, set()
        tmp = None
        operation_error = None
        try:
            tmp = _archive_staging_file(out_dir, ".tar.gz")
            res = download_file(
                pkg["url"],
                tmp,
                max_bytes=archive_max,
            )
            if not res.get("ok"):
                skipped.append({
                    "name": pkg.get("name"),
                    "reason": res.get("skipped_reason"),
                })
                return False, set()
            limit_reasons = []
            extracted = []
            pending = []
            processing_error = None
            staging_error = None
            try:
                with _open_download_staging(tmp) as archive_fh:
                    try:
                        _extract_selected_tar(
                            archive_fh,
                            out_dir,
                            include_images=include_images,
                            max_member_bytes=max_bytes,
                            return_entries=True,
                            cardinality=cardinality,
                            limit_reasons=limit_reasons,
                            published_entries=extracted,
                            pending_entries=pending,
                            transient_files=(tmp,),
                        )
                    except (
                        tarfile.TarError,
                        OSError,
                        ValueError,
                        _ArchiveReadError,
                    ) as error:
                        processing_error = error
            except _UnstableRegularFileError as error:
                staging_error = error
            reconciled, outcomes, reconciliation_error = (
                _reconcile_archive_publications(
                    out_dir,
                    extracted,
                    pending,
                )
            )
            failure = (
                staging_error
                or processing_error
                or reconciliation_error
            )
            if failure is not None:
                if staging_error is not None:
                    reason = (
                        "downloaded archive is not a stable regular "
                        f"file: {staging_error}"
                    )
                else:
                    reason = (
                        f"archive publication unavailable: {failure}"
                        if isinstance(failure, OSError)
                        else (
                            "archive processing unavailable: "
                            f"{failure}"
                        )
                    )
                if outcomes:
                    reason += "; " + "; ".join(outcomes)
                skipped.append({
                    "name": pkg.get("name"),
                    "reason": reason,
                })
            skipped.extend(
                {
                    "name": pkg.get("name"),
                    "reason": reason,
                }
                for reason in limit_reasons
            )
            accepted = _accept_archive_publications(
                out_dir,
                reconciled,
                downloaded,
                skipped,
                archive_name=(
                    pkg.get("name") or "PMC OA package"
                ),
                managed_name_accounting=managed_name_accounting,
                cap_state=cap_state,
                output_journal=output_journal,
                published_outputs=published_outputs,
            )
            return failure is None, set()
        except BaseException as error:
            operation_error = error
            raise
        finally:
            _cleanup_transient_archive(
                tmp,
                pkg.get("name") or "PMC OA package",
                skipped,
                operation_error,
            )
    tmp = _temporary_archive_path(out_dir, ".tar.gz")
    archive_name = pkg.get("name") or "PMC OA package"
    operation_error = None
    try:
        res = download_file(pkg["url"], tmp, max_bytes=archive_max)
        if not res.get("ok"):
            skipped.append({
                "name": pkg.get("name"),
                "reason": res.get("skipped_reason"),
            })
            return False, set()
        try:
            extracted, preserved, member_skipped = (
                _extract_tabular_tar_managed(
                    tmp,
                    out_dir,
                    max_bytes,
                    reusable_names=reusable_names,
                    managed_name_accounting=(
                        managed_name_accounting
                    ),
                    cap_state=cap_state,
                    output_journal=output_journal,
                    archive_name=(
                        pkg.get("name")
                        or os.path.basename(tmp)
                    ),
                    include_images=include_images,
                )
            )
            skipped.extend(member_skipped)
            downloaded.extend(extracted)
        except _ManagedOutputRecoveryRequiredError:
            raise
        except (tarfile.TarError, OSError) as e:
            skipped.append({
                "name": pkg.get("name"),
                "reason": f"bad tar.gz: {e}",
            })
            return False, set()
        return True, preserved
    except BaseException as error:
        operation_error = error
        raise
    finally:
        _cleanup_transient_archive(
            tmp,
            archive_name,
            skipped,
            operation_error,
        )


def _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes,
                                    archive_max=_ARCHIVE_MAX, *,
                                    reusable_names=(),
                                    managed_name_accounting=None,
                                    cap_state=None,
                                    output_journal=None,
                                    include_images=False,
                                    cardinality=None,
                                    published_outputs=None):
    """Fetch a supplementary zip (Europe PMC), extract its tabular members, drop the zip.

    The archive downloads with the larger ``archive_max`` cap; each extracted table is
    still capped at the per-file ``max_bytes``."""
    if not reusable_names:
        blocking_reason = _archive_blocking_reason(cardinality)
        if blocking_reason is not None:
            skipped.append({
                "name": arch.get("name"),
                "reason": blocking_reason,
            })
            return False, set()
        tmp_zip = None
        operation_error = None
        try:
            tmp_zip = _archive_staging_file(out_dir, ".zip")
            res = download_file(
                arch["url"],
                tmp_zip,
                max_bytes=archive_max,
            )
            if not res.get("ok"):
                skipped.append({
                    "name": arch.get("name"),
                    "reason": res.get("skipped_reason"),
                })
                return False, set()
            limit_reasons = []
            extracted = []
            pending = []
            processing_error = None
            staging_error = None
            snapshot_cleanup_warnings = []
            try:
                with _open_download_staging(tmp_zip) as archive_fh:
                    try:
                        with _private_zip_snapshot(
                            archive_fh,
                            max_bytes=archive_max,
                            output=out_dir,
                            cleanup_warnings=(
                                snapshot_cleanup_warnings
                            ),
                        ) as snapshot:
                            _extract_selected_zip(
                                snapshot,
                                out_dir,
                                include_images=include_images,
                                max_member_bytes=max_bytes,
                                return_entries=True,
                                cardinality=cardinality,
                                limit_reasons=limit_reasons,
                                published_entries=extracted,
                                pending_entries=pending,
                                transient_files=(
                                    tmp_zip,
                                    snapshot.staging,
                                ),
                            )
                    except (
                        zipfile.BadZipFile,
                        zipfile.LargeZipFile,
                        OSError,
                        ValueError,
                        _ArchiveReadError,
                    ) as error:
                        processing_error = error
            except _UnstableRegularFileError as error:
                staging_error = error
            for warning in snapshot_cleanup_warnings:
                skipped.append({
                    "name": arch.get("name"),
                    "reason": warning,
                })
            reconciled, outcomes, reconciliation_error = (
                _reconcile_archive_publications(
                    out_dir,
                    extracted,
                    pending,
                )
            )
            failure = (
                staging_error
                or processing_error
                or reconciliation_error
            )
            if failure is not None:
                if staging_error is not None:
                    reason = (
                        "downloaded archive is not a stable regular "
                        f"file: {staging_error}"
                    )
                elif isinstance(failure, zipfile.BadZipFile):
                    reason = "not a valid zip archive"
                else:
                    reason = (
                        f"archive publication unavailable: {failure}"
                        if isinstance(failure, OSError)
                        else (
                            "archive processing unavailable: "
                            f"{failure}"
                        )
                    )
                if outcomes:
                    reason += "; " + "; ".join(outcomes)
                skipped.append({
                    "name": arch.get("name"),
                    "reason": reason,
                })
            skipped.extend(
                {
                    "name": arch.get("name"),
                    "reason": reason,
                }
                for reason in limit_reasons
            )
            _accept_archive_publications(
                out_dir,
                reconciled,
                downloaded,
                skipped,
                archive_name=(
                    arch.get("name")
                    or "supplementary archive"
                ),
                managed_name_accounting=managed_name_accounting,
                cap_state=cap_state,
                output_journal=output_journal,
                published_outputs=published_outputs,
            )
            return failure is None, set()
        except BaseException as error:
            operation_error = error
            raise
        finally:
            _cleanup_transient_archive(
                tmp_zip,
                (
                    arch.get("name")
                    or "supplementary archive"
                ),
                skipped,
                operation_error,
            )
    tmp_zip = _temporary_archive_path(out_dir, ".zip")
    archive_name = (
        arch.get("name") or "supplementary archive"
    )
    operation_error = None
    try:
        res = download_file(arch["url"], tmp_zip, max_bytes=archive_max)
        if not res.get("ok"):
            skipped.append({
                "name": arch.get("name"),
                "reason": res.get("skipped_reason"),
            })
            return False, set()
        try:
            extracted, preserved, member_skipped = (
                _extract_tabular_zip_managed(
                    tmp_zip,
                    out_dir,
                    max_bytes,
                    reusable_names=reusable_names,
                    managed_name_accounting=(
                        managed_name_accounting
                    ),
                    cap_state=cap_state,
                    output_journal=output_journal,
                    archive_name=(
                        arch.get("name")
                        or os.path.basename(tmp_zip)
                    ),
                    include_images=include_images,
                )
            )
            skipped.extend(member_skipped)
            downloaded.extend(extracted)
        except _ManagedOutputRecoveryRequiredError:
            raise
        except (zipfile.BadZipFile, OSError):
            skipped.append({
                "name": arch.get("name"),
                "reason": "not a valid zip archive",
            })
            return False, set()
        return True, preserved
    except BaseException as error:
        operation_error = error
        raise
    finally:
        _cleanup_transient_archive(
            tmp_zip,
            archive_name,
            skipped,
            operation_error,
        )


def _source_sidecar_provenance(cand, managed_files):
    related_dois = cand.get("related_dois")
    if related_dois is None:
        related_dois = []
    provenance = {
        "doi": cand.get("doi"),
        "title": cand.get("title"),
        "source": cand.get("source"),
        "cand_id": cand.get("cand_id"),
        "related_dois": related_dois,
        "managed_files": managed_files,
    }
    if "_paperconan_downloads" in cand:
        downloads = {}
        for entry in cand.get("_paperconan_downloads") or ():
            if not isinstance(entry, dict):
                continue
            name = entry.get("file")
            if isinstance(name, str) and name:
                downloads.setdefault(name, entry)
        provenance["downloads"] = [
            downloads[name] for name in sorted(downloads)
        ]
    return provenance


def _encode_source_sidecar(provenance):
    byte_limit = max(0, int(_SOURCE_SIDECAR_MAX_BYTES))
    try:
        return encode_sidecar(
            provenance,
            byte_limit=byte_limit,
        )
    except SidecarLimitError as error:
        raise _SourceSidecarLimit(
            _source_sidecar_limit_record(
                error.reason,
                limit=byte_limit,
                **error.details,
            )
        ) from None


def _source_sidecar_bytes(cand, out_dir, managed_files):
    safe_files = _safe_managed_fingerprints(
        out_dir,
        managed_files,
        entry_limit=max(0, int(_SOURCE_SIDECAR_ENTRY_LIMIT)),
        name_byte_limit=max(
            0, int(_SOURCE_SIDECAR_NAME_BYTES)
        ),
    )
    return _encode_source_sidecar(
        _source_sidecar_provenance(cand, safe_files)
    )


def _prepare_sidecar_candidate(cand):
    related_dois = cand.get("related_dois")
    if (
        related_dois is None
        or isinstance(
            related_dois,
            (
                str,
                int,
                float,
                bytes,
                bytearray,
                list,
                tuple,
                dict,
                set,
                frozenset,
            ),
        )
    ):
        return cand
    try:
        source = iter(related_dois)
    except TypeError:
        return cand

    retained = []

    class CapturingIterable:
        def __iter__(self):
            for item in source:
                retained.append(item)
                yield item

    prepared = dict(cand)
    prepared["related_dois"] = CapturingIterable()
    _encode_source_sidecar(
        _source_sidecar_provenance(prepared, {})
    )
    prepared["related_dois"] = retained
    return prepared


def _encoded_json_name_bytes(name):
    return len(json.dumps(name).encode("utf-8"))


def _managed_name_list_extra_bytes(count, encoded_name_bytes):
    if count <= 0:
        return 0
    return encoded_name_bytes + 8 + 6 * (count - 1)


def _managed_fingerprint_value_bytes(fingerprint):
    return 106 + len(str(fingerprint["size"]))


def _managed_fingerprint_map_extra_bytes(
    count,
    encoded_name_bytes,
    fingerprint_value_bytes,
):
    if count <= 0:
        return 0
    return (
        encoded_name_bytes
        + fingerprint_value_bytes
        + 10
        + 8 * (count - 1)
    )


class _ManagedNameAccounting:
    def __init__(
        self,
        cand,
        out_dir,
        old_files,
        new_files,
    ):
        self._old_files = old_files
        self._new_files = new_files
        self._entry_limit = max(
            0, int(_SOURCE_SIDECAR_ENTRY_LIMIT)
        )
        self._name_byte_limit = max(
            0, int(_SOURCE_SIDECAR_NAME_BYTES)
        )
        self._sidecar_byte_limit = max(
            0, int(_SOURCE_SIDECAR_MAX_BYTES)
        )
        self._previous_size = 0
        try:
            if isinstance(out_dir, _PinnedOutputDirectory):
                previous = os.stat(
                    SOURCE_SIDECAR,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
                if stat.S_ISREG(previous.st_mode):
                    self._previous_size = previous.st_size
            else:
                self._previous_size = os.path.getsize(
                    os.path.join(
                        _output_path(out_dir), SOURCE_SIDECAR
                    )
                )
        except OSError:
            pass
        self._entry_count = len(old_files)
        self._name_bytes = sum(
            len(name.encode("utf-8", errors="surrogatepass"))
            + 64
            for name in old_files
        )
        self._encoded_name_bytes = sum(
            _encoded_json_name_bytes(name)
            for name in old_files
        )
        self._fingerprint_value_bytes = sum(
            _managed_fingerprint_value_bytes(fingerprint)
            for fingerprint in old_files.values()
        )
        self._base_size = len(_encode_source_sidecar(
            _source_sidecar_provenance(cand, {})
        ))
        current_size = self._payload_size()
        if current_size > self._sidecar_byte_limit:
            raise _SourceSidecarLimit(
                _source_sidecar_limit_record(
                    "source sidecar byte limit",
                    limit=self._sidecar_byte_limit,
                    observed_bytes=current_size,
                    ownership_preserved=True,
                )
            )

    def _contains(self, name):
        return name in self._old_files or name in self._new_files

    def _current_fingerprint(self, name):
        if name in self._new_files:
            return self._new_files[name]
        return self._old_files.get(name)

    def _prospective_counts(self, name, fingerprint=None):
        candidate = (
            fingerprint
            if fingerprint is not None
            else self._current_fingerprint(name)
            or {"size": 0, "sha256": "0" * 64}
        )
        if self._contains(name):
            current = self._current_fingerprint(name)
            return (
                self._entry_count,
                self._name_bytes,
                self._encoded_name_bytes,
                self._fingerprint_value_bytes
                - _managed_fingerprint_value_bytes(current)
                + _managed_fingerprint_value_bytes(candidate),
            )
        return (
            self._entry_count + 1,
            self._name_bytes + len(
                name.encode("utf-8", errors="surrogatepass")
            ) + 64,
            self._encoded_name_bytes
            + _encoded_json_name_bytes(name),
            self._fingerprint_value_bytes
            + _managed_fingerprint_value_bytes(candidate),
        )

    def _payload_size(
        self,
        *,
        entry_count=None,
        encoded_name_bytes=None,
        fingerprint_value_bytes=None,
    ):
        count = (
            self._entry_count
            if entry_count is None
            else entry_count
        )
        encoded = (
            self._encoded_name_bytes
            if encoded_name_bytes is None
            else encoded_name_bytes
        )
        fingerprints = (
            self._fingerprint_value_bytes
            if fingerprint_value_bytes is None
            else fingerprint_value_bytes
        )
        return (
            self._base_size
            + _managed_fingerprint_map_extra_bytes(
                count,
                encoded,
                fingerprints,
            )
        )

    def limitation_for(self, name, fingerprint=None):
        (
            entry_count,
            name_bytes,
            encoded_name_bytes,
            fingerprint_value_bytes,
        ) = self._prospective_counts(name, fingerprint)
        if entry_count > self._entry_limit:
            return _source_sidecar_limit_record(
                "source sidecar managed entry limit",
                limit=self._entry_limit,
                managed_entries_retained=self._entry_count,
                managed_name_bytes_retained=self._name_bytes,
            )
        requested_name_bytes = (
            0
            if self._contains(name)
            else name_bytes - self._name_bytes
        )
        if name_bytes > self._name_byte_limit:
            return _source_sidecar_limit_record(
                "source sidecar managed name byte limit",
                limit=self._name_byte_limit,
                managed_entries_retained=self._entry_count,
                managed_name_bytes_retained=self._name_bytes,
                requested_name_bytes=requested_name_bytes,
            )
        payload_size = self._payload_size(
            entry_count=entry_count,
            encoded_name_bytes=encoded_name_bytes,
            fingerprint_value_bytes=fingerprint_value_bytes,
        )
        if payload_size > self._sidecar_byte_limit:
            return _source_sidecar_limit_record(
                "source sidecar byte limit",
                limit=self._sidecar_byte_limit,
                observed_bytes=payload_size,
            )
        return None

    def replacement_delta_with(self, name, fingerprint=None):
        (
            entry_count,
            _name_bytes,
            encoded_name_bytes,
            fingerprint_value_bytes,
        ) = self._prospective_counts(name, fingerprint)
        return (
            self._payload_size(
                entry_count=entry_count,
                encoded_name_bytes=encoded_name_bytes,
                fingerprint_value_bytes=fingerprint_value_bytes,
            )
            - self._previous_size
        )

    def add(self, name, fingerprint):
        fingerprint = _canonical_fingerprint(fingerprint)
        if fingerprint is None:
            raise ValueError("invalid managed output fingerprint")
        previous = self._current_fingerprint(name)
        if name in self._new_files and previous == fingerprint:
            return
        already_accounted = name in self._old_files
        self._new_files[name] = fingerprint
        if already_accounted:
            self._fingerprint_value_bytes += (
                _managed_fingerprint_value_bytes(fingerprint)
                - _managed_fingerprint_value_bytes(previous)
            )
            return
        self._entry_count += 1
        self._name_bytes += len(
            name.encode("utf-8", errors="surrogatepass")
        ) + 64
        self._encoded_name_bytes += _encoded_json_name_bytes(name)
        self._fingerprint_value_bytes += (
            _managed_fingerprint_value_bytes(fingerprint)
        )

    def replacement_delta(self):
        return self._payload_size() - self._previous_size


def _source_sidecar_replacement_delta(cand, out_dir, managed_files):
    try:
        if isinstance(out_dir, _PinnedOutputDirectory):
            previous = os.stat(
                SOURCE_SIDECAR,
                dir_fd=out_dir.fd,
                follow_symlinks=False,
            )
            previous_size = (
                previous.st_size
                if stat.S_ISREG(previous.st_mode)
                else 0
            )
        else:
            previous_size = os.path.getsize(
                os.path.join(
                    _output_path(out_dir), SOURCE_SIDECAR
                )
            )
    except OSError:
        previous_size = 0
    return (
        len(_source_sidecar_bytes(cand, out_dir, managed_files))
        - previous_size
    )


def _authoritative_sidecar_payload(payload):
    class _DuplicateKey(ValueError):
        pass

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKey
            result[key] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return False
    if type(decoded) is not dict:
        return False
    managed = decoded.get("managed_files")
    if type(managed) is not dict:
        return False
    return all(
        _safe_managed_name(name) is not None
        and _canonical_fingerprint(fingerprint) is not None
        for name, fingerprint in managed.items()
    )


def _raise_source_sidecar_recovery_required(
    journal,
    operation_error,
    rollback_error=None,
):
    recovery_paths = journal.recovery_paths()
    error = _SourceSidecarRecoveryRequiredError(
        "source sidecar recovery required after "
        f"{operation_error}",
        recovery_paths=recovery_paths,
        operation_error=operation_error,
        rollback_error=rollback_error,
    )
    if rollback_error is not None:
        _append_explicit_cause(
            rollback_error,
            operation_error,
        )
        raise error from rollback_error
    raise error from operation_error


def _verify_published_source_sidecar(
    output,
    staged_fd,
    expected_size,
    expected_sha256,
):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _SourceSidecarPublicationError(
            "retained provenance sidecar because it changed "
            "during publication"
        )
    canonical_fd = -1
    try:
        output.verify()
        canonical_fd = os.open(
            SOURCE_SIDECAR,
            os.O_RDONLY | nofollow,
            dir_fd=output.fd,
        )
        staged = os.fstat(staged_fd)
        opened = os.fstat(canonical_fd)
        current = os.stat(
            SOURCE_SIDECAR,
            dir_fd=output.fd,
            follow_symlinks=False,
        )
        identity = (staged.st_dev, staged.st_ino)
        if (
            not stat.S_ISREG(staged.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != identity
            or (current.st_dev, current.st_ino) != identity
            or staged.st_size != expected_size
            or opened.st_size != expected_size
            or current.st_size != expected_size
            or opened.st_mtime_ns != staged.st_mtime_ns
            or opened.st_ctime_ns != staged.st_ctime_ns
            or current.st_mtime_ns != staged.st_mtime_ns
            or current.st_ctime_ns != staged.st_ctime_ns
        ):
            raise _SourceSidecarPublicationError(
                "retained provenance sidecar because it changed "
                "during publication"
            )
        digest = _hash_exact_fd(canonical_fd, expected_size)
        output.verify()
        final_staged = os.fstat(staged_fd)
        final_opened = os.fstat(canonical_fd)
        final_current = os.stat(
            SOURCE_SIDECAR,
            dir_fd=output.fd,
            follow_symlinks=False,
        )
        if (
            digest != expected_sha256
            or not stat.S_ISREG(final_staged.st_mode)
            or not stat.S_ISREG(final_opened.st_mode)
            or not stat.S_ISREG(final_current.st_mode)
            or (final_staged.st_dev, final_staged.st_ino) != identity
            or (final_opened.st_dev, final_opened.st_ino) != identity
            or (final_current.st_dev, final_current.st_ino) != identity
            or final_staged.st_size != expected_size
            or final_opened.st_size != expected_size
            or final_current.st_size != expected_size
            or final_staged.st_mtime_ns != staged.st_mtime_ns
            or final_staged.st_ctime_ns != staged.st_ctime_ns
            or final_opened.st_mtime_ns != staged.st_mtime_ns
            or final_opened.st_ctime_ns != staged.st_ctime_ns
            or final_current.st_mtime_ns != staged.st_mtime_ns
            or final_current.st_ctime_ns != staged.st_ctime_ns
        ):
            raise _SourceSidecarPublicationError(
                "retained provenance sidecar because it changed "
                "during publication"
            )
        _verify_final_published_sidecar_root(output)
    except _SourceSidecarPublicationError:
        raise
    except (
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ) as error:
        raise _SourceSidecarPublicationError(
            "retained provenance sidecar because it changed "
            "during publication"
        ) from error
    finally:
        if canonical_fd >= 0:
            os.close(canonical_fd)


def _verify_final_published_sidecar_root(output):
    output.verify()


def _write_source_sidecar(
    cand,
    out_dir,
    managed_files=None,
    *,
    downloads=None,
):
    """Record which paper/dataset these downloads came from, for scan.json provenance."""
    if managed_files is None:
        managed_files = cand.get(
            "_paperconan_managed_files", {}
        )
    if downloads is not None:
        cand = dict(cand)
        cand["_paperconan_downloads"] = downloads
    if not isinstance(out_dir, _PinnedOutputDirectory):
        with _pinned_output_directory(out_dir) as output:
            return _write_source_sidecar(
                cand,
                output,
                managed_files,
                downloads=downloads,
            )
    payload = _source_sidecar_bytes(cand, out_dir, managed_files)
    pending_cleanup = ()
    max_sidecar_bytes = max(
        0, int(_MAX_SOURCE_SIDECAR_BYTES)
    )
    if len(payload) > max_sidecar_bytes:
        raise _SourceSidecarLimitError(
            "new provenance sidecar exceeds "
            f"{max_sidecar_bytes}-byte limit"
        )
    out_dir.verify()
    try:
        previous = os.stat(
            SOURCE_SIDECAR,
            dir_fd=out_dir.fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        previous = None
    if previous is not None and not stat.S_ISREG(previous.st_mode):
        raise _SourceSidecarPublicationError(
            "retained existing provenance sidecar because it is not "
            "a regular file"
        )
    if (
        previous is not None
        and previous.st_size > max_sidecar_bytes
    ):
        raise _SourceSidecarLimitError(
            "existing provenance sidecar exceeds "
            f"{max_sidecar_bytes}-byte limit"
        )
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise OSError("no-follow sidecar publication is unavailable")
    fd = -1
    temp_name = None
    sidecar_operation_error = None
    sidecar_result = None
    try:
        for _attempt in range(128):
            temp_name = (
                f".{SOURCE_SIDECAR}."
                f"{secrets.token_hex(8)}.part"
            )
            try:
                with _transaction_state_allocation():
                    fd = os.open(
                        temp_name,
                        (
                            os.O_RDWR
                            | os.O_CREAT
                            | os.O_EXCL
                            | nofollow
                        ),
                        0o600,
                        dir_fd=out_dir.fd,
                    )
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(
                "could not allocate source sidecar staging file"
            )
        with os.fdopen(os.dup(fd), "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        opened = os.fstat(fd)
        visible = os.stat(
            temp_name,
            dir_fd=out_dir.fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(visible.st_mode)
            or opened.st_size != len(payload)
            or visible.st_size != len(payload)
            or (opened.st_dev, opened.st_ino)
            != (visible.st_dev, visible.st_ino)
            or _hash_exact_fd(fd, opened.st_size)
            != hashlib.sha256(payload).hexdigest()
        ):
            raise _UnstableRegularFileError(
                "source sidecar staging is not a stable regular file"
            )
        out_dir.verify()
        if previous is None:
            try:
                with _transaction_state_allocation():
                    os.link(
                        temp_name,
                        SOURCE_SIDECAR,
                        src_dir_fd=out_dir.fd,
                        dst_dir_fd=out_dir.fd,
                        follow_symlinks=False,
                    )
            except FileExistsError as error:
                raise _SourceSidecarPublicationError(
                    "retained existing provenance sidecar created "
                    "during publication"
                ) from error
            _verify_published_source_sidecar(
                out_dir,
                fd,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
        else:
            existing_fd = -1
            try:
                existing_fd = os.open(
                    SOURCE_SIDECAR,
                    os.O_RDONLY | nofollow,
                    dir_fd=out_dir.fd,
                )
                opened_previous = os.fstat(existing_fd)
                if (
                    not stat.S_ISREG(opened_previous.st_mode)
                    or (
                        opened_previous.st_dev,
                        opened_previous.st_ino,
                    )
                    != (previous.st_dev, previous.st_ino)
                ):
                    raise _SourceSidecarPublicationError(
                        "retained existing provenance sidecar because "
                        "it changed during verification"
                    )
                if opened_previous.st_size > max_sidecar_bytes:
                    raise _SourceSidecarLimitError(
                        "existing provenance sidecar exceeds "
                        f"{max_sidecar_bytes}-byte limit"
                    )
                existing_payload = bytearray()
                remaining = opened_previous.st_size
                os.lseek(existing_fd, 0, os.SEEK_SET)
                while remaining:
                    chunk = os.read(
                        existing_fd,
                        min(_FILE_COPY_CHUNK_BYTES, remaining),
                    )
                    if not chunk:
                        raise _SourceSidecarPublicationError(
                            "retained existing provenance sidecar "
                            "because it changed during verification"
                        )
                    existing_payload.extend(chunk)
                    remaining -= len(chunk)
                if len(existing_payload) != opened_previous.st_size:
                    raise _SourceSidecarPublicationError(
                        "retained existing provenance sidecar because "
                        "it changed during verification"
                    )
                trailing = os.read(existing_fd, 1)
                if trailing:
                    existing_payload.extend(trailing)
                    if len(existing_payload) > max_sidecar_bytes:
                        raise _SourceSidecarLimitError(
                            "existing provenance sidecar exceeds "
                            f"{max_sidecar_bytes}-byte limit"
                        )
                    raise _SourceSidecarPublicationError(
                        "retained existing provenance sidecar because "
                        "it changed during verification"
                    )
                final_opened = os.fstat(existing_fd)
                current = os.stat(
                    SOURCE_SIDECAR,
                    dir_fd=out_dir.fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(final_opened.st_mode)
                    or not stat.S_ISREG(current.st_mode)
                    or final_opened.st_size
                    != opened_previous.st_size
                    or final_opened.st_mtime_ns
                    != opened_previous.st_mtime_ns
                    or final_opened.st_ctime_ns
                    != opened_previous.st_ctime_ns
                    or (
                        final_opened.st_dev,
                        final_opened.st_ino,
                    )
                    != (previous.st_dev, previous.st_ino)
                    or (current.st_dev, current.st_ino)
                    != (previous.st_dev, previous.st_ino)
                    or current.st_size != previous.st_size
                    or current.st_mtime_ns
                    != previous.st_mtime_ns
                    or current.st_ctime_ns
                    != previous.st_ctime_ns
                ):
                    raise _SourceSidecarPublicationError(
                        "retained existing provenance sidecar because "
                        "it changed during verification"
                    )
                existing_payload = bytes(existing_payload)
                if existing_payload == payload:
                    sidecar_result = _SourceSidecarWriteResult()
                    return sidecar_result
                if not _authoritative_sidecar_payload(
                    existing_payload
                ):
                    raise _SourceSidecarPublicationError(
                        "retained existing provenance sidecar because "
                        "it differs from prepared provenance"
                    )
                sidecar_path = os.path.join(
                    out_dir.path,
                    SOURCE_SIDECAR,
                )
                journal = _ManagedOutputJournal(
                    out_dir,
                    internal_names=(SOURCE_SIDECAR,),
                    backup_prefix=(
                        ".paperconan-sidecar-rollback-"
                    ),
                    backup_entry_prefix="previous-",
                )
                try:
                    try:
                        prepared = journal.prepare(
                            sidecar_path,
                            expected={
                                "size": opened_previous.st_size,
                                "sha256": hashlib.sha256(
                                    existing_payload
                                ).hexdigest(),
                                "identity": (
                                    opened_previous.st_dev,
                                    opened_previous.st_ino,
                                ),
                            },
                            recovery_max_bytes=max_sidecar_bytes,
                        )
                    except _ManagedOutputRecoveryRequiredError as error:
                        raise _SourceSidecarPublicationError(
                            "retained provenance sidecar because it "
                            "changed during publication"
                        ) from error
                    if not prepared:
                        raise _SourceSidecarPublicationError(
                            "retained provenance sidecar because it "
                            "changed during publication"
                        )
                    try:
                        with _transaction_state_allocation():
                            os.link(
                                temp_name,
                                SOURCE_SIDECAR,
                                src_dir_fd=out_dir.fd,
                                dst_dir_fd=out_dir.fd,
                                follow_symlinks=False,
                            )
                    except FileExistsError as error:
                        raise _SourceSidecarPublicationError(
                            "retained existing provenance sidecar "
                            "created during publication"
                        ) from error
                    staged = os.fstat(fd)
                    journal.bind_published(
                        sidecar_path,
                        (staged.st_dev, staged.st_ino),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    )
                    pending_cleanup = tuple(journal.commit())
                except BaseException as operation_error:
                    rollback_error = None
                    try:
                        journal.rollback()
                    except _ManagedOutputRollbackError as error:
                        rollback_error = error
                    if (
                        isinstance(
                            operation_error,
                            _ManagedOutputRecoveryRequiredError,
                        )
                        or (
                            rollback_error is not None
                            and journal.recovery_paths()
                        )
                    ):
                        _raise_source_sidecar_recovery_required(
                            journal,
                            operation_error,
                            rollback_error,
                        )
                    if rollback_error is not None:
                        _raise_operation_with_rollback_errors(
                            operation_error,
                            [rollback_error],
                        )
                    raise
                finally:
                    journal.close()
            finally:
                if existing_fd >= 0:
                    os.close(existing_fd)
        sidecar_result = _SourceSidecarWriteResult(
            pending_cleanup=tuple(dict.fromkeys(
                pending_cleanup
            )),
        )
        return sidecar_result
    except BaseException as error:
        sidecar_operation_error = error
        raise
    finally:
        cleanup_error = None
        if fd >= 0:
            try:
                _unlink_owned_regular_entry(
                    out_dir.fd,
                    temp_name,
                    fd,
                )
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ) as error:
                cleanup_error = error
            try:
                os.close(fd)
            except OSError as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            if sidecar_operation_error is not None:
                _append_explicit_cause(
                    cleanup_error,
                    sidecar_operation_error,
                )
                raise _SourceSidecarPublicationError(
                    "source sidecar staging cleanup incomplete"
                ) from cleanup_error
            sidecar_result.cleanup_warning = (
                "source sidecar staging cleanup incomplete"
            )


def _sidecar_write_failure_record(error, *, operation):
    return {
        "name": SOURCE_SIDECAR,
        "reason": "could not commit source sidecar",
        "operation": operation,
        "error_type": type(error).__name__,
        "error": str(error),
        "ownership_preserved": True,
    }


def _safe_source_url(url):
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or any(
            character.isspace() or ord(character) < 32
            for character in hostname
        )
    ):
        return None
    authority_host = (
        f"[{hostname}]" if ":" in hostname else hostname
    )
    authority = (
        f"{authority_host}:{port}"
        if port is not None
        else authority_host
    )
    return urllib.parse.urlunsplit(
        (scheme, authority, parsed.path, "", "")
    )


def _provenance_entry(
    path,
    source_url,
    content_type=None,
    size=None,
):
    return {
        "file": os.path.basename(path),
        "source_url": _safe_source_url(source_url),
        "content_type": content_type,
        "asset_type": asset_type(os.path.basename(path)),
        "size": size,
    }


def _archive_provenance_entries(
    output,
    paths,
    source_url,
    published_outputs,
):
    by_name = {
        entry.filename: entry
        for entry in published_outputs
    }
    provenance = []
    for path in paths:
        name = os.path.basename(path)
        entry = by_name.get(name)
        if entry is None:
            try:
                state = _stable_managed_file(output, name)
            except (OSError, ValueError):
                continue
            entry = _PublishedOutputFile(
                filename=state.name,
                size=state.size,
                identity=state.identity,
                sha256=state.sha256,
                created=True,
            )
            published_outputs.append(entry)
            by_name[name] = entry
        provenance.append(_provenance_entry(
            entry.display_path(output),
            source_url,
            size=entry.size,
        ))
    return provenance


def _selected_candidate_files(
    cand,
    *,
    tabular_only,
    include_images,
):
    if tabular_only:
        selected = list(cand.get("tabular_files") or ())
    else:
        selected = list(
            cand.get("all_files")
            or cand.get("tabular_files")
            or ()
        )
    if include_images:
        selected.extend(cand.get("image_files") or ())
        selected.extend(
            file_ref
            for file_ref in cand.get("all_files") or ()
            if asset_type(file_ref.get("name") or "")
            == "document"
        )
    unique = []
    seen = set()
    for file_ref in selected:
        if not isinstance(file_ref, dict):
            continue
        key = (
            file_ref.get("name"),
            file_ref.get("download_url"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(file_ref)
    return unique


def _verified_managed_files(output, managed_files):
    if type(managed_files) is not dict:
        return {}
    candidates = []
    normalized_names = {}
    for name, supplied in sorted(managed_files.items()):
        expected = _canonical_fingerprint(supplied)
        safe_name = _safe_managed_name(name)
        if expected is None or safe_name is None:
            continue
        try:
            normalized = unicodedata.normalize(
                "NFC",
                safe_name,
            ).casefold()
        except (TypeError, ValueError):
            return {}
        previous_name = normalized_names.get(normalized)
        if previous_name is not None and previous_name != safe_name:
            return {}
        normalized_names[normalized] = safe_name
        candidates.append((safe_name, expected))

    verified = {}
    verified_identities = {}
    for name, expected in candidates:
        try:
            current = _stable_managed_file(
                output,
                name,
                expected=expected,
            )
        except (OSError, ValueError):
            continue
        previous_name = verified_identities.get(current.identity)
        if previous_name is not None and previous_name != name:
            return {}
        verified_identities[current.identity] = name
        verified[name] = expected
    return verified


def _bounded_in_root_transaction_state_pending(output):
    entries_seen = 0
    name_bytes = 0
    metadata_reads = 0
    try:
        entries = os.scandir(output.fd)
    except (OSError, TypeError, NotImplementedError, ValueError):
        return True
    try:
        with entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > _INTERNAL_STATE_ENTRY_LIMIT:
                    return True
                try:
                    encoded_name = os.fsencode(entry.name)
                except (OSError, TypeError, ValueError):
                    return True
                name_bytes += len(encoded_name)
                if name_bytes > _INTERNAL_STATE_NAME_BYTES:
                    return True
                metadata_reads += 1
                if metadata_reads > _INTERNAL_STATE_METADATA_LIMIT:
                    return True
                try:
                    entry.stat(follow_symlinks=False)
                except (
                    OSError,
                    TypeError,
                    NotImplementedError,
                    ValueError,
                ):
                    return True
                if entry.name.startswith(
                    _IN_ROOT_TRANSACTION_PREFIXES
                ):
                    return True
    except (OSError, TypeError, NotImplementedError, ValueError):
        return True
    return False


def _sibling_rollback_state_pending(output):
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if directory is None or nofollow is None:
        return True
    parent_fd = -1
    try:
        output.verify()
        parent_fd = os.open(
            "..",
            os.O_RDONLY | directory | nofollow,
            dir_fd=output.fd,
        )
        parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent.st_mode):
            return True
        name_bytes = 0
        metadata_reads = 0
        for prefix in _SIBLING_ROLLBACK_PREFIXES:
            name = _rollback_directory_name(output, prefix)
            name_bytes += len(os.fsencode(name))
            if name_bytes > _INTERNAL_STATE_NAME_BYTES:
                return True
            metadata_reads += 1
            if metadata_reads > _INTERNAL_STATE_METADATA_LIMIT:
                return True
            try:
                os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except (
                OSError,
                TypeError,
                NotImplementedError,
                ValueError,
            ):
                return True
            return True
        output.verify()
        return False
    except (
        OSError,
        TypeError,
        NotImplementedError,
        ValueError,
    ):
        return True
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _managed_internal_cleanup_pending(output):
    try:
        output.verify()
    except (OSError, TypeError, NotImplementedError, ValueError):
        return True
    if _bounded_in_root_transaction_state_pending(output):
        return True
    if _sibling_rollback_state_pending(output):
        return True
    try:
        output.verify()
    except (OSError, TypeError, NotImplementedError, ValueError):
        return True
    return False


def _managed_cleanup_pending_result(cand, out_dir):
    return {
        "cand_id": cand.get("cand_id"),
        "out_dir": _output_path(out_dir),
        "downloaded": [],
        "skipped": [{
            "name": "managed-output cleanup",
            "reason": "managed-output cleanup remains pending",
        }],
    }


def _verify_published_output_file(output, entry):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _UnstableRegularFileError(
            "published output verification is unavailable"
        )
    try:
        output.verify()
        fd = os.open(
            entry.filename,
            os.O_RDONLY | nofollow,
            dir_fd=output.fd,
        )
    except (OSError, TypeError, NotImplementedError) as error:
        raise _UnstableRegularFileError(
            "published output entry is unavailable"
        ) from error
    try:
        opened = os.fstat(fd)
        try:
            current = os.stat(
                entry.filename,
                dir_fd=output.fd,
                follow_symlinks=False,
            )
        except (OSError, TypeError, NotImplementedError) as error:
            raise _UnstableRegularFileError(
                "published output entry is unavailable"
            ) from error
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_size != entry.size
            or current.st_size != entry.size
            or (opened.st_dev, opened.st_ino) != entry.identity
            or (current.st_dev, current.st_ino) != entry.identity
            or current.st_mtime_ns != opened.st_mtime_ns
            or current.st_ctime_ns != opened.st_ctime_ns
        ):
            raise _UnstableRegularFileError(
                "published output entry is not a stable regular file"
            )
        digest = _hash_exact_fd(fd, entry.size)
        output.verify()
        try:
            final_opened = os.fstat(fd)
            final_current = os.stat(
                entry.filename,
                dir_fd=output.fd,
                follow_symlinks=False,
            )
        except (OSError, TypeError, NotImplementedError) as error:
            raise _UnstableRegularFileError(
                "published output entry is unavailable"
            ) from error
        if (
            not stat.S_ISREG(final_opened.st_mode)
            or not stat.S_ISREG(final_current.st_mode)
            or final_opened.st_size != entry.size
            or final_current.st_size != entry.size
            or (final_opened.st_dev, final_opened.st_ino)
            != entry.identity
            or (final_current.st_dev, final_current.st_ino)
            != entry.identity
            or final_opened.st_mtime_ns != opened.st_mtime_ns
            or final_opened.st_ctime_ns != opened.st_ctime_ns
            or final_current.st_mtime_ns != opened.st_mtime_ns
            or final_current.st_ctime_ns != opened.st_ctime_ns
        ):
            raise _UnstableRegularFileError(
                "published output entry is not a stable regular file"
            )
        if digest != entry.sha256:
            raise _UnstableRegularFileError(
                "published output entry content changed during publication"
            )
    finally:
        os.close(fd)


def _reconcile_publications(
    output,
    entries,
    *,
    attempts,
    report_verified=False,
):
    reconciled = []
    outcomes = []
    first_error = None
    seen = set()
    attempt_count = max(1, attempts)
    if not entries:
        for _attempt in range(attempt_count):
            try:
                output.verify()
            except (OSError, ValueError) as error:
                if first_error is None:
                    first_error = error
                continue
            break
        return reconciled, outcomes, first_error
    for entry in entries:
        key = (entry.filename, entry.identity)
        if key in seen:
            continue
        seen.add(key)
        entry_error = None
        for _attempt in range(attempt_count):
            attempt_error = None
            try:
                _verify_published_output_file(output, entry)
            except (OSError, ValueError) as error:
                attempt_error = error
            try:
                output.verify()
            except (OSError, ValueError) as error:
                if attempt_error is None:
                    attempt_error = error
            if attempt_error is not None:
                entry_error = attempt_error
                if first_error is None:
                    first_error = attempt_error
                continue
            reconciled.append(entry)
            if entry_error is not None:
                outcomes.append(
                    "recovered stable output after bounded verification "
                    f"retry: {entry.filename}"
                )
            elif report_verified:
                outcomes.append(
                    f"retained verified output: {entry.filename}"
                )
            break
        else:
            if not entry.created:
                outcomes.append(
                    "retained collision-reused output without reporting it: "
                    f"{entry.filename}"
                )
            else:
                outcomes.append(
                    "retained visible output for recovery without reporting "
                    f"it: {entry.filename}"
                )
    return reconciled, outcomes, first_error


def _reconcile_archive_publications(
    output,
    accepted,
    pending,
):
    reconciled, outcomes, first_error = _reconcile_publications(
        output,
        [*accepted, *pending],
        attempts=2,
        report_verified=True,
    )
    accepted[:] = reconciled
    pending.clear()
    return reconciled, outcomes, first_error


def _download_candidate(
    cand,
    out_dir,
    *,
    tabular_only,
    include_images,
    max_bytes,
    archive_max,
    output_journal,
):
    downloaded, skipped = [], []
    published_outputs = []
    cardinality = _CandidateCardinality(
        max_published_files=_MAX_PUBLISHED_FILES_PER_CANDIDATE,
        max_archive_members=_MAX_ARCHIVE_MEMBERS_PER_CANDIDATE,
    )
    display_out_dir = _output_path(out_dir)
    if _managed_internal_cleanup_pending(out_dir):
        return _managed_cleanup_pending_result(cand, out_dir)
    try:
        cand = _prepare_sidecar_candidate(cand)
    except _SourceSidecarLimit as error:
        error.record["ownership_preserved"] = True
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": display_out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    cand = dict(cand)
    provenance_downloads = []
    cand["_paperconan_downloads"] = provenance_downloads
    files = _selected_candidate_files(
        cand,
        tabular_only=tabular_only,
        include_images=include_images,
    )
    try:
        previous = _read_source_sidecar(out_dir)
    except _SourceSidecarLimit as error:
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": display_out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    has_prior_managed_descriptor = "managed_files" in previous
    old_managed = _verified_managed_files(
        out_dir,
        previous.get("managed_files"),
    )
    prior_sidecar = (
        has_prior_managed_descriptor
        or _verified_source_sidecar_identity(out_dir) is not None
    )
    if (
        not _identity_bound_mutation_available()
        and prior_sidecar
    ):
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": display_out_dir,
            "downloaded": downloaded,
            "skipped": [{
                "name": "managed-output cleanup",
                "reason": (
                    "managed-output refresh unavailable: "
                    "identity-bound mutation is unavailable"
                ),
            }],
        }
    reusable_names = set(old_managed)
    new_managed = {}
    preserved_managed = {}
    try:
        managed_name_accounting = _ManagedNameAccounting(
            cand,
            out_dir,
            old_managed,
            new_managed,
        )
    except _SourceSidecarLimit as error:
        error.record["ownership_preserved"] = True
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": display_out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    cap_state = {
        "exceeded": False,
        "ownership_blocked": False,
        "output_root_unavailable": False,
    }
    for file_ref in files:
        try:
            requested_name = str(
                file_ref.get("name") or ""
            )
            _managed_output_text_bytes(
                requested_name, field="requested"
            )
            requested_name = requested_name.strip()
            source_url = str(
                file_ref.get("download_url") or ""
            )
            _managed_output_text_bytes(
                source_url, field="source"
            )
            source_name = requested_name or source_url
            base = (
                os.path.basename(requested_name)
                or os.path.basename(
                    urllib.parse.urlsplit(source_url).path
                )
                or "download"
            )
            if base in (".", ".."):
                base = "download"
            if _is_reserved_source_sidecar(base):
                skipped.append({
                    "name": requested_name or base,
                    "reason": _RESERVED_SOURCE_SIDECAR_REASON,
                })
                continue
            if not cardinality.can_publish():
                skipped.append({
                    "name": requested_name or base,
                    "reason": _published_file_limit_reason(
                        cardinality
                    ),
                })
                break
            if (
                has_prior_managed_descriptor
                or _safe_managed_name(base) is None
                or _is_reserved_managed_name(base)
            ):
                output_name = _managed_output_name(
                    out_dir,
                    base,
                    source_name,
                    reusable_names,
                )
            else:
                output_name = _managed_output_candidate((base,))
        except _ManagedOutputNameLimit as error:
            skipped.append(
                error.record(ownership_preserved=True)
            )
            cap_state["exceeded"] = True
            cap_state["ownership_blocked"] = True
            preserved_managed.update(old_managed)
            break
        reuses_old = output_name in reusable_names
        reusable_names.discard(output_name)
        dest = os.path.join(out_dir.path, output_name)
        replacement_credit = (
            old_managed[output_name]["size"]
            if reuses_old
            else 0
        )
        remaining = (
            _MAX_PAPER_BYTES
            - _paper_data_size(out_dir)
            + replacement_credit
        )
        download_limit = (
            max_bytes
            if (
                not reuses_old
                and not has_prior_managed_descriptor
            )
            else min(max_bytes, remaining)
        )
        if download_limit <= 0:
            cap_state["exceeded"] = True
            skipped.append({
                "name": requested_name,
                "reason": "paper data exceeds per-paper cap",
            })
            if reuses_old:
                preserved_managed[output_name] = old_managed[
                    output_name
                ]
            continue
        suffix = os.path.splitext(output_name)[1]
        staging = _download_staging_file(
            out_dir,
            prefix=".paperconan-download-",
            suffix=suffix,
        )
        operation_error = None
        try:
            res = download_file(
                source_url,
                staging,
                max_bytes=download_limit,
            )
        except BaseException as error:
            operation_error = error
            _cleanup_download_staging(staging)
            raise
        abort_direct_files = False
        try:
            if res.get("ok"):
                staged = _stable_staging_state(staging)
                fingerprint = {
                    "size": staged.size,
                    "sha256": staged.sha256,
                }
                sidecar_limitation = (
                    managed_name_accounting.limitation_for(
                        output_name,
                        fingerprint,
                    )
                )
                if sidecar_limitation is not None:
                    sidecar_limitation["name"] = (
                        requested_name or output_name
                    )
                    skipped.append(sidecar_limitation)
                    cap_state["exceeded"] = True
                    if reuses_old:
                        preserved_managed[output_name] = (
                            old_managed[output_name]
                        )
                    continue
                published = _publish_download_staging(
                    out_dir,
                    staging,
                    output_name=output_name,
                    base=base,
                    source_name=source_name,
                    expected_old=(
                        old_managed.get(output_name)
                        if reuses_old
                        else None
                    ),
                    output_journal=output_journal,
                )
                if published.cleanup_warning is not None:
                    skipped.append({
                        "name": requested_name or published.name,
                        "reason": published.cleanup_warning,
                    })
                fingerprint = {
                    "size": published.size,
                    "sha256": published.sha256,
                }
                sidecar_limitation = (
                    managed_name_accounting.limitation_for(
                        published.name,
                        fingerprint,
                    )
                )
                projected_size = _paper_data_size(
                    out_dir,
                    (staging,),
                )
                if (
                    sidecar_limitation is not None
                    or projected_size > _MAX_PAPER_BYTES
                ):
                    _restore_managed_output(
                        output_journal,
                        os.path.join(
                            out_dir.path, published.name
                        ),
                    )
                    if sidecar_limitation is not None:
                        sidecar_limitation["name"] = (
                            requested_name or published.name
                        )
                        skipped.append(sidecar_limitation)
                    else:
                        skipped.append({
                            "name": requested_name,
                            "reason": (
                                "publication skipped because projected "
                                "paper data exceeds per-paper cap"
                            ),
                        })
                    cap_state["exceeded"] = True
                    if reuses_old:
                        preserved_managed[output_name] = (
                            old_managed[output_name]
                        )
                    continue
                managed_name_accounting.add(
                    published.name,
                    fingerprint,
                )
                published_path = os.path.join(
                    out_dir.path, published.name
                )
                downloaded.append(published_path)
                published_outputs.append(_PublishedOutputFile(
                    filename=published.name,
                    size=published.size,
                    identity=published.identity,
                    sha256=published.sha256,
                    created=published.created,
                    cleanup_warning=published.cleanup_warning,
                ))
                cardinality.record_publication()
                provenance_downloads.append(_provenance_entry(
                    published_path,
                    res.get("source_url") or source_url,
                    content_type=res.get("content_type"),
                    size=published.size,
                ))
            else:
                if (
                    remaining < max_bytes
                    and "exceeds max_bytes"
                    in str(res.get("skipped_reason") or "")
                ):
                    cap_state["exceeded"] = True
                skipped.append({
                    "name": requested_name,
                    "reason": res.get("skipped_reason"),
                })
                if reuses_old:
                    preserved_managed[output_name] = (
                        old_managed[output_name]
                    )
        except _ManagedOutputRecoveryRequiredError:
            raise
        except (_UnstableRegularFileError, ValueError) as error:
            if (
                reuses_old
                and isinstance(error, _ManagedOutputPrepareError)
            ):
                preserved_managed[output_name] = old_managed[
                    output_name
                ]
            skipped.append({
                "name": requested_name or output_name,
                "reason": (
                    "secure publication unavailable: "
                    f"{error}"
                ),
            })
            output_root_changed = (
                "output directory changed" in str(error)
            )
            if output_root_changed:
                cap_state["ownership_blocked"] = True
                cap_state["output_root_unavailable"] = True
                preserved_managed.update(old_managed)
                abort_direct_files = True
        finally:
            cleanup_context = _cleanup_download_staging(staging)
            if cleanup_context is not None:
                skipped.append({
                    "name": requested_name or output_name,
                    "reason": cleanup_context,
                })
        if abort_direct_files:
            break
    pkg = cand.get("oa_package")
    reusable_files = {
        name: old_managed[name]
        for name in reusable_names
        if name in old_managed
    }
    if (
        not cap_state["ownership_blocked"]
        and pkg
        and pkg.get("url")
    ):
        archive_start = len(downloaded)
        archive_ok, archive_preserved = _download_oa_package(
            pkg,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            archive_max=archive_max,
            reusable_names=reusable_files,
            managed_name_accounting=managed_name_accounting,
            cap_state=cap_state,
            output_journal=output_journal,
            include_images=include_images,
            cardinality=cardinality,
            published_outputs=published_outputs,
        )
        provenance_downloads.extend(_archive_provenance_entries(
            out_dir,
            downloaded[archive_start:],
            pkg.get("url"),
            published_outputs,
        ))
        preserved_managed.update({
            name: old_managed[name]
            for name in archive_preserved
            if name in old_managed
        })
        if not archive_ok:
            preserved_managed.update(old_managed)
    arch = cand.get("supplementary_archive")
    if (
        not cap_state["ownership_blocked"]
        and (not downloaded or include_images)
        and arch
        and arch.get("url")
    ):
        archive_start = len(downloaded)
        archive_ok, archive_preserved = _download_supplementary_archive(
            arch,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            archive_max=archive_max,
            reusable_names=reusable_files,
            managed_name_accounting=managed_name_accounting,
            cap_state=cap_state,
            output_journal=output_journal,
            include_images=include_images,
            cardinality=cardinality,
            published_outputs=published_outputs,
        )
        provenance_downloads.extend(_archive_provenance_entries(
            out_dir,
            downloaded[archive_start:],
            arch.get("url"),
            published_outputs,
        ))
        preserved_managed.update({
            name: old_managed[name]
            for name in archive_preserved
            if name in old_managed
        })
        if not archive_ok:
            preserved_managed.update(old_managed)

    boundary_error_seen = set()
    for boundary in ("initial", "final"):
        (
            published_outputs,
            outcomes,
            boundary_error,
        ) = _reconcile_publications(
            out_dir,
            published_outputs,
            attempts=2,
        )
        if boundary_error is not None:
            if "output directory changed" in str(boundary_error):
                cap_state["ownership_blocked"] = True
                cap_state["output_root_unavailable"] = True
            error_text = str(boundary_error)
            key = (boundary, error_text)
            already_reported = any(
                error_text in str(item.get("reason") or "")
                for item in skipped
            )
            if (
                key not in boundary_error_seen
                and not already_reported
            ):
                boundary_error_seen.add(key)
                reason = (
                    "published output verification required "
                    f"reconciliation at the {boundary} "
                    "reconciliation boundary before provenance "
                    f"publication: {boundary_error}"
                )
                if outcomes:
                    reason += "; " + "; ".join(outcomes)
                skipped.append({
                    "name": cand.get("cand_id"),
                    "reason": reason,
                })
    reconciled_names = {
        entry.filename for entry in published_outputs
    }
    for path in tuple(downloaded):
        name = os.path.basename(path)
        if name in reconciled_names:
            continue
        new_managed.pop(name, None)
        detached_backup = output_journal.abandon(path)
        if detached_backup is not None:
            _append_post_commit_cleanup_records(
                skipped,
                (detached_backup,),
            )
    downloaded[:] = [
        entry.display_path(out_dir)
        for entry in published_outputs
    ]
    provenance_downloads[:] = [
        entry
        for entry in provenance_downloads
        if entry.get("file") in reconciled_names
    ]

    managed_files = dict(preserved_managed)
    managed_files.update(new_managed)
    stale_managed = {
        name: fingerprint
        for name, fingerprint in old_managed.items()
        if name not in managed_files
    }
    committed_managed = dict(managed_files)
    committed_managed.update(stale_managed)
    preserve_previous_refresh = (
        cap_state["exceeded"]
        and bool(old_managed)
        and not new_managed
    )
    sidecar_fits = True
    sidecar_committed = False
    outputs_finalized_without_sidecar = False
    if (
        not cap_state["ownership_blocked"]
        and not preserve_previous_refresh
        and sidecar_fits
    ):
        try:
            cand["_paperconan_managed_files"] = committed_managed
            sidecar_result = _write_source_sidecar(
                cand,
                out_dir,
                downloads=provenance_downloads,
            )
        except (
            _SourceSidecarRecoveryRequiredError,
            _ManagedOutputRecoveryRequiredError,
        ):
            raise
        except (
            _SourceSidecarLimitError,
            _SourceSidecarPublicationError,
        ) as error:
            skipped.append({
                "name": SOURCE_SIDECAR,
                "reason": str(error),
            })
            outputs_finalized_without_sidecar = not old_managed
        except OSError as error:
            skipped.append(_sidecar_write_failure_record(
                error, operation="initial"
            ))
        else:
            if sidecar_result is None:
                skipped.append({
                    "name": SOURCE_SIDECAR,
                    "reason": (
                        "provenance sidecar publication unavailable"
                    ),
                })
                outputs_finalized_without_sidecar = not old_managed
            else:
                if sidecar_result.cleanup_warning is not None:
                    skipped.append({
                        "name": SOURCE_SIDECAR,
                        "reason": sidecar_result.cleanup_warning,
                    })
                _append_post_commit_cleanup_records(
                    skipped,
                    getattr(
                        sidecar_result,
                        "pending_cleanup",
                        (),
                    ),
                )
                sidecar_committed = True
    if sidecar_committed:
        pending_cleanup = output_journal.commit_after_sidecar()
        _append_post_commit_cleanup_records(
            skipped,
            pending_cleanup,
        )
        cleanup_journal = _ManagedOutputJournal(out_dir)
        try:
            failed_removals, absent_removals = (
                _stage_managed_file_cleanup(
                    out_dir, stale_managed, cleanup_journal
                )
            )
            failed_removals = set(failed_removals)
            absent_removals = set(absent_removals)
            for relative in sorted(failed_removals):
                skipped.append({
                    "name": relative,
                    "reason": "could not remove managed file",
                })
            final_managed = dict(managed_files)
            final_managed.update({
                relative: stale_managed[relative]
                for relative in failed_removals
                if relative in stale_managed
            })
            if final_managed != committed_managed:
                try:
                    cand["_paperconan_managed_files"] = final_managed
                    sidecar_result = _write_source_sidecar(
                        cand,
                        out_dir,
                        downloads=provenance_downloads,
                    )
                except (
                    _SourceSidecarRecoveryRequiredError,
                    _ManagedOutputRecoveryRequiredError,
                ):
                    raise
                except OSError as error:
                    cleanup_journal.rollback()
                    if absent_removals:
                        raise
                    skipped.append(_sidecar_write_failure_record(
                        error, operation="cleanup_narrowing"
                    ))
                else:
                    if sidecar_result.cleanup_warning is not None:
                        skipped.append({
                            "name": SOURCE_SIDECAR,
                            "reason": sidecar_result.cleanup_warning,
                        })
                    _append_post_commit_cleanup_records(
                        skipped,
                        getattr(
                            sidecar_result,
                            "pending_cleanup",
                            (),
                        ),
                    )
                    pending_cleanup = cleanup_journal.commit()
                    _append_post_commit_cleanup_records(
                        skipped,
                        pending_cleanup,
                    )
            else:
                pending_cleanup = cleanup_journal.commit()
                _append_post_commit_cleanup_records(
                    skipped,
                    pending_cleanup,
                )
        except BaseException as operation_error:
            try:
                cleanup_journal.rollback()
            except _ManagedOutputRollbackError as rollback_error:
                _raise_operation_with_rollback_errors(
                    operation_error,
                    [rollback_error],
                )
            raise
        finally:
            cleanup_journal.close()
    elif (
        outputs_finalized_without_sidecar
        or cap_state["output_root_unavailable"]
    ):
        pending_cleanup = output_journal.commit()
        _append_post_commit_cleanup_records(
            skipped,
            pending_cleanup,
        )
    else:
        rolled_back = output_journal.rollback()
        if rolled_back:
            downloaded[:] = [
                path for path in downloaded
                if os.path.abspath(path) not in rolled_back
            ]
    if cap_state["output_root_unavailable"]:
        skipped.append({
            "name": SOURCE_SIDECAR,
            "reason": "provenance sidecar publication unavailable",
        })
    return {"cand_id": cand.get("cand_id"), "out_dir": display_out_dir,
            "downloaded": downloaded, "skipped": skipped}


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX,
                       archive_max=_ARCHIVE_MAX, include_images=False):
    with _candidate_transaction_admission() as admitted:
        if not admitted:
            return _managed_cleanup_pending_result(
                cand,
                os.path.abspath(os.fspath(out_dir)),
            )
        with _pinned_output_directory(out_dir) as output:
            output_journal = _ManagedOutputJournal(output)
            try:
                try:
                    return _download_candidate(
                        cand,
                        output,
                        tabular_only=tabular_only,
                        include_images=include_images,
                        max_bytes=max_bytes,
                        archive_max=archive_max,
                        output_journal=output_journal,
                    )
                except _ManagedOutputRestoreFailure as failure:
                    rollback_errors = [failure.rollback_error]
                    try:
                        output_journal.rollback()
                    except _ManagedOutputRollbackError as rollback_error:
                        rollback_errors.append(rollback_error)
                    _raise_operation_with_rollback_errors(
                        failure.operation_error,
                        rollback_errors,
                    )
                except _ManagedOutputRollbackError as primary_error:
                    try:
                        output_journal.rollback()
                    except _ManagedOutputRollbackError as rollback_error:
                        raise primary_error from rollback_error
                    raise
                except BaseException as operation_error:
                    try:
                        output_journal.rollback()
                    except _ManagedOutputRollbackError as rollback_error:
                        _raise_operation_with_rollback_errors(
                            operation_error,
                            [rollback_error],
                        )
                    raise
            finally:
                output_journal.close()
