#!/usr/bin/env bash

# Shared compatibility helpers for Claude Code and Codex hooks.

# The state helpers below run in mycelium_hook_state.py, one interpreter per
# call of mycelium__state however many checks the call chains. The module
# documents each op; every op keeps the contract of the bash helper it
# replaced, including the variables that helper assigned.
MYCELIUM__HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
MYCELIUM__STATE_PY="$MYCELIUM__HOOKS_DIR/mycelium_hook_state.py"
# Results of the marker and owner ops (states: absent, invalid, valid).
MYCELIUM_MARKER_STATE=""
MYCELIUM_MARKER_LOG=""
MYCELIUM_MARKER_TS=""
MYCELIUM_MARKER_FORMAT=""
MYCELIUM_OWNER_STATE=""
MYCELIUM_OWNER_ID=""
MYCELIUM__STATE_FAILED=0
MYCELIUM__STATE_DIED=0
MYCELIUM__STATE_HELD=""
# Why the last mycelium__state call refused, when an op could say (for
# example the symlink that made prepare refuse the state tree). Empty
# otherwise; diagnostic only, never a control signal.
MYCELIUM__STATE_REASON=""
# Lock directories this process acquired. A value inherited from the
# environment names someone else's lock: never let a release remove it.
MYCELIUM_STOP_LOCK_DIR=""
MYCELIUM_SESSION_LOCK_DIR=""

# mycelium__state REPO_ROOT PAYLOAD OP...: run the ops in order and stop at
# the first failure. Applies the assignments the ops make (STATE_DIR,
# MYCELIUM_SHARED_STATE_DIR, MYCELIUM_SESSION_*, the lock directories, and the
# marker and owner reads), sets MYCELIUM__STATE_FAILED to the 1-based index of
# the failed op (0 when none failed), and returns nonzero on failure.
# MYCELIUM__STATE_DIED is 1 when that failure was an interpreter that died
# before finishing, rather than an op that checked and refused.
#
# A lock op makes one mkdir(2) attempt in Python. When the lock is held, this
# runs the unchanged bash acquisition loop (dead-owner reaping, retries) and
# then resumes the remaining ops in a fresh interpreter, so every check after
# a lock still runs after it was taken.
mycelium__state() {
  local repo_root="$1"
  local input="$2"
  shift 2
  local -a ops=("$@")
  local sid=""
  local sid_status=ok
  local host=claude
  local op=""
  local offset=0
  local kind="" a="" b="" c="" d=""
  local last_ok=0 failed=0 busy=0 ended=false

  MYCELIUM__STATE_FAILED=0
  MYCELIUM__STATE_DIED=0
  MYCELIUM__STATE_HELD=""
  MYCELIUM__STATE_REASON=""
  (( ${#ops[@]} > 0 )) || return 0
  for op in "${ops[@]}"; do
    case "$op" in
      select:*|owns)
        if ! sid=$(mycelium_json_get_optional_string 'session_id' input); then
          sid=""
          sid_status=fail
        fi
        break
        ;;
    esac
  done
  case "${MYCELIUM_HOOK_HOST:-}" in
    codex|claude|antigravity-ide|antigravity-app) host="$MYCELIUM_HOOK_HOST" ;;
  esac

  while true; do
    last_ok=0
    failed=0
    busy=0
    ended=false
    while IFS= read -r -d '' kind; do
      case "$kind" in
        set)
          IFS= read -r -d '' a || break
          IFS= read -r -d '' b || break
          case "$a" in
            STATE_DIR|MYCELIUM_SHARED_STATE_DIR|MYCELIUM_SESSION_SCOPED|MYCELIUM_SESSION_HOST|MYCELIUM_HOST_SESSION_ID|MYCELIUM_ACTIVE_FLAT_FALLBACK)
              printf -v "$a" '%s' "$b"
              ;;
          esac
          ;;
        lock)
          IFS= read -r -d '' a || break
          IFS= read -r -d '' b || break
          case "$a" in
            stop)
              MYCELIUM_STOP_LOCK_DIR="$b"
              MYCELIUM__STATE_HELD="$MYCELIUM__STATE_HELD stop"
              ;;
            session)
              MYCELIUM_SESSION_LOCK_DIR="$b"
              MYCELIUM__STATE_HELD="$MYCELIUM__STATE_HELD session"
              ;;
          esac
          ;;
        marker)
          IFS= read -r -d '' a || break
          IFS= read -r -d '' b || break
          IFS= read -r -d '' c || break
          IFS= read -r -d '' d || break
          MYCELIUM_MARKER_STATE="$a"
          MYCELIUM_MARKER_LOG="$b"
          MYCELIUM_MARKER_TS="$c"
          MYCELIUM_MARKER_FORMAT="$d"
          ;;
        owner)
          IFS= read -r -d '' a || break
          IFS= read -r -d '' b || break
          MYCELIUM_OWNER_STATE="$a"
          MYCELIUM_OWNER_ID="$b"
          ;;
        reason)
          IFS= read -r -d '' a || break
          MYCELIUM__STATE_REASON="$a"
          ;;
        ok|fail|busy)
          IFS= read -r -d '' a || break
          [[ "$a" =~ ^[0-9]+$ ]] || break
          case "$kind" in
            ok) last_ok="$a" ;;
            fail) failed="$a" ;;
            busy) busy="$a" ;;
          esac
          ;;
        end)
          ended=true
          ;;
      esac
    done < <(python3 -I -S "$MYCELIUM__STATE_PY" \
      "$$" "$host" "$sid_status" "$sid" "$repo_root" \
      "${STATE_DIR:-}" "${MYCELIUM_SHARED_STATE_DIR:-}" \
      "${MYCELIUM_STATE_DIR:-}" "${MYCELIUM_PLUGIN_ROOT:-}" \
      "${ops[@]:$offset}" 2>/dev/null)

    # An interpreter that died before reporting its end failed the op it was
    # running, whatever it had already reported.
    if [[ "$ended" != true ]]; then
      failed=$((last_ok + 1))
      busy=0
      MYCELIUM__STATE_DIED=1
    fi
    if (( failed > 0 )); then
      MYCELIUM__STATE_FAILED=$((offset + failed))
      return 1
    fi
    if (( busy == 0 )); then
      return 0
    fi
    op="${ops[$((offset + busy - 1))]}"
    case "$op" in
      lock:stop)
        if ! mycelium_acquire_stop_lock "${MYCELIUM_SHARED_STATE_DIR:-}"; then
          MYCELIUM__STATE_FAILED=$((offset + busy))
          return 1
        fi
        MYCELIUM__STATE_HELD="$MYCELIUM__STATE_HELD stop"
        ;;
      lock:session)
        if ! mycelium_acquire_session_lock "${STATE_DIR:-}"; then
          MYCELIUM__STATE_FAILED=$((offset + busy))
          return 1
        fi
        MYCELIUM__STATE_HELD="$MYCELIUM__STATE_HELD session"
        ;;
      *)
        MYCELIUM__STATE_FAILED=$((offset + busy))
        return 1
        ;;
    esac
    offset=$((offset + busy))
    if (( offset >= ${#ops[@]} )); then
      return 0
    fi
  done
}

# Release the locks the last mycelium__state call took, narrowest first. A
# failed call may hold a lock even when the op that took it never reported
# success (its process died after the lock record), so callers release by
# this record rather than by the failed op's index.
mycelium__state_release_held() {
  case " $MYCELIUM__STATE_HELD " in
    *" session "*) mycelium_release_session_lock ;;
  esac
  case " $MYCELIUM__STATE_HELD " in
    *" stop "*) mycelium_release_stop_lock ;;
  esac
  MYCELIUM__STATE_HELD=""
}

# Record the marker as unreadable after its read op died.
mycelium__state_marker_unread() {
  MYCELIUM_MARKER_STATE=invalid
  MYCELIUM_MARKER_LOG=""
  MYCELIUM_MARKER_TS=""
  MYCELIUM_MARKER_FORMAT=""
}

mycelium_prepare_state_dir() {
  mycelium__state "$1" "" "prepare:${2:-write}"
}

mycelium_read_active_log_marker() {
  mycelium__state "$1" "" "marker-at=$2" || return 1
  [[ "$MYCELIUM_MARKER_STATE" == valid ]] || return 1
  printf '%s\n%s\n' "$MYCELIUM_MARKER_LOG" "$MYCELIUM_MARKER_TS"
  if [[ -n "$MYCELIUM_MARKER_FORMAT" ]]; then
    printf '%s\n' "$MYCELIUM_MARKER_FORMAT"
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
  mycelium__state "" "" "owner-at=$1" || return 1
  [[ "$MYCELIUM_OWNER_STATE" == valid ]] || return 1
  printf '%s\n' "$MYCELIUM_OWNER_ID"
}

mycelium_select_session_state() {
  mycelium__state "$1" "$2" "select:${3:-read-only}"
}

mycelium_payload_owns_active_session() {
  mycelium__state "$1" "$2" owns
}

# Identity-free payloads keep compatibility with hosts predating session IDs,
# including their markerless activity enforcement, so they may initialize the
# shared state. An identified payload cannot create markerless state: it
# validates the existing transaction without mutating shared preparation.
mycelium__post_tool_prepare_mode() {
  local host_session_id=""
  local input="$1"
  host_session_id=$(mycelium_json_get_optional_string 'session_id' input) || return 1
  if [[ -n "$host_session_id" ]]; then
    MYCELIUM__PREPARE_MODE=read-only
  else
    MYCELIUM__PREPARE_MODE=write
  fi
}

mycelium_prepare_post_tool_state() {
  mycelium__post_tool_prepare_mode "$2" || return 1
  mycelium__state "$1" "$2" \
    "prepare:$MYCELIUM__PREPARE_MODE" select:read-only owns
}

