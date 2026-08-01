#!/usr/bin/env bash

# Shared compatibility helpers for Claude Code and Codex hooks.

mycelium_prepare_state_dir() {
  local repo_root="$1"
  local requested_state=""
  local living_dir=""
  local legacy_dir=""
  local legacy_session=""
  local unsafe_link=""
  local plugin_pointer=""
  local pointer_tmp=""

  repo_root=$(cd "$repo_root" 2>/dev/null && pwd -P) || return 1
  living_dir="$repo_root/.living"

  # These hooks run from a globally trusted plugin but operate on an
  # untrusted checkout. Never follow repository-controlled symlinks for state
  # or lifecycle output: doing so would turn a normal hook into an arbitrary
  # out-of-project writer.
  if [[ -L "$living_dir" ]]; then
    return 1
  fi
  if [[ -d "$living_dir" ]]; then
    unsafe_link=$(find "$living_dir" -type l -print -quit 2>/dev/null || true)
    if [[ -n "$unsafe_link" ]]; then
      return 1
    fi
  fi

  requested_state="${MYCELIUM_STATE_DIR:-$repo_root/.mycelium}"
  requested_state=$(python3 - "$repo_root" "$requested_state" <<'PY'
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
candidate = Path(sys.argv[2])
if not candidate.is_absolute():
    candidate = root / candidate
candidate = Path(os.path.abspath(candidate))
try:
    relative = candidate.relative_to(root)
except ValueError:
    raise SystemExit(1)
if not relative.parts:
    raise SystemExit(1)

# Reject every existing symlink or non-directory component before mkdir. This
# validates the path before the shell can follow a repository-controlled link.
current = root
for part in relative.parts:
    current = current / part
    try:
        mode = current.lstat().st_mode
    except FileNotFoundError:
        continue
    except OSError:
        raise SystemExit(1)
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise SystemExit(1)

try:
    candidate.resolve(strict=False).relative_to(root)
except (OSError, RuntimeError, ValueError):
    raise SystemExit(1)
print(candidate)
PY
  ) || return 1
  mkdir -p "$requested_state" || return 1
  STATE_DIR=$(cd "$requested_state" 2>/dev/null && pwd -P) || return 1
  case "$STATE_DIR" in
    "$repo_root"/*) ;;
    *) return 1 ;;
  esac
  unsafe_link=$(find "$STATE_DIR" -type l -print -quit 2>/dev/null || true)
  if [[ -n "$unsafe_link" ]]; then
    return 1
  fi

  if [[ ! -f "$STATE_DIR/.gitignore" ]]; then
    printf '*\n!.gitignore\n' > "$STATE_DIR/.gitignore"
  fi

  # Codex plugin hooks carry their live bundle root through the dispatcher.
  # Refresh the generated guidance pointer only after the shared state safety
  # checks above, and replace it atomically rather than following an existing
  # path with shell redirection.
  if [[ -n "${MYCELIUM_PLUGIN_ROOT:-}" ]]; then
    plugin_pointer="$STATE_DIR/plugin-root"
    if [[ -L "$plugin_pointer" \
      || ( -e "$plugin_pointer" && ! -f "$plugin_pointer" ) ]]; then
      return 1
    fi
    if [[ ! -f "$plugin_pointer" \
      || "$(cat "$plugin_pointer" 2>/dev/null || true)" != "$MYCELIUM_PLUGIN_ROOT" ]]; then
      pointer_tmp=$(mktemp "$STATE_DIR/.plugin-root.tmp.XXXXXX") || return 1
      if ! printf '%s\n' "$MYCELIUM_PLUGIN_ROOT" > "$pointer_tmp" \
        || ! mv -f "$pointer_tmp" "$plugin_pointer"; then
        rm -f "$pointer_tmp"
        return 1
      fi
    fi
  fi

  # Preserve cross-session context from projects initialized before v0.4.
  legacy_dir="$repo_root/.claude"
  legacy_session="$legacy_dir/last-session.md"
  if [[ ! -f "$STATE_DIR/last-session.md" \
    && -d "$legacy_dir" \
    && ! -L "$legacy_dir" \
    && -f "$legacy_session" \
    && ! -L "$legacy_session" ]]; then
    cp "$legacy_session" "$STATE_DIR/last-session.md"
  fi

  return 0
}

mycelium_living_changed() {
  local repo_root="$1"
  local baseline_file="$2"
  local helper="$3"
  local reminder_ts="${4:-0}"
  local helper_status=2

  if [[ -f "$helper" && -f "$baseline_file" ]]; then
    if python3 "$helper" living-changed \
      --repo-root "$repo_root" \
      --baseline "$baseline_file" >/dev/null 2>&1; then
      return 0
    else
      helper_status=$?
    fi
    if [[ "$helper_status" -eq 1 ]]; then
      return 1
    fi
  fi

  # Rolling-upgrade fallback for sessions whose baseline predates content
  # fingerprints. Nanosecond mtimes avoid the old same-second false negative,
  # and every finding file is checked rather than only the directory mtime.
  python3 - "$repo_root" "$reminder_ts" <<'PY' >/dev/null 2>&1
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
try:
    threshold_ns = int(sys.argv[2]) * 1_000_000_000
except ValueError:
    threshold_ns = 0
living = root / ".living"
paths = [living / name for name in ("learnings.md", "decisions.md", "conventions.md")]
findings = living / "findings"
if findings.is_dir() and not findings.is_symlink():
    paths.extend(path for path in findings.rglob("*") if path.is_file())
for path in paths:
    try:
        if path.stat().st_mtime_ns > threshold_ns:
            raise SystemExit(0)
    except OSError:
        pass
raise SystemExit(1)
PY
}

mycelium_refresh_work_cycle() {
  local repo_root="$1"
  local helper="$2"
  local reminder_file="$STATE_DIR/mycelium-reminded.tmp"
  local baseline_file="$STATE_DIR/living-reminder-baseline.json"
  local reminder_ts=0
  local should_refresh=false

  if [[ ! -f "$reminder_file" ]]; then
    should_refresh=true
  else
    reminder_ts=$(head -1 "$reminder_file" 2>/dev/null || echo 0)
    if mycelium_living_changed \
      "$repo_root" "$baseline_file" "$helper" "$reminder_ts"; then
      should_refresh=true
    fi
  fi

  if [[ "$should_refresh" != true ]]; then
    return 1
  fi

  if [[ -f "$helper" ]]; then
    python3 "$helper" living-snapshot \
      --repo-root "$repo_root" \
      --output "$baseline_file" >/dev/null 2>&1 \
      || rm -f "$baseline_file"
  fi
  date +%s > "$reminder_file"
  return 0
}

mycelium_acquire_stop_lock() {
  local state_dir="$1"
  local attempts=0
  local owner_pid=""
  local owner_ts=""
  local now_ts=""
  local lock_mtime=0
  local owner_is_live=false

  MYCELIUM_STOP_LOCK_DIR="$state_dir/mycelium-stop.lock"
  while ! mkdir "$MYCELIUM_STOP_LOCK_DIR" 2>/dev/null; do
    attempts=$((attempts + 1))
    owner_pid=""
    owner_ts=""
    owner_is_live=false
    if [[ -f "$MYCELIUM_STOP_LOCK_DIR/owner" ]]; then
      read -r owner_pid owner_ts < "$MYCELIUM_STOP_LOCK_DIR/owner" || true
      if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
        owner_is_live=true
      fi
    fi
    now_ts=$(date +%s)
    lock_mtime=$(mycelium_file_mtime "$MYCELIUM_STOP_LOCK_DIR")
    if [[ "$owner_is_live" != true && "$lock_mtime" =~ ^[0-9]+$ ]] \
      && (( now_ts - lock_mtime > 300 )); then
      rm -f "$MYCELIUM_STOP_LOCK_DIR/owner"
      rmdir "$MYCELIUM_STOP_LOCK_DIR" 2>/dev/null || true
      continue
    fi
    if (( attempts >= 600 )); then
      return 1
    fi
    sleep 0.05
  done
  printf '%s %s\n' "$$" "$(date +%s)" > "$MYCELIUM_STOP_LOCK_DIR/owner"
  return 0
}

mycelium_release_stop_lock() {
  if [[ -n "${MYCELIUM_STOP_LOCK_DIR:-}" ]]; then
    rm -f "$MYCELIUM_STOP_LOCK_DIR/owner"
    rmdir "$MYCELIUM_STOP_LOCK_DIR" 2>/dev/null || true
  fi
}

mycelium_knowledge_dir() {
  printf '%s\n' "${MYCELIUM_KNOWLEDGE_DIR:-$HOME/.mycelium/knowledge}"
}

mycelium_file_mtime() {
  local path="${1:-}"
  local value=""

  if [[ -z "$path" || ! -e "$path" ]]; then
    printf '0\n'
    return
  fi

  # GNU stat uses -c; BSD/macOS stat uses -f. GNU `stat -f "%m"` exits
  # successfully but prints a mount point, so validate the result before use.
  value=$(stat -c "%Y" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return
  fi

  value=$(stat -f "%m" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return
  fi

  printf '0\n'
}

mycelium_file_size() {
  local path="${1:-}"
  local value=""

  if [[ -z "$path" || ! -e "$path" ]]; then
    printf '0\n'
    return
  fi

  # As with mtimes, try GNU first and validate before falling back to BSD.
  # GNU `stat -f%z` can otherwise succeed with non-size filesystem output.
  value=$(stat -c "%s" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return
  fi

  value=$(stat -f "%z" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return
  fi

  printf '0\n'
}

mycelium_hook_host() {
  case "${MYCELIUM_HOOK_HOST:-}" in
    codex|claude) printf '%s\n' "$MYCELIUM_HOOK_HOST" ;;
    *) printf '%s\n' "claude" ;;
  esac
}

mycelium_json_get() {
  local dotted_path="$1"
  python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for key in sys.argv[1].split("."):
        value = value.get(key) if isinstance(value, dict) else None
    if value is not None and not isinstance(value, (dict, list)):
        print(value)
except Exception:
    pass
' "$dotted_path"
}

mycelium_bash_exit() {
  python3 -c '
import json, re, sys
try:
    response = json.load(sys.stdin).get("tool_response")
except Exception:
    response = None

keys = ("exit_code", "exitCode", "return_code", "returncode")

def find_structured(value):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
        for candidate in value.values():
            if not isinstance(candidate, (dict, list)):
                continue
            found = find_structured(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = find_structured(candidate)
            if found is not None:
                return found
    return None

result = find_structured(response)
if result is None and isinstance(response, str):
    match = re.search(
        r"(?:exit(?:ed)?(?:[ _-]with)?(?:[ _-]code)?|return(?:[ _-]code)?)"
        r"[\":= ]+(-?\d+)",
        response,
        re.IGNORECASE,
    )
    if match:
        result = int(match.group(1))
if result is not None:
    print(result)
'
}

mycelium_emit_context() {
  local event="$1"
  local context="$2"
  local system_message="${3:-}"
  local host
  host=$(mycelium_hook_host)

  python3 - "$host" "$event" "$context" "$system_message" <<'PY'
import json
import sys

host, event, context, system_message = sys.argv[1:]
if host == "codex":
    if event == "Stop":
        payload = {"systemMessage": system_message or context}
    else:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            }
        }
        if system_message:
            payload["systemMessage"] = system_message
else:
    payload = {"additionalContext": context}
    if system_message:
        payload["systemMessage"] = system_message
print(json.dumps(payload))
PY
}
