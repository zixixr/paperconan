from contextlib import contextmanager
import hashlib
import io
import json
import os
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from paperconan.fetch import _download, _http


@pytest.fixture(autouse=True)
def _install_identity_bound_cleanup_capability(monkeypatch):
    def identity_bound_remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        opened = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (opened.st_dev, opened.st_ino) != (
            visible.st_dev,
            visible.st_ino,
        ):
            raise _download._UnstableRegularFileError(
                "identity-bound cleanup target changed"
            )
        if is_directory:
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        identity_bound_remove,
    )
    monkeypatch.setattr(
        _download,
        "_identity_bound_mutation_available",
        lambda: True,
        raising=False,
    )


class _Resp(io.BytesIO):
    def __init__(
        self,
        data,
        ctype="application/octet-stream",
        final_url="https://cdn.example.test/download",
    ):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}
        self.final_url = final_url
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def info(self): return self.headers
    def geturl(self): return self.final_url


def test_download_file_rejects_html_error_page(monkeypatch, tmp_path):
    monkeypatch.setattr(_http, "open_http",
                        lambda req, timeout=None: _Resp(b"<html>nope</html>", "text/html"))
    res = _download.download_file("https://x/t.xlsx", str(tmp_path / "t.xlsx"))
    assert res["ok"] is False
    assert "html" in res["skipped_reason"].lower()
    assert not (tmp_path / "t.xlsx").exists()


def test_download_file_saves_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(_http, "open_http",
                        lambda req, timeout=None: _Resp(b"col\n1\n2\n", "text/csv"))
    dest = tmp_path / "t.csv"
    res = _download.download_file("https://x/t.csv", str(dest))
    assert res["ok"] is True
    assert dest.read_bytes() == b"col\n1\n2\n"


def test_download_file_ignores_global_urlopen_replacement(
    monkeypatch, tmp_path
):
    global_calls = []
    safe_calls = []
    final_url = "https://cdn.example.test/table.csv"

    def replaced_urlopen(req, timeout=None):
        global_calls.append(req.full_url)
        raise AssertionError("global urlopen must not be used")

    def safe_open(req, timeout=None):
        safe_calls.append(req.full_url)
        return _Resp(
            b"a,b\n1,2\n",
            "text/csv",
            final_url=final_url,
        )

    monkeypatch.setattr(
        _download.urllib.request,
        "urlopen",
        replaced_urlopen,
    )
    monkeypatch.setattr(_http, "open_http", safe_open)
    destination = tmp_path / "table.csv"

    result = _download.download_file(
        "https://repository.example.test/table.csv",
        str(destination),
        retries=1,
    )

    assert global_calls == []
    assert safe_calls == [
        "https://repository.example.test/table.csv"
    ]
    assert result["ok"] is True
    assert result["source_url"] == final_url
    assert destination.read_bytes() == b"a,b\n1,2\n"


def test_download_file_auth_required_message(monkeypatch, tmp_path):
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("https://x/t.csv", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(_http, "open_http", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()
    assert not (tmp_path / "t.csv").exists()


def test_download_file_closes_http_error_without_masking_auth_result(
    monkeypatch,
    tmp_path,
):
    import urllib.error

    class ErrorBody(io.BytesIO):
        def __init__(self):
            super().__init__(b"must not be read")
            self.was_closed = False
            self.close_error = True

        def close(self):
            self.was_closed = True
            if self.close_error:
                self.close_error = False
                raise OSError("synthetic close failure")
            super().close()

    body = ErrorBody()
    error = urllib.error.HTTPError(
        "https://x/t.csv",
        403,
        "Forbidden",
        {},
        body,
    )

    def reject(req, timeout=None):
        raise error

    monkeypatch.setattr(_http, "open_http", reject)

    result = _download.download_file(
        "https://x/t.csv",
        str(tmp_path / "t.csv"),
    )

    assert result["ok"] is False
    assert "authentication" in result["skipped_reason"]
    assert body.was_closed


def test_download_candidate_tabular_only(monkeypatch, tmp_path):
    saved = []
    def stub_download(url, dest, **kw):
        open(dest, "wb").write(b"x"); saved.append(dest); return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_download)
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
        z.writestr("nested/dir/table.xlsx", b"PK-stub-xlsx-bytes")
        z.writestr("figure.csv", b"a,b\n1,2\n")
        z.writestr("readme.txt", b"not data")
    zbytes = buf.getvalue()

    def stub_download(url, dest, **kw):
        open(dest, "wb").write(zbytes)
        return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_download)

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


def test_supplementary_archive_downloads_with_larger_cap_than_per_file(monkeypatch, tmp_path):
    """A supplementary zip bundles ALL supplementary material (often 100MB+ of video),
    yet we only extract its small tabular members. So the archive must download with a
    much larger byte cap than an individual file, or big-but-tabular zips get truncated
    and silently lost (the failure seen on Europe PMC archives)."""
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("table.csv", b"a,b\n1,2\n")
    zbytes = buf.getvalue()
    calls = []
    def stub_download(url, dest, **kw):
        calls.append({"url": url, "max_bytes": kw.get("max_bytes")})
        open(dest, "wb").write(zbytes)
        return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_download)
    cand = {"cand_id": "europepmc:PMC1", "source": "europepmc", "tabular_files": [],
            "supplementary_archive": {"url": "https://ebi/PMC1/supplementaryFiles",
                                      "name": "PMC1.zip"}}
    _download.download_candidate(cand, str(tmp_path))
    arch_call = next(c for c in calls if c["url"].endswith("supplementaryFiles"))
    assert arch_call["max_bytes"] == _download._ARCHIVE_MAX
    assert _download._ARCHIVE_MAX > _download._DEFAULT_MAX


def test_supplementary_archive_extraction_still_caps_each_table(monkeypatch, tmp_path):
    """The larger archive cap must NOT relax the per-table cap: an individual table
    bigger than the per-file limit is still skipped (one bloated sheet shouldn't slip in
    just because it rode inside an archive)."""
    import io, os, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("small.csv", b"a,b\n1,2\n")        # ~8 bytes, kept
        z.writestr("huge.csv", b"x" * 500)            # 500 bytes, over the per-table cap
    zbytes = buf.getvalue()
    monkeypatch.setattr(_download, "download_file",
                        lambda url, dest, **kw: (open(dest, "wb").write(zbytes),
                                                 {"ok": True, "path": dest})[1])
    cand = {"cand_id": "europepmc:PMC1", "source": "europepmc", "tabular_files": [],
            "supplementary_archive": {"url": "https://ebi/PMC1/supplementaryFiles",
                                      "name": "PMC1.zip"}}
    summary = _download.download_candidate(cand, str(tmp_path), max_bytes=100)
    names = sorted(os.path.basename(p) for p in summary["downloaded"])
    assert names == ["small.csv"]
    assert not (tmp_path / "huge.csv").exists()


def test_download_file_rejects_non_http_scheme(tmp_path):
    res = _download.download_file("file:///etc/passwd", str(tmp_path / "x.csv"))
    assert res["ok"] is False
    assert "scheme" in res["skipped_reason"].lower()
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.test/data.csv",
        "https:///missing-host.csv",
        "https://example.test:not-a-port/data.csv",
        "https://[2001:db8::1/data.csv",
    ],
)
def test_download_file_rejects_invalid_initial_http_url(
    monkeypatch,
    tmp_path,
    url,
):
    def reject_open(*args, **kwargs):
        raise AssertionError("invalid initial URL must not be opened")

    monkeypatch.setattr(_http, "open_http", reject_open)

    res = _download.download_file(url, str(tmp_path / "x.csv"), retries=1)

    assert res["ok"] is False
    assert res["skipped_reason"] == "invalid download URL"
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize(
    "final_url",
    [
        "ftp://cdn.example.test/data.csv",
        "https://user:secret@cdn.example.test/data.csv",
        "https:///missing-host.csv",
        "https://cdn.example.test:not-a-port/data.csv",
        "https://[2001:db8::1/data.csv",
    ],
)
def test_download_file_rejects_invalid_final_redirect_url(
    monkeypatch,
    tmp_path,
    final_url,
):
    response = _Resp(b"a,b\n1,2\n", "text/csv", final_url=final_url)
    monkeypatch.setattr(
        _http,
        "open_http",
        lambda req, timeout=None: response,
    )

    res = _download.download_file(
        "https://repository.example.test/data.csv",
        str(tmp_path / "data.csv"),
        retries=1,
    )

    assert res["ok"] is False
    assert res["skipped_reason"] == "download URL rejected by HTTP(S) policy"
    assert not (tmp_path / "data.csv").exists()


@pytest.mark.parametrize("failure_point", ["redirect", "final"])
def test_download_file_url_policy_error_is_terminal_without_retry_or_sleep(
    monkeypatch,
    tmp_path,
    failure_point,
):
    open_calls = []
    sleep_calls = []

    def invalid_final_response(req, timeout=None):
        open_calls.append(req.full_url)
        if failure_point == "redirect":
            raise _http.URLPolicyError("HTTP redirect URL is invalid")
        return _Resp(
            b"a,b\n1,2\n",
            "text/csv",
            final_url="ftp://cdn.example.test/data.csv",
        )

    monkeypatch.setattr(_http, "open_http", invalid_final_response)
    monkeypatch.setattr(
        _download.time,
        "sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    result = _download.download_file(
        "https://repository.example.test/data.csv",
        str(tmp_path / "data.csv"),
    )

    assert result == {
        "ok": False,
        "path": str(tmp_path / "data.csv"),
        "skipped_reason": "download URL rejected by HTTP(S) policy",
    }
    assert open_calls == ["https://repository.example.test/data.csv"]
    assert sleep_calls == []


def test_download_file_allows_https_cdn_final_url(monkeypatch, tmp_path):
    final_url = "https://cdn.example.net/files/data.csv"
    response = _Resp(b"a,b\n1,2\n", "text/csv", final_url=final_url)
    monkeypatch.setattr(
        _http,
        "open_http",
        lambda req, timeout=None: response,
    )
    destination = tmp_path / "data.csv"

    res = _download.download_file(
        "https://repository.example.test/data.csv",
        str(destination),
        retries=1,
    )

    assert res["ok"] is True
    assert res["source_url"] == final_url
    assert destination.read_bytes() == b"a,b\n1,2\n"


def test_download_file_rejects_oversize_via_content_length(monkeypatch, tmp_path):
    def big(req, timeout=None):
        r = _Resp(b"x", "text/csv")
        r.headers["Content-Length"] = "999999999"
        return r
    monkeypatch.setattr(_http, "open_http", big)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"), max_bytes=1000)
    assert res["ok"] is False
    assert "max_bytes" in res["skipped_reason"]
    assert not (tmp_path / "t.csv").exists()


def test_download_file_rejects_oversize_via_body(monkeypatch, tmp_path):
    payload = b"a" * 50
    monkeypatch.setattr(_http, "open_http",
                        lambda req, timeout=None: _Resp(payload, "text/csv"))
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"), max_bytes=10)
    assert res["ok"] is False
    assert "max_bytes" in res["skipped_reason"]
    assert not (tmp_path / "t.csv").exists()


def test_download_file_403_message(monkeypatch, tmp_path):
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("https://x/t.csv", 403, "Forbidden", {}, None)
    monkeypatch.setattr(_http, "open_http", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()


def test_download_candidate_images_are_additive_and_default_stays_tabular(monkeypatch, tmp_path):
    calls = []

    def stub_download(url, dest, **kwargs):
        open(dest, "wb").write(b"x")
        calls.append(dest)
        return {
            "ok": True,
            "path": dest,
            "size": 1,
            "content_type": "application/octet-stream",
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{"name": "data.csv", "download_url": "https://x/data.csv"}],
        "image_files": [{"name": "Fig1.png", "download_url": "https://x/Fig1.png"}],
        "all_files": [
            {"name": "data.csv", "download_url": "https://x/data.csv"},
            {"name": "Fig1.png", "download_url": "https://x/Fig1.png"},
        ],
    }

    default_dir = tmp_path / "default"
    default = _download.download_candidate(cand, str(default_dir))
    assert [Path(p).name for p in default["downloaded"]] == ["data.csv"]

    image_dir = tmp_path / "images"
    image = _download.download_candidate(cand, str(image_dir), include_images=True)
    assert sorted(Path(p).name for p in image["downloaded"]) == ["Fig1.png", "data.csv"]


def test_image_archive_same_basenames_do_not_overwrite(monkeypatch, tmp_path):
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("figures/Fig1.png", b"first-image")
        archive.writestr("supplement/Fig1.png", b"second-image")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload.getvalue())
        return {"ok": True, "path": dest, "size": len(payload.getvalue())}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "tabular_files": [],
        "image_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles",
            "name": "supplementary.zip",
        },
    }
    summary = _download.download_candidate(
        candidate,
        str(tmp_path),
        include_images=True,
    )
    names = sorted(Path(path).name for path in summary["downloaded"])
    assert len(names) == 2
    assert "Fig1.png" in names
    assert any(name.startswith("Fig1-") for name in names)


def test_default_direct_table_download_does_not_also_fetch_archive(monkeypatch, tmp_path):
    calls = []

    def stub_download(url, dest, **kwargs):
        calls.append(url)
        Path(dest).write_bytes(b"table")
        return {"ok": True, "path": dest, "size": 5}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [
            {"name": "data.csv", "download_url": "https://example.test/data.csv"},
        ],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert calls == ["https://example.test/data.csv"]


