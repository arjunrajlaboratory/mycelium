# Cross-Host Compatibility Review Checklist

Use this checklist for changes that can affect Mycelium on Claude Code or
Codex, especially plugin packaging, shared skills, lifecycle hooks,
initialization, migration, session accounting, and data lineage.

The goal is to review the exact release candidate as a system. Passing unit
tests for an individual script is not sufficient when a change crosses host,
hook, filesystem, or lifecycle boundaries.

## How to Use This Checklist

1. Record the exact base commit and head commit being reviewed.
2. Review the complete `base...head` diff, including generated or packaged
   files, rather than only the most recent commit.
3. Mark every item **Pass**, **Fail**, or **N/A**, with a test, command, or code
   reference as evidence.
4. Generalize each defect into an error pattern and search the whole branch for
   other instances.
5. Add a regression test for every fixed defect. Add a checklist item when the
   defect reveals a review category that was previously missing.
6. Re-run the full gate on the final commit after all fixes. Do not rely on CI
   or a review performed against an earlier commit.

## 1. Review Scope and Branch State

- [ ] Record the base commit, head commit, branch, and pull request.
- [ ] Confirm the working tree is clean or account for every local change.
- [ ] Review the complete diff from the merge base to the exact head.
- [ ] Inspect added, deleted, renamed, executable, and symlinked files.
- [ ] Separate current findings from comments made against older commits.
- [ ] Search for every instance of each defect pattern, not only the line that
      exposed it.
- [ ] Confirm that test and documentation changes describe the implemented
      behavior rather than the intended behavior.

## 2. Plugin Packaging and Discovery

### Codex

- [ ] `.codex-plugin/plugin.json` parses and contains the expected identity,
      skill path, interface metadata, and release version.
- [ ] The Codex and Claude plugin manifests use the same package name, base
      version, and shared skill directory; an optional Codex-only cachebuster
      is a single nonempty `+codex.<token>` suffix.
- [ ] Every `skills/*/SKILL.md` has valid `name` and `description` frontmatter,
      and the name matches its directory.
- [ ] Every shared skill has valid `agents/openai.yaml` metadata.
- [ ] `hooks/hooks.json` parses and remains the plugin-level source of Codex
      lifecycle registration.
- [ ] The Codex hook configuration contains the intended five command
      registrations: SessionStart, two Bash PostToolUse handlers, one
      `apply_patch` PostToolUse handler, and Stop.
- [ ] Hook commands dispatch through `${PLUGIN_ROOT}` and do not contain a
      versioned cache path, a developer-machine path, or a repository-local
      installation assumption.
- [ ] The dispatcher resolves only packaged hook scripts and rejects traversal,
      unexpected names, and unsafe roots.
- [ ] A globally dispatched hook performs no project write before the shared
      `.living`/`.mycelium` containment and symlink checks have succeeded.
- [ ] Loading the plugin through Claude Code does not execute the Codex adapter
      that Claude also discovers at `hooks/hooks.json`; native Claude hooks
      still produce one, not duplicated, lifecycle effect.
- [ ] Installation works from the packaged plugin, not only from a source
      checkout.
- [ ] Real-host processes exercise one immutable candidate: no source edit,
      reinstall, or cache replacement occurs between SessionStart and Stop.

### Claude Code

- [ ] `.claude-plugin/plugin.json` and marketplace metadata still parse and
      expose the shared skills as expected.
- [ ] Existing Claude commands and skill discovery remain unchanged unless the
      release explicitly documents a breaking change.
- [ ] Repository-local Claude hook registrations retain all required lifecycle
      handlers and do not acquire Codex-only assumptions.
- [ ] Context-bearing Claude SessionStart and PostToolUse hooks return
      `additionalContext` inside `hookSpecificOutput` with the matching
      `hookEventName`; decision and universal control fields remain at their
      documented levels, and a fresh real task confirms the model receives the
      context rather than merely showing successful hook stdout.
- [ ] Standalone or obsolete lineage Stop handlers are not reintroduced.
- [ ] Custom user instructions, hooks, and existing `.living` content survive
      initialization and migration.

## 3. Installation, Upgrade, and Migration

