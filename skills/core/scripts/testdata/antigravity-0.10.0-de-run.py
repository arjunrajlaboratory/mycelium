#!/usr/bin/env python3
"""Differential expression ratio analysis across drug and placebo arms.

Reads expression counts (genes x samples) and the sample ledger, filters genes
by minimum mean expression across all samples, computes within-donor
week4/baseline ratios and per-arm mean ratios, writes outputs/gene_ratios.csv,
records feature/sample access fingerprints, and registers the count of genes
whose drug-arm mean ratio exceeds the configured threshold.

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = Path(__file__).resolve().parent
DEFAULT_COUNTS = REPO_ROOT / "data" / "raw" / "expr" / "counts.tsv"
DEFAULT_LEDGER = REPO_ROOT / "data" / "metadata" / "expr" / "samples.ledger.json"
DEFAULT_OUTPUT = ANALYSIS_DIR / "outputs" / "gene_ratios.csv"

# Pre-specified minimum mean count across all samples (from analysis request).
MIN_MEAN_COUNT = 20.0
ALLOWED_CONFIG_KEYS = frozenset({"ratio_threshold"})
EXPECTED_VISITS = ("baseline", "week4")


def _load_mycelium_register_value() -> tuple[Any, Any, Any]:
    """Import read_source, record_ids, and register_value from the Mycelium plugin."""
    plugin_root_file = REPO_ROOT / ".mycelium" / "plugin-root"
    if not plugin_root_file.is_file():
        raise FileNotFoundError(
            f"Missing Mycelium plugin-root pointer: {plugin_root_file}"
        )
    plugin_root = Path(plugin_root_file.read_text(encoding="utf-8").strip())
    scripts_dir = plugin_root / "skills" / "core" / "scripts"
    if not (scripts_dir / "register_value.py").is_file():
        raise FileNotFoundError(
            f"register_value.py not found under {scripts_dir}"
        )
    scripts_str = str(scripts_dir)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    from register_value import read_source, record_ids, register_value

    return read_source, record_ids, register_value


def parse_simple_yaml(text: str, source_label: str) -> dict[str, str]:
    """Parse a flat key: value YAML document using only the standard library."""
    parsed: dict[str, str] = {}
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if ":" not in stripped:
            raise ValueError(
                f"{source_label}:{line_no}: expected 'key: value', got {raw_line!r}"
            )
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(
                f"{source_label}:{line_no}: empty key or value in {raw_line!r}"
            )
        if key in parsed:
            raise ValueError(
                f"{source_label}:{line_no}: duplicate config key {key!r}"
            )
        parsed[key] = value
    return parsed


def load_config(
    config_path: Path, read_source_fn: Any
) -> tuple[float, dict[str, Any]]:
    """Read and validate the YAML config and return (ratio_threshold, source_entry)."""
    text, cfg_source = read_source_fn(
        config_path, {"key": "ratio_threshold"}, mode="r"
    )
    raw = parse_simple_yaml(text, str(config_path))
    unknown = set(raw) - ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"Config {config_path} has unexpected keys: {sorted(unknown)}"
        )
    if "ratio_threshold" not in raw:
        raise ValueError(f"Config {config_path} missing required key 'ratio_threshold'")
    try:
        threshold = float(raw["ratio_threshold"])
    except ValueError as exc:
        raise ValueError(
            f"Config {config_path}: ratio_threshold must be numeric, got {raw['ratio_threshold']!r}"
        ) from exc
    if not math.isfinite(threshold) or threshold <= 0.0:
        raise ValueError(
            f"Config {config_path}: ratio_threshold must be positive and finite, got {threshold!r}"
        )
    return threshold, cfg_source


def load_ledger(
    ledger_spec_path: Path,
) -> tuple[Path, list[dict[str, str]], dict[str, Any]]:
    """Load the sample ledger JSON spec and its paired TSV table with strict checks."""
    spec = json.loads(ledger_spec_path.read_text(encoding="utf-8"))
    required_spec_keys = {
        "file",
        "sample_id",
        "group",
        "unit_of_replication",
        "pairing",
    }
    missing_spec = required_spec_keys - set(spec)
    if missing_spec:
        raise ValueError(
            f"Ledger spec {ledger_spec_path} missing keys: {sorted(missing_spec)}"
        )

    sample_col = spec["sample_id"]
    group_col = spec["group"]
    unit_col = spec["unit_of_replication"]
    pairing = spec["pairing"]
    visit_col = pairing["within"]
    pairing_levels = tuple(pairing.get("levels", []))
    if pairing_levels != EXPECTED_VISITS:
        raise ValueError(
            f"Expected pairing levels {EXPECTED_VISITS}, got {pairing_levels}"
        )

    samples_path = (ledger_spec_path.parent / spec["file"]).resolve()
    if not samples_path.is_file():
        raise FileNotFoundError(f"Ledger sample table not found: {samples_path}")

    with samples_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Empty sample table: {samples_path}")
        required_cols = {sample_col, group_col, unit_col, visit_col}
        missing_cols = required_cols - set(reader.fieldnames)
        if missing_cols:
            raise ValueError(
                f"Sample table {samples_path} missing columns: {sorted(missing_cols)}"
            )
        rows = list(reader)

    if not rows:
        raise ValueError(f"Sample table {samples_path} has zero rows")

    seen_samples: set[str] = set()
    for idx, row in enumerate(rows, start=2):
        for col in required_cols:
            val = row.get(col)
            if val is None or val.strip() == "":
                raise ValueError(
                    f"Sample table {samples_path}:{idx}: missing value in column {col!r}"
                )
        sid = row[sample_col]
        if sid in seen_samples:
            raise ValueError(f"Duplicate sample_id {sid!r} in {samples_path}")
        seen_samples.add(sid)

    return samples_path, rows, {
        "sample_col": sample_col,
        "group_col": group_col,
        "unit_col": unit_col,
        "visit_col": visit_col,
    }


def load_counts(
    counts_path: Path, expected_sample_ids: list[str]
) -> list[tuple[str, dict[str, float]]]:
    """Load genes-by-samples count matrix and verify exact sample-ID alignment."""
    if not counts_path.is_file():
        raise FileNotFoundError(f"Counts file not found: {counts_path}")

    with counts_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Empty counts table: {counts_path}") from exc

        if not header or header[0] != "gene":
            raise ValueError(
                f"Counts table {counts_path}: first column must be 'gene', got {header[:1]}"
            )
        sample_cols = header[1:]
        if len(set(sample_cols)) != len(sample_cols):
            raise ValueError(f"Counts table {counts_path} has duplicate sample columns")

        expected_set = set(expected_sample_ids)
        actual_set = set(sample_cols)
        if actual_set != expected_set:
            missing = sorted(expected_set - actual_set)
            extra = sorted(actual_set - expected_set)
            raise ValueError(
                f"Sample ID mismatch between counts and ledger: missing={missing}, extra={extra}"
            )

        genes: list[tuple[str, dict[str, float]]] = []
        seen_genes: set[str] = set()
        for line_no, raw_row in enumerate(reader, start=2):
            if len(raw_row) != len(header):
                raise ValueError(
                    f"Counts table {counts_path}:{line_no}: expected {len(header)} fields, got {len(raw_row)}"
                )
            gene = raw_row[0].strip()
            if not gene:
                raise ValueError(f"Counts table {counts_path}:{line_no}: empty gene ID")
            if gene in seen_genes:
                raise ValueError(
                    f"Counts table {counts_path}:{line_no}: duplicate gene ID {gene!r}"
                )
            seen_genes.add(gene)
            values: dict[str, float] = {}
            for sid, token in zip(sample_cols, raw_row[1:]):
                try:
                    val = float(token)
                except ValueError as exc:
                    raise ValueError(
                        f"Counts table {counts_path}:{line_no} ({gene}, {sid}): non-numeric count {token!r}"
                    ) from exc
                if not math.isfinite(val) or val < 0.0:
                    raise ValueError(
                        f"Counts table {counts_path}:{line_no} ({gene}, {sid}): invalid count {val!r}"
                    )
                values[sid] = val
            genes.append((gene, values))

    if not genes:
        raise ValueError(f"Counts table {counts_path} has zero gene rows")
    return genes


def build_donor_pairs(
    sample_rows: list[dict[str, str]], cols: dict[str, str]
) -> dict[str, dict[str, dict[str, str]]]:
    """Organize samples as arm -> donor -> {baseline: sample_id, week4: sample_id}."""
    donor_arm: dict[str, str] = {}
    donor_visits: dict[str, dict[str, str]] = {}

    sample_col = cols["sample_col"]
    group_col = cols["group_col"]
    unit_col = cols["unit_col"]
    visit_col = cols["visit_col"]

    for row in sample_rows:
        sid = row[sample_col]
        arm = row[group_col]
        donor = row[unit_col]
        visit = row[visit_col]
        if visit not in EXPECTED_VISITS:
            raise ValueError(
                f"Unexpected visit level {visit!r} for sample {sid!r}; expected {EXPECTED_VISITS}"
            )
        if donor in donor_arm and donor_arm[donor] != arm:
            raise ValueError(
                f"Donor {donor!r} appears in multiple arms: {donor_arm[donor]!r} and {arm!r}"
            )
        donor_arm[donor] = arm
        visits = donor_visits.setdefault(donor, {})
        if visit in visits:
            raise ValueError(
                f"Donor {donor!r} has duplicate samples for visit {visit!r}: "
                f"{visits[visit]!r} and {sid!r}"
            )
        visits[visit] = sid

    by_arm: dict[str, dict[str, dict[str, str]]] = {}
    for donor, visits in sorted(donor_visits.items()):
        missing_visits = [v for v in EXPECTED_VISITS if v not in visits]
        if missing_visits:
            raise ValueError(
                f"Donor {donor!r} missing required visit(s): {missing_visits}"
            )
        arm = donor_arm[donor]
        by_arm.setdefault(arm, {})[donor] = visits

    for required_arm in ("drug", "placebo"):
        if required_arm not in by_arm or not by_arm[required_arm]:
            raise ValueError(f"Required arm {required_arm!r} has no paired donors")
    return by_arm


def run_analysis(
    counts_path: Path,
    ledger_spec_path: Path,
    config_path: Path,
    output_csv: Path,
) -> None:
    read_source_fn, record_ids_fn, register_value_fn = _load_mycelium_register_value()

    ratio_threshold, cfg_source = load_config(config_path, read_source_fn)
    samples_path, sample_rows, cols = load_ledger(ledger_spec_path)
    sample_ids = [row[cols["sample_col"]] for row in sample_rows]
    donor_pairs_by_arm = build_donor_pairs(sample_rows, cols)
    genes = load_counts(counts_path, sample_ids)

    n_genes_total = len(genes)
    kept_genes: list[tuple[str, float, dict[str, float]]] = []
    dropped_gene_ids: list[str] = []

    for gene, values in genes:
        mean_count = statistics.fmean(values[sid] for sid in sample_ids)
        if mean_count >= MIN_MEAN_COUNT:
            kept_genes.append((gene, mean_count, values))
        else:
            dropped_gene_ids.append(gene)

    kept_gene_ids = [g for g, _, _ in kept_genes]
    print(
        f"Loaded {n_genes_total} genes x {len(sample_ids)} samples "
        f"({len(donor_pairs_by_arm['drug'])} drug donors, "
        f"{len(donor_pairs_by_arm['placebo'])} placebo donors)."
    )
    print(
        f"Expression filter (mean_count >= {MIN_MEAN_COUNT}): "
        f"kept {len(kept_gene_ids)}, dropped {len(dropped_gene_ids)}."
    )

    if not kept_genes:
        raise ValueError(
            f"No genes passed mean_count >= {MIN_MEAN_COUNT}; aborting."
        )

    # Record row (sample) and feature (gene) access fingerprints for provenance.
    record_ids_fn("samples_used", sample_ids, axis="row", role="used", source=samples_path)
    record_ids_fn("genes_kept", kept_gene_ids, axis="feature", role="used", source=counts_path)
    record_ids_fn(
        "genes_dropped", dropped_gene_ids, axis="feature", role="excluded", source=counts_path
    )

    results: list[dict[str, Any]] = []
    for gene, mean_count, values in kept_genes:
        arm_means: dict[str, float] = {}
        for arm in ("drug", "placebo"):
            donor_ratios: list[float] = []
            for donor, visit_map in sorted(donor_pairs_by_arm[arm].items()):
                base_val = values[visit_map["baseline"]]
                week4_val = values[visit_map["week4"]]
                if base_val <= 0.0:
                    raise ValueError(
                        f"Non-positive baseline count ({base_val}) for gene {gene!r}, donor {donor!r}"
                    )
                donor_ratios.append(week4_val / base_val)
            arm_means[arm] = statistics.fmean(donor_ratios)

        results.append(
            {
                "gene": gene,
                "mean_count": mean_count,
                "drug_mean_ratio": arm_means["drug"],
                "placebo_mean_ratio": arm_means["placebo"],
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["gene", "mean_count", "drug_mean_ratio", "placebo_mean_ratio"]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "gene": row["gene"],
                    "mean_count": f"{row['mean_count']:.6f}",
                    "drug_mean_ratio": f"{row['drug_mean_ratio']:.6f}",
                    "placebo_mean_ratio": f"{row['placebo_mean_ratio']:.6f}",
                }
            )

    # Re-read the written table to count genes exceeding the configured threshold
    # from the exact bytes written to disk.
    n_exceeding = 0
    exceeding_genes: list[str] = []
    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ratio = float(row["drug_mean_ratio"])
            if ratio > ratio_threshold:
                n_exceeding += 1
                exceeding_genes.append(row["gene"])

    register_value_fn(
        "n_genes_drug_ratio_above_threshold",
        n_exceeding,
        source=[
            {"path": str(output_csv), "locator": {"column": "drug_mean_ratio"}},
            cfg_source,
        ],
    )

    print(
        f"Wrote {len(results)} rows to {output_csv.relative_to(REPO_ROOT)}."
    )
    print(
        f"Genes with drug_mean_ratio > {ratio_threshold}: "
        f"{n_exceeding} ({', '.join(exceeding_genes) if exceeding_genes else 'none'})."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute per-arm mean week4/baseline expression ratios."
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to YAML config containing ratio_threshold.",
    )
    parser.add_argument(
        "--counts",
        type=Path,
        default=DEFAULT_COUNTS,
        help=f"Path to counts TSV (default: {DEFAULT_COUNTS}).",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER,
        help=f"Path to sample ledger JSON spec (default: {DEFAULT_LEDGER}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_analysis(
        counts_path=args.counts,
        ledger_spec_path=args.ledger,
        config_path=args.config,
        output_csv=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
