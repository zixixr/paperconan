import gzip
import hashlib
import io
import struct
import tarfile
import warnings
import zipfile
import zlib
from pathlib import Path

import pytest

from paperconan._input import SUPPORTED_INPUT_EXTS
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
    errors = []

    def boom(req, timeout=None):
        error = urllib.error.HTTPError(
            "https://x/t.csv", 401, "Unauthorized", {}, None
        )
        errors.append(error)
        raise error

    monkeypatch.setattr(_download.urllib.request, "urlopen", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()
    assert not (tmp_path / "t.csv").exists()
    assert all(error.closed for error in errors)


def test_download_candidate_tabular_only(monkeypatch, tmp_path):
    saved = []

    def stub_dl(url, dest, **kw):
        Path(dest).write_bytes(b"x")
        saved.append(dest)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_dl)
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
                        lambda url, dest, **kw: (Path(dest).write_bytes(b"x"),
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

    def stub_dl(url, dest, **kw):
        Path(dest).write_bytes(zbytes)
        return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_dl)

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

    def stub_dl(url, dest, **kw):
        calls.append({"url": url, "max_bytes": kw.get("max_bytes")})
        Path(dest).write_bytes(zbytes)
        return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_dl)
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
                        lambda url, dest, **kw: (Path(dest).write_bytes(zbytes),
                                                 {"ok": True, "path": dest})[1])
    cand = {"cand_id": "europepmc:PMC1", "source": "europepmc", "tabular_files": [],
            "supplementary_archive": {"url": "https://ebi/PMC1/supplementaryFiles",
                                      "name": "PMC1.zip"}}
    summary = _download.download_candidate(cand, str(tmp_path), max_bytes=100)
    names = sorted(os.path.basename(p) for p in summary["downloaded"])
    assert names == ["small.csv"]
    assert not (tmp_path / "huge.csv").exists()


def test_oa_package_uses_default_archive_cap(monkeypatch, tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        body = b"a,b\n1,2\n"
        info = tarfile.TarInfo("table.csv")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    archive_bytes = archive.read_bytes()
    calls = []

    def stub_dl(url, dest, **kw):
        calls.append({"url": url, "max_bytes": kw.get("max_bytes")})
        Path(dest).write_bytes(archive_bytes)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_dl)
    cand = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "tabular_files": [],
        "oa_package": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package.tar.gz",
            "name": "oa_package.tar.gz",
        },
    }

    _download.download_candidate(cand, str(tmp_path / "out"))

    assert calls == [{
        "url": cand["oa_package"]["url"],
        "max_bytes": _download._ARCHIVE_MAX,
    }]


def test_oa_package_uses_custom_archive_cap(monkeypatch, tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        body = b"a,b\n1,2\n"
        info = tarfile.TarInfo("table.csv")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    archive_bytes = archive.read_bytes()
    calls = []

    def stub_dl(url, dest, **kw):
        calls.append({"url": url, "max_bytes": kw.get("max_bytes")})
        Path(dest).write_bytes(archive_bytes)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_dl)
    cand = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "tabular_files": [],
        "oa_package": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package.tar.gz",
            "name": "oa_package.tar.gz",
        },
    }

    _download.download_candidate(
        cand,
        str(tmp_path / "out"),
        archive_max=12345,
    )

    assert calls == [{
        "url": cand["oa_package"]["url"],
        "max_bytes": 12345,
    }]


def test_oa_package_extraction_still_caps_each_table(monkeypatch, tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for name, body in (
            ("small.csv", b"a,b\n1,2\n"),
            ("huge.csv", b"x" * 500),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    archive_bytes = archive.read_bytes()

    def stub_dl(url, dest, **kw):
        Path(dest).write_bytes(archive_bytes)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_dl)
    cand = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "tabular_files": [],
        "oa_package": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package.tar.gz",
            "name": "oa_package.tar.gz",
        },
    }
    out_dir = tmp_path / "out"

    summary = _download.download_candidate(
        cand,
        str(out_dir),
        max_bytes=100,
        archive_max=12345,
    )

    assert [Path(path).name for path in summary["downloaded"]] == [
        "small.csv"
    ]
    assert not (out_dir / "huge.csv").exists()


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
    errors = []

    def boom(req, timeout=None):
        error = urllib.error.HTTPError(
            "https://x/t.csv", 403, "Forbidden", {}, None
        )
        errors.append(error)
        raise error

    monkeypatch.setattr(_download.urllib.request, "urlopen", boom)
    res = _download.download_file("https://x/t.csv", str(tmp_path / "t.csv"))
    assert res["ok"] is False
    assert "auth" in res["skipped_reason"].lower()
    assert all(error.closed for error in errors)


def test_stream_failure_preserves_existing_destination(monkeypatch, tmp_path):
    class Broken(_Resp):
        def read(self, size=-1):
            if self.tell() >= 4:
                raise OSError("stream interrupted")
            return super().read(4)

    dest = tmp_path / "t.csv"
    dest.write_bytes(b"old-complete")
    monkeypatch.setattr(
        _download.urllib.request,
        "urlopen",
        lambda req, timeout=None: Broken(b"new-partial-data", "text/csv"),
    )
    result = _download.download_file(
        "https://x/t.csv", str(dest), retries=1
    )
    assert result["ok"] is False
    assert dest.read_bytes() == b"old-complete"
    assert not list(tmp_path.glob("*.part"))


def test_value_error_from_stream_retries_then_succeeds(monkeypatch, tmp_path):
    class TransientValueError(_Resp):
        def read(self, size=-1):
            if self.tell() >= 4:
                raise ValueError("transient stream failure")
            return super().read(4)

    attempts = {"count": 0}
    payload = b"complete-data"

    def urlopen(req, timeout=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return TransientValueError(b"partial-data", "text/csv")
        return _Resp(payload, "text/csv")

    monkeypatch.setattr(_download.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(_download.time, "sleep", lambda *_: None)
    dest = tmp_path / "t.csv"
    result = _download.download_file(
        "https://x/t.csv", str(dest), retries=2, backoff=0.0
    )

    assert attempts["count"] == 2
    assert result == {"ok": True, "path": str(dest), "size": len(payload)}
    assert dest.read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))


def test_body_limit_preserves_existing_destination(monkeypatch, tmp_path):
    dest = tmp_path / "t.csv"
    dest.write_bytes(b"old-complete")
    monkeypatch.setattr(
        _download.urllib.request,
        "urlopen",
        lambda req, timeout=None: _Resp(b"x" * 50, "text/csv"),
    )
    result = _download.download_file(
        "https://x/t.csv", str(dest), max_bytes=10, retries=1
    )
    assert result["ok"] is False
    assert dest.read_bytes() == b"old-complete"
    assert not list(tmp_path.glob("*.part"))


def test_transient_cleanup_removes_file_immediately(tmp_path):
    transient = tmp_path / "transient.part"
    transient.write_bytes(b"partial")

    _download._remove_transient_file(str(transient))

    assert not transient.exists()


def test_transient_cleanup_treats_missing_file_as_success(tmp_path):
    transient = tmp_path / "missing.part"

    _download._remove_transient_file(str(transient))

    assert not transient.exists()


def test_transient_cleanup_retries_once_then_succeeds(
    monkeypatch, tmp_path
):
    transient = tmp_path / "transient.part"
    transient.write_bytes(b"partial")
    original_remove = _download.os.remove
    attempts = 0

    def fail_once(path):
        nonlocal attempts
        if Path(path) == transient:
            attempts += 1
            if attempts == 1:
                raise PermissionError("temporary cleanup failure")
        return original_remove(path)

    monkeypatch.setattr(_download.os, "remove", fail_once)

    _download._remove_transient_file(str(transient))

    assert attempts == 2
    assert not transient.exists()


@pytest.mark.parametrize("failure_kind", ["stream", "size"])
def test_atomic_stream_write_wraps_persistent_part_cleanup_failure(
    monkeypatch, tmp_path, failure_kind
):
    if failure_kind == "stream":
        class BrokenStream(io.BytesIO):
            def read(self, size=-1):
                raise OSError("stream operation failed")

        source = BrokenStream(b"partial")
        max_bytes = 100
        primary_type = OSError
        primary_message = "stream operation failed"
    else:
        source = io.BytesIO(b"oversized")
        max_bytes = 1
        primary_type = _download._SizeLimitExceeded
        primary_message = "file exceeds max_bytes"

    original_remove = _download.os.remove
    attempts = []

    def fail_part_cleanup(path):
        if str(path).endswith(".part"):
            attempts.append(Path(path))
            raise PermissionError("persistent part cleanup failure")
        return original_remove(path)

    monkeypatch.setattr(
        _download.os, "remove", fail_part_cleanup
    )
    dest = tmp_path / "table.csv"

    with pytest.raises(
        _download._TransientCleanupError,
        match="^transient file cleanup failed$",
    ) as raised:
        _download._atomic_stream_write(
            source, str(dest), max_bytes
        )

    error = raised.value
    assert len(attempts) == 2
    assert len(set(attempts)) == 1
    assert not dest.exists()
    assert attempts[0].exists()
    assert error.transient_path == str(attempts[0])
    assert isinstance(error.cleanup_error, PermissionError)
    assert str(error.cleanup_error) == (
        "persistent part cleanup failure"
    )
    assert isinstance(error.operation_error, primary_type)
    assert primary_message in str(error.operation_error)
    assert error.__cause__ is error.cleanup_error
    assert error.cleanup_error.__cause__ is error.operation_error
    top_level = f"{error!s} {error!r}"
    assert str(tmp_path) not in top_level
    assert attempts[0].name not in top_level
    assert "persistent part cleanup failure" not in top_level
    assert primary_message not in top_level


