# paperconan data-fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a paper DOI or title, find and download its tabular source data from Zenodo/Figshare/Dryad, ready for `paperconan <dir>` analysis.

**Architecture:** A new stdlib-only package `src/paperconan/fetch/` with per-repository adapters (search → normalized Candidate dicts with tabular file refs), a paper-query resolver computing match signals, a defensive downloader, and a `paperconan fetch` CLI subcommand. The tool reports candidates + match signals; the agent/user decides which dataset belongs to the paper.

**Tech Stack:** Python ≥3.10, stdlib `urllib`/`json`/`csv` only (no new runtime dependency), pytest with monkeypatched HTTP for offline-deterministic tests.

**Spec:** `docs/superpowers/specs/2026-05-28-paperconan-data-fetch-design.md`

---

## Shared data contracts (used across all tasks — keep keys consistent)

**FileRef** (one downloadable file):
```python
{"name": str, "ext": str, "size": int | None, "download_url": str}
```

**Candidate** (one dataset from a repository):
```python
{
  "cand_id": str,        # "<source>:<id>", stable handle for --download
  "source": str,         # "zenodo" | "figshare" | "dryad"
  "id": str,
  "doi": str | None,
  "title": str,
  "authors": list[str],
  "published": str | None,
  "tabular_files": list[FileRef],   # only .xlsx/.csv/.tsv
  "all_files": list[FileRef],       # every file in the dataset (for --all)
  "all_files_count": int,
  "related_dois": list[str],        # publication DOIs the dataset links to
  "match_signals": dict | None,     # filled by search_all when paper info available
}
```

**match_signals**:
```python
{"doi_in_related": bool, "title_overlap": float | None, "author_overlap": float | None}
```

## File structure

- Create `src/paperconan/fetch/__init__.py` — public API: `search_all`, `download_candidate`.
- Create `src/paperconan/fetch/_http.py` — `get_json`, `post_json` (urllib).
- Create `src/paperconan/fetch/_files.py` — `ext_of`, `is_tabular`, `make_fileref`, `TABULAR_EXTS`.
- Create `src/paperconan/fetch/_sources.py` — `search_zenodo`, `search_figshare`, `search_dryad`.
- Create `src/paperconan/fetch/_resolve.py` — `normalize_query`, `enrich_via_crossref`, `match_signals`.
- Create `src/paperconan/fetch/_download.py` — `download_file`, `download_candidate`.
- Create `src/paperconan/fetch/_cli.py` — `fetch_main(argv)`.
- Modify `src/paperconan/_audit.py` — `main()` dispatches the `fetch` subcommand.
- Modify `src/paperconan/__init__.py` — bump `__version__` to `0.4.0`.
- Modify `pyproject.toml` — version `0.4.0`.
- Create `tests/fetch/__init__.py`, `tests/fetch/conftest.py` (fixture loader + stub HTTP), `tests/fetch/fixtures/*.json`, and one test module per source/unit.
- Modify `skills/paperconan/SKILL.md` + `references/` — fetch-then-audit workflow + honesty rules.
- Modify `README.md` — document `paperconan fetch`.

---

### Task 1: File classification helpers (`_files.py`)

**Files:**
- Create: `src/paperconan/fetch/__init__.py` (empty for now)
- Create: `src/paperconan/fetch/_files.py`
- Test: `tests/fetch/test_files.py`

> Note: do **not** add `tests/fetch/__init__.py`. Keeping `tests/fetch` a non-package (like the existing `tests/`) lets pytest auto-load `conftest.py` and keeps test discovery in prepend mode working. Test module basenames are unique across the suite.

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_files.py
from paperconan.fetch import _files


def test_ext_of_lowercases_and_strips_dot():
    assert _files.ext_of("Data Sheet 1.XLSX") == "xlsx"
    assert _files.ext_of("table.csv") == "csv"
    assert _files.ext_of("readme") == ""


def test_is_tabular():
    assert _files.is_tabular("a.xlsx")
    assert _files.is_tabular("b.CSV")
    assert _files.is_tabular("c.tsv")
    assert not _files.is_tabular("d.zip")
    assert not _files.is_tabular("e.inp")


