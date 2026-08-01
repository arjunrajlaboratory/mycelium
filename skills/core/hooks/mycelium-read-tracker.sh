#!/usr/bin/env bash
# mycelium-read-tracker.sh — Claude Code PostToolUse hook (Read matcher)
# Tracks when an exposed read tool reads .living/ files to measure access rates
# over time. Appends one line per access to .mycelium/mycelium-read-access.log.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Read"
# Input: JSON on stdin with {tool_name, tool_input: {file_path, ...}, ...}
# Output: Silent (no additionalContext, no JSON)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

# Wrap everything in a guard — if anything fails, exit 0 silently
{
  INPUT=$(cat)

  # Extract the file path from tool input
  FILE_PATH=$(printf '%s' "$INPUT" | mycelium_json_get 'tool_input.file_path')
  if [[ -z "$FILE_PATH" ]]; then
    exit 0
  fi

  # Only care about .living/ reads
  if [[ "$FILE_PATH" != *"/.living/"* ]]; then
    exit 0
  fi

  # Find repo root — must be a git repo with .living/ present
  SESSION_CWD=$(printf '%s' "$INPUT" | mycelium_json_get 'cwd')
  [[ -z "$SESSION_CWD" ]] && SESSION_CWD=$(pwd)
  REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]]; then
    exit 0
  fi
  mycelium_prepare_state_dir "$REPO_ROOT" || exit 0

  # Extract the relative .living/... portion of the path
  # e.g. /Users/mst36/repo/.living/INDEX.md → .living/INDEX.md
  RELATIVE_PATH="${FILE_PATH#*/.living/}"
  RELATIVE_PATH=".living/${RELATIVE_PATH}"

  # ISO 8601 timestamp (seconds precision, local time with offset)
  TIMESTAMP=$(date +"%Y-%m-%dT%H:%M:%S")

  # Ensure the provider-neutral state directory and log file exist
  mkdir -p "$STATE_DIR"
  LOG_FILE="$STATE_DIR/mycelium-read-access.log"

  # Append: TIMESTAMP RELATIVE_PATH
  printf '%s %s\n' "$TIMESTAMP" "$RELATIVE_PATH" >> "$LOG_FILE"

} 2>/dev/null || true

exit 0