def test_download_file_propagates_cleanup_failure_without_retry_or_skip(
    monkeypatch, tmp_path
):
    class BrokenStream(_Resp):
        def read(self, size=-1):
            raise OSError("private stream operation detail")

    network_attempts = 0

    def urlopen(req, timeout=None):
        nonlocal network_attempts
        network_attempts += 1
        return BrokenStream(b"partial", "text/csv")

    original_remove = _download.os.remove
    cleanup_attempts = []

    def fail_part_cleanup(path):
        if str(path).endswith(".part"):
            cleanup_attempts.append(Path(path))
            raise PermissionError("private cleanup OS detail")
        return original_remove(path)

    monkeypatch.setattr(
        _download.urllib.request, "urlopen", urlopen
    )
    monkeypatch.setattr(
        _download.os, "remove", fail_part_cleanup
    )
    dest = tmp_path / "table.csv"

    with pytest.raises(
        _download._TransientCleanupError,
        match="^transient file cleanup failed$",
    ) as raised:
        _download.download_file(
            "https://x/table.csv",
            str(dest),
            retries=3,
            backoff=0,
        )

    error = raised.value
    assert isinstance(error, Exception)
    assert not isinstance(error, OSError)
    assert network_attempts == 1
    assert len(cleanup_attempts) == 2
    assert len(set(cleanup_attempts)) == 1
    orphan = cleanup_attempts[0]
    assert orphan.exists()
    assert error.transient_path == str(orphan)
    assert error.cleanup_error is error.__cause__
    assert str(error.cleanup_error) == "private cleanup OS detail"
    assert error.operation_error is error.cleanup_error.__cause__
    assert str(error.operation_error) == (
        "private stream operation detail"
    )
    top_level = f"{error!s} {error!r}"
    assert str(tmp_path) not in top_level
    assert orphan.name not in top_level
    assert "private cleanup OS detail" not in top_level
    assert "private stream operation detail" not in top_level
    assert not dest.exists()


def test_zip_duplicate_basenames_are_both_preserved(tmp_path):
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/table.csv", b"a\n1\n")
        zf.writestr("b/table.csv", b"a\n2\n")
    out = tmp_path / "out"
    out.mkdir()

    paths = _download._extract_tabular_zip(str(archive), str(out))

    names = sorted(Path(path).name for path in paths)
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(name.startswith("table--") for name in names)
    assert {Path(path).read_bytes() for path in paths} == {
        b"a\n1\n",
        b"a\n2\n",
    }


def test_tar_duplicate_basenames_are_both_preserved(tmp_path):
    archive = tmp_path / "supp.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for name, body in (
            ("a/table.csv", b"a\n1\n"),
            ("b/table.csv", b"a\n2\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    out = tmp_path / "out"
    out.mkdir()

    paths = _download._extract_tabular_tar(str(archive), str(out))

    assert len({Path(path).name for path in paths}) == 2
    assert {Path(path).read_bytes() for path in paths} == {
        b"a\n1\n",
        b"a\n2\n",
    }


def test_zip_duplicate_member_occurrences_are_all_preserved(tmp_path):
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/table.csv", b"a\n1\n")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            zf.writestr("nested/table.csv", b"a\n2\n")
    out = tmp_path / "out"
    out.mkdir()

    first_paths = _download._extract_tabular_zip(str(archive), str(out))
    first_names = [Path(path).name for path in first_paths]

    second_out = tmp_path / "out-again"
    second_out.mkdir()
    second_paths = _download._extract_tabular_zip(str(archive), str(second_out))

    assert len(first_names) == 2
    assert len(set(first_names)) == 2
    assert first_names == [Path(path).name for path in second_paths]
    assert {Path(path).read_bytes() for path in first_paths} == {
        b"a\n1\n",
        b"a\n2\n",
    }


def test_tar_duplicate_member_occurrences_are_all_preserved(tmp_path):
    archive = tmp_path / "supp.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for body in (b"a\n1\n", b"a\n2\n"):
            info = tarfile.TarInfo("nested/table.csv")
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    out = tmp_path / "out"
    out.mkdir()

    paths = _download._extract_tabular_tar(str(archive), str(out))

    assert len(paths) == 2
    assert len({Path(path).name for path in paths}) == 2
    assert {Path(path).read_bytes() for path in paths} == {
        b"a\n1\n",
        b"a\n2\n",
    }


def test_tar_members_use_atomic_stream_write(monkeypatch, tmp_path):
    archive = tmp_path / "supp.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        body = b"a\n1\n"
        info = tarfile.TarInfo("nested/table.csv")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    out = tmp_path / "out"
    out.mkdir()
    calls = []
    atomic_stream_write = _download._atomic_stream_write

    def record_atomic_write(src, dest_path, max_bytes):
        calls.append((Path(dest_path).name, max_bytes))
        return atomic_stream_write(src, dest_path, max_bytes)

    monkeypatch.setattr(
        _download,
        "_atomic_stream_write",
        record_atomic_write,
    )

    paths = _download._extract_tabular_tar(
        str(archive),
        str(out),
        max_member_bytes=10,
    )

    assert calls == [("table.csv", 10)]
    assert [Path(path).read_bytes() for path in paths] == [body]


def test_archive_output_names_are_deterministic_for_colliding_paths():
    members = ["b/table.CSV", "a/table.csv"]

    first = _download._archive_output_names(members)
    second = _download._archive_output_names(reversed(members))

    assert first == second
    assert len(set(first.values())) == 2
    assert all(name.startswith("table--") for name in first.values())
    assert all(name.endswith(".csv") for name in first.values())


def test_zip_generated_hash_name_does_not_collide_with_real_basename(tmp_path):
    member = "a/table.csv"
    digest = hashlib.sha256(member.encode("utf-8")).hexdigest()[:10]
    generated_name = f"table--{digest}.csv"
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, b"a\n1\n")
        zf.writestr("b/table.csv", b"a\n2\n")
        zf.writestr(f"literal/{generated_name}", b"a\n3\n")

    extracted_names = []
    extracted_bodies = []
    for index in range(2):
        out = tmp_path / f"out-{index}"
        out.mkdir()
        paths = _download._extract_tabular_zip(str(archive), str(out))
        extracted_names.append([Path(path).name for path in paths])
        extracted_bodies.append([Path(path).read_bytes() for path in paths])

    assert len(extracted_names[0]) == 3
    assert len({name.casefold() for name in extracted_names[0]}) == 3
    assert extracted_names[0] == extracted_names[1]
    assert set(extracted_bodies[0]) == {b"a\n1\n", b"a\n2\n", b"a\n3\n"}
    assert extracted_bodies[0] == extracted_bodies[1]


def test_zip_occurrence_suffix_does_not_collide_case_insensitively(tmp_path):
    member = "a/table.csv"
    digest = hashlib.sha256(member.encode("utf-8")).hexdigest()[:10]
    occurrence_name = f"TABLE--{digest.upper()}--1.CSV"
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, b"a\n1\n")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            zf.writestr(member, b"a\n2\n")
        zf.writestr(f"literal/{occurrence_name}", b"a\n3\n")

    extracted_names = []
    extracted_bodies = []
    for index in range(2):
        out = tmp_path / f"out-{index}"
        out.mkdir()
        paths = _download._extract_tabular_zip(str(archive), str(out))
        extracted_names.append([Path(path).name for path in paths])
        extracted_bodies.append([Path(path).read_bytes() for path in paths])

    assert len(extracted_names[0]) == 3
    assert len({name.casefold() for name in extracted_names[0]}) == 3
    assert extracted_names[0] == extracted_names[1]
    assert set(extracted_bodies[0]) == {b"a\n1\n", b"a\n2\n", b"a\n3\n"}
    assert extracted_bodies[0] == extracted_bodies[1]