# Validate, take the private session lock, and validate again: Stop may have
# accepted this transaction while the event waited. The second pass runs
# inside the critical section, before any state mutation. Also reads the
# active marker for the caller (MYCELIUM_MARKER_*).
#
# With a third argument `reopen`, a caller that has work to record (a
# proven analysis run, a project edit) reopens its own host session's
# transaction when that session has none, then validates once more. Hosts run
# Stop after every turn, and an accepted Stop removes the transaction, so
# without this every later turn of a live session was dropped silently until
# the next SessionStart. Reads and non-work events never pass `reopen`.
#
# Returning 1 covers two different outcomes, and callers exit 0 on both. An
# intentional no-op (no transaction, another session's event, Stop accepted
# while this event waited) leaves nothing. A failed session lock op after
# this event's ownership of a live transaction was proven (a timeout behind
# a live holder, or a dead interpreter) means work this hook should have
# recorded was dropped: it leaves one line in that transaction's
# unrecorded-events file, which Stop treats as work of unknown extent, and a
# diagnostic on stderr. It never reopens: the transaction exists and only its
# lock was unavailable. MYCELIUM_POST_TOOL_UNRECORDED is 1 after such a
# failure (0 otherwise), so a caller with its own no-transaction fallback
# does not mistake it for a missing transaction.
MYCELIUM_POST_TOOL_UNRECORDED=0
mycelium_prepare_locked_post_tool_state() {
  local allow_reopen="${3:-}"
  local status=0
  MYCELIUM_POST_TOOL_UNRECORDED=0
  mycelium__post_tool_prepare_mode "$2" || return 1
  mycelium__prepare_locked_post_tool_state_once "$1" "$2" || status=$?
  if (( status == 0 )); then
    return 0
  fi
  if (( status == 2 )); then
    MYCELIUM_POST_TOOL_UNRECORDED=1
    mycelium__record_unrecorded_event "$1" "$2"
    return 1
  fi
  [[ "$allow_reopen" == reopen ]] || return 1
  mycelium_reopen_post_tool_transaction "$1" "$2" || return 1
  mycelium__post_tool_prepare_mode "$2" || return 1
  status=0
  mycelium__prepare_locked_post_tool_state_once "$1" "$2" || status=$?
  if (( status == 2 )); then
    MYCELIUM_POST_TOOL_UNRECORDED=1
    mycelium__record_unrecorded_event "$1" "$2"
  fi
  (( status == 0 ))
}

# Unrecorded post-tool events: one NDJSON line per event a hook could not
# record, in the owning transaction's directory. Every reader and writer
# holds flock(2) on the file itself; the session lock is what was
# unavailable, so it cannot serialize them. The flock is bounded (about five
# seconds): a holder that is stopped must not hang a hook that already timed
# out, or Stop. A busy flock exits 75.
#
#   append SHARED REL KIND HOOK REASON HOST TS  (payload on stdin)
#       Writer. Walks SHARED/REL with O_NOFOLLOW directory descriptors, so a
#       managed ancestor swapped for a link while the hook waited cannot
#       redirect the write. After taking the flock it checks that its
#       descriptor is still the file at that name: Stop may have consumed or
#       replaced it meanwhile, and a line written there would be lost.
#   take PATH      Print "WORK OTHER SIZE GEN" ("0 0 0 0" when absent).
#       A line whose kind is `lineage` or `read` is OTHER; any other line is
#       WORK. An empty file is nothing yet: a writer may have created it and
#       not yet taken the flock, and its line will follow. A file holding
#       events is stamped, once, with a random generation line `{"gen": ...}`
#       that GEN names; an inode cannot name it, because Linux hands a freed
#       inode straight to the next file created.
#   consume-work PATH SIZE GEN   Drop the WORK lines among the first SIZE
#       bytes (Stop has turned them into a work cycle), keep their OTHER
#       lines until a Stop reports them, keep any line appended since, and
#       print "KEPT_SIZE GEN" for the new generation's stamp and kept lines
#       ("0 0" when no taken line is kept).
#   consume PATH SIZE GEN   The same, keeping no taken line.
#       Both act only on the generation that was taken; a file that has
#       since been replaced is left whole, for the next look to find.
#       What remains is written to a new file renamed over the old one, so a
#       failed write leaves the old file intact.
#   drain-other PATH    Stop without a transaction, holding no session
#       lock: in one flock, when the file holds only lineage and read lines,
#       remove it and print their count; otherwise print 0 and leave it.
# Every open is nonblocking and refuses anything but a regular file with a
# single link, so a FIFO cannot hang a hook that already timed out and a hard
# link cannot carry a write outside the repository.
MYCELIUM__PY_UNRECORDED_EVENTS='
import fcntl, json, os, stat, sys, tempfile, time
NAME = "mycelium-unrecorded-events.tmp"
BUSY = 75

def lock(fd):
    for _ in range(100):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(0.05)
    raise SystemExit(BUSY)

def single_regular(fd):
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise SystemExit(1)
    return opened

def generation(raw):
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if isinstance(value, dict) and "kind" not in value and isinstance(value.get("gen"), str):
        return value["gen"]
    return None

def gen_line(value):
    return (json.dumps({"gen": value}) + "\n").encode("ascii")

def is_other(raw):
    try:
        return json.loads(raw).get("kind") in ("lineage", "read")
    except (ValueError, AttributeError):
        return False

mode = sys.argv[1]
if mode == "append":
    shared, rel, kind, hook, reason, host, ts = sys.argv[2:9]
    tool = None
    try:
        payload = json.loads(sys.stdin.read())
        if isinstance(payload, dict) and isinstance(payload.get("tool_name"), str):
            tool = payload["tool_name"][:200]
    except ValueError:
        pass
    line = (json.dumps({"ts": ts, "kind": kind, "hook": hook, "reason": reason,
                        "host": host, "tool_name": tool}) + "\n").encode("ascii")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(shared, flags)
    try:
        for part in [part for part in rel.split("/") if part]:
            if part in (".", ".."):
                raise SystemExit(1)
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        for _ in range(20):
            fd = os.open(NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                         | os.O_NONBLOCK, 0o644, dir_fd=directory)
            try:
                single_regular(fd)
                lock(fd)
                held = os.fstat(fd)
                try:
                    current = os.stat(NAME, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino) \
                        or current.st_nlink != 1 or not stat.S_ISREG(current.st_mode):
                    continue
                os.write(fd, line)
                raise SystemExit(0)
            finally:
                os.close(fd)
        raise SystemExit(1)
    finally:
        os.close(directory)

path = sys.argv[2]
try:
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    print({"take": "0 0 0 0", "drain-other": "0"}.get(mode, "0 0"))
    raise SystemExit(0)
