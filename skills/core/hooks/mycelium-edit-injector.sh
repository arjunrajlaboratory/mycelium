#!/usr/bin/env bash
# mycelium-edit-injector.sh — Claude Code PreToolUse hook (Edit|Write|MultiEdit)
# Resolves the edit path, de-dups per session, and pushes relevance-ranked
# learnings from .living/learnings/*.md as additionalContext.
#
# See docs/designs/2026-04-22-accessibility-architecture.md §6.2 for the full contract.

set -euo pipefail

SCRIPT_VERSION="phase1-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATCH_EDIT="${SCRIPT_DIR}/../scripts/match_edit.py"
# shellcheck source=_lib/log_rotation.sh
source "${SCRIPT_DIR}/_lib/log_rotation.sh"

_log_error() {
    local repo_root="$1"; shift
    local tool_name="$1"; shift
    local file_path="$1"; shift
    local session_id="$1"; shift
    local error_class="$1"; shift
    local matched_domains="$1"; shift
    local bytes="$1"; shift
    local errlog="${repo_root}/.claude/mycelium-hook-errors.log"
    mkdir -p "$(dirname "${errlog}")" 2>/dev/null || true
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date +%Y-%m-%dT%H:%M:%S)" "${tool_name}" "${file_path}" \
        "${repo_root}" "${session_id}" "${error_class}" "${matched_domains}" \
        "${bytes}" "${SCRIPT_VERSION}" >> "${errlog}" 2>/dev/null || true
}

_log_event() {
    local repo_root="$1"
    local tool_name="$2"
    local target="$3"
    local session_id="$4"
    local outcome="$5"   # fired | dedup-skip
    local detail="$6"    # matched;bytes=N | empty

    [[ -z "${repo_root}" ]] && return 0
    local logfile="${repo_root}/.claude/mycelium-injection-events.log"

    local s_target s_session s_detail
    s_target="$(_mycelium_tsv_sanitize "${target}")"
    s_session="$(_mycelium_tsv_sanitize "${session_id}")"
    s_detail="$(_mycelium_tsv_sanitize "${detail}")"

    local line
    line="$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$(date +%Y-%m-%dT%H:%M:%S)" \
        "edit-injector" \
        "${tool_name}" \
        "${s_target}" \
        "${s_session}" \
        "${outcome}" \
        "${s_detail}" \
        "phase1.5-events-v1")"

    _mycelium_append_log_locked "${logfile}" "${line}"
}

