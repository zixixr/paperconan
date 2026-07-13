"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
from bisect import bisect_right
import codecs
from collections import Counter
import hashlib
import json
import os
import struct
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from paperconan._input import is_supported_input
from paperconan._source_sidecar import (
    SidecarLimitError,
    encode_sidecar,
    read_sidecar,
)

# Provenance sidecar written next to downloads; read back by scan_dir to stamp scan.json.
SOURCE_SIDECAR = "paperconan_source.json"

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
_ZIP_UTF8_FILENAME_FLAG = 1 << 11
_ZIP64_EXTRA_FIELD = 0x0001
_ZIP_UNICODE_PATH_EXTRA_FIELD = 0x7075


class _SizeLimitExceeded(ValueError):
    pass


def _dir_size(path, exclude_paths=()):
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


def _atomic_stream_write(src, dest_path, max_bytes):
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
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
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


class _ManagedOutputJournal:
    def __init__(self, out_dir):
        self._parent = os.path.dirname(os.path.abspath(out_dir))
        self._backup_dir = None
        self._entries = {}
        self._committed = False

    def prepare(self, dest_path):
        if self._committed:
            raise RuntimeError("managed-output journal is committed")
        dest_path = os.path.abspath(dest_path)
        if dest_path in self._entries:
            return
        backup_path = None
        if os.path.lexists(dest_path):
            if self._backup_dir is None:
                self._backup_dir = tempfile.mkdtemp(
                    prefix=".paperconan-output-rollback-",
                    dir=self._parent,
                )
            backup_path = os.path.join(
                self._backup_dir, str(len(self._entries))
            )
            os.replace(dest_path, backup_path)
        self._entries[dest_path] = backup_path

    def restore(self, dest_path, *, cleanup=True):
        dest_path = os.path.abspath(dest_path)
        if dest_path not in self._entries:
            return
        backup_path = self._entries[dest_path]
        if backup_path is None:
            try:
                os.remove(dest_path)
            except FileNotFoundError:
                pass
        else:
            os.replace(backup_path, dest_path)
        self._entries.pop(dest_path)
        if cleanup:
            self._cleanup_backup_dir()

    def discard(self, dest_path):
        dest_path = os.path.abspath(dest_path)
        if dest_path not in self._entries:
            return
        backup_path = self._entries.pop(dest_path)
        if backup_path is not None:
            try:
                os.remove(backup_path)
            except FileNotFoundError:
                pass
        self._cleanup_backup_dir()

    def rollback(self):
        if self._committed:
            self.commit()
            return set()
        paths = set(self._entries)
        failures = []
        for dest_path in reversed(tuple(self._entries)):
            try:
                self.restore(dest_path, cleanup=False)
            except OSError as error:
                failures.append((dest_path, error))
        cleanup_failure = None
        if not self._entries:
            try:
                self._cleanup_backup_dir()
            except _ManagedOutputDirectoryCleanupError as error:
                cleanup_failure = (error.backup_dir, error.error)
        if failures or cleanup_failure is not None:
            raise _ManagedOutputRollbackError(
                failures,
                cleanup_failure=cleanup_failure,
            )
        return paths

    def commit(self):
        self._committed = True
        for dest_path, backup_path in tuple(self._entries.items()):
            if backup_path is None:
                self._entries.pop(dest_path, None)
                continue
            removed = False
            for _attempt in range(2):
                try:
                    os.remove(backup_path)
                    removed = True
                    break
                except FileNotFoundError:
                    removed = True
                    break
                except OSError:
                    continue
            if removed:
                self._entries.pop(dest_path, None)

        pending = [
            backup_path
            for backup_path in self._entries.values()
            if backup_path is not None
        ]
        if not self._entries and self._backup_dir is not None:
            backup_dir = self._backup_dir
            removed = False
            for _attempt in range(2):
                try:
                    os.rmdir(backup_dir)
                    removed = True
                    break
                except FileNotFoundError:
                    removed = True
                    break
                except OSError:
                    continue
            if removed:
                self._backup_dir = None
            else:
                pending.append(backup_dir)
        return pending

    def _cleanup_backup_dir(self):
        if self._entries or self._backup_dir is None:
            return
        backup_dir = self._backup_dir
        try:
            os.rmdir(backup_dir)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise _ManagedOutputDirectoryCleanupError(
                backup_dir,
                error,
            ) from error
        self._backup_dir = None


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
    if not url.lower().startswith(("https://", "http://")):
        return {"ok": False, "path": dest_path,
                "skipped_reason": f"unsupported URL scheme: {url!r}"}
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last_reason = "unknown error"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = (resp.info().get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    return {"ok": False, "path": dest_path,
                            "skipped_reason": f"server returned HTML ({ctype}), not a data file"}
                clen = resp.info().get("Content-Length")
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    return {"ok": False, "path": dest_path,
                            "skipped_reason": f"file exceeds max_bytes ({max_bytes})"}
                try:
                    size = _atomic_stream_write(resp, dest_path, max_bytes)
                except _SizeLimitExceeded as e:
                    return {"ok": False, "path": dest_path,
                            "skipped_reason": str(e)}
                return {"ok": True, "path": dest_path, "size": size}
        except urllib.error.HTTPError as e:
            try:
                if e.code in (401, 403):
                    return {"ok": False, "path": dest_path,
                            "skipped_reason": (f"requires authentication (HTTP {e.code}); "
                                               "download this file manually from the dataset page")}
                last_reason = f"HTTP {e.code}: {e.reason}"
                if not (500 <= e.code < 600):
                    return {"ok": False, "path": dest_path, "skipped_reason": last_reason}
            finally:
                e.close()
        except Exception as e:
            last_reason = f"download error: {e}"
        if attempt < retries - 1:
            time.sleep(backoff * (2 ** attempt))
    return {"ok": False, "path": dest_path, "skipped_reason": last_reason}


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
            in (list, tuple, set, frozenset)
            else None
        )

    lexical_root = os.path.abspath(out_dir)
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
    path = os.path.join(out_dir, SOURCE_SIDECAR)
    byte_limit = max(0, int(_SOURCE_SIDECAR_MAX_BYTES))
    entry_limit = max(0, int(_SOURCE_SIDECAR_ENTRY_LIMIT))
    name_byte_limit = max(
        0, int(_SOURCE_SIDECAR_NAME_BYTES)
    )
    lexical_root = os.path.abspath(out_dir)

    def normalize_name(relative):
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            return None
        return os.path.relpath(path, lexical_root)

    try:
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
    failed = []
    for relative in _safe_managed_names(out_dir, managed_files):
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            failed.append(relative)
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            failed.append(relative)
    return failed


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
        return (
            not os.path.lexists(os.path.join(out_dir, name))
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
            and is_supported_input(member.name)
        ):
            state["eligible_members_retained"] += 1
        return member