def test_image_archive_runs_when_direct_table_download_succeeds(monkeypatch, tmp_path):
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("figures/Fig1.png", b"image")

    def stub_download(url, dest, **kwargs):
        if url.endswith("data.csv"):
            Path(dest).write_bytes(b"table")
            return {"ok": True, "path": dest, "size": 5}
        Path(dest).write_bytes(payload.getvalue())
        return {"ok": True, "path": dest, "size": len(payload.getvalue())}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [
            {"name": "data.csv", "download_url": "https://example.test/data.csv"},
        ],
        "image_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(
        candidate,
        str(tmp_path),
        include_images=True,
    )

    assert sorted(Path(path).name for path in summary["downloaded"]) == [
        "Fig1.png",
        "data.csv",
    ]


def test_identical_archive_file_preserves_direct_download_and_provenance(
    monkeypatch,
    tmp_path,
):
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("figures/Fig1.png", b"image")

    direct_url = "https://example.test/Fig1.png?signature=secret#fragment"

    def stub_download(url, dest, **kwargs):
        if url == direct_url:
            Path(dest).write_bytes(b"image")
            return {
                "ok": True,
                "path": dest,
                "size": 5,
                "content_type": "image/png",
                "source_url": url,
            }
        Path(dest).write_bytes(payload.getvalue())
        return {
            "ok": True,
            "path": dest,
            "size": len(payload.getvalue()),
            "content_type": "application/zip",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "image_files": [
            {"name": "Fig1.png", "download_url": direct_url},
        ],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles?token=archive",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(
        candidate,
        str(tmp_path),
        include_images=True,
    )
    sidecar = json.loads(
        (tmp_path / "paperconan_source.json").read_text(encoding="utf-8")
    )

    assert [Path(path).name for path in summary["downloaded"]] == ["Fig1.png"]
    assert sidecar["downloads"] == [{
        "file": "Fig1.png",
        "source_url": "https://example.test/Fig1.png",
        "content_type": "image/png",
        "asset_type": "image",
        "size": 5,
    }]


def test_direct_files_with_same_name_publish_distinct_files_and_provenance(
    monkeypatch,
    tmp_path,
):
    payloads = {
        "https://example.test/first/Fig1.png": b"first-image",
        "https://example.test/second/Fig1.png": b"second-image",
    }

    def stub_download(url, dest, **kwargs):
        data = payloads[url]
        Path(dest).write_bytes(data)
        return {
            "ok": True,
            "path": dest,
            "size": len(data),
            "content_type": "image/png",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "image_files": [
            {"name": "Fig1.png", "download_url": url}
            for url in payloads
        ],
    }

    summary = _download.download_candidate(
        candidate,
        str(tmp_path),
        include_images=True,
    )
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    published = {Path(path).name: Path(path).read_bytes() for path in summary["downloaded"]}
    second_digest = hashlib.sha256(payloads["https://example.test/second/Fig1.png"]).hexdigest()[:10]
    expected_sources = {
        "Fig1.png": "https://example.test/first/Fig1.png",
        f"Fig1-{second_digest}.png": "https://example.test/second/Fig1.png",
    }
    assert len(published) == 2
    assert set(published.values()) == set(payloads.values())
    assert {
        entry["file"]: entry["source_url"]
        for entry in sidecar["downloads"]
    } == expected_sources


def test_direct_download_at_exact_paper_cap_is_published(
    monkeypatch,
    tmp_path,
):
    payload = b"a,b\n1,2\n"

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "content_type": "text/csv",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(payload))
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(tmp_path / "data.csv")]
    assert (tmp_path / "data.csv").read_bytes() == payload
    assert summary["skipped"] == []


def test_direct_exact_cap_rerun_excludes_verified_provenance_sidecar(
    monkeypatch,
    tmp_path,
):
    payload = b"a,b\n1,2\n"

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "content_type": "text/csv",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(payload))
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    first = _download.download_candidate(candidate, str(tmp_path))
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    first_sidecar = sidecar.read_bytes()
    second = _download.download_candidate(candidate, str(tmp_path))

    assert first["downloaded"] == second["downloaded"] == [
        str(tmp_path / "data.csv"),
    ]
    assert first["skipped"] == second["skipped"] == []
    assert (tmp_path / "data.csv").read_bytes() == payload
    assert sidecar.read_bytes() == first_sidecar
    assert (tmp_path / "data.csv").stat().st_size == len(payload)
    assert sum(
        path.stat().st_size
        for path in tmp_path.iterdir()
        if path.is_file()
    ) > len(payload)


def test_direct_download_one_byte_over_projected_paper_cap_is_skipped(
    monkeypatch,
    tmp_path,
):
    payload = b"a,b\n1,2\n"

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "content_type": "text/csv",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(payload) - 1)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == []
    assert not (tmp_path / "data.csv").exists()
    assert any(
        "projected paper data exceeds per-paper cap" in item["reason"]
        for item in summary["skipped"]
    )
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert sidecar["downloads"] == []


def test_direct_exact_content_collision_reuse_is_not_double_counted(
    monkeypatch,
    tmp_path,
):
    payload = b"a,b\n1,2\n"
    existing = tmp_path / "data.csv"
    existing.write_bytes(payload)

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "content_type": "text/csv",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(payload))
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(existing)]
    assert existing.read_bytes() == payload
    assert summary["skipped"] == []
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert sidecar["downloads"] == [{
        "file": "data.csv",
        "source_url": "https://example.test/data.csv",
        "content_type": "text/csv",
        "asset_type": "tabular",
        "size": len(payload),
    }]


