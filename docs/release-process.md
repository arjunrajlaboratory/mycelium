# Mycelium Release Process

The release gate (issue #67) is one documented entry point that validates a
cross-host release candidate from one immutable tree. It never mutates the
repository and refuses a dirty or untracked working tree outright — release
from a clean disposable clone or worktree.

## Running the gate

```bash
python3 tools/release_gate.py --version X.Y.Z \
  --installed-root ~/.claude/plugins/cache/mycelium/mycelium/X.Y.Z \
  --claude-audit-evidence ../audits/claude-lifecycle.md \
  --codex-audit-evidence ../audits/codex-lifecycle.md \
  --output ../release-evidence.md
```

The evidence summary (and audit evidence you collect) live outside the
candidate tree: an in-repository `--output` path is rejected up front, since
the gate certifies the tree immutable and must not dirty it.

What it enforces, in order (fail-fast):

1. **Clean tree** — `git status --porcelain` must be empty; there is no
   `--force` or dirty-tree bypass.
2. **Version agreement** — `.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json` (`metadata.version`), and the base of
   `.codex-plugin/plugin.json` must all equal `--version`; an optional Codex
   suffix must be exactly one well-formed `+codex.<token>` cachebuster.
3. **Dated changelog** — `CHANGELOG.md` must contain a
   `## [X.Y.Z] - YYYY-MM-DD` section.
4. **Validation ladder** — the full Python/shell/manifest/diff gate from the
   cross-host review checklist, plus a fresh-init + `validate_structure.py`
   smoke in a temporary repository. Use `--skip-knowledge-map` only when the
   separately provisioned knowledge-map dependencies are unavailable, and
   say so in the release notes.
5. **Installed-artifact identity** — with `--installed-root`, every packaged
   file (`skills/`, `hooks/`, `.claude-plugin/`, `.codex-plugin/`) must be
   hash-identical between the candidate and the installed plugin, so a host
   smoke can never silently exercise a stale artifact. Without the flag the
   summary records the step as SKIPPED.
6. **Host audits** — natural-dispatch lifecycle audits
   (`$mycelium:lifecycle-audit`) on both Claude Code and Codex are a
   required release step: pass both evidence files, or explicitly
   `--waive-host-audits "reason"`; the choice is recorded in the evidence
   summary.

The gate prints (and with `--output` writes) a release-evidence summary
identifying the exact commit, semantic version, cachebuster, every check
result, host-audit status, and the intended tag — suitable for a PR comment
or release notes.

## Tagging

The summary's next steps restate the canonical sequence:

1. Before merge: verify `claude plugin tag --dry-run` resolves
   `mycelium--vX.Y.Z`.
2. After merge: `git tag mycelium--vX.Y.Z && git push origin
   mycelium--vX.Y.Z`.

## CI platform coverage

Pull requests run the required `validate` job on `ubuntu-latest` and the
platform-sensitive subset (`validate-macos`) on `macos-latest` — lifecycle
shell suites, Python compatibility tests, and the fresh-init smoke — because
BSD/GNU differences in `date`, `stat`, `sed`, `mktemp`, and shell behavior
have produced real regressions (issue #66). Two-platform CI evidence plus
the release gate constitute the release baseline recorded in
`docs/cross-host-review-checklist.md`.
