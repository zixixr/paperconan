# Bilingual README Design

**Date:** 2026-08-12

## Goal

Make the repository landing page accessible to international visitors without
removing the existing Simplified Chinese documentation. The English README will
preserve the full scope and technical detail of the current Chinese README while
using idiomatic English rather than sentence-by-sentence literal translation.

## File layout

- `README.md` becomes the default English README rendered on the repository page.
- The current Chinese README moves to `README.zh-CN.md`.
- Both files begin with an `English | 简体中文` language switch using relative
  links to the two README files.

## Translation approach

Use section-faithful localization:

- Preserve every existing section, example, command, image, factual statement,
  warning, roadmap item, acknowledgment, and documentation link.
- Rewrite sentences and transitions where needed so the English reads naturally
  to a native speaker.
- Localize culture-specific shorthand. For example, `青椒` becomes
  `early-career researchers` rather than a literal translation.
- Keep commands, paths, option names, identifiers, detector names, and schema
  values unchanged.
- Translate link labels and same-page anchors where appropriate while preserving
  their destinations.

## Neutral-language compliance

Both language versions must present detector output as a statistical signal or
data inconsistency that requires explanation and human review, never as a
judgment about intent or responsibility.

Because moving the Chinese README creates a newly written file, minimally revise
existing non-neutral phrases in that version as part of the move. Use terms such
as `统计信号`, `数据不一致`, `待解释异常`, and `请作者澄清`. In English, prefer
`statistical signal`, `data inconsistency`, `unexplained anomaly`, `human review`,
and `author clarification`.

The publicly documented example remains in both versions, but the description
must clearly distinguish reproducible numeric signals from external institutional
actions and from any conclusion about intent.

## Verification

After editing:

1. Confirm both README files exist and their language-switch links resolve.
2. Compare headings, fenced code blocks, images, and relative documentation links
   to ensure the English version is complete.
3. Check all relative file links in both READMEs against the working tree.
4. Search both files for language prohibited by the repository's neutral-language
   rule and revise any matches.
5. Inspect the final Git diff to ensure unrelated working-tree files are untouched.

## Out of scope

- Translating the documents under `docs/` or `skills/`.
- Adding automatic locale detection, which GitHub does not provide for repository
  READMEs.
- Changing CLI behavior, detector behavior, examples, or report output.