def test_download_candidate_pins_output_root_across_publications(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    displaced = tmp_path / "displaced-out"
    payloads = {
        "https://example.test/Fig1.png": b"first-image",
        "https://example.test/Fig2.png": b"second-image",
    }

    def stub_download(url, dest, **kwargs):
        data = payloads[url]
        Path(dest).write_bytes(data)
        return {"ok": True, "path": dest, "size": len(data), "source_url": url}

    real_write_collision_safe = _download._write_collision_safe
    publications = 0

    def publish_then_swap(output, name, data, **kwargs):
        nonlocal publications
        result = real_write_collision_safe(
            output,
            name,
            data,
            **kwargs,
        )
        publications += 1
        if publications == 1:
            out_dir.rename(displaced)
            out_dir.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        publish_then_swap,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "image_files": [
            {"name": Path(url).name, "download_url": url}
            for url in payloads
        ],
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    assert summary["skipped"]
    assert list(outside.iterdir()) == []
    assert (displaced / "Fig1.png").read_bytes() == b"first-image"
    assert not (displaced / "Fig2.png").exists()


def test_download_candidate_rejects_root_replacement_after_direct_publication(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = b"a,b\n1,2\n"
    replacement = b"replacement,root\n"
    root_replaced = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    real_write_collision_safe = _download._write_collision_safe

    def publish_then_replace_root(output, name, data, **kwargs):
        nonlocal root_replaced
        published = real_write_collision_safe(
            output,
            name,
            data,
            **kwargs,
        )
        if not root_replaced:
            out_dir.rename(displaced)
            out_dir.mkdir()
            (out_dir / "data.csv").write_bytes(replacement)
            root_replaced = True
        return published

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        publish_then_replace_root,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert root_replaced
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == 2
    assert "output directory changed" in summary["skipped"][0]["reason"]
    assert summary["skipped"][1] == {
        "name": _download.SOURCE_SIDECAR,
        "reason": "provenance sidecar publication unavailable",
    }
    assert (out_dir / "data.csv").read_bytes() == replacement
    assert (displaced / "data.csv").read_bytes() == payload


def test_direct_download_staging_stays_on_pinned_root_during_replacement(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = b"a,b\n1,2\n"
    replacement_received_bytes = False

    def swap_then_download(url, destination, **kwargs):
        nonlocal replacement_received_bytes
        out_dir.rename(displaced)
        out_dir.mkdir()
        if hasattr(destination, "fd"):
            os.ftruncate(destination.fd, 0)
            os.lseek(destination.fd, 0, os.SEEK_SET)
            os.write(destination.fd, payload)
        else:
            path = Path(destination)
            path.write_bytes(payload)
            replacement_received_bytes = path.parent == out_dir
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", swap_then_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == []
    assert summary["skipped"]
    assert replacement_received_bytes is False
    assert list(out_dir.iterdir()) == []
    assert not list(displaced.glob(".paperconan-download-*"))


def test_provenance_sidecar_does_not_follow_or_replace_final_symlink(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text("outside-sentinel", encoding="utf-8")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    sidecar.symlink_to(sentinel)

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"image")
        return {"ok": True, "path": dest, "size": 5, "source_url": url}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "image_files": [{
            "name": "Fig1.png",
            "download_url": "https://example.test/Fig1.png",
        }],
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    assert len(summary["downloaded"]) == 1
    assert sidecar.is_symlink()
    assert sidecar.readlink() == sentinel
    assert sentinel.read_text(encoding="utf-8") == "outside-sentinel"
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


@pytest.mark.parametrize("existing_sidecar", [False, True])
def test_final_output_replacement_is_reconciled_before_sidecar_publication(
    monkeypatch,
    tmp_path,
    existing_sidecar,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    previous_bytes = json.dumps({
        "cand_id": "previous:1",
        "downloads": [{"file": "previous.csv"}],
    }).encode("utf-8")
    if existing_sidecar:
        sidecar.write_bytes(previous_bytes)
    payload = b"a,b\n1,2\n"
    replacement = b"x,y\n9,8\n"
    sidecar_writes = 0
    reconciliation_calls = 0

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    real_write_source_sidecar = _download._write_source_sidecar
    real_reconcile = _download._reconcile_publications

    def replace_output_at_second_boundary(*args, **kwargs):
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        if reconciliation_calls == 2:
            output = args[0]
            os.unlink("data.csv", dir_fd=output.fd)
            replacement_fd = os.open(
                "data.csv",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output.fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
        return real_reconcile(*args, **kwargs)

    def track_single_sidecar_publication(cand, output, downloads=None):
        nonlocal sidecar_writes
        sidecar_writes += 1
        assert reconciliation_calls == 2
        if existing_sidecar:
            assert sidecar.read_bytes() == previous_bytes
        else:
            assert not sidecar.exists()
        if existing_sidecar:
            with pytest.raises(_download._SourceSidecarPublicationError):
                real_write_source_sidecar(
                    cand,
                    output,
                    downloads=downloads,
                )
            assert sidecar.read_bytes() == previous_bytes
            raise _download._SourceSidecarPublicationError(
                "retained existing provenance sidecar because it differs "
                "from prepared provenance"
            )
        result = real_write_source_sidecar(
            cand,
            output,
            downloads=downloads,
        )
        assert json.loads(sidecar.read_text(encoding="utf-8"))["downloads"] == []
        return result

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_reconcile_publications",
        replace_output_at_second_boundary,
    )
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        track_single_sidecar_publication,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert reconciliation_calls == 2
    assert sidecar_writes == 1
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == (2 if existing_sidecar else 1)
    assert any(
        "stable regular file" in item["reason"]
        for item in summary["skipped"]
    )
    if existing_sidecar:
        assert sidecar.read_bytes() == previous_bytes
        assert any(
            "retained existing provenance sidecar" in item["reason"]
            for item in summary["skipped"]
        )
    else:
        assert json.loads(sidecar.read_text(encoding="utf-8"))["downloads"] == []
    assert (out_dir / "data.csv").read_bytes() == replacement
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


def test_sidecar_no_replace_keeps_prior_descriptor_bytes(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    previous_bytes = b'{"cand_id":"previous:1","downloads":[]}'
    sidecar.write_bytes(previous_bytes)
    old_reader_fd = os.open(sidecar, os.O_RDONLY | os.O_NOFOLLOW)
    payload = b"a,b\n1,2\n"
    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    try:
        summary = _download.download_candidate(candidate, str(out_dir))
        os.lseek(old_reader_fd, 0, os.SEEK_SET)
        old_inode_bytes = os.read(old_reader_fd, len(previous_bytes))
    finally:
        os.close(old_reader_fd)

    assert old_inode_bytes == previous_bytes
    assert summary["downloaded"] == [str(out_dir / "data.csv")]
    assert any(
        "retained existing provenance sidecar" in item["reason"]
        for item in summary["skipped"]
    )
    assert sidecar.read_bytes() == previous_bytes
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


def test_direct_download_does_not_follow_existing_destination_symlink(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"outside")
    (out_dir / "Fig1.png").symlink_to(sentinel)

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"new-image")
        return {"ok": True, "path": dest, "size": 9, "source_url": url}

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "image_files": [
            {
                "name": "Fig1.png",
                "download_url": "https://example.test/Fig1.png",
            },
        ],
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    assert sentinel.read_bytes() == b"outside"
    assert len(summary["downloaded"]) == 1
    published = Path(summary["downloaded"][0])
    assert published.parent == out_dir
    assert published.is_file()
    assert not published.is_symlink()
    assert published.read_bytes() == b"new-image"


def test_write_collision_safe_does_not_follow_candidate_or_digest_symlinks(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"outside")
    data = b"archive-image"
    digest = hashlib.sha256(data).hexdigest()[:10]
    candidate = out_dir / "Fig1.png"
    digest_path = out_dir / f"Fig1-{digest}.png"
    candidate.symlink_to(sentinel)
    digest_path.symlink_to(sentinel)

    published = Path(
        _download._write_collision_safe(str(out_dir), "nested/Fig1.png", data)
    )

    assert sentinel.read_bytes() == b"outside"
    assert published == out_dir / f"Fig1-{digest}-2.png"
    assert published.is_file()
    assert not published.is_symlink()
    assert published.read_bytes() == data


def test_download_file_replaces_destination_symlink_without_following_it(
    monkeypatch,
    tmp_path,
):
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"outside")
    dest = tmp_path / "data.csv"
    dest.symlink_to(sentinel)
    monkeypatch.setattr(
        _http,
        "open_http",
        lambda req, timeout=None: _Resp(b"a,b\n1,2\n", "text/csv"),
    )

    result = _download.download_file(
        "https://example.test/data.csv",
        str(dest),
        retries=1,
    )

    assert result["ok"] is True
    assert sentinel.read_bytes() == b"outside"
    assert dest.is_file()
    assert not dest.is_symlink()
    assert dest.read_bytes() == b"a,b\n1,2\n"


def test_write_collision_safe_preserves_different_candidate_and_digest_files(tmp_path):
    data = b"new-image"
    digest = hashlib.sha256(data).hexdigest()[:10]
    candidate = tmp_path / "Fig1.png"
    digest_path = tmp_path / f"Fig1-{digest}.png"
    candidate.write_bytes(b"existing-candidate")
    digest_path.write_bytes(b"existing-digest")

    published = Path(
        _download._write_collision_safe(str(tmp_path), "Fig1.png", data)
    )

    assert candidate.read_bytes() == b"existing-candidate"
    assert digest_path.read_bytes() == b"existing-digest"
    assert published == tmp_path / f"Fig1-{digest}-2.png"
    assert published.read_bytes() == data


def test_write_collision_safe_bounds_existing_file_comparison(
    monkeypatch,
    tmp_path,
):
    data = b"new-image"
    digest = hashlib.sha256(data).hexdigest()[:10]
    candidate = tmp_path / "Fig1.png"
    with candidate.open("wb") as fh:
        fh.truncate(1024 * 1024 * 1024)
    digest_path = tmp_path / f"Fig1-{digest}.png"
    digest_path.write_bytes(data)
    candidate_identity = (
        candidate.stat().st_dev,
        candidate.stat().st_ino,
    )
    digest_identity = (
        digest_path.stat().st_dev,
        digest_path.stat().st_ino,
    )
    candidate_read_sizes = []
    digest_read_sizes = []
    real_fdopen = os.fdopen

    class TrackingReader:
        def __init__(self, fh, read_sizes, *, sparse=False):
            self._fh = fh
            self._read_sizes = read_sizes
            self._sparse = sparse

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *args):
            return self._fh.__exit__(*args)

        def read(self, size=-1):
            self._read_sizes.append(size)
            if self._sparse and size < 0:
                return b"different"
            return self._fh.read(size)

    def tracking_fdopen(fd, *args, **kwargs):
        identity = os.fstat(fd)
        identity = (identity.st_dev, identity.st_ino)
        fh = real_fdopen(fd, *args, **kwargs)
        if identity == candidate_identity:
            return TrackingReader(fh, candidate_read_sizes, sparse=True)
        if identity == digest_identity:
            return TrackingReader(fh, digest_read_sizes)
        return fh

    monkeypatch.setattr(_download.os, "fdopen", tracking_fdopen)

    published = Path(
        _download._write_collision_safe(str(tmp_path), "Fig1.png", data)
    )

    assert candidate.stat().st_size == 1024 * 1024 * 1024
    assert published == digest_path
    assert candidate_read_sizes == []
    assert digest_read_sizes
    assert all(size > 0 for size in digest_read_sizes)


def test_write_collision_safe_does_not_clobber_file_created_during_publish(
    monkeypatch,
    tmp_path,
):
    data = b"new-image"
    digest = hashlib.sha256(data).hexdigest()[:10]
    candidate = tmp_path / "Fig1.png"
    real_link = _download.os.link
    raced = False

    def racing_link(src, dst, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and Path(dst).name == candidate.name
            and kwargs.get("dst_dir_fd") is not None
        ):
            raced = True
            candidate.write_bytes(b"concurrent")
            raise FileExistsError(dst)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(_download.os, "link", racing_link)

    published = Path(
        _download._write_collision_safe(str(tmp_path), "Fig1.png", data)
    )

    assert raced is True
    assert candidate.read_bytes() == b"concurrent"
    assert published == tmp_path / f"Fig1-{digest}.png"
    assert published.read_bytes() == data


def test_write_collision_safe_binds_created_identity_before_link(
    monkeypatch,
    tmp_path,
):
    real_open = _download.os.open
    real_fstat = _download.os.fstat
    real_link = _download.os.link
    real_publication = _download._PublishedOutputFile
    staging_fd = None
    staging_identity_bound = False
    created_record_bound = False

    def track_open(path, flags, *args, **kwargs):
        nonlocal staging_fd
        fd = real_open(path, flags, *args, **kwargs)
        if str(path).startswith(".paperconan-publish-"):
            staging_fd = fd
        return fd

    def track_fstat(fd):
        nonlocal staging_identity_bound
        current = real_fstat(fd)
        if fd == staging_fd:
            staging_identity_bound = True
        return current

    def track_publication(*args, **kwargs):
        nonlocal created_record_bound
        entry = real_publication(*args, **kwargs)
        if entry.created:
            created_record_bound = True
        return entry

    def require_bound_identity(*args, **kwargs):
        assert staging_identity_bound, "publication identity was not bound before link"
        assert created_record_bound, "created publication record was not bound before link"
        return real_link(*args, **kwargs)

    monkeypatch.setattr(_download.os, "open", track_open)
    monkeypatch.setattr(_download.os, "fstat", track_fstat)
    monkeypatch.setattr(_download, "_PublishedOutputFile", track_publication)
    monkeypatch.setattr(_download.os, "link", require_bound_identity)

    with _download._pinned_output_directory(str(tmp_path)) as output:
        entry = _download._write_collision_safe(
            output,
            "data.csv",
            b"a,b\n1,2\n",
            _return_entry=True,
        )

    current = (tmp_path / "data.csv").stat()
    assert entry.identity == (current.st_dev, current.st_ino)
    assert entry.size == len(b"a,b\n1,2\n")
    assert not list(tmp_path.glob(".paperconan-publish-*"))


def test_write_collision_safe_retains_replacement_inserted_after_link(
    monkeypatch,
    tmp_path,
):
    original = b"a,b\n1,2\n"
    replacement = b"x,y\n9,8\n"
    assert len(original) == len(replacement)
    real_link = _download.os.link
    linked = False

    def replace_after_link(src, dst, *args, **kwargs):
        nonlocal linked
        result = real_link(src, dst, *args, **kwargs)
        if Path(dst).name == "data.csv":
            output_fd = kwargs["dst_dir_fd"]
            os.unlink(dst, dir_fd=output_fd)
            replacement_fd = os.open(
                dst,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output_fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
            linked = True
        return result

    monkeypatch.setattr(_download.os, "link", replace_after_link)

    with _download._pinned_output_directory(str(tmp_path)) as output:
        with pytest.raises(
            OSError,
            match="retained visible output for recovery: data.csv",
        ):
            _download._write_collision_safe(
                output,
                "data.csv",
                original,
                _return_entry=True,
            )

    assert linked
    assert (tmp_path / "data.csv").read_bytes() == replacement
    assert not list(tmp_path.glob(".paperconan-publish-*"))


def test_write_collision_safe_retains_visible_path_when_first_post_link_stat_fails(
    monkeypatch,
    tmp_path,
):
    real_link = _download.os.link
    real_stat = _download.os.stat
    linked = False
    failed = False

    def track_link(*args, **kwargs):
        nonlocal linked
        result = real_link(*args, **kwargs)
        linked = True
        return result

    def fail_first_visible_stat(path, *args, **kwargs):
        nonlocal failed
        if (
            linked
            and not failed
            and Path(path).name == "data.csv"
            and kwargs.get("dir_fd") is not None
        ):
            failed = True
            raise OSError("post-link stat unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(_download.os, "link", track_link)
    monkeypatch.setattr(_download.os, "stat", fail_first_visible_stat)

    with _download._pinned_output_directory(str(tmp_path)) as output:
        with pytest.raises(
            OSError,
            match="retained visible output for recovery: data.csv",
        ):
            _download._write_collision_safe(
                output,
                "data.csv",
                b"a,b\n1,2\n",
                _return_entry=True,
            )

    assert failed
    assert (tmp_path / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert not list(tmp_path.glob(".paperconan-publish-*"))


def test_write_collision_safe_retains_replacement_after_last_visible_stat(
    monkeypatch,
    tmp_path,
):
    replacement = b"x,y\n9,8\n"
    real_link = _download.os.link
    real_unlink = _download.os.unlink
    real_verify = _download._PinnedOutputDirectory.verify
    linked = False
    replacement_inserted = False
    visible_unlink_attempted = False

    def track_link(*args, **kwargs):
        nonlocal linked
        result = real_link(*args, **kwargs)
        linked = True
        return result

    def fail_after_visible_stat(output):
        nonlocal replacement_inserted
        real_verify(output)
        if linked:
            real_unlink("data.csv", dir_fd=output.fd)
            replacement_fd = os.open(
                "data.csv",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output.fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
            replacement_inserted = True
            raise ValueError("post-link output verification unavailable")

    def reject_visible_unlink(path, *args, **kwargs):
        nonlocal visible_unlink_attempted
        if Path(path).name == "data.csv":
            visible_unlink_attempted = True
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(_download.os, "link", track_link)
    monkeypatch.setattr(
        _download._PinnedOutputDirectory,
        "verify",
        fail_after_visible_stat,
    )
    monkeypatch.setattr(_download.os, "unlink", reject_visible_unlink)

    with _download._pinned_output_directory(str(tmp_path)) as output:
        with pytest.raises(
            OSError,
            match="retained visible output for recovery: data.csv",
        ):
            _download._write_collision_safe(
                output,
                "data.csv",
                b"a,b\n1,2\n",
                _return_entry=True,
            )

    assert replacement_inserted is True
    assert visible_unlink_attempted is False
    assert (tmp_path / "data.csv").read_bytes() == replacement
    assert not list(tmp_path.glob(".paperconan-publish-*"))


def _archive_bytes(kind, member_name="tables/data.csv", data=b"a,b\n1,2\n"):
    payload = io.BytesIO()
    if kind == "oa":
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    else:
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr(member_name, data)
    return payload.getvalue()


def _two_member_archive_bytes(kind):
    payload = io.BytesIO()
    members = [
        ("tables/first.csv", b"first,value\n1,2\n"),
        ("tables/second.csv", b"second,value\n3,4\n"),
    ]
    if kind == "oa":
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    else:
        with zipfile.ZipFile(payload, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
    return payload.getvalue()


def _archive_bytes_with_members(kind, members):
    payload = io.BytesIO()
    if kind == "oa":
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    else:
        with zipfile.ZipFile(payload, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
    return payload.getvalue()


def _classic_eocd_bytes(
    *,
    disk_number=0,
    central_directory_disk=0,
    entries_on_disk=0,
    total_entries=0,
    central_directory_size=0,
    central_directory_offset=0,
    comment=b"",
):
    return struct.pack(
        "<4s4H2IH",
        b"PK\x05\x06",
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        central_directory_size,
        central_directory_offset,
        len(comment),
    ) + comment


def _zip64_archive_bytes(files=()):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, data in files:
            archive.writestr(name, data)
    classic = payload.getvalue()
    eocd_offset = len(classic) - 22
    (
        signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        central_directory_size,
        central_directory_offset,
        comment_size,
    ) = struct.unpack("<4s4H2IH", classic[eocd_offset:])
    assert signature == b"PK\x05\x06"
    assert disk_number == central_directory_disk == 0
    assert entries_on_disk == total_entries
    assert comment_size == 0
    zip64_offset = eocd_offset
    zip64_eocd = struct.pack(
        "<4sQ2H2I4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        total_entries,
        total_entries,
        central_directory_size,
        central_directory_offset,
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, zip64_offset, 1)
    sentinel_eocd = _classic_eocd_bytes(
        entries_on_disk=0xFFFF,
        total_entries=0xFFFF,
        central_directory_size=0xFFFFFFFF,
        central_directory_offset=0xFFFFFFFF,
    )
    return classic[:eocd_offset] + zip64_eocd + locator + sentinel_eocd


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_later_publication_failure_keeps_verified_partial_entry(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "first.csv"
    existing.write_bytes(b"pre-existing")
    payload = _two_member_archive_bytes(archive_kind)

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    real_write_collision_safe = _download._write_collision_safe
    publication_calls = 0

    def fail_second_publication(*args, **kwargs):
        nonlocal publication_calls
        publication_calls += 1
        if publication_calls == 2:
            raise OSError("later archive member publication unavailable")
        return real_write_collision_safe(*args, **kwargs)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        fail_second_publication,
    )
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(out_dir))

    assert publication_calls == 2
    assert existing.read_bytes() == b"pre-existing"
    assert len(summary["downloaded"]) == 1
    published = Path(summary["downloaded"][0])
    assert published.name != existing.name
    assert published.read_bytes() == b"first,value\n1,2\n"
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert [entry["file"] for entry in sidecar["downloads"]] == [
        published.name
    ]
    assert sum(
        item["file"] == published.name
        for item in sidecar["downloads"]
    ) == 1
    assert any(
        "publication unavailable" in item["reason"]
        for item in summary["skipped"]
    )
    assert not (out_dir / "second.csv").exists()
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def _archive_candidate(archive_kind):
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive
    return candidate, archive


def _install_archive_payload(monkeypatch, payload):
    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)


def _zip_processing_failure_bytes(failure_kind):
    compression = (
        zipfile.ZIP_DEFLATED
        if failure_kind == "corrupt"
        else zipfile.ZIP_STORED
    )
    payload = bytearray(
        _archive_bytes_with_members(
            "supplementary",
            [("tables/data.csv", b"a,b\n1,2\n" * 100)],
        )
    )
    if compression == zipfile.ZIP_DEFLATED:
        compressed = io.BytesIO()
        with zipfile.ZipFile(
            compressed,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("tables/data.csv", b"a,b\n1,2\n" * 100)
        payload = bytearray(compressed.getvalue())

    central_offset = payload.index(b"PK\x01\x02")
    if failure_kind == "unsupported":
        struct.pack_into("<H", payload, 8, 99)
        struct.pack_into("<H", payload, central_offset + 10, 99)
    elif failure_kind == "encrypted":
        local_flags = struct.unpack_from("<H", payload, 6)[0] | 1
        central_flags = struct.unpack_from("<H", payload, central_offset + 8)[0] | 1
        struct.pack_into("<H", payload, 6, local_flags)
        struct.pack_into("<H", payload, central_offset + 8, central_flags)
    elif failure_kind == "corrupt":
        name_length, extra_length = struct.unpack_from("<HH", payload, 26)
        compressed_size = struct.unpack_from("<I", payload, 18)[0]
        data_offset = 30 + name_length + extra_length
        for offset in range(min(compressed_size, 5)):
            payload[data_offset + offset] ^= 0xFF
    else:
        raise AssertionError(f"unknown ZIP failure kind: {failure_kind}")
    return bytes(payload)


@pytest.mark.parametrize(
    "failure_kind",
    ["unsupported", "encrypted", "corrupt"],
)
def test_supplementary_zip_processing_failures_are_skipped(
    monkeypatch,
    tmp_path,
    failure_kind,
):
    candidate, archive = _archive_candidate("supplementary")
    _install_archive_payload(
        monkeypatch,
        _zip_processing_failure_bytes(failure_kind),
    )

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == []
    archive_outcomes = [
        item for item in summary["skipped"]
        if item["name"] == archive["name"]
    ]
    assert len(archive_outcomes) == 1
    assert "archive processing unavailable" in archive_outcomes[0]["reason"]
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def test_truncated_oa_gzip_processing_is_skipped(monkeypatch, tmp_path):
    candidate, archive = _archive_candidate("oa")
    payload = _archive_bytes("oa")
    _install_archive_payload(monkeypatch, payload[:-20])

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == []
    archive_outcomes = [
        item for item in summary["skipped"]
        if item["name"] == archive["name"]
    ]
    assert len(archive_outcomes) == 1
    assert "archive processing unavailable" in archive_outcomes[0]["reason"]
    assert not list(tmp_path.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_publication_runtime_error_propagates(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    candidate, _ = _archive_candidate(archive_kind)
    _install_archive_payload(
        monkeypatch,
        _archive_bytes(archive_kind),
    )
    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("internal archive publication error")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="internal archive publication error",
    ):
        _download.download_candidate(candidate, str(tmp_path))

    assert not list(tmp_path.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
@pytest.mark.parametrize(
    "reserved_name",
    [
        _download.SOURCE_SIDECAR,
        _download.SOURCE_SIDECAR.upper(),
    ],
)
def test_archive_reserved_sidecar_basename_is_skipped(
    monkeypatch,
    tmp_path,
    archive_kind,
    reserved_name,
):
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(
        monkeypatch,
        _archive_bytes_with_members(
            archive_kind,
            [(f"nested/{reserved_name}", b"remote-sidecar")],
        ),
    )

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == []
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": "reserved provenance sidecar basename",
    }]
    assert sidecar["cand_id"] == candidate["cand_id"]
    assert sidecar["downloads"] == []


def _fail_download_staging_unlink(monkeypatch):
    real_unlink = _download.os.unlink

    def fail_staging_unlink(path, *args, **kwargs):
        name = os.fspath(path)
        if name.startswith((
            ".paperconan-download-",
            ".paperconan-archive-",
        )):
            raise OSError("sensitive cleanup detail /private/staging")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(_download.os, "unlink", fail_staging_unlink)


def test_direct_cleanup_failure_after_publication_preserves_summary_and_sidecar(
    monkeypatch,
    tmp_path,
):
    data = b"a,b\n1,2\n"

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, data)
        return {
            "ok": True,
            "path": destination,
            "size": len(data),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    _fail_download_staging_unlink(monkeypatch)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == [str(tmp_path / "data.csv")]
    assert sidecar["downloads"][0]["file"] == "data.csv"
    cleanup = [
        item for item in summary["skipped"]
        if item["reason"].startswith("download staging cleanup incomplete")
    ]
    assert cleanup == [{
        "name": "data.csv",
        "reason": "download staging cleanup incomplete: deletion failed",
    }]
    assert "/private" not in cleanup[0]["reason"]
    assert "sensitive cleanup detail" not in cleanup[0]["reason"]


def test_direct_cleanup_failure_preserves_existing_processing_error(
    monkeypatch,
    tmp_path,
):
    data = b"a,b\n1,2\n"

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, data)
        return {
            "ok": True,
            "path": destination,
            "size": len(data),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_read_verified_download_staging",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("content verification unavailable")
        ),
    )
    _fail_download_staging_unlink(monkeypatch)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == []
    assert sidecar["downloads"] == []
    assert any(
        "content verification unavailable" in item["reason"]
        for item in summary["skipped"]
    )
    cleanup = [
        item for item in summary["skipped"]
        if item["reason"].startswith("download staging cleanup incomplete")
    ]
    assert cleanup == [{
        "name": "data.csv",
        "reason": "download staging cleanup incomplete: deletion failed",
    }]


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_cleanup_failure_after_publication_preserves_summary_and_sidecar(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    _fail_download_staging_unlink(monkeypatch)

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == [str(tmp_path / "data.csv")]
    assert sidecar["downloads"][0]["file"] == "data.csv"
    cleanup = [
        item for item in summary["skipped"]
        if item["reason"].startswith("download staging cleanup incomplete")
    ]
    assert cleanup == [{
        "name": archive["name"],
        "reason": "download staging cleanup incomplete: deletion failed",
    }]
    assert "/private" not in cleanup[0]["reason"]
    assert "sensitive cleanup detail" not in cleanup[0]["reason"]


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_cleanup_failure_preserves_existing_processing_error(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, b"not-an-archive")
    _fail_download_staging_unlink(monkeypatch)

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == []
    assert sidecar["downloads"] == []
    cleanup = [
        item for item in summary["skipped"]
        if item["reason"].startswith("download staging cleanup incomplete")
    ]
    processing = [
        item for item in summary["skipped"]
        if item not in cleanup
    ]
    assert len(processing) == 1
    assert processing[0]["name"] == archive["name"]
    assert cleanup == [{
        "name": archive["name"],
        "reason": "download staging cleanup incomplete: deletion failed",
    }]
    assert "/private" not in cleanup[0]["reason"]
    assert "sensitive cleanup detail" not in cleanup[0]["reason"]


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_member_at_exact_paper_cap_is_published(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    data = b"1234"
    payload = _archive_bytes(archive_kind, data=data)
    candidate, _ = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(data))

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(tmp_path / "data.csv")]
    assert (tmp_path / "data.csv").read_bytes() == data
    assert summary["skipped"] == []


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_exact_cap_rerun_excludes_verified_provenance_sidecar(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    data = b"1234"
    payload = _archive_bytes(archive_kind, data=data)
    candidate, _ = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(data))

    first = _download.download_candidate(candidate, str(tmp_path))
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    first_sidecar = sidecar.read_bytes()
    second = _download.download_candidate(candidate, str(tmp_path))

    assert first["downloaded"] == second["downloaded"] == [
        str(tmp_path / "data.csv"),
    ]
    assert first["skipped"] == second["skipped"] == []
    assert (tmp_path / "data.csv").read_bytes() == data
    assert sidecar.read_bytes() == first_sidecar
    assert (tmp_path / "data.csv").stat().st_size == len(data)
    assert sum(
        path.stat().st_size
        for path in tmp_path.iterdir()
        if path.is_file()
    ) > len(data)


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_member_one_byte_over_paper_cap_is_reported_and_skipped(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    data = b"1234"
    payload = _archive_bytes(archive_kind, data=data)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(data) - 1)

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == []
    assert not (tmp_path / "data.csv").exists()
    assert any(
        item["name"] == archive["name"]
        and "projected paper data exceeds per-paper cap" in item["reason"]
        for item in summary["skipped"]
    )


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_exact_content_reuse_is_not_charged_twice(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    data = b"1234"
    existing = tmp_path / "data.csv"
    existing.write_bytes(data)
    payload = _archive_bytes(archive_kind, data=data)
    candidate, _ = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(data))

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(existing)]
    assert existing.read_bytes() == data
    assert summary["skipped"] == []


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_cap_rejection_continues_to_later_smaller_member(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    payload = _archive_bytes_with_members(
        archive_kind,
        [
            ("tables/large.csv", b"12345"),
            ("tables/small.csv", b"1234"),
        ],
    )
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 4)

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(tmp_path / "small.csv")]
    assert not (tmp_path / "large.csv").exists()
    assert (tmp_path / "small.csv").read_bytes() == b"1234"
    assert any(
        item["name"] == archive["name"]
        and "projected paper data exceeds per-paper cap" in item["reason"]
        and "direct file skipped" not in item["reason"]
        for item in summary["skipped"]
    )


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_cap_rejection_continues_to_exact_content_reuse(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    existing = tmp_path / "reuse.csv"
    existing.write_bytes(b"1234")
    payload = _archive_bytes_with_members(
        archive_kind,
        [
            ("tables/new.csv", b"x"),
            ("tables/reuse.csv", b"1234"),
        ],
    )
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 4)

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == [str(existing)]
    assert not (tmp_path / "new.csv").exists()
    assert existing.read_bytes() == b"1234"
    assert any(
        item["name"] == archive["name"]
        and "projected paper data exceeds per-paper cap" in item["reason"]
        for item in summary["skipped"]
    )


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_fresh_accounting_counts_visible_insertion_before_publication(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    data = b"1234"
    payload = _archive_bytes(archive_kind, data=data)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", len(data))
    real_write = _download._write_collision_safe
    inserted = False

    def insert_before_publication(*args, **kwargs):
        nonlocal inserted
        if not inserted:
            (tmp_path / "external.bin").write_bytes(b"x")
            inserted = True
        return real_write(*args, **kwargs)

    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        insert_before_publication,
    )

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert inserted
    assert summary["downloaded"] == []
    assert not (tmp_path / "data.csv").exists()
    assert any(
        item["name"] == archive["name"]
        and "projected paper data exceeds per-paper cap" in item["reason"]
        for item in summary["skipped"]
    )


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_reconciles_entry_verification_failure_after_publication(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    real_verify = _download._verify_published_output_file
    verification_calls = 0

    def fail_first_verification(output, entry):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            raise _download._UnstableRegularFileError(
                "entry verification unavailable"
            )
        return real_verify(output, entry)

    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        fail_first_verification,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert [entry["file"] for entry in sidecar["downloads"]] == ["data.csv"]
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": (
            "archive publication unavailable: entry verification unavailable; "
            "retained verified output: data.csv"
        ),
    }]
    assert (out_dir / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:password@example.test:8443/path?token=secret#part",
            "https://example.test:8443/path",
        ),
        (
            "https://user:password@[2001:db8::1]:9443/data?token=secret",
            "https://[2001:db8::1]:9443/data",
        ),
        ("ftp://example.test/data.csv", None),
        ("https://example.test:not-a-port/data.csv", None),
        ("https://[2001:db8::1/data.csv", None),
        ("https:///missing-host.csv", None),
    ],
)
def test_safe_source_url_rebuilds_valid_authority(url, expected):
    assert _download._safe_source_url(url) == expected


