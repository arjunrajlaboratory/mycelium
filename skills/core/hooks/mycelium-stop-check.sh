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
mycelium_payload_preload INPUT 'opt:session_id' 'get:cwd'
HOST_SESSION_ID=$(mycelium_json_get_optional_string 'session_id' INPUT) || exit 0

# Determine repo root early (used by both log finalization and .living/ checks)
# from the session's own directory, as SessionStart and the PostToolUse
# trackers do (issue #144: a worktree session's hooks run in the main checkout).
REPO_ROOT=$(mycelium_session_repo_root "$(mycelium_json_get 'cwd' INPUT)" \
  "$HOST_SESSION_ID")
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi
# Report, and clear, the skips this host session's hooks recorded while it had
# no transaction it owned. Used on the paths that end Stop without one. With
# `own-run`, STATE_DIR is this host session's own run directory with no
# transaction: lineage and read records a hook left there after the previous
# Stop accepted are reported and cleared too (work records reopen instead,
# before this runs).
mycelium_report_unowned_lineage_gaps() {
  local gap=""
  local taken=""
  local file="${STATE_DIR:-}/mycelium-unrecorded-events.tmp"
  gap=$(mycelium_take_unowned_lineage_skips \
    "$REPO_ROOT" "$(mycelium_hook_host)" "$HOST_SESSION_ID") || gap=""
  if [[ "${1:-}" == own-run && "${MYCELIUM_SESSION_SCOPED:-false}" == true \
    && -n "${STATE_DIR:-}" && "$STATE_DIR" != "${MYCELIUM_SHARED_STATE_DIR:-}" \
    && -f "$file" && ! -L "$file" ]]; then
    # One flock for the read and the removal: a duplicate Stop holds no
    # session lock here either.
    taken=$(mycelium_unrecorded_events drain-other "$file" 2>/dev/null) || taken=""
    if [[ "$taken" =~ ^[1-9][0-9]*$ ]]; then
      gap="${gap:+$gap }UNRECORDED EVENTS: ${taken} data-lineage or .living read record(s) this session were dropped because the session lock was unavailable; treat their provenance as unknown."
    fi
  fi
  if [[ -n "$gap" ]]; then
    mycelium_emit_context "Stop" "$gap"
  fi
}

