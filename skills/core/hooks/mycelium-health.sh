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
SYSTEM_MESSAGE=""
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

mkdir -p "$REPO_ROOT/.claude"

# Clean up stale sentinels from a crashed previous session BEFORE the
# session-start-ts guard below — otherwise the guard mistakes the orphaned
# active-session-log.tmp for an in-progress session, skips refreshing the
# start ts, and the next stop hook computes duration_minutes from the
# crashed session's timestamp (e.g. 10 days = 14794 min).
#
# Sessions can legitimately run for many hours or days (long analyses,
# overnight jobs), so we cannot rely on owner_ts age alone to declare a
# session dead. The activity tracker touches mycelium-session-activity.tmp
# on every Edit/Write and the post-action hook touches
# mycelium-reminded.tmp on every Bash invocation, so a fresh mtime on
# either is a strong liveness signal. We only clean when owner_ts is old
# AND those signals are also quiet.
ACTIVE_LOG_FILE="$REPO_ROOT/.claude/active-session-log.tmp"
if [ -f "$ACTIVE_LOG_FILE" ]; then
  _STALE_LOG=$(head -1 "$ACTIVE_LOG_FILE" 2>/dev/null || echo "")
  _STALE_OWNER_TS=$(sed -n '2p' "$ACTIVE_LOG_FILE" 2>/dev/null || echo "")
  _SHOULD_CLEAN=false
  # Definitive signals — clean regardless of activity:
  if [ -z "$_STALE_OWNER_TS" ]; then
    # Old format (no owner TS) — clean up for format upgrade
    _SHOULD_CLEAN=true
  elif [ -n "$_STALE_LOG" ] && [ -f "$_STALE_LOG" ] && grep -q "^ended: [0-9]" "$_STALE_LOG"; then
    # Log already finalized but sentinel wasn't cleaned
    _SHOULD_CLEAN=true
  elif [ -n "$_STALE_LOG" ] && [ ! -f "$_STALE_LOG" ]; then
    # Log file deleted but sentinel remains
    _SHOULD_CLEAN=true
  elif [ "$(( $(date +%s) - _STALE_OWNER_TS ))" -gt 7200 ]; then
    # owner_ts > 2h: only conclude "crashed" if activity signals are also
    # quiet. If either is fresh, the session is alive — don't touch.
    _NOW=$(date +%s)
    _ACTIVITY_FILE="$REPO_ROOT/.claude/mycelium-session-activity.tmp"
    _REMINDED_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
    _ACT_AGE=999999999
    _REM_AGE=999999999
    if [ -f "$_ACTIVITY_FILE" ]; then
      _ACT_MTIME=$(stat -f "%m" "$_ACTIVITY_FILE" 2>/dev/null \
                   || stat -c "%Y" "$_ACTIVITY_FILE" 2>/dev/null \
                   || echo 0)
      _ACT_AGE=$(( _NOW - _ACT_MTIME ))
    fi
    if [ -f "$_REMINDED_FILE" ]; then
      _REM_MTIME=$(stat -f "%m" "$_REMINDED_FILE" 2>/dev/null \
                   || stat -c "%Y" "$_REMINDED_FILE" 2>/dev/null \
                   || echo 0)
      _REM_AGE=$(( _NOW - _REM_MTIME ))
    fi
    # Clean only if BOTH activity signals are also old (> 2h). If either is
    # fresh, assume the session is still alive.
    if [ "$_ACT_AGE" -gt 7200 ] && [ "$_REM_AGE" -gt 7200 ]; then
      _SHOULD_CLEAN=true
    fi
  fi
  if [ "$_SHOULD_CLEAN" = true ]; then
    # If the orphaned log was never finalized, surface a warning so the new
    # session knows about it. Drop the active-session-log + session-start-ts
    # sentinels so this process starts clean. Deliberately do NOT touch
    # mycelium-session-activity.tmp / mycelium-reminded.tmp here — those
    # have their own dedicated staleness cleanup further down, and wiping
    # them would erase legitimate work signal in any rare misclassification.
    if [ -n "$_STALE_LOG" ] && [ -f "$_STALE_LOG" ] && ! grep -q "## Session Summary" "$_STALE_LOG"; then
      MESSAGES="${MESSAGES}INCOMPLETE SESSION LOG: Previous session log at ${_STALE_LOG} was never finalized (likely a crashed session). Add a '## Session Summary' section and append a row to the registry, or delete it.\n\n"
    fi
    rm -f "$ACTIVE_LOG_FILE"
    rm -f "$REPO_ROOT/.claude/session-start-ts.tmp"
  fi
