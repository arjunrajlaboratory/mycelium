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

# Optional structured batch (Antigravity App Stop recovery only): tool_input.file_paths
# plus mycelium_activity_batch {id, count, sha256}. Every entry must already be an
# exact canonical eligible path inside the repository; the whole batch is refused
# before any state change otherwise. An acknowledgement bound to the exact batch is
# printed only after every entry was recorded. Legacy single-path/patch input is
# unchanged and silent.
BATCH=0
BATCH_ACK=""
BATCH_COUNT=0
if [[ "$INPUT" == *'"mycelium_activity_batch"'* ]]; then
  BATCH=1
fi
batch_fail() {
  printf 'Mycelium activity batch rejected: %s\n' "$1" >&2
  exit "${2:-65}"
}
if [[ "$BATCH" == 1 && "$(mycelium_hook_host)" != "antigravity-app" ]]; then
  batch_fail "batch input is supported only for the Antigravity App host"
fi

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
  [[ "$BATCH" == 1 ]] && batch_fail "session cwd is not inside a repository" 70
  exit 0
fi
if [[ "$BATCH" == 1 ]]; then
  [[ -d "$REPO_ROOT/.living" ]] || batch_fail "repository has no .living opt-in" 70
  # Validate and bind the whole batch before any activity or lifecycle write.
  BATCH_RESULT=$(printf '%s' "$INPUT" | python3 -c '
import hashlib, json, os, sys
repo_root = os.path.realpath(sys.argv[1])
def bad(reason):
    print(reason)
    raise SystemExit(65)
try:
    payload = json.load(sys.stdin)
except Exception:
    bad("payload is not JSON")
batch = payload.get("mycelium_activity_batch") if isinstance(payload, dict) else None
tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
if not isinstance(batch, dict) or set(batch) != {"id", "count", "sha256"}:
    bad("malformed batch identity")
if not isinstance(tool_input, dict) or set(tool_input) != {"file_paths"}:
    bad("batch tool_input must contain only file_paths")
ident, count, digest_in, paths = batch["id"], batch["count"], batch["sha256"], tool_input["file_paths"]
if not isinstance(ident, str) or not 0 < len(ident) <= 200 or any(c in ident for c in "\0\r\n"):
    bad("invalid batch id")
if not isinstance(count, int) or isinstance(count, bool) or not isinstance(digest_in, str):
    bad("invalid batch count or digest")
if not isinstance(paths, list) or not 0 < len(paths) <= 256 or any(not isinstance(p, str) for p in paths):
    bad("file_paths must be 1-256 strings")
if len(set(paths)) != len(paths):
    bad("file_paths must be distinct")
try:
    encoded = [p.encode("utf-8") for p in paths]
except UnicodeEncodeError:
    bad("path is not valid UTF-8")
if sum(len(e) + 1 for e in encoded) > 256 * 1024:
    bad("file_paths exceed the aggregate byte bound")
def ignored(rel):
    for name in (".living", ".mycelium", ".claude", ".codex"):
        if rel.startswith(name + "/") or "/" + name + "/" in rel:
            return True
    return ("/node_modules/" in rel or "/__pycache__/" in rel or rel.endswith(".pyc") or rel.endswith(".lock"))
for path in paths:
    if not path or len(path) > 4096 or any(c in path for c in "\0\r\n") or not os.path.isabs(path):
        bad("unrepresentable or relative path")
    if os.path.realpath(path) != path:
        bad("path is not canonical")
    rel = os.path.relpath(path, repo_root)
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        bad("path is outside the repository")
    if ignored(rel):
        bad("path is not eligible for activity tracking")
digest = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
if count != len(paths) or digest_in != digest:
    bad("batch identity does not match its paths")
print(len(paths))
print(json.dumps({"mycelium_activity_batch": {"id": ident, "count": len(paths), "sha256": digest,
                                              "status": "recorded"}}, sort_keys=True))
' "$REPO_ROOT") || batch_fail "${BATCH_RESULT:-invalid batch}"
  BATCH_COUNT=$(printf '%s\n' "$BATCH_RESULT" | sed -n 1p)
  BATCH_ACK=$(printf '%s\n' "$BATCH_RESULT" | sed -n 2p)
fi
mycelium_prepare_locked_post_tool_state "$REPO_ROOT" "$INPUT" || {
  [[ "$BATCH" == 1 ]] && batch_fail "session state or lock is unavailable" 70
  exit 0
}
trap mycelium_release_session_lock EXIT

# Only enforce in mycelium-enabled repos
if [[ ! -d "$REPO_ROOT/.living" ]]; then
  exit 0
fi

# --- Accumulate modified files for session summary ---

mkdir -p "$STATE_DIR"
ACTIVITY_FILE="$STATE_DIR/mycelium-session-activity.tmp"
touched=false
recorded=0
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
  recorded=$((recorded + 1))
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
if isinstance(payload.get("mycelium_activity_batch"), dict):
    for batch_path in tool_input.get("file_paths") or []:  # already validated as eligible
        emit(batch_path)
command = tool_input.get("command")
if isinstance(command, str):
    for line in command.splitlines():
        match = re.match(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", line)
        if match:
            emit(match.group(1))
' "$SESSION_CWD" "$REPO_ROOT")

if [[ "$BATCH" == 1 && "$recorded" != "$BATCH_COUNT" ]]; then
  batch_fail "recorded $recorded of $BATCH_COUNT entries; no acknowledgement" 70
fi
if [[ "$touched" != true ]]; then
  exit 0
fi

# --- Start or refresh the lifecycle work cycle ---

# The baseline fingerprints .living/ content before the agent records this
# work. If the prior cycle was already documented, refresh it so subsequent
# edits cannot be accepted using an older lifecycle update.
SESSION_CHANGES_HELPER="${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"
mycelium_refresh_work_cycle "$REPO_ROOT" "$SESSION_CHANGES_HELPER" || true

if [[ "$BATCH" == 1 ]]; then
  printf '%s\n' "$BATCH_ACK"
fi
exit 0
