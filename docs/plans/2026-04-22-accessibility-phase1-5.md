# Accessibility Phase 1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two pre-audit fixes to the mycelium accessibility architecture so the Day-7 audit (2026-04-29) measures the intended system rather than a biased proxy.

**Architecture:** Two independent, narrow changes. Item A raises the scorer weight for curator-written `Triggers` (+2 → +3) in a single-line scorer edit. Item B adds a new PreToolUse hook on the `Agent` matcher that prepends `<mycelium-menu>` to dispatched subagent prompts via `hookSpecificOutput.updatedInput`. Neither change modifies Phase 1 contracts (SessionStart menu, edit-time push, dedup, error log).

**Tech Stack:** Python 3.11+ (`match_edit.py`, `pytest`), bash (hooks), `jq` (JSON I/O in hooks), `flock`/`realpath`/`shasum` (existing hook conventions), Claude Code hook contract (`hookSpecificOutput.updatedInput` + `permissionDecision: "allow"`).

**Spec:** [2026-04-22-accessibility-phase1-5.md](../designs/2026-04-22-accessibility-phase1-5.md)

**Branch strategy:** Commit directly to `feat/accessibility-phase1` (per spec §7.1 recommendation). Phase 1.5 is a prerequisite for the Phase 1 audit, not a separate feature.

---

## Task 1: Raise tokenized trigger weight from +2 to +3

Implements spec §3. Single-line scorer change plus test updates. The +3 weight elevates curator-written triggers above tags (+2) and matches title weight — an intentional calibration trade-off documented in spec §3.6.

**Files:**
- Modify: `skills/core/scripts/match_edit.py:90-91`
- Modify: `skills/core/scripts/test_match_edit.py:162-171` (existing `test_score_entry_triggers_plus2`)
- Modify: `skills/core/scripts/test_match_edit.py` (add two new tests after line 171)

- [ ] **Step 1: Update the existing weight-pinning test to expect +3**

Open `skills/core/scripts/test_match_edit.py`. Replace the function at lines 162-171:

```python
def test_score_entry_triggers_plus3() -> None:
    entry = {
        "title": "X",
        "tags": [],
        "triggers": ["panel"],
        "body": "",
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == pytest.approx(3.0)
```

- [ ] **Step 2: Add a new test that asserts triggers outrank tags on same token**

Append to `skills/core/scripts/test_match_edit.py` after the renamed function:

```python
def test_trigger_outranks_tag_same_token() -> None:
    """A trigger match on 'panel' scores higher than a tag match on 'panel'."""
    trigger_entry = {
        "title": "X",
        "tags": [],
        "triggers": ["panel"],
        "body": "",
        "date": "2020-01-01",
    }
    tag_entry = {
        "title": "Y",
        "tags": ["panel"],
        "triggers": [],
        "body": "",
        "date": "2020-01-01",
    }
    trigger_score = me.score_entry(trigger_entry, ["panel"], today="2026-04-22")
    tag_score = me.score_entry(tag_entry, ["panel"], today="2026-04-22")
    assert trigger_score > tag_score
    assert trigger_score == pytest.approx(3.0)
    assert tag_score == pytest.approx(2.0)


def test_trigger_plus_title_stacks_to_6() -> None:
    """Both signals compose additively without capping."""
    entry = {
        "title": "Panel notes",
        "tags": [],
        "triggers": ["panel"],
        "body": "",
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == pytest.approx(6.0)  # +3 title + +3 trigger, no recency
```

- [ ] **Step 3: Run the three tests, expect FAIL**

```bash
cd /Users/mst36/tools/mycelium && \
  python -m pytest skills/core/scripts/test_match_edit.py::test_score_entry_triggers_plus3 \
                   skills/core/scripts/test_match_edit.py::test_trigger_outranks_tag_same_token \
                   skills/core/scripts/test_match_edit.py::test_trigger_plus_title_stacks_to_6 -v
```

Expected: FAIL (existing code scores triggers at +2, so the `approx(3.0)` assertion fails, `trigger_score > tag_score` fails, and `approx(6.0)` becomes 5.0).

- [ ] **Step 4: Implement the weight change**

Open `skills/core/scripts/match_edit.py`. Change the trigger branch in `score_entry()` at lines 90-91:

**Before:**
```python
        if tok in trigger_tokens:
            score += 2
```

**After:**
```python
        if tok in trigger_tokens:
            score += 3
```

