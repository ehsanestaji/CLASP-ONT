"""Declarative ECharts + Plotly JSON specifications, vector PDF/SVG renderer, and LaTeX macro generator.

Implements Section 8 & Section 10 Rule 5:
  - Exports Apache ECharts JSON (*.echarts.json) and Plotly JSON (*.plotly.json)
  - Exports publication-grade vector PDF & SVG with embedded TrueType Type-42 fonts
  - Writes single-source-of-truth `results/summary_metrics.json` and `paper/results_macros.tex`
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Ensure TrueType Type-42 font embedding in PDF and SVG for journal submission gates
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]

# Okabe-Ito colorblind-safe scientific palette
PALETTE = {
    "keep_equiv": "#0072B2",
    "downgrade_sub": "#009E73",
    "downgrade_sup": "#56B4E9",
    "weaken_ancestor": "#E69F00",
    "negate_drop": "#D55E00",
    "primary": "#0072B2",
    "secondary": "#009E73",
    "alert": "#D55E00",
    "neutral": "#7F7F7F",
}


def _write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_all_figures_and_macros(
    summary_metrics: dict,
    figures_dir: Path,
    macros_tex_path: Path,
    summary_json_path: Path,
) -> dict[str, Path]:
    """Compile all declarative ECharts/Plotly JSON specs, vector PDFs/SVGs, and LaTeX macros."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    macros_tex_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(summary_metrics, summary_json_path)

    suites = summary_metrics.get("suites", {})
    suite_names = sorted(suites.keys())
    exported: dict[str, Path] = {"summary_json": summary_json_path}

    # =========================================================================
    # Figure 1: Compositional Hub Breakdown (Pairwise vs. Merged Hub Violations)
    # =========================================================================
    f1_labels: list[str] = []
    f1_pw_unsat: list[int] = []
    f1_hub_unsat: list[int] = []
    f1_pw_cons: list[int] = []
    f1_hub_cons: list[int] = []
    for s in suite_names:
        bd = suites[s].get("h1_breakdown", {})
        f1_labels.append(s)
        f1_pw_unsat.append(int(bd.get("total_pairwise_unsat", 0)))
        f1_hub_unsat.append(int(bd.get("hub_unsat_delta", 0)))
        f1_pw_cons.append(int(bd.get("total_pairwise_consviol", 0)))
        f1_hub_cons.append(int(bd.get("hub_consviol", 0)))

    f1_echarts = {
        "title": {"text": "Compositional Hub Breakdown: Pairwise vs. Merged Hub Defects"},
        "tooltip": {"trigger": "axis"},
        "legend": {
            "data": ["Pairwise Unsat", "Merged Hub Unsat_Delta", "Pairwise ConsViol", "Merged Hub ConsViol"]
        },
        "xAxis": {"type": "category", "data": f1_labels},
        "yAxis": {"type": "value", "name": "Violation Count"},
        "series": [
            {"name": "Pairwise Unsat", "type": "bar", "data": f1_pw_unsat, "color": "#56B4E9"},
            {"name": "Merged Hub Unsat_Delta", "type": "bar", "data": f1_hub_unsat, "color": "#D55E00"},
            {"name": "Pairwise ConsViol", "type": "bar", "data": f1_pw_cons, "color": "#009E73"},
            {"name": "Merged Hub ConsViol", "type": "bar", "data": f1_hub_cons, "color": "#CC79A7"},
        ],
    }
    f1_plotly = {
        "data": [
            {"type": "bar", "name": "Pairwise Unsat", "x": f1_labels, "y": f1_pw_unsat},
            {"type": "bar", "name": "Merged Hub Unsat_Delta", "x": f1_labels, "y": f1_hub_unsat},
            {"type": "bar", "name": "Pairwise ConsViol", "x": f1_labels, "y": f1_pw_cons},
            {"type": "bar", "name": "Merged Hub ConsViol", "x": f1_labels, "y": f1_hub_cons},
        ],
        "layout": {
            "title": "Pairwise Crosswalk Validity vs. Multi-Ontology Hub Breakdown",
            "barmode": "group",
            "yaxis": {"title": "Logical Violations"},
        },
    }
    _write_json(f1_echarts, figures_dir / "fig1_compositional_breakdown.echarts.json")
    _write_json(f1_plotly, figures_dir / "fig1_compositional_breakdown.plotly.json")

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    x = np.arange(max(1, len(f1_labels)))
    w = 0.19
    if f1_labels:
        ax.bar(x - 1.5 * w, f1_pw_unsat, w, label="Pairwise Unsat", color="#56B4E9")
        ax.bar(x - 0.5 * w, f1_hub_unsat, w, label="Hub Unsat_Delta", color="#D55E00")
        ax.bar(x + 0.5 * w, f1_pw_cons, w, label="Pairwise ConsViol", color="#009E73")
        ax.bar(x + 1.5 * w, f1_hub_cons, w, label="Hub ConsViol", color="#CC79A7")
        ax.set_xticks(x)
        ax.set_xticklabels(f1_labels, rotation=10, ha="center")
        max_f1 = max([*f1_pw_unsat, *f1_hub_unsat, *f1_pw_cons, *f1_hub_cons, 1])
        ax.set_ylim(0, max_f1 * 1.28)
    ax.set_ylabel("Violation Count")
    ax.set_title("Pairwise vs. Composed Multi-Ontology Hub Breakdown", fontsize=11, pad=10)
    ax.legend(frameon=False, fontsize=8.5, ncol=2, loc="upper center")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    f1_pdf = figures_dir / "fig1_compositional_breakdown.pdf"
    fig.savefig(f1_pdf, bbox_inches="tight")
    fig.savefig(figures_dir / "fig1_compositional_breakdown.svg", bbox_inches="tight")
    plt.close(fig)
    exported["fig1_pdf"] = f1_pdf

    # =========================================================================
    # Figure 2: Benchmark Equivalence F1 & Directional Subsumption Recall
    # =========================================================================
    primary_suite = "OAEI_BioML_2026" if "OAEI_BioML_2026" in suites else (suite_names[0] if suite_names else "")
    sys_dict = suites.get(primary_suite, {}).get("systems", {})
    sys_order = [k for k in sorted(sys_dict.keys())]
    eq_f1s = [float(sys_dict[k].get("equiv_f1", 0.0)) for k in sys_order]
    sub_recs = [float(sys_dict[k].get("sub_recall", 0.0)) for k in sys_order]
    retentions = [float(sys_dict[k].get("semantic_retention", 0.0)) for k in sys_order]

    f2_echarts = {
        "title": {"text": f"Benchmark Accuracy & Subsumption Recovery ({primary_suite})"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": ["Equivalence F1", "Directional Subsumption Recall", "Semantic Retention"]},
        "xAxis": {"type": "category", "data": sys_order},
        "yAxis": {"type": "value", "min": 0.0, "max": 1.05},
        "series": [
            {"name": "Equivalence F1", "type": "bar", "data": eq_f1s, "color": PALETTE["primary"]},
            {"name": "Directional Subsumption Recall", "type": "bar", "data": sub_recs, "color": PALETTE["secondary"]},
            {"name": "Semantic Retention", "type": "bar", "data": retentions, "color": "#E69F00"},
        ],
    }
    f2_plotly = {
        "data": [
            {"type": "bar", "name": "Equivalence F1", "x": sys_order, "y": eq_f1s},
            {"type": "bar", "name": "Directional Subsumption Recall", "x": sys_order, "y": sub_recs},
            {"type": "bar", "name": "Semantic Retention", "x": sys_order, "y": retentions},
        ],
        "layout": {
            "title": f"Accuracy & Subsumption Recovery ({primary_suite})",
            "barmode": "group",
            "yaxis": {"title": "Score", "range": [0, 1.05]},
        },
    }
    _write_json(f2_echarts, figures_dir / "fig2_accuracy_subsumption.echarts.json")
    _write_json(f2_plotly, figures_dir / "fig2_accuracy_subsumption.plotly.json")

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    if sys_order:
        x2 = np.arange(len(sys_order))
        w2 = 0.25
        ax.bar(x2 - w2, eq_f1s, w2, label="Equivalence F1", color=PALETTE["primary"])
        ax.bar(x2, sub_recs, w2, label="Subsumption Recall", color=PALETTE["secondary"])
        ax.bar(x2 + w2, retentions, w2, label="Semantic Retention", color="#E69F00")
        ax.set_xticks(x2)
        ax.set_xticklabels(sys_order, rotation=15, ha="right")
    ax.set_ylim(0, 1.22)
    ax.set_ylabel("Metric Score")
    ax.set_title(f"Equivalence F1, Subsumption Recovery & Retention ({primary_suite})", fontsize=10.8, pad=10)
    ax.legend(frameon=False, fontsize=8.5, ncol=3, loc="upper center")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    f2_pdf = figures_dir / "fig2_accuracy_subsumption.pdf"
    fig.savefig(f2_pdf, bbox_inches="tight")
    fig.savefig(figures_dir / "fig2_accuracy_subsumption.svg", bbox_inches="tight")
    plt.close(fig)
    exported["fig2_pdf"] = f2_pdf

    # =========================================================================
    # Figure 3: Destructive Deletion vs. 5-Element Lattice Fate Proportions
    # =========================================================================
    actions = ["keep_equiv", "downgrade_sub", "downgrade_sup", "weaken_ancestor", "negate_drop"]
    fate_by_act: dict[str, list[int]] = {a: [] for a in actions}
    for k in sys_order:
        ac = sys_dict[k].get("action_counts", {})
        for a in actions:
            fate_by_act[a].append(int(ac.get(a, 0)))

    f3_echarts = {
        "title": {"text": f"Contested Mapping Fate Distribution ({primary_suite})"},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"data": actions},
        "xAxis": {"type": "value", "name": "Mapping Count"},
        "yAxis": {"type": "category", "data": sys_order},
        "series": [
            {
                "name": a,
                "type": "bar",
                "stack": "total",
                "data": fate_by_act[a],
                "color": PALETTE[a],
            }
            for a in actions
        ],
    }
    f3_plotly = {
        "data": [
            {"type": "bar", "orientation": "h", "name": a, "y": sys_order, "x": fate_by_act[a]}
            for a in actions
        ],
        "layout": {"title": "Mapping Fate Breakdown", "barmode": "stack"},
    }
    _write_json(f3_echarts, figures_dir / "fig3_lattice_fate_breakdown.echarts.json")
    _write_json(f3_plotly, figures_dir / "fig3_lattice_fate_breakdown.plotly.json")

    fig, ax = plt.subplots(figsize=(7.8, 4.3))
    if sys_order:
        y3 = np.arange(len(sys_order))
        left = np.zeros(len(sys_order))
        for a in actions:
            vals = np.array(fate_by_act[a], dtype=float)
            ax.barh(y3, vals, left=left, label=a, color=PALETTE[a])
            left += vals
        ax.set_yticks(y3)
        ax.set_yticklabels(sys_order)
    ax.set_xlabel("Mappings")
    ax.set_title(f"Deletion vs. Non-Destructive Lattice Refinement ({primary_suite})", fontsize=10.8, pad=10)
    ax.legend(
        frameon=False,
        fontsize=8,
        ncol=5,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    f3_pdf = figures_dir / "fig3_lattice_fate_breakdown.pdf"
    fig.savefig(f3_pdf, bbox_inches="tight")
    fig.savefig(figures_dir / "fig3_lattice_fate_breakdown.svg", bbox_inches="tight")
    plt.close(fig)
    exported["fig3_pdf"] = f3_pdf

    # =========================================================================
    # Figure 4: Oracle Calibration & Inter-Agent Agreement
    # =========================================================================
    oracle_stats = suites.get(primary_suite, {}).get("oracle_agreement", {})
    pair_js = oracle_stats.get("pairwise_js_divergence", {})
    js_keys = sorted(pair_js.keys())
    js_vals = [float(pair_js[k]) for k in js_keys]

    f4_echarts = {
        "title": {"text": "Inter-Oracle Jensen-Shannon Divergence & Agreement"},
        "xAxis": {"type": "category", "data": js_keys or ["B4_Self_Check"]},
        "yAxis": {"type": "value", "name": "Jensen-Shannon Divergence (bits)"},
        "series": [{"type": "bar", "data": js_vals or [0.0], "color": PALETTE["primary"]}],
    }
    f4_plotly = {
        "data": [{"type": "bar", "x": js_keys or ["B4_Self_Check"], "y": js_vals or [0.0]}],
        "layout": {"title": "Oracle Agreement & Divergence", "yaxis": {"title": "JSD [0, 1]"}},
    }
    _write_json(f4_echarts, figures_dir / "fig4_oracle_agreement.echarts.json")
    _write_json(f4_plotly, figures_dir / "fig4_oracle_agreement.plotly.json")

    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    labels4 = js_keys if js_keys else ["Pending Frontier Sweep (B4 Ready)"]
    vals4 = js_vals if js_vals else [0.0]
    ax.bar(np.arange(len(labels4)), vals4, width=0.45, color=PALETTE["primary"])
    ax.set_xticks(np.arange(len(labels4)))
    ax.set_xticklabels(labels4, rotation=15, ha="right")
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("Jensen-Shannon Divergence")
    ax.set_title("Multi-Agent Oracle Divergence")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    f4_pdf = figures_dir / "fig4_oracle_agreement.pdf"
    fig.savefig(f4_pdf)
    fig.savefig(figures_dir / "fig4_oracle_agreement.svg")
    plt.close(fig)
    exported["fig4_pdf"] = f4_pdf

    # =========================================================================
    # Figure 5: CEGAR Convergence & CPU Memory Telemetry
    # =========================================================================
    telemetry = suites.get(primary_suite, {}).get("cegar_telemetry", [])
    it_nums = [int(t["iteration"]) for t in telemetry] or [1]
    it_sec = [round(float(t["wall_seconds"]), 3) for t in telemetry] or [0.0]
    it_rss = [round(float(t["peak_rss_mb"]), 1) for t in telemetry] or [0.0]
    it_viols = [int(t["unsat_delta_count"]) + int(t["cons_viol_count"]) for t in telemetry] or [0]

    f5_echarts = {
        "title": {"text": f"CEGAR Convergence & CPU Telemetry ({primary_suite})"},
        "xAxis": {"type": "category", "name": "CEGAR Iteration", "data": it_nums},
        "yAxis": [
            {"type": "value", "name": "Remaining Violations / Wall Sec"},
            {"type": "value", "name": "Peak RSS (MB)"},
        ],
        "series": [
            {"name": "Remaining Violations", "type": "line", "data": it_viols, "color": PALETTE["alert"]},
            {"name": "Wall Seconds", "type": "bar", "data": it_sec, "color": PALETTE["secondary"]},
            {"name": "Peak RSS (MB)", "type": "line", "yAxisIndex": 1, "data": it_rss, "color": PALETTE["primary"]},
        ],
    }
    f5_plotly = {
        "data": [
            {"type": "scatter", "mode": "lines+markers", "name": "Remaining Violations", "x": it_nums, "y": it_viols},
            {"type": "bar", "name": "Wall Seconds", "x": it_nums, "y": it_sec},
            {"type": "scatter", "mode": "lines+markers", "name": "Peak RSS (MB)", "x": it_nums, "y": it_rss, "yaxis": "y2"},
        ],
        "layout": {
            "title": f"CEGAR Convergence & Memory Telemetry ({primary_suite})",
            "xaxis": {"title": "CEGAR Iteration"},
            "yaxis": {"title": "Violations / Seconds"},
            "yaxis2": {"title": "Peak RSS (MB)", "overlaying": "y", "side": "right"},
        },
    }
    _write_json(f5_echarts, figures_dir / "fig5_cegar_telemetry.echarts.json")
    _write_json(f5_plotly, figures_dir / "fig5_cegar_telemetry.plotly.json")

    fig, ax1 = plt.subplots(figsize=(7.2, 4.2))
    l1 = ax1.plot(it_nums, it_viols, marker="o", color=PALETTE["alert"], label="Post-Iter Violations")
    b1 = ax1.bar(it_nums, it_sec, width=0.25, alpha=0.45, color=PALETTE["secondary"], label="Iter Runtime (s)")
    ax1.set_xticks(it_nums)
    ax1.set_xlabel("CEGAR Iteration")
    ax1.set_ylabel("Violations / Runtime (s)")
    ax1.set_ylim(0, max([*it_viols, *it_sec, 1]) * 1.25)
    ax2 = ax1.twinx()
    l2 = ax2.plot(it_nums, it_rss, marker="s", linestyle="--", color=PALETTE["primary"], label="Peak RSS (MB)")
    ax2.set_ylabel("Peak RSS Memory (MB)")
    ax2.set_ylim(0, max(600.0, max(it_rss) * 1.3))
    handles = [*l1, b1, *l2]
    labels_h = [h.get_label() for h in handles]
    ax1.legend(handles, labels_h, frameon=False, fontsize=8.2, ncol=3, loc="upper center")
    ax1.set_title(f"CEGAR Convergence & CPU Memory Footprint ({primary_suite})", fontsize=10.8, pad=10)
    fig.tight_layout()
    f5_pdf = figures_dir / "fig5_cegar_telemetry.pdf"
    fig.savefig(f5_pdf, bbox_inches="tight")
    fig.savefig(figures_dir / "fig5_cegar_telemetry.svg", bbox_inches="tight")
    plt.close(fig)
    exported["fig5_pdf"] = f5_pdf

    # =========================================================================
    # Programmatic LaTeX Macros (Single Source of Numerical Truth)
    # =========================================================================
    macro_lines = [
        "% Auto-generated by src/clasp_ont/export/build_figures.py -- DO NOT EDIT MANUALLY",
    ]
    for s_key, s_data in sorted(suites.items()):
        clean_s = "".join(ch for ch in s_key if ch.isalpha())
        bd = s_data.get("h1_breakdown", {})
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}Classes}}{{{s_data.get('num_classes', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}Mappings}}{{{s_data.get('num_mappings', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}RawUnsat}}{{{bd.get('hub_unsat_delta', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}RawConsViol}}{{{bd.get('hub_consviol', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}PairwiseUnsat}}{{{bd.get('total_pairwise_unsat', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}PairwiseConsViol}}{{{bd.get('total_pairwise_consviol', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}ContestedCount}}{{{s_data.get('contested_mappings', 0)}}}")
        macro_lines.append(f"\\newcommand{{\\Clasp{clean_s}PeakRssMB}}{{{s_data.get('peak_rss_mb', 0.0):.1f}}}")

        for sys_k, sys_v in sorted(s_data.get("systems", {}).items()):
            clean_sys = "".join(ch for ch in sys_k if ch.isalpha())
            macro_lines.append(
                f"\\newcommand{{\\Clasp{clean_s}{clean_sys}Unsat}}{{{sys_v.get('unsat_delta', 0)}}}"
            )
            macro_lines.append(
                f"\\newcommand{{\\Clasp{clean_s}{clean_sys}ConsViol}}{{{sys_v.get('cons_viol', 0)}}}"
            )
            macro_lines.append(
                f"\\newcommand{{\\Clasp{clean_s}{clean_sys}Retention}}{{{sys_v.get('semantic_retention', 0.0):.3f}}}"
            )
            macro_lines.append(
                f"\\newcommand{{\\Clasp{clean_s}{clean_sys}EquivFone}}{{{sys_v.get('equiv_f1', 0.0):.3f}}}"
            )
            macro_lines.append(
                f"\\newcommand{{\\Clasp{clean_s}{clean_sys}SubRecall}}{{{sys_v.get('sub_recall', 0.0):.3f}}}"
            )

    macros_tex_path.write_text("\n".join(macro_lines) + "\n", encoding="utf-8")
    exported["macros_tex"] = macros_tex_path
    return exported
