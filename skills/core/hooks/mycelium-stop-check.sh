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

# --- Session log finalization ---
ACTIVE_LOG_FILE="$HOME/.claude/active-session-log.tmp"
if [ -f "$ACTIVE_LOG_FILE" ]; then
  LOG_PATH=$(cat "$ACTIVE_LOG_FILE")

  if [ -f "$LOG_PATH" ]; then
    # Compute session duration
    LOG_REPO=$(dirname "$(dirname "$(dirname "$LOG_PATH")")")  # .living/log/file -> repo root
    START_FILE="$LOG_REPO/.claude/session-start-ts.tmp"
    NOW_TS=$(date +%s)
    DURATION_MIN=0
    if [ -f "$START_FILE" ]; then
      START_TS=$(cat "$START_FILE")
      DURATION_MIN=$(( (NOW_TS - START_TS) / 60 ))
    fi

    # Compute files changed since session start (committed + uncommitted + staged)
    FILES_CHANGED=0
    if [ -f "$START_FILE" ]; then
      FILES_CHANGED_UNCOMMITTED=$(git -C "$LOG_REPO" diff --name-only 2>/dev/null | wc -l | tr -d ' ')
      FILES_CHANGED_STAGED=$(git -C "$LOG_REPO" diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
      START_TS=$(cat "$START_FILE")
      FILES_CHANGED_COMMITTED=$(git -C "$LOG_REPO" log --since="@${START_TS}" --name-only --pretty=format: 2>/dev/null | sort -u | { grep -v '^$' || true; } | wc -l | tr -d ' ')
      FILES_CHANGED=$((FILES_CHANGED_UNCOMMITTED + FILES_CHANGED_STAGED + FILES_CHANGED_COMMITTED))
    fi

    # Short session check: skip finalization if < 5min and 0 files changed
    if [ "$DURATION_MIN" -lt 5 ] && [ "$FILES_CHANGED" -eq 0 ]; then
      rm -f "$LOG_PATH"
      rm -f "$ACTIVE_LOG_FILE"
      # No registry row, no finalization — clean exit
    else
      # Inject finalization directive
      LOG_DIR=$(dirname "$LOG_PATH")
      echo "SESSION LOG FINALIZATION — MANDATORY: Before stopping, you MUST complete these steps:"
      echo ""
      echo "1. Update the frontmatter in ${LOG_PATH}:"
      echo "   - ended: $(date +%Y-%m-%dT%H:%M:%S%z)"
      echo "   - duration_minutes: ${DURATION_MIN}"
      echo "   - files_changed: ${FILES_CHANGED}"
      echo "2. Write a '## Session Summary' section at the end of ${LOG_PATH} with:"
      echo "   - **Completed**: what was accomplished"
      echo "   - **Blocked**: anything unresolved"
      echo "   - **Files changed**: ${FILES_CHANGED}"
      echo "   - **Key outputs**: notable artifacts produced"
      echo "3. Append a row to ${LOG_DIR}/LOG_REGISTRY.md with all fields filled."
      echo "   IMPORTANT: Use the 'project' value from the log frontmatter as the Project column (the slug, not a human-friendly name)."
      echo ""
      echo "Do this NOW before the session ends."
      # Clean up sentinel AFTER directive is emitted
      rm -f "$ACTIVE_LOG_FILE"
    fi
  else
    # Log file doesn't exist (was deleted?) — clean up sentinel
    rm -f "$ACTIVE_LOG_FILE"
  fi
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