- [ ] **Step 5: Run the three tests, expect PASS**

```bash
cd /Users/mst36/tools/mycelium && \
  python -m pytest skills/core/scripts/test_match_edit.py::test_score_entry_triggers_plus3 \
                   skills/core/scripts/test_match_edit.py::test_trigger_outranks_tag_same_token \
                   skills/core/scripts/test_match_edit.py::test_trigger_plus_title_stacks_to_6 -v
```

Expected: all three PASS.

- [ ] **Step 6: Run the full scripts test suite — regression guard**

```bash
cd /Users/mst36/tools/mycelium && \
  python -m pytest skills/core/scripts/ -v --tb=short
```

Expected: all tests pass (105 existing + 2 new = 107). If any existing test fails, investigate — per spec §3.4, only the renamed weight-pinning test should need modification; other failures indicate unexpected coupling.

- [ ] **Step 7: Commit**

```bash
cd /Users/mst36/tools/mycelium && \
  git add skills/core/scripts/match_edit.py skills/core/scripts/test_match_edit.py && \
  git commit -m "feat(accessibility): raise tokenized trigger weight +2 → +3

Phase 1.5 Item A. Triggers now outrank tags at equal match specificity,
matching the +3 title weight. Test suite 107/107 passing.

See docs/designs/2026-04-22-accessibility-phase1-5.md §3."
```

---

## Task 2: Extract log-rotation helper into shared library

Implements spec §7.2 prereq #2, recommendation (a). The current `_mycelium_rotate_error_log` function lives only in `mycelium-health.sh:32-45`. Task 3 will require it from the new dispatch-injector. Extract now so both hooks can source one source of truth.

**Files:**
- Create: `skills/core/hooks/_lib/log_rotation.sh`
- Modify: `skills/core/hooks/mycelium-health.sh:32-45` (replace inline function with `source` of the shared file)

- [ ] **Step 1: Create the shared library directory**

```bash
mkdir -p /Users/mst36/tools/mycelium/skills/core/hooks/_lib
```

- [ ] **Step 2: Create the shared library file with the rotation function**

Create `/Users/mst36/tools/mycelium/skills/core/hooks/_lib/log_rotation.sh` with this exact content (it contains the function verbatim from `mycelium-health.sh:32-41` — no modifications):

```bash
# log_rotation.sh — shared rotation helper for mycelium hook error logs.
# Source this file; do not execute directly.
#
# Usage:
#   source "${SCRIPT_DIR}/_lib/log_rotation.sh"
#   _mycelium_rotate_error_log "${REPO_ROOT}/.claude/mycelium-hook-errors.log"

_mycelium_rotate_error_log() {
    local errlog="$1"
    [ -f "${errlog}" ] || return 0
    local size
    size=$(wc -c < "${errlog}" 2>/dev/null || echo 0)
    if [ "${size}" -gt 10485760 ]; then
        mv "${errlog}" "${errlog}.prev" 2>/dev/null || true
        : > "${errlog}"
    fi
}
```

No shebang — this file is source'd, never executed. Do not `chmod +x` it.

Verify the file is valid bash by sourcing it into a subshell:

```bash
bash -c 'source /Users/mst36/tools/mycelium/skills/core/hooks/_lib/log_rotation.sh && declare -F _mycelium_rotate_error_log'
```

Expected output: `declare -f _mycelium_rotate_error_log`

- [ ] **Step 3: Modify mycelium-health.sh to source the shared helper**

In `skills/core/hooks/mycelium-health.sh`, delete the inline function definition at lines 31-41 (the `# --- Error log rotation helper ---` comment plus the `_mycelium_rotate_error_log() { ... }` block), and replace with a single `source` line. The final diff should:

1. Remove lines 31-41 inclusive (10 lines including the comment banner).
2. Insert this line immediately before the first call to `_mycelium_rotate_error_log` (which is currently at line 45, but after the deletion it will be at line 35):

```bash
# shellcheck source=_lib/log_rotation.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lib/log_rotation.sh"
```

After the edit, the file has net −9 lines. The sourced file defines the function with the same name, so downstream `_mycelium_rotate_error_log "${...}"` calls are unchanged.

- [ ] **Step 4: Smoke test — invoke mycelium-health.sh directly**

```bash
cd /Users/mst36/tools/mycelium && \
  echo '{"session_id":"test-rotation","cwd":"/tmp"}' | \
  bash skills/core/hooks/mycelium-health.sh
```