class _BoundedZipFile(zipfile.ZipFile):
    """Read only budgeted central-directory metadata into ZipFile state."""

    def __init__(self, file, *, archive_name):
        self.archive_name = archive_name
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
                and is_supported_input(info.filename)
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


def _collect_bounded_tar_members(archive, archive_name):
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
            and is_supported_input(member.name)
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
    extracted = []
    preserved = set()
    skipped = list(initial_skipped)
    coverage_limited = bool(skipped)
    written = _dir_size(out_dir, transient_paths)
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
    reusable = set(_safe_managed_names(out_dir, reusable_names))
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
                if reuses_old and os.path.lexists(
                    os.path.join(out_dir, name)
                ):
                    preserved.add(name)
                continue
        reusable.discard(name)
        dest = os.path.join(out_dir, name)
        sidecar_delta = (
            managed_name_accounting.replacement_delta_with(name)
            if managed_name_accounting is not None
            else 0
        )
        remaining, replacement_credit = (
            _remaining_final_size_allowance(
                written,
                dest,
                reuses_old,
                sidecar_delta,
            )
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
            if reuses_old and os.path.lexists(dest):
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
            if reuses_old and os.path.lexists(dest):
                preserved.add(name)
            continue
        if output_journal is not None:
            output_journal.prepare(dest)
        committed = False
        try:
            src = open_member(member)
            if src is None:
                raise OSError("could not open archive member")
            with src:
                size = _atomic_stream_write(
                    src, dest, write_limit
                )
                committed = True
        except _TarArchiveLimit as error:
            if output_journal is not None:
                _restore_managed_output(output_journal, dest)
            if reuses_old and os.path.lexists(dest):
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
            if output_journal is not None:
                _restore_managed_output(output_journal, dest)
            if reuses_old and os.path.lexists(dest):
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
                written = written - replacement_credit + size
                extracted.append(dest)
                if managed_name_accounting is not None:
                    managed_name_accounting.add(name)
                skipped.append({
                    "name": source_name,
                    "reason": (
                        "archive member close failed after commit: "
                        f"{e}"
                    ),
                })
                continue
            if output_journal is not None:
                _restore_managed_output(
                    output_journal,
                    dest,
                    operation_error=e,
                )
            if reuses_old and os.path.lexists(dest):
                preserved.add(name)
            skipped.append({
                "name": source_name,
                "reason": f"archive member failed: {e}",
            })
            continue
        written = written - replacement_credit + size
        extracted.append(dest)
        if managed_name_accounting is not None:
            managed_name_accounting.add(name)
    if coverage_limited:
        for name in reusable:
            if os.path.lexists(os.path.join(out_dir, name)):
                preserved.add(name)
    return extracted, preserved, skipped


def _extract_tabular_zip(
    zip_path,
    out_dir,
    max_member_bytes=_DEFAULT_MAX,
    *,
    reusable_names=(),
):
    """Extract scanner-supported inputs from a supplementary zip into
    out_dir, flattening internal paths to the basename (no path traversal) and
    capping per-member size. Returns the list of extracted file paths."""
    extracted, _, _ = _extract_tabular_zip_managed(
        zip_path,
        out_dir,
        max_member_bytes,
        reusable_names=reusable_names,
    )
    return extracted


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
):
    stable_archive_name = archive_name or os.path.basename(zip_path)
    with _BoundedZipFile(
        zip_path, archive_name=stable_archive_name
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
):
    """Extract scanner-supported inputs from a .tar.gz into out_dir,
    flattening internal paths to the basename and capping per-member size.
    Returns the list of extracted file paths."""
    extracted, _, _ = _extract_tabular_tar_managed(
        tar_path,
        out_dir,
        max_member_bytes,
        reusable_names=reusable_names,
    )
    return extracted


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
):
    stable_archive_name = (
        archive_name or os.path.basename(tar_path)
    )
    try:
        tf = _BoundedTarFile.open(
            tar_path,
            "r:gz",
            tarinfo=_BoundedTarInfo,
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
                tf, stable_archive_name
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


def _temporary_archive_path(out_dir, suffix):
    fd, path = tempfile.mkstemp(
        prefix=".paperconan-archive-",
        suffix=suffix,
        dir=out_dir,
    )
    os.close(fd)
    return path


def _download_oa_package(
    pkg,
    out_dir,
    downloaded,
    skipped,
    max_bytes,
    *,
    reusable_names=(),
    managed_name_accounting=None,
    cap_state=None,
    output_journal=None,
):
    """Download the static PMC OA tar.gz, extract its tabular members, drop the tarball."""
    tmp = _temporary_archive_path(out_dir, ".tar.gz")
    try:
        res = download_file(pkg["url"], tmp, max_bytes=_ARCHIVE_MAX)
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
                )
            )
            skipped.extend(member_skipped)
            downloaded.extend(extracted)
        except (tarfile.TarError, OSError) as e:
            skipped.append({
                "name": pkg.get("name"),
                "reason": f"bad tar.gz: {e}",
            })
            return False, set()
        return True, preserved
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes,
                                    archive_max=_ARCHIVE_MAX, *,
                                    reusable_names=(),
                                    managed_name_accounting=None,
                                    cap_state=None,
                                    output_journal=None):
    """Fetch a supplementary zip (Europe PMC), extract its tabular members, drop the zip.

    The archive downloads with the larger ``archive_max`` cap; each extracted table is
    still capped at the per-file ``max_bytes``."""
    tmp_zip = _temporary_archive_path(out_dir, ".zip")
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
                )
            )
            skipped.extend(member_skipped)
            downloaded.extend(extracted)
        except (zipfile.BadZipFile, OSError):
            skipped.append({
                "name": arch.get("name"),
                "reason": "not a valid zip archive",
            })
            return False, set()
        return True, preserved
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass


