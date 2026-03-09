#!/usr/bin/env bash
# mycelium-stop-check.sh — Claude Code Stop hook
# Safety net: blocks session end only if analysis was performed (post-action
# hook fired) but .living/ was never updated. Does NOT block read-only,
# config-only, or non-analysis sessions.
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

# Check if the post-action hook fired during this session.
# If it never fired, no analysis/data/algorithm work happened — nothing to enforce.
REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
if [ ! -f "$REMINDER_FILE" ]; then
  exit 0
fi

# Post-action hook fired. Check if .living/ was updated AFTER the reminder.
REMINDER_TS=$(cat "$REMINDER_FILE")

LEARNINGS_UPDATED=false
DECISIONS_UPDATED=false

if [ -f "$LIVING_DIR/learnings.md" ]; then
  LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  if [ "$LEARNINGS_MTIME" -gt "$REMINDER_TS" ]; then
    LEARNINGS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/decisions.md" ]; then
  DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  if [ "$DECISIONS_MTIME" -gt "$REMINDER_TS" ]; then
    DECISIONS_UPDATED=true
  fi
fi

# If either was updated after the post-action hook fired, protocol was followed
if [ "$LEARNINGS_UPDATED" = true ] || [ "$DECISIONS_UPDATED" = true ]; then
  # Clean up reminder file — cycle complete
  rm -f "$REMINDER_FILE"
  exit 0
fi

# Block: analysis happened but .living/ was never updated
cat <<'JSON'
{
  "decision": "block",
  "reason": "MYCELIUM: The post-action hook detected analysis/data/algorithm work this session, but .living/ was not updated afterward. Before ending:\n\n1. Update the relevant manifest (ANALYSIS_MANIFEST.md, DATA_MANIFEST.md, or ALGORITHM_MANIFEST.md)\n2. Append to .living/learnings.md (insights, gotchas, edge cases)\n3. Append to .living/decisions.md (non-obvious choices made)\n\nThis ensures your work is registered and discoverable."
}
JSON
