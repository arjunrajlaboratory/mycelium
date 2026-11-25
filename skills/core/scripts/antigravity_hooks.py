#!/usr/bin/env python3
"""Translate Antigravity IDE and App hooks into base Mycelium lifecycle operations.

The model is deliberately not a routing input. Native USER_EXPLICIT transcript
steps identify work cycles; invocationNum and fullyIdle do not identify turns.
PreToolUse is observation only (``pre_tool_use``): it always answers
``{"decision": "allow"}``, which defers to the host's own approval policy, and
only opens the I/O observer's interval for the call. It never grants permissions.

The host profile comes from the package's hook command (``--host``; the IDE is
the default). A package label alone is not host proof: every event must also
name the exact native conversation directory and transcript under that host's
own data root before any state, lock, or receipt is touched. Each host keeps a
separate state, transaction, lock, and receipt namespace.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

import antigravity_app_store as app_store
from init_repo import ensure_safe_project_directory, ensure_safe_regular_file
from mycelium_locks import atomic_write_text, durable_lock

HOST = "antigravity-ide"  # default profile; existing IDE packages pass no --host
APP_HOST = "antigravity-app"
# Native data roots, relative to the user's home directory. The IDE root is the
# one recorded by every retained IDE run; the App root was observed in App 2.31.0.
PROFILES = {
    HOST: {"label": "Antigravity IDE", "native_root": (".gemini", "antigravity-ide", "brain"),
           "transcripts": frozenset({"transcript_full.jsonl", "transcript.jsonl"}),
           "lock": "antigravity", "legacy_state": True},
    APP_HOST: {"label": "Antigravity App", "native_root": (".gemini", "antigravity", "brain"),
               "transcripts": frozenset({"transcript_full.jsonl"}),
               "lock": "antigravity-app", "legacy_state": False},
}
_ACTIVE_HOST = contextvars.ContextVar("antigravity_host", default=HOST)
APP_UNRESOLVED = ("Antigravity App command step without authoritative workspace, working-directory, "
                  "and exit evidence; output provenance is not recorded")
UNRESOLVED_HOOK = "A Mycelium hook has an unresolved outcome. Inspect retained native-state.json before retrying."


def one_line(text, limit=2000):
    """TEXT as one printable, length-capped line for a model-facing message.

    Causes carry hook stderr and host error text, which can quote
    repository-controlled names: escape control characters (a newline could
    forge a new instruction line) and anything else unprintable.
    """
    text = "".join(c if c.isprintable() else c.encode("unicode_escape", "backslashreplace").decode("ascii")
                   for c in str(text))
    return text if len(text) <= limit else "..." + text[-(limit - 3):]


def mark_uncertain(turn, cause):
    """Hold this work cycle unresolved and record why.

    The first cause is kept: it is the one that made the outcome unknown,
    and later events cannot settle it. Ordinary native errors (a command
    that exited nonzero) never go here, so a blocked Stop cannot blame them.
    """
    if turn.get("uncertain") and "uncertain_cause" not in turn:
        # Marked unresolved by a release that kept no cause: never pin a
        # later, unrelated event on it.
        cause = turn.get("last_hook_error") or ("not recorded (this work cycle was marked "
                                                "unresolved by an earlier Mycelium version)")
    elif turn.get("uncertain") is not True:
        turn.pop("uncertain_cause", None)  # a cause never outlives its resolution
    turn["uncertain"] = True
    cause = str(cause)
    if len(cause) > 2000:  # keep the head that names the step or action, and the tail
        cause = cause[:1000] + " ... " + cause[-995:]
    turn.setdefault("uncertain_cause", cause)


def unresolved_reason(turn):
    """UNRESOLVED_HOOK plus the recorded cause, so a blocked Stop says what failed."""
    # State written before uncertain_cause existed can still name a hook error.
    detail = turn.get("uncertain_cause") or turn.get("last_hook_error")
    # The first cause is kept, so also name every action still unfinished:
    # one killed later must not hide behind an earlier, unrelated failure.
    running = sorted(action for action, value in turn.get("actions", {}).items() if value == "running")
    return (UNRESOLVED_HOOK + (f" Cause: {one_line(detail)}" if detail else "") +
            (" Unfinished hook actions: " + ", ".join(running) + "." if running else ""))


def save_then_raise(save, error):
    """Persist a recorded failure, then raise it; never let a failed save hide it."""
    try:
        save()
    except Exception as save_error:
        raise error from save_error
    raise error


# Leave time within the App package's 45-second Stop hook for Python startup,
# receipt reconciliation and the final state write. Share the budget across roots.
APP_STOP_BUDGET_SECONDS = 35
# Time kept for shared finalization after a Stop-time native edit scan.
APP_FINALIZE_RESERVE_SECONDS = 20
APP_RECOVERY_ACTION_SECONDS = 12  # the shared per-hook default
# A recovery batch starts only with at least this much real tracker allowance.
APP_RECOVERY_BATCH_MIN_SECONDS = 5
APP_RECOVERY_OBSERVE_MIN_SECONDS = 1  # one edit observation (a few hashes, no walk)
ACTIVITY_HOOK = "mycelium-activity-tracker.sh"
# Set only by this adapter, only for its Stop-recovery batch. The shared tracker
# chooses batch mode from this flag plus the App host, never from payload text.
ACTIVITY_BATCH_ENV = "MYCELIUM_ANTIGRAVITY_ACTIVITY_BATCH"
HOOK_PYTHON = "bin/python3"  # generated by tools/antigravity.py
APP_EDIT_SHAPE_WARNING = (
    "Mycelium does not recognise the Antigravity App's record for edit step {step} ({reason}), "
    "so that edit was not tracked. Edit tracking assumes App 2.31.0's FileEdit record; tell the user "
    "to run `tools/antigravity.py doctor --profile app --destination <package> --workspace <repo>` "
    "and report the result.")
STATE_SCHEMA = 2
PLUGIN = Path(__file__).resolve().parents[3]
MAX_STOP_RETRIES = 3
NO_CONTINUATION_ENV = "MYCELIUM_STOP_NO_CONTINUATION"
# A command still pending when the Stop allowance ends can no longer be settled.
FINAL_PENDING = ("Native command step had no terminal evidence when the Stop allowance ended; "
                 "output provenance is not recorded")
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 8 * 1024 * 1024
EVENTS = {"PreInvocation", "PostToolUse", "PostInvocation", "Stop"}
EDITS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
# App edit tools with captured typed native evidence (successful built-in edits).
APP_EDITS = set(app_store.EDIT_TOOLS)
PROTECTED = {".agents", "_agents", ".agent", ".mycelium", ".living"}


def tracker_ignores(relative):
    """The shared activity tracker's own exclusions, applied to a repository-relative path."""
    for name in (".living", ".mycelium", ".claude", ".codex"):
        if relative.startswith(name + "/") or f"/{name}/" in relative:
            return True
    return ("/node_modules/" in relative or "/__pycache__/" in relative or
            relative.endswith(".pyc") or relative.endswith(".lock"))


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def safe_path(path: Path, *, regular=False):
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Antigravity paths must be absolute and contain no traversal")
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise ValueError(f"Refusing linked native evidence: {parent}")
    if regular and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Native transcript must be a regular file")


def profile(host):
    if host not in PROFILES:
        raise ValueError("Unsupported Antigravity host profile")
    return PROFILES[host]


def active_host(host=None):
    """The explicit host, else the profile of the dispatch in progress (IDE by default)."""
    host = host or _ACTIVE_HOST.get()
    profile(host)
    return host


def native_transcript(payload, cid, host):
    """Return the exact native transcript of this host's conversation, or refuse.

    Paths are compared as given, never resolved: an alias, a link, another
    host's root, or a matching suffix under any other directory is refused.
    """
    spec = profile(host)
    artifact_raw, transcript_raw = payload.get("artifactDirectoryPath"), payload.get("transcriptPath")
    if not isinstance(artifact_raw, str) or not isinstance(transcript_raw, str):
        raise ValueError("Native conversation paths are missing")
    artifact = Path.home().joinpath(*spec["native_root"], cid)
    if not artifact.is_absolute() or artifact_raw != str(artifact):
        raise ValueError(f"Conversation is outside the exact {spec['label']} native root")
    allowed = {str(artifact / ".system_generated/logs" / name) for name in spec["transcripts"]}
    if transcript_raw not in allowed:
        raise ValueError("Transcript is outside the exact native conversation")
    path = Path(transcript_raw)
    safe_path(path, regular=True)
    return path