- [ ] A fresh repository initializes successfully for each supported host.
- [ ] Re-running initialization is idempotent.
- [ ] Dry-run migration performs no writes.
- [ ] Actual migration is idempotent and validates successfully afterward.
- [ ] Migration dry-run detects, and actual migration narrowly refreshes, a
      generated `MYCELIUM.md` session-summary rule that still directs all roots
      to shared `.mycelium/last-session.md`; nearby project guidance is
      preserved and a second migration is a no-op.
- [ ] A migration action reported as skipped does not rewrite byte-identical
      managed configuration or churn its timestamp; deliberate data refreshes
      such as INDEX regeneration are identified separately.
- [ ] Initialization and migration preflight every repository-controlled output
      they may mutate—including guidance, hook configuration, todo, index, and
      runtime state—before the first write, so a later rejection cannot leave a
      partial migration.
- [ ] Inputs that can be copied or summarized into project or global state (for
      example legacy session context, living-layer entries, legacy global
      knowledge, and provider MEMORY files) reject symlinks in the file itself
      and in every ancestor before any read or write.
- [ ] Global initialization and migration preflight every source and destination
      before the first mutation; command-line path normalization preserves
      symlink evidence instead of resolving it away before validation.
- [ ] Legacy Claude-only repositories continue to work without mandatory
      migration unless a migration is explicitly documented.
- [ ] Early Codex installations with repository-local `.codex/hooks.json` are
      migrated or preserved safely.
- [ ] Mixed user and Mycelium hook configurations preserve unrelated entries.
- [ ] Malformed, partial, and interrupted configurations fail safely without
      truncating user files.
- [ ] Stale versioned plugin paths and obsolete hook registrations are removed
      only when they can be identified unambiguously.
- [ ] Legacy runtime state is migrated, ignored, or cleaned deliberately; it is
      never mistaken for a live current session.
- [ ] The documented install, update, trust, restart, and migration instructions
      match the current Claude Code and Codex interfaces.

## 4. Lifecycle State Machine

- [ ] SessionStart handles fresh, resumed, cleared, compacted, and stale-crash
      sessions.
- [ ] Session identifiers and log names are unique under rapid or concurrent
      starts.
- [ ] Fresh initialization on an unborn Git branch records one valid branch
      scalar; a failing command's partial stdout is replaced rather than
      concatenated with fallback text.
- [ ] Every identified root publishes its owner token and active marker inside
      `.mycelium/run/<host>/<session-id>/`. A distinct root ID or host receives
      an independent transaction and log without reading, superseding, or
      cleaning another root's state. Dedicated subagent lifecycle events retain
      the parent's session ID.
- [ ] A present host session ID must be a JSON string, match the documented
      character/length policy, and not equal the special path components `.` or
      `..`. Wrongly typed or path-aliasing IDs fail before shared preparation and
      never downgrade to identity-free legacy state.
- [ ] Resuming a live root retains its reminder, activity, lineage, retry, and
      baseline sentinels; stale recovery is confined to that exact root's run
      directory and preserves abandoned audit evidence.
- [ ] Every PostToolUse writer resolves an existing private transaction,
      acquires its per-session lock, then revalidates owner and marker before
      mutation, so an event queued before accepted Stop cannot write afterward.
- [ ] A host-identified PostToolUse event with no active transaction is rejected
      as late; only identity-free legacy payloads retain markerless behavior.
- [ ] Missing or corrupt new-format ownership fails closed. Timestamp matching
      is used only for active legacy sessions that have no owner-token file;
      the active marker identifies new-format ownership so a missing companion
      token cannot silently downgrade the transaction.
- [ ] The active-log marker has a documented, versioned format, and every
      reader parses that format rather than treating the whole file as a path.
- [ ] Ownership-token readers require exactly one validated line; blank lines
      or trailing content cannot be hidden beyond the first value.
- [ ] Every active-log reader validates the marker path as a regular file under
      `.living/log`, validates the owner timestamp, and never emits, follows, or
      trusts a corrupt marker supplied by repository state.
- [ ] A no-work session exits without creating misleading activity or lineage.
- [ ] A lineage-only session reserves and uses a consistent session identifier.
- [ ] The first blocked Stop preserves the active log, baseline, raw events, and
      enforcement state needed by the continuation.
