#!/usr/bin/env bash
# Stable entrypoint for Mycelium's plugin-bundled Codex hooks.
#
# Codex supplies PLUGIN_ROOT for plugin hooks. Keeping that expansion in the
# bundled hook definition avoids embedding a versioned plugin-cache path into
# every initialized repository. The dispatcher also keeps globally enabled
# plugin hooks silent outside Mycelium repositories.

set -euo pipefail

# Claude Code discovers the same conventional hooks/hooks.json path as Codex.
# Initialized Mycelium projects already register the native Claude hooks in
# .claude/settings.local.json, so decline this Codex adapter when Claude
# cross-loads it. Claude Code documents and exports CLAUDE_PROJECT_DIR to every
# hook process; Codex does not.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
  exit 0
fi

HOOK_NAME="${1:-}"
case "$HOOK_NAME" in
  mycelium-health.sh|mycelium-post-action.sh|mycelium-data-tracker.sh|mycelium-activity-tracker.sh|mycelium-data-lineage-stop.sh|mycelium-stop-check.sh)
    ;;
  *)
    printf 'Unknown Mycelium Codex hook: %s\n' "$HOOK_NAME" >&2
    exit 64
    ;;
esac

ROOT="${PLUGIN_ROOT:-}"
if [[ -z "$ROOT" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
SCRIPT="$ROOT/skills/core/hooks/$HOOK_NAME"
if [[ ! -x "$SCRIPT" ]]; then
  printf 'Mycelium hook executable not found: %s\n' "$SCRIPT" >&2
  exit 127
fi

INPUT=$(cat)
SESSION_CWD=$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
except Exception:
    value = {}
print(value.get("cwd") or "")
' 2>/dev/null || true)
if [[ -z "$SESSION_CWD" || ! -d "$SESSION_CWD" ]]; then
  SESSION_CWD=$(pwd)
fi

REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" || -L "$REPO_ROOT/.living" || ! -d "$REPO_ROOT/.living" ]]; then
  exit 0
fi

export MYCELIUM_HOOK_HOST=codex
export MYCELIUM_PLUGIN_ROOT="$ROOT"
cd "$REPO_ROOT"
printf '%s' "$INPUT" | "$SCRIPT"