Expected: exits 0, prints menu JSON or empty output (depending on cwd), no `command not found` or `_mycelium_rotate_error_log: not found` errors.

- [ ] **Step 5: Run full Python test suite — regression guard**

```bash
cd /Users/mst36/tools/mycelium && \
  python -m pytest skills/core/scripts/ -v --tb=short
```

Expected: 107 pass, same as end of Task 1.

- [ ] **Step 6: Commit**

```bash
cd /Users/mst36/tools/mycelium && \
  git add skills/core/hooks/_lib/log_rotation.sh skills/core/hooks/mycelium-health.sh && \
  git commit -m "refactor(hooks): extract log-rotation helper into _lib

Phase 1.5 prerequisite. The new dispatch-injector hook (Task 3) will
source the same helper. One source of truth prevents rotation-logic
divergence.

See docs/designs/2026-04-22-accessibility-phase1-5.md §7.2."
```

---

## Task 3: Dispatch-injector — core skeleton with menu prepend

Creates the new PreToolUse hook and implements the happy path: read stdin, discover `.living/MENU.md`, prepend `<mycelium-menu>` to `tool_input.prompt`, emit `updatedInput` with `permissionDecision: "allow"`. All other features (exclusions, skip-if-already-injected, truncation) land in Task 4.

**Files:**
- Create: `skills/core/hooks/mycelium-dispatch-injector.sh`
- Create: `skills/core/hooks/test_dispatch_injector.sh` (test harness; tests added incrementally)

- [ ] **Step 1: Write a failing integration test — menu-is-prepended-when-MENU.md-exists**

Create `/Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh`:

```bash
#!/usr/bin/env bash
# Integration tests for mycelium-dispatch-injector.sh. Run directly.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${SCRIPT_DIR}/mycelium-dispatch-injector.sh"

TESTS_RUN=0
TESTS_FAILED=0
_assert() {
    TESTS_RUN=$((TESTS_RUN + 1))
    local label="$1"; shift
    if eval "$@"; then
        echo "  ✓ ${label}"
    else
        echo "  ✗ ${label}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test 1: menu prepended when MENU.md present
_test_menu_prepended_when_present() {
    echo "test_menu_prepended_when_present"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    printf '# Mycelium Menu\n\n- figures — color, DPI\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s1","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"original"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "output contains <mycelium-menu>" '[[ "${output}" == *"<mycelium-menu>"* ]]'
    _assert "output preserves original prompt" '[[ "${output}" == *"original"* ]]'
    _assert "output has permissionDecision allow" '[[ "${output}" == *"allow"* ]]'
    _assert "output preserves description" '[[ "${output}" == *"\"description\":\"d\""* ]]'
    _assert "output preserves subagent_type" '[[ "${output}" == *"general-purpose"* ]]'
    rm -rf "${tmp}"
}

_test_menu_prepended_when_present

echo ""
echo "Ran ${TESTS_RUN}, failed ${TESTS_FAILED}"
[[ ${TESTS_FAILED} -eq 0 ]]
```

Make it executable:

```bash
chmod +x /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: all `_assert` calls fail because the hook doesn't exist yet. Exit code non-zero.

- [ ] **Step 3: Create the dispatch-injector hook (skeleton)**

Create `/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-dispatch-injector.sh`:

```bash
#!/usr/bin/env bash
# mycelium-dispatch-injector.sh — Claude Code PreToolUse hook (Agent matcher)
# Prepends <mycelium-menu> to the dispatched subagent's prompt so subagents
# receive the same L1 routing surface top-level sessions get at SessionStart.
#
# See docs/designs/2026-04-22-accessibility-phase1-5.md §4 for the full contract.

set -euo pipefail

SCRIPT_VERSION="phase1-5.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=_lib/log_rotation.sh
source "${SCRIPT_DIR}/_lib/log_rotation.sh"

_find_repo_root() {
    # Walk up from $1 looking for .living/MENU.md. Echoes the matching dir or empty.
    local d="$1"
    while [[ "${d}" != "/" && "${d}" != "." && -n "${d}" ]]; do
        if [[ -f "${d}/.living/MENU.md" ]]; then
            printf '%s' "${d}"
            return 0
        fi
        d="$(dirname "${d}")"
    done
}