fi

# Record session-start timestamp — only for primary sessions (not subagents).
# After the cleanup above, a remaining active-session-log.tmp implies a
# genuine in-progress primary session, so we preserve its start ts.
if [ ! -f "$ACTIVE_LOG_FILE" ]; then
    date +%s > "$REPO_ROOT/.claude/session-start-ts.tmp"
fi

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
  AUDIT_AGE_HOURS=$(( (NOW_TS - AUDIT_TS) / 3600 ))
  if [ "$AUDIT_AGE_HOURS" -ge 24 ]; then
    MESSAGES="${MESSAGES}KNOWLEDGE AUDIT DUE (${AUDIT_AGE_HOURS}h since last): Dispatch a background sonnet subagent (max_turns: 15) to run the knowledge audit. Tasks: (1) BACKFILL PROMOTIONS — scan last ~14 days of entries in every .living/learnings.md under this tree (use tail -120, never full files); for each transferable learning (pattern applies beyond single project — async, API, debugging, test, env, etc.) that is NOT already present in the matching ~/.claude/knowledge/{domain}.md file (grep for the learning's exact title — entries promoted inline will contain the same title), printf >> the entry as: ### Title (use the EXACT title from the learning entry) + **What**/**Evidence** (cite source project + date)/**When useful**/**Scope**/**Status: unreviewed**/**Last validated: YYYY-MM-DD**/**Promoted**: backfill by mycelium. (2) STALENESS — review existing entries in domain files; flag any with Last validated >90d old. (3) INDEX — if ~/.claude/knowledge/INDEX.md exists, regenerate it with current per-domain entry counts. (4) TIMESTAMP — overwrite ~/.claude/knowledge/.last-audit with: \$(date +%s) daily-audit. Report single-line summary of promotions added. Do this silently alongside your current task.\n\n"
  fi
else
  echo "$(date +%s) auto-initialized" > "$KNOWLEDGE_DIR/.last-audit"
fi

# --- Knowledge transfer staleness check ---
# Only runs if we're inside a meta-project (portfolio with multiple subprojects)
META_PROJECT=""
_CHECK_DIR=$(dirname "$REPO_ROOT")
while [ "$_CHECK_DIR" != "/" ] && [ "$_CHECK_DIR" != "." ]; do
  if [ -d "$_CHECK_DIR/.living" ]; then
    META_PROJECT="$_CHECK_DIR"
    break
  fi
  _CHECK_DIR=$(dirname "$_CHECK_DIR")
done

