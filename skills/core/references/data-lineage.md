# Data Lineage

Per-session capture of analysis-script provenance: what scripts ran, against
which input files, producing which outputs — with SHA-256 snapshots taken **at
execution time** so iterative edit-run-edit-run loops don't lose history.

The subsystem is independent from the rest of mycelium's logging: it records an
event when an analysis-script invocation is detected (`python`, `R`, `Rscript`, `jupyter`,
`uv run python`, `poetry run python`, `conda run … python`, including inline
`-c` / `-e`). Standard shell execution wrappers (`time`, `exec`, `nice`, and
`timeout`) may precede or nest around those interpreters.

Pipeline runners are detected too, recording their entry point as the event's
script: a shell script run as `bash`/`sh`/`zsh run.sh` or `./run.sh`, the
Snakefile a `snakemake` run uses (`-s`/`--snakefile`, else the default lookup),
an `sbatch` job script, and the `main.nf` of `nextflow run`. Non-executing modes
are not runs and are ignored, including those selected by an option's value:
`bash -n`/`-c`/`-s`/`-D`, `--dump-strings`, `--pretty-print`, `-o noexec`, and
any option or `-o`/`-O` name the shell would reject; snakemake dry runs, `--executor dryrun` or
`touch`, and graph/lint/list/unlock/touch modes; `nextflow run`
`-preview`/`-stub-run` and non-`run` subcommands; and `sbatch`
`--test-only`/`--help` (on the command line or in the script's `#SBATCH`
header). Entry points and run directories follow each runner's own rules:
Snakemake reads the Snakefile in the invocation directory and runs under
`--directory`; Slurm resolves a relative job script under `--chdir` and runs
the job in the command line's `--chdir`, else the `#SBATCH` header's.
Configuration the parser cannot see is never guessed: a run directory the shell
would expand (`$VAR`, `~`, a substitution), or a Snakemake profile that sets
`directory:` or `snakefile:` (or cannot be located), leaves the event's I/O or
entry unresolved with a warning. Profiles come from `--profile`, else
`$SNAKEMAKE_PROFILE` (the hook's environment or a `VAR=x` prefix on the
command), plus the workflow profile; a profile that puts Snakemake in a
non-executing mode (`executor: dryrun`, `dry-run: true`) is not a run when
every active profile agrees. `bash script` falls back to `PATH` when the script
is not in the cwd, as bash does. Redirection I/O numbers (`bash 2>err run.sh`)
are consumed with their operator. When no entry file
can be named (`sbatch --wrap`, a remote `nextflow run nf-core/<pipeline>`, a
snakemake run with no Snakefile found) the run is still recorded, with a
descriptive `script_source` and `io_detection: "unresolved"`. The scripts these
runners launch internally are not visible to the hook. If static scanning
cannot resolve any file paths, the execution is retained as unresolved with an
explicit warning rather than dropped. Any other Bash call that touches data
leaves a minimal `data_command` record instead (see
[Data commands](#data-commands)).

## Components

| File | Role |
|------|------|
| `skills/core/hooks/mycelium-data-tracker.sh` | PostToolUse:Bash. Per-Bash-call event capture. |
| `skills/core/hooks/mycelium-data-lineage-stop.sh` | Internal Stop phase. Consolidates cumulative events into a manifest before lifecycle enforcement. |
| `skills/core/hooks/mycelium-stop-check.sh` | Registered Stop command. Runs lineage consolidation synchronously, retains state when blocked, and rotates it after Stop is accepted. |
| `skills/core/scripts/extract_data_lineage_event.py` | Per-event extractor (called by tracker hook). |
| `skills/core/scripts/extract_data_lineage.py` | Manifest assembler (called by stop hook). |

Both hooks resolve their Python sibling via
`$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../scripts/<file>.py`, so they
work from any install location without hardcoded paths. Env-var overrides
(`MYCELIUM_DATA_HELPER`, `MYCELIUM_DATA_EXTRACTOR`) are provided as escape
hatches for testing.

## On-disk layout

Per-session (`<sid>` = mycelium date-counter `YYYY-MM-DD-NNN`, or Claude Code
UUID fallback if mycelium hasn't recorded an active session):

```
.mycelium/
  mycelium-data-events.tmp                  # NDJSON, one event per detected script
  mycelium-data-events-prev/<sid>.tmp       # rotated on accepted Stop (20-file cap)
.living/
  log/
    data-lineage/<sid>.json                 # consolidated manifest
    .data-lineage-status-<sid>.json         # status sentinel (20-file cap)
```

The events tmp file is appended to under `fcntl.flock(LOCK_EX)` from inside the
Python helper. Shell `>>` is only atomic up to `PIPE_BUF` (~4 KB on macOS),
and embedded script source can exceed that — concurrent Bash tools firing
PostToolUse hooks in parallel would otherwise interleave NDJSON lines.
If lifecycle enforcement blocks Stop, the marker and cumulative event file stay
in place; the next Stop attempt rebuilds the same session manifest with any
additional analysis events. Cleanup and raw-event rotation happen only after
the Stop check accepts the session.

Claude Code and Codex run Stop after every turn, so an accepted Stop closes a
turn, not the host session. When the tracker finds no transaction for its host
session and the command would record lineage, it reopens that session's
transaction through SessionStart's locked setup (`source: reopen`) and records
the event there; the next turn's lineage therefore lands in a new manifest
rather than being dropped. Commands the detector does not treat as analysis
never reopen; a data command is parked instead (see
[Data commands](#data-commands)). An accepted Stop leaves `next-turn-baseline.json` (the idle
turn's baseline, or a fresh snapshot after a turn with work) and
`next-turn-start-ts.tmp` (when it accepted) in the run directory. The
reopened transaction starts from both, so the triggering command's own file
changes and commits count as session work. A host startup of the same session
drops them; an ended session's run directory keeps them (a few kilobytes),
because deleting them by age would also hit a live session that idled. A
turn whose only work was shell edits reopens nothing from PostToolUse, so a
Stop with no transaction compares the repository (outside `.living/` and
`.mycelium/`) against the carried baseline and, if anything changed, reopens
and runs once more against that transaction. If only `.living/` changed, it
refreshes the carried baseline and start time instead, so a note from a turn
with no other work cannot count as reflection for a later turn's work. The
carried start time bounds accounting only; the reopened marker's liveness
timestamp is always the time of the reopen. After a turn with work, Stop takes
the successor snapshot before removing any active state; if it cannot be
written, Stop blocks with the transaction intact rather than leave the next
turn without a baseline.

A detected script that is gone or unreadable when the hook runs
(`python3 tmp.py && rm tmp.py`) is not an event, since a missing file is no
evidence of a run, but it is not silent either: the tracker records a
`script-unreadable` skip naming the path.

### Skips

A command the tracker meant to record but could not leaves a skip record
instead of nothing, so a missing event is never read as "nothing ran":

| `skip_reason` | When | Where |
|---|---|---|
| `extractor-failed` | the per-event extractor exited nonzero (`detail`: `exit N`) | session raw stream |
| `extractor-missing` | the extractor file is absent; recorded once per session, since no command can be classified | session raw stream, or unowned file |
| `detector-failed` | with no transaction, the pre-reopen check crashed (`detail`: `exit N`; the detector exits 70 on an internal error, never 1, which means "not analysis") | `.mycelium/lineage-skips-unowned.ndjson` |
| `no-transaction` | reopening was refused or failed (corrupt marker, busy lifecycle lock) | `.mycelium/lineage-skips-unowned.ndjson` |
| `script-unreadable` | a detected script was gone or unreadable when the hook ran (`detail`: its path) | session raw stream |

```json
{"ts": "...", "kind": "skip", "skip_id": "9f1c...", "skip_reason": "extractor-failed",
 "detail": "exit 3", "bash_cmd": "python3 analysis.py", "bash_exit": 0,
 "agent_id": null, "agent_type": null}
```

In the session raw stream, skips are consolidated into the manifest's
`skipped` list, and Stop's message carries a `LINEAGE GAP:` line naming the
count and reasons. Unowned records carry `host` and `host_session_id`. That
session's next Stop moves them into its raw stream only when it holds a valid
transaction it owns; on every other path (no transaction, an invalid marker,
another owner) it reports them directly in its message. The shared file is
locked through `lineage-skips-unowned.ndjson.lock` and rewritten by atomic
replacement, so a failed rewrite keeps every record; a rewrite that fails
after a transfer can leave a copy in both places, so every reader counts a
`skip_id` once, and a Stop whose move into its own stream failed blocks
until a retry completes it, so no later turn reports the same gap again.
Before writing to it, a hook re-checks the shared state
directory's containment and symlinks, and refuses a payload without a valid
host session ID. Records are ASCII-only JSON, so no Unicode line separator in
a command can split one. A skip record holds only the command text the host already
showed, the reason, and a short detail; never data. When the shared state
directory itself fails its safety checks there is nowhere safe to write, and
the skip is not recorded.

### Data commands

The analysis detector records only interpreter runs it can prove. Every other
Bash call that touches data leaves one minimal record of its own kind, so a
look at a data file or an inline read the detector rejects still counts toward
how many times the data was looked at:

- shell operations on data (`head`, `zcat`, `awk`, `cut`, `sort`, `cp`, `mv`,
  `samtools`, …) whose text names a data-like path: a file with a data
  extension (the scan list plus compressed and common genomics, tree, and
  image formats), a path under `data/`, or a path under `analysis/**/outputs`.
  Tooling and Mycelium state (`.living/`, `.mycelium/`, `.git/`, `.claude/`,
  `.codex/`, `node_modules/`, `package.json`, `requirements.txt`, …) and URLs
  are not data;
- interpreter runs the detector cannot prove (a heredoc-fed
  `python3 - <<'PY'`, `Rscript` inside a `for` loop, a `cd` it cannot
  follow, a script run through `xargs`), labelled in `analysis_candidates`.
  Each simple command inside unmodelled structure is unwrapped (`time`,
  `nohup`, `uv run`, `conda run -n`, `timeout`, `xargs`, ...) and read by
  the detector's own grammar without the execution-proof requirement, so
  terminal and non-executing modes (`--version`, `bash -n`, `snakemake -n`),
  tooling modules (`python -m pytest`), and Mycelium's own helpers are
  excluded exactly as there. Labels are the resolved script path or
  `<interpreter> (inline)`; an interpreter reading its program from stdin
  (heredoc, `xargs python3`) is labelled by name. Quoted text
  (`echo "python a.py"`) and heredoc bodies (a README written with
  `cat <<EOF`) are not runs. Labels are hints, never claims.

```json
{"ts": "...", "kind": "data_command", "event_id": "4b0e...",
 "bash_cmd": "sort data/x.csv > data/sorted.csv", "bash_exit": 0,
 "cwd": "/path/to/repo", "agent_id": null, "agent_type": null,
 "signals": ["data-path"], "named_paths": ["data/x.csv", "data/sorted.csv"],
 "named_paths_truncated": false, "analysis_candidates": [],
 "inputs": [],
 "outputs": [{"path": "/path/to/repo/data/sorted.csv", "sha256": "...",
              "size_bytes": 12, "evidence": "redirection"}],
 "io_detection": "unresolved"}
```

A data command never claims that an analysis ran (no `script` or
`script_sha256`), and `io_detection` is always `"unresolved"`.
`named_paths` lists literal data-like words in the command text, in order,
capped at 64: they are mentions, not accesses (the word may sit in a comment,
an `echo`, or a branch that never ran), so they carry no direction and are not
read or hashed. `inputs` and `outputs` hold only paths whose direction shell
syntax alone proves. The command must have exited 0 and be a plain `&&` list
of literal simple commands, so every command in it ran and succeeded:

- a `<` redirection target is an input; a `>`, `>>`, `>|`, `&>`, or `&>>`
  target is an output;
- `cp SRC DST` with only role-preserving flags (never `-n`, `-i`, `-u`, `-t`,
  `-s`, or a long option) read a regular-file source and wrote its
  destination (inside it when it is a directory), and `mv SRC DST` that
  renamed a file onto a regular file did the same. Only when every later
  command in the list is read-only, since the destination's kind is read from
  the final filesystem. A symlinked source or destination, a source still
  present after `mv`, a move into a directory, and a copy onto itself claim
  nothing;
- `cd DIR` is followed logically (as bash does through symlinks) to an
  existing directory; a bare name is unresolved when `CDPATH` is mentioned
  or set. After any command that could move or remove a directory
  (everything outside a short list of read-only and file-creating commands,
  including `cp` and `mv`), later relative paths are unresolved.
- `exit`, `return`, `exec`, `set`, `!`, `eval`, `source`, and wrappers
  (`time`, `env`, `nohup`, `timeout`, `xargs`, `coproc`, ...) end what the
  zero status proves; nothing at or after them is claimed.

Pipes, `;`, `||`, backgrounding, subshells, substitutions, `$` expansions,
globs, braces, tilde, heredocs, and an unknown exit status claim nothing. A
command's own operands (`head data/x.csv`) are never inputs: that would need
each tool's grammar. Files are hashed at hook time; a FIFO, device, or
directory is recorded without being opened, and every open is non-blocking
with a regular-file check on the descriptor. Long commands stay linear: the
words are read by a one-pass tokenizer, and the label pass falls back to a
command-position scan beyond 64 KB.

A call with a proven analysis run gets its analysis events and no data
command, so each Bash call yields at most one kind.

**Lifecycle.** Data commands never reopen a transaction, never open a
post-action cycle, and are not session work: a turn whose only lineage is
data commands is accepted without a session log, exactly as before. With a
transaction they go to the session's raw stream. Without one they are parked
in `.mycelium/lineage-skips-unowned.ndjson`, keyed by `host` and
`host_session_id` like unowned skips. That session's next Stop (hosts run it
at the end of every turn) never leaves them waiting: with a valid transaction
it owns, it moves them into its stream; otherwise (no transaction, an invalid
marker, a non-owner child) it reports the skips and writes the data commands
to a manifest of their own. If that manifest cannot be written (a linked
lineage directory, a failed consolidation) they stay parked for the next
Stop. A record that cannot be built becomes a `data-command-failed` skip,
and one that cannot be written exits 70, which the tracker records as a
`detector-failed` gap.

**Data-only manifests.** A stream holding only data commands is not session
work, so a turn with nothing else drops its session log and releases its
`YYYY-MM-DD-NNN` number, which the next session may take again. Such a stream
is therefore never named by that number:
`.living/log/data-lineage/data-commands-<host>-<host session id>-<first event_id, 12 hex>.json`
(the manifest's `session_id` is the same name). A retried Stop rewrites the
same file. When the stream later gains an analysis event (a blocked Stop,
then more work), the session manifest written under the session number
replaces the data-only one, which is removed.

The manifest keeps them apart from `actions` in `data_commands`, counted by
`summary.data_command_count`; each `event_id` is counted once. Their paths do
not enter `summary.unique_inputs`/`unique_outputs`.

## Event schema (NDJSON, one line per detected script)

The authoritative field definitions for every record below, for v1 (0.9.7
and earlier) and v2 (0.9.8 onward), are in
[`provenance-schema.json`](provenance-schema.json) (JSON Schema 2020-12).
That file is what Warrant and other readers validate against. This section
describes the fields; where the two differ, the schema file wins.

```json
{
  "ts": "2026-05-26T15:32:11Z",
  "event_id": "9f2c…",          # v2
  "host": "claude",              # v2
  "host_session_id": "…",        # v2
  "ts_ns": 1764000000000000000,  # v2
  "repo_root": "/path/to/repo",  # v2: the frame for every rel_path
  "agent_id": null,
  "agent_type": null,
  "bash_cmd": "python analysis/foo.py",
  "bash_exit": null,
  "bash_wall_s": null,
  "script": "analysis/foo.py",
  "script_sha256": "ab12...",
  "script_source": "...",
  "script_candidate": null,
  "git_sha": "c3d4...",
  "inputs":  [{"path", "rel_path", "sha256", "size_bytes", "hash_origin", "pre_run_proven"?}],
  "outputs": [{"path", "rel_path", "sha256", "size_bytes", "hash_origin",
               "observed_change"?, "observed_sha256"?}],
  "io_detection": "static",
  "lineage_warnings": [],
  "seeds_detected": [42, ...],
  "step": {"argv", "interpreter", "script_args", "env", "config_files", "seeds"}  # v2
}
```

### v2 identity and frame (0.9.8)

- **`event_id`, `host`, `host_session_id`, `ts_ns`.** Every analysis event
  and data command now has an identity. `ts_ns` orders records within a
  second. Write order between calls comes only from the observation's
  `journal` (`id`, `pre_seq`, `post_seq`, `expired`), never from clocks.
- **`repo_root` and `rel_path`.** `repo_root` is the physical checkout the
  hook resolved. Each file record's `rel_path` is relative to it, framed with
  the observer's rules: no `..`, no symlinked component below the root. A path
  that cannot be framed has `rel_path: null` and a `rel_path_reason`.
- **`hash_origin`.** Says how each hash was taken:
  - `pre-run-proven`: the bytes matched the Pre snapshot;
  - `hashed-at-post`: the observer's stable hash at Post;
  - `hook-post`: the hook's plain digest after the call;
  - `unknown`: a 0.9.7 record.

  Outputs also carry `observed_sha256` (the observation's protocol hash) where
  the call's observation saw them change. The hook digest is never promoted
  to an observed hash.

### Step settings (`step`, v2)

Read from the detected stage's own text, never guessed:

- `argv`, `executable`, `interpreter` and `script_args`. Inline `-c`/`-e`
  bodies become `{"inline_sha256", "length"}`.
- `env`: assignment and `env VAR=x` prefixes of that stage. Unexpanded
  `$VAR` values are marked `expanded: false`.
- `config_files`: named by interpreter-specific operand rules, then hashed
  within one budget per call (8 files, 5 MB each, 250 ms) and proven
  against the Pre snapshot like inputs. A config that changed during the call
  is `effective: unknown`.
- `seeds`: literal seeds from argv (`--seed N`) and source (`np.random.seed`,
  `set.seed`), with their origin.

Two runs of one script with different arguments are two events. Identical
stages (a loop body) are one. A call with more than 64 analysis stages records
the first 64, with `stages_not_recorded`.

`filters_detected` is no longer written. It held unevaluated regex text with
false positives, and nothing read it. Historical records keep it. Evaluated
row and feature access is a separate, in-process record (see
`docs/plans/row-feature-access.md`).

### Redaction

Every stored command text goes through one policy (`lineage_redact.py`):
`bash_cmd`, `argv`, `script_args`, `env`, embedded `script_source`, and the
`bash_cmd` of observations and skips. These values become `<redacted>`:

- secret-named assignments and options (token, secret, password, api key,
  credential, auth, cookie);
- URL passwords;
- `Authorization:` header values;
- quoted secret literals in code.

Nothing is hashed, because a short secret's digest can be guessed. A
redacted embedded source has `script_sha256_of: "sanitized"`.

`bash_exit` is populated when the host exposes a Bash exit code. Conditional
analysis commands are recorded only when the status or a statically known
prefix proves their AND/OR branch executed. Every component of a supported
pipeline is treated as executed, including a failed final analysis command.
Unquoted shell comments are removed before inference, while quoted and escaped
`#` characters remain part of arguments. Ambiguous textual matches are skipped
rather than asserted as provenance. Compound shell commands, heredocs, and
interpreter tokens outside a supported command position are omitted because the
hook does not receive a per-command execution trace. `bash_wall_s` remains
reserved for future host support. The post-action reminder hook uses the same
execution check so skipped, commented, or merely quoted analysis text does not
open a false lifecycle cycle.

Interpreter, flag, script, and notebook arguments are decoded as shell words,
including adjacent quoted, bare, and backslash-escaped components such as
`"analysis script".py` and `'analysis'\ script.py`.

A `python a.py && python b.py` chain emits **two** events (one per detected
script). Inline `-c` scripts emit one event each; their `script` field is
`null`, with the source embedded under `script_source` and identified by
`script_sha256`.

`script_candidate` is `null` except for an R script run (`R -f`,
`R --file=`, `R CMD BATCH`) whose script cannot be named with certainty:
shell syntax the scan cannot read (`R -f a.R $OPTS`, `--file="$SCRIPT"`) or
`R_BATCH_OPTIONS` may change which file R reads. The run is still recorded,
with `script`, `script_sha256`, and `script_source` all `null`,
`script_candidate` holding the file word as the command wrote it (unexpanded,
so it may be `$SCRIPT`), empty `inputs`/`outputs`, `io_detection:
"unresolved"`, and a `lineage_warnings` entry. The candidate is never read or
hashed, since R may have run a different file.

For dynamic `Path`/dictionary composition, a conservative repository fallback
may recover a uniquely named existing file. Candidates must data-flow into the
path argument of a recognized reader or writer, reader aliases must come from
an actual supported import and remain unshadowed, and direction comes from that
call rather than the file's directory. Expressions that choose among runtime
branches or concatenate an unresolved operand remain unresolved; fully static
string concatenation remains eligible. Directly resolved paths bypass the
fallback entirely. Dynamic searches exclude runtime/vendor trees and stop at a
fixed entry bound; ambiguity or a bound hit remains unresolved rather than
guessing.

When I/O is delegated through imports or no safe candidate can be recovered,
the event uses `"io_detection": "unresolved"`, leaves `inputs` and `outputs`
empty, and adds a `lineage_warnings` entry. Stop-time consolidation promotes
that warning to the manifest's `extraction_warnings` list. This preserves
evidence that the script ran without implying that its lineage was fully
captured.

## Manifest schema (consolidated at Stop)

```json
{
  "schema": "mycelium.data_lineage.manifest.v2",   # absent in v1 (0.9.7 and earlier)
  "session_id": "2026-05-26-099",
  "repo_root": "/path/to/repo",
  "git_sha": "c3d4...",
  "started_at": "2026-05-26T15:32:11Z",
  "ended_at":   "2026-05-26T15:48:02Z",
  "n_actions": 7,
  "agents_seen": ["main", "<subagent-id>", ...],
  "actions": [<event 1>, <event 2>, ...],
  "summary": {
    "unique_inputs":  [{"path", "sha256"}, ...],
    "unique_outputs": [{"path", "sha256"}, ...],
    "total_wall_seconds": 12,
    "timed_action_count": 5,
    "total_action_count": 7,
    "known_exit_count": 5,
    "failed_action_count": 0,
    "scripts_executed": ["analysis/foo.py", "(inline ab12cd34ef56)", ...],
    "skipped_count": 0,
    "data_command_count": 0
  },
  "skipped": [<skip 1>, ...],
  "data_commands": [<data command 1>, ...],
  "extraction_warnings": [...]
}
```

Manifests are written atomically: a reader sees the old file or the new
one, never a torn file. Re-consolidating a v1 record into a v2 manifest
keeps it v1-shaped (no identity, `hash_origin` unknown).

`skipped` and `summary.skipped_count` were added after 0.9.5. A manifest written
earlier has neither key: read its coverage as unknown, not as zero skips. The
same holds for `data_commands` and `summary.data_command_count`.

`total_wall_seconds` sums only actions whose host payload carried a wall
time and is `null` when none did (some hosts, including current Codex Bash
PostToolUse, omit per-action timing). The `timed_action_count` /
`total_action_count` pair makes partial timing coverage explicit, and
`known_exit_count` / `failed_action_count` apply the same rule to exit
codes: an unknown exit is neither a success nor a failure.

`scripts_executed` uses the script path when present, falling back to
`(inline <sha-prefix>)` so inline-only sessions still produce a populated
list, and to `(unresolved <candidate>)` for an R run whose script is
uncertain. Repeated invocations of the same inline source collapse to one
entry.

## The project index: `lineage_index.py`

`skills/core/scripts/lineage_index.py` answers provenance questions across
sessions for one checkout:

```bash
python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/lineage_index.py" query --hash <sha256>
python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/lineage_index.py" query --path data/processed/x.csv
python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/lineage_index.py" query --event <event_id>
```

**Sources.** It reads every manifest and every local observation store.
Observation stores include edit-tool observations (Write, Edit, apply_patch
by path) and Bash calls no event referenced.

**Writes.** These come from observations, one set per call. A manifest
committed from another checkout contributes readers but never writes.

**Statuses.** For each write, the index reports:

- a **record** status: `current-in-record`, `superseded` (naming the
  superseding call), `superseded-unverified`, or `unknown` with reasons
  (`order-unproven`, `gap`, `unwatched-path`, `write-hash-unknown`,
  `inferred-write`, …);
- a separate **disk** result: `match`, `differs`, `missing`, `unverified`.

The headline is `current` only when both agree. Supersession needs journal
order and two known, different hashes. A gap the record cannot rule out makes
the status `unknown`, never `current`. Examples of such gaps: an unavailable,
missing or unresolved observation, a capped diff, an unwatched scope.

**Readers.** Readers are listed with their `hash_origin`, proven first. A
hook cannot see reads, so every read stays inferred.

**Cache.** The index is cached per checkout under
`.mycelium/lineage/index-<root hash>.json`. It is rebuilt incrementally from
changed sources on every query, is never committed, and never runs on the
hook path. `--record-only` skips the disk, and `--no-write` never writes the
cache. Exit codes: 0 answered, 1 nothing recorded, 2 bad input.

## Detection coverage

**Analysis invocation patterns detected** (matched anywhere in the command):
- `python` / `python3` (script path or `-c` inline)
- `Rscript` (script path or `-e` inline)
- `R … -e "…"` (inline), `R … -f script.R` / `R … --file=script.R`, and
  `R [options] CMD BATCH [options] script.R`, with R's own startup options
  (`-q`, `-s`, `--encoding ENC`, `--max-ppsize N`, …) before the payload;
  `--help`/`--version`/`--print-home`/`RHOME`/`CMD` or `-d`/`--debugger`
  anywhere before `--args` means R never runs the payload. R's argv is read
  word by word up to `--args`, with line continuations joined and
  redirections (including `"${LOG}"` targets) dropped. A word the shell
  computes (a `$` or backtick outside single quotes) is unknown. Inside
  double quotes (`-f "$D/a.R"`, `--encoding "$ENC"`) it is still one word
  and reading continues past it; unquoted, or where an option belongs, it
  ends what can be read, as does shell syntax the reader cannot follow.
  Known `-e` expressions are recorded and computed ones never are; an
  `R -f`/`R CMD BATCH` run's script is unresolved when its file word is
  computed or a later unreadable word could name another file. `R CMD BATCH`
  also appends `$R_BATCH_OPTIONS`. A literal assignment on the BATCH command
  itself (`R_BATCH_OPTIONS=--vanilla R CMD BATCH a.R`) is read exactly; any
  other mention (an `export`, `env`, a computed value) is not modelled, so
  the run is unresolved unless its options already stop R. Otherwise the
  variable is ignored (the hook's own environment is not the agent shell's).
  A heredoc's delimiter is skipped like any redirection target, and a `$`
  that starts no expansion (`cat('$')`) is literal.
- `jupyter nbconvert|execute notebook.ipynb`
- `conda run … python …`
- `uv run … python …`
- `poetry run … python …`
- `time`, `exec`, `nice`, and `timeout` before or nested around any supported
  interpreter invocation

**Data I/O patterns scanned in the script body** (case-sensitive Python idioms):
- Reads: `pd.read_{parquet,csv,tsv,feather,hdf,h5,json,excel,stata,sas,orc,pickle,table}`, `ad.read_h5ad`, `ad.read_csv`, `np.load`, `xr.open_{dataset,dataarray,zarr,mfdataset}`, `sc.read{,_h5ad,_csv,_mtx,_10x_h5,_10x_mtx}`
- Writes: `.to_{parquet,csv,tsv,feather,hdf,h5,json,excel,stata,sas,orc,pickle,table}`, `.write_{csv,parquet,json,h5ad}`, `np.save{,_compressed,z}`, `(plt|fig|ax).savefig`, `.to_netcdf`
- Seeds: `np.random.seed(N)`, `random.seed(N)`, `torch.manual_seed(N)`, `np.random.default_rng(N)`, and R `set.seed(N)`; a call inside a string literal or comment is not a seed
- Filters are no longer scanned (see *Step settings*)

**Not statically resolved (recorded as warnings)**:
- R-side I/O (only R *invocation* is detected, not its inputs/outputs)
- Generic `open(path)` reads — false-positive risk too high
- Dynamic paths (`pd.read_csv(some_var)`) — only string literals are captured

## Limits

- Script source larger than 100 KB is referenced by SHA but not embedded
- Files larger than 100 MB get path + size only, no SHA
- `bash_exit` is populated when the host exposes it; otherwise conditional
  branches without independent execution evidence are omitted conservatively
- `bash_wall_s` remains `null` until a host exposes reliable wall time

## Installation

`init_repo.install_claude_hooks()` registers the tracker and the coordinating
Stop hook. The lineage consolidation script is invoked synchronously from that
coordinator rather than registered as a sibling. Existing Mycelium repositories
are upgraded by `migrate_existing_repos.py`; the migration removes the old
standalone registration and refreshes the coordinator path atomically.

## Extending the regex pack

Both hooks deliberately use plain regex (not AST) so they can scan inline
`-c` snippets without parsing. Adding a new I/O pattern:

1. Add a `re.compile(...)` to the relevant list in `extract_data_lineage_event.py`
   (`INPUT_REGEXES`, `OUTPUT_REGEXES`, `SEED_REGEXES`, or the R lists `R_INPUT_REGEXES`, `R_OUTPUT_REGEXES`, `R_SEED_REGEXES`).
2. Add a test case to `test_extract_data_lineage_event.py`.
3. Run `python3 -m pytest skills/core/scripts/test_extract_data_lineage_event.py -v`.

Capture group 1 of each regex is treated as the matched path/expression.