try:
    opened = single_regular(fd)
    lock(fd)
    data = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data += chunk
    lines = [raw for raw in data.splitlines(True) if raw.strip()]
    gens = [generation(raw) for raw in lines]
    gen = next((value for value in gens if value), None)
    events = [raw for raw, value in zip(lines, gens) if not value]
    try:
        current = os.stat(path, follow_symlinks=False)
        same = (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
    except FileNotFoundError:
        same = False
    if mode == "take":
        other = sum(1 for raw in events if is_other(raw))
        if events and gen is None and same:
            gen = os.urandom(8).hex()
            stamp = gen_line(gen)
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, stamp)
            data += stamp
        print(len(events) - other, other, len(data), gen or 0)
    elif mode == "drain-other":
        other = sum(1 for raw in events if is_other(raw))
        if events and other == len(events) and same:
            os.unlink(path)
            print(other)
        else:
            print(0)
    elif mode in ("consume-work", "consume"):
        size, token = int(sys.argv[3]), sys.argv[4]
        if not same or str(gen or 0) != token:
            print("0 0")
            raise SystemExit(0)
        kept = b""
        if mode == "consume-work":
            kept = b"".join(raw for raw in data[:size].splitlines(True)
                            if raw.strip() and not generation(raw) and is_other(raw))
        tail = data[size:]
        if not kept and not tail:
            os.unlink(path)
            print("0 0")
            raise SystemExit(0)
        fresh = os.urandom(8).hex()
        head = gen_line(fresh) + kept
        temp_fd, temp = tempfile.mkstemp(prefix=".unrecorded.", dir=os.path.dirname(path))
        try:
            with os.fdopen(temp_fd, "wb") as handle:
                handle.write(head + tail)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o644)
            os.rename(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        print(len(head), fresh) if kept else print("0 0")
    else:
        raise SystemExit(1)
finally:
    os.close(fd)
'

mycelium_unrecorded_events() {
  python3 -c "$MYCELIUM__PY_UNRECORDED_EVENTS" "$@"
}

# mycelium__record_unrecorded_event REPO_ROOT PAYLOAD: called only after a
# state call proved this event owns the transaction in STATE_DIR. Best
# effort: a failure here must not change the hook's fail-open exit status.
# The record's kind is the caller's MYCELIUM_POST_TOOL_EVENT_KIND: `work`
# (the default: a project edit or proven analysis run, which Stop enforces),
# `lineage`, or `read`.
mycelium__record_unrecorded_event() {
  local repo_root="$1"
  local input="$2"
  local kind="${MYCELIUM_POST_TOOL_EVENT_KIND:-work}"
  local hook=""
  local reason=session-lock-unavailable
  local diag_fd="${MYCELIUM_HOOK_DIAG_FD:-2}"
  local run_dir="${STATE_DIR:-}"
  local shared=""
  local rel=""
  hook=$(basename "${0:-hook}")
  if (( MYCELIUM__STATE_DIED == 1 )); then
    reason=state-check-died
  fi
  [[ "$diag_fd" =~ ^[0-9]+$ ]] || diag_fd=2
  case "$kind" in
    work|lineage|read) ;;
    *) kind=work ;;
  esac
  # The waited-on state may have changed: re-prepare the shared directory
  # and require the run directory inside it before the walk.
  if [[ -n "$run_dir" ]] \
    && shared=$(mycelium__checked_shared_state_dir "$repo_root"); then
    if [[ "$run_dir" == "$shared" ]]; then
      rel=""
    elif [[ "$run_dir" == "$shared"/* ]]; then
      rel="${run_dir#"$shared"/}"
    else
      shared=""
    fi
    if [[ -n "$shared" ]]; then
      printf '%s' "$input" | mycelium_unrecorded_events append "$shared" "$rel" \
        "$kind" "$hook" "$reason" "$(mycelium_hook_host)" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$reason" == session-lock-unavailable ]]; then
    printf 'Mycelium: %s could not take the session lock; this tool event was not recorded; Stop will report it.\n' \
      "$hook" >&"$diag_fd" || true
  else
    printf 'Mycelium: %s lost its state check while taking the session lock; this tool event was not recorded; Stop will report it.\n' \
      "$hook" >&"$diag_fd" || true
  fi
}

# mycelium__checked_shared_state_dir REPO_ROOT: print the shared state
# directory after a fresh read-only preparation of it has just passed every
# containment and symlink check. Writers to shared files outside a
# transaction call this immediately before writing, rather than trusting what
# an earlier state call left behind (that call may have failed on a symlink
# it found after assigning the directory). It runs in a subshell, so the
# caller's STATE_DIR and lock records are untouched.
mycelium__checked_shared_state_dir() {
  (
    mycelium__state "$1" "" prepare:read-only >/dev/null 2>&1 || exit 1
    [[ -n "${MYCELIUM_SHARED_STATE_DIR:-}" && -d "$MYCELIUM_SHARED_STATE_DIR" \
      && ! -L "$MYCELIUM_SHARED_STATE_DIR" ]] || exit 1
    printf '%s\n' "$MYCELIUM_SHARED_STATE_DIR"
  )
}

# Returns 0 when locked and authorized, 1 for an intentional no-op, and 2
# when this event owned the transaction (ops 1-3 passed, so STATE_DIR is its
# run directory) but the lock op (4) failed: timed out, or its interpreter
# died. A failed revalidation (5-7) is Stop having accepted meanwhile, a
# no-op; those ops also reassign STATE_DIR, so no evidence is written there.
mycelium__prepare_locked_post_tool_state_once() {
  if ! mycelium__state "$1" "$2" \
    "prepare:$MYCELIUM__PREPARE_MODE" select:read-only owns \
    lock:session \
    prepare:read-only select:read-only owns \
    marker; then
    if (( MYCELIUM__STATE_FAILED == 8 )); then
      # Authorized and locked; only the marker read died. The old helper
      # read a crashed marker reader as an invalid marker and carried on.
      mycelium__state_marker_unread
      return 0
    fi
    mycelium__state_release_held
    if (( MYCELIUM__STATE_FAILED == 4 )); then
      return 2
    fi
    return 1
  fi
  return 0
}

# Lineage skip records. A command the recorder meant to record but could not
# leaves one NDJSON line saying why, so a missing event is distinguishable from
# "nothing ran". Inside a transaction the line goes into the session's own raw
# event stream (`"kind": "skip"`), which Stop consolidates into the manifest's
# `skipped` list and reports. Without a transaction it goes to the shared
# `lineage-skips-unowned.ndjson` audit file, keyed by host and host session ID.
# Neither copies data: only the command text the host already showed, a
# reason, and a short detail. The shared file also holds `"kind":
# "data_command"` records the data tracker parked while its host session had
# no transaction, under the same host and session keys.
#
# The session stream is locked on the file itself, like the extractor's
# appends. The shared file is locked through a stable sibling
# (`lineage-skips-unowned.ndjson.lock`) because Stop rewrites it by atomic
# replacement: a failed rewrite leaves the original records in place.
MYCELIUM__PY_LINEAGE_SKIP='
import fcntl, json, os, sys, time, uuid
path, ts, command, reason, detail = sys.argv[1:6]
# skip_id makes a record countable once however often a retry moves it.
# ts_ns places the gap among the records of the same second. The command
# is kept as the host showed it: Stop consumes the record within the turn
# and commits only allowlisted words of it.
record = {"ts": ts, "ts_ns": time.time_ns(), "kind": "skip", "skip_id": uuid.uuid4().hex,
          "skip_reason": reason, "detail": detail,
          "bash_cmd": command, "agent_id": None, "agent_type": None,
          "bash_exit": None}
rest = sys.argv[6:]
once = False
stable_lock = False
while len(rest) >= 2:
    flag, value = rest[0], rest[1]
    rest = rest[2:]
    if flag == "--agent-id":
        record["agent_id"] = value
    elif flag == "--agent-type":
        record["agent_type"] = value
    elif flag == "--bash-exit":
        try:
            record["bash_exit"] = int(value)
        except ValueError:
            pass
    elif flag == "--session-id":
        record["host_session_id"] = value
    elif flag == "--host":
        record["host"] = value
    elif flag == "--once":
        once = value == "true"
    elif flag == "--stable-lock":
        stable_lock = value == "true"
# The call this skip belongs to, when the tracker knows it: the index then
# matches the skip to that call observation rather than counting a gap.
call = os.environ.get("MYCELIUM__SKIP_CALL", "")
skip_host = record.get("host") or os.environ.get("MYCELIUM__SKIP_HOST", "")
if call and skip_host:
    record["observation_id"] = skip_host + ":" + call
    record.setdefault("host", skip_host)
    if os.environ.get("MYCELIUM__SKIP_SESSION"):
        record.setdefault("host_session_id", os.environ["MYCELIUM__SKIP_SESSION"])
line = (json.dumps(record) + "\n").encode("ascii")
lock_path = path + ".lock" if stable_lock else path
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "a+b") as handle:
        if once:
            # One record per gap, host, and session: decided under the lock
            # so parallel hooks cannot both write it.
            handle.seek(0)
            for existing in handle:
                try:
                    prior = json.loads(existing)
                except ValueError:
                    continue
                if (
                    isinstance(prior, dict)
                    and prior.get("kind") == "skip"
                    and prior.get("skip_reason") == record["skip_reason"]
                    and prior.get("host") == record.get("host")
                    and prior.get("host_session_id") == record.get("host_session_id")
                ):
                    raise SystemExit(0)
        handle.write(line)
        handle.flush()
finally:
    os.close(lock_fd)
'

# mycelium_record_lineage_skip EVENTS_FILE TS COMMAND REASON DETAIL [FLAGS...]
# Caller holds the session lock and owns the transaction.
mycelium_record_lineage_skip() {
  python3 -c "$MYCELIUM__PY_LINEAGE_SKIP" "$@" >/dev/null 2>&1 || true
}

# mycelium_archive_lineage_stream STREAM ARCHIVE: an abandoned session's raw
# event stream is kept only with every record under the commit allowlist
# (an accepted Stop keeps the copy consolidation wrote). The raw stream is
# removed either way.
mycelium_archive_lineage_stream() {
  python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import extract_data_lineage
extract_data_lineage.commit_stream(Path(sys.argv[1]), Path(sys.argv[2]))
' "$1" "$2" "$MYCELIUM__HOOKS_DIR/../scripts" >/dev/null 2>&1 || true
  rm -f "$1"
}

# mycelium_record_unowned_lineage_skip REPO_ROOT PAYLOAD COMMAND REASON DETAIL
# [once]: record a skip with no transaction, in the shared state directory
# only after a fresh preparation of it passed every safety check. Stop reports
# and clears the records for its own host session.
mycelium_record_unowned_lineage_skip() {
  local repo_root="$1"
  local input="$2"
  local command="$3"
  local reason="$4"
  local detail="$5"
  local once="${6:-}"
  local shared=""
  local sid=""
  # Only a valid host session can ever be reported by its own Stop; a
  # malformed identity is refused, never downgraded to an anonymous record.
  sid=$(mycelium_json_get_optional_string 'session_id' input) || return 0
  mycelium_valid_session_id "$sid" || return 0
  shared=$(mycelium__checked_shared_state_dir "$repo_root") || return 0
  mycelium_record_lineage_skip "$shared/lineage-skips-unowned.ndjson" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$command" "$reason" "$detail" \
    --session-id "$sid" --host "$(mycelium_hook_host)" --stable-lock true \
    --once "$([[ "$once" == once ]] && echo true || echo false)"
}

# mycelium_take_unowned_lineage_skips SHARED HOST SID [DEST]: move this host
# session's records out of the shared unowned-skip file. With DEST (the
# session's raw event stream, when Stop holds a valid owned transaction) they
# are appended there first; without it, its skips are taken and one LINEAGE
# GAP sentence about them is printed, and its `data_command` records (not
# gaps) are consolidated into a manifest of their own,
# `.living/log/data-lineage/data-commands-<host>-<sid>-<event id>.json`; if
# that fails they stay in the file for the next Stop. The shared file is then replaced atomically under its stable
# lock, so any failure leaves every record where it was and prints nothing.
# A rewrite that fails after the append leaves copies in both places; each
# carries its skip_id, and every reader counts a skip_id once.
MYCELIUM__PY_TAKE_UNOWNED_SKIPS='
import collections, fcntl, json, os, stat, sys, tempfile
shared, host, sid = sys.argv[1:4]
dest = sys.argv[4] if len(sys.argv) > 4 else ""
path = os.path.join(shared, "lineage-skips-unowned.ndjson")
lock_fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise SystemExit(0)
    with os.fdopen(fd, "rb") as handle:
        mode = stat.S_IMODE(os.fstat(handle.fileno()).st_mode)
        lines = handle.read().splitlines(keepends=True)
    taken, kept, waiting = [], [], []
    # A shared state directory can serve several checkouts: a v2 record
    # names its own, and only this checkout takes it.
    this_root = ""
    if os.environ.get("MYCELIUM__LINEAGE_DIR"):
        this_root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.dirname(
            os.environ["MYCELIUM__LINEAGE_DIR"]))))
    for raw in lines:
        try:
            record = json.loads(raw)
        except ValueError:
            kept.append(raw)
            continue
        if not (isinstance(record, dict) and record.get("host") == host
                and record.get("host_session_id") == sid):
            kept.append(raw)
        elif (this_root and isinstance(record.get("repo_root"), str)
              and os.path.realpath(record["repo_root"]) != this_root):
            kept.append(raw)
        elif dest or record.get("kind") != "data_command":
            taken.append(record)
        else:
            waiting.append((raw, record))
    # Without a stream to join, the data commands of this session are not gaps:
    # they get a manifest of their own, named by host session and first
    # event_id, so a retry rewrites the same file. If it cannot be written
    # they stay here for the next Stop.
    flushed = []
    if waiting:
        lineage_dir = os.environ.get("MYCELIUM__LINEAGE_DIR", "")
        extractor = os.environ.get("MYCELIUM__LINEAGE_EXTRACTOR", "")
        try:
            if not (lineage_dir and os.path.isfile(extractor)):
                raise OSError("no lineage directory or extractor")
            for part in (os.path.dirname(os.path.dirname(lineage_dir)),
                         os.path.dirname(lineage_dir), lineage_dir):
                if os.path.islink(part):
                    raise OSError("linked lineage directory")
            os.makedirs(lineage_dir, exist_ok=True)
            first = "".join(c for c in str(waiting[0][1].get("event_id") or "")
                            if c in "0123456789abcdef")[:12]
            import hashlib, re
            sid_part = sid
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sid):
                sid_part = "h" + hashlib.sha256(sid.encode()).hexdigest()[:16]
            name = f"data-commands-{host}-{sid_part}-{first}"
            temp_fd, events_name = tempfile.mkstemp(prefix=".data-commands.", dir=shared)
            try:
                with os.fdopen(temp_fd, "wb") as out:
                    out.write(b"".join(raw if raw.endswith(b"\n") else raw + b"\n"
                                       for raw, _ in waiting))
                import subprocess
                done = subprocess.run(
                    [sys.executable, extractor, "--events-file", events_name,
                     "--output", os.path.join(lineage_dir, name + ".json"),
                     "--session-id", name,
                     "--repo-root", os.path.dirname(os.path.dirname(os.path.dirname(lineage_dir)))],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            finally:
                os.unlink(events_name)
            if done.returncode != 0:
                raise OSError("extractor failed")
            flushed = waiting
        except OSError:
            kept.extend(raw for raw, _ in waiting)
    if not taken and not flushed:
        raise SystemExit(0)
    if dest:
        out_fd = os.open(dest, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(out_fd, "ab") as out:
            fcntl.flock(out.fileno(), fcntl.LOCK_EX)
            out.write(b"".join(
                (json.dumps(r) + "\n").encode("ascii")
                for r in taken
            ))
            out.flush()
    temp_fd, temp_name = tempfile.mkstemp(prefix=".lineage-skips-unowned.", dir=shared)
    try:
        with os.fdopen(temp_fd, "wb") as out:
            out.write(b"".join(kept))
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
finally:
    os.close(lock_fd)
if not dest and taken:
    reasons = collections.Counter(str(r.get("skip_reason") or "unknown") for r in taken)
    detail = ", ".join(f"{reason} x{count}" for reason, count in sorted(reasons.items()))
    print(
        f"LINEAGE GAP: {len(taken)} data command(s) this session were not recorded "
        f"({detail}), and no lineage transaction was open to hold them; treat "
        "their provenance as unknown."
    )
'

# mycelium_take_unowned_lineage_skips REPO_ROOT HOST SID [DEST]: returns
# nonzero when a transfer or rewrite failed.
mycelium_take_unowned_lineage_skips() {
  local repo_root="$1"
  local shared=""
  local override="${MYCELIUM_STATE_DIR:-}"
  shift
  # A relative state override resolves against the repository root, as the
  # state runner resolves it, not against this hook's working directory.
  if [[ -n "$override" && "$override" != /* ]]; then
    override="$repo_root/$override"
  fi
  # Stop runs this every turn; start interpreters only when there is a file.
  [[ -f "$repo_root/.mycelium/lineage-skips-unowned.ndjson" \
    || ( -n "$override" && -f "$override/lineage-skips-unowned.ndjson" ) ]] \
    || return 0
  shared=$(mycelium__checked_shared_state_dir "$repo_root") || return 0
  # A nonzero status means the records may now be in both places; the caller
  # must not accept until a retry completes the move.
  MYCELIUM__LINEAGE_DIR="$repo_root/.living/log/data-lineage" \
  MYCELIUM__LINEAGE_EXTRACTOR="${MYCELIUM_DATA_EXTRACTOR:-$MYCELIUM__HOOKS_DIR/../scripts/extract_data_lineage.py}" \
    python3 -c "$MYCELIUM__PY_TAKE_UNOWNED_SKIPS" "$shared" "$@" 2>/dev/null
}

# mycelium_reopen_post_tool_transaction REPO_ROOT PAYLOAD: open a transaction
# for the payload's own host session through SessionStart's locked setup
# (`source: reopen`), for a valid identified session only. It returns 0 when
# the caller should validate again: after asking SessionStart to open one, or
# when a marker already exists, which is usually a parallel hook's fresh
# transaction for the same call. The caller's locked validation, not this
# check, decides whether that marker is usable, so a corrupt or foreign
# marker still fails closed. SessionStart in reopen mode never touches an
# existing marker, and parallel hooks for one call open exactly one.
mycelium_reopen_post_tool_transaction() {
  local repo_root="$1"
  local input="$2"
  local sid=""
  local shared=""
  local marker=""
  local sid_json=""
  local cwd_json=""
  sid=$(mycelium_json_get_optional_string 'session_id' input) || return 1
  mycelium_valid_session_id "$sid" || return 1
  [[ -d "$repo_root/.living" && ! -L "$repo_root/.living" ]] || return 1
  if shared=$(mycelium__checked_shared_state_dir "$repo_root"); then
    marker="$shared/run/$(mycelium_hook_host)/$sid/active-session-log.tmp"
    if [[ -e "$marker" || -L "$marker" ]]; then
      return 0
    fi
  fi
  sid_json=$(mycelium_json_dumps_stdin "$sid") || return 1
  cwd_json=$(mycelium_json_dumps_stdin "$repo_root") || return 1
  printf '{"session_id": %s, "cwd": %s, "hook_event_name": "SessionStart", "source": "reopen"}' \
    "$sid_json" "$cwd_json" \
    | "$MYCELIUM__HOOKS_DIR/mycelium-health.sh" >/dev/null 2>&1
}

# mycelium_take_moved_transaction REPO_ROOT SID MODE: take over this host
# session's reservation from a sibling checkout of the same repository.
#
# A session can start in the main checkout and then move into a linked
# worktree (`cd .claude/worktrees/<name>`, or the EnterWorktree tool), or the
# reverse. SessionStart reserved its log and run state in the checkout it
# started in; every later hook resolves the checkout it moved to. Without this,
# the first work hook there opened a second transaction, Stop finalized that
# one, and the first checkout kept an untracked stub log and run state that
# nothing finalized or removed, in the working tree of whatever other session
# owns that checkout.
#
# Only linked worktrees of REPO_ROOT's repository (`git worktree list`) are
# searched, never an unrelated enclosing or nearby project. A reservation is
# taken only while it is untouched: its owner token is SID, its log is the
# untracked, unfinalized stub SessionStart wrote, and it holds no recorded work
# (no activity, data or unrecorded events, no pending handoff finalization).
# Work recorded in the other checkout stays there for its own Stop to judge.
# Both of that checkout's locks are held while it checks and removes, and a
# busy lock leaves the reservation alone.
#
# MODE `adopt` runs in SessionStart or a reopen, under REPO_ROOT's locks, with
# STATE_DIR set to this session's new run directory. It moves a handoff the
# agent wrote at the path SessionStart announced into STATE_DIR, unless one is
# already there; sets MYCELIUM_MOVED_START_TS to the session's original start
# so duration covers the whole session; and writes any project files changed
# in the other checkout since its baseline to STATE_DIR/moved-checkout-
# changes.tmp (that checkout on line 1), which Stop enforces. MODE `probe`
# changes nothing and only reports whether there is a reservation to take.
# Returns 0 after taking (or, for `probe`, finding) a reservation and sets
# MYCELIUM_MOVED_FROM to its checkout; returns 1 when there is none.
MYCELIUM_MOVED_FROM=""
MYCELIUM_MOVED_START_TS=""
mycelium_take_moved_transaction() {
  local repo_root="$1"
  local sid="$2"
  local mode="$3"
  local host=""
  local here=""
  local line=""
  local other=""
  local shared=""
  local run=""
  local override="${MYCELIUM_STATE_DIR:-}"
  MYCELIUM_MOVED_FROM=""
  MYCELIUM_MOVED_START_TS=""
  mycelium_valid_session_id "$sid" || return 1
  # A relative state override resolves against each checkout, as the state
  # runner resolves it. An absolute one is a single directory every checkout
  # shares, so there is no sibling copy to find.
  [[ "$override" != /* ]] || return 1
  host=$(mycelium_hook_host)
  here=$(cd "$repo_root" 2>/dev/null && pwd -P) || return 1
  while IFS= read -r line; do
    [[ "$line" == "worktree "* ]] || continue
    other=$(cd "${line#worktree }" 2>/dev/null && pwd -P) || continue
    [[ "$other" != "$here" ]] || continue
    shared="$other/${override:-.mycelium}"
    shared="${shared%/}"
    run="$shared/run/$host/$sid"
    [[ -d "$other/.living" && ! -L "$other/.living" \
      && -d "$shared" && ! -L "$shared" \
      && -d "$shared/run" && ! -L "$shared/run" \
      && -d "$shared/run/$host" && ! -L "$shared/run/$host" \
      && -d "$run" && ! -L "$run" \
      && -f "$run/active-session-log.tmp" \
      && ! -L "$run/active-session-log.tmp" ]] || continue
    if [[ "$mode" == probe ]]; then
      MYCELIUM_MOVED_FROM="$other"
      return 0
    fi
    if mycelium__take_moved_from "$other" "$shared" "$run" "$sid" "$mode"; then
      MYCELIUM_MOVED_FROM="$other"
      return 0
    fi
  done < <(git -C "$repo_root" worktree list --porcelain 2>/dev/null)
  return 1
}

MYCELIUM__MOVED_STOP_LOCK=""
MYCELIUM__MOVED_SESSION_LOCK=""
mycelium__release_moved_locks() {
  local lock_dir="$MYCELIUM__MOVED_SESSION_LOCK"
  MYCELIUM__MOVED_SESSION_LOCK=""
  mycelium_release_directory_lock "$lock_dir"
  lock_dir="$MYCELIUM__MOVED_STOP_LOCK"
  MYCELIUM__MOVED_STOP_LOCK=""
  mycelium_release_directory_lock "$lock_dir"
}

# The stub SessionStart writes: frontmatter, then only `## Session Log`, one
# `### HH:MM — Session started` heading, and its Branch and Resuming lines.
# Anything else means the agent wrote into it.
MYCELIUM__PY_STUB_LOG='
import re
import sys
text = open(sys.argv[1], encoding="utf-8").read()
parts = text.split("---\n", 2)
if len(parts) != 3 or parts[0] != "":
    sys.exit(1)
front, body = parts[1], parts[2]
if re.search(r"^ended: *\S", front, re.M):
    sys.exit(1)
allowed = (r"## Session Log", r"### \d\d:\d\d \u2014 Session started",
           r"- Branch: .*", r"- Resuming from: .*")
seen = [line for line in body.splitlines() if line.strip()]
if len(seen) != len(allowed):
    sys.exit(1)
for line, pattern in zip(seen, allowed):
    if not re.fullmatch(pattern, line):
        sys.exit(1)
'

# mycelium__moved_evidence RUN: succeed when RUN holds work this takeover
# cannot carry: edit activity, unrecorded or data events, or a handoff
# finalization in progress. Those stay for that checkout's own Stop.
mycelium__moved_evidence() {
  local work=""
  for work in mycelium-session-activity.tmp mycelium-unrecorded-events.tmp \
    mycelium-data-events.tmp handoff-finalization-pending.tmp; do
    if [[ -s "$1/$work" || -L "$1/$work" ]]; then
      return 0
    fi
  done
  [[ -e "$1/mycelium-session-activity.tmp" \
    || -e "$1/handoff-finalization-pending.tmp" ]]
}

mycelium__take_moved_from() {
  local other="$1"
  local shared="$2"
  local run="$3"
  local sid="$4"
  local mode="$5"
  local log=""
  local text=""
  local marker_ts=""
  local carried=""
  local changes=""
  local helper=""
  local collect_args=()
  local start_ts=""
  local entry=""
  local session_lock=""
  local index_script=""
  local attempts="${MYCELIUM_MOVED_LOCK_MAX_ATTEMPTS:-100}"
  local status=1

  [[ -n "$shared" && -n "$run" ]] || return 1
  mycelium_acquire_directory_lock "$shared" "mycelium-stop.lock" \
    MYCELIUM__MOVED_STOP_LOCK "$attempts" || return 1
  if ! mycelium_acquire_directory_lock "$run" "mycelium-session.lock" \
    MYCELIUM__MOVED_SESSION_LOCK "$attempts"; then
    mycelium__release_moved_locks
    return 1
  fi
  while true; do
    # The same shapes the state runner's owner and marker readers accept:
    # exactly the session ID on one line, and a marker of the log path, a
    # numeric owner time and the owner-id-v1 format line.
    [[ -f "$run/active-session-owner-id.tmp" \
      && ! -L "$run/active-session-owner-id.tmp" \
      && -f "$run/active-session-log.tmp" \
      && ! -L "$run/active-session-log.tmp" ]] || break
    text=$(cat "$run/active-session-owner-id.tmp" 2>/dev/null && printf x) || break
    [[ "$text" == "$sid"$'\n'x ]] || break
    text=$(cat "$run/active-session-log.tmp" 2>/dev/null && printf x) || break
    log="${text%%$'\n'*}"
    text="${text#*$'\n'}"
    marker_ts="${text%%$'\n'*}"
    [[ "$marker_ts" =~ ^[0-9]{1,18}$ \
      && "${text#*$'\n'}" == "owner-id-v1"$'\n'x ]] || break
    [[ "$log" == "$other/.living/log/"*.md \
      && "${log#"$other/.living/log/"}" != */* \
      && -f "$log" && ! -L "$log" \
      && -d "$other/.living/log" && ! -L "$other/.living/log" ]] || break
    mycelium__moved_evidence "$run" && break
    if git -C "$other" ls-files --error-unmatch -- "$log" >/dev/null 2>&1; then
      break
    fi
    python3 -I -S -c "$MYCELIUM__PY_STUB_LOG" "$log" 2>/dev/null || break

    if [[ "$mode" == adopt ]]; then
      [[ -n "${STATE_DIR:-}" && -d "$STATE_DIR" && ! -L "$STATE_DIR" ]] || break
      # Everything that checkout still owed reflection for moves with the
      # session, as absolute paths (or a note naming that checkout), into
      # STATE_DIR/moved-checkout-changes.tmp for this checkout's Stop.
      # Shell commands (`sed -i`, `printf >`) record no activity, so compare
      # that checkout with its session baseline, as its own Stop would have;
      # another session's concurrent edits there cost one triage, never
      # silence about this session's. A record it carried from an earlier move
      # comes along, and so does a work cycle its Stop started and did not see
      # reflected (mycelium-reminded.tmp).
      helper="${MYCELIUM_SESSION_CHANGES_HELPER:-$MYCELIUM__HOOKS_DIR/../scripts/session_file_changes.py}"
      start_ts=$(head -1 "$run/session-start-ts.tmp" 2>/dev/null || true)
      collect_args=(collect --repo-root "$other" \
        --baseline "$run/session-file-baseline.json" \
        --exclude ".living/log/LOG_REGISTRY.md" --exclude-prefix ".living/log/" \
        --exclude ".living/INDEX.md")
      [[ "$start_ts" =~ ^[0-9]{1,18}$ ]] && collect_args+=(--start-ts "$start_ts")
      if [[ ! -f "$run/session-file-baseline.json" \
        || -L "$run/session-file-baseline.json" || ! -f "$helper" ]] \
        || ! changes=$(python3 "$helper" "${collect_args[@]}" 2>/dev/null); then
        carried="$other (could not compare with this session's baseline)"$'\n'
      else
        while IFS= read -r entry; do
          [[ -n "$entry" ]] && carried="$carried$other/$entry"$'\n'
        done <<<"$changes"
      fi
      if [[ -f "$run/moved-checkout-changes.tmp" \
        && ! -L "$run/moved-checkout-changes.tmp" ]]; then
        text=$(cat "$run/moved-checkout-changes.tmp" 2>/dev/null && printf x) \
          || break
        carried="$carried${text%x}"
      elif [[ -e "$run/moved-checkout-changes.tmp" \
        || -L "$run/moved-checkout-changes.tmp" ]]; then
        break
      fi
      if [[ -e "$run/mycelium-reminded.tmp" || -L "$run/mycelium-reminded.tmp" ]]; then
        carried="$carried$other (work whose .living/ reflection was still owed there)"$'\n'
      fi
      # A post-tool hook that timed out on the session lock records its event
      # outside that lock; look once more just before anything moves.
      mycelium__moved_evidence "$run" && break
      if [[ -n "$carried" ]]; then
        printf '%s' "$carried" >> "$STATE_DIR/moved-checkout-changes.tmp" \
          2>/dev/null || break
      fi
      if [[ "$start_ts" =~ ^[0-9]{1,18}$ ]]; then
        MYCELIUM_MOVED_START_TS="$start_ts"
      fi
      # A one-time handoff explanation that transaction already received
      # moves with it, so the move cannot grant a second one. The adopting
      # transaction has no log yet, so the marker says "carried", not an ID.
      if [[ -f "$run/handoff-check-blocked.tmp" && ! -L "$run/handoff-check-blocked.tmp" ]]; then
        text=$(head -n 1 "$run/handoff-check-blocked.tmp" 2>/dev/null || true)
        if [[ "$text" == carried \
          || "$text" == "$(sed -n '/^session_id:/{s/^session_id:[[:space:]]*//;p;q;}' "$log" 2>/dev/null || true)" ]]; then
          printf 'carried\n' > "$STATE_DIR/handoff-check-blocked.tmp" 2>/dev/null || break
        fi
      fi
      # The handoff the agent wrote at the path SessionStart announced. When
      # this checkout already holds one, append it rather than lose either.
      if [[ -f "$run/last-session.md" && ! -L "$run/last-session.md" ]]; then
        if [[ ! -e "$STATE_DIR/last-session.md" && ! -L "$STATE_DIR/last-session.md" ]]; then
          mv "$run/last-session.md" "$STATE_DIR/last-session.md" 2>/dev/null \
            || break
        elif [[ -f "$STATE_DIR/last-session.md" && ! -L "$STATE_DIR/last-session.md" ]]; then
          { printf '\n\n<!-- Handoff carried from %s -->\n\n' "$other"
            cat "$run/last-session.md"; } >> "$STATE_DIR/last-session.md" \
            2>/dev/null || break
        else
          break
        fi
      fi
    else
      mycelium__moved_evidence "$run" && break
    fi
    rm -f "$log"
    for entry in "$run"/* "$run"/.[!.]*; do
      [[ -e "$entry" || -L "$entry" ]] || continue
      [[ "$entry" == "$MYCELIUM__MOVED_SESSION_LOCK" ]] && continue
      [[ -d "$entry" && ! -L "$entry" ]] && continue
      rm -f "$entry"
    done
    status=0
    break
  done
  session_lock="$MYCELIUM__MOVED_SESSION_LOCK"
  MYCELIUM__MOVED_SESSION_LOCK=""
  mycelium_release_directory_lock "$session_lock"
  if (( status == 0 )); then
    rmdir "${run:?}" "${shared:?}/run/$(mycelium_hook_host)" "${shared:?}/run" \
      2>/dev/null || true
    # SessionStart's INDEX refresh counted the stub; recount without it, as
    # SessionStart does (no LLM, <1s), still under that checkout's lock.
    index_script="$MYCELIUM__HOOKS_DIR/../scripts/generate_index.py"
    if [[ -f "$other/.living/INDEX.md" && ! -L "$other/.living/INDEX.md" \
      && -f "$index_script" ]]; then
      python3 "$index_script" --living-dir "$other/.living" --summary-heuristic \
        >/dev/null 2>&1 \
        || python3 "$index_script" --living-dir "$other/.living" --counts-only \
          >/dev/null 2>&1 \
        || true
    fi
  fi
  mycelium__release_moved_locks
  return "$status"
}

# Filters stdin into one Markdown table cell. A value of letters, digits,
# `.`, `_`, `/`, and `-` (plus trailing newlines) is already its own cell, so
# it skips the interpreter; anything else, including a NUL byte, goes to it.
MYCELIUM__PY_REGISTRY_CELL='
import html
import sys
value = sys.stdin.read().replace("\r", " ").replace("\n", " ")
print(html.escape(" ".join(value.split()), quote=True).replace("|", "&#124;"))
'

mycelium_registry_cell() {
  local value=""
  if IFS= read -r -d '' value; then
    { printf '%s\0' "$value"; cat; } | python3 -c "$MYCELIUM__PY_REGISTRY_CELL"
    return
  fi
  if mycelium__plain_cell "$value"; then
    mycelium__fast_write "$MYCELIUM__CELL"$'\n' && return 0
  fi
  printf '%s' "$value" | python3 -c "$MYCELIUM__PY_REGISTRY_CELL"
}

# Sets MYCELIUM__CELL when VALUE without its trailing newlines is plain.
# Matching runs on bytes. No interpreter may start in this C-locale scope:
# an exported LC_ALL would reach it.
mycelium__plain_cell() {
  local trimmed="$1"
  local LC_ALL=C
  local plain_re='^[A-Za-z0-9._/-]*$'
  local fs="" stdin="" stdout=""
  # Python would print through its own stdout encoding (utf-16 adds a BOM and
  # fails on a pipe), so only a reported UTF-8 interpreter is matched.
  read -r fs stdin stdout <<< "$MYCELIUM__PC_ENC" || true
  [[ "$stdin" == utf-8/* && "$stdout" == utf-8/* ]] || return 1
  while [[ "$trimmed" == *$'\n' ]]; do
    trimmed="${trimmed%$'\n'}"
  done
  [[ "$trimmed" =~ $plain_re ]] || return 1
  MYCELIUM__CELL="$trimmed"
}

# mycelium_json_dumps_stdin VALUE: print json.dumps of VALUE as an
# interpreter reading it from stdin would, failing as that would.
mycelium_json_dumps_stdin() {
  if mycelium__json_literal stdin "$1"; then
    mycelium__fast_write "$MYCELIUM__JSON"$'\n' && return 0
  fi
  printf '%s' "$1" | python3 -c 'import json, sys; print(json.dumps(sys.stdin.read()))'
}

mycelium_emit_stop_block() {
  local reason="$1"
  local escaped_reason=""
  escaped_reason=$(mycelium_json_dumps_stdin "$reason" 2>/dev/null) || return 1
  # SIGPIPE-safe: Stop writes this while holding its locks.
  mycelium__fast_write "{\"decision\": \"block\", \"reason\": $escaped_reason}"$'\n'
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
  mycelium_start_work_cycle "$repo_root" "$helper" || true
  return 0
}

# mycelium_start_work_cycle REPO_ROOT HELPER: fingerprint .living/ now and
# stamp the reminder, so only a .living/ update made after this point can
# satisfy Stop. The caller holds the session lock. Returns nonzero when the
# fingerprint could not be taken (the reminder is still stamped).
mycelium_start_work_cycle() {
  local repo_root="$1"
  local helper="$2"
  local baseline_file="$STATE_DIR/living-reminder-baseline.json"
  local status=0
  if [[ -f "$helper" ]]; then
    python3 "$helper" living-snapshot \
      --repo-root "$repo_root" \
      --output "$baseline_file" >/dev/null 2>&1 \
      || { rm -f "$baseline_file"; status=1; }
  else
    status=1
  fi
  # Publish by replacement: a FIFO or hard link at the name is replaced, never
  # opened. `mv` onto a directory would move into it, so that is refused.
  local reminder="$STATE_DIR/mycelium-reminded.tmp"
  local temp=""
  if [[ -d "$reminder" && ! -L "$reminder" ]] \
    || ! temp=$(mktemp "$STATE_DIR/.mycelium-reminded.XXXXXX" 2>/dev/null) \
    || ! date +%s > "$temp" \
    || ! mv -f "$temp" "$reminder" 2>/dev/null; then
    if [[ -n "$temp" ]]; then
      rm -f "$temp"
    fi
    status=1
  fi
  return "$status"
}

# Contenders claim reaping inside the lock directory itself; release must wait
# for such a claim to drain before removing the directory.
MYCELIUM_LOCK_REAP_CLAIM=".mycelium-reap.claim"

# Create one directory and succeed only if this call created it. Lock
# acquisition and reap claims depend on mkdir(2) failing with EEXIST for every
# contender but one, so this must not delegate to the mkdir utility: uutils
# coreutils 0.10.0 (the default /usr/bin/mkdir on Ubuntu 26.04) exits 0 without
# -p when its create fails but the path is a directory afterwards, so a
# contender that loses the creation race also believes it won. Python's
# os.mkdir is the bare system call. -I keeps the untrusted checkout's cwd and
# PYTHON* environment out of the interpreter; -S skips site initialization.
# An existing path fails fast without starting an interpreter, so waiting on a
# held lock costs no more per attempt than before; that check only ever
# reports what a slightly earlier EEXIST would have, and the system call
# remains the sole arbiter of who created the directory.
mycelium_mkdir_exclusive() {
  [[ ! -e "$1" && ! -L "$1" ]] || return 1
  python3 -I -S -c 'import os, sys; os.mkdir(sys.argv[1])' "$1" 2>/dev/null
}

# Test seam for the reap interleavings: a no-op unless the test sets
# MYCELIUM_TEST_LOCK_PAUSE_DIR. It then marks the named step as reached for
# this contender (MYCELIUM_TEST_LOCK_PAUSE_TAG) and waits, for at most 30 s,
# while the test holds that step.
mycelium_lock_test_pause() {
  local dir="${MYCELIUM_TEST_LOCK_PAUSE_DIR:-}"
  local tag="${MYCELIUM_TEST_LOCK_PAUSE_TAG:-}"
  local waited=0
  [[ -n "$dir" && -n "$tag" ]] || return 0
  : > "$dir/$tag.$1.reached"
  while [[ -e "$dir/$tag.$1.hold" ]] && (( waited++ < 3000 )); do
    sleep 0.01
  done
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
  local tombstone=""
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
  while ! mycelium_mkdir_exclusive "$lock_dir"; do
    attempts=$((attempts + 1))
    owner_pid=""
    owner_ts=""
    owner_record=""
    owner_is_live=false
    owner_is_dead=false
    if [[ -L "$lock_dir" || ( -e "$lock_dir" && ! -d "$lock_dir" ) ]]; then
      return 1
    fi
    # Capture the generation before observing its owner and age, so a claim
    # cannot validate a successor against evidence gathered from its
    # predecessor.
    lock_inode_before=$(mycelium_file_inode "$lock_dir" 2>/dev/null || true)
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
    # An unreadable mtime (reported as 0) usually means the lock vanished
    # between mkdir and stat, which is the next owner's publication window,
    # so it never counts as age.
    if [[ "$owner_is_dead" == true ]] \
      || { [[ "$owner_is_live" != true && "$lock_mtime" =~ ^[0-9]+$ ]] \
        && (( lock_mtime > 0 && now_ts - lock_mtime > 300 )); }; then
      reap_claim="$lock_dir/$MYCELIUM_LOCK_REAP_CLAIM"
      reap_allowed=false
      mycelium_lock_test_pause reap-before-claim
      if [[ -n "$lock_inode_before" ]] \
        && mycelium_mkdir_exclusive "$reap_claim"; then
        mycelium_lock_test_pause reap-claimed
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
        # Retire the reaped generation by renaming it, with this claim still
        # inside, into a private tombstone, and only then clean it up. While
        # the claim is held no other claim can exist, and after the rename
        # the lock path is either absent or a successor's lock, so a late
        # reaper can never claim inside a half-reaped lock. Dismantling it in
        # place let one do so: the reaper's final rmdir failed and the late
        # reaper, seeing the owner record vanish, withdrew, stranding an
        # ownerless, freshly modified lock for the 300 s age guard. The
        # tombstone sits beside the lock, on the same filesystem, where
        # rename(2) is atomic; os.rename is the bare system call. A rename that reports failure but happened (an NFS retry)
        # still counts. If it did not happen, withdraw the claim and retry.
        if [[ "$reap_allowed" == true ]] \
          && tombstone=$(mktemp -d "$state_dir/.$lock_name.reaped.XXXXXX" 2>/dev/null); then
          python3 -I -S -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' \
            "$lock_dir" "$tombstone/lock" 2>/dev/null || true
          if [[ -d "$tombstone/lock" && ! -L "$tombstone/lock" ]]; then
            mycelium_lock_test_pause reap-committed
            rm -rf "$tombstone"
            continue
          fi
          rmdir "$tombstone" 2>/dev/null || true
        fi
        rmdir "$reap_claim" 2>/dev/null || true
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
  local attempts=0
  if [[ -n "$lock_dir" && -d "$lock_dir" && ! -L "$lock_dir" ]]; then
    # A delayed reaper can hold its claim inside this lock while it revalidates.
    # Keep the owner record until that claim drains: the reaper then sees a
    # record other than the one it observed and withdraws, so it can never
    # recover this lock and hand the path to a successor mid-release. Removing
    # the directory with a claim still inside would fail and leave an
    # ownerless, freshly modified lock that every waiter outwaits. A failed
    # rmdir is retried even when no claim remains: one may have drained
    # between the rmdir and any check of it.
    while true; do
      while [[ -d "$lock_dir/$MYCELIUM_LOCK_REAP_CLAIM" ]] \
        && (( attempts++ < 200 )); do
        sleep 0.05
      done
      rm -f "$lock_dir/owner"
      if rmdir "$lock_dir" 2>/dev/null || (( attempts++ >= 200 )); then
        break
      fi
    done
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
  # Forget the lock before removing it: if this hook is killed in between,
  # an EXIT trap must not release the path a successor may already hold.
  local lock_dir="${MYCELIUM_STOP_LOCK_DIR:-}"
  MYCELIUM_STOP_LOCK_DIR=""
  mycelium_release_directory_lock "$lock_dir"
}

mycelium_acquire_session_lock() {
  mycelium_acquire_directory_lock \
    "$1" \
    "mycelium-session.lock" \
    MYCELIUM_SESSION_LOCK_DIR \
    "${MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS:-600}"
}

mycelium_release_session_lock() {
  # Forget the lock before removing it: if this hook is killed in between,
  # an EXIT trap must not release the path a successor may already hold.
  local lock_dir="${MYCELIUM_SESSION_LOCK_DIR:-}"
  MYCELIUM_SESSION_LOCK_DIR=""
  mycelium_release_directory_lock "$lock_dir"
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
    codex|claude|antigravity-ide|antigravity-app) printf '%s\n' "$MYCELIUM_HOOK_HOST" ;;
    *) printf '%s\n' "claude" ;;
  esac
}

# Payload decoders. Each body runs as `python3 -c BODY [PATH]` with the hook
# payload on stdin. mycelium_payload_preload runs the same bodies in one
# interpreter, so a hook pays for one Python start instead of one per field.
MYCELIUM__PY_JSON_GET='
import json, sys
try:
    value = json.load(sys.stdin)
    for key in sys.argv[1].split("."):
        value = value.get(key) if isinstance(value, dict) else None
    if value is not None and not isinstance(value, (dict, list)):
        print(value)
except Exception:
    pass
'

MYCELIUM__PY_JSON_OPTIONAL_STRING='
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
'

MYCELIUM__PY_BASH_EXIT='
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
        # stdout and stderr are what the command printed, never a host
        # header: a command whose output starts "Exit code 7" exited 0.
        for key, candidate in value.items():
            if key in ("stdout", "stderr"):
                continue
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
        # Only the hosts own header is exit evidence: a line of the header
        # block before an "Output:" line (Codex puts "Process exited with
        # code N" after Chunk ID and Wall time lines), or, with no such
        # block, a header at the very start. The same words anywhere in the
        # captured output of the command are not: one that prints "exit
        # code 0" and then fails must not be recorded as a success.
        exit_rx = (
            r"(?:process\s+)?(?:exit(?:ed)?(?:[ _-]with)?(?:[ _-]code)?|return(?:[ _-]code)?)"
            r"[\":= ]+(-?\d+)"
        )
        header, has_output, _rest = value.partition("\nOutput:")
        if has_output:
            match = re.search(r"(?mi)^[ \t]*" + exit_rx, header)
        else:
            match = re.match(r"\s*" + exit_rx, value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

result = find_structured(response)
if result is None:
    result = find_textual(response)
if result is not None:
    print(result)
'

# The activity tracker's verdict on an edit: true, false, or unknown.
MYCELIUM__PY_EDIT_STATUS='
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
        for key, candidate in value.items():
            if key in ("stdout", "stderr"):
                continue  # printed output, never a host header
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
        # Only the hosts own header is exit evidence: a line of the header
        # block before an "Output:" line (Codex puts "Process exited with
        # code N" after Chunk ID and Wall time lines), or, with no such
        # block, a header at the very start. The same words anywhere in the
        # captured output of the command are not: one that prints "exit
        # code 0" and then fails must not be recorded as a success.
        exit_rx = (
            r"(?:process\s+)?(?:exit(?:ed)?(?:[ _-]with)?(?:[ _-]code)?|return(?:[ _-]code)?)"
            r"[\":= ]+(-?\d+)"
        )
        header, has_output, _rest = value.partition("\nOutput:")
        if has_output:
            match = re.search(r"(?mi)^[ \t]*" + exit_rx, header)
        else:
            match = re.match(r"\s*" + exit_rx, value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

status = structured_exit_code(response)
if status is None:
    status = textual_exit_code(response)
print("unknown" if status is None else ("true" if status == 0 else "false"))
'

# SessionStart's cwd, as `echo "$PAYLOAD" | python3 -c BODY` printed it.
MYCELIUM__PY_START_CWD="import sys,json; print(json.load(sys.stdin).get('cwd', ''))"

# Runs several decoder bodies against one payload. argv holds
# (slot, body, path-or-empty, has-path) quadruples; each body sees exactly the
# stdin, argv, stdout encoding, and exit semantics of its own `python3 -c`.
# A body that raises, exits with a non-integer, or writes a NUL byte is left
# out, and its callers fall back to the separate interpreter.
MYCELIUM__PY_PRELOAD='
import io
import sys

raw = sys.stdin.buffer.read()


# A body run through exec sits a few frames deeper than the same body run by
# python3 -c, so deep JSON can hit the recursion limit here when it would
# not on its own (the decoders swallow that error). Never the other way
# round: this process has no more headroom. So note every RecursionError the
# JSON decoder raises and leave those slots to the separate decoders.
import json

real_load, real_loads = json.load, json.loads
recursion_hit = []


def watched_load(*args, **kwargs):
    try:
        return real_load(*args, **kwargs)
    except RecursionError:
        recursion_hit.append(True)
        raise


def watched_loads(*args, **kwargs):
    try:
        return real_loads(*args, **kwargs)
    except RecursionError:
        recursion_hit.append(True)
        raise
in_encoding, in_errors = sys.stdin.encoding, sys.stdin.errors
out_encoding, out_errors = sys.stdout.encoding, sys.stdout.errors
real_stdin, real_stdout, real_argv = sys.stdin, sys.stdout, sys.argv
records = []
args = sys.argv[1:]
for index in range(0, len(args) - 3, 4):
    slot, body, path, has_path = args[index:index + 4]
    captured = io.BytesIO()
    sys.stdin = io.TextIOWrapper(
        io.BytesIO(raw), encoding=in_encoding, errors=in_errors, newline="\n"
    )
    sys.stdout = io.TextIOWrapper(
        captured, encoding=out_encoding, errors=out_errors, newline="\n"
    )
    sys.argv = ["-c", path] if has_path == "1" else ["-c"]
    status = 0
    del recursion_hit[:]
    json.load, json.loads = watched_load, watched_loads
    try:
        try:
            exec(compile(body, "<string>", "exec"), {"__name__": "__main__"})
        except SystemExit as stop:
            code = stop.code
            if code is None:
                status = 0
            elif isinstance(code, int) and not isinstance(code, bool):
                status = code
            else:
                raise
        sys.stdout.flush()
        output = captured.getvalue()
    except BaseException:
        continue
    finally:
        sys.stdin, sys.stdout, sys.argv = real_stdin, real_stdout, real_argv
        json.load, json.loads = real_load, real_loads
    if recursion_hit or b"\0" in output or not 0 <= status <= 255:
        continue
    records.append((slot, status, output))
out = sys.stdout.buffer
# The text encodings a separate `python3 -c` started by this hook would use,
# so bash can tell when it may encode a JSON string itself (see
# mycelium__json_literal). Its stdin and stdout are pipes, like this process has.
try:
    import codecs

    def canonical(name):
        return codecs.lookup(name).name

    encodings = "%s/%s %s/%s %s/%s" % (
        canonical(sys.getfilesystemencoding()), sys.getfilesystemencodeerrors(),
        canonical(in_encoding), in_errors,
        canonical(out_encoding), out_errors,
    )
    out.write(b"env:\0" + b"0\0" + encodings.encode("ascii") + b"\0")
except Exception:
    pass
for slot, status, output in records:
    out.write(slot.encode() + b"\0" + str(status).encode() + b"\0" + output + b"\0")
out.flush()
'

MYCELIUM__PC_RAW=""
MYCELIUM__PC_ENC=""
MYCELIUM__PC_READY=false
# Above this many bytes, comparing the payload on every cached read costs more
# than starting a decoder, so large payloads keep one interpreter per field.
MYCELIUM__PC_MAX_BYTES=65536
MYCELIUM__PC_BYTES=0
MYCELIUM__PC_OUT=""
MYCELIUM__PC_STATUS=0
MYCELIUM__PC_VAR=""
# Kept across a repeated `source` so the next preload can still clear them.
MYCELIUM__PC_SLOT_VARS="${MYCELIUM__PC_SLOT_VARS-}"

# Print the cached answer. A closed or failing stdout makes this fail rather
# than kill the shell with SIGPIPE, so the caller can let the real decoder
# meet the same stdout.
# Write TEXT for a fast path. Like the cached decoders, a closed or failing
# stdout makes this fail rather than kill the shell with SIGPIPE (which would
# skip the hook's EXIT trap and leave its locks held); the caller then runs
# the interpreter, which meets the same stdout and reports what it would.
mycelium__fast_write() {
  ( trap '' PIPE; printf '%s' "$1" ) 2>/dev/null
}

mycelium__payload_write() {
  ( trap '' PIPE; printf '%s' "$MYCELIUM__PC_OUT" ) 2>/dev/null
}

# Payload helpers take the NAME of the variable holding the payload, never
# its value: on bash 3.2 every multi-megabyte function argument costs tens of
# milliseconds, more than the Python start the cache saves.
mycelium__payload_name_ok() {
  [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && "$1" != __mycelium_* ]]
}

# Sets MYCELIUM__PC_BYTES to the byte length of the payload in variable $1.
# The C locale stays local to this function so the decoders still run in the
# caller's locale.
mycelium__payload_bytes() {
  local LC_ALL=C
  mycelium__payload_name_ok "$1" || return 1
  eval "MYCELIUM__PC_BYTES=\${#$1}"
}

# Slot names are fixed decoder kinds plus dotted payload paths.
mycelium__payload_slot_ok() {
  [[ "$1" =~ ^(get|opt|exit):[A-Za-z_][A-Za-z0-9_.]*$|^(exit|edit|cwd0):$ ]]
}

# Sets MYCELIUM__PC_VAR to the variable prefix that stores SLOT.
mycelium__payload_slot_var() {
  local slot="$1"
  # Escape `_` first so every `_` in the result starts a pair: injective.
  slot="${slot//_/_U}"
  slot="${slot//:/_C}"
  slot="${slot//./_D}"
  MYCELIUM__PC_VAR="MYCELIUM__PC_SLOT_$slot"
}

# mycelium_payload_preload VAR SLOT...: decode the named fields of the payload
# held in variable VAR in a single Python process, keeping the exact stdout
# and exit status each decoder would have produced. Slots are `get:PATH`,
# `opt:PATH`, or `exit:`. Never fails: a slot that cannot be preloaded, or a
# payload over MYCELIUM__PC_MAX_BYTES, falls back to one interpreter per read.
mycelium_payload_preload() {
  local __mycelium_name="$1"
  shift
  local -a argv=()
  local slot="" body="" path="" status="" output="" var=""
  local IFS=$' \t\n'
  local restore_nocasematch=false

  if shopt -q nocasematch; then
    restore_nocasematch=true
    shopt -u nocasematch
  fi
  MYCELIUM__PC_READY=false
  MYCELIUM__PC_RAW=""
  for var in $MYCELIUM__PC_SLOT_VARS; do
    unset "${var}_S" "${var}_O"
  done
  MYCELIUM__PC_SLOT_VARS=""
  if ! mycelium__payload_bytes "$__mycelium_name" \
    || (( MYCELIUM__PC_BYTES > MYCELIUM__PC_MAX_BYTES )); then
    [[ "$restore_nocasematch" == false ]] || shopt -s nocasematch
    return 0
  fi
  for slot in "$@"; do
    mycelium__payload_slot_ok "$slot" || continue
    path="${slot#*:}"
    case "${slot%%:*}" in
      get) body="$MYCELIUM__PY_JSON_GET" ;;
      opt) body="$MYCELIUM__PY_JSON_OPTIONAL_STRING" ;;
      exit) body="$MYCELIUM__PY_BASH_EXIT" ;;
      edit) body="$MYCELIUM__PY_EDIT_STATUS" ;;
      cwd0) body="$MYCELIUM__PY_START_CWD" ;;
    esac
    mycelium__payload_slot_var "$slot"
    var="$MYCELIUM__PC_VAR"
    printf -v "${var}_S" '%s' ""
    MYCELIUM__PC_SLOT_VARS="$MYCELIUM__PC_SLOT_VARS $var"
    if [[ "$slot" == *: ]]; then
      argv+=("$slot" "$body" "" 0)
    else
      argv+=("$slot" "$body" "$path" 1)
    fi
  done
  if (( ${#argv[@]} == 0 )); then
    [[ "$restore_nocasematch" == false ]] || shopt -s nocasematch
    return 0
  fi

  MYCELIUM__PC_ENC=""
  while IFS= read -r -d '' slot \
    && IFS= read -r -d '' status \
    && IFS= read -r -d '' output; do
    if [[ "$slot" == "env:" ]]; then
      MYCELIUM__PC_ENC="$output"
      continue
    fi
    mycelium__payload_slot_ok "$slot" || continue
    [[ "$status" =~ ^[0-9]+$ ]] || continue
    mycelium__payload_slot_var "$slot"
    var="$MYCELIUM__PC_VAR"
    printf -v "${var}_O" '%s' "$output"
    printf -v "${var}_S" '%s' "$status"
  done < <(printf '%s' "${!__mycelium_name}" \
    | python3 -c "$MYCELIUM__PY_PRELOAD" "${argv[@]}" 2>/dev/null)

  MYCELIUM__PC_RAW="${!__mycelium_name}"
  MYCELIUM__PC_READY=true
  [[ "$restore_nocasematch" == false ]] || shopt -s nocasematch
  return 0
}

# Succeeds, setting MYCELIUM__PC_OUT and MYCELIUM__PC_STATUS, when SLOT was
# preloaded for exactly the payload now held in variable $2. Only small
# payloads are ever preloaded, so the comparison stays cheap.
mycelium__payload_cached() {
  local __mycelium_slot="$1"
  local __mycelium_name="$2"
  local __mycelium_var="" __mycelium_status="" __mycelium_output=""
  local __mycelium_same=false

  [[ "$MYCELIUM__PC_READY" == true ]] || return 1
  mycelium__payload_name_ok "$__mycelium_name" || return 1
  mycelium__payload_slot_ok "$__mycelium_slot" || return 1
  if shopt -q nocasematch; then
    shopt -u nocasematch
    [[ "${!__mycelium_name}" == "$MYCELIUM__PC_RAW" ]] && __mycelium_same=true
    shopt -s nocasematch
  else
    [[ "${!__mycelium_name}" == "$MYCELIUM__PC_RAW" ]] && __mycelium_same=true
  fi
  [[ "$__mycelium_same" == true ]] || return 1
  mycelium__payload_slot_var "$__mycelium_slot"
  __mycelium_var="$MYCELIUM__PC_VAR"
  __mycelium_status="${__mycelium_var}_S"
  __mycelium_output="${__mycelium_var}_O"
  [[ -n "${!__mycelium_status:-}" ]] || return 1
  MYCELIUM__PC_STATUS="${!__mycelium_status}"
  MYCELIUM__PC_OUT="${!__mycelium_output:-}"
  return 0
}

# mycelium_session_repo_root CWD [SESSION_ID]: print the repository root a
# session hook acts on. A host may run hooks in the main checkout while the
# session works in a linked worktree (issue #144), so the session's own
# directory (the payload `cwd`) wins when its repository is opted in. When it
# is not (a turn that ended after `cd` into a vendored checkout or submodule),
# walk outward and take an enclosing opted-in repository only if it already
# holds this session's run directory: the worktree that started the session
# keeps it, while an unrelated project that merely encloses the directory gains
# no state. Otherwise fall back to the hook process's directory. Prints nothing
# outside a repository.
mycelium_session_repo_root() {
  local dir="${1:-}"
  local sid="${2:-}"
  local root=""
  local host=""
  if [[ -n "$dir" && -d "$dir" ]]; then
    root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)
    if [[ -n "$root" && -d "$root/.living" ]]; then
      printf '%s\n' "$root"
      return 0
    fi
    if [[ -n "$root" ]] && mycelium_valid_session_id "$sid"; then
      host=$(mycelium_hook_host)
      while [[ "$root" != "/" ]]; do
        dir=$(dirname "$root")
        root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || break
        [[ -n "$root" ]] || break
        if [[ -d "$root/.living" && -d "$root/.mycelium/run/$host/$sid" \
          && ! -L "$root/.mycelium/run/$host/$sid" ]]; then
          printf '%s\n' "$root"
          return 0
        fi
      done
    fi
  fi
  git rev-parse --show-toplevel 2>/dev/null || true
}

# mycelium_json_get PATH [VAR]: print a scalar field of the payload held in
# variable VAR, or read from stdin when VAR is omitted.
mycelium_json_get() {
  local __mycelium_path="$1"
  if (( $# > 1 )); then
    mycelium__payload_name_ok "$2" || return 1
    if mycelium__payload_cached "get:$__mycelium_path" "$2"; then
      # If stdout cannot take the answer, let the decoder meet the same
      # stdout and report whatever it would.
      mycelium__payload_write && return "$MYCELIUM__PC_STATUS"
    fi
    printf '%s' "${!2}" | python3 -c "$MYCELIUM__PY_JSON_GET" "$__mycelium_path"
    return
  fi
  python3 -c "$MYCELIUM__PY_JSON_GET" "$__mycelium_path"
}

# mycelium_json_get_optional_string PATH [VAR]: print a string field, or
# nothing when it is absent or null; fail on malformed JSON or a non-string.
mycelium_json_get_optional_string() {
  local __mycelium_path="$1"
  if (( $# > 1 )); then
    mycelium__payload_name_ok "$2" || return 1
    if mycelium__payload_cached "opt:$__mycelium_path" "$2"; then
      mycelium__payload_write && return "$MYCELIUM__PC_STATUS"
    fi
    printf '%s' "${!2}" \
      | python3 -c "$MYCELIUM__PY_JSON_OPTIONAL_STRING" "$__mycelium_path"
    return
  fi
  python3 -c "$MYCELIUM__PY_JSON_OPTIONAL_STRING" "$__mycelium_path"
}

# mycelium_bash_exit [VAR]: print the tool exit status of the payload held in
# variable VAR, or read from stdin when VAR is omitted.
mycelium_bash_exit() {
  if (( $# > 0 )); then
    mycelium__payload_name_ok "$1" || return 1
    if mycelium__payload_cached "exit:" "$1"; then
      mycelium__payload_write && return "$MYCELIUM__PC_STATUS"
    fi
    printf '%s' "${!1}" | python3 -c "$MYCELIUM__PY_BASH_EXIT"
    return
  fi
  python3 -c "$MYCELIUM__PY_BASH_EXIT"
}

# mycelium_edit_status VAR: print true, false, or unknown for the edit whose
# payload is held in variable VAR.
mycelium_edit_status() {
  mycelium__payload_name_ok "$1" || return 1
  if mycelium__payload_cached "edit:" "$1"; then
    mycelium__payload_write && return "$MYCELIUM__PC_STATUS"
  fi
  printf '%s' "${!1}" | python3 -c "$MYCELIUM__PY_EDIT_STATUS"
}

# mycelium_session_start_cwd VAR: SessionStart's cwd field of the payload
# held in variable VAR, printing nothing when it cannot be read. echo hands
# the decoder the payload plus a newline, which JSON ignores; a payload echo
# would read as options, or any payload while xpg_echo makes echo expand
# escapes, is decoded by the original pipeline.
mycelium_session_start_cwd() {
  mycelium__payload_name_ok "$1" || return 1
  if [[ "${!1}" != -* ]] && ! shopt -q xpg_echo \
    && mycelium__payload_cached "cwd0:" "$1"; then
    if mycelium__payload_write; then
      [[ "$MYCELIUM__PC_STATUS" == 0 ]] && return 0
      echo ""
      return 0
    fi
  fi
  echo "${!1}" | python3 -c "$MYCELIUM__PY_START_CWD" 2>/dev/null || echo ""
}

# mycelium__json_literal MODE VALUE: set MYCELIUM__JSON to what
# `print(json.dumps(text))` prints, without its newline, when a separate
# interpreter reads VALUE from argv (MODE argv) or from stdin (MODE stdin).
# Fails, so the caller runs that interpreter, unless the result is provably
# the same: the preload saw UTF-8 for argv, stdin, and stdout, and VALUE holds
# only printable ASCII, the escapes json.dumps spells out, and a few UTF-8
# punctuation marks Mycelium's own messages use. Matching runs on bytes, and
# UTF-8 decoding never treats those byte sequences as anything else, so a
# byte the table does not cover leaves a non-ASCII byte and falls back.
mycelium__json_literal() {
  local mode="$1"
  local value="$2"
  local LC_ALL=C
  local dq='"'
  local restore_patsub=false
  local fs="" stdin="" stdout=""
  local chunk=0 limit=32768 at=0 size=0 length=0 piece="" out=""
  local continuation_re=$'^[\x80-\xbf]'

  MYCELIUM__JSON=""
  read -r fs stdin stdout <<< "$MYCELIUM__PC_ENC" || true
  [[ "$fs" == utf-8/* && "$stdout" == utf-8/* ]] || return 1
  if [[ "$mode" == stdin ]]; then
    # Text-mode stdin translates CR and CRLF to LF.
    [[ "$stdin" == utf-8/* && "$value" != *$'\r'* ]] || return 1
  else
    [[ "$fs" == utf-8/surrogateescape ]] || return 1
  fi
  # Pattern substitution costs time quadratic in its matches on bash 3.2
  # (16 KB of quotes took ten seconds). Bound the work: bash 3.2 encodes in
  # small pieces split between UTF-8 characters, and long values on any bash
  # go to the interpreter, so a hook holding its locks never stalls here.
  if (( BASH_VERSINFO[0] < 4 )); then
    chunk=512
    limit=8192
  fi
  length=${#value}
  (( length <= limit )) || return 1
  (( chunk > 0 )) || chunk=$length
  if shopt -q patsub_replacement 2>/dev/null; then
    restore_patsub=true
    shopt -u patsub_replacement
  fi
  while (( at < length )); do
    size=$chunk
    # Never split a UTF-8 sequence: move the cut back off continuation bytes.
    while (( size > 1 && at + size < length )) \
      && [[ "${value:$((at + size)):1}" =~ $continuation_re ]]; do
      size=$((size - 1))
    done
    piece="${value:$at:$size}"
    mycelium__json_escape_piece || break
    out="$out$piece"
    at=$((at + size))
  done
  [[ "$restore_patsub" == false ]] || shopt -s patsub_replacement
  (( at >= length )) || return 1
  MYCELIUM__JSON="$dq$out$dq"
}

# Escape `piece` (in mycelium__json_literal's C-locale scope, with
# patsub_replacement off) as json.dumps would; fail if a byte remains that
# the fast path does not cover.
mycelium__json_escape_piece() {
  local bs='\' dq='"' rep=""
  local ascii_re='^[ -~]*$'
  rep="$bs$bs"; piece="${piece//"$bs"/$rep}"
  rep="$bs$dq"; piece="${piece//"$dq"/$rep}"
  rep="${bs}n"; piece="${piece//$'\n'/$rep}"
  rep="${bs}r"; piece="${piece//$'\r'/$rep}"
  rep="${bs}t"; piece="${piece//$'\t'/$rep}"
  rep="${bs}b"; piece="${piece//$'\b'/$rep}"
  rep="${bs}f"; piece="${piece//$'\f'/$rep}"
  rep="${bs}u2014"; piece="${piece//$'\xe2\x80\x94'/$rep}"
  rep="${bs}u2013"; piece="${piece//$'\xe2\x80\x93'/$rep}"
  rep="${bs}u2192"; piece="${piece//$'\xe2\x86\x92'/$rep}"
  rep="${bs}u2026"; piece="${piece//$'\xe2\x80\xa6'/$rep}"
  rep="${bs}u2018"; piece="${piece//$'\xe2\x80\x98'/$rep}"
  rep="${bs}u2019"; piece="${piece//$'\xe2\x80\x99'/$rep}"
  rep="${bs}u201c"; piece="${piece//$'\xe2\x80\x9c'/$rep}"
  rep="${bs}u201d"; piece="${piece//$'\xe2\x80\x9d'/$rep}"
  [[ "$piece" =~ $ascii_re ]]
}

# On a Stop, every host gets `systemMessage` only (CONTEXT, then
# SYSTEM_MESSAGE, one line apart: Claude shows each line as its own "Stop
# says:" line): on Claude, `additionalContext` on a Stop makes the model
# take another turn, and nothing Stop reports without blocking may do that.
# Anything the agent must act on is a block (mycelium_emit_stop_block).
mycelium_emit_context() {
  local event="$1"
  local context="$2"
  local system_message="${3:-}"
  local host
  local payload=""
  host=$(mycelium_hook_host)

  # Same bytes as the interpreter below, built without one when exact.
  if [[ "$event" == "Stop" ]]; then
    payload="$context"
    if [[ -n "$system_message" ]]; then
      payload="${payload:+$payload$'\n'}$system_message"
    fi
    if mycelium__json_literal argv "$payload"; then
      mycelium__fast_write "{\"systemMessage\": $MYCELIUM__JSON}"$'\n' && return 0
    fi
  elif mycelium__json_literal argv "$event"; then
    payload="{\"hookSpecificOutput\": {\"hookEventName\": $MYCELIUM__JSON"
    if mycelium__json_literal argv "$context"; then
      payload="$payload, \"additionalContext\": $MYCELIUM__JSON}"
      if [[ -z "$system_message" ]]; then
        mycelium__fast_write "$payload}"$'\n' && return 0
      elif mycelium__json_literal argv "$system_message"; then
        mycelium__fast_write "$payload, \"systemMessage\": $MYCELIUM__JSON}"$'\n' && return 0
      fi
    fi
  fi

  python3 - "$host" "$event" "$context" "$system_message" <<'PY'
import json
import sys

host, event, context, system_message = sys.argv[1:]
if event == "Stop":
    payload = {"systemMessage": "\n".join(p for p in (context, system_message) if p)}
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
