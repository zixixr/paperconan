import io
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import threading
import tracemalloc
import zipfile

import pytest

from paperconan import _source_sidecar
from paperconan.fetch import _download


_PRODUCTION_IDENTITY_BOUND_REMOVE = _download._identity_bound_remove
_PRODUCTION_IDENTITY_BOUND_MUTATION_AVAILABLE = (
    _download._identity_bound_mutation_available
)


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


def _write_sidecar(
    out_dir,
    managed_files,
    *,
    legacy=False,
    **extra,
):
    if (
        not legacy
        and type(managed_files) in (list, tuple)
        and all(
            isinstance(name, str)
            and _download._safe_managed_name(name) is not None
            and (out_dir / name).is_file()
            for name in managed_files
        )
    ):
        managed_files = {
            name: _owned_entry((out_dir / name).read_bytes())
            for name in managed_files
        }
    payload = {"managed_files": managed_files, **extra}
    (out_dir / _download.SOURCE_SIDECAR).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _owned_entry(data):
    return {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _owned_map(out_dir, managed_names):
    return {
        name: _owned_entry((out_dir / name).read_bytes())
        for name in managed_names
    }


def _write_owned_sidecar(out_dir, managed_names, **extra):
    managed_files = _owned_map(out_dir, managed_names)
    _write_sidecar(out_dir, managed_files, **extra)


def _write_download_bytes(destination, data):
    if hasattr(destination, "fd"):
        os.ftruncate(destination.fd, 0)
        os.lseek(destination.fd, 0, os.SEEK_SET)
        os.write(destination.fd, data)
    else:
        Path(destination).write_bytes(data)


def _replace_visible_owned_entry(
    output,
    descriptor,
    *,
    prefix,
    replacement,
):
    opened = os.fstat(descriptor)
    for name in os.listdir(output.fd):
        if not name.startswith(prefix):
            continue
        current = os.stat(
            name,
            dir_fd=output.fd,
            follow_symlinks=False,
        )
        if (
            current.st_dev,
            current.st_ino,
        ) != (
            opened.st_dev,
            opened.st_ino,
        ):
            continue
        os.unlink(name, dir_fd=output.fd)
        replacement_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=output.fd,
        )
        try:
            os.write(replacement_fd, replacement)
            os.fsync(replacement_fd)
        finally:
            os.close(replacement_fd)
        return Path(output.path) / name
    raise AssertionError("owned staging entry was not found")


def _replace_named_entry(directory_fd, name, replacement):
    os.unlink(name, dir_fd=directory_fd)
    replacement_fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        os.write(replacement_fd, replacement)
        os.fsync(replacement_fd)
    finally:
        os.close(replacement_fd)


def _sidecar_commit_skip(operation, message):
    return {
        "name": _download.SOURCE_SIDECAR,
        "reason": "could not commit source sidecar",
        "operation": operation,
        "error_type": "OSError",
        "error": message,
        "ownership_preserved": True,
    }


def _is_tool_reserved_name(name):
    folded = Path(name).name.casefold()
    sidecar = _download.SOURCE_SIDECAR.casefold()
    return (
        folded == sidecar
        or folded.startswith(".paperconan-archive-")
        or folded.startswith(f".{sidecar}.")
    )


def _is_staged_sidecar_publication(src, dest, kwargs):
    return (
        Path(dest).name == _download.SOURCE_SIDECAR
        and Path(src).name.startswith(
            f".{_download.SOURCE_SIDECAR}."
        )
        and kwargs.get("src_dir_fd") is not None
        and kwargs.get("src_dir_fd")
        == kwargs.get("dst_dir_fd")
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


def _archive_cleanup_pending_record(archive_kind):
    return {
        "name": (
            "supp.zip"
            if archive_kind == "zip"
            else "supp.tar.gz"
        ),
        "reason": "transient archive cleanup pending",
    }


def _explicit_cause_chain(error):
    chain = []
    seen = {id(error)}
    current = error.__cause__
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__
    return chain


def _open_fd_count():
    for path in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(path):
            return len(os.listdir(path))
    pytest.skip("open file-descriptor inspection is unavailable")


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


def _fail_identity_bound_cleanup(*args, **kwargs):
    raise _download._IdentityBoundMutationUnavailableError(
        "identity-bound cleanup is unavailable"
    )


def test_second_fetch_removes_only_previous_managed_files(
    tmp_path, monkeypatch
):
    user = tmp_path / "user.csv"
    user.write_text("keep", encoding="utf-8")

    def stub_download(url, dest, **kwargs):
        _write_download_bytes(dest, url.encode("utf-8"))
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


def test_managed_symlink_is_preserved_without_regular_file_fingerprint(
    tmp_path,
):
    target = tmp_path / "target.csv"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "managed-link.csv"
    link.symlink_to(target)

    _download._remove_managed_files(str(tmp_path), ["managed-link.csv"])

    assert os.path.lexists(link)
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

    expected_managed = (
        {}
        if isinstance(malformed_managed_files, dict)
        else []
    )
    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": expected_managed,
    }


def test_fingerprinted_manifest_is_read_deterministically(tmp_path):
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    first.write_bytes(b"a\n1\n")
    second.write_bytes(b"b\n2\n")
    _write_sidecar(
        tmp_path,
        {
            "b.csv": _owned_entry(second.read_bytes()),
            "a.csv": _owned_entry(first.read_bytes()),
        },
        doi="10.x/example",
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": {
            "a.csv": _owned_entry(first.read_bytes()),
            "b.csv": _owned_entry(second.read_bytes()),
        },
    }


@pytest.mark.parametrize(
    "fingerprint",
    [
        {"size": -1, "sha256": "0" * 64},
        {"size": 1, "sha256": "A" * 64},
        {"size": 1, "sha256": "0" * 63},
        {"size": True, "sha256": "0" * 64},
        {"size": 1, "sha256": "0" * 64, "extra": True},
    ],
)
def test_malformed_fingerprint_manifest_fails_closed(
    tmp_path, fingerprint
):
    _write_sidecar(
        tmp_path,
        {"table.csv": fingerprint},
        doi="10.x/example",
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": {},
    }


@pytest.mark.parametrize(
    "managed_values",
    [
        ("valid", "empty"),
        ("empty", "valid"),
    ],
)
def test_duplicate_top_level_managed_files_keys_fail_closed(
    tmp_path, managed_values
):
    managed = tmp_path / "table.csv"
    original = b"table"
    managed.write_bytes(original)
    valid = json.dumps({
        managed.name: _owned_entry(original),
    })
    values = {
        "valid": valid,
        "empty": "{}",
    }
    payload = (
        '{"managed_files":'
        + values[managed_values[0]]
        + ',"managed_files":'
        + values[managed_values[1]]
        + "}"
    )
    (tmp_path / _download.SOURCE_SIDECAR).write_text(
        payload,
        encoding="utf-8",
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {}


def test_duplicate_top_level_non_ownership_key_fails_closed(tmp_path):
    managed = tmp_path / "table.csv"
    original = b"table"
    managed.write_bytes(original)
    managed_payload = json.dumps({
        managed.name: _owned_entry(original),
    })
    payload = (
        '{"doi":"10.x/first","doi":"10.x/second",'
        '"managed_files":'
        + managed_payload
        + "}"
    )
    (tmp_path / _download.SOURCE_SIDECAR).write_text(
        payload,
        encoding="utf-8",
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {}


def test_fingerprint_metadata_counts_toward_managed_name_budget(
    tmp_path, monkeypatch
):
    managed = tmp_path / "a.csv"
    managed.write_bytes(b"x")
    _write_owned_sidecar(tmp_path, ["a.csv"])
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_NAME_BYTES",
        len("a.csv".encode("utf-8")) + 63,
    )

    with pytest.raises(
        _download._SourceSidecarLimit,
        match="source sidecar managed name byte limit",
    ):
        _download._read_source_sidecar(str(tmp_path))


def test_verified_managed_files_rejects_hard_link_aliases(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    payload = b"shared-output"
    first.write_bytes(payload)
    os.link(first, second)
    managed = _owned_map(tmp_path, [first.name, second.name])

    with _download._pinned_output_directory(str(tmp_path)) as output:
        assert _download._verified_managed_files(
            output,
            managed,
        ) == {}

    assert first.read_bytes() == payload
    assert second.read_bytes() == payload


def test_hard_link_alias_sidecar_cannot_authorize_overwrite(
    tmp_path, monkeypatch
):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    original = b"shared-output"
    published = b"new-output"
    first.write_bytes(original)
    os.link(first, second)
    _write_sidecar(
        tmp_path,
        _owned_map(tmp_path, [first.name, second.name]),
        cand_id="source:old",
    )

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, published)
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(published),
        }

    monkeypatch.setattr(_download, "download_file", stub_download)

    result = _download.download_candidate(
        _candidate(first.name, "https://x/first.csv"),
        str(tmp_path),
    )

    assert first.read_bytes() == original
    assert second.read_bytes() == original
    assert len(result["downloaded"]) == 1
    replacement = Path(result["downloaded"][0])
    assert replacement not in {first, second}
    assert replacement.read_bytes() == published


def test_verified_managed_files_rejects_casefold_name_collision(
    tmp_path,
):
    first = tmp_path / "Table.csv"
    second = tmp_path / "table.csv"
    first.write_bytes(b"first-output")
    second.write_bytes(b"second-output")
    managed = _owned_map(tmp_path, [first.name, second.name])

    with _download._pinned_output_directory(str(tmp_path)) as output:
        assert _download._verified_managed_files(
            output,
            managed,
        ) == {}


def test_verified_managed_files_rejects_unicode_name_collision(
    tmp_path,
):
    composed = tmp_path / "caf\u00e9.csv"
    decomposed = tmp_path / "cafe\u0301.csv"
    composed.write_bytes(b"shared-output")
    decomposed.write_bytes(b"shared-output")
    managed = _owned_map(
        tmp_path,
        [composed.name, decomposed.name],
    )

    with _download._pinned_output_directory(str(tmp_path)) as output:
        assert _download._verified_managed_files(
            output,
            managed,
        ) == {}


def test_verified_managed_files_rejects_case_alias_when_supported(
    tmp_path,
):
    canonical = tmp_path / "CaseProbe.csv"
    alias = tmp_path / "caseprobe.csv"
    payload = b"shared-output"
    canonical.write_bytes(payload)
    try:
        canonical_state = canonical.stat()
        alias_state = alias.stat()
    except FileNotFoundError:
        pytest.skip("filesystem is case-sensitive")
    if (
        canonical_state.st_dev,
        canonical_state.st_ino,
    ) != (
        alias_state.st_dev,
        alias_state.st_ino,
    ):
        pytest.skip("case variants resolve to distinct files")
    managed = {
        canonical.name: _owned_entry(payload),
        alias.name: _owned_entry(payload),
    }

    with _download._pinned_output_directory(str(tmp_path)) as output:
        assert _download._verified_managed_files(
            output,
            managed,
        ) == {}


def test_verified_managed_files_preserves_unambiguous_distinct_names(
    tmp_path,
):
    first = tmp_path / "alpha.csv"
    second = tmp_path / "beta.csv"
    first.write_bytes(b"first-output")
    second.write_bytes(b"second-output")
    managed = _owned_map(tmp_path, [first.name, second.name])

    with _download._pinned_output_directory(str(tmp_path)) as output:
        assert _download._verified_managed_files(
            output,
            managed,
        ) == managed


def test_modified_same_name_uses_collision_and_relinquishes_original(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"old-data")
    _write_owned_sidecar(tmp_path, ["table.csv"], doi="10.x/old")
    managed.write_bytes(b"edit-now")
    assert len(b"edit-now") == len(b"old-data")

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-data")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-data"),
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert managed.read_bytes() == b"edit-now"
    assert len(result["downloaded"]) == 1
    replacement = Path(result["downloaded"][0])
    assert replacement.name != "table.csv"
    assert replacement.read_bytes() == b"new-data"
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {
        replacement.name: _owned_entry(b"new-data"),
    }


def test_prepare_content_race_aborts_candidate_and_restores_changed_output(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    original = b"managed-output"
    modified = b"changed-output"
    assert len(modified) == len(original)
    managed.write_bytes(original)
    _write_owned_sidecar(tmp_path, [managed.name], doi="10.x/old")
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    rollback_dirs_before = set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    )

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-output")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-output"),
        }

    move_to_backup = _download._ManagedOutputJournal._move_to_backup
    raced = []

    def modify_then_move(journal, output_name, backup_name):
        if not raced and output_name == managed.name:
            fd = os.open(
                output_name,
                os.O_WRONLY | os.O_NOFOLLOW,
                dir_fd=journal._output.fd,
            )
            try:
                os.ftruncate(fd, 0)
                os.write(fd, modified)
                os.fsync(fd)
            finally:
                os.close(fd)
            raced.append(output_name)
        move_to_backup(journal, output_name, backup_name)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "_move_to_backup",
        modify_then_move,
    )

    result = _download.download_candidate(
        _candidate(managed.name, "https://x/table.csv"),
        str(tmp_path),
    )

    assert raced == [managed.name]
    assert managed.read_bytes() == modified
    assert sidecar.read_bytes() != original_sidecar
    assert len(result["downloaded"]) == 1
    replacement = Path(result["downloaded"][0])
    assert replacement.name != managed.name
    assert replacement.read_bytes() == b"new-output"
    assert result["skipped"] == []
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == {
        replacement.name: _owned_entry(b"new-output"),
    }
    assert set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    ) == rollback_dirs_before


def test_stale_cleanup_content_race_rolls_back_before_journal_close(
    tmp_path, monkeypatch
):
    rollback_dirs_before = set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    )
    stale = tmp_path / "stale.csv"
    original = b"managed-output"
    modified = b"changed-output"
    assert len(modified) == len(original)
    stale.write_bytes(original)
    _write_owned_sidecar(tmp_path, [stale.name], doi="10.x/old")

    move_to_backup = _download._ManagedOutputJournal._move_to_backup
    raced = []

    def modify_then_move(journal, output_name, backup_name):
        if not raced and output_name == stale.name:
            fd = os.open(
                output_name,
                os.O_WRONLY | os.O_NOFOLLOW,
                dir_fd=journal._output.fd,
            )
            try:
                os.ftruncate(fd, 0)
                os.write(fd, modified)
                os.fsync(fd)
            finally:
                os.close(fd)
            raced.append(output_name)
        move_to_backup(journal, output_name, backup_name)

    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "_move_to_backup",
        modify_then_move,
    )

    result = _download.download_candidate(
        {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
        },
        str(tmp_path),
    )

    assert raced == [stale.name]
    assert stale.read_bytes() == modified
    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": stale.name,
        "reason": "could not remove managed file",
    }]
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {
        stale.name: _owned_entry(original),
    }
    assert set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    ) == rollback_dirs_before


def test_stale_cleanup_rollback_failure_preserves_both_error_contexts(
    tmp_path, monkeypatch
):
    rollback_dirs_before = set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    )
    stale = tmp_path / "stale.csv"
    original = b"managed-output"
    modified = b"changed-output"
    assert len(modified) == len(original)
    stale.write_bytes(original)
    _write_owned_sidecar(tmp_path, [stale.name], doi="10.x/old")

    move_to_backup = _download._ManagedOutputJournal._move_to_backup
    real_link = _download.os.link
    raced = []
    restore_failures = []

    def move_then_modify_backup(journal, output_name, backup_name):
        move_to_backup(journal, output_name, backup_name)
        if not raced and output_name == stale.name:
            fd = os.open(
                backup_name,
                os.O_WRONLY | os.O_NOFOLLOW,
                dir_fd=journal._backup_fd,
            )
            try:
                os.ftruncate(fd, 0)
                os.write(fd, modified)
                os.fsync(fd)
            finally:
                os.close(fd)
            raced.append(output_name)

    def fail_stale_restore_link(src, dest, *args, **kwargs):
        is_restore = (
            Path(src).name.isdigit()
            and Path(dest).name == stale.name
            and kwargs.get("src_dir_fd") != kwargs.get("dst_dir_fd")
        )
        if is_restore:
            restore_failures.append((os.fspath(src), os.fspath(dest)))
            raise PermissionError("injected stale restore failure")
        return real_link(src, dest, *args, **kwargs)

    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "_move_to_backup",
        move_then_modify_backup,
    )
    monkeypatch.setattr(_download.os, "link", fail_stale_restore_link)

    with pytest.raises(
        _download._ManagedOutputRecoveryRequiredError,
        match="rollback entry could not be verified",
    ) as caught:
        _download.download_candidate(
            {
                "cand_id": "source:1",
                "source": "source",
                "tabular_files": [],
            },
            str(tmp_path),
        )

    assert raced == [stale.name]
    assert len(restore_failures) == 1
    assert any(
        isinstance(error, _download._ManagedOutputRollbackError)
        for error in _explicit_cause_chain(caught.value)
    )
    assert not stale.exists()
    rollback_dirs = list(set(
        tmp_path.parent.glob(".paperconan-output-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == modified


def test_modified_stale_output_is_preserved_and_relinquished(
    tmp_path, monkeypatch
):
    stale = tmp_path / "stale.csv"
    stale.write_bytes(b"old-data")
    _write_owned_sidecar(tmp_path, ["stale.csv"], doi="10.x/old")
    stale.write_bytes(b"edit-now")

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-data")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-data"),
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    assert stale.read_bytes() == b"edit-now"
    assert result["downloaded"] == [str(tmp_path / "new.csv")]
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {
        "new.csv": _owned_entry(b"new-data"),
    }


def test_legacy_name_only_sidecar_never_authorizes_mutation(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    stale = tmp_path / "stale.csv"
    managed.write_bytes(b"user-data")
    stale.write_bytes(b"keep-stale")
    _write_sidecar(
        tmp_path,
        ["table.csv", "stale.csv"],
        legacy=True,
        doi="10.x/legacy",
    )

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-data")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-data"),
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert managed.read_bytes() == b"user-data"
    assert stale.read_bytes() == b"keep-stale"
    assert len(result["downloaded"]) == 1
    replacement = Path(result["downloaded"][0])
    assert replacement.name != "table.csv"
    assert replacement.read_bytes() == b"new-data"
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == ["table.csv", "stale.csv"]
    assert sidecar["doi"] == "10.x/legacy"


def test_matching_fingerprints_authorize_refresh_and_stale_cleanup(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    stale = tmp_path / "stale.csv"
    managed.write_bytes(b"old-data")
    stale.write_bytes(b"old-stale")
    _write_owned_sidecar(
        tmp_path,
        ["table.csv", "stale.csv"],
        doi="10.x/old",
    )

    def stub_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-data")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-data"),
        }

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert result["downloaded"] == [str(managed)]
    assert managed.read_bytes() == b"new-data"
    assert not stale.exists()
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {
        "table.csv": _owned_entry(b"new-data"),
    }


def test_oversized_sidecar_rejects_before_json_load_and_preserves_state(
    tmp_path, monkeypatch
):
    managed = tmp_path / "old.csv"
    managed.write_bytes(b"old")
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    payload = json.dumps({
        "managed_files": ["old.csv"],
        "title": "x" * 200,
    }).encode("utf-8")
    sidecar.write_bytes(payload)
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", len(payload) - 1,
        raising=False,
    )
    monkeypatch.setattr(
        _download.json,
        "load",
        lambda _stream: (_ for _ in ()).throw(
            AssertionError("over-budget sidecar must not be decoded")
        ),
    )
    download_calls = []
    monkeypatch.setattr(
        _download,
        "download_file",
        lambda *_args, **_kwargs: download_calls.append(True),
    )

    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar byte limit",
        "limit": len(payload) - 1,
        "observed_bytes": len(payload),
        "observed_bytes_is_lower_bound": True,
        "ownership_preserved": True,
    }]
    assert download_calls == []
    assert managed.read_bytes() == b"old"
    assert sidecar.read_bytes() == payload


def test_sidecar_entry_limit_rejects_without_ownership_transition(
    tmp_path, monkeypatch
):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    _write_sidecar(
        tmp_path,
        [None, "../unsafe.csv", "first.csv", "second.csv"],
        doi="10.x/old",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 3,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", 10_000,
        raising=False,
    )
    download_calls = []
    monkeypatch.setattr(
        _download,
        "download_file",
        lambda *_args, **_kwargs: download_calls.append(True),
    )

    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar managed entry limit",
        "limit": 3,
        "managed_entries_inspected": 3,
        "managed_entries_retained": 1,
        "managed_name_bytes_retained": len("first.csv"),
        "omitted_entries_lower_bound": 1,
        "ownership_preserved": True,
    }]
    assert download_calls == []
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert sidecar.read_bytes() == original_sidecar


def test_sidecar_name_byte_limit_rejects_before_retaining_long_name(
    tmp_path, monkeypatch
):
    first_name = "first.csv"
    second_name = "second-long-name.csv"
    first = tmp_path / first_name
    second = tmp_path / second_name
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    _write_sidecar(tmp_path, [first_name, second_name], doi="10.x/old")
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 10,
        raising=False,
    )
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_NAME_BYTES",
        len(first_name) + 64,
        raising=False,
    )

    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar managed name byte limit",
        "limit": len(first_name) + 64,
        "managed_entries_inspected": 2,
        "managed_entries_retained": 1,
        "managed_name_bytes_retained": len(first_name) + 64,
        "requested_name_bytes": len(second_name) + 64,
        "omitted_entries_lower_bound": 1,
        "ownership_preserved": True,
    }]
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert sidecar.read_bytes() == original_sidecar


def test_sidecar_escaped_name_limit_precedes_local_decode(
    tmp_path, monkeypatch
):
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    token = '"\\udcff.csv"'
    payload = (
        '{"doi":"10.x/old","managed_files":['
        + token
        + "]}"
    ).encode("ascii")
    sidecar.write_bytes(payload)
    decoded_bytes = len(
        "\udcff.csv".encode("utf-8", errors="surrogatepass")
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", len(payload),
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 10,
        raising=False,
    )
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_NAME_BYTES",
        decoded_bytes - 1,
        raising=False,
    )
    original_raw_decode = json.JSONDecoder.raw_decode

    def reject_stdlib_name_decode(
        decoder, text, position=0, *, idx=None
    ):
        if idx is not None:
            position = idx
        if text.startswith(token, position):
            raise AssertionError(
                "managed name must be preflighted before stdlib decode"
            )
        return original_raw_decode(decoder, text, position)

    def reject_local_name_decode(text, start, end):
        if text[start:end] == token:
            raise AssertionError(
                "over-budget managed name must not reach local decode"
            )
        return json.loads(text[start:end])

    monkeypatch.setattr(
        _source_sidecar.json.JSONDecoder,
        "raw_decode",
        reject_stdlib_name_decode,
    )
    monkeypatch.setattr(
        _source_sidecar,
        "_decode_json_string_token",
        reject_local_name_decode,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._read_source_sidecar(str(tmp_path))

    assert error.value.record["reason"] == (
        "source sidecar managed name byte limit"
    )
    assert error.value.record["requested_name_bytes"] == decoded_bytes


def test_sidecar_escaped_name_accepts_exact_limit_after_local_preflight(
    tmp_path, monkeypatch
):
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    token = '"\\ud83d\\ude00.csv"'
    payload = (
        '{"managed_files":[' + token + "]}"
    ).encode("ascii")
    sidecar.write_bytes(payload)
    decoded_name = "\N{GRINNING FACE}.csv"
    exact = len(decoded_name.encode("utf-8"))
    decode_calls = []

    def tracked_local_decode(text, start, end):
        decode_calls.append(text[start:end])
        return json.loads(text[start:end])

    monkeypatch.setattr(
        _source_sidecar,
        "_decode_json_string_token",
        tracked_local_decode,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", len(payload),
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 10,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", exact,
        raising=False,
    )

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "managed_files": [decoded_name],
    }
    assert token in decode_calls


@pytest.mark.parametrize(
    "payload",
    [
        b'{"managed_files":["bad\\u12"]}',
        b'{"managed_files":["bad\\x20.csv"]}',
        b'{"managed_files":["bad\n.csv"]}',
        b'{"managed_files":["bad\\ud800\\u"]}',
    ],
)
def test_sidecar_reader_rejects_malformed_string_boundaries(
    tmp_path, payload
):
    (tmp_path / _download.SOURCE_SIDECAR).write_bytes(payload)

    assert _download._read_source_sidecar(str(tmp_path)) == {}


def test_sidecar_entry_limit_streams_without_json_load(
    tmp_path, monkeypatch
):
    managed_files = [f"{index}.csv" for index in range(20_000)]
    _write_sidecar(
        tmp_path,
        managed_files,
        doi="10.x/old",
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 2_000_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 3,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download.json,
        "load",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "managed_files must not be materialized by json.load"
            )
        ),
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._read_source_sidecar(str(tmp_path))

    assert error.value.record["reason"] == (
        "source sidecar managed entry limit"
    )
    assert error.value.record["managed_entries_inspected"] == 3
    assert error.value.record["managed_entries_retained"] == 3


def test_sidecar_reader_stops_at_limit_plus_one_when_stat_underreports(
    tmp_path, monkeypatch
):
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    payload = json.dumps({
        "doi": "10.x/example",
        "title": "x" * 1_000,
    }).encode("utf-8")
    sidecar.write_bytes(payload)
    byte_limit = 64
    real_getsize = _download.os.path.getsize
    real_open = open
    read_sizes = []

    def stale_getsize(path):
        if Path(path) == sidecar:
            return byte_limit
        return real_getsize(path)

    class GuardedReader:
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            read_sizes.append(size)
            assert 0 <= size <= byte_limit + 1
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._stream.close()

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def guarded_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        if Path(path) == sidecar:
            return GuardedReader(stream)
        return stream

    monkeypatch.setattr(_download.os.path, "getsize", stale_getsize)
    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", byte_limit,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._read_source_sidecar(str(tmp_path))

    assert read_sizes == [byte_limit + 1]
    assert error.value.record == {
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar byte limit",
        "limit": byte_limit,
        "observed_bytes": byte_limit + 1,
        "observed_bytes_is_lower_bound": True,
        "ownership_preserved": True,
    }


def test_source_sidecar_encoding_stops_large_related_doi_iteration(
    tmp_path, monkeypatch
):
    class CountingIterable:
        def __init__(self, count):
            self.count = count
            self.items_yielded = 0

        def __iter__(self):
            for index in range(self.count):
                self.items_yielded += 1
                yield f"10.x/{index}"

        def __bool__(self):
            raise AssertionError(
                "lazy related DOIs must not be tested for truthiness"
            )

        def __str__(self):
            raise AssertionError(
                "lazy related DOIs must not use string fallback"
            )

    related = CountingIterable(20_000)
    cand = {
        "doi": "10.x/example",
        "title": "Example",
        "source": "source",
        "cand_id": "source:1",
        "related_dois": related,
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 256,
        raising=False,
    )
    monkeypatch.setattr(
        _download.json,
        "dumps",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "bounded sidecar encoding must not call json.dumps"
            )
        ),
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert 0 < related.items_yielded < related.count


