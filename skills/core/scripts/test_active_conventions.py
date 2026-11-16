#!/usr/bin/env python3
"""ACTIVE_CONVENTIONS.yaml stays one valid YAML shape across every writer (#129).

The registry is a bare top-level list of pack entries. ``init_repo`` seeds it,
``install_core_convention_packs`` rewrites it, ``install_convention`` appends
to it, and migration repairs files an older writer left broken. Every one of
those must leave a file that ``yaml.safe_load`` reads as a list.
"""

import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import init_repo as ir  # noqa: E402
import install_convention as ic  # noqa: E402
import migrate_existing_repos as mig  # noqa: E402

REGISTRY = Path(".living") / "conventions" / "ACTIVE_CONVENTIONS.yaml"

# The exact shape reported in #129: init's mapping seed with install_convention
# entries appended at column 0 underneath it.
BROKEN_129 = (
    "# Active Convention Packs\n"
    "# Updated by install_convention.py\n"
    "\n"
    "active_conventions: []\n"
    "- name: robust-analysis\n"
    "  path: .living/conventions/robust-analysis/\n"
    "  installed: 2026-04-01T20:22:21Z\n"
    "- name: idea-generator\n"
    "  path: .living/conventions/idea-generator/\n"
    "  installed: 2026-04-01T20:22:22Z\n"
)


def _load(repo: Path):
    return yaml.safe_load((repo / REGISTRY).read_text(encoding="utf-8"))


def _names(repo: Path) -> list[str]:
    data = _load(repo)
    assert isinstance(data, list), f"registry is not a list: {data!r}"
    return [entry["name"] for entry in data]


