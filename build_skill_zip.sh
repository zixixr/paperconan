#!/usr/bin/env bash
# Build paperconan-skill.zip — the downloadable skill bundle attached to GitHub releases.
#
# The bundle is the skill directory (skills/paperconan/) plus the worked example
# (examples/), repacked under a single top-level `paperconan/` folder so it drops
# straight into ~/.claude/skills/paperconan/. The zip itself is gitignored; this
# script is the source of truth for how it's produced.
#
# Usage:  ./build_skill_zip.sh [output-path]
# Then:   gh release upload <tag> paperconan-skill.zip --clobber
set -euo pipefail

OUT="${1:-paperconan-skill.zip}"
OUT_DIR="$(dirname "$OUT")"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
OUT="$OUT_DIR/$(basename "$OUT")"
rm -f "$OUT"

cd "$(dirname "$0")"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/paperconan"
mkdir -p "$ROOT/examples/demo_paper/audit"

# Skill entrypoint + full reference tree
cp -R skills/paperconan/. "$ROOT/"

# Worked example (data generator + preview + the demo audit output users can eyeball)
cp examples/make_demo_data.py examples/report-preview.png examples/README.md "$ROOT/examples/"
cp examples/demo_paper/ED_Fig2_tumor_volume.xlsx examples/demo_paper/ED_Fig4_qPCR.xlsx "$ROOT/examples/demo_paper/"
cp examples/demo_paper/audit/report.html examples/demo_paper/audit/scan.json "$ROOT/examples/demo_paper/audit/"

( cd "$STAGE" && zip -r -X "$OUT" paperconan \
  -x '*.DS_Store' \
     '*/__pycache__/' '*/__pycache__/*' \
     '*/.cache/' '*/.cache/*' \
     '*/.*_cache/' '*/.*_cache/*' \
     '*.py[cod]' )

echo "built $OUT"
unzip -l "$OUT"
