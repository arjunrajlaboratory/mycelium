"""Whole-operation safety tests for the knowledge-map CLI."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).parent / "cli.py"


def test_build_rejects_linked_managed_output_before_lock_creation(tmp_path: Path) -> None:
    portfolio = tmp_path / "portfolio"
    graph = portfolio / ".living" / "graph"
    graph.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("preserve\n")
    (graph / "knowledge-graph.json").symlink_to(outside)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "build", "--portfolio", str(portfolio)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "unsafe knowledge-map managed path" in result.stderr
    assert outside.read_text() == "preserve\n"
    assert not (portfolio / ".mycelium").exists()
