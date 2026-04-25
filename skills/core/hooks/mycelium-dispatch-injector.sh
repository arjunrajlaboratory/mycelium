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
    while [[ -n "${d}" && "${d}" != "/" ]]; do
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

_log_event() {
    local repo_root="$1"
    local target="$2"        # subagent_type value or "unknown"
    local session_id="$3"
    local outcome="$4"       # fired | dedup-skip | excluded-skip
    local detail="$5"        # bytes=N | subagent_type=X | empty

    [[ -z "${repo_root}" ]] && return 0
    local logfile="${repo_root}/.claude/mycelium-injection-events.log"

    local s_target s_session s_detail
    s_target="$(_mycelium_tsv_sanitize "${target}")"
    s_session="$(_mycelium_tsv_sanitize "${session_id}")"
    s_detail="$(_mycelium_tsv_sanitize "${detail}")"

    local line
    line="$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$(date +%Y-%m-%dT%H:%M:%S)" \
        "dispatch-injector" \
        "Agent" \
        "${s_target}" \
        "${s_session}" \
        "${outcome}" \
        "${s_detail}" \
        "phase1.5-events-v1")"

    _mycelium_append_log_locked "${logfile}" "${line}"
}

{
    INPUT="$(cat)"
    SESSION_ID="$(printf '%s' "${INPUT}" | jq -r '.session_id // empty' 2>/dev/null || echo "")"
    SUBAGENT_TYPE="$(printf '%s' "${INPUT}" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")"
    [[ -z "${SUBAGENT_TYPE}" ]] && SUBAGENT_TYPE="unknown"

    # Discover repo root FIRST so guard outcomes can be logged.
    CWD="$(printf '%s' "${INPUT}" | jq -r '.cwd // empty' 2>/dev/null || echo "")"
    [[ -z "${CWD}" ]] && CWD="$(pwd)"
    REPO_ROOT="$(_find_repo_root "${CWD}")"
    if [[ -z "${REPO_ROOT}" ]]; then
        # No log target — silent exit, matches Phase 1.5 spec.
        exit 0
    fi

    # Guard 1: Idempotency — skip if prompt already contains <mycelium-menu>.
    PROMPT_START="$(printf '%s' "${INPUT}" | jq -r '.tool_input.prompt // "" | .[0:20]' 2>/dev/null || echo "")"
    if [[ "${PROMPT_START}" == "<mycelium-menu>"* ]]; then
        _log_event "${REPO_ROOT}" "${SUBAGENT_TYPE}" "${SESSION_ID}" "dedup-skip" "" 2>/dev/null || true
        exit 0
    fi

    # Guard 2: Excluded subagent types.
    case "${SUBAGENT_TYPE}" in
        living-scribe|cost-tracker|statusline-setup|figure-qa|data-qa|stats-reviewer|pdf-gen)
            _log_event "${REPO_ROOT}" "${SUBAGENT_TYPE}" "${SESSION_ID}" "excluded-skip" "subagent_type=${SUBAGENT_TYPE}" 2>/dev/null || true
            exit 0
            ;;
    esac

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

    # Echo entire tool_input, modify only .prompt. Preserves all sibling keys.
    MODIFIED_INPUT="$(printf '%s' "${INPUT}" | jq -c \
        --arg menu "${MENU_CONTENT}" \
        '.tool_input | .prompt = ("<mycelium-menu>\n" + $menu + "\n</mycelium-menu>\n\n" + (.prompt // ""))' \
        2>/dev/null)"

    if [[ -z "${MODIFIED_INPUT}" ]]; then
        _log_error "${REPO_ROOT}" "${SESSION_ID}" "jq-modify-failed" ""
        exit 0
    fi

    MENU_FINAL_BYTES="$(printf '%s' "${MENU_CONTENT}" | wc -c | tr -d ' ')"
    _log_event "${REPO_ROOT}" "${SUBAGENT_TYPE}" "${SESSION_ID}" "fired" "bytes=${MENU_FINAL_BYTES}" 2>/dev/null || true

    jq -nc --argjson inp "${MODIFIED_INPUT}" \
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": $inp}}'

} 2>/dev/null || true

exit 0
