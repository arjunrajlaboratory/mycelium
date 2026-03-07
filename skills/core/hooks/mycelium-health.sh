#!/usr/bin/env bash
# mycelium-health.sh — Claude Code SessionStart hook
# Checks .living/ health on session start and records session timestamp
#
# Install: Add to .claude/settings.local.json under "SessionStart" hooks
# Input: JSON on stdin with {cwd, source, ...}
# Output: JSON with additionalContext if issues found

set -euo pipefail

# Read stdin JSON
INPUT=$(cat)

# Extract cwd from input
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd', ''))" 2>/dev/null || echo "")
if [ -z "$CWD" ]; then
  CWD=$(pwd)
fi

# Find git repo root
REPO_ROOT=$(cd "$CWD" && git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0  # Not in a git repo
fi

# Always record session-start timestamp for the stop hook
mkdir -p "$REPO_ROOT/.claude"
date +%s > "$REPO_ROOT/.claude/session-start-ts.tmp"

# Only run health checks on fresh session starts
SOURCE=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('source', ''))" 2>/dev/null || echo "")
if [ "$SOURCE" != "startup" ]; then
  exit 0
fi

LIVING_DIR="$REPO_ROOT/.living"

# Check 1: .living/ directory exists
if [ ! -d "$LIVING_DIR" ]; then
  cat <<JSON
{
  "additionalContext": "MYCELIUM WARNING: This repository has no .living/ directory. The post-action hook protocol has nowhere to write learnings and decisions. Run mycelium init to scaffold the living layer, or create .living/ manually with decisions.md, learnings.md, and conventions.md."
}
JSON
  exit 0
fi

# Check 2: Required files exist
MISSING_FILES=()
for f in decisions.md learnings.md conventions.md; do
  if [ ! -f "$LIVING_DIR/$f" ]; then
    MISSING_FILES+=("$f")
  fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
  MISSING_LIST=$(printf ", %s" "${MISSING_FILES[@]}")
  MISSING_LIST=${MISSING_LIST:2}  # Remove leading ", "
  cat <<JSON
{
  "additionalContext": "MYCELIUM WARNING: .living/ is missing required files: ${MISSING_LIST}. Create them before starting work so the post-action protocol can log learnings and decisions."
}
JSON
  exit 0
fi

# All checks passed — silent success
exit 0