def identity(payload):
    cid = payload.get("conversationId")
    if not isinstance(cid, str) or str(uuid.UUID(cid)) != cid:
        raise ValueError("Antigravity conversationId must be a canonical UUID")
    return cid


def roots_for(payload):
    paths = payload.get("workspacePaths")
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError("workspacePaths must be an array of absolute paths")
    roots = set()
    for raw in paths:
        path = Path(raw)
        safe_path(path)
        if not path.is_dir():
            continue
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            continue
        # Git ends the path with one newline; a root name may itself end in whitespace.
        root = Path(result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout)
        safe_path(root)
        living = root / ".living"
        if not living.exists() and not living.is_symlink():
            continue
        if living.is_symlink() or not living.is_dir():
            raise ValueError("Unsafe Mycelium opt-in directory")
        roots.add(root)
    return sorted(roots)


def transcript_records(path):
    safe_path(path, regular=True)
    with path.open("rb") as stream:
        length = stream.seek(0, 2)
        offset = max(0, length - MAX_TRANSCRIPT_BYTES)
        stream.seek(offset)
        if offset:
            stream.readline()  # Discard an incomplete leading record.
        content = stream.read(MAX_TRANSCRIPT_BYTES)
    records = []
    for line in content.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            raise ValueError("Native transcript contains an incomplete record")
        if not isinstance(record, dict) or not integer(record.get("step_index")):
            raise ValueError("Invalid native transcript step")
        # Never retain or reinterpret model reasoning as execution evidence.
        retained = {key: record[key] for key in
                    ("step_index", "source", "type", "status", "exit_code") if key in record}
        if record.get("source") == "SYSTEM_SDK" and record.get("type") == "EPHEMERAL_MESSAGE":
            retained["delivered_reminders"] = re.findall(
                r"\[Mycelium reminder ([A-Za-z0-9-]+)\]", str(record.get("content", "")))
        records.append(retained)
    return records


def hook_environment(host, plugin, *, batch=False):
    """Environment for a shared base hook run by this adapter."""
    # Strict capture makes a failed lineage extractor a nonzero hook outcome.
    env = dict(os.environ, MYCELIUM_HOOK_HOST=host, MYCELIUM_PLUGIN_ROOT=str(plugin),
               MYCELIUM_STRICT_CAPTURE="1")
    env.pop(ACTIVITY_BATCH_ENV, None)
    if batch:
        env[ACTIVITY_BATCH_ENV] = "1"
    # The shared hooks call plain `python3`; the installed package's shim runs
    # the interpreter selected at install time. A source checkout has no shim.
    shim = Path(plugin) / HOOK_PYTHON
    if shim.is_file():
        env["PATH"] = str(shim.parent) + os.pathsep + env.get("PATH", "")
    return env


def run_hook(name, data, root, plugin, *, timeout=12, batch=False, last_stop=False):
    host = active_host()
    env = hook_environment(host, plugin, batch=batch)
    # On the last allowed Stop a block cannot reach the agent, so the shared
    # Stop hook skips blocks that only explain (it publishes the fallback).
    env.pop(NO_CONTINUATION_ENV, None)
    if last_stop:
        env[NO_CONTINUATION_ENV] = "1"
    override = env.get("MYCELIUM_STATE_DIR")
    if override and Path(root, override).resolve() != (Path(root) / ".mycelium").resolve():
        # The adapter reads markers and receipts only under <root>/.mycelium.
        # Base hooks would keep state elsewhere, and dropping the override
        # instead would split the lifecycle lock from other hosts' sessions.
        raise RuntimeError("MYCELIUM_STATE_DIR is set, but the Antigravity adapter keeps lifecycle "
                           "state only in .mycelium/; unset it in the App or IDE environment")
    script = plugin / "skills/core/hooks" / name
    marker = root / ".mycelium/run" / host / data["session_id"] / "active-session-log.tmp"
    had_marker = marker.is_file()
    result = subprocess.run(["bash", str(script)], input=json.dumps(data), text=True,
                            capture_output=True, cwd=root, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}): {result.stderr[-1000:]}")
    decoded = json.loads(result.stdout) if result.stdout.strip() else {}
    if name == "mycelium-health.sh" and not marker.is_file():
        # The base hook exits 0 when it refuses to start (for example on a
        # symlink in the state tree); its stderr names the cause.
        detail = result.stderr.strip()[-1000:]
        raise RuntimeError("Base Mycelium did not establish an active lifecycle transaction" +
                           (f": {detail}" if detail else ""))
    if (name == "mycelium-stop-check.sh" and decoded.get("decision") != "block" and
            (not had_marker or not accepted_stop(root, data["session_id"], host))):
        raise RuntimeError("Base Mycelium did not confirm transaction finalization")
    return decoded


def stop_receipt_path(root, session_id, host=None):
    host = active_host(host)
    cid, separator, step = session_id.partition("-turn-")
    if not separator or not step.isdigit() or str(uuid.UUID(cid)) != cid:
        raise ValueError("Invalid native receipt identity")
    directory = ensure_safe_project_directory(root, f".mycelium/run/{host}/{session_id}", create=False)
    receipt = directory / "accepted-stop.json"
    ensure_safe_regular_file(receipt)
    return receipt


def record_accepted_stop(root, session_id, host=None):
    host = active_host(host)
    receipt = stop_receipt_path(root, session_id, host)
    atomic_write_text(receipt, json.dumps({"schema": 1, "host": host,
                      "session_id": session_id, "status": "accepted"}) + "\n",
                      root=root, default_mode=0o600)


def accepted_stop(root, session_id, host=None):
    host = active_host(host)
    receipt = stop_receipt_path(root, session_id, host)
    if not receipt.exists():
        return False
    if receipt.stat().st_size > 4096:
        raise ValueError("Invalid native finalization receipt")
    value = json.loads(receipt.read_text())
    if value != {"schema": 1, "host": host, "session_id": session_id, "status": "accepted"}:
        raise ValueError("Native finalization receipt identity mismatch")
    marker = receipt.parent / "active-session-log.tmp"
    ensure_safe_regular_file(marker)
    return not marker.exists()


def append_session_log(root, session_id, entry, host=None):
    """Append one entry to the active session log; False when no active log is available."""
    host = active_host(host)
    marker = root / ".mycelium/run" / host / session_id / "active-session-log.tmp"
    if not marker.is_file() or marker.is_symlink():
        return False
    lines = marker.read_text(encoding="utf-8").splitlines()
    log = Path(lines[0]) if lines else None
    try:  # every component from the project root down, without following a symlink
        log_dir = ensure_safe_project_directory(root, ".living/log", create=False)
    except ValueError:
        return False
    if (log is None or not log.is_absolute() or log.suffix != ".md" or not log_dir.is_dir() or
            os.path.realpath(log.parent) != str(log_dir)):
        return False
    target = log_dir / log.name  # written only through the validated directory
    ensure_safe_regular_file(target)
    if not target.is_file():
        return False
    descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(entry)
    return True


def append_unverified_provenance(root, session_id, label, steps, host=None):
    """Record unverified command provenance in the active session log (native state also keeps it)."""
    return append_session_log(root, session_id, (
        f"\n### {time.strftime('%H:%M')} — Unverified command provenance ({label})\n"
        f"- Steps: {', '.join(sorted(steps, key=int))}\n"
        "- Mycelium could not verify where, how, or with what exit status these native command steps "
        "ran, or they were still running when the Stop allowance ended. No lineage was recorded for "
        "them; treat their outputs' provenance as unverified.\n"), host)