_log_error() {
    local repo_root="$1"; shift
    local session_id="$1"; shift
    local error_class="$1"; shift
    local details="$1"; shift
    [[ -z "${repo_root}" ]] && return 0
    local errlog="${repo_root}/.claude/mycelium-hook-errors.log"
    mkdir -p "$(dirname "${errlog}")" 2>/dev/null || true
    _mycelium_rotate_error_log "${errlog}" 2>/dev/null || true
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date +%Y-%m-%dT%H:%M:%S)" \
        "dispatch-injector" \
        "${session_id}" \
        "${error_class}" \
        "${details}" \
        "${SCRIPT_VERSION}" >> "${errlog}" 2>/dev/null || true
}

{
    INPUT="$(cat)"
    SESSION_ID="$(printf '%s' "${INPUT}" | jq -r '.session_id // empty' 2>/dev/null || echo "")"

    # Prefer cwd from stdin, fallback to process pwd. Matches mycelium-health.sh discovery.
    CWD="$(printf '%s' "${INPUT}" | jq -r '.cwd // empty' 2>/dev/null || echo "")"
    [[ -z "${CWD}" ]] && CWD="$(pwd)"

    REPO_ROOT="$(_find_repo_root "${CWD}")"
    if [[ -z "${REPO_ROOT}" ]]; then
        exit 0
    fi

    MENU_CONTENT="$(cat "${REPO_ROOT}/.living/MENU.md" 2>/dev/null || echo "")"
    if [[ -z "${MENU_CONTENT}" ]]; then
        _log_error "${REPO_ROOT}" "${SESSION_ID}" "menu-empty" ""
        exit 0
    fi

    # Echo entire tool_input, modify only .prompt. Preserves all sibling keys.
    MODIFIED_INPUT="$(printf '%s' "${INPUT}" | jq -c \
        --arg menu "${MENU_CONTENT}" \
        '.tool_input | .prompt = ("<mycelium-menu>\n" + $menu + "\n</mycelium-menu>\n\n" + (.prompt // ""))' \
        2>/dev/null)"

    if [[ -z "${MODIFIED_INPUT}" ]]; then
        _log_error "${REPO_ROOT}" "${SESSION_ID}" "jq-modify-failed" ""
        exit 0
    fi

    jq -nc --argjson inp "${MODIFIED_INPUT}" \
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": $inp}}'

} 2>/dev/null || true

exit 0
```

Make it executable:

```bash
chmod +x /Users/mst36/tools/mycelium/skills/core/hooks/mycelium-dispatch-injector.sh
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: all 5 assertions pass. If `output preserves description` fails, check that `jq` echoes all fields of `tool_input` (the `.tool_input | .prompt = ...` idiom preserves every sibling key).

- [ ] **Step 5: Commit**

```bash
cd /Users/mst36/tools/mycelium && \
  git add skills/core/hooks/mycelium-dispatch-injector.sh skills/core/hooks/test_dispatch_injector.sh && \
  git commit -m "feat(accessibility): dispatch-injector hook skeleton with menu prepend

Phase 1.5 Item B. PreToolUse hook on Agent matcher. Prepends
<mycelium-menu> to the dispatched subagent prompt via
hookSpecificOutput.updatedInput with permissionDecision: allow.

See docs/designs/2026-04-22-accessibility-phase1-5.md §4.2-4.3."
```

---

## Task 4: Dispatch-injector — exclusions, idempotency, truncation

Adds the three guards defined in spec §4.4 and §4.6:
- Skip if `tool_input.prompt` already starts with `<mycelium-menu>` (prevents double-injection).
- Skip if `subagent_type` is in the hardcoded exclusion list.
- Truncate `MENU.md` at 8 KB with a marker if it exceeds the cap.

**Files:**
- Modify: `skills/core/hooks/mycelium-dispatch-injector.sh`
- Modify: `skills/core/hooks/test_dispatch_injector.sh`

- [ ] **Step 1: Add failing tests for all three guards**

Append these test functions to `test_dispatch_injector.sh` before the final `echo "Ran..."` line. Also add three invocations to the test-runner block.

