#!/usr/bin/env python3
"""Tests for init_knowledge.py — focused on the MEMORY.md routing append step."""

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import init_knowledge as ik  # noqa: E402

# The mycelium repo root (this script lives at skills/core/scripts/)
_MYCELIUM_ROOT = (Path(__file__).resolve().parent / ".." / ".." / "..").resolve()


@pytest.fixture()
def fake_projects(tmp_path: Path) -> Path:
    """Build a fake `~/.claude/projects/<slug>/memory/MEMORY.md` layout."""
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _make_project(projects_dir: Path, slug: str, memory_content: str = "") -> Path:
    proj = projects_dir / slug / "memory"
    proj.mkdir(parents=True)
    memory = proj / "MEMORY.md"
    memory.write_text(memory_content, encoding="utf-8")
    return memory


class TestAppendRoutingToMemoryFiles:
    def test_appends_to_clean_memory_file(self, fake_projects: Path) -> None:
        m = _make_project(
            fake_projects, "alpha", "# Alpha Project Memory\n\nSome existing content.\n"
        )
        appended, skipped = ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        assert appended == 1
        assert skipped == 0

        text = m.read_text()
        assert ik.MEMORY_ROUTING_HEADER in text
        # Existing content preserved
        assert "Some existing content." in text
        # Header appears AFTER existing content
        assert text.index("Some existing content.") < text.index(ik.MEMORY_ROUTING_HEADER)

    def test_skips_when_header_already_present(self, fake_projects: Path) -> None:
        prefilled = (
            "# Beta Project Memory\n\n"
            "## Global Knowledge Domains\n\n"
            "(table already here)\n"
        )
        m = _make_project(fake_projects, "beta", prefilled)
        appended, skipped = ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        assert appended == 0
        assert skipped == 1

        # File untouched
        assert m.read_text() == prefilled

    def test_idempotent_rerun(self, fake_projects: Path) -> None:
        _make_project(fake_projects, "gamma", "# Gamma\n")
        ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        appended2, skipped2 = ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        # Second run is a no-op
        assert appended2 == 0
        assert skipped2 == 1

    def test_multiple_projects(self, fake_projects: Path) -> None:
        _make_project(fake_projects, "p1", "# P1\n")
        _make_project(fake_projects, "p2", "# P2\n")
        _make_project(
            fake_projects, "p3", "# P3\n\n## Global Knowledge Domains\n"
        )
        appended, skipped = ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        assert appended == 2
        assert skipped == 1

    def test_no_projects_dir_returns_zero_zero(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no-such-dir"
        appended, skipped = ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=nonexistent,
        )
        assert appended == 0
        assert skipped == 0

    def test_separator_inserted_when_missing_trailing_newline(
        self, fake_projects: Path
    ) -> None:
        # File ends without a newline
        m = _make_project(fake_projects, "delta", "# Delta\n## Some section\nNo trailing nl")
        ik.append_routing_to_memory_files(
            mycelium_root=_MYCELIUM_ROOT,
            claude_projects_dir=fake_projects,
        )
        text = m.read_text()
        # Existing content unbroken
        assert "No trailing nl" in text
        # Routing table present
        assert ik.MEMORY_ROUTING_HEADER in text
        # No mashed-together lines
        assert "No trailing nl## Global Knowledge Domains" not in text


class TestMemoryOnlyCli:
    def test_memory_only_flag_skips_domain_creation(
        self, fake_projects: Path, tmp_path: Path
    ) -> None:
        """--memory-only must not create domain files in --knowledge-dir."""
        import subprocess

        _make_project(fake_projects, "epsilon", "# Epsilon\n")
        knowledge_dir = tmp_path / "fake-knowledge"  # never created
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "init_knowledge.py"),
                "--knowledge-dir",
                str(knowledge_dir),
                "--mycelium-root",
                str(_MYCELIUM_ROOT),
                "--projects-dir",
                str(fake_projects),
                "--memory-only",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        # Knowledge dir should NOT have been created
        assert not knowledge_dir.exists()
        # MEMORY.md should be updated
        memory = fake_projects / "epsilon" / "memory" / "MEMORY.md"
        assert ik.MEMORY_ROUTING_HEADER in memory.read_text()