def append_undelivered_reminders(root, session_id, label, tokens, host=None):
    """Record post-action guidance that never reached the agent before the Stop allowance ended."""
    return append_session_log(root, session_id, (
        f"\n### {time.strftime('%H:%M')} — Undelivered post-action guidance ({label})\n"
        f"- Reminders: {', '.join(sorted(tokens))}\n"
        "- Mycelium queued post-action guidance for these native steps, but the Stop allowance ended "
        "before it reached the agent. Their session-log and living-layer follow-up may be incomplete.\n"), host)


def context(result):
    return result.get("hookSpecificOutput", {}).get("additionalContext", "") or result.get("systemMessage", "")


def preflight(root, cid):
    host = active_host()
    relative = f".mycelium/{host}/{cid}"
    directory = ensure_safe_project_directory(root, relative, create=False)
    ensure_safe_project_directory(root, f".mycelium/run/{host}", create=False)
    ensure_safe_project_directory(root, ".mycelium/locks", create=False)
    ensure_safe_regular_file(root / ".mycelium/plugin-root")
    ensure_safe_regular_file(directory / "native-state.json")
    return directory


def read_state(path, cid):
    if not path.exists():
        return {"schema": STATE_SCHEMA, "conversationId": cid, "turns": {}}
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("Native state exceeded its safety bound; retained for inspection")
    state = json.loads(path.read_text())
    # A host without earlier releases has no legacy schema to adopt.
    schemas = {1, STATE_SCHEMA} if profile(active_host())["legacy_state"] else {STATE_SCHEMA}
    if (not isinstance(state, dict) or state.get("schema") not in schemas or
            isinstance(state.get("schema"), bool) or
            state.get("conversationId") != cid or not isinstance(state.get("turns"), dict)):
        raise ValueError("Invalid native state; refusing to replace it")
    for key, turn in state["turns"].items():
        if (not key.isdigit() or not isinstance(turn, dict) or
                turn.get("status") not in {"active", "accepted", "incomplete"} or
                not isinstance(turn.get("actions"), dict) or
                not integer(turn.get("retries")) or not isinstance(turn.get("context"), list) or
                any(not isinstance(c, str) for c in turn["context"]) or
                any(value not in ("running", "done") for value in turn["actions"].values()) or
                not isinstance(turn.get("reminder", ""), str) or
                not isinstance(turn.get("reminder_id", ""), str) or
                not isinstance(turn.get("last_hook_error", ""), str) or
                not isinstance(turn.get("uncertain_cause", ""), str) or
                not isinstance(turn.get("uncertain"), bool)):
            raise ValueError("Invalid native work-cycle state")
        pending = turn.get("pending_commands", {})
        reminders = turn.setdefault("reminders", {})
        token_pattern = re.compile(re.escape(f"{cid}-turn-{key}-step-") + r"[0-9]+")
        if (not isinstance(reminders, dict) or any(
                not isinstance(token, str) or not token_pattern.fullmatch(token) or
                not isinstance(message, str) or not message
                for token, message in reminders.items())):
            raise ValueError("Invalid pending native reminders")
        # Additive schema-2 migration: preserve the one undelivered message
        # retained by earlier previews. Already overwritten messages cannot be
        # recovered or retroactively certified.
        old_message, old_token = turn.pop("reminder", ""), turn.pop("reminder_id", "")
        if bool(old_message) != bool(old_token) or (old_token and not token_pattern.fullmatch(old_token)):
            raise ValueError("Invalid legacy native reminder")
        if old_message:
            if old_token in reminders and reminders[old_token] != old_message:
                raise ValueError("Conflicting legacy native reminder")
            reminders[old_token] = old_message
        attempt = turn.get("stop_attempt")
        unresolved = turn.get("unresolved_commands", {})
        if (not isinstance(unresolved, dict) or any(
                not step.isdigit() or not isinstance(reason, str) or not reason or len(reason) > 2000
                for step, reason in unresolved.items())):
            raise ValueError("Invalid unresolved native command record")
        if (not isinstance(pending, dict) or any(
                not step.isdigit() or not isinstance(command, dict) or
                not isinstance(command.get("cwd"), str) or
                not isinstance(command.get("command"), str) or len(command["command"]) > 65536
                for step, command in pending.items())):
            raise ValueError("Invalid pending native command")
        if active_host() == APP_HOST:
            app_pending = turn.setdefault("app_pending", {})
            bindings = turn.setdefault("app_bindings", {})
            if (not isinstance(app_pending, dict) or not isinstance(bindings, dict) or any(
                    not step.isdigit() or not isinstance(binding, dict) or set(binding) != {"command", "call_id"} or
                    not isinstance(binding["command"], str) or len(binding["command"]) > 65536 or
                    not isinstance(binding["call_id"], str) or not 0 < len(binding["call_id"]) <= 256
                    for step, binding in app_pending.items()) or any(
                    not step.isdigit() or not isinstance(call, str) or not 0 < len(call) <= 256
                    for step, call in bindings.items())):
                raise ValueError("Invalid pending App command binding")
            recovery = turn.get("edit_recovery")
            if recovery is not None and (
                    not isinstance(recovery, dict) or
                    recovery.get("status") not in {"complete", "truncated", "unavailable", "deferred"} or
                    not isinstance(recovery.get("credited"), list) or
                    any(not integer(step) for step in recovery["credited"]) or
                    not isinstance(recovery.get("rejected"), dict) or len(recovery["rejected"]) > 64 or
                    not isinstance(recovery.get("reason"), str)):
                raise ValueError("Invalid native edit recovery record")
            turn.setdefault("unresolved_commands", {})
        if attempt is not None and (not isinstance(attempt, dict) or
                attempt.get("status") not in {"running", "blocked", "accepted"} or
                not integer(attempt.get("number"))):
            raise ValueError("Invalid native Stop attempt")
        root = path.parents[3]
        for command in pending.values():
            cwd = Path(command["cwd"])
            safe_path(cwd)
            if cwd != root and root not in cwd.parents:
                raise ValueError("Pending native command escapes its repository")
        stop_receipt_path(root, f"{cid}-turn-{key}")
        if state["schema"] == 1 and turn["status"] == "active":
            # Old previews could have permanently snapshotted RUNNING outputs.
            # Never certify that active history using the new completion rules.
            turn["reason"] = "Legacy active preview cycle requires inspection; start a fresh IDE conversation"
            turn["uncertain"] = True  # this migration is the known cause
            turn.setdefault("uncertain_cause", turn["reason"])
        turn.setdefault("pending_commands", {})
    state["schema"] = STATE_SCHEMA
    return state


def contained_root(raw, roots):
    """The most specific opted-in root containing an exact absolute path, else None."""
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    try:
        safe_path(path)
    except (ValueError, OSError):
        return None
    for root in sorted(roots, key=lambda r: len(r.parts), reverse=True):
        if path == root or root in path.parents:
            return root
    return None


def app_tool(payload, roots):
    """Classify one App PostToolUse: (kind, target roots, read path).

    Only the observed shapes are supported (``read_file``/``Path`` is natively
    confirmed); any other or malformed shape is not tracked and no path is
    guessed. A supported ``run_command`` is settled later from the App's own
    step store (see antigravity_app_store); a command with any other shape, a
    sandbox bypass request, a native error, or a tool call that cannot be
    identified is unresolved in every opted-in root: neither the plugin cwd nor
    a mounted workspace proves where it ran. Edits are tracked only from typed
    native evidence.
    """
    call = payload.get("toolCall")
    name = call.get("name") if isinstance(call, dict) else None
    args = call.get("args") if isinstance(call, dict) else None
    if name == "read_file":
        raw = args.get("Path") if isinstance(args, dict) else None
        root = contained_root(raw, roots) if not payload.get("error") else None
        return ("read", [root], raw) if root else ("ignored", [], None)
    if name == "run_command" and isinstance(args, dict) and not payload.get("error"):
        if app_store.command_args_supported(args):
            # Where and how it ran is settled only by the App's own typed step record.
            return "command", list(roots), args["CommandLine"]
    if name == "run_command" or not isinstance(name, str):
        return "unresolved", list(roots), None
    if name in APP_EDITS and isinstance(args, dict) and isinstance(args.get("Path"), str) and not payload.get("error"):
        return "edit", list(roots), args["Path"]
    return "ignored", [], None


