#!/usr/bin/env bash
# mycelium-post-action.sh — Claude Code PostToolUse hook (Bash matcher)
# Detects analysis/data/algorithm work and directs Claude to execute
# the mycelium post-action protocol (manifest + .living/ updates).
#
# Debounced: fires once per work cycle. Resets when .living/ is updated.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Bash"
# Input: JSON on stdin with {tool_name, tool_input: {command}, ...}
# Output: JSON with additionalContext directive when triggered

set -euo pipefail

INPUT=$(cat)

# Extract the command that was run
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# --- Detection: is this a significant code execution? ---

is_significant=false

# Python script execution (not one-liners, not package management, not tests)
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])python3?\s+[^-].*\.py'; then
  is_significant=true
fi

# Rscript execution
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])Rscript\s+'; then
  is_significant=true
fi

# Jupyter notebook execution
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])jupyter\s+(nbconvert|execute)'; then
  is_significant=true
fi

# conda run with python script
if echo "$COMMAND" | grep -qE 'conda\s+run\s+.*python.*\.py'; then
  is_significant=true
fi

if [[ "$is_significant" != true ]]; then
  exit 0
fi

# --- Exclusions: filter out non-analysis execution ---

# pytest / unittest
if echo "$COMMAND" | grep -qE '(pytest|python3?\s+-m\s+(pytest|unittest))'; then
  exit 0
fi

# pip / package management
if echo "$COMMAND" | grep -qE '(pip\s+install|pip3\s+install|uv\s+pip|setup\.py)'; then
  exit 0
fi

# python -c one-liners
if echo "$COMMAND" | grep -qE 'python3?\s+-c\s+'; then
  exit 0
fi

# python -m (module execution — pytest/unittest already caught above)
if echo "$COMMAND" | grep -qE 'python3?\s+-m\s+'; then
  exit 0
fi

# linting / formatting
if echo "$COMMAND" | grep -qE '(ruff|black|isort|mypy|pyright|flake8)'; then
  exit 0
fi

# --- Repo and .living/ checks ---

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi

LIVING_DIR="$REPO_ROOT/.living"
if [[ ! -d "$LIVING_DIR" ]]; then
  exit 0
fi

# --- Debounce: only fire once per work cycle ---

REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
mkdir -p "$REPO_ROOT/.claude"

if [[ -f "$REMINDER_FILE" ]]; then
  REMINDER_TS=$(cat "$REMINDER_FILE")

  # Check if .living/ was updated since last reminder (cycle complete)
  LEARNINGS_MTIME=0
  DECISIONS_MTIME=0
  if [[ -f "$LIVING_DIR/learnings.md" ]]; then
    LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  fi
  if [[ -f "$LIVING_DIR/decisions.md" ]]; then
    DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  fi

  LATEST_LIVING=$((LEARNINGS_MTIME > DECISIONS_MTIME ? LEARNINGS_MTIME : DECISIONS_MTIME))

  if [[ "$LATEST_LIVING" -le "$REMINDER_TS" ]]; then
    # Already reminded, .living/ not yet updated — stay silent
    exit 0
  fi
  # .living/ was updated since reminder — reset, allow re-fire
fi

# --- Fire the directive ---

# Record reminder timestamp
date +%s > "$REMINDER_FILE"

# Emit directive as additionalContext — Claude treats this as a system instruction
cat <<'JSON'
{
  "additionalContext": "MYCELIUM POST-ACTION PROTOCOL — MANDATORY: You just executed analysis/data processing/algorithm code. Before continuing with other work, you MUST complete the following steps:\n\n1. Save outputs to the appropriate directory (analysis/[name]/outputs/, data/processed/, or algorithms/[name]/)\n2. Add or update the entry in the relevant manifest (analysis/ANALYSIS_MANIFEST.md, data/DATA_MANIFEST.md, or algorithms/ALGORITHM_MANIFEST.md)\n3. Update the subfolder documentation file (UPPER_SNAKE_CASE.md in the affected directory)\n4. Append to .living/learnings.md if anything unexpected was learned\n5. Append to .living/decisions.md if any non-obvious choices were made\n\nDo this NOW as part of your current response. Do not defer to later."
}
JSON
