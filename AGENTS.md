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

- Golden fixtures in `tests/golden/` and `tests/fixtures/`.
- **Live-network** tests are skipped unless `PAPERCONAN_LIVE=1` (pytest marker `network`).
- Detector correctness is guarded by golden + brute-force-oracle tests (e.g. GRIM/GRIMMER, FDR,
  decimal-tail gate). If you touch a detector, keep these green and add a fixture for the new case.

### False-positive benches

`tests/test_curve_bench_baseline.py` (short-row) and `tests/test_column_pair_bench_baseline.py`
(column-pair) are not unit tests. Each generates its own data, so the answer is known by
construction; runs the shipped detector over it; and freezes what came back. They exist so a
change to a detector's tolerance is argued against a measurement rather than against a corpus
sample, which is not an instrument: the same change measured over a small slice of the corpus and
over a larger one gave opposite answers about part of itself.

Practicalities: `pytest -k "not bench_baseline"` deselects both while iterating. Neither is marked
for opt-in — a bench skipped by default is green while the detector moves, which is the condition
they were written to end. Each carries a `__main__` block that reprints its frozen tables
paste-ready; regenerate that way rather than hand-editing a table.

Five rules, each of which cost review rounds to learn. Where a claim below has a size, the
recipe for measuring it is given instead of the figure — see the last rule for why:

- **Cost is what the AGENT sees, not what the detector emits.** The narrowing here is
  deliberately in the reading: SKILL.md's "Reading A Scan In Layers" has the agent go
  `overview` -> `drill <n>` -> `drill --kind` -> `explain <id>`, and `overview` shows at most
  `DEFAULT_MAX_LOCATIONS` panels. So measure a change by what that page does: whether the ranked
  location list moves, whether a known true signal is on it and at what rank, how many steps
  reach it. Both halves of a trade have to be quoted at that same layer.
  A count taken by calling a detector directly is not that. To see the size of the gap, take the
  densest paper you have and count three things -- what one detector returns, what survives into
  `scan.json`, what `overview` lists. Also compare per paper, over papers that completed under
  every setting: corpus papers differ enormously in size, so a sum over them is largely a
  statement about the biggest, and one paper timing out under some settings and not others can
  move a total further than the settings do. Sort the per-paper counts and see how much of the
  sum the top one carries.
  Read the stage you want to credit before crediting it. Every draft of this rule so far has
  credited a component that does not do the thing. Severity demotion does not gate the reading
  layer: `raw_severity` is frozen before `_demote_dense_relations` runs, and `_raw_severity_of`
  says in as many words that the rewritten field "must not drive routing". Family interleaving
  bounds one family's share of the page rather than preventing it. And what a probe skips
  depends on which detector it calls -- some apply the profile themselves before returning, and
  some are never routed to a panel at all. Each was reached by reasoning from a name, and each
  cost a review round.
- **A bench needs a true-positive stratum.** One made only of things that must not fire is passed
  perfectly by a detector that never fires. Include the relation the arm exists to catch, and
  freeze how much of it is currently found — including when that is "almost none", which is a
  recorded gap and not a target. Until a bench has one, a change to that arm can only be argued
  on its cost, and a cost with no measured benefit beside it is half a decision.
- **Freeze a verdict, not a count**, wherever a stratum can sit on a gate — a verdict being a
  band (silent / partial / pervasive, per finding kind) rather than the number of draws that
  fired. Whether a draw fires *at* a gate is a coin flip that no number of repeats turns into a
  constant, so re-deriving a count-based baseline at a neighbouring seed moves strata the
  detector never touched: the same defect the corpus sampling had, arriving from a new source.
  That is what killed the column-pair bench's first, count-based design; how much it bites
  depends on how many of a bench's strata sit on a gate, so measure it on yours rather than
  carrying a share from someone else's. Bands survive reseeding where counts do not — perturb `SEED` over a run of neighbouring
  values and re-measure both to see the difference for yourself. The short-row bench still freezes
  counts, and its own data shows the cost: reseed it and its busiest stratum ranges over most of
  what it can produce, with the committed draw at the top of that range. Treat a movement there
  as a prompt to re-measure across seeds, not as a size.
- **Keep it small; add capabilities singly.** A bench's defects concentrate in the layers added
  to make it "resolve" things rather than in its core — though nothing is exempt: a round spent
  fixing a bench's own claims turned up a defect in the DETECTOR, a constant named for the
  branches it was meant to govern that reached none of them. More than once
  a test written to repair the previous round's defect was itself defective: a control calibrated
  to the wrong gate stayed green through exactly the event its failure message described, and an
  assertion about two gates went vacuous when the first was *tightened*. So whenever you add an
  assertion to a bench, delete the thing it guards and confirm it goes red; if it stays green,
  you added decoration. A simple bench that measures one thing correctly beats an elaborate one
  making claims that do not hold.