@pytest.mark.parametrize("publication_kind", ["direct", "oa", "supplementary"])
def test_published_provenance_strips_credentials_and_preserves_ipv6(
    monkeypatch,
    tmp_path,
    publication_kind,
):
    source_url = (
        "https://reader:private@[2001:db8::1]:8443/source/data.csv"
        "?signature=hidden#fragment"
    )
    data = b"a,b\n1,2\n"
    if publication_kind == "direct":
        def stub_download(url, destination, **kwargs):
            os.ftruncate(destination.fd, 0)
            os.lseek(destination.fd, 0, os.SEEK_SET)
            os.write(destination.fd, data)
            return {
                "ok": True,
                "path": destination,
                "size": len(data),
                "source_url": url,
            }

        monkeypatch.setattr(_download, "download_file", stub_download)
        candidate = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [{
                "name": "data.csv",
                "download_url": source_url,
            }],
        }
    else:
        payload = _archive_bytes(publication_kind, data=data)
        _install_archive_payload(monkeypatch, payload)
        candidate, archive = _archive_candidate(publication_kind)
        archive["url"] = source_url

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == [str(tmp_path / "data.csv")]
    assert sidecar["downloads"][0]["source_url"] == (
        "https://[2001:db8::1]:8443/source/data.csv"
    )


