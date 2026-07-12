import hashlib
import io
import tarfile
import warnings
import zipfile
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
            ("image-2.png", b""),
            ("table.csv", b""),
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

    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": archive.name,
        "reason": "archive member count limit",
        "limit": 2,
        "retained_members": 2,
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
        "retained_members": 1,
        "retained_name_bytes": first_bytes,
        "omitted_members_lower_bound": 1,
    }]
