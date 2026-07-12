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


def _dir_size(path):
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
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


def _extract_tabular_zip(zip_path, out_dir, max_member_bytes=_DEFAULT_MAX):
    """Extract scanner-supported inputs from a supplementary zip into
    out_dir, flattening internal paths to the basename (no path traversal) and
    capping per-member size. Returns the list of extracted file paths."""
    extracted = []
    written = _dir_size(out_dir)
    with zipfile.ZipFile(zip_path) as zf:
        infos = [
            info for info in zf.infolist()
            if not info.is_dir() and is_supported_input(info.filename)
        ]
        names = _archive_occurrence_output_names(
            info.filename for info in infos
        )
        for info, name in zip(infos, names):
            if written > _MAX_PAPER_BYTES:        # per-paper budget reached; stop extracting
                break
            if not name or info.file_size > max_member_bytes:
                continue
            dest = os.path.join(out_dir, name)
            with zf.open(info) as src:
                try:
                    size = _atomic_stream_write(src, dest, max_member_bytes)
                except _SizeLimitExceeded:
                    continue
            written += size
            extracted.append(dest)
    return extracted


def _extract_tabular_tar(tar_path, out_dir, max_member_bytes=_DEFAULT_MAX):
    """Extract scanner-supported inputs from a .tar.gz into out_dir,
    flattening internal paths to the basename and capping per-member size.
    Returns the list of extracted file paths."""
    extracted = []
    written = _dir_size(out_dir)
    with tarfile.open(tar_path, "r:gz") as tf:
        members = [
            member for member in tf.getmembers()
            if member.isfile() and is_supported_input(member.name)
        ]
        names = _archive_occurrence_output_names(
            member.name for member in members
        )
        for member, name in zip(members, names):
            if written > _MAX_PAPER_BYTES:        # per-paper budget reached; stop extracting
                break
            if not name or member.size > max_member_bytes:
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            dest = os.path.join(out_dir, name)
            with src:
                try:
                    size = _atomic_stream_write(src, dest, max_member_bytes)
                except _SizeLimitExceeded:
                    continue
            written += size
            extracted.append(dest)
    return extracted


def _download_oa_package(pkg, out_dir, downloaded, skipped, max_bytes):
    """Download the static PMC OA tar.gz, extract its tabular members, drop the tarball."""
    tmp = os.path.join(out_dir, pkg.get("name") or "oa_package.tar.gz")
    res = download_file(pkg["url"], tmp, max_bytes=_ARCHIVE_MAX)
    if not res.get("ok"):
        skipped.append({"name": pkg.get("name"), "reason": res.get("skipped_reason")})
        return
    try:
        downloaded.extend(_extract_tabular_tar(tmp, out_dir, max_bytes))
    except (tarfile.TarError, OSError) as e:
        skipped.append({"name": pkg.get("name"), "reason": f"bad tar.gz: {e}"})
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes,
                                    archive_max=_ARCHIVE_MAX):
    """Fetch a supplementary zip (Europe PMC), extract its tabular members, drop the zip.

    The archive downloads with the larger ``archive_max`` cap; each extracted table is
    still capped at the per-file ``max_bytes``."""
    tmp_zip = os.path.join(out_dir, arch.get("name") or "supplementary.zip")
    res = download_file(arch["url"], tmp_zip, max_bytes=archive_max)
    if not res.get("ok"):
        skipped.append({"name": arch.get("name"), "reason": res.get("skipped_reason")})
        return
    try:
        downloaded.extend(_extract_tabular_zip(tmp_zip, out_dir, max_bytes))
    except zipfile.BadZipFile:
        skipped.append({"name": arch.get("name"), "reason": "not a valid zip archive"})
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass


def _write_source_sidecar(cand, out_dir):
    """Record which paper/dataset these downloads came from, for scan.json provenance."""
    prov = {"doi": cand.get("doi"), "title": cand.get("title"),
            "source": cand.get("source"), "cand_id": cand.get("cand_id"),
            "related_dois": cand.get("related_dois") or []}
    try:
        with open(os.path.join(out_dir, SOURCE_SIDECAR), "w", encoding="utf-8") as fh:
            json.dump(prov, fh, indent=2, default=str)
    except OSError:
        pass  # provenance is best-effort; never fail a download over it


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX,
                       archive_max=_ARCHIVE_MAX):
    if tabular_only:
        files = cand.get("tabular_files", [])
    else:
        files = cand.get("all_files") or cand.get("tabular_files", [])
    os.makedirs(out_dir, exist_ok=True)
    _write_source_sidecar(cand, out_dir)
    downloaded, skipped = [], []
    for f in files:
        if _dir_size(out_dir) > _MAX_PAPER_BYTES:   # per-paper budget reached; stop downloading
            skipped.append({"name": f["name"], "reason": "paper data exceeds per-paper cap"})
            continue
        dest = os.path.join(out_dir, os.path.basename(f["name"]))
        res = download_file(f["download_url"], dest, max_bytes=max_bytes)
        if res.get("ok"):
            downloaded.append(res["path"])
        else:
            skipped.append({"name": f["name"], "reason": res.get("skipped_reason")})
    pkg = cand.get("oa_package")
    if pkg and pkg.get("url"):
        _download_oa_package(pkg, out_dir, downloaded, skipped, max_bytes)
    arch = cand.get("supplementary_archive")
    if not downloaded and arch and arch.get("url"):
        _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes,
                                        archive_max=archive_max)
    return {"cand_id": cand.get("cand_id"), "out_dir": out_dir,
            "downloaded": downloaded, "skipped": skipped}
