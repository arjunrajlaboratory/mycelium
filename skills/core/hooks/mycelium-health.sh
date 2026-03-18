#!/usr/bin/env bash
# mycelium-health.sh — Claude Code SessionStart hook
# Checks .living/ health and knowledge audit freshness on session start
#
# Install: Add to .claude/settings.local.json under "SessionStart" hooks
# Input: JSON on stdin with {cwd, source, ...}
# Output: Single JSON with additionalContext if issues found

set -euo pipefail

# Read stdin JSON
INPUT=$(cat)

# Initialize message accumulator
MESSAGES=""

# Extract cwd from input
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd', ''))" 2>/dev/null || echo "")
if [ -z "$CWD" ]; then
  CWD=$(pwd)
fi

# Find git repo root
REPO_ROOT=$(cd "$CWD" && git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0  # Not in a git repo
fi

# Always record session-start timestamp for the stop hook
mkdir -p "$REPO_ROOT/.claude"
date +%s > "$REPO_ROOT/.claude/session-start-ts.tmp"

# --- Knowledge audit check (runs regardless of SOURCE) ---
KNOWLEDGE_DIR="$HOME/.claude/knowledge"
if [ ! -d "$KNOWLEDGE_DIR" ]; then
  MESSAGES="${MESSAGES}KNOWLEDGE SYSTEM INIT: ~/.claude/knowledge/ does not exist. Create the directory and initialize domain files per the progressive disclosure spec. Run this silently as a background task.\n\n"
elif [ -f "$KNOWLEDGE_DIR/.last-audit" ]; then
  AUDIT_TS=$(awk '{print $1}' "$KNOWLEDGE_DIR/.last-audit")
  NOW_TS=$(date +%s)
  AUDIT_AGE_DAYS=$(( (NOW_TS - AUDIT_TS) / 86400 ))
  if [ "$AUDIT_AGE_DAYS" -ge 7 ]; then
    MESSAGES="${MESSAGES}KNOWLEDGE AUDIT DUE: Last audit ${AUDIT_AGE_DAYS} days ago. Dispatch a sonnet subagent in the background to run the knowledge audit (read ~/.claude/knowledge/ domain files, check staleness, regenerate INDEX.md files). Do this silently alongside your current task.\n\n"
  fi
else
  echo "$(date +%s) auto-initialized" > "$KNOWLEDGE_DIR/.last-audit"
fi

# Only run .living/ health checks on fresh session starts
SOURCE=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('source', ''))" 2>/dev/null || echo "")
if [ "$SOURCE" != "startup" ]; then
  # Emit any accumulated messages (e.g. knowledge audit) and exit
  if [ -n "$MESSAGES" ]; then
    ESCAPED=$(printf '%s' "$MESSAGES" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
    printf '{"additionalContext": %s}\n' "$ESCAPED"
  fi
  exit 0
fi

LIVING_DIR="$REPO_ROOT/.living"

# Check 1: .living/ directory exists
if [ ! -d "$LIVING_DIR" ]; then
  MESSAGES="${MESSAGES}MYCELIUM WARNING: This repository has no .living/ directory. The post-action hook protocol has nowhere to write learnings and decisions. Run mycelium init to scaffold the living layer, or create .living/ manually with decisions.md, learnings.md, and conventions.md.\n\n"
else
  # Check 2: Required files exist
  MISSING_FILES=()
  for f in decisions.md learnings.md conventions.md; do
    if [ ! -f "$LIVING_DIR/$f" ]; then
      MISSING_FILES+=("$f")
    fi
  done

  if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    MISSING_LIST=$(printf ", %s" "${MISSING_FILES[@]}")
    MISSING_LIST=${MISSING_LIST:2}  # Remove leading ", "
    MESSAGES="${MESSAGES}MYCELIUM WARNING: .living/ is missing required files: ${MISSING_LIST}. Create them before starting work so the post-action protocol can log learnings and decisions.\n\n"
  fi
fi

# --- Emit combined JSON ---
if [ -n "$MESSAGES" ]; then
  ESCAPED=$(printf '%s' "$MESSAGES" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
  printf '{"additionalContext": %s}\n' "$ESCAPED"
fi
exit 0
