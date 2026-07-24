#!/usr/bin/env python3
"""Atomically allocate the next free session-log slot for a given day.

Replaces the "count today's logs + 1" logic in mycelium-health.sh, which is a
TOCTOU race: two chats starting the same day both count N and both pick N+1,
producing the same session id / filename and clobbering one log.

Usage:
    python3 allocate_session_slot.py <log_dir> <date> <slug>

Finds the lowest NNN (>=1, zero-padded to 3) for which
``<date>-NNN-<slug>.md`` does not yet exist, creates that file atomically with
O_CREAT|O_EXCL (reserving the slot against concurrent allocators), and prints
``<date>-NNN<TAB><abs path>``. The caller overwrites the reserved (empty) file
with the real frontmatter.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: allocate_session_slot.py <log_dir> <date> <slug>", file=sys.stderr)
        return 1

    log_dir, date, slug = argv[1], argv[2], argv[3]
    os.makedirs(log_dir, exist_ok=True)

    n = 1
    while True:
        session_id = f"{date}-{n:03d}"
        path = os.path.join(log_dir, f"{session_id}-{slug}.md")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        print(f"{session_id}\t{os.path.abspath(path)}")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
