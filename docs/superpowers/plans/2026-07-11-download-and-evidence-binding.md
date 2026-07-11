# Download and Evidence Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make fetched files atomic and ownership-aware, align fetch formats
with the scanner, and distinguish omitted, matched, and explicitly unmatched
evidence references.

**Architecture:** Centralize supported extensions, stream every download and
archive member through same-directory temporary files, and persist a managed
file manifest in the provenance sidecar. The adjudicated renderer uses an
explicit evidence-binding state rather than treating every missing match as an
automatic fallback.

**Tech Stack:** Python 3.10+, urllib, tempfile, zipfile, tarfile, hashlib,
pathlib, pytest.

## Global Constraints

- Use only neutral statistical-signal and data-inconsistency language.
- Preserve both existing verdict JSON shapes.
- Only an omitted reference may select evidence automatically.
- An explicit unmatched reference renders no unrelated evidence.
- Failed downloads leave existing completed destinations unchanged.
- Only files named by the previous managed manifest may be removed.
- Archive naming and sidecar output must be deterministic.
- Every production change follows a verified red-green cycle.
- Do not modify `recheck/` or `batches/`.

---

### Task 1: Centralize Supported Input Extensions

**Files:**
- Modify: `src/paperconan/_input.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/fetch/_files.py`
- Modify: `src/paperconan/fetch/_sources.py`
- Modify: `src/paperconan/fetch/_cli.py`
- Modify: `tests/fetch/test_files.py`
- Modify: `tests/test_fetch_nature.py`

**Interfaces:**
- `SUPPORTED_INPUT_EXTS`
- `ext_of(name)`
- `is_supported_input(name)`
- `discover_supported_inputs(in_dir)`
- `TABULAR_EXTS` and `is_tabular` remain compatibility aliases.

- [ ] **Step 1: Write extension-parity tests**

Replace and extend `tests/fetch/test_files.py` with:

```python
from paperconan._input import SUPPORTED_INPUT_EXTS
from paperconan.fetch import _files


def test_fetch_extensions_match_scanner_extensions():
    assert SUPPORTED_INPUT_EXTS == (
        "xlsx", "xls", "xlsm", "xlsb",
        "csv", "tsv", "pdf", "docx",
    )
    assert _files.TABULAR_EXTS == set(SUPPORTED_INPUT_EXTS)


def test_supported_input_check_is_case_insensitive():
    for ext in SUPPORTED_INPUT_EXTS:
        assert _files.is_tabular(f"source.{ext}")
        assert _files.is_tabular(f"SOURCE.{ext.upper()}")
    assert not _files.is_tabular("notes.txt")
```

Update Nature/fetch tests so PDF and Word files are classified as downloadable
scanner inputs instead of being excluded from `tabular_files`.

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_files.py \
  tests/test_fetch_nature.py \
  tests/fetch/test_sources_europepmc.py -q
```

Expected: five supported scanner extensions are absent from fetch
classification.

- [ ] **Step 3: Implement the shared format contract**

Add to `_input.py`:

```python
from pathlib import Path

SUPPORTED_INPUT_EXTS = (
    "xlsx", "xls", "xlsm", "xlsb",
    "csv", "tsv", "pdf", "docx",
)


def ext_of(name):
    return Path(name or "").suffix.lstrip(".").lower()


def is_supported_input(name):
    return ext_of(name) in SUPPORTED_INPUT_EXTS


def discover_supported_inputs(in_dir):
    root = Path(in_dir)
    return sorted(
        str(path)
        for path in root.iterdir()
        if path.is_file() and is_supported_input(path.name)
    )
```

In `_files.py`:

```python
from paperconan._input import (
    SUPPORTED_INPUT_EXTS,
    ext_of,
    is_supported_input,
)