- [ ] A failed registry, lineage, or context write followed by a resumed or
      compacted SessionStart preserves the same unfinalized log, session ID, and
      baselines so Stop can retry the original transaction.
- [ ] Work performed after a blocked Stop appears in the eventual final log and
      lineage output.
- [ ] Only an accepted Stop finalizes the log and registry entry and removes or
      archives active state.
- [ ] Stop continuation messages identify the unmet requirement precisely and
      do not enter an infinite retry loop.
- [ ] Stop preserves the authored content of a fresh, complete five-section
      handoff, where every required heading occurs exactly once in order and
      every section has a nonblank body after obsolete lifecycle prose is
      removed. Cleanup and completeness validation share one implementation,
      and the finalizer enforces that post-cleanup contract before writing. It
      atomically publishes an authoritative accepted-status block; an absent,
      stale, malformed, partial, or cleanup-emptied handoff is replaced
      atomically with a complete fallback. Only an accepted Stop publishes the
      private handoff to shared `.mycelium/last-session.md`.
- [ ] Stop updates factual registry fields without overwriting an agent-authored
      Summary, Key Outputs, or Tags for the same session.
- [ ] Cleanup and retention policies distinguish active, accepted, stale, and
      corrupt state.

## 5. Execution and Working-Directory Inference

- [ ] Direct Python, R, and Jupyter execution is detected.
- [ ] Absolute, versioned, virtual-environment, and PATH-resolved interpreter
      names are detected, including quoted or backslash-escaped paths with
      whitespace.
- [ ] Interpreter flags, `-m` modules, and inline execution forms are handled
      according to the documented policy.
- [ ] Interpreter help/version options that terminate before a later payload
      are rejected, including short-option clusters and equivalent Python, R,
      Rscript, and Jupyter forms.
- [ ] Jupyter configuration-only modes such as `--show-config`,
      `--show-config-json`, and `--generate-config` are rejected before or
      after an apparent notebook, without suppressing a neighboring command or
      option value that merely contains similar text. Scanning continues across
      shell redirections, while option-looking redirection targets are excluded
      from Jupyter's argv.
- [ ] Python `-c` and R `-e` source is parsed as one complete shell word, then
      decoded with shell semantics; escaped quotes and adjacent quoted
      components cannot truncate the source before a data reference.
- [ ] Common environment/package wrappers such as `uv`, `conda`, `poetry`, and
      `/usr/bin/env`, plus execution wrappers such as `time`, `exec`, `nice`,
      and `timeout`, are detected (including nested forms) without confusing
      wrapper arguments or option values for executed scripts.
- [ ] Wrapper options that rewrite argv, such as `env -S`/`--split-string`, are
      either modeled completely or rejected conservatively; a later
      interpreter-looking argument is never treated as the executed program.
- [ ] Analysis CLI option arity is parsed before positional inputs are selected;
      separated and assignment-form option values that resemble scripts or
      notebooks cannot be attributed as executed inputs.
- [ ] Wrapper options that change cwd, including `env -C`/`--chdir`,
      `conda run --cwd`, and `uv run --directory`, are applied in nesting order
      before script, input, and output paths are resolved.
- [ ] AND, OR, pipelines, subshells, heredocs, comments, and quoted text are
      interpreted conservatively, with tests for both false positives and false
      negatives.
- [ ] Multiline Python/R control words and heredoc-like text inside quoted
      arguments are not mistaken for shell structure; tooling exclusions are
      based on the executed program/module/script rather than substrings in
      ordinary quoted or unquoted arguments.
- [ ] Exit evidence is associated with the command that actually executed.
- [ ] Codex model-facing `tool_response` forms are covered, including code-mode
      `input_text` arrays whose text contains a serialized structured result;
      nonzero exit status is retained in lineage and failed edits do not count
      as successful activity.
- [ ] The canonical current Codex Bash PostToolUse payload with an empty
      `tool_response` is covered; analysis is still attributed, but unknown
      exit status and wall time remain null rather than being fabricated.
- [ ] Failed commands that are provably reached through a known-success AND
      prefix, or started as a pipeline component, still produce lineage; shell
      operators appearing only inside an unquoted comment produce nothing.
