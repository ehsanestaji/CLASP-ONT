# Project Specification: CLASP-ONT

* **Full System Name:** **CLASP-ONT** (*Certified Lattice ASP Solver and Patching for Multi-Ontology Biological Hubs*)
* **Working Manuscript Title:** *Pairwise Valid, Globally Broken: Non-Destructive Lattice Repair and Certification of Multi-Ontology Biological Crosswalk Hubs via Answer Set Programming and Frontier Agent Oracles*
* **Target Tier-1 Venues:** *Genome Biology* (Method / Benchmark), *Nature Computational Science* (Resource / Article), *Nucleic Acids Research* (Containerized Software / Database Track), or *Bioinformatics*
* **Hardware & Budget Envelope:** Zero local open-weight LLMs ($0\text{ GB}$ GPU VRAM required), peak system memory strictly under $6\text{ GB}$ RAM on a standard CPU laptop, zero human rater labeling, zero post-publication server uptime SLA, and $0\text{ USD}$ marginal API spend via sandboxed commercial agent CLIs.

---

## 1. Executive Summary & Pre-Registered Hypotheses

Modern bioinformatics platforms integrate specialized ontologies across diseases, phenotypes, anatomy, chemicals, and plant traits using public crosswalks stored in the **Simple Standard for Sharing Ontological Mappings (SSSOM)**. While individual ontology pairs often pass basic consistency checks, merging multiple crosswalks into a unified **multi-ontology hub** triggers two widespread, silent corruptions:
1. **Global Logical Incoherence:** Overly strong `skos:exactMatch` ($s \equiv o$) assertions interact across ontology disjointness axioms to render valid biological classes mathematically unsatisfiable ($\bot$).
2. **Deductive Conservativity Violations:** Even when all classes remain satisfiable, composed crosswalks silently rewrite individual source ontologies—entailing false new intra-ontology `rdfs:subClassOf` relationships that domain curators never asserted.

Classical ontology repair engines (`LogMap`, `AML`, `Matcha`) restore coherence destructively by **deleting** offending mappings altogether, discarding valid directional biological knowledge. **CLASP-ONT** replaces destructive deletion with a lightweight, three-stage neuro-symbolic architecture:
* **CPU Symbolic Fault Isolation (`ROBOT` / `ELK` + `OAK` + `DuckDB`):** Isolates exact minimal conflict cores (typically $300$ to $800$ contested mappings across hubs exceeding $100{,}000$ classes).
* **Commercial Frontier Agent Semantic Oracles (`Claude Code`, `Codex`, `Antigravity`):** Evaluates compact definition-and-axiom dossiers for *only* the contested mappings to score candidate moves along a non-destructive **subsumption refinement lattice**.
* **Exact Global Optimization & Proof Certification (`Clingo` ASP + CEGAR):** Computes the globally optimal maximum-weight lattice assignment that guarantees $100\%$ OWL coherence and $100\%$ conservativity, exporting ready-to-merge **SSSOM** tables and **KGCL (Knowledge Graph Change Language)** upstream patches.

### Pre-Registered Hypotheses
* **H1 (Compositional Hub Breakdown):** Public biological crosswalk sets that exhibit fewer than $5\%$ conflicts pairwise induce substantial unsatisfiable-class cascades and hundreds of silent intra-ontology conservativity violations when merged into $4$-to-$6$ ontology hubs.
* **H2 (Lattice Refinement vs. Classical Deletion):** On held-out equivalence and directional subsumption gold standards (`OAEI 2026 Bio-ML`), **CLASP-ONT** retains significantly higher mapping $F_1$ and directional subsumption recall than `LogMap` greedy deletion (`B2`) and binary ASP hitting-set deletion (`B3`), while guaranteeing zero logical violations.
* **H3 (Multi-Agent Consensus Calibration):** Combining independent semantic priors from three distinct commercial frontier model families (`Claude`, `GPT/Codex`, `Gemini/Antigravity`) under deterministic `Clingo` Counterexample-Guided Abstraction Refinement (**CEGAR**) outperforms single-agent oracles and non-agentic lexical orientation heuristics (`B4`).

---

## 2. Formal Mathematical Specification

