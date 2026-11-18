# CLASP-ONT

**Pairwise Valid, Globally Broken: Non-Destructive Lattice Repair and Certification of Multi-Ontology Biological Crosswalk Hubs**

`CLASP-ONT` (*Certified Lattice ASP Solver and Patching for Multi-Ontology Biological Hubs*) is a CPU-only verification and non-destructive repair engine for multi-ontology biological crosswalk hubs (`SSSOM` / OWL 2 EL). Rather than resolving alignment conflicts through greedy pairwise deletion, `CLASP-ONT` optimizes over a five-element subsumption refinement poset (`keep_equiv`, `downgrade_sub`, `downgrade_sup`, `weaken_ancestor`, `negate_drop`) using two-tier conjunctive Horn `0-1` breadth-first search, monotonic `BOTTOM_BOTTOM` syntactic-locality QuickXplain, and Answer Set Programming (`Clingo`) Counterexample-Guided Abstraction Refinement (CEGAR).

Every output alignment is independently verified by `ELK` and `DuckDB` to satisfy zero alignment-induced unsatisfiable classes (`|Unsat_Delta| = 0`) and zero intra-ontology conservativity violations (`|ConsViol| = 0`), exporting certified `.sssom.tsv` crosswalks and Knowledge Graph Change Language (`.kgcl`) pull-request patches.

## Why Hub-Level Certification Matters & Who Uses `CLASP-ONT`

Evaluating ontology crosswalks one pair at a time misses **39%–56%** of hub-emergent unsatisfiable classes (`+63.8%` to `+125.4%` jump upon 4–6 ontology composition) and up to **234,152** silent intra-ontology conservativity violations caused by cross-ontology OWL 2 EL defined-class intersections. Existing pairwise repair tools (`LogMap`) leave thousands of hub-level violations unresolved, while optimal binary hitting-set deletion (`B3`) discards **16%–77%** of valid biological bridges.

| Target Community / Platform | Core Ontologies | Failure Under Pairwise / Delete Workflows | Value Delivered by `CLASP-ONT` (`B4`) |
|---|---|---|---|
| **Rare-disease & mammalian phenomics** | `MONDO`, `ORDO`, `DOID`, `HP`, `MP` | `234,152` silent disease subsumptions or `43.2%` links deleted | **`0` violations**; **`+48,863`** certified cross-species entailments over binary deletion |
| **Oncology & drug-target KGs** | `NCIT`, `DOID`, `OMIM`, `ORDO` | Benign/malignant conflation (`NCIT:C2949` vs `DOID:12759`); `LogMap` `F1 = 0.009` | Certified directional hierarchy; **`F1 = 0.663`** (`-82.5%` fewer deletions) |
| **Plant genomics & GxE trait portals** | `PO`, `TO`, `FLOPO`, `GO`, `CHEBI`, `ENVO` | `40,698` unsatisfiable classes (`84%` collapse) or `76.8%` links deleted | **`2.19x`** retained cross-ontology utility (`0.243 -> 0.532`) at **`0` violations** |
| **Ontology curators (`ODK`, `Biomappings`)** | Any `SSSOM` + OWL 2 EL hub | Pairwise CI misses multi-hop cycles & foreign existential fillers | Laptop/CI gate (**`< 4.3 GB` RAM**, zero GPU) + ready-to-merge **`.kgcl` PR patches** |

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

- `src/clasp_ont/ingest/`: Public suite downloaders, SHA-256 verification, native baseline isolation, and SSSOM I/O
- `src/clasp_ont/symbolic/`: Headless `ELK` closure wrapper, `DuckDB` anti-join conservativity detector, two-tier Horn/QuickXplain core extractor, `Clingo` ASP encoding, and CEGAR loop
- `src/clasp_ont/oracles/`: Compact OAK conflict dossier builder, deterministic TF-IDF/depth lattice heuristic (`B4`), key-scrubbed CLI sandbox runner, and log-opinion-pool consensus
- `src/clasp_ont/export/`: Certified SSSOM & KGCL patch emitters, paired bootstrap/Wilcoxon/McNemar statistics, ECharts/Plotly/vector figure renderer, and upstream PR bundler
- `results/`: Certified `.sssom.tsv` crosswalks, `.kgcl` patches, vector PDF/SVG figures, upstream PR bundle (`results/upstream_prs/top15_kgcl_pull_requests.md`), and `summary_metrics.json`
- `paper/`: Programmatic `results_macros.tex`, `main.tex` (`main.pdf`), `supplementary.tex` (`supplementary.pdf`), `cover_letter.tex` (`cover_letter.pdf`), and `references.bib`

## Authors & Affiliation

- **Ehsan Estaji** ([0009-0005-0838-228X](https://orcid.org/0009-0005-0838-228X), `ehsan.estaji@umu.se`)
- **Jian-Feng Mao** ([0000-0001-9735-8516](https://orcid.org/0000-0001-9735-8516), `jianfeng.mao@umu.se`)

Umeå Plant Science Centre (UPSC), Department of Plant Physiology, Umeå University, SE-901 87 Umeå, Sweden.

## License

Apache License 2.0 (`LICENSE`).
