"""Check a plan's data assumptions against a sample-level table, read-only.

Adapted from mycelium-extra (https://github.com/mdmanurung/mycelium-extra),
Copyright (c) 2026 Mikhael Manurung, MIT License. See issue #91.

Stdlib only; h5py is optional, for the `obs` of an `.h5ad` file. This script
ships under skills/core/scripts/, so Mycelium's hooks treat it as a managed
utility and running it opens no post-action cycle:

    python3 skills/core/scripts/data_contract_check.py --contract contract.json

The contract states what the plan assumes; this script decides what blocks.
A failed assumption blocks, and the contract cannot downgrade it. A table is
CSV, TSV (.tsv, .tab, .txt), or the `obs` of an `.h5ad` file; h5ad is read
through h5py when it is importable, and is a gap (never a pass) when it is
not. Any other format is a gap. Exit codes: 0 clean or warnings only, 2
blocking, 3 a gap and nothing blocking, 1 a malformed contract or usage error.
"""

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter, defaultdict

SCHEMA = "mycelium.data_contract.v1"
# Contracts written for mycelium-extra's checker run unchanged.
ACCEPTED_SCHEMAS = (SCHEMA, "mycelium-extra.data_contract.v1")
ALERT_SCHEMA = "mycelium.contract_alert.v1"
MISSING = {"", "na", "nan", "null", "none", "<na>"}
MAX_EVIDENCE = 5


class ContractError(Exception):
    pass


class TableGap(Exception):
    """The table, or a column of it, cannot be read here; the checks on it are gaps."""


def is_missing(value):
    return value is None or value.strip().lower() in MISSING


def alert(kind, message, blocking, expected=None, observed=None, evidence=None, gap=False):
    record = {
        "schema": ALERT_SCHEMA,
        "severity": "error" if blocking else ("gap" if gap else "warning"),
        "kind": kind,
        "message": message,
        "blocking": blocking,
    }
    if expected is not None:
        record["expected"] = expected
    if observed is not None:
        record["observed"] = observed
    if evidence:
        record["evidence"] = [str(item) for item in evidence[:MAX_EVIDENCE]]
        if len(evidence) > MAX_EVIDENCE:
            record["evidence"].append("... {} more".format(len(evidence) - MAX_EVIDENCE))
    return record


TAB_SEPARATED = (".tsv", ".tab", ".txt")


class TableShapeError(Exception):
    """The table parses but its shape makes every check on it untrustworthy; blocks."""


def read_table(path, wanted=None):
    """Return (columns, rows, unreadable): rows are dicts of strings, unreadable maps column to reason."""
    lower = path.lower()
    if lower.endswith(".h5ad"):
        return read_h5ad_obs(path, wanted)
    if lower.endswith(".csv"):
        delimiter = ","
    elif lower.endswith(TAB_SEPARATED):
        delimiter = "\t"
    else:
        raise TableGap("unsupported table format '{}'; export it to CSV or TSV".format(
            os.path.splitext(path)[1] or path))
    # Decode strictly: replacing bad bytes would merge distinct identifiers
    # (p\xff and p\xfe both become p\ufffd) and could fake a passing check.
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            records = list(csv.reader(handle, delimiter=delimiter))
    except UnicodeDecodeError as error:
        raise TableGap("the table is not valid UTF-8 (byte {} at offset {}); re-export it as UTF-8".format(
            error.object[error.start:error.start + 1], error.start))
    if not records:
        return [], [], {}
    columns = records[0]
    # Blank header fields (trailing delimiters) cannot be named by a contract.
    repeated = sorted(c for c, n in Counter(columns).items() if n > 1 and c.strip())
    if repeated:
        raise TableShapeError("column '{}' appears more than once in the header, so a check on it "
                              "cannot tell which one the plan means".format("', '".join(repeated)))
    rows = []
    for line_no, record in enumerate(records[1:], start=2):
        if not record:
            continue  # a blank line, as csv.DictReader skips it
        extra = record[len(columns):]
        if any(cell.strip() for cell in extra):
            hint = "; an R write.table export with row names does this, so re-export with " \
                "row.names = FALSE" if len(extra) == 1 else ""
            raise TableShapeError("line {} has more fields than the header ({} > {}), so its values "
                                  "cannot be assigned to columns{}".format(
                                      line_no, len(record), len(columns), hint))
        # A short row's absent trailing fields are missing values, never None.
        record = record[:len(columns)] + [""] * (len(columns) - len(record))
        rows.append(dict(zip(columns, record)))
    return columns, rows, {}


