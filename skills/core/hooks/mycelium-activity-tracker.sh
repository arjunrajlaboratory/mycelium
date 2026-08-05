#!/usr/bin/env bash
# mycelium-activity-tracker.sh — Claude/Codex PostToolUse edit hook
# Tracks file modifications so the stop hook enforces .living/ updates for ALL
# sessions with meaningful work, not just analysis execution.
#
# The existing mycelium-post-action.sh only fires on Bash commands matching
# Python/R/Jupyter patterns. This hook closes the gap for Edit/Write operations.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Edit|Write"
# Input: Claude provides tool_input.file_path. Codex apply_patch provides
# tool_input.command with one or more "*** <Action> File:" headers.
# Output: Silent (no additionalContext) — enforcement happens at Stop hook

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

INPUT=$(cat)

# PostToolUse payloads describe attempted edits as well as successful ones.
# Ignore an explicitly failed result; an absent status remains compatible with
# Claude's Edit/Write payloads and older Codex versions.
TOOL_SUCCEEDED=$(printf '%s' "$INPUT" | python3 -c '
import json, re, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    print("unknown")
    raise SystemExit
response = payload.get("tool_response")

def structured_exit_code(value):
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("success") is False:
            return 1
        for key in ("exit_code", "exitCode", "return_code", "returncode"):
            candidate = value.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
        for candidate in value.values():
            found = structured_exit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = structured_exit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            decoded = None
        if decoded is not None and decoded != value:
            found = structured_exit_code(decoded)
            if found is not None:
                return found
    return None

def textual_exit_code(value):
    if isinstance(value, dict):
        for candidate in value.values():
            found = textual_exit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = textual_exit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            decoded = None
        if decoded is not None and decoded != value:
            found = textual_exit_code(decoded)
            if found is not None:
                return found
        if re.match(
            r"^\s*(?:error|failed|patch failed|invalid (?:context|patch))\b",
            value,
            re.IGNORECASE,
        ):
            return 1
        match = re.search(
            r"(?:exit(?:ed)?(?:[ _-]with)?(?:[ _-]code)?|return(?:[ _-]code)?)"
            r"[\":= ]+(-?\d+)",
            value,
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1))
    return None

status = structured_exit_code(response)
if status is None:
    status = textual_exit_code(response)
print("unknown" if status is None else ("true" if status == 0 else "false"))
')
if [[ "$TOOL_SUCCEEDED" == false ]]; then
  exit 0
fi

# --- Repo and .living/ checks ---

SESSION_CWD=$(printf '%s' "$INPUT" | mycelium_json_get 'cwd')
if [[ -z "$SESSION_CWD" ]]; then
  SESSION_CWD=$(pwd)
fi
REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi
mycelium_prepare_locked_post_tool_state "$REPO_ROOT" "$INPUT" || exit 0
trap mycelium_release_session_lock EXIT

# Only enforce in mycelium-enabled repos
if [[ ! -d "$REPO_ROOT/.living" ]]; then
  exit 0
fi

# --- Accumulate modified files for session summary ---

mkdir -p "$STATE_DIR"
ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
touched=false
while IFS= read -r FILE_PATH; do
  [[ -z "$FILE_PATH" ]] && continue

  # Avoid circular enforcement and ignore local agent/runtime configuration.
  if [[ "$FILE_PATH" == .living/* || "$FILE_PATH" == *"/.living/"* ||
        "$FILE_PATH" == .mycelium/* || "$FILE_PATH" == *"/.mycelium/"* ||
        "$FILE_PATH" == .claude/* || "$FILE_PATH" == *"/.claude/"* ||
        "$FILE_PATH" == .codex/* || "$FILE_PATH" == *"/.codex/"* ]]; then
    continue
  fi

  if [[ "$FILE_PATH" == *"/node_modules/"* ||
        "$FILE_PATH" == *"/__pycache__/"* ||
        "$FILE_PATH" == *".pyc" ||
        "$FILE_PATH" == *".lock" ]]; then
    continue
  fi

  if ! grep -qxF "$FILE_PATH" "$ACTIVITY_FILE" 2>/dev/null; then
    printf '%s\n' "$FILE_PATH" >> "$ACTIVITY_FILE"
  fi
  touched=true
done < <(printf '%s' "$INPUT" | python3 -c '
import json, os, re, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
session_cwd = os.path.realpath(sys.argv[1])
repo_root = os.path.realpath(sys.argv[2])

def emit(raw_path):
    if not isinstance(raw_path, str) or not raw_path:
        return
    path = os.path.expanduser(raw_path)
    if not os.path.isabs(path):
        path = os.path.join(session_cwd, path)
    path = os.path.realpath(path)
    relative = os.path.relpath(path, repo_root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return
    print(relative)

tool_input = payload.get("tool_input") or {}
path = tool_input.get("file_path")
emit(path)
command = tool_input.get("command")
if isinstance(command, str):
    for line in command.splitlines():
        match = re.match(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", line)
        if match:
            emit(match.group(1))
' "$SESSION_CWD" "$REPO_ROOT")

if [[ "$touched" != true ]]; then
  exit 0
fi

# --- Start or refresh the lifecycle work cycle ---

# The baseline fingerprints .living/ content before the agent records this
# work. If the prior cycle was already documented, refresh it so subsequent
# edits cannot be accepted using an older lifecycle update.
SESSION_CHANGES_HELPER="${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"
mycelium_refresh_work_cycle "$REPO_ROOT" "$SESSION_CHANGES_HELPER" || true

exit 0
