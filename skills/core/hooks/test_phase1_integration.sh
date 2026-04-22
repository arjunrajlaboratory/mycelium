#!/usr/bin/env bash
# Integration tests for mycelium-edit-injector.sh
set -euo pipefail

HOOK="/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-edit-injector.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

cd "${TMP}"
git init -q
mkdir -p .living/learnings .claude
cat > .living/learnings/figures.md <<'EOF'
---
domain: figures
description: color and DPI
push_active: true
matches:
  - "**/figures/**"
  - "**/panel_*.py"
---
## [2026-04-22] Colorblind palette required for panel figures
**Tags**: panel, figures, colorblind
Use viridis or the 7-color palette for all panel figures.
EOF

PASS=0
FAIL=0
assert() {
    if eval "$1"; then
        echo "PASS: $2"; PASS=$((PASS+1))
    else
        echo "FAIL: $2"; FAIL=$((FAIL+1))
    fi
}

# Test 1: Edit to matching path emits additionalContext
OUT1=$(echo '{"tool_name":"Edit","tool_input":{"file_path":"docs/figures/panel_b.py"},"session_id":"s1"}' | "${HOOK}")
assert "[[ '${OUT1}' == *'mycelium-pushed-learnings'* ]]" "Edit payload triggers injection"
assert "[[ '${OUT1}' == *'Colorblind palette'* ]]" "Injected content includes entry title"

# Test 2: Write to matching path
OUT2=$(echo '{"tool_name":"Write","tool_input":{"file_path":"docs/figures/new_panel.py","content":"..."},"session_id":"s2"}' | "${HOOK}")
assert "[[ '${OUT2}' == *'mycelium-pushed-learnings'* ]]" "Write payload triggers injection"

# Test 3: MultiEdit to matching path
OUT3=$(echo '{"tool_name":"MultiEdit","tool_input":{"file_path":"docs/figures/panel_c.py","edits":[]},"session_id":"s3"}' | "${HOOK}")
assert "[[ '${OUT3}' == *'mycelium-pushed-learnings'* ]]" "MultiEdit payload triggers injection"

# Test 4: Edit to non-matching path → empty output
OUT4=$(echo '{"tool_name":"Edit","tool_input":{"file_path":"src/unrelated.py"},"session_id":"s4"}' | "${HOOK}")
assert "[[ -z '${OUT4}' ]]" "Non-matching path emits nothing"

# Test 5: Missing file_path → logs error, exits 0
OUT5=$(echo '{"tool_name":"Edit","tool_input":{},"session_id":"s5"}' | "${HOOK}" 2>&1)
assert "[[ -z '${OUT5}' ]]" "Missing file_path produces no output"
assert "[[ -f .claude/mycelium-hook-errors.log && \$(grep -c missing-file-path .claude/mycelium-hook-errors.log) -ge 1 ]]" \
    "Missing file_path is logged"

# Test 6: Dedup — identical second fire within TTL emits nothing
OUT6A=$(echo '{"tool_name":"Edit","tool_input":{"file_path":"docs/figures/panel_b.py"},"session_id":"s-dedup"}' | "${HOOK}")
OUT6B=$(echo '{"tool_name":"Edit","tool_input":{"file_path":"docs/figures/panel_b.py"},"session_id":"s-dedup"}' | "${HOOK}")
assert "[[ '${OUT6A}' == *'mycelium-pushed-learnings'* ]]" "First dedup-window fire emits"
assert "[[ -z '${OUT6B}' ]]" "Second dedup-window fire suppressed"

# Test 7: path-outside-repo rejected
mkdir -p /tmp/mycelium-outside && echo > /tmp/mycelium-outside/foo.py
OUT7=$(echo '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/mycelium-outside/foo.py"},"session_id":"s7"}' | "${HOOK}" 2>&1)
assert "[[ -z '${OUT7}' ]]" "Outside-repo path emits nothing"
assert "[[ \$(grep -c path-outside-repo .claude/mycelium-hook-errors.log) -ge 1 ]]" "Outside-repo logged"
rm -rf /tmp/mycelium-outside

echo
echo "Integration: ${PASS} passed, ${FAIL} failed"
exit "${FAIL}"
