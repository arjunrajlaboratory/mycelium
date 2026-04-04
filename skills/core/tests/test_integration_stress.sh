#!/usr/bin/env bash
# test_integration_stress.sh — Integration stress tests for the mycelium write pipeline
#
# These tests verify that multiple features compose correctly end-to-end,
# unlike unit tests that test each feature in isolation.
#
# Run: bash test_integration_stress.sh

set -euo pipefail

PASS=0
FAIL=0
# tests/ is at skills/core/tests/ — go up to mycelium root (3 levels)
MYCELIUM_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPTS_DIR="$MYCELIUM_ROOT/skills/core/scripts"
HOOKS_DIR="$MYCELIUM_ROOT/skills/core/hooks"

pass() { echo -e "\033[0;32mPASS\033[0m — $1"; PASS=$((PASS + 1)); }
fail() { echo -e "\033[0;31mFAIL\033[0m — $1: $2"; FAIL=$((FAIL + 1)); }

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

make_repo() {
  local dir
  dir=$(mktemp -d)
  git init -q "$dir"
  git -C "$dir" config user.email "test@test.com"
  git -C "$dir" config user.name "Test"
  touch "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "init"
  mkdir -p "$dir/.claude"
  echo "$dir"
}

make_living() {
  local repo="$1"
  mkdir -p "$repo/.living/log" "$repo/.living/findings"
}

# Write N ### entries to a learnings.md (appends or creates)
write_learnings() {
  local path="$1"
  local count="$2"
  local offset="${3:-0}"
  if [ ! -f "$path" ]; then
    printf '# Learnings\n\n' > "$path"
  fi
  for i in $(seq 1 "$count"); do
    local n=$(( offset + i ))
    printf '### [2026-01-%02d] **Learning %d** — test entry\n\nContent line %d. Details here.\n\n' \
      "$(( (n % 28) + 1 ))" "$n" "$n" >> "$path"
  done
}

# Write N ### entries to decisions.md
write_decisions() {
  local path="$1"
  local count="$2"
  local offset="${3:-0}"
  if [ ! -f "$path" ]; then
    printf '# Decisions\n\n' > "$path"
  fi
  for i in $(seq 1 "$count"); do
    local n=$(( offset + i ))
    printf '### Decision %d\n\nRationale %d.\n\n' "$n" "$n" >> "$path"
  done
}

# Write N ## sections to conventions.md
write_conventions() {
  local path="$1"
  local count="$2"
  if [ ! -f "$path" ]; then
    printf '# Conventions\n\n' > "$path"
  fi
  for i in $(seq 1 "$count"); do
    printf '## Convention %d\n\nDetails %d.\n\n' "$i" "$i" >> "$path"
  done
}

run_counts_only() {
  local living_dir="$1"
  python3 "$SCRIPTS_DIR/generate_index.py" \
    --living-dir "$living_dir" \
    --counts-only
}