def target_root(payload, roots):
    call = payload.get("toolCall")
    if not isinstance(call, dict) or not isinstance(call.get("args"), dict):
        raise ValueError("This IDE profile requires PostToolUse.toolCall; no execution was inferred")
    args = call["args"]
    raw = args.get("Cwd") if call.get("name") == "run_command" else args.get("TargetFile", args.get("AbsolutePath"))
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    safe_path(path)
    for root in sorted(roots, key=lambda r: len(r.parts), reverse=True):
        if path == root or root in path.parents:
            return root
    return None


def observer_turn_end(root, session_id):
    """Close this work cycle's observer intervals that got no Post; best effort, never raises."""
    try:
        import io_observer
        state_dir = observer_state_dir(root)
        io_dir = state_dir / "io"
        if io_dir.is_dir() and not io_dir.is_symlink():
            io_observer.turn_end(state_dir, host=active_host(), session=session_id)
    except Exception as exc:
        print(f"Mycelium Antigravity: observer turn end: {one_line(exc, 300)}", file=sys.stderr)


def dispatch(event, payload, plugin_root=PLUGIN, host=HOST):
    profile(host)
    token = _ACTIVE_HOST.set(host)
    try:
        return _dispatch(event, payload, plugin_root, host)
    finally:
        _ACTIVE_HOST.reset(token)


