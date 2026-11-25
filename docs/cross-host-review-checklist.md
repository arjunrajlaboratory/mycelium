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
- [ ] The Codex hook configuration contains the intended eight command
      registrations: SessionStart; the I/O observer on PreToolUse for Bash
      and for `apply_patch`; two Bash PostToolUse handlers; the activity
      tracker and the I/O observer on `apply_patch` PostToolUse; and Stop.
- [ ] Every PreToolUse handler is observation only: it exits 0 on every
      path and writes only under `.mycelium/io/` after the shared-state
      containment check. On Claude and Codex it prints nothing and emits no
      permission decision. On Antigravity, which requires a decision, it
      prints exactly `{"decision": "allow"}` (defer to the host's policy) on
      every path, including a crash, a hang past its bound and a malformed
      payload, and never `auto_approve`, `deny`, `ask`, `overwrite` or
      `permissionOverrides`; the wrapper, not the Python helper, prints it.
      A change to this handler needs a live App check that prompts, "Always
      Allow" grants and denials are unchanged.
- [ ] A Bash call's observation runs before session gating, so a lock
      timeout, a missing transaction, or a foreign owner never costs it; an
      observation that cannot be made says `unavailable` with a reason and
      never reads as "nothing changed". Claude's PostToolUseFailure reaches
      the data tracker, which takes the exit from `error` and never
      defaults it to 0.
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

- [ ] Selecting a virtual-environment interpreter preserves its invocation
      path, `sys.prefix`, packages, and child interpreter resolution; validating
      the executable target must not replace the selected environment.
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
- [ ] Install and update instructions say Codex hook approval is pinned to the
      installed bundle's contents (`trusted_content_hash`), so every upgrade or
      reinstall needs hook review again; trust is verified with app-server
      `hooks/list` (`trustStatus` `trusted`), not terminal `/hooks` alone,
      which on Codex 0.169.0 showed hooks as active while skipping them;
      generated guidance keeps a start-of-task, hedged in-context hook health
      check whose sentinel text matches what SessionStart actually emits.
- [ ] Native-host setup instructions identify the installed package, selected
      interpreter and intended workspace, and distinguish initializer-owned
      steps from remaining user choices. Execute the documented commands in an
      isolated existing Git project; do not infer model compliance from that pass.

## 4. Lifecycle State Machine

- [ ] Multiple positive native tools completed before the next model invocation
      retain all mandatory guidance. Pending reminders are keyed by event ID,
      survive restart/legacy migration, and clear only on individual native
      acknowledgement; duplicate events and negative controls cannot erase or
      resurrect other messages.
- [ ] A finalizer attempt is durable before dispatch. If its result is lost,
      an identity-bound acceptance receipt permits reconciliation without
      redispatch; absence of a marker alone cannot fabricate acceptance.
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
- [ ] A host-identified PostToolUse event with no active transaction never
      writes into an accepted or another root's transaction. Hosts run Stop
      after every turn, so an event carrying work (a proven analysis run,
      a project edit) reopens its own host session's transaction through
      SessionStart's locked setup (`source: reopen`); reads, non-work
      commands, and `data_command` records never reopen. A marker found on the way is always handed back
      to locked validation (a parallel hook's fresh transaction is used, a
      corrupt or foreign one fails closed), and reopen mode never cleans or
      replaces an existing marker. The reopened transaction starts from the
      baseline and start time the accepted Stop carried forward, not a
      snapshot or clock reading taken after the triggering command, and no
      age-based cleanup removes a carried baseline. A Stop with no
      transaction compares against the carried baseline and reopens when a
      shell-only edit changed project files. A failed successor snapshot or
      a failed move of unowned skips blocks Stop with the transaction intact
      instead of accepting. Identity-free legacy payloads retain markerless
      behavior.
- [ ] Every command the lineage recorder meant to record but could not leaves
      a skip record with its reason (raw stream `"kind": "skip"`, manifest
      `skipped`, or shared `lineage-skips-unowned.ndjson`), and Stop reports a
      LINEAGE GAP on every path, moving unowned records into a manifest only
      when it holds a valid owned transaction. The shared skip file is written
      only after a fresh containment check and rewritten by atomic
      replacement under a stable lock; no hook path drops a detected data
      command silently.
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
- [ ] An accepted Stop emits only `systemMessage`, on every host, and never
      `additionalContext`. On Claude, `hookSpecificOutput.additionalContext`
      on a Stop continues the conversation like a block. A provenance gap
      goes to `systemMessage` (parts one line apart, no blank line: Claude
      shows each line as "Stop says:") and, when the Stop finalizes a
      transaction, into the finalized session log footer (`- Lineage
      gaps:`). Handoff publication is unchanged. Anything the agent must act
      on is a block reason.
- [ ] Stop preserves the authored content of a fresh, complete five-section
      handoff, where every required heading occurs exactly once in order and
      every section has a nonblank, non-heading body line after obsolete
      lifecycle prose is removed. Cleanup and completeness validation share one implementation,
      and the finalizer enforces that post-cleanup contract before writing. It
      atomically publishes an authoritative accepted-status block. A fresh
      handoff that fails the check blocks Stop once per transaction with its
      specific problems; an absent or stale handoff, one still failing on the
      retry, or a validator error is replaced atomically with a complete
      fallback without a block. Only an accepted Stop publishes the
      private handoff to shared `.mycelium/last-session.md`.
- [ ] Stop updates factual registry fields without overwriting an agent-authored
      Summary, Key Outputs, or Tags for the same session.
- [ ] Any directive asking the agent to write a keyed registry row names the
      exact session ID (or defers the write to Stop), and Stop folds a row
      keyed by a guessed ID that links the same session log into one row.
- [ ] Cleanup and retention policies distinguish active, accepted, stale, and
      corrupt state.

## 5. Execution and Working-Directory Inference

- [ ] Direct Python, R, and Jupyter execution is detected.
- [ ] Pipeline runners (`bash`/`sh` scripts, `./run.sh`, `snakemake`,
      `sbatch`, `nextflow run`) open the post-action cycle and record their
      entry point, while their non-executing modes (`bash -n`, snakemake dry
      runs and graph/lint modes, `nextflow run -preview`, `sbatch --test-only`)
      and mere mentions (`cat run.sh`) do not. Modes selected by an option
      value (`--executor dryrun`, `bash -o noexec`) are parsed, and options
      that move the run (`snakemake --directory`, `sbatch --chdir`) set the
      directory lineage resolves I/O against, never the entry point.
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
- [ ] Each interpreter's startup options are parsed with that interpreter's
      own grammar, not one borrowed from a sibling: `R -q -e`, `R -s -f`, and
      `R CMD BATCH -q` are detected, while R's argv-wide terminal options
      (`--version` after `-e`) and `--args`/`-d` boundaries are honored. The
      post-action one-liner exemption covers every inline form (`python -c`,
      `R -e`, `Rscript -e`) whatever options surround it, without hiding those
      forms from lineage.
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
- [ ] Unknown telemetry stays unknown through consolidation: manifest summaries
      report a null total when no action carried a wall time, expose
      timed/total and known-exit/failed coverage counts, and never interpret
      an unknown exit as success or failure.
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
- [ ] Command scanning stays linear in the command length: no regex retries
      a variable-length shell word from every start position, and no
      per-match proof re-reads the whole command before or after its match.
      Within one pattern, no repeated group has two alternatives that accept
      the same word. Timing tests with generous bounds pin long quoted-word
      runs, many-line commands, long `&&`/`||` chains, repeated Jupyter
      options, and `R CMD BATCH` with escaped blanks.
- [ ] Command-derived paths reach `os` calls only after a NUL check:
      `expanduser`, `realpath` and `os.access` raise ValueError, not OSError.
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
- [ ] Every lifecycle hook resolves the repository from the payload's session
      `cwd`, not the hook process's directory. After `cd` into a nested
      non-opted repository, Stop, its in-process lineage finalizer and
      post-action take an enclosing opted-in
      repository only if it already holds the session's run directory, and
      otherwise fall back to the process directory; an unrelated enclosing
      project gains no state. A session in a linked worktree is judged on, and
      writes into, that worktree.

## 7. Data Lineage Integrity

- [ ] An in-progress native command cannot publish preexisting output hashes
      as completed execution provenance. Terminal evidence reconciles the same
      command once, and missing completion evidence remains unresolved.
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
- [ ] Static stdlib I/O binds `__file__` only to a certain script path: one
      Python ran directly, named by a normal-form word, with no symlink in
      any component (working directory included). A relative word is used
      only through `abspath`/`realpath`/`resolve()`/`absolute()` of
      `__file__` itself, and not at all with a `chdir` in the script.
      Rebinding `__file__`, a non-literal `parents` index, or `resolve()`
      after a joined component stays unresolved.
- [ ] argparse replay through a `parse_args(argv=None)` wrapper requires a
      provably None `argv`: one call site, None defaults, each `argv` used
      only at its one pass-through, `main` called with no arguments from
      the main guard, and a single parse call in the module.
- [ ] Event append, extraction, manifest writing, and status-sentinel updates
      are locked or atomic as appropriate.
- [ ] Concurrent events and Stop attempts do not lose, duplicate, or split a
      session's lineage.
- [ ] Accepted cleanup archives or rotates raw events only after the manifest
      and session log have been written successfully.
- [ ] Extractor failure blocks Stop visibly and preserves the active marker,
      baseline, session ID, and raw events for a deterministic retry.
- [ ] Every Bash call that touches data leaves an analysis event, a
      `data_command` record, or a skip. A `data_command` never claims an
      execution: data-like words in the text are `named_paths` without
      direction, and `inputs`/`outputs` appear only where shell syntax proves
      direction in a command that exited 0. It never reopens a transaction,
      opens a post-action cycle, or counts as session work. Without a
      transaction it is parked, keyed by host session, and that session's
      next Stop moves it into an owned stream or writes it to a manifest of
      its own; it is never reported or cleared as a gap. A stream of only
      data commands is never named by a session number that an idle turn
      releases.

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
- [ ] A Stop retry that recomputes duration or changed files rebuilds the end
      footer, `Modified` summary, and `Files Modified` list from the same
      canonical values written to frontmatter and the registry, collapses
      duplicated legacy footers to one, and preserves body content appended
      between attempts.
- [ ] Machine-block removal in finalized logs matches only the exact emitted
      syntax, tracks Markdown fence delimiters per CommonMark (marker
      character, minimum close length, backtick info-string exclusion), and
      consumes no more lines than the generator recorded, so authored
      look-alike headings, fenced examples, and appended bullets survive.
- [ ] Every emulation of another interpreter's semantics (bash quoting and
      expansion, substitution byte filtering, Markdown structure) is either
      byte-exact or refuses conservatively; a divergence can cost a redundant
      reminder, never a suppressed event, deleted authored content, or a
      crashed hook.
