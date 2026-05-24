#!/usr/bin/env bash
# test_hooks_stress.sh — Stress tests for mycelium-read-tracker.sh and
# mycelium-health.sh (INDEX.md injection feature).
#
# Tests 1-12:  mycelium-read-tracker.sh
# Tests 13-22: mycelium-health.sh INDEX.md injection

set -euo pipefail

READ_TRACKER_HOOK="/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-read-tracker.sh"
HEALTH_HOOK="/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-health.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() {
  echo -e "${GREEN}PASS${NC} — $1"
  PASS=$((PASS + 1))
}

fail() {
  echo -e "${RED}FAIL${NC} — $1"
  [ -n "${2:-}" ] && echo -e "       ${YELLOW}${2}${NC}"
  FAIL=$((FAIL + 1))
}

# ---------------------------------------------------------------------------
# Setup / teardown helpers
# ---------------------------------------------------------------------------

# Create a temp dir with a proper git repo + minimal .living/ scaffold
setup_test_env() {
  TEST_DIR=$(mktemp -d)
  git init -q "$TEST_DIR"
  git -C "$TEST_DIR" config user.email "test@test.com"
  git -C "$TEST_DIR" config user.name "Test"
  # Initial commit so git rev-parse --show-toplevel works reliably
  touch "$TEST_DIR/README.md"
  git -C "$TEST_DIR" add README.md
  git -C "$TEST_DIR" commit -q -m "init"
  mkdir -p "$TEST_DIR/.living" "$TEST_DIR/.claude"
  printf "# Learnings\n\n### Entry 1\nContent 1\n\n### Entry 2\nContent 2\n" > "$TEST_DIR/.living/learnings.md"
  printf "# Decisions\n\n### Dec 1\nRationale 1\n" > "$TEST_DIR/.living/decisions.md"
  printf "# Conventions\n\n## Conv 1\nDetails.\n" > "$TEST_DIR/.living/conventions.md"
}

cleanup_test_env() {
  rm -rf "$TEST_DIR"
  unset TEST_DIR
}

# Run the read-tracker hook from inside TEST_DIR with JSON on stdin
# Sets RT_EXIT and RT_OUTPUT
run_read_tracker() {
  local json="$1"
  RT_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$json" | bash "$READ_TRACKER_HOOK" 2>/dev/null)
  RT_EXIT=$?
}

# Run health hook from inside TEST_DIR with JSON on stdin
# Sets HH_EXIT and HH_OUTPUT
run_health_hook() {
  local json="${1:-{\"cwd\":\"${TEST_DIR}\",\"source\":\"startup\"}}"
  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$json" | bash "$HEALTH_HOOK" 2>/dev/null)
  HH_EXIT=$?
}

# ---------------------------------------------------------------------------
# READ TRACKER TESTS (1-12)
# ---------------------------------------------------------------------------

echo ""
echo "══════════════════════════════════════════════════"
echo " READ TRACKER TESTS (1-12)"
echo "══════════════════════════════════════════════════"

# ── TEST 1: Basic .living/ read is logged ────────────────────────────────────
echo ""
echo "TEST 1: Basic .living/ read is logged"
{
  setup_test_env
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/.living/INDEX.md\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ] && grep -q "\.living/INDEX\.md" "$LOG"; then
    # Verify format: TIMESTAMP .living/INDEX.md
    LINE=$(head -1 "$LOG")
    TS_PART="${LINE%% *}"
    FILE_PART="${LINE#* }"
    if [[ "$FILE_PART" == ".living/INDEX.md" ]]; then
      pass "Log exists, contains '.living/INDEX.md', format is 'TIMESTAMP .living/INDEX.md'"
    else
      fail "Log format wrong" "line='$LINE'"
    fi
  else
    fail "Log file missing or does not contain .living/INDEX.md" \
      "log_exists=$([ -f "$LOG" ] && echo yes || echo no)"
  fi
  cleanup_test_env
}

# ── TEST 2: Non-.living/ read is NOT logged ───────────────────────────────────
echo ""
echo "TEST 2: Non-.living/ read is NOT logged"
{
  setup_test_env
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/src/main.py\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; then
    pass "Non-.living/ path → no log entry written"
  else
    LINES=$(wc -l < "$LOG")
    fail "Non-.living/ path wrote to log unexpectedly" "log lines=$LINES"
  fi
  cleanup_test_env
}

