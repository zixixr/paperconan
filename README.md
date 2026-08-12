**English** | [简体中文](README.zh-CN.md)

# PaperConan

**A numerical detective for the source data behind published papers.**

[![PyPI](https://img.shields.io/pypi/v/paperconan)](https://pypi.org/project/paperconan/)
[![Python](https://img.shields.io/pypi/pyversions/paperconan)](https://pypi.org/project/paperconan/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

`paperconan` scans a paper's supplementary data files with a battery of numeric detectors — columns duplicated across sheets and files, constant offsets between supposedly independent replicates, row-wise scaled copies, reused decimal tails, and more — and pinpoints what deserves a human second look, down to the exact file, sheet, rows, columns, and rule that fired. It reads `.xlsx`, legacy `.xls`, `.xlsm`, `.csv`, and `.tsv`, extracts structured tables from supplementary `.pdf` / `.docx`, and can catalog local figure images when enabled with `--images`.

Two things to know up front:

- **It reports statistical signals, not conclusions about author intent.** Many raw hits have benign explanations. Final interpretation always requires the original tables, figure legends, Methods, the authors' response, and journal or institutional review.
- **It is designed to be driven by an AI agent.** The recommended setup pairs the CLI with the skill bundled in this repository, inside Claude Code, Codex, or a similar agent: you ask in plain language, and the agent runs the real detectors and interprets the output under explicit written rules instead of eyeballing numbers. This page follows that workflow; direct CLI and Python usage is covered in the [CLI and library reference](docs/cli.md).

**Who it's for:**

- Graduate students and early-career researchers running a sanity check before citing or building on a paper
- Labs, research groups, and departments triaging publicly available source data
- PubPeer posters who want the exact table, rows, columns, and rule before deciding what to ask
- Batch audits — a journal, an author group, or a list of DOIs, prioritized by an agent afterwards

**What it does not do:**

- Judge author intent or responsibility, or replace statistical peer review
- Interpret Western blots, microscopy images, gels, or image splicing on its own; semantic image review is handled by an external multimodal agent that can open the local images
- Digitize data points from bar-chart or line-chart pixels
- Configure model APIs, credentials, or provider SDKs; the deterministic `image_findings` are optional hints, not a complete review checklist
- Bypass paywalls — and it never treats "no public data found" as evidence that a paper's data have no issues

---

## See It in Action: A Real Adjudicated Report

The report below comes from a case that is already public and has been handled by the institution involved: the Nature paper *Human HDAC6 senses valine abundancy to regulate DNA damage* (Nature 637, 215–223, 2025; DOI [10.1038/s41586-024-08248-5](https://doi.org/10.1038/s41586-024-08248-5)). Inconsistencies in its source data were raised on [PubPeer](https://pubpeer.com/search?q=10.1038%2Fs41586-024-08248-5) starting in 2025 and drew wider attention in 2026. The authors' university later announced personnel actions: the corresponding author was removed as dean and demoted, and the first author's employment was terminated.

Our part is deliberately narrow and reproducible: feed the paper's public Nature source data to `paperconan`, have an AI agent write `verdict.json` under the skill's [adjudication protocol](#2-connect-the-skill-to-your-agent), and render the result with `paperconan report`.

![Example PaperConan adjudicated report showing a constant_offset signal in Fig. 4c of the Nature HDAC6 paper](docs/images/adjudication-report.png)

PaperConan's `constant_offset` detector independently flagged the following pattern: in `Source Data Fig.4` (labeled `Fig.4c` inside the sheet), the two **shHDAC6** columns labeled **VR (0 h)** and **VR (24 h)** — which should be independent measurements of the same sample batch at 0 and 24 hours — differ by **exactly 0.3 in every one of 35 rows** (0.45/0.15, 0.60/0.30, 3.34/3.04, and so on). This is the same Figure 4c inconsistency previously raised in public. The tool did not guess it from the plotted figure; it located the numerical pattern in the source data itself and named the exact file, sheet, rows, columns, and rule.

The finding was marked `confirmed` only after an **adversarial red-team review** that started from "assume it's a false positive" and worked through ten benign mechanisms one by one. The same scan raised more than 700 signals; the report kept the one that withstood that challenge and downgraded the rest. That restraint is what signal-not-verdict looks like in practice.

> **Keep the boundary clear:** PaperConan reports **reproducible numerical patterns**, not conclusions about author intent. The institutional actions above are **publicly documented facts**, not a PaperConan judgment. The tool's role ends at locating signals anyone can reproduce; interpretation still requires the original data, the authors' response, and journal or institutional review.
>
> For the complete commands used to reproduce this report, see [Reports and tuning › Adjudicated HTML reports](docs/reports.md#判定后-html-报告).

---

## Quick Start (Recommended: Agent + Skill)

### 1. Install the CLI (The Skill Calls It Behind the Scenes)

Python >= 3.10 is required.

```bash
pip install "paperconan[all]"   # [all] includes PDF / Word table extraction; recommended
pip install "paperconan[image]" # adds only image assets, PDF pages, and optional image hints
paperconan --version            # verify the installation
```

> The base package includes the Rust-powered `python-calamine` reader: legacy `.xls` / `.xlsm` / `.xlsb` work out of the box, and `.xlsx` reads are faster. Other install variants: [CLI and library reference › Installation](docs/cli.md#安装).
>
> Once the skill from Step 2 is installed, an agent with shell access and a Python environment (a local Claude Code or Codex session, for example) will detect a missing CLI and `pip install` it before its first scan — so you can skip this step and go straight to Step 2.

### 2. Connect the Skill to Your Agent

The simplest option is the cross-agent [`npx skills`](https://github.com/vercel-labs/skills) installer — one command covers Claude Code, Codex, Cursor, and other compatible agents:

```bash
npx skills add zixixr/paperconan                          # install for detected agents

# You can also select agents or installation scope:
npx skills add zixixr/paperconan -a claude-code -a codex  # install only for these two
npx skills add zixixr/paperconan -g                       # install globally for the user
```

The installer clones the repository, discovers the `paperconan` skill, and wires it up using each agent's own directory conventions — you never touch `~/.claude/skills` or the other agent-specific paths.

**Manual fallback:** if you would rather not use npx, or want the skill to track `git pull`, symlink it into Claude Code's personal skill directory:

```bash
git clone https://github.com/zixixr/paperconan.git
mkdir -p ~/.claude/skills
ln -s "$(pwd)/paperconan/skills/paperconan" ~/.claude/skills/paperconan
```

If your agent does not discover skill directories yet, reference `SKILL.md` from your project's agent instructions instead:

```bash
echo '@'"$(pwd)"'/paperconan/skills/paperconan/SKILL.md' >> AGENTS.md
```

Restart the agent session after installing or updating the skill so it gets picked up.

[`skills/paperconan/SKILL.md`](skills/paperconan/SKILL.md) is the agent entry point. It requires the agent to run the real detectors, interpret their output under the rules in `references/`, and preserve the **signal-not-verdict** boundary.

The skill ships a reusable, public adjudication protocol: [`adjudication-tiers.md`](skills/paperconan/references/adjudication-tiers.md) defines Tier 1/2/3 and `KEEP` / `DROP` / `NEEDS_HUMAN`; [`report-templates.md`](skills/paperconan/references/report-templates.md) defines the short report and the formal eight-section report; [`adversarial-review.md`](skills/paperconan/references/adversarial-review.md) defines the red-team review process. Tiers express review priority and how hard a signal is to explain through benign mechanisms — **they are not conclusions about author intent.**

### 3. Ask in Plain Language

Once the skill is connected, you don't need to remember any commands. Just ask:

- "Check this paper's source data for statistical signals worth reviewing: `10.1038/sxxxxx`"
- "Scan `~/Downloads/source_data/` and show me the few signals that most need human review"
- "Could this cross-sheet match be a false positive? Compare it with the original table"

The agent decides whether to `fetch` or `scan` directly, parses `scan.json`, loads the relevant reference material, and opens the original table when needed. Its answer comes with the evidence, the plausible benign explanations, and the scientific context a human still needs to supply.

Image review runs through the same flow. The agent first confirms it can open local images, then works through every item in `image_assets` — whole image first, original-pixel crops for small panels or unresolved details. `image_findings` are hints only, never a substitute for full asset coverage; every asset must end up recorded as reviewed, unresolved, unreadable, or deferred. Numeric and image assessments land in the same `verdict.json findings[]` array and produce a single adjudicated report.

The full command sequence behind all of this:

```bash
paperconan fetch "<DOI or title>" --auto --images --out data/
paperconan data/ --images
paperconan data/ --images --image-diagnostics
paperconan report data/audit/scan.json --verdict verdict.json --out adjudication.html
```

If the agent cannot open local images, it sets `image_review.status` to `unavailable_no_multimodal`, states explicitly that semantic image review is incomplete, and continues with the numeric review. PaperConan itself never calls or configures model services.

> An agent without a usable Python environment should ask you to run the commands locally. It must **never present an eyeballed guess as PaperConan output**.

---

## Reading the Reports: The Essentials

The `audit/report.html` generated directly by `paperconan <dir>` is a **workbench of raw signals from deterministic detectors**. By design it contains **many false positives** — shared controls, redrawn axes, unit conversions, derived columns, and other benign mechanisms account for most hits. It **is not a conclusion** and is not meant to be published as-is.

For a formal, interpreted report, use an agent together with the skill: the detectors produce reproducible raw signals and optional image hints; the agent checks each one against the original table, the full image, original-pixel crops, the figure legend, and Methods; only then are the numeric and image findings combined into an [adjudicated report](docs/reports.md#判定后-html-报告). The CLI alone never performs this semantic assessment.

How to read the raw signals, control false positives with `--profile`, and generate an adjudicated report: **[Reports and tuning](docs/reports.md)**.

---

## ⚠️ Important Notice

`paperconan` reports **algorithmically identified statistical signals**, not conclusions about author intent or responsibility. Final interpretation requires clarification from the original authors, verification by journal editors, or independent peer review.

**Use established channels:** post a specific, reproducible data inconsistency on PubPeer; contact the journal's ethics team; or, where appropriate, contact your institution's research integrity office.

**Do not:** publicly accuse individual authors on social media, present a PaperConan screenshot as a final conclusion, or skip author clarification and jump straight to a judgment.

The tool is neutral. Its use must be, too.

---

## Documentation

This page covers the main workflow; details live under [`docs/`](docs/):

- [What it detects](docs/detectors.md) — an overview of every detector
- [Reports and tuning](docs/reports.md) — reading reports, controlling false positives with `--profile`, generating adjudicated HTML reports
- [Recommended batch-scanning workflow](docs/batch-workflow.md) — fetch → scan → filter → build dossiers → agent adjudication → tiering → adversarial review
- [CLI and library reference](docs/cli.md) — installation, scanning, fetch, PDF/Word support, the Python library, memory/output safeguards
- [FAQ](docs/faq.md)

For the deeper rules used by **AI agents and the skill**, see [`skills/paperconan/references/`](skills/paperconan/references/):
[detectors](skills/paperconan/references/detectors.md) ·
[output-schema](skills/paperconan/references/output-schema.md) ·
[judgment-rubric](skills/paperconan/references/judgment-rubric.md) ·
[interpretation](skills/paperconan/references/interpretation.md) ·
[adjudication-tiers](skills/paperconan/references/adjudication-tiers.md) ·
[report-templates](skills/paperconan/references/report-templates.md) ·
[adversarial-review](skills/paperconan/references/adversarial-review.md) ·
[batch-workflow](skills/paperconan/references/batch-workflow.md) ·
[case-patterns](skills/paperconan/references/case-patterns.md)

---

## Example

[`examples/`](examples/) contains a complete synthetic demo: two synthetic source-data files, a generated `audit/scan.json` and `report.html`, screenshots, and a finding-by-finding walkthrough. Start with [examples/README.md](examples/README.md) and [examples/report-preview.png](examples/report-preview.png), or run it yourself:

```bash
cd examples
paperconan demo_paper
open demo_paper/audit/report.html
```

---

## Roadmap

**Done:**

- Inputs: `.xlsx` / legacy `.xls` / `.xlsm` / `.csv` / `.tsv`, plus tables from PDF / Word
- `paperconan fetch` — supplement discovery and download from open sources
- Agent skill bundle
- Columnar engine (fast calamine reads, including legacy Excel) with memory/output safeguards
- `review` / `forensic` / `triage` profiles and a deterministic prefilter
- Reuse/transformation detectors: whole-column reuse across sheets and files, matrix decimal-place reuse, contiguous-segment offsets, shared decimals across integer-separated values, fixed row-vector recurrence across figures, row-wise ratios/equality (including scaled reuse across blocks), round-number gaps with preserved tails, high-precision tail clustering
- HTML reports with evidence highlighting
- Local image-asset registration from permitted sources, optional non-gating image hints, coverage accounting by an external multimodal agent, and a unified adjudicated report for numeric and image findings

**Next:**

- Cross-paper scanning — reuse across multiple papers from one lab or author group
- Chart-pixel digitization
- More comprehensive deterministic image-pattern hints
- PubPeer Public API integration

Pull requests are welcome — new detector patterns, documentation, and demos alike.

---

## Why It Exists

PaperConan started as prep for a YouTube / Douyin / Bilibili video: scan publicly available source data from Nature-family papers and locate the numerical patterns that warrant explanation. It is open source so that people doing careful research can spend less time and attention on unreliable data.

## License

MIT.

## Acknowledgments

- *Detective Conan* © Gosho Aoyama / TMS Entertainment. The project takes its name — and its patience for small clues — from the series.
- PubPeer. PaperConan's output should ultimately support public questions that are specific, measured, and reproducible.
