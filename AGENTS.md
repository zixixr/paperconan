# AGENTS.md — paperconan

Numeric forensics for a paper's **supplementary source data** (`.xlsx` / legacy `.xls` / `.xlsm` /
`.csv` / `.tsv`, plus tables inside `.pdf` / `.docx`). It runs a battery of numeric detectors and
surfaces the *locations worth a human re-check* — file, sheet, columns, rows, and the exact rule.

Product/usage docs (install, workflows, report reading, CLI): **[README.md](README.md)**. This file
is the operating guide for AI agents and contributors working *in* the repo.

---

## ⚠️ The one hard rule — neutral language, always

paperconan outputs a **statistical signal, not a misconduct verdict.** In *all* output — reports,
code comments, docstrings, commit messages, PR text, variable names — describe findings as
**"统计信号 / 数据不一致 / 待解释异常 / 请作者澄清 / data inconsistency"**. **Never** write
"fraud", "fabrication", "faked", "misconduct", "guilty", or any accusation of a person. Final
judgement always requires the original data, figure legends, Methods, the authors' response, and
journal/institution review. This red line is non-negotiable and applies everywhere.

---

## Setup

Python ≥ 3.10. The repo uses **uv** (`.venv/`, `uv.lock`, `.python-version`).

```bash
uv sync                       # create/refresh .venv from uv.lock
# or, plain pip for a dev install:
pip install -e ".[dev]"       # engine + pdf/docx extractors + test deps
```

The Rust reader `python-calamine` is a **base** dependency (not optional): it is the *only* reader
for legacy `.xls` / `.xlsm` / `.xlsb` and the fast path for `.xlsx`. PDF/Word table extraction
(`pdfplumber`, `python-docx`) are optional extras, imported lazily.

## Test

```bash
uv run pytest                 # or: .venv/bin/pytest
uv run pytest tests/test_decimal_tail_gate.py -q     # a single file
```

- 41 test files; golden fixtures in `tests/golden/` and `tests/fixtures/`.
- **Live-network** tests are skipped unless `PAPERCONAN_LIVE=1` (pytest marker `network`).
- Detector correctness is guarded by golden + brute-force-oracle tests (e.g. GRIM/GRIMMER, FDR,
  decimal-tail gate). If you touch a detector, keep these green and add a fixture for the new case.

## Run the CLI

```bash
paperconan <in_dir> [--profile review|forensic|triage] [--doi X] [--md] [--no-html]
# scan a directory of source data → writes <in_dir>/audit/{scan.json, report.html}
paperconan report scan.json --verdict verdict.json --out report.html
# render an adjudicated report from a human/agent verdict
```

Entry point: `paperconan._audit:main` (see `[project.scripts]` in `pyproject.toml`).

---

## Repo map

```
src/paperconan/            the engine (installed package)
  _audit.py                CLI entry + orchestration: load → detect → prefilter → report
  detectors.py             per-column & column-pair detectors (offset/ratio/sum/linear/…)
  collisions.py            cross-sheet / cross-file duplication detection
  _sheet.py                columnar Sheet substrate (numpy-backed; the array engine)
  _prefilter.py            _profiles.py   false-positive control (profiles, prefilter gates)
  _extract.py              table extraction from pdf/docx (optional deps)
  _html.py                 _adjudicated_html.py   HTML report renderers
  packet.py schema.py io.py  packet/schema/IO plumbing
  fetch/                   supplement download helpers
tests/                     pytest suite + golden/ + fixtures/
skills/paperconan/         the distributable AI skill (SKILL.md) — the recommended way to use the tool
examples/demo_paper/       committed demo (the ONLY sample data allowed in git)
docs/                      design notes, plans, images
recheck/  batches/         LOCAL-ONLY audit working dirs — GITIGNORED, see below
```

## Conventions & gotchas

- **Signal, not verdict** (see the red line above). False positives are controlled by `--profile`
  and the prefilter, not by weakening detectors — see README "误报控制".
- **Memory caps** on huge supplements are env-tunable: `PAPERCONAN_MAX_FILE_MB`,
  `PAPERCONAN_MAX_CELLS`, `PAPERCONAN_MAX_EVIDENCE_ROWS` / `_COLS`, and for the
  `explain --full` re-read path `PAPERCONAN_MAX_FULL_EVIDENCE_CELLS` /
  `PAPERCONAN_FULL_EVIDENCE_ROW_LIMIT`. Respect them in new code paths;
  large genomics supplements can OOM otherwise.
- **Don't commit data.** `.gitignore` blocks `*.xlsx`, `audit/`, `scan.json`, and the whole
  `recheck/` and `batches/` trees. The only exemption is `examples/**` (the demo). Never add a
  paper's real source data, DOIs, or judgments to git.
- Keep changes deterministic — detectors and reports must produce identical output for identical
  input (golden tests depend on it).

## The local audit pipeline (`recheck/`, local-only)

A separate, **gitignored** backfill/audit pipeline lives under `recheck/codex_task/batch2/`. It runs
the detection engine over a corpus, AI-judges candidates, and builds evidence reports. If you work
there, its authoritative docs are:

- `recheck/codex_task/batch2/PROJECT_STATE.md` — current funnel, numbers, pipeline, where results live.
- `recheck/codex_task/batch2/keep_reports/README.md` — the KEEP → high-fidelity HTML reports pipeline
  (`sync.sh` one-command incremental sync; DB/Blob access details are documented there, not here).

Those trees contain paper DOIs, judgments, dossiers, and source data — treat them as sensitive and
never commit them or paste their contents (or any credentials) into committed files.

## The skill

The recommended way to *use* paperconan is the bundled skill: `skills/paperconan/SKILL.md`. It
teaches an agent the scan → interpret → adjudicate protocol with the neutral-language rules baked in.
