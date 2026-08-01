#!/usr/bin/env bash
# mycelium-data-lineage-stop.sh — internal Mycelium Stop phase
# At session end, if the session captured any data-analysis events into
# .mycelium/mycelium-data-events.tmp, invokes extract_data_lineage.py to
# consolidate them into .living/log/data-lineage/<session_id>.json and
# writes a status sentinel at .living/log/.data-lineage-status-<sid>.json.
#
# Invoked synchronously by mycelium-stop-check.sh before it decides whether the
# Stop is accepted. This script deliberately leaves the canonical session marker
# and cumulative events in place so a blocked Stop can continue the same lineage
# session. It must not be registered as a sibling Stop command because hook
# runtimes may launch sibling commands concurrently.
#
# Called only by mycelium-stop-check.sh; it is not registered as its own hook.
# Input: JSON on stdin: {session_id, cwd, ...}
# Output: Silent.
# Env override: MYCELIUM_DATA_EXTRACTOR may point at an alternate extractor.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"
exec 2>/dev/null

INPUT=$(cat)

SESSION_CWD=$(printf '%s' "$INPUT" | mycelium_json_get 'cwd')
HOST_SESSION_ID=$(printf '%s' "$INPUT" | mycelium_json_get 'session_id')
if [[ -z "$SESSION_CWD" ]] || [[ ! -d "$SESSION_CWD" ]]; then exit 0; fi

REPO_ROOT=$(git -C "$SESSION_CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]] || [[ ! -d "$REPO_ROOT/.living" ]]; then exit 0; fi
mycelium_prepare_state_dir "$REPO_ROOT" || exit 0

SESSION_MARKER="$STATE_DIR/data-lineage-session-id.tmp"

EVENTS_FILE="$STATE_DIR/mycelium-data-events.tmp"
if [[ ! -s "$EVENTS_FILE" ]]; then exit 0; fi  # no events this session

# Resolve SESSION_ID. Prefer mycelium's date-counter format (YYYY-MM-DD-NNN)
# so manifests cross-reference cleanly with LOG_REGISTRY rows. Mycelium
# writes the per-session log path into .mycelium/active-session-log.tmp at
# session start; the basename encodes the session ID. Fall back to Claude
# Code's UUID only if mycelium hasn't recorded an active session.
SESSION_ID=$(head -1 "$SESSION_MARKER" 2>/dev/null || echo "")
if [[ ! "$SESSION_ID" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]+$ ]]; then
  SESSION_ID=""
fi
ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
if [[ -z "$SESSION_ID" && -f "$ACTIVE_LOG_FILE" ]]; then
  if _ACTIVE_MARKER=$(mycelium_read_active_log_marker "$REPO_ROOT" "$ACTIVE_LOG_FILE"); then
    LOG_PATH=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '1p')
  else
    LOG_PATH=""
  fi
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
  SESSION_ID="$HOST_SESSION_ID"
fi
if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then exit 1; fi

EXTRACTOR="${MYCELIUM_DATA_EXTRACTOR:-$HERE/../scripts/extract_data_lineage.py}"
if [[ ! -f "$EXTRACTOR" ]]; then exit 1; fi

OUT_DIR="$REPO_ROOT/.living/log/data-lineage"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/${SESSION_ID}.json"
STATUS_FILE="$REPO_ROOT/.living/log/.data-lineage-status-${SESSION_ID}.json"
LOG_TMP=$(mktemp)

START=$(date +%s)
set +e
python3 "$EXTRACTOR" \
  --events-file "$EVENTS_FILE" \
  --output "$OUT_FILE" \
  --session-id "$SESSION_ID" \
  --repo-root "$REPO_ROOT" \
  > "$LOG_TMP" 2>&1
EXIT_CODE=$?
set -e
END=$(date +%s)

# Write structured status sentinel via Python (handles quoting safely).
EVENTS_SIZE=$(mycelium_file_size "$EVENTS_FILE")
WALL=$((END - START))
python3 - "$STATUS_FILE" "$SESSION_ID" "$EXIT_CODE" "$WALL" "$EVENTS_SIZE" "$OUT_FILE" "$LOG_TMP" <<'PYINNER'
import datetime
import json
import os
import tempfile
import sys
from pathlib import Path

status_file, sid, exit_code, wall, events_size, out_file, log_tmp = sys.argv[1:8]
try:
    with open(log_tmp, encoding="utf-8", errors="replace") as f:
        log_tail = f.read()[-500:]
except OSError:
    log_tail = ""
status_path = Path(status_file)
descriptor, temp_name = tempfile.mkstemp(
    prefix=f".{status_path.name}.tmp.", dir=status_path.parent, text=True
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        json.dump({
            "session_id": sid,
            "exit_code": int(exit_code),
            "wall_seconds": int(wall),
            "events_file_size": int(events_size),
            "output_path": out_file,
            "log_tail": log_tail,
            "dispatched_at": datetime.datetime.now(datetime.UTC).isoformat().replace(
                "+00:00", "Z"
            ),
        }, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp_name, status_path)
finally:
    Path(temp_name).unlink(missing_ok=True)
PYINNER
rm -f "$LOG_TMP"

# Prune status sentinels to the 20 most recent so .living/log/ doesn't grow
# without bound. Sentinels are dot-prefixed; explicit glob avoids matching
# other hidden files.
STATUS_DIR="$REPO_ROOT/.living/log"
ls -t "$STATUS_DIR"/.data-lineage-status-*.json 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
exit "$EXIT_CODE"
