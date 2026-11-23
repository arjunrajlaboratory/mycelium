# Item 7: row and feature access

Status: built, 2026-11-22, with these decisions (agreed with the user):

1. **One vendored file.** `record_ids` lives in `register_value.py`, next to
   `register_value`. Projects copy one file and update one file.
2. **An R version.** `skills/core/scripts/record_access.R` calls the Python
   helper's command line (`register_value.py record-ids …`), so both languages
   write identical records from one implementation. It is tested live with
   Rscript.
3. **Explicit calls only.** No loader patching: what matters is what survives a
   filter, which only the analysis knows. Patching pandas or AnnData would be
   fragile and would record loads rather than uses.
4. **No sketches yet.** Records store `count`, `distinct`, `id_set_sha256`, and
   optionally `order_sha256`, never ids. Overlap-estimating sketches wait until
   a concrete check needs them.
5. **Recommended, not required.** The analyze skill and the report-values guide
   recommend `record_ids` at filter, subset and split steps.

The Stop-time note for a table-writing step with no access record was built
on 2026-11-23 (`skills/core/scripts/access_note.py`):

- **What counts as a table step.** A manifest action with a script file
  that did not fail, and an output the observer saw created or modified
  whose name ends in .csv, .tsv (optionally .gz), .parquet or .h5ad, outside
  `.living/`, `.mycelium/` and `.git/`. Inline runs cannot be joined, and an
  unobserved output is no evidence of a write, so neither counts. Only the
  last run of each script is judged, because `record_ids` replaces a record
  of the same name. A run is named only when the observer saw its call
  alone (`no-known-overlap`), and an output several scripts of one call
  declare is attributed to none of them: the observation marks it changed
  on all, though only one may have written it.
- **The join.** A record covers that run when its process started inside
  the run's window: no later than the run ended (two seconds' slack) and no
  earlier than the previous recorded call ended, or, with none, the session
  started. Its file and call site do not matter, so a helper module, R
  recording into another namespace and `computed_at: "R"` all count, while
  an earlier run's record, or an earlier script's, does not.
- **Errs toward silence.** Files without the `mycelium.access.v1` marker are
  skipped. No symlink is followed on the way to an access file (lstat
  only), nothing outside the repository is read, and each file is opened
  once (`O_NOFOLLOW`, `O_NONBLOCK`) and read with a bound; an unreadable or
  linked file in the step's own analysis makes that step unknown. More than
  100 existing access files or 2000 analysis directories is an incomplete
  scan, and past 4 MB per file, 16 MB in all or 1.5 s, every step is
  unknown; a 3 s alarm ends the helper with no output. Unknown never
  produces a note.
- **Shown once, on acceptance only.** The lineage Stop phase runs the helper
  (only when its manifest names a table and an observed write) into
  `access-note.tmp`. Stop shows the note only when it accepts, and only in
  `systemMessage`, never in a block reason. On Claude, `additionalContext`
  on an accepted Stop makes Claude take another turn, so the note never goes
  there and what Claude receives is unchanged; on Codex `systemMessage` is
  the Stop channel and does not continue the turn. After the transaction is
  archived, Stop has the helper record each script the note names, with
  its content hash, in `access-note-seen.tsv` (regular file only, through
  an exclusively created temporary file), so the same script at the same
  content is not named again. The helper caps the note at 1500 bytes. The
  phase that writes the note cannot fail, and any helper failure prints
  nothing.
- **Not gated on detected filters.** `filters_detected` is no longer
  written (item 4), so the note cannot tell a filter from a summary. The
  once-per-script-content rule keeps that from repeating.

Records go to `analysis/<name>/outputs/access.json` (schema `access_fragment`
in `skills/core/references/provenance-schema.json`). Each record's `run`
(pid, process start) and `computed_at` (script and line) join it to the
lineage event of the same script and time.

The original proposal follows.

## The gap

Hooks see processes and files, not what a process did inside a file. Items 1–6
can say "fit.py read `counts.h5ad` (hash H) and wrote `model.pkl`". They cannot
say which cells, samples or genes it used. Yet that is the question behind most
provenance failures in analysis:

- a filter that silently dropped a group;
- a test set that overlapped training;
- a figure drawn from a subset that differs from the one the statistic used;
- a feature list that changed between runs.