- [ ] Mutex-style directory creation (lock acquisition and reap claims) goes
      through `mycelium_mkdir_exclusive` or the state runner's single
      `lock:*` attempt, both of which call `mkdir(2)` directly; no
      lock trusts the exit status of the `mkdir` utility, which uutils
      coreutils 0.10.0 reports as success for a lost creation race.
- [ ] Lock acquisition has bounded failure behavior; a recorded dead owner is
      recoverable before that bound, while a recent ownerless lock retains a
      publication-race grace period.
- [ ] A stale-lock reaper claims and revalidates the observed directory
      generation plus owner before deletion; it cannot remove a replacement
      owner's lock during the mkdir-to-owner publication window.
- [ ] Staleness guards act only on timestamps actually read; an unreadable or
      vanished lock mtime never counts as age, and a contender's transient
      reap claim inside a lock cannot make the owner's release leave it behind.
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
      both macOS and Linux where it differs, including the uutils (Rust)
      coreutils that Ubuntu 26.04 and `ubuntu-latest` ship as the default.
- [ ] Python availability and version requirements produce actionable errors;
      hooks do not assume a `python` alias exists when only `python3` is present.
- [ ] Missing helpers, missing optional dependencies, invalid JSON, malformed
      timestamps, and partially written state fail safely.
