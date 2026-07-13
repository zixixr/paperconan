import io
import json
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

from paperconan import _source_sidecar
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
        len(first_name),
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
        "limit": len(first_name),
        "managed_entries_inspected": 2,
        "managed_entries_retained": 1,
        "managed_name_bytes_retained": len(first_name),
        "requested_name_bytes": len(second_name),
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
        "managed_files": ["table.csv"],
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
        cand, str(tmp_path), ["table.csv"]
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
        Path(dest).write_bytes(b"x\n1\n")
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
    real_lexists = _download.os.path.lexists

    def occupied(path):
        probes.append(Path(path).name)
        if len(probes) > probe_limit:
            raise AssertionError(
                "collision limit must precede filesystem probe"
            )
        return True

    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT",
        probe_limit,
        raising=False,
    )
    monkeypatch.setattr(_download.os.path, "lexists", occupied)
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
    monkeypatch.setattr(_download.os.path, "lexists", real_lexists)


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

    def occupied(path):
        probes.append(Path(path).name)
        if len(probes) > 1:
            raise AssertionError(
                "archive collision limit must precede next probe"
            )
        return True

    monkeypatch.setattr(
        _download, "_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT", 1,
        raising=False,
    )
    monkeypatch.setattr(_download.os.path, "lexists", occupied)

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
            Path(dest).write_bytes(b"data")
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
            Path(dest).write_bytes(payload)
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
    assert result["skipped"] == [{
        "name": "second.csv",
        "reason": "source sidecar managed entry limit",
        "limit": 1,
        "managed_entries_retained": 1,
        "managed_name_bytes_retained": len("first.csv"),
        "omitted_entries_lower_bound": 1,
    }]
    assert json.loads(
        (tmp_path / _download.SOURCE_SIDECAR).read_text(
            encoding="utf-8"
        )
    )["managed_files"] == ["first.csv"]


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
            Path(dest).write_bytes(b"x")
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
            Path(dest).write_bytes(payload)
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
    ] == ["table.csv"]
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

    assert result["downloaded"] == []
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


def _real_sidecar_size(tmp_path, cand, managed_files):
    probe = tmp_path / "sidecar-probe"
    probe.mkdir(exist_ok=True)
    assert _download._write_source_sidecar(
        cand, str(probe), managed_files
    )
    return (probe / _download.SOURCE_SIDECAR).stat().st_size


def _fail_final_sidecar_commit(monkeypatch):
    replace = _download.os.replace
    failures = []

    def fail_sidecar_replace(src, dest):
        if Path(dest).name == _download.SOURCE_SIDECAR:
            failures.append(dest)
            raise OSError("sidecar commit failed")
        return replace(src, dest)

    monkeypatch.setattr(_download.os, "replace", fail_sidecar_replace)
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
            Path(dest).write_bytes(body)
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
            Path(dest).write_bytes(payload)
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
    if destination_state == "replacement":
        assert output.read_bytes() == b"old-output"
    else:
        assert not output.exists()
    assert stale.read_bytes() == b"stale"
    assert sidecar.read_bytes() == original_sidecar
    assert not list(out_dir.glob(".paperconan-output-rollback-*"))
    assert not list(tmp_path.glob(".paperconan-output-rollback-*"))
    assert not list(out_dir.glob(".paperconan-archive-*"))