```bash
# Test 2: skip when prompt already contains <mycelium-menu>
_test_skip_if_already_injected() {
    echo "test_skip_if_already_injected"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s2","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"<mycelium-menu>\nalready here\n</mycelium-menu>\n\noriginal"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when already injected" '[[ -z "${output}" ]]'
    rm -rf "${tmp}"
}

# Test 3: skip when subagent_type in exclusion list
_test_excluded_subagent_type() {
    echo "test_excluded_subagent_type"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s3","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"cost-tracker","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when subagent excluded" '[[ -z "${output}" ]]'
    rm -rf "${tmp}"
}

# Test 4: truncate when MENU.md > 8KB
_test_truncate_at_8kb() {
    echo "test_truncate_at_8kb"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    local big; big="$(printf 'X%.0s' $(seq 1 12000))"
    printf '%s' "${big}" > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s4","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "output contains truncation marker" '[[ "${output}" == *"truncated; Read .living/MENU.md"* ]]'
    rm -rf "${tmp}"
}

_test_skip_if_already_injected
_test_excluded_subagent_type
_test_truncate_at_8kb
```

- [ ] **Step 2: Run tests, expect 3 new failures**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: Tests 2, 3, 4 fail. Test 1 still passes.

- [ ] **Step 3: Implement the three guards in the hook**

Open `skills/core/hooks/mycelium-dispatch-injector.sh`. Between the `SESSION_ID` extraction and the `REPO_ROOT` block, add the following (replacing nothing, just inserting):

```bash
    # Guard 1: Skip if prompt already contains <mycelium-menu> (idempotency).
    PROMPT_START="$(printf '%s' "${INPUT}" | jq -r '.tool_input.prompt // "" | .[0:20]' 2>/dev/null || echo "")"
    if [[ "${PROMPT_START}" == "<mycelium-menu>"* ]]; then
        exit 0
    fi

    # Guard 2: Skip if subagent_type is in the exclusion list.
    SUBAGENT_TYPE="$(printf '%s' "${INPUT}" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")"
    case "${SUBAGENT_TYPE}" in
        living-scribe|cost-tracker|statusline-setup|figure-qa|data-qa|stats-reviewer|pdf-gen)
            exit 0
            ;;
    esac
```

Then find the `MENU_CONTENT="$(cat ...)"` line. Add truncation logic immediately after:

```bash
    MENU_CONTENT="$(cat "${REPO_ROOT}/.living/MENU.md" 2>/dev/null || echo "")"
    if [[ -z "${MENU_CONTENT}" ]]; then
        _log_error "${REPO_ROOT}" "${SESSION_ID}" "menu-empty" ""
        exit 0
    fi

    MENU_CAP_BYTES=8192
    MENU_BYTES="$(printf '%s' "${MENU_CONTENT}" | wc -c | tr -d ' ')"
    if (( MENU_BYTES > MENU_CAP_BYTES )); then
        MENU_HEAD="$(printf '%s' "${MENU_CONTENT}" | head -c "${MENU_CAP_BYTES}")"
        MENU_CONTENT="${MENU_HEAD}"$'\n'"… [truncated; Read .living/MENU.md]"
        _log_error "${REPO_ROOT}" "${SESSION_ID}" "menu-truncated" "bytes=${MENU_BYTES}"
    fi
```

- [ ] **Step 4: Run tests, expect all 4 PASS**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: all 4 test functions pass, all individual assertions pass, exit 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/mst36/tools/mycelium && \
  git add skills/core/hooks/mycelium-dispatch-injector.sh skills/core/hooks/test_dispatch_injector.sh && \
  git commit -m "feat(accessibility): dispatch-injector guards — idempotency, exclusions, truncation

Phase 1.5 Item B (continued). Three guards added:
- Skip when prompt already contains <mycelium-menu>
- Skip when subagent_type is in the exclusion list (7 types)
- Truncate MENU.md at 8KB with a marker if oversize

See docs/designs/2026-04-22-accessibility-phase1-5.md §4.4, §4.6."
```

---

## Task 5: Dispatch-injector — error-path and edge-case tests

Remaining tests from spec §4.9: no-modification-when-menu-absent, no-modification-outside-repo, error-logged-on-parse-failure, other-tool-fields-preserved. The hook already handles these correctly; this task adds verification.

**Files:**
- Modify: `skills/core/hooks/test_dispatch_injector.sh`

- [ ] **Step 1: Append the four edge-case tests**

Append to `test_dispatch_injector.sh` before the final echo line, and add four invocations:

```bash
# Test 5: no modification when MENU.md absent
_test_no_modification_when_menu_absent() {
    echo "test_no_modification_when_menu_absent"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    # No MENU.md created — walk-up-discovery finds the .living/ but not MENU.md, so bails
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s5","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when no menu" '[[ -z "${output}" ]]'
    rm -rf "${tmp}"
}