- [ ] A bash path that skips an interpreter (cached decoders, JSON and
      registry-cell encoders) is byte-identical to it, exit status included,
      across bash 3.2 and 5.x, locales, `PYTHONIOENCODING`/`PYTHONUTF8`, and
      `patsub_replacement`, or falls back to the interpreter.
- [ ] No stage of a pipeline over a list that can grow with the repository or
      the session (changed files, activity, events, logs) can fail on the
      list's length or content: no consumer that stops reading early (`head`,
      `sed Nq`, `grep -q`, `awk ... exit`), and no `xargs` or per-item
      `basename` that rejects quotes, long lines or a leading `-`. Under
      `pipefail` either exits the hook. Process Git paths as bytes through
      extraction and escape invalid UTF-8 before displaying names.
- [ ] Shell fallbacks do not use `command || echo fallback` when the command can
      emit stdout before failing; captured values are single-line and encoded
      for their JSON, YAML, Markdown, or path destination.
- [ ] Malformed reminder, audit, owner, and session-start timestamps cannot
      abort SessionStart or trigger an ownership/legacy Stop bypass.
- [ ] Paths with spaces and non-ASCII characters work in shell, Python, JSON,
      and Markdown output.
- [ ] Hook failures do not silently erase evidence or leave the repository in a
      state that appears successfully finalized.
