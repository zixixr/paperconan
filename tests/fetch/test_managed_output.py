import io
import json
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

from paperconan.fetch import _download


class _UnexpectedArchiveSignal(BaseException):
    pass


def _candidate(name, url):
    return {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": name,
            "download_url": url,
        }],
    }


def _write_sidecar(out_dir, managed_files, **extra):
    payload = {"managed_files": managed_files, **extra}
    (out_dir / _download.SOURCE_SIDECAR).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _is_tool_reserved_name(name):
    folded = Path(name).name.casefold()
    sidecar = _download.SOURCE_SIDECAR.casefold()
    return (
        folded == sidecar
        or folded.startswith(".paperconan-archive-")
        or folded.startswith(f".{sidecar}.")
    )


def _archive_payload(archive_kind, members):
    buffer = io.BytesIO()
    if archive_kind == "zip":
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, body in members:
                zf.writestr(name, body)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tf.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def _archive_fields(archive_kind):
    if archive_kind == "zip":
        return {
            "supplementary_archive": {
                "name": "supp.zip",
                "url": "https://x/supp.zip",
            },
        }
    return {
        "oa_package": {
            "name": "supp.tar.gz",
            "url": "https://x/supp.tar.gz",
        },
    }


def test_second_fetch_removes_only_previous_managed_files(
    tmp_path, monkeypatch
):
    user = tmp_path / "user.csv"
    user.write_text("keep", encoding="utf-8")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_text(url, encoding="utf-8")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    _download.download_candidate(
        _candidate("old.csv", "https://x/old"), str(tmp_path)
    )
    _download.download_candidate(
        _candidate("new.csv", "https://x/new"), str(tmp_path)
    )
    assert not (tmp_path / "old.csv").exists()
    assert (tmp_path / "new.csv").exists()
    assert user.read_text(encoding="utf-8") == "keep"


def test_invalid_manifest_paths_never_leave_output_directory(tmp_path):
    outside = tmp_path.parent / "outside.csv"
    outside.write_text("keep", encoding="utf-8")
    directory = tmp_path / "managed-dir"
    directory.mkdir()

    _download._remove_managed_files(
        str(tmp_path),
        [
            "",
            ".",
            "..",
            "../outside.csv",
            str(outside),
            "managed-dir",
            ["unhashable"],
            {"also": "unhashable"},
        ],
    )

    assert outside.read_text(encoding="utf-8") == "keep"
    assert tmp_path.is_dir()
    assert directory.is_dir()


def test_safe_managed_path_rejects_traversal_and_outside_symlink(tmp_path):
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.csv"
    outside_file.write_text("keep", encoding="utf-8")
    (tmp_path / "outside-link").symlink_to(outside_dir, target_is_directory=True)

    assert _download._safe_managed_path(str(tmp_path), "") is None
    assert _download._safe_managed_path(str(tmp_path), ".") is None
    assert _download._safe_managed_path(str(tmp_path), "..") is None
    assert _download._safe_managed_path(str(tmp_path), "../outside.csv") is None
    assert _download._safe_managed_path(str(tmp_path), str(outside_file)) is None
    assert (
        _download._safe_managed_path(
            str(tmp_path), "outside-link/outside.csv"
        )
        is None
    )


def test_safe_in_tree_symlink_removal_unlinks_only_named_link(tmp_path):
    target = tmp_path / "target.csv"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "managed-link.csv"
    link.symlink_to(target)

    _download._remove_managed_files(str(tmp_path), ["managed-link.csv"])

    assert not os.path.lexists(link)
    assert target.read_text(encoding="utf-8") == "keep"


def test_manifest_read_tolerates_malformed_shapes_and_entries(tmp_path):
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    sidecar.write_text("[]", encoding="utf-8")
    assert _download._read_source_sidecar(str(tmp_path)) == {}

    sidecar.write_text("{not json", encoding="utf-8")
    assert _download._read_source_sidecar(str(tmp_path)) == {}

    sidecar.write_text(
        json.dumps({
            "doi": "10.x/example",
            "managed_files": [
                "b.csv",
                ["unhashable"],
                "a.csv",
                {"bad": "entry"},
                "../outside.csv",
                "bad\u0000.csv",
                "a.csv",
                None,
            ],
        }),
        encoding="utf-8",
    )
    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": ["a.csv", "b.csv"],
    }