# ---------------------------------------------------------------- h5ad obs

def decode(value):
    """UTF-8 bytes from an .h5ad, strictly; undecodable bytes make the column unreadable."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TableGap("a value is not valid UTF-8 ({!r})".format(value[:error.end]))


def text(value):
    """One obs value as the string a CSV export would hold; missing values become ""."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return decode(value)
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float) and value != value:
        return ""
    return str(value)


def attr(node, key, default=None):
    value = node.attrs.get(key, default)
    if isinstance(value, bytes):
        return decode(value)
    if hasattr(value, "tolist") and not isinstance(value, str):
        value = value.tolist()
    if isinstance(value, list):
        return [text(v) for v in value]
    return value


def obs_values(h5file, node, h5py):
    """A 1-D obs column as a list of strings, in AnnData's on-disk encodings.

    anndata >= 0.8 (dataframe 0.2.0): arrays and string arrays are datasets;
    categoricals are groups with `codes` and `categories`; nullable integer,
    boolean, and string arrays are groups with `values` and `mask`.
    anndata 0.7 (dataframe 0.1.0): a categorical is a dataset of codes whose
    `categories` attribute references `obs/__categories/<column>`.
    Anything else raises TableGap.
    """
    encoding = attr(node, "encoding-type", "")
    if isinstance(node, h5py.Group):
        if encoding == "categorical" and "codes" in node and "categories" in node:
            return decode_codes(node["codes"][()].tolist(), obs_values(h5file, node["categories"], h5py))
        if encoding in ("nullable-integer", "nullable-boolean", "nullable-string-array") \
                and "values" in node and "mask" in node:
            values = obs_values(h5file, node["values"], h5py)
            mask = node["mask"][()].tolist()
            if len(mask) != len(values):
                raise TableGap("its mask and values differ in length")
            return ["" if hidden else v for v, hidden in zip(values, mask)]
        raise TableGap("unrecognised encoding '{}'".format(encoding or "none"))
    if node.dtype.names or len(node.shape) != 1:
        raise TableGap("not a 1-D column (shape {}, encoding '{}')".format(node.shape, encoding or "none"))
    if "categories" in node.attrs and isinstance(node.attrs["categories"], h5py.Reference):
        categories = h5file[node.attrs["categories"]][()].tolist()
        return decode_codes(node[()].tolist(), [text(c) for c in categories])
    return [text(v) for v in node[()].tolist()]


def decode_codes(codes, categories):
    if any(c >= len(categories) for c in codes):
        raise TableGap("a categorical code has no category")
    return ["" if c < 0 else categories[c] for c in codes]


