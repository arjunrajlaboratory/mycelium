# Declared workflows: offering Snakemake or DVC at setup

Status: 2026-11-27. Phase 1 is implemented on this branch, except the two
items marked "deferred" in its table; phases 2 and 3 are proposals. The statements
about what each tool records were checked by running a two-step example
(plus one 3 MB input) in Snakemake 9.27.2 and DVC 3.67.1 on macOS; see
"What the trial showed". Cluster runs, Windows, and R were not tried.

## Summary

Mycelium learns what an analysis read and wrote by inferring it from the
command and the script. The mature tools do not infer: the step's inputs
and outputs are declared, and the tool runs the step. This proposal lets a
repository choose a declared workflow tool at setup, tells the agent how to
use it, and brings the tool's record into the provenance schema that
Warrant reads. A repository that chooses none keeps today's recorder (later
the plain splitter in `lineage-plain-recorder.md`).

Three phases:

1. **Offer and guidance.** Small: one script modelled on
   `setup_linters.py`, one reference guide, a few lines in three skills.
2. **Declared steps in the provenance record.** Moderate: a reader for each
   tool's record, two additive schema changes, a third source for the index.
3. **Checks and clean-up.** Optional, later.

## Why

- **The agent already knows the inputs and outputs.** It wrote the script.
  Declaring them costs it a few lines.
- **Projects already repeat steps.** In a replay of Codex sessions from ten
  projects, 623 distinct scripts account for 1,728 script runs.
- **Inference comes up empty.** The current detector finds an input for
  about a third of script runs. For a `snakemake` call it records the
  Snakefile as the script and cannot see the steps inside.
- **Warrant needs the record.** A declared step states its inputs and
  outputs outright, which is a stronger basis for a check than an
  inferred candidate.
- **The declaration is useful on day one.** The tool reruns only what is
  stale. One existing Mycelium project already has a small Snakefile.

## Phase 1: offer and guidance

### The setup question

Asked once per repository, during `/mycelium:core` initialisation, next to
the linter question (step 4a):

> How should this repository track analysis steps?
> - **Snakemake**: rules with declared inputs and outputs. Standard in
>   genomics; handles cluster runs.
> - **DVC**: stages with declared dependencies and outputs, recorded with
>   content hashes in a committed lock file. Lighter; tracking only.
> - **Neither**: Mycelium records what it can infer.

If the repository already has a `Snakefile` or `dvc.yaml`, propose that
tool. Otherwise present the three without pressing; "neither" is a normal
answer for a small or exploratory project.

The answer is stored in `.living/workflow.json` (committed, so
collaborators share it) by a new `setup_workflow.py` with the same
interface as `setup_linters.py`: `status` exits 2 when undecided, and
`choose snakemake|dvc|none` records the answer. Every error exits 3, so it
cannot be mistaken for "undecided". A repository set up before this
existed is asked the first time the `analyze` skill runs. Installing the
tool is left to the agent and recorded in `ENVIRONMENTS_INSTALLATIONS.md`.

### What the agent does differently

One rule, stated in the `analyze` skill and detailed in a new
`skills/core/references/workflow-guide.md`:

> When a workflow tool is chosen, every kept result comes from a declared
> step, and the step is run through the tool.

- **Kept result**: a file under `analysis/<name>/outputs/` or
  `data/processed/` that a report, a registered value, or a later step
  uses. Looking at a file, a quick one-liner, and scratch work are exempt.
- **Declare every input**: data files, the script itself, and config
  files. Declaring the script makes a code change mark the step stale.
- **Declare every output**, including `outputs/numbers.json` from
  `register_value` and `outputs/access.json`. Only one step per analysis
  may write each of those two files (see the guide).
- **Run through the tool**: `snakemake <target>` or `dvc repro <stage>`,
  not `python script.py`.
- **Layout**: one workflow file per analysis
  (`analysis/<name>/workflow.smk` included from a root `Snakefile`, or
  `analysis/<name>/dvc.yaml`). Exact layout to be confirmed against real
  runs in the guide.
- **DVC outputs are declared with `cache: false`** unless the user wants
  DVC to version the data. In the trial this left outputs as plain files
  and added no `.gitignore` entries.
