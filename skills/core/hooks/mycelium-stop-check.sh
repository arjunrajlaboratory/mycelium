#!/usr/bin/env bash
# mycelium-stop-check.sh — Claude Code Stop hook
# Safety net: warns if analysis was performed (post-action hook fired) but
# .living/ was never updated. Also warns if session summary was not written.
# Does NOT block read-only, config-only, or non-analysis sessions.
#
# Install: Add to .claude/settings.local.json under "Stop" hooks
# Input: JSON on stdin with session metadata
# Output: Non-blocking warnings to stdout (exit 0 in all paths)

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

  # Check if session summary was written (non-blocking warning)
  SESSION_FILE="$REPO_ROOT/.claude/last-session.md"
  SESSION_START_FILE="$REPO_ROOT/.claude/session-start-ts.tmp"
  if [ -f "$SESSION_START_FILE" ]; then
    START_MTIME=$(stat -f "%m" "$SESSION_START_FILE" 2>/dev/null || stat -c "%Y" "$SESSION_START_FILE" 2>/dev/null || echo "0")
    SESSION_MTIME=$(stat -f "%m" "$SESSION_FILE" 2>/dev/null || stat -c "%Y" "$SESSION_FILE" 2>/dev/null || echo "0")
    if [ "$SESSION_MTIME" -lt "$START_MTIME" ] || [ ! -f "$SESSION_FILE" ]; then
      echo "Session summary not written. Next session will lack context."
      echo "Dispatch crystallization subagent or write .claude/last-session.md before stopping."
    fi
  fi

  exit 0
fi

# Warn (non-blocking): analysis happened but .living/ was never updated
echo "Mycelium: post-action hook fired but .living/ was not updated."
echo "Consider logging learnings/decisions before ending."
# Clean up reminder file so it doesn't fire again next session
rm -f "$REMINDER_FILE"
exit 0
