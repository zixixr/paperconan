# paperconan data-fetch — design spec

**Status:** approved (brainstorming) · **Target:** paperconan v0.4 · **Date:** 2026-05-28

## Context

paperconan analyzes a local directory of `.xlsx`/`.csv`/`.tsv` source data. Today the
user must download a paper's supplementary data by hand before running it. The goal of
this feature is to let an agent, given a **paper** (DOI or title), automatically locate
and download that paper's tabular source data from open data repositories, then hand it
to the existing `scan_dir` pipeline.

This stays inside paperconan's ethos: surface signal through legitimate channels, never
bypass paywalls or violate publisher terms of service.

## Verification (done before design — real API calls, 2026-05-28)

Probed the candidate APIs with real identifiers. Findings that shaped this design:

- **Full pipeline proven end-to-end.** Searched Zenodo → downloaded a real 191 KB `.xlsx`
  (record `10277693`) → ran paperconan → 30 finding-blocks, 4 last-digit-anomaly sheets.
- **Zenodo, Figshare, Dryad all allow keyless public read** and expose direct file
  download URLs plus file types/sizes:
  - Zenodo: `GET /api/records?q=...` → `record.files[].links.self` (`.../files/<name>/content`). Clean.
  - Figshare: `POST /v2/articles/search` → `GET /v2/articles/{id}` → `files[].download_url` (ndownloader). Clean, keyless.
  - Dryad: `GET /api/v2/datasets/{doi-enc}` → `/versions` → `/versions/{id}/files` → `files[]._links.stash:download`. Listing works; **raw file download returned HTTP 401** on a bare file id — Dryad needs its proper per-version download flow, a known wrinkle the adapter must handle.
- **Paper → data structured back-links are unreliable.** Crossref `relation` was empty of
  data links for the test DOI; DataCite reverse query (`relatedIdentifiers.relatedIdentifier`)
  returned 0. **Conclusion: do not depend on back-links — search the repositories directly
  by DOI/title/author and present candidates.**
- **Repository → paper forward links do exist** (Zenodo `related_identifiers` carried
  `isPartOf`/`references` publication DOIs) and are usable as a match signal.
- Per-repo quirks confirmed: Dryad download flow, URL-encoding of filenames with `=`/spaces,
  differing JSON shapes. The fetch logic must be defensive and per-source.
- Publisher supplementary (Nature/Elsevier) = paywall/ToS → **excluded from v1**.

## Goals

- Given a paper DOI or title, find candidate datasets in Zenodo / Figshare / Dryad.
- Download only tabular files (`.xlsx`/`.csv`/`.tsv`) into a directory ready for `scan_dir`.
- Be honest when nothing is found or a dataset has no tabular files.
- Keep matching judgment with the agent/user; the tool reports candidates + signals, not verdicts.

## Non-goals (v1)

- Bypassing paywalls or scraping publisher sites.
- Auto-asserting that a candidate dataset belongs to the paper.
- Europe PMC supplementary, OSF, Mendeley Data, GEO/PRIDE (future).
- Cross-paper / whole-lab batch fetching (future).

## Architecture

New package `src/paperconan/fetch/`, **stdlib `urllib` only** (no new dependency — `urllib`
is what the verification used successfully; keeps paperconan lightweight):

- `_sources.py` — one adapter per repository, each with `search(query) -> list[Candidate]`
  and `list_files(candidate) -> list[FileRef]`. Each adapter absorbs its own quirks
  (Dryad version-download flow, filename encoding, JSON shape). Sources: `zenodo`,
  `figshare`, `dryad`.
- `_resolve.py` — normalize a paper DOI/title into a search query; optionally enrich via
  OpenAlex/Crossref to recover title/authors/year for better repository search and for
  computing `match_signals`.
- `_download.py` — defensive download: follow redirects, timeout, retry, max-size cap,
  content-type sniffing (never save an HTML error page as `.xlsx`), keep only tabular
  extensions unless `--all`.
- `__init__.py` — public entry points used by the CLI.

### Candidate contract (the object the agent reasons over)

```json
{
  "source": "zenodo",
  "id": "10277693",
  "doi": "10.5281/zenodo.10277693",
  "title": "...",
  "authors": ["..."],
  "published": "2023",
  "tabular_files": [{"name": "...", "ext": "xlsx", "size": 191562, "download_url": "..."}],
  "related_dois": ["10.15761/JTS.1000455"],
  "match_signals": {"doi_in_related": true, "title_overlap": 0.8, "author_overlap": 0.6}
}
```

The tool computes `match_signals` but does **not** decide the match — the agent uses these
signals (and `related_dois`) to pick, or asks the user.

## CLI surface (subcommands; bare positional stays backward-compatible)

```
paperconan <dir>                              # unchanged: analyze (default action)
paperconan fetch "<DOI or title>"             # search all 3 repos → ranked candidates (JSON + human table)
paperconan fetch "<DOI>" --download <cand-id> --out DIR   # download tabular files from a chosen candidate
paperconan fetch "<DOI>" --auto --out DIR     # opt-in: pick top candidate, download tabular files, then analyze
paperconan fetch "<DOI>" --all ...            # include non-tabular files
```

argparse gains a subparser; the bare `paperconan <dir>` form must keep working (analysis is
the default when the first token is not a known subcommand).

## Behavior decisions (approved)

- **Default = list candidates and let agent/user confirm**; `--auto` opts into picking the
  top-ranked candidate. (Analyzing the wrong dataset is worse than one extra step.)
- **Download = tabular files only** by default; `--all` overrides.

## Honesty rules (encoded in SKILL.md)

- Dataset found but no tabular files → say so and list the other file types.
- No candidate matches the paper → say data may be in journal supplementary (paywalled) or
  simply not deposited; never imply "checked = clean".
- Always report which repositories were searched.

## SKILL.md changes

- Add a "fetch-then-audit" workflow: resolve → search → judge candidate via `match_signals`
  → download → `scan_dir` → report.
- Add the matching + honesty rules and candidate-judgment guidance.
- Extend trigger description: download paper data, fetch source data, 从数据库下载论文数据,
  找源数据, 自动下载并分析.

## Testing strategy (offline-deterministic, matching existing philosophy)

- Capture the **real API responses** gathered during verification, save as JSON fixtures
  under `tests/fixtures/fetch/`, and unit-test parsing, ranking, file-filtering, and
  `match_signals` against them — no network in CI.
- One `@pytest.mark.network` live smoke test (skipped by default) that exercises a real
  Zenodo search + small download.
- Test the defensive download path with a fixture HTML-error-page body (must be rejected,
  not saved as data).

## Dependencies & risks

- No new runtime dependency (stdlib `urllib`). Network failure modes (timeouts, rate limits,
  503s) handled with retries + clear errors.
- Coverage is inherently partial: many papers never deposit machine-readable data. This is a
  documented limitation, surfaced to the user at runtime, not a bug.
- Repository APIs may change shape; adapters are isolated per source to localize breakage.

## Out of scope (future)

- Europe PMC supplementary, OSF, Mendeley Data, GEO/SRA/PRIDE.
- Cross-paper / whole-lab scans; PubPeer API integration.
- Bypassing any access control.
