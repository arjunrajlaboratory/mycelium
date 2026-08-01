#!/usr/bin/env bash
# mycelium-stop-check.sh — Claude Code Stop hook
# 1. Auto-finalizes session log in .living/log/ (factual record, guaranteed)
# 2. Blocks session end if meaningful work was performed but .living/
#    learnings/decisions were not updated (enforces reflection)
# Does NOT block read-only or config-only sessions.
#
# Install: Add to .claude/settings.local.json under "Stop" hooks
# Input: JSON on stdin with session metadata
# Output: JSON with {"decision": "block", "reason": "..."} to prevent stop if needed

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

# Consume the hook payload. Claude Code and Codex set stop_hook_active=true
# after a Stop hook asks the model to continue. That flag must not bypass an
# outstanding Mycelium reminder: the state checks below naturally stop
# blocking once .living/ has been updated, which is the recursion guard.
INPUT=$(cat)
HOST_SESSION_ID=$(printf '%s' "$INPUT" | mycelium_json_get 'session_id')

# Determine repo root early (used by both log finalization and .living/ checks)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi
mycelium_prepare_state_dir "$REPO_ROOT" || exit 0
if ! mycelium_acquire_stop_lock "$STATE_DIR"; then
  # Another Stop invocation owns the transaction and will produce the single
  # authoritative decision/finalization.
  exit 0
fi
trap mycelium_release_stop_lock EXIT

# Consolidate lineage in-process before lifecycle enforcement. Hook runtimes
# launch sibling command hooks concurrently, so registering consolidation as a
# separate Stop handler would race the accepted-Stop cleanup below.
LINEAGE_HOOK="$HERE/mycelium-data-lineage-stop.sh"
if [[ -s "$STATE_DIR/mycelium-data-events.tmp" ]]; then
  if [[ ! -x "$LINEAGE_HOOK" ]] \
    || ! printf '%s' "$INPUT" | "$LINEAGE_HOOK"; then
    mycelium_emit_stop_block \
      "STOP BLOCKED — data-lineage consolidation failed. Raw events and active session state were preserved; inspect the lineage status sentinel or hook installation, then retry Stop."
    exit 0
  fi
fi
LINEAGE_EVENT_COUNT=0
if [[ -s "$STATE_DIR/mycelium-data-events.tmp" ]]; then
  LINEAGE_EVENT_COUNT=1
fi