Let $\mathcal{O} = \{O_1, O_2, \dots, O_k\}$ be $k$ input OWL 2 ontologies with pairwise disjoint class namespaces $\text{Sig}(O_j)$, and let $M = \{m_1, \dots, m_n\}$ be a set of cross-ontology SSSOM correspondences where each $m_i = (s_i, p_i, o_i, w_i)$ has confidence weight $w_i \in (0, 1]$. Let $\mathcal{H}(M) = \left(\bigcup_{j=1}^k O_j\right) \cup \text{OWL}(M)$ denote the merged hub ontology.

### 2.1 Certification Invariants
A repaired hub $M'$ is **certified** if and only if both sets below are empty under OWL 2 EL/DL reasoning:
1. **Induced Unsatisfiability Set ($\text{Unsat}_{\Delta}$):**
   $$\text{Unsat}_{\Delta}(\mathcal{H}(M')) = \left\{ C \in \bigcup_{j=1}^k \text{Sig}(O_j) \;\middle|\; \mathcal{H}(M') \models C \sqsubseteq \bot \;\text{ and }\; O_j \not\models C \sqsubseteq \bot \right\} = \emptyset$$
2. **Deductive Conservativity Violation Set ($\text{ConsViol}$):**
   $$\text{ConsViol}(\mathcal{H}(M')) = \bigcup_{j=1}^k \left\{ (A, B) \in \text{Sig}(O_j) \times \text{Sig}(O_j) \;\middle|\; A \neq B,\; \mathcal{H}(M') \models A \sqsubseteq B,\; O_j \not\models A \sqsubseteq B \right\} = \emptyset$$

### 2.2 The 5-Element Refinement Poset $\mathcal{L}(m_i)$
For every contested correspondence $m_i = (s_i, \equiv, o_i, w_i)$ belonging to at least one minimal conflict core, CLASP-ONT evaluates five ordered refinement actions $a \in \mathcal{L}(m_i)$ with semantic retention factor $\rho(a) \in [0, 1]$:
1. `keep_equiv`: Retain $s_i \equiv o_i$ (`skos:exactMatch` / `owl:equivalentClass`, $\rho = 1.00$).
2. `downgrade_sub`: Refine to $s_i \sqsubseteq o_i$ (`skos:broadMatch` / `rdfs:subClassOf`, $\rho = 0.75$).
3. `downgrade_sup`: Refine to $o_i \sqsubseteq s_i$ (`skos:narrowMatch` / inverse `rdfs:subClassOf`, $\rho = 0.75$).
4. `weaken_ancestor`: Refine to $s_i \sqsubseteq \text{Parent}_1(o_i)$ or $o_i \sqsubseteq \text{Parent}_1(s_i)$ using the nearest named intra-ontology superclass ($\rho = 0.45$).
5. `negate_drop`: Remove axiom from $\mathcal{H}$ and emit an explicit SSSOM negative curation record (`predicate_modifier: Not`, $\rho = 0.00$).

### 2.3 Exact Max-Weight ASP Optimization (`Clingo`)
Given minimal conflict cores $\mathcal{K} = \{K_1, \dots, K_q\}$ (where each $K_r$ is a set of `(mapping_id, action)` pairs that jointly entail an element of $\text{Unsat}_{\Delta} \cup \text{ConsViol}$) and agent posterior probabilities $P_{\text{oracle}}(a \mid m_i)$, `Clingo` chooses exactly one action $a_i \in \mathcal{L}(m_i)$ per contested mapping to maximize integer-scaled total certified semantic utility:
$$\max_{\mathbf{a}} \sum_{m_i \in M_{\text{conflict}}} \left\lfloor 1000 \cdot w_i \cdot \rho(a_i) \cdot P_{\text{oracle}}(a_i \mid m_i) \right\rfloor \quad \text{subject to} \quad \forall K_r \in \mathcal{K},\; \neg \bigwedge_{(m_i, a) \in K_r} \text{selected}(m_i, a)$$

---

## 3. Repository Architecture & Lightweight CPU Stack