@pytest.fixture()
def seeded_repo(tmp_path: Path) -> Path:
    """A repo whose registry holds only init's seed (no core packs installed)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ir.create_directory_structure(repo)
    ir.create_living_layer(repo)
    return repo


def _write_registry(repo: Path, text: str) -> Path:
    path = repo / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestSeed:
    def test_seed_is_an_empty_list(self, seeded_repo: Path) -> None:
        assert _load(seeded_repo) == []

    def test_install_onto_seed_stays_valid(self, seeded_repo: Path) -> None:
        """Core packs not installed at init, then a pack is added (#129)."""
        ic.update_active_conventions(seeded_repo, "bioinformatics")
        assert _names(seeded_repo) == ["bioinformatics"]

    def test_two_installs_onto_seed(self, seeded_repo: Path) -> None:
        ic.update_active_conventions(seeded_repo, "bioinformatics")
        ic.update_active_conventions(seeded_repo, "image-analysis")
        assert _names(seeded_repo) == ["bioinformatics", "image-analysis"]


class TestInstallerRepairsExistingFiles:
    def test_repairs_issue_129_shape(self, tmp_path: Path) -> None:
        _write_registry(tmp_path, BROKEN_129)
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == [
            "robust-analysis",
            "idea-generator",
            "bioinformatics",
        ]

    def test_reinstalling_a_listed_pack_repairs_issue_129_shape(
        self, tmp_path: Path
    ) -> None:
        """#129: re-running the installer used to keep the broken shape."""
        _write_registry(tmp_path, BROKEN_129)
        ic.update_active_conventions(tmp_path, "robust-analysis")
        assert sorted(_names(tmp_path)) == ["idea-generator", "robust-analysis"]

    def test_dedents_entries_nested_under_the_key(self, tmp_path: Path) -> None:
        _write_registry(
            tmp_path,
            "active_conventions:\n"
            "  - name: robust-analysis\n"
            "    path: .living/conventions/robust-analysis/\n"
            "    core: true\n",
        )
        ic.update_active_conventions(tmp_path, "bioinformatics")
        data = _load(tmp_path)
        assert [e["name"] for e in data] == ["robust-analysis", "bioinformatics"]
        assert data[0]["core"] is True

    def test_canonical_file_keeps_comments_and_entries(self, tmp_path: Path) -> None:
        _write_registry(
            tmp_path,
            "# Active Convention Packs\n"
            "# hand note\n\n"
            "- name: robust-analysis\n"
            "  path: .living/conventions/robust-analysis/\n"
            "  core: true\n",
        )
        ic.update_active_conventions(tmp_path, "bioinformatics")
        text = (tmp_path / REGISTRY).read_text(encoding="utf-8")
        assert "# hand note" in text
        assert _load(tmp_path)[0] == {
            "name": "robust-analysis",
            "path": ".living/conventions/robust-analysis/",
            "core": True,
        }

    def test_reinstall_replaces_only_the_exact_name(self, tmp_path: Path) -> None:
        """A pack name that prefixes another must not remove the longer one."""
        _write_registry(
            tmp_path,
            "- name: bioinformatics\n"
            "  path: .living/conventions/bioinformatics/\n"
            "- name: bio\n"
            "  path: .living/conventions/bio/\n",
        )
        ic.update_active_conventions(tmp_path, "bio")
        assert _names(tmp_path) == ["bioinformatics", "bio"]

    def test_reinstall_matches_a_quoted_name(self, tmp_path: Path) -> None:
        _write_registry(
            tmp_path,
            '- name: "bioinformatics"\n'
            "  path: .living/conventions/bioinformatics/\n",
        )
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == ["bioinformatics"]

    def test_reinstalling_a_core_pack_keeps_its_core_flag(
        self, tmp_path: Path
    ) -> None:
        _write_registry(
            tmp_path,
            ic.format_registry_entry("robust-analysis", "2026-01-01", core=True)
            + "\n",
        )
        ic.update_active_conventions(tmp_path, "robust-analysis")
        assert _load(tmp_path)[0]["core"] is True

    def test_symlinked_registry_is_not_written_through(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.yaml"
        outside.write_text("[]\n", encoding="utf-8")
        (tmp_path / REGISTRY).parent.mkdir(parents=True)
        (tmp_path / REGISTRY).symlink_to(outside)
        with pytest.raises(ValueError):
            ic.update_active_conventions(tmp_path, "bioinformatics")
        assert outside.read_text(encoding="utf-8") == "[]\n"

    def test_replaces_atomically_and_preserves_stricter_mode(
        self, tmp_path: Path
    ) -> None:
        path = _write_registry(tmp_path, "[]\n")
        path.chmod(0o600)
        inode = path.stat().st_ino
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert path.stat().st_ino != inode, "registry was rewritten in place"
        assert path.stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("---\n- name: a\n", ["a", "bioinformatics"]),
            ("\ufeff- name: a\n", ["a", "bioinformatics"]),
            ("- name: a\n...\n", ["a", "bioinformatics"]),
            ("-\n  name: a\n", ["a", "bioinformatics"]),
            ("# c\n---\n[]\n", ["bioinformatics"]),
        ],
    )
    def test_accepts_other_valid_yaml_list_forms(
        self, tmp_path: Path, text: str, expected: list[str]
    ) -> None:
        _write_registry(tmp_path, text)
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == expected

    def test_keep_chomped_scalar_survives_another_install(
        self, tmp_path: Path
    ) -> None:
        """Codex P2: trailing blank lines can be part of a `|+` value."""
        _write_registry(tmp_path, "- name: a\n  notes: |+\n    text\n\n\n")
        ic.update_active_conventions(tmp_path, "b")
        data = _load(tmp_path)
        assert data[0]["notes"] == "text\n\n\n"
        assert [e["name"] for e in data] == ["a", "b"]

    def test_empty_list_comment_survives_an_install(self, tmp_path: Path) -> None:
        _write_registry(tmp_path, "[] # hand note\n")
        ic.update_active_conventions(tmp_path, "b")
        assert "# hand note" in (tmp_path / REGISTRY).read_text(encoding="utf-8")
        assert _names(tmp_path) == ["b"]

    def test_symlinked_conventions_directory_is_refused(self, tmp_path: Path) -> None:
        """Codex P2: a linked parent directory must not redirect the write."""
        repo = tmp_path / "repo"
        (repo / ".living").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "ACTIVE_CONVENTIONS.yaml").write_text("[]\n", encoding="utf-8")
        (repo / ".living" / "conventions").symlink_to(outside)
        with pytest.raises(ValueError):
            ic.update_active_conventions(repo, "b")
        assert (outside / "ACTIVE_CONVENTIONS.yaml").read_text(encoding="utf-8") == "[]\n"

    def test_unspaced_hash_is_part_of_the_name(self, tmp_path: Path) -> None:
        """YAML starts a comment only at a `#` preceded by whitespace."""
        _write_registry(tmp_path, "- name: foo#bar\n  path: p/\n")
        ic.update_active_conventions(tmp_path, "foo")
        assert _names(tmp_path) == ["foo#bar", "foo"]

    def test_unrecognized_registry_aborts_before_copying(self, tmp_path: Path) -> None:
        """The CLI refuses before copying the pack, leaving no partial install."""
        repo = tmp_path / "repo"
        path = _write_registry(repo, "version: 1\n- name: a\n")
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "install_convention.py"),
                "--name",
                "bioinformatics",
                "--target-dir",
                str(repo),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "unexpected top-level content" in result.stdout
        assert path.read_text(encoding="utf-8") == "version: 1\n- name: a\n"
        assert not (repo / ".living" / "conventions" / "bioinformatics").exists()

    def test_missing_file_is_created_as_a_list(self, tmp_path: Path) -> None:
        (tmp_path / REGISTRY).parent.mkdir(parents=True)
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == ["bioinformatics"]


