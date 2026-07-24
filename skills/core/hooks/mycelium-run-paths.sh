# mycelium-run-paths.sh — shared helper sourced by the mycelium hook bundle.
#
# Points the per-session runtime sentinels at a session-scoped directory so
# that multiple concurrent Claude chats in the SAME working tree don't clobber
# each other's session state. The scoping key is the Claude `session_id` from
# the hook stdin payload: subagents SHARE their parent's session_id (so they
# group into the parent's session correctly) while independent chats get
# distinct ones (so they stay isolated). The shared .living/ knowledge base is
# deliberately NOT scoped — only the transient per-session bookkeeping is.
#
# Contract: the caller must already have $INPUT (hook stdin JSON) and
# $REPO_ROOT set. Sourcing this sets: MYC_RUN_DIR plus the sentinel path vars
# ACTIVE_LOG_FILE, START_TS_FILE, REMINDER_FILE, ACTIVITY_FILE, EVENTS_FILE.
#
# Back-compat: with no session_id (older Claude Code, or unit tests that craft
# stdin without one) it falls back to the legacy flat .claude/ paths, so
# behaviour is byte-identical to before scoping.

# Extract session_id via python3 (already a hard dep of the bundle; avoids
# assuming jq is on PATH at SessionStart). Sanitise to filesystem-safe chars
# so the id can be used directly as a directory name.
_myc_sid=$(printf '%s' "${INPUT:-}" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('session_id') or '')" \
  2>/dev/null || echo "")
_myc_sid=$(printf '%s' "$_myc_sid" | tr -cd 'A-Za-z0-9._-')

if [ -n "$_myc_sid" ]; then
  MYC_RUN_DIR="$REPO_ROOT/.claude/mycelium/run/$_myc_sid"
  MYC_SCOPED=1  # MYC_RUN_DIR is a private per-session dir, safe to rm -rf
else
  MYC_RUN_DIR="$REPO_ROOT/.claude"  # legacy flat layout (no session_id available)
  MYC_SCOPED=""  # MYC_RUN_DIR IS .claude — never rm -rf it
fi
mkdir -p "$MYC_RUN_DIR" 2>/dev/null || true

ACTIVE_LOG_FILE="$MYC_RUN_DIR/active-session-log.tmp"
START_TS_FILE="$MYC_RUN_DIR/session-start-ts.tmp"
REMINDER_FILE="$MYC_RUN_DIR/mycelium-reminded.tmp"
ACTIVITY_FILE="$MYC_RUN_DIR/mycelium-session-activity.tmp"
EVENTS_FILE="$MYC_RUN_DIR/mycelium-data-events.tmp"
