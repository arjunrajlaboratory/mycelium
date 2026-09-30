"""Build the worked HTML report example from report-template.html.

The example is SYNTHETIC: a made-up drug-response experiment whose numbers are
generated below. It exists to show a finished report + slide deck that passes
every gate (scitexlintr, check_html_report.py, sync_html_report.py --check),
and it doubles as the fixture for the template's browser tests.

Regenerate after any template change (tests fail until you do):

    python network/conventions/report-generator/assets/html-example/build_example.py

Writes, relative to this directory:
    outputs/figures/dose_response.svg   static analysis figure
    outputs/growth_counts.json          data behind the interactive figure
    outputs/doubling_by_replicate.csv   data behind the supplement's registered table
    reports/.manifest.json              values, figures, data, terms
    reports/example-report.html         the report (figures/data not yet inlined)

then runs sync_html_report.py so the committed report is fully inlined.
"""

from __future__ import annotations

import hashlib
import json
import re
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "report-template.html"
# The generator runs from the Mycelium plugin checkout, where the sync script
# sits four levels up (network/conventions/report-generator/assets/html-example).
# An installed copy under .living/ is reference material, not a build tree.
REPO = HERE.parents[4]
SYNC = REPO / "skills/core/scripts/sync_html_report.py"

# ---------------------------------------------------------------------------
# Synthetic analysis outputs
# ---------------------------------------------------------------------------

DAYS = list(range(0, 11))
DOUBLING_H = {"Vehicle": 22.4, "1 µM": 27.9, "5 µM": 38.1}
CAPACITY_K = 400.0   # thousand cells per well
START_K = 10.0
IC50_UM = 1.8
HILL = 1.3
MAX_EFFECT = 0.62


def logistic(day: int, doubling_h: float) -> float:
    r = math.log(2) / doubling_h * 24
    return CAPACITY_K / (1 + (CAPACITY_K / START_K - 1) * math.exp(-r * day))


def growth_counts() -> dict:
    return {
        "x": DAYS,
        "x_label": "Day",
        "y_label": "Cells per well (thousands)",
        "y_min": 0,
        "y_format": {"maximumFractionDigits": 0},
        "series": [
            {"name": name, "values": [round(logistic(d, dt), 1) for d in DAYS]}
            for name, dt in DOUBLING_H.items()
        ],
    }


