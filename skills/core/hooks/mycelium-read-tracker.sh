#!/usr/bin/env bash
# mycelium-read-tracker.sh — Claude Code PostToolUse hook (Read matcher)
# Tracks when Claude reads .living/ files to measure knowledge access rates
# over time. Appends one line per access to .claude/mycelium-read-access.log.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Read"
# Input: JSON on stdin with {tool_name, tool_input: {file_path, ...}, ...}
# Output: Silent (no additionalContext, no JSON)

set -euo pipefail

# Wrap everything in a guard — if anything fails, exit 0 silently
{
  INPUT=$(cat)

  # Extract the file path from tool input
  FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
  if [[ -z "$FILE_PATH" ]]; then
    exit 0
  fi

  # Only care about .living/ reads
  if [[ "$FILE_PATH" != *"/.living/"* ]]; then
    exit 0
  fi

  # Find repo root — must be a git repo with .living/ present
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]]; then
    exit 0
  fi

  # Extract the relative .living/... portion of the path
  # e.g. /Users/mst36/repo/.living/INDEX.md → .living/INDEX.md
  RELATIVE_PATH="${FILE_PATH#*/.living/}"
  RELATIVE_PATH=".living/${RELATIVE_PATH}"

  # ISO 8601 timestamp (seconds precision, local time with offset)
  TIMESTAMP=$(date +"%Y-%m-%dT%H:%M:%S")

  # Ensure the .claude/ directory and log file exist
  mkdir -p "$REPO_ROOT/.claude"
  LOG_FILE="$REPO_ROOT/.claude/mycelium-read-access.log"

  # Append: TIMESTAMP RELATIVE_PATH
  printf '%s %s\n' "$TIMESTAMP" "$RELATIVE_PATH" >> "$LOG_FILE"

} 2>/dev/null || true

exit 0
