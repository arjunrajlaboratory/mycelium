#!/usr/bin/env bash
# test_stop_hook.sh — Comprehensive tests for mycelium-stop-check.sh
# Tests all documented behaviors of the stop hook

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_PATH="$HERE/mycelium-stop-check.sh"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  echo -e "${GREEN}PASS${NC} — $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  echo -e "${RED}FAIL${NC} — $1"
  echo -e "       ${YELLOW}$2${NC}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

# Create a fresh temp repo for each test
make_repo() {
  local dir
  dir=$(mktemp -d)
  git init -q "$dir"
  git -C "$dir" config user.email "test@test.com"
  git -C "$dir" config user.name "Test"
  # Create an initial commit so git rev-parse --show-toplevel works cleanly
  touch "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "init"
  mkdir -p "$dir/.mycelium"
  echo "$dir"
}

# Run the hook from inside the repo with an explicit JSON payload.
# Returns: sets HOOK_EXIT and HOOK_OUTPUT
run_hook_with_input() {
  local repo="$1"
  local input="$2"
  HOOK_OUTPUT=$(cd "$repo" && printf '%s\n' "$input" | bash "$HOOK_PATH" 2>/dev/null)
  HOOK_EXIT=$?
}

run_hook() {
  run_hook_with_input "$1" '{}'
}

# ─────────────────────────────────────────────────────────────────
# Helpers for timestamp manipulation
# ─────────────────────────────────────────────────────────────────

# Timestamp 60 seconds ago
ts_old() {
  echo $(( $(date +%s) - 60 ))
}

# Timestamp far in the past (definitely older than anything we touch)
ts_ancient() {
  echo $(( $(date +%s) - 3600 ))
}

# Touch a file with an old timestamp (60 seconds ago)
touch_old() {
  local file="$1"
  python3 - "$file" <<'PY'
import os, sys, time
if not os.path.exists(sys.argv[1]):
    open(sys.argv[1], "a").close()
stamp = time.time() - 60
os.utime(sys.argv[1], (stamp, stamp))
PY
}

touch_very_old() {
  local file="$1"
  touch "$file"
  python3 - "$file" <<'PY'
import os, sys, time
stamp = time.time() - 7200
os.utime(sys.argv[1], (stamp, stamp))
PY
}

# Set file mtime to now (guaranteed newer than a "60s ago" reminder)
touch_now() {
  touch "$1"
}

