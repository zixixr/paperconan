# Bilingual README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a complete, idiomatic English repository README by default while retaining a complete Simplified Chinese version with reciprocal language links.

**Architecture:** Treat the two README files as a single documentation unit. Move the current Chinese content to `README.zh-CN.md`, localize the full content into English at `README.md`, then validate structural parity, relative links, Markdown syntax, and neutral-language compliance before one documentation commit.

**Tech Stack:** GitHub Flavored Markdown, relative repository links, shell-based structural checks, Git.

## Global Constraints

- `README.md` is the default English README; `README.zh-CN.md` is the Simplified Chinese README.
- Both files begin with an `English | 简体中文` language switch using relative links.
- Preserve every existing section, example, command, image, factual statement, warning, roadmap item, acknowledgment, and documentation link.
- Use idiomatic English localization rather than sentence-by-sentence literal translation.
- Keep commands, paths, option names, identifiers, detector names, and schema values unchanged.
- In both languages, describe findings only as statistical signals, data inconsistencies, unexplained anomalies, or matters requiring human review and author clarification.
- Do not modify documents under `docs/` or `skills/`, CLI behavior, detector behavior, examples, or report output.
- Do not stage or commit pre-existing unrelated working-tree files.

---

### Task 1: Localize and publish the bilingual README pair

**Files:**
- Modify: `README.md`
- Create: `README.zh-CN.md`

**Interfaces:**
- Consumes: the section structure, examples, commands, images, and relative links in the pre-change `README.md`
- Produces: an English repository landing page at `README.md` and a reciprocal Simplified Chinese page at `README.zh-CN.md`

- [ ] **Step 1: Capture the source structure and invariants**

Run:

```bash
git show HEAD:README.md | rg '^(#{1,6}) |^```|^!\['
git show HEAD:README.md | rg -o '\[[^]]+\]\([^)]+\)'
```

Expected: output enumerates the 16 top-level or nested headings, 12 fence markers, one image, and all current Markdown links that must remain represented after localization.

- [ ] **Step 2: Move the complete Chinese source and add its language switch**

Use `apply_patch` to create `README.zh-CN.md` with the complete current `README.md` content. Insert this immediately above its title:

```markdown
[English](README.md) | **简体中文**

```

Minimally revise phrases that conflict with the repository's neutral-language rule. In particular:

```text
弊病丛生                  -> 数据不一致时有发生
算法标注的可疑模式        -> 算法标注的统计信号
把可疑 signal 提交        -> 把待解释 signal 提交
定位可疑数值模式          -> 定位待解释的数值模式
被编造数据挤占空间        -> 被不可靠数据挤占空间
```

Keep the documented statement warning users not to make public accusations, because it is safety guidance rather than an accusation by the project.

- [ ] **Step 3: Write the complete idiomatic English localization**

Use `apply_patch` to replace `README.md` with a section-faithful English version. Insert this immediately above the title:

```markdown
**English** | [简体中文](README.zh-CN.md)

```

Use these English headings in the same order as the Chinese source:

```text
# PaperConan / 论文柯南
## What It Is
## See It in Action: A Real Adjudicated Report
## Quick Start (Recommended: Agent + Skill)
### 1. Install the CLI (the Skill Uses It Behind the Scenes)
### 2. Connect the Skill to Your Agent
### 3. Ask in Plain Language
## Reading the Reports: The Essentials
## ⚠️ Important Notice
## Documentation
## Example
## Roadmap
## Why It Exists
## License
## Acknowledgments
```

Translate all prose, bullets, captions, blockquotes, and link labels. Preserve code blocks verbatim. Use natural equivalents including:

```text
论文源数据 sanity check      -> sanity checker for a paper's source data
研究生 / 青椒                -> graduate students and early-career researchers
判定后报告                   -> adjudicated report
红队对抗复核                 -> adversarial red-team review
良性机制 / 良性解释          -> benign mechanisms / benign explanations
人工复核工作台               -> human-review workbench
```

For the Detective Conan-inspired introduction, recreate the playful cadence in natural English without presenting any detector result as a conclusion about intent.

- [ ] **Step 4: Verify structural completeness**

Run:

```bash
python - <<'PY'
from pathlib import Path

paths = [Path("README.md"), Path("README.zh-CN.md")]
for path in paths:
    text = path.read_text()
    headings = [line for line in text.splitlines() if line.startswith("#")]
    fences = sum(line.startswith("```") for line in text.splitlines())
    images = sum(line.startswith("![") for line in text.splitlines())
    print(path, "headings=", len(headings), "fences=", fences, "images=", images)
    assert len(headings) == 16
    assert fences == 12
    assert images == 1
PY
```

Expected: both files report `headings= 16 fences= 12 images= 1` and the command exits with status 0.

- [ ] **Step 5: Verify reciprocal language links and relative file links**

Run:

```bash
python - <<'PY'
from pathlib import Path
import re

expected_switches = {
    Path("README.md"): "[简体中文](README.zh-CN.md)",
    Path("README.zh-CN.md"): "[English](README.md)",
}
for readme, switch in expected_switches.items():
    text = readme.read_text()
    assert switch in "\n".join(text.splitlines()[:5])
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#")):
            continue
        path_part = target.split("#", 1)[0]
        if path_part:
            candidate = readme.parent / path_part
            assert candidate.exists(), f"{readme}: missing {target}"
print("README language switches and relative file links resolve")
PY
```

Expected: `README language switches and relative file links resolve` and exit status 0.

- [ ] **Step 6: Verify neutral-language compliance and Markdown whitespace**

Run:

```bash
rg -n -i 'fraud|fabricat|faked|misconduct|guilty|弊病|编造|造假|欺诈|不端结论|算法标注的可疑模式|定位可疑数值模式' README.md README.zh-CN.md
git diff --check -- README.md README.zh-CN.md
```

Expected: `rg` returns no matches and `git diff --check` returns no errors. If the two commands are run separately, `rg` exits 1 because there are no matches and `git diff --check` exits 0.

- [ ] **Step 7: Review the final diff for translation completeness and scope**

Run:

```bash
git diff -- README.md README.zh-CN.md
git status --short
```

Expected: the diff contains only the English replacement and the new Chinese README; unrelated pre-existing untracked files remain unmodified and unstaged.

- [ ] **Step 8: Commit the bilingual README pair**

```bash
git add README.md README.zh-CN.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: add English and Chinese READMEs"
```

Expected: the staged diff contains only `README.md` and `README.zh-CN.md`, and the commit succeeds.

- [ ] **Step 9: Run post-commit verification**

Run the structural, link, neutral-language, and `git status --short` checks from Steps 4–6 again.

Expected: all assertions pass; prohibited-language search has no matches; the only remaining working-tree entries are unrelated files that predated this task.
