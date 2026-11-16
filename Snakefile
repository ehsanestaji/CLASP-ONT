# Reproducible Snakemake DAG for CLASP-ONT (including zero-key offline peer-review replay)

rule all:
    input:
        "results/summary_metrics.json",
        "paper/results_macros.tex",
        "results/figures/fig1_compositional_breakdown.pdf",
        "results/figures/fig2_accuracy_subsumption.pdf",
        "results/figures/fig3_lattice_fate_breakdown.pdf",
        "results/figures/fig4_oracle_agreement.pdf",
        "results/figures/fig5_cegar_telemetry.pdf",

rule vendor_jars:
    output:
        "vendor/robot.jar",
        "vendor/classes/ElkClosureRunner.class",
    shell:
        "uv run python vendor/fetch_jars.py"

rule phase0_smoke:
    input:
        "vendor/robot.jar",
        "vendor/classes/ElkClosureRunner.class",
    output:
        "data/dossiers/Phase0_Toy_Hub.jsonl",
        "results/certified/Phase0_Toy_Hub.B4_Lexical_Lattice.sssom.tsv",
        "results/patches/Phase0_Toy_Hub.B4_Lexical_Lattice.kgcl",
    shell:
        "uv run clasp-ont phase0-smoke"

rule reproduce_from_cache:
    input:
        "vendor/robot.jar",
        "vendor/classes/ElkClosureRunner.class",
    output:
        "results/summary_metrics.json",
        "paper/results_macros.tex",
        "results/figures/fig1_compositional_breakdown.pdf",
        "results/figures/fig2_accuracy_subsumption.pdf",
        "results/figures/fig3_lattice_fate_breakdown.pdf",
        "results/figures/fig4_oracle_agreement.pdf",
        "results/figures/fig5_cegar_telemetry.pdf",
    shell:
        "uv run clasp-ont reproduce-from-cache"