def dose_response_svg() -> str:
    doses = [0.1, 0.3, 1.0, 3.0, 10.0]
    obs = [0.98, 0.93, 0.79, 0.52, 0.41]
    err = [0.03, 0.04, 0.05, 0.05, 0.04]
    W, H, L, R, T, B = 640, 400, 72, 24, 20, 56
    lo, hi = math.log10(0.05), math.log10(20)

    def X(d):
        return L + (math.log10(d) - lo) / (hi - lo) * (W - L - R)

    def Y(v):
        return T + (1 - v / 1.1) * (H - T - B)

    def fit(d):
        return 1 - MAX_EFFECT * d**HILL / (IC50_UM**HILL + d**HILL)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        '<defs><clipPath id="plot-area">'
        f'<rect x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}"/></clipPath></defs>',
    ]
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e1e0d9"/>')
        parts.append(f'<text x="{L - 10}" y="{Y(v) + 4:.1f}" font-size="12" fill="#898781" text-anchor="end">{v:g}</text>')
    for d in (0.1, 1, 10):
        parts.append(f'<text x="{X(d):.1f}" y="{H - B + 20}" font-size="12" fill="#898781" text-anchor="middle">{d:g}</text>')
    parts.append(f'<line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" stroke="#c3c2b7"/>')
    parts.append(f'<text x="{(L + W - R) / 2}" y="{H - 12}" font-size="13" fill="#52514e" text-anchor="middle">Drug X dose (µM, log scale)</text>')
    parts.append(f'<text transform="rotate(-90)" x="{-(T + H - B) / 2}" y="18" font-size="13" fill="#52514e" text-anchor="middle">Growth rate relative to vehicle</text>')
    curve = " ".join(
        f"{'M' if i == 0 else 'L'}{X(d):.1f} {Y(fit(d)):.1f}"
        for i, d in enumerate(10 ** (lo + k * (hi - lo) / 120) for k in range(121))
    )
    parts.append(f'<path d="{curve}" fill="none" stroke="#2a78d6" stroke-width="2" clip-path="url(#plot-area)"/>')
    parts.append(f'<line x1="{X(IC50_UM):.1f}" x2="{X(IC50_UM):.1f}" y1="{Y(fit(IC50_UM)):.1f}" y2="{H - B}" stroke="#52514e" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{X(IC50_UM) + 8:.1f}" y="{H - B - 10}" font-size="12" fill="#52514e">half-maximal dose</text>')
    for d, v, e in zip(doses, obs, err):
        parts.append(f'<line x1="{X(d):.1f}" x2="{X(d):.1f}" y1="{Y(v - e):.1f}" y2="{Y(v + e):.1f}" stroke="#0d366b" stroke-width="1.5"/>')
        parts.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="5" fill="#2a78d6" stroke="#ffffff" stroke-width="2"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def doubling_by_replicate_csv() -> str:
    offsets = [-0.6, 0.2, 0.5, -0.1]
    lines = ["condition,replicate,doubling_time_h"]
    for name, dt in DOUBLING_H.items():
        for rep, off in enumerate(offsets, start=1):
            lines.append(f"{name},{rep},{dt + off * (dt / 22.4):.2f}")
    return "\n".join(lines) + "\n"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(fig: Path, data: Path, table: Path) -> dict:
    def num(id_, value, label, **extra):
        return {"id": id_, "value": value, "label_canonical": label,
                "provenance": "scripts/02_fit_growth.py (synthetic example)", **extra}

    return {
        "_note": "SYNTHETIC example manifest for the HTML report template. No real experiment.",
        "numbers": [
            num("drug-x.n_replicates", 4, "replicate wells per dose"),
            num("drug-x.dose_low_um", 1, "low dose (µM)"),
            num("drug-x.dose_high_um", 5, "high dose (µM)"),
            num("drug-x.doubling_time_vehicle_h", DOUBLING_H["Vehicle"], "doubling time with vehicle (hours)"),
            num("drug-x.doubling_time_low_h", DOUBLING_H["1 µM"], "doubling time at the low dose (hours)"),
            num("drug-x.doubling_time_high_h", DOUBLING_H["5 µM"], "doubling time at the high dose (hours)"),
            num("drug-x.growth_rate_reduction_high", round(1 - DOUBLING_H["Vehicle"] / DOUBLING_H["5 µM"], 3),
                "reduction in growth rate at the high dose", unit="percent", precision=1,
                label_aliases_forbidden=["reduction in cell number", "kill rate"]),
            num("drug-x.ic50_um", IC50_UM, "half-maximal dose for growth-rate inhibition (µM)",
                label_aliases_forbidden=["IC50 for viability"]),
            num("drug-x.ic50_ci_low_um", 1.4, "lower bound of the 95% bootstrap interval on the half-maximal dose"),
            num("drug-x.ic50_ci_high_um", 2.3, "upper bound of the 95% bootstrap interval on the half-maximal dose"),
            num("drug-x.viability_high", 0.93, "fraction of cells alive at day ten, high dose",
                unit="percent", precision=0),
            num("drug-x.cell_line", "A549", "cell line"),
        ],
        "figures": [
            {"id": "dose_response", "path": "../outputs/figures/dose_response.svg", "sha256": sha(fig)},
        ],
        "data": [
            {"id": "growth_counts", "path": "../outputs/growth_counts.json", "sha256": sha(data)},
            {"id": "doubling_by_replicate", "path": "../outputs/doubling_by_replicate.csv", "sha256": sha(table)},
        ],
        "terms": [
            {"id": "cytostatic", "expansion": "slows or halts cell division without killing cells"},
        ],
    }


# ---------------------------------------------------------------------------
# Report content
# ---------------------------------------------------------------------------


def v(id_: str, text: str) -> str:
    return f'<span data-sci-val="drug-x.{id_}">{text}</span>'


def t(id_: str, text: str) -> str:
    return f'<span data-sci-text="drug-x.{id_}">{text}</span>'


