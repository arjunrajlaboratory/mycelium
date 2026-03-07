#!/usr/bin/env bash
# mycelium-stop-check.sh — Claude Code Stop hook
# Blocks session end if significant work was done without updating .living/
#
# Install: Add to .claude/settings.local.json under "Stop" hooks
# Input: JSON on stdin with session metadata
# Output: JSON with {"decision": "block", "reason": "..."} to prevent stop

set -euo pipefail

# Read stdin JSON
INPUT=$(cat)

# Prevent infinite recursion: if stop_hook_active is set, let it through
STOP_HOOK_ACTIVE=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('stop_hook_active', False)).lower())" 2>/dev/null || echo "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

# Find git repo root from cwd
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0  # Not in a git repo, nothing to check
fi

# If no .living/ directory, skip (SessionStart hook handles scaffolding)
LIVING_DIR="$REPO_ROOT/.living"
if [ ! -d "$LIVING_DIR" ]; then
  exit 0
fi

# Check session significance: look for transcript size hint
# Use the session-start timestamp to determine if .living/ was updated
TIMESTAMP_FILE="$REPO_ROOT/.claude/session-start-ts.tmp"
if [ ! -f "$TIMESTAMP_FILE" ]; then
  exit 0  # No timestamp recorded, can't compare
fi

SESSION_START_TS=$(cat "$TIMESTAMP_FILE")

# Count uncommitted changes (git diff HEAD includes both staged and unstaged)
UNCOMMITTED_STAT=$(git diff --stat HEAD 2>/dev/null | tail -1 || echo "")
UNCOMMITTED_FILES=0
if echo "$UNCOMMITTED_STAT" | grep -q "file"; then
  UNCOMMITTED_FILES=$(echo "$UNCOMMITTED_STAT" | grep -oE '[0-9]+ file' | grep -oE '[0-9]+' || echo "0")
fi

# Count committed changes made during this session (catches work that was committed mid-session)
COMMITTED_FILES=0
if [ -n "$SESSION_START_TS" ]; then
  COMMITTED_LOG=$(git log --after="@${SESSION_START_TS}" --pretty=format: --name-only 2>/dev/null | sort -u | sed '/^$/d' || true)
  if [ -n "$COMMITTED_LOG" ]; then
    COMMITTED_FILES=$(echo "$COMMITTED_LOG" | wc -l | tr -d ' ')
  fi
fi

TOTAL_FILES=$((UNCOMMITTED_FILES + COMMITTED_FILES))

# If fewer than 3 files changed, consider it trivial
if [ "$TOTAL_FILES" -lt 3 ]; then
  exit 0
fi

# Check if .living/learnings.md or .living/decisions.md was modified after session start
LEARNINGS_UPDATED=false
DECISIONS_UPDATED=false

if [ -f "$LIVING_DIR/learnings.md" ]; then
  LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  if [ "$LEARNINGS_MTIME" -gt "$SESSION_START_TS" ]; then
    LEARNINGS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/decisions.md" ]; then
  DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  if [ "$DECISIONS_MTIME" -gt "$SESSION_START_TS" ]; then
    DECISIONS_UPDATED=true
  fi
fi

# If either was updated, the protocol was followed
if [ "$LEARNINGS_UPDATED" = true ] || [ "$DECISIONS_UPDATED" = true ]; then
  exit 0
fi

# Block the stop — significant work done but .living/ not updated
cat <<'JSON'
{
  "decision": "block",
  "reason": "MYCELIUM: You did significant work this session but did not update .living/. Before ending, reflect on what was learned and decided:\n\n1. Append new entries to .living/learnings.md (gotchas, surprises, insights)\n2. Append new entries to .living/decisions.md (non-obvious choices made)\n3. Update relevant MANIFEST.md files\n\nIf nothing was learned or decided, update .living/learnings.md with a note explaining why."
}
JSON
