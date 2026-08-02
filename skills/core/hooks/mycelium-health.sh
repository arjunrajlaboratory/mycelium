#!/usr/bin/env bash
# mycelium-health.sh — Claude Code SessionStart hook
# Checks .living/ health and knowledge audit freshness on session start
#
# Install: Add to .claude/settings.local.json under "SessionStart" hooks
# Input: JSON on stdin with {cwd, source, ...}
# Output: Single JSON with additionalContext if issues found

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

# Read stdin JSON
INPUT=$(cat)
HOST_SESSION_ID=$(printf '%s' "$INPUT" | mycelium_json_get 'session_id')

# Initialize message accumulator
MESSAGES=""
SYSTEM_MESSAGE=""
NOW_TS=$(date +%s)

# Extract cwd from input
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd', ''))" 2>/dev/null || echo "")
if [ -z "$CWD" ]; then
  CWD=$(pwd)
fi
SOURCE=$(printf '%s' "$INPUT" | mycelium_json_get 'source')

# Find git repo root
REPO_ROOT=$(cd "$CWD" && git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0  # Not in a git repo
fi

mycelium_prepare_state_dir "$REPO_ROOT" || exit 0

# SessionStart and Stop both mutate the active-log ownership transaction.
# Serialize them with the same repository-local lock so concurrent primary and
# subagent starts cannot reserve separate logs or overwrite one another's
# marker while Stop is finalizing the prior session.
if ! mycelium_acquire_stop_lock "$STATE_DIR"; then
  exit 0
fi
trap mycelium_release_stop_lock EXIT

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
ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
ACTIVE_OWNER_FILE="$STATE_DIR/active-session-owner-id.tmp"
mycelium_archive_abandoned_events() {
  local fallback_id="$1"
  local events_file="$STATE_DIR/mycelium-data-events.tmp"
  local lineage_id=""
  local archive_dir="$STATE_DIR/mycelium-data-events-abandoned"
  local destination=""

  if [[ -e "$events_file" || -L "$events_file" ]]; then
    [[ -f "$events_file" && ! -L "$events_file" ]] || return 1
  else
    return 0
  fi
  [[ -s "$events_file" ]] || return 0
  if [[ -e "$STATE_DIR/data-lineage-session-id.tmp" \
    || -L "$STATE_DIR/data-lineage-session-id.tmp" ]]; then
    [[ -f "$STATE_DIR/data-lineage-session-id.tmp" \
      && ! -L "$STATE_DIR/data-lineage-session-id.tmp" ]] || return 1
    lineage_id=$(head -1 "$STATE_DIR/data-lineage-session-id.tmp" 2>/dev/null || true)
  fi
  if [[ ! "$lineage_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    lineage_id="$fallback_id"
  fi
  if [[ ! "$lineage_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    lineage_id="abandoned-$(date +%s)"
  fi
  if [[ -e "$archive_dir" || -L "$archive_dir" ]]; then
    [[ -d "$archive_dir" && ! -L "$archive_dir" ]] || return 1
  else
    mkdir "$archive_dir" || return 1
  fi
  destination="$archive_dir/${lineage_id}.tmp"
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    destination="$archive_dir/${lineage_id}-$(date +%s)-$$.tmp"
  fi
  mv "$events_file" "$destination" || return 1
}
if [ -f "$ACTIVE_LOG_FILE" ]; then
  _SHOULD_CLEAN=false
  _STALE_LOG=""
  _STALE_OWNER_TS=""
  _STALE_OWNER_FORMAT=""
  _STALE_OWNER_ID=""
  _DIFFERENT_ROOT_OWNER=false
  _SUPERSEDED_OWNER=false
  if _ACTIVE_MARKER=$(mycelium_read_active_log_marker "$REPO_ROOT" "$ACTIVE_LOG_FILE"); then
    _STALE_LOG=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '1p')
    _STALE_OWNER_TS=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '2p')
    _STALE_OWNER_FORMAT=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '3p')
  else
    _SHOULD_CLEAN=true
    MESSAGES="${MESSAGES}CORRUPT SESSION MARKER: Removed an invalid active-session marker without following its path. Session lifecycle state will be rebuilt safely.\n\n"
  fi
  # SessionStart represents a root host task; current Claude and Codex
  # subagents use their dedicated lifecycle events and retain the parent's
  # session_id. A startup carrying a different valid host ID is therefore an
  # independent root task. Record the identity mismatch here, but do not let it
  # supersede a transaction until the ordinary liveness checks below prove the
  # prior owner abandoned.
  if [[ "$_SHOULD_CLEAN" != true \
    && "$SOURCE" == "startup" \
    && "$_STALE_OWNER_FORMAT" == "owner-id-v1" \
    && "$HOST_SESSION_ID" =~ ^[A-Za-z0-9._-]+$ \
    && ${#HOST_SESSION_ID} -le 200 ]]; then
    if _STALE_OWNER_ID=$(mycelium_read_session_owner_id "$ACTIVE_OWNER_FILE") \
      && [[ "$_STALE_OWNER_ID" != "$HOST_SESSION_ID" ]]; then
      _DIFFERENT_ROOT_OWNER=true
    fi
  fi
  # Definitive signals — clean regardless of activity:
  if [ "$_SHOULD_CLEAN" = true ]; then
    :
  elif [ -z "$_STALE_OWNER_TS" ]; then
    # Old format (no owner TS) — clean up for format upgrade
    _SHOULD_CLEAN=true
  elif [ -n "$_STALE_LOG" ] && [ -f "$_STALE_LOG" ] \
    && grep -q "^ended: [0-9]" "$_STALE_LOG" \
    && [ ! -e "$STATE_DIR/handoff-finalization-pending.tmp" ] \
    && [ ! -L "$STATE_DIR/handoff-finalization-pending.tmp" ]; then
    # Log already finalized but sentinel wasn't cleaned
    _SHOULD_CLEAN=true
  elif [ -n "$_STALE_LOG" ] && [ ! -f "$_STALE_LOG" ]; then
    # Log file deleted but sentinel remains
    _SHOULD_CLEAN=true
  elif [ "$(( $(date +%s) - _STALE_OWNER_TS ))" -gt 7200 ]; then
    # owner_ts > 2h: only conclude "crashed" if activity signals are also
    # quiet. If either is fresh, the session is alive — don't touch.
    _NOW=$(date +%s)
    _ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
    _REMINDED_FILE="$STATE_DIR/mycelium-reminded.tmp"
    _ACT_AGE=999999999
    _REM_AGE=999999999
    if [ -f "$_ACTIVITY_FILE" ]; then
      _ACT_MTIME=$(mycelium_file_mtime "$_ACTIVITY_FILE")
      _ACT_AGE=$(( _NOW - _ACT_MTIME ))
    fi
    if [ -f "$_REMINDED_FILE" ]; then
      _REM_MTIME=$(mycelium_file_mtime "$_REMINDED_FILE")
      _REM_AGE=$(( _NOW - _REM_MTIME ))
    fi
    # Clean only if BOTH activity signals are also old (> 2h). If either is
    # fresh, assume the session is still alive.
    if [ "$_ACT_AGE" -gt 7200 ] && [ "$_REM_AGE" -gt 7200 ]; then
      _SHOULD_CLEAN=true
    fi
  fi
  if [[ "$_DIFFERENT_ROOT_OWNER" == true ]]; then
    if [[ "$_SHOULD_CLEAN" == true ]]; then
      _SUPERSEDED_OWNER=true
      MESSAGES="${MESSAGES}ABANDONED PRIOR SESSION: A new root host task superseded the inactive transaction owned by ${_STALE_OWNER_ID}. Its log and archived raw lineage were preserved for audit; this task has a fresh lifecycle transaction.\n\n"
    else
      MESSAGES="${MESSAGES}ACTIVE SESSION PRESERVED: Another root task (${_STALE_OWNER_ID}) still owns this repository's live Mycelium transaction. This task will not replace or mutate that lifecycle state.\n\n"
    fi
  fi
  if [ "$_SHOULD_CLEAN" = true ]; then
    # If the orphaned log was never finalized, surface a warning so the new
    # session knows about it. Archive raw lineage before dropping every
    # transaction-scoped sentinel so the fresh root cannot mix old evidence or
    # inherit a completed work cycle.
    if [ -n "$_STALE_LOG" ] && [ -f "$_STALE_LOG" ] && ! grep -q "## Session Summary" "$_STALE_LOG"; then
      MESSAGES="${MESSAGES}INCOMPLETE SESSION LOG: Previous session log at ${_STALE_LOG} was never finalized (likely a crashed session). Add a '## Session Summary' section and append a row to the registry, or delete it.\n\n"
    fi
    _ARCHIVE_FALLBACK="abandoned-$(date +%s)"
    if [ "${_SUPERSEDED_OWNER:-false}" = true ]; then
      _ARCHIVE_FALLBACK="$_STALE_OWNER_ID"
    fi
    if ! mycelium_archive_abandoned_events "$_ARCHIVE_FALLBACK"; then
      exit 0
    fi
    rm -f "$ACTIVE_LOG_FILE"
    rm -f "$ACTIVE_OWNER_FILE"
    rm -f "$STATE_DIR/session-start-ts.tmp"
    rm -f "$STATE_DIR/handoff-finalization-pending.tmp"
    rm -f "$STATE_DIR/data-lineage-session-id.tmp"
    rm -f "$STATE_DIR/mycelium-reminded.tmp"
    rm -f "$STATE_DIR/mycelium-session-activity.tmp"
    rm -f "$STATE_DIR/living-reminder-baseline.json"
    rm -f "$STATE_DIR/session-file-baseline.json"
  fi
fi

# Record session-start timestamp — only for primary sessions (not subagents).
# After the cleanup above, a remaining active-session-log.tmp implies a
# genuine in-progress primary session, so we preserve its start ts.
FRESH_PRIMARY_SESSION=false
if [ ! -f "$ACTIVE_LOG_FILE" ]; then
    date +%s > "$STATE_DIR/session-start-ts.tmp"
    # A host session ID is the only per-invocation identity shared by its
    # SessionStart and Stop processes. Clear any orphaned prior owner before
    # reserving this fresh primary transaction.
    rm -f "$ACTIVE_OWNER_FILE"
    rm -f "$STATE_DIR/handoff-finalization-pending.tmp"
    FRESH_PRIMARY_SESSION=true
fi

# Clean up transaction sentinels only after the prior marker was removed and
# this invocation reserved a fresh primary transaction. A retained marker may
# belong to another live root whose reminder is old but activity is current;
# deleting the pair would erase the evidence that prevents false supersession.
if [ "$FRESH_PRIMARY_SESSION" = true ] \
  && [ -f "$STATE_DIR/mycelium-reminded.tmp" ]; then
  rm -f "$STATE_DIR/mycelium-reminded.tmp"
  rm -f "$STATE_DIR/mycelium-session-activity.tmp"
fi

# --- Knowledge audit check (runs regardless of SOURCE) ---
KNOWLEDGE_DIR=$(mycelium_knowledge_dir)
if [ ! -d "$KNOWLEDGE_DIR" ]; then
  MESSAGES="${MESSAGES}KNOWLEDGE SYSTEM INIT: ~/.mycelium/knowledge/ does not exist. Create the directory and initialize domain files per the progressive disclosure spec. Run this silently as a background task.\n\n"
elif [ -f "$KNOWLEDGE_DIR/.last-audit" ]; then
  AUDIT_TS=$(awk '{print $1}' "$KNOWLEDGE_DIR/.last-audit")
  [[ "$AUDIT_TS" =~ ^[0-9]{1,18}$ ]] || AUDIT_TS=0
  NOW_TS=$(date +%s)
  AUDIT_AGE_HOURS=$(( (NOW_TS - AUDIT_TS) / 3600 ))
  if [ "$AUDIT_AGE_HOURS" -ge 24 ]; then
    MESSAGES="${MESSAGES}KNOWLEDGE AUDIT DUE (${AUDIT_AGE_HOURS}h since last): Dispatch a background capable subagent (max_turns: 15) to run the knowledge audit. Tasks: (1) BACKFILL PROMOTIONS — scan last ~14 days of entries in every .living/learnings.md under this tree (use tail -120, never full files); for each transferable learning (pattern applies beyond single project — async, API, debugging, test, env, etc.) that is NOT already present in the matching ~/.mycelium/knowledge/{domain}.md file (grep for the learning's exact title — entries promoted inline will contain the same title), printf >> the entry as: ### Title (use the EXACT title from the learning entry) + **What**/**Evidence** (cite source project + date)/**When useful**/**Scope**/**Status: unreviewed**/**Last validated: YYYY-MM-DD**/**Promoted**: backfill by mycelium. (2) STALENESS — review existing entries in domain files; flag any with Last validated >90d old. (3) INDEX — if ~/.mycelium/knowledge/INDEX.md exists, regenerate it with current per-domain entry counts. (4) TIMESTAMP — overwrite ~/.mycelium/knowledge/.last-audit with: \$(date +%s) daily-audit. Report single-line summary of promotions added. Do this silently alongside your current task.\n\n"
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
    MESSAGES="${MESSAGES}KNOWLEDGE TRANSFER DUE (${TRANSFER_AGE_MSG}): Dispatch a background capable subagent to run the mycelium transfer protocol. The subagent should: read recent learnings from all subprojects under ${META_PROJECT}, identify cross-project transfer opportunities, and write a report to ${META_PROJECT}/.living/outputs/knowledge-transfers/$(date +%Y-%m-%d).md. Do not block on results.\n\n"
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

  # Create a log only when no active transaction exists. A retained marker may
  # belong to a subagent's parent or another live root task; neither caller may
  # reserve a competing repository-global transaction.
  if [ ! -f "$ACTIVE_LOG_FILE" ]; then
    TODAY=$(date +%Y-%m-%d)
    # Pick one greater than the highest number already used today. Counting
    # files can reuse an occupied number when the sequence has a gap (for
    # example 001 and 003), which would overwrite that existing log.
    MAX_SESSION_NUM=0
    for _f in "$LOG_DIR"/${TODAY}-*.md; do
      [ -f "$_f" ] || continue
      _SESSION_BASENAME=$(basename "$_f")
      if [[ "$_SESSION_BASENAME" =~ ^${TODAY}-([0-9]+)-.*\.md$ ]]; then
        _SESSION_NUM_VALUE=$((10#${BASH_REMATCH[1]}))
        if (( _SESSION_NUM_VALUE > MAX_SESSION_NUM )); then
          MAX_SESSION_NUM=$_SESSION_NUM_VALUE
        fi
      fi
    done
    SESSION_NUM=$(printf "%03d" $((MAX_SESSION_NUM + 1)))

    # Derive slug from project directory name
    PROJECT_NAME=$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd '[:alnum:]-')
    SESSION_ID="${TODAY}-${SESSION_NUM}"
    LOG_FILENAME="${SESSION_ID}-${PROJECT_NAME}.md"
    LOG_PATH="$LOG_DIR/$LOG_FILENAME"

    # Detect project and branch
    # rev-parse prints "HEAD" but exits nonzero for an unborn branch. Do not
    # combine partial stdout with a fallback inside one substitution.
    if ! BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null); then
      BRANCH=$(git -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    fi
    if [[ -z "$BRANCH" || "$BRANCH" == *$'\n'* ]]; then
      BRANCH="unknown"
    fi
    # JSON strings are valid YAML scalars and protect unusual but valid Git
    # branch names (for example, names beginning with a brace).
    BRANCH_YAML=$(printf '%s' "$BRANCH" | python3 -c \
      'import json,sys; print(json.dumps(sys.stdin.read()))')
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
branch: ${BRANCH_YAML}
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

    # Publish the host's per-invocation identity before the active marker. A
    # concurrent child cannot observe the marker until its primary owner token
    # is complete. Older hosts without session_id retain the timestamp fallback.
    _ACTIVE_MARKER_FORMAT=""
    if [[ "$HOST_SESSION_ID" =~ ^[A-Za-z0-9._-]+$ \
      && ${#HOST_SESSION_ID} -le 200 ]]; then
      _ACTIVE_OWNER_TMP=$(mktemp "$STATE_DIR/.active-session-owner-id.tmp.XXXXXX") || {
        rm -f "$LOG_PATH"
        exit 0
      }
      if ! printf '%s\n' "$HOST_SESSION_ID" > "$_ACTIVE_OWNER_TMP" \
        || ! mv -f "$_ACTIVE_OWNER_TMP" "$ACTIVE_OWNER_FILE"; then
        rm -f "$_ACTIVE_OWNER_TMP" "$LOG_PATH"
        exit 0
      fi
      _ACTIVE_MARKER_FORMAT="owner-id-v1"
    fi

    # Store log path + owner timestamp, plus an ownership-format discriminator
    # when this host supplied a per-invocation identity. The third line makes a
    # missing companion owner token distinguishable from a legacy transaction.
    # Publish the complete marker atomically so PostToolUse can never observe a
    # partial transaction while SessionStart is still writing it.
    _ACTIVE_MARKER_TMP=$(mktemp "$STATE_DIR/.active-session-log.tmp.XXXXXX") || {
      rm -f "$ACTIVE_OWNER_FILE" "$LOG_PATH"
      exit 0
    }
    if ! {
      printf "%s\n%s\n" \
        "$LOG_PATH" \
        "$(cat "$STATE_DIR/session-start-ts.tmp" 2>/dev/null || date +%s)"
      if [[ -n "$_ACTIVE_MARKER_FORMAT" ]]; then
        printf '%s\n' "$_ACTIVE_MARKER_FORMAT"
      fi
    } > "$_ACTIVE_MARKER_TMP" \
      || ! mv -f "$_ACTIVE_MARKER_TMP" "$ACTIVE_LOG_FILE"; then
      rm -f "$_ACTIVE_MARKER_TMP" "$ACTIVE_OWNER_FILE" "$LOG_PATH"
      exit 0
    fi
    # Data-lineage consolidation may run concurrently with stop finalization,
    # so keep its canonical session ID in an independent sentinel.
    printf '%s\n' "$SESSION_ID" > "$STATE_DIR/data-lineage-session-id.tmp"

    # Refresh INDEX.md at session start (no LLM, <1s).
    # --summary-heuristic regenerates BOTH the quick reference and the
    # KNOWLEDGE SUMMARY block from tag annotations. Falls back to
    # --counts-only if the script is too old to know the new flag.
    GENERATE_INDEX_SCRIPT="$HERE/../scripts/generate_index.py"
    if [ -f "$GENERATE_INDEX_SCRIPT" ]; then
      python3 "$GENERATE_INDEX_SCRIPT" --living-dir "$LIVING_DIR" --summary-heuristic >/dev/null 2>&1 \
        || python3 "$GENERATE_INDEX_SCRIPT" --living-dir "$LIVING_DIR" --counts-only >/dev/null 2>&1 \
        || true
    fi

    # Snapshot the repository only after SessionStart's own log/INDEX writes.
    # Stop compares against this baseline so pre-existing dirty or untracked
    # work is not misreported as activity from the current session.
    SESSION_CHANGES_SCRIPT="${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"
    SESSION_BASELINE_FILE="$STATE_DIR/session-file-baseline.json"
    if [ -f "$SESSION_CHANGES_SCRIPT" ]; then
      python3 "$SESSION_CHANGES_SCRIPT" snapshot \
        --repo-root "$REPO_ROOT" \
        --output "$SESSION_BASELINE_FILE" >/dev/null 2>&1 \
        || rm -f "$SESSION_BASELINE_FILE"
    fi
  fi
fi

# Only run .living/ health checks on fresh session starts
if [ "$SOURCE" != "startup" ]; then
  # Emit any accumulated messages (e.g. knowledge audit) and exit
  if [ -n "$MESSAGES" ]; then
    mycelium_emit_context "SessionStart" "$MESSAGES"
  fi
  exit 0
fi

# --- Session resume: load last-session.md if recent ---
SESSION_FILE="$STATE_DIR/last-session.md"
if [ -f "$SESSION_FILE" ]; then
  SESSION_MTIME=$(mycelium_file_mtime "$SESSION_FILE")
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
    REGISTRY_ROWS=$(grep -c "^| F-" "$FINDINGS_REGISTRY" 2>/dev/null || true)
    REGISTRY_ROWS=${REGISTRY_ROWS:-0}
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
  [ -f "$LIVING_DIR/learnings.md" ]   && LEARNINGS_COUNT=$(grep -c '^### ' "$LIVING_DIR/learnings.md" 2>/dev/null || true)
  [ -f "$LIVING_DIR/decisions.md" ]   && DECISIONS_COUNT=$(grep -c '^### ' "$LIVING_DIR/decisions.md" 2>/dev/null || true)
  [ -f "$LIVING_DIR/conventions.md" ] && CONVENTIONS_COUNT=$(grep -c '^## ' "$LIVING_DIR/conventions.md" 2>/dev/null || true)
  LEARNINGS_COUNT=${LEARNINGS_COUNT:-0}
  DECISIONS_COUNT=${DECISIONS_COUNT:-0}
  CONVENTIONS_COUNT=${CONVENTIONS_COUNT:-0}

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
  mycelium_emit_context "SessionStart" "$MESSAGES" "$SYSTEM_MESSAGE"
fi
exit 0