@pytest.mark.parametrize("has_entry", [False, True])
def test_reconciliation_verifies_output_root_when_entry_verification_fails(
    monkeypatch,
    tmp_path,
    has_entry,
):
    with _download._pinned_output_directory(str(tmp_path)) as output:
        entries = []
        if has_entry:
            entries.append(_download._PublishedOutputFile(
                filename="data.csv",
                size=0,
                identity=(0, 0),
                sha256=hashlib.sha256(b"").hexdigest(),
                created=True,
            ))
        entry_calls = 0
        root_calls = 0

        def fail_entry_verification(_output, _entry):
            nonlocal entry_calls
            entry_calls += 1
            raise _download._UnstableRegularFileError(
                "entry verification unavailable"
            )

        def fail_root_verification():
            nonlocal root_calls
            root_calls += 1
            raise ValueError("output root verification unavailable")

        monkeypatch.setattr(
            _download,
            "_verify_published_output_file",
            fail_entry_verification,
        )
        monkeypatch.setattr(output, "verify", fail_root_verification)

        reconciled, outcomes, error = _download._reconcile_publications(
            output,
            entries,
            attempts=2,
        )

    assert reconciled == []
    assert root_calls == 2
    assert entry_calls == (2 if has_entry else 0)
    assert error is not None
    if has_entry:
        assert outcomes == [
            "retained visible output for recovery without reporting it: data.csv"
        ]
    else:
        assert outcomes == []


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_retains_new_output_after_output_root_verification_failure(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    real_write = _download._write_collision_safe
    root_replaced = False

    def publish_then_replace_root(output, name, data, **kwargs):
        nonlocal root_replaced
        published = real_write(output, name, data, **kwargs)
        if not root_replaced:
            out_dir.rename(displaced)
            out_dir.mkdir()
            root_replaced = True
        return published

    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        publish_then_replace_root,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    assert root_replaced
    assert summary["downloaded"] == []
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": (
            "downloaded archive is not a stable regular file: "
            "fetch output directory changed during publication; "
            "retained visible output for recovery without reporting it: data.csv"
        ),
    }, {
        "name": _download.SOURCE_SIDECAR,
        "reason": "provenance sidecar publication unavailable",
    }]
    assert list(out_dir.iterdir()) == []
    assert (displaced / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert not list(displaced.glob(".paperconan-archive-*"))
    assert not list(displaced.glob(".paperconan-publish-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_reconciles_post_yield_staging_failure(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    real_open = _download._open_download_staging

    @contextmanager
    def fail_after_yield(staging):
        with real_open(staging) as source:
            yield source
        raise _download._UnstableRegularFileError(
            "post-yield staging verification unavailable"
        )

    monkeypatch.setattr(
        _download,
        "_open_download_staging",
        fail_after_yield,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert [entry["file"] for entry in sidecar["downloads"]] == ["data.csv"]
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": (
            "downloaded archive is not a stable regular file: "
            "post-yield staging verification unavailable; "
            "retained verified output: data.csv"
        ),
    }]
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_never_removes_collision_reused_output_during_reconciliation(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "data.csv"
    existing.write_bytes(b"a,b\n1,2\n")
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)

    def fail_verification(output, entry):
        raise _download._UnstableRegularFileError(
            "entry verification unavailable"
        )

    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        fail_verification,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert summary["downloaded"] == []
    assert sidecar["downloads"] == []
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": (
            "archive publication unavailable: entry verification unavailable; "
            "retained collision-reused output without reporting it: data.csv"
        ),
    }]
    assert existing.read_bytes() == b"a,b\n1,2\n"
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_reconciliation_never_removes_replacement_inode(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    replacement = b"replacement,value\n9,8\n"
    payload = _archive_bytes(archive_kind)
    candidate, archive = _archive_candidate(archive_kind)
    _install_archive_payload(monkeypatch, payload)
    real_write = _download._write_collision_safe
    original_identity = None

    def publish_then_replace_entry(output, name, data, **kwargs):
        nonlocal original_identity
        published = real_write(output, name, data, **kwargs)
        original_fd = os.open(
            published.filename,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=output.fd,
        )
        try:
            opened = os.fstat(original_fd)
            original_identity = (opened.st_dev, opened.st_ino)
            assert original_identity == published.identity
            os.unlink(published.filename, dir_fd=output.fd)
            replacement_fd = os.open(
                published.filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output.fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
        finally:
            os.close(original_fd)
        return published

    monkeypatch.setattr(
        _download,
        "_write_collision_safe",
        publish_then_replace_entry,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    current = (out_dir / "data.csv").stat()
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert original_identity is not None
    assert (current.st_dev, current.st_ino) != original_identity
    assert summary["downloaded"] == []
    assert sidecar["downloads"] == []
    assert summary["skipped"] == [{
        "name": archive["name"],
        "reason": (
            "archive publication unavailable: published output entry is not "
            "a stable regular file; retained visible output for recovery "
            "without reporting it: data.csv"
        ),
    }]
    assert (out_dir / "data.csv").read_bytes() == replacement
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def _publication_candidate(publication_kind):
    url = f"https://example.test/{publication_kind}"
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if publication_kind == "direct":
        candidate["tabular_files"] = [{
            "name": "data.csv",
            "download_url": url,
        }]
    elif publication_kind == "oa":
        candidate["oa_package"] = {
            "name": "oa.archive",
            "url": url,
        }
    else:
        candidate["supplementary_archive"] = {
            "name": "supplementary.archive",
            "url": url,
        }
    return candidate, url


@pytest.mark.parametrize(
    "publication_kind",
    ["direct", "oa", "supplementary"],
)
@pytest.mark.parametrize("boundary", ["initial", "final"])
@pytest.mark.parametrize("failure_mode", ["transient", "persistent"])
def test_final_verification_reconciles_and_coordinates_sidecar(
    monkeypatch,
    tmp_path,
    publication_kind,
    boundary,
    failure_mode,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar_path = out_dir / _download.SOURCE_SIDECAR
    previous_bytes = b'{"cand_id":"previous:1","downloads":[{"file":"old.csv"}]}'
    sidecar_path.write_bytes(previous_bytes)
    data = b"a,b\n1,2\n"
    payload = (
        data
        if publication_kind == "direct"
        else _archive_bytes(publication_kind, data=data)
    )
    candidate, source_url = _publication_candidate(publication_kind)
    _install_archive_payload(monkeypatch, payload)
    final_phase = False
    active_boundary = None
    final_boundary_calls = 0
    sidecar_writes = 0
    boundary_attempts = 0
    sidecar_before_writes = []
    real_verify = _download._verify_published_output_file
    real_write_sidecar = _download._write_source_sidecar
    real_reconcile = _download._reconcile_publications

    if publication_kind == "direct":
        real_publish = _download._write_collision_safe

        def publish_then_arm(*args, **kwargs):
            nonlocal final_phase
            published = real_publish(*args, **kwargs)
            final_phase = True
            return published

        monkeypatch.setattr(
            _download,
            "_write_collision_safe",
            publish_then_arm,
        )
    else:
        helper_name = (
            "_download_oa_package"
            if publication_kind == "oa"
            else "_download_supplementary_archive"
        )
        real_helper = getattr(_download, helper_name)

        def helper_then_arm(*args, **kwargs):
            nonlocal final_phase
            extracted = real_helper(*args, **kwargs)
            final_phase = True
            return extracted

        monkeypatch.setattr(_download, helper_name, helper_then_arm)

    def track_final_boundary(*args, **kwargs):
        nonlocal active_boundary, final_boundary_calls
        if not final_phase:
            return real_reconcile(*args, **kwargs)
        final_boundary_calls += 1
        active_boundary = "initial" if final_boundary_calls == 1 else "final"
        try:
            return real_reconcile(*args, **kwargs)
        finally:
            active_boundary = None

    def fail_at_final_boundary(output, entry):
        nonlocal boundary_attempts
        at_boundary = final_phase and active_boundary == boundary
        if at_boundary:
            boundary_attempts += 1
            if failure_mode == "persistent" or boundary_attempts == 1:
                raise _download._UnstableRegularFileError(
                    "final verification unavailable"
                )
        return real_verify(output, entry)

    def track_sidecar_writes(cand, output, downloads=None):
        nonlocal sidecar_writes
        sidecar_before_writes.append(sidecar_path.read_bytes())
        sidecar_writes += 1
        return real_write_sidecar(
            cand,
            output,
            downloads=downloads,
        )

    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        fail_at_final_boundary,
    )
    monkeypatch.setattr(
        _download,
        "_reconcile_publications",
        track_final_boundary,
    )
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        track_sidecar_writes,
    )

    summary = _download.download_candidate(candidate, str(out_dir))

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    expected_paths = (
        []
        if failure_mode == "persistent"
        else [str(out_dir / "data.csv")]
    )
    assert summary["downloaded"] == expected_paths
    assert sidecar["downloads"] == [{"file": "old.csv"}]
    assert boundary_attempts == 2
    assert final_boundary_calls == 2
    assert sidecar_writes == 1
    assert sidecar_before_writes == [previous_bytes]
    assert len(summary["skipped"]) == 2
    reason = next(
        item["reason"]
        for item in summary["skipped"]
        if "before provenance publication" in item["reason"]
    )
    assert (
        f"{boundary} reconciliation boundary before provenance publication"
        in reason
    )
    if failure_mode == "transient":
        assert (
            "recovered stable output after bounded verification retry: data.csv"
            in reason
        )
    else:
        assert (
            "retained visible output for recovery without reporting it: data.csv"
            in reason
        )
    assert any(
        "retained existing provenance sidecar" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-sidecar-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def test_excessive_zip_entries_are_rejected_before_zipfile_construction(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _zero_byte_archive_bytes("supplementary", "entry", 3)
    zipfile_init_called = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def fail_zipfile_init(archive, *args, **kwargs):
        nonlocal zipfile_init_called
        zipfile_init_called = True
        raise AssertionError("ZipFile was constructed before ZIP preflight")

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_MAX_RAW_ZIP_ENTRIES_PER_ARCHIVE",
        2,
        raising=False,
    )
    monkeypatch.setattr(zipfile.ZipFile, "__init__", fail_zipfile_init)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert zipfile_init_called is False
    assert summary["downloaded"] == []
    assert any(
        "raw ZIP entry count" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob("*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))


def test_supplementary_zip_path_never_uses_unbounded_staged_read(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    read_sizes = []
    real_open_download_staging = _download._open_download_staging

    class BoundedReader:
        def __init__(self, source):
            self._source = source

        def read(self, size=-1):
            read_sizes.append(size)
            if size < 0:
                raise AssertionError("unbounded staged ZIP read")
            return self._source.read(size)

        def __getattr__(self, name):
            return getattr(self._source, name)

    @contextmanager
    def tracked_open_download_staging(staging):
        with real_open_download_staging(staging) as source:
            yield BoundedReader(source)

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_open_download_staging",
        tracked_open_download_staging,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert read_sizes
    assert all(size >= 0 for size in read_sizes)


def test_supplementary_zip_uses_snapshot_after_original_staging_mutation(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    original = _archive_bytes("supplementary")
    replacement = _zero_byte_archive_bytes(
        "supplementary",
        "replacement",
        3,
    )
    staging = None
    real_preflight = _download._preflight_zip_entry_count

    def stub_download(url, destination, **kwargs):
        nonlocal staging
        staging = destination
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, original)
        return {
            "ok": True,
            "path": destination,
            "size": len(original),
            "source_url": url,
        }

    def mutate_original_after_preflight(source, *, max_entries):
        count = real_preflight(source, max_entries=max_entries)
        os.ftruncate(staging.fd, 0)
        os.lseek(staging.fd, 0, os.SEEK_SET)
        os.write(staging.fd, replacement)
        os.fsync(staging.fd)
        return count

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_preflight_zip_entry_count",
        mutate_original_after_preflight,
    )
    monkeypatch.setattr(
        _download,
        "_MAX_RAW_ZIP_ENTRIES_PER_ARCHIVE",
        2,
    )
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert (out_dir / "data.csv").read_bytes() == b"a,b\n1,2\n"
    assert not list(out_dir.glob("replacement-*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))


def test_zip_snapshot_is_pinned_read_only_and_cleaned_after_preflight(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    snapshot_paths = []
    real_preflight = _download._preflight_zip_entry_count

    def inspect_snapshot(source, *, max_entries):
        assert source.staging.output.path == str(out_dir)
        snapshot_path = out_dir / source.staging.name
        snapshot_paths.append(snapshot_path)
        assert snapshot_path.is_file()
        with pytest.raises(OSError):
            os.write(source.fileno(), b"x")
        return real_preflight(source, max_entries=max_entries)

    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(
        _download,
        "_preflight_zip_entry_count",
        inspect_snapshot,
    )
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert snapshot_paths
    assert all(not path.exists() for path in snapshot_paths)


def test_zip_snapshot_copy_uses_positive_bounded_io(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    read_sizes = []
    write_sizes = []
    real_open_download_staging = _download._open_download_staging
    real_write = _download.os.write

    class TrackingReader:
        def __init__(self, source):
            self._source = source

        def read(self, size=-1):
            read_sizes.append(size)
            return self._source.read(size)

        def __getattr__(self, name):
            return getattr(self._source, name)

    @contextmanager
    def track_staging_reads(staging):
        with real_open_download_staging(staging) as source:
            yield TrackingReader(source)

    def stub_download(url, destination, **kwargs):
        with os.fdopen(os.dup(destination.fd), "wb") as target:
            target.write(payload)
            target.flush()
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def track_writes(fd, data):
        write_sizes.append(len(data))
        return real_write(fd, data)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_open_download_staging",
        track_staging_reads,
    )
    monkeypatch.setattr(_download.os, "write", track_writes)
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert read_sizes
    assert write_sizes
    assert all(
        0 < size <= _download._FILE_COPY_CHUNK_BYTES
        for size in read_sizes
    )
    assert all(
        0 < size <= _download._FILE_COPY_CHUNK_BYTES
        for size in write_sizes
    )


def test_zip_snapshot_setup_error_does_not_expose_private_path(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    snapshot_names = []
    real_open = _download.os.open

    def fail_snapshot_reopen(path, flags, *args, **kwargs):
        if (
            os.fspath(path).startswith(
                ".paperconan-zip-snapshot-"
            )
            and flags & os.O_ACCMODE == os.O_RDONLY
            and kwargs.get("dir_fd") is not None
        ):
            snapshot_names.append(os.fspath(path))
            raise OSError(13, "snapshot reopen denied", os.fspath(path))
        return real_open(path, flags, *args, **kwargs)

    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download.os, "open", fail_snapshot_reopen)
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    reasons = [item["reason"] for item in summary["skipped"]]
    assert summary["downloaded"] == []
    assert any("private ZIP snapshot unavailable" in reason for reason in reasons)
    assert snapshot_names
    assert all(
        name not in reason
        for name in snapshot_names
        for reason in reasons
    )
    assert all(
        ".paperconan-zip-snapshot-" not in reason
        for reason in reasons
    )
    assert not list(out_dir.glob(".paperconan-zip-snapshot-*"))
    assert not list(out_dir.glob("*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


@pytest.mark.parametrize(
    "failure_step",
    ["write", "reopen"],
)
def test_zip_snapshot_setup_failure_cleans_owned_staging(
    monkeypatch,
    tmp_path,
    failure_step,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    real_open = _download.os.open
    real_write = _download.os.write
    snapshot_writer_fd = None
    failed = False

    def fail_snapshot_open(path, flags, *args, **kwargs):
        nonlocal snapshot_writer_fd, failed
        is_snapshot = (
            os.fspath(path).startswith(
                ".paperconan-zip-snapshot-"
            )
            and kwargs.get("dir_fd") is not None
        )
        if (
            is_snapshot
            and failure_step == "reopen"
            and not failed
            and flags & os.O_ACCMODE == os.O_RDONLY
        ):
            failed = True
            raise OSError("snapshot read-only reopen unavailable")
        fd = real_open(path, flags, *args, **kwargs)
        if (
            is_snapshot
            and flags & os.O_ACCMODE == os.O_RDWR
        ):
            snapshot_writer_fd = fd
        return fd

    def fail_snapshot_write(fd, data):
        nonlocal failed
        if failure_step == "write" and not failed and fd == snapshot_writer_fd:
            failed = True
            raise OSError("snapshot write unavailable")
        return real_write(fd, data)

    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(_download.os, "open", fail_snapshot_open)
    monkeypatch.setattr(_download.os, "write", fail_snapshot_write)
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert failed is True
    assert summary["downloaded"] == []
    assert any(
        "private ZIP snapshot unavailable" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-zip-snapshot-*"))
    assert not list(out_dir.glob("*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def test_zip_snapshot_cleanup_failure_reports_pending_owned_staging(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    identity_bound_remove = _download._identity_bound_remove
    failures = []

    def fail_snapshot_cleanup(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        if (
            name.startswith(".paperconan-zip-snapshot-")
            and not failures
        ):
            failures.append(name)
            raise PermissionError("snapshot cleanup unavailable")
        return identity_bound_remove(
            directory_fd,
            name,
            descriptor,
            is_directory=is_directory,
        )

    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        fail_snapshot_cleanup,
    )
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == [
        "data.csv"
    ]
    assert failures
    assert {
        "name": candidate["supplementary_archive"]["name"],
        "reason": "private ZIP snapshot cleanup pending",
    } in summary["skipped"]
    snapshots = list(out_dir.glob(".paperconan-zip-snapshot-*"))
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == payload
    assert not list(out_dir.glob(".paperconan-archive-*"))


def test_zip_preflight_accepts_archive_comment():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("tables/data.csv", b"a,b\n1,2\n")
        archive.comment = b"source data comment with PK\x05\x06 marker"

    count = _download._preflight_zip_entry_count(
        io.BytesIO(payload.getvalue()),
        max_entries=10,
    )

    assert count == 1


@pytest.mark.parametrize(
    "payload",
    [
        _classic_eocd_bytes(),
        _zip64_archive_bytes(),
    ],
    ids=["classic", "zip64"],
)
def test_zip_preflight_accepts_empty_archives(payload):
    count = _download._preflight_zip_entry_count(
        io.BytesIO(payload),
        max_entries=10,
    )

    assert count == 0


def test_zip_preflight_accepts_valid_zip64_archive(tmp_path):
    payload = _zip64_archive_bytes([
        ("tables/data.csv", b"a,b\n1,2\n"),
    ])
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    count = _download._preflight_zip_entry_count(
        io.BytesIO(payload),
        max_entries=10,
    )
    extracted = _download._extract_selected_zip(payload, str(out_dir))

    assert count == 1
    assert [Path(path).name for path in extracted] == ["data.csv"]


def _ambiguous_eocd_bytes():
    nested = _classic_eocd_bytes()
    return _classic_eocd_bytes(comment=nested)


def _zip64_without_locator_bytes():
    return _classic_eocd_bytes(
        entries_on_disk=0xFFFF,
        total_entries=0xFFFF,
        central_directory_size=0xFFFFFFFF,
        central_directory_offset=0xFFFFFFFF,
    )


def _zip64_multidisk_locator_bytes():
    payload = _zip64_archive_bytes()
    locator_offset = len(payload) - 22 - 20
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 1, 0, 2)
    return payload[:locator_offset] + locator + payload[locator_offset + 20:]


@pytest.mark.parametrize(
    "payload",
    [
        b"PK\x05\x06truncated",
        _classic_eocd_bytes(disk_number=1),
        _ambiguous_eocd_bytes(),
        _zip64_without_locator_bytes(),
        _zip64_multidisk_locator_bytes(),
        _zip64_archive_bytes()[:-98] + (
            struct.pack(
                "<4sQ2H2I4Q",
                b"PK\x06\x06",
                45,
                45,
                45,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            + _zip64_archive_bytes()[-42:]
        ),
    ],
    ids=[
        "truncated-eocd",
        "multi-disk-classic",
        "ambiguous-eocd",
        "missing-zip64-locator",
        "multi-disk-zip64",
        "invalid-zip64-record-size",
    ],
)
def test_malformed_zip_metadata_is_rejected_before_zipfile_construction(
    monkeypatch,
    tmp_path,
    payload,
):
    out_dir = tmp_path / "out"
    zipfile_init_called = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def fail_zipfile_init(archive, *args, **kwargs):
        nonlocal zipfile_init_called
        zipfile_init_called = True
        raise AssertionError("ZipFile constructed for invalid ZIP metadata")

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(zipfile.ZipFile, "__init__", fail_zipfile_init)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert zipfile_init_called is False
    assert summary["downloaded"] == []
    assert any(
        "archive processing unavailable" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob("*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))


def _rewrite_classic_zip_counts(payload, count):
    rewritten = bytearray(payload)
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    struct.pack_into("<HH", rewritten, eocd_offset + 8, count, count)
    return bytes(rewritten)


def _rewrite_zip64_counts(payload, count):
    rewritten = bytearray(payload)
    zip64_offset = payload.rfind(b"PK\x06\x06")
    assert zip64_offset >= 0
    struct.pack_into("<QQ", rewritten, zip64_offset + 24, count, count)
    return bytes(rewritten)


def _central_directory_offset(payload):
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    fields = struct.unpack_from("<4s4H2IH", payload, eocd_offset)
    return fields[6]


def _classic_zip_bytes(files):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, data in files:
            archive.writestr(name, data)
    return payload.getvalue()


def _forged_low_count_zip():
    payload = _classic_zip_bytes([
        (f"tables/data-{index}.csv", b"")
        for index in range(3)
    ])
    return _rewrite_classic_zip_counts(payload, 1), 2


def _forged_high_count_zip():
    payload = _classic_zip_bytes([
        ("tables/" + ("x" * 100) + ".csv", b""),
    ])
    return _rewrite_classic_zip_counts(payload, 2), 10


def _forged_zip64_count_zip():
    payload = _zip64_archive_bytes([
        ("tables/first.csv", b""),
        ("tables/second.csv", b""),
    ])
    return _rewrite_zip64_counts(payload, 1), 10


def _oversized_central_name_zip():
    payload = bytearray(_classic_zip_bytes([
        ("tables/data.csv", b""),
    ]))
    central_offset = _central_directory_offset(payload)
    struct.pack_into("<H", payload, central_offset + 28, 0xFFFF)
    return bytes(payload), 10


def _unknown_trailing_central_record_zip():
    payload = bytearray(_classic_zip_bytes([
        ("tables/first.csv", b""),
        ("tables/second.csv", b""),
    ]))
    first_offset = _central_directory_offset(payload)
    first_header = struct.unpack_from(
        "<4s6H3I5H2I",
        payload,
        first_offset,
    )
    second_offset = (
        first_offset
        + 46
        + first_header[10]
        + first_header[11]
        + first_header[12]
    )
    payload[second_offset:second_offset + 4] = b"NOPE"
    return bytes(payload), 10


@pytest.mark.parametrize(
    ("payload_factory", "expected_reason"),
    [
        (_forged_low_count_zip, "observed ZIP entry count"),
        (_forged_high_count_zip, "ZIP entry counts are inconsistent"),
        (_forged_zip64_count_zip, "ZIP entry counts are inconsistent"),
        (_oversized_central_name_zip, "central directory record is truncated"),
        (
            _unknown_trailing_central_record_zip,
            "central directory signature is invalid",
        ),
    ],
    ids=[
        "classic-forged-low-count",
        "classic-forged-high-count",
        "zip64-forged-count",
        "oversized-variable-field",
        "unknown-trailing-record",
    ],
)
def test_forged_central_directory_is_rejected_before_zipfile_construction(
    monkeypatch,
    tmp_path,
    payload_factory,
    expected_reason,
):
    payload, max_entries = payload_factory()
    out_dir = tmp_path / "out"
    zipfile_init_called = False
    _install_archive_payload(monkeypatch, payload)

    def fail_zipfile_init(archive, *args, **kwargs):
        nonlocal zipfile_init_called
        zipfile_init_called = True
        raise zipfile.BadZipFile("ZipFile constructed before central walk")

    monkeypatch.setattr(
        _download,
        "_MAX_RAW_ZIP_ENTRIES_PER_ARCHIVE",
        max_entries,
    )
    monkeypatch.setattr(zipfile.ZipFile, "__init__", fail_zipfile_init)
    candidate, _ = _archive_candidate("supplementary")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert zipfile_init_called is False
    assert summary["downloaded"] == []
    assert any(
        expected_reason in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob("*.csv"))
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def _zero_byte_archive_bytes(kind, prefix, count):
    payload = io.BytesIO()
    if kind == "oa":
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for index in range(count):
                info = tarfile.TarInfo(f"tables/{prefix}-{index:03d}.csv")
                info.size = 0
                archive.addfile(info, io.BytesIO())
    else:
        with zipfile.ZipFile(payload, "w") as archive:
            for index in range(count):
                archive.writestr(f"tables/{prefix}-{index:03d}.csv", b"")
    return payload.getvalue()


def test_tar_member_ceiling_streams_without_materializing_member_list(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _zero_byte_archive_bytes("oa", "oa", 4)
    download_calls = []

    def stub_download(url, destination, **kwargs):
        download_calls.append(url)
        if url != "https://example.test/oa":
            raise AssertionError("shared archive member ceiling was not enforced")
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def fail_getmembers(archive):
        raise AssertionError("tar member list was materialized")

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(tarfile.TarFile, "getmembers", fail_getmembers)
    monkeypatch.setattr(_download, "_MAX_PUBLISHED_FILES_PER_CANDIDATE", 10)
    monkeypatch.setattr(_download, "_MAX_ARCHIVE_MEMBERS_PER_CANDIDATE", 2)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "oa_package": {
            "url": "https://example.test/oa",
            "name": "oa.tar.gz",
        },
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    downloaded_names = [Path(path).name for path in summary["downloaded"]]
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert download_calls == ["https://example.test/oa"]
    assert downloaded_names == ["oa-000.csv", "oa-001.csv"]
    assert [entry["file"] for entry in sidecar["downloads"]] == downloaded_names
    assert any(
        "archive member cardinality ceiling" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-*"))


def test_zero_byte_archive_member_ceiling_is_shared_across_tar_and_zip(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payloads = {
        "https://example.test/oa": _zero_byte_archive_bytes("oa", "oa", 2),
        "https://example.test/supplementary": _zero_byte_archive_bytes(
            "supplementary",
            "supplementary",
            4,
        ),
    }

    def stub_download(url, destination, **kwargs):
        payload = payloads[url]
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PUBLISHED_FILES_PER_CANDIDATE", 10)
    monkeypatch.setattr(_download, "_MAX_ARCHIVE_MEMBERS_PER_CANDIDATE", 3)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "oa_package": {
            "url": "https://example.test/oa",
            "name": "oa.tar.gz",
        },
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    downloaded_names = [Path(path).name for path in summary["downloaded"]]
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    published_names = sorted(
        path.name
        for path in out_dir.iterdir()
        if path.name != _download.SOURCE_SIDECAR
    )
    assert downloaded_names == [
        "oa-000.csv",
        "oa-001.csv",
        "supplementary-000.csv",
    ]
    assert published_names == sorted(downloaded_names)
    assert [entry["file"] for entry in sidecar["downloads"]] == sorted(
        downloaded_names
    )
    assert any(
        "archive member cardinality ceiling" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-*"))


def test_published_file_ceiling_spans_direct_and_all_archive_sources(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payloads = {
        "https://example.test/oa": _zero_byte_archive_bytes("oa", "oa", 2),
        "https://example.test/supplementary": _zero_byte_archive_bytes(
            "supplementary",
            "supplementary",
            2,
        ),
    }

    def stub_download(url, destination, **kwargs):
        payload = payloads.get(url, b"")
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_PUBLISHED_FILES_PER_CANDIDATE", 3)
    monkeypatch.setattr(_download, "_MAX_ARCHIVE_MEMBERS_PER_CANDIDATE", 10)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "direct.csv",
            "download_url": "https://example.test/direct.csv",
        }],
        "oa_package": {
            "url": "https://example.test/oa",
            "name": "oa.tar.gz",
        },
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        include_images=True,
    )

    downloaded_names = [Path(path).name for path in summary["downloaded"]]
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert downloaded_names == ["direct.csv", "oa-000.csv", "oa-001.csv"]
    assert len(sidecar["downloads"]) == 3
    assert {
        path.name
        for path in out_dir.iterdir()
        if path.name != _download.SOURCE_SIDECAR
    } == set(downloaded_names)
    assert any(
        "published file cardinality ceiling" in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-*"))


@pytest.mark.parametrize("existing_sidecar", [False, True])
def test_oversized_generated_sidecar_is_not_published(
    monkeypatch,
    tmp_path,
    existing_sidecar,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    previous_bytes = b'{"cand_id":"previous:1","downloads":[]}'
    if existing_sidecar:
        sidecar.write_bytes(previous_bytes)

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, b"")
        return {
            "ok": True,
            "path": destination,
            "size": 0,
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, "_MAX_SOURCE_SIDECAR_BYTES", 32)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "title": "generated provenance exceeds the configured bound",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert any(
        "new provenance sidecar exceeds" in item["reason"]
        for item in summary["skipped"]
    )
    if existing_sidecar:
        assert sidecar.read_bytes() == previous_bytes
    else:
        assert not sidecar.exists()
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


def test_mismatched_prior_sidecar_is_retained_with_explicit_reason(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    previous_bytes = b'{"cand_id":"previous:1","downloads":[]}'
    sidecar.write_bytes(previous_bytes)
    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, b"")
        return {
            "ok": True,
            "path": destination,
            "size": 0,
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == [str(out_dir / "data.csv")]
    assert any(
        "retained existing provenance sidecar" in item["reason"]
        for item in summary["skipped"]
    )
    assert sidecar.read_bytes() == previous_bytes
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


@pytest.mark.parametrize("publication_kind", ["direct", "oa", "supplementary"])
def test_download_candidate_rejects_equal_size_in_place_content_mutation(
    monkeypatch,
    tmp_path,
    publication_kind,
):
    out_dir = tmp_path / "out"
    original = b"a,b\n1,2\n"
    replacement = b"x,y\n9,8\n"
    assert len(replacement) == len(original)
    payload = (
        original
        if publication_kind == "direct"
        else _archive_bytes(publication_kind, data=original)
    )
    content_mutated = False
    final_phase = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    real_verify = _download._verify_published_output_file

    def mutate_content_at_final_boundary(output, entry):
        nonlocal content_mutated
        if final_phase and not content_mutated:
            entry_fd = os.open(
                "data.csv",
                os.O_WRONLY | os.O_NOFOLLOW,
                dir_fd=output.fd,
            )
            try:
                opened = os.fstat(entry_fd)
                os.lseek(entry_fd, 0, os.SEEK_SET)
                os.write(entry_fd, replacement)
                os.fsync(entry_fd)
                current = os.fstat(entry_fd)
            finally:
                os.close(entry_fd)
            assert (opened.st_dev, opened.st_ino) == (
                current.st_dev,
                current.st_ino,
            )
            assert opened.st_size == current.st_size
            content_mutated = True
        return real_verify(output, entry)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        mutate_content_at_final_boundary,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if publication_kind == "direct":
        real_publish = _download._write_collision_safe

        def publish_then_arm(*args, **kwargs):
            nonlocal final_phase
            published = real_publish(*args, **kwargs)
            final_phase = True
            return published

        monkeypatch.setattr(
            _download,
            "_write_collision_safe",
            publish_then_arm,
        )
        candidate["tabular_files"] = [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }]
    else:
        archive = {
            "url": f"https://example.test/{publication_kind}",
            "name": f"{publication_kind}.archive",
        }
        if publication_kind == "oa":
            candidate["oa_package"] = archive
            helper_name = "_download_oa_package"
        else:
            candidate["supplementary_archive"] = archive
            helper_name = "_download_supplementary_archive"
        real_helper = getattr(_download, helper_name)

        def helper_then_arm(*args, **kwargs):
            nonlocal final_phase
            extracted = real_helper(*args, **kwargs)
            final_phase = True
            return extracted

        monkeypatch.setattr(_download, helper_name, helper_then_arm)

    summary = _download.download_candidate(candidate, str(out_dir))

    assert content_mutated
    assert (out_dir / "data.csv").read_bytes() == replacement
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == 1
    assert "content changed" in summary["skipped"][0]["reason"]
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert sidecar["downloads"] == []


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_download_staging_stays_on_pinned_root_during_replacement(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = _archive_bytes(archive_kind)
    replacement_received_bytes = False

    def swap_then_download(url, destination, **kwargs):
        nonlocal replacement_received_bytes
        out_dir.rename(displaced)
        out_dir.mkdir()
        if hasattr(destination, "fd"):
            os.ftruncate(destination.fd, 0)
            os.lseek(destination.fd, 0, os.SEEK_SET)
            os.write(destination.fd, payload)
        else:
            path = Path(destination)
            path.write_bytes(payload)
            replacement_received_bytes = path.parent == out_dir
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", swap_then_download)
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == []
    assert summary["skipped"]
    assert replacement_received_bytes is False
    assert list(out_dir.iterdir()) == []
    assert not list(displaced.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_result_rejects_post_extraction_output_root_replacement(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = _archive_bytes(archive_kind)
    extraction_complete = False
    root_replaced = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    extract_name = (
        "_extract_selected_tar"
        if archive_kind == "oa"
        else "_extract_selected_zip"
    )
    real_extract = getattr(_download, extract_name)
    real_verify = _download._PinnedOutputDirectory.verify

    def track_extraction(*args, **kwargs):
        nonlocal extraction_complete
        extracted = real_extract(*args, **kwargs)
        extraction_complete = True
        return extracted

    def verify_then_replace_root(output):
        nonlocal root_replaced
        real_verify(output)
        if extraction_complete and not root_replaced:
            out_dir.rename(displaced)
            out_dir.mkdir()
            (out_dir / "data.csv").write_bytes(b"replacement,root\n")
            root_replaced = True

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, extract_name, track_extraction)
    monkeypatch.setattr(
        _download._PinnedOutputDirectory,
        "verify",
        verify_then_replace_root,
    )
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(out_dir))

    assert root_replaced
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == 2
    assert "output directory changed" in summary["skipped"][0]["reason"]
    assert summary["skipped"][1] == {
        "name": _download.SOURCE_SIDECAR,
        "reason": "provenance sidecar publication unavailable",
    }
    assert (out_dir / "data.csv").read_bytes() == b"replacement,root\n"
    assert (displaced / "data.csv").read_bytes() == b"a,b\n1,2\n"


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_download_candidate_rejects_root_replacement_after_archive_helper(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced-out"
    payload = _archive_bytes(archive_kind)
    replacement = b"replacement,root\n"
    root_replaced = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    helper_name = (
        "_download_oa_package"
        if archive_kind == "oa"
        else "_download_supplementary_archive"
    )
    real_helper = getattr(_download, helper_name)
    real_getsize = _download.os.path.getsize
    replaced_root_size_reads = []

    def helper_then_replace_root(*args, **kwargs):
        nonlocal root_replaced
        extracted = real_helper(*args, **kwargs)
        out_dir.rename(displaced)
        out_dir.mkdir()
        (out_dir / "data.csv").write_bytes(replacement)
        root_replaced = True
        return extracted

    def reject_replaced_root_size_read(path):
        if root_replaced:
            replaced_root_size_reads.append(os.fspath(path))
            raise AssertionError(
                "archive provenance must not reopen the replaced root"
            )
        return real_getsize(path)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download, helper_name, helper_then_replace_root)
    monkeypatch.setattr(
        _download.os.path,
        "getsize",
        reject_replaced_root_size_read,
    )
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(out_dir))

    assert root_replaced
    assert replaced_root_size_reads == []
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == 2
    assert "output directory changed" in summary["skipped"][0]["reason"]
    assert summary["skipped"][1] == {
        "name": _download.SOURCE_SIDECAR,
        "reason": "provenance sidecar publication unavailable",
    }
    assert (out_dir / "data.csv").read_bytes() == replacement
    assert (displaced / "data.csv").read_bytes() == b"a,b\n1,2\n"


def test_supplementary_archive_skips_replaced_final_entry(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _archive_bytes("supplementary")
    replacement = b"replacement,entry\n"
    entry_replaced = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    real_verify = _download._verify_published_output_file

    def replace_entry_then_verify(output, entry):
        nonlocal entry_replaced
        if not entry_replaced:
            os.unlink(entry.filename, dir_fd=output.fd)
            replacement_fd = os.open(
                entry.filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output.fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
            entry_replaced = True
        return real_verify(output, entry)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        replace_entry_then_verify,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementary",
            "name": "supplementary.zip",
        },
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert entry_replaced
    assert summary["downloaded"] == []
    assert len(summary["skipped"]) == 1
    assert "stable regular file" in summary["skipped"][0]["reason"]
    assert (out_dir / "data.csv").read_bytes() == replacement


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
@pytest.mark.parametrize("metadata_kind", ["symlink", "absolute", "parent"])
def test_archive_download_staging_ignores_unsafe_metadata_paths(
    monkeypatch,
    tmp_path,
    archive_kind,
    metadata_kind,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sentinel = tmp_path / f"{archive_kind}-{metadata_kind}-sentinel"
    sentinel.write_bytes(b"outside")
    if metadata_kind == "symlink":
        metadata_name = f"{archive_kind}.archive"
        (out_dir / metadata_name).symlink_to(sentinel)
    elif metadata_kind == "absolute":
        metadata_name = str(tmp_path / f"{archive_kind}-escaped.archive")
    else:
        metadata_name = f"../{archive_kind}-escaped.archive"

    destinations = []
    payload = _archive_bytes(archive_kind)

    def stub_download(url, dest, **kwargs):
        destinations.append(dest)
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest, "size": len(payload)}

    monkeypatch.setattr(_download, "download_file", stub_download)
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": metadata_name,
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(out_dir))

    assert sentinel.read_bytes() == b"outside"
    assert [Path(path).name for path in summary["downloaded"]] == ["data.csv"]
    assert len(destinations) == 1
    assert hasattr(destinations[0], "fd")
    assert destinations[0].output.path == str(out_dir.resolve())
    assert destinations[0].name != Path(metadata_name).name


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_failed_archive_download_removes_staging_file(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    destinations = []

    def failed_download(url, dest, **kwargs):
        destinations.append(dest)
        return {"ok": False, "path": dest, "skipped_reason": "network unavailable"}

    monkeypatch.setattr(_download, "download_file", failed_download)
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert len(destinations) == 1
    assert summary["downloaded"] == []
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def test_direct_download_rejects_staging_path_swapped_to_symlink(
    monkeypatch,
    tmp_path,
):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"outside,bytes\n")

    def swapped_download(url, dest, **kwargs):
        if hasattr(dest, "fd"):
            os.ftruncate(dest.fd, 0)
            os.write(dest.fd, b"downloaded,bytes\n")
            os.unlink(dest.name, dir_fd=dest.output.fd)
            os.symlink(outside, dest.name, dir_fd=dest.output.fd)
        else:
            path = Path(dest)
            path.write_bytes(b"downloaded,bytes\n")
            path.unlink()
            path.symlink_to(outside)
        return {
            "ok": True,
            "path": dest,
            "size": len(b"downloaded,bytes\n"),
            "content_type": "text/csv",
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", swapped_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [
            {
                "name": "data.csv",
                "download_url": "https://example.test/data.csv",
            },
        ],
    }

    summary = _download.download_candidate(candidate, str(tmp_path / "out"))

    assert outside.read_bytes() == b"outside,bytes\n"
    assert summary["downloaded"] == []
    assert "stable regular file" in summary["skipped"][0]["reason"]
    assert not (tmp_path / "out" / "data.csv").exists()


@pytest.mark.parametrize("archive_kind", ["oa", "supplementary"])
def test_archive_download_rejects_staging_path_swapped_to_symlink(
    monkeypatch,
    tmp_path,
    archive_kind,
):
    outside_archive = tmp_path / f"outside-{archive_kind}.archive"
    outside_archive.write_bytes(
        _archive_bytes(archive_kind, "outside.csv", b"outside,bytes\n")
    )

    def swapped_download(url, dest, **kwargs):
        payload = _archive_bytes(archive_kind)
        if hasattr(dest, "fd"):
            os.ftruncate(dest.fd, 0)
            os.write(dest.fd, payload)
            os.unlink(dest.name, dir_fd=dest.output.fd)
            os.symlink(outside_archive, dest.name, dir_fd=dest.output.fd)
        else:
            path = Path(dest)
            path.write_bytes(payload)
            path.unlink()
            path.symlink_to(outside_archive)
        return {"ok": True, "path": dest, "size": len(payload)}

    monkeypatch.setattr(_download, "download_file", swapped_download)
    archive = {
        "url": f"https://example.test/{archive_kind}",
        "name": f"{archive_kind}.archive",
    }
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }
    if archive_kind == "oa":
        candidate["oa_package"] = archive
    else:
        candidate["supplementary_archive"] = archive

    out_dir = tmp_path / "out"
    summary = _download.download_candidate(candidate, str(out_dir))

    assert outside_archive.exists()
    assert summary["downloaded"] == []
    assert "stable regular file" in summary["skipped"][0]["reason"]
    assert not (out_dir / "outside.csv").exists()
    retained = list(out_dir.glob(".paperconan-archive-*"))
    assert len(retained) == 1
    assert retained[0].is_symlink()
    assert retained[0].resolve() == outside_archive.resolve()
    assert summary["skipped"][-1] == {
        "name": archive["name"],
        "reason": "download staging cleanup incomplete: deletion failed",
    }
    assert retained[0].name not in repr(summary["skipped"])


def test_direct_download_rejects_same_inode_growth_with_bounded_staging_read(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = b"a,b\n1,2\n"
    max_bytes = len(payload)
    read_sizes = []
    real_open_download_staging = _download._open_download_staging

    class GrowingReader:
        def __init__(self, source):
            self._source = source

        def read(self, size=-1):
            read_sizes.append(size)
            return self._source.read(size)

        def __getattr__(self, name):
            return getattr(self._source, name)

    @contextmanager
    def grow_after_download(staging):
        with real_open_download_staging(staging) as source:
            os.lseek(staging.fd, 0, os.SEEK_END)
            os.write(staging.fd, b"growth")
            os.fsync(staging.fd)
            source.seek(0)
            yield GrowingReader(source)

    monkeypatch.setattr(
        _http,
        "open_http",
        lambda req, timeout=None: _Resp(payload, "text/csv"),
    )
    monkeypatch.setattr(
        _download,
        "_open_download_staging",
        grow_after_download,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(
        candidate,
        str(out_dir),
        max_bytes=max_bytes,
    )

    assert summary["downloaded"] == []
    assert not (out_dir / "data.csv").exists()
    assert read_sizes
    assert all(size > 0 for size in read_sizes)
    assert any(
        "downloaded file exceeds max_bytes after staging verification"
        in item["reason"]
        for item in summary["skipped"]
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    if sidecar.exists():
        assert json.loads(sidecar.read_text(encoding="utf-8"))["downloads"] == []


def test_direct_download_rejects_same_size_staging_mutation_after_read(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = b"a,b\n1,2\n"
    replacement = b"x,y\n9,8\n"
    assert len(payload) == len(replacement)
    real_open_download_staging = _download._open_download_staging
    mutated = False
    read_sizes = []

    class MutatingReader:
        def __init__(self, source, staging):
            self._source = source
            self._staging = staging

        def read(self, size=-1):
            nonlocal mutated
            read_sizes.append(size)
            data = self._source.read(size)
            if data and not mutated:
                os.lseek(self._staging.fd, 0, os.SEEK_SET)
                os.write(self._staging.fd, replacement)
                os.ftruncate(self._staging.fd, len(replacement))
                os.fsync(self._staging.fd)
                mutated = True
            return data

        def __getattr__(self, name):
            return getattr(self._source, name)

    @contextmanager
    def mutate_after_read(staging):
        with real_open_download_staging(staging) as source:
            yield MutatingReader(source, staging)

    monkeypatch.setattr(
        _http,
        "open_http",
        lambda req, timeout=None: _Resp(payload, "text/csv"),
    )
    monkeypatch.setattr(
        _download,
        "_open_download_staging",
        mutate_after_read,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert mutated
    assert summary["downloaded"] == []
    assert not (out_dir / "data.csv").exists()
    assert read_sizes
    assert all(size > 0 for size in read_sizes)
    assert any(
        "downloaded file content changed during bounded staging read"
        in item["reason"]
        for item in summary["skipped"]
    )


def _tar_with_members(members, *, tar_format=tarfile.PAX_FORMAT):
    payload = io.BytesIO()
    with tarfile.open(
        fileobj=payload,
        mode="w:gz",
        format=tar_format,
    ) as archive:
        for kind, name, body in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.size = 0
                archive.addfile(info)
            else:
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
    return payload.getvalue()


def test_oa_tar_raw_member_ceiling_counts_ineligible_directories(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _tar_with_members([
        ("dir", f"nested/{index:04d}/", b"")
        for index in range(2000)
    ])
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(
        _download,
        "_MAX_RAW_TAR_MEMBERS_PER_ARCHIVE",
        1000,
        raising=False,
    )
    candidate, _ = _archive_candidate("oa")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == []
    assert any(
        "raw TAR member count exceeds traversal ceiling (1000)"
        in item["reason"]
        for item in summary["skipped"]
    )
    assert not list(out_dir.glob(".paperconan-archive-*"))
    assert not list(out_dir.glob(".paperconan-publish-*"))


def test_oa_tar_rejects_decompressed_stream_above_archive_ceiling(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    payload = _tar_with_members([
        ("file", "nested/ignored.bin", b"\0" * (64 * 1024)),
        ("file", "nested/data.csv", b"a,b\n1,2\n"),
    ])
    assert len(payload) < 4096
    _install_archive_payload(monkeypatch, payload)
    monkeypatch.setattr(
        _download,
        "_MAX_UNCOMPRESSED_TAR_BYTES_PER_ARCHIVE",
        4096,
        raising=False,
    )
    candidate, _ = _archive_candidate("oa")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == []
    assert any(
        "decompressed TAR byte ceiling exceeded (4096)"
        in item["reason"]
        for item in summary["skipped"]
    )
    assert not (out_dir / "data.csv").exists()


@pytest.mark.parametrize(
    "tar_format",
    [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT],
    ids=["gnu", "pax"],
)
def test_oa_tar_streaming_accepts_valid_gnu_and_pax_archives(
    monkeypatch,
    tmp_path,
    tar_format,
):
    out_dir = tmp_path / "out"
    payload = _tar_with_members(
        [("file", "nested/" + "long-" * 24 + "data.csv", b"a,b\n1,2\n")],
        tar_format=tar_format,
    )
    _install_archive_payload(monkeypatch, payload)
    real_tar_open = tarfile.open
    extraction_modes = []

    def track_streaming_open(*args, **kwargs):
        mode = kwargs.get("mode")
        if mode in {"r:gz", "r|"}:
            extraction_modes.append(mode)
        return real_tar_open(*args, **kwargs)

    monkeypatch.setattr(_download.tarfile, "open", track_streaming_open)
    candidate, _ = _archive_candidate("oa")

    summary = _download.download_candidate(candidate, str(out_dir))

    assert [Path(path).name for path in summary["downloaded"]] == [
        "long-" * 24 + "data.csv"
    ]
    assert (out_dir / ("long-" * 24 + "data.csv")).read_bytes() == b"a,b\n1,2\n"
    assert extraction_modes == ["r|"]


@pytest.mark.parametrize(
    "tar_format",
    [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT],
    ids=["gnu-longname", "pax-path"],
)
def test_tar_raw_ceiling_counts_hidden_name_metadata_headers(
    monkeypatch,
    tmp_path,
    tar_format,
):
    payload = _tar_with_members(
        [("file", "nested/" + "long-" * 24 + "data.csv", b"a,b\n1,2\n")],
        tar_format=tar_format,
    )
    monkeypatch.setattr(
        _download,
        "_MAX_RAW_TAR_MEMBERS_PER_ARCHIVE",
        1,
    )

    with _download._pinned_output_directory(str(tmp_path / "out")) as output:
        with pytest.raises(
            ValueError,
            match=r"raw TAR member count exceeds traversal ceiling \(1\)",
        ):
            _download._extract_selected_tar(
                io.BytesIO(payload),
                output,
                return_entries=True,
            )


def test_tar_raw_ceiling_resets_per_archive_and_eligible_ceiling_is_shared(
    monkeypatch,
    tmp_path,
):
    first = _tar_with_members([
        ("file", "first.csv", b"1\n"),
        ("dir", "one/", b""),
        ("dir", "two/", b""),
    ])
    second = _tar_with_members([
        ("dir", "three/", b""),
        ("file", "second.csv", b"2\n"),
    ])
    third = _tar_with_members([
        ("file", "third.csv", b"3\n"),
    ])
    monkeypatch.setattr(
        _download,
        "_MAX_RAW_TAR_MEMBERS_PER_ARCHIVE",
        2,
        raising=False,
    )
    cardinality = _download._CandidateCardinality(
        max_published_files=10,
        max_archive_members=2,
    )
    reasons = []
    out_dir = tmp_path / "out"

    with _download._pinned_output_directory(str(out_dir)) as output:
        with pytest.raises(
            ValueError,
            match=r"raw TAR member count exceeds traversal ceiling \(2\)",
        ):
            _download._extract_selected_tar(
                io.BytesIO(first),
                output,
                return_entries=True,
                cardinality=cardinality,
                limit_reasons=reasons,
            )
        second_entries = _download._extract_selected_tar(
            io.BytesIO(second),
            output,
            return_entries=True,
            cardinality=cardinality,
            limit_reasons=reasons,
        )
        third_entries = _download._extract_selected_tar(
            io.BytesIO(third),
            output,
            return_entries=True,
            cardinality=cardinality,
            limit_reasons=reasons,
        )

    assert [entry.filename for entry in second_entries] == ["second.csv"]
    assert third_entries == []
    assert cardinality.archive_members == 2
    assert reasons == [
        "archive member cardinality ceiling reached "
        "(2); remaining eligible members were skipped"
    ]
    assert (out_dir / "first.csv").read_bytes() == b"1\n"
    assert (out_dir / "second.csv").read_bytes() == b"2\n"
    assert not (out_dir / "third.csv").exists()


def test_sidecar_is_published_once_after_both_output_reconciliation_boundaries(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    payload = b"a,b\n1,2\n"
    reconcile_calls = 0
    sidecar_writes = 0
    real_reconcile = _download._reconcile_publications
    real_write_sidecar = _download._write_source_sidecar

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def track_reconciliation(*args, **kwargs):
        nonlocal reconcile_calls
        reconcile_calls += 1
        assert not sidecar.exists()
        return real_reconcile(*args, **kwargs)

    def track_final_publication(cand, output, downloads=None):
        nonlocal sidecar_writes
        sidecar_writes += 1
        assert reconcile_calls == 2
        return real_write_sidecar(cand, output, downloads=downloads)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_reconcile_publications",
        track_reconciliation,
    )
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        track_final_publication,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert summary["downloaded"] == [str(out_dir / "data.csv")]
    assert reconcile_calls == 2
    assert sidecar_writes == 1
    assert not sidecar.is_symlink()
    published = json.loads(sidecar.read_text(encoding="utf-8"))
    assert published["downloads"][0]["file"] == "data.csv"
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


def test_sidecar_publication_retains_concurrent_no_replace_creation(
    monkeypatch,
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    concurrent_bytes = b'{"cand_id":"concurrent:1","downloads":[]}'
    payload = b"a,b\n1,2\n"
    real_link = os.link
    replacement_installed = False

    def stub_download(url, destination, **kwargs):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
            "source_url": url,
        }

    def create_sidecar_before_link(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal replacement_installed
        if (
            not replacement_installed
            and Path(dst).name == _download.SOURCE_SIDECAR
        ):
            replacement_fd = os.open(
                dst,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(replacement_fd, concurrent_bytes)
            finally:
                os.close(replacement_fd)
            replacement_installed = True
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "link", create_sidecar_before_link)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": "data.csv",
            "download_url": "https://example.test/data.csv",
        }],
    }

    summary = _download.download_candidate(candidate, str(out_dir))

    assert replacement_installed
    assert summary["downloaded"] == [str(out_dir / "data.csv")]
    assert any(
        "retained existing provenance sidecar" in item["reason"]
        for item in summary["skipped"]
    )
    assert sidecar.read_bytes() == concurrent_bytes
    assert not list(out_dir.glob(".paperconan-sidecar-*"))


@pytest.mark.parametrize(
    "reserved_name",
    [
        _download.SOURCE_SIDECAR,
        _download.SOURCE_SIDECAR.upper(),
    ],
)
def test_remote_reserved_sidecar_basename_is_skipped_before_download(
    monkeypatch,
    tmp_path,
    reserved_name,
):
    def reject_download(*args, **kwargs):
        raise AssertionError("reserved provenance basename must not be downloaded")

    monkeypatch.setattr(_download, "download_file", reject_download)
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": f"remote/{reserved_name}",
            "download_url": "https://example.test/paperconan_source.json",
        }],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )

    assert summary["downloaded"] == []
    assert summary["skipped"] == [{
        "name": f"remote/{reserved_name}",
        "reason": "reserved provenance sidecar basename",
    }]
    assert sidecar["downloads"] == []


def test_unavailable_sidecar_publication_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        lambda *args, **kwargs: None,
    )
    candidate = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
    }

    summary = _download.download_candidate(candidate, str(tmp_path))

    assert summary["downloaded"] == []
    assert summary["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "provenance sidecar publication unavailable",
    }]