def read_h5ad_obs(path, wanted=None):
    """Read obs from an .h5ad file through h5py; never reads X or layers."""
    try:
        import h5py
    except ImportError as error:
        raise TableGap("h5py cannot be imported ({}), so the obs of an .h5ad file cannot be read; install "
                       "h5py or export obs to a TSV".format(error))
    try:
        h5file = h5py.File(path, "r")
    except (OSError, IOError) as error:
        raise TableGap("not readable as HDF5: {}".format(error))
    with h5file:
        obs = h5file.get("obs")
        if not isinstance(obs, h5py.Group) or attr(obs, "encoding-type") != "dataframe" \
                or not isinstance(attr(obs, "_index"), str) or attr(obs, "_index") not in obs:
            raise TableGap("obs is not in a recognised AnnData encoding (anndata 0.7 or later expected: "
                           "a dataframe group with an _index attribute)")
        index = attr(obs, "_index")
        order = attr(obs, "column-order", [])
        if not isinstance(order, list):
            order = [text(order)] if order else []
        columns = [index] + [c for c in order if c != index]
        missing = [c for c in columns if c not in obs]
        if missing:
            raise TableGap("obs lists columns it does not hold: " + ", ".join(missing))
        values, unreadable = {}, {}
        for column in columns:
            if wanted is not None and column not in wanted and column != index:
                continue  # read only what the contract names, plus the index for the row count
            try:
                values[column] = obs_values(h5file, obs[column], h5py)
            except TableGap as error:
                if column == index:
                    raise TableGap("its index '{}' is unreadable: {}".format(index, error))
                unreadable[column] = str(error)
        if len({len(v) for v in values.values()}) > 1:
            raise TableGap("obs columns differ in length")
    names = list(values)
    rows = [dict(zip(names, cells)) for cells in zip(*[values[c] for c in names])]
    return columns, rows, unreadable


def apply_filters(rows, filters, columns):
    for rule in filters:
        column = rule.get("column")
        if "equals" in rule:
            allowed = {str(rule["equals"])}
            rows = [r for r in rows if r[column] in allowed]
        elif "in" in rule:
            allowed = {str(v) for v in rule["in"]}
            rows = [r for r in rows if r[column] in allowed]
        elif "not_in" in rule:
            banned = {str(v) for v in rule["not_in"]}
            rows = [r for r in rows if r[column] not in banned]
        else:
            raise ContractError("filter needs equals, in, or not_in: {}".format(rule))
    return rows


def need(check, key):
    if key not in check:
        raise ContractError("{} check needs '{}'".format(check.get("kind"), key))
    return check[key]


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def drop_missing(rows, keys, kind):
    """Split rows into complete and a warning for rows missing any key column."""
    kept = [r for r in rows if not any(is_missing(r[k]) for k in keys)]
    dropped = len(rows) - len(kept)
    warnings = []
    if dropped:
        warnings.append(alert(
            kind, "{} rows have a missing value in {} and were left out of this check".format(
                dropped, ", ".join(keys)),
            blocking=not kept, observed="{} of {} rows".format(dropped, len(rows))))
    return kept, warnings


def check_schema(check, columns, rows):
    missing = [c for c in need(check, "columns") if c not in columns]
    if not missing:
        return []
    return [alert("schema", "required columns are absent", True,
                  expected=", ".join(check["columns"]), observed="missing: " + ", ".join(missing))]


def check_cohort(check, columns, rows):
    alerts = []
    column = check.get("column")
    counted = rows
    if column is not None:
        # A row with no cohort value cannot contribute to a cohort comparison,
        # so it counts toward neither the levels nor the row counts. no_missing
        # below still sees every filtered row.
        counted, alerts = drop_missing(rows, [column], "cohort")
        present = Counter(r[column] for r in counted)
        absent = [lvl for lvl in as_list(check.get("levels")) if str(lvl) not in present]
        if absent:
            alerts.append(alert(
                "cohort", "required levels of '{}' are absent after filtering".format(column), True,
                expected=", ".join(str(l) for l in check["levels"]),
                observed="absent: " + ", ".join(str(l) for l in absent),
                evidence=["{}={}".format(k, v) for k, v in sorted(present.items())]))
    if "min_rows" in check and len(counted) < int(check["min_rows"]):
        alerts.append(alert("cohort", "fewer rows than the plan assumes", True,
                            expected=">= {}".format(check["min_rows"]), observed=str(len(counted))))
    if "rows" in check and len(counted) != int(check["rows"]):
        alerts.append(alert("cohort", "row count differs from the plan", True,
                            expected=str(check["rows"]), observed=str(len(counted))))
    for col in as_list(check.get("no_missing")):
        n = sum(1 for r in rows if is_missing(r[col]))
        if n:
            alerts.append(alert("cohort", "'{}' has missing values".format(col), True,
                                expected="0 missing", observed="{} of {} rows".format(n, len(rows))))
    return alerts


