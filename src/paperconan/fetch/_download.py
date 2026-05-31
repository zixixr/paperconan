"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
import io
import json
import os
import urllib.error
import urllib.request
import zipfile

from ._files import is_tabular

# Provenance sidecar written next to downloads; read back by scan_dir to stamp scan.json.
SOURCE_SIDECAR = "paperconan_source.json"

_UA = "paperconan-fetch/0.4 (+https://github.com/zixixr/paperconan)"
_DEFAULT_MAX = 50 * 1024 * 1024  # 50 MB


def download_file(url, dest_path, timeout=60, max_bytes=_DEFAULT_MAX):
    if not url.lower().startswith(("https://", "http://")):
        return {"ok": False, "path": dest_path,
                "skipped_reason": f"unsupported URL scheme: {url!r}"}
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
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
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "path": dest_path,
                    "skipped_reason": (f"requires authentication (HTTP {e.code}); "
                                       "download this file manually from the dataset page")}
        return {"ok": False, "path": dest_path,
                "skipped_reason": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "path": dest_path, "skipped_reason": f"download error: {e}"}
    if len(data) > max_bytes:
        return {"ok": False, "path": dest_path,
                "skipped_reason": f"file exceeds max_bytes ({max_bytes})"}
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(data)
    return {"ok": True, "path": dest_path, "size": len(data)}


def _extract_tabular_zip(zip_bytes, out_dir, max_member_bytes=_DEFAULT_MAX):
    """Extract only tabular members (.xlsx/.csv/.tsv) from a supplementary zip into
    out_dir, flattening internal paths to the basename (no path traversal) and
    capping per-member size. Returns the list of extracted file paths."""
    extracted = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = os.path.basename(info.filename)
            if not name or not is_tabular(name) or info.file_size > max_member_bytes:
                continue
            dest = os.path.join(out_dir, name)
            with zf.open(info) as src, open(dest, "wb") as fh:
                fh.write(src.read(max_member_bytes + 1)[:max_member_bytes])
            extracted.append(dest)
    return extracted


def _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes):
    """Fetch a supplementary zip (Europe PMC), extract its tabular members, drop the zip."""
    tmp_zip = os.path.join(out_dir, arch.get("name") or "supplementary.zip")
    res = download_file(arch["url"], tmp_zip, max_bytes=max_bytes)
    if not res.get("ok"):
        skipped.append({"name": arch.get("name"), "reason": res.get("skipped_reason")})
        return
    try:
        with open(tmp_zip, "rb") as fh:
            downloaded.extend(_extract_tabular_zip(fh.read(), out_dir, max_bytes))
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


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX):
    if tabular_only:
        files = cand.get("tabular_files", [])
    else:
        files = cand.get("all_files") or cand.get("tabular_files", [])
    os.makedirs(out_dir, exist_ok=True)
    _write_source_sidecar(cand, out_dir)
    downloaded, skipped = [], []
    for f in files:
        dest = os.path.join(out_dir, os.path.basename(f["name"]))
        res = download_file(f["download_url"], dest, max_bytes=max_bytes)
        if res.get("ok"):
            downloaded.append(res["path"])
        else:
            skipped.append({"name": f["name"], "reason": res.get("skipped_reason")})
    arch = cand.get("supplementary_archive")
    if arch and arch.get("url"):
        _download_supplementary_archive(arch, out_dir, downloaded, skipped, max_bytes)
    return {"cand_id": cand.get("cand_id"), "out_dir": out_dir,
            "downloaded": downloaded, "skipped": skipped}
