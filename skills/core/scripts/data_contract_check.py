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
import stat
import sys
import tempfile
from collections import Counter, defaultdict

SCHEMA = "mycelium.data_contract.v1"
# Contracts written for mycelium-extra's checker run unchanged.
ACCEPTED_SCHEMAS = (SCHEMA, "mycelium-extra.data_contract.v1")
ALERT_SCHEMA = "mycelium.contract_alert.v1"
MISSING = {"", "na", "n/a", "#n/a", "nan", "null", "none", "<na>"}
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
    # Parse strictly too: lenient csv reads everything after an unclosed quote
    # into one field, so later rows silently vanish.
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=delimiter, strict=True)
            try:
                records = list(reader)
            except csv.Error as error:
                raise TableShapeError("line {} cannot be parsed ({}), so later rows may be missing or "
                                      "merged".format(reader.line_num, error))
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
        # Any extra field is malformed, even an empty one: the extra field may be
        # a leading row name, which shifts every value one column to the right.
        extra = record[len(columns):]
        if extra:
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


def apply_filters(rows, filters, columns, kind):
    """Return (kept rows, warnings)."""
    warnings = []
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
            # A row with no value passes a not_in filter by absence of evidence.
            unknown = sum(1 for r in rows if is_missing(r[column]))
            if unknown:
                warnings.append(alert(
                    kind, "{} rows have no value in filter column '{}' and were kept by its not_in "
                    "filter".format(unknown, column), False, observed="{} of {} rows".format(unknown, len(rows))))
        else:
            raise ContractError("filter needs equals, in, or not_in: {}".format(rule))
    return rows, warnings


def spelling_variants(rows, columns, kind):
    """Block when one value is spelled two ways that differ only in surrounding whitespace.

    "p1" and "p1 " compare as two units, which can hide a repeated cell or a
    shared unit, so the table's identities cannot be trusted as written.
    """
    alerts = []
    for column in columns:
        spellings = defaultdict(set)
        for r in rows:
            if not is_missing(r[column]):
                spellings[r[column].strip()].add(r[column])
        clashes = sorted(sorted(v) for v in spellings.values() if len(v) > 1)
        if clashes:
            alerts.append(alert(
                kind, "values of '{}' differ only in surrounding whitespace, so one identity may be "
                "counted twice".format(column), True, expected="one spelling per value",
                observed="{} values spelled more than one way".format(len(clashes)),
                evidence=[" / ".join(repr(s) for s in v) for v in clashes]))
    return alerts


def need(check, key):
    if key not in check:
        raise ContractError("{} check needs '{}'".format(check.get("kind"), key))
    return check[key]


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def having(rows, *keys):
    """The rows with a value in every one of `keys`.

    Each fact a check reports is derived from the rows complete in exactly the
    columns that fact needs, never from rows complete in all of the check's
    columns: a row missing one key still reveals the groups, units, and levels
    in its other keys, and those are held to the contract like any other.
    """
    return [r for r in rows if not any(is_missing(r[k]) for k in keys)]


def drop_missing(rows, keys, kind):
    """Rows complete in every key, plus a warning naming the rows that are not."""
    kept = having(rows, *keys)
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
        # A row with no cohort value counts toward neither the levels nor the
        # row counts; no_missing below still sees every filtered row.
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


def scoped(rows, unit, scope):
    """Rows whose unit value is qualified by its parents' values.

    With local labels (`derivation` 1 under line A and under line B are two
    derivations), a unit's identity is the full ancestor path. A row missing
    any scope value keeps no unit value, so it is left out like any row
    missing its unit.
    """
    if not scope:
        return rows
    out = []
    for r in rows:
        r = dict(r)
        parts = [r[c] for c in scope] + [r[unit]]
        # A JSON list, not a joined string: ("A/B", "C") and ("A", "B/C")
        # stay two identities.
        r[unit] = "" if any(is_missing(v) for v in parts) else json.dumps(parts)
        out.append(r)
    return out


def check_unit(check, columns, rows):
    unit = need(check, "unit")
    group = need(check, "group")
    within = as_list(check.get("within"))
    rows = scoped(rows, unit, as_list(check.get("unit_scope")))
    _, alerts = drop_missing(rows, [unit, group] + within, "unit_of_replication")
    # Repeats need every key; membership needs only unit and group; the groups
    # held to the minimum need only group (a group with no unit has 0 units).
    # On a sample ledger (`rows: samples`) several rows per unit are the
    # point (samples nested in a unit), so only membership and counts apply.
    cells = Counter((r[group],) + tuple(r[w] for w in within) + (r[unit],)
                    for r in having(rows, unit, group, *within))
    repeats = [] if check.get("rows") == "samples" else \
        sorted(key for key, n in cells.items() if n > 1)
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
    units = {r[group]: set() for r in having(rows, group)}
    for r in having(rows, unit, group):
        units[r[group]].add(r[unit])
    minimum = int(check.get("min_units_per_group", 2))
    small = sorted((g, len(u)) for g, u in units.items() if len(u) < minimum)
    if small:
        alerts.append(alert(
            "unit_of_replication", "groups with fewer distinct {} than required".format(unit), True,
            expected=">= {} per {}".format(minimum, group),
            observed=", ".join("{}={}".format(g, n) for g, n in small)))
    spans = Counter(u for members in units.values() for u in members)
    shared = sorted(u for u, n in spans.items() if n > 1)
    if shared and not check.get("units_may_span_groups", False):
        alerts.append(alert(
            "unit_of_replication", "some {} appear in more than one {} level".format(unit, group), True,
            expected="each {} in one {}".format(unit, group),
            observed="{} shared units".format(len(shared)), evidence=shared))
    return alerts


def distinct_levels(values):
    """Listed levels as the text a table compares them as, first spelling
    kept, duplicates dropped."""
    out = []
    for value in as_list(values):
        if str(value) not in out:
            out.append(str(value))
    return out