def check_unit(check, columns, rows):
    unit = need(check, "unit")
    group = need(check, "group")
    within = as_list(check.get("within"))
    # Each sub-check prunes only on the keys it uses. Every observed group is
    # held to the minimum, even one whose rows all lack a unit, and a unit's
    # group membership does not depend on its `within` value being present.
    groups = {r[group] for r in rows if not is_missing(r[group])}
    members = [r for r in rows if not is_missing(r[unit]) and not is_missing(r[group])]
    rows, alerts = drop_missing(rows, [unit, group] + within, "unit_of_replication")
    if not rows:
        return alerts
    cells = Counter((r[group],) + tuple(r[w] for w in within) + (r[unit],) for r in rows)
    repeats = sorted(key for key, n in cells.items() if n > 1)
    if repeats:
        labels = [group] + within + [unit]
        alerts.append(alert(
            "unit_of_replication",
            "a unit appears more than once in the same {} cell; rows are not independent units".format(
                " x ".join([group] + within)),
            True, expected="1 row per {} per cell".format(unit),
            observed="{} repeated cells".format(len(repeats)),
            evidence=[", ".join("{}={}".format(l, v) for l, v in zip(labels, key)) +
                      " ({} rows)".format(cells[key]) for key in repeats]))
    units = {g: set() for g in groups}
    for r in members:
        units[r[group]].add(r[unit])
    minimum = int(check.get("min_units_per_group", 2))
    small = sorted((g, len(u)) for g, u in units.items() if len(u) < minimum)
    if small:
        alerts.append(alert(
            "unit_of_replication", "groups with fewer distinct {} than required".format(unit), True,
            expected=">= {} per {}".format(minimum, group),
            observed=", ".join("{}={}".format(g, n) for g, n in small)))
    shared = sorted(u for u in set.union(set(), *units.values()) if sum(u in s for s in units.values()) > 1) \
        if len(units) > 1 else []
    if shared and not check.get("units_may_span_groups", False):
        alerts.append(alert(
            "unit_of_replication", "some {} appear in more than one {} level".format(unit, group), True,
            expected="each {} in one {}".format(unit, group),
            observed="{} shared units".format(len(shared)), evidence=shared))
    return alerts


def check_pairing(check, columns, rows):
    unit = need(check, "unit")
    within = need(check, "within")
    # A unit whose rows all lack a `within` value is observed at no level: it
    # stays in the population as incomplete rather than vanishing.
    population = {r[unit] for r in rows if not is_missing(r[unit])}
    rows, alerts = drop_missing(rows, [unit, within], "pairing")
    if not rows:
        return alerts
    levels = [str(l) for l in as_list(check.get("levels"))] or sorted({r[within] for r in rows})
    seen = {u: set() for u in population}
    for r in rows:
        seen[r[unit]].add(r[within])
    incomplete = sorted((u, sorted(set(levels) - s)) for u, s in seen.items() if set(levels) - s)
    if incomplete:
        alerts.append(alert(
            "pairing", "units are missing a level of '{}'".format(within), True,
            expected="every {} at {}".format(unit, ", ".join(levels)),
            observed="{} of {} units incomplete".format(len(incomplete), len(seen)),
            evidence=["{} lacks {}".format(u, ", ".join(m)) for u, m in incomplete]))
    return alerts