- [ ] Nested and changed working directories resolve script and output paths
      correctly.
- [ ] Failed or conditional `cd` commands do not change the inferred directory.
- [ ] Symlink aliases, quoted and backslash-escaped whitespace, shell
      metacharacters inside quotes, and non-ASCII path components do not
      silently drop valid script or notebook activity.
- [ ] Adjacent quoted, unquoted, and backslash-escaped components that form one
      shell word (for example `"analysis script".py` or
      `'analysis'\ script.py`) are decoded as one interpreter, flag value, or
      script path rather than silently dropped.
- [ ] Interpreter and script regexes require a complete shell-word boundary;
      suffixes such as `"analysis.py".bak`, `"a.R".bak`, and
      `"a.ipynb".bak` cannot be attributed to the shorter quoted filename,
      while adjacent shell redirections remain valid word boundaries.
- [ ] Inferred paths are canonicalized and proven to remain within the project
      before they are trusted or written.
- [ ] `apply_patch` activity is recorded only after a successful tool result,
      and relative paths are normalized against the actual repository root.

## 6. Git and Session Accounting

- [ ] The session baseline accounts for pre-existing tracked, dirty, staged,
      untracked, and deleted files.
- [ ] New, modified, deleted, renamed, and committed files are attributed to the
      session correctly.
- [ ] Filenames containing spaces, tabs, Unicode, and leading punctuation are
      handled without line-oriented parsing errors.
- [ ] Commits, amendments, rebases, checkouts to older commits, and branch
      switches do not hide session work.
- [ ] A temporary branch that returns to the original tree is accounted for
      according to the documented policy.
- [ ] Reflog absence, pruning, or an unborn HEAD fails conservatively.
- [ ] Exclusions for `.living`, runtime state, and generated metadata are
      intentional and do not suppress real scientific or source changes.
- [ ] The final file list is deduplicated without losing the strongest evidence
      for a change.
- [ ] Pre-existing dirty and untracked files use a content identity, so a
      same-size rewrite whose mtime is restored still counts as session work.

## 7. Data Lineage Integrity

- [ ] Exactly one lineage manifest is produced for an accepted session that
      executed tracked analysis.
- [ ] Raw events accumulate across a blocked Stop and are not discarded before
      successful extraction.
- [ ] Script hashes represent execution-time content or clearly disclose when
      only final-state content is available.
- [ ] Missing or unresolved scripts remain visible with warnings rather than
      being silently omitted.
- [ ] Repository-assisted lineage recovery never duplicates a direct relative
      path, never aliases an absolute or URL input by basename, and reports
      fallback detection only when it contributes a new unambiguous path.
- [ ] Recovery candidates data-flow into recognized reader/writer path
      arguments, derive direction from that call, bypass repository discovery
      when static I/O is complete, reject runtime branch selection and
      partially resolved concatenation, require an actual supported import for
      reader aliases, reject every locally rebound alias form, and fail
      unresolved at a fixed entry bound.
- [ ] Event append, extraction, manifest writing, and status-sentinel updates
      are locked or atomic as appropriate.
- [ ] Concurrent events and Stop attempts do not lose, duplicate, or split a
      session's lineage.
- [ ] Accepted cleanup archives or rotates raw events only after the manifest
      and session log have been written successfully.
- [ ] Extractor failure blocks Stop visibly and preserves the active marker,
      baseline, session ID, and raw events for a deterministic retry.

## 8. Concurrency, Atomicity, and Filesystem Safety

- [ ] Stop acquires the repository lifecycle lock and then its target session
      lock; the decision, final log, registry update, lineage extraction,
      handoff publication, and cleanup are one serialized transaction.
- [ ] Concurrent Stop attempts produce exactly one finalization, one registry
      row, and one lineage archive.
- [ ] Concurrent SessionStart and PostToolUse writes cannot truncate or
      interleave one root's state, while PostToolUse for distinct roots remains
      independent.
- [ ] Concurrent SessionStart and Stop share repository lifecycle
      serialization; distinct identified roots own distinct markers and logs,
      and numbering uses the next unused value rather than the number of
      existing files.
- [ ] Subagents retain the root session identity and cannot reserve or finalize
      a second root transaction; superseded root tasks fail the owner gate
      before mutating the current log, lineage, baselines, or sentinels.