# Test 6: no modification outside a .living-enabled repo
_test_no_modification_outside_repo() {
    echo "test_no_modification_outside_repo"
    local tmp; tmp="$(mktemp -d)"
    # No .living/ directory anywhere above tmp
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s6","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output outside repo" '[[ -z "${output}" ]]'
    rm -rf "${tmp}"
}

# Test 7: error logged when jq-modify fails on malformed-but-discoverable input
# Note: truly malformed stdin (non-JSON) exits early before discovering REPO_ROOT,
# so we simulate a jq-modify failure by providing a valid-JSON-but-nonsense tool_input.
_test_error_logged_on_jq_failure() {
    echo "test_error_logged_on_jq_failure"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living" "${tmp}/.claude"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    # tool_input is an array (not an object) — .prompt = ... will fail
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s7","cwd":$cwd,"tool_name":"Agent","tool_input":["unexpected","array"]}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output on jq failure" '[[ -z "${output}" ]]'
    _assert "error log contains jq-modify-failed row" 'grep -q "jq-modify-failed" "${tmp}/.claude/mycelium-hook-errors.log"'
    _assert "error log row references session_id" 'grep -q "s7" "${tmp}/.claude/mycelium-hook-errors.log"'
    rm -rf "${tmp}"
}

# Test 8: other tool_input fields preserved (superset)
_test_other_tool_fields_preserved() {
    echo "test_other_tool_fields_preserved"
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    # Include a synthetic future-field to verify forward-compat
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s8","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig","future_field_xyz":"keep-me"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "unknown field preserved verbatim" '[[ "${output}" == *"future_field_xyz"* ]]'
    _assert "unknown field value preserved" '[[ "${output}" == *"keep-me"* ]]'
    rm -rf "${tmp}"
}

_test_no_modification_when_menu_absent
_test_no_modification_outside_repo
_test_error_logged_on_jq_failure
_test_other_tool_fields_preserved
```

- [ ] **Step 2: Run the full test harness — all 8 tests should pass**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: 8 tests run, 0 failures, exit 0. If `test_other_tool_fields_preserved` fails, audit the `jq` expression in the hook — it should use `.tool_input | .prompt = ...` to preserve all siblings, NOT construct a fresh object.

- [ ] **Step 3: Commit**

```bash
cd /Users/mst36/tools/mycelium && \
  git add skills/core/hooks/test_dispatch_injector.sh && \
  git commit -m "test(accessibility): edge-case coverage for dispatch-injector

Phase 1.5. Four additional integration tests: missing menu, outside repo,
malformed stdin, unknown tool_input field preservation. 8/8 passing.

See docs/designs/2026-04-22-accessibility-phase1-5.md §4.9."
```

---

## Task 6: Register dispatch-injector in KG canary settings

Activates the hook in the live canary. Until this task runs, the hook exists but fires nowhere. After this task, every Agent dispatch inside `Scientific Claims Knowledge Graph/` invokes the hook.

**Files:**
- Modify: `/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json`

- [ ] **Step 1: Read the current settings.local.json PreToolUse block**

```bash
jq '.hooks.PreToolUse' \
  "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json"
```

Expected: array of 3 objects — matchers `Write|Edit`, `Bash`, `Edit|Write|MultiEdit`. The new entry becomes the 4th.

- [ ] **Step 2: Add the Agent matcher block (jq with backup)**

Use `jq` to atomically append the new hook registration, with a pre/post JSON-validity guard and a backup:

```bash
SETTINGS="/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json"

# Pre-flight: validate current JSON
jq -e . "${SETTINGS}" > /dev/null || { echo "settings.local.json is not valid JSON — ABORT"; exit 1; }

# Backup
cp "${SETTINGS}" "${SETTINGS}.bak"

# Append new matcher block
jq '.hooks.PreToolUse += [{"matcher": "Agent", "hooks": [{"type": "command", "command": "/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-dispatch-injector.sh"}]}]' \
  "${SETTINGS}" > "${SETTINGS}.tmp"

# Post-validate the new file
if jq -e . "${SETTINGS}.tmp" > /dev/null; then
    mv "${SETTINGS}.tmp" "${SETTINGS}"
    rm "${SETTINGS}.bak"
    echo "registration applied"
