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

echo ""
echo "Ran ${TESTS_RUN}, failed ${TESTS_FAILED}"
[[ ${TESTS_FAILED} -eq 0 ]]
