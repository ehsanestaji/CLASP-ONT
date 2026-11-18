# CLASP-ONT

**Certified Lattice ASP Solver and Patching for Multi-Ontology Biological Hubs**

`CLASP-ONT` is a CPU-only verification and non-destructive repair engine for multi-ontology biological crosswalk hubs (`SSSOM` / OWL 2 EL). Rather than resolving alignment conflicts through greedy pairwise deletion, `CLASP-ONT` optimizes over a five-element subsumption refinement poset (`keep_equiv`, `downgrade_sub`, `downgrade_sup`, `weaken_ancestor`, `negate_drop`) using two-tier conjunctive Horn `0-1` breadth-first search, monotonic `BOTTOM_BOTTOM` syntactic-locality QuickXplain, and Answer Set Programming (`Clingo`) Counterexample-Guided Abstraction Refinement (CEGAR).

Every output alignment is independently verified by `ELK` and `DuckDB` to satisfy zero alignment-induced unsatisfiable classes (`|Unsat_Delta| = 0`) and zero intra-ontology conservativity violations (`|ConsViol| = 0`), exporting certified `.sssom.tsv` crosswalks and Knowledge Graph Change Language (`.kgcl`) patches.

## Certified Empirical Summary

| Suite (`Ontologies` / `Classes` / `Mappings`) | Pairwise -> Merged Hub `Unsat_Delta` | Raw Hub `ConsViol` | `B2_LogMap` (`Unsat` / `ConsViol`) | `B3_ASP_Delete` (`Unsat` / `ConsViol`, Dropped, Ret.) | `B4_Lexical_Lattice` (`Unsat` / `ConsViol`, Dropped, Ret.) | Peak RSS |
|---|---|---|---|---|---|---|
| `Phase0_Toy_Hub` (`4` / `200` / `60`) | `22` -> **`26`** | `30` | `0` / `18` | **`0` / `0`**, drops `15`, `0.774` | **`0` / `0`**, drops **`1`**, **`0.912`** (`F1 = 0.945`) | `491.3 MB` |
| `OAEI_BioML_2026` (`4` / `43,150` / `16,694`) | `12,223` -> **`23,619`** (`+93.2%`) | `33,192` | `4` / `5,126` | **`0` / `0`**, drops `2,732`, `0.845` | **`0` / `0`**, drops **`479`** (`-82.5%`), **`0.873`** (`F1 = 0.663`) | `3,438.1 MB` |
| `Plant_Biodiv_Hub` (`6` / `48,407` / `943`) | `18,057` -> **`40,698`** (`+125.4%`) | `805` | `17,518` / `15` | **`0` / `0`**, drops `724`, `0.243` | **`0` / `0`**, drops **`277`** (`-61.7%`), **`0.532`** | `2,737.4 MB` |
| `Disease_Phenotype_Hub` (`5` / `96,273` / `24,038`) | `6,499` -> **`10,644`** (`+63.8%`) | `234,152` | `1,607` / `20,045` | **`0` / `0`**, drops `10,379`, `0.568` | **`0` / `0`**, drops **`2,352`** (`-77.3%`), **`0.735`** | `4,289.1 MB` |

## Requirements

- Python `3.12` managed via [`uv`](https://docs.astral.sh/uv/)
- Headless OpenJDK `17+` (`21` recommended; JVM heap strictly capped at `-Xmx4G`)
- Standard CPU laptop/workstation (`< 6 GB` total RAM; zero GPU or local language-model weights)

## Quickstart & Reproducibility

```bash
# 1. Sync locked Python 3.12 environment and fetch/compile checksummed ROBOT/ELK & LogMap JARs
make setup

# 2. Run linter and unit/integration test suite (9 tests)
make lint
make test

# 3. Run deterministic 200-class Phase 0 verification smoke gate
make phase0

# 4. Download SHA-256 verified public hubs and execute full B1..B4 audit & CEGAR certification
make phase1-public

# 5. Optional: human-launched sandboxed 3-vendor CLI oracle sweep + offline cache replay
make run-frontier-oracles
make certify-from-cache
```

## Repository Layout

- `src/clasp_ont/ingest/` — Public suite downloaders, SHA-256 verification, native baseline isolation, and SSSOM I/O
- `src/clasp_ont/symbolic/` — Headless `ELK` closure wrapper, `DuckDB` anti-join conservativity detector, two-tier Horn/QuickXplain core extractor, `Clingo` ASP encoding, and CEGAR loop
- `src/clasp_ont/oracles/` — Compact OAK conflict dossier builder, deterministic TF-IDF/depth lattice heuristic (`B4`), key-scrubbed CLI sandbox runner, and log-opinion-pool consensus
- `src/clasp_ont/export/` — Certified SSSOM & KGCL patch emitters, paired bootstrap/Wilcoxon/McNemar statistics, ECharts/Plotly/vector figure renderer, and upstream PR bundler
- `results/` — Certified `.sssom.tsv` crosswalks, `.kgcl` patches, vector PDF/SVG figures, upstream PR bundle, and `summary_metrics.json`
- `paper/` — Programmatic `results_macros.tex`, `main.tex` (`main.pdf`), `supplementary.tex` (`supplementary.pdf`), and `references.bib`

## License

Apache License 2.0 (`LICENSE`).
