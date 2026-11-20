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

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

{
  INPUT=$(cat)
  mycelium_payload_preload INPUT 'get:tool_input.command' 'get:cwd' 'opt:session_id' 'get:agent_id' 'get:agent_type' 'exit:'

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
  BASH_EXIT=$(mycelium_bash_exit INPUT)
  if [[ -z "$BASH_EXIT" && "$(mycelium_hook_host)" == "claude" ]]; then
    # Claude Code invokes this PostToolUse registration only for successful
    # Bash calls; failures use its separate failure event.
    BASH_EXIT=0
  fi

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
      ${DATA_COMMAND_FLAGS[@]+"${DATA_COMMAND_FLAGS[@]}"} \
      ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"} >/dev/null 2>&1 || CHECK_STATUS=$?
    if (( CHECK_STATUS == 1 )); then
      exit 0
    elif (( CHECK_STATUS != 0 )); then
      mycelium_record_unowned_lineage_skip "$REPO_ROOT" "$INPUT" "$COMMAND" \
        "detector-failed" "exit $CHECK_STATUS"
      exit 0
    fi
    if ! mycelium_reopen_post_tool_transaction "$REPO_ROOT" "$INPUT" \
      || ! mycelium_prepare_locked_post_tool_state "$REPO_ROOT" "$INPUT"; then
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
  EXTRACTOR_OUT=$(python3 "$HELPER" \
    --cwd "$SESSION_CWD" \
    --ts "$TS" \
    --bash-cmd "$COMMAND" \
    --append-to "$EVENTS_FILE" \
    ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} \
    ${EXIT_FLAGS[@]+"${EXIT_FLAGS[@]}"} 2>/dev/null) || EXTRACTOR_STATUS=$?
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
