"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import os
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from paperconan._input import is_supported_input

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


def _safe_managed_names(out_dir, managed_files):
    if isinstance(managed_files, str):
        entries = [managed_files]
    else:
        try:
            entries = list(managed_files or ())
        except TypeError:
            entries = []

    lexical_root = os.path.abspath(out_dir)
    safe = set()
    for relative in entries:
        if not isinstance(relative, str):
            continue
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        safe.add(os.path.relpath(path, lexical_root))
    return sorted(safe)


def _read_source_sidecar(out_dir):
    path = os.path.join(out_dir, SOURCE_SIDECAR)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    data = dict(data)
    if "managed_files" in data:
        managed_files = data.get("managed_files")
        if not isinstance(managed_files, list):
            managed_files = []
        data["managed_files"] = _safe_managed_names(
            out_dir, managed_files
        )
    return data


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


def _managed_output_name(out_dir, base, source_name, reusable_names):
    reusable = {
        name for name in (reusable_names or ())
        if (
            isinstance(name, str)
            and not _is_reserved_managed_name(name)
        )
    }
    base = os.path.basename(base) or "download"
    if base in (".", ".."):
        base = "download"
    digest = hashlib.sha256(
        str(source_name).encode("utf-8")
    ).hexdigest()
    if _is_reserved_managed_name(base):
        _, suffix = os.path.splitext(base)
        base = f"download--{digest[:10]}{suffix.lower()}"

    def available(name):
        return (
            not _is_reserved_managed_name(name)
            and (
                name in reusable
                or not os.path.lexists(os.path.join(out_dir, name))
            )
        )

    if available(base):
        return base

    stem, suffix = os.path.splitext(base)
    for width in range(10, len(digest) + 1, 2):
        candidate = f"{stem}--{digest[:width]}{suffix.lower()}"
        if available(candidate):
            return candidate

    counter = 2
    while True:
        candidate = (
            f"{stem}--{digest}-{counter}{suffix.lower()}"
        )
        if available(candidate):
            return candidate
        counter += 1


def _archive_output_names(member_names):
    eligible = sorted(member_names)
    counts = Counter(
        os.path.basename(name).casefold()
        for name in eligible
    )
    out = {}
    for member in eligible:
        base = os.path.basename(member)
        if counts[base.casefold()] == 1:
            out[member] = base
            continue
        stem, suffix = os.path.splitext(base)
        digest = hashlib.sha256(member.encode("utf-8")).hexdigest()[:10]
        out[member] = f"{stem}--{digest}{suffix.lower()}"
    return out


def _allocate_archive_output_names(preferred_names):
    used = set()
    allocated = []
    for preferred in preferred_names:
        candidate = preferred
        stem, suffix = os.path.splitext(preferred)
        disambiguator = 2
        while candidate.casefold() in used:
            candidate = f"{stem}--{disambiguator}{suffix}"
            disambiguator += 1
        used.add(candidate.casefold())
        allocated.append(candidate)
    return allocated


def _archive_occurrence_output_names(member_names):
    member_names = list(member_names)
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
        out.append(f"{stem}--{seen[member]}{suffix}")
    return _allocate_archive_output_names(out)


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
    sidecar_delta_for_names=None,
    cap_state=None,
):
    extracted = []
    preserved = set()
    skipped = []
    written = _dir_size(out_dir, transient_paths)
    preferred_names = _archive_occurrence_output_names(
        member_name(member) for member in members
    )
    reusable = set(_safe_managed_names(out_dir, reusable_names))
    accepted_names = set()
    for member, preferred in zip(members, preferred_names):
        source_name = member_name(member)
        name = _managed_output_name(
            out_dir, preferred, source_name, reusable
        )
        reuses_old = name in reusable
        reusable.discard(name)
        dest = os.path.join(out_dir, name)
        sidecar_delta = (
            sidecar_delta_for_names(accepted_names | {name})
            if sidecar_delta_for_names is not None
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
        if (
            write_limit <= 0
            or member_size(member) > write_limit
        ):
            if (
                cap_state is not None
                and remaining < max_member_bytes
            ):
                cap_state["exceeded"] = True
            if reuses_old and os.path.lexists(dest):
                preserved.add(name)
            continue
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
        except _SizeLimitExceeded:
            if reuses_old and os.path.lexists(dest):
                preserved.add(name)
            continue
        except member_errors as e:
            if committed:
                written = written - replacement_credit + size
                extracted.append(dest)
                accepted_names.add(name)
                skipped.append({
                    "name": source_name,
                    "reason": (
                        "archive member close failed after commit: "
                        f"{e}"
                    ),
                })
                continue
            if reuses_old and os.path.lexists(dest):
                preserved.add(name)
            skipped.append({
                "name": source_name,
                "reason": f"archive member failed: {e}",
            })
            continue
        written = written - replacement_credit + size
        extracted.append(dest)
        accepted_names.add(name)
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
    sidecar_delta_for_names=None,
    cap_state=None,
):
    with zipfile.ZipFile(zip_path) as zf:
        infos = [
            info for info in zf.infolist()
            if not info.is_dir() and is_supported_input(info.filename)
        ]
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
            sidecar_delta_for_names=sidecar_delta_for_names,
            cap_state=cap_state,
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
    sidecar_delta_for_names=None,
    cap_state=None,
):
    with tarfile.open(tar_path, "r:gz") as tf:
        members = [
            member for member in tf.getmembers()
            if member.isfile() and is_supported_input(member.name)
        ]
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
            sidecar_delta_for_names=sidecar_delta_for_names,
            cap_state=cap_state,
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
    sidecar_delta_for_names=None,
    cap_state=None,
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
                    sidecar_delta_for_names=sidecar_delta_for_names,
                    cap_state=cap_state,
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
                                    sidecar_delta_for_names=None,
                                    cap_state=None):
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
                    sidecar_delta_for_names=sidecar_delta_for_names,
                    cap_state=cap_state,
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


