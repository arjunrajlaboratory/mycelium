#!/usr/bin/env python3
"""ACTIVE_CONVENTIONS.yaml stays one valid YAML shape across every writer (#129).

The registry is a bare top-level list of pack entries. ``init_repo`` seeds it,
``install_core_convention_packs`` rewrites it, ``install_convention`` appends
to it, and migration repairs files an older writer left broken. Every one of
those must leave a file that ``yaml.safe_load`` reads as a list.
"""

import json
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
            ("# c\n---\n[]\n", ["bioinformatics"]),
        ],
    )
    def test_accepts_other_valid_yaml_list_forms(
        self, tmp_path: Path, text: str, expected: list[str]
    ) -> None:
        _write_registry(tmp_path, text)
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == expected

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

    def test_capitalized_core_flag_is_kept(self, tmp_path: Path) -> None:
        _write_registry(tmp_path, "- name: a\n  core: True\n")
        ic.update_active_conventions(tmp_path, "a")
        assert _load(tmp_path)[0]["core"] is True

    def test_refused_update_leaves_an_installed_pack_untouched(
        self, tmp_path: Path
    ) -> None:
        """Codex P2: the CLI validates the exact rewrite before copying."""
        repo = tmp_path / "repo"
        path = _write_registry(repo, "version: 1\n- name: bioinformatics\n")
        pack = repo / ".living" / "conventions" / "bioinformatics"
        pack.mkdir()
        (pack / "local-edit.md").write_text("keep\n", encoding="utf-8")
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
        assert (pack / "local-edit.md").read_text(encoding="utf-8") == "keep\n"
        assert path.read_text(encoding="utf-8") == "version: 1\n- name: bioinformatics\n"

    def test_name_specific_refusal_happens_before_copying(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preflight runs the exact rewrite for this pack name, not a generic read."""
        repo = tmp_path / "repo"
        _write_registry(repo, "- name: a\n")
        pack = repo / ".living" / "conventions" / "bioinformatics"
        pack.mkdir()
        (pack / "local-edit.md").write_text("keep\n", encoding="utf-8")

        def refuse(target_dir: Path, name: str, now: str) -> str:
            assert name == "bioinformatics"
            raise ic.UnrecognizedRegistryError("refused for this name")

        monkeypatch.setattr(ic, "_plan_update", refuse)
        monkeypatch.setattr(
            sys, "argv", ["install_convention.py", "--name", "bioinformatics",
                          "--target-dir", str(repo)],
        )
        with pytest.raises(SystemExit):
            ic.main()
        assert (pack / "local-edit.md").read_text(encoding="utf-8") == "keep\n"

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
        assert "unsupported registry line" in result.stdout
        assert path.read_text(encoding="utf-8") == "version: 1\n- name: a\n"
        assert not (repo / ".living" / "conventions" / "bioinformatics").exists()

    def test_field_after_a_blank_line_stays_with_its_entry(
        self, tmp_path: Path
    ) -> None:
        """Codex P2: a blank line inside an entry must not end it."""
        _write_registry(
            tmp_path,
            "- name: robust-analysis\n  path: original/\n"
            "- name: bioinformatics\n  path: old/\n\n  core: true\n",
        )
        ic.update_active_conventions(tmp_path, "bioinformatics")
        data = _load(tmp_path)
        assert data[0] == {"name": "robust-analysis", "path": "original/"}
        assert data[1]["name"] == "bioinformatics" and data[1]["core"] is True

    def test_name_on_a_later_field_line_is_matched(self, tmp_path: Path) -> None:
        """Codex P2: the name need not be the entry's first field."""
        _write_registry(tmp_path, "- path: old/\n  name: bioinformatics\n")
        ic.update_active_conventions(tmp_path, "bioinformatics")
        assert _names(tmp_path) == ["bioinformatics"]

    def test_commented_core_flag_is_kept(self, tmp_path: Path) -> None:
        """Codex P2: a trailing comment does not hide the flag."""
        _write_registry(tmp_path, "- name: a\n  core: true # required\n")
        ic.update_active_conventions(tmp_path, "a")
        assert _load(tmp_path)[0]["core"] is True

    @pytest.mark.parametrize("pyyaml", [True, False])
    def test_quoted_true_is_not_the_core_flag(
        self, tmp_path: Path, pyyaml: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2: `core: "true"` is a string; reinstall must not make it a flag."""
        if not pyyaml:
            monkeypatch.setattr(ic, "_yaml", lambda: None)
        _write_registry(tmp_path, '- name: a\n  core: "true"\n')
        ic.update_active_conventions(tmp_path, "a")
        assert "core" not in _load(tmp_path)[0]

    @pytest.mark.parametrize("pyyaml", [True, False])
    @pytest.mark.parametrize("name", ["on", "No", "null", "123", "1e3", "a b"])
    def test_yaml_sensitive_names_round_trip(
        self, tmp_path: Path, name: str, pyyaml: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2: a name YAML would read as bool/null/number stays a string."""
        if not pyyaml:
            monkeypatch.setattr(ic, "_yaml", lambda: None)
        _write_registry(tmp_path, f"- name: {json.dumps(name)}\n")
        ic.update_active_conventions(tmp_path, name)
        assert _names(tmp_path) == [name]

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

    def test_compact_form_under_the_key(self) -> None:
        """YAML lets a key's list sit at the key's own indent."""
        assert yaml.safe_load(
            ic.normalize_active_conventions("active_conventions:\n- name: a\n")
        ) == [{"name": "a"}]

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

    @pytest.mark.parametrize(
        "text",
        [
            "active_conventions:\n  owner: lab\n",
            "  owner: lab\n",
            "active_conventions:\n  # c\n  owner: lab\n",
        ],
    )
    def test_non_list_block_is_refused_without_pyyaml(
        self, text: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2: the line rules alone must not dedent a mapping."""
        monkeypatch.setattr(ic, "_yaml", lambda: None)
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic.normalize_active_conventions(text)

    def test_issue_129_repair_without_pyyaml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ic, "_yaml", lambda: None)
        assert yaml.safe_load(ic.normalize_active_conventions(BROKEN_129)) == [
            {"name": "robust-analysis", "path": ".living/conventions/robust-analysis/",
             "installed": yaml.safe_load("2026-04-01T20:22:21Z")},
            {"name": "idea-generator", "path": ".living/conventions/idea-generator/",
             "installed": yaml.safe_load("2026-04-01T20:22:22Z")},
        ]

    def test_rewrite_check_refuses_output_that_is_not_a_list(self) -> None:
        """The PyYAML guard catches any shape the line rules get wrong."""
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic._check_rewrite("- name: a\n", "- name: a\n  path: [unclosed\n")

    def test_rewrite_check_refuses_changed_entries(self) -> None:
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic._check_rewrite("- name: a\n", "- name: b\n")

    @pytest.mark.parametrize(
        "text",
        [
            BROKEN_129,
            "active_conventions:\n- name: a\n  path: p/\n",
            "- name: a\n  path: p/\n",
        ],
    )
    def test_normalize_is_idempotent(self, text: str) -> None:
        once = ic.normalize_active_conventions(text)
        assert ic.normalize_active_conventions(once) == once
        assert isinstance(yaml.safe_load(once), list)


# Valid YAML outside the machine-written grammar, collected across review
# rounds. Each must be refused with nothing written, whether or not PyYAML is
# installed: a line editor cannot rewrite these safely.
OUT_OF_GRAMMAR = [
    "active_conventions:\n  - name: a\n    path: p/\n",  # nested under the key
    "active_conventions:\n    # note\n  - name: a\n",
    "active_conventions:\n  - name: a\n    notes: |\n      # note\n",
    "active_conventions:\n  owner: lab\n",  # mapping, not a list
    "active_conventions:\n  []\n",
    "  - name: a\n    path: p/\n",  # list indented as a whole
    "-\n  name: a\n",  # bare dash item
    "- name: a\n  notes: |+\n    text\n\n\n",  # block scalar
    "- name: a\n  notes: |\n    core: true\n",
    "- name: a\n  path: &p shared/\n- name: b\n  path: *p\n",  # anchor/alias
    "[{name: robust-analysis, path: p/}]\n",  # flow list
    '- name: a\n  notes: "first\nactive_conventions: []\n    last"\n',  # quoted multi-line
    "- name: a\n  notes: x\u2028y\n",  # Unicode line separator
    "- name: foo#bar\n",  # `#` without a preceding space is scalar text
    "- name: a\n  name: b\n",  # duplicate key
    "version: 1\nactive_conventions:\n  - name: a\n",
]


class TestOutOfGrammar:
    @pytest.mark.parametrize("pyyaml", [True, False])
    @pytest.mark.parametrize("text", OUT_OF_GRAMMAR)
    def test_installer_refuses_without_writing(
        self, tmp_path: Path, text: str, pyyaml: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not pyyaml:
            monkeypatch.setattr(ic, "_yaml", lambda: None)
        path = _write_registry(tmp_path, text)
        with pytest.raises(ic.UnrecognizedRegistryError):
            ic.update_active_conventions(tmp_path, "a")
        assert path.read_text(encoding="utf-8") == text

    @pytest.mark.parametrize("pyyaml", [True, False])
    @pytest.mark.parametrize("text", OUT_OF_GRAMMAR)
    def test_migration_leaves_file_byte_identical(
        self, tmp_path: Path, text: str, pyyaml: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if not pyyaml:
            monkeypatch.setattr(ic, "_yaml", lambda: None)
        path = _write_registry(tmp_path, text)
        assert mig.repair_active_conventions(tmp_path) in (False, None)
        assert path.read_text(encoding="utf-8") == text


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