class TestNormalize:
    @pytest.mark.parametrize(
        "text",
        [
            "active_conventions: []\n",
            "# c\n\nactive_conventions: []\n",
            "active_conventions:\n",
            "active_conventions: [] # nothing yet\n",
            "[]\n",
            "",
        ],
    )
    def test_empty_shapes_become_an_empty_list(self, text: str) -> None:
        assert yaml.safe_load(ic.normalize_active_conventions(text)) == []

    @pytest.mark.parametrize(
        "text, expected",
        [
            # A deeper comment before the first item sets no indent.
            (
                "active_conventions:\n    # note\n  - name: a\n    path: p/\n",
                [{"name": "a", "path": "p/"}],
            ),
            # A column-0 comment does not end the key's block.
            ("active_conventions:\n# note\n  - name: a\n", [{"name": "a"}]),
            # Blank lines inside the block do not end it either.
            (
                "active_conventions:\n  - name: a\n\n  - name: b\n",
                [{"name": "a"}, {"name": "b"}],
            ),
            # YAML's compact form: items at the key's own indent.
            ("active_conventions:\n- name: a\n", [{"name": "a"}]),
            # A top-level list indented as a whole (Codex P2).
            ("  - name: a\n    path: p/\n", [{"name": "a", "path": "p/"}]),
            ("# c\n  - name: a\n", [{"name": "a"}]),
            # An empty flow list on its own line under the key (Codex P2).
            ("active_conventions:\n  []\n", []),
        ],
    )
    def test_nested_block_variants(self, text: str, expected: list) -> None:
        assert yaml.safe_load(ic.normalize_active_conventions(text)) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "version: 1\nactive_conventions:\n  - name: a\n",
            "active_conventions:\n    - name: a\n  - name: b\n",
            "packs:\n  - name: a\n",
            "- name: a\n---\n- name: b\n",
        ],
    )
    def test_unrecognized_content_is_refused(self, text: str) -> None:
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic.normalize_active_conventions(text)

    def test_rewrite_check_refuses_output_that_is_not_a_list(self) -> None:
        """The PyYAML guard catches any shape the line rules get wrong."""
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic._check_rewrite("- name: a\n", "- name: a\n  path: [unclosed\n")

    def test_rewrite_check_refuses_changed_entries(self) -> None:
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic._check_rewrite("- name: a\n", "- name: b\n")

    def test_block_scalar_under_the_key_keeps_its_value(self) -> None:
        """Codex P2: a `#` line inside a block scalar is content, not a comment."""
        text = "active_conventions:\n  - name: a\n    notes: |\n      # note\n      more\n"
        assert yaml.safe_load(ic.normalize_active_conventions(text)) == [
            {"name": "a", "notes": "# note\nmore\n"}
        ]

    @pytest.mark.parametrize(
        "text",
        [
            BROKEN_129,
            "active_conventions:\n  - name: a\n    path: p/\n",
            "- name: a\n  path: p/\n",
        ],
    )
    def test_normalize_is_idempotent(self, text: str) -> None:
        once = ic.normalize_active_conventions(text)
        assert ic.normalize_active_conventions(once) == once
        assert isinstance(yaml.safe_load(once), list)


class TestFreshInit:
    def test_init_then_install_is_valid_yaml(self, tmp_path: Path) -> None:
        """End to end through the CLIs, as the issue asks."""
        repo = tmp_path / "proj"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            [sys.executable, str(_SCRIPT_DIR / "init_repo.py"), "--target-dir", str(repo)],
            check=True,
            capture_output=True,
        )
        core = _names(repo)
        assert core, "init installed no core packs"
        subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "install_convention.py"),
                "--name",
                "bioinformatics",
                "--target-dir",
                str(repo),
            ],
            check=True,
            capture_output=True,
        )
        assert _names(repo) == core + ["bioinformatics"]


