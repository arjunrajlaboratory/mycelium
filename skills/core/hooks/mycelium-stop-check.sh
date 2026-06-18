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

# Read stdin JSON
INPUT=$(cat)

# Prevent infinite recursion: if stop_hook_active is set, let it through
STOP_HOOK_ACTIVE=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('stop_hook_active', False)).lower())" 2>/dev/null || echo "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

# Determine repo root early (used by both log finalization and .living/ checks)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")

# Resolve this hook's mycelium-core dir once, in absolute form. Used to locate
# the upsert script and the log-scribe template. BASH_SOURCE may be unset in
# weird invocations (e.g. `sh -c "$(...)"`), so fall back to $0; if even that
# fails, leave SCRIPT_DIR empty and downstream existence checks will skip.
HOOK_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR=$(cd "$(dirname "$(dirname "$HOOK_SOURCE")")" 2>/dev/null && pwd || echo "")
UPSERT_SCRIPT="$SCRIPT_DIR/scripts/upsert_registry_row.py"
TEMPLATE_FILE="$SCRIPT_DIR/templates/log-scribe-prompt.md"

# --- Session log finalization ---
ACTIVE_LOG_FILE="$REPO_ROOT/.claude/active-session-log.tmp"
if [ -n "$REPO_ROOT" ] && [ -f "$ACTIVE_LOG_FILE" ]; then
  LOG_PATH=$(head -1 "$ACTIVE_LOG_FILE")
  OWNER_TS=$(sed -n '2p' "$ACTIVE_LOG_FILE" 2>/dev/null || echo "")
  OUR_TS=$(cat "$REPO_ROOT/.claude/session-start-ts.tmp" 2>/dev/null || echo "")

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
    START_FILE="$LOG_REPO/.claude/session-start-ts.tmp"
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

    # Compute files changed since session start (committed + uncommitted + staged + activity tracker)
    FILES_CHANGED=0
    FILES_CHANGED_UNCOMMITTED=$({ git -C "$LOG_REPO" diff --name-only 2>/dev/null || true; } | wc -l | tr -d ' ')
    FILES_CHANGED_STAGED=$({ git -C "$LOG_REPO" diff --cached --name-only 2>/dev/null || true; } | wc -l | tr -d ' ')
    FILES_CHANGED_COMMITTED=0
    if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
      FILES_CHANGED_COMMITTED=$({ git -C "$LOG_REPO" log --since="@${START_TS}" --name-only --pretty=format: 2>/dev/null || true; } | sort -u | { grep -v '^$' || true; } | wc -l | tr -d ' ')
    fi
    FILES_CHANGED=$((FILES_CHANGED_UNCOMMITTED + FILES_CHANGED_STAGED + FILES_CHANGED_COMMITTED))
    # Also count activity-tracked files (Edit/Write operations not yet in git)
    ACTIVITY_FILE_CHECK="$LOG_REPO/.claude/mycelium-session-activity.tmp"
    if [ -f "$ACTIVITY_FILE_CHECK" ] && [ "$FILES_CHANGED" -eq 0 ]; then
      FILES_CHANGED=$(sort -u "$ACTIVITY_FILE_CHECK" | grep -c . || echo "0")
    fi

    # Compute session-local activity: Edit/Write (activity tracker), commits, OR Bash-mutated files
    # (files in `git status` whose mtime is newer than session start). The mtime signal catches
    # `sed -i`, `perl -pi`, formatters, and any other Bash mutation that bypasses the Edit/Write
    # activity tracker. Without it we incorrectly discard sessions that did real work via Bash.
    ACTIVITY_COUNT=0
    if [ -f "$ACTIVITY_FILE_CHECK" ]; then
      ACTIVITY_COUNT=$(sort -u "$ACTIVITY_FILE_CHECK" | grep -c . 2>/dev/null || echo "0")
    fi
    COMMITS_THIS_SESSION=0
    UNCOMMITTED_RECENT=0
    if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
      COMMITS_THIS_SESSION=$({ git -C "$LOG_REPO" log --since="@${START_TS}" --oneline 2>/dev/null || true; } | grep -c . || echo "0")
      # Files in `git status` whose mtime is > session start. `awk 'NF{print $NF}'` extracts
      # the filename (last whitespace-separated token), handling `M  filename` and `?? filename`.
      # Renames (`R  oldname -> newname`) collapse to `newname`, which is the right behavior.
      while IFS= read -r f; do
        [ -z "$f" ] && continue
        if [ -e "$LOG_REPO/$f" ]; then
          FILE_MTIME=$(stat -f "%m" "$LOG_REPO/$f" 2>/dev/null || stat -c "%Y" "$LOG_REPO/$f" 2>/dev/null || echo "0")
          if [ "$FILE_MTIME" -gt "$START_TS" ] 2>/dev/null; then
            UNCOMMITTED_RECENT=$((UNCOMMITTED_RECENT + 1))
          fi
        fi
      done < <(git -C "$LOG_REPO" status --porcelain 2>/dev/null | awk 'NF{print $NF}')
    fi

    # Short session check: skip finalization only if NO evidence of work in any signal.
    # Duration is irrelevant — a long session that only read files is still noise.
    if [ "$ACTIVITY_COUNT" -eq 0 ] && [ "$COMMITS_THIS_SESSION" -eq 0 ] && [ "$UNCOMMITTED_RECENT" -eq 0 ]; then
      rm -f "$LOG_PATH"
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$REPO_ROOT/.claude/session-start-ts.tmp"
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

      # Append file list from activity tracker or git diff
      ACTIVITY_FILE="$LOG_REPO/.claude/mycelium-session-activity.tmp"
      FILE_LIST_MD=""
      GIT_FILES=""
      if [ -f "$ACTIVITY_FILE" ]; then
        FILE_LIST_MD=$(sort -u "$ACTIVITY_FILE" | sed 's|^|- `|;s|$|`|')
      fi
      if [ -z "$FILE_LIST_MD" ]; then
        # Fallback to git diff
        GIT_FILES=$(git -C "$LOG_REPO" diff --name-only HEAD 2>/dev/null || echo "")
        if [ -n "$GIT_FILES" ]; then
          FILE_LIST_MD=$(echo "$GIT_FILES" | sed 's|^|- `|;s|$|`|')
        fi
      fi
      # Append a timestamped session-end entry (health hook extracts this for next-session context)
      END_TIME_SHORT=$(date +%H:%M)
      if [ -n "$FILE_LIST_MD" ]; then
        # Build a readable summary for the timestamped entry
        FILE_SUMMARY=""
        if [ -f "$ACTIVITY_FILE" ]; then
          FILE_SUMMARY=$(sort -u "$ACTIVITY_FILE" | head -3 | xargs -I {} basename {} 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
        fi
        if [ -z "$FILE_SUMMARY" ] && [ -n "$GIT_FILES" ]; then
          FILE_SUMMARY=$(echo "$GIT_FILES" | head -3 | xargs -I {} basename {} 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
        fi
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
      # Summary from first 3 unique files
      SUMMARY=""
      if [ -f "$ACTIVITY_FILE" ]; then
        SUMMARY=$(sort -u "$ACTIVITY_FILE" | head -3 | xargs -I {} basename {} 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
        UNIQUE_COUNT=$(sort -u "$ACTIVITY_FILE" | grep -c . || echo "0")
        if [ "$UNIQUE_COUNT" -gt 3 ]; then
          SUMMARY="${SUMMARY} (+$((UNIQUE_COUNT - 3)) more)"
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

      # Deterministic fallback Summary: commit subjects since session start.
      # Runs in milliseconds, no LLM, no dependency. The haiku call (if available)
      # will upgrade this to a semantic Summary; if not, this is what stays.
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
      _SESSION_FILE="$REPO_ROOT/.claude/last-session.md"
      _WORK_LINES=""
      # Try recent commit messages first
      if [ -n "${START_TS:-}" ]; then
          _WORK_LINES=$(git -C "$REPO_ROOT" log --since="@${START_TS}" --pretty=format:"- %s" 2>/dev/null | head -10)
      fi
      # Fall back to modified file list
      if [ -z "$_WORK_LINES" ] && [ -f "$ACTIVITY_FILE" ]; then
          _WORK_LINES=$(sort -u "$ACTIVITY_FILE" | head -10 | while read -r _f; do echo "- Modified \`$(basename "$_f")\`"; done)
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
      rm -f "$REPO_ROOT/.claude/session-start-ts.tmp"
    fi
  else
    # Log file doesn't exist (was deleted?) — clean up sentinels
    rm -f "$ACTIVE_LOG_FILE"
    rm -f "$REPO_ROOT/.claude/session-start-ts.tmp"
  fi
fi

# Not in a git repo — nothing further to check
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# If no .living/ directory, skip (SessionStart hook handles scaffolding)
LIVING_DIR="$REPO_ROOT/.living"
if [ ! -d "$LIVING_DIR" ]; then
  exit 0
fi

# Check if any work was done this session.
# Work detected by: mycelium-reminded.tmp (analysis or Edit/Write) or mycelium-session-activity.tmp
REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
ACTIVITY_FILE="$REPO_ROOT/.claude/mycelium-session-activity.tmp"
if [ ! -f "$REMINDER_FILE" ] && [ ! -f "$ACTIVITY_FILE" ]; then
  exit 0
fi

# Use reminder timestamp if available, otherwise session start timestamp
if [ -f "$REMINDER_FILE" ]; then
  WORK_TS=$(cat "$REMINDER_FILE")
elif [ -f "$REPO_ROOT/.claude/session-start-ts.tmp" ]; then
  WORK_TS=$(cat "$REPO_ROOT/.claude/session-start-ts.tmp")
else
  WORK_TS=0
fi

# Post-action hook fired. Check if .living/ was updated AFTER the reminder.
REMINDER_TS="$WORK_TS"

LEARNINGS_UPDATED=false
DECISIONS_UPDATED=false
CONVENTIONS_UPDATED=false

if [ -f "$LIVING_DIR/learnings.md" ]; then
  LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  if [ "$LEARNINGS_MTIME" -gt "$REMINDER_TS" ]; then
    LEARNINGS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/decisions.md" ]; then
  DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  if [ "$DECISIONS_MTIME" -gt "$REMINDER_TS" ]; then
    DECISIONS_UPDATED=true
  fi
fi

if [ -f "$LIVING_DIR/conventions.md" ]; then
  CONVENTIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/conventions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/conventions.md" 2>/dev/null || echo "0")
  if [ "$CONVENTIONS_MTIME" -gt "$REMINDER_TS" ]; then
    CONVENTIONS_UPDATED=true
  fi
fi

FINDINGS_UPDATED=false
FINDINGS_DIR="$LIVING_DIR/findings"
if [ -d "$FINDINGS_DIR" ]; then
  FINDINGS_MTIME=$(stat -f "%m" "$FINDINGS_DIR" 2>/dev/null || stat -c "%Y" "$FINDINGS_DIR" 2>/dev/null || echo "0")
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
  # Try to spawn a background haiku log-scribe to upgrade the deterministic Summary
  # to a semantic one. Completely silent — the main agent never sees a prompt,
  # the Stop hook returns in 0s, the haiku writes the row when it finishes.
  # Falls through gracefully if `claude` is not on PATH.
  SCRIBE_DISPATCHED=false
  if [ -f "$TEMPLATE_FILE" ] && [ -n "${SESSION_ID:-}" ]; then
    # Probe for the claude CLI. Try PATH first, then common install locations.
    CLAUDE_BIN=""
    for candidate in \
      "$(command -v claude 2>/dev/null)" \
      "/Applications/cmux.app/Contents/Resources/bin/claude" \
      "/opt/homebrew/bin/claude" \
      "/usr/local/bin/claude" \
      "$HOME/.local/bin/claude"; do
      if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        CLAUDE_BIN="$candidate"
        break
      fi
    done

    if [ -n "$CLAUDE_BIN" ]; then
      START_TS_ISO=$(date -r "${START_TS:-0}" -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -d "@${START_TS:-0}" -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")
      TODAY=$(date +%Y-%m-%d)
      SCRIBE_PROMPT=$(SESSION_ID="$SESSION_ID" \
                      PROJECT_SLUG="$PROJECT_SLUG" \
                      LOG_PATH="$LOG_PATH" \
                      REGISTRY_PATH="$LOG_DIR/LOG_REGISTRY.md" \
                      REPO_ROOT="$REPO_ROOT" \
                      START_TS_ISO="$START_TS_ISO" \
                      DURATION_MIN="$DURATION_MIN" \
                      FILES_CHANGED="$FILES_CHANGED" \
                      BRANCH="$BRANCH" \
                      DATE="$TODAY" \
                      UPSERT_SCRIPT="$UPSERT_SCRIPT" \
                      TEMPLATE_FILE="$TEMPLATE_FILE" \
                      python3 - <<'PY'
import os, sys
text = open(os.environ["TEMPLATE_FILE"], encoding="utf-8").read()
keys = ["SESSION_ID","PROJECT_SLUG","LOG_PATH","REGISTRY_PATH","REPO_ROOT",
        "START_TS_ISO","DURATION_MIN","FILES_CHANGED","BRANCH","DATE","UPSERT_SCRIPT"]
for k in keys:
    text = text.replace("{{" + k + "}}", os.environ.get(k, ""))
sys.stdout.write(text)
PY
)
      SCRIBE_RUN_LOG="$LOG_DIR/.log-scribe-${SESSION_ID}.log"
      # Spawn detached. No budget cap — the scribe must run to completion to
      # write the ## Session Summary section into the log file (consumed by the
      # knowledge graph) AND upsert the registry row. Cap removed 2026-06-18
      # after run-logs showed "Error: Exceeded USD budget (0.05)" on every dispatch.
      nohup "$CLAUDE_BIN" -p "$SCRIBE_PROMPT" \
        --model claude-haiku-4-5 \
        --output-format text \
        --dangerously-skip-permissions \
        >"$SCRIBE_RUN_LOG" 2>&1 </dev/null &
      disown 2>/dev/null || true
      SCRIBE_DISPATCHED=true
    fi
  fi

  # Clean up reminder file — cycle complete
  rm -f "$REMINDER_FILE"
  rm -f "$ACTIVITY_FILE"

  # Emit a small additionalContext: enhance .claude/last-session.md. If the
  # scribe couldn't be dispatched (claude not on PATH), include a fallback
  # instruction to update the registry row by hand.
  if [ "$SCRIBE_DISPATCHED" = true ]; then
    ENHANCE_MSG=".living/ updated. Enhance .claude/last-session.md (5 sections: work, decisions, blockers, state, next steps). Log-scribe is running in the background — the LOG_REGISTRY row will be upgraded automatically."
  else
    ENHANCE_MSG=".living/ updated. Enhance .claude/last-session.md (5 sections: work, decisions, blockers, state, next steps). Note: claude CLI not on PATH so log-scribe could not auto-dispatch; the deterministic Summary from commit subjects is in place. If you want a richer Summary, dispatch a haiku log-scribe subagent by hand or set PATH so the Stop hook can find claude."
  fi
  ESCAPED_ENHANCE=$(printf '%s' "$ENHANCE_MSG" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
  printf '{"additionalContext": %s}\n' "$ESCAPED_ENHANCE"
  exit 0
fi

# Debounce: if work started less than 5 minutes ago, don't block yet — session is likely still active
NOW_TS_CHECK=$(date +%s)
WORK_AGE=$(( NOW_TS_CHECK - WORK_TS ))
if [ "$WORK_AGE" -lt 300 ]; then
  # Work is < 5 min old — session likely still in progress, don't block
  exit 0
fi

# Block: work happened but .living/ was never updated
REASON="STOP BLOCKED — ${FILE_COUNT} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update last-session.md."

ESCAPED_REASON=$(printf '%s' "$REASON" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
printf '{"decision": "block", "reason": %s}\n' "$ESCAPED_REASON"