def test_journal_cleanup_error_preserves_successful_sidecar_commit(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    _write_sidecar(out_dir, ["table.csv"], doi="10.x/old")

    def source_download(url, dest, **kwargs):
        Path(dest).write_bytes(b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_remove = _download.os.remove
    failures = []

    def fail_first_backup_remove(path):
        parent_name = Path(path).parent.name
        if (
            parent_name.startswith(".paperconan-output-rollback-")
            and not failures
        ):
            failures.append(os.fspath(path))
            raise PermissionError("injected journal cleanup failure")
        return real_remove(path)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os, "remove", fail_first_backup_remove
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
    assert sidecar["managed_files"] == ["table.csv"]
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
        Path(dest).write_bytes(b"new-output")
        return {"ok": True, "path": dest, "size": 10}

    real_remove = _download.os.remove
    attempts = []

    def fail_backup_remove(path):
        if Path(path).parent.name.startswith(
            ".paperconan-output-rollback-"
        ):
            attempts.append(os.fspath(path))
            raise PermissionError("persistent backup cleanup failure")
        return real_remove(path)

    monkeypatch.setattr(_download, "download_file", source_download)
    monkeypatch.setattr(
        _download.os, "remove", fail_backup_remove
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
    )["managed_files"] == ["table.csv"]
    assert result["downloaded"] == [str(output)]
    assert result["skipped"] == [{
        "name": backups[0].name,
        "reason": "post-commit cleanup pending",
        "path": str(backups[0]),
    }]


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
        Path(dest).write_bytes(b"new-output")
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
    )["managed_files"] == ["table.csv"]
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


def test_journal_failed_restore_remains_retryable_and_continues(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    first = out_dir / "first.csv"
    second = out_dir / "second.csv"
    first.write_bytes(b"old-first")
    second.write_bytes(b"old-second")
    journal = _download._ManagedOutputJournal(str(out_dir))
    journal.prepare(first)
    journal.prepare(second)
    first.write_bytes(b"new-first")
    second.write_bytes(b"new-second")
    second_dest = next(
        dest for dest in journal._entries
        if Path(dest).name == second.name
    )
    second_backup = Path(journal._entries[second_dest])
    real_replace = _download.os.replace
    failures = []

    def fail_once(src, dest):
        if (
            os.path.abspath(src) == os.path.abspath(second_backup)
            and not failures
        ):
            failures.append(os.fspath(src))
            raise PermissionError("injected restore failure")
        return real_replace(src, dest)

    monkeypatch.setattr(_download.os, "replace", fail_once)

    with pytest.raises(
        _download._ManagedOutputRollbackError,
        match="could not restore 1 managed output",
    ):
        journal.rollback()

    assert len(failures) == 1
    assert first.read_bytes() == b"old-first"
    assert second.read_bytes() == b"new-second"
    assert journal._entries == {second_dest: str(second_backup)}
    assert second_backup.read_bytes() == b"old-second"

    assert journal.rollback() == {second_dest}
    assert second.read_bytes() == b"old-second"
    assert journal._entries == {}
    assert not list(tmp_path.glob(".paperconan-output-rollback-*"))


@pytest.mark.parametrize("persistent", [False, True])
def test_journal_empty_backup_directory_cleanup_remains_retryable(
    tmp_path, monkeypatch, persistent
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / "table.csv"
    output.write_bytes(b"old-output")
    journal = _download._ManagedOutputJournal(str(out_dir))
    journal.prepare(output)
    output.write_bytes(b"new-output")
    backup_dir = Path(journal._backup_dir)
    real_rmdir = _download.os.rmdir
    failures = []

    def fail_cleanup(path):
        is_backup_dir = (
            os.path.abspath(path) == os.path.abspath(backup_dir)
        )
        if is_backup_dir and (persistent or not failures):
            failures.append(os.fspath(path))
            raise PermissionError(
                "injected rollback directory cleanup failure"
            )
        return real_rmdir(path)

    monkeypatch.setattr(_download.os, "rmdir", fail_cleanup)

    with pytest.raises(
        _download._ManagedOutputRollbackError,
        match="could not remove managed-output rollback directory",
    ) as first:
        journal.rollback()

    assert first.value.failures == ()
    assert first.value.cleanup_failure[0] == str(backup_dir)
    assert output.read_bytes() == b"old-output"
    assert journal._entries == {}
    assert journal._backup_dir == str(backup_dir)
    assert backup_dir.is_dir()
    assert list(backup_dir.iterdir()) == []

    if persistent:
        with pytest.raises(
            _download._ManagedOutputRollbackError,
            match="could not remove managed-output rollback directory",
        ) as second:
            journal.rollback()
        assert second.value.failures == ()
        assert second.value.cleanup_failure[0] == str(backup_dir)
        assert len(failures) == 2
        assert journal._backup_dir == str(backup_dir)
        assert backup_dir.is_dir()
    else:
        assert journal.rollback() == set()
        assert len(failures) == 1
        assert journal._backup_dir is None
        assert not backup_dir.exists()
    assert output.read_bytes() == b"old-output"
    assert journal._entries == {}


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
    operation_error = OSError("source operation failed")

    if channel == "direct":
        cand = _candidate(output.name, "https://x/table.csv")

        def source_download(url, dest, **kwargs):
            Path(dest).write_bytes(b"partial-output")
            raise operation_error

        monkeypatch.setattr(
            _download, "download_file", source_download
        )
    else:
        payload = _archive_payload(
            channel, [(f"nested/{output.name}", b"source-output")]
        )
        cand = {
            "cand_id": "source:1",
            "source": "source",
            "tabular_files": [],
            **_archive_fields(channel),
        }

        def archive_download(url, dest, **kwargs):
            Path(dest).write_bytes(payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

        def fail_member_write(src, dest, max_bytes):
            Path(dest).write_bytes(b"partial-output")
            raise operation_error

        monkeypatch.setattr(
            _download, "download_file", archive_download
        )
        monkeypatch.setattr(
            _download, "_atomic_stream_write", fail_member_write
        )

    failures = []
    if destination_state == "replacement":
        real_replace = _download.os.replace

        def fail_restore(src, dest):
            is_restore = (
                Path(src).parent.name.startswith(
                    ".paperconan-output-rollback-"
                )
                and os.path.abspath(dest) == os.path.abspath(output)
            )
            if is_restore and (persistent or not failures):
                failures.append((os.fspath(src), os.fspath(dest)))
                raise PermissionError("injected restore failure")
            return real_replace(src, dest)

        monkeypatch.setattr(_download.os, "replace", fail_restore)
    else:
        real_remove = _download.os.remove

        def fail_restore(path):
            is_restore = (
                os.path.abspath(path) == os.path.abspath(output)
            )
            if is_restore and (persistent or not failures):
                failures.append(os.fspath(path))
                raise PermissionError("injected restore failure")
            return real_remove(path)

        monkeypatch.setattr(_download.os, "remove", fail_restore)

    with pytest.raises(OSError, match="source operation failed") as caught:
        _download.download_candidate(cand, str(out_dir))

    assert caught.value is operation_error
    assert caught.value.__cause__ is not None
    assert sidecar.read_bytes() == original_sidecar
    if persistent:
        assert output.read_bytes() == b"partial-output"
        if destination_state == "replacement":
            rollback_dirs = list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
            assert len(rollback_dirs) == 1
            backups = list(rollback_dirs[0].iterdir())
            assert len(backups) == 1
            assert backups[0].read_bytes() == old_bytes
    else:
        assert not list(
            tmp_path.glob(".paperconan-output-rollback-*")
        )
        if destination_state == "replacement":
            assert output.read_bytes() == old_bytes
        else:
            assert not output.exists()


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
            Path(dest).write_bytes(body)
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
            Path(dest).write_bytes(payload)
            return {
                "ok": True,
                "path": dest,
                "size": len(payload),
            }

    monkeypatch.setattr(_download, "download_file", source_download)
    failures = []
    real_replace = _download.os.replace
    real_remove = _download.os.remove

    def fail_replace(src, dest):
        if Path(dest).name == _download.SOURCE_SIDECAR:
            raise OSError("sidecar commit failed")
        is_restore = (
            Path(src).parent.name.startswith(
                ".paperconan-output-rollback-"
            )
            and os.path.abspath(dest) == os.path.abspath(output)
        )
        if (
            destination_state == "replacement"
            and is_restore
            and (persistent or not failures)
        ):
            failures.append((os.fspath(src), os.fspath(dest)))
            raise PermissionError("injected restore failure")
        return real_replace(src, dest)

    def fail_remove(path):
        is_restore = os.path.abspath(path) == os.path.abspath(output)
        if (
            destination_state == "new"
            and is_restore
            and (persistent or not failures)
        ):
            failures.append(os.fspath(path))
            raise PermissionError("injected restore failure")
        return real_remove(path)

    monkeypatch.setattr(_download.os, "replace", fail_replace)
    monkeypatch.setattr(_download.os, "remove", fail_remove)

    with pytest.raises(
        _download._ManagedOutputRollbackError,
        match="could not restore 1 managed output",
    ):
        _download.download_candidate(cand, str(out_dir))

    assert sidecar.read_bytes() == original_sidecar
    if persistent:
        assert len(failures) == 2
        assert output.read_bytes() == body
        if destination_state == "replacement":
            rollback_dirs = list(
                tmp_path.glob(".paperconan-output-rollback-*")
            )
            assert len(rollback_dirs) == 1
            backups = list(rollback_dirs[0].iterdir())
            assert len(backups) == 1
            assert backups[0].read_bytes() == old_bytes
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
        Path(dest).write_bytes(b"first")
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


def _write_slightly_larger_previous_sidecar(
    out_dir, final_sidecar_size, max_shrink
):
    for title_size in range(1000):
        old_cand = {
            "cand_id": "old",
            "source": "source",
            "title": "x" * title_size,
            "tabular_files": [],
        }
        payload = _download._source_sidecar_bytes(
            old_cand, str(out_dir), ["old.csv"]
        )
        shrink = len(payload) - final_sidecar_size
        if 0 < shrink <= max_shrink:
            assert _download._write_source_sidecar(
                old_cand, str(out_dir), ["old.csv"]
            )
            return payload
    raise AssertionError("could not construct bounded sidecar shrink")


def test_direct_shrink_credit_rolls_back_on_sidecar_commit_failure(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "old.csv"
    old.write_bytes(b"old")
    sidecar = out_dir / _download.SOURCE_SIDECAR
    payload = b"new-data"
    cand = _candidate("new.csv", "https://x/new")
    final_sidecar_size = _real_sidecar_size(
        tmp_path, cand, ["new.csv", "old.csv"]
    )
    original_sidecar = _write_slightly_larger_previous_sidecar(
        out_dir, final_sidecar_size, len(payload)
    )
    cap = len(b"old") + len(payload) + final_sidecar_size
    assert _download._dir_size(out_dir) <= cap
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
    assert _download._dir_size(out_dir) <= cap


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_archive_shrink_credit_rolls_back_on_sidecar_commit_failure(
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
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest, "size": len(payload)}

    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }
    final_sidecar_size = _real_sidecar_size(
        tmp_path, cand, ["new.csv", "old.csv"]
    )
    original_sidecar = _write_slightly_larger_previous_sidecar(
        out_dir, final_sidecar_size, len(member_body)
    )
    cap = len(b"old") + len(member_body) + final_sidecar_size
    assert _download._dir_size(out_dir) <= cap
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)
    monkeypatch.setattr(_download, "download_file", archive_download)
    failures = _fail_final_sidecar_commit(monkeypatch)

    result = _download.download_candidate(cand, str(out_dir))

    assert len(failures) == 1
    assert result["downloaded"] == []
    assert old.read_bytes() == b"old"
    assert not (out_dir / "new.csv").exists()
    assert sidecar.read_bytes() == original_sidecar
    assert _download._dir_size(out_dir) <= cap
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
    cap = (
        4
        + 6
        + _real_sidecar_size(tmp_path, cand, ["exact.csv"])
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(cand, str(out_dir))

    assert result["downloaded"] == [str(out_dir / "exact.csv")]
    assert calls == [("https://x/exact", 6)]
    assert _download._dir_size(out_dir) == cap
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
    cap = (
        4
        + 6
        + _real_sidecar_size(tmp_path, cand, ["overflow.csv"])
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(
        cand,
        str(out_dir),
    )

    assert result["downloaded"] == []
    assert calls == [("https://x/overflow", 6)]
    assert _download._dir_size(out_dir) <= cap
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
    cap = (
        4
        + 6
        + _real_sidecar_size(tmp_path, cand, ["table.csv"])
    )
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
        assert _download._dir_size(out_dir) == cap
    else:
        assert (
            out_dir / _download.SOURCE_SIDECAR
        ).read_bytes() == original_sidecar


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    ("member_body", "accepted"),
    [(b"123456", True), (b"1234567", False)],
)
def test_candidate_archive_cap_includes_real_sidecar(
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
        Path(dest).write_bytes(payload)
        return {"ok": True, "path": dest, "size": len(payload)}

    monkeypatch.setattr(_download, "download_file", archive_download)
    cand = {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [],
        **_archive_fields(archive_kind),
    }
    cap = (
        4
        + 6
        + _real_sidecar_size(tmp_path, cand, ["table.csv"])
    )
    monkeypatch.setattr(_download, "_MAX_PAPER_BYTES", cap)

    result = _download.download_candidate(cand, str(out_dir))

    table = out_dir / "table.csv"
    assert result["downloaded"] == ([str(table)] if accepted else [])
    assert table.exists() is accepted
    assert _download._dir_size(out_dir) <= cap
    sidecar = out_dir / _download.SOURCE_SIDECAR
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[
        "managed_files"
    ] == (["table.csv"] if accepted else [])


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
        Path(dest).write_bytes(payload)
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
    )["managed_files"] == ["new.csv", "old.csv"]
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