- [ ] A fail-open exit separates an intentional no-op from a failure. A
      post-tool hook that proved it owns a live transaction but could not take
      its session lock leaves an unrecorded-event line and a stderr diagnostic,
      still exits 0, and does not then take a no-transaction fallback
      (reopen, unowned skip) for the same event. Stop enforces a lost edit or
      analysis run as work and reports a lost lineage or read record without
      treating it as work its recorded form never was.
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
- [ ] Every `.living` entry parser (INDEX.md counts, SessionStart summary,
      `collect_entries` IDs and tags, `detect_recurrence`, `recall_lessons`
      slicing, knowledge-map extraction) skips lines that
      `markdown_fences.mark_fenced` marks as fenced, matching
      `validate_structure`.
- [ ] Tally parsing is bounded to the `## Finding tally` section and ignores
      unrelated tables in later appendices.
- [ ] All required category rows are validated, including zero-finding
      categories, and unexpected finding or tally categories are rejected.

## Required Adversarial Regression Matrix

For Antigravity packaging, verify file modes alongside bytes, include generator
and effective host guidance in source identity, and keep the selected interpreter
path separate. A permission-only repair must preserve the old package and refuse
changed content, extra files, links and malformed ownership.

These cases are mandatory because ordinary happy-path tests are unlikely to
exercise them.

