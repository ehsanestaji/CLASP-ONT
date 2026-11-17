"""Unified command-line interface for CLASP-ONT phased execution and offline referee replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clasp_ont.baselines.run_asp_delete import run_binary_asp_delete
from clasp_ont.baselines.run_logmap_repair import run_logmap_repair
from clasp_ont.export.build_figures import build_all_figures_and_macros
from clasp_ont.export.kgcl_patches import export_certified_sssom_and_kgcl
from clasp_ont.export.metrics_stats import (
    apply_bh_fdr,
    evaluate_alignment_against_gold,
    paired_bootstrap_and_tests,
    run_pairwise_vs_hub_audit,
)
from clasp_ont.ingest.download_hubs import fetch_public_suite
from clasp_ont.ingest.preclean import run_native_preclean
from clasp_ont.ingest.sssom_io import action_to_owl_pair, write_mappings_owl
from clasp_ont.ingest.toy_hub import build_toy_hub
from clasp_ont.oracles.consensus import aggregate_log_opinion_pool
from clasp_ont.oracles.dossier_builder import build_dossiers
from clasp_ont.oracles.heuristic_baseline import score_lexical_lattice_heuristic
from clasp_ont.oracles.sandbox_cli_runner import run_vendor_sweep
from clasp_ont.symbolic.cegar_loop import ALL_LATTICE_ACTIONS, load_lattice_config, run_clingo_cegar
from clasp_ont.symbolic.conflict_cores import extract_conflict_cores
from clasp_ont.symbolic.conservativity import audit_merged_hub

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_phase0_smoke(out_root: Path | None = None) -> dict:
    """Execute the complete Phase 0 200-class 4-ontology smoke slice end-to-end."""
    root = out_root or REPO_ROOT
    toy_dir = root / "data" / "toy"
    work_dir = root / "data" / "interim" / "phase0"
    results_dir = root / "results"
    figures_dir = results_dir / "figures"
    macros_path = root / "paper" / "results_macros.tex"
    summary_path = results_dir / "summary_metrics.json"

    bundle = build_toy_hub(toy_dir)
    native = run_native_preclean(
        "Phase0_Toy_Hub",
        bundle.ontology_paths,
        work_dir / "native",
        namespace_hints=bundle.namespace_hints,
    )

    # Gold sets from ground-truth actions on the 60 correspondences
    gold_equiv: set[tuple[str, str]] = set()
    gold_sub: set[tuple[str, str]] = set()
    for m in bundle.mappings:
        gt_act = bundle.ground_truth_actions[m.mapping_id]
        pair = action_to_owl_pair(m, gt_act)
        if pair is None:
            continue
        rel, lhs, rhs = pair
        if rel == "equiv":
            gold_equiv.add((lhs, rhs))
        else:
            gold_sub.add((lhs, rhs))

    # B1: Raw unrepaired hub + H1 pairwise-vs-hub breakdown
    raw_bridge = write_mappings_owl(bundle.mappings, None, work_dir / "B1_raw_bridge.owl")
    raw_audit = audit_merged_hub(native, raw_bridge, work_dir / "elk_B1", tag="B1_Raw_Hub")
    h1_bd = run_pairwise_vs_hub_audit(native, bundle.mappings, raw_audit, work_dir / "pairwise")

    # Stage 2: Minimal conflict cores & OAK dossiers
    cores, traces = extract_conflict_cores(native, bundle.mappings, raw_audit)
    contested_ids = sorted({mid for c in cores for mid, _ in c.literals})
    rec_by_id = {m.mapping_id: m for m in bundle.mappings}
    contested_records = [rec_by_id[mid] for mid in contested_ids if mid in rec_by_id]
    dossiers_path = root / "data" / "dossiers" / "Phase0_Toy_Hub.jsonl"
    dossiers = build_dossiers(native, contested_records, traces, out_jsonl_path=dossiers_path)

    # B2: LogMap standalone DEBUGGER repair
    b2_res = run_logmap_repair(native, bundle.mappings, work_dir / "B2_LogMap")

    # B3: Exact binary ASP hitting-set deletion
    b3_res = run_binary_asp_delete(native, bundle.mappings, cores, work_dir / "B3_ASP_Delete")

    # B4: Non-LLM Lexical + Depth Lattice Heuristic + Clingo CEGAR
    b4_decs = score_lexical_lattice_heuristic(native, bundle.mappings, traces)
    b4_res = run_clingo_cegar(
        system_id="B4_Lexical_Lattice",
        native_audit=native,
        mappings=bundle.mappings,
        initial_cores=cores,
        oracle_decisions=b4_decs,
        work_dir=work_dir / "B4_Lexical_Lattice",
        fallback_oracle_fn=lambda recs: score_lexical_lattice_heuristic(native, recs, traces),
    )

    # Export certified SSSOM + KGCL patch files for B4
    export_certified_sssom_and_kgcl(
        mappings=bundle.mappings,
        selected_actions=b4_res.selected_actions,
        oracle_decisions=b4_decs,
        out_sssom_path=results_dir / "certified" / "Phase0_Toy_Hub.B4_Lexical_Lattice.sssom.tsv",
        out_kgcl_path=results_dir / "patches" / "Phase0_Toy_Hub.B4_Lexical_Lattice.kgcl",
        system_id="B4_Lexical_Lattice",
        suite_id="Phase0_Toy_Hub",
    )

    # Evaluate B1..B4
    c_set = set(b4_res.contested_mapping_ids)
    b1_actions = {m.mapping_id: "keep_equiv" for m in bundle.mappings}
    systems_summary: dict[str, dict] = {}

    for sys_id, act_map, aud in [
        ("B1_Raw_Hub", b1_actions, raw_audit),
        ("B2_LogMap_Repair", b2_res.selected_actions, b2_res.final_audit),
        ("B3_ASP_Binary_Delete", b3_res.selected_actions, b3_res.final_audit),
        ("B4_Lexical_Lattice", b4_res.selected_actions, b4_res.final_audit),
    ]:
        scores = evaluate_alignment_against_gold(
            bundle.mappings, act_map, gold_equiv, gold_sub, contested_ids=c_set
        )
        scores["unsat_delta"] = len(aud.unsat_delta)
        scores["cons_viol"] = aud.cons_viol_count
        scores["novel_cross_entailments"] = aud.novel_cross_entailments
        scores["action_counts"] = {
            a: sum(1 for v in act_map.values() if v == a) for a in ALL_LATTICE_ACTIONS
        }
        systems_summary[sys_id] = scores

    # Paired statistical tests (B4 lattice vs B3 binary delete and B2 LogMap across contested mappings)
    cfg = load_lattice_config()
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}
    eval_ids = sorted(bundle.trap_ids)
    b4_util = {
        i: rho_map[b4_res.selected_actions[i]]
        * (1.0 if b4_res.selected_actions[i] == bundle.ground_truth_actions[i] else 0.35)
        for i in eval_ids
    }
    b3_util = {
        i: rho_map[b3_res.selected_actions[i]]
        * (1.0 if b3_res.selected_actions[i] == bundle.ground_truth_actions[i] else 0.35)
        for i in eval_ids
    }
    b2_util = {
        i: rho_map[b2_res.selected_actions[i]]
        * (1.0 if b2_res.selected_actions[i] == bundle.ground_truth_actions[i] else 0.35)
        for i in eval_ids
    }
    b4_corr = {i: b4_res.selected_actions[i] == bundle.ground_truth_actions[i] for i in eval_ids}
    b3_corr = {i: b3_res.selected_actions[i] == bundle.ground_truth_actions[i] for i in eval_ids}
    b2_corr = {i: b2_res.selected_actions[i] == bundle.ground_truth_actions[i] for i in eval_ids}

    stat_raw = {
        "B4_vs_B3_BinaryDelete": paired_bootstrap_and_tests(
            eval_ids, b4_util, b3_util, b4_corr, b3_corr
        ),
        "B4_vs_B2_LogMap": paired_bootstrap_and_tests(
            eval_ids, b4_util, b2_util, b4_corr, b2_corr
        ),
    }
    stat_fdr = apply_bh_fdr(stat_raw, alpha=0.01)

    peak_rss = max(
        native.peak_rss_mb,
        raw_audit.peak_rss_mb,
        b2_res.final_audit.peak_rss_mb,
        max((it.peak_rss_mb for it in b3_res.iterations), default=0.0),
        max((it.peak_rss_mb for it in b4_res.iterations), default=0.0),
    )

    phase0_metrics = {
        "num_ontologies": len(bundle.ontology_paths),
        "num_classes": len(native.class_to_ont),
        "native_unsat": len(native.native_unsat_classes),
        "num_mappings": len(bundle.mappings),
        "contested_mappings": len(b4_res.contested_mapping_ids),
        "num_dossiers": len(dossiers),
        "max_dossier_tokens": max((d["estimated_tokens"] for d in dossiers), default=0),
        "initial_conflict_cores": len(cores),
        "total_cegar_cores_b4": b4_res.total_cores,
        "b4_certificate_sha256": b4_res.certificate_sha256,
        "peak_rss_mb": round(peak_rss, 2),
        "h1_breakdown": {
            "total_pairwise_unsat": h1_bd.total_pairwise_unsat,
            "total_pairwise_consviol": h1_bd.total_pairwise_consviol,
            "hub_unsat_delta": h1_bd.hub_unsat_delta,
            "hub_consviol": h1_bd.hub_consviol,
            "pairwise_conflict_mapping_rate": h1_bd.pairwise_conflict_mapping_rate,
            "pairwise_audits": h1_bd.pairwise_audits,
        },
        "systems": systems_summary,
        "statistical_tests": stat_fdr,
        "cegar_telemetry": [
            {
                "iteration": it.iteration,
                "active_cores": it.active_cores,
                "contested_mappings": it.contested_mappings,
                "clingo_utility": it.clingo_utility,
                "unsat_delta_count": it.unsat_delta_count,
                "cons_viol_count": it.cons_viol_count,
                "wall_seconds": round(it.wall_seconds, 4),
                "peak_rss_mb": round(it.peak_rss_mb, 2),
            }
            for it in b4_res.iterations
        ],
        "oracle_agreement": {"pairwise_js_divergence": {}},
    }
    suites_ckpt_dir = results_dir / "suites"
    suites_ckpt_dir.mkdir(parents=True, exist_ok=True)
    (suites_ckpt_dir / "Phase0_Toy_Hub.json").write_text(
        json.dumps(phase0_metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    merged_suites: dict[str, dict] = {"Phase0_Toy_Hub": phase0_metrics}
    for ckpt_f in sorted(suites_ckpt_dir.glob("*.json")):
        try:
            merged_suites[ckpt_f.stem] = json.loads(ckpt_f.read_text(encoding="utf-8"))
        except Exception:
            pass
    summary = {"seed": 20261116, "suites": merged_suites}

    build_all_figures_and_macros(
        summary_metrics=summary,
        figures_dir=figures_dir,
        macros_tex_path=macros_path,
        summary_json_path=summary_path,
    )
    return summary


def prioritize_dossiers(dossiers: list[dict], max_count: int = 800) -> list[dict]:
    """Select top-K highest-conflict contested dossiers (preserving deterministic mapping_id order)."""
    if max_count <= 0 or len(dossiers) <= max_count:
        return dossiers
    top = sorted(
        dossiers,
        key=lambda d: (
            len(d.get("conflict_traces", [])),
            1 if d.get("input_predicate") == "skos:exactMatch" else 0,
            float(d.get("input_confidence", 0.0)),
            d.get("mapping_id", ""),
        ),
        reverse=True,
    )[:max_count]
    return sorted(top, key=lambda d: d.get("mapping_id", ""))


def run_frontier_cli_sweep_command(
    dossiers_dir: Path,
    human_confirmed: bool,
    max_dossiers_per_suite: int = 800,
) -> None:
    """Human-launched terminal command evaluating exported *.jsonl dossiers across 3 CLI vendors."""
    if not human_confirmed:
        raise PermissionError(
            "Refusing autonomous third-party CLI execution. Run `make run-frontier-oracles` from a terminal."
        )
    jsonl_files = sorted(dossiers_dir.glob("*.jsonl"))
    if not jsonl_files:
        raise FileNotFoundError(f"No dossier *.jsonl files found in {dossiers_dir}.")

    for jf in jsonl_files:
        all_dossiers = [
            json.loads(line)
            for line in jf.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        dossiers = prioritize_dossiers(all_dossiers, max_count=max_dossiers_per_suite)
        for vendor_id in ("C1_Claude", "C2_Codex", "C3_Antigravity"):
            print(
                f"[frontier-oracles] Running {vendor_id} on {jf.name} "
                f"({len(dossiers)}/{len(all_dossiers)} prioritized dossiers)..."
            )
            run_vendor_sweep(
                vendor_id=vendor_id,
                dossiers=dossiers,
                allow_live_cli=True,
                human_terminal_confirmed=True,
            )


def replay_cached_oracles_for_toy(root: Path | None = None) -> dict:
    """Offline zero-key replay: runs Phase 0 + any cached C1..C3 vendor responses in results/oracle_cache/."""
    base = root or REPO_ROOT
    summary = run_phase0_smoke(base)
    dossiers_file = base / "data" / "dossiers" / "Phase0_Toy_Hub.jsonl"
    if not dossiers_file.is_file():
        return summary

    dossiers = [
        json.loads(line)
        for line in dossiers_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    vendor_decs = {}
    for vendor_id in ("C1_Claude", "C2_Codex", "C3_Antigravity"):
        try:
            vendor_decs[vendor_id] = run_vendor_sweep(
                vendor_id=vendor_id,
                dossiers=dossiers,
                allow_live_cli=False,
                human_terminal_confirmed=False,
            )
        except RuntimeError:
            continue

    if len(vendor_decs) == 3:
        cons = aggregate_log_opinion_pool(vendor_decs)
        summary["suites"]["Phase0_Toy_Hub"]["oracle_agreement"] = {
            "fleiss_kappa": cons.fleiss_kappa,
            "mean_js_divergence": cons.mean_js_divergence,
            "pairwise_js_divergence": cons.pairwise_js_divergence,
        }
        build_all_figures_and_macros(
            summary_metrics=summary,
            figures_dir=base / "results" / "figures",
            macros_tex_path=base / "paper" / "results_macros.tex",
            summary_json_path=base / "results" / "summary_metrics.json",
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="clasp-ont", description="CLASP-ONT pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("phase0-smoke", help="Run Phase 0 200-class 4-ontology verification gate")

    p_fetch = sub.add_parser("fetch-suite", help="Download and checksum a public evaluation suite")
    p_fetch.add_argument("--suite", required=True, choices=["OAEI_BioML_2026", "Plant_Biodiv_Hub", "Disease_Phenotype_Hub"])

    p_pub = sub.add_parser("run-public-suites", help="Run Phase 1-3 audit, repair, and synthesis on public hubs")
    p_pub.add_argument(
        "--suites",
        nargs="+",
        default=["OAEI_BioML_2026", "Plant_Biodiv_Hub", "Disease_Phenotype_Hub"],
        choices=["OAEI_BioML_2026", "Plant_Biodiv_Hub", "Disease_Phenotype_Hub"],
    )

    p_oracles = sub.add_parser("run-frontier-oracles", help="Human-launched sandboxed 3-vendor CLI oracle sweep")
    p_oracles.add_argument("--dossiers-dir", type=Path, default=REPO_ROOT / "data" / "dossiers")
    p_oracles.add_argument("--max-dossiers-per-suite", type=int, default=800)
    p_oracles.add_argument("--human-terminal-confirm", action="store_true")

    sub.add_parser("reproduce-from-cache", help="Zero-key offline reproduction from results/oracle_cache/")

    args = parser.parse_args()
    if args.cmd == "phase0-smoke":
        res = run_phase0_smoke()
        s = res["suites"]["Phase0_Toy_Hub"]
        print(json.dumps({"Phase0_Toy_Hub": {
            "classes": s["num_classes"],
            "mappings": s["num_mappings"],
            "raw_unsat": s["h1_breakdown"]["hub_unsat_delta"],
            "raw_consviol": s["h1_breakdown"]["hub_consviol"],
            "b4_unsat": s["systems"]["B4_Lexical_Lattice"]["unsat_delta"],
            "b4_consviol": s["systems"]["B4_Lexical_Lattice"]["cons_viol"],
            "b4_equiv_f1": s["systems"]["B4_Lexical_Lattice"]["equiv_f1"],
            "b4_sub_recall": s["systems"]["B4_Lexical_Lattice"]["sub_recall"],
            "peak_rss_mb": s["peak_rss_mb"],
        }}, indent=2))
    elif args.cmd == "fetch-suite":
        out = fetch_public_suite(args.suite)
        print(json.dumps(out, indent=2))
    elif args.cmd == "run-public-suites":
        from clasp_ont.public_runner import run_all_public_suites

        run_all_public_suites(suites=tuple(args.suites))
    elif args.cmd == "run-frontier-oracles":
        run_frontier_cli_sweep_command(
            args.dossiers_dir,
            args.human_terminal_confirm,
            max_dossiers_per_suite=args.max_dossiers_per_suite,
        )
    elif args.cmd == "reproduce-from-cache":
        from clasp_ont.public_runner import run_all_public_suites

        replay_cached_oracles_for_toy()
        public_ready = [
            s
            for s in ("OAEI_BioML_2026", "Plant_Biodiv_Hub", "Disease_Phenotype_Hub")
            if (REPO_ROOT / "data" / "raw" / s / "provenance_sha256.json").is_file()
        ]
        if public_ready:
            run_all_public_suites(suites=tuple(public_ready))
        print("[reproduce-from-cache] Completed deterministic offline replay.")


if __name__ == "__main__":
    main()