# Also check if current dir IS the meta-project (has subprojects with .living/)
if [ -z "$META_PROJECT" ] && [ -d "$REPO_ROOT/.living" ]; then
  SUBPROJECT_COUNT=0
  for _sp in "$REPO_ROOT"/*/.living; do
    [ -d "$_sp" ] && SUBPROJECT_COUNT=$(( SUBPROJECT_COUNT + 1 ))
  done
  [ "$SUBPROJECT_COUNT" -ge 2 ] && META_PROJECT="$REPO_ROOT"
fi

if [ -n "$META_PROJECT" ]; then
  TRANSFER_LAST_RUN="$META_PROJECT/.living/outputs/knowledge-transfers/.last-run"
  TRANSFER_STALE=false
  if [ ! -f "$TRANSFER_LAST_RUN" ]; then
    TRANSFER_STALE=true
    TRANSFER_AGE_MSG="never run"
  else
    TRANSFER_TS=$(date -jf "%Y-%m-%dT%H:%M:%SZ" "$(cat "$TRANSFER_LAST_RUN")" +%s 2>/dev/null || date -d "$(cat "$TRANSFER_LAST_RUN")" +%s 2>/dev/null || echo "0")
    TRANSFER_AGE_HOURS=$(( (NOW_TS - TRANSFER_TS) / 3600 ))
    if [ "$TRANSFER_AGE_HOURS" -ge 24 ]; then
      TRANSFER_STALE=true
      TRANSFER_AGE_MSG="${TRANSFER_AGE_HOURS}h since last run"
    fi
  fi

  if [ "$TRANSFER_STALE" = true ]; then
    MESSAGES="${MESSAGES}KNOWLEDGE TRANSFER DUE (${TRANSFER_AGE_MSG}): Dispatch a background sonnet subagent to run the mycelium transfer protocol. The subagent should: read recent learnings from all subprojects under ${META_PROJECT}, identify cross-project transfer opportunities, and write a report to ${META_PROJECT}/.living/outputs/knowledge-transfers/$(date +%Y-%m-%d).md. Do not block on results.\n\n"
  fi
fi

# --- Session log setup (runs every invocation, idempotent) ---
LIVING_DIR="$REPO_ROOT/.living"
LOG_DIR="$LIVING_DIR/log"

if [ -d "$LIVING_DIR" ]; then
  # ACTIVE_LOG_FILE was set above (early stale-cleanup block). Reuse it.
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

  # Create new log file only if no active session log exists (fresh process start)
  # If active-session-log.tmp exists, we're a subagent — skip log creation
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

    # Store log path + owner timestamp (for subagent detection in stop hook)
    printf "%s\n%s\n" "$LOG_PATH" "$(cat "$REPO_ROOT/.claude/session-start-ts.tmp" 2>/dev/null || date +%s)" > "$ACTIVE_LOG_FILE"

    # Refresh INDEX.md at session start (no LLM, <1s).
    # --summary-heuristic regenerates BOTH the quick reference and the
    # KNOWLEDGE SUMMARY block from tag annotations. Falls back to
    # --counts-only if the script is too old to know the new flag.
    GENERATE_INDEX_SCRIPT="$(dirname "$(dirname "$(realpath "$0")")")/scripts/generate_index.py"
    if [ -f "$GENERATE_INDEX_SCRIPT" ]; then
      python3 "$GENERATE_INDEX_SCRIPT" --living-dir "$LIVING_DIR" --summary-heuristic >/dev/null 2>&1 \
        || python3 "$GENERATE_INDEX_SCRIPT" --living-dir "$LIVING_DIR" --counts-only >/dev/null 2>&1 \
        || true
    fi
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
      # Build visible summary for user (systemMessage field)
      SESSION_DATE=$(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$SESSION_FILE" 2>/dev/null \
        || date -r "$SESSION_FILE" '+%Y-%m-%d %H:%M' 2>/dev/null \
        || echo "recent")
      SYSTEM_MESSAGE="SESSION RESUME (${SESSION_DATE}):\n${SESSION_CONTENT}"
      # Add full content to agent context via MESSAGES accumulator
      MESSAGES="${MESSAGES}${SESSION_CONTENT}\n\nPresent the user with a 1-2 sentence reminder of the above before proceeding.\n\n"
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

  # --- Inject INDEX.md knowledge cluster summaries ---
  INDEX_FILE="$LIVING_DIR/INDEX.md"
  if [ -f "$INDEX_FILE" ]; then
    # Only inject if sentinel markers are present (structured format — not legacy)
    if grep -q "<!-- BEGIN KNOWLEDGE SUMMARY -->" "$INDEX_FILE" 2>/dev/null; then
      KNOWLEDGE_SUMMARY=$(awk '/<!-- BEGIN KNOWLEDGE SUMMARY -->/{found=1; next} /<!-- END KNOWLEDGE SUMMARY -->/{exit} found{print}' "$INDEX_FILE" 2>/dev/null)
      if [ -n "$KNOWLEDGE_SUMMARY" ]; then
        MESSAGES="${MESSAGES}KNOWLEDGE MAP (read .living/INDEX.md for full details):\n${KNOWLEDGE_SUMMARY}\n\nReview relevant clusters before making decisions in those areas.\n\n"
      fi
    fi
    # If file exists but has no sentinels (legacy format), skip — don't load the whole file
  fi
fi

# --- Emit combined JSON ---
if [ -n "$MESSAGES" ] || [ -n "$SYSTEM_MESSAGE" ]; then
  ESCAPED_CTX=$(printf '%s' "$MESSAGES" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
  if [ -n "$SYSTEM_MESSAGE" ]; then
    ESCAPED_SYS=$(printf '%s' "$SYSTEM_MESSAGE" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
    printf '{"additionalContext": %s, "systemMessage": %s}\n' "$ESCAPED_CTX" "$ESCAPED_SYS"
  else
    printf '{"additionalContext": %s}\n' "$ESCAPED_CTX"
  fi
fi
exit 0