- **Do not put a number in prose that no test asserts.** A measurement quoted in a comment to
  justify a design decision decays as the file changes, and one quoted without the configuration
  it was taken on is not falsifiable at all — a claim about "thirty seeds" that never said which
  thirty was false for the obvious choice and true only for an unstated one. Name what to measure
  and how; carry a figure only where it is structural (a literal in the source, greppable) or
  where a test checks it. This applies to non-numeric claims too, which decay the same way and
  are harder to spot. Three that were committed in this repo and are all false, quoted as they
  were written: a bench layout that "returns 0 no matter what the detector does"; a guard the
  design note "plans to remove"; and one that "changed only the finest rung". Note the last:
  what it replaced — "deleting that guard changes nothing" — was TRUE, and rewriting it to sound
  more specific is what made it false. Precision added without re-measuring is not precision.
  Re-measure before repeating a claim from another commit's prose, including your own.
  Keep the record of what was tried and rejected — that is what stops the next person re-running
  it — but as what happened, not as decimal places.

  A CHEAP FIRST PASS, and only a first pass: before committing, grep your own added prose for
  universal quantifiers — every, everything, none, all, always, never, only, whatever, "no matter
  what", "by construction" — and verify each separately. That is worth doing because one
  recurring class of false claim has exactly that shape: a sentence that would have been true
  with a qualifier and was written without one.

  It is NOT sufficient, and the first draft of this very paragraph claimed it was. Of the
  examples above, the design-note one carries no word on that list, and neither do other
  corrections made in these files — a comparative stated backwards, a mechanism attributed to
  the wrong gate, a generator described as drawing data it does not draw. Nor is the list
  stable: "no matter what" is on it only because writing this paragraph found it missing, and
  adding it changed which of the examples above the check would have caught. That is the
  argument for running the grep and against trusting it. Nothing replaces re-measuring; the
  grep only makes the cheapest subset free. It is also line-oriented, so a phrase wrapped across
  two lines slips through — including in this paragraph.

  Whatever the check, run it on the REPAIR and not just the first draft. Every instance of this
  found so far arrived in a sentence written to correct another sentence, because prose written
  to fix something reads as though it has already been checked.

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

## Releasing

The version is written in five places and `tests/test_packaging.py` pins all five to
`__version__`, so pytest is the check for them:

`pyproject.toml`, `src/paperconan/__init__.py`, `skills/paperconan/SKILL.md`
frontmatter, the sample in `skills/paperconan/references/output-schema.md`, and
`examples/demo_paper/audit/scan.json`.

What pytest cannot check is any of them against the git tag — the tag does not exist
when the suite runs. That pair is what `.github/workflows/release.yml` adds.

1. Move all five. For the demo scan, edit the version string; regenerate it only if
   detector output actually moved, because a regeneration writes an absolute `input_dir`
   into `examples/demo_paper/audit/scan.json` (`_audit.py` calls `os.path.abspath`) and
   the committed file holds a relative path.
2. `uv run pytest`, then `uv build`, then install the wheel into a fresh venv and run the
   CLI there. Verifying from the working tree does not prove the artifact works.
3. Push the tag, formatted exactly `vMAJOR.MINOR.PATCH` — the workflow's trigger matches
   nothing else, and a tag that does not match produces no run, no failure and no
   notification, which is the silence this whole arrangement exists to remove. The
   workflow re-runs the packaging tests, fails if the tag and `pyproject.toml` disagree,
   and creates the Release entry with generated notes.
4. Only then upload to PyPI (`uvx twine upload dist/*`; credentials from `~/.pypirc`).
   Tag first because PyPI forbids re-uploading a version: if the tag check is going to
   fail, it has to fail while the mistake is still fixable. This step is not automated —
   `.github/workflows/release.yml` says why.

Commit subjects reach the Releases page verbatim through the generated notes, so the
neutral-language rule and the no-DOIs rule apply to them the same as to anything else
published.

Tags and Releases are separate things. v0.8.4 and v0.8.5 both reached PyPI while the
Releases page still showed v0.8.3 from the month before, because "tag and upload" was
treated as the whole of a release. Anyone reading the project from that page saw a month
of silence across two releases. That is what step 3 exists to prevent.

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
