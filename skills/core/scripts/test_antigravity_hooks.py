"""Contracts retained from Antigravity IDE 2.5.5's automatic hook dispatch."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "antigravity_hooks", Path(__file__).with_name("antigravity_hooks.py")
)
ag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ag)
CID = "a0000000-0000-4000-8000-000000000001"


@pytest.fixture
def native(tmp_path, monkeypatch):
    repo = tmp_path / "research α"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    # Exact IDE native root under an isolated home: the adapter refuses any other root.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    brain = tmp_path / "home/.gemini/antigravity-ide/brain" / CID
    logs = brain / ".system_generated/logs"
    logs.mkdir(parents=True)
    transcript = logs / "transcript_full.jsonl"
    transcript.write_text(json.dumps({"step_index": 0, "source": "USER_EXPLICIT",
                                      "type": "USER_INPUT", "status": "DONE"}) + "\n")
    payload = {"conversationId": CID, "workspacePaths": [str(repo)],
               "artifactDirectoryPath": str(brain), "transcriptPath": str(transcript),
               "invocationNum": 0, "initialNumSteps": 1, "modelName": "gemini-3.8-flash-high"}
    calls = []

    def hook(name, data, root, plugin):
        calls.append((name, data))
        if name == "mycelium-health.sh":
            return {"hookSpecificOutput": {"additionalContext": "NATIVE-START"}}
        return {}

    monkeypatch.setattr(ag, "run_hook", hook)
    return repo, transcript, payload, calls


def append(transcript, **record):
    with transcript.open("a") as stream:
        stream.write(json.dumps(record) + "\n")


def test_legacy_singleton_reminder_migrates_without_loss(native):
    repo, transcript, payload, calls = native
    ag.dispatch("PreInvocation", payload)
    path = next((repo / ".mycelium").rglob("native-state.json"))
    state = json.loads(path.read_text())
    token = CID + "-turn-0-step-3"
    message = f"[Mycelium reminder {token}] Retain this undelivered guidance."
    turn = state["turns"]["0"]
    turn.pop("reminders", None)
    turn.update(reminder_id=token, reminder=message)
    path.write_text(json.dumps(state))
    response = ag.dispatch("PreInvocation", payload)
    assert message in response["injectSteps"][0]["ephemeralMessage"]
    retained = json.loads(path.read_text())["turns"]["0"]
    assert retained["reminders"] == {token: message}
    assert "reminder" not in retained and "reminder_id" not in retained
    append(transcript, step_index=3, source="SYSTEM_SDK", type="EPHEMERAL_MESSAGE", status="DONE", content=message)
    assert message not in ag.dispatch("PreInvocation", dict(payload, initialNumSteps=4))["injectSteps"][0]["ephemeralMessage"]


@pytest.mark.parametrize("reminders", [[], {"wrong-turn-step-3": "message"}, {CID + "-turn-0-step-3": 42}])
def test_malformed_pending_reminders_preserve_state(native, reminders):
    repo, transcript, payload, calls = native
    ag.dispatch("PreInvocation", payload)
    path = next((repo / ".mycelium").rglob("native-state.json"))
    state = json.loads(path.read_text())
    state["turns"]["0"]["reminders"] = reminders
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="pending native reminders"):
        ag.dispatch("PreInvocation", payload)
    assert path.read_bytes() == before


def test_captured_ide_event_sequence_preserves_native_execution_outcomes(native):
    repo, transcript, p, calls = native
    fixture = Path(__file__).with_name("testdata") / "antigravity-ide-2.5.5.json"
    captured = json.loads(fixture.read_text().replace("@ROOT@", str(repo)).replace(
        "@ARTIFACT@", p["artifactDirectoryPath"]))
    for event in captured["events"]:
        transcript.write_text("".join(json.dumps(r) + "\n" for r in event["transcript"]))
        ag.dispatch(event["event"], event["payload"])
    executions = [data for name, data in calls if name == "mycelium-data-tracker.sh"]
    assert [data["tool_response"]["exit_code"] for data in executions] == [0, 7]
    starts = [data["session_id"] for name, data in calls if name == "mycelium-health.sh"]
    assert starts == [CID + "-turn-0", CID + "-turn-10"]
    state = json.loads(next((repo / ".mycelium").rglob("native-state.json")).read_text())
    assert state["turns"]["0"]["status"] == "accepted"
    assert state["turns"]["10"]["status"] == "incomplete"


def test_host_does_not_follow_model_and_invocation_reset_is_not_new_turn(native):
    repo, transcript, p, calls = native
    for model in ["gemini-3.8-flash-high", "claude-4.6-sonnet", "gpt-oss-120b"]:
        response = ag.dispatch("PreInvocation", dict(p, modelName=model))
        assert "NATIVE-START" in json.dumps(response)
    assert len(calls) == 1
    assert calls[0][1]["session_id"] == CID + "-turn-0"
    append(transcript, step_index=10, source="USER_EXPLICIT", type="USER_INPUT", status="DONE")
    ag.dispatch("PreInvocation", dict(p, initialNumSteps=11))
    assert len(calls) == 2
    assert calls[-1][1]["session_id"] == CID + "-turn-10"


def test_native_empty_error_does_not_overwrite_exit_seven_and_replay_is_silent(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=7)
    post = dict(p, stepIdx=3, error="", toolCall={"name": "run_command", "args": {
        "CommandLine": "python3 analysis.py", "Cwd": str(repo)}})
    assert ag.dispatch("PostToolUse", post) == {}
    ag.dispatch("PostToolUse", post)
    executions = [data for name, data in calls if name == "mycelium-data-tracker.sh"]
    assert len(executions) == 1
    assert executions[0]["tool_response"]["exit_code"] == 7


def test_async_post_stays_pending_and_nonidle_stop_does_not_finalize(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="RUN_COMMAND", status="RUNNING")
    ag.dispatch("PostToolUse", dict(p, stepIdx=3, error="", toolCall={"name": "run_command",
        "args": {"CommandLine": "python3 analysis.py", "Cwd": str(repo), "WaitMsBeforeAsync": 0}}))
    assert not any(name == "mycelium-data-tracker.sh" for name, _ in calls)
    state = json.loads(next((repo / ".mycelium").rglob("native-state.json")).read_text())
    assert state["turns"]["0"]["pending_commands"]["3"]["command"] == "python3 analysis.py"
    ag.dispatch("Stop", dict(p, fullyIdle=False, terminationReason="NO_TOOL_CALL", executionNum=0))
    assert not any(name == "mycelium-stop-check.sh" for name, _ in calls)


def test_done_command_without_exit_retains_unknown_status(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE")
    ag.dispatch("PostToolUse", dict(p, stepIdx=3, error="", toolCall={"name": "run_command",
        "args": {"CommandLine": "python3 analysis.py", "Cwd": str(repo)}}))
    data = next(data for name, data in calls if name == "mycelium-data-tracker.sh")
    assert data["tool_response"]["exit_code"] is None


def test_active_legacy_preview_is_retained_as_uncertain(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    path = next((repo / ".mycelium").rglob("native-state.json"))
    state = json.loads(path.read_text())
    state["schema"] = 1
    del state["turns"]["0"]["pending_commands"]
    path.write_text(json.dumps(state))
    assert ag.dispatch("Stop", dict(p, fullyIdle=True, terminationReason="NO_TOOL_CALL"))["decision"] == "continue"
    retained = json.loads(path.read_text())
    assert retained["schema"] == 2
    assert retained["turns"]["0"]["uncertain"] is True
    assert not any(name == "mycelium-stop-check.sh" for name, _ in calls)


def test_stop_retry_is_bounded_and_does_not_reset_transaction(native, monkeypatch):
    repo, transcript, p, calls = native
    original = ag.run_hook
    def blocked(name, data, root, plugin):
        if name == "mycelium-stop-check.sh":
            return {"decision": "block", "reason": "Update the living layer"}
        return original(name, data, root, plugin)
    monkeypatch.setattr(ag, "run_hook", blocked)
    ag.dispatch("PreInvocation", p)
    stop = dict(p, fullyIdle=True, terminationReason="NO_TOOL_CALL", executionNum=0)
    for i in range(ag.MAX_STOP_RETRIES):
        assert ag.dispatch("Stop", dict(stop, executionNum=i))["decision"] == "continue"
        ag.dispatch("PreInvocation", p)
    assert ag.dispatch("Stop", dict(stop, executionNum=ag.MAX_STOP_RETRIES)) == {}
    assert len([name for name, _ in calls if name == "mycelium-health.sh"]) == 1
    assert "incomplete" in next((repo / ".mycelium").rglob("native-state.json")).read_text()


@pytest.mark.parametrize("bad", [None, 5, "..", "../escape"])
def test_invalid_identity_has_no_side_effect(native, bad):
    repo, _, p, calls = native
    with pytest.raises(ValueError):
        ag.dispatch("PreInvocation", dict(p, conversationId=bad))
    assert not (repo / ".mycelium").exists()
    assert not calls


def test_plain_repo_and_scratch_are_silent_and_mixed_roots_remain_isolated(native, tmp_path):
    repo, _, p, calls = native
    plain = tmp_path / "plain"
    plain.mkdir()
    subprocess.run(["git", "init", "-q", str(plain)], check=True)
    assert ag.dispatch("PreInvocation", dict(p, workspacePaths=[str(plain)])) == {}
    assert ag.dispatch("PreInvocation", dict(p, workspacePaths=[])) == {}
    ag.dispatch("PreInvocation", dict(p, workspacePaths=[str(repo), str(plain)]))
    assert not (plain / ".mycelium").exists()
    assert not (plain / ".living").exists()
    assert len(calls) == 1


def test_unsafe_late_root_preflights_all_roots(native, tmp_path):
    repo, _, p, calls = native
    second = tmp_path / "second"
    second.mkdir()
    subprocess.run(["git", "init", "-q", str(second)], check=True)
    (second / ".living").mkdir()
    (second / ".mycelium").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        ag.dispatch("PreInvocation", dict(p, workspacePaths=[str(repo), str(second)]))
    assert not (repo / ".mycelium").exists()
    assert not calls


def test_foreign_transcript_and_failed_edits_do_not_create_activity(native, tmp_path):
    repo, transcript, p, calls = native
    with pytest.raises(ValueError):
        ag.dispatch("PreInvocation", dict(p, transcriptPath=str(tmp_path / "secret")))
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="WRITE_TO_FILE", status="ERROR")
    ag.dispatch("PostToolUse", dict(p, stepIdx=3, error="permission denied",
        toolCall={"name": "write_to_file", "args": {"TargetFile": str(repo / "a.py")}}))
    assert not any(name == "mycelium-activity-tracker.sh" for name, _ in calls)


def test_accepted_stop_ignores_late_event_and_next_user_turn_still_starts(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    ag.dispatch("Stop", dict(p, fullyIdle=True, terminationReason="NO_TOOL_CALL", executionNum=0))
    count = len(calls)
    ag.dispatch("PreInvocation", p)
    assert len(calls) == count
    append(transcript, step_index=20, source="USER_EXPLICIT", type="USER_INPUT", status="DONE")
    ag.dispatch("PreInvocation", dict(p, initialNumSteps=21))
    assert calls[-1][1]["session_id"].endswith("-turn-20")


def test_shared_stop_parses_with_system_bash():
    script = Path(__file__).parents[1] / "hooks/mycelium-stop-check.sh"
    result = subprocess.run(["/bin/bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_denied_command_is_not_attributed_as_executed(native):
    repo, transcript, p, calls = native
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="RUN_COMMAND", status="ERROR")
    ag.dispatch("PostToolUse", dict(p, stepIdx=3, error="Permission denied",
        toolCall={"name": "run_command", "args": {"Cwd": str(repo), "CommandLine": "python3 analysis.py"}}))
    assert not any(name == "mycelium-data-tracker.sh" for name, _ in calls)


def test_concurrent_duplicate_start_has_one_core_transaction(native):
    from concurrent.futures import ThreadPoolExecutor
    _, _, p, calls = native
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ag.dispatch("PreInvocation", p), range(2)))
    assert all("NATIVE-START" in json.dumps(r) for r in results)
    assert len(calls) == 1


def test_post_action_context_is_acknowledged_before_the_negative_neighbor(native, monkeypatch):
    repo, transcript, p, calls = native
    original = ag.run_hook
    def hook(name, data, root, plugin):
        result = original(name, data, root, plugin)
        if name == "mycelium-post-action.sh" and data["tool_input"]["command"] == "python3 analysis.py":
            return {"hookSpecificOutput": {"additionalContext": "POST-ACTION-MARKER"}}
        return result
    monkeypatch.setattr(ag, "run_hook", hook)
    ag.dispatch("PreInvocation", p)
    append(transcript, step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    post = dict(p, stepIdx=3, error="", toolCall={"name": "run_command", "args": {
        "CommandLine": "python3 analysis.py", "Cwd": str(repo)}})
    ag.dispatch("PostToolUse", post)
    response = ag.dispatch("PreInvocation", dict(p, initialNumSteps=4))
    delivered = response["injectSteps"][0]["ephemeralMessage"]
    assert "POST-ACTION-MARKER" in delivered
    append(transcript, step_index=4, source="SYSTEM_SDK", type="EPHEMERAL_MESSAGE", status="DONE", content=delivered)
    append(transcript, step_index=6, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    post["stepIdx"] = 6
    post["toolCall"]["args"]["CommandLine"] = 'env -S "echo prefix" python3 analysis.py'
    ag.dispatch("PostToolUse", post)
    assert "POST-ACTION-MARKER" not in json.dumps(ag.dispatch("PreInvocation", dict(p, initialNumSteps=7)))