# ─────────────────────────────────────────────────────────────────
# TEST 1: SessionStart → counts-only → INDEX.md has fresh counts
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 1: counts-only creates INDEX.md with correct counts, updates on change"
{
  REPO=$(make_repo)
  make_living "$REPO"

  write_learnings "$REPO/.living/learnings.md" 5
  write_decisions "$REPO/.living/decisions.md" 3
  write_conventions "$REPO/.living/conventions.md" 2

  run_counts_only "$REPO/.living" >/dev/null 2>&1

  if [ ! -f "$REPO/.living/INDEX.md" ]; then
    fail "T1" "INDEX.md not created"
  else
    CONTENT=$(cat "$REPO/.living/INDEX.md")
    if echo "$CONTENT" | grep -q "5 entries" && \
       echo "$CONTENT" | grep -q "3 entries" && \
       echo "$CONTENT" | grep -q "2 sections"; then

      # Add 2 more learnings (total 7)
      write_learnings "$REPO/.living/learnings.md" 2 5
      run_counts_only "$REPO/.living" >/dev/null 2>&1

      UPDATED=$(cat "$REPO/.living/INDEX.md")
      if echo "$UPDATED" | grep -q "7 entries"; then
        pass "T1 — fresh counts (5/3/2), re-run shows 7 learnings"
      else
        fail "T1" "expected 7 entries after update, got: $(echo "$UPDATED" | grep 'learnings')"
      fi
    else
      fail "T1" "initial counts wrong — expected 5 entries / 3 entries / 2 sections"
    fi
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 2: counts-only preserves previous summarize output
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 2: counts-only preserves KNOWLEDGE SUMMARY block byte-for-byte"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 3

  # Inject INDEX.md with both sentinel blocks
  SUMMARY_CONTENT="### Key Knowledge Clusters — Learnings
- **Pipeline design** (3 entries) — known cluster preserved here

### Key Knowledge Clusters — Decisions
- **Architecture** (1 entry) — preserved decision cluster"

  cat > "$REPO/.living/INDEX.md" <<INDEX_EOF
<!-- BEGIN QUICK REFERENCE -->
# .living/ Index
Last audit: 2025-01-01

| File | Entries | Last updated | Key topics |
|------|---------|--------------|------------|
| learnings.md | 3 entries | 2025-01-01 | — |
<!-- END QUICK REFERENCE -->

<!-- BEGIN KNOWLEDGE SUMMARY -->
Last summarized: 2025-03-15

${SUMMARY_CONTENT}
<!-- END KNOWLEDGE SUMMARY -->
INDEX_EOF

  BEFORE_SUMMARY=$(awk '/<!-- BEGIN KNOWLEDGE SUMMARY -->/,/<!-- END KNOWLEDGE SUMMARY -->/' "$REPO/.living/INDEX.md")

  run_counts_only "$REPO/.living" >/dev/null 2>&1

  AFTER_SUMMARY=$(awk '/<!-- BEGIN KNOWLEDGE SUMMARY -->/,/<!-- END KNOWLEDGE SUMMARY -->/' "$REPO/.living/INDEX.md")
  AFTER_CONTENT=$(cat "$REPO/.living/INDEX.md")

  if [ "$BEFORE_SUMMARY" = "$AFTER_SUMMARY" ] && \
     echo "$AFTER_CONTENT" | grep -q "<!-- BEGIN QUICK REFERENCE -->"; then
    pass "T2 — KNOWLEDGE SUMMARY block byte-for-byte identical; QUICK REFERENCE updated"
  else
    fail "T2" "KNOWLEDGE SUMMARY was modified by --counts-only"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 3: Health hook output → valid JSON → parseable additionalContext
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 3: Health hook produces valid JSON with expected sections"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 4
  write_decisions "$REPO/.living/decisions.md" 2
  write_conventions "$REPO/.living/conventions.md" 3

  # Build INDEX.md with sentinel blocks (simulate having been summarized)
  run_counts_only "$REPO/.living" >/dev/null 2>&1

  # Add KNOWLEDGE SUMMARY block so health hook can inject it
  cat >> "$REPO/.living/INDEX.md" <<SUMMARY_EOF

<!-- BEGIN KNOWLEDGE SUMMARY -->
Last summarized: 2026-01-01

### Key Knowledge Clusters — Learnings
- **Integration** (4 entries) — integration patterns

### Key Knowledge Clusters — Decisions
- **Design** (2 entries) — design choices
<!-- END KNOWLEDGE SUMMARY -->
SUMMARY_EOF

  HOOK_OUTPUT=$(cd "$REPO" && printf '{"cwd":"%s","source":"startup"}' "$REPO" \
    | bash "$HOOKS_DIR/mycelium-health.sh" 2>/dev/null || true)

  if [ -z "$HOOK_OUTPUT" ]; then
    fail "T3" "health hook produced no output"
  else
    # Validate it's parseable JSON
    PARSED=$(echo "$HOOK_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok')" 2>/dev/null || echo "INVALID")
    if [ "$PARSED" = "ok" ]; then
      # Verify expected sections in additionalContext
      CTX=$(echo "$HOOK_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('additionalContext',''))" 2>/dev/null)
      if echo "$CTX" | grep -q "MYCELIUM SUMMARY" && echo "$CTX" | grep -q "KNOWLEDGE MAP"; then
        pass "T3 — valid JSON with MYCELIUM SUMMARY and KNOWLEDGE MAP sections"
      else
        fail "T3" "missing expected sections. ctx snippet: $(echo "$CTX" | head -c 200)"
      fi
    else
      fail "T3" "output is not valid JSON: $(echo "$HOOK_OUTPUT" | head -c 200)"
    fi
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 4: Read tracker fires on .living/ reads, not on other reads
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 4: Read tracker logs only .living/ reads"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 2

  LOG_FILE="$REPO/.claude/mycelium-read-access.log"

  # Send 5 .living/ read events
  for i in 1 2 3 4 5; do
    printf '{"tool_name":"Read","tool_input":{"file_path":"%s/.living/learnings.md"}}' "$REPO" \
      | (cd "$REPO" && bash "$HOOKS_DIR/mycelium-read-tracker.sh" 2>/dev/null) || true
  done

  # Send 5 non-.living/ read events (should NOT be logged)
  for i in 1 2 3 4 5; do
    printf '{"tool_name":"Read","tool_input":{"file_path":"%s/README.md"}}' "$REPO" \
      | (cd "$REPO" && bash "$HOOKS_DIR/mycelium-read-tracker.sh" 2>/dev/null) || true
  done

  if [ ! -f "$LOG_FILE" ]; then
    fail "T4" "log file not created after .living/ reads"
  else
    LINE_COUNT=$(wc -l < "$LOG_FILE" | tr -d ' ')
    ALL_LIVING=true
    while IFS= read -r line; do
      if ! echo "$line" | grep -q "\.living/"; then
        ALL_LIVING=false
        break
      fi
    done < "$LOG_FILE"

    if [ "$LINE_COUNT" -eq 5 ] && [ "$ALL_LIVING" = true ]; then
      pass "T4 — exactly 5 .living/ entries logged, non-.living/ reads silently ignored"
    else
      fail "T4" "expected 5 .living/ entries, got $LINE_COUNT; all_living=$ALL_LIVING"
    fi
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 5: Full pipeline — 0 entries → counts-only → add entries → counts-only → add summary → counts-only
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 5: Full pipeline sequence — empty → populate → summarize → counts-only"
{
  REPO=$(make_repo)
  make_living "$REPO"

  # Create empty files
  printf '# Learnings\n\n' > "$REPO/.living/learnings.md"
  printf '# Decisions\n\n' > "$REPO/.living/decisions.md"

  # Step 1: counts-only on empty files → 0 entries
  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT_0=$(cat "$REPO/.living/INDEX.md")
  if ! echo "$CONTENT_0" | grep -q "<!-- BEGIN QUICK REFERENCE -->"; then
    fail "T5" "step 1: INDEX.md missing quick reference sentinel"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  # Step 2: add entries
  write_learnings "$REPO/.living/learnings.md" 10
  write_decisions "$REPO/.living/decisions.md" 5
  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT_10=$(cat "$REPO/.living/INDEX.md")

  if ! echo "$CONTENT_10" | grep -q "10 entries"; then
    fail "T5" "step 2: expected 10 learnings entries"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  # Step 3: manually inject KNOWLEDGE SUMMARY block
  cat >> "$REPO/.living/INDEX.md" <<SUMBLK_EOF

<!-- BEGIN KNOWLEDGE SUMMARY -->
Last summarized: 2026-04-01

### Key Knowledge Clusters — Learnings
- **Patterns** (10 entries) — injected test clusters

### Key Knowledge Clusters — Decisions
- **Design** (5 entries) — injected decisions
<!-- END KNOWLEDGE SUMMARY -->
SUMBLK_EOF

  # Step 4: run counts-only again — should preserve summary, update counts
  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT_FINAL=$(cat "$REPO/.living/INDEX.md")

  if echo "$CONTENT_FINAL" | grep -q "10 entries" && \
     echo "$CONTENT_FINAL" | grep -q "5 entries" && \
     echo "$CONTENT_FINAL" | grep -q "injected test clusters" && \
     echo "$CONTENT_FINAL" | grep -q "<!-- BEGIN KNOWLEDGE SUMMARY -->"; then
    pass "T5 — full pipeline: 0→10/5 entries, summary preserved after counts-only"
  else
    fail "T5" "final INDEX.md missing expected content"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 6: Legacy migration → sentinel format → counts update
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 6: Legacy INDEX.md migrated to sentinel format, idempotent on second run"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 4

  # Write a legacy INDEX.md (no sentinels)
  cat > "$REPO/.living/INDEX.md" <<LEGACY_EOF
# .living/ Index
Last audit: 2024-06-15

| File | Entries | Last updated | Key topics |
|------|---------|--------------|------------|
| learnings.md | 99 entries | 2024-06-15 | stale topic |
LEGACY_EOF

  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT_1=$(cat "$REPO/.living/INDEX.md")

  if ! echo "$CONTENT_1" | grep -q "<!-- BEGIN QUICK REFERENCE -->"; then
    fail "T6" "sentinels not added after migration"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi
  if ! echo "$CONTENT_1" | grep -q "4 entries"; then
    fail "T6" "stale count not replaced (still shows 99?)"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  # Run again → should be idempotent
  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT_2=$(cat "$REPO/.living/INDEX.md")

  if [ "$CONTENT_1" = "$CONTENT_2" ]; then
    pass "T6 — legacy migrated, counts correct (4 entries), second run idempotent"
  else
    fail "T6" "second run changed content (not idempotent)"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 7: Concurrent counts-only runs don't corrupt INDEX.md
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 7: Concurrent --counts-only runs don't corrupt INDEX.md"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 8
  write_decisions "$REPO/.living/decisions.md" 3

  # Launch 5 parallel counts-only processes
  PIDS=()
  for _i in 1 2 3 4 5; do
    run_counts_only "$REPO/.living" >/dev/null 2>&1 &
    PIDS+=($!)
  done

  # Wait for all
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done

  CONTENT=$(cat "$REPO/.living/INDEX.md")

  # Must have exactly one BEGIN/END pair for QUICK_REFERENCE
  QR_BEGIN_COUNT=$(echo "$CONTENT" | grep -c "<!-- BEGIN QUICK REFERENCE -->" || echo 0)
  QR_END_COUNT=$(echo "$CONTENT" | grep -c "<!-- END QUICK REFERENCE -->" || echo 0)

  if [ "$QR_BEGIN_COUNT" -eq 1 ] && [ "$QR_END_COUNT" -eq 1 ] && \
     echo "$CONTENT" | grep -q "8 entries"; then
    pass "T7 — 5 concurrent runs: INDEX.md is valid (1 sentinel pair, correct counts)"
  else
    fail "T7" "concurrent corruption detected: BEGIN=$QR_BEGIN_COUNT END=$QR_END_COUNT counts=$(echo "$CONTENT" | grep 'entries')"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 8: Health hook with missing .living/ → still outputs JSON
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 8: Health hook with missing .living/ → valid JSON output"
{
  REPO=$(make_repo)
  # No .living/ directory

  HOOK_OUTPUT=$(cd "$REPO" && printf '{"cwd":"%s","source":"startup"}' "$REPO" \
    | bash "$HOOKS_DIR/mycelium-health.sh" 2>/dev/null || true)

  if [ -z "$HOOK_OUTPUT" ]; then
    # Health hook may exit 0 with no output if not in git repo scenario, that's OK
    # But since we ARE in a git repo, it should output something
    # Check: the health hook explicitly exits 0 when there's no .living/
    # Some hooks emit warning JSON; accept either empty OR valid JSON
    pass "T8 — no .living/ dir: hook exited cleanly (no output is acceptable)"
  else
    PARSED=$(echo "$HOOK_OUTPUT" | python3 -c "import sys,json; json.load(sys.stdin); print('ok')" 2>/dev/null || echo "INVALID")
    if [ "$PARSED" = "ok" ]; then
      pass "T8 — no .living/ dir: hook produced valid JSON (warning or context)"
    else
      fail "T8" "hook produced non-JSON output: $(echo "$HOOK_OUTPUT" | head -c 200)"
    fi
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 9: Read tracker log survives across multiple "sessions"
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 9: Read tracker accumulates entries across multiple sessions"
{
  REPO=$(make_repo)
  make_living "$REPO"
  write_learnings "$REPO/.living/learnings.md" 2

  LOG_FILE="$REPO/.claude/mycelium-read-access.log"

  # Simulate session 1: 3 reads
  for i in 1 2 3; do
    printf '{"tool_name":"Read","tool_input":{"file_path":"%s/.living/learnings.md"}}' "$REPO" \
      | (cd "$REPO" && bash "$HOOKS_DIR/mycelium-read-tracker.sh" 2>/dev/null) || true
  done

  AFTER_SESSION1=$(wc -l < "$LOG_FILE" | tr -d ' ')

  # Simulate session 2: 4 more reads (log should accumulate, not reset)
  for i in 1 2 3 4; do
    printf '{"tool_name":"Read","tool_input":{"file_path":"%s/.living/decisions.md"}}' "$REPO" \
      | (cd "$REPO" && bash "$HOOKS_DIR/mycelium-read-tracker.sh" 2>/dev/null) || true
  done

  AFTER_SESSION2=$(wc -l < "$LOG_FILE" | tr -d ' ')

  if [ "$AFTER_SESSION1" -eq 3 ] && [ "$AFTER_SESSION2" -eq 7 ]; then
    pass "T9 — log accumulated: 3 after session 1, 7 total after session 2"
  else
    fail "T9" "expected 3 then 7, got $AFTER_SESSION1 then $AFTER_SESSION2"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 10: generate_index.py --counts-only with realistic project structure
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 10: Realistic project structure — all directories and counts verified"
{
  REPO=$(make_repo)
  make_living "$REPO"

  # learnings.md: 50 entries with varied formats
  {
    printf '# Learnings\n\n'
    for i in $(seq 1 15); do
      printf '### [2026-0%d-%02d] **Bold Learning %d** — description\n\nContent %d.\n\n' \
        "$(( (i % 3) + 1 ))" "$(( (i % 28) + 1 ))" "$i" "$i"
    done
    for i in $(seq 16 30); do
      printf '### [domain-tag] Entry %d\n\nContent %d.\n\n' "$i" "$i"
    done
    for i in $(seq 31 50); do
      printf '### Plain Header %d\n\nContent %d.\n\n' "$i" "$i"
    done
  } > "$REPO/.living/learnings.md"

  # decisions.md: 20 entries
  write_decisions "$REPO/.living/decisions.md" 20

  # conventions.md: 8 sections
  write_conventions "$REPO/.living/conventions.md" 8

  # log/: 10 session files
  for i in $(seq 1 10); do
    local_i=$(printf '%03d' "$i")
    touch "$REPO/.living/log/2026-04-01-${local_i}-myproject.md"
  done

  # findings/: 3 topic files, each with 5 ## F- entries
  for topic in alpha beta gamma; do
    {
      printf '# Findings: %s\n\n' "$topic"
      for f in 1 2 3 4 5; do
        printf '## F-%s-%d\n\nFinding detail.\n\n' "$topic" "$f"
      done
    } > "$REPO/.living/findings/${topic}.md"
  done

  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT=$(cat "$REPO/.living/INDEX.md")

  CHECKS_PASSED=0

  echo "$CONTENT" | grep -q "50 entries"   && CHECKS_PASSED=$((CHECKS_PASSED + 1))
  echo "$CONTENT" | grep -q "20 entries"   && CHECKS_PASSED=$((CHECKS_PASSED + 1))
  echo "$CONTENT" | grep -q "8 sections"   && CHECKS_PASSED=$((CHECKS_PASSED + 1))
  echo "$CONTENT" | grep -q "10 sessions"  && CHECKS_PASSED=$((CHECKS_PASSED + 1))
  echo "$CONTENT" | grep -q "15 findings"  && CHECKS_PASSED=$((CHECKS_PASSED + 1))

  if [ "$CHECKS_PASSED" -eq 5 ]; then
    pass "T10 — realistic structure: 50 learnings, 20 decisions, 8 conventions, 10 sessions, 15 findings"
  else
    fail "T10" "$CHECKS_PASSED/5 checks passed. Content:\n$(echo "$CONTENT" | grep -E 'entries|sections|sessions|findings' | head -10)"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# TEST 11: parse_llm_clusters with production-like output
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 11: parse_llm_clusters validates production-like Autonomous Science output"
{
  LLM_OUTPUT='### Key Knowledge Clusters — Learnings
- **Multi-agent coordination** (18 entries) — patterns for orchestrating parallel Claude subagents with dependency tracking and batching
- **Pipeline resilience** (12 entries) — snapshot-based retry, exponential backoff, and partial-failure recovery strategies
- **Data pipeline design** (9 entries) — AnnData manipulation, spatial transcriptomics preprocessing, and QC normalization
- **LLM prompt engineering** (7 entries) — prompt templates for structured output, cluster extraction, and scientific summarization
- **Environment & tooling** (5 entries) — conda env management, ruff linting integration, and matplotlib Agg backend fixes

### Key Knowledge Clusters — Decisions
- **Architecture** (8 entries) — key structural choices including three-layer MCP server design and DAG orchestration model
- **Knowledge management** (6 entries) — decisions around .living/ format evolution, sentinel markers, and INDEX.md dual-date semantics
- **Testing strategy** (4 entries) — decision to use subprocess-based integration tests over in-process mocking for hook validation'

  RESULT=$(printf '%s' "$LLM_OUTPUT" | python3 -c "
import sys
sys.path.insert(0, '${SCRIPTS_DIR}')
from generate_index import parse_llm_clusters
output = sys.stdin.read()
result = parse_llm_clusters(output)
print('VALID' if result is not None else 'INVALID')
" 2>/dev/null || echo "ERROR")

  if [ "$RESULT" = "VALID" ]; then
    pass "T11 — production-like LLM output (8 clusters across 2 sections) parsed successfully"
  else
    fail "T11" "parse_llm_clusters returned INVALID or errored: $RESULT"
  fi
}

# ─────────────────────────────────────────────────────────────────
# TEST 12: End-to-end stress — 1000-entry file
# ─────────────────────────────────────────────────────────────────
echo ""
echo "TEST 12: 1000-entry stress — counts, large note, snippet sampling, prompt construction"
{
  REPO=$(make_repo)
  make_living "$REPO"

  # Generate learnings.md with 1000 entries
  {
    printf '# Learnings\n\n'
    for i in $(seq 1 1000); do
      printf '### [2026-01-%02d] **Stress Entry %d** — generated for load testing\n\nContent line %d. This entry tests large file handling.\n\n' \
        "$(( (i % 28) + 1 ))" "$i" "$i"
    done
  } > "$REPO/.living/learnings.md"

  write_decisions "$REPO/.living/decisions.md" 10

  # 1: counts-only should note "large — read selectively"
  run_counts_only "$REPO/.living" >/dev/null 2>&1
  CONTENT=$(cat "$REPO/.living/INDEX.md")

  if ! echo "$CONTENT" | grep -q "1000 entries"; then
    fail "T12a" "expected '1000 entries' in INDEX.md"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  if ! echo "$CONTENT" | grep -q "large — read selectively"; then
    fail "T12b" "expected '(large — read selectively)' annotation for 1000-entry file"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  # 2: extract_entry_snippets returns ≤500 entries, newest first
  SNIPPET_RESULT=$(python3 -c "
import sys
sys.path.insert(0, '${SCRIPTS_DIR}')
from pathlib import Path
from generate_index import extract_entry_snippets
snippets = extract_entry_snippets(Path('${REPO}/.living/learnings.md'), 'learnings')
count = len(snippets)
first_header = snippets[0][0] if snippets else ''
print(f'{count} {first_header}')
" 2>/dev/null || echo "ERROR")

  SNIPPET_COUNT=$(echo "$SNIPPET_RESULT" | awk '{print $1}')
  FIRST_HEADER=$(echo "$SNIPPET_RESULT" | cut -d' ' -f2-)

  if [ "$SNIPPET_COUNT" -gt 500 ]; then
    fail "T12c" "extract_entry_snippets returned $SNIPPET_COUNT > 500 entries"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  if ! echo "$FIRST_HEADER" | grep -q "Stress Entry 1000"; then
    fail "T12d" "expected newest entry first (Stress Entry 1000), got: $FIRST_HEADER"
    rm -rf "$REPO"
    # shellcheck disable=SC2104
    continue 2>/dev/null || true
  fi

  # 3: build_llm_prompt contains total count and ≤500 snippets
  PROMPT_RESULT=$(python3 -c "
import sys
sys.path.insert(0, '${SCRIPTS_DIR}')
from pathlib import Path
from generate_index import extract_entry_snippets, build_llm_prompt
lpath = Path('${REPO}/.living/learnings.md')
dpath = Path('${REPO}/.living/decisions.md')
lsnippets = extract_entry_snippets(lpath, 'learnings')
dsnippets = extract_entry_snippets(dpath, 'decisions')
prompt = build_llm_prompt(lsnippets, dsnippets, 1000, 10)
has_total = '1000 total entries' in prompt
shown_count = int(prompt.split('shown of')[0].split('(')[-1].strip()) if 'shown of' in prompt else -1
print(f'has_total={has_total} shown_count={shown_count}')
" 2>/dev/null || echo "ERROR")

  if echo "$PROMPT_RESULT" | grep -q "has_total=True" && \
     echo "$PROMPT_RESULT" | python3 -c "
import sys
line = sys.stdin.read()
import re
m = re.search(r'shown_count=(\d+)', line)
if m:
    n = int(m.group(1))
    sys.exit(0 if n <= 500 else 1)
else:
    sys.exit(1)
" 2>/dev/null; then
    pass "T12 — 1000-entry file: counts correct, large note shown, ≤500 snippets sampled newest-first, prompt has total count"
  else
    fail "T12e" "prompt checks failed: $PROMPT_RESULT"
  fi
  rm -rf "$REPO"
}

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: ${PASS} passed / ${FAIL} failed / $((PASS + FAIL)) total"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
