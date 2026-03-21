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

# --- Build combined directive ---

ACTIVE_LOG_FILE="$HOME/.claude/active-session-log.tmp"
LOG_DIRECTIVE=""
LIVING_DIRECTIVE=""

# Part 1: Log append (always fires, no debounce)
if [ -f "$ACTIVE_LOG_FILE" ]; then
  LOG_PATH=$(cat "$ACTIVE_LOG_FILE")
  LOG_DIRECTIVE="SESSION LOG UPDATE: Append a 2-3 line timestamped entry to ${LOG_PATH} describing what you just did, the result, and any notable outputs. Format: ### HH:MM — <action title> followed by bullet points with Command, Result, and Output fields as applicable."
fi

# Part 2: .living/ update reminder (debounced — existing behavior)
REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
mkdir -p "$REPO_ROOT/.claude"

SHOULD_REMIND=true
if [[ -f "$REMINDER_FILE" ]]; then
  REMINDER_TS=$(cat "$REMINDER_FILE")
  LEARNINGS_MTIME=0
  DECISIONS_MTIME=0
  if [[ -f "$LIVING_DIR/learnings.md" ]]; then
    LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  fi
  if [[ -f "$LIVING_DIR/decisions.md" ]]; then
    DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  fi
  LATEST_LIVING=$((LEARNINGS_MTIME > DECISIONS_MTIME ? LEARNINGS_MTIME : DECISIONS_MTIME))
  # Also check findings directory mtime (updated when any finding file is written)
  FINDINGS_DIR="$LIVING_DIR/findings"
  if [ -d "$FINDINGS_DIR" ]; then
    FINDINGS_MTIME=$(stat -f "%m" "$FINDINGS_DIR" 2>/dev/null || stat -c "%Y" "$FINDINGS_DIR" 2>/dev/null || echo "0")
    if [ "$FINDINGS_MTIME" -gt "$LATEST_LIVING" ]; then
      LATEST_LIVING="$FINDINGS_MTIME"
    fi
  fi
  if [[ "$LATEST_LIVING" -le "$REMINDER_TS" ]]; then
    SHOULD_REMIND=false
  fi
fi

if [[ "$SHOULD_REMIND" == true ]]; then
  date +%s > "$REMINDER_FILE"
  LIVING_DIRECTIVE="MYCELIUM POST-ACTION PROTOCOL — MANDATORY: You just executed analysis/data processing/algorithm code. Before continuing with other work, you MUST complete the following steps:\n\n1. Save outputs to the appropriate directory (analysis/[name]/outputs/, data/processed/, or algorithms/[name]/)\n2. Add or update the entry in the relevant manifest (analysis/ANALYSIS_MANIFEST.md, data/DATA_MANIFEST.md, or algorithms/ALGORITHM_MANIFEST.md)\n3. Update the subfolder documentation file (UPPER_SNAKE_CASE.md in the affected directory)\n4. Append to .living/learnings.md if anything unexpected was learned\n5. Append to .living/decisions.md if any non-obvious choices were made\n\nDo this NOW as part of your current response. Do not defer to later.\n\nFINDINGS CHECK: If this work produced a scientific finding (an empirical observation, a validated/invalidated hypothesis, a quantitative result, or a methodological discovery about the domain — NOT a tooling/process insight), crystallize it to .living/findings/{topic}.md. First check if a meta-project findings INDEX.md exists by walking up from the repo root looking for a parent directory with .living/ — if found, read {meta-project}/.living/findings/INDEX.md for existing topics. Route to an existing topic file or create a new one. When choosing a topic name, prefer broad scientific questions over project-specific or method-specific names — name it so a researcher in a different system would recognize it as relevant. Use the template format from skills/core/templates/findings-topic.md and skills/core/templates/findings-entry.md. After writing each finding, upsert a row in .living/findings/FINDINGS_REGISTRY.md (create from skills/core/templates/findings-registry.md if it does not exist yet) — match on finding ID (F-NNN) and update or append.\n\nRouting rule:\n- How the tool/pipeline/code works → .living/learnings.md\n- What the data/analysis revealed about the domain → .living/findings/{topic}.md\n- A design choice about implementation → .living/decisions.md"
fi

# Assemble and emit single JSON
if [ -n "$LOG_DIRECTIVE" ] && [ -n "$LIVING_DIRECTIVE" ]; then
  COMBINED="${LOG_DIRECTIVE}\n\n---\n\n${LIVING_DIRECTIVE}"
elif [ -n "$LOG_DIRECTIVE" ]; then
  COMBINED="$LOG_DIRECTIVE"
elif [ -n "$LIVING_DIRECTIVE" ]; then
  COMBINED="$LIVING_DIRECTIVE"
else
  exit 0
fi

ESCAPED=$(printf '%s' "$COMBINED" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
printf '{"additionalContext": %s}\n' "$ESCAPED"
