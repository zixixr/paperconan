---
name: paperconan
version: 0.7.0
description: Paper data sanity check — scan supplementary source data (.xlsx / .csv / .tsv files in a directory) for numerical fabrication red flags. Use when user mentions 论文数据检查, 论文数据造假, 数据 sanity check, 学术不端检测, 数据取证, 检查论文数据, paper data audit, source data audit, PubPeer prep, suspicious paper data, fabrication check, research integrity, or hands you a directory of supplementary data tables and asks "does this look real?". Produces structured findings (scan.json) the agent reads, plus a self-contained HTML report (report.html) the user opens in a browser, 从数据库下载论文数据, 找源数据, fetch paper data, download source data and analyze.
---

# paperconan — paper data sanity check

Tool repository: https://github.com/zixixr/paperconan

> **Signal, not verdict.** paperconan surfaces statistical anomalies for a human
> to follow up — it never proves misconduct, and you must never call a paper
> "fake"/"fraudulent" or name authors. Read the red-lines in
> "CRITICAL: signal, not verdict" at the end of this file before reporting anything.

## When to use

Use this skill when the user:

- Hands over a directory of supplementary source data (`.xlsx` / `.csv` / `.tsv`) and asks for a data integrity / sanity check
- Wants to prep a PubPeer post and asks you to surface suspicious numeric patterns first
- Asks about a paper they suspect has fabricated data and wants statistical signal before deciding next step
- Says things like "帮我看看这篇论文数据有没有问题", "扫一下这个 source data", "查查这些表格的可疑模式", "audit this paper's data"

## When NOT to use

- The concern is image fraud (Western blot, microscopy, gel splicing) — paperconan only inspects numeric tables
- The data lives in a **chart** (bar/line plot) rather than a table — paperconan does not digitize pixels, and does not OCR scanned images
- The input is `.xls` (old binary Excel) — convert to `.xlsx` first. paperconan reads `.xlsx` / `.csv` / `.tsv` natively, plus **tables inside `.pdf` / `.docx` supplements** when the `[all]` extra is installed (see Prerequisites)
- The user wants statistical methodology review or peer-review-style scrutiny — paperconan is forensic, not statistical

## Prerequisites

```bash
pip install paperconan          # base install (xlsx / csv / tsv)
pip install "paperconan[all]"   # + supplementary PDF / Word table extraction
# Dev install from a clone:  pip install -e /path/to/paperconan
```

Verify with `paperconan --help` (or `paperconan --version`).