No Docker GPU containers, vector databases, or local LLM weights are used. `ROBOT` and `LogMap` run via a standard headless OpenJDK 17+ runtime capped strictly at $4\text{ GB}$ heap (`JAVA_TOOL_OPTIONS="-Xmx4G"`), and all data, graph, and logic operations run in-process in Python 3.12 managed by `uv`.

    clasp-ont/
    |-- CLAUDE.md                         # Agent governance & hard scientific invariants (copied to AGENTS.md)
    |-- SPEC.md                           # Master technical specification
    |-- pyproject.toml                    # uv-managed Python 3.12 dependencies + pinned uv.lock
    |-- Snakefile                         # Reproducible end-to-end DAG (including offline proof replay)
    |-- Makefile                          # Developer shortcuts & human-launched CLI oracle targets
    |-- configs/
    |   |-- hubs.yaml                     # URLs, PURLs, HuggingFace IDs, SHA-256 digests, seed=20261116
    |   |-- lattice_weights.yaml          # Retention utilities rho(a) and Clingo scaling constants
    |   `-- cli_oracles.yaml              # Sandboxed claude -p, codex exec, and agy -p configurations
    |-- schemas/
    |   |-- dossier.schema.json           # Input conflict-core dossier contract
    |   `-- oracle_batch.schema.json      # 25-item structured JSON output schema for commercial CLIs
    |-- vendor/
    |   `-- fetch_jars.py                 # Checksummed downloader for robot.jar (v1.9.x) & logmap-matcher.jar
    |-- src/clasp_ont/
    |   |-- ingest/
    |   |   |-- download_hubs.py          # Fetches OAEI 2026 Bio-ML, Plant/Biodiv, and Disease/Biomappings hubs
    |   |   |-- preclean.py               # Audits native single-ontology unsatisfiability & normalizes CURIEs
    |   |   `-- sssom_io.py               # Standardized SSSOM TSV/OWL conversion via sssom-py & sssom-pydantic
    |   |-- symbolic/
    |   |   |-- reasoner_elk.py           # Wrapper for ROBOT/ELK + pywhelk fast subsumption & unsat checks
    |   |   |-- conservativity.py         # DuckDB exact set-diff detector for novel intra-ontology subsumptions
    |   |   |-- conflict_cores.py         # Minimal hitting-set / justification core extractor (MUPS / MUS)
    |   |   |-- asp_lattice.lp            # Grounded Potassco Clingo optimization program
    |   |   `-- cegar_loop.py             # Outer exact verification loop guaranteeing 0 violations
    |   |-- oracles/
    |   |   |-- dossier_builder.py        # Extracts OAK definitions, synonyms, 2-hop hierarchy & axiom trace
    |   |   |-- sandbox_cli_runner.py     # Zero-API-cost isolated runner for Claude, Codex, and Antigravity CLIs
    |   |   |-- consensus.py              # Dawid-Skene / calibrated log-pool aggregation across 3 agent families
    |   |   `-- heuristic_baseline.py     # Non-LLM lexical + ontology-depth orientation baseline (B4)
    |   |-- baselines/
    |   |   |-- run_logmap_repair.py      # Classical LogMap repair wrapper (B2)
    |   |   `-- run_asp_delete.py         # Binary keep-or-delete Clingo hitting-set baseline (B3)
    |   |-- export/
    |   |   |-- kgcl_patches.py           # Emits upstream-ready KGCL diffs & certified SSSOM files
    |   |   |-- metrics_stats.py          # Equivalence/Subsumption F1, Retention, Wilcoxon, Bootstrap CIs
    |   |   `-- build_figures.py          # Apache ECharts + Plotly JSON specs, vector PDFs, results_macros.tex
    |-- results/
    |   `-- oracle_cache/                 # SHA-256 hashed CLI responses enabling 5-min reviewer replay without keys
    `-- tests/
        |-- test_toy_conservativity.py    # Synthetic diamond/disjointness conflict unit tests
        |-- test_asp_optimality.py        # Verifies Clingo lattice solver against brute-force small graphs
        |-- test_cegar_zero_leak.py       # Hard gate: asserts Unsat_Delta == 0 and ConsViol == 0 post-repair
        `-- test_cli_sandbox.py           # Asserts temp-dir isolation, stripped API keys, and disabled tools

### Pinned Python Dependencies (`pyproject.toml`)
* **Ontology & SSSOM Tooling:** `oaklib>=0.7.4`, `sssom>=0.4.22`, `sssom-pydantic>=0.6.10`, `kgcl-schema>=0.6.0`, `rdflib>=7.1.0`, `curies>=0.9.0`, `pywhelk` (or `py-horned-owl` for ultra-fast in-memory Rust OWL EL closure alongside `robot.jar`)
* **Symbolic Solver & Data Engine:** `clingo>=5.8.2`, `duckdb>=1.1.0`, `polars>=1.12.0`, `networkx>=3.4`
* **Dataset & Schema Validation:** `datasets>=3.1.0` (Hugging Face downloader for OAEI Bio-ML 2026), `jsonschema>=4.23.0`, `pydantic>=2.10`
* **Statistics & Declarative Visualization:** `scipy>=1.14.0`, `statsmodels>=0.14.0`, `plotly>=5.24.0`, `kaleido`

---

## 4. Public Datasets & Multi-Ontology Hubs (Zero License Walls)

All three evaluation suites use $100\%$ open, redistributable ontologies and crosswalks so automated scripts never stall on proprietary UMLS or SNOMED license portals:

### Suite 1: `OAEI_BioML_2026` (Ground-Truth Equivalence & Subsumption Benchmark)
* **Primary Source:** Download from Hugging Face `OAEI-ML/bio-ml` (`2026` edition tag, Apache-2.0) covering the full open `NCIT--DOID` biomedical disease/oncology ontology pair (both `refs_equiv` equivalence alignments and directional `refs_subs` subsumption reference sets), supplemented with open OAEI Disease/Phenotype and Anatomy pairs (`DOID--ORDO`, `HP--MP`, `Mouse--Human Anatomy`).
* **Objective Ground-Truth Test:** Feed noisy high-recall candidate alignments (from lexical/matcher pools and `LogMapLt` / `Biomappings` predictions) into each repair method. Measure **Equivalence $F_1$**, **Directional Subsumption Recall & $F_1$** (evaluating whether true $s \sqsubseteq o$ pairs mislabeled as $s \equiv o$ are accurately downgraded rather than deleted), **Global Coherence**, and **Conservativity**.

### Suite 2: `Plant_Biodiv_Hub` ($6$-Ontology Plant & Environmental Crosswalk Hub)
* **Component Ontologies (OBO Foundry PURLs + OAEI Biodiv):**
  1. `PO` (Plant Ontology)
  2. `TO` (Plant Trait Ontology)
  3. `FLOPO` (Flora Phenotype Ontology)
  4. `GO` (Gene Ontology biological process & cellular component plant slim/module)
  5. `CHEBI` (Plant secondary metabolites & agrochemicals module)
  6. `ENVO` + `SWEET` (OAEI Biodiversity & Ecology environmental crosswalk)
* **Crosswalk Sources:** Planteome / OBO Foundry embedded `hasDbXref` & SSSOM mappings plus OAEI Biodiv alignment sets. Quantify how pairwise-clean plant trait and environment mappings trigger cross-kingdom disjointness and conservativity violations when composed into a single $6$-ontology hub.

### Suite 3: `Disease_Phenotype_Hub` ($5$-Ontology Clinical & Mammalian Hub + `Biomappings`)
* **Component Ontologies & Crosswalks:** Merge `MONDO` (`mondo.sssom.tsv`), `DOID`, `ORDO` (Orphanet), `HPO` (Human Phenotype Ontology), and `MP` (Mammalian Phenotype Ontology) together with community-curated and predicted SSSOM tables from `biopragmatics/biomappings` (`positive.sssom.tsv` and `predictions.sssom.tsv`, CC0 1.0).
* **Real-World Deliverable:** Generate certified `KGCL` pull-request patches and repaired SSSOM artifacts pinpointing exact over-strong `exactMatch` assertions in live public repositories.

---

## 5. Step-by-Step Algorithmic Pipeline

### Stage 1: Native Baseline Audit & DuckDB Closure Indexing (`symbolic/`)
1. Run `ROBOT reason --reasoner ELK` on each input ontology $O_j$ individually. Record and isolate any pre-existing native unsatisfiable classes so hub diagnostics strictly measure **alignment-induced** defects ($\text{Unsat}_{\Delta}$).
2. Materialize the exact intra-ontology reflexive-transitive subsumption table $T_j = \{(A, B) \mid O_j \models A \sqsubseteq B\}$ for each $O_j$ into `DuckDB` (`intra_closure.parquet`).
3. Merge $\mathcal{H}(M)$ and materialize post-merge subsumptions $T_{\mathcal{H}}$. Compute $\text{ConsViol}(\mathcal{H}(M))$ in milliseconds in `DuckDB` via exact SQL anti-join:
   `SELECT h.sub, h.sup, h.ont_id FROM hub_closure h ANTI JOIN intra_closure i USING (ont_id, sub, sup) WHERE h.sub != h.sup;`

### Stage 2: Minimal Conflict Core Extraction (`conflict_cores.py`)
1. For every $C \in \text{Unsat}_{\Delta}$ and a stratified/complete cover of $(A, B) \in \text{ConsViol}$, invoke `ROBOT explain` / fast justification tracing over the bipartite mapping graph to extract minimal conflict sets $K_r \subseteq M$.
2. Take the union of contested mappings $M_{\text{conflict}} = \bigcup_r K_r$. Across typical biological hubs, $|M_{\text{conflict}}|$ represents less than $3\%$ of $|M|$ ($300$ to $800$ mappings), keeping downstream agent calls tiny.

### Stage 3: Multi-Vendor Commercial Agent Semantic Oracles (`oracles/`)
1. For each $m_i = (s_i, p_i, o_i, w_i) \in M_{\text{conflict}}$, `dossier_builder.py` uses `oaklib` to build a compact JSON dossier ($\le 350$ tokens):
   * Canonical labels, exact synonyms, and `IAO:0000115` text definitions for $s_i$ and $o_i$.
   * 2-hop named superclasses and subclasses in $O_{\text{source}}$ and $O_{\text{target}}$, plus Immediate Parent IDs $\text{Parent}_1(s_i)$ and $\text{Parent}_1(o_i)$.
   * The concise formal justification path showing *why* $s_i \equiv o_i$ causes $C \sqsubseteq \bot$ or $A \sqsubseteq B$.
2. Group $25$ shuffled dossiers per structured JSON-Schema micro-batch (`schemas/oracle_batch.schema.json`). Query three commercial subscription CLIs independently:
   * **`C1_Claude`:** `claude -p --output-format json --json-schema ... --tools "" --max-turns 1`
   * **`C2_Codex`:** `codex exec --json --output-schema ... --ephemeral`
   * **`C3_Antigravity`:** `agy -p` (run from an empty temporary directory)
3. Each oracle returns probability distribution $P_v(a \mid m_i)$ across the $5$ lattice actions plus a one-sentence ontological rationale referencing specific genus-differentia mismatches (e.g., anatomical structure vs. biological process, or species-specific vs. taxon-neutral scope).
4. `consensus.py` computes the calibrated **3-Vendor Consensus (`C4_Consensus_CLASP_ONT`)** via temperature-scaled logarithmic opinion pooling and quantifies inter-agent agreement (Fleiss' $\kappa$ and Jensen-Shannon divergence).

### Stage 4: Exact `Clingo` Lattice Optimization & CEGAR Loop (`cegar_loop.py`)
1. Ground all conflict cores $\mathcal{K}$, pairwise lattice incompatibilities, and composite weights into `asp_lattice.lp` and solve to global optimality via `clingo.Control(["--opt-mode=optN", "-t", "4"])`.
2. Apply optimal action vector $\mathbf{a}^{(t)}$ to produce candidate hub $\mathcal{H}(M^{(t)})$ and re-verify with `ELK` + `DuckDB`:
   * If $|\text{Unsat}_{\Delta}| = 0$ and $|\text{ConsViol}| = 0$, **terminate and emit cryptographic proof certificate**.
   * Otherwise, extract newly exposed secondary conflict cores $\mathcal{K}_{\text{new}}$, set $\mathcal{K} \leftarrow \mathcal{K} \cup \mathcal{K}_{\text{new}}$, and re-solve in `Clingo` (typically converges in $2$ to $4$ iterations, under $2$ minutes on CPU).
3. Export certified `.sssom.tsv` crosswalks (populating `curation_rule`, `mapping_justification = semapv:LogicalReasoning`, and `comment` rationale) alongside standard **KGCL** patch files (`create edge`, `delete edge`, `change relationship between`).

---

## 6. Zero-API-Spend ($0\text{ USD}$) & Reviewer Reproducibility Protocol

1. **Five Mandatory CLI Sandbox Rules (`sandbox_cli_runner.py`):**
   * **Scrub Metered Keys:** Strip `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, and `GEMINI_API_KEY` from subprocess environments so CLIs authenticate strictly via cached subscription credentials.
   * **Empty Temporary Working Directory:** Execute every CLI subprocess inside `tempfile.TemporaryDirectory()` outside the repository so agents cannot read `SPEC.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or gold reference files on disk.
   * **Zero Tool Access:** Disable all file/shell/MCP tools (`--tools ""` for Claude; `--ephemeral` for Codex) so the CLI behaves purely as a structured text-to-JSON reasoner.
   * **Micro-Batch Parity Check:** Verify on $50$ conflict dossiers that $25$-item micro-batching matches single-item oracle agreement within $\pm 2.0\%$ before running all hubs ($\sim 25\text{ to }35$ CLI calls total per vendor).
   * **Human-Launched Terminal Execution:** Coding agents build and test the pipeline using deterministic mock/heuristic oracles (`B4`), then output a single command (`make run-frontier-oracles`) for the researcher to run directly from a standard terminal with `time.sleep(3)` pacing—complying $100\%$ with provider Terms of Service.
2. **Zero-Key Offline Peer-Review Replay:** Every raw CLI JSON response is timestamped, SHA-256 hashed, and stored in `results/oracle_cache/`. Any journal referee can run `uv run snakemake reproduce_from_cache --cores 4` on a standard CPU laptop with **zero API keys and zero subscriptions** to re-execute `Clingo`, `ELK`, `DuckDB`, and all statistical tests from scratch in under $5$ minutes.

---

## 7. Competitor Matrix, Evaluation Metrics & Statistical Tests

### 7.1 Evaluated Baselines & Systems
* **`B1_Raw_Hub`:** Unrepaired union of input SSSOM crosswalks.
* **`B2_LogMap_Repair`:** Classical logic-based greedy deletion via standalone `LogMap` repair module.
* **`B3_ASP_Binary_Delete`:** Exact `Clingo` minimal-weight hitting set restricted to binary `{keep_equiv, negate_drop}` (isolates the exact gain of the 5-point subsumption lattice over optimal deletion).
* **`B4_Lexical_Lattice_Heuristic`:** Non-LLM orientation search ranking lattice moves via TF-IDF definition overlap and ontology tree depth.
* **`C1_Claude` / `C2_Codex` / `C3_Antigravity`:** Individual commercial frontier oracle + `Clingo` CEGAR.
* **`C4_CLASP_ONT` (Proposed):** 3-vendor calibrated consensus oracle + `Clingo` exact Max-Weight Lattice CEGAR.

### 7.2 Objective Metrics & Statistical Rigor (`metrics_stats.py`)
* **Certification Completeness:** Post-repair $|\text{Unsat}_{\Delta}|$ and $|\text{ConsViol}|$ (must equal $0$ for `C1`--`C4` and `B3`).
* **Benchmark Accuracy (`Suite 1`):** Equivalence Precision, Recall, and $F_1$; Directional Subsumption Recall and $F_1$ on `OAEI 2026 Bio-ML` `refs_subs`; and Semantic Knowledge Retention ($\sum_{i} w_i \rho(a_i) / \sum_i w_i$).
* **Hub Preservation (`Suites 2 & 3`):** Fraction of contested cross-ontology bridges preserved as valid directional subsumptions rather than deleted, plus novel valid cross-ontology entailments unlocked after hub certification.
* **Statistical Tests:** $10{,}000$-iteration paired bootstrap $95\%$ confidence intervals; two-sided paired **Wilcoxon signed-rank** and **McNemar's** tests across conflict cores with **Benjamini-Hochberg FDR** ($\alpha = 0.01$).

---

## 8. Automated Declarative Figures & LaTeX Macros (`build_figures.py`)

Every figure is exported as a **declarative JSON specification** (**Apache ECharts** and **Plotly** JSON schemas for zero-hallucination visual inspection), static vector **PDF/SVG**, and programmatic LaTeX macros (`paper/results_macros.tex`):
* **Figure 1 (Compositional Breakdown Sankey / Chord Diagram):** Pairwise conflict counts vs. hub-merged $\text{Unsat}_{\Delta}$ and $\text{ConsViol}$ explosion across `Plant_Biodiv_Hub` and `Disease_Phenotype_Hub`.
* **Figure 2 (OAEI 2026 Bio-ML Accuracy & Subsumption Recovery):** Grouped bar chart with $95\%$ bootstrap CIs comparing `B1`--`B4` and `C1`--`C4` on Equivalence $F_1$ and recovered Directional Subsumption Recall.
* **Figure 3 (Destructive Deletion vs. Lattice Refinement Breakdown):** Stacked horizontal proportion chart showing exact mapping fates (`keep_equiv`, `downgrade_sub`, `downgrade_sup`, `weaken_ancestor`, `negate_drop`) across all three suites.
* **Figure 4 (Multi-Agent Oracle Calibration & Agreement Matrix):** Heatmap and reliability diagram comparing `Claude`, `Codex`, `Antigravity`, and `Consensus` probabilities against held-out OAEI ground truth.
* **Figure 5 (CEGAR Convergence & CPU Scaling Telemetry):** Wall-clock runtime (seconds) and memory footprint ($\text{MB}$) across hub sizes up to $150{,}000+$ classes, proving laptop-scale execution.

---

## 9. Phased Execution Gates for the Coding Agent

1. **Phase 0 -- Toy Hub & CPU Verifier Smoke Slice (Day 1 Mandatory Gate):**
   * Set up `uv` project, fetch `robot.jar`, and synthesize a deterministic $200$-class, $4$-ontology toy biological hub containing $15$ planted incoherence and conservativity traps (diamond cycles, cross-kingdom disjointness, and false `exactMatch` bridges with known ground-truth directional repairs).
   * Implement `reasoner_elk.py`, `conservativity.py`, `conflict_cores.py`, `asp_lattice.lp`, `cegar_loop.py`, `heuristic_baseline.py` (`B4`), and `kgcl_patches.py`.
   * Pass all $4$ `pytest` suites (`uv run pytest -q`), confirming $|\text{Unsat}_{\Delta}| = 0$, $|\text{ConsViol}| = 0$, peak RAM under $2\text{ GB}$, and end-to-end figure JSON + `results_macros.tex` export. **PAUSE and report Phase 0 summary.**
2. **Phase 1 -- Public Hub Ingestion & Compositional Audit (`H1` Discovery):**
   * Download checksummed `Suite 1` (`OAEI-ML/bio-ml` 2026), `Suite 2` (`Plant_Biodiv_Hub`), and `Suite 3` (`Disease_Phenotype_Hub`).
   * Run pairwise vs. full-hub `ELK` + `DuckDB` audits (`B1`), `LogMap` repair (`B2`), binary ASP deletion (`B3`), and lexical lattice heuristic (`B4`), exporting all extracted conflict dossiers (`data/dossiers/*.jsonl`).
3. **Phase 2 -- Commercial CLI Oracle Sweep & Consensus Certification (`H2` & `H3`):**
   * Validate `sandbox_cli_runner.py` isolation tests and expose `make run-frontier-oracles`.
   * After the human researcher runs the terminal micro-batches ($\sim 30$ calls per CLI), execute `C1`--`C4` through the `Clingo` CEGAR loop, populate `results/oracle_cache/`, and generate certified `.sssom.tsv` and `.kgcl` patch bundles.
4. **Phase 3 -- Statistical Synthesis, Upstream PR Bundle & Manuscript Package:**
   * Compile final ECharts/Plotly figures, `paper/results_macros.tex`, and top $15$ highest-impact `KGCL` patches formatted as ready-to-submit GitHub pull requests for `Biomappings` and OBO Foundry repositories, plus the 5-element 2026 journal AI disclosure manifest.

---

## 10. Agent Governance Contract (Copy Verbatim to `CLAUDE.md` and `AGENTS.md`)

1. **Zero Fabricated Metrics or Synthetic Placeholders:** Never hardcode dummy scores, mocked reasoner counts, or placeholder p-values outside `tests/`. Every reported unsatisfiability and conservativity count must come from live `ELK` and `DuckDB` execution.
2. **Hard Mathematical Certification Gate:** No repaired alignment from `C1`--`C4` may be marked complete unless `cegar_loop.py` independently verifies $|\text{Unsat}_{\Delta}| = 0$ and $|\text{ConsViol}| = 0$ on the re-merged OWL hub.
3. **Strict Memory Ceiling:** Always invoke JVM tools (`robot`, `logmap`) with `-Xmx4G` and stream tabular joins through `DuckDB` so total RSS memory never exceeds $6\text{ GB}$. Never download local LLM checkpoints (`torch`/`transformers`/`vllm` are prohibited).
4. **CLI Sandbox & ToS Compliance:** Never execute `claude`, `codex`, `agy`, or `cursor-agent` inside the repository working tree, never leave billing API keys in subprocess `env`, and never autonomously loop third-party subscription CLIs from inside an agent session. Build and verify everything against `B3`/`B4` and cached fixtures first, then print `make run-frontier-oracles` for human terminal launch.
5. **Single Source of Numerical Truth:** Every number in manuscript tables or text must be read programmatically from `paper/results_macros.tex` and `results/summary_metrics.json`. Run `uv run ruff check src/ tests/` and `uv run pytest -q` before every git commit.