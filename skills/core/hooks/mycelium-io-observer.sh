#!/usr/bin/env bash
# mycelium-io-observer.sh — tool-call observation for data lineage.
#
# PreToolUse (Bash and edit tools): opens the call's interval in the
# repository call journal (.mycelium/io/calls.ndjson) and, for Bash, takes a
# stat snapshot of the watched scope so the Post can say which files the
# call changed. PostToolUse / PostToolUseFailure for edit tools closes their
# interval. The Bash Post runs from mycelium-data-tracker.sh, so the lineage
# record it writes can name the observation.
#
# Observation only: this hook never prints, never emits a permission
# decision, and always exits 0, so it can neither block nor approve a tool.
# It writes only under .mycelium/io/ after the shared-state containment and
# symlink checks pass, and needs no lifecycle transaction (it is not session
# work and never reopens one).
#
# Install: registered by init_repo.install_claude_hooks() (Claude) and
#   hooks/hooks.json (Codex). Input: the host's hook payload on stdin.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh" 2>/dev/null || exit 0

{
  INPUT=$(cat)
  [[ -n "$INPUT" ]] || exit 0
  mycelium_payload_preload INPUT 'get:cwd'
  SESSION_CWD=$(mycelium_json_get 'cwd' INPUT)
  if [[ -z "$SESSION_CWD" || ! -d "$SESSION_CWD" ]]; then exit 0; fi
  REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" || -L "$REPO_ROOT/.living" || ! -d "$REPO_ROOT/.living" ]]; then
    exit 0
  fi
  SHARED=$(mycelium__checked_shared_state_dir "$REPO_ROOT") || exit 0
  OBSERVER="${MYCELIUM_IO_OBSERVER:-$HERE/../scripts/io_observer.py}"
  [[ -f "$OBSERVER" ]] || exit 0
  printf '%s' "$INPUT" | python3 "$OBSERVER" hook \
    --state-dir "$SHARED" --repo-root "$REPO_ROOT" --host "$(mycelium_hook_host)"
} >/dev/null 2>&1

exit 0