def test_archive_extracts_every_scanner_extension(tmp_path):
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in SUPPORTED_INPUT_EXTS:
            zf.writestr(f"nested/source.{ext}", b"x")
    out = tmp_path / "out"
    out.mkdir()

    paths = _download._extract_tabular_zip(str(archive), str(out))

    assert {Path(path).suffix.lstrip(".") for path in paths} == set(
        SUPPORTED_INPUT_EXTS
    )


def _write_bounded_archive(path, archive_kind, members):
    if archive_kind == "zip":
        with zipfile.ZipFile(path, "w") as archive:
            for name, body in members:
                archive.writestr(name, body)
        return
    with tarfile.open(path, "w:gz") as archive:
        for name, body in members:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))


def _extract_bounded_archive(path, archive_kind, out_dir):
    extract = (
        _download._extract_tabular_zip_managed
        if archive_kind == "zip"
        else _download._extract_tabular_tar_managed
    )
    return extract(
        str(path),
        str(out_dir),
        100,
        reusable_names=(),
        archive_name=path.name,
    )


def _unicode_path_extra(raw_name, unicode_name, *, crc=None):
    raw_name = raw_name.encode("cp437")
    encoded_name = unicode_name.encode("utf-8")
    payload = struct.pack(
        "<BL",
        1,
        zlib.crc32(raw_name) if crc is None else crc,
    ) + encoded_name
    return struct.pack("<HH", 0x7075, len(payload)) + payload


def _write_unicode_path_archive(
    path, raw_name, unicode_name, *, crc=None, body=b"a\n1\n"
):
    info = zipfile.ZipInfo(raw_name)
    info.extra = _unicode_path_extra(
        raw_name, unicode_name, crc=crc
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, body)


def _write_zip64_central_archive(
    path, name, body, *, zip64_payload=None
):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, body)
    original = path.read_bytes()
    central_offset = original.index(zipfile.stringCentralDir)
    end_offset = original.index(
        zipfile.stringEndArchive, central_offset
    )
    central = list(struct.unpack(
        zipfile.structCentralDir,
        original[
            central_offset:central_offset + zipfile.sizeCentralDir
        ],
    ))
    filename_length = central[zipfile._CD_FILENAME_LENGTH]
    extra_length = central[zipfile._CD_EXTRA_FIELD_LENGTH]
    comment_length = central[zipfile._CD_COMMENT_LENGTH]
    variable_start = central_offset + zipfile.sizeCentralDir
    raw_name = original[
        variable_start:variable_start + filename_length
    ]
    comment = original[
        variable_start + filename_length + extra_length:
        variable_start + filename_length + extra_length + comment_length
    ]
    if zip64_payload is None:
        zip64_payload = struct.pack(
            "<QQQ", len(body), len(body), 0
        )
    zip64_extra = (
        struct.pack("<HH", 0x0001, len(zip64_payload))
        + zip64_payload
    )
    central[zipfile._CD_UNCOMPRESSED_SIZE] = 0xFFFFFFFF
    central[zipfile._CD_COMPRESSED_SIZE] = 0xFFFFFFFF
    central[zipfile._CD_LOCAL_HEADER_OFFSET] = 0xFFFFFFFF
    central[zipfile._CD_EXTRA_FIELD_LENGTH] = len(zip64_extra)
    rewritten_central = (
        struct.pack(zipfile.structCentralDir, *central)
        + raw_name
        + zip64_extra
        + comment
    )
    end = list(struct.unpack(
        zipfile.structEndArchive,
        original[end_offset:end_offset + zipfile.sizeEndCentDir],
    ))
    end[zipfile._ECD_SIZE] += len(zip64_extra) - extra_length
    rewritten_end = struct.pack(zipfile.structEndArchive, *end)
    path.write_bytes(
        original[:central_offset]
        + rewritten_central
        + rewritten_end
        + original[end_offset + zipfile.sizeEndCentDir:]
    )


def _write_extended_tar(path, extension_kind, long_value):
    tar_format = (
        tarfile.PAX_FORMAT
        if extension_kind == "pax"
        else tarfile.GNU_FORMAT
    )
    with tarfile.open(path, "w:gz", format=tar_format) as archive:
        if extension_kind == "gnu_longlink":
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = long_value
            archive.addfile(info)
            return
        info = tarfile.TarInfo(long_value)
        body = b"a\n1\n"
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))


def _first_tar_header(path):
    with gzip.open(path, "rb") as stream:
        return tarfile.TarInfo.frombuf(
            stream.read(tarfile.BLOCKSIZE),
            "utf-8",
            "surrogateescape",
        )


def _pax_record(key, value):
    body = key + b"=" + value + b"\n"
    length = len(body) + 2
    while True:
        encoded = str(length).encode("ascii") + b" " + body
        if len(encoded) == length:
            return encoded
        length = len(encoded)


def _raw_tar_member(name, type_, payload):
    info = tarfile.TarInfo(name)
    info.type = type_
    info.size = len(payload)
    header = info.tobuf(
        format=tarfile.GNU_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    )
    padding = (-len(payload)) % tarfile.BLOCKSIZE
    return header + payload + (b"\0" * padding)


def _write_raw_tar(path, members):
    payload = b"".join(
        _raw_tar_member(name, type_, body)
        for name, type_, body in members
    )
    payload += b"\0" * (tarfile.BLOCKSIZE * 2)
    with gzip.open(path, "wb") as stream:
        stream.write(payload)


def _bounded_tar_names(
    path,
    tmp_path,
    monkeypatch,
    name_limit,
    *,
    encoding=None,
):
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", name_limit
    )
    open_kwargs = {}
    if encoding is not None:
        open_kwargs["encoding"] = encoding
    try:
        archive = _download._BoundedTarFile.open(
            path,
            "r:gz",
            tarinfo=_download._BoundedTarInfo,
            **open_kwargs,
        )
    except _download._TarArchiveLimit as error:
        return [], set(), [error.record(path.name)]
    with archive:
        members, skipped = _download._collect_bounded_tar_members(
            archive, path.name
        )
    return [member.name for member in members], set(), skipped


def _tar_info_snapshot(path, monkeypatch, name_limit):
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", name_limit
    )
    with tarfile.open(path, "r:gz") as archive:
        expected = archive.next()
    with _download._BoundedTarFile.open(
        path,
        "r:gz",
        tarinfo=_download._BoundedTarInfo,
    ) as archive:
        actual = archive.next()
    return expected, actual


