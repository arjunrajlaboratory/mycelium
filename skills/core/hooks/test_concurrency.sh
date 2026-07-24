#!/usr/bin/env bash
# test_concurrency.sh — session-scoping tests for the mycelium hook bundle.
#
# Verifies the multi-chat concurrency model:
#   * two independent chats (distinct session_id) get isolated run dirs + logs
#   * a subagent / resume (same session_id) reuses the parent's session, no dup
#   * concurrent SessionStarts race-allocate distinct session numbers
#   * legacy stdin (no session_id) falls back to the flat .claude/ layout
#   * a SubagentStop event does not finalize the parent's session log
#
# Self-locating: resolves the hooks from this script's own directory, so it
# runs from any checkout (no hardcoded paths).

set -uo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
HEALTH="$HOOKS_DIR/mycelium-health.sh"
STOP="$HOOKS_DIR/mycelium-stop-check.sh"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
PASS=0; FAIL=0
pass() { echo -e "${GREEN}PASS${NC} — $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}FAIL${NC} — $1"; [ -n "${2:-}" ] && echo -e "       ${YELLOW}${2}${NC}"; FAIL=$((FAIL + 1)); }

new_repo() {
  local T; T=$(mktemp -d)
  git init -q "$T"; git -C "$T" config user.email t@t.com; git -C "$T" config user.name T
  touch "$T/README.md"; git -C "$T" add README.md; git -C "$T" commit -q -m init
  mkdir -p "$T/.living/log"
  printf "# Learnings\n\n### Entry 1\nx\n" > "$T/.living/learnings.md"
  echo "$T"
}

# Run the SessionStart hook for a given repo + session_id (empty = legacy).
start_session() {
  local repo="$1" sid="${2:-}"
  if [ -n "$sid" ]; then
    (cd "$repo" && printf '{"cwd":"%s","source":"startup","session_id":"%s"}' "$repo" "$sid" \
      | bash "$HEALTH" >/dev/null 2>&1)
  else
    (cd "$repo" && printf '{"cwd":"%s","source":"startup"}' "$repo" \
      | bash "$HEALTH" >/dev/null 2>&1)
  fi
}

count_logs() { ls "$1"/.living/log/*-*.md 2>/dev/null | grep -v LOG_REGISTRY | wc -l | tr -d ' '; }

# ── TEST 1: two chats get isolated run dirs + distinct logs ──────────────────
echo "TEST 1: two independent chats → isolated dirs, distinct logs"
{
  R=$(new_repo)
  start_session "$R" "chat-AAA"
  start_session "$R" "chat-BBB"
  A="$R/.claude/mycelium/run/chat-AAA/active-session-log.tmp"
  B="$R/.claude/mycelium/run/chat-BBB/active-session-log.tmp"
  if [ -f "$A" ] && [ -f "$B" ] && [ "$(head -1 "$A")" != "$(head -1 "$B")" ] && [ "$(count_logs "$R")" = "2" ]; then
    pass "distinct run dirs, distinct log files, 2 logs total"
  else
    fail "expected 2 isolated logs" "A=$(head -1 "$A" 2>/dev/null) B=$(head -1 "$B" 2>/dev/null) logs=$(count_logs "$R")"
  fi
}

# ── TEST 2: distinct session numbers (no clobber) ────────────────────────────
echo "TEST 2: two chats → distinct YYYY-MM-DD-NNN session ids"
{
  R=$(new_repo)
  start_session "$R" "s1"; start_session "$R" "s2"
  ids=$(grep -h '^session_id:' "$R"/.living/log/*-*.md 2>/dev/null | sort -u | wc -l | tr -d ' ')
  [ "$ids" = "2" ] && pass "2 distinct session ids" || fail "expected 2 distinct session ids, got $ids"
}

# ── TEST 3: subagent / resume (same session_id) does NOT create a 2nd log ─────
echo "TEST 3: same session_id twice → single log (subagent/resume)"
{
  R=$(new_repo)
  start_session "$R" "chat-AAA"
  first=$(head -1 "$R/.claude/mycelium/run/chat-AAA/active-session-log.tmp")
  start_session "$R" "chat-AAA"   # simulate a subagent SessionStart / resume
  second=$(head -1 "$R/.claude/mycelium/run/chat-AAA/active-session-log.tmp")
  if [ "$(count_logs "$R")" = "1" ] && [ "$first" = "$second" ]; then
    pass "reused the parent session's log; no duplicate created"
  else
    fail "expected single reused log" "logs=$(count_logs "$R") first=$first second=$second"
  fi
}

# ── TEST 4: activity is isolated between chats ───────────────────────────────
echo "TEST 4: run dirs keep per-chat sentinels isolated"
{
  R=$(new_repo)
  start_session "$R" "s1"; start_session "$R" "s2"
  echo "/x/a.py" > "$R/.claude/mycelium/run/s1/mycelium-session-activity.tmp"
  if [ ! -f "$R/.claude/mycelium/run/s2/mycelium-session-activity.tmp" ]; then
    pass "chat s1 activity not visible to chat s2"
  else
    fail "activity leaked across chats"
  fi
}

# ── TEST 5: concurrent SessionStarts race → distinct slots, no lost logs ──────
echo "TEST 5: 5 concurrent SessionStarts → 5 distinct logs"
{
  R=$(new_repo)
  for i in 1 2 3 4 5; do start_session "$R" "race-$i" & done
  wait
  n=$(count_logs "$R")
  uniq=$(grep -h '^session_id:' "$R"/.living/log/*-*.md 2>/dev/null | sort -u | wc -l | tr -d ' ')
  if [ "$n" = "5" ] && [ "$uniq" = "5" ]; then
    pass "5 logs, 5 distinct session ids (atomic slot allocation)"
  else
    fail "expected 5 distinct logs" "logs=$n distinct_ids=$uniq"
  fi
}

# ── TEST 6: legacy stdin (no session_id) → flat layout ───────────────────────
echo "TEST 6: no session_id → legacy flat .claude/ layout"
{
  R=$(new_repo)
  start_session "$R" ""
  if [ -f "$R/.claude/active-session-log.tmp" ] && [ ! -d "$R/.claude/mycelium" ]; then
    pass "fell back to flat .claude/active-session-log.tmp"
  else
    fail "expected legacy flat layout"
  fi
}

# ── TEST 7: SubagentStop event does not finalize the session ─────────────────
echo "TEST 7: SubagentStop event → no finalization"
{
  R=$(new_repo)
  start_session "$R" "chat-AAA"
  logpath=$(head -1 "$R/.claude/mycelium/run/chat-AAA/active-session-log.tmp")
  (cd "$R" && printf '{"cwd":"%s","session_id":"chat-AAA","hook_event_name":"SubagentStop"}' "$R" \
    | bash "$STOP" >/dev/null 2>&1)
  if ! grep -q '^ended: [0-9]' "$logpath" 2>/dev/null; then
    pass "subagent stop left the parent log open"
  else
    fail "subagent stop wrongly finalized the parent log"
  fi
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "Results: ${GREEN}${PASS} passed${NC} / ${RED}${FAIL} failed${NC} / $((PASS + FAIL)) total"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$FAIL" -eq 0 ]