def _dispatch(event, payload, plugin_root, host):
    if event not in EVENTS or not isinstance(payload, dict):
        raise ValueError("Unknown Antigravity event or malformed payload")
    app_stop_deadline = (time.monotonic() + APP_STOP_BUDGET_SECONDS
                         if host == APP_HOST and event == "Stop" else None)
    cid = identity(payload)
    # Host proof precedes every workspace probe, state read, lock, or write.
    transcript = native_transcript(payload, cid, host)
    app = host == APP_HOST
    roots = roots_for(payload)
    if not roots:
        return {}
    # Preflight every mounted opted-in root before even creating a lock.
    directories = {root: preflight(root, cid) for root in roots}
    for root in roots:
        read_state(directories[root] / "native-state.json", cid)
    records = transcript_records(transcript)
    observed_turns = [r["step_index"] for r in records
                      if r.get("source") == "USER_EXPLICIT" and r.get("type") == "USER_INPUT"]
    boundary = payload.get("stepIdx") if event == "PostToolUse" else payload.get("initialNumSteps")
    if event == "Stop":
        boundary = max((r["step_index"] for r in records), default=-1)
    if not integer(boundary):
        raise ValueError("Missing native event step boundary")
    app_kind, app_read, evidence = None, None, {}

    def app_command(step, command):
        """Cached (kind, value) for one App command step; kind is pending, verified or unresolved."""
        if (step, command) not in evidence:
            try:
                found = app_store.command(cid, step, command)
                root = contained_root(found["cwd"], roots)
                outcome = ("verified", dict(found, root=root)) if root and Path(found["cwd"]).is_dir() else \
                    ("unresolved", None)
            except app_store.Pending as running:
                outcome = ("pending", running.call_id)
            except app_store.Unverified:
                outcome = ("unresolved", None)
            evidence[(step, command)] = outcome
        return evidence[(step, command)]

    def app_edit(step, name, path):
        if ("edit", step) not in evidence:
            try:
                evidence[("edit", step)] = app_store.edit(cid, step, name, path)
            except app_store.UnrecognizedShape as error:
                evidence[("edit", step)] = None
                evidence[("edit-shape", step)] = str(error)[:200]
            except (app_store.Pending, app_store.Unverified):
                evidence[("edit", step)] = None
        return evidence[("edit", step)]

    def app_recovery(after, through):
        """One bounded Stop-time scan of committed native edits per exact range in this dispatch."""
        key = ("recovery", after, through)
        if key not in evidence:
            remaining = app_stop_deadline - time.monotonic()
            seconds = min(app_store.SCAN_SECONDS, remaining - APP_FINALIZE_RESERVE_SECONDS)
            if seconds <= 0:
                evidence[key] = {"status": "deferred", "edits": [], "rejected": {},
                                        "reason": "No time remained to scan native edits before finalization"}
            else:
                try:
                    evidence[key] = app_store.recover_edits(cid, after, through, seconds=seconds)
                except app_store.Unverified as error:
                    evidence[key] = {"status": "unavailable", "edits": [], "rejected": {},
                                            "reason": str(error)[:200]}
        return evidence[key]

    if event == "PostToolUse" and app:
        app_kind, targets, app_read = app_tool(payload, roots)
    elif event == "PostToolUse":
        targets = [target_root(payload, roots)]
    # A later root's receipt must not fail preflight after an earlier root starts.
    for root in roots:
        prior = read_state(directories[root] / "native-state.json", cid)
        candidates = [n for n in observed_turns + [int(k) for k in prior["turns"]] if n <= boundary]
        if candidates:
            stop_receipt_path(root, f"{cid}-turn-{max(candidates)}")
    injected, reasons = [], []
    for root in roots:
        if event == "PostToolUse" and root not in targets:
            continue
        state_path = directories[root] / "native-state.json"
        # Validate persisted bytes before the first mutation, then revalidate
        # under the durable conversation lock before deriving new bytes. The
        # wait uses the shared bounded lock policy: a duplicate event must
        # outlast legitimate held hook work, and a real timeout still fails.
        read_state(state_path, cid)
        with durable_lock(root, f"{profile(host)['lock']}-{cid}.lock"):
            preflight(root, cid)
            state = read_state(state_path, cid)
            candidates = [n for n in observed_turns + [int(k) for k in state["turns"]] if n <= boundary]
            if not candidates:
                raise ValueError("No explicit native user-turn identity; no lifecycle was started")
            user_step = max(candidates)
            key = str(user_step)
            turn = state["turns"].get(key)
            if turn is None and event != "PreInvocation":
                continue  # Late delivery cannot create a transaction.
            session_id = f"{cid}-turn-{user_step}"
            data = {"cwd": str(root), "session_id": session_id, "source": "startup"}
            ensure_safe_project_directory(root, f".mycelium/run/{host}/{session_id}", create=False)
            stop_receipt_path(root, session_id)
            directory = ensure_safe_project_directory(root, directories[root].relative_to(root), create=True)

            def save():
                text = json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n"
                if len(text.encode()) > MAX_STATE_BYTES:
                    raise ValueError("Native state safety bound reached")
                atomic_write_text(directory / "native-state.json", text, root=root, default_mode=0o600)

            if turn is None:
                turn = {"status": "active", "actions": {}, "retries": 0, "context": [],
                        "uncertain": False, "pending_commands": {}, "reminders": {}}
                if app:
                    turn.update(unresolved_commands={}, app_pending={}, app_bindings={})
                state["turns"][key] = turn
                turn["actions"]["startup"] = "running"
                save()
                try:
                    result = run_hook("mycelium-health.sh", data, root, plugin_root)
                except Exception as exc:
                    # As in apply_hooks: startup stays running and is never
                    # redispatched, but the retained state says why.
                    turn["last_hook_error"] = f"startup: {exc}"[-2000:]
                    mark_uncertain(turn, turn["last_hook_error"])
                    save_then_raise(save, exc)
                turn["actions"]["startup"] = "done"
                if context(result):
                    turn["context"].append(context(result))
                save()
            if turn["status"] != "active":
                continue
            if turn.get("stop_attempt", {}).get("status") == "running":
                if accepted_stop(root, session_id):
                    turn["stop_attempt"]["status"] = "accepted"
                    turn["status"] = "accepted"
                    if app:
                        turn["uncertain"] = False
                        turn.pop("uncertain_cause", None)
                    turn["context"] = []
                    save()
                    continue
                mark_uncertain(turn, "Stop finalization: an earlier Stop check ended before "
                                     "recording whether it finalized this work cycle")
                save()
            running = sorted(action for action, value in turn["actions"].items() if value == "running")
            if running:
                # The process that ran these ended before recording an outcome.
                # Not a hook failure, so last_hook_error stays for real ones.
                mark_uncertain(turn, turn.get("last_hook_error") or (
                    ", ".join(running) + ": hook outcome was not recorded"))
                save()

            capture_deferred = []

            def capture_budget():
                """A capture hook's timeout inside a shared App Stop budget, or None to wait."""
                if app_stop_deadline is None:
                    return {}
                remaining = app_stop_deadline - time.monotonic() - APP_FINALIZE_RESERVE_SECONDS
                if remaining < APP_RECOVERY_BATCH_MIN_SECONDS:
                    return None
                return {"timeout": min(APP_RECOVERY_ACTION_SECONDS, remaining)}

            def apply_hooks(hooks, command_data, step_index):
                for hook in hooks:
                    action = f"{step_index}:{hook}"
                    if action in turn["actions"]:
                        continue
                    budget = capture_budget()
                    if budget is None:  # not journaled: a later Stop dispatches it once
                        capture_deferred.append(action)
                        return
                    turn["actions"][action] = "running"
                    save()
                    try:
                        result = run_hook(hook, command_data, root, plugin_root, **budget)
                    except Exception as exc:
                        # The action stays running: never redispatched, never
                        # accepted. Stop reports it until the bounded allowance ends.
                        turn["last_hook_error"] = f"{action}: {exc}"[-2000:]
                        mark_uncertain(turn, turn["last_hook_error"])
                        save_then_raise(save, exc)
                    turn["actions"][action] = "done"
                    reminder = context(result)
                    if reminder:
                        token = f"{cid}-turn-{user_step}-step-{step_index}"
                        if token in turn["reminders"]:
                            turn["reminders"][token] += "\n" + reminder
                        else:
                            turn["reminders"][token] = (
                                f"[Mycelium reminder {token}] Post-action guidance from native tool step {step_index}, "
                                "delivered at PreInvocation:\n" + reminder)
                    save()

            def settle_app_command(step_key, command, expected_call=None):
                """Bind one App command step to its exact native call, once, or leave it unresolved."""
                unresolved, pending, bindings = (turn["unresolved_commands"], turn["app_pending"],
                                                 turn["app_bindings"])
                kind, value = app_command(int(step_key), command)
                if step_key in unresolved:  # historical unresolved state is never upgraded
                    pending.pop(step_key, None)
                    save()
                    return
                call = value if kind == "pending" else value["call_id"] if kind == "verified" else None
                bound = bindings.get(step_key, expected_call)
                reused = any(c == call and s != step_key for s, c in bindings.items())
                stale = kind == "verified" and value["source_step"] < user_step
                if kind == "unresolved" or (bound and call != bound) or reused or stale:
                    pending.pop(step_key, None)
                    unresolved[step_key] = APP_UNRESOLVED
                    save()
                elif kind == "pending":
                    pending[step_key] = {"command": command, "call_id": call}
                    save()
                else:
                    # Bind first and keep the pending entry until every capture action
                    # is journaled, so an interruption here is retried, never dropped.
                    bindings[step_key] = call
                    save()
                    hooks = ["mycelium-data-tracker.sh", "mycelium-post-action.sh"]
                    if value["root"] == root:
                        apply_hooks(hooks, dict(data, cwd=value["cwd"], tool_name="Bash",
                                                tool_use_id=observer_call_id(int(step_key)),
                                                tool_input={"command": command},
                                                tool_response={"exit_code": value["exit_code"]}), int(step_key))
                    if value["root"] != root or all(f"{step_key}:{h}" in turn["actions"] for h in hooks):
                        pending.pop(step_key, None)
                        save()

            def observe_edit(step_index, path, timeout=12):
                """Close a successful edit's observer interval (opened by PreToolUse) with its
                edit observation. At most once per step, and never a lifecycle failure: an
                observation that cannot be made leaves the interval open until turn end."""
                action = f"{step_index}:io-observer"
                if action in turn["actions"]:
                    return
                turn["actions"][action] = "done"
                save()
                try:
                    run_hook("mycelium-io-observer.sh", dict(
                        data, hook_event_name="PostToolUse", tool_name="Write",
                        tool_use_id=observer_call_id(step_index), tool_input={"file_path": path},
                        # Callers pass only edits whose success the native record already
                        # proved (a DONE step without error, or a typed App FileEdit).
                        tool_response={"success": True, "exit_code": 0}), root, plugin_root,
                        timeout=timeout)
                except Exception as exc:
                    print(f"Mycelium Antigravity: edit observation for step {step_index}: "
                          f"{one_line(exc, 300)}", file=sys.stderr)

            def edit_target(found, step_index):
                """Whether one verified native edit earns activity credit in this root."""
                if found["source_step"] != step_index or step_index <= user_step:
                    return False
                if contained_root(found["path"], roots) != root:
                    return False
                relative = Path(found["path"]).relative_to(root)
                return bool(relative.parts) and relative.parts[0] not in PROTECTED

            def credit_app_edit(found, step_index):
                """Direct-callback activity credit; one durable action key per step."""
                if edit_target(found, step_index):
                    apply_hooks([ACTIVITY_HOOK],
                                dict(data, tool_name="Write", tool_input={"file_path": found["path"]},
                                     tool_response={"success": True}), step_index)
                    observe_edit(step_index, found["path"])

            def recover_app_edits(scan):
                """One shared-tracker batch for this root's verified, not yet recorded native edits.

                Returns (status, reason, newly credited steps, extra rejections). All new
                keys become running in one write before the tracker starts and done in one
                write only after its exact acknowledgement; otherwise they stay running and
                are never redispatched.
                """
                selected, rejected = [], {}
                for found in scan["edits"]:
                    step = found["step"]
                    if not edit_target(found, step) or f"{step}:{ACTIVITY_HOOK}" in turn["actions"]:
                        continue
                    path = found["path"]
                    if tracker_ignores(Path(path).relative_to(root).as_posix()):
                        continue  # verified, but the shared tracker records no activity for it
                    if any(c in path for c in "\0\r\n") or len(path) > 4096 or os.path.realpath(path) != path:
                        rejected[str(step)] = "native edit path is not representable for activity tracking"
                        continue
                    try:
                        path.encode("utf-8")
                    except UnicodeEncodeError:
                        rejected[str(step)] = "native edit path is not valid UTF-8"
                        continue
                    selected.append((step, path))
                if not selected:
                    return scan["status"], scan.get("reason", ""), [], rejected
                remaining = app_stop_deadline - time.monotonic() - APP_FINALIZE_RESERVE_SECONDS
                if remaining < APP_RECOVERY_BATCH_MIN_SECONDS:
                    return "deferred", "No time remained to record recovered native edit activity", [], rejected
                unique = list(dict.fromkeys(path for _, path in selected))
                paths, size = [], 0
                for path in unique[:256]:  # the tracker's batch bounds; the rest waits, reported truncated
                    size += len(path.encode("utf-8")) + 1
                    if size > 256 * 1024:
                        break
                    paths.append(path)
                capped = len(unique) > len(paths)
                steps = [step for step, path in selected if path in paths]
                digest = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
                batch = {"id": f"{session_id}-recovery-{min(steps)}-{max(steps)}-{digest[:16]}",
                         "count": len(paths), "sha256": digest}
                for step in steps:
                    turn["actions"][f"{step}:{ACTIVITY_HOOK}"] = "running"
                save()
                try:
                    result = run_hook(ACTIVITY_HOOK, dict(
                        data, tool_name="Write", tool_input={"file_paths": paths}, tool_response={"success": True},
                        mycelium_activity_batch=batch), root, plugin_root,
                        timeout=min(APP_RECOVERY_ACTION_SECONDS, remaining), batch=True)
                    if result != {"mycelium_activity_batch": dict(batch, status="recorded")}:
                        raise RuntimeError("missing or malformed batch acknowledgement")
                except Exception as exc:
                    turn["last_hook_error"] = (f"recovery batch {batch['id']} (steps "
                                               f"{', '.join(map(str, steps))}): {exc}")[-2000:]
                    mark_uncertain(turn, turn["last_hook_error"])
                    save()
                    return scan["status"], scan.get("reason", ""), [], rejected
                for step in steps:
                    turn["actions"][f"{step}:{ACTIVITY_HOOK}"] = "done"
                return ("truncated" if capped else scan["status"]), scan.get("reason", ""), steps, rejected

            def observe_recovered_edits(scan):
                """Edit observations for verified edits that only Stop recovery credited.

                The App can deliver an edit's PostToolUse while its step row is still
                running, so the callback credits nothing and recovery credits it at Stop.
                Its PreToolUse interval must still close with an edit observation (made now,
                so it overlaps every call after it), as the direct callback's does. Only
                edits with recorded activity (or that the tracker ignores, which the callback
                observes too) qualify; best effort, and only within the Stop's budget.
                """
                for found in scan["edits"]:
                    step, path = found["step"], found["path"]
                    if not edit_target(found, step) or f"{step}:io-observer" in turn["actions"]:
                        continue
                    if any(c in path for c in "\0\r\n"):
                        continue
                    credited = turn["actions"].get(f"{step}:{ACTIVITY_HOOK}") == "done"
                    if not credited and not tracker_ignores(Path(path).relative_to(root).as_posix()):
                        continue
                    remaining = app_stop_deadline - time.monotonic() - APP_FINALIZE_RESERVE_SECONDS
                    if remaining < APP_RECOVERY_OBSERVE_MIN_SECONDS:
                        return  # the rest close as turn-ended, never as a guessed edit
                    observe_edit(step, path, timeout=min(APP_RECOVERY_ACTION_SECONDS, remaining))

            def complete_commands():
                for step_key, command in list(turn["pending_commands"].items()):
                    step_index = int(step_key)
                    step = next((r for r in records if r["step_index"] == step_index), {})
                    if step_index > boundary or step.get("type") != "RUN_COMMAND" or step.get("status") != "DONE":
                        continue
                    command_root = target_root({"toolCall": {"name": "run_command", "args": {
                        "Cwd": command["cwd"]}}}, roots)
                    if command_root != root:
                        raise ValueError("Pending command no longer belongs to its native root")
                    exit_code = step.get("exit_code")
                    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                        exit_code = None
                    command_data = dict(data, cwd=command["cwd"], tool_name="Bash",
                                        tool_use_id=observer_call_id(step_index),
                                        tool_input={"command": command["command"]},
                                        tool_response={"exit_code": exit_code})
                    hooks = ["mycelium-data-tracker.sh", "mycelium-post-action.sh"]
                    apply_hooks(hooks, command_data, step_index)
                    if all(turn["actions"].get(f"{step_index}:{hook}") == "done" for hook in hooks):
                        del turn["pending_commands"][step_key]
                        save()

            if app:
                for step_key, binding in list(turn["app_pending"].items()):
                    settle_app_command(step_key, binding["command"], binding["call_id"])
            if event != "Stop" or (payload.get("fullyIdle") is True and
                    payload.get("terminationReason") in {"NO_TOOL_CALL", "model_stop"} and not payload.get("error")):
                complete_commands()
            if event == "PreInvocation":
                delivered = {token for record in records for token in record.get("delivered_reminders", [])}
                for token in delivered:
                    turn["reminders"].pop(token, None)
                save()  # Persist acknowledgement and any singleton migration.
                label = profile(host)["label"]
                guidance = (f"MYCELIUM — {label}. Project: {root}. Read {root}/MYCELIUM.md "
                            f"for this project's scientific conventions. Use the current {label} model and native "
                            "tools; do not launch Claude Code, Codex, or Gemini CLI as a substitute. "
                            f"Private handoff: {root}/.mycelium/run/{host}/{session_id}/last-session.md.")
                injected.append(guidance + "\n" + "\n".join(turn["context"]) +
                                ("\nMycelium could not start lifecycle tracking for this work cycle: " +
                                 one_line(turn["uncertain_cause"]) + " Correct the cause, then start a new "
                                 "explicit request."
                                 if turn.get("actions", {}).get("startup") == "running" and
                                 turn.get("uncertain_cause", "").startswith("startup: ") else
                                 "\nMycelium capture failed and this work cycle cannot be finalized: " +
                                 one_line(turn["last_hook_error"]) + " Inspect retained native-state.json; start a new "
                                 "explicit request after correcting the cause."
                                 if turn.get("last_hook_error") else
                                 "\nMycelium cannot finalize this work cycle: " +
                                 one_line(turn["uncertain_cause"]) + " Inspect retained native-state.json."
                                 if turn.get("uncertain") and turn.get("uncertain_cause") else "") +
                                ("\n" + "\n".join(turn["reminders"].values()) if turn["reminders"] else "") +
                                ("\nMycelium is waiting for terminal native evidence for command steps " +
                                 ", ".join(turn["pending_commands"]) + ". Output provenance is not finalized."
                                 if turn["pending_commands"] else "") +
                                ("\nMycelium is waiting for the Antigravity App to finish command steps " +
                                 ", ".join(turn["app_pending"]) + ". Output provenance is not finalized."
                                 if turn.get("app_pending") else "") +
                                ("\nMycelium cannot verify where or how command steps " +
                                 ", ".join(turn["unresolved_commands"]) + " ran in the Antigravity App. Their "
                                 "output provenance is unrecorded and will be logged as unverified when this work cycle ends."
                                 if turn.get("unresolved_commands") else ""))
            elif event == "PostToolUse" and app:
                hooks = []
                if app_kind == "read":
                    data.update(tool_name="Read", tool_input={"file_path": app_read}, tool_response={})
                    hooks = ["mycelium-read-tracker.sh"]
                elif app_kind == "unresolved":
                    turn["unresolved_commands"][str(boundary)] = APP_UNRESOLVED
                    save()
                elif app_kind == "command":
                    settle_app_command(str(boundary), app_read)
                elif app_kind == "edit":
                    found = app_edit(boundary, payload["toolCall"]["name"], app_read)
                    if found:
                        credit_app_edit(found, boundary)
                    elif ("edit-shape", boundary) in evidence:
                        warning = APP_EDIT_SHAPE_WARNING.format(
                            step=boundary, reason=evidence[("edit-shape", boundary)])
                        print(warning, file=sys.stderr)
                        if warning not in turn["context"]:
                            turn["context"].append(warning)
                            save()
                apply_hooks(hooks, data, boundary)
            elif event == "PostToolUse":
                call = payload["toolCall"]
                name, args = call.get("name"), call["args"]
                step = next((r for r in records if r["step_index"] == boundary), {})
                hooks = []
                if name == "run_command":
                    if step.get("type") != "RUN_COMMAND" or step.get("status") not in {"DONE", "RUNNING"}:
                        turn["last_native_error"] = str(payload.get("error") or "Execution evidence unavailable")
                        if step.get("status") != "ERROR" or not payload.get("error"):
                            mark_uncertain(turn, f"command step {boundary}: native execution evidence "
                                                 f"unavailable ({turn['last_native_error']})")
                        save()
                        continue
                    command = args.get("CommandLine")
                    if not isinstance(command, str) or len(command) > 65536:
                        raise ValueError("Invalid native command")
                    hook_names = ["mycelium-data-tracker.sh", "mycelium-post-action.sh"]
                    if not all(turn["actions"].get(f"{boundary}:{hook}") == "done" for hook in hook_names):
                        turn["pending_commands"].setdefault(str(boundary), {"cwd": args["Cwd"], "command": command})
                        save()
                        complete_commands()
                elif name in EDITS and not payload.get("error") and step.get("status") == "DONE":
                    relative = Path(args["TargetFile"]).relative_to(root)
                    if relative.parts and relative.parts[0] not in {".agents", "_agents", ".agent", ".mycelium", ".living"}:
                        data.update(tool_name="Write", tool_input={"file_path": args["TargetFile"]}, tool_response={"success": True})
                        hooks = ["mycelium-activity-tracker.sh"]
                        apply_hooks(hooks, data, boundary)
                        hooks = []
                        observe_edit(boundary, args["TargetFile"])
                elif name == "view_file" and not payload.get("error"):
                    data.update(tool_name="Read", tool_input={"file_path": args["AbsolutePath"]}, tool_response={})
                    hooks = ["mycelium-read-tracker.sh"]
                apply_hooks(hooks, data, boundary)
            elif event == "Stop":
                if payload.get("fullyIdle") is not True:
                    continue
                if payload.get("terminationReason") not in {"NO_TOOL_CALL", "model_stop"} or payload.get("error"):
                    turn["status"] = "incomplete"
                    turn["reason"] = "Native cancellation, limit, or error; lifecycle evidence retained"
                    save()
                    continue
                if app:
                    # Recover committed native edits whose callback was not delivered,
                    # for this explicit-user turn only, before shared finalization.
                    scan = app_recovery(user_step, boundary)
                    status, reason, credited, extra = recover_app_edits(scan)
                    observe_recovered_edits(scan)
                    rejected = dict(list({**scan["rejected"], **extra}.items())[:64])
                    turn["edit_recovery"] = {"status": status, "after": user_step, "through": boundary,
                                             "credited": credited, "rejected": rejected, "reason": reason}
                    save()
                # Reminders already injected by an earlier PreInvocation are acknowledged here too.
                for token in {t for record in records for t in record.get("delivered_reminders", [])}:
                    turn["reminders"].pop(token, None)
                if turn["reminders"] and turn["retries"] >= MAX_STOP_RETRIES:
                    # Last allowed Stop: record undelivered guidance instead of holding Stop.
                    append_undelivered_reminders(root, session_id, profile(host)["label"], turn["reminders"])
                    turn.setdefault("undelivered_reminders", []).extend(sorted(turn["reminders"]))
                    turn["reminders"] = {}
                    save()
                if turn["retries"] >= MAX_STOP_RETRIES and not turn["uncertain"] and not capture_deferred:
                    # Last allowed Stop: commands still pending can no longer be settled.
                    unresolved = turn.setdefault("unresolved_commands", {})
                    for pending_key in ("pending_commands", "app_pending"):
                        for step_key in list(turn.get(pending_key, {})):
                            unresolved.setdefault(step_key, FINAL_PENDING)
                            del turn[pending_key][step_key]
                    save()
                record = turn.setdefault("unverified_provenance", {})
                unverified = {k: v for k, v in turn.get("unresolved_commands", {}).items() if k not in record}
                if unverified:
                    # No agent action can settle these, so they never hold Stop: record them explicitly.
                    append_unverified_provenance(root, session_id, profile(host)["label"], unverified)
                    record.update(unverified)
                    save()
                if turn["uncertain"]:
                    result = {"decision": "block", "reason": unresolved_reason(turn)}
                elif capture_deferred:
                    result = {"decision": "block", "reason": "No time remained in this Stop to record completed native command steps. Active state was preserved; the next Stop records them."}
                elif turn["pending_commands"]:
                    result = {"decision": "block", "reason": "Native analysis commands still lack terminal evidence. Wait for completion; Mycelium has not finalized output provenance."}
                elif turn.get("app_pending"):
                    result = {"decision": "block", "reason": "Antigravity App command steps " + ", ".join(turn["app_pending"]) + " are still running. Wait for them to finish; Mycelium has not finalized their output provenance."}
                elif turn["reminders"]:
                    # Guidance queued after the last PreInvocation: this Stop reason delivers it once.
                    result = {"decision": "block", "reason": "Mycelium post-action guidance arrived after your "
                              "last step. Complete it, then end your turn again:\n" +
                              "\n".join(turn["reminders"].values())}
                    turn["reminders"] = {}
                elif app and turn["edit_recovery"]["status"] == "deferred":
                    result = {"decision": "block", "reason": "No time remains to recover native edit activity before Mycelium finalization. Active state was preserved for retry."}
                elif app_stop_deadline is not None and app_stop_deadline <= time.monotonic():
                    result = {"decision": "block", "reason": "No time remains to start Mycelium finalization. Active state was preserved for retry."}
                else:
                    if FINAL_PENDING not in turn.get("unresolved_commands", {}).values():
                        # Nothing of this work cycle is still running: close the observer
                        # intervals of calls that got no Post (denied, failed, unrecorded), as
                        # Claude's Stop does, so they stop marking later calls as overlapping. A
                        # command still running at the final allowance keeps its interval open.
                        observer_turn_end(root, session_id)
                    number = turn.get("stop_attempt", {}).get("number", 0) + 1
                    turn["stop_attempt"] = {"status": "running", "number": number}
                    save()
                    try:
                        budget = {} if app_stop_deadline is None else {
                            "timeout": max(0.001, app_stop_deadline - time.monotonic())}
                        result = run_hook("mycelium-stop-check.sh", data, root, plugin_root,
                                          last_stop=turn["retries"] >= MAX_STOP_RETRIES, **budget)
                    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as error:
                        if not app:
                            raise
                        # A killed shell is not proof that its finalizer stopped.
                        # Reconcile an exact receipt or keep the attempt unresolved.
                        if accepted_stop(root, session_id):
                            result = {}
                        else:
                            turn["last_native_error"] = f"App Stop outcome unavailable ({type(error).__name__})"
                            mark_uncertain(turn, f"{turn['last_native_error']}: {one_line(error, 1000)}")
                            result = {"decision": "block", "reason": unresolved_reason(turn)}
                    if not turn["uncertain"]:
                        turn["stop_attempt"]["status"] = "blocked" if result.get("decision") == "block" else "accepted"
                    save()
                if result.get("decision") == "block":
                    reason = str(result.get("reason", "Mycelium work remains incomplete"))
                    if turn["retries"] < MAX_STOP_RETRIES:
                        turn["retries"] += 1
                        reasons.append(reason)
                    else:
                        turn["status"] = "incomplete"
                        turn["reason"] = reason
                        print("Mycelium Stop allowance exhausted; incomplete evidence retained", file=sys.stderr)
                else:
                    turn["status"] = "accepted"
                    turn["context"] = []
                save()
    if event == "PreInvocation" and injected:
        return {"injectSteps": [{"ephemeralMessage": "\n\n".join(injected)}]}
    if event == "Stop" and reasons:
        return {"decision": "continue", "reason": "\n\n".join(reasons)}
    return {}


