# Provenance items 3–6: event identity, a content-hash index, step settings, hash-linked report values, a sample ledger

Status: implemented on `feat/provenance-index` (2026-11-22). Results are in
"Measured results" at the end. The design text below is revision 2; the
revision 3 amendments win where they conflict.

Design history: revision 2 (2026-11-22). Revision 1 was reviewed by a Claude
reviewer and by Codex (read-only). Both asked for the same structural changes,
and this revision makes them:

- writes come from observations, per call, not from inferred outputs;
- uncertain evidence stays uncertain through the query;
- status is per path and per write, with evidence-based ordering;
- a v2 path frame;
- atomic manifests and an on-demand incremental index (no build at Stop);
- step settings captured inside detection;
- one redaction policy for every place the command text is stored;
- one source-resolution contract for the three report tools;
- a ledger that does not reuse model-table row semantics.

**Revision 3 amendments** (a Codex blocking-only review of revision 2). Where
these conflict with the text below, the amendments win.

1. **Observation stores are an index source.** The index also reads every
   observation store of the checkout (`<shared>/io/obs/**/*.ndjson`), deduped
   with the manifests by observation key. An observation no event references
   (the tracker failed after the observer wrote, an edit-only turn, an
   unowned flush) still contributes its writes, attributed to a call with no
   events. The manifests stay the committed record. The stores are local
   evidence and are labelled `source: store`.