TABULAR_EXTS = set(SUPPORTED_INPUT_EXTS)
is_tabular = is_supported_input
```

Use `discover_supported_inputs` in `scan_dir`, and use
`is_supported_input` in source classifiers and archive extraction.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_files.py \
  tests/test_fetch_nature.py \
  tests/fetch/test_sources_europepmc.py \
  tests/test_xls_reading.py \
  tests/test_extract.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_input.py src/paperconan/_audit.py \
  src/paperconan/fetch/_files.py src/paperconan/fetch/_sources.py \
  src/paperconan/fetch/_cli.py tests/fetch/test_files.py \
  tests/test_fetch_nature.py
git commit -m "refactor: share supported input formats"
```

---

### Task 2: Make Direct Downloads Atomic

**Files:**
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `tests/fetch/test_download.py`
- Modify: `tests/test_fetch_download.py`

**Interfaces:**
- `_copy_limited(src, dest, max_bytes) -> int`
- `_atomic_stream_write(src, dest_path, max_bytes) -> int`
- `download_file` keeps its public return shape.

- [ ] **Step 1: Add interruption and preservation tests**

Append to `tests/fetch/test_download.py`:

```python
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
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_download.py \
  tests/test_fetch_download.py -q
```

Expected: the old destination is overwritten or truncated on failure.

- [ ] **Step 3: Implement bounded copying**

Add:

```python
def _copy_limited(src, dest, max_bytes):
    total = 0
    while True:
        chunk = src.read(65536)
        if not chunk:
            return total
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"file exceeds max_bytes ({max_bytes})")
        dest.write(chunk)
```

- [ ] **Step 4: Implement atomic stream writes**

Add:

```python
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
```

`download_file` performs content-type and content-length checks first, then
calls `_atomic_stream_write`. Convert `ValueError` into the existing size-limit
result. Retryable stream failures start a fresh temporary file; no attempt
opens `dest_path` directly.

- [ ] **Step 5: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_download.py \
  tests/test_fetch_download.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/fetch/_download.py \
  tests/fetch/test_download.py tests/test_fetch_download.py