| Case | Expected result |
| --- | --- |
| Two positive native commands finish in one invocation, followed by partial acknowledgement | Mandatory first-command guidance and later context both survive; only the acknowledged ID is removed, including after restart. |
| An owned Antigravity hook loses its executable bit | Doctor and ordinary install fail; explicit permission repair retains the old package and restores a working hook. |
| Only the Antigravity package generator, guidance or source permission mode changes | The development source digest changes; a different selected Python path alone does not change source identity. |
| An async native command overwrites an existing output after its first hook | No output snapshot is finalized while RUNNING; matching terminal evidence produces one action with the final observed hash, while unavailable completion remains unresolved. |
| The Antigravity PreToolUse helper crashes, prints another decision, exits silently, cannot start, or hangs with SIGALRM blocked | The packaged wrapper still prints exactly `{"decision": "allow"}` and exits 0 within about 2 s; a live App run shows prompts, "Always Allow" grants and denials unchanged. |
| An Antigravity PreToolUse arrives for a foreign native root, an unsupported tool or a protected edit path | Nothing under `.mycelium/io` is written; the answer is still `allow`. |
| An Antigravity command the Pre cannot observe still runs (App `IsDaemon`/`BypassSandbox`/unknown argument, IDE `Cwd` outside the project, or an inactive work cycle) and writes while a later foreground call runs | It is journaled without a snapshot as a background call, which turn end leaves open; the later call is marked overlapping, and the index never gives its write `current-in-record` with sole attribution. |
| A Stop result is lost after the real finalizer completes | The adapter reconciles an identity-bound acceptance receipt without dispatching again; a missing or mismatched receipt retains an uncertain outcome. |
| A native host's SQLite store is read while its writer is live, and after it exits | Every App file (database, `-wal`, `-shm`) is byte- and mtime-identical afterwards and no file is created: reads go through a private snapshot, because a `mode=ro` WAL reader still writes `-shm`. |
| A Claude or Codex edit mentions an adapter-only field name, or reaches the Antigravity package through the shared plugin-root pointer | Host-specific modes are chosen from adapter-set environment, never payload text or a package marker that overrides an explicit host. |
| The selected Python launcher is a venv symlink | Both the generated hook and its child python3 preserve the selected environment and can import its packages. |
| `.mycelium` or `.living` is a symlink outside the project | The hook refuses the unsafe state and writes nothing outside the project. |
| Session work updates `.living` within the same timestamp second | A valid update is accepted. |
| Session work updates an existing finding file | The content change is detected even if the parent directory mtime is unchanged. |
| `apply_patch` fails | No successful activity is recorded for the failed patch. |
| Tool cwd uses a symlinked or nested path | Valid in-project changes are attributed to the canonical project root. |
| A session works in a linked worktree while the host runs hooks in the main checkout, which holds another session's dirty work | Stop judges only the worktree's changes, accepts reflection in the worktree's `.living/`, and leaves the main checkout byte-identical; a missing `.mycelium/plugin-root` is seeded in the worktree. |
| Bash creates or modifies a project file without `apply_patch` | Stop enforces the living-repository update requirement. |
| An accepted Stop on Claude has a lineage gap (e.g. from helper `--help` calls) | The Stop emits only `systemMessage`, with no empty line; the gap is in the finalized log footer; the published handoff is exactly what Stop publishes without a gap; no further turn or transaction starts. |
| First Stop is blocked, more work occurs, then Stop succeeds | State remains active after the block, and the final log includes the later work. |
| Multiple Stop hooks run concurrently | Exactly one finalization, registry row, lineage extraction, and cleanup occurs. |
| Python runs as `python3 -u`, `python3 -W`, an absolute or venv interpreter, `/usr/bin/env python3`, or `python -m` | Execution reminders and lineage events are emitted according to policy. |
| Project path contains spaces or Unicode | Hooks, state, logs, and lineage remain correct. |
| Runtime state is malformed or partially written | The hook fails visibly without destructive cleanup or out-of-project writes. |
| The abandoned raw-lineage event path is a nonempty directory or other non-regular object | SessionStart leaves the object, active marker, and owner intact and performs no archive mutation. |
| A script names a direct relative input, or an absolute external input shares a basename with repository data | Lineage records the authoritative static path exactly once and does not claim repository recovery. |
| A live host session runs analysis or makes a project edit after an earlier turn's Stop was accepted (including an edit queued on the session lock while Stop accepted) | The event never writes into the accepted transaction; it reopens a fresh transaction for its own host session, with a new log, lineage, and Stop enforcement. Parallel hooks for one call reopen exactly one transaction, another root's live state stays byte-identical, and reads, non-work commands, `.living`/`.mycelium` edits, and unsafe state never reopen. |
| A Bash call touches data without a proven analysis run (`head data/x.csv`, `cp a.csv outputs/`, a heredoc-fed `python3 -`), inside a transaction and after an accepted Stop | One `data_command` record with `io_detection: "unresolved"`; direction only for redirections and modelled `cp`/`mv` in an exit-0 `&&` list; quoted, commented, piped, backgrounded, expanded, globbed, `cp -t`, `cp -n`, and unknown-exit neighbors claim nothing; no reopen, no post-action cycle, no session log; parked and written by that session's next Stop; a data-only turn's manifest survives the next session reusing its log number. |
| A post-tool hook times out on its owned session's lock (a live holder, such as a slow Stop or parallel subagent hooks) | The hook exits 0, prints a diagnostic on stderr, and appends one line to that run directory's `mycelium-unrecorded-events.tmp`; it writes nothing through the lock it never held and records no unowned skip. For an edit or analysis hook, Stop takes the record into a fresh work cycle and blocks with an UNRECORDED WORK reason until `.living/` is updated after it (an earlier update does not count); evidence appended while Stop runs blocks that Stop for a retry, and evidence left after an accepted Stop reopens the session at the next Stop; for the data or read tracker, Stop accepts with an UNRECORDED EVENTS note. Either way it then removes the file. Another session's event and an uncontended event leave nothing. |
| The lineage extractor fails or is missing for a detected command | A skip record with the reason reaches the session's raw stream and the manifest's `skipped` list, and Stop's message carries a LINEAGE GAP line. |
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
| `learnings.md`, `decisions.md`, `conventions.md`, or a findings topic documents its own heading format inside a ``` or `~~~` fence | INDEX.md, the SessionStart summary, entry IDs, recurrence clusters, recall text, knowledge-map entries, `FINDINGS_REGISTRY.md` rows and fields, and the cross-project findings INDEX (count, strongest status, open questions) reflect only live structure; an entry's text runs past its fenced example; a registry whose only rows were fenced examples is emptied; an unclosed fence in a findings topic is reported with its line. |
| A required review category has no findings but claims a nonzero tally, or a later appendix has another four-column table | The false zero-category tally is rejected and the appendix table is ignored. |
| The host has fewer subagent slots than review specialists | Specialists run in waves; capacity errors are retried or completed in-line, and all applicable axes appear exactly once. |
| Interpreter or script paths concatenate quoted, bare, and escaped shell-word components | Python, R, and Jupyter reminders and lineage use the single decoded argv value. |
| A quoted interpreter/script suffix is followed by more characters in the same shell word, such as `python "a.py".bak` | The longer argv value is evaluated as a whole; no event is attributed to `a.py`. |
| `env -S 'echo prefix' python a.py` or another argv-rewriting wrapper precedes an interpreter-looking argument | The parser rejects the ambiguous execution unless it fully models the wrapper expansion. |
| `env -C sub`, `conda run --cwd sub`, or `uv run --directory sub` wraps an analysis command | Scripts and data paths resolve relative to the wrapper-selected directory; missing or dynamic directories are rejected. |
| `time`, `exec`, `nice`, and `timeout` wrap or nest around an interpreter | Real executions are captured, while malformed options and an unrelated wrapped command containing interpreter text are rejected. |
| Jupyter uses `--to notebook input.ipynb` or `--output converted.ipynb input.ipynb` | Option arity is honored and only `input.ipynb` is attributed; an unknown separated-value option fails conservatively. |
| A command holds thousands of quoted words after an interpreter, thousands of interpreter lines, or a 1,500-command `&&`/`||` chain | Detection finishes in well under a second and matches the plain (unpruned, uncached) scan exactly. |
| Python/R inline source contains escaped quotes or adjacent quoted components | The complete shell word is decoded and later data references remain visible. |
| `R -e`, `R -q -e`, `R --vanilla -q -e`, `R -e … --args`, `Rscript -e`, and `python -c` run; or `R -f script.R`, `R --file=script.R`, `R CMD BATCH script.R`, and `Rscript script.R` run | Every inline one-liner is exempt from the post-action check yet still recorded as lineage; every script run opens the post-action cycle and records its script. `R -e … --version`, `R --args -e …`, `R -d lldb -e …`, and `R -f script.R RHOME` record nothing. |
| `--help`/`--version` or `-h`/`-V` precedes an apparent interpreter payload | No execution or lineage is attributed, including terminal short-option clusters and adjacent R/Jupyter forms. |
| Atomic session-log replacement fails after registry/context preparation | Stop blocks, frontmatter remains unaccepted with no footer, active state survives resume/compact, and one retry produces one footer. |
| The agent writes a fresh five-section private `last-session.md` before Stop | Stop validates the post-cleanup body, preserves authored content, adds/updates its authoritative accepted-status block, and removes stale standalone "Stop pending" lines. A fresh partial or cleanup-emptied handoff blocks Stop once with its problems named; a missing or stale one, or one still failing on retry, receives an atomic five-section fallback instead; direct finalizer rejection leaves both source and destination unchanged. Accepted Stop atomically publishes the complete file to shared `.mycelium/last-session.md`; blocked Stop does not. |
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
| Any script shipped under `skills/core/scripts/` (including `recall_lessons.py`, `detect_recurrence.py`, `upsert_table_row.py`, `crystallize_findings.py`, `extract_data_lineage.py`, and knowledge-map tools) runs via source, installed-cache, relative, or `.mycelium/plugin-root`-accessor path | No post-action reminder and no lineage event is produced; a sweep enumerating every bundled `.py` proves the exclusion registry is complete. |
| A user project contains its own `skills/core/scripts/<colliding-name>.py` with no Mycelium plugin manifest, or a `myskills/core/scripts/` component-boundary neighbor | The script keeps full lineage and post-action bookkeeping; only a verified Mycelium plugin root (or the documented plugin-root accessor) suppresses it. |
| Stop finalizes a log, then a retry supplies a different duration, file count, and file list | Frontmatter, footer, `Modified` summary, file list, and registry row all report the retry values with exactly one footer; body content appended between attempts survives. |
| Every action in a session has `bash_wall_s` and `bash_exit` null | The manifest reports `total_wall_seconds: null` with zero timed/known-exit coverage and full `total_action_count`; nothing reads as a measured zero or an implicit success. |
| The plugin-root pointer carries a trailing space, CRLF, NUL byte, or extra lines; or the accessor word is single-quoted, bare, prefixed with a user component, or followed by `..` traversal | Verification reproduces the exact bash expansion or refuses: only a double-quoted, word-anchored accessor whose expanded value verifies is suppressed; every divergent form keeps detection and no input can raise out of the hook. |
| A finalized log contains an authored `— Session ended` look-alike heading, a machine-shaped example in a four-backtick fence with inner ``` lines, or an authored bullet directly after the generated file list | Finalization rebuilds only the machine footer; every authored line survives byte-identical across retries. |
| The agent upserts its registry row under a guessed session ID that links the session's own log | Stop leaves exactly one row with the canonical session ID and the agent's authored Summary, Key Outputs, and Tags; a row for any other log survives. |
| Stop finalizes a registry row already enriched by the agent | Date, branch, duration, file count, status, and log link become factual final values while Summary, Key Outputs, and Tags remain authored and byte-equivalent at the cell level. |
| A review report is rendered from multiple specialist outputs | Same-root findings are deduplicated, IDs are globally consecutive, per-category/global tallies match the rendered headings, and cross-input schema/feature/label comparability was checked. |
| An analysis composes existing data paths dynamically from `Path`, dictionaries, or loop variables | Unique filenames reachable from recognized I/O path arguments are recovered conservatively after execution; direction follows the call, while ambiguous or over-bound searches remain unresolved with an explicit warning. |
| A recent lifecycle lock records an owner PID that has terminated | The next caller claims and revalidates that exact generation before recovering it; a recent ownerless lock is not mistaken for dead-owner proof, and a replacement owner's lock survives competing delayed reapers. |
| A waiter stats the lock just after the prior owner's `rmdir`, or a delayed reaper's claim sits inside the lock when its owner releases | The missing mtime is not treated as age, so no claim lands in the successor's lock; release waits out any transient claim, leaving no ownerless lock for waiters to time out on and no dropped PostToolUse event. |
| `/usr/bin/mkdir` reports success for a lost creation race (uutils coreutils 0.10.0 on Ubuntu 26.04) and two lock contenders or two distinct-root SessionStarts race | Lock holders' critical sections never overlap, competing dead-owner reapers do not both claim, and each root receives its own session log. |
| Jupyter receives `--show-config`, `--show-config-json`, or `--generate-config` before or after an apparent notebook, including after a shell redirection | No execution or lineage is attributed; terminal-looking text inside an ordinary option value, a redirection target, and an actual neighboring Jupyter command remain scoped correctly. |
| Fresh initialization encounters an unsafe later managed target | Preflight rejects it before creating any Mycelium directory or file. |
| A Claude or Codex hook config is valid JSON but has malformed event, group, handler, or command types | Initialization and migration reject it before changing guidance, runtime state, hooks, todo files, or the knowledge index. |
| `bash run.sh`, `./run.sh`, `snakemake -c1`, `sbatch job.sh`, or `nextflow run main.nf` runs; or `snakemake -n`, `bash -n run.sh`, `sbatch --test-only job.sh`, or `cat run.sh` runs | Runs open the post-action cycle and record their entry script as lineage; non-executing modes and mentions produce neither. |
| Codex has skipped every Mycelium hook because an upgrade invalidated its approval | The root agent finds no `SESSION HANDOFF PATH:` line in its context and tells the user the hooks are probably not running and to re-trust the `mycelium@mycelium` hooks (from the desktop app on Codex 0.169.0); `hooks/list` confirms `trustStatus` `trusted` before an audit; migration delivers that check to existing projects, and renaming the SessionStart sentinel fails a test. |
| A hook state check, lock attempt, or encoder moves out of its own interpreter | A differential test runs the moved code and the code it replaced on the same adversarial state and requires identical statuses, variables, output, and filesystem effects; launch counts per hook are pinned. |
| Integration stress suite runs in CI | CI invokes `skills/core/tests/test_integration_stress.sh` and fails if it fails. |
| Stop runs with more than 128 KiB of changed-file or activity path text, including a name with a quote, a name starting with `-` and a path of 255 bytes or more, with SIGPIPE inherited ignored and default | Stop exits 0 with no "Broken pipe": it blocks with the full count and 15 names (the `-` name among them), blocks again on retry, then finalizes after a `.living/` update with `ended:`, the counted registry summary and a published handoff. |

## Validation Gate

For releases, `python3 tools/release_gate.py` (see `docs/release-process.md`)
runs this gate fail-fast from one immutable candidate and records the
evidence summary; CI runs the platform-sensitive subset on both
`ubuntu-latest` and `macos-latest`.

Adapt individual commands when the repository layout changes, but preserve the
coverage represented by every line.

```bash
python3 -m pytest -q -n auto --dist worksteal skills/core/scripts --ignore=skills/core/scripts/knowledge_map  # drop -n/--dist without pytest-xdist
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