# ---- PreToolUse: observation only ---------------------------------------------------------------
#
# Antigravity requires a `decision` from every PreToolUse hook; `allow` delegates to the next
# hook or the host's default policy. This handler prints exactly PRE_ALLOW on every path and
# nothing else: it never grants, denies, asks, or rewrites a call. Its only effect is the I/O
# observer's pre-snapshot (run_command) or journal interval (edit tools), keyed so the matching
# PostToolUse closes the same call. The packaged wrapper (mycelium-antigravity-pre.sh) discards
# this process's output and prints the allow itself, so even a crash here cannot change it.

PRE_ALLOW = '{"decision": "allow"}\n'
# Soft deadline for the whole handler, from its start; the wrapper's watchdog kills the process
# at about 2 s for anything this deadline cannot interrupt.
PRE_TOOL_SECONDS = 1.5
PRE_RESERVE_SECONDS = 0.15  # left after the walk for writing the snapshot
PRE_LOCK_SECONDS = 0.5  # journal lock wait; the observer's default (2 s) exceeds the budget
# Fault injection for live verification only: a word in this project file (or the environment)
# makes the handler fail on purpose, to show approvals are unchanged when observation fails.
PRE_FAULT_FILE = ".mycelium/antigravity-pre-fault"
PRE_FAULT_ENV = "MYCELIUM_ANTIGRAVITY_PRE_FAULT"
PRE_FAULTS = {"crash", "slow", "hang", "exit"}