def _source_sidecar_bytes(cand, out_dir, managed_files):
    prov = {
        "doi": cand.get("doi"),
        "title": cand.get("title"),
        "source": cand.get("source"),
        "cand_id": cand.get("cand_id"),
        "related_dois": cand.get("related_dois") or [],
        "managed_files": _safe_managed_names(out_dir, managed_files),
    }
    return (
        json.dumps(
            prov,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    ).encode("utf-8")


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


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX,
                       archive_max=_ARCHIVE_MAX):
    if tabular_only:
        files = cand.get("tabular_files", [])
    else:
        files = cand.get("all_files") or cand.get("tabular_files", [])
    os.makedirs(out_dir, exist_ok=True)
    previous = _read_source_sidecar(out_dir)
    old_managed = set(previous.get("managed_files") or ())
    reusable_names = set(old_managed)
    downloaded, skipped = [], []
    new_managed = set()
    preserved_managed = set()
    cap_state = {"exceeded": False}
    for file_ref in files:
        requested_name = str(file_ref.get("name") or "").strip()
        source_url = str(file_ref.get("download_url") or "")
        source_name = requested_name or source_url
        base = (
            os.path.basename(requested_name)
            or os.path.basename(urllib.parse.urlsplit(source_url).path)
            or "download"
        )
        if base in (".", ".."):
            base = "download"
        output_name = _managed_output_name(
            out_dir, base, source_name, reusable_names
        )
        reuses_old = output_name in reusable_names
        reusable_names.discard(output_name)
        dest = os.path.join(out_dir, output_name)
        potential_managed = old_managed | new_managed | {output_name}
        sidecar_delta = _source_sidecar_replacement_delta(
            cand, out_dir, potential_managed
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
        res = download_file(
            source_url,
            dest,
            max_bytes=download_limit,
        )
        if res.get("ok"):
            downloaded.append(res["path"])
            new_managed.add(output_name)
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
                preserved_managed.add(output_name)
    pkg = cand.get("oa_package")
    if pkg and pkg.get("url"):
        archive_start = len(downloaded)
        archive_base_managed = old_managed | new_managed

        def archive_sidecar_delta(names):
            return _source_sidecar_replacement_delta(
                cand,
                out_dir,
                archive_base_managed | set(names),
            )

        archive_ok, archive_preserved = _download_oa_package(
            pkg,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            reusable_names=reusable_names,
            sidecar_delta_for_names=archive_sidecar_delta,
            cap_state=cap_state,
        )
        preserved_managed.update(archive_preserved)
        if not archive_ok:
            preserved_managed.update(old_managed)
        for path in downloaded[archive_start:]:
            new_managed.add(os.path.relpath(path, out_dir))
    arch = cand.get("supplementary_archive")
    if not downloaded and arch and arch.get("url"):
        archive_start = len(downloaded)
        archive_base_managed = old_managed | new_managed

        def archive_sidecar_delta(names):
            return _source_sidecar_replacement_delta(
                cand,
                out_dir,
                archive_base_managed | set(names),
            )

        archive_ok, archive_preserved = _download_supplementary_archive(
            arch,
            out_dir,
            downloaded,
            skipped,
            max_bytes,
            archive_max=archive_max,
            reusable_names=reusable_names,
            sidecar_delta_for_names=archive_sidecar_delta,
            cap_state=cap_state,
        )
        preserved_managed.update(archive_preserved)
        if not archive_ok:
            preserved_managed.update(old_managed)
        for path in downloaded[archive_start:]:
            new_managed.add(os.path.relpath(path, out_dir))
    managed_files = new_managed | preserved_managed
    stale_managed = old_managed - managed_files
    committed_managed = managed_files | stale_managed
    preserve_previous_refresh = (
        cap_state["exceeded"]
        and bool(old_managed)
        and not new_managed
    )
    sidecar_delta = _source_sidecar_replacement_delta(
        cand, out_dir, committed_managed
    )
    sidecar_fits = (
        _dir_size(out_dir) + sidecar_delta <= _MAX_PAPER_BYTES
    )
    if (
        not preserve_previous_refresh
        and sidecar_fits
        and _write_source_sidecar(cand, out_dir, committed_managed)
    ):
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
    return {"cand_id": cand.get("cand_id"), "out_dir": out_dir,
            "downloaded": downloaded, "skipped": skipped}