A complete worked example — synthetic data + the report it produces + a guided
walkthrough of every finding — lives in the repo's
[`examples/`](https://github.com/zixixr/paperconan/tree/main/examples) directory
of the repo. Read it to see the output shape before running on real data.

## Runtime & graceful fallback

paperconan runs **real Python detectors** — its findings cannot be faked by
eyeballing a table. Pick the path that matches your environment, in order:

1. **Python + network (authoritative):** `pip install paperconan` →
   `paperconan <dir>`. Always prefer this. If `pip` isn't available, try
   `pipx run paperconan <dir>` or `uvx paperconan <dir>`.
2. **Python, no PyPI access:** install from a local clone if one exists
   (`pip install -e /path/to/paperconan`); otherwise tell the user.
3. **No Python runtime at all:** do **not** invent findings. Say plainly that you
   cannot run the authoritative scan in this environment, and tell the user to run
   `paperconan <dir>` on their own machine. You *may* offer a clearly-labelled,
   **non-authoritative** manual look using
   [references/detectors.md](references/detectors.md) — but mark it as a hint, not
   tool output, and never attach `high`/`medium`/`low` severities as if the
   detectors produced them.

The rule that never bends: **never present eyeballed guesses as paperconan
output.**

## How to invoke

Single command, takes a directory of data tables (`.xlsx` / `.csv` / `.tsv`):

```bash
paperconan <input-dir>
# Default output: <input-dir>/audit/scan.json + <input-dir>/audit/report.html
```

Common variants:

```bash
paperconan <input-dir> --out /tmp/audit-X     # custom output dir
paperconan <input-dir> --md                   # also write REPORT.md
paperconan <input-dir> --no-html              # only scan.json (CI / scripted use)
paperconan <input-dir> --profile forensic     # raw signals, nothing demoted (see Profiles)
```

Exit code is 0 even when findings are present — findings are not errors.

### Profiles — the false-positive filter you are reading through

`--profile {review,forensic,triage}` controls a false-positive interpretation
layer that sits **on top of** the raw detectors. **The default is `review`**, so
by the time you read scan.json some findings have already been quietly demoted —
you are never looking at raw detector output unless you ask for it.

| Profile | What it does | When to run it |
|---|---|---|
| `review` (default) | Demotes name-matched likely-FP findings to `low` but keeps them visible, tagged with why | Normal audits — the balanced default |
| `forensic` | Demotes **nothing** — every detector hit keeps its raw severity | When you want to second-guess a demotion, or verify a `high` the default may have hidden. This is the tool-level lever for the "先开原表再下结论" check |
| `triage` | Same demotions as `review`, but hides them (`profile_action: "hidden"`) instead of showing them | When you want the shortest clean list for a summary |

**How a finding gets demoted (review/triage):** `_profiles.py` matches column /
sheet names and finding shape against a few innocent-explanation patterns —
dose/time/index **axis** columns (`arithmetic_progression`), boundary values like
0/1/-1/100 (`within_col_value_duplication`), unit-conversion / derived relations
(`constant_ratio`, `exact_linear`, `sum_constant`), same-figure or
source-data replots (`cross_sheet_position_identical` with `perfect_dup`), and
omics/large-matrix boundary floods. Each demotion writes a
`false_positive_context` tag + a `likely_benign` note onto the finding.

**Practical rule:** if a `review`-profile finding sits at `low` with a
`profile_action` of `demoted`, that severity is the *filter's* opinion, not the
detector's. Before you tell the user "nothing high here," re-run
`--profile forensic` and check what the raw severities were — a demotion is a
name-regex heuristic and can be wrong.

## Fetching a paper's data automatically

> **Secondary, network-dependent.** This needs outbound access to Zenodo /
> Figshare / Europe PMC / NCBI. In a sandboxed runtime without network it will
> not work — fall back to asking the user for a local data directory and audit
> that instead. The local audit above is the core capability; fetch is a
> convenience on top of it.

If the user gives a paper (DOI or title) instead of a local directory:

```bash
paperconan fetch "<DOI or title>"                 # list candidate datasets + match signals
paperconan fetch "<DOI or title>" --json          # same listing as machine-readable JSON (parse this, don't scrape the table)
paperconan fetch "<DOI>" --download <cand_id> --out data/   # download chosen candidate's tabular files
paperconan data/                                  # then audit as usual
```

Other flags: `--all` (also download non-tabular files), `--per-source N` (max
results per repository, default 5), `--auto` (download only a confidently-matched
top candidate), `--force` (download a no-match candidate anyway).

Workflow:
1. Run `paperconan fetch "<DOI>" --json` and parse the candidates. Each has `match_signals`
   (`doi_in_related`, `title_overlap`, `author_overlap`).
2. **You decide the match** — prefer `doi_in_related: true`; otherwise weigh title/author
   overlap. If unsure, show the user the candidates and ask. Repository full-text search
   (especially figshare/zenodo) often returns **unrelated deposits**, so `fetch --auto`
   refuses to download a candidate with no DOI match / weak title overlap (it falls back
   to journal guidance), and `fetch --download <id>` of such a candidate requires `--force`.
   A candidate flagged `⚠ no DOI/title match` in the listing is probably not this paper's data.
3. Download the chosen candidate, then run `paperconan <dir>` on the output.

### Honesty rules (REQUIRED)
- Searched repositories are Zenodo / Figshare / Dryad / Europe PMC.
- If a candidate has no `.xlsx/.csv/.tsv`, say so and name the other file types.
- If nothing matches, `fetch` now prints a journal-guidance block derived from the
  DOI (publisher + `doi.org` article link + where that publisher puts source data,
  e.g. Nature's `...MOESM<N>_ESM.xlsx`). Relay it — never imply "checked = clean".
- Do not bypass paywalls or scrape publisher sites.
- Download works keyless for **Zenodo and Figshare**. **Europe PMC** is also keyless:
  for open-access papers it serves supplementary material as one zip, and `fetch`
  downloads it and extracts the tabular members automatically. **Dryad** is
  discovery/listing only — its download API needs authentication, so report Dryad hits
  to the user and point them to the Dryad dataset page to download the files manually.

## How to read the output

Three artifacts may exist in the output dir:

| File | Audience | Purpose |
|---|---|---|
| `scan.json` | **you (the agent)** | full structured findings — parse this when analyzing |
| `report.html` | **the user** | self-contained interactive HTML report — tell the user to open it in a browser |
| `REPORT.md` | optional | markdown report; only present with `--md` |

### scan.json top-level schema

```json
{
  "tool": "paperconan",
  "tool_version": "0.7.0",        // matches the pyproject version; provenance for archived reports
  "scanned_at": "2026-05-29T02:08:53+00:00",
  "profile": "review",            // which FP profile ran (review|forensic|triage) — severities are post-filter unless "forensic"
  "input_dir": "...",
  "paper": {"doi": "10.1038/...", "title": "..."},  // provenance, or null (see below)
  "n_files": 3,
  "n_blocks_with_findings": 8,
  "scan_errors": [                // files that failed to parse — surface these, don't imply a clean scan
    {"file": "broken.xlsx", "error": "..."}
  ],
  "scan_stats": {                 // per-file / per-sheet sizing + timing (files[], sheets[], elapsed_ms)
    "files": [...], "sheets": [...], "elapsed_ms": 412.5
  },
  "relations_blocks": [
    {
      "file": "ED_Fig8b.xlsx",
      "sheet": "Sheet1",
      "block": {"rows": "6-15", "cols": "1-30", "header": [...]},
      "relations": [...],              // cross-column relations
      "progressions": [...],           // arithmetic progressions
      "equal_pairs": [...],            // pairs of columns with many equal rows
      "within_col": [...],             // within-column anomalies
      "identical_after_rounding": [...], // cells matching after rounding
      "grim": [...]                    // GRIM/GRIMMER: reported mean/SD impossible for integer data
    }
  ],
  // per-sheet last-digit χ². Each: {label, n, chi2, p, p_adj, fdr_significant, counts, top}
  // Filter on fdr_significant (BH-FDR q ≤ 0.05), NOT raw p — dozens of sheets are tested.
  "digit_distribution": [...],
  // per-sheet two-decimal ending counts. Each: {label, n, n_unique, top}
  "decimal_endings": [...],
  // bit-identical / value-overlap across sheets (same file OR cross-file). See fields below.
  "cross_sheet_findings": [...]
}
```

`paper` provenance is populated from a `paperconan_source.json` sidecar that
`paperconan fetch --download/--auto` writes alongside the data, or from
`paperconan <dir> --doi <DOI> --title <T>`. It is `null` when neither is present
(a bare directory audit) — never read `null` as "no paper".

### Every finding has

- `kind`: detector name (see [references/detectors.md](references/detectors.md))
- `severity`: `"high"` | `"medium"` | `"low"`
- `rule`: human-readable rule string e.g. `col[27] ≡ col[28] in 9/10 rows`
- `n`: sample size for the rule
- `evidence`: block snippet `{headers, rows, highlight_cols, ...}` — used by report.html, but you can also surface a few highlighted values if useful
- `likely_benign` (optional): a common innocent explanation for this kind — surface it to the user alongside the finding so a signal is never reported as a verdict
- `profile_action`: `"kept"` | `"demoted"` | `"hidden"` — what the active profile did to this finding. `"demoted"`/`"hidden"` means the current `severity` is the **filter's** downgrade, not the detector's raw verdict (always `"kept"` under `--profile forensic`). See the Profiles section.
- `false_positive_context` (list): machine tags for *why* it was demoted — e.g. `axis_or_scan_column`, `censoring_or_boundary_value`, `derived_or_unit_conversion`, `same_data_replot_or_duplicate_upload`, `omics_or_large_matrix_boundary_flood`. Map these back to the "常见误报" notes in [references/detectors.md](references/detectors.md).
- `dense_block` (optional, column-relation / equal-pair findings): `true` means this finding comes from a sheet that floods with pairwise column relations (a dense / correlated matrix — correlation tables, normalized replicate panels). Such findings are auto-demoted to `low` severity because identical/linear columns there are expected by construction, not a duplication red flag — don't treat them as high-severity signal

### cross_sheet_findings fields

- `same_file`: whether the two sheets live in one workbook or span two files
- `figure_a` / `figure_b` / `same_figure`: parsed figure identity (e.g. `main:5`, `ext:6`). When `same_figure` is true the overlap is a combined-vs-individual re-plot of one display item — it is **downgraded to `low`** and carries a `context` note. Cross-figure / cross-file overlaps keep `high`/`medium` and are the ones worth checking against the legend.
- `delta`: how the two near-duplicate tables differ — `{pattern, modified_cells, shared_values, only_in_a, only_in_b}`. `pattern` is one of:
  - `perfect_dup` — identical value multiset (clean re-plot)
  - `superset` — one side strictly contains the other (e.g. an extra replicate column, n=5 vs n=6)
  - `value_tweaked` — cells changed in place (copy-then-tweak fingerprint; most worth investigating)
  - `value_divergent` — both sides hold values the other lacks

### What to surface to the user

1. **Cross-sheet findings first.** `cross_sheet_position_identical` is the single most-investigated paperconan signal — same position, same value, across "independent" sheets.
2. **Group by file, then by severity.** Most users want "which figure should I look at first."
3. **Always include severity badges in your summary.** Don't flatten `high` and `medium` together.
4. **Point them to `report.html`.** That file has the actual table snippets with the suspicious cells highlighted — much easier for the user than re-reading xlsx.
5. **Read [references/interpretation.md](references/interpretation.md)** for the response template and the red lines.

## CRITICAL: signal, not verdict

paperconan output is a **statistical anomaly**, NOT a determination of misconduct. Do not:

- ❌ Say "this paper is fake / fabricated / fraudulent"
- ❌ Name authors as having "fabricated data"
- ❌ Suggest the user post accusations on Weibo / Twitter / Zhihu
- ❌ Use the word "实锤" (rock-solid proof) — it isn't

Do:

- ✅ Report as "N high-severity suspicious patterns" with concrete locations
- ✅ Surface common false positives (shared controls, dose-axis duplication, count quantization)
- ✅ Recommend the user verify against figure legend + Methods, then escalate via PubPeer / journal editor / research integrity office

Full response template lives in [references/interpretation.md](references/interpretation.md).
