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
NOW_TS=$(date +%s)

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

# Clean up stale sentinels from crashed sessions
# These are per-repo, so safe to clean on fresh session start
if [ -f "$REPO_ROOT/.claude/mycelium-reminded.tmp" ]; then
  # Check if the reminder is from a previous session (older than session-start-ts)
  STALE_TS=$(cat "$REPO_ROOT/.claude/mycelium-reminded.tmp" 2>/dev/null || echo "0")
  NOW_TS=$(date +%s)
  STALE_AGE=$(( NOW_TS - STALE_TS ))
  # If older than 1 hour, it's definitely stale (sessions rarely last >1h)
  if [ "$STALE_AGE" -gt 3600 ]; then
    rm -f "$REPO_ROOT/.claude/mycelium-reminded.tmp"
    rm -f "$REPO_ROOT/.claude/mycelium-session-activity.tmp"
  fi
fi

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

# --- Session log setup (runs every invocation, idempotent) ---
LIVING_DIR="$REPO_ROOT/.living"
LOG_DIR="$LIVING_DIR/log"

if [ -d "$LIVING_DIR" ]; then
  ACTIVE_LOG_FILE="$REPO_ROOT/.claude/active-session-log.tmp"
  # Ensure log directory and registry exist
  mkdir -p "$LOG_DIR"
  mkdir -p "$LIVING_DIR/findings"
  if [ ! -f "$LOG_DIR/LOG_REGISTRY.md" ]; then
    cat > "$LOG_DIR/LOG_REGISTRY.md" << 'REGISTRY_EOF'
# Session Log Registry

