"""Hard certification gate test: asserts post-repair |Unsat_Delta| == 0, |ConsViol| == 0, and <2GB RSS."""

from __future__ import annotations

from pathlib import Path

from clasp_ont.cli import run_phase0_smoke
from clasp_ont.oracles.consensus import aggregate_log_opinion_pool
from clasp_ont.oracles.heuristic_baseline import score_lexical_lattice_heuristic
from clasp_ont.symbolic.cegar_loop import OracleDecision, run_clingo_cegar
from clasp_ont.symbolic.conflict_cores import extract_conflict_cores
from clasp_ont.symbolic.conservativity import audit_merged_hub


def test_phase0_end_to_end_zero_leak_and_exports(tmp_path: Path) -> None:
    summary = run_phase0_smoke(out_root=tmp_path)
    toy_stats = summary["suites"]["Phase0_Toy_Hub"]

    # Hard memory ceiling (< 2 GB = 2048 MB for Phase 0)
    assert toy_stats["peak_rss_mb"] < 2048.0
    assert toy_stats["max_dossier_tokens"] <= 350

    b3 = toy_stats["systems"]["B3_ASP_Binary_Delete"]
    b4 = toy_stats["systems"]["B4_Lexical_Lattice"]

    # Zero-leak invariant
    assert b3["unsat_delta"] == 0
    assert b3["cons_viol"] == 0
    assert b4["unsat_delta"] == 0
    assert b4["cons_viol"] == 0

    # H2 directional subsumption & semantic retention superiority over binary deletion
    assert b4["semantic_retention"] > b3["semantic_retention"]
    assert b4["sub_recall"] > b3["sub_recall"]

    # Verify all declarative figure JSONs, PDFs, KGCL patches, SSSOM, and LaTeX macros exist
    assert (tmp_path / "results" / "summary_metrics.json").is_file()
    assert (tmp_path / "paper" / "results_macros.tex").is_file()
    assert (tmp_path / "results" / "patches" / "Phase0_Toy_Hub.B4_Lexical_Lattice.kgcl").is_file()
    assert (tmp_path / "results" / "certified" / "Phase0_Toy_Hub.B4_Lexical_Lattice.sssom.tsv").is_file()
    for fname in (
        "fig1_compositional_breakdown",
        "fig2_accuracy_subsumption",
        "fig3_lattice_fate_breakdown",
        "fig4_oracle_agreement",
        "fig5_cegar_telemetry",
    ):
        assert (tmp_path / "results" / "figures" / f"{fname}.echarts.json").is_file()
        assert (tmp_path / "results" / "figures" / f"{fname}.plotly.json").is_file()
        assert (tmp_path / "results" / "figures" / f"{fname}.pdf").is_file()


def test_consensus_c4_cegar_zero_leak(tmp_path: Path) -> None:
    from clasp_ont.ingest.preclean import run_native_preclean
    from clasp_ont.ingest.sssom_io import write_mappings_owl
    from clasp_ont.ingest.toy_hub import build_toy_hub

    bundle = build_toy_hub(tmp_path / "toy")
    native = run_native_preclean(
        "Phase0_Toy_Hub", bundle.ontology_paths, tmp_path / "work", bundle.namespace_hints
    )
    raw_bridge = write_mappings_owl(bundle.mappings, None, tmp_path / "raw.owl")
    raw_audit = audit_merged_hub(native, raw_bridge, tmp_path / "elk_raw")
    cores, traces = extract_conflict_cores(native, bundle.mappings, raw_audit)

    base_b4 = score_lexical_lattice_heuristic(native, bundle.mappings, traces)
    # Construct 3 distinct calibrated synthetic vendor perturbations to test consensus + CEGAR certification
    v1 = base_b4
    v2 = {
        k: OracleDecision(
            mapping_id=k,
            probs={a: (p * 1.1 if a == "downgrade_sub" else p * 0.97) for a, p in d.probs.items()},
            preferred_weaken_direction=d.preferred_weaken_direction,
            rationale="Synthetic vendor 2",
        )
        for k, d in base_b4.items()
    }
    v3 = {
        k: OracleDecision(
            mapping_id=k,
            probs={a: (p * 1.1 if a == "downgrade_sup" else p * 0.97) for a, p in d.probs.items()},
            preferred_weaken_direction=d.preferred_weaken_direction,
            rationale="Synthetic vendor 3",
        )
        for k, d in base_b4.items()
    }
    # Normalize v2, v3
    for vm in (v2, v3):
        for d in vm.values():
            s = sum(d.probs.values())
            d.probs = {a: v / s for a, v in d.probs.items()}

    cons = aggregate_log_opinion_pool({"C1": v1, "C2": v2, "C3": v3})
    assert -1.0 <= cons.fleiss_kappa <= 1.0
    assert 0.0 <= cons.mean_js_divergence <= 1.0

    c4_res = run_clingo_cegar(
        system_id="C4_CLASP_ONT",
        native_audit=native,
        mappings=bundle.mappings,
        initial_cores=cores,
        oracle_decisions=cons.decisions,
        work_dir=tmp_path / "c4_work",
    )
    assert c4_res.final_audit.unsat_delta == []
    assert c4_res.final_audit.cons_violations == []
    assert c4_res.final_audit.is_certified
