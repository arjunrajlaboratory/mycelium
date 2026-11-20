# Data Lineage

Per-session capture of analysis-script provenance: what scripts ran, against
which input files, producing which outputs — with SHA-256 snapshots taken **at
execution time** so iterative edit-run-edit-run loops don't lose history.

The subsystem is independent from the rest of mycelium's logging: it fires when
an analysis-script invocation is detected (`python`, `R`, `Rscript`, `jupyter`,
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
explicit warning rather than dropped.

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
never reopen. An accepted Stop leaves `next-turn-baseline.json` (the idle
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

## Event schema (NDJSON, one line per detected script)

```json
{
  "ts": "2026-05-26T15:32:11Z",
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
  "inputs":  [{"path", "sha256", "size_bytes", "n_rows"?}],
  "outputs": [{"path", "sha256", "size_bytes"}],
  "io_detection": "static",
  "lineage_warnings": [],
  "filters_detected": ["df.query(...)", ...],
  "seeds_detected": [42, ...]
}
```

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
    "skipped_count": 0
  },
  "skipped": [<skip 1>, ...],
  "extraction_warnings": [...]
}
```

`skipped` and `summary.skipped_count` were added after 0.9.5. A manifest written
earlier has neither key: read its coverage as unknown, not as zero skips.

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
- Filters: `.query(…)`, `.sample(…)`, `.filter(…)`, boolean masks (`df[df.col > x]` / `df[df['col'] != '']`), `.loc[…]` / `.iloc[…]`, `.merge(…)`, `.join(…)`, `pd.concat(…)`
- Seeds: `np.random.seed(N)`, `random.seed(N)`, `torch.manual_seed(N)`, `np.random.default_rng(N)`

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
   (`INPUT_REGEXES`, `OUTPUT_REGEXES`, `FILTER_REGEXES`, or `SEED_REGEXES`).
2. Add a test case to `test_extract_data_lineage_event.py`.
3. Run `python3 -m pytest skills/core/scripts/test_extract_data_lineage_event.py -v`.

Capture group 1 of each regex is treated as the matched path/expression.