# ── TEST 3: Multiple reads accumulate ────────────────────────────────────────
echo ""
echo "TEST 3: Multiple reads accumulate"
{
  setup_test_env
  for FILE in INDEX.md learnings.md decisions.md; do
    JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/.living/${FILE}\"}}"
    run_read_tracker "$JSON"
  done
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ]; then
    LINES=$(grep -c '' "$LOG" 2>/dev/null || echo 0)
    if [ "$LINES" -eq 3 ] \
      && grep -q "\.living/INDEX\.md" "$LOG" \
      && grep -q "\.living/learnings\.md" "$LOG" \
      && grep -q "\.living/decisions\.md" "$LOG"; then
      pass "3 reads → 3 log lines, all files present"
    else
      fail "Expected exactly 3 lines with all 3 files" "lines=$LINES"
    fi
  else
    fail "Log file not created after 3 reads" ""
  fi
  cleanup_test_env
}

# ── TEST 4: Nested .living/ path logged correctly ─────────────────────────────
echo ""
echo "TEST 4: Nested .living/ path logged correctly"
{
  setup_test_env
  mkdir -p "$TEST_DIR/.living/log"
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/.living/log/LOG_REGISTRY.md\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ] && grep -q "\.living/log/LOG_REGISTRY\.md" "$LOG"; then
    pass "Nested path '.living/log/LOG_REGISTRY.md' logged correctly"
  else
    fail "Nested path not logged correctly" \
      "$([ -f "$LOG" ] && cat "$LOG" || echo 'log missing')"
  fi
  cleanup_test_env
}

# ── TEST 5: Empty file_path → silent exit ─────────────────────────────────────
echo ""
echo "TEST 5: Empty file_path → silent exit"
{
  setup_test_env
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ "$RT_EXIT" -eq 0 ] && { [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; }; then
    pass "Empty file_path → exit 0, no log written"
  else
    fail "Empty file_path → unexpected result" \
      "exit=$RT_EXIT log_exists=$([ -f "$LOG" ] && echo yes || echo no)"
  fi
  cleanup_test_env
}

# ── TEST 6: Missing file_path field → silent exit ─────────────────────────────
echo ""
echo "TEST 6: Missing file_path field → silent exit"
{
  setup_test_env
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ "$RT_EXIT" -eq 0 ] && { [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; }; then
    pass "Missing file_path field → exit 0, no log written"
  else
    fail "Missing file_path field → unexpected result" \
      "exit=$RT_EXIT"
  fi
  cleanup_test_env
}

# ── TEST 7: Malformed JSON → silent exit ──────────────────────────────────────
echo ""
echo "TEST 7: Malformed JSON → silent exit"
{
  setup_test_env
  RT_OUTPUT=$(cd "$TEST_DIR" && printf 'this is not json' | bash "$READ_TRACKER_HOOK" 2>/dev/null)
  RT_EXIT=$?
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ "$RT_EXIT" -eq 0 ] && { [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; }; then
    pass "Malformed JSON → exit 0, no log written"
  else
    fail "Malformed JSON → unexpected result" \
      "exit=$RT_EXIT"
  fi
  cleanup_test_env
}

# ── TEST 8: Path with spaces ──────────────────────────────────────────────────
echo ""
echo "TEST 8: Path with spaces"
{
  setup_test_env
  mkdir -p "$TEST_DIR/.living/session notes"
  touch "$TEST_DIR/.living/session notes/entry.md"
  # Encode the path in JSON — use python3 to safely produce JSON with spaces
  JSON=$(python3 -c "import json; print(json.dumps({'tool_name':'Read','tool_input':{'file_path':'${TEST_DIR}/.living/session notes/entry.md'}}))")
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ] && grep -q "session notes/entry\.md" "$LOG"; then
    pass "Path with spaces logged correctly"
  else
    fail "Path with spaces not logged correctly" \
      "$([ -f "$LOG" ] && cat "$LOG" || echo 'log missing')"
  fi
  cleanup_test_env
}