def check_batch(check, columns, rows):
    batch = need(check, "batch")
    contrast = need(check, "contrast")
    observed_levels = {r[contrast] for r in rows if not is_missing(r[contrast])}
    rows, alerts = drop_missing(rows, [batch, contrast], "batch_confounding")
    if not rows:
        return alerts
    # A level seen only with a missing batch drops out of the batch table, so
    # its confounding with batch cannot be checked at all.
    unbatched = sorted(observed_levels - {r[contrast] for r in rows})
    if unbatched:
        alerts.append(alert(
            "batch_confounding", "contrast levels with no known batch; their batch confounding cannot "
            "be checked", True, expected="every {} level has rows with a {}".format(contrast, batch),
            observed=", ".join(unbatched)))
    table = defaultdict(Counter)
    for r in rows:
        table[r[batch]][r[contrast]] += 1
    levels = sorted({r[contrast] for r in rows})
    evidence = ["{}: {}".format(b, ", ".join("{}={}".format(l, c[l]) for l in levels if c[l]))
                for b, c in sorted(table.items())]
    if len(levels) < 2:
        return alerts + [alert("batch_confounding", "the contrast has fewer than two levels", True,
                               observed=", ".join(levels) or "none")]
    single = sorted(b for b, c in table.items() if len(c) == 1)
    if len(single) == len(table):
        alerts.append(alert(
            "batch_confounding", "'{}' is fully nested in '{}': no batch holds two contrast levels".format(
                contrast, batch), True,
            expected="batches shared across {} levels".format(contrast),
            observed="{} of {} batches single-level".format(len(single), len(table)), evidence=evidence))
        return alerts
    if single:
        alerts.append(alert(
            "batch_confounding", "some batches hold only one '{}' level".format(contrast), False,
            observed="{} of {} batches single-level".format(len(single), len(table)),
            evidence=["{}: {}".format(b, next(iter(table[b]))) for b in single]))
    components = level_components(table, levels)
    if len(components) > 1:
        alerts.append(alert(
            "batch_confounding",
            "contrast levels fall into groups that share no batch; any comparison across groups is "
            "confounded with batch",
            True, expected="every level linked to every other through shared batches",
            observed=" | ".join(", ".join(group) for group in components),
            evidence=["{}: {}".format(", ".join(group), ", ".join(
                sorted(b for b, c in table.items() if any(c[l] for l in group)))) for group in components]))
    isolated = {l for group in components if len(group) == 1 for l in group}
    lone = sorted(l for l in levels if l not in isolated
                  and sum(1 for c in table.values() if c[l]) == 1)
    if lone:
        alerts.append(alert(
            "batch_confounding", "contrast levels measured in a single batch", False,
            observed=", ".join(lone)))
    share = check.get("max_share")
    if share is not None:
        totals = Counter(r[contrast] for r in rows)
        over = [(b, l) for b, c in sorted(table.items()) for l in levels
                if c[l] and c[l] / float(totals[l]) > share]
        if over:
            alerts.append(alert(
                "batch_confounding",
                "a batch holds more than {} of a '{}' level".format(percent(share), contrast), False,
                expected="each batch at most {} of each {} level".format(percent(share), contrast),
                observed="{} batch-level pairs over".format(len(over)),
                evidence=["{} holds {} of {} {} rows ({})".format(
                    b, table[b][l], totals[l], l, percent(table[b][l] / float(totals[l]))) for b, l in over]))
    return alerts


def level_components(table, levels):
    """Contrast levels grouped by the batches that connect them, each group sorted."""
    parent = {l: l for l in levels}

    def root(level):
        while parent[level] != level:
            parent[level] = parent[parent[level]]
            level = parent[level]
        return level

    for counts in table.values():
        present = [l for l in levels if counts[l]]
        for other in present[1:]:
            parent[root(other)] = root(present[0])
    groups = defaultdict(list)
    for level in levels:
        groups[root(level)].append(level)
    return sorted(sorted(group) for group in groups.values())


def percent(share):
    return "{:.0f}%".format(100 * share)


def cross_table(check, rows):
    """The batch-by-contrast row counts the check ran on, shown on every run."""
    batch, contrast = check["batch"], check["contrast"]
    rows = [r for r in rows if not is_missing(r[batch]) and not is_missing(r[contrast])]
    counts = defaultdict(Counter)
    for r in rows:
        counts[r[batch]][r[contrast]] += 1
    return {"batch": batch, "contrast": contrast, "levels": sorted({r[contrast] for r in rows}),
            "counts": dict((b, dict(c)) for b, c in sorted(counts.items()))}


