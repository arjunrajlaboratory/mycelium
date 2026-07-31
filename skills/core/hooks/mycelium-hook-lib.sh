#!/usr/bin/env bash

# Shared compatibility helpers for Claude Code and Codex hooks.

mycelium_prepare_state_dir() {
  local repo_root="$1"
  STATE_DIR="${MYCELIUM_STATE_DIR:-$repo_root/.mycelium}"
  mkdir -p "$STATE_DIR"

  if [[ ! -f "$STATE_DIR/.gitignore" ]]; then
    printf '*\n!.gitignore\n' > "$STATE_DIR/.gitignore"
  fi

  # Preserve cross-session context from projects initialized before v0.4.
  if [[ ! -f "$STATE_DIR/last-session.md" && -f "$repo_root/.claude/last-session.md" ]]; then
    cp "$repo_root/.claude/last-session.md" "$STATE_DIR/last-session.md"
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