@pytest.mark.parametrize(
    "malformed_managed_files",
    ["a.csv", {"a.csv": True}, 1, None],
)
def test_manifest_read_rejects_non_list_managed_files(
    tmp_path, malformed_managed_files
):
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    sidecar.write_text(
        json.dumps({
            "doi": "10.x/example",
            "managed_files": malformed_managed_files,
        }),
        encoding="utf-8",
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": [],
    }


def test_manifest_contains_only_sorted_successful_relative_paths(
    tmp_path, monkeypatch
):
    def stub_download(url, dest, **kwargs):
        if url.endswith("skip"):
            return {"ok": False, "path": dest, "skipped_reason": "unavailable"}
        Path(dest).write_bytes(b"x")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    cand = {
        "cand_id": "source:1",
        "tabular_files": [
            {"name": "b.csv", "download_url": "https://x/b"},
            {"name": "a.csv", "download_url": "https://x/a"},
            {"name": "skip.csv", "download_url": "https://x/skip"},
        ],
    }
    _download.download_candidate(cand, str(tmp_path))
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )
    assert sidecar["managed_files"] == ["a.csv", "b.csv"]


def test_source_sidecar_is_deterministic_and_filters_unsafe_paths(tmp_path):
    cand = {
        "cand_id": "source:1",
        "source": "source",
        "doi": "10.x/example",
        "title": "Example",
        "related_dois": ["10.x/related"],
    }
    managed = [
        "b.csv",
        "../outside.csv",
        ["unhashable"],
        "a.csv",
        "/tmp/absolute.csv",
        "a.csv",
    ]

    assert _download._write_source_sidecar(
        cand, str(tmp_path), managed
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    first = sidecar.read_bytes()
    assert _download._write_source_sidecar(
        cand, str(tmp_path), reversed(managed)
    )

    assert sidecar.read_bytes() == first
    assert json.loads(first)["managed_files"] == ["a.csv", "b.csv"]
    assert not list(tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part"))


def test_failed_refresh_preserves_reused_managed_file(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    original = b"old-complete"
    managed.write_bytes(original)
    _write_sidecar(tmp_path, ["table.csv"], doi="10.x/old")

    monkeypatch.setattr(
        _download,
        "download_file",
        lambda url, dest, **kwargs: {
            "ok": False,
            "path": dest,
            "skipped_reason": "unavailable",
        },
    )
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert managed.read_bytes() == original
    assert result["downloaded"] == []
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )["managed_files"] == ["table.csv"]


def test_failed_archive_refresh_preserves_previous_managed_files(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    original = b"old-complete"
    managed.write_bytes(original)
    _write_sidecar(tmp_path, ["table.csv"])

    def bad_archive(url, dest, **kwargs):
        Path(dest).write_bytes(b"not-a-zip")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", bad_archive)
    result = _download.download_candidate({
        "cand_id": "source:1",
        "tabular_files": [],
        "supplementary_archive": {
            "name": "supp.zip",
            "url": "https://x/supp.zip",
        },
    }, str(tmp_path))

    assert managed.read_bytes() == original
    assert result["downloaded"] == []
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(encoding="utf-8")
    )["managed_files"] == ["table.csv"]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_oversized_reused_member_preserves_previous_file(
    tmp_path, monkeypatch, archive_kind
):
    managed = tmp_path / "table.csv"
    original = b"old-complete"
    managed.write_bytes(original)
    _write_sidecar(tmp_path, ["table.csv"])
    payload = _archive_payload(
        archive_kind,
        [("nested/table.csv", b"x" * 100)],
    )

    def archive_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", archive_download)
    result = _download.download_candidate({
        "cand_id": "source:1",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path), max_bytes=10)

    assert managed.read_bytes() == original
    assert result["downloaded"] == []
    assert result["skipped"] == []
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == ["table.csv"]
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def test_sidecar_commit_failure_does_not_prune_old_managed_files(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.csv"
    old.write_text("old", encoding="utf-8")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")
    original_sidecar = (
        tmp_path / _download.SOURCE_SIDECAR
    ).read_bytes()

    def stub_download(url, dest, **kwargs):
        Path(dest).write_text("new", encoding="utf-8")
        return {"ok": True, "path": dest}

    replace = _download.os.replace

    def fail_sidecar_replace(src, dest):
        if Path(dest).name == _download.SOURCE_SIDECAR:
            raise OSError("sidecar commit failed")
        return replace(src, dest)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "replace", fail_sidecar_replace)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    assert result["downloaded"] == [str(tmp_path / "new.csv")]
    assert old.read_text(encoding="utf-8") == "old"
    assert (
        tmp_path / _download.SOURCE_SIDECAR
    ).read_bytes() == original_sidecar
    assert not list(tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part"))


def test_cleanup_failure_keeps_residual_file_owned_and_reports_it(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.csv"
    old.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"new-complete")
        return {"ok": True, "path": dest}

    remove = _download.os.remove

    def fail_old_remove(path):
        if Path(path).name == "old.csv":
            raise OSError("cleanup blocked")
        return remove(path)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "remove", fail_old_remove)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    new = tmp_path / "new.csv"
    assert new.read_bytes() == b"new-complete"
    assert old.read_bytes() == b"old-complete"
    assert result["downloaded"] == [str(new)]
    assert result["skipped"] == [{
        "name": "old.csv",
        "reason": "could not remove managed file",
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "cand_id": "source:1",
        "doi": None,
        "managed_files": ["new.csv", "old.csv"],
        "related_dois": [],
        "source": "source",
        "title": None,
    }


@pytest.mark.parametrize("probe_mode", ["false", "unavailable"])
def test_cleanup_failure_retains_ownership_without_existence_probe(
    tmp_path, monkeypatch, probe_mode
):
    old = tmp_path / "old.csv"
    old.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"new-complete")
        return {"ok": True, "path": dest}

    remove = _download.os.remove

    def fail_old_remove(path):
        if Path(path).name == "old.csv":
            raise OSError("cleanup blocked")
        return remove(path)

    lexists = _download.os.path.lexists

    def unreliable_lexists(path):
        if Path(path).name != "old.csv":
            return lexists(path)
        if probe_mode == "false":
            return False
        raise OSError("existence probe unavailable")

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "remove", fail_old_remove)
    monkeypatch.setattr(
        _download.os.path, "lexists", unreliable_lexists
    )
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    new = tmp_path / "new.csv"
    assert new.read_bytes() == b"new-complete"
    assert old.read_bytes() == b"old-complete"
    assert result["downloaded"] == [str(new)]
    assert result["skipped"] == [{
        "name": "old.csv",
        "reason": "could not remove managed file",
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == ["new.csv", "old.csv"]


def test_narrowing_commit_failure_keeps_broad_manifest_ownership(
    tmp_path, monkeypatch
):
    removed = tmp_path / "removed.csv"
    residual = tmp_path / "residual.csv"
    removed.write_bytes(b"remove-me")
    residual.write_bytes(b"keep-owned")
    _write_sidecar(
        tmp_path,
        ["removed.csv", "residual.csv"],
        doi="10.x/old",
    )

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"new-complete")
        return {"ok": True, "path": dest}

    remove = _download.os.remove

    def fail_residual_remove(path):
        if Path(path).name == "residual.csv":
            raise OSError("cleanup blocked")
        return remove(path)

    replace = _download.os.replace
    sidecar_replaces = 0

    def fail_narrowing_replace(src, dest):
        nonlocal sidecar_replaces
        if Path(dest).name == _download.SOURCE_SIDECAR:
            sidecar_replaces += 1
            if sidecar_replaces == 2:
                raise OSError("narrowing commit failed")
        return replace(src, dest)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "remove", fail_residual_remove)
    monkeypatch.setattr(_download.os, "replace", fail_narrowing_replace)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    new = tmp_path / "new.csv"
    assert new.read_bytes() == b"new-complete"
    assert not removed.exists()
    assert residual.read_bytes() == b"keep-owned"
    assert result["downloaded"] == [str(new)]
    assert result["skipped"] == [{
        "name": "residual.csv",
        "reason": "could not remove managed file",
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == ["new.csv", "removed.csv", "residual.csv"]
    assert not list(tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part"))


def test_unmanaged_direct_target_is_preserved(tmp_path, monkeypatch):
    user = tmp_path / "table.csv"
    user.write_text("user", encoding="utf-8")

    def stub_download(url, dest, **kwargs):
        Path(dest).write_text("managed", encoding="utf-8")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )
    assert user.read_text(encoding="utf-8") == "user"
    managed = [Path(path).name for path in result["downloaded"]]
    assert len(managed) == 1
    assert managed[0].startswith("table--")


@pytest.mark.parametrize(
    ("unsafe_name", "url"),
    [
        (".", "https://x/."),
        ("..", "https://x/.."),
        ("", "https://x/.."),
    ],
)
def test_unsafe_direct_basename_uses_download_fallback(
    tmp_path, monkeypatch, unsafe_name, url
):
    def stub_download(url, dest, **kwargs):
        Path(dest).write_text("managed", encoding="utf-8")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate(unsafe_name, url),
        str(tmp_path),
    )

    assert result["downloaded"] == [str(tmp_path / "download")]
    assert (tmp_path / "download").read_text(encoding="utf-8") == "managed"


@pytest.mark.parametrize(
    "reserved_name",
    [
        "paperconan_source.json",
        "PAPERCONAN_SOURCE.JSON",
        ".paperconan-archive-user.csv",
        ".PAPERCONAN-ARCHIVE-user.CSV",
        ".paperconan_source.json.user.part",
        ".PAPERCONAN_SOURCE.JSON.user.PART",
    ],
)
def test_reserved_direct_name_uses_safe_destination_and_keeps_metadata(
    tmp_path, monkeypatch, reserved_name
):
    source_bytes = b"source-complete"

    def stub_download(url, dest, **kwargs):
        Path(dest).write_bytes(source_bytes)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate(reserved_name, "https://x/source"),
        str(tmp_path),
    )

    assert result["skipped"] == []
    assert len(result["downloaded"]) == 1
    downloaded = Path(result["downloaded"][0])
    assert not _is_tool_reserved_name(downloaded.name)
    assert downloaded.read_bytes() == source_bytes
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["cand_id"] == "source:1"
    assert payload["managed_files"] == [downloaded.name]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    "reserved_name",
    [
        "paperconan_source.json",
        "PAPERCONAN_SOURCE.JSON",
        ".paperconan-archive-member.csv",
        ".PAPERCONAN-ARCHIVE-member.CSV",
        ".paperconan_source.json.member.csv",
        ".PAPERCONAN_SOURCE.JSON.member.CSV",
    ],
)
def test_reserved_archive_member_uses_safe_destination_and_keeps_metadata(
    tmp_path, monkeypatch, archive_kind, reserved_name
):
    source_bytes = b"source-complete"
    buffer = io.BytesIO()
    if archive_kind == "zip":
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(f"nested/{reserved_name}", source_bytes)
        archive_fields = {
            "supplementary_archive": {
                "name": "supp.zip",
                "url": "https://x/supp.zip",
            },
        }
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            info = tarfile.TarInfo(f"nested/{reserved_name}")
            info.size = len(source_bytes)
            tf.addfile(info, io.BytesIO(source_bytes))
        archive_fields = {
            "oa_package": {
                "name": "supp.tar.gz",
                "url": "https://x/supp.tar.gz",
            },
        }
    payload = buffer.getvalue()

    def archive_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download, "is_supported_input", lambda name: True
    )
    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **archive_fields,
    }, str(tmp_path))

    assert result["skipped"] == []
    assert len(result["downloaded"]) == 1
    downloaded = Path(result["downloaded"][0])
    assert not _is_tool_reserved_name(downloaded.name)
    assert downloaded.read_bytes() == source_bytes
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["cand_id"] == "source:1"
    assert sidecar_payload["managed_files"] == [downloaded.name]
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def test_malicious_manifest_reserved_names_are_not_owned_or_removed(
    tmp_path,
):
    archive_temp = tmp_path / ".PAPERCONAN-ARCHIVE-user.csv"
    sidecar_temp = (
        tmp_path / ".PAPERCONAN_SOURCE.JSON.user.PART"
    )
    safe = tmp_path / "safe.csv"
    archive_temp.write_bytes(b"user-archive")
    sidecar_temp.write_bytes(b"user-sidecar-temp")
    safe.write_bytes(b"managed")
    managed_files = [
        "paperconan_source.json",
        "PAPERCONAN_SOURCE.JSON",
        archive_temp.name,
        ".paperconan-archive-other.csv",
        sidecar_temp.name,
        ".paperconan_source.json.other.part",
        safe.name,
    ]
    _write_sidecar(
        tmp_path,
        managed_files,
        doi="10.x/example",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": ["safe.csv"],
    }
    assert _download._remove_managed_files(
        str(tmp_path), managed_files
    ) == []

    assert sidecar.read_bytes() == original_sidecar
    assert archive_temp.read_bytes() == b"user-archive"
    assert sidecar_temp.read_bytes() == b"user-sidecar-temp"
    assert not safe.exists()


