# log_rotation.sh — shared rotation helper for mycelium hook error logs.
# Source this file; do not execute directly.
#
# Usage:
#   source "${SCRIPT_DIR}/_lib/log_rotation.sh"
#   _mycelium_rotate_error_log "${REPO_ROOT}/.claude/mycelium-hook-errors.log"

_mycelium_rotate_error_log() {
    local errlog="$1"
    [ -f "${errlog}" ] || return 0
    local size
    size=$(wc -c < "${errlog}" 2>/dev/null || echo 0)
    if [ "${size}" -gt 10485760 ]; then
        mv "${errlog}" "${errlog}.prev" 2>/dev/null || true
        : > "${errlog}"
    fi
}
