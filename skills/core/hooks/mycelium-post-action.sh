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
# Output: JSON with structured additionalContext directive when triggered

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/mycelium-hook-lib.sh"

INPUT=$(cat)

# Extract the command that was run
COMMAND=$(printf '%s' "$INPUT" | mycelium_json_get 'tool_input.command')
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# --- Detection: is this a proven significant code execution? ---

# Share the data-lineage parser's command-position and control-flow checks so
# skipped compound branches and interpreter text used as arguments cannot open
# a false bookkeeping cycle.
SESSION_CWD=$(printf '%s' "$INPUT" | mycelium_json_get 'cwd')
if [[ -z "$SESSION_CWD" || ! -d "$SESSION_CWD" ]]; then
  SESSION_CWD=$(pwd)
fi
BASH_EXIT=$(printf '%s' "$INPUT" | mycelium_bash_exit)
if [[ -z "$BASH_EXIT" && "$(mycelium_hook_host)" == "claude" ]]; then
  BASH_EXIT=0
fi
DETECTOR="${MYCELIUM_EXECUTION_HELPER:-$HERE/../scripts/extract_data_lineage_event.py}"
if [[ ! -f "$DETECTOR" ]]; then
  exit 0
fi
DETECT_ARGS=(
  --cwd "$SESSION_CWD"
  --ts "$(date +%Y-%m-%dT%H:%M:%S%z)"
  --bash-cmd "$COMMAND"
  --check-post-action
)
if [[ "$BASH_EXIT" =~ ^-?[0-9]+$ ]]; then
  DETECT_ARGS+=(--bash-exit "$BASH_EXIT")
fi
if ! python3 "$DETECTOR" "${DETECT_ARGS[@]}" >/dev/null 2>&1; then
  exit 0
fi

# --- Repo and .living/ checks ---

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi
mycelium_prepare_post_tool_state "$REPO_ROOT" "$INPUT" || exit 0

LIVING_DIR="$REPO_ROOT/.living"
if [[ ! -d "$LIVING_DIR" ]]; then
  exit 0
fi

# --- Build combined directive ---

ACTIVE_LOG_FILE="$STATE_DIR/active-session-log.tmp"
LOG_DIRECTIVE=""
LIVING_DIRECTIVE=""

# Part 1: Log append (always fires, no debounce)
if [ -f "$ACTIVE_LOG_FILE" ]; then
  # SessionStart stores the log path on line 1 and the owning session's
  # timestamp on line 2. Only the path belongs in the agent directive.
  if _ACTIVE_MARKER=$(mycelium_read_active_log_marker "$REPO_ROOT" "$ACTIVE_LOG_FILE"); then
    LOG_PATH=$(printf '%s\n' "$_ACTIVE_MARKER" | sed -n '1p')
    LOG_DIRECTIVE="SESSION LOG UPDATE: Append a 2-3 line timestamped entry to ${LOG_PATH} describing what you just did, the result, and any notable outputs. Format: ### HH:MM — <action title> followed by bullet points with Command, Result, and Output fields as applicable."
  fi
fi

# Part 2: .living/ update reminder (debounced — existing behavior)
REMINDER_FILE="$STATE_DIR/mycelium-reminded.tmp"
mkdir -p "$STATE_DIR"

SESSION_CHANGES_HELPER="${MYCELIUM_SESSION_CHANGES_HELPER:-$HERE/../scripts/session_file_changes.py}"
SHOULD_REMIND=false
if mycelium_refresh_work_cycle "$REPO_ROOT" "$SESSION_CHANGES_HELPER"; then
  SHOULD_REMIND=true
fi

if [[ "$SHOULD_REMIND" == true ]]; then
  LIVING_DIRECTIVE="MYCELIUM POST-ACTION PROTOCOL — MANDATORY: You just executed analysis/data processing/algorithm code. Complete the following steps before continuing.\n\n--- TIER 1 (ALL contexts — main + subagents) ---\n\n4. LEARNINGS: Append to .living/learnings.md if anything unexpected was learned (gotcha, edge case, failure, insight). Use printf >> to append. Format: ## [YYYY-MM-DD] Title, then Category/What happened/Why it matters/Resolution/Tags fields.\n   KNOWLEDGE PROMOTION: If the learning is transferable (a pattern that applies beyond this project — async patterns, API quirks, debugging insights, test patterns, env setup, etc. — NOT project-specific implementation), ALSO printf >> to the matching global domain file at ~/.mycelium/knowledge/{domain}.md. Format: ### Title, then **What**/**Evidence** (cite source project)/**When useful** (trigger condition)/**Scope**/**Status: unreviewed**/**Last validated: YYYY-MM-DD**/**Promoted**: inline by mycelium. IMPORTANT: Use the EXACT same title as the .living/learnings.md entry (copy the ## [date] Title line, changing ## to ###) so the daily backfill audit can detect it via grep and skip duplicates. Domains: python-patterns, debugging-patterns, external-apis, data-pipelines, testing-patterns, git-workflows, environment-setup, figure-standards, scientific-analysis, llm-patterns, writing-conventions, publishing-workflows, spatial-biology, data-formats. If no domain fits, skip promotion.\n5. DECISIONS: Append to .living/decisions.md if any non-obvious design choice was made.\n6. FINDINGS: If this work produced a scientific finding (empirical observation, validated/invalidated hypothesis, quantitative result, or domain methodology discovery — NOT tooling), crystallize it to .living/findings/{topic}.md. Walk up from repo root to find meta-project .living/findings/INDEX.md for existing topics. Route to existing topic or create new. Use templates from skills/core/templates/findings-entry.md and findings-topic.md. Upsert row in .living/findings/FINDINGS_REGISTRY.md.\n\nRouting rule:\n- How the tool/pipeline/code works → .living/learnings.md\n- What the data/analysis revealed about the domain → .living/findings/{topic}.md\n- A design choice about implementation → .living/decisions.md\n\nDo Tier 1 NOW. If you are a subagent, stop here after Tier 1.\n\n--- TIER 2 (Main context only — skip if you are a subagent) ---\n\n1. OUTPUTS: Save outputs to the appropriate directory (analysis/[name]/outputs/, data/processed/, or algorithms/[name]/).\n2. MANIFESTS: Add or update the entry in the relevant manifest (ANALYSIS_MANIFEST.md, DATA_MANIFEST.md, or ALGORITHM_MANIFEST.md).\n3. DOCUMENTATION: Update the subfolder documentation file (UPPER_SNAKE_CASE.md in the affected directory).\n7. CRYSTALLIZE: Read .living/learnings.md (tail -50). If 3+ entries share tags or themes, check .living/conventions.md for an existing convention on that topic. If one exists, append new Source: citations. If none exists, add a new convention with Source: citations linking to the originating learnings. Do not create near-duplicates.\n8. LOG REGISTRY: Update the current session row in .living/log/LOG_REGISTRY.md — replace the stub Summary with a 1-sentence past-tense accomplishment. Fill Key Outputs with semicolon-separated artifacts.\n9. CONVENTION FEEDBACK: If any installed convention pack practices were relevant to this work, note in .living/conventions.md whether they were helpful or had gaps.\n10. SESSION SUMMARY: Update .mycelium/last-session.md with cumulative 5-section summary (What worked on / Key decisions / Blockers / Current state / Next steps). Run git log --since=<session-start> to ground in facts."
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

mycelium_emit_context "PostToolUse" "$COMBINED"
