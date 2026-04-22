#!/usr/bin/env bash
# mycelium-bash-access.sh — Claude Code PostToolUse hook (Bash matcher)
# Tracks when Claude accesses .living/ files via Bash commands (printf >>, cat,
# grep, etc.) — the ~65% of accesses invisible to the Read-tool hook.
# Appends one line per unique path to .claude/mycelium-bash-access.log.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Bash"
# Input: JSON on stdin with {tool_name, tool_input: {command, ...}, ...}
# Output: Silent (no additionalContext, no JSON emitted)

set -euo pipefail

{
  INPUT=$(cat)

  # Extract the bash command string
  COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
  if [[ -z "$COMMAND" ]]; then
    exit 0
  fi

  # Early-exit if the command doesn't touch .living/ at all
  if [[ "$COMMAND" != *".living/"* ]]; then
    exit 0
  fi

  # Find repo root — must be a git repo
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]]; then
    exit 0
  fi

  # Must have a .living/ directory in the repo root
  if [[ ! -d "$REPO_ROOT/.living" ]]; then
    exit 0
  fi

  # Determine operation from command syntax
  if [[ "$COMMAND" == *">>"* ]]; then
    OP="append"
  elif [[ "$COMMAND" == *">"* ]]; then
    OP="write"
  else
    OP="read"
  fi

  # Extract unique .living/<path> tokens from the command
  PATHS=$(printf '%s' "$COMMAND" | grep -oE '\.living/[A-Za-z0-9_./-]+' | sort -u)
  if [[ -z "$PATHS" ]]; then
    exit 0
  fi

  # ISO 8601 timestamp (seconds precision, local time)
  TIMESTAMP=$(date +"%Y-%m-%dT%H:%M:%S")

  # Ensure the .claude/ directory and log file exist
  mkdir -p "$REPO_ROOT/.claude"
  LOG_FILE="$REPO_ROOT/.claude/mycelium-bash-access.log"

  # Append one line per unique path: TIMESTAMP OP PATH
  while IFS= read -r PATH_TOKEN; do
    printf '%s %s %s\n' "$TIMESTAMP" "$OP" "$PATH_TOKEN" >> "$LOG_FILE"
  done <<< "$PATHS"

} 2>/dev/null || true

exit 0