def check_pairing(check, columns, rows):
    unit = need(check, "unit")
    within = need(check, "within")
    rows = scoped(rows, unit, as_list(check.get("unit_scope")))
    _, alerts = drop_missing(rows, [unit, within], "pairing")
    # The units come from every row with a unit, the levels (when the contract
    # does not list them) from every row with a level: a unit seen at no level
    # is incomplete, and a level seen with no unit is still a level.
    observed_levels = sorted({r[within] for r in having(rows, within)})
    # Listed levels compare as text, like table values: "28" and 28 are one
    # level, and a level listed twice is still one side of the pairing.
    listed = distinct_levels(check.get("levels"))
    levels = listed or observed_levels
    # How many distinct sides the pairing must have. Only an explicit
    # `min_levels` sets a minimum (ledger contracts set it to 2). Without it,
    # any `levels` list keeps its 0.9.7 behavior, where every unit seen at
    # the listed levels passes: `levels: ["baseline"]` and a list that
    # collapses to one level (`["control", "control"]`) alike.
    minimum = check.get("min_levels") or 0
    if check.get("exact_levels") and check.get("levels"):
        extra = [lvl for lvl in observed_levels if lvl not in levels]
        absent = [lvl for lvl in levels if lvl not in observed_levels]
        if extra or absent:
            # The pairing licenses reuse across exactly these levels: any
            # other level would reuse units nothing verified, and a missing
            # one leaves the pairing unchecked.
            alerts.append(alert(
                "pairing", "levels of '{}' differ from the declared pairing levels".format(within),
                True, expected="exactly " + ", ".join(levels),
                observed="; ".join(filter(None, ["extra: " + ", ".join(extra) if extra else "",
                                                 "absent: " + ", ".join(absent) if absent else ""]))))
            return alerts
    seen = {r[unit]: set() for r in having(rows, unit)}
    if seen and len(levels) < minimum:
        # A pairing with one level pairs nothing: the other level's rows are
        # missing (an export left out), so the design cannot be checked.
        alerts.append(alert(
            "pairing", "fewer than {} distinct levels of '{}'; a pairing needs both sides".format(
                minimum, within),
            True, expected=">= {} distinct levels".format(minimum),
            observed=", ".join(levels) or "none"))
        return alerts
    if not seen or not levels:
        return alerts
    for r in having(rows, unit, within):
        seen[r[unit]].add(r[within])
    incomplete = sorted((u, sorted(set(levels) - s)) for u, s in seen.items() if set(levels) - s)
    if incomplete:
        alerts.append(alert(
            "pairing", "units are missing a level of '{}'".format(within), True,
            expected="every {} at {}".format(unit, ", ".join(levels)),
            observed="{} of {} units incomplete".format(len(incomplete), len(seen)),
            evidence=["{} lacks {}".format(u, ", ".join(m)) for u, m in incomplete]))
    return alerts


def _rank(matrix):
    """Rank of a small matrix, exactly, by elimination over fractions."""
    from fractions import Fraction

    rows = [[Fraction(v) for v in row] for row in matrix]
    rank, cols = 0, len(rows[0]) if rows else 0
    for col in range(cols):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][col] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for r in range(len(rows)):
            if r != rank and rows[r][col] != 0:
                factor = rows[r][col] / rows[rank][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[rank])]
        rank += 1
    return rank


def check_estimability(check, columns, rows):
    """Whether each contrast level stays estimable when every declared batch
    column is adjusted for at once.

    Per-column checks can all pass while the batches jointly determine the
    group: controls at (date 1, run 1) and treated at (date 2, run 1) or
    (date 1, run 2) make treatment the sum of the date-2 and run-2
    indicators. The design is the distinct (contrast, batches...) cells; a
    contrast level is estimable when its indicator adds rank beyond the
    intercept, the other levels, and all batch indicators.
    """
    batches = need(check, "batch")
    contrast = need(check, "contrast")
    scopes = check.get("batch_scopes") or {}
    for column in batches:
        rows = scoped(rows, column, as_list(scopes.get(column)))
    kept, alerts = drop_missing(rows, [contrast] + batches, "batch_estimability")
    present = sorted({r[contrast] for r in having(rows, contrast)})
    cells = sorted({(r[contrast],) + tuple(r[b] for b in batches) for r in kept})
    levels = sorted({c[0] for c in cells})
    lost = [lvl for lvl in present if lvl not in levels]
    if lost:
        # A model adjusting for every batch drops rows missing any of them;
        # a level with no complete row cannot be estimated at all.
        alerts.append(alert(
            "batch_estimability",
            "{} has no row with every batch column ({}), so a model adjusting for "
            "all of them cannot estimate it".format(", ".join(lost), ", ".join(batches)),
            True, expected="each level of {} with complete batch values".format(contrast),
            observed="lost: " + ", ".join(lost)))
        return alerts
    if len(levels) < 2:
        return alerts
    batch_levels = [sorted({c[i + 1] for c in cells}) for i in range(len(batches))]

    def design(drop_level=None):
        matrix = []
        for cell in cells:
            row = [1]
            row += [1 if cell[0] == lvl else 0 for lvl in levels[1:] if lvl != drop_level]
            for i, values in enumerate(batch_levels):
                row += [1 if cell[i + 1] == v else 0 for v in values[1:]]
            matrix.append(row)
        return matrix

    full = _rank(design())
    lost = [lvl for lvl in levels[1:] if _rank(design(drop_level=lvl)) == full]
    if lost:
        alerts.append(alert(
            "batch_estimability",
            "adjusting for {} together, the contrast of {} against {} cannot be "
            "estimated: the batches jointly determine it".format(
                ", ".join(batches), ", ".join(lost), levels[0]), True,
            expected="each level of {} estimable given all batch columns".format(contrast),
            observed="not estimable: " + ", ".join(lost),
            evidence=["{}={}: {}".format(contrast, c[0], ", ".join(
                "{}={}".format(b, v) for b, v in zip(batches, c[1:]))) for c in cells][:5]))
    return alerts


