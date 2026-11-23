#!/usr/bin/env bash
# mycelium-data-tracker.sh — Claude Code PostToolUse hook (Bash matcher)
# Detects analysis invocations (python/R/Rscript/jupyter/uv/poetry, plus
# shell-script, snakemake, sbatch, and nextflow runners), regex-
# extracts the script's data I/O at execution time, SHA-snapshots the script
# + touched files, and appends one NDJSON event per detected script to
# .mycelium/mycelium-data-events.tmp under fcntl.flock. Any other Bash call
# that touches data (shell operations on data paths, inline reads the
# detector rejects) appends one minimal `"kind": "data_command"` record. Consumed at Stop by
# mycelium-data-lineage-stop.sh -> extract_data_lineage.py to assemble the
# per-session manifest at .living/log/data-lineage/<sid>.json.
#
# Independent of the mycelium scribe / .living/-updated logic — records only
# commands that run an analysis or touch data.
#
# Install: registered automatically by init_repo.install_claude_hooks() under
#   PostToolUse with matcher "Bash". Innermost-wins: subproject settings need
#   the complete bundle.
# Input: JSON on stdin: {tool_input:{command}, cwd, agent_id?, agent_type?, ...}
# Output: Silent (no stdout, no additionalContext).
# Env override: MYCELIUM_DATA_HELPER may point at an alternate event extractor.
# Strict capture: only the durable Antigravity adapters set
# MYCELIUM_STRICT_CAPTURE=1 with MYCELIUM_HOOK_HOST=antigravity-ide or
# antigravity-app. Then a missing or failing extractor in an owned transaction
# exits nonzero with its diagnostic on stderr, so the adapter retains an
# unresolved outcome instead of the skip record other hosts get (the adapter
# reports that gap itself). Every other host keeps the fail-open behavior
# below; intentional no-ops and gaps recorded as skips still exit 0.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

STRICT_CAPTURE=0
if [[ "${MYCELIUM_STRICT_CAPTURE:-}" == "1" ]] \
  && [[ "$(mycelium_hook_host)" == "antigravity-ide" || "$(mycelium_hook_host)" == "antigravity-app" ]]; then
  STRICT_CAPTURE=1
fi
# The block below silences stderr; strict diagnostics, and the shared
# unrecorded-event diagnostic, use the original stream.
exec 3>&2
MYCELIUM_HOOK_DIAG_FD=3
# An event this hook could not record is lineage, not session work.
MYCELIUM_POST_TOOL_EVENT_KIND=lineage

