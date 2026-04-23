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
