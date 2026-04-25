# log_rotation.sh — shared helpers for mycelium hook log files.
# Source this file; do not execute directly.
#
# Public API:
#   _mycelium_rotate_log <logfile>            — rotate if >10MB (no lock; best-effort)
#   _mycelium_append_log_locked <logfile> <line>
#                                              — flock + rotate-check + append (atomic)
#   _mycelium_tsv_sanitize <value>            — echo value with \t\r\n → space
#
# Back-compat:
#   _mycelium_rotate_error_log <logfile>      — alias for _mycelium_rotate_log

_mycelium_rotate_log() {
    local logfile="$1"
    [ -f "${logfile}" ] || return 0
    local size
    size=$(wc -c < "${logfile}" 2>/dev/null || echo 0)
    if [ "${size}" -gt 10485760 ]; then
        mv "${logfile}" "${logfile}.prev" 2>/dev/null || true
        : > "${logfile}"
    fi
}

_mycelium_rotate_error_log() {
    _mycelium_rotate_log "$1"
}

_mycelium_tsv_sanitize() {
    # Replace tab, CR, LF with single space. Use printf+tr to avoid subshell tricks.
    printf '%s' "$1" | tr '\t\r\n' '   '
}

_mycelium_append_log_locked() {
    local logfile="$1"
    local line="$2"
    local lockfile="${logfile}.lock"
    mkdir -p "$(dirname "${logfile}")" 2>/dev/null || true
    if command -v flock >/dev/null 2>&1; then
        (
            exec 9>"${lockfile}"
            flock 9
            _mycelium_rotate_log "${logfile}"
            printf '%s\n' "${line}" >> "${logfile}"
        ) 2>/dev/null || true
    else
        # Fallback: best-effort, no lock. Single printf >> is line-atomic for short writes
        # on most local filesystems but is not POSIX-guaranteed across processes.
        _mycelium_rotate_log "${logfile}" 2>/dev/null || true
        printf '%s\n' "${line}" >> "${logfile}" 2>/dev/null || true
    fi
}