def test_unmanaged_archive_target_is_preserved(tmp_path):
    user = tmp_path / "table.csv"
    user.write_text("user", encoding="utf-8")
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/table.csv", b"a\n1\n")

    paths = _download._extract_tabular_zip(
        str(archive),
        str(tmp_path),
        reusable_names=set(),
    )
    assert user.read_text(encoding="utf-8") == "user"
    assert len(paths) == 1
    assert Path(paths[0]).name.startswith("table--")


def test_unmanaged_tar_target_is_preserved(tmp_path):
    user = tmp_path / "table.csv"
    user.write_text("user", encoding="utf-8")
    archive = tmp_path / "supp.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        body = b"a\n1\n"
        info = tarfile.TarInfo("nested/table.csv")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))

    paths = _download._extract_tabular_tar(
        str(archive),
        str(tmp_path),
        reusable_names=set(),
    )

    assert user.read_text(encoding="utf-8") == "user"
    assert len(paths) == 1
    assert Path(paths[0]).name.startswith("table--")


def test_archive_member_reuses_only_previous_managed_name(tmp_path):
    managed = tmp_path / "table.csv"
    managed.write_text("old", encoding="utf-8")
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/table.csv", b"a\n1\n")

    paths = _download._extract_tabular_zip(
        str(archive),
        str(tmp_path),
        reusable_names={"table.csv"},
    )

    assert paths == [str(managed)]
    assert managed.read_bytes() == b"a\n1\n"


