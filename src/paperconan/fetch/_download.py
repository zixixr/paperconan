"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
import os
import urllib.error
import urllib.request

_UA = "paperconan-fetch/0.4 (+https://github.com/zixixr/paperconan)"
_DEFAULT_MAX = 50 * 1024 * 1024  # 50 MB


def download_file(url, dest_path, timeout=60, max_bytes=_DEFAULT_MAX):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.info().get("Content-Type") or "").lower()
            if "text/html" in ctype:
                return {"ok": False, "path": dest_path,
                        "skipped_reason": f"server returned HTML ({ctype}), not a data file"}
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


def download_candidate(cand, out_dir, tabular_only=True, max_bytes=_DEFAULT_MAX):
    if tabular_only:
        files = cand.get("tabular_files", [])
    else:
        files = cand.get("all_files") or cand.get("tabular_files", [])
    os.makedirs(out_dir, exist_ok=True)
    downloaded, skipped = [], []
    for f in files:
        dest = os.path.join(out_dir, os.path.basename(f["name"]))
        res = download_file(f["download_url"], dest, max_bytes=max_bytes)
        if res.get("ok"):
            downloaded.append(res["path"])
        else:
            skipped.append({"name": f["name"], "reason": res.get("skipped_reason")})
    return {"cand_id": cand.get("cand_id"), "out_dir": out_dir,
            "downloaded": downloaded, "skipped": skipped}