def test_source_sidecar_bounds_lazy_managed_names_before_complete_iteration(
    tmp_path, monkeypatch
):
    class CountingNames:
        def __init__(self, count):
            self.count = count
            self.items_yielded = 0

        def __iter__(self):
            for index in range(self.count):
                self.items_yielded += 1
                yield f"{index}.csv"

        def __bool__(self):
            raise AssertionError(
                "lazy managed names must not be tested for truthiness"
            )

    names = CountingNames(20_000)
    cand = {
        "doi": "10.x/example",
        "title": "Example",
        "source": "source",
        "cand_id": "source:1",
        "related_dois": [],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 3,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", 10_000,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), names
        )

    assert error.value.record["reason"] == (
        "source sidecar managed entry limit"
    )
    assert error.value.record["managed_entries_inspected"] == 3
    assert 0 < names.items_yielded < names.count


@pytest.mark.parametrize(
    ("entry_limit", "item_count"),
    [(0, 1), (3, 4), (3, 3)],
    ids=["zero", "over-limit", "unknown-exact-boundary"],
)
def test_source_sidecar_managed_limit_precedes_iterator_advance(
    tmp_path,
    monkeypatch,
    entry_limit,
    item_count,
):
    class InstrumentedNames:
        def __init__(self):
            self.next_calls = 0
            self.items_yielded = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.next_calls += 1
            if self.items_yielded >= item_count:
                raise StopIteration
            name = f"{self.items_yielded}.csv"
            self.items_yielded += 1
            return name

    names = InstrumentedNames()
    cand = {
        "doi": None,
        "title": None,
        "source": None,
        "cand_id": None,
        "related_dois": [],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", entry_limit,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", 10_000,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), names
        )

    assert error.value.record["reason"] == (
        "source sidecar managed entry limit"
    )
    assert error.value.record["managed_entries_inspected"] == (
        entry_limit
    )
    assert "omitted_entries_lower_bound" not in error.value.record
    assert error.value.record["iterator_exhaustion_unverified"] is True
    assert names.next_calls == entry_limit
    assert names.items_yielded == entry_limit


@pytest.mark.parametrize("retained_count", [0, 2])
def test_source_sidecar_related_doi_budget_precedes_iterator_advance(
    tmp_path,
    monkeypatch,
    retained_count,
):
    class InstrumentedRelatedDois:
        def __init__(self):
            self.next_calls = 0
            self.items_yielded = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.next_calls += 1
            if self.items_yielded > retained_count:
                raise StopIteration
            self.items_yielded += 1
            return 0

    cand = {
        "doi": None,
        "title": None,
        "source": None,
        "cand_id": None,
        "related_dois": [0] * retained_count,
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    exact = len(
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )
    )
    related_dois = InstrumentedRelatedDois()
    cand["related_dois"] = related_dois
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", exact,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert error.value.record["iterator_exhaustion_unverified"] is True
    assert error.value.record["retained_bytes"] <= exact
    assert (
        error.value.record["minimum_bytes_if_additional_entry"]
        > exact
    )
    assert "observed_bytes" not in error.value.record
    assert related_dois.next_calls == retained_count
    assert related_dois.items_yielded == retained_count


@pytest.mark.parametrize(
    "value",
    [
        [],
        [0],
        {"nested": [[]]},
    ],
    ids=["zero", "exact-single", "nested-empty"],
)
def test_source_sidecar_known_iterables_accept_exact_boundary(value):
    expected = (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    assert _source_sidecar.encode_sidecar(
        value,
        byte_limit=len(expected),
    ) == expected


def test_source_sidecar_known_iterable_reports_exact_one_over():
    exact = len(
        _source_sidecar.encode_sidecar(
            [0],
            byte_limit=10_000,
        )
    )

    with pytest.raises(_source_sidecar.SidecarLimitError) as error:
        _source_sidecar.encode_sidecar(
            [0, 0],
            byte_limit=exact,
        )

    assert error.value.reason == "source sidecar byte limit"
    assert (
        "iterator_exhaustion_unverified"
        not in error.value.details
    )
    assert error.value.details["iterable_entries_retained"] == 1
    assert error.value.details["iterable_entries_remaining"] == 1


def test_source_sidecar_nested_known_iterable_reports_exact_remaining(
    tmp_path, monkeypatch
):
    cand = {
        "doi": None,
        "title": None,
        "source": None,
        "cand_id": None,
        "related_dois": [[0]],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    exact = len(
        _download._source_sidecar_bytes(
            cand,
            str(tmp_path),
            [],
        )
    )
    cand["related_dois"] = [[0], [0], [0]]
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_MAX_BYTES",
        exact,
        raising=False,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand,
            str(tmp_path),
            [],
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert "iterator_exhaustion_unverified" not in error.value.record
    assert error.value.record["iterable_entries_retained"] == 1
    assert error.value.record["iterable_entries_remaining"] == 2


@pytest.mark.parametrize(
    (
        "value",
        "byte_limit",
        "retained",
        "remaining",
    ),
    [
        (["x" * 100], 8, 0, 1),
        ([0, "x" * 100, 0], 13, 1, 2),
    ],
    ids=["first-item", "mid-list"],
)
def test_source_sidecar_known_iterable_item_failure_reports_exact_counts(
    value,
    byte_limit,
    retained,
    remaining,
):
    with pytest.raises(_source_sidecar.SidecarLimitError) as error:
        _source_sidecar.encode_sidecar(
            value,
            byte_limit=byte_limit,
        )

    assert error.value.reason == "source sidecar byte limit"
    assert error.value.details["observed_bytes"] > byte_limit
    assert error.value.details["observed_bytes_is_lower_bound"] is True
    assert (
        "iterator_exhaustion_unverified"
        not in error.value.details
    )
    assert error.value.details["iterable_entries_retained"] == retained
    assert error.value.details["iterable_entries_remaining"] == remaining


def test_source_sidecar_nested_item_failure_keeps_inner_exact_counts():
    with pytest.raises(_source_sidecar.SidecarLimitError) as error:
        _source_sidecar.encode_sidecar(
            [[0, "x" * 100], 0],
            byte_limit=26,
        )

    assert error.value.reason == "source sidecar byte limit"
    assert error.value.details["observed_bytes"] > 26
    assert error.value.details["observed_bytes_is_lower_bound"] is True
    assert (
        "iterator_exhaustion_unverified"
        not in error.value.details
    )
    assert error.value.details["iterable_entries_retained"] == 1
    assert error.value.details["iterable_entries_remaining"] == 1


def test_source_sidecar_unknown_iterable_item_failure_has_no_exact_counts():
    class UnknownIterable:
        def __iter__(self):
            yield "x" * 100

    with pytest.raises(_source_sidecar.SidecarLimitError) as error:
        _source_sidecar.encode_sidecar(
            UnknownIterable(),
            byte_limit=8,
        )

    assert error.value.reason == "source sidecar byte limit"
    assert error.value.details["observed_bytes"] > 8
    assert error.value.details["observed_bytes_is_lower_bound"] is True
    assert "iterable_entries_retained" not in error.value.details
    assert "iterable_entries_remaining" not in error.value.details


def test_source_sidecar_encoding_rejects_large_title_before_json_escape(
    tmp_path, monkeypatch
):
    title = "\N{LATIN SMALL LETTER E WITH ACUTE}" * 10_000
    cand = {
        "doi": "10.x/example",
        "title": title,
        "source": "source",
        "cand_id": "source:1",
        "related_dois": [],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 256,
        raising=False,
    )
    original_encoder = json.encoder.encode_basestring_ascii

    def reject_large_scalar(value):
        if value == title:
            raise AssertionError(
                "oversized title must be bounded before JSON escaping"
            )
        return original_encoder(value)

    monkeypatch.setattr(
        json.encoder,
        "encode_basestring_ascii",
        reject_large_scalar,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert error.value.record["observed_bytes_is_lower_bound"] is True


def test_source_sidecar_encoding_bounds_escape_heavy_title_incrementally(
    tmp_path, monkeypatch
):
    title = "\n" * 400
    cand = {
        "doi": "10.x/example",
        "title": title,
        "source": "source",
        "cand_id": "source:1",
        "related_dois": [],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 600,
        raising=False,
    )
    original_encoder = json.encoder.encode_basestring_ascii

    def reject_complete_escape(value):
        if value == title:
            raise AssertionError(
                "escape-heavy title must not form a complete token"
            )
        return original_encoder(value)

    monkeypatch.setattr(
        json.encoder,
        "encode_basestring_ascii",
        reject_complete_escape,
    )

    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert error.value.record["observed_bytes_is_lower_bound"] is True


@pytest.mark.parametrize("huge_key", ["a_huge", "z_huge"])
def test_source_sidecar_preflight_saturates_huge_early_and_late_strings(
    monkeypatch, huge_key
):
    import builtins

    byte_limit = 128
    huge = "x" * 100_000
    value = {
        huge_key: huge,
        "m_small": "ok",
    }
    original_ord = builtins.ord
    huge_char_visits = 0

    def counted_ord(char):
        nonlocal huge_char_visits
        if char == "x":
            huge_char_visits += 1
        return original_ord(char)

    monkeypatch.setattr(builtins, "ord", counted_ord)

    with pytest.raises(_source_sidecar.SidecarLimitError):
        _source_sidecar.encode_sidecar(
            value,
            byte_limit=byte_limit,
        )

    assert huge_char_visits <= byte_limit + 1


def test_source_sidecar_preflight_shares_work_across_many_large_values(
    monkeypatch,
):
    import builtins

    byte_limit = 256
    value = {
        f"k{index:03d}": "x" * 64
        for index in range(200)
    }
    original_ord = builtins.ord
    original_minimum = _source_sidecar._minimum_json_value_size
    huge_char_visits = 0
    values_visited = 0

    def counted_ord(char):
        nonlocal huge_char_visits
        if char == "x":
            huge_char_visits += 1
        return original_ord(char)

    def counted_minimum(value, *args, **kwargs):
        nonlocal values_visited
        values_visited += 1
        return original_minimum(value, *args, **kwargs)

    monkeypatch.setattr(builtins, "ord", counted_ord)
    monkeypatch.setattr(
        _source_sidecar,
        "_minimum_json_value_size",
        counted_minimum,
    )

    with pytest.raises(_source_sidecar.SidecarLimitError):
        _source_sidecar.encode_sidecar(
            value,
            byte_limit=byte_limit,
        )

    assert huge_char_visits <= byte_limit + 1
    assert values_visited < 10


def test_source_sidecar_preflight_saturates_across_many_long_keys(
    monkeypatch,
):
    import builtins

    byte_limit = 256
    value = {
        f"{index:03d}-" + ("k" * 200): None
        for index in range(200)
    }
    original_ord = builtins.ord
    key_char_visits = 0

    def counted_ord(char):
        nonlocal key_char_visits
        if char == "k":
            key_char_visits += 1
        return original_ord(char)

    monkeypatch.setattr(builtins, "ord", counted_ord)

    with pytest.raises(_source_sidecar.SidecarLimitError):
        _source_sidecar.encode_sidecar(
            value,
            byte_limit=byte_limit,
        )

    assert key_char_visits <= byte_limit + 1


def test_source_sidecar_preflight_proves_tiny_limit_without_string_scan(
    monkeypatch,
):
    import builtins

    original_ord = builtins.ord
    huge_char_visits = 0

    def counted_ord(char):
        nonlocal huge_char_visits
        if char == "x":
            huge_char_visits += 1
        return original_ord(char)

    monkeypatch.setattr(builtins, "ord", counted_ord)

    with pytest.raises(_source_sidecar.SidecarLimitError):
        _source_sidecar.encode_sidecar(
            {"a": "x" * 100_000},
            byte_limit=1,
        )

    assert huge_char_visits == 0


def test_source_sidecar_preflight_has_no_per_key_tail_size_list():
    assert (
        "_dict_tail_sizes"
        not in _source_sidecar._BoundedJsonWriter.__dict__
    )


def test_source_sidecar_encoding_never_calls_custom_string_fallback(
    tmp_path, monkeypatch
):
    class Unsupported:
        def __init__(self):
            self.string_calls = 0

        def __str__(self):
            self.string_calls += 1
            raise AssertionError(
                "unsupported sidecar values must not call __str__"
            )

    title = Unsupported()
    cand = {
        "doi": "10.x/example",
        "title": title,
        "source": "source",
        "cand_id": "source:1",
        "related_dois": [],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )

    with pytest.raises(TypeError, match="unsupported sidecar value"):
        _download._source_sidecar_bytes(
            cand, str(tmp_path), []
        )

    assert title.string_calls == 0


def test_source_sidecar_canonicalizes_scalar_subclasses_without_hooks():
    class HostileString(str):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

    class HostileInteger(int):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

        def __int__(self):
            raise AssertionError("custom __int__ must not run")

        def __index__(self):
            raise AssertionError("custom __index__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

    class HostileFloat(float):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

        def __float__(self):
            raise AssertionError("custom __float__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

    value = {
        "string": HostileString("text"),
        "integer": HostileInteger(7),
        "float": HostileFloat(1.25),
    }
    expected = (
        json.dumps(
            {
                "string": "text",
                "integer": 7,
                "float": 1.25,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    assert _source_sidecar.encode_sidecar(
        value, byte_limit=len(expected)
    ) == expected


@pytest.mark.parametrize("negative", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_source_sidecar_rejects_huge_integer_before_decimal_materialization(
    negative,
    nested,
):
    value = 1 << 166_100
    if negative:
        value = -value
    payload = {"value": value} if nested else value
    byte_limit = 32 if nested else 16
    setter = getattr(sys, "set_int_max_str_digits", None)
    getter = getattr(sys, "get_int_max_str_digits", None)
    previous_limit = getter() if getter is not None else None
    error = None

    if setter is not None:
        setter(0)
    try:
        tracemalloc.start()
        try:
            _source_sidecar.encode_sidecar(
                payload,
                byte_limit=byte_limit,
            )
        except _source_sidecar.SidecarLimitError as exc:
            error = exc
        finally:
            _, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
    finally:
        if setter is not None:
            setter(previous_limit)

    assert error is not None
    assert error.reason == "source sidecar byte limit"
    assert error.details["observed_bytes"] > byte_limit
    assert error.details["observed_bytes_is_lower_bound"] is True
    assert peak_bytes < 100_000


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        10**99,
        -(10**98),
    ],
    ids=["zero", "negative-small", "positive-boundary", "negative-boundary"],
)
def test_source_sidecar_integer_exact_boundary_matches_json(value):
    expected = (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")

    assert _source_sidecar.encode_sidecar(
        value,
        byte_limit=len(expected),
    ) == expected

    with pytest.raises(_source_sidecar.SidecarLimitError):
        _source_sidecar.encode_sidecar(
            value,
            byte_limit=len(expected) - 1,
        )


def test_source_sidecar_preserves_runtime_integer_string_limit():
    value = 10**700
    setter = getattr(sys, "set_int_max_str_digits", None)
    getter = getattr(sys, "get_int_max_str_digits", None)
    if setter is None or getter is None:
        expected = (str(value) + "\n").encode("ascii")
        assert _source_sidecar.encode_sidecar(
            value,
            byte_limit=len(expected),
        ) == expected
        return

    previous_limit = getter()
    setter(640)
    try:
        with pytest.raises(ValueError, match="Exceeds the limit"):
            _source_sidecar.encode_sidecar(
                value,
                byte_limit=1_000,
            )
    finally:
        setter(previous_limit)


def test_fetch_classifies_scalar_subclasses_before_iterator_probe(
    tmp_path,
):
    class HostileString(str):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

    class HostileInteger(int):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

        def __int__(self):
            raise AssertionError("custom __int__ must not run")

        def __index__(self):
            raise AssertionError("custom __index__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

    class HostileFloat(float):
        def __str__(self):
            raise AssertionError("custom __str__ must not run")

        def __repr__(self):
            raise AssertionError("custom __repr__ must not run")

        def __float__(self):
            raise AssertionError("custom __float__ must not run")

        def __iter__(self):
            raise AssertionError("custom __iter__ must not run")

    cases = [
        (HostileString("text"), "text"),
        (HostileInteger(7), 7),
        (HostileFloat(1.25), 1.25),
        (True, True),
    ]
    for index, (related_dois, expected) in enumerate(cases):
        out_dir = tmp_path / str(index)
        result = _download.download_candidate(
            {
                "cand_id": f"source:{index}",
                "source": "source",
                "related_dois": related_dois,
                "tabular_files": [],
            },
            str(out_dir),
        )

        assert result["skipped"] == []
        payload = json.loads(
            (out_dir / _download.SOURCE_SIDECAR).read_text(
                encoding="utf-8"
            )
        )
        assert payload["related_dois"] == expected


@pytest.mark.parametrize("value", ["text", 7, 1.25])
def test_source_sidecar_exact_scalars_keep_json_shape(value):
    expected = (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    assert _source_sidecar.encode_sidecar(
        value, byte_limit=len(expected)
    ) == expected


def test_source_sidecar_encoding_matches_deterministic_json_bytes(
    tmp_path, monkeypatch
):
    cand = {
        "doi": "10.x/example",
        "title": "line 1\nline 2 \x7f \N{GRINNING FACE}",
        "source": "source",
        "cand_id": "source:1",
        "related_dois": ["10.x/z", "10.x/a"],
    }
    expected_value = {
        **cand,
        "managed_files": {
            "table.csv": _owned_entry(b"table"),
        },
    }
    expected = (
        json.dumps(expected_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_MAX_BYTES",
        len(expected),
        raising=False,
    )

    assert _download._source_sidecar_bytes(
        cand,
        str(tmp_path),
        {"table.csv": _owned_entry(b"table")},
    ) == expected


def test_fetch_preserves_one_shot_related_dois_after_size_accounting(
    tmp_path, monkeypatch
):
    related_dois = iter(["10.x/first", "10.x/second"])
    cand = _candidate("table.csv", "https://x/table")
    cand.update({
        "doi": "10.x/example",
        "title": "Example",
        "source": "source",
        "related_dois": related_dois,
    })

    def stub_download(_url, dest, **_kwargs):
        _write_download_bytes(dest, b"x\n1\n")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)

    result = _download.download_candidate(cand, str(tmp_path))

    assert result["skipped"] == []
    payload = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert payload["related_dois"] == [
        "10.x/first",
        "10.x/second",
    ]


def test_source_sidecar_encoding_accepts_exact_limit_and_rejects_one_over(
    tmp_path, monkeypatch
):
    cand = {
        "doi": "10.x/example",
        "title": "Example",
        "source": "source",
        "cand_id": "source:1",
        "related_dois": ["10.x/related"],
    }
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    payload = _download._source_sidecar_bytes(
        cand, str(tmp_path), ["table.csv"]
    )
    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_MAX_BYTES",
        len(payload),
        raising=False,
    )

    assert _download._source_sidecar_bytes(
        cand, str(tmp_path), ["table.csv"]
    ) == payload

    monkeypatch.setattr(
        _download,
        "_SOURCE_SIDECAR_MAX_BYTES",
        len(payload) - 1,
        raising=False,
    )
    with pytest.raises(_download._SourceSidecarLimit) as error:
        _download._source_sidecar_bytes(
            cand, str(tmp_path), ["table.csv"]
        )

    assert error.value.record["reason"] == "source sidecar byte limit"
    assert error.value.record["observed_bytes"] == len(payload)


def test_managed_output_name_uses_membership_without_copying_reusable_names(
    tmp_path,
):
    class MembershipOnly:
        def __contains__(self, name):
            return name == "table.csv"

        def __iter__(self):
            raise AssertionError("reusable names must not be copied")

    assert _download._managed_output_name(
        str(tmp_path),
        "table.csv",
        "nested/table.csv",
        MembershipOnly(),
    ) == "table.csv"


def test_direct_name_limit_precedes_hash_probe_and_sidecar_change(
    tmp_path, monkeypatch
):
    requested_name = "x" * 10_000 + ".csv"
    hash_calls = []
    probe_calls = []
    download_calls = []
    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_NAME_BYTES", 64, raising=False
    )
    monkeypatch.setattr(
        _download.hashlib,
        "sha256",
        lambda *_args, **_kwargs: hash_calls.append(True),
    )
    monkeypatch.setattr(
        _download.os.path,
        "lexists",
        lambda *_args, **_kwargs: probe_calls.append(True),
    )
    monkeypatch.setattr(
        _download,
        "download_file",
        lambda *_args, **_kwargs: download_calls.append(True),
    )

    result = _download.download_candidate(
        _candidate(requested_name, "https://x/source"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": "managed output",
        "reason": "managed output name byte limit",
        "limit": 64,
        "field": "requested",
        "observed_name_bytes_lower_bound": 65,
        "ownership_preserved": True,
    }]
    assert hash_calls == []
    assert probe_calls == []
    assert download_calls == []
    assert not (tmp_path / _download.SOURCE_SIDECAR).exists()


def test_direct_collision_probe_limit_preserves_authoritative_state(
    tmp_path, monkeypatch
):
    managed = tmp_path / "old.csv"
    managed.write_bytes(b"old")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    probe_limit = 3
    probes = []
    real_stat = _download.os.stat

    def occupied(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name.startswith("table")
        ):
            probes.append(Path(path).name)
            if len(probes) > probe_limit:
                raise AssertionError(
                    "collision limit must precede filesystem probe"
                )
            return real_stat(managed)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT",
        probe_limit,
        raising=False,
    )
    monkeypatch.setattr(_download.os, "stat", occupied)
    monkeypatch.setattr(
        _download,
        "download_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collision exhaustion must not download")
        ),
    )

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": "table.csv",
        "reason": "managed output collision probe limit",
        "limit": probe_limit,
        "collision_probes": probe_limit,
        "ownership_preserved": True,
    }]
    assert len(probes) == probe_limit
    assert managed.read_bytes() == b"old"
    assert sidecar.read_bytes() == original_sidecar


def test_archive_collision_probe_limit_uses_shared_allocator(
    tmp_path, monkeypatch
):
    user = tmp_path / "table.csv"
    user.write_bytes(b"user")
    archive = tmp_path / "supp.zip"
    _write_archive(
        archive,
        "zip",
        [("nested/table.csv", b"a\n1\n")],
    )
    probes = []

    real_stat = _download.os.stat

    def occupied(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name.startswith("table")
        ):
            probes.append(Path(path).name)
            if len(probes) > 1:
                raise AssertionError(
                    "archive collision limit must precede next probe"
                )
            return real_stat(user)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT", 1,
        raising=False,
    )
    monkeypatch.setattr(_download.os, "stat", occupied)
    monkeypatch.setattr(
        _download,
        "_paper_data_size",
        lambda *_args, **_kwargs: len(b"user"),
    )

    extracted, preserved, skipped = _extract_archive_managed(
        archive,
        "zip",
        tmp_path,
    )

    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": "table.csv",
        "reason": "managed output collision probe limit",
        "limit": 1,
        "collision_probes": 1,
    }]
    assert probes == ["table.csv"]
    assert user.read_bytes() == b"user"


def test_archive_occurrence_name_limit_precedes_final_allocation(
    monkeypatch,
):
    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_NAME_BYTES", 17, raising=False
    )
    monkeypatch.setattr(
        _download,
        "_allocate_archive_output_names",
        lambda _names: (_ for _ in ()).throw(
            AssertionError(
                "occurrence name limit must precede allocation"
            )
        ),
    )

    with pytest.raises(
        _download._ManagedOutputNameLimit
    ) as error:
        _download._archive_occurrence_output_names(
            ["a.csv", "a.csv"]
        )

    assert error.value.reason == "managed output name byte limit"
    assert error.value.field == "candidate"


def test_archive_casefold_collision_name_limit_precedes_candidate(
    monkeypatch,
):
    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_NAME_BYTES", 5, raising=False
    )

    with pytest.raises(
        _download._ManagedOutputNameLimit
    ) as error:
        _download._allocate_archive_output_names(
            ["a.csv", "A.csv"]
        )

    assert error.value.reason == "managed output name byte limit"
    assert error.value.field == "candidate"


