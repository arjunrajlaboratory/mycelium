#!/usr/bin/env bash
# mycelium-post-action.sh — Claude Code PostToolUse hook (Bash matcher)
# Detects analysis/data/algorithm work and directs Claude to execute
# the mycelium post-action protocol (manifest + .living/ updates).
#
# Debounced: fires once per work cycle. Resets when .living/ is updated.
#
# Install: Add to .claude/settings.local.json under "PostToolUse" hooks
#   with matcher "Bash"
# Input: JSON on stdin with {tool_name, tool_input: {command}, ...}
# Output: JSON with additionalContext directive when triggered

set -euo pipefail

INPUT=$(cat)

# Extract the command that was run
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# --- Detection: is this a significant code execution? ---

is_significant=false

# Python script execution (not one-liners, not package management, not tests)
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])python3?\s+[^-].*\.py'; then
  is_significant=true
fi

# Rscript execution
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])Rscript\s+'; then
  is_significant=true
fi

# Jupyter notebook execution
if echo "$COMMAND" | grep -qE '(^|&&|\||\;|[[:space:]])jupyter\s+(nbconvert|execute)'; then
  is_significant=true
fi

# conda run with python script
if echo "$COMMAND" | grep -qE 'conda\s+run\s+.*python.*\.py'; then
  is_significant=true
fi

if [[ "$is_significant" != true ]]; then
  exit 0
fi

# --- Exclusions: filter out non-analysis execution ---

# pytest / unittest
if echo "$COMMAND" | grep -qE '(pytest|python3?\s+-m\s+(pytest|unittest))'; then
  exit 0
fi

# pip / package management
if echo "$COMMAND" | grep -qE '(pip\s+install|pip3\s+install|uv\s+pip|setup\.py)'; then
  exit 0
fi

# python -c one-liners
if echo "$COMMAND" | grep -qE 'python3?\s+-c\s+'; then
  exit 0
fi

# python -m (only skip known non-analysis modules)
if echo "$COMMAND" | grep -qE 'python3?\s+-m\s+(pip|venv|ensurepip|compileall|site|http\.server|json\.tool|zipfile|tarfile|timeit|cProfile|pdb|doctest|pydoc)'; then
  exit 0
fi

# linting / formatting
if echo "$COMMAND" | grep -qE '(ruff|black|isort|mypy|pyright|flake8)'; then
  exit 0
fi

# --- Repo and .living/ checks ---

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi

LIVING_DIR="$REPO_ROOT/.living"
if [[ ! -d "$LIVING_DIR" ]]; then
  exit 0
fi

# --- Build combined directive ---

ACTIVE_LOG_FILE="$REPO_ROOT/.claude/active-session-log.tmp"
LOG_DIRECTIVE=""
LIVING_DIRECTIVE=""

# Part 1: Log append (always fires, no debounce)
if [ -f "$ACTIVE_LOG_FILE" ]; then
  LOG_PATH=$(cat "$ACTIVE_LOG_FILE")
  LOG_DIRECTIVE="SESSION LOG UPDATE: Append a 2-3 line timestamped entry to ${LOG_PATH} describing what you just did, the result, and any notable outputs. Format: ### HH:MM — <action title> followed by bullet points with Command, Result, and Output fields as applicable."
fi

# Part 2: .living/ update reminder (debounced — existing behavior)
REMINDER_FILE="$REPO_ROOT/.claude/mycelium-reminded.tmp"
mkdir -p "$REPO_ROOT/.claude"

SHOULD_REMIND=true
if [[ -f "$REMINDER_FILE" ]]; then
  REMINDER_TS=$(cat "$REMINDER_FILE")
  LEARNINGS_MTIME=0
  DECISIONS_MTIME=0
  if [[ -f "$LIVING_DIR/learnings.md" ]]; then
    LEARNINGS_MTIME=$(stat -f "%m" "$LIVING_DIR/learnings.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/learnings.md" 2>/dev/null || echo "0")
  fi
  if [[ -f "$LIVING_DIR/decisions.md" ]]; then
    DECISIONS_MTIME=$(stat -f "%m" "$LIVING_DIR/decisions.md" 2>/dev/null || stat -c "%Y" "$LIVING_DIR/decisions.md" 2>/dev/null || echo "0")
  fi
  LATEST_LIVING=$((LEARNINGS_MTIME > DECISIONS_MTIME ? LEARNINGS_MTIME : DECISIONS_MTIME))
  # Also check findings directory mtime (updated when any finding file is written)
  FINDINGS_DIR="$LIVING_DIR/findings"
  if [ -d "$FINDINGS_DIR" ]; then
    FINDINGS_MTIME=$(stat -f "%m" "$FINDINGS_DIR" 2>/dev/null || stat -c "%Y" "$FINDINGS_DIR" 2>/dev/null || echo "0")
    if [ "$FINDINGS_MTIME" -gt "$LATEST_LIVING" ]; then
      LATEST_LIVING="$FINDINGS_MTIME"
    fi
  fi
  if [[ "$LATEST_LIVING" -le "$REMINDER_TS" ]]; then
    SHOULD_REMIND=false
  fi
