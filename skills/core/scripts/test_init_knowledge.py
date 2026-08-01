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

    def test_rejects_symlinked_memory_file_without_modifying_target(
        self, fake_projects: Path
    ) -> None:
        victim = fake_projects.parent / "private-memory.md"
        original = "# Host-private memory\n"
        victim.write_text(original)
        memory_dir = fake_projects / "linked" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").symlink_to(victim)

        with pytest.raises(ValueError, match="symlink"):
            ik.append_routing_to_memory_files(
                mycelium_root=_MYCELIUM_ROOT,
                claude_projects_dir=fake_projects,
            )

        assert victim.read_text() == original


class TestMigrateLegacyKnowledge:
    def test_rejects_symlinked_source_before_creating_destination(
        self, tmp_path: Path
    ) -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        victim = tmp_path / "private.md"
        original = "host-private knowledge\n"
        victim.write_text(original)
        (legacy / "safe.md").write_text("safe legacy knowledge\n")
        (legacy / "linked.md").symlink_to(victim)
        knowledge = tmp_path / "knowledge"

        with pytest.raises(ValueError, match="symlink"):
            ik.migrate_legacy_knowledge(legacy, knowledge)

        assert victim.read_text() == original
        assert not knowledge.exists()

    def test_rejects_symlinked_legacy_directory(self, tmp_path: Path) -> None:
        victim_dir = tmp_path / "private-knowledge"
        victim_dir.mkdir()
        (victim_dir / "private.md").write_text("host-private knowledge\n")
        legacy = tmp_path / "legacy"
        legacy.symlink_to(victim_dir, target_is_directory=True)
        knowledge = tmp_path / "knowledge"

        with pytest.raises(ValueError, match="symlink"):
            ik.migrate_legacy_knowledge(legacy, knowledge)

        assert not knowledge.exists()

    def test_rejects_symlinked_legacy_ancestor(self, tmp_path: Path) -> None:
        actual_parent = tmp_path / "actual-parent"
        legacy = actual_parent / "legacy"
        legacy.mkdir(parents=True)
        (legacy / "private.md").write_text("host-private knowledge\n")
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
        knowledge = tmp_path / "knowledge"

        with pytest.raises(ValueError, match="symlink"):
            ik.migrate_legacy_knowledge(linked_parent / "legacy", knowledge)

        assert not knowledge.exists()

    def test_rejects_dangling_destination_symlink(self, tmp_path: Path) -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "entry.md").write_text("safe legacy knowledge\n")
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        victim = tmp_path / "outside-destination.md"
        (knowledge / "entry.md").symlink_to(victim)

        with pytest.raises(ValueError, match="symlink"):
            ik.migrate_legacy_knowledge(legacy, knowledge)

        assert not victim.exists()

    def test_rejects_symlinked_destination_ancestor(self, tmp_path: Path) -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "entry.md").write_text("safe legacy knowledge\n")
        outside = tmp_path / "outside-knowledge"
        outside.mkdir()
        linked_parent = tmp_path / "linked-destination"
        linked_parent.symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="symlink"):
            ik.migrate_legacy_knowledge(legacy, linked_parent / "knowledge")

        assert not (outside / "knowledge").exists()


class TestInitKnowledgeSafety:
    def test_rejects_symlinked_knowledge_directory_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside-knowledge"
        outside.mkdir()
        knowledge = tmp_path / "knowledge"
        knowledge.symlink_to(outside, target_is_directory=True)
        monkeypatch.setattr(ik, "append_routing_to_memory_files", lambda *_: (0, 0))

        with pytest.raises(ValueError, match="symlink"):
            ik.init_knowledge(knowledge, _MYCELIUM_ROOT)

        assert list(outside.iterdir()) == []

    def test_rejects_symlinked_domain_before_any_managed_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        victim = tmp_path / "private-documentation.md"
        original = "host-private global knowledge\n"
        victim.write_text(original)
        (knowledge / "documentation.md").symlink_to(victim)
        monkeypatch.setattr(ik, "append_routing_to_memory_files", lambda *_: (0, 0))

        with pytest.raises(ValueError, match="symlink"):
            ik.init_knowledge(knowledge, _MYCELIUM_ROOT)

        assert victim.read_text() == original
        assert sorted(path.name for path in knowledge.iterdir()) == [
            "documentation.md"
        ]


class TestMemoryOnlyCli:
    def test_rejects_symlinked_projects_dir_without_modifying_memory(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        actual_projects = tmp_path / "actual-projects"
        actual_projects.mkdir()
        memory = _make_project(actual_projects, "linked", "# Private memory\n")
        original = memory.read_text()
        linked_projects = tmp_path / "linked-projects"
        linked_projects.symlink_to(actual_projects, target_is_directory=True)

        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "init_knowledge.py"),
                "--knowledge-dir",
                str(tmp_path / "unused-knowledge"),
                "--mycelium-root",
                str(_MYCELIUM_ROOT),
                "--projects-dir",
                str(linked_projects),
                "--memory-only",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "symlink" in result.stderr
        assert memory.read_text() == original

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