git commit -m "fix: write downloads atomically"
```

---

### Task 3: Stream Archives and Preserve Colliding Members

**Files:**
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `tests/fetch/test_download.py`
- Modify: `tests/test_fetch_download.py`

**Interfaces:**
- `_archive_output_names(member_names) -> dict[str, str]`
- `_extract_tabular_zip(zip_path, out_dir,
  max_member_bytes=_DEFAULT_MAX) -> list[str]`
- `_extract_tabular_tar(tar_path, out_dir,
  max_member_bytes=_DEFAULT_MAX) -> list[str]`

- [ ] **Step 1: Add collision and path-based ZIP tests**

Append:

```python
def test_zip_duplicate_basenames_are_both_preserved(tmp_path):
    archive = tmp_path / "supp.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/table.csv", b"a\n1\n")
        zf.writestr("b/table.csv", b"a\n2\n")
    out = tmp_path / "out"
    out.mkdir()
    paths = _download._extract_tabular_zip(str(archive), str(out))
    names = sorted(path.name for path in map(Path, paths))
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(name.startswith("table--") for name in names)
    assert {Path(path).read_bytes() for path in paths} == {
        b"a\n1\n", b"a\n2\n"
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
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_download.py \
  tests/test_fetch_download.py -q
```

Expected: ZIP rejects a path input, colliding basenames overwrite, and five
extensions are omitted.

- [ ] **Step 3: Implement deterministic member names**

Add:

```python
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
```

Precompute names for every eligible archive member so all members in a
collision group receive path-derived names.

- [ ] **Step 4: Stream archive members**

Change ZIP to open `zip_path` directly:

```python
with zipfile.ZipFile(zip_path) as zf:
    names = _archive_output_names(
        info.filename for info in zf.infolist()
        if not info.is_dir() and is_supported_input(info.filename)
    )
```

For each accepted member, call `_atomic_stream_write` on `zf.open(info)`.
Apply the same naming map and `_atomic_stream_write` to TAR member streams.
Reject members whose declared size exceeds `max_member_bytes`. Do not call
`read()` without a bounded chunk size.

Update `_download_supplementary_archive` to pass the archive path directly
instead of reading it into memory.

- [ ] **Step 5: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_download.py \
  tests/test_fetch_download.py \
  tests/fetch/test_files.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/fetch/_download.py \
  tests/fetch/test_download.py tests/test_fetch_download.py
git commit -m "fix: stream archive inputs without collisions"
```

---

### Task 4: Track and Clean Only Managed Fetch Outputs

**Files:**
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `src/paperconan/_audit.py`
- Create: `tests/fetch/test_managed_output.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- `_read_source_sidecar(out_dir) -> dict`
- `_safe_managed_path(out_dir, relative) -> str | None`
- `_remove_managed_files(out_dir, managed_files)`
- `_managed_output_name(out_dir, base, source_name, reusable_names) -> str`
- `_write_source_sidecar(cand, out_dir, managed_files)`
- `_extract_tabular_zip(zip_path, out_dir,
  max_member_bytes=_DEFAULT_MAX, *, reusable_names=()) -> list[str]`
- `_extract_tabular_tar(tar_path, out_dir,
  max_member_bytes=_DEFAULT_MAX, *, reusable_names=()) -> list[str]`

- [ ] **Step 1: Write managed-output tests**

Create `tests/fetch/test_managed_output.py`:

```python
import json
from pathlib import Path
import zipfile

from paperconan.fetch import _download


def _candidate(name, url):
    return {
        "cand_id": "source:1",
        "source": "source",
        "tabular_files": [{
            "name": name,
            "download_url": url,
        }],
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
    _download._remove_managed_files(
        str(tmp_path),
        ["../outside.csv", "/tmp/absolute.csv"],
    )
    assert outside.read_text(encoding="utf-8") == "keep"


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
```

- [ ] **Step 2: Add provenance filtering**

Append to `tests/test_smoke.py`:

```python
def test_managed_files_do_not_enter_scan_paper_metadata(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "t.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (data / "paperconan_source.json").write_text(
        '{"doi":"10.x/example","managed_files":["t.csv"]}',
        encoding="utf-8",
    )
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["paper"] == {"doi": "10.x/example"}
```

- [ ] **Step 3: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_managed_output.py \
  tests/test_smoke.py -q
```

Expected: stale managed files remain and `managed_files` enters `scan.paper`.

- [ ] **Step 4: Implement safe manifest paths**

Add:

```python
def _safe_managed_path(out_dir, relative):
    if not isinstance(relative, str) or os.path.isabs(relative):
        return None
    root = os.path.realpath(out_dir)
    candidate = os.path.realpath(os.path.join(root, relative))
    if os.path.commonpath([root, candidate]) != root:
        return None
    return candidate


def _remove_managed_files(out_dir, managed_files):
    for relative in sorted(set(managed_files or [])):
        path = _safe_managed_path(out_dir, relative)
        if path is None:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
```

- [ ] **Step 5: Implement manifest read/write and collision-safe direct names**

Read the previous sidecar before downloads. A direct destination may reuse an
existing path only when that relative path was in the previous managed list.
Rename the direct-file loop variable to `file_ref`, add `import urllib.parse`,
and derive the source identity before resolving the destination:

```python
requested_name = str(file_ref.get("name") or "").strip()
source_url = str(file_ref.get("download_url") or "")
source_name = requested_name or source_url
base = (
    os.path.basename(requested_name)
    or os.path.basename(urllib.parse.urlsplit(source_url).path)
    or "download"
)
```

Implement `_managed_output_name` exactly as:

```python
def _managed_output_name(
    out_dir, base, source_name, reusable_names
):
    reusable = set(reusable_names or ())
    base = os.path.basename(base) or "download"

    def available(name):
        return (
            name in reusable
            or not os.path.lexists(os.path.join(out_dir, name))
        )

    if available(base):
        return base

    stem, suffix = os.path.splitext(base)
    digest = hashlib.sha256(
        source_name.encode("utf-8")
    ).hexdigest()
    for width in range(10, len(digest) + 1, 2):
        candidate = f"{stem}--{digest[:width]}{suffix.lower()}"
        if available(candidate):
            return candidate

    counter = 2
    while True:
        candidate = (
            f"{stem}--{digest}-{counter}{suffix.lower()}"
        )
        if available(candidate):
            return candidate
        counter += 1
```

Call it for every direct file before `download_file`. Use the same resolver for
archive members, with `source_name` set to the complete member path and `base`
set to `_archive_output_names`' selected basename. The keyword-only
`reusable_names=()` argument means only paths from the previous manifest may be
reused.

Write the sidecar through a same-directory temporary file and `os.replace`.
Store sorted relative paths under `managed_files`. After the new sidecar is
committed, remove `old_managed - new_managed`.

- [ ] **Step 6: Filter internal sidecar fields from scan provenance**

In `_load_provenance`:

```python
data = json.load(fh)
if not isinstance(data, dict):
    return None
return {
    key: value
    for key, value in data.items()
    if key != "managed_files"
}
```

- [ ] **Step 7: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_managed_output.py \
  tests/fetch/test_download.py \
  tests/test_smoke.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/paperconan/fetch/_download.py src/paperconan/_audit.py \
  tests/fetch/test_managed_output.py tests/test_smoke.py
git commit -m "feat: track managed fetch outputs"
```

---

### Task 5: Bind Verdict Evidence With Explicit Three-State Semantics

**Files:**
- Modify: `src/paperconan/_adjudicated_html.py`
- Modify: `tests/test_adjudicated_report.py`
- Modify: `tests/test_adjudicated_report_unified.py`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `docs/reports.md`

**Interfaces:**
- `_EvidenceBinding(state, ref, item)`
- `_bind_finding_ref(scan_findings, finding) -> _EvidenceBinding`
- States: `omitted`, `matched`, `unmatched`.

- [ ] **Step 1: Replace the legacy unmatched fallback test**

Change the existing unmatched-reference test to:

```python
def test_explicit_unmatched_reference_never_falls_back():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "report_md": "## t",
        "finding_refs": [{"sheet": "Nonexistent"}],
    }
    html = render_adjudicated_report(scan, verdict)
    assert html.count('class="finding-card"') == 0
    assert "Nonexistent" in html
    assert "constant_offset" not in html
    assert "within_col_value_duplication" not in html
```

- [ ] **Step 2: Add omitted, empty, and extra-reference tests**

Append:

```python
def test_omitted_reference_uses_labeled_automatic_selection():
    html = render_adjudicated_report(
        _scan_two_findings(),
        {"verdict": "KEEP", "report_md": "## t"},
    )
    assert "automatic evidence selection" in html.lower()
    assert "constant_offset" in html


def test_explicit_empty_selector_is_unmatched():
    verdict = {
        "verdict": "KEEP",
        "findings": [{
            "title": "x",
            "finding_ref": {},
            "report_md": "x",
        }],
    }
    html = render_adjudicated_report(_scan_two_findings(), verdict)
    assert "unmatched" in html.lower()
    assert html.count('class="finding-card"') == 0


def test_unmatched_extra_reference_is_visible_without_fallback():
    verdict = {
        "verdict": "KEEP",
        "report_md": "## t",
        "finding_refs": [
            {"sheet": "Alpha", "kind": "constant_offset"},
            {"sheet": "Missing", "kind": "constant_ratio"},
        ],
    }
    html = render_adjudicated_report(_scan_two_findings(), verdict)
    assert html.count('class="finding-card"') == 1
    assert "Missing" in html
    assert "constant_ratio" in html
```

- [ ] **Step 3: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py -q
```

Expected: explicit unmatched references still display the strongest unrelated
scan finding, and automatic selection is unlabeled.

- [ ] **Step 4: Implement evidence binding**

Add:

```python
from typing import Literal, NamedTuple


class _EvidenceBinding(NamedTuple):
    state: Literal["omitted", "matched", "unmatched"]
    ref: dict[str, Any] | None
    item: dict[str, Any] | None


def _bind_finding_ref(scan_findings, finding):
    if (
        "finding_ref" not in finding
        or finding.get("finding_ref") is None
    ):
        item = scan_findings[0] if scan_findings else None
        return _EvidenceBinding("omitted", None, item)
    ref = finding.get("finding_ref")
    if not isinstance(ref, dict):
        return _EvidenceBinding("unmatched", {"value": ref}, None)
    item = _match_finding(scan_findings, ref)
    if item is None:
        return _EvidenceBinding("unmatched", ref, None)
    return _EvidenceBinding("matched", ref, item)
```

- [ ] **Step 5: Render each state without ambiguity**

In `_render_finding_block`:

```python
binding = _bind_finding_ref(scan_findings, finding)
if binding.state == "matched":
    evidence = _render_key_finding(binding.item, idx)
elif binding.state == "omitted" and binding.item is not None:
    evidence = (
        '<p class="scope-note">Automatic evidence selection: '
        'the strongest visible statistical signal is shown because no '
        'finding_ref was supplied.</p>'
        + _render_key_finding(binding.item, idx)
    )
elif binding.state == "omitted":
    evidence = '<p class="no-evidence">No scan evidence is available.</p>'
else:
    selector = json.dumps(
        binding.ref, ensure_ascii=False, sort_keys=True
    )
    evidence = (
        '<p class="no-evidence">Explicit finding_ref unmatched: '
        f'<code>{_esc(selector)}</code></p>'
    )
```

Bind every `extra_ref` independently; append either its matching evidence or an
explicit unmatched-selector note. `_render_findings_index` uses the same
binding helper.

In `_normalize_verdict`, preserve missing/`None` as omitted, preserve an
explicit dictionary as explicit, and keep every additional legacy selector in
`extra_refs`.

- [ ] **Step 6: Document legacy and primary behavior**

Update report/schema docs with:

- omitted `finding_ref`: automatic evidence may be shown and is labeled;
- matched explicit selector: only matched evidence is shown;
- unmatched explicit selector: no evidence table is substituted;
- both legacy `finding_refs` and primary `findings[].finding_ref` use the same
  binding rules.

- [ ] **Step 7: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py \
  tests/test_smoke.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/paperconan/_adjudicated_html.py \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py \
  skills/paperconan/references/output-schema.md docs/reports.md
git commit -m "fix: bind verdict evidence explicitly"
```

---

### Task 6: Download and Evidence Regression Gate

**Files:**
- Modify only to address regressions in this component.

- [ ] **Step 1: Run fetch tests**

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_files.py \
  tests/fetch/test_download.py \
  tests/fetch/test_managed_output.py \
  tests/fetch/test_sources_dryad.py \
  tests/fetch/test_sources_europepmc.py \
  tests/fetch/test_sources_figshare.py \
  tests/fetch/test_sources_zenodo.py \
  tests/test_fetch_download.py \
  tests/test_fetch_nature.py \
  tests/test_fetch_pmc_oa.py -q
```

Expected: all pass.

- [ ] **Step 2: Run report binding tests**

```bash
.venv/bin/python -m pytest \
  tests/test_adjudicated_report.py \
  tests/test_adjudicated_report_unified.py \
  tests/test_smoke.py -q
```

Expected: all pass.

- [ ] **Step 3: Run the complete suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with only the intentional live-network skip.

- [ ] **Step 4: Verify direct failure behavior**

Run the exact regression nodes for interrupted writes, unmanaged archive
targets, and explicit unmatched evidence:

```bash
.venv/bin/python -m pytest \
  tests/fetch/test_download.py::test_stream_failure_preserves_existing_destination \
  tests/fetch/test_managed_output.py::test_unmanaged_archive_target_is_preserved \
  tests/test_adjudicated_report.py::test_explicit_unmatched_reference_never_falls_back -q
```

Expected: all pass.
