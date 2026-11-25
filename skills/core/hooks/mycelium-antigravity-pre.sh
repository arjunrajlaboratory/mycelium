#!/bin/bash
# mycelium-antigravity-pre.sh — Antigravity PreToolUse: observation only.
#
# Antigravity requires every PreToolUse hook to print a `decision`. `allow`
# hands the call to the next hook or the host's default policy, so approvals,
# "Always Allow" grants and denials behave exactly as without this hook.
#
# This wrapper prints exactly {"decision": "allow"} and exits 0 on EVERY path:
# the Python helper's own output is discarded, and a helper that crashes,
# prints something else, is missing, or runs past the hard bound (killed by
# the watchdog below) changes nothing. It never prints auto_approve, deny,
# ask, overwrite or permissionOverrides.
#
# The helper (antigravity_hooks.py PreToolUse) takes the I/O observer's
# pre-snapshot for run_command and opens the journal interval for the edit
# tools, under its own soft deadline (antigravity_hooks.PRE_TOOL_SECONDS).
#
# Usage (generated in hooks.json by tools/antigravity.py):
#   bash skills/core/hooks/mycelium-antigravity-pre.sh <python> [--host HOST]
# Input: the native PreToolUse payload on stdin.

# Watchdog: one timed sleep (a poll loop drifts with fork cost on a loaded
# machine), so the whole hook ends in about 2 s even when the helper is stuck
# in a system call that its own deadline cannot interrupt.
MYCELIUM__PRE_HARD_SECONDS=1.8

mycelium__pre_observe() {
  local python="${1:-}"
  shift
  local here script pid dog
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || return 0
  script="$here/../scripts/antigravity_hooks.py"
  [[ -n "$python" && -f "$script" ]] || return 0
  # Explicit stdin: a background command would otherwise read /dev/null.
  exec 3<&0
  "$python" "$script" "$@" PreToolUse <&3 >/dev/null 2>&1 3<&- &
  pid=$!
  exec 3<&-
  (
    # Cancelled with TERM when the helper exits first: the trap ends the
    # sleep too, so no stray process outlives the hook.
    sleep "$MYCELIUM__PRE_HARD_SECONDS" &
    nap=$!
    trap 'kill "$nap" 2>/dev/null; exit 0' TERM
    wait "$nap"
    kill -KILL "$pid" 2>/dev/null
  ) </dev/null >/dev/null 2>&1 &
  dog=$!
  wait "$pid" 2>/dev/null
  kill "$dog" 2>/dev/null
  wait "$dog" 2>/dev/null
  return 0
}

mycelium__pre_observe "$@" >/dev/null 2>&1
printf '{"decision": "allow"}\n'
exit 0
