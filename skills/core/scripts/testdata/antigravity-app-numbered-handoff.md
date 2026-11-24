# Session Handoff — 2026-11-23-001

## 1. Accomplished

- Verified the `expr` sample ledger via `data_contract_check.py --ledger data/metadata/expr/samples.ledger.json --emit-contract analysis/de/data_contract.json` (7/7 checks PASS, 0 warnings, 0 blocking alerts).
- Implemented `analysis/de/run.py` (Python 3 standard library only + Mycelium `register_value`/`read_source`/`record_ids`) and `analysis/de/params.yaml`, computing per-gene control/treated means and ratios for all 40 genes in `data/raw/expr/counts.tsv` across 12 samples in `data/metadata/expr/samples.tsv`.
- Ran `analysis/de/run.py --config analysis/de/params.yaml` with `ratio_threshold: 1.5`, producing `analysis/de/outputs/gene_means.csv`, `analysis/de/outputs/access.json`, and `analysis/de/outputs/numbers.json` (`n_genes_above_threshold = 3`: `g3`, `g7`, `g11`, hash-linked to both `gene_means.csv` and `params.yaml`).
- Edited `analysis/de/params.yaml` to `ratio_threshold: 1.3`, confirmed staleness via `check_report_values.py --fragment analysis/de/outputs/numbers.json --strict --explain` (exit 1, `[stale-value]` on `params.yaml`, `lineage: no-recorded-write`), reran `analysis/de/run.py`, and re-verified with `check_report_values.py` (exit 0, 0 findings).
- Queried `lineage_index.py` both before and after Stop consolidated `.living/log/data-lineage/2026-11-23-001.json`: post-consolidation `build` showed `manifests: 1, stores: 0, events: 10, observed_calls: 0, writes: 0, reads: 0`, and `query --path analysis/de/outputs/gene_means.csv` still returned exit 1 (`found: false`, `record.status: "no-recorded-write"`, `disk: "unverified"`, `reason: "nothing-to-compare"`).
- Updated `analysis/de/DE.md`, `analysis/ANALYSIS_MANIFEST.md`, `data/DATA_MANIFEST.md`, `.living/decisions.md`, `.living/learnings.md`, `.living/findings/differential-expression.md`, `.living/findings/FINDINGS_REGISTRY.md`, and `.living/log/2026-11-23-001-ag-154-live-2.md`.

## 2. Current State

- `analysis/de/params.yaml` is set to `ratio_threshold: 1.3` (`sha256: c88da3dea601e98aa01999d4419ac7221d02cbc3339c2dd0abc03383ec3a650a`).
- `analysis/de/outputs/gene_means.csv` holds 40 genes (`sha256: ce4aa3064d1a95052c065cb381395ece64a8aecbfc95f212021dcf8297052d7f`).
- `analysis/de/outputs/numbers.json` registers `n_genes_above_threshold = 3`, passing `check_report_values.py --strict --explain` with 0 errors and 0 warnings.
- `analysis/de/outputs/access.json` records `samples_used` (12) and `genes_analyzed` (40).
- `.living/log/data-lineage/2026-11-23-001.json` is consolidated; `.living/` manifests, decisions, learnings, findings, index, and session log are updated and pass `validate_structure.py`.

## 3. Key Decisions

- Implemented `analysis/de/run.py` strictly with Python 3 standard library plus `.mycelium/plugin-root`'s `register_value.py` helper, including a strict flat-YAML validator and 1-to-1 sample/gene checks.
- Kept `gene_means.csv` threshold-independent (`gene,control_mean,treated_mean,ratio`) and linked `n_genes_above_threshold` to both `gene_means.csv` and `params.yaml` via `read_source` + `register_value(..., source=[...])`.
- Updated `ratio_threshold` to `1.3` and refreshed `numbers.json` by rerunning `analysis/de/run.py` rather than editing hashes.

### Locked decisions

- Never modify `data/raw/expr/counts.tsv` (raw data is immutable).
- Never refresh `source.sha256` in `numbers.json` by hand; always rerun the analysis script.
- Keep `analysis/de/run.py` standard-library-only (no pandas or third-party dependencies).

## 4. Blockers

- `lineage_index.py` cannot certify which call produced `analysis/de/outputs/gene_means.csv` even after Stop consolidated `.living/log/data-lineage/2026-11-23-001.json` (`manifests: 1, events: 10`), because `mycelium-io-observer.sh` did not produce observation stores in this Antigravity App turn (`stores: 0, observed_calls: 0, writes: 0`, `observation_id: null` on events) and static I/O scanning marks stdlib `open()`/`csv` paths `unresolved`.
- `scilintr` is not installed in the environment (`scilintr not found`), so automated scientific linting could not be executed.

### Dead ends — do not redo

- Running `data_contract_check.py --emit-contract analysis/de/data_contract.json` before `analysis/de/` exists fails with `No such file or directory`; create the analysis directory first.
- Waiting for Stop consolidation alone does not populate `lineage_index.py` writes when `io_observer` stores are absent (`stores: 0`).

## 5. Next Steps

- Investigate why `mycelium-io-observer.sh` did not run in this Antigravity App turn so `lineage_index.py` can record observed writes and certify output producers.
- Install `scilintr` (`pip install scilintr`) and run `scilintr analysis/de/` when package installation is available.
- Optionally add a derivation-level statistical test adjusting for `prep_date` and `sequencing_run` if formal DE inference is needed beyond fold-change ratios.
