#!/usr/bin/env bash
# mycelium-activity-tracker.sh — Claude Code PostToolUse hook (Edit|Write matcher)
# Tracks file modifications so the stop hook enforces .living/ updates for ALL
# sessions with meaningful work, not just analysis execution.
#
# The existing mycelium-post-action.sh only fires on Bash commands matching
# Python/R/Jupyter patterns. This hook closes the gap for Edit/Write operations.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Edit|Write"
# Input: JSON on stdin with {tool_name, tool_input: {file_path, ...}, ...}
# Output: Silent (no additionalContext) — enforcement happens at Stop hook

set -euo pipefail

INPUT=$(cat)

# Extract the file path from tool input
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# --- Skip non-meaningful files ---

# Skip .living/ files (avoid circular enforcement)
if [[ "$FILE_PATH" == *"/.living/"* ]]; then
  exit 0
fi

# Skip .claude/ temp/config files
if [[ "$FILE_PATH" == *"/.claude/"* ]]; then
  exit 0
fi

# Skip lock files, caches, node_modules, __pycache__
if [[ "$FILE_PATH" == *"/node_modules/"* ]] || \
   [[ "$FILE_PATH" == *"/__pycache__/"* ]] || \
   [[ "$FILE_PATH" == *".pyc" ]] || \
   [[ "$FILE_PATH" == *".lock" ]]; then
  exit 0
fi

# --- Repo and .living/ checks ---

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi

# Only enforce in mycelium-enabled repos
if [[ ! -d "$REPO_ROOT/.living" ]]; then
  exit 0
fi

# --- Accumulate modified files for session summary ---

mkdir -p "$REPO_ROOT/.claude"
ACTIVITY_FILE="$REPO_ROOT/.claude/mycelium-session-activity.tmp"
if ! grep -qxF "$FILE_PATH" "$ACTIVITY_FILE" 2>/dev/null; then
  echo "$FILE_PATH" >> "$ACTIVITY_FILE"
fi

# --- Create reminder file if it doesn't exist (triggers stop hook) ---
# Only on first activity — don't overwrite timestamp from post-action hook

REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
if [[ ! -f "$REMINDER_FILE" ]]; then
  date +%s > "$REMINDER_FILE"
fi

exit 0
