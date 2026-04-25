#!/usr/bin/env bash
# test_edit_injector.sh — events-log assertions for edit-injector.

set -u
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${HOOK_DIR}/mycelium-edit-injector.sh"
PASSED=0
FAILED=0

_assert() {
    local label="$1"; shift
    if eval "$@"; then
        printf 'PASS %s\n' "${label}"
        PASSED=$((PASSED + 1))
    else
        printf 'FAIL %s\n' "${label}"
        FAILED=$((FAILED + 1))
    fi
}

_setup_repo() {
    local tmp="$1"
    git init -q "${tmp}"
    mkdir -p "${tmp}/.living/learnings" "${tmp}/.claude" "${tmp}/Claim Extraction"
    cat > "${tmp}/.living/learnings/extraction.md" <<'EOF'
---
domain: extraction
description: test
push_active: true
matches:
  - "**/Claim Extraction/**"
---
## [2026-04-25] test entry
**Tags**: test, extraction
**Triggers**: extraction, test
- minimal body
EOF
    touch "${tmp}/Claim Extraction/scratch_x.py"
}

_run_fire_test() {
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' RETURN
    _setup_repo "${tmp}"
    local resolved_root; resolved_root="$(cd "${tmp}" && pwd -P)"
    cd "${resolved_root}" || return 1
    # Use relative file_path to sidestep /tmp vs /private/tmp canonicalization (Codex #12)
    local INPUT
    INPUT='{"session_id":"sess-1","tool_name":"Edit","tool_input":{"file_path":"Claim Extraction/scratch_x.py"}}'

    echo "${INPUT}" | "${HOOK}" >/dev/null 2>&1
    # Use awk -F'\t' for portable literal-tab matching (Codex rev 2 review NEW finding #1):
    # POSIX grep does not guarantee \t in -E patterns matches a literal tab.
    _assert "fire writes events-log row with correct fields" \
        'awk -F"\t" -v p="Claim Extraction/scratch_x.py" '"'"'$2=="edit-injector" && $3=="Edit" && $4==p && $5=="sess-1" && $6=="fired"'"'"' "'"${resolved_root}"'/.claude/mycelium-injection-events.log" | grep -q .'

    # Second fire within TTL → dedup-skip row
    echo "${INPUT}" | "${HOOK}" >/dev/null 2>&1
    _assert "dedup-skip writes events-log row" \
        'awk -F"\t" -v p="Claim Extraction/scratch_x.py" '"'"'$2=="edit-injector" && $3=="Edit" && $4==p && $5=="sess-1" && $6=="dedup-skip"'"'"' "'"${resolved_root}"'/.claude/mycelium-injection-events.log" | grep -q .'
}

_run_fire_test
printf '\n%d passed, %d failed\n' "${PASSED}" "${FAILED}"
[ "${FAILED}" -eq 0 ]