class _Echelon:
    """An exact row echelon form over the rationals, sparse and fraction-free.

    Rows are {column: integer}. Each added row is reduced against the pivots
    in the order they were made, so membership of a vector in the row space
    is one reduction: it is in the row space when nothing is left.
    """

    def __init__(self, prefer=None, budget=None):
        self.prefer = prefer or (lambda column: 0)
        self.pivots, self.order = {}, {}
        self.budget = budget  # an _AdvisoryBudget, or None for no limit

    class TooLarge(Exception):
        pass

    def reduce(self, row):
        import heapq
        from math import gcd

        row = {k: v for k, v in row.items() if v}
        heap = [(self.order[k], k) for k in row if k in self.pivots]
        heapq.heapify(heap)
        while heap:
            _, column = heapq.heappop(heap)
            a = row.get(column)
            if not a:
                continue
            pivot = self.pivots[column]
            if self.budget is not None:
                self.budget.spend(len(pivot) + len(row))
            b = pivot[column]
            out = {k: v * b for k, v in row.items()}
            for k, v in pivot.items():
                value = out.get(k, 0) - a * v
                if value:
                    if k not in out and k in self.pivots:
                        heapq.heappush(heap, (self.order[k], k))
                    out[k] = value
                else:
                    out.pop(k, None)
            common = 0
            for v in out.values():
                common = gcd(common, v)
                if common == 1:
                    break
            row = {k: v // common for k, v in out.items()} if common > 1 else out
        return row

    def add(self, row):
        row = self.reduce(row)
        if row:
            column = min(row, key=self.prefer)
            if row[column] < 0:
                row = {k: -v for k, v in row.items()}
            self.order[column] = len(self.order)
            self.pivots[column] = row

    def spans(self, row):
        return not self.reduce(row)


# Entry operations one paired_batch check may spend on elimination (about a
# second); past it the check reports that it did not run.
PAIRED_BATCH_BUDGET = 5000000
# Seconds all advisory checks in one run may take together, row preparation
# included; checks reached after it runs out are skipped with a warning.
ADVISORY_SECONDS = 1.0


class _AdvisoryBudget:
    """Work and wall-clock shared by the advisory checks of one run."""

    def __init__(self, ops=None, seconds=None):
        import time

        self.clock = time.monotonic
        self.ops = PAIRED_BATCH_BUDGET if ops is None else ops
        self.deadline = self.clock() + (ADVISORY_SECONDS if seconds is None else seconds)
        self.calls = 0

    def spend(self, n=1):
        self.ops -= n
        self.calls += 1
        if self.ops < 0 or (self.calls % 256 == 0 and self.clock() > self.deadline):
            raise _Echelon.TooLarge()

    def exhausted(self):
        return self.ops < 0 or self.clock() > self.deadline

UNKNOWN_GROUP = object()  # a row whose group is unknown: its own nuisance cells


def check_paired_batch(check, columns, rows):
    """See _paired_batch. Advisory: running out of budget anywhere (row
    preparation, elimination, contrast tests) discards partial results and
    reports that the check did not run; it never raises."""
    budget = check.get("_budget") or _AdvisoryBudget()
    try:
        return _paired_batch(check, rows, budget)
    except _Echelon.TooLarge:
        return [alert(
            "paired_batch", "the design is too large to check within the time advisory checks "
            "allow, so batch against the {} contrast was not checked".format(
                _clip(check.get("within"))),
            False, observed="{} rows, batch columns: {}".format(
                len(rows), ", ".join(_clip(b) for b in as_list(check.get("batch")))))]


def _paired_batch(check, rows, budget):
    """Whether the within-unit contrasts of a paired design survive batch.

    One design for the whole check: rows with a known unit and `within`
    value; a cell parameter for every observed (group, within) pair, every
    within level included whether `levels` declares it or not (a level
    outside the contrast can still identify a batch); the unit absorbed by
    differencing each unit's cells against its first; and one indicator per
    batch level. Cell parameters carry the group main effect and the
    group-by-within interaction, so a group that varies inside a unit (a
    donor's control and knockout lines) is modelled. A missing group is
    filled from the unit when the unit has one group, else the row keeps
    its own nuisance cells. Batch identities are scoped apart from the raw
    within value, so a column that is both keeps its levels.

    Contrasts: each `levels` level (default: every level) against the first
    one observed, within each group. A contrast blocks when it is estimable
    from the pairs before rows missing a batch are dropped but not after
    (missing values erased it), or estimable after the drop without batch
    but not with it (confounded). A contrast the pairs never estimate is
    left to the pairing check.
    """
    unit = need(check, "unit")
    within = need(check, "within")
    batches = as_list(need(check, "batch"))
    group = check.get("group")
    if group == within:
        group = None
    # Names and values as messages show them: clipped, so a pathological
    # column name or label cannot make the report enormous.
    unit_name, within_name = _clip(unit), _clip(within)
    batch_names = ", ".join(_clip(b) for b in batches)
    scopes = check.get("batch_scopes") or {}
    unit_ids = [r[unit] for r in scoped(rows, unit, as_list(check.get("unit_scope")))]
    batch_ids = [[r[b] for r in scoped(rows, b, as_list(scopes.get(b)))] for b in batches]
    # One record per row: (unit, group, within, batch identities), with the
    # raw within value whatever the batch scoping did to its column.
    budget.spend(len(rows) * (2 + len(batches)))
    records = [(unit_ids[i], r[group] if group else None, r[within],
                tuple(ids[i] for ids in batch_ids)) for i, r in enumerate(rows)]
    alerts = []
    if group:
        known = defaultdict(set)
        for u, g, _, _ in records:
            if not is_missing(u) and not is_missing(g):
                known[u].add(g)
        filled = []
        for u, g, w, b in records:
            if is_missing(g):
                g = next(iter(known[u])) if len(known.get(u, ())) == 1 else UNKNOWN_GROUP
            filled.append((u, g, w, b))
        unknown = sum(1 for u, g, w, _ in filled if g is UNKNOWN_GROUP and
                      not is_missing(u) and not is_missing(w))
        if unknown:
            alerts.append(alert(
                "paired_batch", "{} rows have no '{}' and their {} has no single one to fill it "
                "from; they are modelled as an unknown group".format(unknown, _clip(group), unit_name),
                False,
                observed="{} of {} rows".format(unknown, len(rows))))
        records = filled
    paired_rows = [r for r in records if not is_missing(r[0]) and not is_missing(r[2])]
    batched = [r for r in paired_rows if not any(is_missing(b) for b in r[3])]
    if len(batched) < len(paired_rows):
        alerts.append(alert(
            "paired_batch", "{} rows have a missing value in {}; a model adjusting for batch "
            "drops them (checked below for lost pairs)".format(len(paired_rows) - len(batched),
                                                    batch_names), False,
            observed="{} of {} rows".format(len(paired_rows) - len(batched), len(paired_rows))))
    contrasts = []
    listed = distinct_levels(check.get("levels"))
    observed = defaultdict(set)
    for _, g, w, _ in paired_rows:
        observed[g].add(w)
    for g in sorted((g for g in observed if g is not UNKNOWN_GROUP), key=str):
        levels = [lvl for lvl in (listed or sorted(observed[g])) if lvl in observed[g]]
        contrasts += [(g, lvl, levels[0]) for lvl in levels[1:]]
    if not contrasts:
        return alerts

    def key(g):
        # Tagged, so a recorded label (even "?") never meets the unknown group.
        return ("unknown",) if g is UNKNOWN_GROUP else ("label", g)

    def echelon(records, with_batch):
        # Batch columns pivot first; ties go to the first key in the row.
        ech = _Echelon(prefer=lambda column: 0 if column[0] == "b" else 1, budget=budget)
        first, seen = {}, set()
        # Repeated samples of one cell (aliquots, images) add nothing; the
        # rest keep the table's order.
        for u, g, w, b in dict.fromkeys((u, key(g), w, b if with_batch else ())
                                        for u, g, w, b in records):
            vector = {("c", g, w): 1}
            vector.update((("b", i, v), 1) for i, v in enumerate(b))
            if u not in first:
                first[u] = vector
                continue
            difference = dict(vector)
            for k, v in first[u].items():
                difference[k] = difference.get(k, 0) - v
            signature = frozenset((k, v) for k, v in difference.items() if v)
            if signature and signature not in seen:
                seen.add(signature)
                ech.add(difference)
        return ech

    def vector(contrast):
        g, lvl, ref = contrast
        return {("c", key(g), lvl): 1, ("c", key(g), ref): -1}

    before, after = echelon(paired_rows, False), echelon(batched, False)
    adjusted = echelon(batched, True)
    erased, confounded = [], []
    for contrast in contrasts:
        if not before.spans(vector(contrast)):
            continue  # no pair estimates it: the pairing check's to report
        if not after.spans(vector(contrast)):
            erased.append(contrast)
        elif not adjusted.spans(vector(contrast)):
            confounded.append(contrast)

    def label(contrasts):
        shown = "; ".join("{} vs {}".format(_side_label(group, (g, lvl)), _clip(ref))
                          for g, lvl, ref in contrasts[:5])
        return shown + ("; and {} more".format(len(contrasts) - 5) if len(contrasts) > 5 else "")

    if erased:
        alerts.append(alert(
            "paired_batch",
            "missing {} values leave no {} with both sides of the {} contrast, so a model "
            "adjusting for batch has no pair to estimate it from".format(
                batch_names, unit_name, within_name), False,
            expected="pairs with batch values on both sides",
            observed="no complete pair: " + label(erased)))
    if confounded:
        involved = {(g, lvl) for g, lvl, _ in confounded} | {(g, ref) for g, _, ref in confounded}
        by_unit = defaultdict(set)
        for u, g, w, b in batched:
            if (g, w) in involved:
                by_unit[u].add("{}@{}".format(_clip(w), _clip("/".join(b))))
        alerts.append(alert(
            "paired_batch",
            "within {}, the {} contrast cannot be separated from {}: the pairs are split "
            "across batches in step with it".format(unit_name, within_name, batch_names), False,
            expected="each {} contrast estimable within {} after adjusting for {}".format(
                within_name, unit_name, batch_names),
            observed="not estimable: " + label(confounded),
            evidence=["{}={}: {}".format(unit_name, _clip(u), _clip(", ".join(sorted(v))))
                      for u, v in sorted(by_unit.items())[:5]]))
    share = check.get("max_share")
    if share is None:
        return alerts
    # Estimable is not the same as well supported: eight donors with base in
    # run 1 and week 4 in run 2, and two with both visits in run 3, leave the
    # visit effect resting on two donors. Among the units split across
    # batches, the most common batch on one side is the step; it warns when
    # that one batch carries one side for more than `max_share` of the
    # paired units. Counterbalanced splits (half each way, or a rotation
    # through three runs) and randomized runs spread both sides out.
    budget.spend(len(batched))
    batch_sets = defaultdict(lambda: defaultdict(set))  # (group, within) -> unit -> batches
    for u, g, w, b in batched:
        batch_sets[(g, w)][u].add(b)
    reported = set(confounded) | set(erased)
    hits = []
    for contrast in contrasts:
        g, lvl, ref = contrast
        budget.spend()
        if contrast in reported:
            continue
        on_ref, on_lvl = batch_sets.get((g, ref), {}), batch_sets.get((g, lvl), {})
        budget.spend(len(on_ref))
        pairs = [(on_ref[u], on_lvl[u]) for u in on_ref if u in on_lvl]
        steps = Counter()
        for a, b in pairs:
            if not a & b:
                steps.update((ref, t) for t in a)
                steps.update((lvl, t) for t in b)
        if not steps:
            continue
        (level, batch), count = max(steps.items(), key=lambda item: item[1])
        if count / len(pairs) > share:
            hits.append((contrast, count, len(pairs), level, batch))
    if hits:
        # One warning per check: a count and at most five examples.
        examples = ["{} vs {}: {} of {} units have {} in {} and the other side in another "
                    "batch".format(_side_label(group, (g, lvl)), _clip(ref), count, n, _clip(level),
                                   _clip("/".join(batch)))
                    for (g, lvl, ref), count, n, level, batch in hits[:5]]
        alerts.append(alert(
            "paired_batch",
            "within {}, {} {} contrast{} rest{} mostly on pairs split across {} in step with "
            "{}".format(unit_name, len(hits), within_name, "s" if len(hits) > 1 else "",
                        "" if len(hits) > 1 else "s", batch_names,
                        "them" if len(hits) > 1 else "it"), False,
            expected="at most {} of {} with one side in the same batch and the other "
                     "elsewhere".format(percent(share), unit_name),
            observed="; ".join(examples) +
                     ("; and {} more".format(len(hits) - 5) if len(hits) > 5 else "")))
    return alerts


def _side_label(group, side):
    if group:
        return "{}={}: {}".format(_clip(group), _clip(side[0]), _clip(side[1]))
    return _clip(side[1])


def _clip(value, limit=120):
    """A name or value as a message shows it: at most `limit` characters."""
    text = str(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def check_unique(check, columns, rows):
    cols = need(check, "columns")
    alerts = []
    missing = [r for r in rows if any(is_missing(r[c]) for c in cols)]
    if missing:
        alerts.append(alert(
            "unique", "rows have no value in {}".format(", ".join(cols)), True,
            expected="a value in every row", observed="{} of {} rows".format(len(missing), len(rows))))
    counts = Counter(tuple(r[c] for c in cols) for r in having(rows, *cols))
    repeated = sorted(key for key, n in counts.items() if n > 1)
    if repeated:
        alerts.append(alert(
            "unique", "values of {} repeat; each row must be one distinct sample".format(
                ", ".join(cols)), True,
            expected="each value once", observed="{} repeated values".format(len(repeated)),
            evidence=[", ".join(key) + " ({} rows)".format(counts[key]) for key in repeated]))
    return alerts


def check_nesting(check, columns, rows):
    """Every finer level sits under exactly one value of the level above.

    With global labels a dish called "3" under two preparations is two dishes
    sharing a name, which every later unit count would merge; it blocks. With
    `local_labels`, labels are qualified by their parents, so the nesting
    holds by construction and only missing values are reported.
    """
    levels = need(check, "levels")
    local = bool(check.get("local_labels", False))
    alerts = []
    for coarse, fine in zip(levels, levels[1:]):
        kept, warnings = drop_missing(rows, [coarse, fine], "nesting")
        alerts.extend(warnings)
        if local:
            continue
        parents = defaultdict(set)
        for r in kept:
            parents[r[fine]].add(r[coarse])
        crossed = sorted((f, sorted(p)) for f, p in parents.items() if len(p) > 1)
        if crossed:
            alerts.append(alert(
                "nesting", "'{}' values sit under more than one '{}'".format(fine, coarse), True,
                expected="each {} under one {} (or declare local_labels)".format(fine, coarse),
                observed="{} {} values crossed".format(len(crossed), fine),
                evidence=["{}={} under {}={}".format(fine, f, coarse, ", ".join(p))
                          for f, p in crossed]))
    return alerts


def check_batch(check, columns, rows):
    batch = need(check, "batch")
    contrast = need(check, "contrast")
    rows = scoped(rows, batch, as_list(check.get("batch_scope")))
    batched, alerts = drop_missing(rows, [batch, contrast], "batch_confounding")
    observed = sorted({r[contrast] for r in having(rows, contrast)})
    if len(observed) < 2:
        return alerts + [alert("batch_confounding", "the contrast has fewer than two levels", True,
                               observed=", ".join(observed) or "none")]
    # A level seen only with a missing batch drops out of the batch table, so
    # its confounding with batch cannot be checked at all.
    levels = sorted({r[contrast] for r in batched})
    unbatched = sorted(set(observed) - set(levels))
    if unbatched:
        alerts.append(alert(
            "batch_confounding", "contrast levels with no known batch; their batch confounding cannot "
            "be checked", True, expected="every {} level has rows with a {}".format(contrast, batch),
            observed=", ".join(unbatched)))
    if not batched:
        return alerts
    table = defaultdict(Counter)
    for r in batched:
        table[r[batch]][r[contrast]] += 1
    evidence = ["{}: {}".format(b, ", ".join("{}={}".format(l, c[l]) for l in levels if c[l]))
                for b, c in sorted(table.items())]
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
        totals = Counter(r[contrast] for r in batched)
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
        reject_unknown(at, rule, FILTER_KEYS)
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


NOTES = ("note", "reason")  # free text, recorded with the contract and never read
CONTRACT_KEYS = {"schema", "table", "filters", "checks"} | set(NOTES)
CHECK_KEYS = {"kind", "table", "filters", "label"} | set(NOTES)
KIND_KEYS = {
    "schema": {"columns"},
    "cohort": {"column", "levels", "min_rows", "rows", "no_missing"},
    "unit_of_replication": {"unit", "group", "within", "min_units_per_group", "units_may_span_groups",
                            "rows", "unit_scope"},
    "pairing": {"unit", "within", "levels", "unit_scope", "exact_levels", "min_levels"},
    "batch_confounding": {"batch", "contrast", "max_share", "batch_scope"},
    "batch_estimability": {"batch", "contrast", "batch_scopes"},
    "paired_batch": {"unit", "within", "batch", "group", "unit_scope", "batch_scopes", "max_share",
                     "levels"},
    "unique": {"columns"},
    "nesting": {"levels", "local_labels"},
}
FILTER_KEYS = {"column", "equals", "in", "not_in"}


def reject_unknown(where, value, known):
    """An unread key is an assumption the contract states but nothing checks."""
    unknown = sorted(set(value) - known)
    if unknown:
        fail(where, "has unknown key '{}' (a misspelled key would silently drop an assumption); "
                    "known keys: {}".format(unknown[0], ", ".join(sorted(known))))


def reject_duplicate_keys(pairs):
    """json object_pairs_hook: a repeated key would silently keep only its last value."""
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ContractError("duplicate key '{}' in the contract".format(key))
        seen[key] = value
    return seen


def validate_contract(contract, public=False):
    """Contract syntax the checks rely on; a bad value is a usage error, never a pass.

    Every type a check dereferences is constrained here, before any table is
    read; unknown keys are left alone.
    """
    if not isinstance(contract, dict):
        raise ContractError("contract must be a JSON object")
    if contract.get("schema") not in ACCEPTED_SCHEMAS:
        raise ContractError("contract schema must be " + SCHEMA)
    reject_unknown("contract", contract, CONTRACT_KEYS)
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
        if kind not in CHECKS or (public and kind in INTERNAL_KINDS):
            raise ContractError("unknown check kind at {}: {}{}".format(
                where, json.dumps(kind), " (generated by --ledger only in this release)"
                if kind in INTERNAL_KINDS else ""))
        where = "{} ({})".format(where, kind)
        reject_unknown(where, check, CHECK_KEYS | KIND_KEYS[kind])
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
            if "rows" in check and check["rows"] != "samples":
                fail(where, "'rows' may only be \"samples\" (a sample ledger): {}".format(
                    json.dumps(check["rows"])))
            validate_columns(where, check, "unit_scope", allow_single=True)
        elif kind == "pairing":
            validate_column(where, check, "unit", required=True)
            validate_column(where, check, "within", required=True)
            validate_levels(where, check)
            validate_columns(where, check, "unit_scope", allow_single=True)
            if "exact_levels" in check and not isinstance(check["exact_levels"], bool):
                fail(where, "'exact_levels' must be true or false")
            if check.get("exact_levels") and "levels" not in check:
                fail(where, "'exact_levels' needs 'levels'")
            validate_count(where, check, "min_levels", 2)
        elif kind == "unique":
            validate_columns(where, check, "columns", required=True)
        elif kind in ("batch_estimability", "paired_batch"):
            validate_columns(where, check, "batch", required=True,
                             allow_single=kind == "paired_batch")
            scopes = check.get("batch_scopes", {})
            if not isinstance(scopes, dict) or not all(
                    isinstance(k, str) and isinstance(v, list) and all(isinstance(c, str) for c in v)
                    for k, v in scopes.items()):
                fail(where, "'batch_scopes' must map batch columns to lists of columns")
            if kind == "paired_batch":
                validate_share(where, check)
                validate_levels(where, check)
                validate_column(where, check, "unit", required=True)
                validate_column(where, check, "within", required=True)
                validate_column(where, check, "group")
                validate_columns(where, check, "unit_scope", allow_single=True)
            elif len(as_list(check.get("batch"))) < 2:
                fail(where, "'batch' needs at least two columns (one column is batch_confounding)")
            if kind == "batch_estimability":
                validate_column(where, check, "contrast", required=True)
        elif kind == "nesting":
            validate_columns(where, check, "levels", required=True)
            if len(as_list(check.get("levels"))) < 2:
                fail(where, "'levels' needs at least two columns, coarse to fine")
            if "local_labels" in check and not isinstance(check["local_labels"], bool):
                fail(where, "'local_labels' must be true or false: {}".format(
                    json.dumps(check["local_labels"])))
        elif kind == "batch_confounding":
            validate_column(where, check, "batch", required=True)
            validate_column(where, check, "contrast", required=True)
            validate_columns(where, check, "batch_scope", allow_single=True)
            validate_share(where, check)


def validate_share(where, check):
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
    "unique": check_unique,
    "nesting": check_nesting,
    "batch_estimability": check_estimability,
    "paired_batch": check_paired_batch,
}


def referenced_columns(check):
    keys = ["column", "unit", "group", "batch", "contrast"]
    cols = [check[k] for k in keys if k in check and isinstance(check[k], str)]
    cols += as_list(check.get("within")) + as_list(check.get("no_missing"))
    cols += as_list(check.get("unit_scope")) + as_list(check.get("batch_scope"))
    if check.get("kind") in ("batch_estimability", "paired_batch"):
        cols += as_list(check.get("batch"))
        for scope in (check.get("batch_scopes") or {}).values():
            cols += as_list(scope)
    if check.get("kind") in ("unique", "nesting"):
        cols += as_list(check.get("columns")) + as_list(check.get("levels"))
    return cols


def filter_columns(filters):
    return [rule.get("column") for rule in filters]


RELAXATIONS = ("units_may_span_groups",)
# Kinds that warn and never block in this release, until proven on real
# ledgers: paired_batch.
ADVISORY_KINDS = ("paired_batch",)
# Kinds only ledger mode generates: a hand-written --contract may not use
# them, so v1 contracts stay portable across checker versions.
INTERNAL_KINDS = ("paired_batch",)


def advisory(alerts):
    """The same findings as warnings: reported, never blocking."""
    out = []
    for a in alerts:
        if a["blocking"]:
            a = dict(a, blocking=False, severity="warning")
        out.append(a)
    return out


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
    budget = None  # shared by the advisory checks, started at the first one
    for index, check, kind, table, filters, path, named in plan:
        label = "checks[{}] {}".format(index, check.get("label", kind))
        found = []
        if kind in ADVISORY_KINDS:
            budget = budget or _AdvisoryBudget()
            if budget.exhausted():
                skipped = alert(kind, "skipped: the time this run allows advisory checks is used "
                                      "up", False)
                skipped.update(check=label, table=table, rows_checked=0)
                summaries.append({"check": label, "kind": kind, "table": table, "rows_checked": 0,
                                  "status": "warn", "relaxations": []})
                alerts.append(skipped)
                continue
            check = dict(check, _budget=budget)
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
            # Filter columns are scanned on every row, since a variant spelling
            # silently escapes the filter; the check's own columns only on the
            # rows the filters keep, since excluded rows are outside the check.
            found.extend(spelling_variants(rows, sorted(set(filter_columns(filters))), kind))
        if not found:
            rows, filter_warnings = apply_filters(rows, filters, columns, kind)
            found.extend(filter_warnings)
            rows_checked = len(rows)
            identity = [check[k] for k in ("column", "unit", "group", "batch", "contrast")
                        if k in check and isinstance(check[k], str)]
            if kind in ("batch_estimability", "paired_batch"):
                identity += as_list(check.get("batch"))
            identity += as_list(check.get("within")) + as_list(check.get("unit_scope"))
            if kind in ("unique", "nesting"):
                identity += as_list(check.get("columns")) + as_list(check.get("levels"))
            found.extend(spelling_variants(rows, sorted(set(identity)), kind))
            # Parents that qualify a local batch label (of the batch columns
            # this check uses) are part of its identity. Checked from this
            # release, so a variant spelling warns rather than blocking a
            # contract that passed before.
            parents = as_list(check.get("batch_scope"))
            scopes = check.get("batch_scopes") or {}
            for column in as_list(check.get("batch")):
                parents += as_list(scopes.get(column))
            found.extend(advisory(spelling_variants(rows, sorted(set(parents) - set(identity)), kind)))
            if not rows:
                found.append(alert(kind, "no rows left after filters; the check would pass vacuously", True,
                                   expected=">= 1 row",
                                   observed="0 of {} rows".format(len(cache[path][1])),
                                   evidence=[json.dumps(f, sort_keys=True) for f in filters]))
            if rows:
                found.extend(CHECKS[kind](check, columns, rows))
            if kind in ADVISORY_KINDS:
                found = [a for a in found if a["kind"] != kind] + \
                    advisory([a for a in found if a["kind"] == kind])
        for record in found:
            record["check"] = label
            record["table"] = table
            record["rows_checked"] = rows_checked
        status = "block" if any(a["blocking"] for a in found) else \
            "gap" if any(a["severity"] == "gap" for a in found) else ("warn" if found else "pass")
        summary = {"check": label, "kind": kind, "table": table, "rows_checked": rows_checked,
                   "status": status, "relaxations": [k for k in RELAXATIONS if check.get(k)]}
        if kind == "batch_confounding" and rows_checked:
            summary["cross_table"] = cross_table(
                check, scoped(rows, check["batch"], as_list(check.get("batch_scope"))))
        summaries.append(summary)
        alerts.extend(found)
    return summaries, alerts


LEDGER_SCHEMA = "mycelium.sample_ledger.v1"
LEDGER_KEYS = {"schema", "file", "sample_id", "group", "unit_of_replication", "nesting",
               "local_labels", "batch", "pairing"} | set(NOTES)


def ledger_contract(spec, spec_dir):
    """The contract a sample ledger implies.

    The ledger (one row per sample) is checked for what holds at ingest:
    unique sample ids, the declared nesting of replication levels, at least
    two distinct replication units per group (`rows: samples`, since several
    samples per unit are expected), each crossed batch variable against the
    group, and the declared pairing. Whether rows are independent units is a
    question about the modeled table, so the emitted contract tells the
    analysis to point its replication check there.
    """
    if not isinstance(spec, dict):
        raise ContractError("the ledger spec must be a JSON object")
    if spec.get("schema") != LEDGER_SCHEMA:
        raise ContractError("ledger schema must be " + LEDGER_SCHEMA)
    reject_unknown("ledger", spec, LEDGER_KEYS)
    for key in ("file", "sample_id", "group", "unit_of_replication"):
        if not isinstance(spec.get(key), str) or not spec[key]:
            raise ContractError("ledger '{}' must be a non-empty string".format(key))
    nesting = spec.get("nesting") or []
    batch = spec.get("batch") or []
    for key, value in (("nesting", nesting), ("batch", batch)):
        if not isinstance(value, list) or not all(isinstance(c, str) and c for c in value):
            raise ContractError("ledger '{}' must be a list of column names".format(key))
    unit = spec["unit_of_replication"]
    if nesting and unit not in nesting:
        raise ContractError("ledger unit_of_replication '{}' must be one of the nesting levels".format(unit))
    local = spec.get("local_labels", False)
    if not isinstance(local, bool):
        raise ContractError("ledger 'local_labels' must be true or false")
    if nesting:
        at_or_below = nesting[nesting.index(unit):]
        clash = [c for c in batch if c in at_or_below]
        if clash:
            raise ContractError(
                "ledger batch column '{}' is a replication level at or below the unit of "
                "replication, so it is nested in the group by construction; list a crossed "
                "batch variable (a calendar date, a sequencing run) in 'batch' and keep "
                "nested preparation instances in 'nesting'".format(clash[0]))
    group = spec["group"]
    scope = nesting[:nesting.index(unit)] if (local and nesting) else []
    pairing = spec.get("pairing")
    if pairing is not None:
        if not isinstance(pairing, dict) or set(pairing) - {"unit", "within", "levels"} or \
                not all(isinstance(pairing.get(k), str) and pairing[k] for k in ("unit", "within")):
            raise ContractError("ledger 'pairing' must be {\"unit\": ..., \"within\": ..., "
                                "\"levels\": [...]} or null")
        if "levels" in pairing and (not isinstance(pairing["levels"], list) or
                                    not all(is_scalar(v) for v in pairing["levels"]) or
                                    len(distinct_levels(pairing["levels"])) < 2):
            # Distinct as the table compares them: ["A", "A"] is one level.
            raise ContractError("ledger pairing 'levels' must list at least two distinct "
                                "expected levels")
    table = spec["file"] if os.path.isabs(spec["file"]) else os.path.join(spec_dir, spec["file"])
    named = [spec["sample_id"], group] + nesting + batch
    if unit not in named:
        named.append(unit)
    checks = [
        {"kind": "schema", "columns": sorted(set(named), key=named.index)},
        {"kind": "unique", "columns": [spec["sample_id"]]},
    ]
    if len(nesting) >= 2:
        checks.append({"kind": "nesting", "levels": nesting, "local_labels": local})
    unit_check = {"kind": "unit_of_replication", "unit": unit, "group": group, "rows": "samples",
                  "note": "on the ledger: distinct units per group. In the analysis contract, "
                          "point this check (dropping rows), the pairing check and the batch "
                          "checks at the modeled table, so rows the model drops are counted"}
    if scope:
        unit_check["unit_scope"] = scope
    # Cross-group reuse only when the declared pairing verifies that same unit
    # across the group column itself.
    if pairing and pairing["unit"] == unit and pairing["within"] == group:
        unit_check["units_may_span_groups"] = True
    checks.append(unit_check)
    def ancestors(column):
        # Local labels are qualified by the levels above them.
        if local and column in nesting:
            return nesting[:nesting.index(column)]
        return []

    for column in batch:
        batch_check = {"kind": "batch_confounding", "batch": column, "contrast": group}
        if ancestors(column):
            batch_check["batch_scope"] = ancestors(column)
        checks.append(batch_check)
    if len(batch) >= 2:
        joint = {"kind": "batch_estimability", "batch": batch, "contrast": group,
                 "note": "for an analysis that adjusts for all of these batch terms"}
        scopes = {column: ancestors(column) for column in batch if ancestors(column)}
        if scopes:
            joint["batch_scopes"] = scopes
        checks.append(joint)
    if pairing:
        # A declared pairing needs both sides on whatever table it runs on.
        pair_check = {"kind": "pairing", "unit": pairing["unit"], "within": pairing["within"],
                      "min_levels": 2}
        spans = pairing["unit"] == unit and pairing["within"] == group
        if pairing.get("levels"):
            pair_check["levels"] = distinct_levels(pairing["levels"])
            if spans:
                # The pairing licenses units in several groups, so it must
                # hold for exactly the declared groups, on whatever table the
                # pairing check is pointed at (the modeled one included).
                pair_check["exact_levels"] = True
        if ancestors(pairing["unit"]):
            pair_check["unit_scope"] = ancestors(pairing["unit"])
        checks.append(pair_check)
        # Batch against the within-unit contrast: one check per batch column,
        # and all of them together, as for batch against the group above.
        for columns in [[c] for c in batch] + ([batch] if len(batch) >= 2 else []):
            # Blocking stays exact; max_share warns when most pairs are
            # split in step with the contrast, so it rests on a few units.
            paired = {"kind": "paired_batch", "unit": pairing["unit"],
                      "within": pairing["within"], "batch": columns, "max_share": 0.5}
            if pairing["within"] != group:
                paired["group"] = group
            if ancestors(pairing["unit"]):
                paired["unit_scope"] = ancestors(pairing["unit"])
            scopes = {column: ancestors(column) for column in columns if ancestors(column)}
            if scopes:
                paired["batch_scopes"] = scopes
            if pairing.get("levels"):
                paired["levels"] = distinct_levels(pairing["levels"])
            if len(columns) >= 2:
                paired["note"] = "for a paired analysis that adjusts for all of these batch terms"
            checks.append(paired)
    return {"schema": SCHEMA, "table": table, "checks": checks,
            "note": "generated from the sample ledger spec; extend it in the analysis "
                    "contract (filters, the modeled table), never loosen it"}


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


def refuse_input_alias(destination, inputs):
    """Refuse an --emit-contract destination that names an input: the ledger
    spec or its sample table, directly or through a symlink or hard link.
    Writing there would destroy the input the contract describes."""
    dest_real = os.path.realpath(destination)
    for source in inputs:
        same = os.path.realpath(source) == dest_real
        if not same and os.path.exists(destination) and os.path.exists(source):
            try:
                same = os.path.samefile(destination, source)
            except OSError:
                same = False
        if same:
            raise ContractError("--emit-contract {} is the same file as the input {}; choose a "
                                "new path such as analysis/<name>/data_contract.json".format(
                                    destination, source))


def _write_exclusive(path, text, exists):
    """Create `path` only if no file (or link) has that name, then write
    `text` into it and fsync. A partial file left by a failed write is
    removed: it is this call's own file, since the exclusive create proved
    nothing else had the name."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        raise exists
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def write_atomic_json(path, document, replace=True):
    """Write through a temporary file in the destination's directory and
    replace: a failure leaves the old file (or none), never a truncated one.
    With replace=False the file is published only if `path` still does not
    exist (a hard link fails on an existing name), so a file created since
    the caller looked is never clobbered. Without hard links the file is
    created exclusively (O_CREAT|O_EXCL) and written in place: never a
    check-then-replace, which could clobber a file published in between."""
    text = json.dumps(document, indent=2) + "\n"
    # The directory the OS will publish into: resolved without collapsing
    # `..` lexically first (`out/latest/../c.json` under a symlinked
    # `latest` is not out/c.json), so the replace stays in one directory.
    directory = os.path.realpath(os.path.dirname(path) or os.curdir)
    fd, tmp = tempfile.mkstemp(prefix=".{}.".format(os.path.basename(path)), suffix=".tmp",
                               dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        # A replaced file keeps its own mode (a 0600 contract stays 0600);
        # a new one gets what open() would create under the umask, not
        # mkstemp's 0600.
        mode = None
        if replace:
            try:
                existing = os.stat(path)
            except OSError:
                existing = None
            if existing is not None and stat.S_ISREG(existing.st_mode):
                mode = stat.S_IMODE(existing.st_mode)
        if mode is None:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        if replace:
            os.replace(tmp, path)
        else:
            exists = ContractError("--emit-contract {} already exists; nothing was written. Pass "
                                   "--overwrite to replace it, or choose a new path".format(path))
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise exists
            except OSError:
                # A filesystem without hard links: os.replace would clobber a
                # file another process published after any existence check,
                # so create the destination exclusively and write into it.
                _write_exclusive(path, text, exists)
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--contract", help="path to the contract JSON")
    source.add_argument("--ledger", help="path to a sample ledger spec (samples.ledger.json)")
    parser.add_argument("--root", default=".", help="directory relative table paths resolve from")
    parser.add_argument("--emit-contract", default=None,
                        help="with --ledger: also write the generated contract here (a new "
                             "file, unless --overwrite)")
    parser.add_argument("--overwrite", action="store_true",
                        help="with --emit-contract: replace an existing destination contract")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.emit_contract and not args.ledger:
        print("contract error: --emit-contract needs --ledger", file=sys.stderr)
        return 1
    if args.overwrite and not args.emit_contract:
        print("contract error: --overwrite needs --emit-contract", file=sys.stderr)
        return 1
    emitted = None
    try:
        if args.ledger:
            # Paths are resolved with realpath on the joined, un-normalized
            # text, never normpath/abspath first: the OS resolves a symlink
            # before a following `..` (`latest/../x` under `latest ->
            # snapshot/staging` is snapshot/x), and lexical collapsing would
            # name another file than the one read or written.
            ledger_path = os.path.realpath(args.ledger)
            with open(ledger_path, encoding="utf-8") as handle:
                spec = json.load(handle, object_pairs_hook=reject_duplicate_keys)
            # The table is relative to the directory the ledger was named
            # from (a symlinked ledger keeps its link's directory).
            contract = ledger_contract(
                spec, os.path.realpath(os.path.dirname(args.ledger) or os.curdir))
            # One resolved table path for reading, the checks, the alias
            # guard, and the emitted contract.
            table = os.path.realpath(contract["table"])
            contract["table"] = table
            if args.emit_contract:
                emitted = dict(contract)
                # Advisory kinds run in ledger mode but stay out of the v1
                # export, so readers that predate them can replay it.
                emitted["checks"] = [c for c in contract["checks"]
                                     if c["kind"] not in ADVISORY_KINDS]
                if len(emitted["checks"]) < len(contract["checks"]):
                    emitted["note"] = contract["note"] + "; advisory paired_batch checks ran " \
                        "in ledger mode and are left out of this contract"
                root = os.path.realpath(args.root)
                if table.startswith(root.rstrip(os.sep) + os.sep):
                    # Relative to --root, so the contract replays against the
                    # checkout it is run in, not the one it was emitted from.
                    emitted["table"] = os.path.relpath(table, root)
                refuse_input_alias(args.emit_contract, [ledger_path, table])
                if os.path.lexists(args.emit_contract) and not args.overwrite:
                    # The destination may be the analysis's extended contract
                    # (filters, the modeled table); regenerating it from the
                    # ledger would drop that work.
                    raise ContractError(
                        "--emit-contract {} already exists; nothing was written. Pass "
                        "--overwrite to replace it, or choose a new path".format(args.emit_contract))
                validate_contract(emitted, public=True)
        else:
            with open(args.contract, encoding="utf-8") as handle:
                contract = json.load(handle, object_pairs_hook=reject_duplicate_keys)
            validate_contract(contract, public=True)
        # The checks run before anything is published.
        summaries, alerts = run_contract(contract, args.root)
        if emitted is not None:
            if os.path.isfile(table):
                # Written even when the checks block or gap: the analysis
                # extends this contract, and the alerts are reported below.
                write_atomic_json(args.emit_contract, emitted, replace=args.overwrite)
            else:
                print("contract not written: the sample table {} does not exist".format(
                    contract["table"]), file=sys.stderr)
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