- [ ] Shared registry/index/graph writers hold stable durable-file locks across
      their complete read/derive/write operations; atomic replacement alone is
      not treated as lost-update protection.
- [ ] Log, registry, manifest, marker, and sentinel replacements are atomic.
- [ ] A log's acceptance marker, duration/file-count frontmatter, and matching
      completion footer are published as one atomic replacement; an injected
      write/replace failure leaves the original unfinalized log and active
      retry markers intact.
- [ ] Lock acquisition has bounded failure behavior; a recorded dead owner is
      recoverable before that bound, while a recent ownerless lock retains a
      publication-race grace period.
- [ ] A stale-lock reaper claims and revalidates the observed directory
      generation plus owner before deletion; it cannot remove a replacement
      owner's lock during the mkdir-to-owner publication window.
- [ ] `.mycelium`, `.living`, marker files, and output parents cannot redirect
      writes outside the project through symlinks.
- [ ] Machine-local pointer refreshes reject existing links and use atomic
      replacement instead of truncating a repository-controlled path.
- [ ] Atomic replacement preserves stricter existing file permissions instead
      of resetting every updated file to a permissive default mode.
- [ ] Repository containment checks use canonical paths and reject traversal,
      prefix collisions, and nonexistent-parent tricks.
- [ ] Cleanup cannot delete a path outside the project, even when state files
      are corrupt or attacker-controlled.
- [ ] Crash-recovery archive sources and identity files must be non-symlink
      regular files, archive parents must be non-symlink directories, and an
      unsafe runtime object cannot be moved or consumed as lineage state.
- [ ] Same-second writes and coarse filesystem timestamps cannot cause false
      enforcement failures.
- [ ] Updating an existing finding or learning is detected; directory mtime is
      not used as the sole evidence of content change.
- [ ] A Bash-only file mutation triggers the same lifecycle enforcement as an
      equivalent editor or `apply_patch` mutation.

## 9. Portability and Failure Handling

- [ ] Shell scripts pass `bash -n` and run under the project's minimum supported
      Bash version.
- [ ] `stat`, `date`, `sed`, `mktemp`, hashing, and locking behavior is tested on
      both macOS and Linux where it differs.
- [ ] Python availability and version requirements produce actionable errors;
      hooks do not assume a `python` alias exists when only `python3` is present.
- [ ] Missing helpers, missing optional dependencies, invalid JSON, malformed
      timestamps, and partially written state fail safely.
- [ ] Shell fallbacks do not use `command || echo fallback` when the command can
      emit stdout before failing; captured values are single-line and encoded
      for their JSON, YAML, Markdown, or path destination.
- [ ] Malformed reminder, audit, owner, and session-start timestamps cannot
      abort SessionStart or trigger an ownership/legacy Stop bypass.
- [ ] Paths with spaces and non-ASCII characters work in shell, Python, JSON,
      and Markdown output.
- [ ] Hook failures do not silently erase evidence or leave the repository in a
      state that appears successfully finalized.
- [ ] Registry values derived from filenames, branches, commits, or frontmatter
      cannot break the Markdown table; a rejected registry upsert blocks Stop
      and an eventual retry does not duplicate the session-end footer.
- [ ] Security-sensitive failures prefer a visible safe refusal over an
      out-of-project write or destructive cleanup.

## 10. Documentation and Release Consistency

- [ ] README install and update instructions cover both Claude Code and Codex.
- [ ] CLI-only instructions are labeled separately from Codex app behavior.
- [ ] Hook names, counts, paths, and lifecycle descriptions match the shipped
      manifests and scripts.
- [ ] Plugin versions, changelog entries, marketplace metadata, and examples
      agree.
- [ ] The backward-compatibility and upgrade path is explicit, including whether
      existing repositories need to run a migration or reapprove hooks.
- [ ] Architecture and troubleshooting documentation contains no stale command
      names, paths, or assumptions from a previous host integration.

## 11. Review Orchestration and Report Validation

- [ ] Specialist reviews are parallelized only up to the host's available
      subagent capacity; remaining specialists run in later waves.