def observer_call_id(step):
    """The I/O observer's call id for one native tool step; Pre and Post derive it alike."""
    return f"ag-step-{step}"


def observer_state_dir(root):
    """The shared state directory the base hooks use for ROOT (<root>/.mycelium, physical)."""
    override = os.environ.get("MYCELIUM_STATE_DIR")
    if override and Path(root, override).resolve() != (Path(root) / ".mycelium").resolve():
        raise RuntimeError("MYCELIUM_STATE_DIR is set to another directory")
    directory = ensure_safe_project_directory(root, ".mycelium", create=False)
    if not directory.is_dir():
        raise RuntimeError("No Mycelium state directory")
    return Path(os.path.realpath(directory))


def _pre_fault(word, roots=()):
    """Apply one injected fault (live verification), after recording that it fired."""
    if word not in PRE_FAULTS:
        return
    for root in roots:
        try:
            import io_observer
            state_dir = observer_state_dir(root)
            io_observer.append_line(state_dir / "io" / "antigravity-pre-faults.ndjson",
                                    {"fault": word, "pid": os.getpid(), "wall_ns": time.time_ns()},
                                    io_root=state_dir / "io")
        except Exception:
            pass
    if word == "crash":
        raise RuntimeError("injected PreToolUse observer crash")
    if word == "exit":
        os._exit(3)
    if word == "hang":
        import signal
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM, signal.SIGTERM})
    time.sleep(60)


