import io
from paperconan.fetch import _download


class _Resp(io.BytesIO):
    def __init__(self, data, ctype="application/octet-stream"):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def info(self): return self.headers


def test_download_file_rejects_html_error_page(monkeypatch, tmp_path):
    monkeypatch.setattr(_download.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(b"<html>nope</html>", "text/html"))
    res = _download.download_file("https://x/t.xlsx", str(tmp_path / "t.xlsx"))
    assert res["ok"] is False
    assert "html" in res["skipped_reason"].lower()
    assert not (tmp_path / "t.xlsx").exists()


def test_download_file_saves_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(_download.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(b"col\n1\n2\n", "text/csv"))
    dest = tmp_path / "t.csv"
    res = _download.download_file("https://x/t.csv", str(dest))
    assert res["ok"] is True
    assert dest.read_bytes() == b"col\n1\n2\n"


def test_download_file_auth_required_message(monkeypatch, tmp_path):
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("https://x/t.csv", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(_download.urllib.request, "urlopen", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()
    assert not (tmp_path / "t.csv").exists()


def test_download_candidate_tabular_only(monkeypatch, tmp_path):
    saved = []
    def fake_dl(url, dest, **kw):
        open(dest, "wb").write(b"x"); saved.append(dest); return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", fake_dl)
    cand = {"cand_id": "zenodo:1", "tabular_files": [
        {"name": "a.csv", "ext": "csv", "size": 5, "download_url": "https://x/a.csv"}]}
    summary = _download.download_candidate(cand, str(tmp_path))
    assert len(summary["downloaded"]) == 1
    assert summary["downloaded"][0].endswith("a.csv")


def test_download_candidate_writes_provenance_sidecar(monkeypatch, tmp_path):
    """Downloading must record where the data came from, so the later audit can
    stamp scan.json with the paper's DOI/title (provenance for archiving)."""
    import json
    monkeypatch.setattr(_download, "download_file",
                        lambda url, dest, **kw: (open(dest, "wb").write(b"x"),
                                                 {"ok": True, "path": dest})[1])
    cand = {"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.5281/zenodo.42",
            "title": "My deposited data", "related_dois": ["10.1038/paper"],
            "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 1,
                               "download_url": "https://x/a.csv"}]}
    _download.download_candidate(cand, str(tmp_path))
    sidecar = tmp_path / "paperconan_source.json"
    assert sidecar.exists(), "expected a provenance sidecar next to the downloads"
    p = json.loads(sidecar.read_text(encoding="utf-8"))
    assert p["doi"] == "10.5281/zenodo.42"
    assert p["cand_id"] == "zenodo:1"
    assert p["source"] == "zenodo"


def test_download_candidate_extracts_tabular_from_supplementary_zip(monkeypatch, tmp_path):
    """Europe PMC serves supplementary material as one zip — download_candidate must
    extract only the tabular members (xlsx/csv/tsv) into out_dir, dropping the rest,
    and flatten any internal paths (no path traversal)."""
    import io, os, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("nested/dir/table.xlsx", b"PK-fake-xlsx-bytes")
        z.writestr("figure.csv", b"a,b\n1,2\n")
        z.writestr("readme.txt", b"not data")
    zbytes = buf.getvalue()

    def fake_dl(url, dest, **kw):
        open(dest, "wb").write(zbytes)
        return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", fake_dl)

    cand = {"cand_id": "europepmc:PMC1", "source": "europepmc", "doi": "10.1038/x",
            "title": "T", "tabular_files": [],
            "supplementary_archive": {
                "url": "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/supplementaryFiles",
                "name": "PMC1_supplementary.zip"}}
    summary = _download.download_candidate(cand, str(tmp_path))

    names = sorted(os.path.basename(p) for p in summary["downloaded"])
    assert names == ["figure.csv", "table.xlsx"]
    assert not (tmp_path / "readme.txt").exists()
    assert not (tmp_path / "PMC1_supplementary.zip").exists(), "zip should be cleaned up"


def test_download_file_rejects_non_http_scheme(tmp_path):
    res = _download.download_file("file:///etc/passwd", str(tmp_path / "x.csv"))
    assert res["ok"] is False
    assert "scheme" in res["skipped_reason"].lower()
    assert not (tmp_path / "x.csv").exists()


def test_download_file_rejects_oversize_via_content_length(monkeypatch, tmp_path):
    def big(req, timeout=None):
        r = _Resp(b"x", "text/csv")
        r.headers["Content-Length"] = "999999999"
        return r
    monkeypatch.setattr(_download.urllib.request, "urlopen", big)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"), max_bytes=1000)
    assert res["ok"] is False
    assert "max_bytes" in res["skipped_reason"]
    assert not (tmp_path / "t.csv").exists()


def test_download_file_rejects_oversize_via_body(monkeypatch, tmp_path):
    payload = b"a" * 50
    monkeypatch.setattr(_download.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(payload, "text/csv"))
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"), max_bytes=10)
    assert res["ok"] is False
    assert "max_bytes" in res["skipped_reason"]
    assert not (tmp_path / "t.csv").exists()


def test_download_file_403_message(monkeypatch, tmp_path):
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("https://x/t.csv", 403, "Forbidden", {}, None)
    monkeypatch.setattr(_download.urllib.request, "urlopen", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()
