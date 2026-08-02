#!/usr/bin/env bash
# mycelium-data-tracker.sh — Claude Code PostToolUse hook (Bash matcher)
# Detects analysis invocations (python/R/Rscript/jupyter/uv/poetry), regex-
# extracts the script's data I/O at execution time, SHA-snapshots the script
# + touched files, and appends one NDJSON event per detected script to
# .mycelium/mycelium-data-events.tmp under fcntl.flock. Consumed at Stop by
# mycelium-data-lineage-stop.sh -> extract_data_lineage.py to assemble the
# per-session manifest at .living/log/data-lineage/<sid>.json.
#
# Independent of the mycelium scribe / .living/-updated logic — fires only
# when actual data analysis is detected.
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

  COMMAND=$(printf '%s' "$INPUT" | mycelium_json_get 'tool_input.command')
  if [[ -z "$COMMAND" ]]; then exit 0; fi

  SESSION_CWD=$(printf '%s' "$INPUT" | mycelium_json_get 'cwd')
  if [[ -z "$SESSION_CWD" ]] || [[ ! -d "$SESSION_CWD" ]]; then exit 0; fi

  # Resolve REPO_ROOT from session cwd (not hook cwd — same lesson as the
  # other trackers). Bail unless the repo has .living/ (mycelium-enabled).
  REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]] || [[ ! -d "$REPO_ROOT/.living" ]]; then exit 0; fi
  mycelium_prepare_post_tool_state "$REPO_ROOT" "$INPUT" || exit 0

  AGENT_ID=$(printf '%s' "$INPUT" | mycelium_json_get 'agent_id')
  AGENT_TYPE=$(printf '%s' "$INPUT" | mycelium_json_get 'agent_type')
  BASH_EXIT=$(printf '%s' "$INPUT" | mycelium_bash_exit)
  if [[ -z "$BASH_EXIT" && "$(mycelium_hook_host)" == "claude" ]]; then
    # Claude Code invokes this PostToolUse registration only for successful
    # Bash calls; failures use its separate failure event.
    BASH_EXIT=0
  fi

  # Locate the Python extractor helper. Default: sibling in skills/core/scripts/.
  HELPER="${MYCELIUM_DATA_HELPER:-$HERE/../scripts/extract_data_lineage_event.py}"
  if [[ ! -f "$HELPER" ]]; then exit 0; fi

  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  EVENTS_FILE="$STATE_DIR/mycelium-data-events.tmp"
  mkdir -p "$STATE_DIR"

  # Build optional flags conditionally. macOS bash 3.2 treats empty-array
  # expansion as unbound under `set -u`, so use the ${arr+"${arr[@]}"} idiom
  # which expands to nothing when the array is empty.
  AGENT_FLAGS=()
  if [[ -n "$AGENT_ID" ]]; then AGENT_FLAGS+=(--agent-id "$AGENT_ID"); fi
  if [[ -n "$AGENT_TYPE" ]]; then AGENT_FLAGS+=(--agent-type "$AGENT_TYPE"); fi
  if [[ "$BASH_EXIT" =~ ^-?[0-9]+$ ]]; then
    AGENT_FLAGS+=(--bash-exit "$BASH_EXIT")
  fi

  # --append-to delegates the file write to Python so we get fcntl.flock-
  # protected appends. Shell `>>` is only atomic for writes <= PIPE_BUF, and
  # embedded script_source can push lines past that. Concurrent Bash tools
  # firing PostToolUse hooks in parallel would otherwise interleave bytes.
  python3 "$HELPER" \
    --cwd "$SESSION_CWD" \
    --ts "$TS" \
    --bash-cmd "$COMMAND" \
    --append-to "$EVENTS_FILE" \
    ${AGENT_FLAGS[@]+"${AGENT_FLAGS[@]}"} 2>/dev/null || exit 0
} 2>/dev/null || true

exit 0