def _source_sidecar_provenance(cand, managed_files):
    related_dois = cand.get("related_dois")
    if related_dois is None:
        related_dois = []
    return {
        "doi": cand.get("doi"),
        "title": cand.get("title"),
        "source": cand.get("source"),
        "cand_id": cand.get("cand_id"),
        "related_dois": related_dois,
        "managed_files": managed_files,
    }


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
    safe_names = _safe_managed_names(
        out_dir,
        managed_files,
        entry_limit=max(0, int(_SOURCE_SIDECAR_ENTRY_LIMIT)),
        name_byte_limit=max(
            0, int(_SOURCE_SIDECAR_NAME_BYTES)
        ),
    )
    return _encode_source_sidecar(
        _source_sidecar_provenance(cand, safe_names)
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
        _source_sidecar_provenance(prepared, [])
    )
    prepared["related_dois"] = retained
    return prepared


def _encoded_json_name_bytes(name):
    return len(json.dumps(name).encode("utf-8"))


def _managed_name_list_extra_bytes(count, encoded_name_bytes):
    if count <= 0:
        return 0
    return encoded_name_bytes + 8 + 6 * (count - 1)


class _ManagedNameAccounting:
    def __init__(
        self,
        cand,
        out_dir,
        old_names,
        new_names,
    ):
        self._old_names = old_names
        self._new_names = new_names
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
            self._previous_size = os.path.getsize(
                os.path.join(out_dir, SOURCE_SIDECAR)
            )
        except OSError:
            pass
        self._entry_count = len(old_names)
        self._name_bytes = sum(
            len(name.encode("utf-8", errors="surrogatepass"))
            for name in old_names
        )
        self._encoded_name_bytes = sum(
            _encoded_json_name_bytes(name)
            for name in old_names
        )
        self._base_size = len(_encode_source_sidecar(
            _source_sidecar_provenance(cand, [])
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
        return name in self._old_names or name in self._new_names

    def _prospective_counts(self, name):
        if self._contains(name):
            return (
                self._entry_count,
                self._name_bytes,
                self._encoded_name_bytes,
            )
        return (
            self._entry_count + 1,
            self._name_bytes + len(
                name.encode("utf-8", errors="surrogatepass")
            ),
            self._encoded_name_bytes
            + _encoded_json_name_bytes(name),
        )

    def _payload_size(
        self,
        *,
        entry_count=None,
        encoded_name_bytes=None,
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
        return (
            self._base_size
            + _managed_name_list_extra_bytes(count, encoded)
        )

    def limitation_for(self, name):
        entry_count, name_bytes, encoded_name_bytes = (
            self._prospective_counts(name)
        )
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
        )
        if payload_size > self._sidecar_byte_limit:
            return _source_sidecar_limit_record(
                "source sidecar byte limit",
                limit=self._sidecar_byte_limit,
                observed_bytes=payload_size,
            )
        return None

    def replacement_delta_with(self, name):
        entry_count, _name_bytes, encoded_name_bytes = (
            self._prospective_counts(name)
        )
        return (
            self._payload_size(
                entry_count=entry_count,
                encoded_name_bytes=encoded_name_bytes,
            )
            - self._previous_size
        )

    def add(self, name):
        if name in self._new_names:
            return
        already_accounted = name in self._old_names
        self._new_names.add(name)
        if already_accounted:
            return
        self._entry_count += 1
        self._name_bytes += len(
            name.encode("utf-8", errors="surrogatepass")
        )
        self._encoded_name_bytes += _encoded_json_name_bytes(name)

    def replacement_delta(self):
        return self._payload_size() - self._previous_size


def _source_sidecar_replacement_delta(cand, out_dir, managed_files):
    path = os.path.join(out_dir, SOURCE_SIDECAR)
    try:
        previous_size = os.path.getsize(path)
    except OSError:
        previous_size = 0
    return (
        len(_source_sidecar_bytes(cand, out_dir, managed_files))
        - previous_size
    )


def _write_source_sidecar(cand, out_dir, managed_files):
    """Record which paper/dataset these downloads came from, for scan.json provenance."""
    payload = _source_sidecar_bytes(cand, out_dir, managed_files)
    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{SOURCE_SIDECAR}.",
            suffix=".part",
            dir=out_dir,
        )
        stream = os.fdopen(fd, "wb")
        fd = None
        with stream as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, os.path.join(out_dir, SOURCE_SIDECAR))
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _download_candidate(
    cand,
    out_dir,
    *,
    tabular_only,
    max_bytes,
    archive_max,
    output_journal,
):
    downloaded, skipped = [], []
    try:
        cand = _prepare_sidecar_candidate(cand)
    except _SourceSidecarLimit as error:
        error.record["ownership_preserved"] = True
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    if tabular_only:
        files = cand.get("tabular_files", [])
    else:
        files = cand.get("all_files") or cand.get("tabular_files", [])
    try:
        previous = _read_source_sidecar(out_dir)
    except _SourceSidecarLimit as error:
        return {
            "cand_id": cand.get("cand_id"),
            "out_dir": out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    old_managed = set(previous.get("managed_files") or ())
    reusable_names = set(old_managed)
    new_managed = set()
    preserved_managed = set()
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
            "out_dir": out_dir,
            "downloaded": downloaded,
            "skipped": [error.record],
        }
    cap_state = {
        "exceeded": False,
        "ownership_blocked": False,
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
            output_name = _managed_output_name(
                out_dir, base, source_name, reusable_names
            )
        except _ManagedOutputNameLimit as error:
            skipped.append(
                error.record(ownership_preserved=True)
            )
            cap_state["exceeded"] = True
            cap_state["ownership_blocked"] = True
            preserved_managed.update(old_managed)
            break
        reuses_old = output_name in reusable_names
        sidecar_limitation = (
            managed_name_accounting.limitation_for(output_name)
        )
        if sidecar_limitation is not None:
            sidecar_limitation["name"] = (
                requested_name or output_name
            )
            skipped.append(sidecar_limitation)
            cap_state["exceeded"] = True
            preserved_managed.update(old_managed)
            continue
        reusable_names.discard(output_name)
        dest = os.path.join(out_dir, output_name)
        sidecar_delta = (
            managed_name_accounting.replacement_delta_with(
                output_name
            )
        )
        remaining, _ = _remaining_final_size_allowance(
            _dir_size(out_dir),
            dest,
            reuses_old,
            sidecar_delta,
        )
        download_limit = min(max_bytes, remaining)
        if download_limit <= 0:
            cap_state["exceeded"] = True
            skipped.append({
                "name": requested_name,
                "reason": "paper data exceeds per-paper cap",
            })
            if reuses_old:
                preserved_managed.add(output_name)
            continue
        output_journal.prepare(dest)
        try:
            res = download_file(
                source_url,
                dest,
                max_bytes=download_limit,
            )
        except BaseException as error:
            _restore_managed_output(
                output_journal,
                dest,
                operation_error=error,
            )
            raise
        if res.get("ok"):
            downloaded.append(res["path"])
            managed_name_accounting.add(output_name)
        else:
            _restore_managed_output(output_journal, dest)
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
                preserved_managed.add(output_name)
    pkg = cand.get("oa_package")
    if (
        not cap_state["ownership_blocked"]
        and pkg
        and pkg.get("url")
    ):
        archive_ok, archive_preserved = _download_oa_package(
            pkg,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            reusable_names=reusable_names,
            managed_name_accounting=managed_name_accounting,
            cap_state=cap_state,
            output_journal=output_journal,
        )
        preserved_managed.update(archive_preserved)
        if not archive_ok:
            preserved_managed.update(old_managed)
    arch = cand.get("supplementary_archive")
    if (
        not cap_state["ownership_blocked"]
        and not downloaded
        and arch
        and arch.get("url")
    ):
        archive_ok, archive_preserved = _download_supplementary_archive(
            arch,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            archive_max=archive_max,
            reusable_names=reusable_names,
            managed_name_accounting=managed_name_accounting,
            cap_state=cap_state,
            output_journal=output_journal,
        )
        preserved_managed.update(archive_preserved)
        if not archive_ok:
            preserved_managed.update(old_managed)
    managed_files = new_managed | preserved_managed
    stale_managed = old_managed - managed_files
    committed_managed = managed_files | stale_managed
    preserve_previous_refresh = (
        cap_state["exceeded"]
        and bool(old_managed)
        and not new_managed
    )
    sidecar_delta = managed_name_accounting.replacement_delta()
    sidecar_fits = (
        _dir_size(out_dir) + sidecar_delta <= _MAX_PAPER_BYTES
    )
    sidecar_committed = (
        not cap_state["ownership_blocked"]
        and
        not preserve_previous_refresh
        and sidecar_fits
        and _write_source_sidecar(cand, out_dir, committed_managed)
    )
    if sidecar_committed:
        pending_cleanup = output_journal.commit()
        for path in pending_cleanup:
            skipped.append({
                "name": os.path.basename(path),
                "reason": "post-commit cleanup pending",
                "path": path,
            })
        failed_removals = set(
            _remove_managed_files(out_dir, stale_managed)
        )
        for relative in sorted(failed_removals):
            skipped.append({
                "name": relative,
                "reason": "could not remove managed file",
            })
        final_managed = managed_files | failed_removals
        if final_managed != committed_managed:
            _write_source_sidecar(cand, out_dir, final_managed)
    else:
        rolled_back = output_journal.rollback()
        if rolled_back:
            downloaded[:] = [
                path for path in downloaded
                if os.path.abspath(path) not in rolled_back
            ]
    return {"cand_id": cand.get("cand_id"), "out_dir": out_dir,
            "downloaded": downloaded, "skipped": skipped}


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX,
                       archive_max=_ARCHIVE_MAX):
    os.makedirs(out_dir, exist_ok=True)
    output_journal = _ManagedOutputJournal(out_dir)
    try:
        return _download_candidate(
            cand,
            out_dir,
            tabular_only=tabular_only,
            max_bytes=max_bytes,
            archive_max=archive_max,
            output_journal=output_journal,
        )
    except _ManagedOutputRestoreFailure as failure:
        try:
            output_journal.rollback()
        except _ManagedOutputRollbackError as rollback_error:
            raise failure.operation_error from rollback_error
        raise failure.operation_error from failure.rollback_error
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
            raise operation_error from rollback_error
        raise