fi

if [[ "$SHOULD_REMIND" == true ]]; then
  date +%s > "$REMINDER_FILE"
  LIVING_DIRECTIVE="MYCELIUM POST-ACTION PROTOCOL — MANDATORY: You just executed analysis/data processing/algorithm code. Complete the following steps before continuing.\n\n--- TIER 1 (ALL contexts — main + subagents) ---\n\n4. LEARNINGS: Append to .living/learnings.md if anything unexpected was learned (gotcha, edge case, failure, insight). Use printf >> to append. Format: ## [YYYY-MM-DD] Title, then Category/What happened/Why it matters/Resolution/Tags fields.\n   KNOWLEDGE PROMOTION: If the learning is transferable (a pattern that applies beyond this project — async patterns, API quirks, debugging insights, test patterns, env setup, etc. — NOT project-specific implementation), ALSO printf >> to the matching global domain file at ~/.claude/knowledge/{domain}.md. Format: ### Title, then **What**/**Evidence** (cite source project)/**When useful** (trigger condition)/**Scope**/**Status: unreviewed**/**Last validated: YYYY-MM-DD**/**Promoted**: inline by mycelium. IMPORTANT: Use the EXACT same title as the .living/learnings.md entry (copy the ## [date] Title line, changing ## to ###) so the daily backfill audit can detect it via grep and skip duplicates. Domains: python-patterns, debugging-patterns, external-apis, data-pipelines, testing-patterns, git-workflows, environment-setup, figure-standards, scientific-analysis, llm-patterns, writing-conventions, publishing-workflows, spatial-biology, data-formats. If no domain fits, skip promotion.\n5. DECISIONS: Append to .living/decisions.md if any non-obvious design choice was made.\n6. FINDINGS: If this work produced a scientific finding (empirical observation, validated/invalidated hypothesis, quantitative result, or domain methodology discovery — NOT tooling), crystallize it to .living/findings/{topic}.md. Walk up from repo root to find meta-project .living/findings/INDEX.md for existing topics. Route to existing topic or create new. Use templates from skills/core/templates/findings-entry.md and findings-topic.md. Upsert row in .living/findings/FINDINGS_REGISTRY.md.\n\nRouting rule:\n- How the tool/pipeline/code works → .living/learnings.md\n- What the data/analysis revealed about the domain → .living/findings/{topic}.md\n- A design choice about implementation → .living/decisions.md\n\nDo Tier 1 NOW. If you are a subagent, stop here after Tier 1.\n\n--- TIER 2 (Main context only — skip if you are a subagent) ---\n\n1. OUTPUTS: Save outputs to the appropriate directory (analysis/[name]/outputs/, data/processed/, or algorithms/[name]/).\n2. MANIFESTS: Add or update the entry in the relevant manifest (ANALYSIS_MANIFEST.md, DATA_MANIFEST.md, or ALGORITHM_MANIFEST.md).\n3. DOCUMENTATION: Update the subfolder documentation file (UPPER_SNAKE_CASE.md in the affected directory).\n7. CRYSTALLIZE: Read .living/learnings.md (tail -50). If 3+ entries share tags or themes, check .living/conventions.md for an existing convention on that topic. If one exists, append new Source: citations. If none exists, add a new convention with Source: citations linking to the originating learnings. Do not create near-duplicates.\n8. LOG REGISTRY: Update the current session row in .living/log/LOG_REGISTRY.md — replace the stub Summary with a 1-sentence past-tense accomplishment. Fill Key Outputs with semicolon-separated artifacts.\n9. CONVENTION FEEDBACK: If any installed convention pack practices were relevant to this work, note in .living/conventions.md whether they were helpful or had gaps.\n10. SESSION SUMMARY: Update .claude/last-session.md with cumulative 5-section summary (What worked on / Key decisions / Blockers / Current state / Next steps). Run git log --since=<session-start> to ground in facts."
fi

# Assemble and emit single JSON
if [ -n "$LOG_DIRECTIVE" ] && [ -n "$LIVING_DIRECTIVE" ]; then
  COMBINED="${LOG_DIRECTIVE}\n\n---\n\n${LIVING_DIRECTIVE}"
elif [ -n "$LOG_DIRECTIVE" ]; then
  COMBINED="$LOG_DIRECTIVE"
elif [ -n "$LIVING_DIRECTIVE" ]; then
  COMBINED="$LIVING_DIRECTIVE"
else
  exit 0
fi

ESCAPED=$(printf '%s' "$COMBINED" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null)
printf '{"additionalContext": %s}\n' "$ESCAPED"