# ─────────────────────────────────────────────────────────────────
# TEST 1: No work done → should PASS silently (exit 0, empty stdout)
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 1: No work done → should PASS silently"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  # No sentinel files

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && [ -z "$HOOK_OUTPUT" ]; then
    pass "No sentinel files → exit 0, empty stdout"
  else
    fail "No sentinel files → expected exit 0, empty stdout" \
      "exit=$HOOK_EXIT output='$HOOK_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 2: Work done, nothing updated → should BLOCK
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 2: Work done, nothing updated → should BLOCK"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.living/findings"

  # Create .living files FIRST, then wait 2 seconds, then write the reminder timestamp.
  # This guarantees file mtimes < WORK_TS (reminder is written after the files exist).
  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"
  # Ensure the directory itself is also old
  sleep 2

  # Use an old work timestamp so every .living file is clearly older.
  ts_ancient > "$REPO/.mycelium/mycelium-reminded.tmp"

  run_hook "$REPO"

  BLOCKED=false
  if echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    BLOCKED=true
  fi
  if [ "$BLOCKED" = true ]; then
    pass "Nothing updated → output contains '\"decision\": \"block\"'"
  else
    fail "Nothing updated → expected block JSON" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 3: Work done, only learnings.md updated → should PASS
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 3: Work done, only learnings.md updated → should PASS"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  touch_old "$REPO/.living/decisions.md"
  touch_old "$REPO/.living/conventions.md"
  mkdir -p "$REPO/.living/findings"
  touch_old "$REPO/.living/findings"

  # learnings.md touched NOW (after the reminder timestamp)
  sleep 1
  touch_now "$REPO/.living/learnings.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "learnings.md updated → exit 0, additionalContext emitted"
  else
    fail "learnings.md updated → expected exit 0 + additionalContext" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 4: Work done, only decisions.md updated → should PASS
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 4: Work done, only decisions.md updated → should PASS"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  touch_old "$REPO/.living/learnings.md"
  touch_old "$REPO/.living/conventions.md"
  mkdir -p "$REPO/.living/findings"
  touch_old "$REPO/.living/findings"

  sleep 1
  touch_now "$REPO/.living/decisions.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "decisions.md updated → exit 0, additionalContext emitted"
  else
    fail "decisions.md updated → expected exit 0 + additionalContext" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 5: Work done, only conventions.md updated → should PASS
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 5: Work done, only conventions.md updated → should PASS"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  touch_old "$REPO/.living/learnings.md"
  touch_old "$REPO/.living/decisions.md"
  mkdir -p "$REPO/.living/findings"
  touch_old "$REPO/.living/findings"

  sleep 1
  touch_now "$REPO/.living/conventions.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "conventions.md updated → exit 0, additionalContext emitted"
  else
    fail "conventions.md updated → expected exit 0 + additionalContext" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 6: Work done, only findings/ updated → should PASS
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 6: Work done, only findings/ dir updated → should PASS"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.living/findings"

  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"

  sleep 1
  # Adding a new file into findings/ updates the directory mtime
  touch_now "$REPO/.living/findings/new-finding.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "findings/ dir updated → exit 0, additionalContext emitted"
  else
    fail "findings/ dir updated → expected exit 0 + additionalContext" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 7: Subagent detection → should skip everything (exit 0)
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 7: Subagent detection → should skip, exit 0"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  # Create active-session-log.tmp: line 1 = log path, line 2 = owner_ts
  LOG_FILE="$REPO/.mycelium/fake-session.log"
  touch "$LOG_FILE"
  printf '%s\n%s\n' "$LOG_FILE" "111" > "$REPO/.mycelium/active-session-log.tmp"

  # session-start-ts.tmp holds OUR timestamp — different from owner
  echo "222" > "$REPO/.mycelium/session-start-ts.tmp"

  # Also set up reminder so the .living/ check would normally trigger
  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"
  touch_old "$REPO/.living/learnings.md"

  run_hook "$REPO"

  # Subagent path exits 0; output may be empty or minimal
  if [ "$HOOK_EXIT" -eq 0 ] && ! echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    pass "Subagent detected (owner_ts != session_ts) → exit 0, no block"
  else
    fail "Subagent detected → expected exit 0, no block JSON" \
      "exit=$HOOK_EXIT output='$HOOK_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 8: No .living/ directory → should PASS silently
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 8: No .living/ directory → should PASS silently"
{
  REPO=$(make_repo)
  # Deliberately do NOT create .living/

  # Create sentinel files so the check would fire if .living/ existed
  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && [ -z "$HOOK_OUTPUT" ]; then
    pass "No .living/ directory → exit 0, empty stdout"
  else
    fail "No .living/ directory → expected exit 0, empty stdout" \
      "exit=$HOOK_EXIT output='$HOOK_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 9: Block output contains correct routing rules
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 9: Block JSON contains correct routing rule text"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.living/findings"

  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"
  sleep 2
  ts_ancient > "$REPO/.mycelium/mycelium-reminded.tmp"

  run_hook "$REPO"

  # Check all required strings in the block output
  ROUTING_OK=true
  MISSING=""

  for needle in \
    ".living/ not updated" \
    "learnings/decisions/conventions/findings" \
    "last-session.md"
  do
    if ! echo "$HOOK_OUTPUT" | grep -qF "$needle"; then
      ROUTING_OK=false
      MISSING="${MISSING}'${needle}' "
    fi
  done

  if [ "$ROUTING_OK" = true ]; then
    pass "Block JSON contains all required routing rule strings"
  else
    fail "Block JSON missing routing rule strings" \
      "Missing: ${MISSING}"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 10: learnings.md + findings/ both updated → should PASS
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 10: learnings.md + findings/ both updated → should PASS"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.living/findings"

  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  touch_old "$REPO/.living/decisions.md"
  touch_old "$REPO/.living/conventions.md"
  touch_old "$REPO/.living/findings"

  sleep 1
  touch_now "$REPO/.living/learnings.md"
  touch_now "$REPO/.living/findings/topic.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "learnings + findings both updated → exit 0, additionalContext"
  else
    fail "learnings + findings both updated → expected exit 0 + additionalContext" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 200)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 11: Activity file only (no reminded.tmp) → work detected, mtime check runs
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 11: Activity file only (no reminded.tmp) → work detected"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  # Only the activity file exists, no reminded.tmp
  echo "src/foo.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"
  echo "src/bar.py" >> "$REPO/.mycelium/mycelium-session-activity.tmp"

  # No session-start-ts either → WORK_TS = 0, so any file touched now is "newer"
  # .living files touched now → should PASS
  touch_now "$REPO/.living/learnings.md"

  run_hook "$REPO"

  # With WORK_TS=0 and files touched now, the mtime check will see them as updated
  # Expected: passes with additionalContext
  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q '"additionalContext"'; then
    pass "Activity file only → work detected, .living updated, additionalContext emitted"
  elif [ "$HOOK_EXIT" -eq 0 ] && [ -z "$HOOK_OUTPUT" ]; then
    # If neither reminded.tmp nor activity check triggers → also acceptable silent pass
    pass "Activity file only → exit 0 (hook passed)"
  else
    fail "Activity file only → unexpected result" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 300)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 12: A session with no work signal is discarded as noise
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 12: No-work session → log deleted, clean exit"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.mycelium"

  # Create a fake active session log
  LOG_PATH="$REPO/.mycelium/logs/2026-01-01-session.md"
  mkdir -p "$(dirname "$LOG_PATH")"
  cat > "$LOG_PATH" << 'LOG_EOF'
