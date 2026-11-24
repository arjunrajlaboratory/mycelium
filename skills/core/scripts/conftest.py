"""Shared pytest configuration for the core script tests.

The ``platform`` marker selects the tests whose behaviour can differ between
macOS and Linux: anything that runs Bash or the lifecycle hook scripts, takes
file locks, or depends on symlinks, path spelling, path-length limits, or
process timing. The Linux CI job runs the whole suite; the macOS job runs only
``pytest -m platform`` (issue #66). ``test_platform_marker.py`` fails when a
module that spawns a shell or a hook script is not marked.

Modules mark themselves with ``pytestmark = pytest.mark.platform``. In
addition, any test whose name says it exercises symlinks, canonical path
spelling, case-insensitive paths, path-length limits, file locks or xattrs
is marked here, so such tests in otherwise platform-neutral modules (path
safety checks in the ledger, contract, or note writers) still run on macOS.

``--shard K/N`` keeps only the tests whose id hashes to shard K, so the macOS
job can split the platform tests across parallel runners without losing any.
"""

from __future__ import annotations

import re
import zlib

import pytest

PLATFORM_NAME = re.compile(
    r"symlink|realpath|casefold|case_insensitive|path_max|flock|fcntl|apfs|xattr",
    re.IGNORECASE,
)


def pytest_addoption(parser):
    parser.addoption(
        "--shard",
        default=None,
        metavar="K/N",
        help="run only shard K of N (1-based), chosen by a stable hash of each "
        "test id; the macOS CI job splits the platform tests across runners",
    )


def parse_shard(value: str) -> tuple[int, int]:
    try:
        k, n = (int(part) for part in value.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--shard expects K/N, got {value!r}") from None
    if not 1 <= k <= n:
        raise pytest.UsageError(f"--shard {value}: K must be between 1 and N")
    return k, n


def shard_of(nodeid: str, n: int) -> int:
    return zlib.crc32(nodeid.encode("utf-8")) % n + 1


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "platform: behaviour can differ on macOS vs Linux (shell, hooks, "
        "locks, symlinks, paths, timing); the macOS CI job runs only these",
    )


@pytest.hookimpl(tryfirst=True)  # mark before `-m platform` deselects
def pytest_collection_modifyitems(config, items):
    for item in items:
        if PLATFORM_NAME.search(item.name):
            item.add_marker(pytest.mark.platform)
    shard = config.getoption("--shard")
    if shard:
        k, n = parse_shard(shard)
        keep = [item for item in items if shard_of(item.nodeid, n) == k]
        dropped = [item for item in items if shard_of(item.nodeid, n) != k]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
        items[:] = keep