else
    mv "${SETTINGS}.bak" "${SETTINGS}"
    rm -f "${SETTINGS}.tmp"
    echo "post-edit JSON invalid — ROLLED BACK"
    exit 1
fi
```

After the edit, verify:

```bash
jq -e '.hooks.PreToolUse | length == 4' \
  "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json"
jq -e '.hooks.PreToolUse[3].matcher == "Agent"' \
  "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json"
jq -e '.hooks.PreToolUse[3].hooks[0].command | endswith("/mycelium-dispatch-injector.sh")' \
  "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/.claude/settings.local.json"
```

Expected: all three commands exit 0.

- [ ] **Step 3: Live smoke test — dispatch a subagent inside the KG project**

**This step is manual** — it requires a live Claude Code session in the canary project. Document the verification procedure in the commit message and audit worksheet (Task 7).

Procedure (for the auditor to run post-deployment):
1. Start a Claude Code session with `cwd = /Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph`.
2. Ask Claude to dispatch any subagent (e.g., "use the Explore agent to find all Python files in Graph Construction/").
3. In the subagent's first turn, verify the initial context contains `<mycelium-menu>` followed by the MENU.md content.
4. Record the outcome in `todo/accessibility-phase1-audit.md` under the new delivery-check rows (added in Task 7).

Until Step 3 verification happens live, the hook is registered but unverified end-to-end. That's acceptable for commit — the unit/integration tests in Tasks 3-5 already cover the hook's behavior in isolation.

- [ ] **Step 4: Commit the canary registration**

```bash
cd "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph" && \
  git add .claude/settings.local.json && \
  git commit -m "chore(canary): register mycelium-dispatch-injector on Agent matcher

Phase 1.5 Item B activation. PreToolUse hook fires on every Agent
dispatch inside the KG project, prepending <mycelium-menu> to the
dispatched prompt so subagents see L1 routing surface.

See mycelium docs/designs/2026-04-22-accessibility-phase1-5.md."
```

Note: this commit lands in the Scientific Claims Knowledge Graph repo, not the mycelium repo. The two repos are tracked separately. The mycelium-repo commits from Tasks 1-5 stand on their own.

---

## Task 7: Update audit worksheet with Phase 1.5 deliverables

Adds the two delivery-check rows from spec §5.1 and the diagnostic-signal rows from §5.2 to the audit worksheet. Marks the diagnostic section explicitly as NOT a go/no-go input.

**Files:**
- Modify: `/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/todo/accessibility-phase1-audit.md`

- [ ] **Step 1: Read the current worksheet**

```bash
cat "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph/todo/accessibility-phase1-audit.md"
```

Confirm the existing delivery-checks section exists at the top of the file (4 bullet items ending with hook error rate).

- [ ] **Step 2: Add two new delivery-check rows**

Open the worksheet and modify the "Delivery checks (objective)" section. After the existing 4 bullets, add:

```markdown
- [ ] ≥95% of sampled subagent-dispatch events contain `<mycelium-menu>` at the top of `tool_input.prompt` (Phase 1.5)
- [ ] Error rate from `dispatch-injector` in `.claude/mycelium-hook-errors.log` < 1% of Agent-dispatch fires (Phase 1.5)
```

- [ ] **Step 3: Add the diagnostic signal section**

After the existing "Precision review" section and before "Go/no-go", insert a new section:

```markdown
## Diagnostic signal (NOT a go/no-go input) — menu-to-pull conversion

Per Phase 1.5 spec §5.2: because subagents receive only the menu (not pushed entries), this signal isolates progressive-disclosure performance from the L3 safety net. This section informs Phase 2 prioritization; it does NOT alter the Phase 1 architecture pass/fail decision.

For each subagent session in the 10-session sample that received `<mycelium-menu>`:

| # | Session UUID | Dispatched task summary | Pulled ≥1 learnings/*.md? (y/n) | If yes: domain-relevant to task? (relevant/partial/irrelevant) |
|---|---|---|---|---|
| 1 |   |   |   |   |
| 2 |   |   |   |   |
| 3 |   |   |   |   |

**Pull rate**: __ / __ sessions = __%
**Relevance rate** (pulls with relevant domain / pulls): __ / __ = __%