def render_cross_table(table):
    """Counts with each cell's share of its contrast level, as plain aligned text."""
    levels = table["levels"]
    totals = Counter()
    for counts in table["counts"].values():
        totals.update(counts)
    header = ["{} \\ {}".format(table["batch"], table["contrast"])] + levels + ["total"]
    body = []
    for b, counts in sorted(table["counts"].items()):
        cells = ["{} ({})".format(counts.get(l, 0), percent(counts.get(l, 0) / float(totals[l])))
                 for l in levels]
        body.append([b] + cells + [str(sum(counts.values()))])
    body.append(["total"] + [str(totals[l]) for l in levels] + [str(sum(totals.values()))])
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in [header] + body]


def fail(where, message):
    raise ContractError("{} {}".format(where, message).strip())


def is_scalar(value):
    return isinstance(value, str) or (isinstance(value, (int, float)) and not isinstance(value, bool))


def validate_column(where, check, key, required=False):
    if key not in check:
        if required:
            fail(where, "needs '{}'".format(key))
        return
    if not isinstance(check[key], str) or not check[key]:
        fail(where, "'{}' must be a column name (a non-empty string): {}".format(
            key, json.dumps(check[key])))


def validate_columns(where, check, key, allow_single=False, required=False):
    """A list of column names; `allow_single` also accepts one name, as as_list() does."""
    if key not in check:
        if required:
            fail(where, "needs '{}'".format(key))
        return
    value = check[key]
    if allow_single and isinstance(value, str) and value:
        return
    if not isinstance(value, list) or (required and not value) \
            or not all(isinstance(c, str) and c for c in value):
        fail(where, "'{}' must be a list of column names: {}".format(key, json.dumps(value)))