@pytest.mark.parametrize("channel", ["direct", "zip", "tar"])
def test_sidecar_entry_limit_bounds_new_direct_and_archive_names(
    tmp_path, monkeypatch, channel
):
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_MAX_BYTES", 10_000,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_ENTRY_LIMIT", 1,
        raising=False,
    )
    monkeypatch.setattr(
        _download, "_SOURCE_SIDECAR_NAME_BYTES", 10_000,
        raising=False,
    )
    if channel == "direct":
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [
                {
                    "name": "first.csv",
                    "download_url": "https://x/first",
                },
                {
                    "name": "second.csv",
                    "download_url": "https://x/second",
                },
            ],
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, b"data")
            return {"ok": True, "path": dest, "size": 4}
    else:
        payload = _archive_payload(
            channel,
            [
                ("first.csv", b"first"),
                ("second.csv", b"second"),
            ],
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

    monkeypatch.setattr(_download, "download_file", source_download)

    result = _download.download_candidate(cand, str(tmp_path))

    assert [Path(path).name for path in result["downloaded"]] == [
        "first.csv"
    ]
    skipped_name = (
        "second.csv"
        if channel == "direct"
        else "supp.zip"
        if channel == "zip"
        else "supp.tar.gz"
    )
    assert result["skipped"] == [{
        "name": skipped_name,
        "reason": "source sidecar managed entry limit",
        "limit": 1,
        "managed_entries_retained": 1,
        "managed_name_bytes_retained": len("first.csv") + 64,
        "omitted_entries_lower_bound": 1,
    }]
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(tmp_path, ["first.csv"])


@pytest.mark.parametrize("channel", ["direct", "zip", "tar"])
def test_sidecar_accounting_does_not_serialize_per_output(
    tmp_path, monkeypatch, channel
):
    names = [f"table-{index}.csv" for index in range(8)]
    if channel == "direct":
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [
                {
                    "name": name,
                    "download_url": f"https://x/{name}",
                }
                for name in names
            ],
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, b"x")
            return {"ok": True, "path": dest, "size": 1}
    else:
        payload = _archive_payload(
            channel, [(name, b"x") for name in names]
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

    monkeypatch.setattr(_download, "download_file", source_download)
    serialization_calls = []
    source_sidecar_bytes = _download._source_sidecar_bytes

    def tracked_sidecar_bytes(*args, **kwargs):
        serialization_calls.append(True)
        return source_sidecar_bytes(*args, **kwargs)

    monkeypatch.setattr(
        _download, "_source_sidecar_bytes", tracked_sidecar_bytes
    )

    result = _download.download_candidate(cand, str(tmp_path))

    assert len(result["downloaded"]) == len(names)
    assert len(serialization_calls) <= 2


def test_manifest_contains_only_sorted_successful_relative_paths(
    tmp_path, monkeypatch
):
    def stub_download(url, dest, **kwargs):
        if url.endswith("skip"):
            return {"ok": False, "path": dest, "skipped_reason": "unavailable"}
        _write_download_bytes(dest, b"x")
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
    assert sidecar["managed_files"] == _owned_map(
        tmp_path, ["a.csv", "b.csv"]
    )


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
    (tmp_path / "a.csv").write_bytes(b"a")
    (tmp_path / "b.csv").write_bytes(b"b")

    assert _download._write_source_sidecar(
        cand, str(tmp_path), managed
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    first = sidecar.read_bytes()
    assert _download._write_source_sidecar(
        cand, str(tmp_path), reversed(managed)
    )

    assert sidecar.read_bytes() == first
    assert json.loads(first)["managed_files"] == _owned_map(
        tmp_path, ["a.csv", "b.csv"]
    )
    assert not list(tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part"))


def test_write_source_sidecar_rejects_oversized_existing_before_read(
    tmp_path, monkeypatch
):
    cand = {
        "cand_id": "source:1",
        "source": "source",
    }
    generated = _download._source_sidecar_bytes(
        cand,
        str(tmp_path),
        {},
    )
    byte_limit = len(generated)
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original = b"x" * (byte_limit + 1)
    sidecar.write_bytes(original)
    existing_fds = set()
    existing_reads = []
    real_open = _download.os.open
    real_read = _download.os.read

    def track_existing_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if (
            os.fspath(path) == _download.SOURCE_SIDECAR
            and kwargs.get("dir_fd") is not None
        ):
            existing_fds.add(fd)
        return fd

    def reject_existing_read(fd, size):
        if fd in existing_fds:
            existing_reads.append(size)
            raise AssertionError(
                "oversized existing sidecar must not be read"
            )
        return real_read(fd, size)

    monkeypatch.setattr(
        _download,
        "_MAX_SOURCE_SIDECAR_BYTES",
        byte_limit,
    )
    monkeypatch.setattr(_download.os, "open", track_existing_open)
    monkeypatch.setattr(_download.os, "read", reject_existing_read)

    with pytest.raises(
        _download._SourceSidecarLimitError,
        match="existing provenance sidecar exceeds",
    ):
        _download._write_source_sidecar(
            cand,
            str(tmp_path),
            {},
        )

    assert existing_reads == []
    assert sidecar.read_bytes() == original
    assert not list(
        tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part")
    )


def test_existing_sidecar_concurrent_replacement_before_move_is_preserved(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    concurrent = json.dumps({
        "cand_id": "source:concurrent",
        "managed_files": {},
    }).encode("utf-8")
    real_link = _download.os.link
    installed = []

    def install_concurrent_before_move_link(
        src,
        dest,
        *args,
        **kwargs,
    ):
        is_backup_link = (
            Path(src).name == _download.SOURCE_SIDECAR
            and Path(dest).name.startswith("previous-")
            and kwargs.get("src_dir_fd") != kwargs.get("dst_dir_fd")
        )
        if is_backup_link and not installed:
            sidecar.unlink()
            sidecar.write_bytes(concurrent)
            installed.append(True)
        return real_link(src, dest, *args, **kwargs)

    monkeypatch.setattr(
        _download.os,
        "link",
        install_concurrent_before_move_link,
    )

    with pytest.raises(
        _download._SourceSidecarPublicationError,
        match="changed during publication",
    ):
        _download._write_source_sidecar(
            {
                "cand_id": "source:new",
                "source": "source",
            },
            str(tmp_path),
            _owned_map(tmp_path, [managed.name]),
        )

    assert installed == [True]
    assert sidecar.read_bytes() == concurrent
    assert not list(tmp_path.glob(".paperconan-sidecar-*"))


def test_existing_sidecar_post_move_mismatch_is_restored(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original = sidecar.read_bytes()
    changed = b"x" * len(original)
    real_identity_bound_remove = _download._identity_bound_remove
    moved = []

    def change_after_move(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        result = real_identity_bound_remove(
            directory_fd,
            name,
            descriptor,
            is_directory=is_directory,
        )
        if (
            not moved
            and name == _download.SOURCE_SIDECAR
            and not is_directory
        ):
            rollback_dirs = list(
                tmp_path.parent.glob(
                    ".paperconan-sidecar-rollback-*"
                )
            )
            assert len(rollback_dirs) == 1
            backup = rollback_dirs[0] / "previous-0"
            fd = os.open(
                backup,
                os.O_WRONLY | os.O_NOFOLLOW,
            )
            try:
                os.ftruncate(fd, 0)
                os.write(fd, changed)
                os.fsync(fd)
            finally:
                os.close(fd)
            moved.append(name)
        return result

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        change_after_move,
    )

    with pytest.raises(
        _download._SourceSidecarPublicationError,
        match="changed during publication",
    ):
        _download._write_source_sidecar(
            {
                "cand_id": "source:new",
                "source": "source",
            },
            str(tmp_path),
            _owned_map(tmp_path, [managed.name]),
        )

    assert moved
    assert sidecar.read_bytes() == changed
    assert not list(tmp_path.glob(".paperconan-sidecar-*"))


def test_existing_sidecar_concurrent_creation_after_move_retains_both_copies(
    tmp_path, monkeypatch
):
    rollback_dirs_before = set(
        tmp_path.parent.glob(".paperconan-sidecar-rollback-*")
    )
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original = sidecar.read_bytes()
    concurrent = json.dumps({
        "cand_id": "source:concurrent",
        "managed_files": {},
    }).encode("utf-8")
    real_link = _download.os.link
    installed = []

    def install_concurrent_before_publish(
        src,
        dest,
        *args,
        **kwargs,
    ):
        publishing_staged_sidecar = (
            not installed
            and Path(dest).name == _download.SOURCE_SIDECAR
            and Path(src).name.startswith(
                f".{_download.SOURCE_SIDECAR}."
            )
            and kwargs.get("src_dir_fd")
            == kwargs.get("dst_dir_fd")
        )
        if publishing_staged_sidecar:
            fd = os.open(
                dest,
                (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                ),
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(fd, concurrent)
            finally:
                os.close(fd)
            installed.append(True)
        return real_link(src, dest, *args, **kwargs)

    monkeypatch.setattr(
        _download.os,
        "link",
        install_concurrent_before_publish,
    )

    with pytest.raises(
        _download._SourceSidecarPublicationError,
        match="created during publication",
    ) as caught:
        _download._write_source_sidecar(
            {
                "cand_id": "source:new",
                "source": "source",
            },
            str(tmp_path),
            _owned_map(tmp_path, [managed.name]),
        )

    assert installed == [True]
    assert any(
        isinstance(error, _download._ManagedOutputRollbackError)
        for error in _explicit_cause_chain(caught.value)
    )
    assert sidecar.read_bytes() == concurrent
    rollback_dirs = list(set(
        tmp_path.parent.glob(".paperconan-sidecar-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_existing_sidecar_transaction_replaces_authoritative_metadata(
    tmp_path,
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )

    assert _download._write_source_sidecar(
        {
            "cand_id": "source:new",
            "source": "source",
        },
        str(tmp_path),
        _owned_map(tmp_path, [managed.name]),
    )

    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["cand_id"] == "source:new"
    assert sidecar["managed_files"] == _owned_map(
        tmp_path, [managed.name]
    )
    assert not list(tmp_path.glob(".paperconan-sidecar-*"))


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
    )["managed_files"] == _owned_map(tmp_path, ["table.csv"])


def test_failed_archive_refresh_preserves_previous_managed_files(
    tmp_path, monkeypatch
):
    managed = tmp_path / "table.csv"
    original = b"old-complete"
    managed.write_bytes(original)
    _write_sidecar(tmp_path, ["table.csv"])

    def bad_archive(url, dest, **kwargs):
        _write_download_bytes(dest, b"not-a-zip")
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
    )["managed_files"] == _owned_map(tmp_path, ["table.csv"])


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
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", archive_download)
    result = _download.download_candidate({
        "cand_id": "source:1",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path), max_bytes=10)

    assert managed.read_bytes() == original
    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": "nested/table.csv",
        "reason": "archive member exceeds per-member cap",
        "limit": 10,
        "declared_size": 100,
    }]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == _owned_map(tmp_path, ["table.csv"])
    assert not list(tmp_path.glob(".paperconan-archive-*"))


def test_sidecar_commit_failure_rolls_back_new_direct_output(
    tmp_path, monkeypatch
):
    old = tmp_path / "old.csv"
    old.write_text("old", encoding="utf-8")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")
    original_sidecar = (
        tmp_path / _download.SOURCE_SIDECAR
    ).read_bytes()

    def stub_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new")
        return {"ok": True, "path": dest}

    link = _download.os.link

    def fail_sidecar_link(src, dest, *args, **kwargs):
        if _is_staged_sidecar_publication(src, dest, kwargs):
            raise OSError("sidecar commit failed")
        return link(src, dest, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(_download.os, "link", fail_sidecar_link)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    assert result["downloaded"] == []
    assert result["skipped"] == [
        _sidecar_commit_skip("initial", "sidecar commit failed")
    ]
    assert old.read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "new.csv").exists()
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
        _write_download_bytes(dest, b"new-complete")
        return {"ok": True, "path": dest}

    identity_bound_remove = _download._identity_bound_remove

    def fail_old_remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        if name == "old.csv":
            raise OSError("cleanup blocked")
        return identity_bound_remove(
            directory_fd,
            name,
            descriptor,
            is_directory=is_directory,
        )

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        fail_old_remove,
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
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload.pop("downloads") == [{
        "asset_type": "tabular",
        "content_type": None,
        "file": "new.csv",
        "size": len(b"new-complete"),
        "source_url": "https://x/new.csv",
    }]
    assert payload == {
        "cand_id": "source:1",
        "doi": None,
        "managed_files": _owned_map(
            tmp_path, ["new.csv", "old.csv"]
        ),
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
        _write_download_bytes(dest, b"new-complete")
        return {"ok": True, "path": dest}

    identity_bound_remove = _download._identity_bound_remove

    def fail_old_remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        if name == "old.csv":
            raise OSError("cleanup blocked")
        return identity_bound_remove(
            directory_fd,
            name,
            descriptor,
            is_directory=is_directory,
        )

    lexists = _download.os.path.lexists

    def unreliable_lexists(path):
        if Path(path).name != "old.csv":
            return lexists(path)
        if probe_mode == "false":
            return False
        raise OSError("existence probe unavailable")

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        fail_old_remove,
    )
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
    ] == _owned_map(tmp_path, ["new.csv", "old.csv"])


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
        _write_download_bytes(dest, b"new-complete")
        return {"ok": True, "path": dest}

    identity_bound_remove = _download._identity_bound_remove

    def fail_residual_remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        if name == "residual.csv":
            raise OSError("cleanup blocked")
        return identity_bound_remove(
            directory_fd,
            name,
            descriptor,
            is_directory=is_directory,
        )

    link = _download.os.link
    sidecar_links = 0

    def fail_narrowing_link(src, dest, *args, **kwargs):
        nonlocal sidecar_links
        if _is_staged_sidecar_publication(src, dest, kwargs):
            sidecar_links += 1
            if sidecar_links == 2:
                raise OSError("narrowing commit failed")
        return link(src, dest, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", stub_download)
    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        fail_residual_remove,
    )
    monkeypatch.setattr(_download.os, "link", fail_narrowing_link)
    result = _download.download_candidate(
        _candidate("new.csv", "https://x/new.csv"),
        str(tmp_path),
    )

    new = tmp_path / "new.csv"
    assert new.read_bytes() == b"new-complete"
    assert removed.read_bytes() == b"remove-me"
    assert residual.read_bytes() == b"keep-owned"
    assert result["downloaded"] == [str(new)]
    assert result["skipped"] == [
        {
            "name": "residual.csv",
            "reason": "could not remove managed file",
        },
        _sidecar_commit_skip(
            "cleanup_narrowing", "narrowing commit failed"
        ),
    ]
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == _owned_map(
        tmp_path, ["new.csv", "removed.csv", "residual.csv"]
    )
    assert not list(tmp_path.glob(f".{_download.SOURCE_SIDECAR}.*.part"))


def test_unmanaged_direct_target_is_preserved(tmp_path, monkeypatch):
    user = tmp_path / "table.csv"
    user.write_text("user", encoding="utf-8")

    def stub_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"managed")
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )
    assert user.read_text(encoding="utf-8") == "user"
    managed = [Path(path).name for path in result["downloaded"]]
    assert len(managed) == 1
    assert managed[0].startswith("table-")


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
        _write_download_bytes(dest, b"managed")
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
        _write_download_bytes(dest, source_bytes)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", stub_download)
    result = _download.download_candidate(
        _candidate(reserved_name, "https://x/source"),
        str(tmp_path),
    )

    if reserved_name.lower() == _download.SOURCE_SIDECAR:
        assert result["downloaded"] == []
        assert result["skipped"] == [{
            "name": reserved_name,
            "reason": "reserved provenance sidecar basename",
        }]
        sidecar = json.loads(
            (tmp_path / _download.SOURCE_SIDECAR).read_text(
                encoding="utf-8"
            )
        )
        assert sidecar["managed_files"] == {}
        return
    assert result["skipped"] == []
    assert len(result["downloaded"]) == 1
    downloaded = Path(result["downloaded"][0])
    assert not _is_tool_reserved_name(downloaded.name)
    assert downloaded.read_bytes() == source_bytes
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["cand_id"] == "source:1"
    assert payload["managed_files"] == {
        downloaded.name: _owned_entry(source_bytes),
    }


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
        _write_download_bytes(dest, payload)
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

    if reserved_name.lower() == _download.SOURCE_SIDECAR:
        archive_name = (
            "supp.zip"
            if archive_kind == "zip"
            else "supp.tar.gz"
        )
        assert result["downloaded"] == []
        assert result["skipped"] == [{
            "name": archive_name,
            "reason": "reserved provenance sidecar basename",
        }]
        sidecar = json.loads(
            (tmp_path / _download.SOURCE_SIDECAR).read_text(
                encoding="utf-8"
            )
        )
        assert sidecar["managed_files"] == {}
        return
    assert result["skipped"] == []
    assert len(result["downloaded"]) == 1
    downloaded = Path(result["downloaded"][0])
    assert not _is_tool_reserved_name(downloaded.name)
    assert downloaded.read_bytes() == source_bytes
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["cand_id"] == "source:1"
    assert sidecar_payload["managed_files"] == {
        downloaded.name: _owned_entry(source_bytes),
    }
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
    managed_files = {
        "paperconan_source.json": _owned_entry(b"invalid"),
        "PAPERCONAN_SOURCE.JSON": _owned_entry(b"invalid"),
        archive_temp.name: _owned_entry(archive_temp.read_bytes()),
        ".paperconan-archive-other.csv": _owned_entry(b"invalid"),
        sidecar_temp.name: _owned_entry(sidecar_temp.read_bytes()),
        ".paperconan_source.json.other.part": _owned_entry(b"invalid"),
        safe.name: _owned_entry(safe.read_bytes()),
    }
    _write_sidecar(
        tmp_path,
        managed_files,
        doi="10.x/example",
    )
    sidecar = tmp_path / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()

    assert _download._read_source_sidecar(str(tmp_path)) == {
        "doi": "10.x/example",
        "managed_files": {
            "safe.csv": _owned_entry(b"managed"),
        },
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
        _write_download_bytes(dest, payload)
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
def test_persistent_archive_cleanup_after_success_is_visible_and_unmanaged(
    tmp_path, monkeypatch, archive_kind
):
    payload = _archive_payload(
        archive_kind,
        [("nested/table.csv", b"a\n1\n")],
    )

    def archive_download(url, dest, **kwargs):
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest}

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_archive_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-archive-"):
            cleanup_attempts.append(tmp_path / Path(path).name)
            raise PermissionError("private cleanup detail")
        return original_unlink(path, *args, **kwargs)

    prepared = []
    original_prepare = _download._ManagedOutputJournal.prepare

    def record_prepare(journal, path):
        prepared.append(Path(path))
        return original_prepare(journal, path)

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download.os, "unlink", fail_archive_cleanup
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal, "prepare", record_prepare
    )

    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    assert result["downloaded"] == [str(tmp_path / "table.csv")]
    archive_name = (
        "supp.zip" if archive_kind == "zip" else "supp.tar.gz"
    )
    assert result["skipped"] == [{
        "name": archive_name,
        "reason": (
            "download staging cleanup incomplete: deletion failed"
        ),
    }]
    assert "private cleanup detail" not in repr(result["skipped"])
    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    transient = cleanup_attempts[0]
    assert transient.exists()
    assert transient not in map(Path, result["downloaded"])
    assert transient not in prepared
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == _owned_map(
        tmp_path, ["table.csv"]
    )
    assert transient.name not in sidecar["managed_files"]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_expected_archive_failure_keeps_primary_and_cleanup_records(
    tmp_path, monkeypatch, archive_kind
):
    def unavailable_archive(url, dest, **kwargs):
        return {
            "ok": False,
            "path": dest,
            "skipped_reason": "archive unavailable",
        }

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_archive_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-archive-"):
            cleanup_attempts.append(tmp_path / Path(path).name)
            raise PermissionError("private cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download, "download_file", unavailable_archive
    )
    monkeypatch.setattr(
        _download.os, "unlink", fail_archive_cleanup
    )

    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    archive_name = (
        "supp.zip" if archive_kind == "zip" else "supp.tar.gz"
    )
    assert result["downloaded"] == []
    assert result["skipped"] == [
        {
            "name": archive_name,
            "reason": "archive unavailable",
        },
        {
            "name": archive_name,
            "reason": (
                "download staging cleanup incomplete: deletion failed"
            ),
        },
    ]
    assert "private cleanup detail" not in repr(result["skipped"])
    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    assert cleanup_attempts[0].exists()
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {}


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    "primary_type",
    [RuntimeError, _UnexpectedArchiveSignal],
)
def test_unexpected_archive_failure_reports_cleanup_without_masking_primary(
    tmp_path, monkeypatch, archive_kind, primary_type
):
    primary = primary_type("unexpected archive operation")

    def fail_archive_operation(url, dest, **kwargs):
        raise primary

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_archive_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-archive-"):
            cleanup_attempts.append(tmp_path / Path(path).name)
            raise PermissionError("persistent archive cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download, "download_file", fail_archive_operation
    )
    monkeypatch.setattr(
        _download.os, "unlink", fail_archive_cleanup
    )
    archive = _archive_fields(archive_kind)
    downloaded = []
    skipped = []

    with pytest.raises(primary_type) as raised:
        with _download._pinned_output_directory(tmp_path) as output:
            if archive_kind == "zip":
                _download._download_supplementary_archive(
                    archive["supplementary_archive"],
                    output,
                    downloaded,
                    skipped,
                    _download._DEFAULT_MAX,
                )
            else:
                _download._download_oa_package(
                    archive["oa_package"],
                    output,
                    downloaded,
                    skipped,
                    _download._DEFAULT_MAX,
                )

    assert raised.value is primary
    assert downloaded == []
    archive_name = (
        "supp.zip" if archive_kind == "zip" else "supp.tar.gz"
    )
    assert skipped == [{
        "name": archive_name,
        "reason": (
            "download staging cleanup incomplete: deletion failed"
        ),
    }]
    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    assert cleanup_attempts[0].exists()
    assert "persistent archive cleanup failure" not in repr(skipped)


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_download_reports_staging_cleanup_without_private_path(
    tmp_path, monkeypatch, archive_kind
):
    class BrokenArchiveResponse(io.BytesIO):
        headers = {"Content-Type": "application/octet-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def info(self):
            return self.headers

        def geturl(self):
            return "https://x/archive"

        def read(self, size=-1):
            raise OSError("private archive stream detail")

    network_attempts = 0

    def urlopen(req, timeout=None):
        nonlocal network_attempts
        network_attempts += 1
        return BrokenArchiveResponse(b"partial")

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_part_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-archive-"):
            cleanup_attempts.append(tmp_path / Path(path).name)
            raise PermissionError("private archive cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download._http, "open_http", urlopen
    )
    monkeypatch.setattr(
        _download.os, "unlink", fail_part_cleanup
    )
    monkeypatch.setattr(_download.time, "sleep", lambda *_: None)

    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    assert network_attempts == 3
    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    orphan = cleanup_attempts[0]
    assert orphan.exists()
    assert result["downloaded"] == []
    assert result["skipped"][-1]["reason"] == (
        "download staging cleanup incomplete: deletion failed"
    )
    assert str(tmp_path) not in repr(result["skipped"])
    assert orphan.name not in repr(result["skipped"])
    assert "private archive cleanup detail" not in repr(
        result["skipped"]
    )
    assert (
        tmp_path / _download.SOURCE_SIDECAR
    ).exists()


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_member_cleanup_failure_is_reported_without_data_loss(
    tmp_path, monkeypatch, archive_kind
):
    old = tmp_path / "old.csv"
    old.write_bytes(b"old-complete")
    _write_sidecar(tmp_path, ["old.csv"], doi="10.x/old")
    payload = _archive_payload(
        archive_kind,
        [("nested/old.csv", b"member-data")],
    )

    def archive_download(url, dest, **kwargs):
        _write_download_bytes(dest, payload)
        return {
            "ok": True,
            "path": dest,
            "size": len(payload),
        }

    class BrokenMemberStream:
        def __init__(self, inner):
            self._inner = inner

        def read(self, size=-1):
            raise OSError("private member stream detail")

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    monkeypatch.setattr(_download, "download_file", archive_download)
    if archive_kind == "zip":
        open_member = _download.zipfile.ZipFile.open

        def open_broken(archive, member, *args, **kwargs):
            return BrokenMemberStream(
                open_member(archive, member, *args, **kwargs)
            )

        monkeypatch.setattr(
            _download.zipfile.ZipFile, "open", open_broken
        )
    else:
        open_member = _download.tarfile.TarFile.extractfile

        def open_broken(archive, member, *args, **kwargs):
            return BrokenMemberStream(
                open_member(archive, member, *args, **kwargs)
            )

        monkeypatch.setattr(
            _download.tarfile.TarFile,
            "extractfile",
            open_broken,
        )

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_part_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-member-"):
            cleanup_attempts.append(tmp_path / Path(path).name)
            raise PermissionError("private member cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download.os, "unlink", fail_part_cleanup
    )

    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    orphan = cleanup_attempts[0]
    assert orphan.exists()
    assert result["downloaded"] == []
    assert result["skipped"][-1] == {
        "name": "nested/old.csv",
        "reason": (
            "download staging cleanup incomplete: deletion failed"
        ),
    }
    assert "private member cleanup detail" not in repr(
        result["skipped"]
    )
    assert old.read_bytes() == b"old-complete"
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(tmp_path, ["old.csv"])


@pytest.mark.parametrize("persistent_restore", [False, True])
def test_download_staging_cleanup_failure_never_mutates_managed_output(
    tmp_path, monkeypatch, persistent_restore
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_sidecar(out_dir, [output.name], doi="10.x/old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()

    class BrokenSourceResponse(io.BytesIO):
        headers = {"Content-Type": "text/csv"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def info(self):
            return self.headers

        def geturl(self):
            return "https://x/table.csv"

        def read(self, size=-1):
            raise OSError("private source stream detail")

    network_attempts = 0

    def urlopen(req, timeout=None):
        nonlocal network_attempts
        network_attempts += 1
        return BrokenSourceResponse(b"partial")

    original_unlink = _download.os.unlink
    cleanup_attempts = []

    def fail_part_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".paperconan-download-"):
            cleanup_attempts.append(out_dir / Path(path).name)
            raise PermissionError("private source cleanup detail")
        return original_unlink(path, *args, **kwargs)

    original_replace = _download.os.replace
    restore_attempts = []

    def fail_restore(src, dest, *args, **kwargs):
        is_restore = (
            Path(src).parent.name.startswith(
                ".paperconan-output-rollback-"
            )
            and kwargs.get("dst_dir_fd") is not None
            and Path(dest).name == output.name
        )
        if is_restore and (
            persistent_restore or not restore_attempts
        ):
            restore_attempts.append(
                (os.fspath(src), os.fspath(dest))
            )
            raise PermissionError("private managed restore detail")
        return original_replace(src, dest, *args, **kwargs)

    monkeypatch.setattr(
        _download._http, "open_http", urlopen
    )
    monkeypatch.setattr(
        _download.os, "unlink", fail_part_cleanup
    )
    monkeypatch.setattr(_download.os, "replace", fail_restore)
    monkeypatch.setattr(_download.time, "sleep", lambda *_: None)

    result = _download.download_candidate(
        _candidate(output.name, "https://x/table.csv"),
        str(out_dir),
    )

    assert network_attempts == 3
    assert len(cleanup_attempts) == 1
    assert len(set(cleanup_attempts)) == 1
    assert restore_attempts == []
    assert result["downloaded"] == []
    assert result["skipped"][-1]["reason"] == (
        "download staging cleanup incomplete: deletion failed"
    )
    assert "private source cleanup detail" not in repr(
        result["skipped"]
    )
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == {
        output.name: _owned_entry(b"old-output"),
    }
    assert output.read_bytes() == b"old-output"
    assert not list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )


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
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest}

    publish_download_staging = _download._publish_download_staging

    def fail_old_member(*args, output_name, **kwargs):
        if output_name == "old.csv":
            raise OSError("member write failed")
        return publish_download_staging(
            *args,
            output_name=output_name,
            **kwargs,
        )

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download, "_publish_download_staging", fail_old_member
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
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    archive_url = (
        "https://x/supp.zip"
        if archive_kind == "zip"
        else "https://x/supp.tar.gz"
    )
    assert payload.pop("downloads") == [{
        "asset_type": "tabular",
        "content_type": None,
        "file": "new.csv",
        "size": len(b"new-complete"),
        "source_url": archive_url,
    }]
    assert payload == {
        "cand_id": "source:1",
        "doi": "10.x/new",
        "managed_files": _owned_map(
            tmp_path, ["new.csv", "old.csv"]
        ),
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
        _write_download_bytes(dest, payload)
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
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    archive_url = (
        "https://x/supp.zip"
        if archive_kind == "zip"
        else "https://x/supp.tar.gz"
    )
    assert payload.pop("downloads") == [{
        "asset_type": "tabular",
        "content_type": None,
        "file": "table.csv",
        "size": len(b"new-complete"),
        "source_url": archive_url,
    }]
    assert payload == {
        "cand_id": "source:1",
        "doi": "10.x/new",
        "managed_files": _owned_map(tmp_path, ["table.csv"]),
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
        _write_download_bytes(dest, payload)
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
        "managed_files": _owned_map(tmp_path, ["table.csv"]),
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


def _real_sidecar_size(tmp_path, cand, managed_files):
    probe = tmp_path / "sidecar-probe"
    probe.mkdir(exist_ok=True)
    assert _download._write_source_sidecar(
        cand, str(probe), managed_files
    )
    return (probe / _download.SOURCE_SIDECAR).stat().st_size


def _fail_final_sidecar_commit(monkeypatch):
    link = _download.os.link
    failures = []

    def fail_sidecar_link(src, dest, *args, **kwargs):
        if _is_staged_sidecar_publication(src, dest, kwargs):
            failures.append(dest)
            raise OSError("sidecar commit failed")
        return link(src, dest, *args, **kwargs)

    monkeypatch.setattr(_download.os, "link", fail_sidecar_link)
    return failures


@pytest.mark.parametrize("channel", ["direct", "zip", "tar"])
@pytest.mark.parametrize("destination_state", ["replacement", "new"])
def test_final_sidecar_failure_rolls_back_every_accepted_output(
    tmp_path, monkeypatch, channel, destination_state
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    stale.write_bytes(b"stale")
    managed_files = ["stale.csv"]
    output_name = "table.csv"
    output = out_dir / output_name
    if destination_state == "replacement":
        output.write_bytes(b"old-output")
        managed_files.append(output_name)
    _write_sidecar(out_dir, managed_files, doi="10.x/old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    body = b"new-output"

    if channel == "direct":
        cand = _candidate(output_name, "https://x/table.csv")

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, body)
            return {"ok": True, "path": dest, "size": len(body)}

    else:
        payload = _archive_payload(
            channel, [(f"nested/{output_name}", body)]
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

    final_managed = sorted(set(managed_files) | {output_name})
    assert _download._source_sidecar_replacement_delta(
        cand, str(out_dir), final_managed
    ) >= 0

    monkeypatch.setattr(_download, "download_file", source_download)
    failures = _fail_final_sidecar_commit(monkeypatch)

    result = _download.download_candidate(cand, str(out_dir))

    assert len(failures) == 1
    assert result["downloaded"] == []
    assert result["skipped"] == [
        _sidecar_commit_skip("initial", "sidecar commit failed")
    ]
    if destination_state == "replacement":
        assert output.read_bytes() == b"old-output"
    else:
        assert not output.exists()
    assert stale.read_bytes() == b"stale"
    assert sidecar.read_bytes() == original_sidecar
    assert not list(out_dir.glob(".paperconan-output-rollback-*"))
    assert not list(tmp_path.glob(".paperconan-output-rollback-*"))
    assert not list(out_dir.glob(".paperconan-archive-*"))


@pytest.mark.parametrize("destination_state", ["new", "replacement"])
def test_sidecar_failure_preserves_in_place_modified_published_output(
    tmp_path, monkeypatch, destination_state
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    old_bytes = b"old-output"
    if destination_state == "replacement":
        output.write_bytes(old_bytes)
        managed_files = [output.name]
    else:
        stale = out_dir / "stale.csv"
        stale.write_bytes(b"stale")
        managed_files = [stale.name]
    _write_sidecar(out_dir, managed_files, doi="10.x/old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    published = b"new-output"
    modified = b"tampered!!"
    assert len(modified) == len(published)
    observed_identities = []

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, published)
        return {
            "ok": True,
            "path": dest,
            "size": len(published),
        }

    def modify_then_fail(*_args, **_kwargs):
        before = output.stat()
        with output.open("r+b") as stream:
            stream.seek(0)
            stream.write(modified)
            stream.truncate(len(modified))
            stream.flush()
            os.fsync(stream.fileno())
        after = output.stat()
        observed_identities.append((
            (before.st_dev, before.st_ino),
            (after.st_dev, after.st_ino),
        ))
        raise OSError("sidecar commit failed")

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        modify_then_fail,
    )

    with pytest.raises(
        _download._ManagedOutputRollbackError,
        match="could not restore 1 managed output",
    ):
        _download.download_candidate(
            _candidate(output.name, "https://x/table.csv"),
            str(out_dir),
        )

    assert observed_identities
    assert all(before == after for before, after in observed_identities)
    assert output.read_bytes() == modified
    assert sidecar.read_bytes() == original_sidecar
    rollback_dirs = list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    if destination_state == "replacement":
        assert len(rollback_dirs) == 1
        backups = list(rollback_dirs[0].iterdir())
        assert len(backups) == 1
        assert backups[0].read_bytes() == old_bytes
    else:
        assert rollback_dirs == []


@pytest.mark.parametrize(
    "change",
    ["removed", "equal-size-modified"],
)
def test_output_commit_rejects_changed_published_replacement(
    tmp_path, monkeypatch, change
):
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    modified = b"bad-output"
    assert len(modified) == len(published)
    output.write_bytes(original)
    _write_owned_sidecar(out_dir, [output.name], doi="10.x/old")

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, published)
        return {"ok": True, "path": dest, "size": len(published)}

    real_write_sidecar = _download._write_source_sidecar

    def change_after_sidecar(*args, **kwargs):
        result = real_write_sidecar(*args, **kwargs)
        if change == "removed":
            output.unlink()
        else:
            with output.open("r+b") as stream:
                stream.write(modified)
                stream.flush()
                os.fsync(stream.fileno())
        return result

    real_commit = _download._ManagedOutputJournal.commit
    output_commit_calls = []

    def track_commit(journal):
        if any(
            entry["name"] == output.name
            for entry in journal._entries.values()
            if type(entry) is dict
        ):
            output_commit_calls.append(journal)
        return real_commit(journal)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download, "_write_source_sidecar", change_after_sidecar
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        track_commit,
    )

    with pytest.raises(
        _download._ManagedOutputRecoveryRequiredError,
        match="changed before journal commit",
    ):
        _download.download_candidate(
            _candidate(output.name, "https://x/table.csv"),
            str(out_dir),
        )

    assert len(output_commit_calls) == 1
    if change == "removed":
        assert not output.exists()
    else:
        assert output.read_bytes() == modified
    rollback_dirs = list(set(
        tmp_path.glob(".paperconan-output-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


@pytest.mark.parametrize(
    "change",
    ["equal-size-modified", "replaced"],
)
def test_output_commit_rejects_changed_rollback_entry(
    tmp_path, monkeypatch, change
):
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    modified = b"bad-output"
    assert len(modified) == len(original)
    output.write_bytes(original)
    _write_owned_sidecar(out_dir, [output.name], doi="10.x/old")
    observed_backups = []

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, published)
        return {"ok": True, "path": dest, "size": len(published)}

    real_write_sidecar = _download._write_source_sidecar

    def change_backup_after_sidecar(*args, **kwargs):
        result = real_write_sidecar(*args, **kwargs)
        rollback_dirs = list(set(
            tmp_path.glob(".paperconan-output-rollback-*")
        ) - rollback_dirs_before)
        assert len(rollback_dirs) == 1
        backups = list(rollback_dirs[0].iterdir())
        assert len(backups) == 1
        backup = backups[0]
        if change == "equal-size-modified":
            with backup.open("r+b") as stream:
                stream.write(modified)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            backup.unlink()
            backup.write_bytes(original)
        observed_backups.append(backup)
        return result

    real_commit = _download._ManagedOutputJournal.commit
    output_commit_calls = []

    def track_commit(journal):
        if any(
            entry["name"] == output.name
            for entry in journal._entries.values()
            if type(entry) is dict
        ):
            output_commit_calls.append(journal)
        return real_commit(journal)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        change_backup_after_sidecar,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        track_commit,
    )

    with pytest.raises(
        _download._ManagedOutputRecoveryRequiredError,
        match="changed before journal commit",
    ):
        _download.download_candidate(
            _candidate(output.name, "https://x/table.csv"),
            str(out_dir),
        )

    assert len(output_commit_calls) == 1
    assert output.read_bytes() == published
    assert len(observed_backups) == 1
    assert observed_backups[0].exists()
    assert observed_backups[0].read_bytes() == (
        modified if change == "equal-size-modified" else original
    )


def test_cleanup_commit_rejects_reappeared_staged_removal(
    tmp_path, monkeypatch
):
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    original = b"old-output"
    concurrent = b"user-output"
    stale.write_bytes(original)
    _write_owned_sidecar(out_dir, [stale.name], doi="10.x/old")

    real_write_sidecar = _download._write_source_sidecar
    sidecar_writes = 0

    def recreate_after_narrowing(*args, **kwargs):
        nonlocal sidecar_writes
        result = real_write_sidecar(*args, **kwargs)
        sidecar_writes += 1
        if sidecar_writes == 2:
            stale.write_bytes(concurrent)
        return result

    real_commit = _download._ManagedOutputJournal.commit
    cleanup_commit_calls = []

    def track_commit(journal):
        if any(
            entry["name"] == stale.name
            for entry in journal._entries.values()
            if type(entry) is dict
        ):
            cleanup_commit_calls.append(journal)
        return real_commit(journal)

    monkeypatch.setattr(
        _download, "_write_source_sidecar", recreate_after_narrowing
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        track_commit,
    )

    with pytest.raises(
        _download._ManagedOutputRecoveryRequiredError,
        match="changed before journal commit",
    ):
        _download.download_candidate(
            {
                "cand_id": "source:1",
                "source": "source",
                "tabular_files": [],
            },
            str(out_dir),
        )

    assert sidecar_writes == 2
    assert len(cleanup_commit_calls) == 1
    assert stale.read_bytes() == concurrent
    rollback_dirs = list(set(
        tmp_path.glob(".paperconan-output-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == {}


def test_commit_rechecks_published_output_after_final_backup_verification(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    modified = b"bad-output"
    assert len(modified) == len(published)
    output.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                output,
                expected=_owned_entry(original),
            )
            output.write_bytes(published)
            state = output.stat()
            journal.bind_published(
                output,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_entry = journal._verify_backup_entry
            verify_calls = []

            def modify_after_final_verification(
                backup_name, expected
            ):
                verify_backup_entry(backup_name, expected)
                verify_calls.append(backup_name)
                if len(verify_calls) == 2:
                    with output.open("r+b") as stream:
                        stream.write(modified)
                        stream.flush()
                        os.fsync(stream.fileno())

            monkeypatch.setattr(
                journal,
                "_verify_backup_entry",
                modify_after_final_verification,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ) as caught:
                journal.commit()

            assert verify_calls == ["0", "0"]
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert output.read_bytes() == modified
            assert backup.read_bytes() == original
            assert backup in map(Path, caught.value.recovery_paths)
        finally:
            journal.close()


def test_commit_rechecks_staged_removal_after_final_backup_verification(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    original = b"old-output"
    concurrent = b"user-output"
    stale.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.stage_removal(
                stale,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_entry = journal._verify_backup_entry
            verify_calls = []

            def recreate_after_final_verification(
                backup_name, expected
            ):
                verify_backup_entry(backup_name, expected)
                verify_calls.append(backup_name)
                if len(verify_calls) == 2:
                    stale.write_bytes(concurrent)

            monkeypatch.setattr(
                journal,
                "_verify_backup_entry",
                recreate_after_final_verification,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ) as caught:
                journal.commit()

            assert verify_calls == ["0", "0"]
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert stale.read_bytes() == concurrent
            assert backup.read_bytes() == original
            assert backup in map(Path, caught.value.recovery_paths)
        finally:
            journal.close()


def test_commit_final_helper_rejects_published_mutation_before_unlink(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    modified = b"bad-output"
    assert len(modified) == len(published)
    output.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                output,
                expected=_owned_entry(original),
            )
            output.write_bytes(published)
            state = output.stat()
            journal.bind_published(
                output,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            final_unlink = (
                journal._unlink_backup_after_visible_commit_check
            )
            helper_calls = []

            def mutate_inside_final_helper(backup_name, entry):
                helper_calls.append(backup_name)
                with output.open("r+b") as stream:
                    stream.write(modified)
                    stream.flush()
                    os.fsync(stream.fileno())
                return final_unlink(backup_name, entry)

            monkeypatch.setattr(
                journal,
                "_unlink_backup_after_visible_commit_check",
                mutate_inside_final_helper,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ) as caught:
                journal.commit()

            assert helper_calls == ["0"]
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert output.read_bytes() == modified
            assert backup.read_bytes() == original
            assert backup in map(Path, caught.value.recovery_paths)
        finally:
            journal.close()


def test_commit_final_helper_rejects_staged_removal_recreation_before_unlink(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    original = b"old-output"
    concurrent = b"user-output"
    stale.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.stage_removal(
                stale,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            final_unlink = (
                journal._unlink_backup_after_visible_commit_check
            )
            helper_calls = []

            def recreate_inside_final_helper(backup_name, entry):
                helper_calls.append(backup_name)
                stale.write_bytes(concurrent)
                return final_unlink(backup_name, entry)

            monkeypatch.setattr(
                journal,
                "_unlink_backup_after_visible_commit_check",
                recreate_inside_final_helper,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ) as caught:
                journal.commit()

            assert helper_calls == ["0"]
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert stale.read_bytes() == concurrent
            assert backup.read_bytes() == original
            assert backup in map(Path, caught.value.recovery_paths)
        finally:
            journal.close()


def test_commit_accounts_backup_unlink_before_later_verification(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    output.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                output,
                expected=_owned_entry(original),
            )
            output.write_bytes(published)
            state = output.stat()
            journal.bind_published(
                output,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_dir = journal._verify_backup_dir
            stale_verifications = []

            def reject_stale_entry_after_unlink():
                verify_backup_dir()
                if (
                    not backup.exists()
                    and str(output) in journal._entries
                ):
                    stale_verifications.append(str(backup))
                    raise _download._UnstableRegularFileError(
                        "injected verification after completed unlink"
                    )

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                reject_stale_entry_after_unlink,
            )

            assert journal.commit() == []

            assert stale_verifications == []
            assert journal._state.value == "COMMITTED"
            assert journal._entries == {}
            assert journal.recovery_paths() == ()
            assert output.read_bytes() == published
            assert not backup.exists()
        finally:
            journal.close()


def test_discard_accounts_backup_unlink_before_later_verification(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    output.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                output,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_dir = journal._verify_backup_dir
            stale_verifications = []

            def reject_stale_entry_after_unlink():
                verify_backup_dir()
                if (
                    not backup.exists()
                    and str(output) in journal._entries
                ):
                    stale_verifications.append(str(backup))
                    raise _download._UnstableRegularFileError(
                        "injected verification after completed unlink"
                    )

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                reject_stale_entry_after_unlink,
            )

            journal.discard(output)

            assert stale_verifications == []
            assert journal._state.value == "OPEN"
            assert journal._entries == {}
            assert journal.recovery_paths() == ()
            assert not output.exists()
            assert not backup.exists()
        finally:
            journal.close()


def test_created_output_rollback_accounts_unlink_before_later_verification(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    created = out_dir / "table.csv"
    payload = b"new-output"
    created.write_bytes(payload)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            state = created.stat()
            journal.record_created(
                created,
                (state.st_dev, state.st_ino),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
            verify_output = pinned.verify
            stale_verifications = []

            def reject_stale_entry_after_unlink():
                verify_output()
                if (
                    not created.exists()
                    and str(created) in journal._entries
                ):
                    stale_verifications.append(str(created))
                    raise ValueError(
                        "injected verification after completed unlink"
                    )

            monkeypatch.setattr(
                pinned,
                "verify",
                reject_stale_entry_after_unlink,
            )

            assert journal.rollback() == {str(created)}

            assert stale_verifications == []
            assert journal._state.value == "OPEN"
            assert journal._entries == {}
            assert journal.recovery_paths() == ()
            assert not created.exists()
        finally:
            journal.close()


def test_created_output_rollback_final_helper_preserves_replacement(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    created = out_dir / "table.csv"
    payload = b"new-output"
    unrelated = b"user-output"
    created.write_bytes(payload)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            state = created.stat()
            journal.record_created(
                created,
                (state.st_dev, state.st_ino),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
            final_unlink = (
                journal._unlink_created_after_final_canonical_check
            )
            helper_calls = []

            def replace_inside_final_helper(name, expected):
                helper_calls.append(name)
                created.unlink()
                created.write_bytes(unrelated)
                return final_unlink(name, expected)

            monkeypatch.setattr(
                journal,
                "_unlink_created_after_final_canonical_check",
                replace_inside_final_helper,
            )

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match="could not restore 1 managed output",
            ):
                journal.rollback()

            assert helper_calls == [created.name]
            assert created.read_bytes() == unrelated
            assert str(created) in journal._entries
        finally:
            journal.close()


def test_replacement_rollback_final_helper_preserves_replacement(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    unrelated = b"user-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            final_restore = (
                journal._restore_backup_over_published_after_final_check
            )
            helper_calls = []

            def replace_inside_final_helper(
                backup_name,
                output_name,
                expected,
            ):
                helper_calls.append((backup_name, output_name))
                managed.unlink()
                managed.write_bytes(unrelated)
                return final_restore(
                    backup_name,
                    output_name,
                    expected,
                )

            monkeypatch.setattr(
                journal,
                "_restore_backup_over_published_after_final_check",
                replace_inside_final_helper,
            )

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match="could not restore 1 managed output",
            ):
                journal.rollback()

            assert helper_calls == [("0", managed.name)]
            assert managed.read_bytes() == unrelated
            assert backup.read_bytes() == original
            assert str(managed) in journal._entries
        finally:
            journal.close()


def test_staged_removal_rollback_final_helper_preserves_recreated_path(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    original = b"old-output"
    unrelated = b"user-output"
    stale.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.stage_removal(
                stale,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            final_restore = (
                journal._restore_backup_into_absent_path_after_final_check
            )
            helper_calls = []

            def recreate_inside_final_helper(backup_name, output_name):
                helper_calls.append((backup_name, output_name))
                stale.write_bytes(unrelated)
                return final_restore(backup_name, output_name)

            monkeypatch.setattr(
                journal,
                "_restore_backup_into_absent_path_after_final_check",
                recreate_inside_final_helper,
            )

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match="could not restore 1 managed output",
            ):
                journal.rollback()

            assert helper_calls == [("0", stale.name)]
            assert stale.read_bytes() == unrelated
            assert backup.read_bytes() == original
            assert str(stale) in journal._entries
        finally:
            journal.close()


def test_journal_cleanup_error_preserves_successful_sidecar_commit(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_sidecar(out_dir, ["table.csv"], doi="10.x/old")

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_unlink = _download.os.unlink
    failures = []

    def fail_first_backup_remove(path, *args, **kwargs):
        is_backup = (
            kwargs.get("dir_fd") is not None
            and Path(path).name.isdigit()
        )
        if (
            is_backup
            and not failures
        ):
            failures.append(os.fspath(path))
            raise PermissionError("injected journal cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os, "unlink", fail_first_backup_remove
    )

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(out_dir),
    )

    assert len(failures) == 1
    assert result["downloaded"] == [str(output)]
    assert output.read_bytes() == b"new-output"
    sidecar = json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == _owned_map(
        out_dir, ["table.csv"]
    )
    assert not list(tmp_path.glob(".paperconan-output-rollback-*"))


def test_persistent_post_commit_backup_cleanup_is_reported_and_recoverable(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_sidecar(out_dir, ["table.csv"], doi="10.x/old")

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_unlink = _download.os.unlink
    attempts = []

    def fail_backup_remove(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name.isdigit()
        ):
            attempts.append(os.fspath(path))
            raise PermissionError("persistent backup cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os, "unlink", fail_backup_remove
    )

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(out_dir),
    )

    rollback_dirs = list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    assert len(attempts) == 2
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old-output"
    assert output.read_bytes() == b"new-output"
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(out_dir, ["table.csv"])
    assert result["downloaded"] == [str(output)]
    assert result["skipped"] == [{
        "name": backups[0].name,
        "reason": "post-commit cleanup pending",
        "path": str(backups[0]),
    }]


def test_initial_sidecar_prior_copy_cleanup_pending_is_reported(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_owned_sidecar(
        out_dir,
        [output.name],
        cand_id="source:old",
    )

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_unlink = _download.os.unlink
    attempts = []

    def fail_prior_sidecar_remove(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name == "previous-0"
        ):
            attempts.append(os.fspath(path))
            raise PermissionError(
                "persistent prior sidecar cleanup failure"
            )
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os,
        "unlink",
        fail_prior_sidecar_remove,
    )

    result = _download.download_candidate(
        _candidate(output.name, "https://x/table.csv"),
        str(out_dir),
    )

    rollback_dirs = list(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    )
    assert len(attempts) == 2
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert output.read_bytes() == b"new-output"
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(out_dir, [output.name])
    assert result["downloaded"] == [str(output)]
    assert result["skipped"] == [{
        "name": backups[0].name,
        "reason": "post-commit cleanup pending",
        "path": str(backups[0]),
    }]


def test_cleanup_narrowing_sidecar_cleanup_pending_is_reported(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    stale.write_bytes(b"old-output")
    _write_owned_sidecar(
        out_dir,
        [stale.name],
        cand_id="source:old",
    )
    real_commit = _download._ManagedOutputJournal.commit
    real_unlink = _download.os.unlink
    sidecar_commits = 0
    fail_active = False
    attempts = []

    def track_sidecar_commit(journal):
        nonlocal sidecar_commits, fail_active
        if (
            journal._backup_prefix
            == ".paperconan-sidecar-rollback-"
        ):
            sidecar_commits += 1
            fail_active = sidecar_commits == 2
            try:
                return real_commit(journal)
            finally:
                fail_active = False
        return real_commit(journal)

    def fail_second_prior_sidecar_remove(path, *args, **kwargs):
        if (
            fail_active
            and kwargs.get("dir_fd") is not None
            and Path(path).name == "previous-0"
        ):
            attempts.append(os.fspath(path))
            raise PermissionError(
                "persistent narrowed sidecar cleanup failure"
            )
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        track_sidecar_commit,
    )
    monkeypatch.setattr(
        _download.os,
        "unlink",
        fail_second_prior_sidecar_remove,
    )

    result = _download.download_candidate(
        {
            "cand_id": "source:new",
            "source": "source",
            "tabular_files": [],
        },
        str(out_dir),
    )

    rollback_dirs = list(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    )
    assert sidecar_commits == 2
    assert len(attempts) == 2
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert not stale.exists()
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == {}
    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": backups[0].name,
        "reason": "post-commit cleanup pending",
        "path": str(backups[0]),
    }]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_standalone_archive_backup_cleanup_pending_is_reported(
    tmp_path, monkeypatch, archive_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    archive = tmp_path / (
        "source.zip" if archive_kind == "zip" else "source.tar.gz"
    )
    archive.write_bytes(_archive_payload(
        archive_kind,
        [("nested/table.csv", b"new-output")],
    ))
    real_unlink = _download.os.unlink
    attempts = []

    def fail_backup_remove(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name == "0"
        ):
            attempts.append(os.fspath(path))
            raise PermissionError(
                "persistent archive backup cleanup failure"
            )
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download.os,
        "unlink",
        fail_backup_remove,
    )

    if archive_kind == "zip":
        extracted = _download._extract_tabular_zip(
            str(archive),
            str(out_dir),
            reusable_names=[output.name],
        )
    else:
        extracted = _download._extract_tabular_tar(
            str(archive),
            str(out_dir),
            reusable_names=[output.name],
        )

    rollback_dirs = list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    assert len(attempts) == 2
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old-output"
    assert extracted == [str(output)]
    assert output.read_bytes() == b"new-output"
    assert extracted.skipped == [{
        "name": backups[0].name,
        "reason": "post-commit cleanup pending",
        "path": str(backups[0]),
    }]


@pytest.mark.parametrize(
    "journal_kind",
    ["output", "cleanup", "archive"],
)
def test_persistent_journal_cleanup_does_not_leak_fds_across_calls(
    tmp_path, monkeypatch, journal_kind
):
    real_unlink = _download.os.unlink

    def fail_backup_remove(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name.isdigit()
        ):
            raise PermissionError("persistent backup cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download.os, "unlink", fail_backup_remove
    )

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    monkeypatch.setattr(_download, "download_file", source_download)
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("table.csv", b"new-output")

    baseline = _open_fd_count()
    observed = []
    for index in range(3):
        out_dir = tmp_path / f"{journal_kind}-{index}" / "out"
        out_dir.mkdir(parents=True)
        output = out_dir / "table.csv"
        output.write_bytes(b"old-output")
        _write_sidecar(out_dir, [output.name], doi="10.x/old")

        if journal_kind == "output":
            result = _download.download_candidate(
                _candidate(
                    output.name,
                    "https://x/table.csv",
                ),
                str(out_dir),
            )
            assert result["downloaded"] == [str(output)]
        elif journal_kind == "cleanup":
            result = _download.download_candidate(
                {
                    "cand_id": "source:1",
                    "source": "source",
                    "tabular_files": [],
                },
                str(out_dir),
            )
            assert result["downloaded"] == []
        else:
            assert _download._extract_tabular_zip(
                str(archive),
                str(out_dir),
                reusable_names=[output.name],
            ) == [str(output)]

        assert list(out_dir.parent.glob(
            ".paperconan-output-rollback-*"
        ))
        observed.append(_open_fd_count())

    assert observed == [baseline, baseline, baseline]


def test_journal_close_is_idempotent_and_preserves_recovery_state(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)
    real_unlink = _download.os.unlink

    def fail_backup_remove(path, *args, **kwargs):
        if (
            kwargs.get("dir_fd") is not None
            and Path(path).name.isdigit()
        ):
            raise PermissionError("persistent backup cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        _download.os, "unlink", fail_backup_remove
    )

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        assert journal.prepare(
            managed,
            expected=_owned_entry(original),
        )
        pending = journal.commit()
        backup_dir = Path(journal._backup_dir)
        backup = backup_dir / "0"
        assert pending == [str(backup)]

        journal.close()
        journal.close()

        assert journal._backup_fd == -1
        assert journal._backup_parent_fd == -1
        assert backup.read_bytes() == original
        assert backup_dir.is_dir()


@pytest.mark.parametrize("persistent", [False, True])
def test_post_commit_directory_cleanup_retries_and_reports_path(
    tmp_path, monkeypatch, persistent
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_sidecar(out_dir, ["table.csv"], doi="10.x/old")

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_rmdir = _download.os.rmdir
    attempts = []

    def fail_directory_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(
            ".paperconan-output-rollback-"
        ) and (persistent or not attempts):
            attempts.append(os.fspath(path))
            raise PermissionError("rollback directory cleanup failure")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os, "rmdir", fail_directory_cleanup
    )

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(out_dir),
    )

    assert output.read_bytes() == b"new-output"
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(out_dir, ["table.csv"])
    rollback_dirs = list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )
    if persistent:
        assert len(attempts) == 2
        assert len(rollback_dirs) == 1
        assert list(rollback_dirs[0].iterdir()) == []
        assert result["skipped"] == [{
            "name": rollback_dirs[0].name,
            "reason": "post-commit cleanup pending",
            "path": str(rollback_dirs[0]),
        }]
    else:
        assert len(attempts) == 1
        assert rollback_dirs == []
        assert result["skipped"] == []


def test_commit_accounts_rmdir_before_any_post_removal_check(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            backup_fd = journal._backup_fd
            real_rmdir = _download.os.rmdir
            real_fstat = _download.os.fstat
            removed = []
            post_removal_checks = []

            def track_rmdir(path, *args, **kwargs):
                result = real_rmdir(path, *args, **kwargs)
                if Path(path).name == backup_dir.name:
                    removed.append(path)
                return result

            def reject_post_removal_fstat(fd):
                if removed and fd == backup_fd:
                    post_removal_checks.append(fd)
                    raise OSError(
                        "injected descriptor check after completed rmdir"
                    )
                return real_fstat(fd)

            monkeypatch.setattr(_download.os, "rmdir", track_rmdir)
            monkeypatch.setattr(_download.os, "fstat", reject_post_removal_fstat)

            assert journal.commit() == []

            assert len(removed) == 1
            assert post_removal_checks == []
            assert journal._backup_dir is None
            assert journal._backup_fd == -1
            assert journal._backup_parent_fd == -1
            assert journal._state.value == "COMMITTED"
            assert not backup_dir.exists()
        finally:
            journal.close()


def test_commit_cleanup_retry_success_clears_backup_binding(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            real_rmdir = _download.os.rmdir
            attempts = []

            def fail_first_rmdir(path, *args, **kwargs):
                if Path(path).name == backup_dir.name:
                    attempts.append(path)
                    if len(attempts) == 1:
                        raise PermissionError(
                            "injected rollback directory cleanup failure"
                        )
                return real_rmdir(path, *args, **kwargs)

            monkeypatch.setattr(
                _download.os,
                "rmdir",
                fail_first_rmdir,
            )

            assert journal.commit() == []

            assert len(attempts) == 2
            assert journal._backup_dir is None
            assert journal._backup_fd == -1
            assert journal._backup_parent_fd == -1
            assert journal._state.value == "COMMITTED"
            assert not backup_dir.exists()
        finally:
            journal.close()


def test_commit_rmdir_does_not_claim_recreated_backup_name(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            sentinel = backup_dir / "sentinel.txt"
            real_rmdir = _download.os.rmdir
            replacements = []

            def recreate_after_rmdir(path, *args, **kwargs):
                result = real_rmdir(path, *args, **kwargs)
                if (
                    Path(path).name == backup_dir.name
                    and not replacements
                ):
                    backup_dir.mkdir()
                    sentinel.write_bytes(b"unrelated")
                    replacements.append(True)
                return result

            monkeypatch.setattr(
                _download.os,
                "rmdir",
                recreate_after_rmdir,
            )

            assert journal.commit() == []

            assert replacements == [True]
            assert sentinel.read_bytes() == b"unrelated"
            assert journal._backup_dir is None
            assert journal._backup_fd == -1
            assert journal._backup_parent_fd == -1
            assert journal._state.value == "COMMITTED"
        finally:
            journal.close()


def test_pinned_journal_backup_swap_cannot_redirect_prepare(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    managed.write_bytes(original)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "0"
    sentinel.write_bytes(b"outside")
    displaced = tmp_path / "displaced-rollback"
    swapped = {}

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        ensure_backup_dir = journal._ensure_backup_dir

        def swap_after_creation():
            backup_dir = Path(ensure_backup_dir())
            if not swapped:
                backup_dir.rename(displaced)
                backup_dir.symlink_to(
                    external,
                    target_is_directory=True,
                )
                swapped["path"] = backup_dir
            return str(backup_dir)

        monkeypatch.setattr(
            journal,
            "_ensure_backup_dir",
            swap_after_creation,
        )

        assert journal.prepare(
            managed,
            expected=_owned_entry(original),
        ) is False
        assert managed.read_bytes() == original
        assert sentinel.read_bytes() == b"outside"
        assert list(displaced.iterdir()) == []

        swapped["path"].unlink()
        displaced.rename(swapped["path"])
        assert journal.rollback() == set()


def test_prepare_rejects_equal_size_in_place_change_moved_to_backup(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    modified = b"changed-output"
    assert len(modified) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        move_to_backup = journal._move_to_backup

        def modify_then_move(output_name, backup_name):
            fd = os.open(
                output_name,
                os.O_WRONLY | os.O_NOFOLLOW,
                dir_fd=output.fd,
            )
            try:
                os.ftruncate(fd, 0)
                os.write(fd, modified)
                os.fsync(fd)
            finally:
                os.close(fd)
            move_to_backup(output_name, backup_name)

        monkeypatch.setattr(
            journal,
            "_move_to_backup",
            modify_then_move,
        )

        assert journal.prepare(
            managed,
            expected=_owned_entry(original),
        ) is False

        assert managed.read_bytes() == modified
        assert journal._entries == {}
        assert journal._state.value == "OPEN"
        assert journal.commit() == []
        assert journal._state.value == "COMMITTED"
        assert managed.read_bytes() == modified
        assert not list(
            tmp_path.glob(".paperconan-output-rollback-*")
        )


def test_prepare_restore_failure_enters_recovery_and_blocks_commit(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    modified = b"changed-output"
    assert len(modified) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            move_to_backup = journal._move_to_backup
            real_link = _download.os.link

            def move_then_modify_backup(output_name, backup_name):
                move_to_backup(output_name, backup_name)
                fd = os.open(
                    backup_name,
                    os.O_WRONLY | os.O_NOFOLLOW,
                    dir_fd=journal._backup_fd,
                )
                try:
                    os.ftruncate(fd, 0)
                    os.write(fd, modified)
                    os.fsync(fd)
                finally:
                    os.close(fd)

            def fail_restore_link(src, dest, *args, **kwargs):
                is_restore = (
                    Path(src).name.isdigit()
                    and Path(dest).name == managed.name
                    and kwargs.get("src_dir_fd")
                    != kwargs.get("dst_dir_fd")
                )
                if is_restore:
                    raise PermissionError(
                        "injected prepare-conflict restore failure"
                    )
                return real_link(src, dest, *args, **kwargs)

            monkeypatch.setattr(
                journal,
                "_move_to_backup",
                move_then_modify_backup,
            )
            monkeypatch.setattr(
                _download.os,
                "link",
                fail_restore_link,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="rollback entry could not be verified",
            ) as prepare_error:
                journal.prepare(
                    managed,
                    expected=_owned_entry(original),
                )

            backup = Path(journal._backup_dir) / "0"
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert not managed.exists()
            assert backup.read_bytes() == modified
            assert backup in map(
                Path,
                prepare_error.value.recovery_paths,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="rollback entry could not be verified",
            ):
                journal.commit()

            assert journal._state.value == "RECOVERY_REQUIRED"
            assert not managed.exists()
            assert backup.read_bytes() == modified
        finally:
            journal.close()


def test_prepare_post_move_verification_failure_is_not_ordinary_conflict(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            verify_backup_dir = journal._verify_backup_dir
            failures = []

            def fail_once_after_move():
                verify_backup_dir()
                backup = Path(journal._backup_dir) / "0"
                if (
                    not failures
                    and backup.exists()
                    and not managed.exists()
                ):
                    failures.append(backup)
                    raise _download._UnstableRegularFileError(
                        "injected post-move verification failure"
                    )

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                fail_once_after_move,
            )

            with pytest.raises(
                _download._UnstableRegularFileError,
                match="injected post-move verification failure",
            ):
                journal.prepare(
                    managed,
                    expected=_owned_entry(original),
                )

            assert failures
            assert managed.read_bytes() == original
            assert journal._entries == {}
            assert journal._state.value == "OPEN"
            assert not list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
        finally:
            journal.close()


def test_direct_post_move_verification_failure_preserves_restored_owner(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    managed.write_bytes(original)
    _write_owned_sidecar(
        out_dir,
        [managed.name],
        cand_id="source:old",
    )

    def source_download(url, destination, **kwargs):
        _write_download_bytes(destination, b"new-output")
        return {
            "ok": True,
            "path": os.fspath(destination),
            "size": len(b"new-output"),
        }

    verify_backup_dir = (
        _download._ManagedOutputJournal._verify_backup_dir
    )
    failures = []

    def fail_once_after_output_move(journal):
        verify_backup_dir(journal)
        if (
            not failures
            and journal._backup_prefix
            == ".paperconan-output-rollback-"
            and journal._backup_dir is not None
        ):
            backup = Path(journal._backup_dir) / "0"
            if backup.exists() and not managed.exists():
                failures.append(backup)
                raise _download._UnstableRegularFileError(
                    "injected direct post-move verification failure"
                )

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "_verify_backup_dir",
        fail_once_after_output_move,
    )

    result = _download.download_candidate(
        _candidate(managed.name, "https://x/table.csv"),
        str(out_dir),
    )

    assert failures
    assert result["downloaded"] == []
    assert any(
        item["reason"].startswith("secure publication unavailable")
        for item in result["skipped"]
    )
    assert managed.read_bytes() == original
    assert json.loads(
        (out_dir / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == {
        managed.name: _owned_entry(original),
    }
    assert not list(
        tmp_path.glob(".paperconan-output-rollback-*")
    )


def test_prepare_persistent_post_move_failure_tracks_exact_backup(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"managed-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            verify_backup_dir = journal._verify_backup_dir

            def fail_after_move():
                verify_backup_dir()
                backup = Path(journal._backup_dir) / "0"
                if backup.exists() and not managed.exists():
                    raise _download._UnstableRegularFileError(
                        "injected persistent post-move failure"
                    )

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                fail_after_move,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="could not be verified after prepare conflict",
            ) as caught:
                journal.prepare(
                    managed,
                    expected=_owned_entry(original),
                )

            backup = Path(journal._backup_dir) / "0"
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert not managed.exists()
            assert backup.read_bytes() == original
            assert backup in map(Path, caught.value.recovery_paths)
            assert str(managed) in journal._entries
        finally:
            journal.close()


def test_restore_post_move_verification_failure_completes_accounted_restore(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_dir = journal._verify_backup_dir
            failures = []

            def fail_once_after_restore():
                verify_backup_dir()
                if (
                    not failures
                    and not backup.exists()
                    and managed.exists()
                    and managed.read_bytes() == original
                ):
                    failures.append(managed)
                    raise _download._UnstableRegularFileError(
                        "injected post-restore verification failure"
                    )

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                fail_once_after_restore,
            )

            journal.restore(managed)

            assert failures == [managed]
            assert managed.read_bytes() == original
            assert journal._entries == {}
            assert journal._state.value == "OPEN"
            assert not backup.exists()
            assert not list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
        finally:
            journal.close()


def test_restore_root_replacement_reports_private_prior_copy(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    replacement = b"replacement"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_dir = journal._verify_backup_dir
            replaced = []

            def replace_root_after_restore():
                verify_backup_dir()
                if (
                    not replaced
                    and not backup.exists()
                    and managed.exists()
                    and managed.read_bytes() == original
                ):
                    out_dir.rename(displaced)
                    out_dir.mkdir()
                    managed.write_bytes(replacement)
                    replaced.append(True)

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                replace_root_after_restore,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="during rollback",
            ) as caught:
                journal.restore(managed)

            recovery_paths = list(map(Path, caught.value.recovery_paths))
            assert replaced == [True]
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert managed.read_bytes() == replacement
            assert (displaced / managed.name).read_bytes() == original
            assert len(recovery_paths) == 1
            assert recovery_paths[0] != managed
            assert recovery_paths[0].exists()
            assert recovery_paths[0].read_bytes() == original
            assert recovery_paths[0].parent.name.startswith(
                ".paperconan-output-rollback-"
            )
        finally:
            journal.close()


def test_successful_restore_retry_clears_deleted_anchor_marker(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            anchor = backup_dir / ".restore-0"
            restore_from_backup = journal._restore_from_backup
            cleanup_restore_anchor = journal._cleanup_restore_anchor
            restore_failures = []
            cleanup_failures = []

            def fail_first_restore(backup_name, output_name):
                if not restore_failures:
                    restore_failures.append(backup_name)
                    raise PermissionError("injected restore failure")
                return restore_from_backup(backup_name, output_name)

            def fail_first_anchor_cleanup(anchor_name, expected):
                if not cleanup_failures:
                    cleanup_failures.append(anchor_name)
                    raise _download._ManagedOutputDirectoryCleanupError(
                        journal._backup_path(anchor_name),
                        PermissionError(
                            "injected restore-anchor cleanup failure"
                        ),
                    )
                return cleanup_restore_anchor(anchor_name, expected)

            monkeypatch.setattr(
                journal,
                "_restore_from_backup",
                fail_first_restore,
            )
            monkeypatch.setattr(
                journal,
                "_cleanup_restore_anchor",
                fail_first_anchor_cleanup,
            )

            with pytest.raises(
                PermissionError,
                match="injected restore failure",
            ):
                journal.restore(managed)

            assert restore_failures == ["0"]
            assert cleanup_failures == [".restore-0"]
            assert anchor.read_bytes() == original
            assert str(anchor) in journal._detached_backup_paths

            journal.restore(managed)

            assert managed.read_bytes() == original
            assert not anchor.exists()
            assert journal._detached_backup_paths == {}
            assert journal._entries == {}
            assert journal._backup_dir is None
            assert not backup_dir.exists()
            assert journal.commit() == []
            assert journal._state.value == "COMMITTED"
        finally:
            journal.close()


def test_journal_failed_restore_remains_retryable_and_continues(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    first = out_dir / "first.csv"
    second = out_dir / "second.csv"
    originals = {
        first: b"old-first",
        second: b"old-second",
    }
    published = {
        first: b"new-first",
        second: b"new-second",
    }
    for path, payload in originals.items():
        path.write_bytes(payload)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            for path in (first, second):
                assert journal.prepare(
                    path,
                    expected=_owned_entry(originals[path]),
                )
                path.write_bytes(published[path])
                state = path.stat()
                journal.bind_published(
                    path,
                    (state.st_dev, state.st_ino),
                    len(published[path]),
                    hashlib.sha256(published[path]).hexdigest(),
                )
            second_dest = str(second)
            second_backup = Path(journal._backup_dir) / "1"
            real_link = _download.os.link
            failures = []

            def fail_once(src, dest, *args, **kwargs):
                is_second_restore = (
                    Path(src).name == second_backup.name
                    and Path(dest).name == second.name
                    and kwargs.get("src_dir_fd")
                    != kwargs.get("dst_dir_fd")
                )
                if is_second_restore and not failures:
                    failures.append(os.fspath(src))
                    raise PermissionError("injected restore failure")
                return real_link(src, dest, *args, **kwargs)

            monkeypatch.setattr(_download.os, "link", fail_once)

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match="could not restore 1 managed output",
            ):
                journal.rollback()

            assert len(failures) == 1
            assert first.read_bytes() == originals[first]
            assert not second.exists()
            assert tuple(journal._entries) == (second_dest,)
            assert second_backup.read_bytes() == originals[second]

            assert journal.rollback() == {second_dest}
            assert second.read_bytes() == originals[second]
            assert journal._entries == {}
            assert not list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
        finally:
            journal.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_journal_empty_backup_directory_cleanup_remains_retryable(
    tmp_path, monkeypatch, persistent
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    output.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as pinned:
        journal = _download._ManagedOutputJournal(pinned)
        try:
            assert journal.prepare(
                output,
                expected=_owned_entry(original),
            )
            output.write_bytes(published)
            state = output.stat()
            journal.bind_published(
                output,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            real_rmdir = _download.os.rmdir
            failures = []

            def fail_cleanup(path, *args, **kwargs):
                is_backup_dir = (
                    Path(path).name == backup_dir.name
                    and kwargs.get("dir_fd") is not None
                )
                if is_backup_dir and (persistent or not failures):
                    failures.append(os.fspath(path))
                    raise PermissionError(
                        "injected rollback directory cleanup failure"
                    )
                return real_rmdir(path, *args, **kwargs)

            monkeypatch.setattr(_download.os, "rmdir", fail_cleanup)

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match=(
                    "could not remove managed-output rollback directory"
                ),
            ) as first:
                journal.rollback()

            assert first.value.failures == ()
            assert first.value.cleanup_failure[0] == str(backup_dir)
            assert output.read_bytes() == original
            assert journal._entries == {}
            assert journal._backup_dir == str(backup_dir)
            assert backup_dir.is_dir()
            assert list(backup_dir.iterdir()) == []

            if persistent:
                with pytest.raises(
                    _download._ManagedOutputRollbackError,
                    match=(
                        "could not remove managed-output "
                        "rollback directory"
                    ),
                ) as second:
                    journal.rollback()
                assert second.value.failures == ()
                assert (
                    second.value.cleanup_failure[0]
                    == str(backup_dir)
                )
                assert len(failures) == 2
                assert journal._backup_dir == str(backup_dir)
                assert backup_dir.is_dir()
            else:
                assert journal.rollback() == set()
                assert len(failures) == 1
                assert journal._backup_dir is None
                assert not backup_dir.exists()
            assert output.read_bytes() == original
            assert journal._entries == {}
        finally:
            journal.close()


@pytest.mark.parametrize("channel", ["direct", "zip", "tar"])
@pytest.mark.parametrize("destination_state", ["new", "replacement"])
@pytest.mark.parametrize("persistent", [False, True])
def test_restore_failure_preserves_operation_error_and_recovery_state(
    tmp_path, monkeypatch, channel, destination_state, persistent
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    old_bytes = b"old-output"
    managed_files = []
    if destination_state == "replacement":
        output.write_bytes(old_bytes)
        managed_files.append(output.name)
    _write_sidecar(out_dir, managed_files, doi="10.x/old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    operation_error = RuntimeError("source operation failed")
    body = b"new-output"

    if channel == "direct":
        cand = _candidate(output.name, "https://x/table.csv")

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, body)
            return {"ok": True, "path": dest, "size": len(body)}
    else:
        payload = _archive_payload(
            channel, [(f"nested/{output.name}", body)]
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def archive_download(url, dest, **kwargs):
            _write_download_bytes(dest, payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

        source_download = archive_download

    monkeypatch.setattr(
        _download, "download_file", source_download
    )

    def fail_after_publication(*_args, **_kwargs):
        raise operation_error

    monkeypatch.setattr(
        _download, "_write_source_sidecar", fail_after_publication
    )

    failures = []
    if destination_state == "replacement":
        real_link = _download.os.link

        def fail_restore(src, dest, *args, **kwargs):
            is_restore = (
                kwargs.get("src_dir_fd") is not None
                and kwargs.get("dst_dir_fd") is not None
                and Path(src).name.isdigit()
                and Path(dest).name == output.name
                and kwargs["src_dir_fd"] != kwargs["dst_dir_fd"]
            )
            if is_restore and (persistent or not failures):
                failures.append((os.fspath(src), os.fspath(dest)))
                raise PermissionError("injected restore failure")
            return real_link(src, dest, *args, **kwargs)

        monkeypatch.setattr(_download.os, "link", fail_restore)
    else:
        real_unlink = _download.os.unlink

        def fail_restore(path, *args, **kwargs):
            is_restore = (
                kwargs.get("dir_fd") is not None
                and Path(path).name == output.name
            )
            if is_restore and (persistent or not failures):
                failures.append(os.fspath(path))
                raise PermissionError("injected restore failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(_download.os, "unlink", fail_restore)

    with pytest.raises(
        RuntimeError, match="source operation failed"
    ) as caught:
        _download.download_candidate(cand, str(out_dir))

    assert caught.value is operation_error
    assert caught.value.__cause__ is not None
    assert sidecar.read_bytes() == original_sidecar
    assert len(failures) == 1
    if destination_state == "replacement":
        assert not output.exists()
        rollback_dirs = list(
            tmp_path.glob(".paperconan-output-rollback-*")
        )
        assert len(rollback_dirs) == 1
        backups = list(rollback_dirs[0].iterdir())
        assert len(backups) == 1
        assert backups[0].read_bytes() == old_bytes
    else:
        assert output.read_bytes() == body
        assert not list(
            tmp_path.glob(".paperconan-output-rollback-*")
        )


@pytest.mark.parametrize("channel", ["direct", "zip", "tar"])
@pytest.mark.parametrize("destination_state", ["new", "replacement"])
@pytest.mark.parametrize("persistent", [False, True])
def test_sidecar_rollback_failure_is_actionable_and_preserves_recovery_state(
    tmp_path, monkeypatch, channel, destination_state, persistent
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    old_bytes = b"old-output"
    managed_files = []
    if destination_state == "replacement":
        output.write_bytes(old_bytes)
        managed_files.append(output.name)
    _write_sidecar(out_dir, managed_files, doi="10.x/old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original_sidecar = sidecar.read_bytes()
    body = b"new-output"

    if channel == "direct":
        cand = _candidate(output.name, "https://x/table.csv")

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, body)
            return {"ok": True, "path": dest, "size": len(body)}
    else:
        payload = _archive_payload(
            channel, [(f"nested/{output.name}", body)]
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def source_download(url, dest, **kwargs):
            _write_download_bytes(dest, payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

    monkeypatch.setattr(_download, "download_file", source_download)
    failures = []
    real_unlink = _download.os.unlink
    real_link = _download.os.link

    def fail_unlink(path, *args, **kwargs):
        is_restore = (
            kwargs.get("dir_fd") is not None
            and Path(path).name == output.name
        )
        if (
            destination_state == "new"
            and is_restore
            and (persistent or not failures)
        ):
            failures.append(os.fspath(path))
            raise PermissionError("injected restore failure")
        return real_unlink(path, *args, **kwargs)

    def fail_link(src, dest, *args, **kwargs):
        is_restore = (
            kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and Path(src).name.isdigit()
            and Path(dest).name == output.name
            and kwargs["src_dir_fd"] != kwargs["dst_dir_fd"]
        )
        if (
            destination_state == "replacement"
            and is_restore
            and (persistent or not failures)
        ):
            failures.append((os.fspath(src), os.fspath(dest)))
            raise PermissionError("injected restore failure")
        if _is_staged_sidecar_publication(src, dest, kwargs):
            raise OSError("sidecar commit failed")
        return real_link(src, dest, *args, **kwargs)

    monkeypatch.setattr(_download.os, "unlink", fail_unlink)
    monkeypatch.setattr(_download.os, "link", fail_link)

    with pytest.raises(
        _download._ManagedOutputRollbackError,
        match="could not restore 1 managed output",
    ):
        _download.download_candidate(cand, str(out_dir))

    assert sidecar.read_bytes() == original_sidecar
    if persistent:
        assert len(failures) == 2
        if destination_state == "replacement":
            assert not output.exists()
            rollback_dirs = list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
            assert len(rollback_dirs) == 1
            backups = list(rollback_dirs[0].iterdir())
            assert len(backups) == 1
            assert backups[0].read_bytes() == old_bytes
        else:
            assert output.read_bytes() == body
    else:
        assert len(failures) == 1
        assert not list(
            tmp_path.glob(".paperconan-output-rollback-*")
        )
        if destination_state == "replacement":
            assert output.read_bytes() == old_bytes
        else:
            assert not output.exists()


def test_later_download_error_rolls_back_earlier_accepted_output(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "old.csv"
    old.write_bytes(b"old")
    _write_sidecar(out_dir, ["old.csv"], doi="10.x/old")
    original_sidecar = (
        out_dir / _download.SOURCE_SIDECAR
    ).read_bytes()

    def source_download(url, dest, **kwargs):
        if url.endswith("/second"):
            raise RuntimeError("download interrupted")
        _write_download_bytes(dest, b"first")
        return {"ok": True, "path": dest, "size": 5}

    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [
            {
                "name": "first.csv",
                "download_url": "https://x/first",
            },
            {
                "name": "second.csv",
                "download_url": "https://x/second",
            },
        ],
    }
    monkeypatch.setattr(_download, "download_file", source_download)

    with pytest.raises(RuntimeError, match="download interrupted"):
        _download.download_candidate(cand, str(out_dir))

    assert old.read_bytes() == b"old"
    assert not (out_dir / "first.csv").exists()
    assert not (out_dir / "second.csv").exists()
    assert (
        out_dir / _download.SOURCE_SIDECAR
    ).read_bytes() == original_sidecar
    assert not list(out_dir.glob(".paperconan-output-rollback-*"))
    assert not list(tmp_path.glob(".paperconan-output-rollback-*"))


def _paper_data_size(out_dir):
    with _download._pinned_output_directory(out_dir) as output:
        return _download._paper_data_size(output)


def _write_previous_sidecar(out_dir):
    old_cand = {
        "cand_id": "old",
        "source": "source",
        "title": "previous provenance",
        "tabular_files": [],
    }
    assert _download._write_source_sidecar(
        old_cand, str(out_dir), ["old.csv"]
    )
    return (out_dir / _download.SOURCE_SIDECAR).read_bytes()


def test_direct_data_cap_rolls_back_on_sidecar_commit_failure(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "old.csv"
    old.write_bytes(b"old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    payload = b"new-data"
    cand = _candidate("new.csv", "https://x/new")
    original_sidecar = _write_previous_sidecar(out_dir)
    cap = len(b"old") + len(payload)
    assert _paper_data_size(out_dir) <= cap
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)
    calls = []
    _bounded_download_stub(
        monkeypatch, {"https://x/new": payload}, calls
    )
    failures = _fail_final_sidecar_commit(monkeypatch)

    result = _download.download_candidate(cand, str(out_dir))

    assert calls == [("https://x/new", len(payload))]
    assert len(failures) == 1
    assert result["downloaded"] == []
    assert old.read_bytes() == b"old"
    assert not (out_dir / "new.csv").exists()
    assert sidecar.read_bytes() == original_sidecar
    assert _paper_data_size(out_dir) <= cap


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_data_cap_rolls_back_on_sidecar_commit_failure(
    tmp_path, monkeypatch, archive_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "old.csv"
    old.write_bytes(b"old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    member_body = b"new-data"
    payload = _archive_payload(
        archive_kind, [("nested/new.csv", member_body)]
    )

    def archive_download(url, dest, **kwargs):
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest, "size": len(payload)}

    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }
    original_sidecar = _write_previous_sidecar(out_dir)
    cap = len(b"old") + len(member_body)
    assert _paper_data_size(out_dir) <= cap
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)
    monkeypatch.setattr(_download, "download_file", archive_download)
    failures = _fail_final_sidecar_commit(monkeypatch)

    result = _download.download_candidate(cand, str(out_dir))

    assert len(failures) == 1
    assert result["downloaded"] == []
    assert old.read_bytes() == b"old"
    assert not (out_dir / "new.csv").exists()
    assert sidecar.read_bytes() == original_sidecar
    assert _paper_data_size(out_dir) <= cap
    assert not list(out_dir.glob(".paperconan-archive-*"))


def test_direct_download_accepts_exact_fit_then_stops_at_paper_cap(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.bin").write_bytes(b"seed")
    payloads = {
        "https://x/exact": b"123456",
        "https://x/overflow": b"x",
    }
    calls = []
    _bounded_download_stub(monkeypatch, payloads, calls)
    cand = {
        "cand_id": "source:1",
        "source": "source",
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
    }
    cap = 4 + 6
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(cand, str(out_dir))

    assert result["downloaded"] == [str(out_dir / "exact.csv")]
    assert calls == [
        ("https://x/exact", _download._DEFAULT_MAX),
        ("https://x/overflow", _download._DEFAULT_MAX),
    ]
    assert _paper_data_size(out_dir) == cap
    assert not (out_dir / "overflow.csv").exists()


def test_direct_download_rejects_one_byte_paper_cap_overflow(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.bin").write_bytes(b"seed")
    calls = []
    _bounded_download_stub(
        monkeypatch,
        {"https://x/overflow": b"1234567"},
        calls,
    )
    cand = _candidate("overflow.csv", "https://x/overflow")
    cap = 4 + 6
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(
        cand,
        str(out_dir),
    )

    assert result["downloaded"] == []
    assert calls == [
        ("https://x/overflow", _download._DEFAULT_MAX)
    ]
    assert _paper_data_size(out_dir) <= cap
    assert not (out_dir / "overflow.csv").exists()
    assert (out_dir / _download.SOURCE_SIDECAR).exists()


@pytest.mark.parametrize(
    ("replacement", "accepted"),
    [(b"ABCDEF", True), (b"ABCDEFG", False)],
)
def test_direct_download_credits_managed_replacement_once(
    tmp_path, monkeypatch, replacement, accepted
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "table.csv"
    old.write_bytes(b"123456")
    (out_dir / "existing.bin").write_bytes(b"seed")
    old_cand = {
        "cand_id": "old",
        "source": "source",
        "tabular_files": [],
    }
    assert _download._write_source_sidecar(
        old_cand, str(out_dir), ["table.csv"]
    )
    original_sidecar = (
        out_dir / _download.SOURCE_SIDECAR
    ).read_bytes()
    calls = []
    _bounded_download_stub(
        monkeypatch,
        {"https://x/table": replacement},
        calls,
    )
    cand = _candidate("table.csv", "https://x/table")
    cand["title"] = "replacement provenance that grows the sidecar"
    cap = 4 + 6
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(
        cand,
        str(out_dir),
    )

    assert calls == [("https://x/table", 6)]
    assert result["downloaded"] == (
        [str(old)] if accepted else []
    )
    assert old.read_bytes() == (
        replacement if accepted else b"123456"
    )
    if accepted:
        assert _paper_data_size(out_dir) == cap
    else:
        assert (
            out_dir / _download.SOURCE_SIDECAR
        ).read_bytes() == original_sidecar


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    ("member_body", "accepted"),
    [(b"123456", True), (b"1234567", False)],
)
def test_candidate_archive_cap_excludes_bounded_sidecar(
    tmp_path, monkeypatch, archive_kind, member_body, accepted
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.bin").write_bytes(b"seed")
    payload = _archive_payload(
        archive_kind,
        [("nested/table.csv", member_body)],
    )

    def archive_download(url, dest, **kwargs):
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest, "size": len(payload)}

    monkeypatch.setattr(_download, "download_file", archive_download)
    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }
    cap = 4 + 6
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(cand, str(out_dir))

    table = out_dir / "table.csv"
    assert result["downloaded"] == ([str(table)] if accepted else [])
    assert table.exists() is accepted
    assert _paper_data_size(out_dir) <= cap
    sidecar = out_dir / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == (
        _owned_map(out_dir, ["table.csv"])
        if accepted
        else {}
    )


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


def _extract_archive_managed(
    path,
    archive_kind,
    out_dir,
    *,
    max_member_bytes=100,
    cap_state=None,
):
    extract = (
        _download._extract_tabular_zip_managed
        if archive_kind == "zip"
        else _download._extract_tabular_tar_managed
    )
    return extract(
        str(path),
        str(out_dir),
        max_member_bytes,
        reusable_names=(),
        cap_state=cap_state,
        archive_name=path.name,
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


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_output_file_limit_discloses_every_retained_member(
    tmp_path, monkeypatch, archive_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    archive = tmp_path / f"supp.{archive_kind}"
    _write_archive(
        archive,
        archive_kind,
        [
            ("first.csv", b""),
            ("second.csv", b""),
            ("third.csv", b""),
        ],
    )
    monkeypatch.setattr(_download, "_ARCHIVE_MEMBER_LIMIT", 10)
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1_000
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_OUTPUT_FILE_LIMIT", 1
    )

    extracted, preserved, skipped = _extract_archive_managed(
        archive, archive_kind, out_dir
    )

    assert extracted == [str(out_dir / "first.csv")]
    assert preserved == set()
    assert skipped == [
        {
            "name": "second.csv",
            "reason": "archive output file limit",
            "limit": 1,
        },
        {
            "name": "third.csv",
            "reason": "archive output file limit",
            "limit": 1,
        },
        {
            "name": archive.name,
            "reason": "archive output file limit",
            "limit": 1,
            "retained_outputs": 1,
            "omitted_members": 2,
        },
    ]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize("limit_kind", ["member", "output"])
def test_partial_archive_limit_preserves_unprocessed_managed_outputs(
    tmp_path, monkeypatch, archive_kind, limit_kind
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
        _write_download_bytes(dest, payload)
        return {"ok": True, "path": dest}

    monkeypatch.setattr(_download, "download_file", archive_download)
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_MEMBER_LIMIT",
        1 if limit_kind == "member" else 10,
    )
    monkeypatch.setattr(
        _download, "_ARCHIVE_MEMBER_NAME_BYTES", 1_000
    )
    monkeypatch.setattr(
        _download,
        "_ARCHIVE_OUTPUT_FILE_LIMIT",
        1 if limit_kind == "output" else 10,
    )

    result = _download.download_candidate({
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }, str(tmp_path))

    assert (tmp_path / "new.csv").read_bytes() == b"new-complete"
    assert old.read_bytes() == b"old-complete"
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == _owned_map(
        tmp_path, ["new.csv", "old.csv"]
    )
    archive_name = (
        "supp.zip" if archive_kind == "zip" else "supp.tar.gz"
    )
    if limit_kind == "member":
        assert result["skipped"] == [{
            "name": archive_name,
            "reason": "archive member count limit",
            "limit": 1,
            "members_inspected": 1,
            "eligible_members_retained": 1,
            "retained_members": 1,
            "omitted_members_lower_bound": 1,
        }]
    else:
        assert result["skipped"] == [
            {
                "name": "old.csv",
                "reason": "archive output file limit",
                "limit": 1,
            },
            {
                "name": archive_name,
                "reason": "archive output file limit",
                "limit": 1,
                "retained_outputs": 1,
                "omitted_members": 1,
            },
        ]


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_declared_per_paper_rejection_is_disclosed(
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
    cap_state = {"exceeded": False}

    extracted, preserved, skipped = _extract_archive_managed(
        archive,
        archive_kind,
        out_dir,
        cap_state=cap_state,
    )

    assert extracted == []
    assert preserved == set()
    assert skipped == [{
        "name": "overflow.csv",
        "reason": "archive member exceeds per-paper cap",
        "limit": 10,
        "remaining_bytes": 6,
        "declared_size": 7,
    }]
    assert cap_state == {"exceeded": True}


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize("limit_kind", ["member", "paper"])
def test_archive_streamed_size_rejection_is_disclosed_once(
    tmp_path, monkeypatch, archive_kind, limit_kind
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    archive = tmp_path / f"supp.{archive_kind}"
    _write_archive(
        archive,
        archive_kind,
        [("stream.csv", b"x")],
    )
    cap_state = {"exceeded": False}
    if limit_kind == "paper":
        (out_dir / "existing.bin").write_bytes(b"seed")
        monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", 10)
        max_member_bytes = 100
        expected = {
            "name": "stream.csv",
            "reason": (
                "archive member exceeds per-paper cap while streaming"
            ),
            "limit": 10,
            "remaining_bytes": 6,
        }
    else:
        max_member_bytes = 10
        expected = {
            "name": "stream.csv",
            "reason": (
                "archive member exceeds per-member cap while streaming"
            ),
            "limit": 10,
        }

    def exceed_stream(_src, _dest, _max_bytes):
        raise _download._SizeLimitExceeded("injected stream limit")

    monkeypatch.setattr(
        _download, "_atomic_stream_write", exceed_stream
    )

    extracted, preserved, skipped = _extract_archive_managed(
        archive,
        archive_kind,
        out_dir,
        max_member_bytes=max_member_bytes,
        cap_state=cap_state,
    )

    assert extracted == []
    assert preserved == set()
    assert skipped == [expected]
    assert cap_state == {
        "exceeded": limit_kind == "paper"
    }


@pytest.mark.parametrize(
    "change",
    ["removed", "equal-size-modified"],
)
def test_top_level_sidecar_commit_recovery_is_propagated(
    tmp_path, monkeypatch, change
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        out_dir,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original = sidecar.read_bytes()
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    )
    real_commit = _download._ManagedOutputJournal.commit
    invalidated = []
    changed_payload = []

    def invalidate_before_sidecar_commit(journal):
        if (
            journal._backup_prefix
            == ".paperconan-sidecar-rollback-"
            and not invalidated
        ):
            published = sidecar.read_bytes()
            if change == "removed":
                sidecar.unlink()
            else:
                replacement = (
                    (b"x" if published[:1] != b"x" else b"y")
                    + published[1:]
                )
                assert len(replacement) == len(published)
                with sidecar.open("r+b") as stream:
                    stream.write(replacement)
                    stream.flush()
                    os.fsync(stream.fileno())
                changed_payload.append(replacement)
            invalidated.append(change)
        return real_commit(journal)

    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        invalidate_before_sidecar_commit,
    )

    with pytest.raises(BaseException) as caught:
        _download.download_candidate(
            {
                "cand_id": "source:new",
                "source": "source",
                "tabular_files": [],
            },
            str(out_dir),
        )

    error = caught.value
    assert type(error).__name__ == (
        "_SourceSidecarRecoveryRequiredError"
    )
    assert invalidated == [change]
    if change == "removed":
        assert not sidecar.exists()
    else:
        assert sidecar.read_bytes() == changed_payload[0]
    rollback_dirs = list(set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert set(map(Path, error.recovery_paths)) == set(backups)
    assert error.operation_error is not None


def test_top_level_concurrent_sidecar_rollback_recovery_is_propagated(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        out_dir,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    original = sidecar.read_bytes()
    concurrent = json.dumps({
        "cand_id": "source:concurrent",
        "managed_files": {},
    }).encode("utf-8")
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    )
    real_link = _download.os.link
    installed = []

    def install_concurrent_before_publish(
        src,
        dest,
        *args,
        **kwargs,
    ):
        if (
            not installed
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            fd = os.open(
                dest,
                (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                ),
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(fd, concurrent)
                os.fsync(fd)
            finally:
                os.close(fd)
            installed.append(True)
        return real_link(src, dest, *args, **kwargs)

    monkeypatch.setattr(
        _download.os,
        "link",
        install_concurrent_before_publish,
    )

    with pytest.raises(BaseException) as caught:
        _download.download_candidate(
            {
                "cand_id": "source:new",
                "source": "source",
                "tabular_files": [],
            },
            str(out_dir),
        )

    error = caught.value
    assert type(error).__name__ == (
        "_SourceSidecarRecoveryRequiredError"
    )
    assert installed == [True]
    assert sidecar.read_bytes() == concurrent
    rollback_dirs = list(set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert set(map(Path, error.recovery_paths)) == set(backups)
    assert isinstance(
        error.rollback_error,
        _download._ManagedOutputRollbackError,
    )


def test_cleanup_narrowing_sidecar_recovery_is_propagated(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale = out_dir / "stale.csv"
    stale.write_bytes(b"stale")
    _write_owned_sidecar(
        out_dir,
        [stale.name],
        cand_id="source:old",
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    )
    real_commit = _download._ManagedOutputJournal.commit
    sidecar_commits = []

    def remove_second_sidecar_before_commit(journal):
        if (
            journal._backup_prefix
            == ".paperconan-sidecar-rollback-"
        ):
            sidecar_commits.append(journal)
            if len(sidecar_commits) == 2:
                sidecar.unlink()
        return real_commit(journal)

    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit",
        remove_second_sidecar_before_commit,
    )

    with pytest.raises(BaseException) as caught:
        _download.download_candidate(
            {
                "cand_id": "source:new",
                "source": "source",
                "tabular_files": [],
            },
            str(out_dir),
        )

    error = caught.value
    assert type(error).__name__ == (
        "_SourceSidecarRecoveryRequiredError"
    )
    assert len(sidecar_commits) == 2
    assert stale.read_bytes() == b"stale"
    assert not sidecar.exists()
    rollback_dirs = list(set(
        tmp_path.glob(".paperconan-sidecar-rollback-*")
    ) - rollback_dirs_before)
    assert len(rollback_dirs) == 1
    backups = list(rollback_dirs[0].iterdir())
    assert len(backups) == 1
    assert set(map(Path, error.recovery_paths)) == set(backups)


def test_initial_sidecar_post_link_replacement_is_preserved(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    concurrent = b'{"cand_id":"source:concurrent","managed_files":{}}'
    real_link = _download.os.link
    replacements = []

    def replace_after_sidecar_link(src, dest, *args, **kwargs):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replacements
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            os.unlink(dest, dir_fd=kwargs["dst_dir_fd"])
            replacement_fd = os.open(
                dest,
                (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                ),
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(replacement_fd, concurrent)
                os.fsync(replacement_fd)
            finally:
                os.close(replacement_fd)
            replacements.append(True)
        return result

    monkeypatch.setattr(
        _download.os,
        "link",
        replace_after_sidecar_link,
    )

    with _download._pinned_output_directory(str(out_dir)) as output:
        with pytest.raises(
            _download._SourceSidecarPublicationError,
            match="changed during publication",
        ):
            _download._write_source_sidecar(
                {
                    "cand_id": "source:prepared",
                    "source": "source",
                },
                output,
                downloads=[],
            )

    assert replacements == [True]
    assert sidecar.read_bytes() == concurrent
    assert not list(out_dir.glob(".paperconan_source.json.*.part"))


def test_download_candidate_rejects_initial_sidecar_post_link_replacement(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    concurrent = b'{"cand_id":"source:concurrent","managed_files":{}}'
    real_link = _download.os.link
    replacements = []

    def replace_after_sidecar_link(src, dest, *args, **kwargs):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replacements
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            os.unlink(dest, dir_fd=kwargs["dst_dir_fd"])
            replacement_fd = os.open(
                dest,
                (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                ),
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(replacement_fd, concurrent)
                os.fsync(replacement_fd)
            finally:
                os.close(replacement_fd)
            replacements.append(True)
        return result

    monkeypatch.setattr(
        _download.os,
        "link",
        replace_after_sidecar_link,
    )

    result = _download.download_candidate(
        {
            "cand_id": "source:prepared",
            "source": "source",
            "tabular_files": [],
        },
        str(out_dir),
    )

    assert replacements == [True]
    assert result["downloaded"] == []
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": (
            "retained provenance sidecar because it changed "
            "during publication"
        ),
    }]
    assert sidecar.read_bytes() == concurrent
    assert not list(out_dir.glob(".paperconan_source.json.*.part"))


def test_initial_sidecar_final_root_helper_rejects_replaced_root(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    replacement = b'{"cand_id":"source:replacement","managed_files":{}}'
    final_root_check = (
        _download._verify_final_published_sidecar_root
    )
    replacements = []

    def replace_before_final_root_check(output):
        out_dir.rename(displaced)
        out_dir.mkdir()
        sidecar.write_bytes(replacement)
        replacements.append(True)
        return final_root_check(output)

    monkeypatch.setattr(
        _download,
        "_verify_final_published_sidecar_root",
        replace_before_final_root_check,
    )

    with _download._pinned_output_directory(str(out_dir)) as output:
        with pytest.raises(
            _download._SourceSidecarPublicationError,
            match="changed during publication",
        ):
            _download._write_source_sidecar(
                {
                    "cand_id": "source:new",
                    "source": "source",
                },
                output,
                {},
            )

    assert replacements == [True]
    assert sidecar.read_bytes() == replacement
    assert (
        displaced / _download.SOURCE_SIDECAR
    ).read_bytes() != replacement


def test_sidecar_only_transaction_rechecks_root_before_empty_commit(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir()
    sidecar = out_dir / _download.SOURCE_SIDECAR
    replacement = b'{"cand_id":"source:replacement","managed_files":{}}'
    write_source_sidecar = _download._write_source_sidecar
    replacements = []

    def replace_after_sidecar_write(*args, **kwargs):
        result = write_source_sidecar(*args, **kwargs)
        out_dir.rename(displaced)
        out_dir.mkdir()
        sidecar.write_bytes(replacement)
        replacements.append(True)
        return result

    monkeypatch.setattr(
        _download,
        "_write_source_sidecar",
        replace_after_sidecar_write,
    )

    with pytest.raises(
        ValueError,
        match="output directory changed",
    ):
        _download.download_candidate(
            {
                "cand_id": "source:new",
                "source": "source",
                "tabular_files": [],
            },
            str(out_dir),
        )

    assert replacements == [True]
    assert sidecar.read_bytes() == replacement
    assert (
        displaced / _download.SOURCE_SIDECAR
    ).read_bytes() != replacement


def test_published_output_verification_bounds_growing_stream_reads(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    published = out_dir / "table.csv"
    payload = b"x"
    published.write_bytes(payload)
    state = published.stat()
    entry = _download._PublishedOutputFile(
        filename=published.name,
        size=len(payload),
        identity=(state.st_dev, state.st_ino),
        sha256=hashlib.sha256(payload).hexdigest(),
        created=True,
    )
    real_read = _download.os.read
    bounded_reads = []
    growing_reads = []

    class GrowingReader:
        def __init__(self, fd):
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            os.close(self.fd)

        def read(self, _size=-1):
            growing_reads.append(True)
            if len(growing_reads) > 8:
                return b""
            return b"x"

    def growing_fdopen(fd, *_args, **_kwargs):
        return GrowingReader(fd)

    def track_bounded_read(fd, size):
        bounded_reads.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(_download.os, "fdopen", growing_fdopen)
    monkeypatch.setattr(_download.os, "read", track_bounded_read)

    with _download._pinned_output_directory(str(out_dir)) as output:
        _download._verify_published_output_file(output, entry)

    assert growing_reads == []
    assert bounded_reads == [1, 1]


@pytest.mark.parametrize(
    "change",
    ["missing", "equal-size-modified", "replaced"],
)
def test_production_abandon_rejects_unverified_backup(
    tmp_path, monkeypatch, change
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    changed = b"bad-output"
    assert len(changed) == len(original)
    output.write_bytes(original)
    _write_owned_sidecar(
        out_dir,
        [output.name],
        cand_id="source:old",
    )
    observed = {}

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, published)
        return {"ok": True, "path": dest, "size": len(published)}

    def fail_reconciliation(*_args, **_kwargs):
        raise _download._UnstableRegularFileError(
            "injected publication reconciliation failure"
        )

    real_abandon = _download._ManagedOutputJournal.abandon

    def change_backup_then_abandon(journal, dest_path):
        entry = journal._entries[os.path.abspath(dest_path)]
        backup = Path(journal._backup_dir) / entry["backup"]
        if change == "missing":
            backup.unlink()
        elif change == "equal-size-modified":
            with backup.open("r+b") as stream:
                stream.write(changed)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            backup.unlink()
            backup.write_bytes(original)
        observed["backup"] = backup
        try:
            return real_abandon(journal, dest_path)
        finally:
            observed["state"] = journal._state.value
            observed["retained"] = (
                os.path.abspath(dest_path) in journal._entries
            )

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        fail_reconciliation,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "abandon",
        change_backup_then_abandon,
    )

    with pytest.raises(
        _download._ManagedOutputRecoveryRequiredError,
        match="changed before abandon",
    ) as caught:
        _download.download_candidate(
            _candidate(output.name, "https://x/table.csv"),
            str(out_dir),
        )

    backup = observed["backup"]
    assert observed["state"] == "RECOVERY_REQUIRED"
    assert observed["retained"] is True
    assert output.read_bytes() == published
    if change == "missing":
        assert not backup.exists()
    else:
        assert backup.read_bytes() == (
            changed
            if change == "equal-size-modified"
            else original
        )
    assert caught.value.recovery_paths == ()


def test_production_abandon_reports_detached_backup_once(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    output.write_bytes(original)
    _write_owned_sidecar(
        out_dir,
        [output.name],
        cand_id="source:old",
    )
    detached = []

    def source_download(url, dest, **kwargs):
        _write_download_bytes(dest, published)
        return {"ok": True, "path": dest, "size": len(published)}

    def fail_reconciliation(*_args, **_kwargs):
        raise _download._UnstableRegularFileError(
            "injected publication reconciliation failure"
        )

    real_abandon = _download._ManagedOutputJournal.abandon

    def track_abandon(journal, dest_path):
        backup_path = real_abandon(journal, dest_path)
        detached.append(Path(backup_path))
        return backup_path

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download,
        "_verify_published_output_file",
        fail_reconciliation,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "abandon",
        track_abandon,
    )

    result = _download.download_candidate(
        _candidate(output.name, "https://x/table.csv"),
        str(out_dir),
    )

    assert len(detached) == 1
    backup = detached[0]
    assert backup.read_bytes() == original
    assert output.read_bytes() == published
    assert result["downloaded"] == []
    pending = [
        item
        for item in result["skipped"]
        if item.get("reason") == "post-commit cleanup pending"
    ]
    assert pending == [{
        "name": backup.name,
        "reason": "post-commit cleanup pending",
        "path": str(backup),
    }]
    assert not any(
        item.get("path") == str(backup.parent)
        for item in result["skipped"]
    )


def test_commit_reports_replaced_detached_backup_directory_and_retries(
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    unrelated = b"user-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal.abandon(managed))
            backup_dir = backup.parent
            backup.unlink()
            backup.write_bytes(unrelated)

            assert journal.commit() == [str(backup_dir)]

            assert backup.read_bytes() == unrelated
            assert journal._detached_backup_paths == {}
            assert journal._state.value == "COMMIT_CLEANUP"

            backup.unlink()

            assert journal.commit() == []
            assert journal._backup_dir is None
            assert journal._state.value == "COMMITTED"
            assert not backup_dir.exists()
        finally:
            journal.close()


def test_commit_reconciles_missing_detached_backup_without_pending(
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal.abandon(managed))
            backup_dir = backup.parent
            backup.unlink()

            assert journal.commit() == []

            assert journal._detached_backup_paths == {}
            assert journal._backup_dir is None
            assert journal._state.value == "COMMITTED"
            assert not backup_dir.exists()
        finally:
            journal.close()


@pytest.mark.parametrize(
    "change",
    ["equal-size-modified", "replaced"],
)
def test_rollback_rejects_changed_backup_without_touching_publication(
    tmp_path, change
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    changed = b"bad-output"
    assert len(changed) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            published_state = managed.stat()
            journal.bind_published(
                managed,
                (published_state.st_dev, published_state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            if change == "equal-size-modified":
                with backup.open("r+b") as stream:
                    stream.write(changed)
                    stream.flush()
                    os.fsync(stream.fileno())
                expected_backup = changed
            else:
                backup.unlink()
                backup.write_bytes(original)
                expected_backup = original

            with pytest.raises(
                _download._ManagedOutputRollbackError,
                match="could not restore 1 managed output",
            ):
                journal.rollback()

            assert managed.read_bytes() == published
            assert backup.read_bytes() == expected_backup
            assert str(managed) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


@pytest.mark.parametrize("change", ["missing", "replaced"])
def test_recovery_paths_omit_unverified_bound_backup(tmp_path, change):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    unrelated = b"user-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            backup.unlink()
            if change == "replaced":
                backup.write_bytes(unrelated)

            entries_before = dict(journal._entries)

            assert journal.recovery_paths() == ()
            assert journal._entries == entries_before
            if change == "missing":
                assert not backup.exists()
            else:
                assert backup.read_bytes() == unrelated
        finally:
            journal.close()


def test_recovery_paths_reports_verified_restored_canonical_copy(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            real_cleanup = journal._cleanup_restore_anchor

            def remove_anchor_then_fail(anchor_name, expected):
                real_cleanup(anchor_name, expected)
                raise _download._ManagedOutputDirectoryCleanupError(
                    journal._backup_path(anchor_name),
                    PermissionError("injected post-anchor cleanup failure"),
                )

            monkeypatch.setattr(
                journal,
                "_cleanup_restore_anchor",
                remove_anchor_then_fail,
            )

            with pytest.raises(
                _download._ManagedOutputDirectoryCleanupError,
                match="injected post-anchor cleanup failure",
            ):
                journal.restore(managed, cleanup=False)

            entry = journal._entries[str(managed)]
            anchor = Path(journal._backup_dir) / entry["backup"]
            assert not anchor.exists()
            assert managed.read_bytes() == original
            assert journal.recovery_paths() == (str(managed),)
        finally:
            journal.close()


def test_recovery_paths_reports_verified_restore_anchor_only(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    unrelated = b"user-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            stable_managed_file = _download._stable_managed_file
            replaced = []

            def replace_restored_canonical(
                pinned,
                name,
                expected=None,
            ):
                if (
                    not replaced
                    and name == managed.name
                    and expected == _owned_entry(original)
                    and managed.read_bytes() == original
                ):
                    managed.unlink()
                    managed.write_bytes(unrelated)
                    replaced.append(True)
                return stable_managed_file(
                    pinned,
                    name,
                    expected=expected,
                )

            monkeypatch.setattr(
                _download,
                "_stable_managed_file",
                replace_restored_canonical,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="during rollback",
            ) as caught:
                journal.restore(managed, cleanup=False)

            entry = journal._entries[str(managed)]
            anchor = Path(journal._backup_dir) / entry["backup"]
            assert replaced == [True]
            assert managed.read_bytes() == unrelated
            assert anchor.read_bytes() == original
            assert caught.value.recovery_paths == (str(anchor),)
            assert journal.recovery_paths() == (str(anchor),)
        finally:
            journal.close()


def test_two_entry_commit_preflights_all_entries_before_deletion(
    tmp_path,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    first = out_dir / "first.csv"
    second = out_dir / "second.csv"
    originals = {
        first: b"old-one",
        second: b"old-two",
    }
    published = {
        first: b"new-one",
        second: b"new-two",
    }
    for path, payload in originals.items():
        path.write_bytes(payload)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            for path in (first, second):
                assert journal.prepare(
                    path,
                    expected=_owned_entry(originals[path]),
                )
                path.write_bytes(published[path])
                state = path.stat()
                journal.bind_published(
                    path,
                    (state.st_dev, state.st_ino),
                    len(published[path]),
                    hashlib.sha256(published[path]).hexdigest(),
                )
            backups = sorted(Path(journal._backup_dir).iterdir())
            assert len(backups) == 2
            with second.open("r+b") as stream:
                stream.write(b"bad-two")
                stream.flush()
                os.fsync(stream.fileno())

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ):
                journal.commit()

            assert [path.read_bytes() for path in backups] == [
                originals[first],
                originals[second],
            ]
            assert set(journal._entries) == {
                str(first),
                str(second),
            }
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


@pytest.mark.parametrize(
    "change",
    ["equal-size-modified", "replaced", "missing"],
)
def test_discard_rejects_changed_backup_and_retains_entry(
    tmp_path, change
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    changed = b"bad-output"
    assert len(changed) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            if change == "equal-size-modified":
                with backup.open("r+b") as stream:
                    stream.write(changed)
                    stream.flush()
                    os.fsync(stream.fileno())
                expected_backup = changed
            elif change == "replaced":
                backup.unlink()
                backup.write_bytes(original)
                expected_backup = original
            else:
                backup.unlink()
                expected_backup = None

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before discard",
            ) as caught:
                journal.discard(managed)

            assert not managed.exists()
            if expected_backup is None:
                assert not backup.exists()
            else:
                assert backup.read_bytes() == expected_backup
            assert str(managed) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
            assert caught.value.recovery_paths == ()
        finally:
            journal.close()


def test_initial_symlink_output_root_is_rejected_before_download(
    tmp_path, monkeypatch
):
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    output = tmp_path / "out"
    output.symlink_to(target, target_is_directory=True)
    download_calls = []

    monkeypatch.setattr(
        _download,
        "download_file",
        lambda *_args, **_kwargs: download_calls.append(True),
    )

    with pytest.raises(
        ValueError,
        match="stable no-follow directory",
    ):
        _download.download_candidate(
            _candidate("table.csv", "https://x/table.csv"),
            str(output),
        )

    assert download_calls == []
    assert output.is_symlink()
    assert sentinel.read_bytes() == b"keep"
    assert not (target / "table.csv").exists()
    assert not (target / _download.SOURCE_SIDECAR).exists()


def test_remove_managed_files_preserves_replacement_at_final_boundary(
    tmp_path,
    monkeypatch,
):
    managed = tmp_path / "managed.csv"
    original = b"managed-output"
    replacement = b"user-replacement"
    managed.write_bytes(original)
    stable_managed_file = _download._stable_managed_file
    replaced = []

    def replace_after_fingerprint(output, name, expected=None):
        state = stable_managed_file(
            output,
            name,
            expected=expected,
        )
        if not replaced and name == managed.name:
            os.unlink(name, dir_fd=output.fd)
            replacement_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=output.fd,
            )
            try:
                os.write(replacement_fd, replacement)
                os.fsync(replacement_fd)
            finally:
                os.close(replacement_fd)
            replaced.append(True)
        return state

    monkeypatch.setattr(
        _download,
        "_stable_managed_file",
        replace_after_fingerprint,
    )

    assert _download._remove_managed_files(
        str(tmp_path),
        {managed.name: _owned_entry(original)},
    ) == [managed.name]
    assert replaced == [True]
    assert managed.read_bytes() == replacement


def test_download_staging_cleanup_preserves_replacement_and_reports(
    tmp_path,
    monkeypatch,
):
    replacement = b"user-staging-replacement"
    replaced_paths = []

    def replace_failed_download_staging(url, staging, **kwargs):
        _write_download_bytes(staging, b"partial-download")
        replaced_paths.append(_replace_visible_owned_entry(
            staging.output,
            staging.fd,
            prefix=".paperconan-download-",
            replacement=replacement,
        ))
        return {
            "ok": False,
            "path": staging,
            "skipped_reason": "download unavailable",
        }

    monkeypatch.setattr(
        _download,
        "download_file",
        replace_failed_download_staging,
    )

    result = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(tmp_path),
    )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == replacement
    assert result["downloaded"] == []
    assert result["skipped"][:2] == [
        {
            "name": "table.csv",
            "reason": "download unavailable",
        },
        {
            "name": "table.csv",
            "reason": (
                "download staging cleanup incomplete: deletion failed"
            ),
        },
    ]
    assert replaced_paths[0].name not in repr(result["skipped"])


def test_download_staging_allocation_failure_preserves_replacement(
    tmp_path,
    monkeypatch,
):
    replacement = b"user-allocation-replacement"
    replaced_paths = []
    verify = _download._PinnedOutputDirectory.verify

    def replace_before_allocation_cleanup(output):
        verify(output)
        names = [
            name
            for name in os.listdir(output.fd)
            if name.startswith(".paperconan-download-")
        ]
        if names and not replaced_paths:
            name = names[0]
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=output.fd,
            )
            try:
                replaced_paths.append(_replace_visible_owned_entry(
                    output,
                    fd,
                    prefix=".paperconan-download-",
                    replacement=replacement,
                ))
            finally:
                os.close(fd)
            raise ValueError("injected final output verification failure")

    monkeypatch.setattr(
        _download._PinnedOutputDirectory,
        "verify",
        replace_before_allocation_cleanup,
    )

    with _download._pinned_output_directory(str(tmp_path)) as output:
        with pytest.raises(
            _download._TransientCleanupError,
            match="^transient file cleanup failed$",
        ) as caught:
            _download._download_staging_file(
                output,
                prefix=".paperconan-download-",
                suffix=".csv",
            )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == replacement
    assert isinstance(caught.value.operation_error, ValueError)
    assert str(tmp_path) not in str(caught.value)
    assert replaced_paths[0].name not in str(caught.value)


def test_publication_staging_cleanup_preserves_replacement(
    tmp_path,
    monkeypatch,
):
    replacement = b"user-publication-replacement"
    replaced_paths = []
    hash_exact_fd = _download._hash_exact_fd

    with _download._pinned_output_directory(str(tmp_path)) as output:
        def replace_after_publication_hash(fd, size):
            digest = hash_exact_fd(fd, size)
            if not replaced_paths:
                replaced_paths.append(_replace_visible_owned_entry(
                    output,
                    fd,
                    prefix=".paperconan-publish-",
                    replacement=replacement,
                ))
                raise _download._UnstableRegularFileError(
                    "injected publication verification failure"
                )
            return digest

        monkeypatch.setattr(
            _download,
            "_hash_exact_fd",
            replace_after_publication_hash,
        )

        with pytest.raises(
            _download._PublicationRecoveryError,
            match="^publication staging cleanup incomplete$",
        ):
            _download._write_collision_safe(
                output,
                "table.csv",
                b"a,b\n1,2\n",
            )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == replacement
    assert not (tmp_path / "table.csv").exists()


def test_source_sidecar_staging_cleanup_preserves_replacement(
    tmp_path,
    monkeypatch,
):
    replacement = b"user-sidecar-staging-replacement"
    replaced_paths = []
    hash_exact_fd = _download._hash_exact_fd

    with _download._pinned_output_directory(str(tmp_path)) as output:
        def replace_after_sidecar_hash(fd, size):
            digest = hash_exact_fd(fd, size)
            if not replaced_paths:
                replaced_paths.append(_replace_visible_owned_entry(
                    output,
                    fd,
                    prefix=f".{_download.SOURCE_SIDECAR}.",
                    replacement=replacement,
                ))
                raise _download._UnstableRegularFileError(
                    "injected sidecar verification failure"
                )
            return digest

        monkeypatch.setattr(
            _download,
            "_hash_exact_fd",
            replace_after_sidecar_hash,
        )

        with pytest.raises(
            _download._SourceSidecarPublicationError,
            match="^source sidecar staging cleanup incomplete$",
        ):
            _download._write_source_sidecar(
                {
                    "cand_id": "source:1",
                    "source": "source",
                },
                output,
                downloads=[],
            )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == replacement
    assert not (tmp_path / _download.SOURCE_SIDECAR).exists()


def test_recovery_paths_omit_stale_backup_path_after_parent_rename(
    tmp_path,
):
    parent = tmp_path / "parent"
    out_dir = parent / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir(parents=True)
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            stale_backup = Path(journal._backup_dir) / "0"
            parent.rename(displaced)
            moved_backup = (
                displaced / stale_backup.relative_to(parent)
            )

            assert journal.recovery_paths() == ()
            assert not stale_backup.exists()
            assert moved_backup.read_bytes() == original
        finally:
            journal.close()


def test_commit_final_backup_unlink_preserves_replacement_and_requires_recovery(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    replacement = b"bad-output"
    assert len(replacement) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            final_unlink = (
                journal._unlink_backup_after_visible_commit_check
            )
            replacements = []

            def replace_at_final_unlink(*args, **kwargs):
                if not replacements:
                    _replace_named_entry(
                        journal._backup_fd,
                        backup.name,
                        replacement,
                    )
                    replacements.append(True)
                return final_unlink(*args, **kwargs)

            monkeypatch.setattr(
                journal,
                "_unlink_backup_after_visible_commit_check",
                replace_at_final_unlink,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before journal commit",
            ):
                journal.commit()

            assert replacements == [True]
            assert backup.read_bytes() == replacement
            assert managed.read_bytes() == published
            assert str(managed) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


def test_restore_anchor_final_cleanup_preserves_replacement_and_reports(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    replacement = b"bad-output"
    assert len(replacement) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            entry = journal._entries[str(managed)]
            backup_state = entry["backup_state"]
            anchor_name = journal._ensure_restore_anchor(
                entry["backup"],
                backup_state,
            )
            anchor = Path(journal._backup_dir) / anchor_name
            backup = Path(journal._backup_dir) / entry["backup"]
            verify_backup_entry = journal._verify_backup_entry
            replacements = []

            def replace_after_verification(name, expected, **kwargs):
                result = verify_backup_entry(
                    name,
                    expected,
                    **kwargs,
                )
                if name == anchor_name and not replacements:
                    _replace_named_entry(
                        journal._backup_fd,
                        anchor_name,
                        replacement,
                    )
                    replacements.append(True)
                return result

            monkeypatch.setattr(
                journal,
                "_verify_backup_entry",
                replace_after_verification,
            )

            with pytest.raises(
                _download._ManagedOutputDirectoryCleanupError,
                match="managed-output rollback entry changed",
            ) as caught:
                journal._cleanup_restore_anchor(
                    anchor_name,
                    backup_state,
                )

            assert replacements == [True]
            assert caught.value.backup_dir == str(anchor)
            assert anchor.read_bytes() == replacement
            assert backup.read_bytes() == original
        finally:
            journal.close()


def test_discard_final_backup_cleanup_preserves_replacement(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    replacement = b"bad-output"
    assert len(replacement) == len(original)
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal._backup_dir) / "0"
            verify_backup_entry = journal._verify_backup_entry
            replacements = []

            def replace_after_verification(name, expected, **kwargs):
                result = verify_backup_entry(
                    name,
                    expected,
                    **kwargs,
                )
                if name == backup.name and not replacements:
                    _replace_named_entry(
                        journal._backup_fd,
                        backup.name,
                        replacement,
                    )
                    replacements.append(True)
                return result

            monkeypatch.setattr(
                journal,
                "_verify_backup_entry",
                replace_after_verification,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="changed before discard",
            ):
                journal.discard(managed)

            assert replacements == [True]
            assert backup.read_bytes() == replacement
            assert str(managed) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


def test_commit_final_directory_rebind_preserves_replacement_and_binding(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)
            displaced = tmp_path / "displaced-rollback"
            verify_backup_dir = journal._verify_backup_dir
            replacements = []

            def rebind_empty_directory_at_final_boundary():
                verify_backup_dir()
                if (
                    not replacements
                    and backup_dir.is_dir()
                    and list(backup_dir.iterdir()) == []
                ):
                    backup_dir.rename(displaced)
                    backup_dir.mkdir()
                    replacements.append(True)

            monkeypatch.setattr(
                journal,
                "_verify_backup_dir",
                rebind_empty_directory_at_final_boundary,
            )

            pending = journal.commit()
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert replacements == [True]
            assert backup_dir.is_dir()
            assert list(backup_dir.iterdir()) == []
            assert displaced.is_dir()
            assert list(displaced.iterdir()) == []
            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert journal._backup_dir == str(backup_dir)
            assert journal._backup_fd >= 0
            assert journal._backup_parent_fd >= 0
            assert journal._state.value == "COMMIT_CLEANUP"
        finally:
            journal.close()


def test_commit_unlink_syscall_boundary_fails_closed_and_stays_pending(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    replacement = b"user-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            real_unlink = _download.os.unlink
            boundary_replacements = []

            def replace_immediately_before_unlink(
                path,
                *args,
                **kwargs,
            ):
                if (
                    not boundary_replacements
                    and os.fspath(path) == backup.name
                    and kwargs.get("dir_fd") == journal._backup_fd
                ):
                    real_unlink(path, *args, **kwargs)
                    replacement_fd = os.open(
                        path,
                        (
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | os.O_NOFOLLOW
                        ),
                        0o600,
                        dir_fd=journal._backup_fd,
                    )
                    try:
                        os.write(replacement_fd, replacement)
                        os.fsync(replacement_fd)
                    finally:
                        os.close(replacement_fd)
                    boundary_replacements.append(True)
                return real_unlink(path, *args, **kwargs)

            monkeypatch.setattr(
                _download.os,
                "unlink",
                replace_immediately_before_unlink,
            )
            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )

            pending = journal.commit()
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert boundary_replacements == []
            assert backup.read_bytes() == original
            assert managed.read_bytes() == published
            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert str(managed) in journal._entries
            assert journal._state.value == "COMMIT_CLEANUP"
        finally:
            journal.close()


def test_commit_rmdir_syscall_boundary_fails_closed_and_stays_pending(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            backup_dir = Path(journal._ensure_backup_dir())
            displaced = tmp_path / "displaced-rollback"
            replacement = backup_dir
            real_rmdir = _download.os.rmdir
            boundary_rebindings = []

            def rebind_immediately_before_rmdir(
                path,
                *args,
                **kwargs,
            ):
                if (
                    not boundary_rebindings
                    and os.fspath(path) == backup_dir.name
                    and kwargs.get("dir_fd")
                    == journal._backup_parent_fd
                ):
                    backup_dir.rename(displaced)
                    replacement.mkdir()
                    boundary_rebindings.append(True)
                return real_rmdir(path, *args, **kwargs)

            monkeypatch.setattr(
                _download.os,
                "rmdir",
                rebind_immediately_before_rmdir,
            )
            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )

            pending = journal.commit()
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert boundary_rebindings == []
            assert backup_dir.is_dir()
            assert not displaced.exists()
            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert journal._backup_dir == str(backup_dir)
            assert journal._backup_fd >= 0
            assert journal._backup_parent_fd >= 0
            assert journal._state.value == "COMMIT_CLEANUP"
        finally:
            journal.close()


def test_commit_identity_bound_cleanup_capability_retries_and_converges(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)
    real_unlink = _download.os.unlink
    real_rmdir = _download.os.rmdir
    cleanup_calls = []

    def identity_bound_remove(
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        cleanup_calls.append((os.fspath(name), is_directory))
        if len(cleanup_calls) == 1:
            raise PermissionError(
                "injected identity-bound cleanup retry"
            )
        opened = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        assert (opened.st_dev, opened.st_ino) == (
            visible.st_dev,
            visible.st_ino,
        )
        if is_directory:
            real_rmdir(name, dir_fd=directory_fd)
        else:
            real_unlink(name, dir_fd=directory_fd)

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        identity_bound_remove,
        raising=False,
    )

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_dir = Path(journal._backup_dir)

            assert journal.commit() == []

            assert cleanup_calls == [
                (managed.name, False),
                (managed.name, False),
                ("0", False),
                (backup_dir.name, True),
            ]
            assert journal._entries == {}
            assert journal._backup_dir is None
            assert journal._state.value == "COMMITTED"
            assert managed.read_bytes() == published
            assert not backup_dir.exists()
        finally:
            journal.close()


def test_successful_publication_cleanup_replacement_is_reported_and_managed(
    tmp_path,
    monkeypatch,
):
    payload = b"a,b\n1,2\n"
    staging_replacement = b"user-publication-staging"
    published = tmp_path / "table.csv"
    replaced_paths = []

    def source_download(url, destination, **kwargs):
        _write_download_bytes(destination, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
        }

    real_link = _download.os.link

    def replace_publication_staging_after_link(
        src,
        dest,
        *args,
        **kwargs,
    ):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replaced_paths
            and Path(src).name.startswith(".paperconan-publish-")
            and Path(dest).name == published.name
            and kwargs.get("src_dir_fd") is not None
        ):
            _replace_named_entry(
                kwargs["src_dir_fd"],
                src,
                staging_replacement,
            )
            replaced_paths.append(tmp_path / os.fspath(src))
        return result

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os,
        "link",
        replace_publication_staging_after_link,
    )

    result = _download.download_candidate(
        _candidate(published.name, "https://x/table.csv"),
        str(tmp_path),
    )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == staging_replacement
    assert published.read_bytes() == payload
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"] == {
        published.name: _owned_entry(payload),
    }
    assert result["downloaded"] == [str(published)]
    assert result["skipped"] == [{
        "name": published.name,
        "reason": "publication staging cleanup incomplete",
    }]
    assert replaced_paths[0].name not in repr(result["skipped"])


def test_initial_sidecar_success_survives_staging_cleanup_replacement(
    tmp_path,
    monkeypatch,
):
    payload = b"a,b\n1,2\n"
    staging_replacement = b"user-sidecar-staging"
    published = tmp_path / "table.csv"
    replaced_paths = []
    commit_after_sidecar_calls = []
    rollback_calls = []

    def source_download(url, destination, **kwargs):
        _write_download_bytes(destination, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
        }

    real_link = _download.os.link
    real_commit_after_sidecar = (
        _download._ManagedOutputJournal.commit_after_sidecar
    )
    real_rollback = _download._ManagedOutputJournal.rollback

    def replace_sidecar_staging_after_link(
        src,
        dest,
        *args,
        **kwargs,
    ):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replaced_paths
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            _replace_named_entry(
                kwargs["src_dir_fd"],
                src,
                staging_replacement,
            )
            replaced_paths.append(tmp_path / os.fspath(src))
        return result

    def track_commit_after_sidecar(journal):
        if (
            journal._backup_prefix
            == ".paperconan-output-rollback-"
        ):
            commit_after_sidecar_calls.append(True)
        return real_commit_after_sidecar(journal)

    def track_rollback(journal):
        if (
            journal._backup_prefix
            == ".paperconan-output-rollback-"
        ):
            rollback_calls.append(True)
        return real_rollback(journal)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os,
        "link",
        replace_sidecar_staging_after_link,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit_after_sidecar",
        track_commit_after_sidecar,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "rollback",
        track_rollback,
    )

    result = _download.download_candidate(
        _candidate(published.name, "https://x/table.csv"),
        str(tmp_path),
    )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == staging_replacement
    assert commit_after_sidecar_calls == [True]
    assert rollback_calls == []
    assert published.read_bytes() == payload
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"][published.name] == _owned_entry(
        payload
    )
    assert result["downloaded"] == [str(published)]
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar staging cleanup incomplete",
    }]
    assert replaced_paths[0].name not in repr(result["skipped"])


def test_replacement_sidecar_success_survives_staging_cleanup_replacement(
    tmp_path,
    monkeypatch,
):
    managed = tmp_path / "table.csv"
    managed.write_bytes(b"table")
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )
    staging_replacement = b"user-sidecar-staging"
    replaced_paths = []
    rollback_dirs_before = set(
        tmp_path.parent.glob(".paperconan-sidecar-rollback-*")
    )
    real_link = _download.os.link

    def replace_sidecar_staging_after_link(
        src,
        dest,
        *args,
        **kwargs,
    ):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replaced_paths
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            _replace_named_entry(
                kwargs["src_dir_fd"],
                src,
                staging_replacement,
            )
            replaced_paths.append(tmp_path / os.fspath(src))
        return result

    monkeypatch.setattr(
        _download.os,
        "link",
        replace_sidecar_staging_after_link,
    )

    with _download._pinned_output_directory(str(tmp_path)) as output:
        result = _download._write_source_sidecar(
            {
                "cand_id": "source:new",
                "source": "source",
            },
            output,
            {managed.name: _owned_entry(managed.read_bytes())},
            downloads=[],
        )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == staging_replacement
    assert result.cleanup_warning == (
        "source sidecar staging cleanup incomplete"
    )
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["cand_id"] == "source:new"
    assert sidecar["managed_files"] == {
        managed.name: _owned_entry(managed.read_bytes()),
    }
    assert set(
        tmp_path.parent.glob(".paperconan-sidecar-rollback-*")
    ) == rollback_dirs_before


def test_existing_managed_refresh_remains_consistent_after_sidecar_cleanup(
    tmp_path,
    monkeypatch,
):
    managed = tmp_path / "table.csv"
    original = b"old-output"
    refreshed = b"new-output"
    managed.write_bytes(original)
    _write_owned_sidecar(
        tmp_path,
        [managed.name],
        cand_id="source:old",
    )
    staging_replacement = b"user-sidecar-staging"
    replaced_paths = []
    commit_after_sidecar_calls = []
    rollback_calls = []

    def source_download(url, destination, **kwargs):
        _write_download_bytes(destination, refreshed)
        return {
            "ok": True,
            "path": destination,
            "size": len(refreshed),
        }

    real_link = _download.os.link
    real_commit_after_sidecar = (
        _download._ManagedOutputJournal.commit_after_sidecar
    )
    real_rollback = _download._ManagedOutputJournal.rollback

    def replace_sidecar_staging_after_link(
        src,
        dest,
        *args,
        **kwargs,
    ):
        result = real_link(src, dest, *args, **kwargs)
        if (
            not replaced_paths
            and _is_staged_sidecar_publication(src, dest, kwargs)
        ):
            _replace_named_entry(
                kwargs["src_dir_fd"],
                src,
                staging_replacement,
            )
            replaced_paths.append(tmp_path / os.fspath(src))
        return result

    def track_commit_after_sidecar(journal):
        if (
            journal._backup_prefix
            == ".paperconan-output-rollback-"
        ):
            commit_after_sidecar_calls.append(True)
        return real_commit_after_sidecar(journal)

    def track_rollback(journal):
        if (
            journal._backup_prefix
            == ".paperconan-output-rollback-"
        ):
            rollback_calls.append(True)
        return real_rollback(journal)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os,
        "link",
        replace_sidecar_staging_after_link,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "commit_after_sidecar",
        track_commit_after_sidecar,
    )
    monkeypatch.setattr(
        _download._ManagedOutputJournal,
        "rollback",
        track_rollback,
    )

    result = _download.download_candidate(
        _candidate(managed.name, "https://x/table.csv"),
        str(tmp_path),
    )

    assert len(replaced_paths) == 1
    assert replaced_paths[0].read_bytes() == staging_replacement
    assert commit_after_sidecar_calls == [True]
    assert rollback_calls == []
    assert managed.read_bytes() == refreshed
    sidecar = json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["managed_files"][managed.name] == _owned_entry(
        refreshed
    )
    assert result["downloaded"] == [str(managed)]
    assert result["skipped"] == [{
        "name": _download.SOURCE_SIDECAR,
        "reason": "source sidecar staging cleanup incomplete",
    }]


def test_commit_unavailable_detached_cleanup_reports_generic_pending(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal.abandon(managed))
            real_stat = _download.os.stat
            unavailable_checks = []

            def make_detached_verification_unavailable(
                path,
                *args,
                **kwargs,
            ):
                if (
                    os.fspath(path) == backup.name
                    and kwargs.get("dir_fd") == journal._backup_fd
                ):
                    unavailable_checks.append(os.fspath(path))
                    raise PermissionError(
                        "injected detached verification unavailable"
                    )
                return real_stat(path, *args, **kwargs)

            monkeypatch.setattr(
                _download.os,
                "stat",
                make_detached_verification_unavailable,
            )

            pending = journal.commit()
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert unavailable_checks
            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert str(backup) in journal._detached_backup_paths
            assert journal._state.value == "COMMIT_CLEANUP"
            assert backup.read_bytes() == original
        finally:
            journal.close()


def test_commit_parent_renamed_detached_cleanup_uses_generic_pending(
    tmp_path,
):
    parent = tmp_path / "parent"
    out_dir = parent / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir(parents=True)
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            backup = Path(journal.abandon(managed))
            parent.rename(displaced)
            moved_backup = displaced / backup.relative_to(parent)

            pending = journal.commit()
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert all("path" not in record for record in records)
            assert str(backup) in journal._detached_backup_paths
            assert journal._state.value == "COMMIT_CLEANUP"
            assert not backup.exists()
            assert moved_backup.read_bytes() == original
        finally:
            journal.close()


def test_commit_parent_renamed_bound_cleanup_uses_generic_pending(
    tmp_path,
    monkeypatch,
):
    parent = tmp_path / "parent"
    out_dir = parent / "out"
    displaced = tmp_path / "displaced"
    out_dir.mkdir(parents=True)
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    managed.write_bytes(original)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup = Path(journal._backup_dir) / "0"
            real_unlink = _download.os.unlink
            backup_copy_matches = journal._backup_copy_matches
            attempts = []
            renamed = []

            def fail_bound_backup_cleanup(path, *args, **kwargs):
                if (
                    os.fspath(path) == backup.name
                    and kwargs.get("dir_fd") == journal._backup_fd
                ):
                    attempts.append(os.fspath(path))
                    raise PermissionError(
                        "injected bound backup cleanup failure"
                    )
                return real_unlink(path, *args, **kwargs)

            def rename_parent_after_owned_check(backup_name, expected):
                matches = backup_copy_matches(backup_name, expected)
                if matches and not renamed:
                    parent.rename(displaced)
                    renamed.append(True)
                return matches

            monkeypatch.setattr(
                _download.os,
                "unlink",
                fail_bound_backup_cleanup,
            )
            monkeypatch.setattr(
                journal,
                "_backup_copy_matches",
                rename_parent_after_owned_check,
            )

            pending = journal.commit()
            moved_backup = displaced / backup.relative_to(parent)
            records = []
            _download._append_post_commit_cleanup_records(
                records,
                pending,
            )

            assert len(attempts) == 2
            assert renamed == [True]
            assert records == [{
                "name": "managed-output cleanup",
                "reason": "post-commit cleanup pending",
            }]
            assert all("path" not in record for record in records)
            assert str(managed) in journal._entries
            assert journal._state.value == "COMMIT_CLEANUP"
            assert not backup.exists()
            assert moved_backup.read_bytes() == original
        finally:
            journal.close()


def test_download_staging_cleanup_is_idempotent_after_incomplete_cleanup(
    tmp_path,
    monkeypatch,
):
    replacement = b"user-staging-replacement"

    with _download._pinned_output_directory(str(tmp_path)) as output:
        staging = _download._download_staging_file(
            output,
            prefix=".paperconan-download-",
            suffix=".csv",
        )
        staging_fd = staging.fd
        replacement_path = _replace_visible_owned_entry(
            output,
            staging.fd,
            prefix=".paperconan-download-",
            replacement=replacement,
        )
        real_close = _download.os.close
        close_calls = []

        def track_close(fd):
            close_calls.append(fd)
            return real_close(fd)

        monkeypatch.setattr(_download.os, "close", track_close)

        first = _download._cleanup_download_staging(staging)
        second = _download._cleanup_download_staging(staging)

        assert first == (
            "download staging cleanup incomplete: deletion failed"
        )
        assert second == first
        assert close_calls == [staging_fd]
        assert replacement_path.read_bytes() == replacement


@pytest.mark.parametrize(
    ("staging_kind", "prefix", "expected_warning"),
    [
        (
            "download",
            ".paperconan-download-",
            "download staging cleanup incomplete: deletion failed",
        ),
        (
            "publication",
            ".paperconan-publish-",
            "publication staging cleanup incomplete",
        ),
        (
            "sidecar",
            f".{_download.SOURCE_SIDECAR}.",
            "source sidecar staging cleanup incomplete",
        ),
    ],
)
def test_staging_unlink_syscall_boundary_fails_closed_and_reports(
    tmp_path,
    monkeypatch,
    staging_kind,
    prefix,
    expected_warning,
):
    replacement = b"unrelated-replacement"
    replacement_name = f"user-{staging_kind}-replacement.bin"
    replacement_path = tmp_path / replacement_name
    replacement_path.write_bytes(replacement)

    with _download._pinned_output_directory(str(tmp_path)) as output:
        staging = None
        if staging_kind == "download":
            staging = _download._download_staging_file(
                output,
                prefix=prefix,
                suffix=".csv",
            )
            _write_download_bytes(staging, b"download-staging")

        real_unlink = _download.os.unlink
        boundary_replacements = []

        def replace_immediately_before_unlink(path, *args, **kwargs):
            if (
                not boundary_replacements
                and os.fspath(path).startswith(prefix)
                and kwargs.get("dir_fd") == output.fd
            ):
                real_unlink(path, *args, **kwargs)
                os.rename(
                    replacement_name,
                    path,
                    src_dir_fd=output.fd,
                    dst_dir_fd=output.fd,
                )
                boundary_replacements.append(os.fspath(path))
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(
            _download,
            "_identity_bound_remove",
            _fail_identity_bound_cleanup,
        )
        monkeypatch.setattr(
            _download.os,
            "unlink",
            replace_immediately_before_unlink,
        )

        if staging_kind == "download":
            warning = _download._cleanup_download_staging(staging)
        elif staging_kind == "publication":
            published = _download._write_collision_safe(
                output,
                "table.csv",
                b"published-output",
                _return_entry=True,
            )
            warning = published.cleanup_warning
        else:
            result = _download._write_source_sidecar(
                {
                    "cand_id": "source:new",
                    "source": "source",
                },
                output,
                downloads=[],
            )
            warning = result.cleanup_warning

        assert boundary_replacements == []
        assert replacement_path.read_bytes() == replacement
        assert warning == expected_warning
        assert any(name.startswith(prefix) for name in os.listdir(output.fd))


def test_created_output_rollback_unlink_boundary_fails_closed(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    created = out_dir / "created.csv"
    published = b"published-output"
    replacement = b"unrelated-replacement"
    created.write_bytes(published)
    replacement_path = out_dir / "user-replacement.csv"
    replacement_path.write_bytes(replacement)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            state = created.stat()
            journal.record_created(
                created,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            real_unlink = _download.os.unlink
            boundary_replacements = []

            def replace_immediately_before_unlink(path, *args, **kwargs):
                if (
                    not boundary_replacements
                    and os.fspath(path) == created.name
                    and kwargs.get("dir_fd") == output.fd
                ):
                    real_unlink(path, *args, **kwargs)
                    os.rename(
                        replacement_path.name,
                        path,
                        src_dir_fd=output.fd,
                        dst_dir_fd=output.fd,
                    )
                    boundary_replacements.append(os.fspath(path))
                return real_unlink(path, *args, **kwargs)

            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )
            monkeypatch.setattr(
                _download.os,
                "unlink",
                replace_immediately_before_unlink,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="cleanup remains pending",
            ) as caught:
                journal.restore(created)

            assert boundary_replacements == []
            assert created.read_bytes() == published
            assert replacement_path.read_bytes() == replacement
            assert caught.value.recovery_paths == ()
            assert str(created) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


def test_restore_replace_syscall_boundary_never_overwrites_replacement(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    published = b"new-output"
    replacement = b"unrelated-replacement"
    managed.write_bytes(original)
    replacement_path = out_dir / "user-replacement.csv"
    replacement_path.write_bytes(replacement)

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            assert journal.prepare(
                managed,
                expected=_owned_entry(original),
            )
            managed.write_bytes(published)
            state = managed.stat()
            journal.bind_published(
                managed,
                (state.st_dev, state.st_ino),
                len(published),
                hashlib.sha256(published).hexdigest(),
            )
            backup_name = journal._entries[str(managed)]["backup"]
            real_replace = _download.os.replace
            boundary_replacements = []

            def replace_immediately_before_restore(
                src,
                dest,
                *args,
                **kwargs,
            ):
                if (
                    not boundary_replacements
                    and os.fspath(src) == backup_name
                    and os.fspath(dest) == managed.name
                    and kwargs.get("src_dir_fd") == journal._backup_fd
                    and kwargs.get("dst_dir_fd") == output.fd
                ):
                    os.unlink(managed.name, dir_fd=output.fd)
                    os.rename(
                        replacement_path.name,
                        managed.name,
                        src_dir_fd=output.fd,
                        dst_dir_fd=output.fd,
                    )
                    boundary_replacements.append(os.fspath(dest))
                return real_replace(src, dest, *args, **kwargs)

            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )
            monkeypatch.setattr(
                _download,
                "_identity_bound_mutation_available",
                lambda: False,
                raising=False,
            )
            monkeypatch.setattr(
                _download.os,
                "replace",
                replace_immediately_before_restore,
            )

            with pytest.raises(
                _download._ManagedOutputRecoveryRequiredError,
                match="cleanup remains pending",
            ) as caught:
                journal.restore(managed)

            assert boundary_replacements == []
            assert managed.read_bytes() == published
            assert replacement_path.read_bytes() == replacement
            assert caught.value.recovery_paths == ()
            assert str(managed) in journal._entries
            assert journal._state.value == "RECOVERY_REQUIRED"
        finally:
            journal.close()


def test_move_to_backup_replace_boundary_fails_before_mutation(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    replacement = b"unrelated-replacement"
    managed.write_bytes(original)
    replacement_path = out_dir / "user-replacement.csv"
    replacement_path.write_bytes(replacement)
    rollback_dirs_before = set(
        tmp_path.glob(".paperconan-output-rollback-*")
    )

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            real_replace = _download.os.replace
            boundary_replacements = []

            def replace_immediately_before_backup_move(
                src,
                dest,
                *args,
                **kwargs,
            ):
                if (
                    not boundary_replacements
                    and os.fspath(src) == managed.name
                    and kwargs.get("src_dir_fd") == output.fd
                    and kwargs.get("dst_dir_fd") == journal._backup_fd
                ):
                    os.unlink(managed.name, dir_fd=output.fd)
                    os.rename(
                        replacement_path.name,
                        managed.name,
                        src_dir_fd=output.fd,
                        dst_dir_fd=output.fd,
                    )
                    boundary_replacements.append(os.fspath(src))
                return real_replace(src, dest, *args, **kwargs)

            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )
            monkeypatch.setattr(
                _download,
                "_identity_bound_mutation_available",
                lambda: False,
                raising=False,
            )
            monkeypatch.setattr(
                _download.os,
                "replace",
                replace_immediately_before_backup_move,
            )

            with pytest.raises(
                _download._IdentityBoundMutationUnavailableError,
                match="identity-bound cleanup is unavailable",
            ):
                journal.prepare(
                    managed,
                    expected=_owned_entry(original),
                )

            assert boundary_replacements == []
            assert managed.read_bytes() == original
            assert replacement_path.read_bytes() == replacement
            assert journal._entries == {}
            assert set(
                tmp_path.glob(".paperconan-output-rollback-*")
            ) == rollback_dirs_before
        finally:
            journal.close()


def test_rollback_directory_error_cleanup_never_rmdirs_replacement(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    replacement_dir = tmp_path / "user-replacement"
    replacement_dir.mkdir()
    displaced_dir = tmp_path / "displaced-rollback"

    with _download._pinned_output_directory(str(out_dir)) as output:
        journal = _download._ManagedOutputJournal(output)
        try:
            real_open = _download.os.open
            real_rmdir = _download.os.rmdir
            boundary_replacements = []

            def fail_rollback_directory_open(path, flags, *args, **kwargs):
                if (
                    os.fspath(path).startswith(
                        ".paperconan-output-rollback-"
                    )
                    and kwargs.get("dir_fd") is not None
                ):
                    raise PermissionError(
                        "injected rollback directory open failure"
                    )
                return real_open(path, flags, *args, **kwargs)

            def replace_immediately_before_rmdir(path, *args, **kwargs):
                if (
                    not boundary_replacements
                    and os.fspath(path).startswith(
                        ".paperconan-output-rollback-"
                    )
                    and kwargs.get("dir_fd") is not None
                ):
                    os.rename(
                        path,
                        displaced_dir.name,
                        src_dir_fd=kwargs["dir_fd"],
                        dst_dir_fd=kwargs["dir_fd"],
                    )
                    os.rename(
                        replacement_dir.name,
                        path,
                        src_dir_fd=kwargs["dir_fd"],
                        dst_dir_fd=kwargs["dir_fd"],
                    )
                    boundary_replacements.append(os.fspath(path))
                return real_rmdir(path, *args, **kwargs)

            monkeypatch.setattr(
                _download,
                "_identity_bound_remove",
                _fail_identity_bound_cleanup,
            )
            monkeypatch.setattr(
                _download,
                "_identity_bound_mutation_available",
                lambda: False,
                raising=False,
            )
            monkeypatch.setattr(
                _download.os,
                "open",
                fail_rollback_directory_open,
            )
            monkeypatch.setattr(
                _download.os,
                "rmdir",
                replace_immediately_before_rmdir,
            )

            with pytest.raises(
                _download._IdentityBoundMutationUnavailableError,
                match="identity-bound cleanup is unavailable",
            ):
                journal._ensure_backup_dir()

            assert boundary_replacements == []
            assert replacement_dir.is_dir()
            assert not displaced_dir.exists()
            assert not list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
        finally:
            journal.close()


def test_unsupported_refresh_attempts_are_bounded_and_deterministic(
    tmp_path,
    monkeypatch,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)
    _write_owned_sidecar(
        out_dir,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    sidecar_bytes = sidecar.read_bytes()
    download_calls = []

    def unexpected_download(url, destination, **kwargs):
        download_calls.append(url)
        _write_download_bytes(destination, b"new-output")
        return {"ok": True, "path": destination}

    def disk_usage():
        return sum(
            path.stat().st_size
            for path in tmp_path.rglob("*")
            if path.is_file()
        )

    def hidden_state():
        return tuple(sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
            if (
                path.name.startswith(".paperconan-")
                or path.name.startswith(
                    f".{_download.SOURCE_SIDECAR}."
                )
            )
        ))

    monkeypatch.setattr(
        _download,
        "_identity_bound_mutation_available",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        _fail_identity_bound_cleanup,
    )
    monkeypatch.setattr(
        _download,
        "download_file",
        unexpected_download,
    )

    baseline_usage = disk_usage()
    baseline_hidden = hidden_state()
    results = [
        _download.download_candidate(
            _candidate(managed.name, "https://x/new"),
            str(out_dir),
        )
        for _attempt in range(4)
    ]

    expected = {
        "cand_id": "source:1",
        "out_dir": str(out_dir),
        "downloaded": [],
        "skipped": [{
            "name": "managed-output cleanup",
            "reason": (
                "managed-output refresh unavailable: "
                "identity-bound mutation is unavailable"
            ),
        }],
    }
    assert results == [expected] * 4
    assert download_calls == []
    assert managed.read_bytes() == original
    assert sidecar.read_bytes() == sidecar_bytes
    assert hidden_state() == baseline_hidden
    assert disk_usage() == baseline_usage


def test_managed_output_journal_requires_pinned_directory(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with pytest.raises(
        _download._UnstableRegularFileError,
        match="pinned output directory",
    ):
        _download._ManagedOutputJournal(str(out_dir))


def test_private_zip_snapshot_cleanup_fails_closed_and_reports_pending(
    tmp_path,
    monkeypatch,
):
    payload = b"PK\x05\x06" + (b"\0" * 18)
    warnings = []
    real_unlink = _download.os.unlink
    boundary_calls = []

    with _download._pinned_output_directory(str(tmp_path)) as output:
        def trap_snapshot_unlink(path, *args, **kwargs):
            if (
                os.fspath(path).startswith(
                    ".paperconan-zip-snapshot-"
                )
                and kwargs.get("dir_fd") == output.fd
            ):
                boundary_calls.append(os.fspath(path))
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(
            _download,
            "_identity_bound_remove",
            _fail_identity_bound_cleanup,
        )
        monkeypatch.setattr(
            _download.os,
            "unlink",
            trap_snapshot_unlink,
        )

        with _download._private_zip_snapshot(
            io.BytesIO(payload),
            max_bytes=len(payload),
            output=output,
            cleanup_warnings=warnings,
        ) as snapshot:
            assert snapshot.read() == payload

        assert boundary_calls == []
        assert warnings == [
            "private ZIP snapshot cleanup pending"
        ]
        assert any(
            name.startswith(".paperconan-zip-snapshot-")
            for name in os.listdir(output.fd)
        )


def test_private_zip_snapshot_signal_cleans_owned_staging(tmp_path):
    class Signal(BaseException):
        pass

    class SignalingSource(io.BytesIO):
        def read(self, size=-1):
            raise Signal("injected snapshot signal")

    warnings = []
    with _download._pinned_output_directory(str(tmp_path)) as output:
        with pytest.raises(Signal, match="injected snapshot signal"):
            with _download._private_zip_snapshot(
                SignalingSource(b"payload"),
                max_bytes=100,
                output=output,
                cleanup_warnings=warnings,
            ):
                raise AssertionError("snapshot setup must not complete")

        assert warnings == []
        assert not any(
            name.startswith(".paperconan-zip-snapshot-")
            for name in os.listdir(output.fd)
        )


class _StubFunlinkat:
    def __init__(self, results):
        self.argtypes = None
        self.restype = None
        self.results = list(results)
        self.calls = []

    def __call__(self, directory_fd, name, descriptor, flags):
        self.calls.append((directory_fd, name, descriptor, flags))
        result, error_number = self.results.pop(0)
        ctypes.set_errno(error_number)
        return result


class _StubLibc:
    def __init__(self, funlinkat):
        self.funlinkat = funlinkat


def test_load_funlinkat_configures_signature_and_flags(
    monkeypatch,
):
    symbol = _StubFunlinkat([(0, 0), (0, 0)])
    libc = _StubLibc(symbol)
    monkeypatch.setattr(_download.sys, "platform", "freebsd14")
    monkeypatch.setattr(
        _download.ctypes,
        "CDLL",
        lambda *args, **kwargs: libc,
    )

    remove = _download._load_funlinkat()

    assert symbol.argtypes == (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
    )
    assert symbol.restype is ctypes.c_int
    remove(7, "entry.csv", 11, is_directory=False)
    remove(7, "rollback", 13, is_directory=True)
    assert symbol.calls == [
        (7, b"entry.csv", 11, 0),
        (7, b"rollback", 13, _download._FREEBSD_AT_REMOVEDIR),
    ]


def test_load_funlinkat_propagates_errno_and_detects_unsupported(
    monkeypatch,
):
    symbol = _StubFunlinkat([
        (-1, errno.EPERM),
        (-1, errno.ENOSYS),
    ])
    libc = _StubLibc(symbol)
    monkeypatch.setattr(_download.sys, "platform", "freebsd14")
    monkeypatch.setattr(
        _download.ctypes,
        "CDLL",
        lambda *args, **kwargs: libc,
    )
    remove = _download._load_funlinkat()

    with pytest.raises(OSError) as denied:
        remove(7, "entry.csv", 11, is_directory=False)
    assert denied.value.errno == errno.EPERM
    assert denied.value.filename == "entry.csv"

    with pytest.raises(
        _download._IdentityBoundMutationUnavailableError,
        match="identity-bound cleanup is unavailable",
    ) as unsupported:
        remove(7, "entry.csv", 11, is_directory=False)
    assert unsupported.value.errno == errno.ENOSYS

    monkeypatch.setattr(_download.sys, "platform", "darwin")
    assert _download._load_funlinkat() is None

    class MissingSymbolLibc:
        pass

    monkeypatch.setattr(_download.sys, "platform", "freebsd14")
    monkeypatch.setattr(
        _download.ctypes,
        "CDLL",
        lambda *args, **kwargs: MissingSymbolLibc(),
    )
    assert _download._load_funlinkat() is None


class _FirstUnsupportedThenIdentityBoundRemove:
    def __init__(self, unsupported_errno):
        self.unsupported_errno = unsupported_errno
        self.calls = []

    def __call__(
        self,
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        self.calls.append((os.fspath(name), is_directory))
        if len(self.calls) == 1:
            raise _download._IdentityBoundMutationUnavailableError(
                self.unsupported_errno,
                "identity-bound cleanup is unavailable",
                os.fspath(name),
            )
        opened = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        assert (opened.st_dev, opened.st_ino) == (
            visible.st_dev,
            visible.st_ino,
        )
        if is_directory:
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _deterministic_rollback_name(out_dir, prefix):
    opened = out_dir.stat()
    identity_seed = (
        f"{opened.st_dev}:{opened.st_ino}:{prefix}"
    ).encode("ascii")
    return (
        f"{prefix}"
        f"{hashlib.sha256(identity_seed).hexdigest()[:16]}"
    )


def _transaction_state_snapshot(root):
    prefixes = (
        ".paperconan-download-",
        ".paperconan-member-",
        ".paperconan-archive-",
        ".paperconan-zip-snapshot-",
        ".paperconan-publish-",
        ".paperconan-output-rollback-",
        ".paperconan-sidecar-rollback-",
        f".{_download.SOURCE_SIDECAR}.",
    )
    snapshot = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not any(
            part.startswith(prefixes)
            for part in relative.parts
        ):
            continue
        current = path.lstat()
        snapshot.append((
            str(relative),
            stat.S_IFMT(current.st_mode),
            current.st_size,
        ))
    return tuple(sorted(snapshot))


def _pending_cleanup_result(out_dir):
    return {
        "cand_id": "source:1",
        "out_dir": str(out_dir),
        "downloaded": [],
        "skipped": [{
            "name": "managed-output cleanup",
            "reason": "managed-output cleanup remains pending",
        }],
    }


@pytest.mark.parametrize(
    "unsupported_errno",
    [errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP],
)
def test_runtime_unsupported_funlinkat_latches_and_bounds_later_attempts(
    tmp_path,
    monkeypatch,
    unsupported_errno,
):
    out_dir = tmp_path / "out"
    payload = b"new-output"
    download_calls = []
    funlinkat = _FirstUnsupportedThenIdentityBoundRemove(
        unsupported_errno
    )

    def source_download(url, destination, **kwargs):
        download_calls.append(url)
        _write_download_bytes(destination, payload)
        return {
            "ok": True,
            "path": destination,
            "size": len(payload),
        }

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        _PRODUCTION_IDENTITY_BOUND_REMOVE,
    )
    monkeypatch.setattr(
        _download,
        "_identity_bound_mutation_available",
        _PRODUCTION_IDENTITY_BOUND_MUTATION_AVAILABLE,
    )
    monkeypatch.setattr(_download, "_FUNLINKAT", funlinkat)
    monkeypatch.setattr(
        _download,
        "download_file",
        source_download,
    )

    first = _download.download_candidate(
        _candidate("table.csv", "https://x/table.csv"),
        str(out_dir),
    )
    managed = out_dir / "table.csv"
    sidecar = out_dir / _download.SOURCE_SIDECAR
    assert first["downloaded"] == [str(managed)]
    managed_bytes = managed.read_bytes()
    sidecar_bytes = sidecar.read_bytes()
    retained_state = _transaction_state_snapshot(tmp_path)
    later = []
    for _attempt in range(3):
        try:
            later.append(_download.download_candidate(
                _candidate("table.csv", "https://x/table.csv"),
                str(out_dir),
            ))
        except Exception as error:
            later.append(error)

    expected = _pending_cleanup_result(out_dir)
    assert later == [expected] * 3
    assert _download._identity_bound_mutation_available() is False
    assert len(funlinkat.calls) == 1
    assert download_calls == ["https://x/table.csv"]
    assert managed.read_bytes() == managed_bytes
    assert sidecar.read_bytes() == sidecar_bytes
    assert _transaction_state_snapshot(tmp_path) == retained_state
    cleanup_records = [
        record
        for result in later
        for record in result["skipped"]
    ]
    assert str(tmp_path) not in repr(cleanup_records)
    assert ".paperconan-" not in repr(cleanup_records)


@pytest.mark.parametrize(
    "orphan_kind",
    [
        "output_rollback",
        "sidecar_rollback",
        "download",
        "member",
        "archive",
        "zip_snapshot",
        "publication",
        "sidecar_staging",
    ],
)
def test_restart_orphaned_transaction_state_blocks_before_network(
    tmp_path,
    monkeypatch,
    orphan_kind,
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    managed = out_dir / "table.csv"
    original = b"old-output"
    managed.write_bytes(original)
    _write_owned_sidecar(
        out_dir,
        [managed.name],
        cand_id="source:old",
    )
    sidecar = out_dir / _download.SOURCE_SIDECAR
    sidecar_bytes = sidecar.read_bytes()
    rollback_prefixes = {
        "output_rollback": ".paperconan-output-rollback-",
        "sidecar_rollback": ".paperconan-sidecar-rollback-",
    }
    staging_names = {
        "download": ".paperconan-download-orphan.csv",
        "member": ".paperconan-member-orphan.csv",
        "archive": ".paperconan-archive-orphan.zip",
        "zip_snapshot": ".paperconan-zip-snapshot-orphan.zip",
        "publication": ".paperconan-publish-orphan",
        "sidecar_staging": (
            f".{_download.SOURCE_SIDECAR}.orphan.part"
        ),
    }
    if orphan_kind in rollback_prefixes:
        orphan = out_dir.parent / _deterministic_rollback_name(
            out_dir,
            rollback_prefixes[orphan_kind],
        )
        orphan.mkdir()
        (orphan / "recovery.bin").write_bytes(b"retained-recovery")
    else:
        orphan = out_dir / staging_names[orphan_kind]
        orphan.write_bytes(b"retained-staging")
    download_calls = []

    def unexpected_download(url, destination, **kwargs):
        download_calls.append(url)
        _write_download_bytes(destination, b"new-output")
        return {"ok": True, "path": destination}

    monkeypatch.setattr(
        _download,
        "download_file",
        unexpected_download,
    )
    assert _download._identity_bound_mutation_available() is True
    retained_state = _transaction_state_snapshot(tmp_path)
    results = []
    for _attempt in range(3):
        try:
            results.append(_download.download_candidate(
                _candidate(managed.name, "https://x/new"),
                str(out_dir),
            ))
        except Exception as error:
            results.append(error)

    expected = _pending_cleanup_result(out_dir)
    assert results == [expected] * 3
    assert download_calls == []
    assert managed.read_bytes() == original
    assert sidecar.read_bytes() == sidecar_bytes
    assert _transaction_state_snapshot(tmp_path) == retained_state
    cleanup_records = [
        record
        for result in results
        for record in result["skipped"]
    ]
    assert str(tmp_path) not in repr(cleanup_records)
    assert orphan.name not in repr(cleanup_records)


class _ConcurrentUnsupportedFunlinkat:
    def __init__(self, unsupported_errno):
        self.unsupported_errno = unsupported_errno
        self.a_entered = threading.Event()
        self.release_a = threading.Event()
        self.b_entered = threading.Event()
        self.release_b = threading.Event()
        self.b_completed = threading.Event()

    def __call__(
        self,
        directory_fd,
        name,
        descriptor,
        *,
        is_directory,
    ):
        assert not is_directory
        if os.fspath(name) == "a":
            self.a_entered.set()
            assert self.release_a.wait(5)
            raise _download._IdentityBoundMutationUnavailableError(
                self.unsupported_errno,
                "identity-bound cleanup is unavailable",
                os.fspath(name),
            )
        assert os.fspath(name) == "b"
        self.b_entered.set()
        assert self.release_b.wait(5)
        opened = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        assert (opened.st_dev, opened.st_ino) == (
            visible.st_dev,
            visible.st_ino,
        )
        os.unlink(name, dir_fd=directory_fd)
        self.b_completed.set()


def _threaded_identity_bound_remove(
    directory_fd,
    name,
    descriptor,
    results,
    done,
):
    try:
        _PRODUCTION_IDENTITY_BOUND_REMOVE(
            directory_fd,
            name,
            descriptor,
            is_directory=False,
        )
    except Exception as error:
        results[name] = error
    else:
        results[name] = "removed"
    finally:
        done.set()


@pytest.mark.parametrize(
    "unsupported_errno",
    [errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP],
)
def test_concurrent_funlinkat_caller_cannot_run_after_runtime_latch(
    tmp_path,
    monkeypatch,
    unsupported_errno,
):
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").write_bytes(b"b")
    directory_fd = os.open(
        tmp_path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    a_fd = os.open("a", os.O_RDONLY, dir_fd=directory_fd)
    b_fd = os.open("b", os.O_RDONLY, dir_fd=directory_fd)
    adapter = _ConcurrentUnsupportedFunlinkat(unsupported_errno)
    results = {}
    a_done = threading.Event()
    b_done = threading.Event()
    b_calling = threading.Event()

    def call_b():
        b_calling.set()
        _threaded_identity_bound_remove(
            directory_fd,
            "b",
            b_fd,
            results,
            b_done,
        )

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        _PRODUCTION_IDENTITY_BOUND_REMOVE,
    )
    monkeypatch.setattr(
        _download,
        "_identity_bound_mutation_available",
        _PRODUCTION_IDENTITY_BOUND_MUTATION_AVAILABLE,
    )
    monkeypatch.setattr(_download, "_FUNLINKAT", adapter)
    a_thread = threading.Thread(
        target=_threaded_identity_bound_remove,
        args=(
            directory_fd,
            "a",
            a_fd,
            results,
            a_done,
        ),
    )
    b_thread = threading.Thread(target=call_b)
    try:
        a_thread.start()
        assert adapter.a_entered.wait(5)
        b_thread.start()
        assert b_calling.wait(5)
        b_entered_before_latch = adapter.b_entered.wait(1)
        adapter.release_a.set()
        assert a_done.wait(5)
        assert _download._identity_bound_mutation_available() is False
        adapter.release_b.set()
        assert b_done.wait(5)
        a_thread.join(5)
        b_thread.join(5)

        assert not a_thread.is_alive()
        assert not b_thread.is_alive()
        assert b_entered_before_latch is False
        assert isinstance(
            results["a"],
            _download._IdentityBoundMutationUnavailableError,
        )
        assert isinstance(
            results["b"],
            _download._IdentityBoundMutationUnavailableError,
        )
        assert (tmp_path / "a").read_bytes() == b"a"
        assert (tmp_path / "b").read_bytes() == b"b"
        assert not adapter.b_completed.is_set()
    finally:
        adapter.release_a.set()
        adapter.release_b.set()
        a_thread.join(5)
        b_thread.join(5)
        os.close(a_fd)
        os.close(b_fd)
        os.close(directory_fd)


def test_runtime_latch_blocks_waiting_candidate_before_staging(
    tmp_path,
    monkeypatch,
):
    control = tmp_path / "control"
    control.mkdir()
    (control / "a").write_bytes(b"a")
    directory_fd = os.open(
        control,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    a_fd = os.open("a", os.O_RDONLY, dir_fd=directory_fd)
    adapter = _ConcurrentUnsupportedFunlinkat(errno.ENOSYS)
    results = {}
    a_done = threading.Event()
    candidate_done = threading.Event()
    candidate_calling = threading.Event()
    network_entered = threading.Event()
    allow_network = threading.Event()
    network_calls = []
    out_dir = tmp_path / "out"

    def source_download(url, destination, **kwargs):
        network_calls.append(url)
        network_entered.set()
        assert allow_network.wait(5)
        _write_download_bytes(destination, b"candidate-output")
        return {
            "ok": True,
            "path": destination,
            "size": len(b"candidate-output"),
        }

    def call_candidate():
        candidate_calling.set()
        try:
            results["candidate"] = _download.download_candidate(
                _candidate("table.csv", "https://x/table.csv"),
                str(out_dir),
            )
        except Exception as error:
            results["candidate"] = error
        finally:
            candidate_done.set()

    monkeypatch.setattr(
        _download,
        "_identity_bound_remove",
        _PRODUCTION_IDENTITY_BOUND_REMOVE,
    )
    monkeypatch.setattr(
        _download,
        "_identity_bound_mutation_available",
        _PRODUCTION_IDENTITY_BOUND_MUTATION_AVAILABLE,
    )
    monkeypatch.setattr(_download, "_FUNLINKAT", adapter)
    monkeypatch.setattr(
        _download,
        "download_file",
        source_download,
    )
    baseline_state = _transaction_state_snapshot(tmp_path)
    a_thread = threading.Thread(
        target=_threaded_identity_bound_remove,
        args=(
            directory_fd,
            "a",
            a_fd,
            results,
            a_done,
        ),
    )
    candidate_thread = threading.Thread(target=call_candidate)
    try:
        a_thread.start()
        assert adapter.a_entered.wait(5)
        candidate_thread.start()
        assert candidate_calling.wait(5)
        network_entered_before_latch = network_entered.wait(1)
        adapter.release_a.set()
        assert a_done.wait(5)
        assert _download._identity_bound_mutation_available() is False
        retained_after_latch = _transaction_state_snapshot(tmp_path)
        allow_network.set()
        assert candidate_done.wait(5)
        a_thread.join(5)
        candidate_thread.join(5)

        assert not a_thread.is_alive()
        assert not candidate_thread.is_alive()
        assert network_entered_before_latch is False
        assert network_calls == []
        assert results["candidate"] == _pending_cleanup_result(out_dir)
        assert retained_after_latch == baseline_state
        assert _transaction_state_snapshot(tmp_path) == baseline_state
        cleanup_records = results["candidate"]["skipped"]
        assert str(tmp_path) not in repr(cleanup_records)
        assert ".paperconan-" not in repr(cleanup_records)
    finally:
        adapter.release_a.set()
        adapter.release_b.set()
        allow_network.set()
        a_thread.join(5)
        candidate_thread.join(5)
        os.close(a_fd)
        os.close(directory_fd)
