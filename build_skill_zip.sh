#!/usr/bin/env bash
# Build paperconan-skill.zip — the downloadable skill bundle attached to GitHub releases.
#
# The bundle is the skill directory (skills/paperconan/) plus the worked example
# (examples/), repacked under a single top-level `paperconan/` folder so it drops
# straight into ~/.claude/skills/paperconan/. The zip itself is gitignored; this
# script is the source of truth for how it's produced.
#
# Usage:  ./build_skill_zip.sh
# Then:   gh release upload <tag> paperconan-skill.zip --clobber
set -euo pipefail
cd "$(dirname "$0")"

OUT="paperconan-skill.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/paperconan"
mkdir -p "$ROOT/references" "$ROOT/examples/demo_paper/audit"

# Skill entrypoint + references
cp skills/paperconan/SKILL.md "$ROOT/"
cp \
  skills/paperconan/references/detectors.md \
  skills/paperconan/references/interpretation.md \
  skills/paperconan/references/output-schema.md \
  skills/paperconan/references/judgment-rubric.md \
  "$ROOT/references/"

# Worked example (data generator + preview + the demo audit output users can eyeball)
cp examples/make_demo_data.py examples/report-preview.png examples/README.md "$ROOT/examples/"
cp examples/demo_paper/ED_Fig2_tumor_volume.xlsx examples/demo_paper/ED_Fig4_qPCR.xlsx "$ROOT/examples/demo_paper/"
cp examples/demo_paper/audit/report.html examples/demo_paper/audit/scan.json "$ROOT/examples/demo_paper/audit/"

rm -f "$OUT"
( cd "$STAGE" && zip -r -X - paperconan -x '*.DS_Store' ) > "$OUT"

echo "built $OUT"
unzip -l "$OUT"