{
    INPUT="$(cat)"

    # Step 1: Tool-specific payload extraction
    TOOL_NAME="$(printf '%s' "${INPUT}" | jq -r '.tool_name // empty' 2>/dev/null)"
    FILE_PATH="$(printf '%s' "${INPUT}" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
    SESSION_ID="$(printf '%s' "${INPUT}" | jq -r '.session_id // empty' 2>/dev/null)"

    if [[ -z "${SESSION_ID}" ]]; then
        SESSION_ID="ppid-${PPID}-$(hostname -s 2>/dev/null || echo host)"
    fi

    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"

    if [[ -z "${FILE_PATH}" ]]; then
        [[ -n "${REPO_ROOT}" ]] && _log_error "${REPO_ROOT}" "${TOOL_NAME}" "" "${SESSION_ID}" "missing-file-path" "" "0"
        exit 0
    fi

    if [[ -z "${REPO_ROOT}" || ! -d "${REPO_ROOT}/.living" ]]; then
        exit 0
    fi

    # Step 2: Path resolution (fail-fast)
    if [[ "${FILE_PATH}" != /* ]]; then
        RESOLVED="${REPO_ROOT}/${FILE_PATH}"
    else
        RESOLVED="${FILE_PATH}"
    fi
    RESOLVED="$(realpath -m "${RESOLVED}" 2>/dev/null || echo "${RESOLVED}")"

    # Step 2a: repo containment
    case "${RESOLVED}" in
        "${REPO_ROOT}"/*) ;;
        *)
            _log_error "${REPO_ROOT}" "${TOOL_NAME}" "${FILE_PATH}" "${SESSION_ID}" "path-outside-repo" "" "0"
            exit 0
            ;;
    esac

    REL_PATH="${RESOLVED#${REPO_ROOT}/}"

    # Step 4: De-dup check
    DEDUP_FILE="${REPO_ROOT}/.claude/mycelium-injection-dedup.tmp"
    mkdir -p "$(dirname "${DEDUP_FILE}")" 2>/dev/null || true
    touch "${DEDUP_FILE}"
    NOW_EPOCH="$(date +%s)"
    TTL=600
    HASH="$(printf '%s' "${SESSION_ID}||${REL_PATH}" | shasum | awk '{print $1}')"

    (
        if command -v flock >/dev/null 2>&1; then
            exec 9>"${DEDUP_FILE}.lock"
            flock 9
        fi
        if [[ -s "${DEDUP_FILE}" ]]; then
            awk -v now="${NOW_EPOCH}" -v ttl="${TTL}" '
                { if (now - $2 < ttl) print $0 }
            ' "${DEDUP_FILE}" > "${DEDUP_FILE}.clean"
            mv "${DEDUP_FILE}.clean" "${DEDUP_FILE}"
        fi
        if grep -qF "	${HASH}" "${DEDUP_FILE}" 2>/dev/null; then
            echo "__DEDUP_SKIP__"
        else
            printf '%s\t%s\t%s\n' "${SESSION_ID}" "${NOW_EPOCH}" "${HASH}" >> "${DEDUP_FILE}"
        fi
    ) > /tmp/mycelium-dedup-result.$$ 2>/dev/null
    DEDUP_RESULT="$(cat /tmp/mycelium-dedup-result.$$ 2>/dev/null || echo "")"
    rm -f /tmp/mycelium-dedup-result.$$

    if [[ "${DEDUP_RESULT}" == *"__DEDUP_SKIP__"* ]]; then
        _log_event "${REPO_ROOT}" "${TOOL_NAME}" "${REL_PATH}" "${SESSION_ID}" "dedup-skip" ""
        exit 0
    fi

    # Step 5: Invoke match_edit.py
    RESULT="$(python3 "${MATCH_EDIT}" "${REL_PATH}" --living-dir "${REPO_ROOT}/.living" 2>/dev/null)"
    if [[ -z "${RESULT}" ]]; then
        _log_error "${REPO_ROOT}" "${TOOL_NAME}" "${REL_PATH}" "${SESSION_ID}" "match-edit-empty-output" "" "0"
        exit 0
    fi

    # Step 6: Extract rendered content; skip if entries empty
    ENTRY_COUNT="$(printf '%s' "${RESULT}" | jq '.entries | length' 2>/dev/null || echo 0)"
    if [[ "${ENTRY_COUNT}" -eq 0 ]]; then
        exit 0
    fi

    CONTENT="$(printf '%s' "${RESULT}" | jq -r '.entries | map(.content) | join("\n\n")' 2>/dev/null)"
    MATCHED="$(printf '%s' "${RESULT}" | jq -r '.matched_domains | join(",")' 2>/dev/null)"

    WRAPPED="<mycelium-pushed-learnings domains=\"${MATCHED}\">
${CONTENT}
</mycelium-pushed-learnings>"

    PAYLOAD_BYTES="$(printf '%s' "${WRAPPED}" | wc -c | tr -d ' ')"
    _log_event "${REPO_ROOT}" "${TOOL_NAME}" "${REL_PATH}" "${SESSION_ID}" "fired" "${MATCHED};bytes=${PAYLOAD_BYTES}"

    jq -nc --arg ctx "${WRAPPED}" \
        '{"hookSpecificOutput": {"additionalContext": $ctx}}'

} 2>/dev/null || true

exit 0