FILL = {
    "TITLE": "Drug X slows lung cancer cell growth without killing the cells",
    "PROJECT": "Worked example (synthetic data)",
    "DEK": "Does Drug X stop cultured lung cancer cells from growing? It slows them, in proportion to dose, but the treated cells stay alive.",
    "AUTHORS": "Mycelium report generator",
    "DATE": "30 September 2026",
    "ABSTRACT": f"""
      <div class="caveat"><span class="caveat-label">Synthetic example</span>
      <p>This report shows the HTML template filled in end to end. Its data were generated for the example and describe no real experiment.</p></div>
      <p>We asked whether Drug X slows the growth of cultured lung cancer cells, compared with cells given only the drug's solvent (vehicle). Cells were counted daily for ten days at two doses. At the high dose the growth rate fell by {v("growth_rate_reduction_high", "41.2%")}, and the effect grew steadily with dose. The treated cells stayed alive: {v("viability_high", "93%")} were viable at the end of the experiment, so the drug slows division rather than killing cells. All experiments used one cell line, so we do not yet know whether the effect generalizes.</p>
    """,
    "PROBLEM_STATEMENT": """
      <p>A drug can stop a tumor from growing in two different ways: it can kill the cells, or it can slow how often they divide. The second kind of drug is called cytostatic (it slows or halts cell division without killing cells). The distinction matters because a cytostatic drug has to be combined with something else to shrink a tumor.</p>
      <p>This report answers one question: does Drug X slow the growth of lung cancer cells in culture, and if it does, is that because the cells die or because they divide less often? The comparison throughout is cells given the vehicle alone.</p>
    """,
    "METHODS_OVERVIEW": f"""
      <p>We grew {t("cell_line", "A549")} lung cancer cells in {v("n_replicates", "4")} replicate wells per condition and counted them once a day. Each condition received vehicle, a low dose ({v("dose_low_um", "1")}&nbsp;µM), or a high dose ({v("dose_high_um", "5")}&nbsp;µM) of Drug X. From each growth curve we estimated a doubling time: the time the population takes to double while it is still growing freely. A separate dose series measured growth rate at more doses so that we could fit a dose–response curve. <a class="xref" href="#fig-design">Figure</a> shows the design; the supplement gives the fitting details.</p>
      <figure class="sci-figure" id="fig-design" data-sci-diagram>
        <div class="sci-media">
          <svg viewBox="0 0 640 150" role="img" aria-label="Experimental design">
            <g font-family="system-ui, sans-serif" font-size="14" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="10" y="45" width="150" height="60" rx="8"/>
              <rect x="245" y="45" width="150" height="60" rx="8"/>
              <rect x="480" y="45" width="150" height="60" rx="8"/>
              <path d="M160 75h80" marker-end="url(#design-arrow)"/>
              <path d="M395 75h80" marker-end="url(#design-arrow)"/>
            </g>
            <defs><marker id="design-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
            <g font-family="system-ui, sans-serif" font-size="14" fill="currentColor" text-anchor="middle">
              <text x="85" y="72">Seed cells</text><text x="85" y="92" font-size="12" opacity=".7">vehicle or drug</text>
              <text x="320" y="72">Count daily</text><text x="320" y="92" font-size="12" opacity=".7">ten days</text>
              <text x="555" y="72">Fit growth</text><text x="555" y="92" font-size="12" opacity=".7">doubling time</text>
            </g>
          </svg>
        </div>
        <figcaption>Experimental design. Cells are seeded with vehicle or Drug X, counted daily, and each growth curve is summarized by its doubling time.</figcaption>
      </figure>
    """,
    "RESULTS": f"""
      <section id="result-growth">
        <h3>Drug X slows growth, and higher doses slow it more</h3>
        <p>Cells given vehicle doubled every {v("doubling_time_vehicle_h", "22.4")} hours. The low dose stretched the doubling time to {v("doubling_time_low_h", "27.9")} hours and the high dose to {v("doubling_time_high_h", "38.1")} hours, which is a {v("growth_rate_reduction_high", "41.2%")} reduction in growth rate at the high dose. <a class="xref" href="#fig-growth">Figure</a> shows the daily counts; drag the slider to compare the conditions on any day.</p>
        <figure class="sci-figure wide" id="fig-growth" data-sci-interactive="timeseries" data-default-index="last">
          <script type="application/json" data-sci-data="growth_counts">{{}}</script>
          <div class="sci-media"></div>
          <figcaption>Cells per well over ten days for vehicle and two doses of Drug X (mean of replicate wells). The treated populations approach the same ceiling as vehicle, but more slowly.</figcaption>
        </figure>
        <div class="table-wrap">
          <table class="sci-table" id="tab-doubling">
            <caption>Doubling time by condition.</caption>
            <thead><tr><th>Condition</th><th class="num">Doubling time (hours)</th></tr></thead>
            <tbody>
              <tr><td>Vehicle</td><td class="num">{v("doubling_time_vehicle_h", "22.4")}</td></tr>
              <tr><td>Low dose</td><td class="num">{v("doubling_time_low_h", "27.9")}</td></tr>
              <tr><td>High dose</td><td class="num">{v("doubling_time_high_h", "38.1")}</td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <section id="result-dose">
        <h3>Growth rate follows a standard dose–response curve</h3>
        <p>Across the dose series, growth rate fell along a sigmoid curve (<a class="xref" href="#fig-dose">Figure</a>). Half of the maximal effect was reached at {v("ic50_um", "1.8")}&nbsp;µM, with a 95% bootstrap interval from {v("ic50_ci_low_um", "1.4")} to {v("ic50_ci_high_um", "2.3")}&nbsp;µM. This is a half-maximal dose for growth rate, not for cell death.</p>
        <figure class="sci-figure" id="fig-dose" data-sci-fig="dose_response" data-alt="Dose–response curve for growth rate">
          <div class="sci-media"><!-- sci-media --><!-- /sci-media --></div>
          <figcaption>Growth rate relative to vehicle across a dose series of Drug X (points: mean and spread of replicate wells; line: fitted dose–response curve; dashed line: half-maximal dose).</figcaption>
        </figure>
      </section>
      <section id="result-viability">
        <h3>Treated cells stay alive, so Drug X is cytostatic</h3>
        <p>If the drug killed cells, the slower growth could simply reflect fewer living cells. It did not: at the high dose {v("viability_high", "93%")} of cells were alive at the end of the experiment. Drug X slows division rather than killing cells.</p>
      </section>
    """,
    "CONCLUSIONS": f"""
      <p>Drug X slows the growth of cultured lung cancer cells in proportion to dose, and it does so without killing them. On its own it would hold a tumor back rather than shrink it.</p>
      <div class="caveat" id="caveat-one-line"><span class="caveat-label">Main caveat</span>
      <p>Every experiment used one cell line ({t("cell_line", "A549")}). The effect may be specific to it.</p></div>
    """,
    "NEXT_STEPS": """
      <p>Repeat the growth assay across a panel of lung cancer cell lines, and test whether combining Drug X with a cytotoxic drug reduces cell number.</p>
    """,
    "PROVENANCE": """
      <dl>
        <dt>Manifest</dt><dd><code>reports/.manifest.json</code></dd>
        <dt>Growth fit</dt><dd><code>scripts/02_fit_growth.py</code> — doubling times from the daily counts (synthetic example)</dd>
        <dt>Dose response</dt><dd><code>scripts/03_dose_response.py</code> — sigmoid fit and bootstrap interval (synthetic example)</dd>
        <dt>Report build</dt><dd><code>build_example.py</code> regenerates this file from the template</dd>
      </dl>
    """,
    "SUPPLEMENT": """
      <section id="supp-fitting">
        <h3>How the doubling time is estimated</h3>
        <p>Each growth curve is fit with a logistic model, which captures free exponential growth early on and the slowdown as wells fill (<a class="xref" href="#fig-logistic">Figure</a>). The doubling time is the early, exponential phase of that fit. <a class="xref" href="#tab-replicates">Table</a> lists the estimate for every replicate well.</p>
        <figure class="sci-figure" id="fig-logistic" data-sci-diagram>
          <div class="sci-media">
            <svg viewBox="0 0 640 200" role="img" aria-label="Logistic growth schematic">
              <path d="M40 170 C 200 168, 260 150, 320 100 S 440 32, 600 30" fill="none" stroke="currentColor" stroke-width="2"/>
              <path d="M40 30 H600" stroke="currentColor" stroke-dasharray="4 4" opacity=".5"/>
              <g font-family="system-ui, sans-serif" font-size="14" fill="currentColor">
                <text x="60" y="150">exponential phase: doubling time</text>
                <text x="430" y="22">carrying capacity</text>
              </g>
            </svg>
          </div>
          <figcaption>The logistic growth model. The early, exponential phase sets the doubling time; the dashed line is the ceiling a full well approaches.</figcaption>
        </figure>
        <div class="table-wrap" id="tab-replicates">
          <table class="sci-table" data-sci-table="doubling_by_replicate" data-precision="1">
            <caption>Doubling time of every replicate well, by condition.</caption>
            <thead><tr><th>Condition</th><th class="num">Replicate</th><th class="num">Doubling time (hours)</th></tr></thead>
            <tbody><!-- sci-rows --><!-- /sci-rows --></tbody>
          </table>
        </div>
      </section>
    """,
}