# Accepting Stop owns final cleanup of data-lineage state. Retaining both files
# across a blocked Stop lets later analysis append to the same manifest.
mycelium_accept_lineage_session() {
  local session_marker="$STATE_DIR/data-lineage-session-id.tmp"
  local events_file="$STATE_DIR/mycelium-data-events.tmp"
  local session_id
  session_id=$(head -1 "$session_marker" 2>/dev/null || echo "")
  if [[ -z "$session_id" ]]; then
    session_id="$HOST_SESSION_ID"
  fi
  if [[ ! "$session_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    session_id="unattributed-$(date +%s)"
  fi

  if [[ -f "$events_file" ]]; then
    local prev_dir="$STATE_DIR/mycelium-data-events-prev"
    mkdir -p "$prev_dir"
    mv "$events_file" "$prev_dir/${session_id}.tmp"
    # Keep only the 20 most recent raw-event archives.
    ls -t "$prev_dir"/*.tmp 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
  fi
  rm -f "$session_marker"
}

# Resolve this hook's mycelium-core dir once, in absolute form. Used to locate
# the upsert script and the log-scribe template. BASH_SOURCE may be unset in
# weird invocations (e.g. `sh -c "$(...)"`), so fall back to $0; if even that
# fails, leave SCRIPT_DIR empty and downstream existence checks will skip.
HOOK_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR=$(cd "$(dirname "$(dirname "$HOOK_SOURCE")")" 2>/dev/null && pwd || echo "")
UPSERT_SCRIPT="${MYCELIUM_REGISTRY_UPSERT_HELPER:-$SCRIPT_DIR/scripts/upsert_registry_row.py}"

# --- Session log finalization ---
ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
if [ -n "$REPO_ROOT" ] && [ -f "$ACTIVE_LOG_FILE" ]; then
  ACTIVE_MARKER_VALID=true
  if _ACTIVE_MARKER=$(mycelium_read_active_log_marker "$REPO_ROOT" "$ACTIVE_LOG_FILE"); then
    LOG_PATH=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '1p')
    OWNER_TS=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '2p')
  else
    # Remove only the invalid marker, then continue to independent lifecycle
    # enforcement. Never follow or quote the untrusted path it contained.
    ACTIVE_MARKER_VALID=false
    LOG_PATH=""
    OWNER_TS=""
    rm -f "$ACTIVE_LOG_FILE"
  fi
  OUR_TS=$(cat "$STATE_DIR/session-start-ts.tmp" 2>/dev/null || echo "")

  # Subagent detection: if owner timestamp exists and doesn't match ours, we're a subagent
  if [[ "$OWNER_TS" =~ ^[0-9]{1,18}$ \
    && "$OUR_TS" =~ ^[0-9]{1,18}$ \
    && "$OWNER_TS" != "$OUR_TS" ]]; then
      # Subagent: skip all finalization and .living/ checks
      # File activity is tracked in the shared activity file for the primary session
      exit 0
  fi

  if [[ "$ACTIVE_MARKER_VALID" == true && -f "$LOG_PATH" ]]; then
    # Compute session duration. Prefer the frontmatter `started:` field
    # (set when the SessionStart hook created this log) over
    # session-start-ts.tmp, which can be stale across crashed sessions and
    # produce nonsense durations like 14794 minutes for a 55-second session.
    LOG_REPO="$REPO_ROOT"
    START_FILE="$STATE_DIR/session-start-ts.tmp"
    NOW_TS=$(date +%s)
    DURATION_MIN=0
    START_TS=""

    FM_STARTED=$({ grep -m1 '^started:' "$LOG_PATH" 2>/dev/null || true; } | sed 's/^started:[[:space:]]*//; s/[[:space:]]*$//')
    if [ -n "$FM_STARTED" ]; then
      # Try BSD date (macOS) first, then GNU date (Linux). Frontmatter format
      # is e.g. 2026-04-26T06:43:53-0400.
      START_TS=$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$FM_STARTED" +%s 2>/dev/null \
                 || date -d "$FM_STARTED" +%s 2>/dev/null \
                 || echo "")
    fi
    if [ -z "$START_TS" ] && [ -f "$START_FILE" ]; then
      START_TS=$(cat "$START_FILE" 2>/dev/null || echo "")
    fi
    if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
      DURATION_MIN=$(( (NOW_TS - START_TS) / 60 ))
      [ "$DURATION_MIN" -lt 0 ] && DURATION_MIN=0
    fi

    # Build one unique, session-local file set. SessionStart snapshots the
    # pre-existing dirty state, so an uncommitted repository does not make
    # every old path look like work from this session.
    ACTIVITY_FILE_CHECK="$STATE_DIR/mycelium-session-activity.tmp"
    SESSION_BASELINE_FILE="$STATE_DIR/session-file-baseline.json"
    SESSION_CHANGES_SCRIPT="${MYCELIUM_SESSION_CHANGES_HELPER:-$SCRIPT_DIR/scripts/session_file_changes.py}"
    ACTIVE_LOG_REL=""
    case "$LOG_PATH" in
      "$LOG_REPO"/*) ACTIVE_LOG_REL="${LOG_PATH#"$LOG_REPO"/}" ;;
    esac
    if [ -f "$SESSION_CHANGES_SCRIPT" ]; then
      _CHANGE_ARGS=(collect --repo-root "$LOG_REPO" --baseline "$SESSION_BASELINE_FILE" --activity-file "$ACTIVITY_FILE_CHECK")
      if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
        _CHANGE_ARGS+=(--start-ts "$START_TS")
      fi
      if [ -n "$ACTIVE_LOG_REL" ]; then
        _CHANGE_ARGS+=(--exclude "$ACTIVE_LOG_REL")
      fi
      _CHANGE_ARGS+=(--exclude ".living/log/LOG_REGISTRY.md")
      _CHANGE_ARGS+=(--exclude-prefix ".living/log/")
      SESSION_CHANGED_FILES=$(python3 "$SESSION_CHANGES_SCRIPT" "${_CHANGE_ARGS[@]}" 2>/dev/null || true)
    else
      # Compatibility fallback for an incomplete/older installation.
      SESSION_CHANGED_FILES=$(
        {
          if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
            git -C "$LOG_REPO" log --since="@${START_TS}" --name-only --pretty=format: 2>/dev/null || true
          fi
          if [ -f "$ACTIVITY_FILE_CHECK" ]; then
            while IFS= read -r activity_path; do
              [ -z "$activity_path" ] && continue
              case "$activity_path" in
                "$LOG_REPO"/*) printf '%s\n' "${activity_path#"$LOG_REPO"/}" ;;
                *) printf '%s\n' "$activity_path" ;;
              esac
            done < "$ACTIVITY_FILE_CHECK"
          fi
        } | sed '/^[[:space:]]*$/d' | sort -u
      )
    fi
    FILES_CHANGED=0
    if [ -n "$SESSION_CHANGED_FILES" ]; then
      FILES_CHANGED=$(printf '%s\n' "$SESSION_CHANGED_FILES" | grep -c . || echo "0")
    fi

    # Explicit Edit/Write activity is an independent work signal. The helper's
    # file set already includes committed and Bash-mutated paths.
    ACTIVITY_COUNT=0
    if [ -f "$ACTIVITY_FILE_CHECK" ]; then
      ACTIVITY_COUNT=$(sort -u "$ACTIVITY_FILE_CHECK" | grep -c . 2>/dev/null || echo "0")
    fi
    REMINDER_COUNT=0
    if [ -f "$STATE_DIR/mycelium-reminded.tmp" ]; then
      REMINDER_COUNT=1
    fi

    # Decide whether Stop is accepted before mutating any final state. File
    # changes discovered from Git are an enforcement signal even when a Bash
    # command bypassed the editor/activity hook. Lineage-only inline work keeps
    # its log and session ID but does not require a scientific reflection.
    ENFORCEMENT_REQUIRED=0
    if [ "$ACTIVITY_COUNT" -gt 0 ] \
      || [ "$REMINDER_COUNT" -gt 0 ] \
      || [ "$FILES_CHANGED" -gt 0 ]; then
      ENFORCEMENT_REQUIRED=1
    fi
    if [ "$ENFORCEMENT_REQUIRED" -eq 1 ]; then
      WORK_TS=$(head -1 "$STATE_DIR/mycelium-reminded.tmp" 2>/dev/null || echo "$START_TS")
      [[ "$WORK_TS" =~ ^[0-9]+$ ]] || WORK_TS=0
      LIVING_BASELINE_FILE="$STATE_DIR/living-reminder-baseline.json"
      if [[ ! -f "$LIVING_BASELINE_FILE" ]]; then
        LIVING_BASELINE_FILE="$SESSION_BASELINE_FILE"
      fi
      if ! mycelium_living_changed \
        "$REPO_ROOT" "$LIVING_BASELINE_FILE" "$SESSION_CHANGES_SCRIPT" "$WORK_TS"; then
        FILE_NAMES=$(printf '%s\n' "$SESSION_CHANGED_FILES" | head -15 \
          | while IFS= read -r changed_path; do basename "$changed_path"; done \
          | tr '\n' ',' | sed 's/,$//; s/,/, /g')
        REASON="STOP BLOCKED — ${FILES_CHANGED} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update last-session.md."
        ESCAPED_REASON=$(printf '%s' "$REASON" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
        printf '{"decision": "block", "reason": %s}\n' "$ESCAPED_REASON"
        exit 0
      fi
    fi

    # Skip finalization only if NO evidence of work exists in any signal.
    # Lineage-only inline analyses still reserve their session ID and manifest.
    if [ "$ACTIVITY_COUNT" -eq 0 ] \
      && [ "$REMINDER_COUNT" -eq 0 ] \
      && [ "$FILES_CHANGED" -eq 0 ] \
      && [ "$LINEAGE_EVENT_COUNT" -eq 0 ]; then
      rm -f "$LOG_PATH"
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$STATE_DIR/session-start-ts.tmp"
      rm -f "$SESSION_BASELINE_FILE"
      rm -f "$STATE_DIR/living-reminder-baseline.json"
      # No registry row, no finalization — clean exit (noise session)
    else
      # Prepare the finalization transaction, but do not stamp the log as
      # accepted until every required registry/context write has succeeded.
      # SessionStart treats a nonempty `ended:` field as definitive cleanup
      # evidence, so writing it before a failed registry upsert loses retry
      # ownership on resume/compact.
      LOG_DIR=$(dirname "$LOG_PATH")

      # Append to LOG_REGISTRY.md
      PROJECT_SLUG=$({ grep '^project:' "$LOG_PATH" || echo "project: unknown"; } | sed 's/^project: *//')
      SESSION_ID=$({ grep '^session_id:' "$LOG_PATH" || echo "session_id: unknown"; } | sed 's/^session_id: *//')
      BRANCH=$({ grep '^branch:' "$LOG_PATH" || echo "branch: unknown"; } | sed 's/^branch: *//')
      if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session registry finalization failed because the session ID is invalid. Active state was preserved for repair and retry."
        exit 0
      fi
      # Summary from the first 3 paths in the same unique session file set.
      SUMMARY=""
      if [ -n "$SESSION_CHANGED_FILES" ]; then
        SUMMARY=$(printf '%s\n' "$SESSION_CHANGED_FILES" | head -3 \
          | while IFS= read -r changed_path; do basename "$changed_path"; done \
          | tr '\n' ',' | sed 's/,$//; s/,/, /g')
        if [ "$FILES_CHANGED" -gt 3 ]; then
          SUMMARY="${SUMMARY} (+$((FILES_CHANGED - 3)) more)"
        fi
      fi
      PROJECT_SLUG=$(printf '%s' "$PROJECT_SLUG" | mycelium_registry_cell)
      BRANCH=$(printf '%s' "$BRANCH" | mycelium_registry_cell)
      SUMMARY=$(printf '%s' "$SUMMARY" | mycelium_registry_cell)
      LOG_BASENAME=$(basename "$LOG_PATH")
      # Atomic upsert via the script resolved at the top of this hook ($UPSERT_SCRIPT).
      NEW_ROW="| $(date +%Y-%m-%d) | ${SESSION_ID} | ${PROJECT_SLUG} | ${BRANCH} | ${DURATION_MIN}m | ${FILES_CHANGED} | ${SUMMARY} | | complete | | [log](${LOG_BASENAME}) |"
      REGISTRY_OK=false
      if [ -f "$LOG_DIR/LOG_REGISTRY.md" ]; then
        if [ -f "$UPSERT_SCRIPT" ]; then
          # If the script rejects (e.g. wrong pipe count), the error stays in
          # .upsert_registry_row.err for operator debugging. Do NOT echo the
          # row on rejection — that would defeat the validation the script
          # exists to perform.
          python3 "$UPSERT_SCRIPT" "$LOG_DIR/LOG_REGISTRY.md" "$SESSION_ID" "$NEW_ROW" \
            >/dev/null 2>"$LOG_DIR/.upsert_registry_row.err" \
            && { rm -f "$LOG_DIR/.upsert_registry_row.err"; REGISTRY_OK=true; }
        fi
      fi
      if [[ "$REGISTRY_OK" != true ]]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session registry finalization failed. The active log and session baselines were preserved; inspect .living/log/.upsert_registry_row.err or the helper installation, then retry Stop."
        exit 0
      fi

      # Deterministic Summary: commit subjects since session start.
      # Runs in milliseconds with no model or provider dependency.
      if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
        DETERMINISTIC_SUMMARY=$(
          { git -C "$LOG_REPO" log --since="@${START_TS}" --pretty=format:'%s' 2>/dev/null || true; } \
            | head -3 | tr '\n' ';' | sed 's/;$//; s/;/; /g'
        )
        # Cap at 200 chars
        if [ ${#DETERMINISTIC_SUMMARY} -gt 200 ]; then
          DETERMINISTIC_SUMMARY="${DETERMINISTIC_SUMMARY:0:197}..."
        fi
        if [ -n "$DETERMINISTIC_SUMMARY" ]; then
          DETERMINISTIC_SUMMARY=$(printf '%s' "$DETERMINISTIC_SUMMARY" | mycelium_registry_cell)
          # Re-upsert the row with the deterministic Summary. Same row, better Summary.
          NEW_ROW_DET="| $(date +%Y-%m-%d) | ${SESSION_ID} | ${PROJECT_SLUG} | ${BRANCH} | ${DURATION_MIN}m | ${FILES_CHANGED} | ${DETERMINISTIC_SUMMARY} | | complete | | [log](${LOG_BASENAME}) |"
          if [ -f "$UPSERT_SCRIPT" ]; then
            if ! python3 "$UPSERT_SCRIPT" "$LOG_DIR/LOG_REGISTRY.md" "$SESSION_ID" "$NEW_ROW_DET" \
              >/dev/null 2>"$LOG_DIR/.upsert_registry_row.err"; then
              mycelium_emit_stop_block \
                "STOP BLOCKED — session registry finalization failed while writing the deterministic summary. Active state was preserved for retry."
              exit 0
            fi
            rm -f "$LOG_DIR/.upsert_registry_row.err"
          fi
        fi
      fi

      # Auto-write last-session.md for next session context
      _SESSION_FILE="$STATE_DIR/last-session.md"
      _WORK_LINES=""
      # Try recent commit messages first
      if [ -n "${START_TS:-}" ]; then
          _WORK_LINES=$(
            { git -C "$REPO_ROOT" log --since="@${START_TS}" --pretty=format:"- %s" 2>/dev/null || true; } \
              | head -10
          )
      fi
      # Fall back to the de-duplicated modified file list.
      if [ -z "$_WORK_LINES" ] && [ -n "$SESSION_CHANGED_FILES" ]; then
          _WORK_LINES=$(printf '%s\n' "$SESSION_CHANGED_FILES" | head -10 | while IFS= read -r _f; do echo "- Modified \`$(basename "$_f")\`"; done)
      fi
      if [ -z "$_WORK_LINES" ] && [ "$LINEAGE_EVENT_COUNT" -gt 0 ]; then
          _WORK_LINES="- Captured data-lineage provenance"
      fi
      # Last resort: generic summary
      if [ -z "$_WORK_LINES" ]; then
          _WORK_LINES="- Session: ${FILES_CHANGED} files changed over ${DURATION_MIN}m"
      fi
      _UNCOMMITTED_COUNT=$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
      _BRANCH_NOTE="Branch: \`${BRANCH}\`"
      [ "$_UNCOMMITTED_COUNT" -gt 0 ] && _BRANCH_NOTE="${_BRANCH_NOTE}, ${_UNCOMMITTED_COUNT} uncommitted changes"
      cat > "$_SESSION_FILE" << LAST_SESSION_EOF
## What was worked on
${_WORK_LINES}

## Current state
- ${_BRANCH_NOTE}
LAST_SESSION_EOF

      # All fallible transaction participants have accepted the session. Only
      # now publish final state in the log, which lets SessionStart distinguish
      # an accepted session from one that still needs a deterministic retry.
      ENDED=$(date +%Y-%m-%dT%H:%M:%S%z)
      sed -i.bak "s|^ended:.*|ended: ${ENDED}|" "$LOG_PATH" 2>/dev/null
      sed -i.bak "s|^duration_minutes:.*|duration_minutes: ${DURATION_MIN}|" "$LOG_PATH" 2>/dev/null
      sed -i.bak "s|^files_changed:.*|files_changed: ${FILES_CHANGED}|" "$LOG_PATH" 2>/dev/null
      rm -f "${LOG_PATH}.bak"

      FILE_LIST_MD=""
      if [ -n "$SESSION_CHANGED_FILES" ]; then
        FILE_LIST_MD=$(printf '%s\n' "$SESSION_CHANGED_FILES" | sed 's|^|- `|;s|$|`|')
      fi
      END_TIME_SHORT=$(date +%H:%M)
      if grep -q '^### .* — Session ended (' "$LOG_PATH" 2>/dev/null; then
        : # An interrupted accepted finalization is safe to retry idempotently.
      elif [ -n "$FILE_LIST_MD" ]; then
        FILE_SUMMARY=$(printf '%s\n' "$SESSION_CHANGED_FILES" | head -3 \
          | while IFS= read -r changed_path; do basename "$changed_path"; done \
          | tr '\n' ',' | sed 's/,$//; s/,/, /g')
        if [ "$FILES_CHANGED" -gt 3 ]; then
          FILE_SUMMARY="${FILE_SUMMARY} (+$((FILES_CHANGED - 3)) more)"
        fi
        printf "\n### %s — Session ended (%sm, %s files)\n- Modified: %s\n\n### Files Modified\n%s\n" "$END_TIME_SHORT" "$DURATION_MIN" "$FILES_CHANGED" "$FILE_SUMMARY" "$FILE_LIST_MD" >> "$LOG_PATH"
      else
        printf "\n### %s — Session ended (%sm, %s files)\n" "$END_TIME_SHORT" "$DURATION_MIN" "$FILES_CHANGED" >> "$LOG_PATH"
      fi

      # Clean up sentinels
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$SESSION_BASELINE_FILE"
    fi
  else
    # Log file doesn't exist (was deleted?) — clean up sentinels
    rm -f "$ACTIVE_LOG_FILE"
    if [[ "${ACTIVE_MARKER_VALID:-false}" == true ]]; then
      rm -f "$STATE_DIR/session-file-baseline.json"
    fi
  fi
fi

# Not in a git repo — nothing further to check
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# If no .living/ directory, skip (SessionStart hook handles scaffolding)
LIVING_DIR="$REPO_ROOT/.living"
if [ ! -d "$LIVING_DIR" ]; then
  rm -f "$STATE_DIR/living-reminder-baseline.json"
  mycelium_accept_lineage_session
  exit 0
fi

# Check if any work was done this session.
# Work detected by: mycelium-reminded.tmp (analysis or Edit/Write) or mycelium-session-activity.tmp
REMINDER_FILE="$STATE_DIR/mycelium-reminded.tmp"
ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
if [ ! -f "$REMINDER_FILE" ] && [ ! -f "$ACTIVITY_FILE" ]; then
  rm -f "$STATE_DIR/session-start-ts.tmp"
  rm -f "$STATE_DIR/session-file-baseline.json"
  rm -f "$STATE_DIR/living-reminder-baseline.json"
  mycelium_accept_lineage_session
  exit 0
fi

# Use reminder timestamp if available, otherwise session start timestamp
if [ -f "$REMINDER_FILE" ]; then
  WORK_TS=$(cat "$REMINDER_FILE")
elif [ -f "$STATE_DIR/session-start-ts.tmp" ]; then
  WORK_TS=$(cat "$STATE_DIR/session-start-ts.tmp")
else
  WORK_TS=0
fi

# Post-action hook fired. Check if .living/ was updated AFTER the reminder.
REMINDER_TS="$WORK_TS"

LIVING_UPDATED=false
LIVING_BASELINE_FILE="$STATE_DIR/living-reminder-baseline.json"
if [[ ! -f "$LIVING_BASELINE_FILE" ]]; then
  LIVING_BASELINE_FILE="$STATE_DIR/session-file-baseline.json"
fi
SESSION_CHANGES_SCRIPT="${MYCELIUM_SESSION_CHANGES_HELPER:-$SCRIPT_DIR/scripts/session_file_changes.py}"
if mycelium_living_changed \
  "$REPO_ROOT" "$LIVING_BASELINE_FILE" "$SESSION_CHANGES_SCRIPT" "$REMINDER_TS"; then
  LIVING_UPDATED=true
fi

# Build file context for triage instructions
FILE_COUNT=0
FILE_NAMES=""
if [ -f "$ACTIVITY_FILE" ]; then
  FILE_COUNT=$(sort -u "$ACTIVITY_FILE" | grep -c . || echo "0")
  FILE_NAMES=$(sort -u "$ACTIVITY_FILE" | head -15 | xargs -I {} basename {} 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
fi

# --- Session-end triage (short signals — full protocol is in the mycelium skill) ---

# If any was updated after the post-action hook fired, protocol was followed
if [ "$LIVING_UPDATED" = true ]; then
  # Clean up reminder file — cycle complete
  rm -f "$REMINDER_FILE"
  rm -f "$ACTIVITY_FILE"
  rm -f "$STATE_DIR/session-start-ts.tmp"
  rm -f "$STATE_DIR/session-file-baseline.json"
  rm -f "$STATE_DIR/living-reminder-baseline.json"

  ENHANCE_MSG=".living/ updated. Enhance .mycelium/last-session.md with work, decisions, blockers, current state, and next steps. The deterministic LOG_REGISTRY summary is already in place."
  mycelium_emit_context "Stop" "$ENHANCE_MSG"
  mycelium_accept_lineage_session
  exit 0
fi

# Block: work happened but .living/ was never updated
REASON="STOP BLOCKED — ${FILE_COUNT} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update last-session.md."

ESCAPED_REASON=$(printf '%s' "$REASON" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
printf '{"decision": "block", "reason": %s}\n' "$ESCAPED_REASON"