def validate_count(where, check, key, minimum):
    if key in check:
        value = check[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            fail(where, "'{}' must be a whole number of at least {}: {}".format(
                key, minimum, json.dumps(value)))


def validate_levels(where, check):
    if "levels" in check:
        levels = check["levels"]
        if not isinstance(levels, list) or not levels or not all(is_scalar(l) for l in levels):
            fail(where, "'levels' must be a non-empty list of strings or numbers: {}".format(
                json.dumps(levels)))


def validate_filters(where, filters):
    if not isinstance(filters, list):
        fail(where, "filters must be a list: {}".format(json.dumps(filters)))
    for n, rule in enumerate(filters):
        at = "{}filters[{}]".format(where + " " if where else "", n)
        if not isinstance(rule, dict):
            fail(at, "must be an object")
        if "column" not in rule:
            fail(at, "needs a 'column'")
        validate_column(at, rule, "column")
        ops = [op for op in ("equals", "in", "not_in") if op in rule]
        if len(ops) != 1:
            fail(at, "needs exactly one of equals, in, not_in")
        op = ops[0]
        values = [rule[op]] if op == "equals" else rule[op]
        if op != "equals" and not isinstance(values, list):
            fail(at, "'{}' must be a list".format(op))
        if not all(is_scalar(v) for v in values):
            fail(at, "values must each be a string or number (compared as text): {}".format(
                json.dumps(rule[op])))


def validate_contract(contract):
    """Contract syntax the checks rely on; a bad value is a usage error, never a pass.

    Every type a check dereferences is constrained here, before any table is
    read; unknown keys are left alone.
    """
    if not isinstance(contract, dict):
        raise ContractError("contract must be a JSON object")
    if contract.get("schema") not in ACCEPTED_SCHEMAS:
        raise ContractError("contract schema must be " + SCHEMA)
    if "table" in contract and (not isinstance(contract["table"], str) or not contract["table"]):
        raise ContractError("table must be a non-empty path string")
    validate_filters("", contract.get("filters", []))
    checks = contract.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ContractError("checks must be a non-empty list of check objects")
    for index, check in enumerate(checks):
        where = "checks[{}]".format(index)
        if not isinstance(check, dict):
            fail(where, "must be an object")
        kind = check.get("kind")
        if kind not in CHECKS:
            raise ContractError("unknown check kind at {}: {}".format(where, json.dumps(kind)))
        where = "{} ({})".format(where, kind)
        if "table" in check and (not isinstance(check["table"], str) or not check["table"]):
            fail(where, "'table' must be a non-empty path string")
        if "label" in check and not isinstance(check["label"], str):
            fail(where, "'label' must be a string")
        validate_filters(where, check.get("filters", []))
        if kind == "schema":
            validate_columns(where, check, "columns", required=True)
        elif kind == "cohort":
            validate_column(where, check, "column")
            validate_levels(where, check)
            if "levels" in check and "column" not in check:
                fail(where, "'levels' needs 'column', the column whose levels they are")
            validate_count(where, check, "min_rows", 0)
            validate_count(where, check, "rows", 0)
            validate_columns(where, check, "no_missing", allow_single=True)
        elif kind == "unit_of_replication":
            validate_column(where, check, "unit", required=True)
            validate_column(where, check, "group", required=True)
            validate_columns(where, check, "within", allow_single=True)
            validate_count(where, check, "min_units_per_group", 1)
            if "units_may_span_groups" in check and not isinstance(check["units_may_span_groups"], bool):
                fail(where, "'units_may_span_groups' must be true or false: {}".format(
                    json.dumps(check["units_may_span_groups"])))
        elif kind == "pairing":
            validate_column(where, check, "unit", required=True)
            validate_column(where, check, "within", required=True)
            validate_levels(where, check)
        elif kind == "batch_confounding":
            validate_column(where, check, "batch", required=True)
            validate_column(where, check, "contrast", required=True)
            if "max_share" in check:
                share = check["max_share"]
                if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
                    fail(where, "max_share must be a number above 0 and at most 1, as a "
                                         "share (0.7 for 70%): {}".format(json.dumps(share)))


CHECKS = {
    "schema": check_schema,
    "cohort": check_cohort,
    "unit_of_replication": check_unit,
    "pairing": check_pairing,
    "batch_confounding": check_batch,
}


def referenced_columns(check):
    keys = ["column", "unit", "group", "batch", "contrast"]
    cols = [check[k] for k in keys if k in check]
    cols += as_list(check.get("within")) + as_list(check.get("no_missing"))
    return cols


def filter_columns(filters):
    return [rule.get("column") for rule in filters]


RELAXATIONS = ("units_may_span_groups",)


def run_contract(contract, base_dir):
    """Return (per-check summaries, alerts)."""
    validate_contract(contract)
    summaries, alerts = [], []
    cache = {}
    plan = []
    for index, check in enumerate(contract["checks"]):
        kind = check["kind"]
        table = check.get("table", contract.get("table"))
        if not table:
            raise ContractError("checks[{}] has no table".format(index))
        filters = contract.get("filters", []) + check.get("filters", [])
        path = table if os.path.isabs(table) else os.path.join(base_dir, table)
        named = filter_columns(filters) + ([] if kind == "schema" else referenced_columns(check))
        plan.append((index, check, kind, table, filters, path, named))
    wanted = defaultdict(set)  # an .h5ad's obs is read only for the columns the contract names
    for _, _, _, _, _, path, named in plan:
        wanted[path].update(named)
    for index, check, kind, table, filters, path, named in plan:
        label = "checks[{}] {}".format(index, check.get("label", kind))
        found = []
        if not os.path.isfile(path):
            found.append(alert("schema", "the table the plan names does not exist", True,
                               observed="not found: " + table))
            columns, rows = [], []
        else:
            if path not in cache:
                try:
                    cache[path] = read_table(path, wanted[path])
                except (TableGap, TableShapeError) as error:
                    cache[path] = error
            if isinstance(cache[path], TableGap):
                found.append(alert(kind, "the table cannot be read, so this check did not run", False,
                                   observed=str(cache[path]), gap=True))
                columns, rows, unreadable = [], [], {}
            elif isinstance(cache[path], TableShapeError):
                found.append(alert("schema", "the table is malformed, so no check on it can be trusted", True,
                                   observed=str(cache[path])))
                columns, rows, unreadable = [], [], {}
            else:
                columns, rows, unreadable = cache[path]
            absent = sorted({c for c in named if c not in columns}) if columns else []
            if absent:
                found.append(alert("schema", "columns this check or its filters name are absent", True,
                                   observed="missing: " + ", ".join(absent)))
            gaps = sorted({c for c in named if c in unreadable})
            if gaps:
                found.append(alert(kind, "columns this check or its filters name cannot be read, so "
                                         "this check did not run", False, gap=True,
                                   evidence=["{}: {}".format(c, unreadable[c]) for c in gaps]))
        rows_checked = 0
        if not found:
            rows = apply_filters(rows, filters, columns)
            rows_checked = len(rows)
            if not rows:
                found.append(alert(kind, "no rows left after filters; the check would pass vacuously", True,
                                   expected=">= 1 row",
                                   observed="0 of {} rows".format(len(cache[path][1])),
                                   evidence=[json.dumps(f, sort_keys=True) for f in filters]))
            else:
                found.extend(CHECKS[kind](check, columns, rows))
        for record in found:
            record["check"] = label
            record["table"] = table
            record["rows_checked"] = rows_checked
        status = "block" if any(a["blocking"] for a in found) else \
            "gap" if any(a["severity"] == "gap" for a in found) else ("warn" if found else "pass")
        summary = {"check": label, "kind": kind, "table": table, "rows_checked": rows_checked,
                   "status": status, "relaxations": [k for k in RELAXATIONS if check.get(k)]}
        if kind == "batch_confounding" and rows_checked:
            summary["cross_table"] = cross_table(check, rows)
        summaries.append(summary)
        alerts.extend(found)
    return summaries, alerts


def render(summaries, alerts):
    blocking = sum(a["blocking"] for a in alerts)
    gaps = sum(a["severity"] == "gap" for a in alerts)
    lines = ["{} checks; {} blocking alerts, {} warnings{}.".format(
        len(summaries), blocking, len(alerts) - blocking - gaps, ", {} gaps".format(gaps) if gaps else "")]
    for summary in summaries:
        line = "{:5} {} ({} rows)".format(summary["status"].upper(), summary["check"], summary["rows_checked"])
        if summary["relaxations"]:
            line += "  relaxed: " + ", ".join(summary["relaxations"])
        lines.append(line)
        if "cross_table" in summary:
            lines.append("    rows by batch and contrast level (share of the level):")
            lines.extend("      " + row for row in render_cross_table(summary["cross_table"]))
        for a in (a for a in alerts if a["check"] == summary["check"]):
            label = "BLOCK" if a["blocking"] else ("GAP  " if a["severity"] == "gap" else "warn:")
            lines.append("    {} {}".format(label, a["message"]))
            for key in ("expected", "observed"):
                if key in a:
                    lines.append("      {}: {}".format(key, a[key]))
            for item in a.get("evidence", []):
                lines.append("      - " + item)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", required=True, help="path to the contract JSON")
    parser.add_argument("--root", default=".", help="directory relative table paths resolve from")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        with open(args.contract, encoding="utf-8") as handle:
            contract = json.load(handle)
        summaries, alerts = run_contract(contract, args.root)
    except (ContractError, ValueError, OSError) as error:
        print("contract error: {}".format(error), file=sys.stderr)
        return 1
    if args.json:
        json.dump({"checks": summaries, "alerts": alerts}, sys.stdout, indent=1)
        print()
    else:
        print(render(summaries, alerts))
    if any(a["blocking"] for a in alerts):
        return 2
    return 3 if any(a["severity"] == "gap" for a in alerts) else 0


if __name__ == "__main__":
    if sys.stdout.encoding.lower().replace("-", "") != "utf8":  # an ASCII locale on Python 3.6
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, sys.stdout.encoding, "backslashreplace")
    sys.exit(main())
