# Declared Workflows (Snakemake or DVC)

Each repository records once how it tracks analysis steps, in
`.living/workflow.json`. `python3 skills/core/scripts/setup_workflow.py status`
exits 2 while undecided: ask the user, and record the answer with
`setup_workflow.py choose snakemake`, `choose dvc`, or `choose none`. If the
status output says the repository already has files for one tool, propose
that tool. `none` is a normal answer for a small or exploratory project.
Exit 0 means the repository has chosen: do not ask again unless the user
wants to change it. Run the script from the repository root, or pass
`--target-dir <repository root>` before `status` or `choose`. Commit
`.living/workflow.json` after recording, so collaborators are not asked
again.

With `none`, skip the rest of this guide.

## The rule

When a workflow tool is chosen, **every kept result comes from a declared
step, and the step is run through the tool.**

- **A kept result** is a file under `analysis/<name>/outputs/` or
  `data/processed/` that a report, a registered value, or a later step uses.
- **Exempt:** looking at a file, a quick one-liner, scratch work, and
  anything in a temporary directory. Do not write a step for these.
- **Declare every input:** the data files, the script itself, and any
  config file. Declaring the script makes a code change mark the step stale.
  That includes helpers the script imports: keep the copy of
  `register_value.py` in `analysis/_lib/` and list it as an input. A step
  that loads it from the plugin folder fails on a fresh clone.
- **Declare every output.** Each output file has exactly one step that
  writes it.
- **One step per analysis writes `outputs/numbers.json`.** Both tools refuse
  two steps that declare the same output, and both delete a step's declared
  outputs before running it, so a second step that appends to the file
  would silently lose the first step's values. Put every `register_value`
  call of an analysis in one step and declare `numbers.json` as that step's
  output. In a one-step analysis that is the step itself. With several
  steps, add a final `register` step that reads the earlier outputs.
- **The same holds for `outputs/access.json`**, which may have a different
  writer from `numbers.json`. `record_ids` needs the ids a filter kept and
  dropped, which a later step cannot work out from the filtered table, so
  the step that filters calls it and declares `access.json`. When filters
  live in more than one step, each may write a kept/dropped table that a
  final `register` step reads, provided the ids are ones the committed
  output tables already show, such as gene names. If the ids are sensitive
  (patient or sample identifiers, cell barcodes), ask the user how to pass
  them along: `access.json` exists so that only counts and fingerprints
  are kept.
- **Converting an analysis that already has outputs.** Check first that
  the steps recreate everything in the existing files (above all
  `numbers.json` and `access.json`) and that the current copies are
  committed or saved; otherwise ask the user. DVC deletes a declared
  output before the first run. Snakemake does the opposite and keeps
  existing outputs that are newer than their inputs without running the
  rule, so force the first run (`snakemake --cores 1 --forceall <targets>`)
  and check the outputs were rewritten. In `numbers.json` and
  `access.json`, expect only timestamps and run details to differ.
- **Ignore the helper's lock files.** `register_value` leaves
  `numbers.json.lock` and `access.json.lock` beside the outputs. Add
  `analysis/*/outputs/*.lock` to `.gitignore`; do not declare or commit
  them.
- **Scripts create the folder they write to**, for example
  `Path(out).parent.mkdir(parents=True, exist_ok=True)`. Git does not keep
  an empty `outputs/` folder, so a step that relies on one fails on a fresh
  clone under DVC.
- **Run through the tool**, not `python script.py`. The tool reruns only
  what is out of date, so rerunning everything is cheap.
- **Workflow files use paths relative to the repository root, and the tool
  is run from the root.** Pass paths to scripts as arguments, as the
  examples do, so a script does not depend on the directory it runs in.
  This differs from the analysis-directory paths in
  `analysis-conventions.md`; for an existing analysis whose scripts assume
  that directory, ask the user before converting it.
- **`run.sh` calls the tool from the root.** Replace
  `cd "$(dirname "$0")"` with `cd "$(dirname "$0")/../.."`, then run the
  command given below for the chosen tool.

A declaration is a claim, not a check: neither tool notices a script that
reads a file its step does not list. List what the script opens.

## Snakemake

Install it in the project environment and record it in
`ENVIRONMENTS_INSTALLATIONS.md`. Add `.snakemake/` to the repository's
`.gitignore`; Snakemake does not.

One rule file per analysis, included from the repository's main Snakefile.
If one already exists (at the root or as `workflow/Snakefile`), add the
`include:` line and the targets to it, keeping its name and place; a second
Snakefile at the root would hide the existing one. An `include:` path is
relative to the file that contains it, so from `workflow/Snakefile` write
`../analysis/<name>/workflow.smk`. Otherwise create a root `Snakefile`:

```python
# Snakefile
include: "analysis/expression-fit/workflow.smk"

rule all:
    default_target: True
    input: "analysis/expression-fit/outputs/summary.json"
```