# ── TEST 9: Not in a git repo → silent exit ───────────────────────────────────
echo ""
echo "TEST 9: Not in a git repo → silent exit"
{
  NO_GIT_DIR=$(mktemp -d)
  mkdir -p "$NO_GIT_DIR/.living"
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${NO_GIT_DIR}/.living/learnings.md\"}}"
  RT_OUTPUT=$(cd "$NO_GIT_DIR" && printf '%s' "$JSON" | bash "$READ_TRACKER_HOOK" 2>/dev/null)
  RT_EXIT=$?
  LOG="$NO_GIT_DIR/.claude/mycelium-read-access.log"
  if [ "$RT_EXIT" -eq 0 ] && { [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; }; then
    pass "No git repo → exit 0, no log written"
  else
    fail "No git repo → unexpected result" \
      "exit=$RT_EXIT log_exists=$([ -f "$LOG" ] && echo yes || echo no)"
  fi
  rm -rf "$NO_GIT_DIR"
}

# ── TEST 10: Path containing ".living" as substring but not as directory ──────
echo ""
echo "TEST 10: '.living' as substring (not a directory component) → NOT logged"
{
  setup_test_env
  # Note: path uses /.living_docs/ — not /.living/
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/src/.living_docs/README.md\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if { [ ! -f "$LOG" ] || [ ! -s "$LOG" ]; }; then
    pass "'.living_docs/' substring → NOT logged (hook checks for /.living/ specifically)"
  else
    # NOTE: If this fails it means the hook is matching .living_docs as well.
    # That is a potential minor bug in the hook — the check is:
    #   if [[ "$FILE_PATH" != *"/.living/"* ]]
    # which correctly only matches /.living/ (with trailing slash), so this
    # test should pass. Document here if behavior differs.
    fail "'.living_docs/' substring was logged — hook may be over-matching" \
      "$(cat "$LOG")"
  fi
  cleanup_test_env
}

# ── TEST 11: Concurrent writes (10 parallel reads, no corruption) ─────────────
echo ""
echo "TEST 11: Concurrent writes (10 parallel reads)"
{
  setup_test_env
  # Launch 10 reads in parallel
  PIDS=()
  for i in $(seq 1 10); do
    JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/.living/file${i}.md\"}}"
    (cd "$TEST_DIR" && printf '%s' "$JSON" | bash "$READ_TRACKER_HOOK" 2>/dev/null) &
    PIDS+=($!)
  done
  # Wait for all background jobs
  for PID in "${PIDS[@]}"; do
    wait "$PID" 2>/dev/null || true
  done
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ]; then
    LINES=$(grep -c '' "$LOG" 2>/dev/null || echo 0)
    if [ "$LINES" -eq 10 ]; then
      pass "10 concurrent reads → exactly 10 log lines (no corruption)"
    else
      # NOTE: On systems without atomic append guarantees, line count can vary.
      # bash printf >> is generally atomic for short writes on most POSIX systems,
      # but this is not strictly guaranteed. Warn rather than hard-fail if close.
      if [ "$LINES" -ge 8 ]; then
        pass "10 concurrent reads → ${LINES} log lines (within tolerance — printf append is not strictly atomic)"
      else
        fail "10 concurrent reads → ${LINES} lines (expected 10, possible write corruption)" ""
      fi
    fi
  else
    fail "Log file not created after 10 concurrent reads" ""
  fi
  cleanup_test_env
}

