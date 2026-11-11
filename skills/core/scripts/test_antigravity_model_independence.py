"""IDE helpers must not silently switch to a separately authenticated model."""
from pathlib import Path
import sys

import pytest

import generate_index

sys.path.insert(0, str(Path(__file__).parent / "knowledge_map"))
import concept_labeler


def test_ide_index_does_not_resolve_a_provider_cli(monkeypatch):
    monkeypatch.setenv("MYCELIUM_HOOK_HOST", "antigravity-ide")
    monkeypatch.setenv("MYCELIUM_AGENT_CLI", "claude")
    with pytest.raises(FileNotFoundError, match="Antigravity"):
        generate_index._agent_cli_command()


def test_ide_concept_labels_keep_the_offline_fallback(monkeypatch):
    monkeypatch.setenv("MYCELIUM_HOOK_HOST", "antigravity-ide")
    monkeypatch.setenv("MYCELIUM_AGENT_CLI", "codex")
    assert concept_labeler._resolve_agent_cli() is None


def test_installed_package_also_blocks_cli_fallback_without_hook_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("MYCELIUM_HOOK_HOST", raising=False)
    monkeypatch.setenv("MYCELIUM_AGENT_CLI", "claude")
    (tmp_path / ".antigravity-host").write_text("antigravity-ide\n")
    monkeypatch.setattr(generate_index, "__file__", str(tmp_path / "skills/core/scripts/generate_index.py"))
    monkeypatch.setattr(concept_labeler, "__file__", str(tmp_path / "skills/core/scripts/knowledge_map/concept_labeler.py"))
    with pytest.raises(FileNotFoundError, match="Antigravity"):
        generate_index._agent_cli_command()
    assert concept_labeler._resolve_agent_cli() is None


def test_app_host_also_keeps_the_selected_model(monkeypatch):
    monkeypatch.setenv("MYCELIUM_HOOK_HOST", "antigravity-app")
    monkeypatch.setenv("MYCELIUM_AGENT_CLI", "claude")
    with pytest.raises(FileNotFoundError, match="Antigravity"):
        generate_index._agent_cli_command()
    assert concept_labeler._resolve_agent_cli() is None