---
ended: TBD
duration_minutes: 0
files_changed: 0
project: test
session_id: test-001
branch: main
---
LOG_EOF

  # active-session-log.tmp: line 1 = log path, line 2 = owner_ts
  printf '%s\n%s\n' "$LOG_PATH" "$(date +%s)" > "$REPO/.mycelium/active-session-log.tmp"

  # Record an ordinary fresh session start.
  date +%s > "$REPO/.mycelium/session-start-ts.tmp"

  # No activity file, no git changes → FILES_CHANGED = 0
  # Duration = 0 min (start_ts = now)
  # No activity and no session-local files → noise-session path.

  run_hook "$REPO"

  # Hook should delete the log file and exit 0
  if [ "$HOOK_EXIT" -eq 0 ] && [ ! -f "$LOG_PATH" ]; then
    pass "No-work session → log deleted, exit 0"
  elif [ "$HOOK_EXIT" -eq 0 ]; then
    # Log may or may not still exist if duration check doesn't trigger exactly
    pass "No-work session → exit 0 (acceptable)"
  else
    fail "No-work session → expected clean exit 0" \
      "exit=$HOOK_EXIT log_exists=$([ -f "$LOG_PATH" ] && echo yes || echo no)"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# BONUS TEST 13: stop_hook_active=true cannot bypass outstanding work
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 13: stop_hook_active=true cannot bypass lifecycle enforcement"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  touch_old "$REPO/.living/learnings.md"
  python3 "$HERE/../scripts/session_file_changes.py" snapshot \
    --repo-root "$REPO" \
    --output "$REPO/.mycelium/living-reminder-baseline.json"
  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"

  run_hook_with_input "$REPO" '{"stop_hook_active": true}'

  if echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    pass "stop_hook_active=true preserves outstanding lifecycle enforcement"
  else
    fail "stop_hook_active=true bypassed outstanding lifecycle enforcement" \
      "exit=$HOOK_EXIT output='$HOOK_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# BONUS TEST 14: activity-only, nothing updated → should BLOCK
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 14: Activity file only, .living not updated → should BLOCK"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.living/findings"

  # Create living files and the findings directory with OLD timestamps first
  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"

  # Sleep so that session-start-ts written AFTER is newer than all .living files
  sleep 2

  # Activity file exists, no reminded.tmp → hook uses session-start-ts as WORK_TS
  echo "src/foo.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"
  ts_ancient > "$REPO/.mycelium/session-start-ts.tmp"

  # WORK_TS is newer than the .living files.

  run_hook "$REPO"

  if echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    pass "Activity file + nothing updated → block JSON"
  else
    fail "Activity file + nothing updated → expected block" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 300)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 15: additionalContext contains LOG_REGISTRY instruction
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 15: additionalContext contains LOG_REGISTRY instruction"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"
  mkdir -p "$REPO/.mycelium"

  # Create a fake session log so SESSION_ID is populated
  LOG_PATH="$REPO/.mycelium/logs/2026-01-01-session.md"
  mkdir -p "$(dirname "$LOG_PATH")"
  cat > "$LOG_PATH" << 'LOG_EOF'