```python
# analysis/expression-fit/workflow.smk
rule expression_fit_prepare:
    input:
        data="data/raw/counts.csv",
        script="analysis/expression-fit/scripts/prepare.py",
    output: "analysis/expression-fit/outputs/clean.csv"
    shell: "python {input.script} {input.data} {output}"

rule expression_fit_fit:
    input:
        data="analysis/expression-fit/outputs/clean.csv",
        script="analysis/expression-fit/scripts/fit.py",
    output: "analysis/expression-fit/outputs/summary.json"
    shell: "python {input.script} {input.data} {output}"
```

- Prefix rule names with the analysis name; rule names are global.
- Add each analysis's final outputs to `rule all`. A second analysis whose
  rule file is included but whose outputs are not listed there is not run
  by a bare `snakemake`.
- Run with `snakemake --cores 1 <target>`, or `snakemake --cores 1` for
  `all`. Snakemake refuses to run without `--cores`.
  In `run.sh`, name the analysis's own final outputs as targets.
- `snakemake -n` lists what is out of date and why, without running it.
- In a sandbox that only allows writes inside the repository (Codex's
  default), Snakemake fails at startup because it keeps a cache in the
  user's cache folder. Prefix its commands with
  `XDG_CACHE_HOME="$PWD/.snakemake/cache"`, from the repository root.
- `snakemake --detailed-summary <target>` lists the outputs behind that
  target with their rule and inputs. It reads `.snakemake/`, which is not
  committed, so on a fresh clone it shows no inputs until the steps run.

## DVC

Install it in the project environment and record it in
`ENVIRONMENTS_INSTALLATIONS.md`. Run `dvc init` once (`dvc init --subdir`
when the Mycelium repository is a subfolder of a larger git repository). It
expects a git repository; if there is none, ask the user before running
`git init`. It
stages its own files (`.dvc/config`, `.dvc/.gitignore`, `.dvcignore`) in
git; tell the user before committing them.

One `dvc.yaml` per analysis:

```yaml
# analysis/expression-fit/dvc.yaml
stages:
  prepare:
    wdir: ../..
    cmd: python analysis/expression-fit/scripts/prepare.py data/raw/counts.csv analysis/expression-fit/outputs/clean.csv
    deps:
      - data/raw/counts.csv
      - analysis/expression-fit/scripts/prepare.py
    outs:
      - analysis/expression-fit/outputs/clean.csv:
          cache: false
  fit:
    wdir: ../..
    cmd: python analysis/expression-fit/scripts/fit.py analysis/expression-fit/outputs/clean.csv analysis/expression-fit/outputs/summary.json
    deps:
      - analysis/expression-fit/outputs/clean.csv
      - analysis/expression-fit/scripts/fit.py
    outs:
      - analysis/expression-fit/outputs/summary.json:
          cache: false
```

- `wdir: ../..` makes every path relative to the repository root.
- `cache: false` leaves each output as an ordinary file where the script
  wrote it. Leave it off only if the user wants DVC to store the data.
- DVC does not create output folders (Snakemake does). The script must
  create the folder it writes to, as the rule above says.
- Run with `dvc repro analysis/expression-fit/dvc.yaml`, or `dvc repro -P`
  for every pipeline. A bare `dvc repro` fails when there is no root
  `dvc.yaml`.
- `dvc status` lists what is out of date and why.
- In a sandbox that only allows writes inside the repository (Codex's
  default), DVC fails because it keeps a cache outside it. Prefix
  `dvc init` with `DVC_SITE_CACHE_DIR="$PWD/.dvc/tmp/site-cache"`, then run
  `dvc config --local core.site_cache_dir "$PWD/.dvc/tmp/site-cache"` once
  from the repository root. `--local` keeps the setting out of the
  committed `.dvc/config`. Where the sandbox also protects `.git`,
  `dvc init` still ends with a permission error after creating its files:
  it could not stage them. Stage them when you commit.
- Do not edit `dvc.lock` by hand, including to tidy whitespace. DVC wraps
  long commands there and leaves trailing spaces; if a whitespace check
  rejects the file, exclude `dvc.lock` from the check.

## What Mycelium records

Today Mycelium's lineage record does not read either tool's own record. A
`snakemake` run is recorded as one event naming the Snakefile, and the files
it changed are observed as for any command; a `dvc repro` run leaves only
the observed changes. Say this if the user asks where a result came from,
and answer from the tool: `snakemake --detailed-summary`, or `dvc.lock` and
`dvc dag`.

Mycelium's automatic reminder to record an analysis fires for `snakemake`
but not for `dvc repro`, which it does not yet recognise as an analysis
run. After a `dvc repro` that produced or changed results, complete the
post-action protocol yourself; do not wait to be prompted.