- [ ] An `agent thread limit reached` dispatch failure is retried after a slot
      opens or completed in-line, and every applicable checklist runs exactly
      once before synthesis.
- [ ] Finding IDs are unique and consecutive, each remediation root has one
      global ID, and per-category plus global tallies are derived from the
      final report rather than draft prose.
- [ ] The review-report validator ignores headings, finding-like text, and
      tables inside fenced Markdown code blocks.
- [ ] Tally parsing is bounded to the `## Finding tally` section and ignores
      unrelated tables in later appendices.
- [ ] All required category rows are validated, including zero-finding
      categories, and unexpected finding or tally categories are rejected.

## Required Adversarial Regression Matrix

These cases are mandatory because ordinary happy-path tests are unlikely to
exercise them.

| Case | Expected result |
| --- | --- |
| `.mycelium` or `.living` is a symlink outside the project | The hook refuses the unsafe state and writes nothing outside the project. |
| Session work updates `.living` within the same timestamp second | A valid update is accepted. |
| Session work updates an existing finding file | The content change is detected even if the parent directory mtime is unchanged. |
| `apply_patch` fails | No successful activity is recorded for the failed patch. |
| Tool cwd uses a symlinked or nested path | Valid in-project changes are attributed to the canonical project root. |
| Bash creates or modifies a project file without `apply_patch` | Stop enforces the living-repository update requirement. |
| First Stop is blocked, more work occurs, then Stop succeeds | State remains active after the block, and the final log includes the later work. |
| Multiple Stop hooks run concurrently | Exactly one finalization, registry row, lineage extraction, and cleanup occurs. |
| Python runs as `python3 -u`, `python3 -W`, an absolute or venv interpreter, `/usr/bin/env python3`, or `python -m` | Execution reminders and lineage events are emitted according to policy. |
| Project path contains spaces or Unicode | Hooks, state, logs, and lineage remain correct. |
| Runtime state is malformed or partially written | The hook fails visibly without destructive cleanup or out-of-project writes. |
| The abandoned raw-lineage event path is a nonempty directory or other non-regular object | SessionStart leaves the object, active marker, and owner intact and performs no archive mutation. |
| A script names a direct relative input, or an absolute external input shares a basename with repository data | Lineage records the authoritative static path exactly once and does not claim repository recovery. |
| A completed host task delivers Bash/edit/read PostToolUse after its marker was removed | Every shared writer exits silently without recreating reminders, activity, read telemetry, or raw lineage. |
| A second identified root SessionStart arrives while the first root is live | Both roots receive distinct host-scoped run directories, markers, logs, baselines, lineage, and Stop enforcement; neither can mutate or clean the other's state. |
| A session ID is numeric, Boolean, object/list-valued, `.`, or `..` | SessionStart creates no runtime directory, log, or shared file; a present invalid value never aliases another namespace or downgrades to legacy state. |
| A module docstring or non-I/O call contains `ghost.csv`, or a reader consumes a file under `results/` | The unrelated literal is ignored, and the actual reader path is classified as input regardless of directory name. |
| A dynamic I/O path uses a conditional expression, or a user helper is named `read_csv` | Recovery remains unresolved instead of recording both runtime branches or treating the unproven helper as data I/O; an explicitly imported supported reader remains eligible. |
| A supported-looking reader alias is rebound by assignment, loop, context manager, unpacking, exception handling, walrus, comprehension, or pattern matching; or a runtime prefix is concatenated with a literal filename | Recovery remains unresolved; only an unshadowed supported import and a fully static `+` expression can contribute lineage. |
| A script's direct reader/writer literals are already resolved | Repository fallback is not invoked; dynamic discovery has a fixed fail-safe entry bound. |
| Active-log marker points outside `.living/log` | PostToolUse omits the unsafe log directive; Stop still enforces outstanding work and never reveals or follows the path. |
| A dirty or untracked file is rewritten with the same size and restored mtime | Session accounting detects the content change. |
| `true && python failed.py` exits nonzero, or Python is a pipeline component | The provably executed analysis is recorded; an unknown failed AND prefix remains omitted. |
| Shell operators and Python text occur only after an unquoted `#` comment marker | No reminder or lineage event is created; quoted and escaped hashes remain valid arguments. |
| Registry summary contains `|`, or the registry/lineage helper fails | Table cells remain valid; helper failure blocks Stop and preserves retry state without duplicate finalization. |
| Registry finalization fails, then SessionStart resumes or compacts before Stop retries | The same unfinalized log and session ID remain active; the retry produces one final log and registry row. |
| A review report contains category headings, `F99`, or tally rows inside a fenced evidence block | Fenced examples are ignored; live finding IDs and tallies still validate exactly. |
| A required review category has no findings but claims a nonzero tally, or a later appendix has another four-column table | The false zero-category tally is rejected and the appendix table is ignored. |
| The host has fewer subagent slots than review specialists | Specialists run in waves; capacity errors are retried or completed in-line, and all applicable axes appear exactly once. |
| Interpreter or script paths concatenate quoted, bare, and escaped shell-word components | Python, R, and Jupyter reminders and lineage use the single decoded argv value. |
| A quoted interpreter/script suffix is followed by more characters in the same shell word, such as `python "a.py".bak` | The longer argv value is evaluated as a whole; no event is attributed to `a.py`. |
| `env -S 'echo prefix' python a.py` or another argv-rewriting wrapper precedes an interpreter-looking argument | The parser rejects the ambiguous execution unless it fully models the wrapper expansion. |
| `env -C sub`, `conda run --cwd sub`, or `uv run --directory sub` wraps an analysis command | Scripts and data paths resolve relative to the wrapper-selected directory; missing or dynamic directories are rejected. |
| `time`, `exec`, `nice`, and `timeout` wrap or nest around an interpreter | Real executions are captured, while malformed options and an unrelated wrapped command containing interpreter text are rejected. |
| Jupyter uses `--to notebook input.ipynb` or `--output converted.ipynb input.ipynb` | Option arity is honored and only `input.ipynb` is attributed; an unknown separated-value option fails conservatively. |
| Python/R inline source contains escaped quotes or adjacent quoted components | The complete shell word is decoded and later data references remain visible. |
| `--help`/`--version` or `-h`/`-V` precedes an apparent interpreter payload | No execution or lineage is attributed, including terminal short-option clusters and adjacent R/Jupyter forms. |
| Atomic session-log replacement fails after registry/context preparation | Stop blocks, frontmatter remains unaccepted with no footer, active state survives resume/compact, and one retry produces one footer. |
| The agent writes a fresh five-section private `last-session.md` before Stop | Stop validates the post-cleanup body, preserves authored content, adds/updates its authoritative accepted-status block, and removes stale standalone "Stop pending" lines. A missing, stale, partial, or cleanup-emptied handoff receives an atomic five-section fallback instead; direct finalizer rejection leaves both source and destination unchanged. Accepted Stop atomically publishes the complete file to shared `.mycelium/last-session.md`; blocked Stop does not. |
| A code-mode local tool returns model-facing `input_text` blocks containing serialized result JSON | Exit status is recovered recursively for lineage, conditional execution, and failed-edit activity classification. |
| Current Codex Bash PostToolUse supplies an empty `tool_response` before its outer command event completes | The analysis execution is retained, exit status and wall time stay null, and no success value is fabricated. |
| Current Claude SessionStart or PostToolUse returns context | The raw hook response uses `hookSpecificOutput` with the matching event name, and a fresh Claude task receives the context in its model-visible system reminder. |
| Claude Code cross-discovers the plugin's Codex `hooks/hooks.json` adapter | The Codex dispatcher exits silently under Claude while the native project hooks produce one lifecycle effect. |
| Multiple SessionStart hooks race, or today's log numbers contain a gap | Repeated starts for one identity reuse one transaction; distinct identities receive distinct markers/logs; no occupied log number is reused. |
| SessionStart runs before the repository's first commit, including on a YAML-looking branch name | The prospective branch is stored as one YAML string, injected as one summary value, and decoded without quotes during Stop finalization. |
| A subagent uses the host's dedicated subagent lifecycle while its root task is active | It retains the parent session ID, may contribute Tier 1 activity, and cannot create or finalize a second root transaction. A missing, multiline, or corrupt owner token for a marker declaring host-ID ownership blocks Stop without falling back to timestamps. |
| A pre-upgrade flat transaction exists when a fresh identified root starts | An exact flat owner match continues in place; a nonmatching identity receives a new scoped transaction without touching the flat marker, owner, lineage, or retry evidence. |
| Two registry/index writers read the same initial bytes and finish in the opposite order | A stable durable-file lock covers each complete derivation, every distinct row survives, output remains structurally valid, and the existing target mode is preserved. |
| Mycelium runs `generate_index.py`, registry/log/handoff finalizers, session accounting, or review validation | These control-plane utilities do not open or refresh an analysis bookkeeping cycle; a same-named user script outside the managed path remains eligible. |
| Stop finalizes a registry row already enriched by the agent | Date, branch, duration, file count, status, and log link become factual final values while Summary, Key Outputs, and Tags remain authored and byte-equivalent at the cell level. |
| A review report is rendered from multiple specialist outputs | Same-root findings are deduplicated, IDs are globally consecutive, per-category/global tallies match the rendered headings, and cross-input schema/feature/label comparability was checked. |
| An analysis composes existing data paths dynamically from `Path`, dictionaries, or loop variables | Unique filenames reachable from recognized I/O path arguments are recovered conservatively after execution; direction follows the call, while ambiguous or over-bound searches remain unresolved with an explicit warning. |
| A recent lifecycle lock records an owner PID that has terminated | The next caller claims and revalidates that exact generation before recovering it; a recent ownerless lock is not mistaken for dead-owner proof, and a replacement owner's lock survives competing delayed reapers. |
| Jupyter receives `--show-config`, `--show-config-json`, or `--generate-config` before or after an apparent notebook, including after a shell redirection | No execution or lineage is attributed; terminal-looking text inside an ordinary option value, a redirection target, and an actual neighboring Jupyter command remain scoped correctly. |
| Fresh initialization encounters an unsafe later managed target | Preflight rejects it before creating any Mycelium directory or file. |
| A Claude or Codex hook config is valid JSON but has malformed event, group, handler, or command types | Initialization and migration reject it before changing guidance, runtime state, hooks, todo files, or the knowledge index. |
| Integration stress suite runs in CI | CI invokes `skills/core/tests/test_integration_stress.sh` and fails if it fails. |