| Date | Session ID | Project | Branch | Duration | Files Changed | Summary | Key Outputs | Status | Tags | Log |
|------|-----------|---------|--------|----------|---------------|---------|-------------|--------|------|-----|
REGISTRY_EOF
  fi

  # Check for incomplete log from interrupted previous session
  if [ -f "$ACTIVE_LOG_FILE" ]; then
    STALE_LOG=$(cat "$ACTIVE_LOG_FILE")
    if [ -f "$STALE_LOG" ] && ! grep -q "## Session Summary" "$STALE_LOG"; then
      MESSAGES="${MESSAGES}INCOMPLETE SESSION LOG: Previous session log at ${STALE_LOG} was never finalized. Please add a '## Session Summary' section and append a row to the registry before starting new work.\n\n"
    fi
  fi

  # Create new log file only if no active session log exists (fresh process start)
  if [ ! -f "$ACTIVE_LOG_FILE" ]; then
    TODAY=$(date +%Y-%m-%d)
    # Determine session counter for today
    EXISTING_COUNT=0
    for _f in "$LOG_DIR"/${TODAY}-*.md; do
      [ -f "$_f" ] && [ "$(basename "$_f")" != "LOG_REGISTRY.md" ] && EXISTING_COUNT=$((EXISTING_COUNT + 1))
    done
    SESSION_NUM=$(printf "%03d" $((EXISTING_COUNT + 1)))

    # Derive slug from project directory name
    PROJECT_NAME=$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
    SESSION_ID="${TODAY}-${SESSION_NUM}"
    LOG_FILENAME="${SESSION_ID}-${PROJECT_NAME}.md"
    LOG_PATH="$LOG_DIR/$LOG_FILENAME"

    # Detect project and branch
    BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    STARTED=$(date +%Y-%m-%dT%H:%M:%S%z)
    TIME_SHORT=$(date +%H:%M)

    # Find previous session log for this project (glob-safe, no pipefail risk)
    PREV_LOG=""
    for _pf in "$LOG_DIR"/*-${PROJECT_NAME}.md; do
      [ -f "$_pf" ] && PREV_LOG="$_pf"
    done
    if [ -n "$PREV_LOG" ]; then
      PREV_LINK="$(basename "$PREV_LOG")"
    else
      PREV_LINK="(first session)"
    fi

    # Write log file with frontmatter
    cat > "$LOG_PATH" << LOG_EOF
---
session_id: ${SESSION_ID}
project: ${PROJECT_NAME}
branch: ${BRANCH}
started: ${STARTED}
ended:
duration_minutes:
files_changed:
---

## Session Log

### ${TIME_SHORT} — Session started
- Branch: \`${BRANCH}\`
- Resuming from: ${PREV_LINK}
LOG_EOF

    # Store active log path globally
    echo "$LOG_PATH" > "$ACTIVE_LOG_FILE"
  fi
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

# --- Session resume: load last-session.md if recent ---
SESSION_FILE="$REPO_ROOT/.claude/last-session.md"
if [ -f "$SESSION_FILE" ]; then
  SESSION_MTIME=$(stat -f "%m" "$SESSION_FILE" 2>/dev/null || stat -c "%Y" "$SESSION_FILE" 2>/dev/null || echo "0")
  NOW_TS=$(date +%s)
  SESSION_AGE_DAYS=$(( (NOW_TS - SESSION_MTIME) / 86400 ))
  if [ "$SESSION_AGE_DAYS" -lt 7 ]; then
    SESSION_CONTENT=$(cat "$SESSION_FILE")
    if [ -n "$SESSION_CONTENT" ]; then
      # Show resume to user immediately via stderr
      echo "$SESSION_CONTENT" >&2
      echo "---" >&2
      # Add to agent context via MESSAGES accumulator
      MESSAGES="${MESSAGES}${SESSION_CONTENT}\n\n"
    fi
  fi
fi

# --- Load recent session log context (project-filtered) ---
SESSION_LOG_DIR="$REPO_ROOT/.living/log"
if [ -d "$SESSION_LOG_DIR" ] && [ -f "$SESSION_LOG_DIR/LOG_REGISTRY.md" ]; then
  PROJECT_SLUG=$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
  RECENT_ROWS=$({ grep "| $PROJECT_SLUG " "$SESSION_LOG_DIR/LOG_REGISTRY.md" || true; } 2>/dev/null | tail -5)
  if [ -n "$RECENT_ROWS" ]; then
    HEADER="| Date | Session ID | Project | Branch | Duration | Files Changed | Summary | Key Outputs | Status | Tags | Log |"
    SEPARATOR="|------|-----------|---------|--------|----------|---------------|---------|-------------|--------|------|-----|"
    LOG_CONTEXT="RECENT SESSION LOG (${PROJECT_SLUG}):\n${HEADER}\n${SEPARATOR}\n${RECENT_ROWS}\n\nFull logs: .living/log/"
    MESSAGES="${MESSAGES}${LOG_CONTEXT}\n\n"
  fi
fi

# --- Load findings INDEX.md if meta-project exists ---
if [ -d "$LIVING_DIR/findings" ]; then
  # Walk up to find meta-project (parent directory with .living/)
  META_ROOT=""
  CHECK_DIR=$(dirname "$REPO_ROOT")
  while [ "$CHECK_DIR" != "/" ] && [ "$CHECK_DIR" != "." ]; do
    if [ -d "$CHECK_DIR/.living" ]; then
      META_ROOT="$CHECK_DIR"
      break
    fi
    CHECK_DIR=$(dirname "$CHECK_DIR")
  done

  # Load cross-project findings index if it exists
  if [ -n "$META_ROOT" ] && [ -f "$META_ROOT/.living/findings/INDEX.md" ]; then
    FINDINGS_INDEX=$(cat "$META_ROOT/.living/findings/INDEX.md")
    MESSAGES="${MESSAGES}${FINDINGS_INDEX}\n\n"
  fi

  # Mention per-project FINDINGS_REGISTRY.md if it exists
  FINDINGS_REGISTRY="$LIVING_DIR/findings/FINDINGS_REGISTRY.md"
  if [ -f "$FINDINGS_REGISTRY" ]; then
    # Count topic files (excluding INDEX.md and FINDINGS_REGISTRY.md)
    TOPIC_COUNT=0
    for _tf in "$LIVING_DIR/findings/"*.md; do
      _bn=$(basename "$_tf")
      if [ "$_bn" != "INDEX.md" ] && [ "$_bn" != "FINDINGS_REGISTRY.md" ] && [ -f "$_tf" ]; then
        TOPIC_COUNT=$((TOPIC_COUNT + 1))
      fi
    done
    REGISTRY_ROWS=$(grep -c "^| F-" "$FINDINGS_REGISTRY" 2>/dev/null || echo "0")
    MESSAGES="${MESSAGES}FINDINGS REGISTRY: .living/findings/FINDINGS_REGISTRY.md exists (${REGISTRY_ROWS} findings across ${TOPIC_COUNT} topics). Read it for a quick scan of all findings in this project.\n\n"
  fi
fi

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

# --- Content summary (always emit when .living/ exists) ---
if [ -d "$LIVING_DIR" ]; then
  # Count entries in each file
  LEARNINGS_COUNT=0
  DECISIONS_COUNT=0
  CONVENTIONS_COUNT=0
  [ -f "$LIVING_DIR/learnings.md" ]   && LEARNINGS_COUNT=$(grep -c '^### ' "$LIVING_DIR/learnings.md" 2>/dev/null || echo 0)
  [ -f "$LIVING_DIR/decisions.md" ]   && DECISIONS_COUNT=$(grep -c '^### ' "$LIVING_DIR/decisions.md" 2>/dev/null || echo 0)
  [ -f "$LIVING_DIR/conventions.md" ] && CONVENTIONS_COUNT=$(grep -c '^## ' "$LIVING_DIR/conventions.md" 2>/dev/null || echo 0)

  # Count session logs (exclude registry files)
  SESSION_LOG_COUNT=0
  if [ -d "$LOG_DIR" ]; then
    for _lf in "$LOG_DIR"/*.md; do
      _bn=$(basename "$_lf" 2>/dev/null || true)
      [ -f "$_lf" ] && [ "$_bn" != "LOG_REGISTRY.md" ] && [ "$_bn" != "REGISTRY.md" ] && SESSION_LOG_COUNT=$((SESSION_LOG_COUNT + 1))
    done
  fi

  # Count findings topics (exclude INDEX.md and FINDINGS_REGISTRY.md)
  FINDINGS_COUNT=0
  if [ -d "$LIVING_DIR/findings" ]; then
    for _ff in "$LIVING_DIR/findings"/*.md; do
      _ffbn=$(basename "$_ff")
      [ -f "$_ff" ] && [ "$_ffbn" != "INDEX.md" ] && [ "$_ffbn" != "FINDINGS_REGISTRY.md" ] && FINDINGS_COUNT=$((FINDINGS_COUNT + 1))
    done
  fi

  # Extract a brief highlight from the most recent session log
  LAST_SESSION_DATE=""
  LAST_SESSION_SNIPPET=""
  if [ -d "$LOG_DIR" ] && [ "$SESSION_LOG_COUNT" -gt 0 ]; then
    # Find the most recently modified session log
    MOST_RECENT_LOG=""
    for _lf in "$LOG_DIR"/*.md; do
      _bn=$(basename "$_lf" 2>/dev/null || true)
      [ -f "$_lf" ] && [ "$_bn" != "LOG_REGISTRY.md" ] && [ "$_bn" != "REGISTRY.md" ] && MOST_RECENT_LOG="$_lf"
    done
    if [ -n "$MOST_RECENT_LOG" ]; then
      LAST_SESSION_DATE=$(basename "$MOST_RECENT_LOG" | cut -d'-' -f1-3)
      # Extract first timestamped entry content (bullet lines after the first ### HH:MM header)
      LAST_SESSION_SNIPPET=$(awk '/^### [0-9][0-9]:[0-9][0-9]/{found=1; next} found && /^-/{print; count++; if(count>=2) exit} found && /^###/{exit}' "$MOST_RECENT_LOG" 2>/dev/null | head -2 | sed 's/^- //' | tr '\n' ' ' | sed 's/  */ /g;s/ $//')
    fi
  fi

  # Build summary line
  SUMMARY_LINE="MYCELIUM SUMMARY: ${LEARNINGS_COUNT} learnings, ${DECISIONS_COUNT} decisions, ${CONVENTIONS_COUNT} conventions, ${SESSION_LOG_COUNT} session logs"
  [ "$FINDINGS_COUNT" -gt 0 ] && SUMMARY_LINE="${SUMMARY_LINE}, ${FINDINGS_COUNT} findings"
  SUMMARY_LINE="${SUMMARY_LINE}."
  if [ -n "$LAST_SESSION_DATE" ] && [ -n "$LAST_SESSION_SNIPPET" ]; then
    SUMMARY_LINE="${SUMMARY_LINE} Last session (${LAST_SESSION_DATE}): ${LAST_SESSION_SNIPPET}"
  elif [ -n "$LAST_SESSION_DATE" ]; then
    SUMMARY_LINE="${SUMMARY_LINE} Last session: ${LAST_SESSION_DATE}."
  fi

  MESSAGES="${MESSAGES}${SUMMARY_LINE}\n\n"
fi

# --- Emit combined JSON ---
if [ -n "$MESSAGES" ]; then
  ESCAPED=$(printf '%s' "$MESSAGES" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
  printf '{"additionalContext": %s}\n' "$ESCAPED"
fi
exit 0