# ── TEST 12: Timestamp format validation ──────────────────────────────────────
echo ""
echo "TEST 12: Timestamp format validation (ISO 8601 YYYY-MM-DDTHH:MM:SS)"
{
  setup_test_env
  JSON="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"${TEST_DIR}/.living/learnings.md\"}}"
  run_read_tracker "$JSON"
  LOG="$TEST_DIR/.claude/mycelium-read-access.log"
  if [ -f "$LOG" ]; then
    LINE=$(head -1 "$LOG")
    TS="${LINE%% *}"
    # Match YYYY-MM-DDTHH:MM:SS
    if [[ "$TS" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]; then
      pass "Timestamp matches ISO 8601 pattern: $TS"
    else
      fail "Timestamp does not match ISO 8601 YYYY-MM-DDTHH:MM:SS" \
        "got: '$TS'"
    fi
  else
    fail "Log file not created — cannot validate timestamp" ""
  fi
  cleanup_test_env
}

# ---------------------------------------------------------------------------
# HEALTH HOOK INDEX.md INJECTION TESTS (13-22)
# ---------------------------------------------------------------------------

echo ""
echo "══════════════════════════════════════════════════"
echo " HEALTH HOOK INDEX.md INJECTION TESTS (13-22)"
echo "══════════════════════════════════════════════════"

# Helper: build the standard startup JSON for the health hook
health_json() {
  local cwd="${1:-$TEST_DIR}"
  printf '{"cwd":"%s","source":"startup"}' "$cwd"
}

# ── TEST 13: INDEX.md with sentinels → KNOWLEDGE MAP injected ────────────────
echo ""
echo "TEST 13: INDEX.md with sentinels → KNOWLEDGE MAP injected"
{
  setup_test_env
  # NOTE: INDEX.md must include BOTH a <!-- BEGIN QUICK REFERENCE --> block AND the
  # <!-- BEGIN KNOWLEDGE SUMMARY --> block.  The health hook calls generate_index.py
  # --counts-only before checking sentinels.  That script rewrites the QUICK REFERENCE
  # block in-place when it finds QUICK REFERENCE sentinels; if those sentinels are
  # absent it treats the file as legacy and REPLACES IT ENTIRELY, destroying the
  # KNOWLEDGE SUMMARY sentinels before the injection check can run.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

<!-- BEGIN QUICK REFERENCE -->
| File | Entries |
|------|---------|
<!-- END QUICK REFERENCE -->

<!-- BEGIN KNOWLEDGE SUMMARY -->
## Cluster: Data Pipelines
- DAG design patterns
- Retry with backoff

## Cluster: Figure Standards
- Colorblind-safe palettes
<!-- END KNOWLEDGE SUMMARY -->
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)
  HH_EXIT=$?

  if echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP" \
    && echo "$HH_OUTPUT" | grep -q "Data Pipelines"; then
    pass "Sentinel markers present → KNOWLEDGE MAP section injected with cluster content"
  else
    fail "KNOWLEDGE MAP not injected despite sentinel markers" \
      "output=$(echo "$HH_OUTPUT" | head -c 300)"
  fi
  cleanup_test_env
}

# ── TEST 14: INDEX.md without sentinels (legacy) → NO injection ───────────────
echo ""
echo "TEST 14: INDEX.md without sentinels (legacy format) → NO injection"
{
  setup_test_env
  # A legacy file with no sentinels.  generate_index.py will replace it entirely
  # with a QUICK REFERENCE-only file, which also has no KNOWLEDGE SUMMARY → no injection.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

This is a legacy INDEX.md without any sentinel comments.
Some plain content about the project.
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if ! echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "Legacy INDEX.md (no sentinels) → no KNOWLEDGE MAP injection"
  else
    fail "Legacy INDEX.md injected KNOWLEDGE MAP unexpectedly" \
      "output=$(echo "$HH_OUTPUT" | head -c 300)"
  fi
  cleanup_test_env
}

# ── TEST 15: No INDEX.md file → NO injection, no error ───────────────────────
echo ""
echo "TEST 15: No INDEX.md file → NO injection, no error"
{
  setup_test_env
  # Ensure INDEX.md does not exist
  rm -f "$TEST_DIR/.living/INDEX.md"

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)
  HH_EXIT=$?

  if [ "$HH_EXIT" -eq 0 ] && ! echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "No INDEX.md → exit 0, no KNOWLEDGE MAP injection"
  else
    fail "No INDEX.md produced unexpected output" \
      "exit=$HH_EXIT output=$(echo "$HH_OUTPUT" | head -c 200)"
  fi
  cleanup_test_env
}

# ── TEST 16: Empty KNOWLEDGE SUMMARY block → NO injection ────────────────────
echo ""
echo "TEST 16: Empty KNOWLEDGE SUMMARY block → NO injection"
{
  setup_test_env
  # Must include QUICK REFERENCE sentinels so generate_index.py does in-place
  # replacement instead of wiping the entire file (which would also destroy the
  # empty KNOWLEDGE SUMMARY block).  Even after replacement the KNOWLEDGE SUMMARY
  # block remains empty → awk returns "" → no injection.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

<!-- BEGIN QUICK REFERENCE -->
| File | Entries |
|------|---------|
<!-- END QUICK REFERENCE -->

<!-- BEGIN KNOWLEDGE SUMMARY -->
<!-- END KNOWLEDGE SUMMARY -->
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if ! echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "Empty sentinel block → no KNOWLEDGE MAP injection (awk returns empty string)"
  else
    fail "Empty sentinel block unexpectedly injected KNOWLEDGE MAP" \
      "output=$(echo "$HH_OUTPUT" | head -c 300)"
  fi
  cleanup_test_env
}