{
  INPUT=$(cat)
  mycelium_payload_preload INPUT 'get:tool_input.command' 'get:cwd' 'opt:session_id' \
    'get:agent_id' 'get:agent_type' 'exit:' 'opt:tool_use_id' 'opt:hook_event_name' \
    'get:tool_input.run_in_background' 'opt:error' 'get:tool_response.backgroundTaskId'

  COMMAND=$(mycelium_json_get 'tool_input.command' INPUT)
  if [[ -z "$COMMAND" ]]; then exit 0; fi

  SESSION_CWD=$(mycelium_json_get 'cwd' INPUT)
  if [[ -z "$SESSION_CWD" ]] || [[ ! -d "$SESSION_CWD" ]]; then exit 0; fi

  # Resolve REPO_ROOT from session cwd (not hook cwd — same lesson as the
  # other trackers). Bail unless the repo has .living/ (mycelium-enabled).
  REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]] || [[ ! -d "$REPO_ROOT/.living" ]]; then exit 0; fi

  AGENT_ID=$(mycelium_json_get 'agent_id' INPUT)
  AGENT_TYPE=$(mycelium_json_get 'agent_type' INPUT)
  HOOK_EVENT=$(mycelium_json_get_optional_string 'hook_event_name' INPUT) || HOOK_EVENT=""
  OUTCOME=unknown
  # A background launch (the agent asked for one, or the host moved a long
  # command to the background and returned a backgroundTaskId) fires
  # PostToolUse when the job starts, not when it ends: its exit status is
  # unknown, so nothing may claim the job's writes as completed.
  BACKGROUND_LAUNCH=0
  if [[ "$(mycelium_json_get 'tool_input.run_in_background' INPUT)" == "True" \
    || -n "$(mycelium_json_get 'tool_response.backgroundTaskId' INPUT)" ]]; then
    BACKGROUND_LAUNCH=1
  fi
  if [[ "$BACKGROUND_LAUNCH" == 1 && "$HOOK_EVENT" != "PostToolUseFailure" ]]; then
    BASH_EXIT=""
  elif [[ "$HOOK_EVENT" == "PostToolUseFailure" ]]; then
    # Claude Code's failure event carries no tool_response; its `error`
    # text starts "Exit code N" for an ordinary nonzero exit. An interrupt
    # or timeout may carry no code: the exit stays unknown, never 0.
    OUTCOME=failure
    FAILURE_TEXT=$(mycelium_json_get_optional_string 'error' INPUT) || FAILURE_TEXT=""
    BASH_EXIT=""
    if [[ "$FAILURE_TEXT" =~ ^Exit\ code\ (-?[0-9]+) ]]; then
      BASH_EXIT="${BASH_REMATCH[1]}"
    fi
  elif [[ "$(mycelium_hook_host)" == "claude" \
    && ( -z "$HOOK_EVENT" || "$HOOK_EVENT" == "PostToolUse" ) ]]; then
    # Claude Code invokes PostToolUse only for successful Bash calls;
    # failures arrive through PostToolUseFailure, handled above. Its
    # response carries no exit field, so any decoded number would come from
    # the command's own output: the event itself is the exit evidence.
    BASH_EXIT=0
  else
    BASH_EXIT=$(mycelium_bash_exit INPUT)
  fi
  if [[ "$BASH_EXIT" == 0 ]]; then
    OUTCOME=success
  elif [[ "$BASH_EXIT" =~ ^-?[0-9]+$ ]]; then
    OUTCOME=failure
  fi

  # Observe which files this call changed, before any session gating: a
  # session-lock timeout, a missing transaction, or a foreign owner must
  # not cost the observation. It needs no transaction and is not session
  # work; its record goes to this host session's own observation store.
  OBSERVATION_FLAGS=()
  OBS_SID=$(mycelium_json_get_optional_string 'session_id' INPUT) || OBS_SID=""
  OBS_CALL=$(mycelium_json_get_optional_string 'tool_use_id' INPUT) || OBS_CALL=""
  # Every skip this call records names the call, so the lineage index can
  # match it to the call's observation instead of treating it as a gap.
  export MYCELIUM__SKIP_HOST MYCELIUM__SKIP_SESSION MYCELIUM__SKIP_CALL
  MYCELIUM__SKIP_HOST="$(mycelium_hook_host)"
  MYCELIUM__SKIP_SESSION="$OBS_SID"
  MYCELIUM__SKIP_CALL="$OBS_CALL"
  OBSERVER="${MYCELIUM_IO_OBSERVER:-$HERE/../scripts/io_observer.py}"
  if [[ -n "$OBS_SID" && -n "$OBS_CALL" && -f "$OBSERVER" ]] \
    && mycelium_valid_session_id "$OBS_SID" \
    && OBS_SHARED=$(mycelium__checked_shared_state_dir "$REPO_ROOT"); then
    OBS_ARGS=(post --state-dir "$OBS_SHARED" --host "$(mycelium_hook_host)"
      --session "$OBS_SID" --call-id "$OBS_CALL" --tool Bash
      --repo-root "$REPO_ROOT" --outcome "$OUTCOME" --bash-cmd "$COMMAND")
    if [[ -n "$AGENT_ID" ]]; then OBS_ARGS+=(--agent-id "$AGENT_ID"); fi
    if [[ "$BASH_EXIT" =~ ^-?[0-9]+$ ]]; then OBS_ARGS+=(--bash-exit "$BASH_EXIT"); fi
    # Background evidence from either side: the agent asked for it, or the
    # host moved a long foreground command to the background at its
    # timeout (Claude Code then returns a backgroundTaskId). Either way the
    # job may still be writing, so its interval stays open.
    OBS_TASK_ID=$(mycelium_json_get 'tool_response.backgroundTaskId' INPUT)
    if [[ "$BACKGROUND_LAUNCH" == 1 ]]; then
      OBS_ARGS+=(--background)
      # The host's task id lets a later Stop snapshot end the job.
      if [[ "$OBS_TASK_ID" =~ ^[A-Za-z0-9_.:-]{1,128}$ ]]; then
        OBS_ARGS+=(--task-id "$OBS_TASK_ID")
      fi
    fi
    OBS_SUMMARY=$(python3 "$OBSERVER" "${OBS_ARGS[@]}" 2>/dev/null) \
      || OBS_SUMMARY='{"status": "unavailable", "reason": "observer-failed"}'
    [[ -n "$OBS_SUMMARY" ]] \
      || OBS_SUMMARY='{"status": "unavailable", "reason": "observer-silent"}'
    OBSERVATION_FLAGS=(--observation-json "$OBS_SUMMARY")
  fi
  # The call's outcome reaches the lineage record even without an exit
  # code (an interrupted or timed-out call is a failure, not a success).
  OBSERVATION_FLAGS+=(--outcome "$OUTCOME")

  # Locate the Python extractor helper. Default: sibling in skills/core/scripts/.
  HELPER="${MYCELIUM_DATA_HELPER:-$HERE/../scripts/extract_data_lineage_event.py}"

  # Build optional flags conditionally. macOS bash 3.2 treats empty-array
  # expansion as unbound under `set -u`, so use the ${arr+"${arr[@]}"} idiom
  # which expands to nothing when the array is empty.
  AGENT_FLAGS=()
  if [[ -n "$AGENT_ID" ]]; then AGENT_FLAGS+=(--agent-id "$AGENT_ID"); fi
  if [[ -n "$AGENT_TYPE" ]]; then AGENT_FLAGS+=(--agent-type "$AGENT_TYPE"); fi
  EXIT_FLAGS=()
  if [[ "$BASH_EXIT" =~ ^-?[0-9]+$ ]]; then
    EXIT_FLAGS+=(--bash-exit "$BASH_EXIT")
  fi

  if ! mycelium_prepare_locked_post_tool_state "$REPO_ROOT" "$INPUT"; then
    # The transaction exists but its session lock was unavailable: the
    # helper already left the evidence Stop reports. Reopening would only
    # wait on the same lock again.
    if [[ "$MYCELIUM_POST_TOOL_UNRECORDED" == 1 ]]; then
      exit 0
    fi
    # No transaction for this host session. Hosts run Stop after every turn,
    # so a live session whose earlier turn was accepted lands here. Reopen it
    # only for a command that would record lineage (a proven analysis run).
    # A detector that cannot answer is a gap, not a "no", and is recorded as
    # one. A data command (`head data/x.csv`, an inline read the detector
    # rejects) does not reopen: it is not work the lifecycle must account
    # for. Its minimal record waits in the shared unowned file, keyed by host
    # session, until this session's next owned Stop moves it into a stream.
    if [[ ! -f "$HELPER" ]]; then
      mycelium_record_unowned_lineage_skip "$REPO_ROOT" "$INPUT" "$COMMAND" \
        "extractor-missing" "no command could be checked: $HELPER" once
      exit 0
    fi
    DATA_COMMAND_FLAGS=()
    UNOWNED_SID=$(mycelium_json_get_optional_string 'session_id' INPUT) || UNOWNED_SID=""
    if [[ -n "$UNOWNED_SID" ]] && mycelium_valid_session_id "$UNOWNED_SID" \
      && UNOWNED_SHARED=$(mycelium__checked_shared_state_dir "$REPO_ROOT"); then
      DATA_COMMAND_FLAGS=(
        --data-command-to "$UNOWNED_SHARED/lineage-skips-unowned.ndjson"
        --host "$(mycelium_hook_host)" --host-session-id "$UNOWNED_SID"
        ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"}
      )
    fi
    CHECK_STATUS=0
    python3 "$HELPER" \
      --cwd "$SESSION_CWD" \
      --ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --bash-cmd "$COMMAND" \
      --check-execution \
      --repo-root "$REPO_ROOT" \
      ${DATA_COMMAND_FLAGS[@]+"${DATA_COMMAND_FLAGS[@]}"} \
      ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"} \
      ${OBSERVATION_FLAGS[@]+"${OBSERVATION_FLAGS[@]}"} >/dev/null 2>&1 || CHECK_STATUS=$?
    if (( CHECK_STATUS == 1 )); then
      exit 0
    elif (( CHECK_STATUS != 0 )); then
      mycelium_record_unowned_lineage_skip "$REPO_ROOT" "$INPUT" "$COMMAND" \
        "detector-failed" "exit $CHECK_STATUS"
      exit 0
    fi
    if ! mycelium_reopen_post_tool_transaction "$REPO_ROOT" "$INPUT" \
      || ! mycelium_prepare_locked_post_tool_state "$REPO_ROOT" "$INPUT"; then
      # A reopened transaction whose lock was then unavailable already holds
      # its evidence; only a refused reopen is an unowned gap.
      if [[ "$MYCELIUM_POST_TOOL_UNRECORDED" == 1 ]]; then
        exit 0
      fi
      mycelium_record_unowned_lineage_skip "$REPO_ROOT" "$INPUT" "$COMMAND" \
        "no-transaction" "reopen refused or failed"
      exit 0
    fi
  fi
  trap mycelium_release_session_lock EXIT

  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  EVENTS_FILE="$STATE_DIR/mycelium-data-events.tmp"
  mkdir -p "$STATE_DIR"

  if [[ ! -f "$HELPER" ]]; then
    if [[ "$STRICT_CAPTURE" == "1" ]]; then
      printf 'Mycelium data capture failed: extractor not found: %s\n' "$HELPER" >&3
      exit 70
    fi
    # Without the extractor no command can be classified, so this is one gap
    # for the session, recorded once, not a lost data command per Bash call.
    mycelium_record_lineage_skip "$EVENTS_FILE" "$TS" "$COMMAND" \
      "extractor-missing" "no command could be checked: $HELPER" \
      ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"} \
      --once true
    exit 0
  fi

  # --append-to delegates the file write to Python so we get fcntl.flock-
  # protected appends. Shell `>>` is only atomic for writes <= PIPE_BUF, and
  # embedded script_source can push lines past that. Concurrent Bash tools
  # firing PostToolUse hooks in parallel would otherwise interleave bytes.
  # A failed extractor used to vanish here; it now leaves a skip record in
  # the same stream, so the manifest shows the gap instead of nothing.
  EXTRACTOR_STATUS=0
  EXTRACTOR_ERR=/dev/null
  if [[ "$STRICT_CAPTURE" == "1" ]]; then
    EXTRACTOR_ERR=$(mktemp "${TMPDIR:-/tmp}/mycelium-extractor-err.XXXXXX")
  fi
  # Identity for the v2 record: host, host session, and the root its paths
  # are framed against. The host is kept even without a session id.
  IDENTITY_FLAGS=(--host "$(mycelium_hook_host)" --repo-root "$REPO_ROOT")
  if [[ -n "$OBS_SID" ]] && mycelium_valid_session_id "$OBS_SID"; then
    IDENTITY_FLAGS+=(--host-session-id "$OBS_SID")
  fi
  EXTRACTOR_OUT=$(python3 "$HELPER" \
    --cwd "$SESSION_CWD" \
    --ts "$TS" \
    --bash-cmd "$COMMAND" \
    --append-to "$EVENTS_FILE" \
    "${IDENTITY_FLAGS[@]}" \
    ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} \
    ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"} \
    ${OBSERVATION_FLAGS[@]+"${OBSERVATION_FLAGS[@]}"} 2>"$EXTRACTOR_ERR") || EXTRACTOR_STATUS=$?
  if [[ "$STRICT_CAPTURE" == "1" ]]; then
    EXTRACTOR_DIAG=$(tail -c 1500 "$EXTRACTOR_ERR" 2>/dev/null || true)
    rm -f "$EXTRACTOR_ERR"
    if (( EXTRACTOR_STATUS != 0 )); then
      printf 'Mycelium data capture failed: extractor exited %s: %s\n' \
        "$EXTRACTOR_STATUS" "$EXTRACTOR_DIAG" >&3
      exit 70
    fi
  fi
  if (( EXTRACTOR_STATUS != 0 )); then
    mycelium_record_lineage_skip "$EVENTS_FILE" "$TS" "$COMMAND" \
      "extractor-failed" "exit $EXTRACTOR_STATUS" \
      ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"}
  elif [[ -n "$EXTRACTOR_OUT" ]]; then
    # The extractor names each detected script it could not read (gone or
    # unreadable when the hook ran): an unknown execution, recorded as a gap.
    while IFS= read -r UNREADABLE_LINE; do
      [[ -n "$UNREADABLE_LINE" ]] || continue
      UNREADABLE_PATH=$(python3 -c \
        'import json, sys; print(json.loads(sys.argv[1])["unreadable_script"])' \
        "$UNREADABLE_LINE" 2>/dev/null) || continue
      mycelium_record_lineage_skip "$EVENTS_FILE" "$TS" "$COMMAND" \
        "script-unreadable" "$UNREADABLE_PATH" \
        ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"}
    done <<<"$EXTRACTOR_OUT"
  fi
} 2>/dev/null || true

exit 0