- **DVC stages set `wdir` to the repository root**, so paths read the same
  as everywhere else in Mycelium, and are run with
  `dvc repro analysis/<name>/dvc.yaml` or `dvc repro -P`. A bare
  `dvc repro` fails when there is no root `dvc.yaml`.

### Files touched

| File | Change |
| --- | --- |
| `skills/core/scripts/setup_workflow.py` (new) | `status` / `choose`, writes `.living/workflow.json` |
| `skills/core/scripts/test_setup_workflow.py` (new) | mirrors `test_setup_linters.py` |
| `skills/core/references/workflow-guide.md` (new) | the rule above, one worked example per tool, what not to declare |
| `skills/core/SKILL.md` | init step 4b: the question |
| `skills/core/scripts/init_repo.py` | one more "next steps" line. (`init_repo.py` writes no root `.gitignore`, so the guide tells the agent to add `.snakemake/`.) |
| `skills/core/references/antigravity-init.md` | the same question for that host |
| `skills/analyze/SKILL.md` | check `setup_workflow.py status` once per session; the rule, pointing to the guide |
| `skills/report/SKILL.md` (deferred) | figures and tables a report uses come from declared steps when a tool is chosen |
| `skills/core/scripts/io_observer.py` | add `.dvc` to `EXCLUDED_DIR_NAMES` (`.snakemake` is already there), so a DVC cache is not walked |
| `skills/core/scripts/extract_data_lineage_event.py` (deferred) | recognise `dvc repro` as a run, as `snakemake` already is |
| `README.md`, `CHANGELOG.md` | one short section and an entry |

To check during implementation: that `validate_structure.py` accepts a root
`Snakefile`, `dvc.yaml`, `dvc.lock`, `.dvc/` and `.dvcignore`; that session
accounting at Stop does not count `.snakemake/` or `.dvc/` contents as
session work or spend its fingerprint budget on them; and which
doc-contract tests pin the skill text being edited.

Phase 1 changes no hook and no recorded field. It is safe to ship alone,
and is the "small additions to existing skills" part.

## Phase 2: declared steps in the provenance record

Warrant validates against `skills/core/references/provenance-schema.json`.
It should keep reading one schema whatever produced the record, so the
tool's record is translated into Mycelium's, not handed to Warrant as is.

### Where each tool keeps its record

- **DVC** writes a `dvc.lock` beside each `dvc.yaml`: for each stage, the
  command and every dependency and output with an md5 and a size,
  whatever the file's size. It is meant to be committed.
- **Snakemake** writes one JSON file per output under
  `.snakemake/metadata/` (file name is the base64 of the output path):
  rule, inputs, the shell command, start and end times, and sha256
  checksums of inputs. A 3 MB input got no checksum, and outputs are never
  hashed. The folder is not committed, and the format carries its own
  version number (`record_format_version: 6`). `snakemake
  --detailed-summary` prints the same edges as a table.

### What the trial showed

- **Both tools did the job.** Each ran the two steps, skipped them when
  nothing had changed, and reran exactly the affected steps after an edit
  to a script or to the raw data.
- **One step record from both is practical.** A 50-line prototype read
  `dvc.lock` and the Snakemake metadata into the same shape, hashing each
  file once for md5 and sha256. Where the tool's own hash matched, the
  entry is marked as the content the step used; an input edited after the
  run came out as `changed-since-run`.
- **Snakemake's `onsuccess:` handler ran** after a successful run, so an
  exporter can be triggered from the Snakefile.
- **Today's detector sees a Snakemake run as one event** with the
  Snakefile as its script and no inputs or outputs, and opens a
  bookkeeping cycle for it.
- **Today's detector does not see a DVC run at all**: `dvc repro` leaves
  no event and opens no bookkeeping cycle (a `run.sh` that calls it is
  seen as a wrapper run). Recognising `dvc repro` directly is deferred;
  until then the guide tells the agent to do the bookkeeping unprompted.
- **`dvc init` stages its own files in git** (`.dvc/config`,
  `.dvc/.gitignore`, `.dvcignore`). Snakemake does not ignore
  `.snakemake/` for you.
