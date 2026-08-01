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
mycelium_prepare_state_dir "$REPO_ROOT"

# Accepting Stop owns final cleanup of data-lineage state. The lineage hook
# runs first and may be followed by a blocking decision here; retaining both
# files across that block lets later analysis append to the same manifest.
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
UPSERT_SCRIPT="$SCRIPT_DIR/scripts/upsert_registry_row.py"

# --- Session log finalization ---
ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
if [ -n "$REPO_ROOT" ] && [ -f "$ACTIVE_LOG_FILE" ]; then
  LOG_PATH=$(head -1 "$ACTIVE_LOG_FILE")
  OWNER_TS=$(sed -n '2p' "$ACTIVE_LOG_FILE" 2>/dev/null || echo "")
  OUR_TS=$(cat "$STATE_DIR/session-start-ts.tmp" 2>/dev/null || echo "")

  # Subagent detection: if owner timestamp exists and doesn't match ours, we're a subagent
  if [ -n "$OWNER_TS" ] && [ -n "$OUR_TS" ] && [ "$OWNER_TS" != "$OUR_TS" ]; then
      # Subagent: skip all finalization and .living/ checks
      # File activity is tracked in the shared activity file for the primary session
      exit 0
  fi

  if [ -f "$LOG_PATH" ]; then
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

    # Short session check: skip finalization only if NO evidence of work in any signal.
    # Duration is irrelevant — a long session that only read files is still noise.
    if [ "$ACTIVITY_COUNT" -eq 0 ] && [ "$REMINDER_COUNT" -eq 0 ] && [ "$FILES_CHANGED" -eq 0 ]; then
      rm -f "$LOG_PATH"
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$STATE_DIR/session-start-ts.tmp"
      rm -f "$SESSION_BASELINE_FILE"
      # No registry row, no finalization — clean exit (noise session)
    else
      # Auto-finalize the session log (factual record — no Claude needed)
      LOG_DIR=$(dirname "$LOG_PATH")
      ENDED=$(date +%Y-%m-%dT%H:%M:%S%z)

      # Update frontmatter in-place using sed
      sed -i.bak "s|^ended:.*|ended: ${ENDED}|" "$LOG_PATH" 2>/dev/null
      sed -i.bak "s|^duration_minutes:.*|duration_minutes: ${DURATION_MIN}|" "$LOG_PATH" 2>/dev/null
      sed -i.bak "s|^files_changed:.*|files_changed: ${FILES_CHANGED}|" "$LOG_PATH" 2>/dev/null
      rm -f "${LOG_PATH}.bak"

      # Append the same de-duplicated file set used for files_changed.
      ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
      FILE_LIST_MD=""
      if [ -n "$SESSION_CHANGED_FILES" ]; then
        FILE_LIST_MD=$(printf '%s\n' "$SESSION_CHANGED_FILES" | sed 's|^|- `|;s|$|`|')
      fi
      # Append a timestamped session-end entry (health hook extracts this for next-session context)
      END_TIME_SHORT=$(date +%H:%M)
      if [ -n "$FILE_LIST_MD" ]; then
        # Build a readable summary for the timestamped entry
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

      # Append to LOG_REGISTRY.md
      PROJECT_SLUG=$({ grep '^project:' "$LOG_PATH" || echo "project: unknown"; } | sed 's/^project: *//')
      SESSION_ID=$({ grep '^session_id:' "$LOG_PATH" || echo "session_id: unknown"; } | sed 's/^session_id: *//')
      BRANCH=$({ grep '^branch:' "$LOG_PATH" || echo "branch: unknown"; } | sed 's/^branch: *//')
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
      # Atomic upsert via the script resolved at the top of this hook ($UPSERT_SCRIPT).
      NEW_ROW="| $(date +%Y-%m-%d) | ${SESSION_ID} | ${PROJECT_SLUG} | ${BRANCH} | ${DURATION_MIN}m | ${FILES_CHANGED} | ${SUMMARY} | | complete | | [log](${SESSION_ID}-${PROJECT_SLUG}.md) |"
      if [ -f "$LOG_DIR/LOG_REGISTRY.md" ]; then
        if [ -f "$UPSERT_SCRIPT" ]; then
          # If the script rejects (e.g. wrong pipe count), the error stays in
          # .upsert_registry_row.err for operator debugging. Do NOT echo the
          # row on rejection — that would defeat the validation the script
          # exists to perform.
          python3 "$UPSERT_SCRIPT" "$LOG_DIR/LOG_REGISTRY.md" "$SESSION_ID" "$NEW_ROW" \
            >/dev/null 2>"$LOG_DIR/.upsert_registry_row.err" \
            && rm -f "$LOG_DIR/.upsert_registry_row.err"
        else
          # Script missing entirely — fall back to plain append so we don't lose the row.
          echo "$NEW_ROW" >> "$LOG_DIR/LOG_REGISTRY.md"
        fi
      fi

      # Deterministic Summary: commit subjects since session start.
      # Runs in milliseconds with no model or provider dependency.
      if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
        DETERMINISTIC_SUMMARY=$(git -C "$LOG_REPO" log --since="@${START_TS}" --pretty=format:'%s' 2>/dev/null \
          | head -3 | tr '\n' ';' | sed 's/;$//; s/;/; /g')
        # Cap at 200 chars
        if [ ${#DETERMINISTIC_SUMMARY} -gt 200 ]; then
          DETERMINISTIC_SUMMARY="${DETERMINISTIC_SUMMARY:0:197}..."
        fi
        if [ -n "$DETERMINISTIC_SUMMARY" ]; then
          # Re-upsert the row with the deterministic Summary. Same row, better Summary.
          NEW_ROW_DET="| $(date +%Y-%m-%d) | ${SESSION_ID} | ${PROJECT_SLUG} | ${BRANCH} | ${DURATION_MIN}m | ${FILES_CHANGED} | ${DETERMINISTIC_SUMMARY} | | complete | | [log](${SESSION_ID}-${PROJECT_SLUG}.md) |"
          if [ -f "$UPSERT_SCRIPT" ]; then
            python3 "$UPSERT_SCRIPT" "$LOG_DIR/LOG_REGISTRY.md" "$SESSION_ID" "$NEW_ROW_DET" >/dev/null 2>&1 || true
          fi
        fi
      fi

      # Auto-write last-session.md for next session context
      _SESSION_FILE="$STATE_DIR/last-session.md"
      _WORK_LINES=""
      # Try recent commit messages first
      if [ -n "${START_TS:-}" ]; then
          _WORK_LINES=$(git -C "$REPO_ROOT" log --since="@${START_TS}" --pretty=format:"- %s" 2>/dev/null | head -10)
      fi
      # Fall back to the de-duplicated modified file list.
      if [ -z "$_WORK_LINES" ] && [ -n "$SESSION_CHANGED_FILES" ]; then
          _WORK_LINES=$(printf '%s\n' "$SESSION_CHANGED_FILES" | head -10 | while IFS= read -r _f; do echo "- Modified \`$(basename "$_f")\`"; done)
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

      # Clean up sentinels
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$SESSION_BASELINE_FILE"
    fi
  else
    # Log file doesn't exist (was deleted?) — clean up sentinels
    rm -f "$ACTIVE_LOG_FILE"
    rm -f "$STATE_DIR/session-file-baseline.json"
  fi
fi

# Not in a git repo — nothing further to check
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# If no .living/ directory, skip (SessionStart hook handles scaffolding)
LIVING_DIR="$REPO_ROOT/.living"
if [ ! -d "$LIVING_DIR" ]; then
  mycelium_accept_lineage_session
  exit 0
fi

# Check if any work was done this session.
# Work detected by: mycelium-reminded.tmp (analysis or Edit/Write) or mycelium-session-activity.tmp
REMINDER_FILE="$STATE_DIR/mycelium-reminded.tmp"
ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
if [ ! -f "$REMINDER_FILE" ] && [ ! -f "$ACTIVITY_FILE" ]; then
  rm -f "$STATE_DIR/session-start-ts.tmp"
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

LEARNINGS_UPDATED=false
DECISIONS_UPDATED=false
CONVENTIONS_UPDATED=false

if [ -f "$LIVING_DIR/learnings.md" ]; then
  LEARNINGS_MTIME=$(mycelium_file_mtime "$LIVING_DIR/learnings.md")
  if [ "$LEARNINGS_MTIME" -gt "$REMINDER_TS" ]; then
    LEARNINGS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/decisions.md" ]; then
  DECISIONS_MTIME=$(mycelium_file_mtime "$LIVING_DIR/decisions.md")
  if [ "$DECISIONS_MTIME" -gt "$REMINDER_TS" ]; then
    DECISIONS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/conventions.md" ]; then
  CONVENTIONS_MTIME=$(mycelium_file_mtime "$LIVING_DIR/conventions.md")
  if [ "$CONVENTIONS_MTIME" -gt "$REMINDER_TS" ]; then
    CONVENTIONS_UPDATED=true
  fi
fi

FINDINGS_UPDATED=false
FINDINGS_DIR="$LIVING_DIR/findings"
if [ -d "$FINDINGS_DIR" ]; then
  FINDINGS_MTIME=$(mycelium_file_mtime "$FINDINGS_DIR")
  if [ "$FINDINGS_MTIME" -gt "$REMINDER_TS" ]; then
    FINDINGS_UPDATED=true
  fi
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
if [ "$LEARNINGS_UPDATED" = true ] || [ "$DECISIONS_UPDATED" = true ] || [ "$CONVENTIONS_UPDATED" = true ] || [ "$FINDINGS_UPDATED" = true ]; then
  # Clean up reminder file — cycle complete
  rm -f "$REMINDER_FILE"
  rm -f "$ACTIVITY_FILE"
  rm -f "$STATE_DIR/session-start-ts.tmp"

  ENHANCE_MSG=".living/ updated. Enhance .mycelium/last-session.md with work, decisions, blockers, current state, and next steps. The deterministic LOG_REGISTRY summary is already in place."
  mycelium_emit_context "Stop" "$ENHANCE_MSG"
  mycelium_accept_lineage_session
  exit 0
fi

# Block: work happened but .living/ was never updated
REASON="STOP BLOCKED — ${FILE_COUNT} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update last-session.md."

ESCAPED_REASON=$(printf '%s' "$REASON" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
printf '{"decision": "block", "reason": %s}\n' "$ESCAPED_REASON"