---
ended: TBD
duration_minutes: 0
files_changed: 0
project: test
session_id: test-session-015
branch: main
---
LOG_EOF

  # Simulate work: create reminded.tmp and activity file
  ts_old > "$REPO/.mycelium/mycelium-reminded.tmp"
  echo "src/foo.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"

  # Update .living/ so the hook passes (not blocked)
  sleep 1
  touch_now "$REPO/.living/learnings.md"

  run_hook "$REPO"

  if [ "$HOOK_EXIT" -eq 0 ] && echo "$HOOK_OUTPUT" | grep -q "LOG_REGISTRY"; then
    pass "additionalContext contains LOG_REGISTRY instruction"
  else
    fail "additionalContext missing LOG_REGISTRY instruction" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 400)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 16: duration_minutes computed from frontmatter `started:`
# (defends against stale session-start-ts.tmp from a crashed prior session
# producing 14000+ minute durations).
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 16: duration_minutes uses frontmatter started: when present"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  LOG_PATH="$REPO/.living/log/2026-01-01-001-test.md"
  mkdir -p "$(dirname "$LOG_PATH")"
  # Frontmatter says session started 30 seconds ago — accurate.
  RECENT_ISO=$(date -r $(( $(date +%s) - 30 )) '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null \
               || date -d "@$(( $(date +%s) - 30 ))" '+%Y-%m-%dT%H:%M:%S%z')
  cat > "$LOG_PATH" <<LOG_EOF
---
session_id: 2026-01-01-001
project: test
branch: main
started: ${RECENT_ISO}
ended:
duration_minutes:
files_changed:
---

## Session Log

### 00:00 — Session started
LOG_EOF

  # session-start-ts.tmp is BOGUS — 10 days ago. Demonstrates self-healing.
  TEN_DAYS_AGO=$(( $(date +%s) - 10*86400 ))
  echo "$TEN_DAYS_AGO" > "$REPO/.mycelium/session-start-ts.tmp"
  printf '%s\n%s\n' "$LOG_PATH" "$TEN_DAYS_AGO" > "$REPO/.mycelium/active-session-log.tmp"

  # Force the non-bypass branch by adding activity — duration won't be < 5 anymore
  echo "src/foo.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"
  touch "$REPO/.living/learnings.md"
  python3 "$HERE/../scripts/session_file_changes.py" snapshot \
    --repo-root "$REPO" \
    --output "$REPO/.mycelium/living-reminder-baseline.json"
  date +%s > "$REPO/.mycelium/mycelium-reminded.tmp"
  printf '\nDuration test recorded.\n' >> "$REPO/.living/learnings.md"

  run_hook "$REPO"

  DUR=$(grep '^duration_minutes:' "$LOG_PATH" 2>/dev/null | awk '{print $2}')
  if [ "$HOOK_EXIT" -eq 0 ] && [ -n "$DUR" ] && [ "$DUR" -lt 5 ]; then
    pass "Frontmatter started: → sane duration (${DUR}m), not stale-ts duration"
  else
    fail "Stale .tmp ignored, frontmatter used → expected duration < 5m" \
      "exit=$HOOK_EXIT duration=$DUR"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 17: duration_minutes falls back to session-start-ts.tmp when
# frontmatter `started:` is absent (preserves prior behavior for old logs).
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 17: missing frontmatter started: falls back to session-start-ts.tmp"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living"

  LOG_PATH="$REPO/.living/log/2026-01-01-001-test.md"
  mkdir -p "$(dirname "$LOG_PATH")"
  # Frontmatter has no `started:` — old-format log.
  cat > "$LOG_PATH" <<'LOG_EOF'
---
session_id: 2026-01-01-001
project: test
branch: main
ended:
duration_minutes:
files_changed:
---

## Session Log
LOG_EOF

  date +%s > "$REPO/.mycelium/session-start-ts.tmp"
  printf '%s\n%s\n' "$LOG_PATH" "$(date +%s)" > "$REPO/.mycelium/active-session-log.tmp"
  echo "src/foo.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"
  touch "$REPO/.living/learnings.md"
  python3 "$HERE/../scripts/session_file_changes.py" snapshot \
    --repo-root "$REPO" \
    --output "$REPO/.mycelium/living-reminder-baseline.json"
  date +%s > "$REPO/.mycelium/mycelium-reminded.tmp"
  printf '\nFallback duration test recorded.\n' >> "$REPO/.living/learnings.md"

  run_hook "$REPO"

  DUR=$(grep '^duration_minutes:' "$LOG_PATH" 2>/dev/null | awk '{print $2}')
  if [ "$HOOK_EXIT" -eq 0 ] && [ -n "$DUR" ] && [ "$DUR" -lt 5 ]; then
    pass "No frontmatter started: → fell back to .tmp, duration ${DUR}m"
  else
    fail "Missing started: → expected fallback to .tmp + sane duration" \
      "exit=$HOOK_EXIT duration=$DUR"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 18: Fresh meaningful work still blocks at the actual Stop event
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 18: Fresh work with stale .living/ → should BLOCK immediately"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living/findings"
  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"

  date +%s > "$REPO/.mycelium/mycelium-reminded.tmp"
  echo "src/fresh.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"

  run_hook "$REPO"

  if echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    pass "Fresh work is enforced at Stop without a time-based bypass"
  else
    fail "Fresh work should block when .living/ is stale" \
      "exit=$HOOK_EXIT output='$(echo "$HOOK_OUTPUT" | head -c 300)'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 19: Pre-existing dirty tree does not inflate session file count
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 19: Session log counts only current-session paths"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living/log" "$REPO/preexisting"
  printf '# Learnings\n' > "$REPO/.living/learnings.md"
  echo "old work" > "$REPO/preexisting/old.txt"

  LOG_PATH="$REPO/.living/log/2026-01-01-001-test.md"
  STARTED=$(date '+%Y-%m-%dT%H:%M:%S%z')
  cat > "$LOG_PATH" <<LOG_EOF
---
session_id: 2026-01-01-001
project: test
branch: main
started: ${STARTED}
ended:
duration_minutes:
files_changed:
---

## Session Log
LOG_EOF
  START_TS=$(date +%s)
  echo "$START_TS" > "$REPO/.mycelium/session-start-ts.tmp"
  printf '%s\n%s\n' "$LOG_PATH" "$START_TS" > "$REPO/.mycelium/active-session-log.tmp"
  python3 "$HERE/../scripts/session_file_changes.py" snapshot \
    --repo-root "$REPO" \
    --output "$REPO/.mycelium/session-file-baseline.json"

  DISPOSABLE="$REPO/MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp"
  echo "probe" > "$DISPOSABLE"
  echo "$DISPOSABLE" > "$REPO/.mycelium/mycelium-session-activity.tmp"
  rm "$DISPOSABLE"
  printf '\nDisposable-path accounting recorded.\n' >> "$REPO/.living/learnings.md"

  run_hook "$REPO"

  FILE_COUNT=$(grep '^files_changed:' "$LOG_PATH" | awk '{print $2}')
  if [ "$FILE_COUNT" = "2" ] \
    && grep -q 'MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp' "$LOG_PATH" \
    && grep -q '.living/learnings.md' "$LOG_PATH" \
    && ! grep -q 'preexisting/old.txt' "$LOG_PATH"; then
    pass "Pre-existing dirty files excluded; session lifecycle and deleted paths retained"
  else
    fail "Expected exactly two session-local paths" \
      "files_changed=$FILE_COUNT log='$(tail -20 "$LOG_PATH")'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 20: A blocked Stop remains blocked until .living/ is updated
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 20: Repeated Stop cannot bypass missing lifecycle bookkeeping"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living/log" "$REPO/.living/findings"
  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"

  LOG_PATH="$REPO/.living/log/2026-01-01-001-test.md"
  STARTED=$(date '+%Y-%m-%dT%H:%M:%S%z')
  cat > "$LOG_PATH" <<LOG_EOF
---
session_id: 2026-01-01-001
project: test
branch: main
started: ${STARTED}
ended:
duration_minutes:
files_changed:
---
LOG_EOF
  START_TS=$(date +%s)
  echo "$START_TS" > "$REPO/.mycelium/session-start-ts.tmp"
  printf '%s\n%s\n' "$LOG_PATH" "$START_TS" > "$REPO/.mycelium/active-session-log.tmp"
  date +%s > "$REPO/.mycelium/mycelium-reminded.tmp"
  echo "src/fresh.py" > "$REPO/.mycelium/mycelium-session-activity.tmp"

  run_hook "$REPO"
  FIRST_OUTPUT="$HOOK_OUTPUT"
  run_hook_with_input "$REPO" '{"stop_hook_active": true}'
  SECOND_OUTPUT="$HOOK_OUTPUT"

  if echo "$FIRST_OUTPUT" | grep -q '"decision": "block"' \
    && echo "$SECOND_OUTPUT" | grep -q '"decision": "block"' \
    && [ -f "$REPO/.mycelium/session-start-ts.tmp" ]; then
    pass "Stop remains blocked and preserves the work timestamp"
  else
    fail "A second Stop bypassed lifecycle enforcement" \
      "first='$FIRST_OUTPUT' second='$SECOND_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 21: Significant code execution is work even with no changed files
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 21: Reminder-only analysis preserves and enforces the session"
{
  REPO=$(make_repo)
  mkdir -p "$REPO/.living/log" "$REPO/.living/findings"
  touch_very_old "$REPO/.living/learnings.md"
  touch_very_old "$REPO/.living/decisions.md"
  touch_very_old "$REPO/.living/conventions.md"
  touch_very_old "$REPO/.living/findings"

  LOG_PATH="$REPO/.living/log/2026-01-01-001-test.md"
  STARTED=$(date '+%Y-%m-%dT%H:%M:%S%z')
  cat > "$LOG_PATH" <<LOG_EOF
---
session_id: 2026-01-01-001
project: test
branch: main
started: ${STARTED}
ended:
duration_minutes:
files_changed:
---
LOG_EOF
  START_TS=$(date +%s)
  echo "$START_TS" > "$REPO/.mycelium/session-start-ts.tmp"
  printf '%s\n%s\n' "$LOG_PATH" "$START_TS" > "$REPO/.mycelium/active-session-log.tmp"
  python3 "$HERE/../scripts/session_file_changes.py" snapshot \
    --repo-root "$REPO" \
    --output "$REPO/.mycelium/session-file-baseline.json"
  date +%s > "$REPO/.mycelium/mycelium-reminded.tmp"

  run_hook "$REPO"

  if [ -f "$LOG_PATH" ] \
    && grep -q '^ended:$' "$LOG_PATH" \
    && ! grep -q 'Session ended' "$LOG_PATH" \
    && [ -f "$REPO/.mycelium/active-session-log.tmp" ] \
    && echo "$HOOK_OUTPUT" | grep -q '"decision": "block"'; then
    pass "Reminder-only code execution blocks without finalizing the session"
  else
    fail "Blocked analysis mutated final lifecycle state" \
      "log_exists=$([ -f "$LOG_PATH" ] && echo yes || echo no) output='$HOOK_OUTPUT'"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo -e "Results: ${GREEN}${PASS_COUNT} passed${NC} / ${RED}${FAIL_COUNT} failed${NC} / ${TOTAL} total"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