2. **Edit-tool writes are observed by path.** The observer's PostToolUse
   for Write, Edit, MultiEdit and NotebookEdit (Claude), and for apply_patch
   (Codex, from the patch's `*** Add/Update/Delete File:` lines), appends an
   `edit` observation. It lists the edited paths with the post-edit hash of
   each, under the same per-call hash budget, and journal bounds. Edit calls
   stop being whole-repo gaps.
3. **Observations carry their checkout.** Every observation stores the
   physical `repo_root`. A Post whose Pre snapshot names a different root is
   `unavailable` with reason `cross-root`. The index uses only observations
   and manifests whose `repo_root` is this checkout. Unowned data commands
   already carry `repo_root`, and the flush keeps only those of its checkout.
4. **Status splits record and disk.** Each write's result has:
   - a `record` status: `current-in-record`, `superseded`,
     `superseded-unverified`, or `unknown` with reasons;
   - a separate `disk` result: `match`, `differs`, `missing`, `unverified`.

   The combined headline `current` needs both `current-in-record` and
   `match`. Rules for `record`:
   - Supersession is asserted only on journal order with trustworthy bounds:
     same `journal_id`, neither interval expired, and
     `earlier.post_seq < later.pre_seq`.
   - Clock order is display only. A later write ordered only by clock gives
     `unknown` with reason `order-unproven`.
   - A whole-repo gap that is not provably before the write gives `unknown`
     with reason `gap`. Whole-repo gaps are unavailable, missing or
     unresolved observations in this checkout.
   - A path-specific gap gives `unknown` with reason `gap` for that path:
     a capped diff, an unwatched scope covering the path, or a racy entry
     naming it.
5. **`hash_origin` is written by every v2 hash producer** and preserved through
   consolidation:
   - inputs: `pre-run-proven`, `hashed-at-post`, `hook-post`;
   - outputs and config files: `hook-post`;
   - observation sides: `observed`.

   v1 digests get `hash_origin: unknown`, unless the record carries
   `pre_run_proven`.
6. **Config files are proven against the Pre snapshot,** exactly like inputs.
   They go through `prove_inputs` and get `pre_run_proven` with a reason. A
   config replaced during the call is therefore not reported as the step's
   settings: `effective: unknown`. The post-only digest is kept as separate
   evidence.
7. **Redaction covers every serialized representation:** `bash_cmd`, `argv`,
   `script_args`, `env`, `step.params`, embedded `script_source` (file-backed
   too), and observation and skip `bash_cmd`. When redaction changed a body,
   `script_sha256` is the hash of the sanitized text, labelled
   `script_sha256_of: sanitized`. No digest of a secret-bearing raw body is
   stored.
8. **`register_value`'s hash cache** is keyed by the opened file's (dev, ino,
   size, mtime_ns, ctime_ns), with fstat before and after hashing and a
   path/descriptor identity check. An unstable source raises.
9. **TeX gate:** `check_report_values.py --manifest` is an explicit TeX gate
   step before rendering macros. `check_html_report.py --manifest` stays on
   the HTML path only.
10. **Local ledger labels** compose the full ancestor tuple into one identity,
    used by nesting, replication and pairing alike, including in emitted
    contracts. Cross-group reuse (`units_may_span_groups`) is allowed only
    when the declared pairing verifies that same unit across the group
    column.

**Post-PR review amendments** (Codex review rounds on PR #154):

- **Supersession and gaps.** A journal-ordered later observed write with different known bytes, or a deletion, makes a write `superseded` even when a gap is open. Whatever the gap hides, those bytes were replaced. The applicable gaps are reported with the verdict.
- **Open calls are reported, not counted.** A Bash call with a journaled Pre and no Post yet is listed under `open_calls` with each later verdict. It does not make the verdict `unknown`, because the command running a query is always such a call.
- **Skips are reconciled.** Skips carry their call's `observation_id`. A skip whose call has an observation with a change list is covered, and any other skip is a gap.
- **Background jobs.** A background job stays a gap even after the journal expires its interval: the timeout is not evidence the job stopped.

Item 7 (row and feature access) is a separate note,
`docs/plans/row-feature-access.md`, for agreement before building.

Source: `BioReasoning/docs/mycelium-provenance-prompt.md`, items 3–6, the
Constraints, and "Done when". Items 1–2 shipped in 0.9.7
(`docs/plans/lineage-observed-io.md`).

## Constraints carried from the prompt

- It works under Claude Code and Codex.
- It is backward compatible:
  - 0.9.7 manifests and observations load;
  - new fields are optional on read;
  - a missing field means "unknown", never "pass".
- Hooks stay cheap:
  - nothing new walks the tree on the hook path;
  - item 4 hashes at most a few small config files per call, under a per-call
    budget;
  - the index is built only when queried.
- Hashes, sizes, paths and counts only, never data content. Secrets are
  redacted, not hashed.
- A missing record is reported as a gap, never read as "nothing happened".

## Item 3: identity, the path frame, and the index

### 3a. Records (v2)

**Analysis events** gain:

- `event_id` (uuid4 hex);
- `host` and `host_session_id`. The tracker passes `--host` and
  `--host-session-id`; the flags already exist but the owned call omits them.
  `host` is kept even when there is no session id.
- `ts_ns` (`time.time_ns()` in the extractor), beside the second-resolution
  `ts`;
- `repo_root`: the physical git toplevel that the hook used, so each record
  carries its own frame. This handles a session that moved into a worktree.

**File records** gain `rel_path`, the path relative to that `repo_root`.

- It is computed with the same rules as `io_observer.repo_relative_unfollowed`:
  a lexical relpath first, then a realpath of the parent, refusing `..` and
  symlinked components.
- When it cannot be computed, `rel_path` is null with `rel_path_reason`.
- `path` stays absolute.

**data_command records** also carry `host`, `host_session_id`, `ts_ns` and
`repo_root` on every record, not only when parked unowned. They keep their
existing `event_id`.

**Skips** carry `host`, `host_session_id`, `ts_ns`, and the call id when one is
known.

**Observations v2** (`OBSERVATION_VERSION` 2) add:

- `pre_seq` and `post_seq` from the observer journal;
- `journal_id`: a random id created once per shared state dir, in
  `io/journal-id`. Sequence numbers never restart after compaction (comment at
  io_observer.py:629); the id catches a deleted journal.

**Normalizers** (`normalize_event`, `normalize_data_command`, `normalize_skip`)
pass every new field through explicitly. They also pass `step` (item 4) and
keep `filters_detected` where a historical record has it.

**Manifest:**

- top-level `schema: "mycelium.data_lineage.manifest.v2"`; absence means v1;
- written atomically (mkstemp in the same directory, fsync, `os.replace`) by
  `extract_data_lineage.py` and by the unowned data-command flush path, so a
  reader never sees a torn file;
- `_mark_observed_changes` also stamps each output with `observed_sha256`,
  `observed_hash_status` and `observed_change`, so the join happens once, at
  extraction.

**`extract()`** dedupes analysis events by `event_id`, and observations by
(`host`, `host_session_id`, `observation_id`).

### 3b. What the index records

New script: `skills/core/scripts/lineage_index.py`, stdlib only.

**Source set:** the `.living/log/data-lineage/*.json` manifests of one checkout
(v1 and v2, including `data-commands-*`). Cross-worktree aggregation is out of
scope.

**Writes come from observations,** one set per call:

- Every `changes.created` and `changes.modified` entry is a write. Every
  `changes.deleted` entry is a tombstone. `metadata_only` entries are skipped.
- Each write is attributed to the call (`observation_id`) and lists the call's
  events: the analysis events and data command that share that
  `observation_id`. `python a.py && python b.py` is one call with two
  candidate scripts, not two producers.
- `attribution` comes from the observation status:
  - `sole` for `no-known-overlap`;
  - `shared` for `overlap` (the overlapping calls are listed);
  - `uncertain` for `background`.
- Failed calls that changed bytes are writes too, with `tool_outcome` kept.
- The hash is `after.sha256` (protocol hash). If it is null (`pending`,
  `unstable`, `dataless`), the write keeps `sha256: null` and
  `hash_status`. The hook digest is **never** substituted for a null protocol
  hash.
- `rename_candidates` are kept as hints linking the tombstone and the create.
- Writes from a call whose observation was capped (`capped`, `top_level`) carry
  `incomplete: true`.

**Inferred writes:** outputs of calls with no observation (v1 manifests,
`unavailable` or missing observations) become *candidate* writes:

- `evidence: inferred`, the hook digest as `sha256` with
  `hash_origin: hook-post`, and the record's `observed_change` reason when
  present;
- they never make another write `superseded` on their own (see 3c).

**Reads:** each input with a hash, keyed by hash and by path, with a
`hash_origin`:

- `pre-run-proven`;
- `hashed-at-post` (protocol hash taken at Post);
- `hook-post` (hook digest);
- `legacy-enriched` (a v1 record whose hash Stop filled from disk later, of
  unknown timing).

Reads are always `evidence: inferred`, because a hook cannot see reads. The
proof covers the bytes at the path, not the access. Unhashed inputs are kept by
path with their reason.

**Gaps** are kept with timestamps, so a query can say what the record cannot
rule out:

- skips;
- observations with status `unavailable`;
- `observations_missing` and `unresolved_calls`;
- capped diffs;
- unreadable or invalid manifests;
- edit-tool calls (Write, Edit, apply_patch), which the observer journals
  without a snapshot. Their file changes are invisible to Bash observations.

**Ordering:** a write W1 is *before* W2 when they share a `journal_id` and
`W1.post_seq < W2.pre_seq`.

- Overlapping intervals are `concurrent`.
- Writes with no journal order (v1, or different `journal_id`) fall back to
  `ts_ns`, or `ts`, and are marked `order: clock`.
- Equal clock values with different hashes are `order: ambiguous`.
- Manifest order is display only.

**Identity:**

- v2 records use `event_id`.
- v1 data commands keep their existing `event_id`.
- v1 analysis events get a synthetic reference, `v1:<manifest>#<index>`,
  marked `synthetic: true`. It is documented as stable only while that
  manifest file is unchanged.
- Records duplicated across manifests (a data-only manifest and its
  replacement, or an unowned record moved in) are deduped by `event_id` and
  observation key.

**Paths:**

- v2: `rel_path`.
- v1: a lexical relpath against the manifest's recorded `repo_root`, then
  against its realpath. Otherwise the absolute path is kept with
  `frame: unresolved`, and is never joined to a repo-relative path.

### 3c. Storage, freshness and locking

- **Location:** `<shared state dir>/lineage/index-<sha256(realpath(root))[:12]>.json`.
  It is derived and rebuildable, never committed. It is keyed by root, so
  worktrees sharing an absolute `MYCELIUM_STATE_DIR` don't thrash one file.
- **Cache contents:**
  - the builder version;
  - the root realpath;
  - per-manifest contributions, keyed by (name, dev, ino, size, mtime_ns,
    ctime_ns).

  On each query, a metadata scan finds new, changed or removed manifests. Only
  those are re-parsed.
- **No build at Stop.** The index is built only when a query needs it.
- **Locking:** the cache is written under `durable_lock(root, "lineage-index")`
  with a short timeout (2 s). If the lock is busy, or the directory is
  read-only, the query builds in memory and answers without writing. A
  manifest that fails to parse becomes a gap; the query never aborts on it.

### 3d. Queries and statuses

`lineage_index.py query` takes one of `--hash H`, `--path P` or `--event E`,
plus optionally `--json`, `--record-only` and `--max-bytes N`. Results are
grouped by path, because common content such as an empty file can sit at many
unrelated paths.

**Status of a specific write** (path P, hash H, call C). By default it checks
the disk:

- the file's hash comes from the observer's hash cache by identity, or from a
  streaming hash up to `--max-bytes` (default 4 GiB);
- `..` and symlinked components are refused, as in the observer.

Statuses:

- `current`: no later observed write to P, and the disk hash equals H.
- `superseded`: a later observed write to P has a different hash or is a
  tombstone. The result names that write's call and events. If the later
  write's hash is unknown and it is a `modified` entry, the status is
  `superseded-unverified` (it could have been a touch).
- `changed-outside-record`: no later observed write, but the disk hash differs.
  The result includes the gaps after W that could explain it.
- `missing-on-disk`: no later observed write, but the file is gone.
- `unverified`: the disk hash is unavailable (over `--max-bytes`, unreadable,
  or a refused path).

**Restored content** (A → B → A): the first write of A is `superseded`. The
latest write of A is `current`.

**`--record-only`** skips the disk check and reports `current-in-record`,
`superseded`, `no-recorded-write` or `unknown`. It always includes the count
and list of gaps after the write. `current-in-record` is never presented as
`current`.

**Hashes without a write in the record:** a hash with no observed write at any
path is `no-recorded-write` for that hash, plus the disk status of each path it
was read from.

**Large outputs.** A protocol hash `pending` at Post is resolved at query time
when the file's identity still matches the observation's stored `identity`.
The result is labelled `hash_origin: query-identity-match`.

**`--hash H`** returns:

- the producing writes (all of them; a hash can have several producers), each
  with its per-path status;
- the readers grouped by `hash_origin`. Proven readers are listed first, and
  weaker evidence stays labelled.

**`--path P`** returns the write history with ordering labels, the readers of P
by path (so unhashed or large baselines still show), and the current status.

**`--event E`** returns the event's reads and writes with statuses.

The acceptance questions map to these queries:

- "Which events read H / produced H / is H current" is `--hash H`.
- "Two steps read the same baseline" is `--hash <baseline>`, or `--path` for
  baselines that couldn't be hashed.
- "A rerun makes the earlier hash superseded" is `--hash <old>`.

## Item 4: the step's settings

### Captured inside detection

The detector keeps returning its public tuple. Internally, each detection now
carries a `Invocation` record built at match time:

- `stage_offset`;
- the literal `argv` of the matched simple command;
- `executable`;
- `operand_start` (where the script or `-c`/`-e` body begins);
- the invocation cwd and the runner I/O cwd;
- the prefix assignments that were actually parsed.

Runners (snakemake, nextflow, sbatch) keep their argv instead of discarding
it.

**Dedupe:**

- The key is (script, stage_offset), so
  `python a.py --seed 1 && python a.py --seed 2` gives two events. Event counts
  change for repeated invocations. That is accepted and tested.
- Anything unmodeled (`eval`, an expansion that decides the executable, a
  control-flow body) gives `step.partial: true` with a reason. Nothing is
  guessed.

### The `step` object (analysis events; data commands get the I/O stage's argv)

- `argv` (after redaction, below). Tokens over 4 KB are truncated with a
  marker. Inline `-c`/`-e` bodies are replaced by `{"inline_sha256", "length"}`,
  since `script_source` already holds them.
- `executable` and `interpreter` (python, R, snakemake, nextflow, sbatch, …).
- `script_args`: the operands after the script or inline body.
- `env`: prefix assignments and `env VAR=x` assignments of that stage, as
  literal text. `$(...)` and `$VAR` values are stored unexpanded, with
  `expanded: false`. The hook cannot see the agent shell's environment and
  claims nothing about it.
- `config_files`: per-interpreter operand rules, never a global flag list.
  - python / Rscript: `--config`, `--config-file`, `--configfile`,
    `--params`, `--params-file` and `--settings`, in `--flag value` or
    `--flag=value` form, and operands with a `.yaml`, `.yml`, `.toml`, `.ini`,
    `.cfg` or `.conf` suffix. `.json` counts only after one of those flags.
    `-c` is never a config flag for python (it is code).
  - snakemake: `--configfile`/`--configfiles` (files), and `--profile` (a
    directory: its `config.yaml`, `config.v8+.yaml`). `--config` takes KEY=VALUE
    pairs and is recorded verbatim in `step.params`. `-c` is `--cores`.
  - nextflow: `-params-file`, `-c`/`-config`. `-C` replaces the config.
  - Each entry: `{path, rel_path, sha256, size_bytes, via, hash_status}`.
    Missing or unresolvable files are listed with `hash_status: missing` or
    `unresolved`.
  - Hashing uses `_open_regular` (non-blocking, regular files only) with a
    **per-call budget**: at most 8 files, 5 MB each, 250 ms in total. Over
    budget, `hash_status: skipped-budget`. The hash is labelled
    `hash_origin: hook-post`, since it is taken after the call.
- `seeds`: `--seed N`, `--random-state N` and `--random_state N` in argv,
  `set.seed(N)` in R source (inline or file-backed), and the existing Python
  seed literals. The scanner skips strings and comments, using the
  language-aware spans that I/O scanning already uses. Each seed has
  `origin: argv | source`, and only literals are recorded. `seeds_detected`
  stays populated for v1 readers.

### One redaction policy (v2) for every place the command text is stored

It applies before serialization, in the event, the data command, the
observation and the skip. It covers `bash_cmd`, `argv`, `script_args`, `env`,
and the observation's `bash_cmd`.

**What is redacted:**

- assignments whose name matches
  `(?i)(token|secret|passw|pwd|api_?key|private_?key|credential|auth|cookie|session)`;
- the values of flags matching the same pattern, in `--flag value` and
  `--flag=value` form;
- URL userinfo (`scheme://user:pass@`);
- `Authorization:` header values.

**How:** the value is replaced by `<redacted>`, and the structured field stores
`{"redacted": true}`. There is no hash: a hash of a low-entropy secret is
guessable.

**`script_source`:** inline bodies get the same assignment and URL rules.
`script_sha256` stays the hash of the true text, so it still identifies the
code.

**Old records** are left as they were. The redaction is applied going forward.

### `filters_detected`

v2 records stop collecting it. It is still read by field presence on historical
records, including ones re-consolidated into a v2 manifest. data-lineage.md
documents it as removed. Evaluated filters belong to item 7.

## Item 5: hash-linked report values

### One source-resolution and freshness contract

**Repo root resolver** (shared): an explicit `--repo-root` wins. Otherwise use
the nearest ancestor of the fragment or manifest that has `.living/`, then the
git toplevel. `register_value` (vendored, stdlib only) uses the first two.

**Registration:**

```python
register_value(key, value, *, namespace=None, provenance=None, source=None)
```

- `source` is a path, or `{"path": …, "locator": …}`.
- A relative path resolves against the process cwd, as `open()` does. It is
  stored repo-relative. `computed_at` stays relative to the analysis root, as
  today.
- A `locator` is required with a source: a non-empty string or a dict with any
  of `row`, `column`, `key`, `sheet`, `cell`, `query`. It is recorded as
  declared and not evaluated; the docs say so.
- The file is hashed at registration: streaming, no size cap (this runs in
  the analysis, not a hook), with a per-process cache keyed by (realpath,
  size, mtime_ns). A missing file raises `ValueRegistrationError` with
  "register after the file is written and closed". The docs and tests show
  that order.

  Stored entry:

  ```json
  "source": {"path": "analysis/qc/outputs/tables/cell_qc.csv", "root": "repo",
             "sha256": "…", "size_bytes": 1234,
             "locator": {"row": "passing", "column": "count"}, "hashed_at": "…Z"}
  ```

- `provenance` defaults to `"<path>:<locator>"` when a source is given.
- **Fragment writes:** read-modify-replace under a sidecar `numbers.json.lock`.
  It uses `fcntl.flock`, with no lock where `fcntl` is unavailable, so the
  helper stays vendorable. The temp file is unique (mkstemp in the same
  directory), and the file mode is preserved.
- **The in-process collision cache** is updated only after the write succeeds.
  Re-registering the same value with a changed source updates the entry. A
  different value still raises.

**Phase 1** copies `source` unedited with the other mechanical fields. It never
recomputes `source.sha256`: refreshing a hash would bless a stale number. The
conventions say so, and a test checks the merge helper text and the example.

### The checker

New `skills/core/scripts/check_report_values.py`, an importable module plus a
CLI:

- `check_numbers(entries, repo_root) -> list[Finding]`, where `entries` accepts
  fragment `key` or manifest `id`.
- CLI: `--manifest <path>` or `--fragment <path>`, `--repo-root`, `--json`,
  `--strict`.

| Finding | Severity | Meaning |
|---|---|---|
| `stale-value` | error | the source hash differs from the file now |
| `source-missing` | error | the source file is gone. Names a rename target if the lineage index has one |
| `source-invalid` | error | malformed path (absolute, `..`, symlinked component), bad hash, or missing locator |
| `source-unreadable` | error | the file exists but cannot be hashed |
| `unchecked-value` | warning (error with `--strict`) | no `source` (legacy, or never linked) |

**The fix is to recompute and re-register.** Every `stale-value` message says
so: rerun the analysis that registers the number, then redo the Phase 1 merge.
Refreshing the hash alone is never the fix.

**Exit codes:** 0 clean, 1 on any error (or warnings with `--strict`), 2 on bad
input. These match `check_html_report.py`.

**`--explain`** optionally names the event that wrote the current content. It
uses `lineage_index.py` in read-only, in-memory mode, and never writes
`.mycelium` or takes the index lock.

**`sync_html_report.py`:**

- `plan(source, manifest, base, repo_root=None)`. `--root` keeps its meaning
  (the report asset base), and a new `--repo-root` supplies the repo frame for
  number sources.
- Number findings at error level block the sync, like a stale figure. The
  report bytes stay unchanged. `unchecked-value` is printed as a warning.
- It never calls `--explain`.

**`check_html_report.py --manifest M`** is allowed only with a single report
path. It adds `[stale-value]`, `[source-missing]`, `[source-invalid]` and
`[source-unreadable]` errors and `[unchecked-value]` warnings. The HTML and TeX
gates in the conventions are updated to pass it.

**Compatibility:**

- scitexlintr ignores extra number fields. Codex confirmed this against the
  local 0.2.1 checkout; a shipped test asserts parse equality.
- render_report_values_tex ignores them too.
- `manifest-example.json` moves to `_schema_version` 0.5.0 with a worked
  source.
- The docs say how projects refresh the vendored `register_value.py` and the
  installed report-generator pack.

## Item 6: a standard sample ledger

### The ledger spec lives in its own JSON file

`data/metadata/<name>/samples.ledger.json` (stdlib JSON, no YAML dependency).
`schema.yaml` gains a single line, `sample_ledger: samples.ledger.json`.
`skills/core/templates/samples.ledger.json` and `samples.tsv` are the
templates:

```json
{
  "schema": "mycelium.sample_ledger.v1",
  "file": "samples.tsv",
  "sample_id": "sample_id",
  "group": "condition",
  "unit_of_replication": "derivation",
  "nesting": ["line", "derivation", "prep", "dish", "library"],
  "local_labels": false,
  "batch": ["prep_date", "sequencing_run"],
  "pairing": null
}
```

The fields:

- `file` is resolved relative to the ledger spec. Each ledger row is one
  sample.
- `nesting` lists replication levels from coarse to fine. Each must be a
  column, and `unit_of_replication` must be one of them.
- With `local_labels: false`, a finer label must be unique across the study:
  "dish 3" under two preps is an error. With `true`, labels are local to their
  parent, and the checker composes `parent/label` ids before checking.
- `batch` lists **crossed** nuisance variables (a calendar date, a sequencing
  run). A batch column may not be a nesting level at or below
  `unit_of_replication`. That is a spec error (exit 1), because such a column
  is confounded with the group by construction. Nested preparation instances
  belong in `nesting`; calendar batches belong in `batch`.
- `pairing`, when declared, is `{unit, within}`. The generated unit check then
  sets `units_may_span_groups: true`.

### Wiring to `data_contract_check.py`

`--ledger <spec.json>` and `--contract <contract.json>` become a mutually
exclusive required group. Ledger mode builds a contract in memory, with the
ledger table, and runs:

- `unique` (new kind): `columns: [sample_id]`. It blocks on duplicate or
  missing values.
- `nesting` (new kind): `levels: [...]`, `local_labels`. For each adjacent
  (coarse, fine) pair, every fine id maps to exactly one coarse value.
  Otherwise it blocks, with up to 5 evidence rows. Missing values follow the
  existing drop-with-warning semantics, and `MissingKeyMatrixTest` gains
  `nesting` and `unique`.
- `unit_of_replication` with a new key, `rows: "samples"`. The check runs on
  the distinct (unit, group) projection: minimum units per group, and units
  spanning groups unless allowed. It drops the one-row-per-cell repeats check.
  Without `rows` the check behaves exactly as today, which keeps model-table
  semantics unchanged.
- `batch_confounding`: one per batch column, with the group as contrast. This
  is the existing check, now reachable from ingest.
- `pairing`, when declared.

`--emit-contract <path>` writes the generated contract with a `note` telling
analyze step 6 to point the replication check at the modeled table (dropping
`rows: "samples"` there) and add its filters. With `--root`, the contract
replays from any cwd.

The new kinds go into `KIND_KEYS`, `CHECKS`, the guide's first example (which
`DocumentationContractTest` requires to cover exactly the shipped kinds), the
analysis-decisions table, and data-contract-check.md. Older checkers reject
contracts that use them (exit 1); the guide says so.

### Ingest and manifest

- **ingest SKILL.md**, after step 6: when the data has samples, write
  `samples.tsv` and `samples.ledger.json`, then run
  `data_contract_check.py --ledger …`. A block at ingest is fixed in the
  ledger or the data, never by loosening the spec. data-ingest-conventions.md
  gets the same step and checklist line.
- **`dataset-manifest-entry.yaml`** gains `sample_ledger:`. Analyze step 6 and
  data-contract-check.md name the ledger first when locating the sample-level
  table.

## The schema file Warrant reads

`skills/core/references/provenance-schema.json`, JSON Schema 2020-12.

**Stable entry points:**

- `#/$defs/manifest_v1` and `manifest_v2`;
- `analysis_event`, `data_command`, `skip`, `observation`;
- `file_record`, `step`;
- `lineage_index`, `index_query_result`;
- `report_fragment`, `report_number`, `value_source`, `report_finding`;
- `sample_ledger`.

**Tests:**

- validate real outputs produced by the test suite: a v2 manifest, an index, a
  query result, a fragment, a manifest number entry and a ledger;
- validate committed v1 fixtures;
- reject malformed fixtures (an empty record, a bad hash, a wrong type).

`jsonschema` becomes a test dependency in both CI jobs, and a missing
validator fails CI, not skips. Locally the tests skip with a message when it is
not installed.

## Measurement and audits

**Hook overhead,** on a scratch repo with multi-gigabyte data (several 1–4 GB
files under `data/`), against 0.9.7 on the same tree. Median and p90 over 20
runs each:

- end-to-end per Bash call (Pre plus Post), trivial command;
- a script reading a 2 GB input;
- the worst-case config call (8 config files);
- query time for the index, cold and warm, on a project with about 2,000
  recorded commands. A synthetic manifest set is used if no real one is
  available.

**Lifecycle audits** (Claude Code and Codex) with the 0.9.7 probes, plus:

- a rerun that writes **different bytes** to the same output, checking the
  index reports the old hash `superseded` with the superseding call;
- a number registered against the old output is flagged `stale-value` by
  `check_report_values`, `check_html_report --manifest` and
  `sync_html_report`, and the report bytes stay unchanged on the failed sync;
- two scripts reading one baseline: exactly two readers with
  `pre-run-proven` evidence, including after a cache rebuild;
- a command with `VAR=x`, a secret-named variable (redacted everywhere), a
  `--config` file and `--seed`.

**Known limits, stated in the audit report:**

- Inline heredoc reads remain inferred, and can be empty when the body's paths
  are not static.
- The pure-shell I/O recognizer needs exit 0, which Codex's Bash PostToolUse
  does not supply.
- Edit-tool writes are gaps, not observed writes.

## Out of scope here

- Observed reads (item 7).
- Evaluating filters.
- The Warrant adapter.
- Cross-worktree index aggregation.
- The 95% transcript-replay criterion belongs to item 1. It is re-run at the
  end only if the AP1-learning and SNP-tree transcripts are on this machine,
  and the result is reported either way.

## Build order

One branch (`feat/provenance-index`) and one PR. Each item gets tests and a
review round before the next:

1. 3a (records, frame, atomic manifests, observation v2);
2. 3b–3d (index and queries);
3. item 4;
4. item 5;
5. item 6;
6. the schema file and docs;
7. measurement and audits.

## Measured results (2026-11-22)

**Hook overhead per Bash call.** Measured end to end with PreToolUse
observer plus PostToolUse tracker, driving the real hook scripts. The repo
had 5 GB of data (two 2 GB files and one 1 GB file) plus 2,000 small sample
files, on a loaded machine (load average about 7). Times are the median and
p90 of 20 runs:

| Case | 0.9.7 | this branch |
|---|---|---|
| trivial (`ls data`) | 527 / 561 ms | 501 / 550 ms |
| script reading a 2 GB input | 579 / 626 ms | 537 / 577 ms |
| worst-case config call (8 configs) | 577 / 615 ms | 546 / 585 ms |

There is no measurable regression; the differences are within noise.
Neither tree hashes the 2 GB input on the hook path: the observer's 2 s
budget leaves it `pending`, and the index resolves it later by identity. All
8 configs were hashed and proven. Both trees recorded every call (66
observations, 44 events).

**Index query time.** A synthetic project with 2,000 events in 40 v2
manifests, plus 40 observation stores:

| Query | Time |
|---|---|
| cold build plus `--hash` query | 179 ms |
| warm query | 55 ms |
| after one new manifest (incremental) | 141 ms |
| `--path` warm (80-write history) | 57 ms |

**Live Claude Code audit** (Claude Code 2.1.x, sonnet, candidate hooks via the
worktree's `init_repo.py`):

- Manifest: the v2 manifest has identity on every action.
- Step settings: argv, `MODE=fast`, `HF_TOKEN` stored as `{"redacted": true}`,
  `--seed 7`, and `--config` proven `pre-run-proven`.
- Secrets: the secret string is absent from the manifest and the observation
  stores.
- Supersession: a rerun writing different bytes left the first output
  `superseded` by the rerun's call.
- Readers: three proven readers of the baseline.
- Edit-tool writes: observed and `current`.
- Report values: `check_report_values --explain` flagged the number
  registered against the first output as `stale-value` and named the rerun
  event.
- Lifecycle: the Stop block, retry and handoff were unchanged.

**Not yet run:**

- the Codex live audit, which needs the hooks approved in the desktop app
  for a candidate install;
- the 95% transcript replay (item 1's criterion).