- **`dvc.lock` folds long commands across lines.** A line-by-line reader
  would break; it needs a real YAML parser.
- **The two tools together are a 230 MB install.**

### Design

1. **A step record.** One small committed JSON file per analysis,
   `analysis/<name>/outputs/steps.json`, listing each declared step that
   has run: tool, step name, command, and inputs and outputs with sha256
   and size. For DVC it is derived from `dvc.lock` (hash each listed file
   once, computing md5 and sha256 together; where the md5 matches the lock,
   the sha256 is the content the step used). Lock paths are relative to
   the stage's `wdir`, which only `dvc.yaml` records, so the reader needs
   both files. For Snakemake it is written
   by an exporter at the end of a run.
2. **The exporter runs off the hook path.** `export_declared_steps.py` is
   run by the workflow itself (an `onsuccess:` handler in the Snakefile
   template; a final DVC stage or a plain call after `dvc repro`), so it
   does not depend on the agent remembering and adds nothing to
   PostToolUse or Stop.
3. **The index reads step records as a third source**, beside manifests
   and observation stores. A declared input becomes a read with
   `source: "declared"`; a declared output becomes a write that the
   observer's change list for the same call can confirm.
4. **Two additive schema changes**: `declared` joins the `source` values
   of a file record (today `inferred-static`, `inferred-repository`,
   `inferred-shell`), and an event or index entry may carry
   `workflow: {tool, step, file}`. No existing field changes meaning.
5. **The lifecycle is untouched.** A `snakemake` or `dvc repro` call is
   still one Bash call that the observer brackets and the reminder logic
   sees as today.

Roughly: the exporter and the `dvc.lock` reader (300 to 400 lines with
tests), the index source (100 to 200), the schema and its documentation.

## Phase 3: checks and clean-up

- **Advisory at Stop**, warn-only like the access note: a kept output
  changed in this session and no declared step lists it.
- **Staleness for reports**: `check_report_values.py --explain` can say
  which declared step produced a stale source and what to rerun.
- **Remove the runner models** (`snakemake` profiles, `sbatch` headers)
  from the detector, as `lineage-plain-recorder.md` already proposes. With
  step records they have no job left.

## Fallback

A repository that answers "neither", or work that is not a declared step,
is recorded as today. The plain splitter replaces the shell interpretation
there on its own schedule. Its inputs carry an `inferred-*` source, so a
reader can always tell a declared edge from an inferred one.

## Open questions

1. **What does Warrant read?** If it reads index query results, design
   point 3 is enough. If it reads committed manifests directly, step
   records must also be folded into manifests, or Warrant must read
   `steps.json`.
2. **Should the offer recommend a tool?** Proposed: neutral, except when a
   workflow file already exists.
3. **Which tool for whom.** Snakemake fits the field and cluster work. DVC
   fits provenance better: its lock file is committed and already carries
   content hashes, so it needs no exporter.
4. **Cluster jobs.** Outputs that appear after the Bash call returns are
   invisible to the observer. A step record still covers them, which is a
   reason to prefer declaration for cluster work, but the exporter must run
   when the jobs finish.
5. **Reading YAML.** `dvc.lock` needs a real YAML parser and Mycelium's
   scripts avoid third-party imports. The simplest answer is to run the
   exporter with the project's own Python, where DVC has already installed
   one.
6. **Small-input checksums in Snakemake.** For a large input the exporter
   can only hash the file as it is at export time, so it cannot tell
   whether the file changed between the run and the export. Running the
   exporter from `onsuccess:` keeps that gap short.
7. **Declared is not verified.** A script can read a file its rule does
   not list. Neither tool checks reads by default; neither does this.

## Suggested order

1. Done: both tools run on a two-step example (see "What the trial
   showed"). Still to try: an R step, a cluster run, and a real project.
2. Phase 1. Done on Claude Code with a live setup and one DVC analysis;
   still to do: a live Codex run, a live Snakemake analysis, and making
   `dvc repro` count as an analysis run.
3. Answer open question 1 with whoever builds Warrant.
4. Phase 2 for DVC first (no exporter needed), then Snakemake.
5. Phase 3 as needed.
