#!/usr/bin/env bash

# Shared compatibility helpers for Claude Code and Codex hooks.

mycelium_prepare_state_dir() {
  local repo_root="$1"
  local mode="${2:-write}"
  local requested_state=""
  local living_dir=""
  local legacy_dir=""
  local legacy_session=""
  local unsafe_link=""
  local plugin_pointer=""
  local pointer_tmp=""
  local managed_target=""

  [[ "$mode" == "write" \
    || "$mode" == "read-only" \
    || "$mode" == "bootstrap" ]] || return 1

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
  if [[ "$mode" == "read-only" ]]; then
    [[ -d "$requested_state" ]] || return 1
  else
    mkdir -p "$requested_state" || return 1
  fi
  STATE_DIR=$(cd "$requested_state" 2>/dev/null && pwd -P) || return 1
  case "$STATE_DIR" in
    "$repo_root"/*) ;;
    *) return 1 ;;
  esac
  # STATE_DIR historically named the repository-global runtime directory.
  # Keep that value as the shared coordination root even after an identified
  # host task selects its private run directory below.
  MYCELIUM_SHARED_STATE_DIR="$STATE_DIR"
  unsafe_link=$(find "$STATE_DIR" -type l -print -quit 2>/dev/null || true)
  if [[ -n "$unsafe_link" ]]; then
    return 1
  fi

  # Ownership preflight for host-identified PostToolUse events must happen
  # before state initialization, plugin-pointer refresh, or legacy migration.
  if [[ "$mode" == "read-only" || "$mode" == "bootstrap" ]]; then
    return 0
  fi

  plugin_pointer="$STATE_DIR/plugin-root"
  legacy_dir="$repo_root/.claude"
  legacy_session="$legacy_dir/last-session.md"
  for managed_target in \
    "$STATE_DIR/.gitignore" \
    "$STATE_DIR/last-session.md"; do
    if [[ ( -e "$managed_target" || -L "$managed_target" ) \
      && ( ! -f "$managed_target" || -L "$managed_target" ) ]]; then
      return 1
    fi
  done
  if [[ -n "${MYCELIUM_PLUGIN_ROOT:-}" \
    && ( -e "$plugin_pointer" || -L "$plugin_pointer" ) \
    && ( ! -f "$plugin_pointer" || -L "$plugin_pointer" ) ]]; then
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
  if [[ ! -f "$STATE_DIR/last-session.md" \
    && -d "$legacy_dir" \
    && ! -L "$legacy_dir" \
    && -f "$legacy_session" \
    && ! -L "$legacy_session" ]]; then
    cp "$legacy_session" "$STATE_DIR/last-session.md"
  fi

  return 0
}

mycelium_read_active_log_marker() {
  local repo_root="$1"
  local marker_file="$2"
  local raw_path=""
  local owner_ts=""
  local ownership_format=""
  local line_count=""
  local safe_path=""

  [[ -f "$marker_file" && ! -L "$marker_file" ]] || return 1
  raw_path=$(sed -n '1p' "$marker_file" 2>/dev/null || true)
  owner_ts=$(sed -n '2p' "$marker_file" 2>/dev/null || true)
  ownership_format=$(sed -n '3p' "$marker_file" 2>/dev/null || true)
  line_count=$(awk 'END { print NR }' "$marker_file" 2>/dev/null || true)
  [[ -n "$raw_path" && "$owner_ts" =~ ^[0-9]{1,18}$ ]] || return 1
  if [[ "$line_count" == 2 ]]; then
    [[ -z "$ownership_format" ]] || return 1
  elif [[ "$line_count" == 3 ]]; then
    [[ "$ownership_format" == "owner-id-v1" ]] || return 1
  else
    return 1
  fi

  safe_path=$(python3 - "$repo_root" "$raw_path" <<'PY'
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
raw = Path(sys.argv[2])
if not raw.is_absolute():
    raise SystemExit(1)
log_root = (root / ".living" / "log").resolve(strict=False)
candidate = Path(os.path.abspath(raw))
try:
    relative = candidate.resolve(strict=False).relative_to(log_root)
except (OSError, RuntimeError, ValueError):
    raise SystemExit(1)
if len(relative.parts) != 1:
    raise SystemExit(1)
try:
    mode = candidate.lstat().st_mode
except FileNotFoundError:
    pass
except OSError:
    raise SystemExit(1)
else:
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SystemExit(1)
print(candidate)
PY
  ) || return 1
  [[ -n "$safe_path" ]] || return 1
  printf '%s\n%s\n' "$safe_path" "$owner_ts"
  if [[ -n "$ownership_format" ]]; then
    printf '%s\n' "$ownership_format"
  fi
}

mycelium_valid_session_id() {
  local session_id="${1:-}"

  [[ -n "$session_id" \
    && "$session_id" != "." \
    && "$session_id" != ".." \
    && "$session_id" =~ ^[A-Za-z0-9._-]+$ \
    && ${#session_id} -le 200 ]]
}

mycelium_read_session_owner_id() {
  local owner_file="$1"
  local owner_id=""
  local line_count=""

  [[ -f "$owner_file" && ! -L "$owner_file" ]] || return 1
  owner_id=$(sed -n '1p' "$owner_file" 2>/dev/null || true)
  line_count=$(awk 'END { print NR }' "$owner_file" 2>/dev/null || true)
  mycelium_valid_session_id "$owner_id" || return 1
  [[ "$line_count" == 1 ]] || return 1
  printf '%s\n' "$owner_id"
}

mycelium_select_session_state() {
  local repo_root="$1"
  local input="$2"
  local mode="${3:-read-only}"
  local shared_state="${MYCELIUM_SHARED_STATE_DIR:-${STATE_DIR:-}}"
  local host_session_id=""
  local host=""
  local run_state=""
  local flat_owner=""
  local flat_marker=""

  [[ -n "$shared_state" && -d "$shared_state" && ! -L "$shared_state" ]] \
    || return 1
  repo_root=$(cd "$repo_root" 2>/dev/null && pwd -P) || return 1
  host_session_id=$(printf '%s' "$input" \
    | mycelium_json_get_optional_string 'session_id') || return 1

  # Identity-free hosts retain the pre-0.7 flat transaction layout. Never map
  # an invalid nonempty identity onto that compatibility path.
  if [[ -z "$host_session_id" ]]; then
    STATE_DIR="$shared_state"
    MYCELIUM_SESSION_SCOPED=false
    MYCELIUM_SESSION_HOST="legacy"
    MYCELIUM_HOST_SESSION_ID=""
    MYCELIUM_ACTIVE_FLAT_FALLBACK=false
    return 0
  fi
  mycelium_valid_session_id "$host_session_id" || return 1
  host=$(mycelium_hook_host)
  [[ "$host" == "claude" || "$host" == "codex" ]] || return 1

  run_state="$shared_state/run/$host/$host_session_id"

  # Rolling-upgrade compatibility: a task started by the prior build may still
  # own the flat transaction. Route only an exact owner match there; another
  # root gets a new scoped transaction and cannot consume the old evidence.
  if [[ ! -e "$run_state" && ! -L "$run_state" \
    && -f "$shared_state/active-session-log.tmp" \
    && ! -L "$shared_state/active-session-log.tmp" ]]; then
    flat_owner=$(mycelium_read_session_owner_id \
      "$shared_state/active-session-owner-id.tmp" 2>/dev/null || true)
    flat_marker=$(mycelium_read_active_log_marker \
      "$repo_root" "$shared_state/active-session-log.tmp" 2>/dev/null || true)
    if [[ -n "$flat_marker" && "$flat_owner" == "$host_session_id" ]]; then
      STATE_DIR="$shared_state"
      MYCELIUM_SESSION_SCOPED=false
      MYCELIUM_SESSION_HOST="$host"
      MYCELIUM_HOST_SESSION_ID="$host_session_id"
      MYCELIUM_ACTIVE_FLAT_FALLBACK=true
      return 0
    fi
  fi

  run_state=$(python3 - "$shared_state" "$host" "$host_session_id" "$mode" <<'PY'
import os
import stat
import sys
from pathlib import Path

shared = Path(sys.argv[1]).resolve(strict=True)
host, session_id, mode = sys.argv[2:]
if host not in {"claude", "codex"}:
    raise SystemExit(1)
if not session_id or len(session_id) > 200 or session_id in {".", ".."}:
    raise SystemExit(1)
if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for ch in session_id):
    raise SystemExit(1)
if mode not in {"create", "read-only"}:
    raise SystemExit(1)

current = shared
for part in ("run", host, session_id):
    current = current / part
    try:
        metadata = current.lstat()
    except FileNotFoundError:
        if mode != "create":
            raise SystemExit(1)
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise SystemExit(1)
    except OSError:
        raise SystemExit(1)
    else:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SystemExit(1)

candidate = current.resolve(strict=True)
try:
    candidate.relative_to(shared)
except ValueError:
    raise SystemExit(1)
print(candidate)
PY
  ) || return 1
  [[ -n "$run_state" ]] || return 1
  STATE_DIR="$run_state"
  MYCELIUM_SESSION_SCOPED=true
  MYCELIUM_SESSION_HOST="$host"
  MYCELIUM_HOST_SESSION_ID="$host_session_id"
  MYCELIUM_ACTIVE_FLAT_FALLBACK=false
  return 0
}

mycelium_payload_owns_active_session() {
  local repo_root="$1"
  local input="$2"
  local marker_file="$STATE_DIR/active-session-log.tmp"
  local owner_file="$STATE_DIR/active-session-owner-id.tmp"
  local marker=""
  local owner_format=""
  local owner_id=""
  local host_session_id=""
  local host_identified=false

  host_session_id=$(printf '%s' "$input" \
    | mycelium_json_get_optional_string 'session_id') || return 1
  if [[ -n "$host_session_id" ]]; then
    mycelium_valid_session_id "$host_session_id" || return 1
    host_identified=true
  fi

  # A host-identified event with no active transaction is a delayed event from
  # a completed/superseded task. Only payloads from legacy hosts that omit a
  # session identity may retain the pre-owner compatibility behavior.
  if [[ ! -f "$marker_file" ]]; then
    [[ "$host_identified" != true \
      && ! -e "$owner_file" && ! -L "$owner_file" ]]
    return
  fi
  if ! marker=$(mycelium_read_active_log_marker "$repo_root" "$marker_file"); then
    # A legacy/corrupt marker with no owner token cannot authorize a log write,
    # but it also must not disable independent activity enforcement. A claimed
    # host-owned transaction remains fail-closed.
    [[ "$host_identified" != true \
      && ! -e "$owner_file" && ! -L "$owner_file" ]]
    return
  fi
  owner_format=$(printf '%s\n' "$marker" | sed -n '3p')
  if [[ "$owner_format" != "owner-id-v1" \
    && ! -e "$owner_file" && ! -L "$owner_file" ]]; then
    return 0
  fi
  owner_id=$(mycelium_read_session_owner_id "$owner_file") || return 1
  [[ "$host_identified" == true && "$host_session_id" == "$owner_id" ]]
}

mycelium_prepare_post_tool_state() {
  local repo_root="$1"
  local input="$2"
  local host_session_id=""

  host_session_id=$(printf '%s' "$input" \
    | mycelium_json_get_optional_string 'session_id') || return 1
  if [[ -n "$host_session_id" ]]; then
    # An identified payload cannot create markerless state. Validate the
    # existing transaction without mutating shared preparation state. The
    # locked wrapper below revalidates after the event-lock wait.
    mycelium_prepare_state_dir "$repo_root" read-only || return 1
    mycelium_select_session_state "$repo_root" "$input" read-only || return 1
    mycelium_payload_owns_active_session "$repo_root" "$input" || return 1
  else
    # Identity-free payloads retain compatibility with hosts predating session
    # IDs, including their markerless activity enforcement.
    mycelium_prepare_state_dir "$repo_root" || return 1
    mycelium_select_session_state "$repo_root" "$input" read-only || return 1
    mycelium_payload_owns_active_session "$repo_root" "$input" || return 1
  fi
}

mycelium_prepare_locked_post_tool_state() {
  local repo_root="$1"
  local input="$2"

  mycelium_prepare_post_tool_state "$repo_root" "$input" || return 1
  mycelium_acquire_session_lock "$STATE_DIR" || return 1

  # Stop may have accepted this transaction while the event waited. Resolve
  # and authorize the payload again inside the private session critical section
  # before allowing any state mutation.
  if ! mycelium_prepare_state_dir "$repo_root" read-only \
    || ! mycelium_select_session_state "$repo_root" "$input" read-only \
    || ! mycelium_payload_owns_active_session "$repo_root" "$input"; then
    mycelium_release_session_lock
    return 1
  fi
  return 0
}

mycelium_registry_cell() {
  python3 -c '
import html
import sys
value = sys.stdin.read().replace("\r", " ").replace("\n", " ")
print(html.escape(" ".join(value.split()), quote=True).replace("|", "&#124;"))
'
}

mycelium_emit_stop_block() {
  local reason="$1"
  local escaped_reason=""
  escaped_reason=$(printf '%s' "$reason" | python3 -c \
    'import json, sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null) || return 1
  printf '{"decision": "block", "reason": %s}\n' "$escaped_reason"
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

mycelium_acquire_directory_lock() {
  local state_dir="$1"
  local lock_name="$2"
  local result_variable="$3"
  local configured_attempts="$4"
  local attempts=0
  local max_attempts="$configured_attempts"
  local owner_pid=""
  local owner_ts=""
  local owner_record=""
  local current_owner_record=""
  local now_ts=""
  local lock_mtime=0
  local lock_inode_before=""
  local lock_inode_after=""
  local reap_claim=""
  local reap_allowed=false
  local owner_is_live=false
  local owner_is_dead=false

  if [[ ! "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
    max_attempts=600
  fi

  [[ -d "$state_dir" && ! -L "$state_dir" ]] || return 1
  [[ "$lock_name" != "." \
    && "$lock_name" != ".." \
    && "$lock_name" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
  local lock_dir="$state_dir/$lock_name"
  while ! mkdir "$lock_dir" 2>/dev/null; do
    attempts=$((attempts + 1))
    owner_pid=""
    owner_ts=""
    owner_record=""
    owner_is_live=false
    owner_is_dead=false
    if [[ -L "$lock_dir" || ( -e "$lock_dir" && ! -d "$lock_dir" ) ]]; then
      return 1
    fi
    if [[ -f "$lock_dir/owner" && ! -L "$lock_dir/owner" ]]; then
      owner_record=$(cat "$lock_dir/owner" 2>/dev/null || true)
      read -r owner_pid owner_ts <<< "$owner_record" || true
      if [[ "$owner_pid" =~ ^[0-9]+$ ]]; then
        if kill -0 "$owner_pid" 2>/dev/null; then
          owner_is_live=true
        else
          owner_is_dead=true
        fi
      fi
    fi
    now_ts=$(date +%s)
    lock_mtime=$(mycelium_file_mtime "$lock_dir")
    # A recorded dead owner cannot still be in its critical section, so recover
    # immediately. Missing or malformed owner state may instead be the narrow
    # mkdir-to-owner publication window; retain the age guard for that case.
    # Claim reaping inside the directory and revalidate its inode plus owner
    # before deletion. Without that claim, two reapers can observe owner A as
    # dead, one can remove A's directory and let owner B acquire it, and the
    # delayed reaper can then delete B's newly created lock (an ABA race).
    if [[ "$owner_is_dead" == true ]] \
      || { [[ "$owner_is_live" != true && "$lock_mtime" =~ ^[0-9]+$ ]] \
        && (( now_ts - lock_mtime > 300 )); }; then
      lock_inode_before=$(mycelium_file_inode "$lock_dir" 2>/dev/null || true)
      reap_claim="$lock_dir/.mycelium-reap.claim"
      reap_allowed=false
      if [[ -n "$lock_inode_before" ]] \
        && mkdir "$reap_claim" 2>/dev/null; then
        lock_inode_after=$(mycelium_file_inode "$lock_dir" 2>/dev/null || true)
        current_owner_record=""
        if [[ -f "$lock_dir/owner" && ! -L "$lock_dir/owner" ]]; then
          current_owner_record=$(cat "$lock_dir/owner" 2>/dev/null || true)
        fi
        if [[ "$lock_inode_after" == "$lock_inode_before" ]]; then
          if [[ "$owner_is_dead" == true \
            && "$current_owner_record" == "$owner_record" \
            && "$owner_pid" =~ ^[0-9]+$ ]] \
            && ! kill -0 "$owner_pid" 2>/dev/null; then
            reap_allowed=true
          elif [[ "$owner_is_dead" != true \
            && "$current_owner_record" == "$owner_record" ]]; then
            if [[ -n "$owner_record" \
              || ( ! -e "$lock_dir/owner" && ! -L "$lock_dir/owner" ) ]]; then
              reap_allowed=true
            fi
          fi
        fi
        if [[ "$reap_allowed" == true ]]; then
          rm -f "$lock_dir/owner"
          rmdir "$reap_claim" 2>/dev/null || true
          if rmdir "$lock_dir" 2>/dev/null; then
            continue
          fi
        else
          rmdir "$reap_claim" 2>/dev/null || true
        fi
      fi
    fi
    if (( attempts >= max_attempts )); then
      return 1
    fi
    sleep 0.05
  done
  if ! printf '%s %s\n' "$$" "$(date +%s)" > "$lock_dir/owner"; then
    rmdir "$lock_dir" 2>/dev/null || true
    return 1
  fi
  printf -v "$result_variable" '%s' "$lock_dir"
  return 0
}

mycelium_release_directory_lock() {
  local lock_dir="$1"
  if [[ -n "$lock_dir" && -d "$lock_dir" && ! -L "$lock_dir" ]]; then
    rm -f "$lock_dir/owner"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}

mycelium_acquire_stop_lock() {
  mycelium_acquire_directory_lock \
    "$1" \
    "mycelium-stop.lock" \
    MYCELIUM_STOP_LOCK_DIR \
    "${MYCELIUM_STOP_LOCK_MAX_ATTEMPTS:-600}"
}

mycelium_release_stop_lock() {
  mycelium_release_directory_lock "${MYCELIUM_STOP_LOCK_DIR:-}"
  MYCELIUM_STOP_LOCK_DIR=""
}

mycelium_acquire_session_lock() {
  mycelium_acquire_directory_lock \
    "$1" \
    "mycelium-session.lock" \
    MYCELIUM_SESSION_LOCK_DIR \
    "${MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS:-600}"
}

mycelium_release_session_lock() {
  mycelium_release_directory_lock "${MYCELIUM_SESSION_LOCK_DIR:-}"
  MYCELIUM_SESSION_LOCK_DIR=""
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

mycelium_file_inode() {
  local path="${1:-}"
  local value=""

  if [[ -z "$path" || ! -e "$path" ]]; then
    return 1
  fi

  value=$(stat -c "%d:%i" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+:[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  value=$(stat -f "%d:%i" "$path" 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+:[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  return 1
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

mycelium_json_get_optional_string() {
  local dotted_path="$1"
  python3 -c '
import json, sys

missing = object()
try:
    value = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for key in sys.argv[1].split("."):
    if not isinstance(value, dict) or key not in value:
        value = missing
        break
    value = value[key]
if value is missing or value is None:
    raise SystemExit(0)
if not isinstance(value, str):
    raise SystemExit(1)
sys.stdout.write(value)
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
            found = find_structured(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = find_structured(candidate)
            if found is not None:
                return found
    elif isinstance(value, str):
        # Code-mode local tools expose model-facing output as input_text blocks.
        # Their text can itself be the JSON serialization of the structured
        # command result, so recurse through that serialization.
        try:
            decoded = json.loads(value)
        except Exception:
            decoded = None
        if decoded is not None and decoded != value:
            found = find_structured(decoded)
            if found is not None:
                return found
    return None

def find_textual(value):
    if isinstance(value, dict):
        for candidate in value.values():
            found = find_textual(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = find_textual(candidate)
            if found is not None:
                return found
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            decoded = None
        if decoded is not None and decoded != value:
            found = find_textual(decoded)
            if found is not None:
                return found
        match = re.search(
            r"(?:exit(?:ed)?(?:[ _-]with)?(?:[ _-]code)?|return(?:[ _-]code)?)"
            r"[\":= ]+(-?\d+)",
            value,
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1))
    return None

result = find_structured(response)
if result is None:
    result = find_textual(response)
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
if host == "codex" and event == "Stop":
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
print(json.dumps(payload))
PY
}
