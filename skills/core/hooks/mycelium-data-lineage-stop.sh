#!/usr/bin/env bash
# mycelium-data-lineage-stop.sh — Claude Code Stop hook
# At session end, if the session captured any data-analysis events into
# .claude/mycelium-data-events.tmp, invokes extract_data_lineage.py to
# consolidate them into .living/log/data-lineage/<session_id>.json and
# writes a status sentinel at .living/log/.data-lineage-status-<sid>.json.
#
# Independent of mycelium-stop-check.sh (which handles the .living/-update
# enforcement + log-scribe dispatch). Both can run on the same Stop event;
# order doesn't matter.
#
# Install: registered automatically by init_repo.install_claude_hooks() under
#   Stop. Innermost-wins: subproject settings need the complete bundle.
# Input: JSON on stdin: {session_id, cwd, ...}
# Output: Silent.
# Env override: MYCELIUM_DATA_EXTRACTOR may point at an alternate extractor.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

{
  INPUT=$(cat)

  SESSION_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
  CLAUDE_UUID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
  if [[ -z "$SESSION_CWD" ]] || [[ ! -d "$SESSION_CWD" ]]; then exit 0; fi

  REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
  if [[ -z "$REPO_ROOT" ]] || [[ ! -d "$REPO_ROOT/.living" ]]; then exit 0; fi

  EVENTS_FILE="$REPO_ROOT/.claude/mycelium-data-events.tmp"
  if [[ ! -s "$EVENTS_FILE" ]]; then exit 0; fi  # no events this session

  # Resolve SESSION_ID. Prefer mycelium's date-counter format (YYYY-MM-DD-NNN)
  # so manifests cross-reference cleanly with LOG_REGISTRY rows. Mycelium
  # writes the per-session log path into .claude/active-session-log.tmp at
  # session start; the basename encodes the session ID. Fall back to Claude
  # Code's UUID only if mycelium hasn't recorded an active session.
  SESSION_ID=""
  ACTIVE_LOG_FILE="$REPO_ROOT/.claude/active-session-log.tmp"
  if [[ -f "$ACTIVE_LOG_FILE" ]]; then
    LOG_PATH=$(head -1 "$ACTIVE_LOG_FILE" 2>/dev/null || echo "")
    if [[ -n "$LOG_PATH" ]]; then
      LOG_BASENAME=$(basename "$LOG_PATH" .md)
      # Extract mycelium session ID prefix YYYY-MM-DD-NNN. The project slug
      # that follows may contain dashes (e.g. scientific-claims-prefilter),
      # so anchor on the digit pattern rather than a trailing greedy strip.
      SESSION_ID=$(printf '%s' "$LOG_BASENAME" | sed -E 's/^([0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]+).*$/\1/')
      # If the regex didn't match (basename doesn't have the prefix), clear
      # SESSION_ID so we fall through to the UUID fallback.
      if [[ ! "$SESSION_ID" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]+$ ]]; then
        SESSION_ID=""
      fi
    fi
  fi
  if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID="$CLAUDE_UUID"
  fi
  if [[ -z "$SESSION_ID" ]]; then exit 0; fi

  EXTRACTOR="${MYCELIUM_DATA_EXTRACTOR:-$HERE/../scripts/extract_data_lineage.py}"
  if [[ ! -f "$EXTRACTOR" ]]; then exit 0; fi

  OUT_DIR="$REPO_ROOT/.living/log/data-lineage"
  mkdir -p "$OUT_DIR"
  OUT_FILE="$OUT_DIR/${SESSION_ID}.json"
  STATUS_FILE="$REPO_ROOT/.living/log/.data-lineage-status-${SESSION_ID}.json"
  LOG_TMP=$(mktemp)

  START=$(date +%s)
  python3 "$EXTRACTOR" \
    --events-file "$EVENTS_FILE" \
    --output "$OUT_FILE" \
    --session-id "$SESSION_ID" \
    --repo-root "$REPO_ROOT" \
    > "$LOG_TMP" 2>&1
  EXIT_CODE=$?
  END=$(date +%s)

  # Write structured status sentinel via Python (handles quoting safely).
  EVENTS_SIZE=$(stat -f%z "$EVENTS_FILE" 2>/dev/null || stat -c%s "$EVENTS_FILE" 2>/dev/null || echo 0)
  WALL=$((END - START))
  python3 - "$STATUS_FILE" "$SESSION_ID" "$EXIT_CODE" "$WALL" "$EVENTS_SIZE" "$OUT_FILE" "$LOG_TMP" <<'PYINNER'
import json, sys, datetime
status_file, sid, exit_code, wall, events_size, out_file, log_tmp = sys.argv[1:8]
try:
    with open(log_tmp, encoding="utf-8", errors="replace") as f:
        log_tail = f.read()[-500:]
except OSError:
    log_tail = ""
with open(status_file, "w", encoding="utf-8") as out:
    json.dump({
        "session_id": sid,
        "exit_code": int(exit_code),
        "wall_seconds": int(wall),
        "events_file_size": int(events_size),
        "output_path": out_file,
        "log_tail": log_tail,
        "dispatched_at": datetime.datetime.utcnow().isoformat() + "Z",
    }, out, indent=2)
PYINNER
  rm -f "$LOG_TMP"

  # Clear the events tmp file so the next session starts fresh.
  # Rotate to a per-session-ID prev so successive sessions don't clobber
  # each other's raw events backup. The operator can inspect any recent
  # session's raw events as long as the file hasn't been pruned.
  PREV_DIR="$REPO_ROOT/.claude/mycelium-data-events-prev"
  mkdir -p "$PREV_DIR"
  mv "$EVENTS_FILE" "$PREV_DIR/${SESSION_ID}.tmp"
  # Prune: keep only the 20 most recent per-session prev files. Cheap
  # quota; one tmp file is typically a few KB to ~hundreds of KB.
  ls -t "$PREV_DIR"/*.tmp 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true

  # Prune status sentinels to the 20 most recent so .living/log/ doesn't grow
  # without bound. Sentinels are dot-prefixed; explicit glob avoids matching
  # other hidden files.
  STATUS_DIR="$REPO_ROOT/.living/log"
  ls -t "$STATUS_DIR"/.data-lineage-status-*.json 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
} 2>/dev/null || true

exit 0