@pytest.mark.parametrize("package_kind", ["zip", "tar"])
def test_archive_package_temporary_never_uses_candidate_basename(
    tmp_path, monkeypatch, package_kind
):
    candidate_name = "user-package.zip" if package_kind == "zip" else "user-package.tar.gz"
    user = tmp_path / candidate_name
    user.write_text("keep", encoding="utf-8")
    seen_destinations = []

    if package_kind == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("nested/table.csv", b"a\n1\n")
        payload = buffer.getvalue()
        archive_fields = {
            "supplementary_archive": {
                "name": candidate_name,
                "url": "https://x/package.zip",
            },
        }
    else:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            body = b"a\n1\n"
            info = tarfile.TarInfo("nested/table.csv")
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
        payload = buffer.getvalue()
        archive_fields = {
            "oa_package": {
                "name": candidate_name,
                "url": "https://x/package.tar.gz",
            },
        }

    def stub_download(url, dest, **kwargs):
        seen_destinations.append(Path(dest))
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate({
        "cand_id": "source:1",
        "tabular_files": [],
        **archive_fields,
    }, str(tmp_path))

    assert user.read_text(encoding="utf-8") == "keep"
    assert [Path(path).name for path in result["downloaded"]] == ["table.csv"]
    assert len(seen_destinations) == 1
    assert seen_destinations[0] != user
    assert not seen_destinations[0].exists()


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_partial_member_failure_keeps_success_and_reused_owner(
    tmp_path, monkeypatch, archive_kind
):
    old = tmp_path / "old.csv"
    old.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")
    payload = _archive_payload(
        archive_kind,
        [
            ("new.csv", b"new-complete"),
            ("old.csv", b"replacement"),
        ],
    )

    def archive_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    atomic_stream_write = _download._atomic_stream_write

    def fail_old_member(src, dest, max_bytes):
        if Path(dest).name == "old.csv":
            raise OSError("member write failed")
        return atomic_stream_write(src, dest, max_bytes)

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download, "_atomic_stream_write", fail_old_member
    )
    result = _download.download_candidate({
        "cand_id": "source:1",
        "doi": "10.x/new",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    new = tmp_path / "new.csv"
    assert new.read_bytes() == b"new-complete"
    assert old.read_bytes() == b"old-complete"
    assert result["downloaded"] == [str(new)]
    assert result["skipped"] == [{
        "name": "old.csv",
        "reason": "archive member failed: member write failed",
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "cand_id": "source:1",
        "doi": "10.x/new",
        "managed_files": ["new.csv", "old.csv"],
        "related_dois": [],
        "source": None,
        "title": None,
    }
    assert not list(tmp_path.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_member_close_failure_after_commit_stays_downloaded(
    tmp_path, monkeypatch, archive_kind
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["table.csv"], doi="10.x/old")
    payload = _archive_payload(
        archive_kind,
        [("table.csv", b"new-complete")],
    )

    def archive_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    class CloseFailureStream:
        def __init__(self, inner):
            self._inner = inner

        def read(self, size=-1):
            return self._inner.read(size)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._inner.__exit__(exc_type, exc_value, traceback)
            raise OSError("member close failed")

    monkeypatch.setattr(_download, "download_file", archive_download)
    if archive_kind == "zip":
        open_member = _download.zipfile.ZipFile.open

        def open_with_close_failure(archive, member, *args, **kwargs):
            return CloseFailureStream(
                open_member(archive, member, *args, **kwargs)
            )

        monkeypatch.setattr(
            _download.zipfile.ZipFile,
            "open",
            open_with_close_failure,
        )
    else:
        open_member = _download.tarfile.TarFile.extractfile

        def open_with_close_failure(archive, member, *args, **kwargs):
            return CloseFailureStream(
                open_member(archive, member, *args, **kwargs)
            )

        monkeypatch.setattr(
            _download.tarfile.TarFile,
            "extractfile",
            open_with_close_failure,
        )

    result = _download.download_candidate({
        "cand_id": "source:1",
        "doi": "10.x/new",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    assert managed.read_bytes() == b"new-complete"
    assert result["downloaded"] == [str(managed)]
    assert result["skipped"] == [{
        "name": "table.csv",
        "reason": (
            "archive member close failed after commit: "
            "member close failed"
        ),
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "cand_id": "source:1",
        "doi": "10.x/new",
        "managed_files": ["table.csv"],
        "related_dois": [],
        "source": None,
        "title": None,
    }
    assert not list(tmp_path.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (MemoryError, "resource exhausted"),
        (ValueError, "unexpected value"),
        (_UnexpectedArchiveSignal, "unexpected signal"),
    ],
)
def test_unexpected_member_failure_propagates(
    tmp_path, monkeypatch, archive_kind, error_type, message
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["table.csv"], doi="10.x/old")
    payload = _archive_payload(
        archive_kind,
        [("table.csv", b"new-complete")],
    )

    def archive_download(url, dest, **kwargs):
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest}

    def fail_unexpectedly(src, dest, max_bytes):
        raise error_type(message)

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download, "_atomic_stream_write", fail_unexpectedly
    )

    with pytest.raises(error_type, match=message):
        _download.download_candidate({
            "cand_id": "source:1",
            "tabular_files": [],
            **_archive_fields(archive_kind),
        }, str(tmp_path))

    assert managed.read_bytes() == b"old-complete"
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    ) == {
        "managed_files": ["table.csv"],
        "doi": "10.x/old",
    }
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def _bounded_download_stub(monkeypatch, payloads, calls):
    def download(url, dest, **kwargs):
        limit = kwargs["max_bytes"]
        calls.append((url, limit))
        try:
            size = _download._atomic_stream_write(
                io.BytesIO(payloads[url]),
                dest,
                limit,
            )
        except _download._SizeLimitExceeded as error:
            return {
                "ok": False,
                "path": dest,
                "skipped_reason": str(error),
            }
        return {"ok": True, "path": dest, "size": size}

    monkeypatch.setattr(_download, "download_file", download)
    monkeypatch.setattr(
        _download, "_read_source_sidecar", lambda _out_dir: {}
    )
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        lambda _cand, _out_dir, _managed_files: True,
    )


def test_direct_download_accepts_exact_fit_then_stops_at_paper_cap(
    tmp_path, monkeypatch
):
    (tmp_path / "existing.bin").write_bytes(b"seed")
    payloads = {
        "https://x/exact": b"123456",
        "https://x/overflow": b"x",
    }
    calls = []
    _bounded_download_stub(monkeypatch, payloads, calls)
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    result = _download.download_candidate({
        "cand_id": "source:1",
        "tabular_files": [
            {
                "name": "exact.csv",
                "download_url": "https://x/exact",
            },
            {
                "name": "overflow.csv",
                "download_url": "https://x/overflow",
            },
        ],
    }, str(tmp_path))

    assert result["downloaded"] == [str(tmp_path / "exact.csv")]
    assert calls == [("https://x/exact", 6)]
    assert _download._dir_size(tmp_path) == 10
    assert not (tmp_path / "overflow.csv").exists()


def test_direct_download_rejects_one_byte_paper_cap_overflow(
    tmp_path, monkeypatch
):
    (tmp_path / "existing.bin").write_bytes(b"seed")
    calls = []
    _bounded_download_stub(
        monkeypatch,
        {"https://x/overflow": b"1234567"},
        calls,
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    result = _download.download_candidate(
        _candidate("overflow.csv", "https://x/overflow"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert calls == [("https://x/overflow", 6)]
    assert _download._dir_size(tmp_path) == 4
    assert not (tmp_path / "overflow.csv").exists()


@pytest.mark.parametrize(
    ("replacement", "accepted"),
    [(b"ABCDEF", True), (b"ABCDEFG", False)],
)
def test_direct_download_credits_managed_replacement_once(
    tmp_path, monkeypatch, replacement, accepted
):
    old = tmp_path / "table.csv"
    old.write_bytes(b"123456")
    (tmp_path / "existing.bin").write_bytes(b"seed")
    calls = []
    _bounded_download_stub(
        monkeypatch,
        {"https://x/table": replacement},
        calls,
    )
    monkeypatch.setattr(
        _download,
        "_read_source_sidecar",
        lambda _out_dir: {"managed_files": ["table.csv"]},
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table"),
        str(tmp_path),
    )

    assert calls == [("https://x/table", 6)]
    assert result["downloaded"] == (
        [str(old)] if accepted else []
    )
    assert old.read_bytes() == (
        replacement if accepted else b"123456"
    )
    assert _download._dir_size(tmp_path) == 10


def _write_archive(path, archive_kind, members):
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


def _extract_archive(path, archive_kind, out_dir, reusable_names=()):
    extract = (
        _download._extract_tabular_zip
        if archive_kind == "zip"
        else _download._extract_tabular_tar
    )
    return extract(
        str(path),
        str(out_dir),
        max_member_bytes=100,
        reusable_names=reusable_names,
    )


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_accepts_exact_fit_then_stops_at_paper_cap(
    tmp_path, monkeypatch, archive_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.bin").write_bytes(b"seed")
    archive = tmp_path / f"supp.{archive_kind}"
    _write_archive(
        archive,
        archive_kind,
        [("exact.csv", b"123456"), ("overflow.csv", b"x")],
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    paths = _extract_archive(archive, archive_kind, out_dir)

    assert paths == [str(out_dir / "exact.csv")]
    assert _download._dir_size(out_dir) == 10
    assert not (out_dir / "overflow.csv").exists()


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_rejects_one_byte_paper_cap_overflow(
    tmp_path, monkeypatch, archive_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.bin").write_bytes(b"seed")
    archive = tmp_path / f"supp.{archive_kind}"
    _write_archive(
        archive,
        archive_kind,
        [("overflow.csv", b"1234567")],
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    paths = _extract_archive(archive, archive_kind, out_dir)

    assert paths == []
    assert _download._dir_size(out_dir) == 4
    assert not (out_dir / "overflow.csv").exists()


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    ("replacement", "accepted"),
    [(b"ABCDEF", True), (b"ABCDEFG", False)],
)
def test_archive_credits_managed_replacement_once(
    tmp_path, monkeypatch, archive_kind, replacement, accepted
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "table.csv"
    old.write_bytes(b"123456")
    (out_dir / "existing.bin").write_bytes(b"seed")
    archive = tmp_path / f"supp.{archive_kind}"
    _write_archive(
        archive,
        archive_kind,
        [("table.csv", replacement)],
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)

    paths = _extract_archive(
        archive,
        archive_kind,
        out_dir,
        reusable_names=["table.csv"],
    )

    assert paths == ([str(old)] if accepted else [])
    assert old.read_bytes() == (
        replacement if accepted else b"123456"
    )
    assert _download._dir_size(out_dir) == 10