## Validation Gate

Adapt individual commands when the repository layout changes, but preserve the
coverage represented by every line.

```bash
python3 -m pytest -q skills/core/scripts --ignore=skills/core/scripts/knowledge_map
python3 -m pytest -q skills/core/scripts/knowledge_map
bash skills/core/hooks/test_stop_hook.sh
bash skills/core/hooks/test_hooks_stress.sh
bash skills/core/tests/test_integration_stress.sh
find hooks skills -type f -name '*.sh' -exec bash -n {} +
python3 -m compileall -q skills/core/scripts
python3 -m json.tool .claude-plugin/plugin.json >/dev/null
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool hooks/hooks.json >/dev/null
git diff --check
```

Also perform:

- [ ] A fresh initialization and structure-validation smoke test.
- [ ] Dry-run and actual migrations from representative legacy Claude and Codex
      repositories, followed by validation and a second idempotency run.
- [ ] A packaged-plugin smoke test in a disposable real repository on both
      supported hosts.
- [ ] A lifecycle smoke test that lets hooks dispatch naturally; do not invoke
      the hook scripts manually as a substitute.
- [ ] The child task retains ordinary read/edit capabilities required for the
      expected Stop-compliance retry, even if recursive skill loading is
      disabled.
- [ ] Real-host conclusions come from the automatic event stream and filesystem
      effects; the agent's self-report is checked against, not substituted for,
      those primary observations.
- [ ] CI verification against the exact final head commit.
- [ ] Codex review against the exact final head, with no unresolved P1 or P2
      findings.

## Merge Gate

A cross-host lifecycle change is ready to merge only when:

- [ ] Every applicable checklist item has evidence and no unexplained failure.
- [ ] All required regression and stress tests pass locally and in CI.
- [ ] Fresh install, migration, and natural-dispatch smoke tests pass.
- [ ] The exact final commit has been reviewed, not merely an earlier revision.
- [ ] There are no unresolved P1 or P2 findings.
- [ ] Any remaining lower-priority risk is recorded and explicitly accepted.