@pytest.mark.parametrize(
    (
        "global_records",
        "local_records",
        "sparse_member",
    ),
    [
        (
            [],
            [
                (b"path", b"path-value.csv"),
                (b"GNU.sparse.name", b"sparse-val.csv"),
            ],
            False,
        ),
        (
            [],
            [
                (b"GNU.sparse.name", b"sparse-val.csv"),
                (b"path", b"path-value.csv"),
            ],
            True,
        ),
        (
            [
                (b"path", b"path-value.csv"),
                (b"GNU.sparse.name", b"sparse-val.csv"),
            ],
            [(b"path", b"final-path.csv")],
            False,
        ),
        (
            [
                (b"GNU.sparse.name", b"sparse-val.csv"),
                (b"path", b"path-value.csv"),
            ],
            [(b"GNU.sparse.name", b"final-sprs.csv")],
            True,
        ),
        (
            [],
            [
                (b"path", b"first-path.csv"),
                (b"GNU.sparse.name", b"sparse-val.csv"),
                (b"path", b"final-path.csv"),
            ],
            False,
        ),
        (
            [],
            [
                (b"GNU.sparse.name", b"first-sprs.csv"),
                (b"path", b"path-value.csv"),
                (b"GNU.sparse.name", b"final-sprs.csv"),
            ],
            True,
        ),
    ],
    ids=[
        "local-path-then-sparse",
        "local-sparse-then-path",
        "global-order-local-path-replacement",
        "global-order-local-sparse-replacement",
        "repeated-path-keeps-first-position",
        "repeated-sparse-keeps-first-position",
    ],
)
def test_tar_pax_name_application_matches_stdlib_mapping_order(
    tmp_path,
    monkeypatch,
    global_records,
    local_records,
    sparse_member,
):
    archive = tmp_path / "pax-name-order.tar.gz"
    members = []
    if global_records:
        members.append((
            "global",
            tarfile.XGLTYPE,
            b"".join(
                _pax_record(key, value)
                for key, value in global_records
            ),
        ))
    if sparse_member:
        local_records = [
            *local_records,
            (b"GNU.sparse.map", b"0,0"),
        ]
    members.extend([
        (
            "extended",
            tarfile.XHDTYPE,
            b"".join(
                _pax_record(key, value)
                for key, value in local_records
            ),
        ),
        ("placeholder", tarfile.REGTYPE, b""),
    ])
    _write_raw_tar(archive, members)

    with tarfile.open(archive, "r:gz") as stdlib_archive:
        expected_name = stdlib_archive.next().name
    exact = len(expected_name.encode("utf-8"))
    expected, actual = _tar_info_snapshot(
        archive, monkeypatch, exact
    )

    assert actual.name == expected.name
    assert actual.pax_headers == expected.pax_headers
    assert actual.sparse == expected.sparse

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_pax_uses_last_effective_utf8_path_at_exact_boundary(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-repeated.tar.gz"
    final_name = "nested/\N{LATIN SMALL LETTER E WITH ACUTE}.csv"
    pax = (
        _pax_record(b"path", b"x" * 1_000 + b".csv")
        + _pax_record(b"path", final_name.encode("utf-8"))
    )
    _write_raw_tar(
        archive,
        [
            ("pax", tarfile.XHDTYPE, pax),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    exact = len(final_name.encode("utf-8"))

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"
    assert skipped[0]["retained_name_bytes"] == 0


def test_tar_pax_binary_counts_surrogateescape_expansion_before_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-binary.tar.gz"
    raw_name = b"nested/\xff.csv"
    pax = (
        _pax_record(b"hdrcharset", b"BINARY")
        + _pax_record(b"path", raw_name)
    )
    _write_raw_tar(
        archive,
        [
            ("pax", tarfile.XHDTYPE, pax),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    decoded = raw_name.decode("utf-8", "surrogateescape")
    exact = len(decoded.encode("utf-8", "surrogatepass"))

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert len(names) == 1
    assert names[0].endswith(".csv")
    assert preserved == set()
    assert skipped == []

    original_decode = _download._RawTarText.decode

    def reject_full_path_decode(value):
        if value.raw == raw_name:
            raise AssertionError(
                "over-budget PAX path must not be fully decoded"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_full_path_decode,
    )
    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_pax_uses_first_hdrcharset_for_name_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-first-hdrcharset.tar.gz"
    raw_name = b"nested/\xc3\xa9.csv"
    pax = (
        _pax_record(b"hdrcharset", b"BINARY")
        + _pax_record(
            b"hdrcharset",
            b"ISO-IR 10646 2000 UTF-8",
        )
        + _pax_record(b"path", raw_name)
    )
    _write_raw_tar(
        archive,
        [
            ("pax", tarfile.XHDTYPE, pax),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    expected = raw_name.decode("latin-1")

    names, preserved, skipped = _bounded_tar_names(
        archive,
        tmp_path,
        monkeypatch,
        len(expected.encode("utf-8")),
        encoding="latin-1",
    )

    assert names == [expected]
    assert preserved == set()
    assert skipped == []


def test_tar_pax_rejects_winning_local_path_before_local_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-local-over-budget.tar.gz"
    raw_name = b"nested/" + (b"x" * 1_000) + b".csv"
    _write_raw_tar(
        archive,
        [
            (
                "pax",
                tarfile.XHDTYPE,
                _pax_record(b"path", raw_name),
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    original_decode = _download._RawTarText.decode

    def reject_winning_path_decode(value):
        if value.raw == raw_name:
            raise AssertionError(
                "winning local PAX path must be bounded before decode"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_winning_path_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, 32
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_pax_rejects_winning_global_path_before_local_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-global-over-budget.tar.gz"
    raw_name = b"nested/" + (b"x" * 1_000) + b".csv"
    _write_raw_tar(
        archive,
        [
            (
                "global",
                tarfile.XGLTYPE,
                _pax_record(b"path", raw_name),
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    original_decode = _download._RawTarText.decode

    def reject_winning_path_decode(value):
        if value.raw == raw_name:
            raise AssertionError(
                "winning global PAX path must be bounded before decode"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_winning_path_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, 32
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_gnu_longname_uses_first_nul_at_exact_boundary(
    tmp_path, monkeypatch
):
    archive = tmp_path / "gnu-first-nul.tar.gz"
    final_name = "nested/table.csv"
    long_payload = (
        final_name.encode("utf-8")
        + b"\0ignored"
        + (b"x" * 1_000)
        + b"\0"
    )
    _write_raw_tar(
        archive,
        [
            ("long", tarfile.GNUTYPE_LONGNAME, long_payload),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    exact = len(final_name.encode("utf-8"))

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_gnu_longname_without_nul_checks_before_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "gnu-no-nul.tar.gz"
    final_name = "nested/" + ("a" * 501) + ".csv"
    assert len(final_name.encode("utf-8")) == tarfile.BLOCKSIZE
    _write_raw_tar(
        archive,
        [
            (
                "long",
                tarfile.GNUTYPE_LONGNAME,
                final_name.encode("utf-8"),
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    exact = len(final_name.encode("utf-8"))

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    original_nts = tarfile.nts

    def reject_full_name_decode(value, *args):
        if value.startswith(final_name.encode("utf-8")):
            raise AssertionError(
                "over-budget GNU name must not be fully decoded"
            )
        return original_nts(value, *args)

    monkeypatch.setattr(tarfile, "nts", reject_full_name_decode)
    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_global_pax_path_can_be_overridden_before_retention(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-global-override.tar.gz"
    final_name = "nested/final.csv"
    _write_raw_tar(
        archive,
        [
            (
                "global",
                tarfile.XGLTYPE,
                _pax_record(b"path", b"x" * 1_000 + b".csv"),
            ),
            (
                "extended",
                tarfile.XHDTYPE,
                _pax_record(b"path", final_name.encode("utf-8")),
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    exact = len(final_name.encode("utf-8"))
    original_decode = _download._RawTarText.decode

    def reject_superseded_global_decode(value):
        if value.raw.startswith(b"x" * 1_000):
            raise AssertionError(
                "superseded global PAX path must not be decoded"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_superseded_global_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def test_tar_local_pax_path_supersedes_pending_gnu_name_without_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-over-gnu.tar.gz"
    final_name = "nested/final.csv"
    raw_gnu_name = b"x" * 1_000 + b".csv\0"
    _write_raw_tar(
        archive,
        [
            (
                "pax",
                tarfile.XHDTYPE,
                _pax_record(b"path", final_name.encode("utf-8")),
            ),
            (
                "long",
                tarfile.GNUTYPE_LONGNAME,
                raw_gnu_name,
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    original_decode = _download._RawTarText.decode

    def reject_superseded_gnu_decode(value):
        if value.raw == raw_gnu_name[:-1]:
            raise AssertionError(
                "superseded GNU long name must not be decoded"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_superseded_gnu_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive,
        tmp_path,
        monkeypatch,
        len(final_name.encode("utf-8")),
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []


def test_tar_pax_path_supersedes_sparse_name_without_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-over-sparse-name.tar.gz"
    final_name = "nested/final.csv"
    raw_sparse_name = b"x" * 1_000 + b".csv"
    pax = (
        _pax_record(b"GNU.sparse.name", raw_sparse_name)
        + _pax_record(b"path", final_name.encode("utf-8"))
    )
    _write_raw_tar(
        archive,
        [
            ("pax", tarfile.XHDTYPE, pax),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    original_decode = _download._RawTarText.decode

    def reject_superseded_sparse_decode(value):
        if value.raw == raw_sparse_name:
            raise AssertionError(
                "superseded GNU sparse name must not be decoded"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_superseded_sparse_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive,
        tmp_path,
        monkeypatch,
        len(final_name.encode("utf-8")),
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    expected, actual = _tar_info_snapshot(
        archive,
        monkeypatch,
        len(final_name.encode("utf-8")),
    )

    assert tuple(actual.pax_headers) == tuple(expected.pax_headers)
    assert "GNU.sparse.name" in actual.pax_headers


@pytest.mark.parametrize(
    (
        "global_records",
        "local_records",
        "sparse_member",
        "losing_field",
        "winning_field",
    ),
    [
        (
            [],
            [
                (b"path", b"x" * 1_000 + b".csv"),
                (b"GNU.sparse.name", b"final.csv"),
            ],
            False,
            "path",
            "GNU.sparse.name",
        ),
        (
            [],
            [
                (b"GNU.sparse.name", b"x" * 1_000 + b".csv"),
                (b"path", b"final.csv"),
            ],
            True,
            "GNU.sparse.name",
            "path",
        ),
        (
            [
                (b"path", b"x" * 1_000 + b".csv"),
                (b"GNU.sparse.name", b"initial.csv"),
            ],
            [(b"GNU.sparse.name", b"final.csv")],
            True,
            "path",
            "GNU.sparse.name",
        ),
        (
            [
                (b"GNU.sparse.name", b"x" * 1_000 + b".csv"),
                (b"path", b"initial.csv"),
            ],
            [(b"path", b"final.csv")],
            False,
            "GNU.sparse.name",
            "path",
        ),
    ],
    ids=[
        "local-path-before-sparse",
        "local-sparse-before-path",
        "inherited-path-local-sparse-update",
        "inherited-sparse-local-path-update",
    ],
)
def test_tar_raw_losing_pax_name_is_retained_but_never_applied(
    tmp_path,
    monkeypatch,
    global_records,
    local_records,
    sparse_member,
    losing_field,
    winning_field,
):
    archive = tmp_path / "pax-raw-loser.tar.gz"
    members = []
    if global_records:
        members.append((
            "global",
            tarfile.XGLTYPE,
            b"".join(
                _pax_record(key, value)
                for key, value in global_records
            ),
        ))
    if sparse_member:
        local_records = [
            *local_records,
            (b"GNU.sparse.map", b"0,0"),
        ]
    members.extend([
        (
            "extended",
            tarfile.XHDTYPE,
            b"".join(
                _pax_record(key, value)
                for key, value in local_records
            ),
        ),
        ("placeholder", tarfile.REGTYPE, b""),
    ])
    _write_raw_tar(archive, members)

    base_path = tarfile.TarInfo.path

    def reject_raw_assignment(info, value):
        if isinstance(value, _download._RawTarText):
            raise AssertionError(
                "raw losing PAX name must not be applied to TarInfo"
            )
        return base_path.fset(info, value)

    def reject_raw_string_method(_value, *_args):
        raise AssertionError(
            "raw losing PAX name must not use string methods"
        )

    monkeypatch.setattr(
        _download._BoundedTarInfo,
        "path",
        property(
            base_path.fget,
            reject_raw_assignment,
            base_path.fdel,
            base_path.__doc__,
        ),
    )
    monkeypatch.setattr(
        _download._RawTarText,
        "rstrip",
        reject_raw_string_method,
        raising=False,
    )

    expected, actual = _tar_info_snapshot(
        archive,
        monkeypatch,
        len("final.csv"),
    )

    assert actual.name == expected.name == "final.csv"
    assert tuple(actual.pax_headers) == tuple(expected.pax_headers)
    assert isinstance(
        actual.pax_headers[losing_field],
        _download._RawTarText,
    )
    assert actual.pax_headers[winning_field] == "final.csv"
    assert actual.sparse == expected.sparse


@pytest.mark.parametrize(
    "records",
    [
        [
            (b"path", b"losing-path.csv"),
            (b"GNU.sparse.name", b"final.csv"),
        ],
        [
            (b"GNU.sparse.name", b"losing-sparse.csv"),
            (b"path", b"final.csv"),
        ],
    ],
    ids=["path-before-sparse", "sparse-before-path"],
)
def test_tar_only_winning_decoded_pax_name_mutates_tarinfo(
    tmp_path, monkeypatch, records
):
    archive = tmp_path / "pax-single-name-application.tar.gz"
    _write_raw_tar(
        archive,
        [
            (
                "extended",
                tarfile.XHDTYPE,
                b"".join(
                    _pax_record(key, value)
                    for key, value in records
                ),
            ),
            ("placeholder", tarfile.REGTYPE, b""),
        ],
    )
    base_path = tarfile.TarInfo.path
    applied_names = []

    def record_assignment(info, value):
        applied_names.append(value)
        return base_path.fset(info, value)

    monkeypatch.setattr(
        _download._BoundedTarInfo,
        "path",
        property(
            base_path.fget,
            record_assignment,
            base_path.fdel,
            base_path.__doc__,
        ),
    )

    expected, actual = _tar_info_snapshot(
        archive,
        monkeypatch,
        100,
    )

    assert actual.name == expected.name == "final.csv"
    assert actual.pax_headers == expected.pax_headers
    assert applied_names == ["final.csv"]


def test_tar_raw_global_pax_loser_is_not_applied_before_gnu_name(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-before-gnu.tar.gz"
    raw_loser = b"x" * 1_000 + b".csv"
    final_name = "final.csv"
    _write_raw_tar(
        archive,
        [
            (
                "global",
                tarfile.XGLTYPE,
                _pax_record(b"path", raw_loser),
            ),
            (
                "long",
                tarfile.GNUTYPE_LONGNAME,
                final_name.encode("utf-8") + b"\0",
            ),
            ("placeholder", tarfile.REGTYPE, b""),
        ],
    )
    base_path = tarfile.TarInfo.path

    def reject_raw_assignment(info, value):
        if isinstance(value, _download._RawTarText):
            raise AssertionError(
                "raw losing PAX name must not be applied to TarInfo"
            )
        return base_path.fset(info, value)

    def reject_raw_string_method(_value, *_args):
        raise AssertionError(
            "raw losing PAX name must not use string methods"
        )

    monkeypatch.setattr(
        _download._BoundedTarInfo,
        "path",
        property(
            base_path.fget,
            reject_raw_assignment,
            base_path.fdel,
            base_path.__doc__,
        ),
    )
    monkeypatch.setattr(
        _download._RawTarText,
        "rstrip",
        reject_raw_string_method,
        raising=False,
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_MEMBER_NAME_BYTES",
        len(final_name),
    )

    with _download._BoundedTarFile.open(
        archive,
        "r:gz",
        tarinfo=_download._BoundedTarInfo,
    ) as bounded:
        actual = bounded.next()

    assert actual.name == final_name
    assert isinstance(
        actual.pax_headers["path"],
        _download._RawTarText,
    )


def test_tar_pending_gnu_loser_is_not_applied_before_pax_name(
    tmp_path, monkeypatch
):
    archive = tmp_path / "gnu-before-pax-application.tar.gz"
    raw_gnu_name = b"x" * 1_000 + b".csv"
    final_name = "final.csv"
    _write_raw_tar(
        archive,
        [
            (
                "extended",
                tarfile.XHDTYPE,
                _pax_record(
                    b"path",
                    final_name.encode("utf-8"),
                ),
            ),
            (
                "long",
                tarfile.GNUTYPE_LONGNAME,
                raw_gnu_name + b"\0",
            ),
            ("placeholder", tarfile.REGTYPE, b""),
        ],
    )
    original_decode = _download._RawTarText.decode

    def reject_losing_gnu_decode(value):
        if value.raw == raw_gnu_name:
            raise AssertionError(
                "pending GNU loser must not be decoded"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_losing_gnu_decode,
    )

    names, preserved, skipped = _bounded_tar_names(
        archive,
        tmp_path,
        monkeypatch,
        len(final_name),
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []


def test_tar_sparse_name_uses_exact_boundary_before_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-sparse-name.tar.gz"
    final_name = "nested/sparse.csv"
    raw_name = final_name.encode("utf-8")
    _write_raw_tar(
        archive,
        [
            (
                "pax",
                tarfile.XHDTYPE,
                _pax_record(b"GNU.sparse.name", raw_name),
            ),
            ("placeholder", tarfile.REGTYPE, b"a\n1\n"),
        ],
    )
    exact = len(raw_name)

    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact
    )

    assert names == [final_name]
    assert preserved == set()
    assert skipped == []

    original_decode = _download._RawTarText.decode

    def reject_over_budget_sparse_decode(value):
        if value.raw == raw_name:
            raise AssertionError(
                "winning GNU sparse name must be bounded before decode"
            )
        return original_decode(value)

    monkeypatch.setattr(
        _download._RawTarText,
        "decode",
        reject_over_budget_sparse_decode,
    )
    names, preserved, skipped = _bounded_tar_names(
        archive, tmp_path, monkeypatch, exact - 1
    )

    assert names == []
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member name byte limit"


def _tar_header_with_checksum(header):
    header = bytearray(header)
    header[148:156] = b" " * 8
    header[148:156] = tarfile.itn(
        sum(header), 8, tarfile.GNU_FORMAT
    )
    return bytes(header)


def _legacy_sparse_header(name, initial_entries, *, extended):
    info = tarfile.TarInfo(name)
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    header = bytearray(info.tobuf(format=tarfile.GNU_FORMAT))
    for index, (offset, size) in enumerate(initial_entries):
        position = 386 + index * 24
        header[position:position + 12] = tarfile.itn(
            offset, 12, tarfile.GNU_FORMAT
        )
        header[position + 12:position + 24] = tarfile.itn(
            size, 12, tarfile.GNU_FORMAT
        )
    header[482] = int(extended)
    header[483:495] = tarfile.itn(
        10_000, 12, tarfile.GNU_FORMAT
    )
    return _tar_header_with_checksum(header)


def _legacy_sparse_extension(entries, *, extended):
    block = bytearray(tarfile.BLOCKSIZE)
    for index, (offset, size) in enumerate(entries):
        position = index * 24
        block[position:position + 12] = tarfile.itn(
            offset, 12, tarfile.GNU_FORMAT
        )
        block[position + 12:position + 24] = tarfile.itn(
            size, 12, tarfile.GNU_FORMAT
        )
    block[504] = int(extended)
    return bytes(block)


def _write_raw_tar_bytes(path, payload):
    payload += b"\0" * (tarfile.BLOCKSIZE * 2)
    with gzip.open(path, "wb") as stream:
        stream.write(payload)


def _open_sparse_member(path):
    try:
        archive = _download._BoundedTarFile.open(
            path,
            "r:gz",
            tarinfo=_download._BoundedTarInfo,
        )
    except _download._TarArchiveLimit as error:
        return None, error.record(path.name)
    with archive:
        return archive.next(), None


def _write_pax_sparse_archive(path, version, entries):
    if version == "0.0":
        records = [_pax_record(b"GNU.sparse.size", b"10000")]
        for offset, size in entries:
            records.append(
                _pax_record(
                    b"GNU.sparse.offset",
                    str(offset).encode("ascii"),
                )
            )
            records.append(
                _pax_record(
                    b"GNU.sparse.numbytes",
                    str(size).encode("ascii"),
                )
            )
        body = b""
    elif version == "0.1":
        sparse_map = ",".join(
            str(value)
            for entry in entries
            for value in entry
        ).encode("ascii")
        records = [
            _pax_record(b"GNU.sparse.map", sparse_map),
        ]
        body = b""
    else:
        records = [
            _pax_record(b"GNU.sparse.major", b"1"),
            _pax_record(b"GNU.sparse.minor", b"0"),
            _pax_record(b"GNU.sparse.realsize", b"10000"),
        ]
        fields = [str(len(entries)).encode("ascii")]
        for offset, size in entries:
            fields.extend([
                str(offset).encode("ascii"),
                str(size).encode("ascii"),
            ])
        raw_fields = b"\n".join(fields) + b"\n"
        body = raw_fields + (
            b"\0" * ((-len(raw_fields)) % tarfile.BLOCKSIZE)
        )
    _write_raw_tar(
        path,
        [
            ("pax", tarfile.XHDTYPE, b"".join(records)),
            ("table.csv", tarfile.REGTYPE, body),
        ],
    )


@pytest.mark.parametrize(
    ("version", "processor_name"),
    [
        ("0.0", "_proc_gnusparse_00"),
        ("0.1", "_proc_gnusparse_01"),
        ("1.0", "_proc_gnusparse_10"),
    ],
)
def test_tar_pax_sparse_entry_limit_precedes_stdlib_list_growth(
    tmp_path, monkeypatch, version, processor_name
):
    archive = tmp_path / f"sparse-{version}.tar.gz"
    entries = [(index * 2, 1) for index in range(20)]
    _write_pax_sparse_archive(archive, version, entries)
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 3, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)
    monkeypatch.setattr(
        tarfile.TarInfo,
        processor_name,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "sparse limit must precede stdlib list growth"
            )
        ),
    )

    member, limitation = _open_sparse_member(archive)

    assert member is None
    assert limitation["reason"] == "archive sparse entry limit"
    assert limitation["limit"] == 3
    assert limitation["sparse_entries_retained"] <= 3
    assert limitation["omitted_sparse_entries"] == len(entries)


def test_tar_legacy_sparse_limit_stops_after_first_extension_block(
    tmp_path, monkeypatch
):
    archive = tmp_path / "legacy-sparse.tar.gz"
    initial = [(index * 2, 1) for index in range(4)]
    first_extension = [(10 + index * 2, 1) for index in range(21)]
    later_extension = [(100 + index * 2, 1) for index in range(21)]
    payload = (
        _legacy_sparse_header(
            "table.csv", initial, extended=True
        )
        + _legacy_sparse_extension(
            first_extension, extended=True
        )
        + _legacy_sparse_extension(
            later_extension, extended=False
        )
    )
    _write_raw_tar_bytes(archive, payload)
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 4, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)
    monkeypatch.setattr(
        tarfile.TarInfo,
        "_proc_sparse",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "legacy sparse limit must precede stdlib list growth"
            )
        ),
    )

    member, limitation = _open_sparse_member(archive)

    assert member is None
    assert limitation["reason"] == "archive sparse entry limit"
    assert limitation["sparse_entries_retained"] == 4
    assert limitation["sparse_extension_blocks_processed"] == 1
    assert limitation["metadata_bytes_processed"] == tarfile.BLOCKSIZE
    assert limitation["omitted_sparse_entries_lower_bound"] >= 1


def test_tar_legacy_sparse_initial_omission_is_lower_bound_with_extensions(
    tmp_path, monkeypatch
):
    archive = tmp_path / "legacy-sparse-initial-limit.tar.gz"
    initial = [(index * 2, 1) for index in range(4)]
    later = [(100, 1)]
    payload = (
        _legacy_sparse_header(
            "table.csv", initial, extended=True
        )
        + _legacy_sparse_extension(later, extended=False)
    )
    _write_raw_tar_bytes(archive, payload)
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 2, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)

    member, limitation = _open_sparse_member(archive)

    assert member is None
    assert limitation["reason"] == "archive sparse entry limit"
    assert limitation["sparse_entries_retained"] == 2
    assert "omitted_sparse_entries" not in limitation
    assert limitation["omitted_sparse_entries_lower_bound"] == 2


def test_tar_legacy_sparse_initial_omission_is_exact_without_extensions(
    tmp_path, monkeypatch
):
    archive = tmp_path / "legacy-sparse-initial-exact.tar.gz"
    initial = [(index * 2, 1) for index in range(4)]
    _write_raw_tar_bytes(
        archive,
        _legacy_sparse_header(
            "table.csv", initial, extended=False
        ),
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 2, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)

    member, limitation = _open_sparse_member(archive)

    assert member is None
    assert limitation["reason"] == "archive sparse entry limit"
    assert limitation["omitted_sparse_entries"] == 2
    assert "omitted_sparse_entries_lower_bound" not in limitation


def test_tar_sparse_10_long_unterminated_field_has_linear_work():
    class WorkMeter:
        scanned_or_copied = 0

    meter = WorkMeter()

    class MeasuredBytes(bytes):
        def __new__(cls, value):
            return super().__new__(cls, value)

        def __contains__(self, value):
            meter.scanned_or_copied += len(self)
            return super().__contains__(value)

        def __add__(self, value):
            meter.scanned_or_copied += len(self) + len(value)
            return MeasuredBytes(super().__add__(value))

        def split(self, separator=None, maxsplit=-1):
            meter.scanned_or_copied += len(self)
            return [
                MeasuredBytes(part)
                for part in super().split(separator, maxsplit)
            ]

    class MeasuredStream(io.BytesIO):
        def read(self, size=-1):
            return MeasuredBytes(super().read(size))

    blocks = 64
    admitted = blocks * tarfile.BLOCKSIZE
    payload = b"1\n" + (b"9" * (admitted - 2))
    state = {
        "metadata_byte_limit": admitted,
        "metadata_bytes_processed": 0,
        "sparse_extension_blocks_processed": 0,
        "sparse_fields_processed": 0,
        "sparse_entry_limit": 1,
        "sparse_entries_retained": 0,
    }

    class Archive:
        fileobj = MeasuredStream(payload)
        _paperconan_budget_state = state

    with pytest.raises(
        _download._TarArchiveLimit,
        match="archive metadata byte limit",
    ):
        _download._parse_pax_sparse_10(
            tarfile.TarInfo("table.csv"),
            Archive(),
        )

    assert meter.scanned_or_copied <= admitted * 4
    line_work = state.get("sparse_line_bytes_processed", 0)
    assert admitted <= line_work <= admitted * 4


@pytest.mark.parametrize("version", ["0.0", "0.1", "1.0"])
def test_tar_pax_sparse_entries_within_budget_are_preserved(
    tmp_path, monkeypatch, version
):
    archive = tmp_path / f"sparse-ok-{version}.tar.gz"
    entries = [(0, 1), (4, 2)]
    _write_pax_sparse_archive(archive, version, entries)
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", len(entries),
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)

    member, limitation = _open_sparse_member(archive)

    assert limitation is None
    assert member.sparse == entries


def test_tar_pax_sparse_00_ignores_unpaired_offset_state(
    tmp_path, monkeypatch
):
    archive = tmp_path / "sparse-00-unpaired.tar.gz"
    records = [
        _pax_record(b"GNU.sparse.size", b"10000"),
        _pax_record(b"GNU.sparse.offset", b"0"),
        _pax_record(b"GNU.sparse.numbytes", b"1"),
        _pax_record(
            b"GNU.sparse.offset",
            b"unpaired-value-must-not-be-parsed",
        ),
    ]
    _write_raw_tar(
        archive,
        [
            ("pax", tarfile.XHDTYPE, b"".join(records)),
            ("table.csv", tarfile.REGTYPE, b""),
        ],
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 1, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)

    member, limitation = _open_sparse_member(archive)

    assert limitation is None
    assert member.sparse == [(0, 1)]


def test_tar_pax_sparse_01_preserves_empty_map(
    tmp_path, monkeypatch
):
    archive = tmp_path / "sparse-01-empty.tar.gz"
    _write_raw_tar(
        archive,
        [
            (
                "pax",
                tarfile.XHDTYPE,
                _pax_record(b"GNU.sparse.map", b""),
            ),
            ("table.csv", tarfile.REGTYPE, b""),
        ],
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_SPARSE_ENTRY_LIMIT", 0, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 100_000, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 20)

    member, limitation = _open_sparse_member(archive)

    assert limitation is None
    assert member.sparse == []


def _guard_gzip_seek_beyond(monkeypatch, max_offset):
    original_seek = gzip.GzipFile.seek
    blocked = []

    def guarded_seek(stream, offset, whence=0):
        if whence == 0 and offset > max_offset:
            blocked.append(offset)
            raise AssertionError(
                "TAR traversal must be budgeted before gzip seek"
            )
        return original_seek(stream, offset, whence)

    monkeypatch.setattr(gzip.GzipFile, "seek", guarded_seek)
    return blocked


def test_tar_traversal_records_actual_short_forward_seek():
    class ShortSeek:
        def __init__(self):
            self.position = 0

        def tell(self):
            return self.position

        def seek(self, offset, whence=0):
            assert whence == 0
            self.position = min(offset, 10)
            return self.position

    state = {
        "traversal_byte_limit": 100,
        "decompressed_bytes_traversed": 0,
    }
    stream = _download._TarTraversalFile(ShortSeek(), state)

    assert stream.seek(50) == 10
    assert state["decompressed_bytes_traversed"] == 10


def test_tar_member_cap_does_not_inflate_large_member_for_lookahead(
    tmp_path, monkeypatch
):
    archive = tmp_path / "large-lookahead.tar.gz"
    _write_bounded_archive(
        archive,
        "tar",
        [
            ("image.bin", b"x" * 1_000_000),
            ("second.csv", b"y"),
        ],
    )
    traversal_limit = 4_096
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 1)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_TAR_TRAVERSAL_BYTES",
        traversal_limit,
        raising=False,
    )
    blocked = _guard_gzip_seek_beyond(
        monkeypatch, traversal_limit
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert blocked == []
    assert extracted == []
    assert preserved == set()
    assert skipped[0]["reason"] == (
        "archive decompressed traversal limit"
    )
    assert skipped[0]["limit"] == traversal_limit
    assert skipped[0]["decompressed_bytes_traversed"] <= traversal_limit
    assert skipped[0]["requested_traversal_bytes"] > traversal_limit
    assert skipped[0]["omitted_members_lower_bound"] == 0


def test_tar_normal_advancement_checks_large_skip_before_gzip_seek(
    tmp_path, monkeypatch
):
    archive = tmp_path / "large-skip.tar.gz"
    _write_bounded_archive(
        archive,
        "tar",
        [
            ("image.bin", b"x" * 1_000_000),
            ("table.csv", b"a\n1\n"),
        ],
    )
    traversal_limit = 4_096
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_TAR_TRAVERSAL_BYTES",
        traversal_limit,
        raising=False,
    )
    blocked = _guard_gzip_seek_beyond(
        monkeypatch, traversal_limit
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert blocked == []
    assert extracted == []
    assert preserved == set()
    assert skipped[0]["reason"] == (
        "archive decompressed traversal limit"
    )
    assert skipped[0]["decompressed_bytes_traversed"] <= traversal_limit
    assert skipped[0]["requested_traversal_bytes"] > traversal_limit
    assert skipped[0]["omitted_members_lower_bound"] == 0


def test_tar_traversal_budget_counts_backward_extraction_replay(
    tmp_path, monkeypatch
):
    archive = tmp_path / "replayed-traversal.tar.gz"
    first_body = b"a\n1\n"
    large_body = b"x" * 1_000_000
    _write_bounded_archive(
        archive,
        "tar",
        [
            ("first.csv", first_body),
            ("image.bin", large_body),
        ],
    )
    large_padded = (
        (len(large_body) + tarfile.BLOCKSIZE - 1)
        // tarfile.BLOCKSIZE
        * tarfile.BLOCKSIZE
    )
    forward_scan_bytes = (
        tarfile.BLOCKSIZE
        + tarfile.BLOCKSIZE
        + tarfile.BLOCKSIZE
        + large_padded
        + tarfile.BLOCKSIZE
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_TAR_TRAVERSAL_BYTES",
        forward_scan_bytes,
        raising=False,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert extracted == []
    assert preserved == set()
    assert skipped[-1]["reason"] == (
        "archive decompressed traversal limit"
    )
    assert skipped[-1]["decompressed_bytes_traversed"] == (
        forward_scan_bytes
    )
    assert skipped[-1]["requested_traversal_bytes"] > 0
    assert skipped[-1]["omitted_members_lower_bound"] == 1


@pytest.mark.parametrize(
    ("members", "expected_reason", "omitted_lower_bound"),
    [
        ([("only.csv", b"")], None, None),
        (
            [("first.csv", b""), ("second.csv", b"")],
            "archive member count limit",
            1,
        ),
    ],
)
def test_tar_member_cap_uses_only_budgeted_end_marker_lookahead(
    tmp_path,
    monkeypatch,
    members,
    expected_reason,
    omitted_lower_bound,
):
    archive = tmp_path / "member-cap-end.tar.gz"
    _write_bounded_archive(archive, "tar", members)
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 1)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_TAR_TRAVERSAL_BYTES",
        2_048,
        raising=False,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    expected_names = (
        ["first.csv"] if len(members) > 1 else ["only.csv"]
    )
    assert [Path(path).name for path in extracted] == expected_names
    assert preserved == set()
    if expected_reason is None:
        assert skipped == []
    else:
        assert skipped[0]["reason"] == expected_reason
        assert skipped[0]["omitted_members_lower_bound"] == (
            omitted_lower_bound
        )


@pytest.mark.parametrize(
    ("extension_kind", "processor_name"),
    [
        ("pax", "_proc_pax"),
        ("gnu_longname", "_proc_gnulong"),
        ("gnu_longlink", "_proc_gnulong"),
    ],
)
def test_tar_extended_metadata_budget_rejects_before_stdlib_handler(
    tmp_path, monkeypatch, extension_kind, processor_name
):
    archive = tmp_path / f"{extension_kind}.tar.gz"
    long_value = "nested/" + "a" * 200_000 + ".csv"
    _write_extended_tar(archive, extension_kind, long_value)
    first_header = _first_tar_header(archive)
    assert first_header.size > 100_000
    assert archive.stat().st_size < 5_000
    processor_calls = []
    original_processor = getattr(
        tarfile.TarInfo, processor_name
    )

    def tracked_processor(info, opened_archive):
        processor_calls.append(info.size)
        return original_processor(info, opened_archive)

    monkeypatch.setattr(
        tarfile.TarInfo, processor_name, tracked_processor
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 1, raising=False
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1_000_000
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert processor_calls == []
    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive metadata byte limit",
        "limit": 1,
        "members_inspected": 1,
        "eligible_members_retained": 0,
        "retained_members": 0,
        "metadata_bytes_processed": 0,
        "requested_metadata_bytes": first_header.size,
        "omitted_members_lower_bound": 1,
    }]


def test_tar_extension_record_consumes_member_budget_before_payload(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-member-limit.tar.gz"
    _write_extended_tar(
        archive,
        "pax",
        "nested/" + "a" * 200_000 + ".csv",
    )
    processor_calls = []
    original_processor = tarfile.TarInfo._proc_pax

    def tracked_processor(info, opened_archive):
        processor_calls.append(info.size)
        return original_processor(info, opened_archive)

    monkeypatch.setattr(
        tarfile.TarInfo, "_proc_pax", tracked_processor
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 1)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_METADATA_BYTES",
        1_000_000,
        raising=False,
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert processor_calls == []
    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive member count limit",
        "limit": 1,
        "members_inspected": 1,
        "eligible_members_retained": 0,
        "retained_members": 0,
        "omitted_members_lower_bound": 1,
    }]


def test_tar_pax_final_name_budget_rejects_before_decode(
    tmp_path, monkeypatch
):
    archive = tmp_path / "pax-name-limit.tar.gz"
    _write_extended_tar(
        archive,
        "pax",
        "nested/" + "a" * 200_000 + ".csv",
    )
    first_header = _first_tar_header(archive)
    processor_calls = []
    original_processor = tarfile.TarInfo._proc_pax

    def tracked_processor(info, opened_archive):
        processor_calls.append(info.size)
        return original_processor(info, opened_archive)

    monkeypatch.setattr(
        tarfile.TarInfo, "_proc_pax", tracked_processor
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_METADATA_BYTES",
        1_000_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert processor_calls == []
    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive member name byte limit",
        "limit": 1,
        "members_inspected": 1,
        "eligible_members_retained": 0,
        "retained_members": 0,
        "retained_name_bytes": 0,
        "metadata_bytes_processed": first_header.size,
        "omitted_members_lower_bound": 1,
    }]


@pytest.mark.parametrize(
    "extension_kind",
    ["pax", "gnu_longname"],
)
def test_tar_extended_names_extract_normally_within_budgets(
    tmp_path, monkeypatch, extension_kind
):
    archive = tmp_path / f"{extension_kind}.tar.gz"
    final_name = "nested/" + "a" * 180 + ".csv"
    _write_extended_tar(archive, extension_kind, final_name)
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 10_000, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 10_000
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert [Path(path).read_bytes() for path in extracted] == [
        b"a\n1\n"
    ]
    assert preserved == set()
    assert skipped == []


def test_tar_gnu_longlink_continues_to_supported_member_within_budgets(
    tmp_path, monkeypatch
):
    archive = tmp_path / "gnu-longlink.tar.gz"
    long_link = "nested/" + "a" * 180
    with tarfile.open(
        archive, "w:gz", format=tarfile.GNU_FORMAT
    ) as output:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = long_link
        output.addfile(link)
        body = b"a\n1\n"
        table = tarfile.TarInfo("table.csv")
        table.size = len(body)
        output.addfile(table, io.BytesIO(body))
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download, "_ARCHIVE_METADATA_BYTES", 10_000, raising=False
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 10_000
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert [Path(path).read_bytes() for path in extracted] == [body]
    assert preserved == set()
    assert skipped == []


def test_zip_unicode_path_budget_charges_sanitized_final_name(
    tmp_path, monkeypatch
):
    archive = tmp_path / "unicode.zip"
    raw_name = "a.csv"
    final_name = "nested/" + "u" * 80 + ".csv"
    _write_unicode_path_archive(archive, raw_name, final_name)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", len(raw_name)
    )
    monkeypatch.setattr(
        zipfile.ZipInfo,
        "_decodeExtra",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ZIP extras must use local parsing")
        ),
    )

    with _download._BoundedZipFile(
        archive, archive_name=archive.name
    ) as bounded:
        assert bounded.filelist == []
        assert bounded.NameToInfo == {}
        assert bounded.selection_skipped == [{
            "name": archive.name,
            "reason": "archive member name byte limit",
            "limit": len(raw_name),
            "members_inspected": 1,
            "eligible_members_retained": 0,
            "retained_members": 0,
            "retained_name_bytes": 0,
            "omitted_members_lower_bound": 1,
        }]


def test_zip_unicode_path_crc_mismatch_keeps_raw_name(
    tmp_path, monkeypatch
):
    archive = tmp_path / "unicode-crc.zip"
    raw_name = "table.csv"
    _write_unicode_path_archive(
        archive,
        raw_name,
        "nested/" + "u" * 80 + ".csv",
        crc=0,
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", len(raw_name)
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "zip", out_dir
    )

    assert [Path(path).name for path in extracted] == [raw_name]
    assert preserved == set()
    assert skipped == []


def test_zip_malformed_unicode_extra_raises_bad_zip_file(tmp_path):
    archive = tmp_path / "malformed-unicode.zip"
    info = zipfile.ZipInfo("table.csv")
    info.extra = struct.pack("<HH", 0x7075, 4) + b"\x01\x00\x00\x00"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(info, b"a\n1\n")

    with pytest.raises(
        zipfile.BadZipFile,
        match="Corrupt unicode path extra field",
    ):
        _download._BoundedZipFile(
            archive, archive_name=archive.name
        )


def test_zip64_extra_is_parsed_locally_for_member_open(
    tmp_path, monkeypatch
):
    archive = tmp_path / "zip64.zip"
    body = b"a\n1\n"
    _write_zip64_central_archive(archive, "table.csv", body)
    monkeypatch.setattr(
        zipfile.ZipInfo,
        "_decodeExtra",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ZIP extras must use local parsing")
        ),
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "zip", out_dir
    )

    assert [Path(path).read_bytes() for path in extracted] == [body]
    assert preserved == set()
    assert skipped == []


def test_malformed_zip64_extra_raises_bad_zip_file(tmp_path):
    archive = tmp_path / "malformed-zip64.zip"
    _write_zip64_central_archive(
        archive,
        "table.csv",
        b"a\n1\n",
        zip64_payload=struct.pack("<Q", 4),
    )

    with pytest.raises(
        zipfile.BadZipFile,
        match="Corrupt zip64 extra field",
    ):
        _download._BoundedZipFile(
            archive, archive_name=archive.name
        )


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_member_limit_bounds_zero_byte_metadata_and_names(
    tmp_path, monkeypatch, archive_kind
):
    archive = tmp_path / f"supp.{archive_kind}"
    members = [
        ("a/table.csv", b""),
        ("b/table.csv", b""),
        ("c/table.csv", b""),
        ("d/table.csv", b""),
    ]
    _write_bounded_archive(archive, archive_kind, members)
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 2)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1_000
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_OUTPUT_FILE_LIMIT", 10
    )
    if archive_kind == "tar":
        monkeypatch.setattr(
            tarfile.TarFile,
            "getmembers",
            lambda _archive: (_ for _ in ()).throw(
                AssertionError("TAR extraction must stream metadata")
            ),
        )

    runs = []
    for index in range(2):
        out_dir = tmp_path / f"out-{index}"
        out_dir.mkdir()
        extracted, preserved, skipped = _extract_bounded_archive(
            archive, archive_kind, out_dir
        )
        runs.append([Path(path).name for path in extracted])
        assert preserved == set()
        assert skipped == [{
            "name": archive.name,
            "reason": "archive member count limit",
            "limit": 2,
            "members_inspected": 2,
            "eligible_members_retained": 2,
            "retained_members": 2,
            "omitted_members_lower_bound": 1,
        }]

    assert runs[0] == runs[1]
    assert len(runs[0]) == 2
    assert len({name.casefold() for name in runs[0]}) == 2


def test_zip_member_budget_avoids_eager_central_directory_reader(
    tmp_path, monkeypatch
):
    archive = tmp_path / "supp.zip"
    _write_bounded_archive(
        archive,
        "zip",
        [(f"{index}.csv", b"") for index in range(4)],
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 2)
    monkeypatch.setattr(
        _download.zipfile.ZipFile,
        "_RealGetContents",
        lambda _archive: (_ for _ in ()).throw(
            AssertionError("ZIP metadata must be read incrementally")
        ),
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "zip", out_dir
    )

    assert len(extracted) == 2
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member count limit"


def test_tar_member_budget_avoids_caching_archive_iterator(
    tmp_path, monkeypatch
):
    archive = tmp_path / "supp.tar"
    _write_bounded_archive(
        archive,
        "tar",
        [(f"{index}.csv", b"") for index in range(4)],
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 2)
    monkeypatch.setattr(
        _download.tarfile.TarFile,
        "__iter__",
        lambda _archive: (_ for _ in ()).throw(
            AssertionError("TAR metadata must not use the caching iterator")
        ),
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, "tar", out_dir
    )

    assert len(extracted) == 2
    assert preserved == set()
    assert skipped[0]["reason"] == "archive member count limit"


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_member_limit_counts_ineligible_metadata_work(
    tmp_path, monkeypatch, archive_kind
):
    archive = tmp_path / f"supp.{archive_kind}"
    _write_bounded_archive(
        archive,
        archive_kind,
        [
            ("image-1.png", b""),
            ("table.csv", b""),
            ("image-2.png", b""),
        ],
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 2)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1_000
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, archive_kind, out_dir
    )

    assert [Path(path).name for path in extracted] == ["table.csv"]
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive member count limit",
        "limit": 2,
        "members_inspected": 2,
        "eligible_members_retained": 1,
        "retained_members": 1,
        "omitted_members_lower_bound": 1,
    }]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_member_name_byte_limit_stops_before_retention(
    tmp_path, monkeypatch, archive_kind
):
    first = "nested/" + "a" * 20 + ".csv"
    second = "nested/" + "b" * 20 + ".csv"
    archive = tmp_path / f"supp.{archive_kind}"
    _write_bounded_archive(
        archive,
        archive_kind,
        [(first, b""), (second, b"")],
    )
    first_bytes = len(first.encode("utf-8"))
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", first_bytes
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_OUTPUT_FILE_LIMIT", 10
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    extracted, preserved, skipped = _extract_bounded_archive(
        archive, archive_kind, out_dir
    )

    assert [Path(path).name for path in extracted] == [
        Path(first).name
    ]
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive member name byte limit",
        "limit": first_bytes,
        "members_inspected": 2,
        "eligible_members_retained": 1,
        "retained_members": 1,
        "retained_name_bytes": first_bytes,
        "omitted_members_lower_bound": 1,
    }]
