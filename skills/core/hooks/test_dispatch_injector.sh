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
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    printf '# Mycelium Menu\n\n- figures — color, DPI\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s1","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"original"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "output contains <mycelium-menu>" '[[ "${output}" == *"<mycelium-menu>"* ]]'
    _assert "output preserves original prompt" '[[ "${output}" == *"original"* ]]'
    _assert "output has permissionDecision allow" '[[ "${output}" == *"allow"* ]]'
    _assert "output preserves description" '[[ "${output}" == *"\"description\":\"d\""* ]]'
    _assert "output preserves subagent_type" '[[ "${output}" == *"general-purpose"* ]]'
}

_test_menu_prepended_when_present

# Test 2: skip when prompt already contains <mycelium-menu>
_test_skip_if_already_injected() {
    echo "test_skip_if_already_injected"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s2","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"<mycelium-menu>\nalready here\n</mycelium-menu>\n\noriginal"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when already injected" '[[ -z "${output}" ]]'
}

# Test 3: skip when subagent_type in exclusion list
_test_excluded_subagent_type() {
    echo "test_excluded_subagent_type"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s3","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"cost-tracker","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when subagent excluded" '[[ -z "${output}" ]]'
}

# Test 4: truncate when MENU.md > 8KB
_test_truncate_at_8kb() {
    echo "test_truncate_at_8kb"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    local big; big="$(printf 'X%.0s' $(seq 1 12000))"
    printf '%s' "${big}" > "${tmp}/.living/MENU.md"
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s4","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "output contains truncation marker" '[[ "${output}" == *"truncated; Read .living/MENU.md"* ]]'
}

_test_skip_if_already_injected
_test_excluded_subagent_type
_test_truncate_at_8kb

# Test 5: no modification when MENU.md absent
_test_no_modification_when_menu_absent() {
    echo "test_no_modification_when_menu_absent"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    # No MENU.md created — walk-up-discovery finds the .living/ but not MENU.md, so bails
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s5","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output when no menu" '[[ -z "${output}" ]]'
}

# Test 6: no modification outside a .living-enabled repo
_test_no_modification_outside_repo() {
    echo "test_no_modification_outside_repo"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    # No .living/ directory anywhere above tmp
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s6","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output outside repo" '[[ -z "${output}" ]]'
}

# Test 7: error logged when jq-modify fails on malformed-but-discoverable input
# Note: truly malformed stdin (non-JSON) exits early before discovering REPO_ROOT,
# so we simulate a jq-modify failure by providing a valid-JSON-but-nonsense tool_input.
_test_error_logged_on_jq_failure() {
    echo "test_error_logged_on_jq_failure"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living" "${tmp}/.claude"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    # tool_input is an array (not an object) — .prompt = ... will fail
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s7","cwd":$cwd,"tool_name":"Agent","tool_input":["unexpected","array"]}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "empty output on jq failure" '[[ -z "${output}" ]]'
    _assert "error log contains jq-modify-failed row" 'grep -q "jq-modify-failed" "${tmp}/.claude/mycelium-hook-errors.log"'
    _assert "error log row references session_id" 'grep -q "s7" "${tmp}/.claude/mycelium-hook-errors.log"'
}

# Test 8: other tool_input fields preserved (superset)
_test_other_tool_fields_preserved() {
    echo "test_other_tool_fields_preserved"
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    mkdir -p "${tmp}/.living"
    printf '# Menu\n- figures\n' > "${tmp}/.living/MENU.md"
    # Include a synthetic future-field to verify forward-compat
    local input; input="$(jq -nc --arg cwd "${tmp}" '{"session_id":"s8","cwd":$cwd,"tool_name":"Agent","tool_input":{"description":"d","subagent_type":"general-purpose","prompt":"orig","future_field_xyz":"keep-me"}}')"
    local output; output="$(printf '%s' "${input}" | bash "${HOOK}" 2>/dev/null)"
    _assert "unknown field preserved verbatim" '[[ "${output}" == *"future_field_xyz"* ]]'
    _assert "unknown field value preserved" '[[ "${output}" == *"keep-me"* ]]'
}

_test_no_modification_when_menu_absent
_test_no_modification_outside_repo
_test_error_logged_on_jq_failure
_test_other_tool_fields_preserved

echo ""
echo "Ran ${TESTS_RUN}, failed ${TESTS_FAILED}"
[[ ${TESTS_FAILED} -eq 0 ]]