class TestMigration:
    def test_repairs_issue_129_shape(self, tmp_path: Path) -> None:
        path = _write_registry(tmp_path, BROKEN_129)
        assert mig.repair_active_conventions(tmp_path) is True
        assert _names(tmp_path) == ["robust-analysis", "idea-generator"]
        assert "# Active Convention Packs" in path.read_text(encoding="utf-8")

    def test_second_run_is_a_byte_stable_noop(self, tmp_path: Path) -> None:
        path = _write_registry(tmp_path, BROKEN_129)
        mig.repair_active_conventions(tmp_path)
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        assert mig.repair_active_conventions(tmp_path) is False
        assert path.read_bytes() == before
        assert path.stat().st_mtime_ns == mtime

    def test_dry_run_reports_without_writing(self, tmp_path: Path) -> None:
        path = _write_registry(tmp_path, BROKEN_129)
        assert mig.repair_active_conventions(tmp_path, dry_run=True) is True
        assert path.read_text(encoding="utf-8") == BROKEN_129

    def test_seed_only_file_becomes_an_empty_list(self, tmp_path: Path) -> None:
        _write_registry(tmp_path, "# c\n\nactive_conventions: []\n")
        assert mig.repair_active_conventions(tmp_path) is True
        assert _load(tmp_path) == []

    @pytest.mark.parametrize(
        "text",
        [
            "- name: a\n  path: p/\n",
            "- name: a\n  path: p/",  # no trailing newline: not churned
            "- name: a\n  path: p/\n\n\n",  # trailing blank lines: not churned
            "[]\n",
            "[] # hand note\n",  # Codex P3: valid, so its comment stays
            "  - name: a\n    path: p/\n",  # Codex P2: valid indented list
            "- name: a\n  notes: |+\n    text\n\n\n",
        ],
    )
    def test_canonical_file_is_untouched(self, tmp_path: Path, text: str) -> None:
        path = _write_registry(tmp_path, text)
        assert mig.repair_active_conventions(tmp_path) is False
        assert path.read_text(encoding="utf-8") == text

    def test_unrecognized_file_is_left_alone(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "version: 1\nactive_conventions:\n  - name: a\n"
        path = _write_registry(tmp_path, text)
        assert mig.repair_active_conventions(tmp_path) is None
        assert path.read_text(encoding="utf-8") == text
        assert "left .living/conventions/ACTIVE_CONVENTIONS.yaml unchanged" in (
            capsys.readouterr().err
        )

    def test_repair_rejects_a_symlinked_registry(self, tmp_path: Path) -> None:
        """Pins repair's own guard, independent of migrate_one's tree check."""
        outside = tmp_path / "outside.yaml"
        outside.write_text(BROKEN_129, encoding="utf-8")
        (tmp_path / REGISTRY).parent.mkdir(parents=True)
        (tmp_path / REGISTRY).symlink_to(outside)
        with pytest.raises(ValueError):
            mig.repair_active_conventions(tmp_path)
        assert outside.read_text(encoding="utf-8") == BROKEN_129

    def test_missing_file_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".living").mkdir()
        assert mig.repair_active_conventions(tmp_path) is False
        assert not (tmp_path / REGISTRY).exists()

    def test_preserves_stricter_mode(self, tmp_path: Path) -> None:
        path = _write_registry(tmp_path, BROKEN_129)
        path.chmod(0o600)
        mig.repair_active_conventions(tmp_path)
        assert path.stat().st_mode & 0o777 == 0o600

    def test_symlinked_registry_is_rejected_before_any_write(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        (repo / ".living").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
        outside = tmp_path / "outside.yaml"
        outside.write_text(BROKEN_129, encoding="utf-8")
        (repo / REGISTRY).parent.mkdir(parents=True)
        (repo / REGISTRY).symlink_to(outside)
        with pytest.raises(ValueError):
            mig.migrate_one(repo)
        assert outside.read_text(encoding="utf-8") == BROKEN_129
        assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "# p\n"

    def test_migrate_one_reports_the_repair(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / ".living").mkdir(parents=True)
        _write_registry(repo, BROKEN_129)
        result = mig.migrate_one(repo)
        assert result["ACTIVE_CONVENTIONS.yaml shape"] == "applied"
        assert _names(repo) == ["robust-analysis", "idea-generator"]
        again = mig.migrate_one(repo)
        assert again["ACTIVE_CONVENTIONS.yaml shape"].startswith("skipped")

    def test_migrate_one_does_not_call_unrecognized_up_to_date(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        (repo / ".living").mkdir(parents=True)
        _write_registry(repo, "version: 1\nactive_conventions:\n  - name: a\n")
        result = mig.migrate_one(repo)
        status = result["ACTIVE_CONVENTIONS.yaml shape"]
        assert status == mig.UNRECOGNIZED_REGISTRY_STATUS
        assert "up-to-date" not in status