def _file_fault(roots):
    for root in roots:
        path = Path(root) / PRE_FAULT_FILE
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 64:
                continue
            word = path.read_text(errors="replace").strip()
        except OSError:
            continue
        if word in PRE_FAULTS:
            return word
    return None


def pre_tool_use(payload, host=HOST, *, deadline=None):
    """Open the observer's interval for one native call; return [(root, session, call)] observed.

    Never decides anything about the call. It refuses (observes nothing) on the same grounds as
    PostToolUse: host proof, the exact native conversation, an opted-in root, a supported tool
    shape, and an active work cycle (a late event never starts one). Read-only on native state.
    """
    profile(host)
    token = _ACTIVE_HOST.set(host)
    try:
        return _pre_tool_use(payload, host, time.monotonic() + PRE_TOOL_SECONDS if deadline is None
                             else deadline)
    finally:
        _ACTIVE_HOST.reset(token)


def _pre_tool_use(payload, host, deadline):
    import io_observer

    if not isinstance(payload, dict):
        return []
    app = host == APP_HOST
    call = payload.get("toolCall")
    name = call.get("name") if isinstance(call, dict) else None
    args = call.get("args") if isinstance(call, dict) else None
    step = payload.get("stepIdx")
    if name not in (APP_EDITS if app else EDITS) | {"run_command"} or not isinstance(args, dict) \
            or not integer(step):
        return []
    cid = identity(payload)
    transcript = native_transcript(payload, cid, host)
    roots = roots_for(payload)
    if not roots:
        return []
    fault = _file_fault(roots)
    if fault:
        _pre_fault(fault, roots)
    command = None
    # Roots where the call may write but will never be observed (no snapshot, and no Post that
    # closes it): it is journaled there as a background call, so every call that runs beside
    # it is marked overlapping instead of being credited with its writes. Turn end leaves a
    # background interval open; only the journal's age bound closes it.
    unobserved = []
    if name == "run_command":
        command = args.get("CommandLine")
        if not isinstance(command, str) or not command or len(command) > 65536:
            targets, unobserved = [], list(roots)
        elif app:
            # Where it ran is proven only afterwards, by the App's own step record. A daemon,
            # sandbox bypass or unrecognised argument is never settled and may outlive the call.
            targets, unobserved = ((list(roots), []) if app_store.command_args_supported(args)
                                   else ([], list(roots)))
        else:
            root = contained_root(args.get("Cwd"), roots)
            # A command whose Cwd is outside every opted-in root is never credited, but it can
            # still `cd` into one and write there.
            targets, unobserved = (([root], []) if root and Path(args["Cwd"]).is_dir()
                                   else ([], list(roots)))
    else:
        raw = args.get("Path" if app else "TargetFile")
        root = contained_root(raw, roots)
        if root is None:
            return []
        relative = Path(raw).relative_to(root)
        if not relative.parts or relative.parts[0] in PROTECTED:
            return []
        targets = [root]
    records = transcript_records(transcript)
    observed_turns = [r["step_index"] for r in records
                      if r.get("source") == "USER_EXPLICIT" and r.get("type") == "USER_INPUT"]
    call_id = observer_call_id(step)
    observed = []
    saved_lock_wait = io_observer.LOCK_WAIT_SECONDS
    io_observer.LOCK_WAIT_SECONDS = min(saved_lock_wait, PRE_LOCK_SECONDS)
    try:
        for root in targets + unobserved:
            try:
                directory = preflight(root, cid)
                state = read_state(directory / "native-state.json", cid)
                candidates = [n for n in observed_turns + [int(k) for k in state["turns"]] if n <= step]
                if not candidates:
                    continue
                user_step = max(candidates)
                turn = state["turns"].get(str(user_step))
                if turn is None:
                    continue  # no work cycle was ever started: nothing to key the call to
                session = f"{cid}-turn-{user_step}"
                state_dir = observer_state_dir(root)
                if root in unobserved or turn["status"] != "active":
                    # Late or unowned (no Post will close it), or never observable: journal
                    # it without a snapshot, as background, so it cannot hide behind others.
                    if name == "run_command":
                        try:
                            io_observer.pre(state_dir, host=host, session=session, call_id=call_id,
                                            tool="Bash", repo_root=root, snapshot=False,
                                            background=True, detached=True)
                        except io_observer.LockTimeout:
                            pass  # its open marker still marks later calls as overlapping
                    continue
                detached = command is not None and io_observer.starts_background_job(command)
                seconds = max(0.01, min(io_observer.DEFAULT_WALK_SECONDS,
                                        deadline - time.monotonic() - PRE_RESERVE_SECONDS))
                try:
                    io_observer.pre(state_dir, host=host, session=session, call_id=call_id,
                                    tool="Bash" if command is not None else "Write",
                                    repo_root=root, snapshot=command is not None,
                                    background=detached, detached=detached, walk_seconds=seconds)
                except io_observer.LockTimeout:
                    pass  # the snapshot (if any) is still taken and records the journal gap
                observed.append((root, session, call_id))
            except Exception as error:
                print(f"Mycelium Antigravity PreToolUse ({root}): {one_line(error, 300)}", file=sys.stderr)
    finally:
        io_observer.LOCK_WAIT_SECONDS = saved_lock_wait
    return observed


def pre_tool_main(args, host):
    """Entry point for PreToolUse: always PRE_ALLOW on stdout and exit 0."""
    import signal

    def expire(_signum, _frame):
        os.write(1, PRE_ALLOW.encode())
        os._exit(0)

    try:
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, PRE_TOOL_SECONDS)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        _pre_fault(os.environ.get(PRE_FAULT_ENV, ""))
        if len(args) == 1 and args[0] == "PreToolUse":
            raw = sys.stdin.read(1024 * 1024 + 1)
            if len(raw) <= 1024 * 1024 and raw.strip():
                pre_tool_use(json.loads(raw), host)
    except Exception as error:
        print(f"Mycelium Antigravity PreToolUse: {one_line(error, 300)}", file=sys.stderr)
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        except (ValueError, OSError, AttributeError):
            pass
    os.write(1, PRE_ALLOW.encode())
    return 0


def main():
    if "PreToolUse" in sys.argv[1:]:
        args, host = sys.argv[1:], HOST
        if len(args) == 3 and args[0] == "--host" and args[1] in PROFILES:
            host, args = args[1], args[2:]
        return pre_tool_main(args, host)
    try:
        if len(sys.argv) in (4, 5) and sys.argv[1] == "--record-accepted-stop":
            # Existing IDE callers omit the host; the shared Stop hook names it.
            host = sys.argv[4] if len(sys.argv) == 5 else HOST
            record_accepted_stop(Path(sys.argv[2]), sys.argv[3], active_host(host))
            return 0
        args, host = sys.argv[1:], HOST
        if len(args) == 3 and args[0] == "--host":
            host, args = args[1], args[2:]
        if len(args) != 1:
            raise ValueError("Usage: antigravity_hooks.py [--host HOST] EVENT")
        profile(host)
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Native payload exceeds safety bound")
        result = dispatch(args[0], json.loads(raw), host=host)
    except Exception as exc:
        print(f"Mycelium Antigravity: {exc}", file=sys.stderr)
        # Preserve the native approval policy and all unfinalized state.
        print("{}")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