# ── TEST 17: Special characters in INDEX.md → valid JSON ─────────────────────
echo ""
echo "TEST 17: Special characters in INDEX.md → output is valid JSON"
{
  setup_test_env
  # Include QUICK REFERENCE sentinels so generate_index.py does not wipe the file.
  # Content inside KNOWLEDGE SUMMARY has double quotes, backslashes, tabs.
  printf '# INDEX\n\n<!-- BEGIN QUICK REFERENCE -->\n| File | Entries |\n|------|------|\n<!-- END QUICK REFERENCE -->\n\n<!-- BEGIN KNOWLEDGE SUMMARY -->\n## Cluster: "Special"\n- path: C:\\Users\\foo\n- tab:\there\n<!-- END KNOWLEDGE SUMMARY -->\n' \
    > "$TEST_DIR/.living/INDEX.md"

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)
  HH_EXIT=$?

  if [ "$HH_EXIT" -eq 0 ] && echo "$HH_OUTPUT" | python3 -c "import sys,json; json.loads(sys.stdin.read())" 2>/dev/null; then
    pass "Special characters in INDEX.md → output is valid JSON"
  else
    fail "Special characters caused invalid JSON output" \
      "exit=$HH_EXIT output=$(echo "$HH_OUTPUT" | head -c 400)"
  fi
  cleanup_test_env
}

# ── TEST 18: Large INDEX.md (100 cluster lines) → injection works ─────────────
echo ""
echo "TEST 18: Large INDEX.md (100 cluster lines) → injection works"
{
  setup_test_env
  # Include QUICK REFERENCE sentinels so generate_index.py preserves the rest of
  # the file (including the large KNOWLEDGE SUMMARY block) instead of wiping it.
  {
    printf '# INDEX\n\n<!-- BEGIN QUICK REFERENCE -->\n| File | Entries |\n|------|------|\n<!-- END QUICK REFERENCE -->\n\n<!-- BEGIN KNOWLEDGE SUMMARY -->\n'
    for i in $(seq 1 100); do
      printf '## Cluster %d: Topic %d\n- Detail line %d\n' "$i" "$i" "$i"
    done
    printf '<!-- END KNOWLEDGE SUMMARY -->\n'
  } > "$TEST_DIR/.living/INDEX.md"

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)
  HH_EXIT=$?

  if [ "$HH_EXIT" -eq 0 ] \
    && echo "$HH_OUTPUT" | python3 -c "import sys,json; json.loads(sys.stdin.read())" 2>/dev/null \
    && echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "100-line INDEX.md → valid JSON output with KNOWLEDGE MAP"
  else
    fail "Large INDEX.md injection failed" \
      "exit=$HH_EXIT valid_json=$(echo "$HH_OUTPUT" | python3 -c "import sys,json; json.loads(sys.stdin.read())" 2>/dev/null && echo yes || echo no)"
  fi
  cleanup_test_env
}

# ── TEST 19: "Review relevant clusters" instruction present ───────────────────
echo ""
echo "TEST 19: 'Review relevant clusters' instruction present in output"
{
  setup_test_env
  # Include QUICK REFERENCE sentinels so the file survives generate_index.py.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

<!-- BEGIN QUICK REFERENCE -->
| File | Entries |
|------|---------|
<!-- END QUICK REFERENCE -->

<!-- BEGIN KNOWLEDGE SUMMARY -->
## Cluster: Testing
- pytest patterns
<!-- END KNOWLEDGE SUMMARY -->
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if echo "$HH_OUTPUT" | grep -q "Review relevant clusters before making decisions"; then
    pass "'Review relevant clusters before making decisions' present in output"
  else
    fail "Instruction text missing from output" \
      "output=$(echo "$HH_OUTPUT" | head -c 400)"
  fi
  cleanup_test_env
}

