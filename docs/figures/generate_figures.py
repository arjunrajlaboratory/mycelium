"""
Generate Panels B, C, and the composed summary figure for mycelium README.
Usage: python3.11 generate_figures.py
"""

import csv
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.image as mpimg
from datetime import datetime
from collections import Counter

# ── color palette ──────────────────────────────────────────────────────────
BG = "#FAF5EC"
GREEN = "#2F5E3F"
TEAL = "#3A8C8C"
CYAN = "#3FB8C9"
TERRA = "#C66B3D"
CREAM = "#F5E9D3"
DARK = "#2D4F4F"
MUTED = "#8C8275"

SPINE_KW = dict(color=MUTED, linewidth=0.8)

OUT = "/Users/mst36/tools/mycelium-main/docs/figures"

# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def style_ax(ax):
    """Remove top/right spines, style remaining ones."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(MUTED)
        ax.spines[sp].set_linewidth(0.8)
    ax.tick_params(colors=MUTED, length=3)
    ax.set_facecolor(BG)


# ─────────────────────────────────────────────────────────────────────────────
# Panel B – cumulative deliveries
# ─────────────────────────────────────────────────────────────────────────────


def load_deliveries():
    log = (
        "/Users/mst36/Desktop/Projects/Science/Scientific Claims Knowledge Graph"
        "/.claude/mycelium-pull-events.log"
    )
    deliveries = []
    with open(log) as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 6:
                continue
            ts, outcome = row[0], row[5]
            if outcome in ("rendered", "rendered-counts-only", "pulled"):
                try:
                    deliveries.append(datetime.fromisoformat(ts))
                except ValueError:
                    pass
    deliveries.sort()
    return deliveries


def draw_panel_b(ax):
    deliveries = load_deliveries()
    fix_date = datetime(2026, 5, 7)

    # aggregate by date → cumulative
    date_counts = Counter(d.date() for d in deliveries)
    all_dates = sorted(date_counts.keys())
    # fill in gaps for smooth cumulative line
    from_date = all_dates[0]
    to_date = all_dates[-1]
    delta = (to_date - from_date).days
    date_range = [
        from_date + __import__("datetime").timedelta(days=i) for i in range(delta + 1)
    ]
    cumulative = []
    running = 0
    for d in date_range:
        running += date_counts.get(d, 0)
        cumulative.append(running)

    total = cumulative[-1]
    pre = sum(date_counts[d] for d in date_counts if d < fix_date.date())

    xs = list(range(len(date_range)))
    ax.fill_between(xs, cumulative, alpha=0.12, color=GREEN)
    ax.plot(xs, cumulative, color=GREEN, linewidth=2.5, solid_capstyle="round")

    # vertical annotation for render-gate fix
    fix_idx = next((i for i, d in enumerate(date_range) if d >= fix_date.date()), None)
    if fix_idx is not None:
        ax.axvline(fix_idx, color=DARK, linewidth=1.0, linestyle="--", alpha=0.7)
        ax.text(
            fix_idx + 0.3,
            total * 0.38,
            "render-gate fix",
            color=DARK,
            fontsize=7.5,
            va="center",
            ha="left",
            rotation=0,
            style="italic",
        )

    # total annotation at right edge
    ax.text(
        xs[-1],
        total + total * 0.04,
        f"N={total} deliveries\nover {delta + 1} days",
        color=DARK,
        fontsize=7,
        ha="right",
        va="bottom",
        linespacing=1.4,
    )

    # x ticks every 3 days
    tick_xs = [i for i, d in enumerate(date_range) if d.day % 3 == 0]
    tick_labs = [date_range[i].strftime("%-m/%-d") for i in tick_xs]
    ax.set_xticks(tick_xs)
    ax.set_xticklabels(tick_labs, fontsize=7.5, color=MUTED)

    ax.set_ylabel("cumulative deliveries", fontsize=9, color=DARK, labelpad=6)
    ax.tick_params(axis="y", labelcolor=MUTED, labelsize=7.5)
    ax.set_xlim(-0.5, xs[-1] + 1)
    ax.set_ylim(0, total * 1.15)

    style_ax(ax)
    return pre, total - pre, total


# ─────────────────────────────────────────────────────────────────────────────
# Panel C – recurrence rate by mitigation type
# ─────────────────────────────────────────────────────────────────────────────


def draw_panel_c(ax):
    categories = ["Structural\nmitigation", "Ambient\nawareness"]
    values = [0, 100]  # 0% and 100% recurrence
    colors = [GREEN, TERRA]
    counts_txt = ["0 of 2 recurred", "4 of 4 recurred"]

    y_pos = [0, 1]
    bars = ax.barh(
        y_pos,
        values,
        color=colors,
        height=0.45,
        left=0,
        edgecolor="none",
    )

    for bar, label in zip(bars, counts_txt):
        x_end = bar.get_width()
        ax.text(
            x_end + 2,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            ha="left",
            fontsize=8,
            color=DARK,
        )

    ax.set_yticks(y_pos)
    ax.set_yticklabels(categories, fontsize=9, color=DARK)
    ax.set_xlabel(
        "% of learnings that recurred\n(when opportunity arose)",
        fontsize=8,
        color=DARK,
        labelpad=6,
    )
    ax.set_xlim(0, 130)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100"], fontsize=7.5, color=MUTED)

    # inline title as text (top-left, transform=ax.transAxes)
    ax.text(
        0,
        1.10,
        "Recurrence rate by mitigation type",
        transform=ax.transAxes,
        fontsize=9,
        color=DARK,
        va="bottom",
        ha="left",
    )
    ax.text(
        0,
        1.03,
        "12-learning audit, opportunity-conditioned",
        transform=ax.transAxes,
        fontsize=7.5,
        color=MUTED,
        va="bottom",
        ha="left",
        style="italic",
    )

    style_ax(ax)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone panel B and C PNGs
# ─────────────────────────────────────────────────────────────────────────────


def save_standalone_b():
    fig, ax = plt.subplots(figsize=(6, 4), facecolor=BG)
    ax.set_facecolor(BG)
    pre, post, total = draw_panel_b(ax)
    fig.tight_layout()
    fig.savefig(
        f"{OUT}/panel-b-accession.png", dpi=300, bbox_inches="tight", facecolor=BG
    )
    plt.close(fig)
    return pre, post, total


def save_standalone_c():
    fig, ax = plt.subplots(figsize=(6, 4), facecolor=BG)
    ax.set_facecolor(BG)
    draw_panel_c(ax)
    fig.tight_layout()
    fig.savefig(
        f"{OUT}/panel-c-recurrence.png", dpi=300, bbox_inches="tight", facecolor=BG
    )
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Composed summary figure
# ─────────────────────────────────────────────────────────────────────────────


def save_summary_figure(pre, post, total):
    fig = plt.figure(figsize=(14, 9), facecolor=BG)

    # GridSpec: 2 rows, 2 cols
    # row 0 spans both cols (Panel A), row 1 split into B and C
    gs = gridspec.GridSpec(
        2,
        2,
        figure=fig,
        height_ratios=[5, 4],
        hspace=0.30,
        wspace=0.28,
        left=0.06,
        right=0.97,
        top=0.96,
        bottom=0.07,
    )

    # ── Panel A ──
    ax_a = fig.add_subplot(gs[0, :])
    schematic = mpimg.imread(f"{OUT}/panel-a-workflow.png")
    ax_a.imshow(schematic, aspect="auto")
    ax_a.axis("off")
    ax_a.set_facecolor(BG)

    # ── Panel B ──
    ax_b = fig.add_subplot(gs[1, 0])
    ax_b.set_facecolor(BG)
    draw_panel_b(ax_b)

    # ── Panel C ──
    ax_c = fig.add_subplot(gs[1, 1])
    ax_c.set_facecolor(BG)
    draw_panel_c(ax_c)

    # ── Panel letters ──
    panel_label_kw = dict(
        fontsize=13,
        color=GREEN,
        fontweight="bold",
        transform=None,  # will be set per-axis
        va="top",
        ha="left",
    )
    for ax, letter in [(ax_a, "A"), (ax_b, "B"), (ax_c, "C")]:
        ax.text(
            0.01,
            0.99,
            letter,
            transform=ax.transAxes,
            fontsize=13,
            color=GREEN,
            fontweight="bold",
            va="top",
            ha="left",
        )

    fig.savefig(
        f"{OUT}/mycelium-summary-figure.png",
        dpi=300,
        bbox_inches="tight",
        facecolor=BG,
    )
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating Panel B …")
    pre, post, total = save_standalone_b()
    print(f"  pre-fix={pre}, post-fix={post}, total={total}")

    print("Generating Panel C …")
    save_standalone_c()

    print("Composing summary figure …")
    save_summary_figure(pre, post, total)

    print("Done. Files written to docs/figures/")