SLIDES = [
    ("slide--statement", "#result-growth", "Drug X slows the growth of cultured lung cancer cells.",
     f'<div class="stat">{v("growth_rate_reduction_high", "41.2%")}</div><p class="stat-label">lower growth rate at the high dose, compared with vehicle</p>'),
    ("slide--figure", "#methods", "We counted treated and untreated cells daily for ten days.",
     '<div class="slide-figure" data-fig-ref="fig-design"></div>'),
    ("slide--statement", "#result-growth", "Untreated cells doubled roughly once a day.",
     f'<div class="stat">{v("doubling_time_vehicle_h", "22.4")} h</div><p class="stat-label">doubling time with vehicle</p>'),
    ("slide--figure", "#result-growth", "Each higher dose slowed growth further.",
     '<div class="slide-figure" data-fig-ref="fig-growth"></div><p class="takeaway">Drag the slider or press Play to step through the days.</p>'),
    ("slide--points", "#result-growth", "The high dose stretched the doubling time by more than half.",
     f'<ul><li>Vehicle: {v("doubling_time_vehicle_h", "22.4")} hours.</li><li>Low dose: {v("doubling_time_low_h", "27.9")} hours.</li><li>High dose: {v("doubling_time_high_h", "38.1")} hours.</li></ul>'),
    ("slide--split", "#result-dose", "Growth rate falls along a standard dose–response curve.",
     '<div class="slide-figure" data-fig-ref="fig-dose"></div><div class="slide-text"><p>Growth rate declines smoothly between the lowest and highest doses tested.</p></div>'),
    ("slide--statement", "#result-dose", "Half of the maximal effect arrives below the high dose.",
     f'<div class="stat">{v("ic50_um", "1.8")} µM</div><p class="stat-label">half-maximal dose for growth rate (95% interval {v("ic50_ci_low_um", "1.4")} to {v("ic50_ci_high_um", "2.3")} µM)</p>'),
    ("slide--statement", "#result-viability", "Treated cells stayed alive, so the drug is cytostatic.",
     f'<div class="stat">{v("viability_high", "93%")}</div><p class="stat-label">of cells alive at the end, high dose</p>'),
    ("slide--points", "#caveat-one-line", "All of these results come from a single cell line.",
     f'<ul><li>Every experiment used {t("cell_line", "A549")} cells.</li><li>The effect may not generalize to other lung cancers.</li></ul>'),
    ("slide--points", "#next-steps", "A panel of cell lines would show whether the effect generalizes.",
     '<ul><li>Repeat the growth assay across several lung cancer lines.</li><li>Test Drug X alongside a drug that kills cells.</li></ul>'),
    ("slide--points", "#provenance", "Every number in this deck traces to a registered analysis output.",
     '<ul><li>Values, figures, and the growth data are checked against the report manifest.</li><li>Press Esc on any slide to read the matching report section.</li></ul>'),
]