The same gap is why `filters_detected` could only ever be a regex guess. It was
dropped in item 4.

## Proposal

A small, dependency-free helper module, vendored next to `register_value.py`.
An analysis calls it to declare, or lets it record, the row ids and feature ids
that a step read and wrote.

### API sketch

```python
from record_access import record_ids, recording

# Explicit: declare the id sets a step used.
record_ids("cells_after_qc", ids=adata.obs_names, axis="row",
           role="read", source="data/processed/counts.h5ad")
record_ids("genes_tested", ids=adata.var_names[mask], axis="feature",
           role="used")

# Wrapper: record what passes through a loader.
with recording(source="data/processed/counts.h5ad") as rec:
    adata = sc.read_h5ad(path)
    rec.rows(adata.obs_names)
    rec.features(adata.var_names)
```

### What is stored (never the ids themselves)

For each declaration, one entry in `analysis/<ns>/outputs/access.json`, written
the same way as `numbers.json`:

- the name, `axis` (row or feature), and `role` (read, used, written, excluded);
- `count`;
- `id_set_sha256`: the sha256 of the sorted, newline-joined, UTF-8,
  NFC-normalized ids;
- `order_sha256` (optional): the hash in the order given, for steps where order
  matters;
- `source`: `{path, sha256}` in the same frame as item 5;
- `computed_at`, as `register_value` records it;
- `run_id` (optional): an id for the process. This joins the entry to the
  lineage event whose observation saw that process.

Comparing two id sets needs only their hashes and counts, so two steps that
claim to use "the same cells" can be checked without storing any cell
barcodes.

For a diff, an opt-in `sketch` field stores a MinHash or Bloom summary. It
estimates overlap between two sets without revealing their members. It is off
by default and added only if a Warrant check needs it.

### Joining to lineage

The joining key is the process. The helper records `os.getpid()`, its start
time and the script path. The lineage event knows the command and the script,
and the observation knows the call interval. Matching script plus interval is
reliable enough for a report. An exact join would need the hook to pass an id
into the process, for example `MYCELIUM_EVENT_ID` in the environment. Hooks
cannot set the environment of a command that has already started, so that
needs either:

- a PreToolUse rewrite of the command (rejected: hooks must never change what
  runs);
- or the helper reading a per-session "current call" marker that PreToolUse
  writes. That marker already exists as the observer's open-call marker.
  Concurrency makes the match ambiguous when two calls overlap; the overlap is
  recorded either way.

**Recommendation:** match by script plus interval, mark ambiguous matches as
such, and add the env-var path later only if needed.

### Checks this enables (later, in Warrant or `check_report_values`)

- A number's `source` plus the id set it was computed over. Two numbers that
  claim the same population but carry different `id_set_sha256` are flagged.
- A train/test split declared as two `role="used"` sets. A non-empty overlap
  is shown by a sketch, or exactly by an opt-in local check that never writes
  the ids.
- An `excluded` set per filter step makes "what did QC drop, and how many per
  group" a recorded fact instead of a regex guess.

## Questions to settle before building

1. **Vendored helper or importable package.** `register_value.py` is copied
   into projects; this module would follow the same model. Is a third vendored
   file acceptable, or should both move into one `mycelium_report` module?
2. **Languages.** Should there be an R twin (`record_ids()` writing the same
   JSON)? Most of the lab's analyses in the probe were Python and R. I would
   ship both, with a shared format test.
3. **Default granularity.** Should `recording()` hash automatically on load
   when given a known loader (AnnData, pandas)? Or keep it explicit only? I
   recommend explicit only for the first version, because implicit loader
   patching is fragile.
4. **Sketches.** Should overlap-estimating sketches be in v1 at all? I propose
   leaving them out until a concrete check needs one.
5. **Enforcement.** Should the analyze skill require declarations for every
   filter step, or only recommend them? I propose recommending them, plus a
   Stop-time note when a step that wrote a table recorded no access entries. (Built 2026-11-23; see the top of this file.)

## Cost

There is no hook-path cost: the work is in-process, in the analysis. Hashing a
million ids is about one second in Python. That is negligible next to loading
the data they came from.
