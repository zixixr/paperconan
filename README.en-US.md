

# Paper Conan / paperconan

> **The truth is always a single one!**
>
> **The academic world is currently riddled with problems,**
> **everyone should be wary of paperconan's deductions!**
> **The only one who sees through the truth of paper data,**
> **is this seemingly simple Python tool,**
> **whose wisdom far exceeds the norm—**
>
> **the Great Detective, Paper Conan!**

---

## What Is It

`paperconan` is a **paper source data sanity check** tool. You feed it a directory (`.xlsx` / legacy `.xls` / `.xlsm` / `.csv` / `.tsv`; you can also mix in structured tables from supplementary `.pdf` / `.docx` files, as well as local images explicitly enabled via `--images`). It runs numerical detectors, registers image assets, and passes "locations worth manual review" to an external Agent for unified judgment.

**It outputs statistical signals, not judgments of author intent.** Final judgment still requires consulting the original tables, figure legends, Methods, author responses, and journal/institution verification.

Its most common and highly recommended usage is **running it via an AI agent (like Claude Code or Codex) combined with this repository's skill**: You state your needs in natural language, the agent calls the actual Python detectors, parses the results, interprets them according to rules, rather than guessing numbers with your eyes. The following sections focus on this scenario. For pure CLI / Python library usage, see [CLI & Library Reference](docs/cli.md).

**Who is it for:**

- Graduate students / junior faculty: run a sanity check before citing a paper
- Labs / research groups / departments: conduct initial screening of publicly available source data
- PubPeer prep: pinpoint specific tables, rows/columns, and rules first, then decide how to frame your questions
- Batch auditing: scan a journal / author group / batch of DOIs, then use an agent for tiered review

**What it does NOT do:**

- Does not judge author intent or responsibility, nor does it replace statistical peer review
- Does not autonomously judge Western blots, microscopy images, gel images, or image composites; image semantic review is handled by external multimodal Agents capable of reading local images
- Does not digitize data points from bar/line chart pixels
- Does not configure model APIs, keys, or provider SDKs; deterministic `image_findings` are merely optional prompts, not a complete review checklist
- Does not bypass paywalls, nor does it treat "no public data found" as "the paper is clean"

---

## A Quick Look: A Real Post-Adjudication Report

