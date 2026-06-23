---
name: paperconan
version: 0.8.0
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

Verify with `paperconan --help` (or `paperconan --version`). A complete worked
example (synthetic data + the report it produces + a per-finding walkthrough) lives
in the repo's [`examples/`](https://github.com/zixixr/paperconan/tree/main/examples).

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

The rule that never bends: **never present eyeballed guesses as paperconan output.**

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

### Profiles — the false-positive filter you're reading through

`--profile {review,forensic,triage}` sits **on top of** the raw detectors, so by the
time you read scan.json some findings are already demoted — you're not seeing raw
detector output unless you ask.

- `review` (default) — demotes name-matched likely-FP findings to `low` but keeps
  them visible, tagged with why.
- `forensic` — demotes **nothing**; every hit keeps its raw severity. Re-run with
  this before telling the user "nothing high here" — a demotion is a name-regex
  heuristic and can be wrong.
- `triage` — same demotions as `review`, but hidden (`profile_action: "hidden"`).

A `low` finding with `profile_action: "demoted"` is the *filter's* opinion, not the
detector's. Demotion reasons + the full mapping are in
[references/detectors.md](references/detectors.md).

## Fetching a paper's data automatically

> **Secondary, network-dependent.** Needs outbound access to Zenodo / Figshare /
> Europe PMC / NCBI. In a sandboxed runtime without network it won't work — fall
> back to asking the user for a local data directory. The local audit above is the
> core capability; fetch is a convenience on top of it.

If the user gives a paper (DOI or title) instead of a local directory:

```bash
paperconan fetch "<DOI or title>"            # list candidate datasets + match signals
paperconan fetch "<DOI or title>" --json     # machine-readable listing (parse this)
paperconan fetch "<DOI>" --download <id> --out data/   # download a candidate's tabular files
paperconan data/                             # then audit as usual
```

Flags: `--all` (also non-tabular), `--per-source N` (default 5), `--auto` (download
only a confidently-matched top candidate), `--force` (download a no-match anyway).

**You decide the match** from each candidate's `match_signals` (`doi_in_related`,
`title_overlap`, `author_overlap`) — prefer `doi_in_related: true`. Repository
full-text search (esp. figshare/zenodo) often returns **unrelated deposits**, so
`--auto` refuses a no-DOI/weak-title candidate and `--download` of one needs `--force`.
A candidate flagged `⚠ no DOI/title match` is probably not this paper's data.

**Honesty rules (REQUIRED):** searched repos are Zenodo / Figshare / Dryad / Europe
PMC. If a candidate has no `.xlsx/.csv/.tsv`, say so and name the other types. If
nothing matches, `fetch` prints DOI-derived journal guidance — relay it, never imply
"checked = clean". Don't bypass paywalls or scrape publisher sites. Download is
keyless for Zenodo/Figshare and Europe PMC (OA supplements auto-extracted); **Dryad**
is listing-only (its download API needs auth) — point the user to the Dryad page.

## How to read the output

| File | Audience | Purpose |
|---|---|---|
| `scan.json` | **you (the agent)** | full structured findings — parse this |
| `report.html` | **the user** | self-contained interactive report — tell them to open it in a browser |
| `REPORT.md` | optional | markdown report; only with `--md` |

**Full structured schema** — top-level `scan.json` fields, the per-finding fields
(`kind` / `severity` / `rule` / `evidence` / `profile_action` /
`false_positive_context` / `dense_block` …), and `cross_sheet_findings` fields — is
in [references/output-schema.md](references/output-schema.md). Read it before parsing.

What to surface to the user:

1. **Cross-sheet findings first.** `cross_sheet_position_identical` is the single
   most-investigated paperconan signal — same position, same value, across
   "independent" sheets.
2. **Group by file, then severity** — most users want "which figure first."
3. **Always show severity badges** — don't flatten `high` and `medium`.
4. **Point them to `report.html`** — it has the table snippets with suspicious cells
   highlighted, much easier than re-reading xlsx.
5. Read [references/interpretation.md](references/interpretation.md) for the response
   template and the red lines.

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