If pull rate is below 40% AND Phase 1 go/no-go otherwise passes: file a Phase 2 advancement for subagent-aware push.
```

- [ ] **Step 4: Commit**

```bash
cd "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph" && \
  git add todo/accessibility-phase1-audit.md && \
  git commit -m "docs(audit): add Phase 1.5 delivery checks and diagnostic signal section

Adds two objective delivery checks for subagent menu injection and
hook error rate. Adds a separate diagnostic-signal section for
menu-to-pull conversion — explicitly NOT a go/no-go input.

See mycelium docs/designs/2026-04-22-accessibility-phase1-5.md §5."
```

---

## Task 8: Final regression + summary

End-of-plan checkpoint. Confirms the full test suite is green, counts commits, and records the handoff state.

- [ ] **Step 1: Run the full Python test suite**

```bash
cd /Users/mst36/tools/mycelium && \
  python -m pytest skills/core/scripts/ -v --tb=short
```

Expected: 107/107 pass (105 Phase 1 + 2 new in Task 1).

- [ ] **Step 2: Run the full dispatch-injector test harness**

```bash
bash /Users/mst36/tools/mycelium/skills/core/hooks/test_dispatch_injector.sh
```

Expected: 8/8 tests pass.

- [ ] **Step 3: Smoke-run mycelium-health.sh** (regression guard for Task 2's refactor)

```bash
cd /Users/mst36/tools/mycelium && \
  echo '{"session_id":"final-smoke","cwd":"/tmp"}' | \
  bash skills/core/hooks/mycelium-health.sh
```

Expected: exits 0, no `_mycelium_rotate_error_log: not found` errors.

- [ ] **Step 3a: Run the Phase 1 edit-injector integration test** (regression guard — the edit-injector does not directly source the shared helper today, but verifies Phase 1 contracts are intact)

```bash
cd /Users/mst36/tools/mycelium && \
  bash skills/core/hooks/test_phase1_integration.sh
```

Expected: all existing Phase 1 integration assertions pass. If any fail, investigate before concluding Phase 1.5 — the scorer change in Task 1 should not affect this suite, but the refactor in Task 2 could if the log-rotation helper gets introduced to edit-injector in a follow-up.

- [ ] **Step 4: Count new commits on the branch**

```bash
cd /Users/mst36/tools/mycelium && \
  git log --oneline main..feat/accessibility-phase1 | wc -l
```

Expected: ≥ 32 (27 pre-Phase-1.5 + 5 Phase 1.5 mycelium-repo commits from Tasks 1-5). The two canary-repo commits from Tasks 6-7 live in a different tree.

- [ ] **Step 5: No commit for this task — it's a verification checkpoint.**

---

## Plan Summary

| Task | What it delivers | Files changed | Tests affected |
|------|------------------|---------------|----------------|
| 1 | Triggers scorer +3 | match_edit.py, test_match_edit.py | 1 modified, 2 new |
| 2 | Extracted log-rotation helper | mycelium-health.sh, new `_lib/log_rotation.sh` | 0 |
| 3 | Dispatch-injector skeleton + menu prepend | new mycelium-dispatch-injector.sh, new test_dispatch_injector.sh | 1 new (5 assertions) |
| 4 | Dispatch-injector guards | mycelium-dispatch-injector.sh, test_dispatch_injector.sh | 3 new |
| 5 | Dispatch-injector edge-case tests | test_dispatch_injector.sh | 4 new |
| 6 | KG canary registration | Scientific Claims KG/.claude/settings.local.json | 0 (manual live smoke) |
| 7 | Audit worksheet update | Scientific Claims KG/todo/accessibility-phase1-audit.md | 0 |
| 8 | Final regression checkpoint | — | 107 python + 8 bash |

**Commits**: 5 in mycelium repo + 2 in KG canary repo = 7 total.

**Rollback**: Task 1 is a single-line revert. Task 2 is a 2-file revert (mycelium-health.sh section + delete `_lib/`). Tasks 3-5 are "delete the new files." Task 6 is restoration from `.bak` (Step 2 creates one). Task 7 is a doc-only revert. See spec §6.2.

**Constraint (spec §7.2.3): MENU.md format is frozen during Phase 1.5.** Phase 1 set the format (one line per domain with description). Do not change `generate_menu.py` output shape, add new frontmatter fields to `learnings/*.md`, or otherwise perturb the L1 content format while Phase 1.5 is in flight. The hook's 8 KB truncation cap in Task 4 is the only MENU.md size-related behavior introduced by Phase 1.5.