def test_make_fileref():
    ref = _files.make_fileref("t.csv", 1234, "https://x/t.csv")
    assert ref == {"name": "t.csv", "ext": "csv", "size": 1234, "download_url": "https://x/t.csv"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_files.py -v`
Expected: FAIL (`ModuleNotFoundError: paperconan.fetch._files`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/__init__.py
```
(leave empty in this task)

```python
# src/paperconan/fetch/_files.py
"""Pure helpers for classifying downloadable files by extension."""
from __future__ import annotations
import os

TABULAR_EXTS = {"xlsx", "csv", "tsv"}


def ext_of(name: str) -> str:
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def is_tabular(name: str) -> bool:
    return ext_of(name) in TABULAR_EXTS


def make_fileref(name: str, size, download_url: str) -> dict:
    return {"name": name, "ext": ext_of(name),
            "size": int(size) if isinstance(size, (int, float)) else None,
            "download_url": download_url}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_files.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/__init__.py src/paperconan/fetch/_files.py tests/fetch/test_files.py
git commit -m "feat(fetch): file-extension classification helpers"
```

---

### Task 2: HTTP helpers (`_http.py`)

**Files:**
- Create: `src/paperconan/fetch/_http.py`
- Test: `tests/fetch/test_http.py`

`get_json`/`post_json` wrap urllib. Pure-unit testing of real HTTP is out of scope (covered by a network-marked smoke test); here we test the URL/param assembly via a monkeypatched `urlopen`.

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_http.py
import io
import json
import pytest
from paperconan.fetch import _http


class _StubResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def test_get_json_builds_query_and_parses(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _StubResp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.get_json("https://api.example.org/x", params={"q": "a b", "size": 3})
    assert out == {"ok": True}
    assert seen["url"].startswith("https://api.example.org/x?")
    assert "q=a+b" in seen["url"] and "size=3" in seen["url"]
    assert seen["headers"].get("accept") == "application/json"


def test_post_json_sends_body(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["data"] = req.data
        seen["method"] = req.get_method()
        return _StubResp(json.dumps([{"id": 1}]).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.post_json("https://api.example.org/search", {"search_for": "x"})
    assert out == [{"id": 1}]
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"search_for": "x"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_http.py -v`
Expected: FAIL (`ModuleNotFoundError: paperconan.fetch._http`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/_http.py
"""Thin stdlib HTTP helpers returning parsed JSON. No third-party deps."""
from __future__ import annotations
import json
import urllib.parse
import urllib.request

_UA = "paperconan-fetch/0.4 (+https://github.com/zixixr/paperconan)"


def get_json(url, params=None, headers=None, timeout=15):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"Accept": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def post_json(url, payload, headers=None, timeout=15):
    body = json.dumps(payload).encode("utf-8")
    h = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_http.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_http.py tests/fetch/test_http.py
git commit -m "feat(fetch): stdlib JSON HTTP helpers"
```

---

### Task 3: Test fixtures + stub-HTTP conftest

**Files:**
- Create: `tests/fetch/conftest.py`
- Create: `tests/fetch/fixtures/zenodo_search.json`
- Create: `tests/fetch/fixtures/figshare_search.json`
- Create: `tests/fetch/fixtures/figshare_article.json`
- Create: `tests/fetch/fixtures/dryad_dataset.json`
- Create: `tests/fetch/fixtures/dryad_versions.json`
- Create: `tests/fetch/fixtures/dryad_files.json`

These fixtures mirror the real API shapes verified on 2026-05-28. `stub_http` lets a test map URL substrings to fixture objects.

- [ ] **Step 1: Create the fixture files**

```json
// tests/fetch/fixtures/zenodo_search.json
{"hits": {"total": 1, "hits": [{
  "id": 10277693, "doi": "10.5281/zenodo.10277693",
  "metadata": {
    "title": "Platelets retrospective biomarker dataset",
    "creators": [{"name": "Doe, Jane"}, {"name": "Roe, Richard"}],
    "publication_date": "2023-12-07",
    "related_identifiers": [
      {"identifier": "10.15761/JTS.1000455", "relation": "isPartOf", "scheme": "doi"}
    ]
  },
  "files": [
    {"key": "BASE_INFO.xlsx", "size": 191562, "links": {"self": "https://zenodo.org/api/records/10277693/files/BASE_INFO.xlsx/content"}},
    {"key": "notes.pdf", "size": 5000, "links": {"self": "https://zenodo.org/api/records/10277693/files/notes.pdf/content"}}
  ]
}]}}
```

```json
// tests/fetch/fixtures/figshare_search.json
[{"id": 32340066, "doi": "10.3389/fphar.2026.1817103.s002", "title": "Data Sheet 1 Thrombocytopenia"}]
```

```json
// tests/fetch/fixtures/figshare_article.json
{"id": 32340066, "doi": "10.3389/fphar.2026.1817103.s002",
 "title": "Data Sheet 1 Thrombocytopenia",
 "authors": [{"full_name": "Alice Smith"}],
 "published_date": "2026-01-15T00:00:00Z",
 "files": [
   {"name": "Data Sheet 1.xlsx", "size": 23273, "download_url": "https://ndownloader.figshare.com/files/64751361"},
   {"name": "cover.png", "size": 1000, "download_url": "https://ndownloader.figshare.com/files/64751362"}
 ]}
```

```json
// tests/fetch/fixtures/dryad_dataset.json
{"title": "Sabertooth predatory behavior data",
 "identifier": "doi:10.5061/dryad.7rh4625",
 "authors": [{"firstName": "Sam", "lastName": "Jones"}],
 "publicationDate": "2018-05-01",
 "relatedWorks": [{"identifier": "10.1098/rspb.2018.0123", "relationship": "article"}],
 "_links": {"stash:version": {"href": "/api/v2/versions/124910"}}}
```

```json
// tests/fetch/fixtures/dryad_versions.json
{"_embedded": {"stash:versions": [
  {"versionNumber": 1, "_links": {"stash:files": {"href": "/api/v2/versions/124910/files"}}}
]}}
```

```json
// tests/fetch/fixtures/dryad_files.json
{"_embedded": {"stash:files": [
  {"path": "measurements.csv", "mimeType": "text/csv", "size": 8000, "_links": {"stash:download": {"href": "/api/v2/files/9/download"}}},
  {"path": "model.inp", "mimeType": "application/octet-stream", "size": 4000, "_links": {"stash:download": {"href": "/api/v2/files/1/download"}}}
]}}
```

- [ ] **Step 2: Create conftest with fixture loader + stub HTTP**

```python
# tests/fetch/conftest.py
import json
import os
import pytest

_FX = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixture():
    return load_fixture


def make_stub_get_json(routes):
    """routes: list of (url_substring, fixture_object). First match wins."""
    def _stub(url, params=None, headers=None, timeout=15):
        for sub, obj in routes:
            if sub in url:
                return obj
        raise AssertionError(f"no stub route for GET {url}")
    return _stub


def make_stub_post_json(routes):
    def _stub(url, payload, headers=None, timeout=15):
        for sub, obj in routes:
            if sub in url:
                return obj
        raise AssertionError(f"no stub route for POST {url}")
    return _stub


@pytest.fixture
def stub_http():
    return {"get": make_stub_get_json, "post": make_stub_post_json}
```

- [ ] **Step 3: Verify fixtures load**

Run: `python -m pytest tests/fetch/ -q`
Expected: PASS (existing tests still pass; no new test yet — this confirms conftest imports cleanly)

- [ ] **Step 4: Commit**

```bash
git add tests/fetch/conftest.py tests/fetch/fixtures/
git commit -m "test(fetch): real-shape API fixtures and stub-HTTP harness"
```

---

### Task 4: Zenodo adapter (`_sources.search_zenodo`)

**Files:**
- Create: `src/paperconan/fetch/_sources.py`
- Test: `tests/fetch/test_sources_zenodo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_sources_zenodo.py
from paperconan.fetch import _sources, _http


def test_search_zenodo_normalizes_candidate(monkeypatch, fixture, stub_http):
    routes = [("zenodo.org/api/records", fixture("zenodo_search.json"))]
    monkeypatch.setattr(_http, "get_json", stub_http["get"](routes))

    cands = _sources.search_zenodo("10.15761/JTS.1000455", size=5)
    assert len(cands) == 1
    c = cands[0]
    assert c["source"] == "zenodo"
    assert c["cand_id"] == "zenodo:10277693"
    assert c["doi"] == "10.5281/zenodo.10277693"
    assert c["authors"] == ["Doe, Jane", "Roe, Richard"]
    assert c["published"] == "2023-12-07"
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["BASE_INFO.xlsx"]
    assert c["tabular_files"][0]["download_url"].endswith("/content")
    assert "10.15761/JTS.1000455" in c["related_dois"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_sources_zenodo.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'search_zenodo'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/_sources.py
"""Per-repository search adapters. Each returns normalized Candidate dicts
(see the plan's shared contracts). Network calls go through _http so tests can
monkeypatch them."""
from __future__ import annotations

from . import _http
from ._files import TABULAR_EXTS, make_fileref


def _candidate(source, cid, doi, title, authors, published, all_files, related):
    tabular = [f for f in all_files if f["ext"] in TABULAR_EXTS]
    return {"cand_id": f"{source}:{cid}", "source": source, "id": str(cid),
            "doi": doi, "title": title or "", "authors": authors or [],
            "published": published, "tabular_files": tabular,
            "all_files": all_files, "all_files_count": len(all_files),
            "related_dois": related or [], "match_signals": None}


def search_zenodo(query, size=5):
    data = _http.get_json("https://zenodo.org/api/records",
                          params={"q": query, "size": size})
    out = []
    for h in data.get("hits", {}).get("hits", []):
        md = h.get("metadata", {})
        all_files = [make_fileref(f.get("key"), f.get("size"),
                                  f.get("links", {}).get("self"))
                     for f in h.get("files", [])]
        related = [r.get("identifier") for r in md.get("related_identifiers", [])
                   if r.get("identifier")]
        out.append(_candidate(
            "zenodo", h.get("id"), h.get("doi"), md.get("title"),
            [c.get("name") for c in md.get("creators", []) if c.get("name")],
            md.get("publication_date"), all_files, related))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_sources_zenodo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_sources.py tests/fetch/test_sources_zenodo.py
git commit -m "feat(fetch): Zenodo search adapter"
```

---

### Task 5: Figshare adapter (`_sources.search_figshare`)

**Files:**
- Modify: `src/paperconan/fetch/_sources.py`
- Test: `tests/fetch/test_sources_figshare.py`

Figshare search returns articles without files; fetch each article for its files (cap to `size`).

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_sources_figshare.py
from paperconan.fetch import _sources, _http


def test_search_figshare_fetches_article_files(monkeypatch, fixture, stub_http):
    monkeypatch.setattr(_http, "post_json",
                        stub_http["post"]([("api.figshare.com/v2/articles/search",
                                            fixture("figshare_search.json"))]))
    monkeypatch.setattr(_http, "get_json",
                        stub_http["get"]([("api.figshare.com/v2/articles/32340066",
                                           fixture("figshare_article.json"))]))

    cands = _sources.search_figshare("thrombocytopenia", size=5)
    assert len(cands) == 1
    c = cands[0]
    assert c["cand_id"] == "figshare:32340066"
    assert c["authors"] == ["Alice Smith"]
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["Data Sheet 1.xlsx"]
    assert c["tabular_files"][0]["download_url"].startswith("https://ndownloader.figshare.com/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_sources_figshare.py -v`
Expected: FAIL (`AttributeError: ... 'search_figshare'`)

- [ ] **Step 3: Write minimal implementation (append to `_sources.py`)**

```python
def search_figshare(query, size=5):
    arts = _http.post_json("https://api.figshare.com/v2/articles/search",
                           {"search_for": query, "page_size": size})
    out = []
    for a in arts[:size]:
        aid = a.get("id")
        if aid is None:
            continue
        full = _http.get_json(f"https://api.figshare.com/v2/articles/{aid}")
        all_files = [make_fileref(f.get("name"), f.get("size"), f.get("download_url"))
                     for f in full.get("files", [])]
        authors = [au.get("full_name") for au in full.get("authors", []) if au.get("full_name")]
        out.append(_candidate(
            "figshare", aid, full.get("doi") or None, full.get("title"),
            authors, full.get("published_date"), all_files, []))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_sources_figshare.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_sources.py tests/fetch/test_sources_figshare.py
git commit -m "feat(fetch): Figshare search adapter"
```

---

### Task 6: Dryad adapter (`_sources.search_dryad`)

**Files:**
- Modify: `src/paperconan/fetch/_sources.py`
- Test: `tests/fetch/test_sources_dryad.py`

Dryad chains dataset → version → files. Search uses `/api/v2/search?q=`; for the plan we
test the per-dataset normalization via `_dryad_candidate(doi)` which the search maps over.

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_sources_dryad.py
from paperconan.fetch import _sources, _http


def test_dryad_candidate_follows_version_chain(monkeypatch, fixture, stub_http):
    routes = [
        ("/api/v2/datasets/doi%3A10.5061%2Fdryad.7rh4625", fixture("dryad_dataset.json")),
        ("/api/v2/versions/124910/files", fixture("dryad_files.json")),
    ]
    monkeypatch.setattr(_http, "get_json", stub_http["get"](routes))

    c = _sources._dryad_candidate("doi:10.5061/dryad.7rh4625")
    assert c["cand_id"] == "dryad:10.5061/dryad.7rh4625"
    assert c["authors"] == ["Sam Jones"]
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["measurements.csv"]
    assert c["tabular_files"][0]["download_url"] == "https://datadryad.org/api/v2/files/9/download"
    assert "10.1098/rspb.2018.0123" in c["related_dois"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_sources_dryad.py -v`
Expected: FAIL (`AttributeError: ... '_dryad_candidate'`)

- [ ] **Step 3: Write minimal implementation (append to `_sources.py`)**

```python
import urllib.parse as _urlparse

_DRYAD = "https://datadryad.org"


def _dryad_candidate(doi):
    enc = _urlparse.quote(doi, safe="")
    ds = _http.get_json(f"{_DRYAD}/api/v2/datasets/{enc}")
    vhref = ds.get("_links", {}).get("stash:version", {}).get("href")
    all_files = []
    if vhref:
        files = _http.get_json(f"{_DRYAD}{vhref}/files")
        for f in files.get("_embedded", {}).get("stash:files", []):
            dl = f.get("_links", {}).get("stash:download", {}).get("href")
            all_files.append(make_fileref(f.get("path"), f.get("size"),
                                          f"{_DRYAD}{dl}" if dl else None))
    authors = [f"{a.get('firstName','')} {a.get('lastName','')}".strip()
               for a in ds.get("authors", [])]
    related = [w.get("identifier") for w in ds.get("relatedWorks", []) if w.get("identifier")]
    bare = doi[4:] if doi.startswith("doi:") else doi
    return _candidate("dryad", bare, bare, ds.get("title"), authors,
                      ds.get("publicationDate"), all_files, related)


def search_dryad(query, size=5):
    data = _http.get_json(f"{_DRYAD}/api/v2/search", params={"q": query, "per_page": size})
    out = []
    for ds in data.get("_embedded", {}).get("stash:datasets", [])[:size]:
        ident = ds.get("identifier")
        if not ident:
            continue
        try:
            out.append(_dryad_candidate(ident))
        except Exception:
            continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_sources_dryad.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_sources.py tests/fetch/test_sources_dryad.py
git commit -m "feat(fetch): Dryad search adapter with version-chain file resolution"
```

---

### Task 7: Query resolver + match signals (`_resolve.py`)

**Files:**
- Create: `src/paperconan/fetch/_resolve.py`
- Test: `tests/fetch/test_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_resolve.py
from paperconan.fetch import _resolve


def test_normalize_query_detects_doi():
    q = _resolve.normalize_query("10.1371/journal.pone.0173664")
    assert q["is_doi"] is True
    assert q["doi"] == "10.1371/journal.pone.0173664"


def test_normalize_query_treats_text_as_title():
    q = _resolve.normalize_query("Array programming with NumPy")
    assert q["is_doi"] is False
    assert q["title"] == "Array programming with NumPy"


def test_match_signals_doi_in_related():
    cand = {"related_dois": ["10.15761/JTS.1000455"], "title": "Platelets data",
            "authors": ["Doe, Jane"]}
    paper = {"doi": "10.15761/JTS.1000455", "title": None, "authors": []}
    sig = _resolve.match_signals(cand, paper)
    assert sig["doi_in_related"] is True
    assert sig["title_overlap"] is None


def test_match_signals_title_overlap():
    cand = {"related_dois": [], "title": "Platelets retrospective biomarker dataset",
            "authors": ["Doe, Jane"]}
    paper = {"doi": "x", "title": "Platelets biomarker study", "authors": ["Jane Doe"]}
    sig = _resolve.match_signals(cand, paper)
    assert sig["doi_in_related"] is False
    assert sig["title_overlap"] > 0.3
    assert sig["author_overlap"] > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_resolve.py -v`
Expected: FAIL (`ModuleNotFoundError: paperconan.fetch._resolve`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/_resolve.py
"""Resolve a paper DOI/title into a search query and score candidate matches."""
from __future__ import annotations
import re

from . import _http

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_query(text):
    s = (text or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    if _DOI_RE.match(s):
        return {"raw": text, "is_doi": True, "doi": s, "title": None}
    return {"raw": text, "is_doi": False, "doi": None, "title": s}


def enrich_via_crossref(doi):
    """Best-effort title/authors/year for a paper DOI. Returns None on any failure."""
    try:
        m = _http.get_json(f"https://api.crossref.org/works/{doi}").get("message", {})
    except Exception:
        return None
    title = (m.get("title") or [None])[0]
    authors = [f"{a.get('given','')} {a.get('family','')}".strip()
               for a in m.get("author", [])]
    year = None
    dp = m.get("issued", {}).get("date-parts", [[None]])
    if dp and dp[0]:
        year = str(dp[0][0])
    return {"doi": doi, "title": title, "authors": authors, "year": year}


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_signals(cand, paper):
    related = set(cand.get("related_dois") or [])
    doi_in_related = bool(paper.get("doi") and paper["doi"] in related)
    title_overlap = None
    if paper.get("title"):
        title_overlap = round(_jaccard(_tokens(paper["title"]), _tokens(cand.get("title"))), 3)
    author_overlap = None
    if paper.get("authors"):
        pa = _tokens(" ".join(paper["authors"]))
        ca = _tokens(" ".join(cand.get("authors") or []))
        author_overlap = round(_jaccard(pa, ca), 3)
    return {"doi_in_related": doi_in_related,
            "title_overlap": title_overlap, "author_overlap": author_overlap}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_resolve.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_resolve.py tests/fetch/test_resolve.py
git commit -m "feat(fetch): query normalization, crossref enrichment, match signals"
```

---

### Task 8: Defensive downloader (`_download.py`)

**Files:**
- Create: `src/paperconan/fetch/_download.py`
- Test: `tests/fetch/test_download.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_download.py
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


def test_download_candidate_tabular_only(monkeypatch, tmp_path):
    saved = []
    def stub_dl(url, dest, **kw):
        open(dest, "wb").write(b"x"); saved.append(dest); return {"ok": True, "path": dest}
    monkeypatch.setattr(_download, "download_file", stub_dl)
    cand = {"cand_id": "zenodo:1", "tabular_files": [
        {"name": "a.csv", "ext": "csv", "size": 5, "download_url": "https://x/a.csv"}]}
    summary = _download.download_candidate(cand, str(tmp_path))
    assert len(summary["downloaded"]) == 1
    assert summary["downloaded"][0].endswith("a.csv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_download.py -v`
Expected: FAIL (`ModuleNotFoundError: paperconan.fetch._download`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/_download.py
"""Defensive file download: redirects (urllib default), timeout, size cap,
content-type sniffing so an HTML error page is never saved as data."""
from __future__ import annotations
import os
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_download.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/_download.py tests/fetch/test_download.py
git commit -m "feat(fetch): defensive downloader with content-type and size guards"
```

---

### Task 9: Aggregation API (`search_all` in `__init__.py`)

**Files:**
- Modify: `src/paperconan/fetch/__init__.py`
- Test: `tests/fetch/test_search_all.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_search_all.py
from paperconan import fetch


def test_search_all_merges_ranks_and_signals(monkeypatch):
    z = [{"cand_id": "zenodo:1", "source": "zenodo", "id": "1", "doi": "10.x/z",
          "title": "Platelets biomarker dataset", "authors": ["Doe, Jane"],
          "published": "2023", "tabular_files": [{"name": "a.xlsx", "ext": "xlsx",
          "size": 1, "download_url": "u"}], "all_files_count": 1,
          "related_dois": ["10.15761/JTS.1000455"], "match_signals": None}]
    fg = [{"cand_id": "figshare:2", "source": "figshare", "id": "2", "doi": None,
           "title": "Unrelated thing", "authors": [], "published": None,
           "tabular_files": [], "all_files_count": 3, "related_dois": [],
           "match_signals": None}]
    monkeypatch.setattr(fetch._sources, "search_zenodo", lambda q, size=5: z)
    monkeypatch.setattr(fetch._sources, "search_figshare", lambda q, size=5: fg)
    monkeypatch.setattr(fetch._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", lambda doi: None)

    cands = fetch.search_all("10.15761/JTS.1000455", per_source=5)
    # zenodo candidate (doi_in_related + has tabular) ranks above the unrelated figshare one
    assert cands[0]["cand_id"] == "zenodo:1"
    assert cands[0]["match_signals"]["doi_in_related"] is True
    assert all("match_signals" in c and c["match_signals"] is not None for c in cands)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_search_all.py -v`
Expected: FAIL (`AttributeError: module 'paperconan.fetch' has no attribute 'search_all'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/paperconan/fetch/__init__.py
"""paperconan data-fetch: locate and download a paper's tabular source data
from open repositories (Zenodo / Figshare / Dryad)."""
from __future__ import annotations

from . import _sources, _resolve
from ._download import download_candidate  # noqa: F401


def _rank(cand):
    sig = cand.get("match_signals") or {}
    score = 0.0
    if sig.get("doi_in_related"):
        score += 100
    score += (sig.get("title_overlap") or 0) * 10
    score += (sig.get("author_overlap") or 0) * 5
    if cand.get("tabular_files"):
        score += 2
    return score


def search_all(query, per_source=5):
    q = _resolve.normalize_query(query)
    paper = {"doi": q["doi"], "title": q["title"], "authors": []}
    if q["is_doi"]:
        enriched = _resolve.enrich_via_crossref(q["doi"])
        if enriched:
            paper["title"] = paper["title"] or enriched.get("title")
            paper["authors"] = enriched.get("authors") or []
    search_term = q["doi"] or q["title"] or query

    cands = []
    for fn in (_sources.search_zenodo, _sources.search_figshare, _sources.search_dryad):
        try:
            cands.extend(fn(search_term, size=per_source))
        except Exception:
            continue
    for c in cands:
        c["match_signals"] = _resolve.match_signals(c, paper)
    cands.sort(key=_rank, reverse=True)
    return cands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/fetch/test_search_all.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/fetch/__init__.py tests/fetch/test_search_all.py
git commit -m "feat(fetch): search_all aggregation, match scoring, ranking"
```

---

### Task 10: CLI subcommand (`_cli.py` + `main()` dispatch)

**Files:**
- Create: `src/paperconan/fetch/_cli.py`
- Modify: `src/paperconan/_audit.py` (the `main()` function)
- Test: `tests/fetch/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fetch/test_cli.py
import json
from paperconan.fetch import _cli


def test_fetch_list_prints_candidates_json(monkeypatch, capsys):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z",
              "title": "T", "tabular_files": [{"name": "a.xlsx"}],
              "all_files_count": 1, "match_signals": {"doi_in_related": True,
              "title_overlap": None, "author_overlap": None}}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    rc = _cli.fetch_main(["10.15761/JTS.1000455", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["cand_id"] == "zenodo:1"


def test_fetch_download_selected_candidate(monkeypatch, tmp_path):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z", "title": "T",
              "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 3,
              "download_url": "u"}], "all_files_count": 1, "match_signals": None}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    captured = {}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda c, out_dir, **kw: captured.update(cid=c["cand_id"], out=out_dir)
                        or {"downloaded": [out_dir + "/a.csv"], "skipped": []})
    rc = _cli.fetch_main(["10.x/paper", "--download", "zenodo:1", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["cid"] == "zenodo:1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/fetch/test_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: paperconan.fetch._cli`)

- [ ] **Step 3: Write `_cli.py`**

```python
# src/paperconan/fetch/_cli.py
"""`paperconan fetch` subcommand: search repositories for a paper's data and
optionally download a chosen candidate's tabular files."""
from __future__ import annotations
import argparse
import json
import sys

from . import search_all
from ._download import download_candidate


def _print_table(cands):
    if not cands:
        print("no candidate datasets found in Zenodo / Figshare / Dryad.")
        print("the data may be in journal supplementary (paywalled) or not deposited.")
        return
    for c in cands:
        sig = c.get("match_signals") or {}
        flags = []
        if sig.get("doi_in_related"):
            flags.append("DOI-match")
        if sig.get("title_overlap"):
            flags.append(f"title~{sig['title_overlap']}")
        ntab = len(c.get("tabular_files", []))
        print(f"[{c['cand_id']}] {c['source']:8} tabular={ntab}/{c.get('all_files_count','?')} "
              f"{' '.join(flags):20} {c.get('title','')[:60]}")
        if ntab == 0:
            print("    (no .xlsx/.csv/.tsv files in this dataset)")


def fetch_main(argv):
    ap = argparse.ArgumentParser(prog="paperconan fetch",
                                 description="Find/download a paper's tabular source data")
    ap.add_argument("query", help="paper DOI or title")
    ap.add_argument("--json", action="store_true", help="print candidates as JSON")
    ap.add_argument("--download", metavar="CAND_ID", help="download this candidate's files")
    ap.add_argument("--auto", action="store_true", help="download the top-ranked candidate")
    ap.add_argument("--out", default=None, help="output dir for downloads")
    ap.add_argument("--all", action="store_true", help="download non-tabular files too")
    ap.add_argument("--per-source", type=int, default=5)
    args = ap.parse_args(argv)

    cands = search_all(args.query, per_source=args.per_source)

    target = None
    if args.download:
        target = next((c for c in cands if c["cand_id"] == args.download), None)
        if target is None:
            print(f"candidate {args.download!r} not in results", file=sys.stderr)
            return 2
    elif args.auto and cands:
        target = cands[0]

    if target is None:
        if args.json:
            print(json.dumps(cands, indent=2, default=str))
        else:
            _print_table(cands)
        return 0

    out_dir = args.out or "paperconan_data"
    summary = download_candidate(target, out_dir, tabular_only=not args.all)
    print(f"downloaded {len(summary['downloaded'])} file(s) from {target['cand_id']} -> {out_dir}")
    for p in summary["downloaded"]:
        print(f"  {p}")
    for s in summary["skipped"]:
        print(f"  skipped {s['name']}: {s['reason']}")
    if summary["downloaded"]:
        print(f"\n  → now run: paperconan {out_dir}")
    return 0
```

- [ ] **Step 4: Wire the subcommand into `main()` in `src/paperconan/_audit.py`**

Replace the first two lines of `main()` (the `ap = argparse.ArgumentParser(...)` setup) with an early dispatch. The current `main()` starts:

```python
def main():
    ap = argparse.ArgumentParser(description="Scan a paper's source-data xlsx files for statistical-signal patterns")
```

Insert the dispatch immediately inside `main()`, before that line:

```python
def main():
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        from .fetch._cli import fetch_main
        sys.exit(fetch_main(sys.argv[2:]))
    ap = argparse.ArgumentParser(description="Scan a paper's source-data xlsx files for statistical-signal patterns")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/fetch/test_cli.py -v`
Expected: PASS (2 tests)

Run: `python -m pytest tests/ -q`
Expected: PASS (all prior tests still green)

- [ ] **Step 6: Manual smoke (back-compat + new subcommand help)**

Run: `paperconan --help` (still shows scan options)
Run: `paperconan fetch --help` (shows fetch options)
Expected: both succeed, exit 0

- [ ] **Step 7: Commit**

```bash
git add src/paperconan/fetch/_cli.py src/paperconan/_audit.py tests/fetch/test_cli.py
git commit -m "feat(fetch): paperconan fetch CLI subcommand + main() dispatch"
```

---

### Task 11: Live network smoke test (skipped by default)

**Files:**
- Create: `tests/fetch/test_live_network.py`

- [ ] **Step 1: Write the network-marked test**

```python
# tests/fetch/test_live_network.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PAPERCONAN_LIVE") != "1",
    reason="live network test; set PAPERCONAN_LIVE=1 to run")


def test_zenodo_search_live():
    from paperconan.fetch import _sources
    cands = _sources.search_zenodo("spreadsheet", size=3)
    assert isinstance(cands, list)
    # at least one Zenodo record should come back for a common term
    assert cands and cands[0]["source"] == "zenodo"
```

- [ ] **Step 2: Register the marker to avoid warnings — modify `pyproject.toml`**

Add under `[tool.pytest.ini_options]` (create the table if absent):

```toml
[tool.pytest.ini_options]
markers = ["network: live network test, skipped unless PAPERCONAN_LIVE=1"]
```

- [ ] **Step 3: Verify it skips by default**

Run: `python -m pytest tests/fetch/test_live_network.py -v`
Expected: SKIPPED (1 skipped)

- [ ] **Step 4: Optionally verify live**

Run: `PAPERCONAN_LIVE=1 python -m pytest tests/fetch/test_live_network.py -v`
Expected: PASS (requires internet)

- [ ] **Step 5: Commit**

```bash
git add tests/fetch/test_live_network.py pyproject.toml
git commit -m "test(fetch): opt-in live network smoke test"
```

---

### Task 12: Docs + version bump (SKILL.md, README, version)

**Files:**
- Modify: `skills/paperconan/SKILL.md`
- Modify: `skills/paperconan/references/interpretation.md`
- Modify: `README.md`
- Modify: `src/paperconan/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version**

In `src/paperconan/__init__.py` change `__version__ = "0.3.0"` to `__version__ = "0.4.0"`.
In `pyproject.toml` change `version = "0.3.0"` to `version = "0.4.0"`.

- [ ] **Step 2: Add the fetch-then-audit workflow to `skills/paperconan/SKILL.md`**

After the "How to invoke" section, add:

```markdown
## Fetching a paper's data automatically

If the user gives a paper (DOI or title) instead of a local directory:

```bash
paperconan fetch "<DOI or title>"                 # list candidate datasets + match signals
paperconan fetch "<DOI>" --download <cand_id> --out data/   # download chosen candidate's tabular files
paperconan data/                                  # then audit as usual
```

Workflow:
1. Run `paperconan fetch "<DOI>"`. Each candidate has `match_signals`
   (`doi_in_related`, `title_overlap`, `author_overlap`).
2. **You decide the match** — prefer `doi_in_related: true`; otherwise weigh title/author
   overlap. If unsure, show the user the candidates and ask.
3. Download the chosen candidate, then run `paperconan <dir>` on the output.

### Honesty rules (REQUIRED)
- Searched repositories are Zenodo / Figshare / Dryad only.
- If a candidate has no `.xlsx/.csv/.tsv`, say so and name the other file types.
- If nothing matches, tell the user the data may be in journal supplementary
  (paywalled) or simply not deposited — never imply "checked = clean".
- Do not bypass paywalls or scrape publisher sites.
```

- [ ] **Step 3: Extend the frontmatter description trigger in `skills/paperconan/SKILL.md`**

Append to the existing `description:` value, before the closing period:
`, 从数据库下载论文数据, 找源数据, fetch paper data, download source data and analyze`

- [ ] **Step 4: Add a fetch note to `README.md`**

After the "安装 & 跑" code block, add:

```markdown
### 自动抓取论文数据（v0.4）

只有论文、没有本地数据时，可以让 paperconan 去开放数据仓库找：

```bash
paperconan fetch "10.xxxx/your.doi"            # 列出 Zenodo/Figshare/Dryad 的候选数据集 + 匹配信号
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan data/                                # 再照常分析
```

只覆盖开放仓库、不绕付费墙；很多论文没把数据存进可机读仓库，抓不到会如实告知。
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests green, 1 network test skipped)

Run: `paperconan --version`
Expected: `paperconan 0.4.0`

- [ ] **Step 6: Commit**

```bash
git add skills/ README.md src/paperconan/__init__.py pyproject.toml
git commit -m "docs(fetch): SKILL fetch workflow + honesty rules, README, v0.4.0"
```

---

## Self-review notes

- **Spec coverage:** discovery via 3 repos (Tasks 4–6, 9), paper-query resolution + match
  signals (Task 7), defensive download with content-type/size guards (Task 8), CLI subcommand
  with list/`--download`/`--auto`/`--all` and backward-compatible bare positional (Task 10),
  honesty rules + SKILL workflow (Task 12), offline-deterministic tests via fixtures (Tasks 3–10)
  plus opt-in live smoke (Task 11), stdlib-only / no new dependency (Tasks 2, 8). All spec
  sections map to a task.
- **Contract consistency:** Candidate/FileRef/match_signals keys are identical across Tasks
  4–10; `cand_id` format `"<source>:<id>"` used in search, ranking, and `--download` lookup.
- **No placeholders:** every code/test step contains complete runnable code and exact commands.