The report below comes from a **publicly disclosed and institutionally handled** case—a Nature paper titled *Human HDAC6 senses valine abundancy to regulate DNA damage* (Nature 637, 215–223, 2025; DOI [10.1038/s41586-024-08248-5](https://doi.org/10.1038/s41586-024-08248-5)). Source data issues in this paper were publicly raised on [PubPeer](https://pubpeer.com/search?q=10.1038%2Fs41586-024-08248-5) starting in 2025, further disseminated by science communication accounts in 2026, and subsequently led to an institutional announcement: the corresponding author was removed from their dean position and demoted, and the first author was dismissed.

We only do one reproducible thing: feed this paper's publicly available Nature source data into `paperconan`, have the AI agent generate a `verdict.json` according to the skill's [adjudication protocol](#2-connect-the-skill-to-your-agent), and finally render this report using `paperconan report`.

![paperconan post-adjudication report example: constant_offset signal in Fig.4c of the Nature HDAC6 paper](docs/images/adjudication-report.png)

The `constant_offset` detector in paperconan **independently** flagged that in `Source Data Fig.4` (labeled `Fig.4c` in the table), the two columns for the **shHDAC6** group labeled **VR (0 h)** and **VR (24 h)** (which should be independent measurements of the same sample batch at 0 h and 24 h) **strictly differ by a fixed 0.3 row by row across all 35 rows** (0.45/0.15, 0.60/0.30, 3.34/3.04…). This matches the anomaly in Figure 4c previously pointed out by the public—the tool did not "guess by looking"; it precisely located this numerical pattern in the raw source data to "[which file, which table, which rows, which rule]."

This finding also underwent a round of **red-team adversarial review** (starting from the assumption it was a false positive to find counterarguments, systematically ruling out 10 benign mechanisms) before being marked `confirmed`. A single scan actually triggered 700+ signals; the report only includes the **one that withstands counter-challenges** in its verdict, downgrading the rest—this restraint is the practical implementation of signal-not-verdict.

> **Stay on the line**: paperconan outputs **verifiable numerical patterns**, not conclusions about author intent. The institutional actions above are **publicly established facts**, not determinations by paperconan. The tool only serves to clearly locate signals that anyone can reproduce; subsequent judgments still rely on raw data, author responses, and institution/journal verification.
>
> See the full commands to reproduce this report at [Reports & Tuning › Post-Adjudication HTML Report](docs/reports.md#判定后-html-报告).

---

## Quick Start (Recommended: Agent + Skill)

### 1. Install the CLI (the skill calls it in the background)

Requires Python >= 3.10.

```bash
pip install "paperconan[all]"   # [all] includes PDF / Word table extraction, recommended
pip install "paperconan[image]" # Only adds image assets, PDF pages, and optional image prompts
paperconan --version            # Verify installation
```

> The base version already includes the Rust-powered reading engine (`python-calamine`); legacy `.xls` / `.xlsm` / `.xlsb` read out-of-the-box, and `.xlsx` is also faster; see other installation variants at [CLI & Library Reference › Installation](docs/cli.md#安装).
>
> Agents with shell + Python environments (e.g., local Claude Code / Codex) will automatically detect and `pip install` this CLI before the first scan once the skill is installed. You can also skip this step and proceed directly to step 2.

### 2. Connect the Skill to Your Agent

The simplest way: use the cross-agent installer [`npx skills`](https://github.com/vercel-labs/skills) to set up Claude Code / Codex / Cursor, etc., with a single command:

```bash
npx skills add zixixr/paperconan                          # Auto-installs to detected agents

# Or specify agents or installation scope:
npx skills add zixixr/paperconan -a claude-code -a codex  # Only install for these two
npx skills add zixixr/paperconan -g                       # Install globally (user-level)
```

It will clone the repo, discover the `paperconan` skill inside, and wire it up according to each agent's own directory conventions—you don't need to worry about path differences like `~/.claude/skills` or Codex's specific paths.

**Manual method (fallback):** If you prefer not to use npx, or want the skill to update along with `git pull`, you can symlink your personal Claude Code skill directory:

```bash
git clone https://github.com/zixixr/paperconan.git
mkdir -p ~/.claude/skills
ln -s "$(pwd)/paperconan/skills/paperconan" ~/.claude/skills/paperconan
```

If your agent doesn't yet support skill directory discovery, as a fallback, you can reference `SKILL.md` in your project prompts:

```bash
echo '@'"$(pwd)"'/paperconan/skills/paperconan/SKILL.md' >> AGENTS.md
```

After installing or updating the skill, restart your agent session to let it rediscover the skill.

[`skills/paperconan/SKILL.md`](skills/paperconan/SKILL.md) is the agent's entry point; it forces the agent to run real detectors, interpret results according to rules in `references/`, and strictly maintain the **signal-not-verdict** line.

The skill also contains publicly reusable adjudication protocols: [`adjudication-tiers.md`](skills/paperconan/references/adjudication-tiers.md) defines Tiers 1/2/3, `KEEP` / `DROP` / `NEEDS_HUMAN`; [`report-templates.md`](skills/paperconan/references/report-templates.md) defines short and formal 8-section reports; [`adversarial-review.md`](skills/paperconan/references/adversarial-review.md) defines the red-team review process. These tiers only indicate review priority and difficulty of innocent explanations, **not judgments of author intent**.

### 3. Request Using Natural Language

Once connected, you don't need to memorize any commands; just speak naturally, for example:

- "Check the source data for this paper for any issues: `10.1038/sxxxxx`"
- "Scan the `~/Downloads/source_data/` directory and pick out the top candidates for manual review"
- "Is this cross-sheet hit a false positive? Help me cross-reference it with the original table"

The agent will autonomously decide whether to `fetch` or `scan`, parse `scan.json`, load the corresponding reference, open the original table if necessary, and return an answer with evidence, benign explanations, and notes on "what manual context is still needed."

Image review follows the same workflow. The agent first confirms its ability to open local images, then processes each `image_asset`: it starts with the full image, then uses raw pixel crops for small panels or unresolved details. `image_findings` are for prompting only and cannot replace full asset coverage; every asset must fall into one of `reviewed`, `unresolved`, `unreadable`, or `deferred`. Numerical and image judgments are written into the same `verdict.json findings[]`, ultimately generating a single post-adjudication report.

The corresponding full commands are:

```bash
paperconan fetch "<DOI or title>" --auto --images --out data/
paperconan data/ --images
paperconan data/ --images --image-diagnostics
paperconan report data/audit/scan.json --verdict verdict.json --out adjudication.html
```

If the Agent lacks local image capabilities, it should set `image_review.status` to `unavailable_no_multimodal`, clearly state that image semantic review is incomplete, and proceed with numerical review. PaperConan itself does not invoke or configure model services.

> Agents without an available Python environment should ask you to run the commands locally. **Never substitute visual guessing for paperconan outputs.**

---

## How to Read the Report (Key Points)

`paperconan <dir>` directly generates `audit/report.html`, which is the **raw signal from deterministic detectors / a manual review workspace**—by design, it contains **many false positives** (shared controls, redrawn axes, unit conversions, derived columns... most hits have benign explanations), **does not represent any conclusion**, and is not suitable for direct external publication as-is.

**To obtain a formal, evaluated report, please use Agent + Skill**: Detectors only produce reproducible raw signals and optional image prompts. The Agent evaluates them one by one against the original table, full image, raw pixel crops, figure legends, and Methods, then generates a [post-adjudication report](docs/reports.md#判定后-html-报告) combining numerical and image findings. Pure CLI does not autonomously perform semantic judgment.

For details on how to read these raw signals, `--profile` false-positive control, and post-adjudication report generation, see **[Reports & Tuning](docs/reports.md)**.

---

## ⚠️ Important Disclaimer

`paperconan` outputs **algorithm-annotated suspicious patterns**, not conclusions of academic misconduct. Final determinations must be clarified by the original authors, verified by journal editors, or reviewed by independent peer review.

**Please follow official channels:** Submit suspicious signals to PubPeer / contact the journal for an ethics inquiry / if it involves your institution, route it through the research integrity office.

**Please do not:** Directly accuse specific authors on social media / treat paperconan screenshots as final conclusions / skip author clarification and jump to qualitative judgments.

The tool is neutral; the manner of its use must be responsible.

---

## Documentation

The main page is here; details are in [`docs/`](docs/):

- [What It Can Detect](docs/detectors.md) — Overview of all detectors
- [Reports & Tuning](docs/reports.md) — How to read reports, `--profile` false-positive control, post-adjudication HTML report
- [Recommended Batch Scan Workflow](docs/batch-workflow.md) — fetch → scan → filter → case filing → agent adjudication → tiering → adversarial review
- [CLI & Library Reference](docs/cli.md) — Installation, scanning, fetch, PDF/Word, Python library, memory/output protection
- [FAQ](docs/faq.md)

In-depth rules for **AI agents / skills** are in [`skills/paperconan/references/`](skills/paperconan/references/):
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

## Examples

[`examples/`](examples/) contains a complete synthetic demo: two synthetic source datasets, pre-generated `audit/scan.json` + `report.html`, screenshots, and line-by-line explanations. First check [examples/README.md](examples/README.md) and [examples/report-preview.png](examples/report-preview.png), or run it yourself:

```bash
cd examples
paperconan demo_paper
open demo_paper/audit/report.html
```

---

## Roadmap

**Completed:** `.xlsx` / legacy `.xls` / `.xlsm` / `.csv` / `.tsv` input · HTML report and evidence highlighting · PDF/Word table input · `paperconan fetch` for open-source retrieval and download · Agent skill bundle · Columnar engine (fast reading via calamine, including legacy Excel) + memory/output protection · `review` / `forensic` / `triage` profiles and deterministic prefilter · Cross-sheet / cross-file column reuse + matrix decimal-place reuse + continuous segment offset + integer difference shared decimals + fixed row vector cross-figure recurrence + **row ratio/similarity (including cross-block scaling reuse) + round-number difference tail protection + high-precision tail digit clustering** and other repetition/transformation detectors · Legitimate local image asset registration, optional non-gating image prompts, external multimodal agent coverage logging, and a unified post-adjudication report for numerical and image findings.

**Planned / Incomplete:** Cross-paper scanning (multiple papers from one lab/author group for shared review) · Chart pixel digitization · More comprehensive deterministic image pattern prompts · Integration with the PubPeer Public API.

PRs are welcome — adding detector modes, updating documentation, and creating demos are all encouraged.

---

## Origin / Background

This tool was originally created for a YouTube / Douyin / Bilibili video: scanning Nature and sub-journal papers using publicly available source data to locate suspicious numerical patterns. Open-sourced for everyone, hoping it can help diligent experimentalists reduce the probability of their work being crowded out by fabricated data.

## License

MIT.

## Acknowledgments

- Detective Conan / Detective Conan © Gosho Aoyama / TMS Entertainment. The opening narrative structure was adapted.
- PubPeer. paperconan's outputs should ultimately serve specific, restrained, and verifiable public inquiries.