def slides_html() -> str:
    out = []
    for kind, source, title, body in SLIDES:
        out.append(
            f'      <section class="slide {kind}" data-source="{source}">\n'
            f"        <h2>{title}</h2>\n"
            f'        <div class="slide-body">{body}</div>\n'
            f"      </section>"
        )
    return "\n\n".join(out).lstrip()


def build(out_dir: Path = HERE) -> Path:
    fig = out_dir / "outputs/figures/dose_response.svg"
    data = out_dir / "outputs/growth_counts.json"
    table = out_dir / "outputs/doubling_by_replicate.csv"
    report = out_dir / "reports/example-report.html"
    manifest_path = out_dir / "reports/.manifest.json"
    for p in (fig, data, report):
        p.parent.mkdir(parents=True, exist_ok=True)
    fig.write_text(dose_response_svg(), encoding="utf-8")
    data.write_text(json.dumps(growth_counts(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    table.write_text(doubling_by_replicate_csv(), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest(fig, data, table), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    text = TEMPLATE.read_text(encoding="utf-8")
    fill = dict(FILL, SLIDES=slides_html())
    for key, value in fill.items():
        text = text.replace(f"%%{key}%%", value.strip())
    # A finished report drops the template's guidance comments (the docs say
    # to); only the sync markers stay.
    text = re.sub(r"[ \t]*<!--(?!\s*/?sci-(?:media|rows)\s*-->).*?-->[ \t]*\n?", "", text, flags=re.S)
    report.write_text(text, encoding="utf-8")
    if not SYNC.is_file():
        raise SystemExit(
            f"sync_html_report.py not found at {SYNC}; run build_example.py from the "
            "Mycelium plugin checkout, not from an installed copy of the pack"
        )
    subprocess.run(
        [sys.executable, str(SYNC), str(report), f"--manifest={manifest_path}"],
        check=True, stdout=subprocess.DEVNULL,
    )
    return report


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
    print(f"wrote {out}")