# With no transaction for this host session, compare the repository against
# the baseline the previous accepted Stop carried forward (from the time it
# recorded). Project changes outside `.living/` and `.mycelium/` mean the turn
# did work no PostToolUse hook reopened for: reopen (status 0). Changes only
# under `.living/` (a note written in a turn with no other work) refresh the
# carried baseline and start time instead, so that note can never stand in
# for the reflection a later turn's work owes. Called with the state call's
# run directory in STATE_DIR and no locks held.
mycelium_stop_reopen_for_shell_changes() {
  local state_dir="${STATE_DIR:-}"
  local carried="$state_dir/next-turn-baseline.json"
  local helper="${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"
  local start_ts=""
  local changed=""
  local changed_path=""
  local project_changed=false
  local collect_args=()
  [[ "${MYCELIUM_SESSION_SCOPED:-false}" == true && -n "$state_dir" \
    && "$state_dir" != "${MYCELIUM_SHARED_STATE_DIR:-}" ]] || return 1
  [[ -f "$carried" && ! -L "$carried" && -f "$helper" ]] || return 1
  start_ts=$(head -1 "$state_dir/next-turn-start-ts.tmp" 2>/dev/null || true)
  collect_args=(collect --repo-root "$REPO_ROOT" --baseline "$carried" \
    --exclude-prefix ".living/log/" --exclude ".living/INDEX.md")
  if [[ "$start_ts" =~ ^[0-9]{1,18}$ ]]; then
    collect_args+=(--start-ts "$start_ts")
  fi
  changed=$(python3 "$helper" "${collect_args[@]}" 2>/dev/null) || return 1
  [[ -n "$changed" ]] || return 1
  while IFS= read -r changed_path; do
    case "$changed_path" in
      ""|.living/*) ;;
      *) project_changed=true ;;
    esac
  done <<<"$changed"
  if [[ "$project_changed" == true ]]; then
    mycelium_reopen_post_tool_transaction "$REPO_ROOT" "$INPUT"
    return $?
  fi
  python3 "$helper" snapshot --repo-root "$REPO_ROOT" --output "$carried.tmp" \
    >/dev/null 2>&1 && mv -f "$carried.tmp" "$carried" \
    && printf '%s\n' "$(date +%s)" > "$state_dir/next-turn-start-ts.tmp"
  rm -f "$carried.tmp"
  return 1
}

# A post-tool hook whose lock wait outlasted the previous Stop's acceptance
# left work evidence in a run directory with no transaction. Reopen, so this
# Stop runs once more against a transaction that takes and enforces it. A
# lineage or read record alone waits for this session's next transaction.
# Called with STATE_DIR set to the run directory and no locks held.
mycelium_stop_reopen_for_unrecorded_work() {
  local file="${STATE_DIR:-}/mycelium-unrecorded-events.tmp"
  local taken=""
  [[ "${MYCELIUM_SESSION_SCOPED:-false}" == true && -n "${STATE_DIR:-}" \
    && "$STATE_DIR" != "${MYCELIUM_SHARED_STATE_DIR:-}" ]] || return 1
  [[ -f "$file" && ! -L "$file" ]] || return 1
  taken=$(mycelium_unrecorded_events take "$file" 2>/dev/null) || return 1
  [[ "$taken" =~ ^[1-9][0-9]*\  ]] || return 1
  mycelium_reopen_post_tool_transaction "$REPO_ROOT" "$INPUT"
}

# One state call runs the whole entry sequence below; a lock that is already
# held falls back to the bash retry loop and resumes the remaining checks.
#
# An identified late Stop must not recreate a completed transaction, so it
# resolves only an already-existing private run before normal shared setup.
# Another Stop for the same root may complete while this one waits for the
# repository lifecycle lock, and a duplicate may win the session lock first:
# re-resolve without creating state after each lock. A missing marker at any
# of those points means a harmless duplicate or late delivery.
if [[ -n "$HOST_SESSION_ID" ]]; then
  _STOP_OPS=(
    prepare:read-only select:read-only require-marker
    lock:stop
    prepare:write
    prepare:read-only select:read-only require-marker
    lock:session
    prepare:read-only select:read-only require-marker
    marker owner
  )
  _STOP_LOCK_OP=4
  _STOP_PREPARE_OP=5
  _STOP_SESSION_LOCK_OP=9
  _STOP_READ_OP=13
else
  _STOP_OPS=(
    prepare:bootstrap select:read-only
    lock:stop
    prepare:write
    lock:session
    marker owner
  )
  _STOP_LOCK_OP=3
  _STOP_PREPARE_OP=4
  _STOP_SESSION_LOCK_OP=5
  _STOP_READ_OP=6
fi
# Release whatever the state call takes even if this hook is killed while it
# waits: the lifecycle lock is recorded before any wait for the session lock.
# Both releases do nothing for a lock that was never taken.
trap 'mycelium_release_session_lock; mycelium_release_stop_lock' EXIT
if ! mycelium__state "$REPO_ROOT" "$INPUT" "${_STOP_OPS[@]}"; then
  _STOP_FAILED="$MYCELIUM__STATE_FAILED"
  mycelium__state_release_held
  if (( _STOP_FAILED == _STOP_LOCK_OP )); then
    # A concurrent owner may still fail or be interrupted, so accepting this
    # Stop would bypass lifecycle enforcement. Preserve state and ask the host
    # to retry.
    mycelium_emit_stop_block \
      "STOP BLOCKED — the lifecycle transaction lock remained busy. Active state was preserved; wait for the other lifecycle hook to finish, then retry Stop."
  elif (( _STOP_FAILED == _STOP_PREPARE_OP )); then
    mycelium_emit_stop_block \
      "STOP BLOCKED — shared lifecycle state preparation failed. Active state was preserved; repair the unsafe state and retry Stop."
  elif (( _STOP_FAILED == _STOP_SESSION_LOCK_OP )); then
    mycelium_emit_stop_block \
      "STOP BLOCKED — the session event lock remained busy. Active state was preserved; wait for the in-flight hook to finish, then retry Stop."
  elif (( _STOP_FAILED >= _STOP_READ_OP )); then
    # The marker and owner reads never fail; reaching here means the state
    # process died after both locks were taken. Never accept on that.
    mycelium_emit_stop_block \
      "STOP BLOCKED — the active session state could not be read. Active state was preserved; retry Stop."
  elif [[ -n "$HOST_SESSION_ID" ]]; then
    # This host session has no transaction. A turn whose only work was shell
    # edits (`printf > model.py`) reopens nothing from PostToolUse, so check
    # the baseline the last accepted Stop carried forward; if project files
    # changed, reopen and run this Stop once more against that transaction.
    if [[ -z "${MYCELIUM__STOP_REOPENED:-}" ]] \
      && { mycelium_stop_reopen_for_unrecorded_work \
        || mycelium_stop_reopen_for_shell_changes; }; then
      export MYCELIUM__STOP_REOPENED=1
      exec "$HERE/mycelium-stop-check.sh" <<<"$INPUT"
    fi
    # Commands its hooks could not record were kept in the shared unowned-
    # skip file; report them now rather than end the turn in silence.
    mycelium_report_unowned_lineage_gaps own-run
  fi
  exit 0
fi

HANDOFF_PATH="$STATE_DIR/last-session.md"

# Resolve and validate the active transaction before any Stop-side mutation.
# The host session ID is per invocation; repository timestamps are shared and
# therefore cannot distinguish a primary from a concurrently running child.
ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
ACTIVE_OWNER_FILE="$STATE_DIR/active-session-owner-id.tmp"
ACTIVE_MARKER_VALID=false
SESSION_OWNERSHIP="legacy"
LOG_PATH=""
OWNER_TS=""
OWNER_FORMAT=""
if [ -f "$ACTIVE_LOG_FILE" ]; then
  if [[ "$MYCELIUM_MARKER_STATE" == valid ]]; then
    ACTIVE_MARKER_VALID=true
    LOG_PATH="$MYCELIUM_MARKER_LOG"
    OWNER_TS="$MYCELIUM_MARKER_TS"
    OWNER_FORMAT="$MYCELIUM_MARKER_FORMAT"
  else
    # Never trust a separate owner token when the marker it supposedly owns is
    # invalid. Remove both and continue to independent lifecycle enforcement.
    rm -f "$ACTIVE_LOG_FILE" "$ACTIVE_OWNER_FILE"
  fi
fi

if [[ "$ACTIVE_MARKER_VALID" == true \
  && ( "$OWNER_FORMAT" == "owner-id-v1" \
    || -e "$ACTIVE_OWNER_FILE" || -L "$ACTIVE_OWNER_FILE" ) ]]; then
  OWNER_SESSION_ID="$MYCELIUM_OWNER_ID"
  if [[ "$MYCELIUM_OWNER_STATE" != valid ]] \
    || ! mycelium_valid_session_id "$HOST_SESSION_ID"; then
    mycelium_emit_stop_block \
      "STOP BLOCKED — active session ownership could not be validated. The primary lifecycle state was preserved; repair the owner marker or retry from its host session."
    exit 0
  elif [[ "$OWNER_SESSION_ID" != "$HOST_SESSION_ID" ]]; then
    # Child session: do not consolidate lineage, finalize/delete the primary
    # log, or consume any of the primary's shared enforcement state. Its own
    # unowned gaps are still reported, never appended to the owner's stream.
    mycelium_report_unowned_lineage_gaps
    exit 0
  else
    SESSION_OWNERSHIP="host-id"
  fi
fi

# Skips this host session's hooks recorded while it had no transaction (a
# refused reopen, a failed detector) join its raw stream when this Stop holds
# a valid transaction it owns, so the manifest's `skipped` list and the
# LINEAGE GAP report include them. Without one (the marker was invalid and
# has just been removed) no manifest will be written, so they are reported
# directly in this Stop's message instead.
UNOWNED_GAP_MSG=""
if [[ -n "$HOST_SESSION_ID" ]]; then
  if [[ "$ACTIVE_MARKER_VALID" == true && "$SESSION_OWNERSHIP" == host-id ]]; then
    # A failed move may leave copies in both the session stream and the
    # shared file. Accepting now would archive this stream and let a later
    # turn report the same gaps again, so block and let the retry finish the
    # move; skip_id collapses the repeated append within this manifest.
    if ! mycelium_take_unowned_lineage_skips "$REPO_ROOT" "$(mycelium_hook_host)" \
      "$HOST_SESSION_ID" "$STATE_DIR/mycelium-data-events.tmp" >/dev/null; then
      mycelium_emit_stop_block \
        "STOP BLOCKED — this session's unowned lineage skip records could not be moved into its lineage stream (lineage-skips-unowned.ndjson rewrite failed). Active state was preserved; retry Stop."
      exit 0
    fi
  else
    UNOWNED_GAP_MSG=$(mycelium_take_unowned_lineage_skips \
      "$REPO_ROOT" "$(mycelium_hook_host)" "$HOST_SESSION_ID") || UNOWNED_GAP_MSG=""
  fi
fi

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
# Lineage counts as session work only through a record other than a
# data_command: a look at a data file (`head data/x.csv`) is recorded, but
# is not work that obliges a session log, as it does not reopen a
# transaction either. Every writer emits `"kind": "data_command"` with
# json.dumps spacing; inside a JSON string the quotes are escaped, so a
# command that merely contains that text cannot match.
LINEAGE_EVENT_COUNT=0
if [[ -s "$STATE_DIR/mycelium-data-events.tmp" ]] \
  && grep -qv '"kind": "data_command"' "$STATE_DIR/mycelium-data-events.tmp" 2>/dev/null; then
  LINEAGE_EVENT_COUNT=1
fi

# Commands the lineage recorder meant to record but could not left skip
# records in the raw stream (now the manifest's `skipped` list). Say so at
# Stop: silence must never read as "nothing ran".
LINEAGE_GAP_MSG=""
if [[ -s "$STATE_DIR/mycelium-data-events.tmp" ]] \
  && grep -q '"kind": "skip"' "$STATE_DIR/mycelium-data-events.tmp" 2>/dev/null; then
  LINEAGE_GAP_MSG=$(python3 - "$STATE_DIR/mycelium-data-events.tmp" <<'PY' 2>/dev/null || true
import collections, json, sys
reasons = collections.Counter()
seen = set()
with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
    for line in handle:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == "skip":
            skip_id = record.get("skip_id")
            if skip_id in seen:
                continue
            if skip_id:
                seen.add(skip_id)
            reasons[str(record.get("skip_reason") or "unknown")] += 1
if reasons:
    total = sum(reasons.values())
    detail = ", ".join(f"{reason} x{count}" for reason, count in sorted(reasons.items()))
    print(
        f"LINEAGE GAP: {total} data command(s) so far this session were not recorded "
        f"({detail}). They are listed under `skipped` in this session's "
        ".living/log/data-lineage manifest; treat their provenance as unknown."
    )
PY
)
fi
if [[ -n "$UNOWNED_GAP_MSG" ]]; then
  LINEAGE_GAP_MSG="${LINEAGE_GAP_MSG:+$LINEAGE_GAP_MSG }$UNOWNED_GAP_MSG"
fi
# Post-tool events of this transaction that a hook could not record (its
# session lock was unavailable). Take them now, under this Stop's locks. A
# `work` record (a project edit, a proven analysis run) is work of unknown
# extent: it starts a fresh work cycle, so only a .living/ update made after
# this point satisfies Stop, the block reason says why, and the record is
# consumed (the cycle now carries it). A `lineage` or `read` record is not
# session work, as its recorded counterpart is not; it is reported with the
# lineage gaps so its provenance reads as unknown rather than absent, and it
# stays in the file until an accepting Stop has reported it. Anything a hook
# appends while this Stop runs is caught by the acceptance checks below.
UNRECORDED_FILE="$STATE_DIR/mycelium-unrecorded-events.tmp"
UNRECORDED_MSG=""
UNRECORDED_KEPT=0
UNRECORDED_KEPT_GEN=0
if [[ -e "$UNRECORDED_FILE" || -L "$UNRECORDED_FILE" ]]; then
  _UNRECORDED_STATUS=0
  _UNRECORDED_TAKEN=$(mycelium_unrecorded_events take "$UNRECORDED_FILE" 2>/dev/null) \
    || _UNRECORDED_STATUS=$?
  if (( _UNRECORDED_STATUS == 75 )); then
    mycelium_emit_stop_block \
      "STOP BLOCKED — a post-tool hook is still writing evidence of an event it could not record. Active state was preserved; retry Stop." || true
    exit 0
  fi
  _UNRECORDED_WORK=1
  _UNRECORDED_OTHER=0
  _UNRECORDED_SIZE=""
  _UNRECORDED_GEN=""
  if [[ "$_UNRECORDED_TAKEN" =~ ^([0-9]+)\ ([0-9]+)\ ([0-9]+)\ ([0-9a-f]+)$ ]]; then
    _UNRECORDED_WORK="${BASH_REMATCH[1]}"
    _UNRECORDED_OTHER="${BASH_REMATCH[2]}"
    _UNRECORDED_SIZE="${BASH_REMATCH[3]}"
    _UNRECORDED_GEN="${BASH_REMATCH[4]}"
  fi
  if (( _UNRECORDED_WORK > 0 )); then
    if ! mycelium_start_work_cycle "$REPO_ROOT" \
      "${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"; then
      # Without a fresh fingerprint and reminder an earlier .living/ update
      # could satisfy this work. Keep the evidence and retry.
      mycelium_emit_stop_block \
        "STOP BLOCKED — work a post-tool hook could not record needs a fresh .living/ fingerprint and reminder, and writing them failed. The evidence was preserved; repair ${STATE_DIR} if needed, then retry Stop." || true
      exit 0
    fi
    UNRECORDED_MSG="UNRECORDED WORK: ${_UNRECORDED_WORK} edit or analysis hook run(s) this session could not record their event because the session lock was unavailable. That work is not in the counts above; triage this turn's work as if it ran."
  fi
  if (( _UNRECORDED_OTHER > 0 )); then
    _UNRECORDED_NOTE="UNRECORDED EVENTS: ${_UNRECORDED_OTHER} data-lineage or .living read record(s) this session were dropped because the session lock was unavailable; treat their provenance as unknown."
    LINEAGE_GAP_MSG="${LINEAGE_GAP_MSG:+$LINEAGE_GAP_MSG }$_UNRECORDED_NOTE"
  fi
  if [[ -n "$_UNRECORDED_SIZE" ]]; then
    _UNRECORDED_STATUS=0
    _UNRECORDED_KEPT=$(mycelium_unrecorded_events consume-work "$UNRECORDED_FILE" \
      "$_UNRECORDED_SIZE" "$_UNRECORDED_GEN" 2>/dev/null) || _UNRECORDED_STATUS=$?
    if (( _UNRECORDED_STATUS != 0 )) \
      || [[ ! "$_UNRECORDED_KEPT" =~ ^([0-9]+)\ ([0-9a-f]+)$ ]]; then
      mycelium_emit_stop_block \
        "STOP BLOCKED — evidence of an event a post-tool hook could not record could not be updated. It was preserved; retry Stop." || true
      exit 0
    fi
    UNRECORDED_KEPT="${BASH_REMATCH[1]}"
    UNRECORDED_KEPT_GEN="${BASH_REMATCH[2]}"
  elif ! rm -f "$UNRECORDED_FILE" 2>/dev/null; then
    # Unreadable and unremovable (a directory, say). The work cycle above
    # already stands in for it, but it would block every retry: say so.
    mycelium_emit_stop_block \
      "STOP BLOCKED — ${UNRECORDED_FILE} is not a regular file Mycelium can read or remove. Remove it, then retry Stop." || true
    exit 0
  fi
fi
# The file now holds only the UNRECORDED_KEPT bytes (generation
# UNRECORDED_KEPT_GEN) of the lineage and read records this Stop reports.
# Anything else means a hook failed while this Stop ran: evidence this Stop
# has not accounted for, so ask for a retry that will.
mycelium_block_for_late_unrecorded() {
  local taken=""
  [[ -e "$UNRECORDED_FILE" || -L "$UNRECORDED_FILE" ]] || return 1
  taken=$(mycelium_unrecorded_events take "$UNRECORDED_FILE" 2>/dev/null) || taken=""
  if [[ "$taken" =~ ^0\ [0-9]+\ ([0-9]+)\ ([0-9a-f]+)$ \
    && "${BASH_REMATCH[1]}" == "$UNRECORDED_KEPT" ]] \
    && { (( UNRECORDED_KEPT == 0 )) \
      || [[ "${BASH_REMATCH[2]}" == "$UNRECORDED_KEPT_GEN" ]]; }; then
    return 1
  fi
  mycelium_emit_stop_block \
    "STOP BLOCKED — a post-tool hook could not record its event while this Stop ran. Active state was preserved; retry Stop so that event is accounted for." || true
  return 0
}
# Once acceptance can no longer fail before it reports them, the kept
# lineage and read records are done with (that generation only).
mycelium_consume_reported_unrecorded() {
  if (( UNRECORDED_KEPT > 0 )); then
    mycelium_unrecorded_events consume "$UNRECORDED_FILE" "$UNRECORDED_KEPT" \
      "$UNRECORDED_KEPT_GEN" >/dev/null 2>&1 || true
  fi
}
# mycelium_with_unrecorded REASON FILE_COUNT: append the unrecorded-work
# sentence. When no file change was seen at all, the "0 files changed" lead
# would contradict it, so the lead is replaced.
mycelium_with_unrecorded() {
  local reason="$1"
  if [[ -z "$UNRECORDED_MSG" ]]; then
    printf '%s' "$reason"
    return 0
  fi
  if [[ "${2:-0}" == 0 ]]; then
    reason="STOP BLOCKED — work this turn could not be fully recorded and .living/ was not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update ${HANDOFF_PATH}."
  fi
  printf '%s\n\n%s' "$reason" "$UNRECORDED_MSG"
}
mycelium_with_lineage_gap() {
  if [[ -n "$LINEAGE_GAP_MSG" ]]; then
    printf '%s\n\n%s' "$1" "$LINEAGE_GAP_MSG"
  else
    printf '%s' "$1"
  fi
}

# Hosts run Stop after every turn, so an accepted Stop usually ends a turn,
# not the host session. The next turn's work reopens a transaction from a
# PostToolUse hook, after its first command already ran, so SessionStart's
# snapshot at that moment would count that command's changes as pre-existing.
# Leave the next turn a baseline taken here instead: an idle turn's baseline
# is still exact (nothing changed against it), and a turn with work gets a
# fresh snapshot of where it ended. Reopen, resume, and compaction use it,
# together with the turn's start time (next-turn-start-ts.tmp).
TURN_HAD_WORK=1
BASELINE_CARRIED=false
mycelium__carry_is_scoped() {
  [[ "${MYCELIUM_SESSION_SCOPED:-false}" == true \
    && "$STATE_DIR" != "$MYCELIUM_SHARED_STATE_DIR" ]]
}

# Take a turn-with-work's successor snapshot before any active state is
# removed. It fails, and the caller blocks with the transaction intact, when
# the snapshot cannot be written: accepting without a successor baseline would
# let the next turn's shell-only edits go unenforced.
mycelium_prepare_carried_baseline() {
  local helper="${MYCELIUM_SESSION_CHANGES_HELPER:-$SCRIPT_DIR/scripts/session_file_changes.py}"
  local next="$STATE_DIR/next-turn-baseline.json.next"
  rm -f "$next"
  mycelium__carry_is_scoped || return 0
  [[ "$TURN_HAD_WORK" == 0 ]] && return 0
  [[ -f "$helper" ]] || return 1
  python3 "$helper" snapshot --repo-root "$REPO_ROOT" --output "$next" \
    >/dev/null 2>&1 && [[ -f "$next" && ! -L "$next" ]] || {
    rm -f "$next"
    return 1
  }
}

# Install what the next turn starts from: the prepared snapshot after a turn
# with work, or this idle turn's own baseline, which is still exact.
mycelium_carry_baseline_forward() {
  local baseline="$STATE_DIR/session-file-baseline.json"
  local carried="$STATE_DIR/next-turn-baseline.json"
  local next="$STATE_DIR/next-turn-baseline.json.next"
  [[ "$BASELINE_CARRIED" == false ]] || return 0
  BASELINE_CARRIED=true
  if ! mycelium__carry_is_scoped; then
    rm -f "$baseline"
    return 0
  fi
  rm -f "$carried"
  # The next turn begins now. Its reopened log starts from this time, not
  # from the reopen, so a commit its first command makes is still inside the
  # window session accounting reads commits from.
  printf '%s\n' "$(date +%s)" > "$STATE_DIR/next-turn-start-ts.tmp" 2>/dev/null || true
  if [[ -f "$next" && ! -L "$next" ]]; then
    mv -f "$next" "$carried" 2>/dev/null || rm -f "$next"
    rm -f "$baseline"
  elif [[ "$TURN_HAD_WORK" == 0 && -f "$baseline" && ! -L "$baseline" ]]; then
    mv -f "$baseline" "$carried" 2>/dev/null || rm -f "$baseline"
  else
    rm -f "$baseline"
  fi
}

mycelium_block_for_carried_baseline() {
  mycelium_emit_stop_block \
    "STOP BLOCKED — the baseline this session's next turn starts from could not be written (session_file_changes.py snapshot failed). Active state was preserved; retry Stop."
}

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
    local prev_dir="$MYCELIUM_SHARED_STATE_DIR/mycelium-data-events-prev"
    local first_event_id=""
    # A stream of only data commands belongs to a turn that may release its
    # session number, which the next session can take again; name its raw
    # archive by the stream's first event_id too, as its manifest is.
    if ! grep -qv '"kind": "data_command"' "$events_file" 2>/dev/null; then
      first_event_id=$(grep -o '"event_id": "[0-9a-f]*"' "$events_file" 2>/dev/null \
        | head -1 | sed -E 's/.*: "([0-9a-f]*)"/\1/' | cut -c1-12 || true)
      if [[ -n "$first_event_id" ]]; then
        session_id="${session_id}-data-commands-${first_event_id}"
      fi
    fi
    mkdir -p "$prev_dir"
    mv "$events_file" "$prev_dir/${session_id}.tmp"
    # Keep only the 20 most recent raw-event archives.
    ls -t "$prev_dir"/*.tmp 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
  fi
  rm -f "$session_marker"
}

mycelium_cleanup_accepted_scoped_state() {
  if [[ "${MYCELIUM_SESSION_SCOPED:-false}" != true \
    || "$STATE_DIR" == "$MYCELIUM_SHARED_STATE_DIR" ]]; then
    return 0
  fi
  # These are accepted-session artifacts only; retry-bearing state is retained
  # on every earlier failure/block path. Never recurse into an identity-derived
  # directory.
  rm -f "$STATE_DIR/last-session.md"
  rm -f "$STATE_DIR/mycelium-read-access.log"
  # This IDE can lose the hook response after finalization. Publish an
  # identity-bound receipt only after all acceptance effects have completed;
  # the native adapter can reconcile it without dispatching Stop again.
  local antigravity_host
  antigravity_host=$(mycelium_hook_host)
  if [[ "$antigravity_host" == "antigravity-ide" || "$antigravity_host" == "antigravity-app" ]]; then
    python3 "$HERE/../scripts/antigravity_hooks.py" --record-accepted-stop \
      "$REPO_ROOT" "$HOST_SESSION_ID" "$antigravity_host"
  fi
  mycelium_release_session_lock
  rmdir "$STATE_DIR" 2>/dev/null || true
}

# Resolve this hook's mycelium-core dir once, in absolute form. Used to locate
# the upsert script and the log-scribe template. BASH_SOURCE may be unset in
# weird invocations (e.g. `sh -c "$(...)"`), so fall back to $0; if even that
# fails, leave SCRIPT_DIR empty and downstream existence checks will skip.
HOOK_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR=$(cd "$(dirname "$(dirname "$HOOK_SOURCE")")" 2>/dev/null && pwd || echo "")
UPSERT_SCRIPT="${MYCELIUM_REGISTRY_UPSERT_HELPER:-$SCRIPT_DIR/scripts/upsert_registry_row.py}"
FINALIZE_LOG_SCRIPT="${MYCELIUM_LOG_FINALIZER_HELPER:-$SCRIPT_DIR/scripts/finalize_session_log.py}"
FINALIZE_HANDOFF_SCRIPT="${MYCELIUM_HANDOFF_FINALIZER_HELPER:-$SCRIPT_DIR/scripts/finalize_handoff.py}"

# --- Session log finalization ---
if [[ "$ACTIVE_MARKER_VALID" == true ]]; then
  OUR_TS=$(cat "$STATE_DIR/session-start-ts.tmp" 2>/dev/null || echo "")

  # Backward compatibility for an active session created before owner IDs were
  # introduced. New sessions use the per-host identity gate above.
  if [[ "$SESSION_OWNERSHIP" == "legacy" \
    && "$OWNER_TS" =~ ^[0-9]{1,18}$ \
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
      # SessionStart regenerates this shared derived index. A second root's
      # startup must not make an otherwise idle transaction look like user work.
      _CHANGE_ARGS+=(--exclude ".living/INDEX.md")
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
                ("$LOG_REPO"/*) printf '%s\n' "${activity_path#"$LOG_REPO"/}" ;;
                (*) printf '%s\n' "$activity_path" ;;
              esac
            done < "$ACTIVITY_FILE_CHECK"
          fi
        } | sed '/^[[:space:]]*$/d' | sort -u
      )
    fi
    FILES_CHANGED=0
    if [ -n "$SESSION_CHANGED_FILES" ]; then
      FILES_CHANGED=$(printf '%s\n' "$SESSION_CHANGED_FILES" | grep -c . || true)
      FILES_CHANGED=${FILES_CHANGED:-0}
    fi

    # Explicit Edit/Write activity is an independent work signal. The helper's
    # file set already includes committed and Bash-mutated paths.
    ACTIVITY_COUNT=0
    if [ -f "$ACTIVITY_FILE_CHECK" ]; then
      ACTIVITY_COUNT=$(sort -u "$ACTIVITY_FILE_CHECK" | grep -c . 2>/dev/null || true)
      ACTIVITY_COUNT=${ACTIVITY_COUNT:-0}
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
    TURN_HAD_WORK=$ENFORCEMENT_REQUIRED
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
        REASON="STOP BLOCKED — ${FILES_CHANGED} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update ${HANDOFF_PATH}."
        REASON=$(mycelium_with_unrecorded "$REASON" "$FILES_CHANGED")
        REASON=$(mycelium_with_lineage_gap "$REASON")
        ESCAPED_REASON=$(mycelium_json_dumps_stdin "$REASON" 2>/dev/null)
        mycelium__fast_write "{\"decision\": \"block\", \"reason\": $ESCAPED_REASON}"$'\n' || true
        exit 0
      fi
    fi

    # Last look before this transaction is discarded or finalized.
    if mycelium_block_for_late_unrecorded; then
      exit 0
    fi
    # Skip finalization only if NO evidence of work exists in any signal.
    # Lineage-only inline analyses still reserve their session ID and manifest.
    if [ "$ACTIVITY_COUNT" -eq 0 ] \
      && [ "$REMINDER_COUNT" -eq 0 ] \
      && [ "$FILES_CHANGED" -eq 0 ] \
      && [ "$LINEAGE_EVENT_COUNT" -eq 0 ]; then
      rm -f "$LOG_PATH"
      rm -f "$ACTIVE_LOG_FILE"
      rm -f "$ACTIVE_OWNER_FILE"
      rm -f "$STATE_DIR/session-start-ts.tmp"
      mycelium_carry_baseline_forward
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
      BRANCH=$({ grep '^branch:' "$LOG_PATH" || echo "branch: unknown"; } \
        | sed 's/^branch: *//' \
        | python3 -c '
import json
import sys

raw = sys.stdin.read().strip()
try:
    value = json.loads(raw)
except (TypeError, ValueError):
    value = raw
print(value if isinstance(value, str) and "\n" not in value else "unknown")
')
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
      # Prefer commit subjects when the agent has not already authored a row.
      # Compute the best machine fallback before the single preserving upsert;
      # otherwise a preliminary filename row looks "authored" to a second
      # upsert and accidentally defeats this deterministic summary.
      if [ -n "$START_TS" ] && [ "$START_TS" -gt 0 ] 2>/dev/null; then
        DETERMINISTIC_SUMMARY=$(
          { git -C "$LOG_REPO" log --since="@${START_TS}" --pretty=format:'%s' 2>/dev/null || true; } \
            | head -3 | tr '\n' ';' | sed 's/;$//; s/;/; /g'
        )
        if [ ${#DETERMINISTIC_SUMMARY} -gt 200 ]; then
          DETERMINISTIC_SUMMARY="${DETERMINISTIC_SUMMARY:0:197}..."
        fi
        if [ -n "$DETERMINISTIC_SUMMARY" ]; then
          SUMMARY="$DETERMINISTIC_SUMMARY"
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
          # --log-link folds a row the agent keyed by a guessed session ID
          # but linked to this session's log, so one session keeps one row.
          python3 "$UPSERT_SCRIPT" --preserve-authored --log-link "$LOG_BASENAME" \
            "$LOG_DIR/LOG_REGISTRY.md" "$SESSION_ID" "$NEW_ROW" \
            >/dev/null 2>"$LOG_DIR/.upsert_registry_row.err" \
            && { rm -f "$LOG_DIR/.upsert_registry_row.err"; REGISTRY_OK=true; }
        fi
      fi
      if [[ "$REGISTRY_OK" != true ]]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session registry finalization failed. The active log and session baselines were preserved; inspect .living/log/.upsert_registry_row.err or the helper installation, then retry Stop."
        exit 0
      fi

      # Auto-write last-session.md for next session context
      _SESSION_FILE="$STATE_DIR/last-session.md"
      _SESSION_COMPLETE=true
      _SESSION_FRESH=false
      _SESSION_MTIME=0
      if [ -f "$_SESSION_FILE" ] && [ ! -L "$_SESSION_FILE" ]; then
        _SESSION_MTIME=$(mycelium_file_mtime "$_SESSION_FILE" 2>/dev/null || echo "0")
        # Ask the finalizer to validate the exact body it would publish after
        # removing obsolete lifecycle prose. A pre-cleanup check can accept a
        # section whose only content is then deleted during finalization.
        if [ -f "$FINALIZE_HANDOFF_SCRIPT" ] \
          && python3 "$FINALIZE_HANDOFF_SCRIPT" \
            --handoff "$_SESSION_FILE" \
            --check-complete \
            >/dev/null 2>/dev/null; then
          _SESSION_COMPLETE=true
        else
          _SESSION_COMPLETE=false
        fi
        if [[ "$START_TS" =~ ^[0-9]+$ \
          && "$_SESSION_MTIME" =~ ^[0-9]+$ \
          && "$_SESSION_MTIME" -ge "$START_TS" ]]; then
          _SESSION_FRESH=true
        fi
      else
        _SESSION_COMPLETE=false
      fi

      # Preserve a complete handoff authored during this session. If it is
      # absent, stale, or partial, publish a complete deterministic fallback
      # atomically instead of replacing a rich handoff with two headings.
      if [[ "$_SESSION_COMPLETE" != true || "$_SESSION_FRESH" != true ]]; then
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
        _SESSION_TMP=$(mktemp "$STATE_DIR/.last-session.tmp.XXXXXX") || {
          mycelium_emit_stop_block \
            "STOP BLOCKED — session handoff finalization failed. Active state was preserved for retry."
          exit 0
        }
        if ! cat > "$_SESSION_TMP" << LAST_SESSION_EOF
SESSION RESUME — Last session ($(date '+%Y-%m-%d %H:%M')):

## What was worked on
- Completed the session work recorded in the finalized session log.
${_WORK_LINES}

## Key decisions made
- See \`.living/decisions.md\` for decisions recorded during this session.

## Blockers & surprises
- See the finalized session log and \`.living/learnings.md\` for recorded issues.

## Current state
- ${_BRANCH_NOTE}

## Next steps
- Review the finalized session log and continue from the current branch state.
LAST_SESSION_EOF
        then
          rm -f "$_SESSION_TMP"
          mycelium_emit_stop_block \
            "STOP BLOCKED — session handoff finalization failed. Active state was preserved for retry."
          exit 0
        fi
        if ! mv -f "$_SESSION_TMP" "$_SESSION_FILE"; then
          rm -f "$_SESSION_TMP"
          mycelium_emit_stop_block \
            "STOP BLOCKED — session handoff finalization failed. Active state was preserved for retry."
          exit 0
        fi
      fi

      if [ ! -f "$FINALIZE_HANDOFF_SCRIPT" ]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session handoff finalization failed. Active state was preserved for retry."
        exit 0
      fi

      # All other transaction participants have accepted the session. Publish
      # the completed frontmatter and matching footer with one atomic replace,
      # so SessionStart can never observe an ended log without its footer.
      # A retry sentinel distinguishes a fully accepted ended log from the
      # narrow state where the log committed but handoff publication did not.
      # It also pins the timestamp so a retry cannot make frontmatter/footer
      # disagree about when Stop was accepted.
      HANDOFF_PENDING_FILE="$STATE_DIR/handoff-finalization-pending.tmp"
      ENDED=""
      END_TIME_SHORT=""
      if [[ ( -e "$HANDOFF_PENDING_FILE" || -L "$HANDOFF_PENDING_FILE" ) \
        && ( ! -f "$HANDOFF_PENDING_FILE" || -L "$HANDOFF_PENDING_FILE" ) ]]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — handoff retry state is unsafe. Active state was preserved for repair."
        exit 0
      fi
      if [ -f "$HANDOFF_PENDING_FILE" ] && [ ! -L "$HANDOFF_PENDING_FILE" ]; then
        _HANDOFF_PENDING_LINES=$(awk 'END { print NR }' "$HANDOFF_PENDING_FILE" 2>/dev/null || true)
        if [ "$_HANDOFF_PENDING_LINES" = 2 ]; then
          ENDED=$(sed -n '1p' "$HANDOFF_PENDING_FILE" 2>/dev/null || true)
          END_TIME_SHORT=$(sed -n '2p' "$HANDOFF_PENDING_FILE" 2>/dev/null || true)
        fi
      fi
      if [[ ! "$ENDED" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4}$ \
        || ! "$END_TIME_SHORT" =~ ^[0-9]{2}:[0-9]{2}$ ]]; then
        ENDED=$(date +%Y-%m-%dT%H:%M:%S%z)
        END_TIME_SHORT=$(date +%H:%M)
        _HANDOFF_PENDING_TMP=$(mktemp "$STATE_DIR/.handoff-finalization-pending.tmp.XXXXXX") || {
          mycelium_emit_stop_block \
            "STOP BLOCKED — session handoff finalization could not reserve retry state. Active state was preserved."
          exit 0
        }
        if ! printf '%s\n%s\n' "$ENDED" "$END_TIME_SHORT" > "$_HANDOFF_PENDING_TMP" \
          || ! mv -f "$_HANDOFF_PENDING_TMP" "$HANDOFF_PENDING_FILE"; then
          rm -f "$_HANDOFF_PENDING_TMP"
          mycelium_emit_stop_block \
            "STOP BLOCKED — session handoff finalization could not reserve retry state. Active state was preserved."
          exit 0
        fi
      fi
      _FINALIZE_LOG_STATUS=0
      if [ ! -f "$FINALIZE_LOG_SCRIPT" ]; then
        _FINALIZE_LOG_STATUS=127
      else
        printf '%s' "$SESSION_CHANGED_FILES" \
          | python3 "$FINALIZE_LOG_SCRIPT" \
            --log-path "$LOG_PATH" \
            --ended "$ENDED" \
            --duration-minutes "$DURATION_MIN" \
            --files-changed "$FILES_CHANGED" \
            --end-time "$END_TIME_SHORT" \
            >/dev/null 2>"$LOG_DIR/.finalize_session_log.err" \
          || _FINALIZE_LOG_STATUS=$?
      fi
      if [ "$_FINALIZE_LOG_STATUS" -eq 3 ]; then
        # Status 3 is the finalizer's frontmatter-defect contract: the agent
        # rewrote or truncated the hook-owned log header. Name the log and the
        # cause in the block itself so the agent can repair it without reading
        # hook source or the .err sidecar.
        _FRONTMATTER_CAUSE=$(head -n 1 "$LOG_DIR/.finalize_session_log.err" 2>/dev/null \
          | tr -d '\r' | cut -c 1-200 || true)
        mycelium_emit_stop_block \
          "STOP BLOCKED — session log ${LOG_PATH#"$REPO_ROOT"/} lost its YAML frontmatter (${_FRONTMATTER_CAUSE:-header is missing or malformed}); restore the header block (session_id, project, branch, started, ended, duration_minutes, files_changed) or append to the log instead of rewriting it, then retry Stop."
        exit 0
      elif [ "$_FINALIZE_LOG_STATUS" -ne 0 ]; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session log finalization failed. The active log and session baselines were preserved; inspect the helper installation, then retry Stop."
        exit 0
      fi
      rm -f "$LOG_DIR/.finalize_session_log.err"

      # Only now is it truthful to publish an authoritative accepted state in
      # the rich handoff. If this atomic write fails, the pending sentinel keeps
      # SessionStart and Stop on the same retryable transaction.
      _FINALIZE_HANDOFF_ARGS=(
        --handoff "$_SESSION_FILE"
        --accepted-at "$ENDED"
      )
      if [[ "$STATE_DIR" != "$MYCELIUM_SHARED_STATE_DIR" ]]; then
        _FINALIZE_HANDOFF_ARGS+=(
          --publish-to "$MYCELIUM_SHARED_STATE_DIR/last-session.md"
        )
      fi
      if ! python3 "$FINALIZE_HANDOFF_SCRIPT" \
        "${_FINALIZE_HANDOFF_ARGS[@]}" \
        >/dev/null 2>"$LOG_DIR/.finalize_handoff.err"; then
        mycelium_emit_stop_block \
          "STOP BLOCKED — session handoff finalization failed. Active state was preserved for retry."
        exit 0
      fi
      rm -f "$LOG_DIR/.finalize_handoff.err"
      # Keep the active marker and retry sentinel until the shared enforcement
      # and lineage phase below accepts and archives the whole transaction.
    fi
  else
    # Log file doesn't exist (was deleted?) — clean up sentinels
    rm -f "$ACTIVE_LOG_FILE"
    rm -f "$ACTIVE_OWNER_FILE"
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
  rm -f "$ACTIVE_LOG_FILE"
  rm -f "$ACTIVE_OWNER_FILE"
  rm -f "$UNRECORDED_FILE"
  rm -f "$STATE_DIR/handoff-finalization-pending.tmp"
  rm -f "$STATE_DIR/session-file-baseline.json"
  rm -f "$STATE_DIR/living-reminder-baseline.json"
  mycelium_accept_lineage_session
  mycelium_cleanup_accepted_scoped_state
  exit 0
fi

# Check if any work was done this session.
# Work detected by: mycelium-reminded.tmp (analysis or Edit/Write, or an
# unrecorded work event taken above) or mycelium-session-activity.tmp.
REMINDER_FILE="$STATE_DIR/mycelium-reminded.tmp"
ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
if [ ! -f "$REMINDER_FILE" ] && [ ! -f "$ACTIVITY_FILE" ]; then
  if mycelium_block_for_late_unrecorded; then
    exit 0
  fi
  if ! mycelium_prepare_carried_baseline; then
    mycelium_block_for_carried_baseline
    exit 0
  fi
  mycelium_consume_reported_unrecorded
  rm -f "$ACTIVE_LOG_FILE"
  rm -f "$ACTIVE_OWNER_FILE"
  rm -f "$STATE_DIR/handoff-finalization-pending.tmp"
  rm -f "$STATE_DIR/session-start-ts.tmp"
  mycelium_carry_baseline_forward
  rm -f "$STATE_DIR/living-reminder-baseline.json"
  mycelium_accept_lineage_session
  mycelium_cleanup_accepted_scoped_state
  if [[ -n "$LINEAGE_GAP_MSG" ]]; then
    mycelium_emit_context "Stop" "$LINEAGE_GAP_MSG"
  fi
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
  FILE_COUNT=$(sort -u "$ACTIVITY_FILE" | grep -c . || true)
  FILE_COUNT=${FILE_COUNT:-0}
  FILE_NAMES=$(sort -u "$ACTIVITY_FILE" | head -15 | xargs -I {} basename {} 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
fi

# --- Session-end triage (short signals — full protocol is in the mycelium skill) ---

# If any was updated after the post-action hook fired, protocol was followed
if [ "$LIVING_UPDATED" = true ]; then
  if mycelium_block_for_late_unrecorded; then
    exit 0
  fi
  if ! mycelium_prepare_carried_baseline; then
    mycelium_block_for_carried_baseline
    exit 0
  fi
  mycelium_consume_reported_unrecorded
  # Clean up reminder file — cycle complete
  rm -f "$REMINDER_FILE"
  rm -f "$ACTIVITY_FILE"
  rm -f "$ACTIVE_LOG_FILE"
  rm -f "$ACTIVE_OWNER_FILE"
  rm -f "$STATE_DIR/handoff-finalization-pending.tmp"
  rm -f "$STATE_DIR/session-start-ts.tmp"
  mycelium_carry_baseline_forward
  rm -f "$STATE_DIR/living-reminder-baseline.json"

  ENHANCE_MSG=".living/ updated. The accepted handoff was published to ${MYCELIUM_SHARED_STATE_DIR}/last-session.md, and the deterministic LOG_REGISTRY summary is in place."
  ENHANCE_MSG=$(mycelium_with_lineage_gap "$ENHANCE_MSG")
  mycelium_emit_context "Stop" "$ENHANCE_MSG"
  mycelium_accept_lineage_session
  mycelium_cleanup_accepted_scoped_state
  exit 0
fi

# Block: work happened but .living/ was never updated
REASON="STOP BLOCKED — ${FILE_COUNT} files changed (${FILE_NAMES}) but .living/ not updated. Run mycelium session-end protocol: triage to learnings/decisions/conventions/findings, then update ${HANDOFF_PATH}."
REASON=$(mycelium_with_unrecorded "$REASON" "$FILE_COUNT")
REASON=$(mycelium_with_lineage_gap "$REASON")

ESCAPED_REASON=$(mycelium_json_dumps_stdin "$REASON" 2>/dev/null)
# A host that stopped reading must not keep the hook from releasing its locks.
mycelium__fast_write "{\"decision\": \"block\", \"reason\": $ESCAPED_REASON}"$'\n' || true