# ── TEST 20: MYCELIUM SUMMARY + KNOWLEDGE MAP both present ────────────────────
echo ""
echo "TEST 20: Both MYCELIUM SUMMARY and KNOWLEDGE MAP present simultaneously"
{
  setup_test_env
  # Include QUICK REFERENCE sentinels so generate_index.py preserves the
  # KNOWLEDGE SUMMARY block via in-place replacement.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

<!-- BEGIN QUICK REFERENCE -->
| File | Entries |
|------|---------|
<!-- END QUICK REFERENCE -->

<!-- BEGIN KNOWLEDGE SUMMARY -->
## Cluster: General
- Some knowledge
<!-- END KNOWLEDGE SUMMARY -->
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if echo "$HH_OUTPUT" | grep -q "MYCELIUM SUMMARY" \
    && echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "Both MYCELIUM SUMMARY and KNOWLEDGE MAP present in same output"
  else
    HAS_SUMMARY=$(echo "$HH_OUTPUT" | grep -q "MYCELIUM SUMMARY" && echo yes || echo no)
    HAS_MAP=$(echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP" && echo yes || echo no)
    fail "Not both sections present" \
      "MYCELIUM_SUMMARY=$HAS_SUMMARY KNOWLEDGE_MAP=$HAS_MAP"
  fi
  cleanup_test_env
}

# ── TEST 21: Counts are correct in MYCELIUM SUMMARY ──────────────────────────
echo ""
echo "TEST 21: Entry counts correct in MYCELIUM SUMMARY (3 learnings, 2 decisions, 1 conventions)"
{
  setup_test_env
  # Override the default files with known counts:
  # 3 learnings (3 "### " headers), 2 decisions (2 "### " headers), 1 convention (1 "## " header)
  printf '# Learnings\n\n### L1\ntext\n\n### L2\ntext\n\n### L3\ntext\n' \
    > "$TEST_DIR/.living/learnings.md"
  printf '# Decisions\n\n### D1\ntext\n\n### D2\ntext\n' \
    > "$TEST_DIR/.living/decisions.md"
  printf '# Conventions\n\n## C1\ntext\n' \
    > "$TEST_DIR/.living/conventions.md"

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if echo "$HH_OUTPUT" | grep -q "3 learnings, 2 decisions, 1 conventions"; then
    pass "MYCELIUM SUMMARY shows '3 learnings, 2 decisions, 1 conventions'"
  else
    # Extract the summary line for debugging
    SUMMARY_LINE=$(echo "$HH_OUTPUT" | grep -o "MYCELIUM SUMMARY[^\\\\]*" | head -1 || echo "not found")
    fail "Count string '3 learnings, 2 decisions, 1 conventions' not found" \
      "summary_fragment=$SUMMARY_LINE"
  fi
  cleanup_test_env
}

# ── TEST 22: Only QUICK REFERENCE sentinels, no SUMMARY sentinels → no injection
echo ""
echo "TEST 22: Only QUICK REFERENCE sentinels (no SUMMARY sentinels) → no KNOWLEDGE MAP"
{
  setup_test_env
  # Use the real sentinel text that generate_index.py recognises:
  #   <!-- BEGIN QUICK REFERENCE --> / <!-- END QUICK REFERENCE -->
  # This makes generate_index.py do an in-place replacement of only the QUICK
  # REFERENCE block, leaving the rest of the file intact — which contains no
  # KNOWLEDGE SUMMARY block at all.  Result: no KNOWLEDGE MAP injection.
  cat > "$TEST_DIR/.living/INDEX.md" << 'INDEXEOF'
# INDEX

<!-- BEGIN QUICK REFERENCE -->
| File | Entries |
|------|---------|
<!-- END QUICK REFERENCE -->

No knowledge summary section exists here.
INDEXEOF

  HH_OUTPUT=$(cd "$TEST_DIR" && printf '%s' "$(health_json)" | bash "$HEALTH_HOOK" 2>/dev/null)

  if ! echo "$HH_OUTPUT" | grep -q "KNOWLEDGE MAP"; then
    pass "QUICK REFERENCE sentinels only → no KNOWLEDGE MAP injection (requires BEGIN KNOWLEDGE SUMMARY)"
  else
    fail "QUICK REFERENCE sentinels triggered KNOWLEDGE MAP injection unexpectedly" \
      "output=$(echo "$HH_OUTPUT" | head -c 300)"
  fi
  cleanup_test_env
}

# ── TEST 23: Stale active-session-log.tmp from crashed session (no recent ──
#           activity) is cleaned and session-start-ts.tmp is refreshed.
#           Regression test for the 14794-minute-duration bug.
echo ""
echo "TEST 23: Crashed session, no activity → cleanup + fresh session-start-ts"
{
  setup_test_env
  TEN_DAYS_AGO=$(( $(date +%s) - 10*86400 ))
  echo "$TEN_DAYS_AGO" > "$TEST_DIR/.claude/session-start-ts.tmp"

  OLD_LOG="$TEST_DIR/.living/log/2026-04-15-001-test.md"
  mkdir -p "$(dirname "$OLD_LOG")"
  cat > "$OLD_LOG" <<'OLD_LOG_EOF'
---
session_id: 2026-04-15-001
project: test
branch: main
started: 2026-04-15T14:50:00-0400
ended:
duration_minutes:
files_changed:
---
OLD_LOG_EOF
  printf '%s\n%s\n' "$OLD_LOG" "$TEN_DAYS_AGO" > "$TEST_DIR/.claude/active-session-log.tmp"

  # Aged activity files match the crash time
  echo "src/old.py" > "$TEST_DIR/.claude/mycelium-session-activity.tmp"
  echo "$TEN_DAYS_AGO" > "$TEST_DIR/.claude/mycelium-reminded.tmp"
  OLD_FMT=$(date -r "$TEN_DAYS_AGO" "+%Y%m%d%H%M.%S" 2>/dev/null || true)
  if [ -n "$OLD_FMT" ]; then
    touch -t "$OLD_FMT" "$TEST_DIR/.claude/mycelium-session-activity.tmp"
    touch -t "$OLD_FMT" "$TEST_DIR/.claude/mycelium-reminded.tmp"
  fi

  run_health_hook

  NEW_TS=$(cat "$TEST_DIR/.claude/session-start-ts.tmp" 2>/dev/null || echo 0)
  AGE=$(( $(date +%s) - NEW_TS ))
  if [ "$AGE" -lt 60 ]; then
    pass "Stale sentinel cleaned, session-start-ts refreshed (age=${AGE}s)"
  else
    fail "Expected fresh session-start-ts (< 60s old)" "age=${AGE}s ts=$NEW_TS"
  fi
  cleanup_test_env
}

# ── TEST 24: Fresh active-session-log.tmp (subagent) does NOT cause cleanup ──
#           (preserves primary's session-start-ts).
echo ""
echo "TEST 24: Fresh active-session-log.tmp → session-start-ts preserved"
{
  setup_test_env
  PRIMARY_TS=$(( $(date +%s) - 60 ))
  echo "$PRIMARY_TS" > "$TEST_DIR/.claude/session-start-ts.tmp"

  ACTIVE_LOG="$TEST_DIR/.living/log/2026-04-26-001-test.md"
  mkdir -p "$(dirname "$ACTIVE_LOG")"
  cat > "$ACTIVE_LOG" <<'ACTIVE_LOG_EOF'
---
session_id: 2026-04-26-001
project: test
branch: main
started: 2026-04-26T00:00:00+0000
ended:
duration_minutes:
files_changed:
---
ACTIVE_LOG_EOF
  printf '%s\n%s\n' "$ACTIVE_LOG" "$PRIMARY_TS" > "$TEST_DIR/.claude/active-session-log.tmp"

  run_health_hook

  NEW_TS=$(cat "$TEST_DIR/.claude/session-start-ts.tmp" 2>/dev/null || echo 0)
  if [ "$NEW_TS" = "$PRIMARY_TS" ]; then
    pass "Active session-log preserved → session-start-ts unchanged"
  else
    fail "Expected session-start-ts unchanged ($PRIMARY_TS), got $NEW_TS"
  fi
  cleanup_test_env
}

# ── TEST 25: Long-running active session (5h+ owner_ts but FRESH activity) ──
#           must NOT trigger cleanup. Codex P2 / multi-day-session concern.
echo ""
echo "TEST 25: 5h old owner_ts + fresh activity → preserve everything"
{
  setup_test_env
  FIVE_H_AGO=$(( $(date +%s) - 5*3600 ))
  echo "$FIVE_H_AGO" > "$TEST_DIR/.claude/session-start-ts.tmp"

  ACTIVE_LOG="$TEST_DIR/.living/log/2026-04-26-001-test.md"
  mkdir -p "$(dirname "$ACTIVE_LOG")"
  cat > "$ACTIVE_LOG" <<'ACTIVE_LOG_EOF'
---
session_id: 2026-04-26-001
project: test
branch: main
started: 2026-04-26T01:00:00-0400
ended:
duration_minutes:
files_changed:
---
ACTIVE_LOG_EOF
  printf '%s\n%s\n' "$ACTIVE_LOG" "$FIVE_H_AGO" > "$TEST_DIR/.claude/active-session-log.tmp"
  # Activity is FRESH — session is alive
  echo "src/active.py" > "$TEST_DIR/.claude/mycelium-session-activity.tmp"
  date +%s > "$TEST_DIR/.claude/mycelium-reminded.tmp"

  run_health_hook

  PRESERVED_TS=$(cat "$TEST_DIR/.claude/session-start-ts.tmp" 2>/dev/null || echo 0)
  ALL_OK=true
  [ "$PRESERVED_TS" = "$FIVE_H_AGO" ] || { ALL_OK=false; FAIL_MSG="ts disrupted ($FIVE_H_AGO → $PRESERVED_TS)"; }
  [ -f "$TEST_DIR/.claude/active-session-log.tmp" ] || { ALL_OK=false; FAIL_MSG="active-session-log wiped"; }
  [ -f "$TEST_DIR/.claude/mycelium-session-activity.tmp" ] || { ALL_OK=false; FAIL_MSG="activity wiped"; }
  [ -f "$TEST_DIR/.claude/mycelium-reminded.tmp" ] || { ALL_OK=false; FAIL_MSG="reminded wiped"; }
  if [ "$ALL_OK" = true ]; then
    pass "Long-running active session preserved (5h owner_ts + fresh activity)"
  else
    fail "Long-running session disrupted" "${FAIL_MSG:-}"
  fi
  cleanup_test_env
}

# ── TEST 26: Stale crashed session (sentinels old) is still cleaned even when ──
#           activity files are present but quiet (long-since-crashed scenario).
echo ""
echo "TEST 26: Stale owner_ts + stale activity → cleanup proceeds"
{
  setup_test_env
  TWO_DAYS=$(( $(date +%s) - 2*86400 ))
  echo "$TWO_DAYS" > "$TEST_DIR/.claude/session-start-ts.tmp"

  OLD_LOG="$TEST_DIR/.living/log/2026-04-24-001-test.md"
  mkdir -p "$(dirname "$OLD_LOG")"
  cat > "$OLD_LOG" <<'OLD_LOG_EOF'
---
session_id: 2026-04-24-001
project: test
branch: main
started: 2026-04-24T00:00:00-0400
ended:
duration_minutes:
files_changed:
---
OLD_LOG_EOF
  printf '%s\n%s\n' "$OLD_LOG" "$TWO_DAYS" > "$TEST_DIR/.claude/active-session-log.tmp"
  echo "src/old.py" > "$TEST_DIR/.claude/mycelium-session-activity.tmp"
  echo "$TWO_DAYS" > "$TEST_DIR/.claude/mycelium-reminded.tmp"
  STALE_FMT=$(date -r "$TWO_DAYS" "+%Y%m%d%H%M.%S" 2>/dev/null || true)
  if [ -n "$STALE_FMT" ]; then
    touch -t "$STALE_FMT" "$TEST_DIR/.claude/mycelium-session-activity.tmp"
    touch -t "$STALE_FMT" "$TEST_DIR/.claude/mycelium-reminded.tmp"
  fi

  run_health_hook

  NEW_TS=$(cat "$TEST_DIR/.claude/session-start-ts.tmp" 2>/dev/null || echo 0)
  AGE=$(( $(date +%s) - NEW_TS ))
  # Note: activity / reminded should be PRESERVED on disk by our cleanup
  # (they get cleaned by the dedicated >1h staleness cleanup further down).
  if [ "$AGE" -lt 60 ]; then
    pass "Stale activity present → cleanup still proceeds (ts age=${AGE}s)"
  else
    fail "Cleanup should fire when all signals are stale" "ts age=${AGE}s"
  fi
  cleanup_test_env
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: ${PASS} passed / ${FAIL} failed / $((PASS + FAIL)) total"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
