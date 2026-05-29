# Data Lineage

Per-session capture of analysis-script provenance: what scripts ran, against
which input files, producing which outputs — with SHA-256 snapshots taken **at
execution time** so iterative edit-run-edit-run loops don't lose history.

The subsystem is independent from the rest of mycelium's logging: it fires only
when actual data analysis is detected (`python`, `R`, `Rscript`, `jupyter`,
`uv run python`, `poetry run python`, `conda run … python`, including inline
`-c` / `-e`). Sessions that don't touch data produce no output.

## Components

| File | Role |
|------|------|
| `skills/core/hooks/mycelium-data-tracker.sh` | PostToolUse:Bash. Per-Bash-call event capture. |
| `skills/core/hooks/mycelium-data-lineage-stop.sh` | Stop. Consolidates events into a manifest. |
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
.claude/
  mycelium-data-events.tmp                  # NDJSON, one event per detected script
  mycelium-data-events-prev/<sid>.tmp       # rotated on Stop (20-file cap)
.living/
  log/
    data-lineage/<sid>.json                 # consolidated manifest
    .data-lineage-status-<sid>.json         # status sentinel (20-file cap)
```

The events tmp file is appended to under `fcntl.flock(LOCK_EX)` from inside the
Python helper. Shell `>>` is only atomic up to `PIPE_BUF` (~4 KB on macOS),
and embedded script source can exceed that — concurrent Bash tools firing
PostToolUse hooks in parallel would otherwise interleave NDJSON lines.

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
  "git_sha": "c3d4...",
  "inputs":  [{"path", "sha256", "size_bytes", "n_rows"?}],
  "outputs": [{"path", "sha256", "size_bytes"}],
  "filters_detected": ["df.query(...)", ...],
  "seeds_detected": [42, ...]
}
```

`bash_exit` / `bash_wall_s` are reserved — PostToolUse stdin doesn't currently
expose exit code or wall time, but the fields stay in the schema so they can
be populated forward-compatibly.

A `python a.py && python b.py` chain emits **two** events (one per detected
script). Inline `-c` scripts emit one event each; their `script` field is
`null`, with the source embedded under `script_source` and identified by
`script_sha256`.

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
    "total_wall_seconds": 0,
    "scripts_executed": ["analysis/foo.py", "(inline ab12cd34ef56)", ...]
  },
  "extraction_warnings": [...]
}
```

`scripts_executed` uses the script path when present, falling back to
`(inline <sha-prefix>)` so inline-only sessions still produce a populated
list. Repeated invocations of the same inline source collapse to one entry.

## Detection coverage

**Analysis invocation patterns detected** (matched anywhere in the command):
- `python` / `python3` (script path or `-c` inline)
- `Rscript` (script path)
- `R … -e "…"` (inline)
- `jupyter nbconvert|execute notebook.ipynb`
- `conda run … python …`
- `uv run … python …`
- `poetry run … python …`

**Data I/O patterns scanned in the script body** (case-sensitive Python idioms):
- Reads: `pd.read_{parquet,csv,tsv,feather,hdf,h5,json,excel,stata,sas,orc,pickle,table}`, `ad.read_h5ad`, `ad.read_csv`, `np.load`, `xr.open_{dataset,dataarray,zarr,mfdataset}`, `sc.read{,_h5ad,_csv,_mtx,_10x_h5,_10x_mtx}`
- Writes: `.to_{parquet,csv,tsv,feather,hdf,h5,json,excel,stata,sas,orc,pickle,table}`, `.write_{csv,parquet,json,h5ad}`, `np.save{,_compressed,z}`, `(plt|fig|ax).savefig`, `.to_netcdf`
- Filters: `.query(…)`, `.sample(…)`, `.filter(…)`, boolean masks (`df[df.col > x]` / `df[df['col'] != '']`), `.loc[…]` / `.iloc[…]`, `.merge(…)`, `.join(…)`, `pd.concat(…)`
- Seeds: `np.random.seed(N)`, `random.seed(N)`, `torch.manual_seed(N)`, `np.random.default_rng(N)`

**Not detected (deliberate)**:
- R-side I/O (only R *invocation* is detected, not its inputs/outputs)
- Generic `open(path)` reads — false-positive risk too high
- Dynamic paths (`pd.read_csv(some_var)`) — only string literals are captured

## Limits

- Script source larger than 100 KB is referenced by SHA but not embedded
- Files larger than 100 MB get path + size only, no SHA
- `bash_exit` and `bash_wall_s` are always `null` until Claude Code exposes
  them through PostToolUse stdin

## Installation

`init_repo.install_claude_hooks()` registers both hooks alongside the existing
five. Existing mycelium-enabled repos get the new hooks back-filled by
`migrate_existing_repos.py` — both flows are idempotent and consolidate stale
duplicates.

## Extending the regex pack

Both hooks deliberately use plain regex (not AST) so they can scan inline
`-c` snippets without parsing. Adding a new I/O pattern:

1. Add a `re.compile(...)` to the relevant list in `extract_data_lineage_event.py`
   (`INPUT_REGEXES`, `OUTPUT_REGEXES`, `FILTER_REGEXES`, or `SEED_REGEXES`).
2. Add a test case to `test_extract_data_lineage_event.py`.
3. Run `python3 -m pytest skills/core/scripts/test_extract_data_lineage_event.py -v`.

Capture group 1 of each regex is treated as the matched path/expression.
