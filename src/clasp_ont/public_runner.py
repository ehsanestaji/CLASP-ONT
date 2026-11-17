"""End-to-end public multi-ontology hub runner (Phase 1 audit, Phase 2 cache certification, Phase 3 synthesis).

Executes live ELK + DuckDB + LogMap + Clingo CEGAR across:
  - Suite 1: OAEI_BioML_2026 (NCIT, DOID, ORDO, OMIM; held-out refs_equiv & refs_subs ground truth)
  - Suite 2: Plant_Biodiv_Hub (PO, TO, FLOPO, GO_PLANT, CHEBI_PLANT, ENVO; 6-ontology hub)
  - Suite 3: Disease_Phenotype_Hub (MONDO, DOID, ORDO, HP, MP + Mondo & Biomappings SSSOM; 5-ontology hub)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import polars as pl

from clasp_ont.baselines.run_asp_delete import run_binary_asp_delete
from clasp_ont.baselines.run_logmap_repair import LOGMAP_JAR, run_logmap_repair
from clasp_ont.export.build_figures import build_all_figures_and_macros
from clasp_ont.export.kgcl_patches import export_certified_sssom_and_kgcl
from clasp_ont.export.metrics_stats import (
    apply_bh_fdr,
    evaluate_alignment_against_gold,
    paired_bootstrap_and_tests,
    run_pairwise_vs_hub_audit,
)
from clasp_ont.export.upstream_pr_bundle import (
    build_top15_upstream_pr_bundle,
    rebuild_top15_from_saved_artifacts,
)
from clasp_ont.ingest.download_hubs import extract_chebi_plant_module, fetch_public_suite
from clasp_ont.ingest.preclean import (
    NativeHubAudit,
    extract_embedded_crosswalks_fast,
    run_native_preclean,
    stream_owl_annotations_and_xrefs,
)
from clasp_ont.ingest.sssom_io import (
    MappingRecord,
    normalize_predicate,
    write_mappings_owl,
    write_sssom_tsv,
)
from clasp_ont.oracles.consensus import aggregate_log_opinion_pool
from clasp_ont.oracles.dossier_builder import build_dossiers
from clasp_ont.oracles.heuristic_baseline import score_lexical_lattice_heuristic
from clasp_ont.oracles.sandbox_cli_runner import run_vendor_sweep
from clasp_ont.symbolic.cegar_loop import (
    ALL_LATTICE_ACTIONS,
    OracleDecision,
    load_lattice_config,
    run_clingo_cegar,
)
from clasp_ont.symbolic.conflict_cores import extract_conflict_cores
from clasp_ont.symbolic.conservativity import audit_merged_hub
from clasp_ont.symbolic.reasoner_elk import resolve_java_bin

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_hub_iri_resolver(class_to_ont: dict[str, str]) -> dict[str, str]:
    """Build fast lookup mapping full IRIs, OBO PURL variants, and PREFIX:LOCAL_ID CURIEs to hub IRIs."""
    lookup: dict[str, str] = {}
    for iri in class_to_ont:
        lookup[iri] = iri
        if "/obo/" in iri:
            tail = iri.rsplit("/obo/", 1)[-1]
            if "_" in tail:
                pfx, code = tail.split("_", 1)
                up = pfx.upper()
                lookup[f"{up}:{code}"] = iri
                lookup[f"{pfx.lower()}:{code}"] = iri
        elif "Thesaurus.owl#" in iri:
            code = iri.rsplit("#", 1)[-1]
            lookup[f"NCIT:{code}"] = iri
            lookup[f"ncit:{code}"] = iri
            lookup[f"NCI:{code}"] = iri
            lookup[f"http://purl.obolibrary.org/obo/NCIT_{code}"] = iri
        elif "Orphanet_" in iri:
            code = iri.rsplit("Orphanet_", 1)[-1]
            for p in ("ORDO", "ordo", "Orphanet", "orphanet", "ORPHA", "orpha"):
                lookup[f"{p}:{code}"] = iri
            lookup[f"http://purl.obolibrary.org/obo/ORDO_{code}"] = iri
        elif "omim" in iri.lower():
            code = iri.rstrip("/").rsplit("/", 1)[-1].rsplit("_", 1)[-1].rsplit("#", 1)[-1]
            for p in ("OMIM", "omim", "MIM"):
                lookup[f"{p}:{code}"] = iri
            lookup[f"https://omim.org/entry/{code}"] = iri
            lookup[f"http://omim.org/entry/{code}"] = iri
            lookup[f"http://purl.obolibrary.org/obo/OMIM_{code}"] = iri
    return lookup


def _norm_label(s: str) -> str:
    s_clean = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    toks = [t for t in s_clean.split() if t and t not in {"disease", "syndrome", "disorder", "of", "the", "and", "in", "plant", "trait"}]
    return " ".join(toks)


def _run_logmap_matcher_pair(ont1: Path, ont2: Path, heap_flag: str = "-Xmx4G") -> list[tuple[str, str, float]]:
    """Run standalone LogMap 4.0 MATCHER on (ont1, ont2) to harvest candidate alignments."""
    if not LOGMAP_JAR.is_file():
        return []
    java_bin = resolve_java_bin()
    with tempfile.TemporaryDirectory(prefix="clasp_lm_match_") as td:
        tmp_dir = Path(td)
        out_dir = tmp_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY"):
            env.pop(k, None)
        env["JAVA_TOOL_OPTIONS"] = heap_flag
        cmd = [
            str(java_bin),
            heap_flag,
            "--add-opens",
            "java.base/java.lang=ALL-UNNAMED",
            "--add-opens",
            "java.base/java.util=ALL-UNNAMED",
            "-jar",
            str(LOGMAP_JAR),
            "MATCHER",
            f"file:{ont1.resolve()}",
            f"file:{ont2.resolve()}",
            str(out_dir.resolve()) + "/",
            "false",
        ]
        proc = subprocess.run(cmd, cwd=str(tmp_dir), capture_output=True, text=True, env=env, timeout=300)
        if proc.returncode != 0:
            return []
        txt_file = out_dir / "logmap2_mappings.txt"
        pairs: list[tuple[str, str, float]] = []
        target_files = [txt_file] if txt_file.is_file() else list(out_dir.glob("*.txt"))
        for tf in target_files:
            for line in tf.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and parts[0].startswith("http") and parts[1].startswith("http"):
                    if len(parts) >= 5 and parts[4] != "CLS":
                        continue
                    try:
                        conf = float(parts[3])
                    except ValueError:
                        conf = 0.85
                    pairs.append((parts[0], parts[1], max(0.50, min(0.99, conf))))
        return pairs


def _lexical_logmaplt_candidates(
    native: NativeHubAudit,
    ont_pairs: list[tuple[str, str]],
    max_per_pair: int = 2500,
) -> list[tuple[str, str, float]]:
    """Fast LogMapLt-style normalized label/synonym + head-modifier containment candidate generator.

    Operates strictly on native ontology labels/synonyms (zero access to held-out reference alignments).
    """
    by_ont_exact: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    by_ont_tokens: dict[str, dict[str, list[tuple[str, set[str]]]]] = defaultdict(lambda: defaultdict(list))

    for iri, tm in native.term_metadata.items():
        oid = tm.ont_id
        for surface in [tm.label, *tm.synonyms[:3]]:
            nl = _norm_label(surface)
            if not nl or len(nl) < 3:
                continue
            if iri not in by_ont_exact[oid][nl] and len(by_ont_exact[oid][nl]) < 4:
                by_ont_exact[oid][nl].append(iri)
            toks = nl.split()
            if 1 <= len(toks) <= 5:
                head = toks[-1]
                if len(head) >= 4 and len(by_ont_tokens[oid][head]) < 80:
                    by_ont_tokens[oid][head].append((iri, set(toks)))

    cands: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()

    for o1, o2 in ont_pairs:
        pair_cands: list[tuple[str, str, float]] = []
        # 1. Exact normalized stem matches
        shared_keys = set(by_ont_exact[o1].keys()) & set(by_ont_exact[o2].keys())
        for k in sorted(shared_keys):
            for s_iri in by_ont_exact[o1][k]:
                for t_iri in by_ont_exact[o2][k]:
                    pk = (min(s_iri, t_iri), max(s_iri, t_iri))
                    if pk not in seen:
                        seen.add(pk)
                        pair_cands.append((s_iri, t_iri, 0.91))

        # 2. Head-noun + high Jaccard modifier containment (captures s <= o over-asserted as s == o by lexical matchers)
        shared_heads = set(by_ont_tokens[o1].keys()) & set(by_ont_tokens[o2].keys())
        for h in sorted(shared_heads):
            l1 = by_ont_tokens[o1][h]
            l2 = by_ont_tokens[o2][h]
            if len(l1) * len(l2) > 1600:
                continue
            for s_iri, s_toks in l1:
                for t_iri, t_toks in l2:
                    if s_toks == t_toks:
                        continue
                    if not (t_toks < s_toks or s_toks < t_toks):
                        continue
                    jacc = len(s_toks & t_toks) / len(s_toks | t_toks)
                    if jacc >= 0.60:
                        pk = (min(s_iri, t_iri), max(s_iri, t_iri))
                        if pk not in seen:
                            seen.add(pk)
                            pair_cands.append((s_iri, t_iri, round(0.72 + 0.18 * jacc, 4)))

        pair_cands.sort(key=lambda x: x[2], reverse=True)
        cands.extend(pair_cands[:max_per_pair])
    return cands


def _load_sssom_resolved(
    tsv_path: Path,
    resolver: dict[str, str],
    class_to_ont: dict[str, str],
) -> list[tuple[str, str, str, float]]:
    """Parse SSSOM TSV resolving CURIEs/URIs through hub `resolver`."""
    if not tsv_path.is_file():
        return []
    lines = [ln for ln in tsv_path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []
    import io

    df = pl.read_csv(io.StringIO("\n".join(lines)), separator="\t", infer_schema_length=5000, ignore_errors=True, quote_char=None)
    cols = set(df.columns)
    if "subject_id" not in cols or "object_id" not in cols:
        return []
    out: list[tuple[str, str, str, float]] = []
    for row in df.iter_rows(named=True):
        mod = str(row.get("predicate_modifier") or "").strip()
        if mod.lower() == "not":
            continue
        raw_s = str(row.get("subject_id") or "").strip()
        raw_o = str(row.get("object_id") or "").strip()
        s_iri = resolver.get(raw_s)
        o_iri = resolver.get(raw_o)
        if not s_iri or not o_iri or s_iri == o_iri:
            continue
        if class_to_ont.get(s_iri) == class_to_ont.get(o_iri):
            continue
        pred = normalize_predicate(str(row.get("predicate_id") or "skos:exactMatch"))
        try:
            conf = float(row.get("confidence") or 0.90)
        except ValueError:
            conf = 0.90
        out.append((s_iri, pred, o_iri, max(0.50, min(0.99, conf))))
    return out


def _load_mondo_implied_cliques(
    mondo_tsv: Path,
    resolver: dict[str, str],
    class_to_ont: dict[str, str],
) -> list[tuple[str, str, str, float]]:
    """Project MONDO exactMatch cliques onto pairwise cross-ontology bridges in the hub."""
    if not mondo_tsv.is_file():
        return []
    lines = [ln for ln in mondo_tsv.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip() and not ln.startswith("#")]
    import io

    df = pl.read_csv(io.StringIO("\n".join(lines)), separator="\t", infer_schema_length=5000, ignore_errors=True, quote_char=None)
    by_mondo: dict[str, list[str]] = defaultdict(list)
    for row in df.iter_rows(named=True):
        pred = normalize_predicate(str(row.get("predicate_id") or ""))
        if pred != "skos:exactMatch":
            continue
        m_subj = str(row.get("subject_id") or "").strip()
        raw_obj = str(row.get("object_id") or "").strip()
        o_iri = resolver.get(raw_obj)
        if m_subj and o_iri:
            by_mondo[m_subj].append(o_iri)

    implied: list[tuple[str, str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for targets in by_mondo.values():
        uniq = sorted(set(targets))
        if len(uniq) < 2:
            continue
        for a, b in combinations(uniq, 2):
            if class_to_ont.get(a) != class_to_ont.get(b) and (a, b) not in seen:
                seen.add((a, b))
                implied.append((a, "skos:exactMatch", b, 0.89))
    return implied


def _parse_bioml_gold_tsv(tsv_path: Path, resolver: dict[str, str]) -> set[tuple[str, str]]:
    if not tsv_path.is_file():
        return set()
    df = pl.read_csv(tsv_path, separator="\t", infer_schema_length=2000, ignore_errors=True, quote_char=None)
    pairs: set[tuple[str, str]] = set()
    c0, c1 = df.columns[0], df.columns[1]
    for row in df.iter_rows(named=True):
        s = resolver.get(str(row[c0]).strip())
        o = resolver.get(str(row[c1]).strip())
        if s and o and s != o:
            pairs.add((s, o))
    return pairs


def prepare_suite_inputs(
    suite_id: str,
    raw_root: Path,
    work_dir: Path,
) -> tuple[NativeHubAudit, list[MappingRecord], set[tuple[str, str]], set[tuple[str, str]]]:
    """Prepare NativeHubAudit, deduplicated input MappingRecords, and optional gold sets for a public suite."""
    suite_raw = raw_root / suite_id
    if not (suite_raw / "provenance_sha256.json").is_file():
        fetch_public_suite(suite_id, raw_root=raw_root)

    if suite_id == "OAEI_BioML_2026":
        ont_paths = {
            "NCIT": suite_raw / "ncit_doid" / "ncit-doid" / "ncit.owl",
            "DOID": suite_raw / "ncit_doid" / "ncit-doid" / "doid.owl",
            "ORDO": suite_raw / "omim_ordo" / "omim-ordo" / "ordo.owl",
            "OMIM": suite_raw / "omim_ordo" / "omim-ordo" / "omim.owl",
        }
        ns_hints = {
            "NCIT": "Thesaurus.owl#",
            "DOID": "/obo/DOID_",
            "ORDO": "Orphanet_",
            "OMIM": "omim.org/",
        }
        native = run_native_preclean(suite_id, ont_paths, work_dir / "native", namespace_hints=ns_hints)
        resolver = build_hub_iri_resolver(native.class_to_ont)

        # Held-out gold equivalence & directional subsumption references (NEVER exposed to matchers/solvers!)
        gold_equiv = (
            _parse_bioml_gold_tsv(suite_raw / "ncit_doid" / "ncit-doid" / "refs_equiv" / "full.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "omim_ordo" / "omim-ordo" / "refs_equiv" / "full.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "hf_bioml_2026" / "NCIT-DOID" / "refs_equiv" / "train.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "hf_bioml_2026" / "NCIT-DOID" / "refs_equiv" / "valid.tsv", resolver)
        )
        gold_subs = (
            _parse_bioml_gold_tsv(suite_raw / "ncit_doid" / "ncit-doid" / "refs_subs" / "train.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "ncit_doid" / "ncit-doid" / "refs_subs" / "test.cands.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "omim_ordo" / "omim-ordo" / "refs_subs" / "train.tsv", resolver)
            | _parse_bioml_gold_tsv(suite_raw / "omim_ordo" / "omim-ordo" / "refs_subs" / "test.cands.tsv", resolver)
        )

        # Real blind high-recall candidate pool: LogMap MATCHER + Mondo/Biomappings + LogMapLt lexical/head-noun
        raw_pool: list[tuple[str, str, str, float]] = []
        for o1, o2 in [("NCIT", "DOID"), ("OMIM", "ORDO")]:
            for s, o, c in _run_logmap_matcher_pair(ont_paths[o1], ont_paths[o2]):
                if s in native.class_to_ont and o in native.class_to_ont:
                    raw_pool.append((s, "skos:exactMatch", o, c))

        for cw_name in ("biomappings_positive.sssom.tsv", "biomappings_predictions.sssom.tsv", "mondo_sssom.sssom.tsv"):
            raw_pool.extend(_load_sssom_resolved(suite_raw / "crosswalks" / cw_name, resolver, native.class_to_ont))
        raw_pool.extend(_load_mondo_implied_cliques(suite_raw / "crosswalks" / "mondo_sssom.sssom.tsv", resolver, native.class_to_ont))

        for s, o, c in _lexical_logmaplt_candidates(
            native, [("NCIT", "DOID"), ("OMIM", "ORDO")], max_per_pair=600
        ):
            raw_pool.append((s, "skos:exactMatch", o, c))

        for s, p, o in extract_embedded_crosswalks_fast(ont_paths, native.class_to_ont):
            raw_pool.append((s, p, o, 0.88))

    elif suite_id == "Plant_Biodiv_Hub":
        base_onts = {
            "PO": suite_raw / "ontologies" / "po.owl",
            "TO": suite_raw / "ontologies" / "to.owl",
            "FLOPO": suite_raw / "ontologies" / "flopo.owl",
            "GO_PLANT": suite_raw / "ontologies" / "go_plant.owl",
            "ENVO": suite_raw / "ontologies" / "envo.owl",
        }
        # Collect ChEBI seed IRIs referenced across plant ontologies & biomappings
        seed_chebi: set[str] = set()
        for p in base_onts.values():
            _ann, xrefs, _dj = stream_owl_annotations_and_xrefs(p)
            for _s, _pr, tgt in xrefs:
                if "CHEBI:" in tgt.upper():
                    code = tgt.upper().split("CHEBI:", 1)[1].strip()
                    seed_chebi.add(f"http://purl.obolibrary.org/obo/CHEBI_{code}")
                elif "/CHEBI_" in tgt:
                    seed_chebi.add(tgt.strip())

        chebi_mod = extract_chebi_plant_module(
            suite_raw / "ontologies" / "chebi_lite.owl",
            seed_chebi_iris=seed_chebi,
            out_module_owl=work_dir / "chebi_plant_module.owl",
            target_cap=10000,
        )
        ont_paths = {**base_onts, "CHEBI": chebi_mod}
        ns_hints = {
            "PO": "/obo/PO_",
            "TO": "/obo/TO_",
            "FLOPO": "/obo/FLOPO_",
            "GO_PLANT": "/obo/GO_",
            "ENVO": "/obo/ENVO_",
            "CHEBI": "/obo/CHEBI_",
        }
        native = run_native_preclean(suite_id, ont_paths, work_dir / "native", namespace_hints=ns_hints)
        resolver = build_hub_iri_resolver(native.class_to_ont)
        gold_equiv, gold_subs = set(), set()

        raw_pool = []
        for o1, o2 in [("PO", "FLOPO"), ("TO", "FLOPO"), ("PO", "TO"), ("GO_PLANT", "TO"), ("PO", "ENVO")]:
            for s, o, c in _run_logmap_matcher_pair(ont_paths[o1], ont_paths[o2]):
                if s in native.class_to_ont and o in native.class_to_ont:
                    raw_pool.append((s, "skos:exactMatch", o, c))
        for s, p, o in extract_embedded_crosswalks_fast(ont_paths, native.class_to_ont):
            raw_pool.append((s, p, o, 0.90))
        for cw_name in ("biomappings_positive.sssom.tsv", "biomappings_predictions.sssom.tsv"):
            raw_pool.extend(_load_sssom_resolved(suite_raw / "crosswalks" / cw_name, resolver, native.class_to_ont))
        for s, o, c in _lexical_logmaplt_candidates(
            native,
            [("PO", "FLOPO"), ("TO", "FLOPO"), ("PO", "ENVO"), ("GO_PLANT", "TO"), ("CHEBI", "TO"), ("PO", "TO")],
            max_per_pair=1500,
        ):
            raw_pool.append((s, "skos:exactMatch", o, c))

    elif suite_id == "Disease_Phenotype_Hub":
        ont_paths = {
            "MONDO": suite_raw / "ontologies" / "mondo.owl",
            "DOID": suite_raw / "ontologies" / "doid.owl",
            "ORDO": suite_raw / "ontologies" / "ordo.owl",
            "HP": suite_raw / "ontologies" / "hp.owl",
            "MP": suite_raw / "ontologies" / "mp.owl",
        }
        ns_hints = {
            "MONDO": "/obo/MONDO_",
            "DOID": "/obo/DOID_",
            "ORDO": "Orphanet_",
            "HP": "/obo/HP_",
            "MP": "/obo/MP_",
        }
        native = run_native_preclean(suite_id, ont_paths, work_dir / "native", namespace_hints=ns_hints)
        resolver = build_hub_iri_resolver(native.class_to_ont)
        gold_equiv, gold_subs = set(), set()

        raw_pool = []
        for cw_name in ("mondo_sssom.sssom.tsv", "biomappings_positive.sssom.tsv", "biomappings_predictions.sssom.tsv"):
            raw_pool.extend(_load_sssom_resolved(suite_raw / "crosswalks" / cw_name, resolver, native.class_to_ont))
        for s, p, o in extract_embedded_crosswalks_fast(ont_paths, native.class_to_ont):
            raw_pool.append((s, p, o, 0.90))
        for s, o, c in _lexical_logmaplt_candidates(
            native, [("HP", "MP")], max_per_pair=600
        ):
            raw_pool.append((s, "skos:exactMatch", o, c))
    else:
        raise ValueError(f"Unsupported public suite: {suite_id}")

    # Deduplicate (min_iri, max_iri) keeping highest-confidence entry & excluding native unsat endpoints
    dedup: dict[tuple[str, str], tuple[str, str, str, float]] = {}
    for s_iri, pred, o_iri, conf in raw_pool:
        if s_iri in native.native_unsat_classes or o_iri in native.native_unsat_classes:
            continue
        if native.class_to_ont.get(s_iri) == native.class_to_ont.get(o_iri):
            continue
        pk = (min(s_iri, o_iri), max(s_iri, o_iri))
        prev = dedup.get(pk)
        if prev is None or conf > prev[3]:
            dedup[pk] = (s_iri, pred, o_iri, conf)

    mappings: list[MappingRecord] = []
    for idx, (_pk, (s_iri, pred, o_iri, conf)) in enumerate(sorted(dedup.items()), start=1):
        s_tm = native.term_metadata.get(s_iri)
        o_tm = native.term_metadata.get(o_iri)
        mappings.append(
            MappingRecord(
                mapping_id=f"{suite_id[:4].lower()}_m_{idx:05d}",
                subject_id=s_iri,
                predicate_id=pred,
                object_id=o_iri,
                confidence=round(conf, 4),
                subject_label=s_tm.label if s_tm else "",
                object_label=o_tm.label if o_tm else "",
                subject_parent1_id=s_tm.parent1_id if s_tm else None,
                object_parent1_id=o_tm.parent1_id if o_tm else None,
            )
        )

    write_sssom_tsv(mappings, work_dir / f"{suite_id}.input_raw.sssom.tsv")
    return native, mappings, gold_equiv, gold_subs


def execute_single_public_suite(
    suite_id: str,
    root: Path = REPO_ROOT,
) -> tuple[dict, list[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit]]]:
    """Run full Stage 1-4 audit, B1..B4 (and C1..C4 if cached), and export dossiers/patches for one suite."""
    raw_root = root / "data" / "raw"
    work_dir = root / "data" / "interim" / suite_id
    results_dir = root / "results"

    print(f"[{suite_id}] Stage 1: Native ELK pre-clean & input crosswalk resolution...", flush=True)
    native, mappings, gold_equiv, gold_subs = prepare_suite_inputs(suite_id, raw_root, work_dir)
    print(
        f"[{suite_id}] Loaded {len(native.class_to_ont):,} classes across {len(native.ontology_paths)} ontologies "
        f"(native_unsat={len(native.native_unsat_classes)}, native_disjoints={len(native.native_disjoints)}) "
        f"and {len(mappings):,} crosswalks.",
        flush=True,
    )

    print(f"[{suite_id}] Auditing B1_Raw_Hub & H1 pairwise-vs-hub breakdown...", flush=True)
    raw_bridge = write_mappings_owl(mappings, None, work_dir / "B1_raw_bridge.owl")
    raw_audit = audit_merged_hub(native, raw_bridge, work_dir / "elk_B1", tag="B1_Raw_Hub")
    h1_bd = run_pairwise_vs_hub_audit(native, mappings, raw_audit, work_dir / "pairwise")
    print(
        f"[{suite_id}] H1 Result: Pairwise (Unsat={h1_bd.total_pairwise_unsat}, ConsViol={h1_bd.total_pairwise_consviol}) "
        f"--> Merged Hub (Unsat_Delta={h1_bd.hub_unsat_delta}, ConsViol={h1_bd.hub_consviol})!",
        flush=True,
    )

    print(f"[{suite_id}] Stage 2: Extracting minimal conflict cores & OAK dossiers...", flush=True)
    cores, traces = extract_conflict_cores(native, mappings, raw_audit)
    rec_by_id = {m.mapping_id: m for m in mappings}
    contested_ids = sorted({mid for c in cores for mid, _ in c.literals if mid in rec_by_id})
    print(
        f"[{suite_id}] Stage 2 extracted {len(cores)} minimal cores across {len(contested_ids)} contested mappings.",
        flush=True,
    )

    print(f"[{suite_id}] Running B2_LogMap_Repair...", flush=True)
    b2_res = run_logmap_repair(native, mappings, work_dir / "B2_LogMap")

    print(f"[{suite_id}] Running B3_ASP_Binary_Delete CEGAR...", flush=True)
    b3_res = run_binary_asp_delete(native, mappings, cores, work_dir / "B3_ASP_Delete")
    warm_cores = b3_res.discovered_cores if b3_res.discovered_cores else cores
    print(
        f"[{suite_id}] Certified B3 in {len(b3_res.iterations)} iters (warm_cores={len(warm_cores)}). "
        f"Running B4_Lexical_Lattice CEGAR...",
        flush=True,
    )
    b4_decs = score_lexical_lattice_heuristic(native, mappings, traces)
    b4_res = run_clingo_cegar(
        system_id="B4_Lexical_Lattice",
        native_audit=native,
        mappings=mappings,
        initial_cores=warm_cores,
        oracle_decisions=b4_decs,
        work_dir=work_dir / "B4_Lexical_Lattice",
        fallback_oracle_fn=lambda recs: score_lexical_lattice_heuristic(native, recs, traces),
    )

    # Export dossiers covering ALL mappings touched across initial + secondary CEGAR cores!
    all_contested_ids = sorted(set(contested_ids) | set(b4_res.contested_mapping_ids))
    contested_records = [rec_by_id[mid] for mid in all_contested_ids if mid in rec_by_id]
    dossiers_path = root / "data" / "dossiers" / f"{suite_id}.jsonl"
    dossiers = build_dossiers(native, contested_records, traces, out_jsonl_path=dossiers_path)
    print(
        f"[{suite_id}] Certified B4 in {len(b4_res.iterations)} CEGAR iterations "
        f"(Unsat_Delta={len(b4_res.final_audit.unsat_delta)}, ConsViol={b4_res.final_audit.cons_viol_count}); "
        f"exported {len(dossiers)} OAK dossiers -> {dossiers_path.name}"
    )

    export_certified_sssom_and_kgcl(
        mappings=mappings,
        selected_actions=b4_res.selected_actions,
        oracle_decisions=b4_decs,
        out_sssom_path=results_dir / "certified" / f"{suite_id}.B4_Lexical_Lattice.sssom.tsv",
        out_kgcl_path=results_dir / "patches" / f"{suite_id}.B4_Lexical_Lattice.kgcl",
        system_id="B4_Lexical_Lattice",
        suite_id=suite_id,
    )

    # Optional Phase 2 replay if commercial frontier oracle cache is populated for this suite
    c_systems: list[tuple[str, dict[str, str], object, dict[str, OracleDecision]]] = []
    vendor_decs: dict[str, dict[str, OracleDecision]] = {}
    if dossiers:
        from clasp_ont.cli import prioritize_dossiers

        prio_dossiers = prioritize_dossiers(dossiers, max_count=800)
        for v_id in ("C1_Claude", "C2_Codex", "C3_Antigravity"):
            try:
                vendor_decs[v_id] = run_vendor_sweep(
                    vendor_id=v_id,
                    dossiers=dossiers,
                    allow_live_cli=False,
                    human_terminal_confirmed=False,
                )
            except RuntimeError:
                try:
                    vendor_decs[v_id] = run_vendor_sweep(
                        vendor_id=v_id,
                        dossiers=prio_dossiers,
                        allow_live_cli=False,
                        human_terminal_confirmed=False,
                    )
                except RuntimeError:
                    continue

    oracle_agreement_meta: dict[str, object] = {"pairwise_js_divergence": {}}
    best_active_actions = b4_res.selected_actions
    best_active_decs = b4_decs
    best_active_sys = "B4_Lexical_Lattice"

    shared_cores = b4_res.discovered_cores if b4_res.discovered_cores else warm_cores
    for v_id, v_dec in sorted(vendor_decs.items()):
        v_res = run_clingo_cegar(
            system_id=v_id,
            native_audit=native,
            mappings=mappings,
            initial_cores=shared_cores,
            oracle_decisions=v_dec,
            work_dir=work_dir / v_id,
            fallback_oracle_fn=lambda recs: score_lexical_lattice_heuristic(native, recs, traces),
        )
        c_systems.append((v_id, v_res.selected_actions, v_res.final_audit, v_dec))

    if len(vendor_decs) == 3:
        cons = aggregate_log_opinion_pool(vendor_decs)
        oracle_agreement_meta = {
            "fleiss_kappa": cons.fleiss_kappa,
            "mean_js_divergence": cons.mean_js_divergence,
            "pairwise_js_divergence": cons.pairwise_js_divergence,
        }
        c4_res = run_clingo_cegar(
            system_id="C4_CLASP_ONT",
            native_audit=native,
            mappings=mappings,
            initial_cores=shared_cores,
            oracle_decisions=cons.decisions,
            work_dir=work_dir / "C4_CLASP_ONT",
            fallback_oracle_fn=lambda recs: score_lexical_lattice_heuristic(native, recs, traces),
        )
        c_systems.append(("C4_CLASP_ONT", c4_res.selected_actions, c4_res.final_audit, cons.decisions))
        export_certified_sssom_and_kgcl(
            mappings=mappings,
            selected_actions=c4_res.selected_actions,
            oracle_decisions=cons.decisions,
            out_sssom_path=results_dir / "certified" / f"{suite_id}.C4_CLASP_ONT.sssom.tsv",
            out_kgcl_path=results_dir / "patches" / f"{suite_id}.C4_CLASP_ONT.kgcl",
            system_id="C4_CLASP_ONT",
            suite_id=suite_id,
        )
        best_active_actions = c4_res.selected_actions
        best_active_decs = cons.decisions
        best_active_sys = "C4_CLASP_ONT"

    c_set = set(all_contested_ids)
    b1_actions = {m.mapping_id: "keep_equiv" for m in mappings}
    systems_summary: dict[str, dict] = {}

    eval_rows = [
        ("B1_Raw_Hub", b1_actions, raw_audit),
        ("B2_LogMap_Repair", b2_res.selected_actions, b2_res.final_audit),
        ("B3_ASP_Binary_Delete", b3_res.selected_actions, b3_res.final_audit),
        ("B4_Lexical_Lattice", b4_res.selected_actions, b4_res.final_audit),
        *[(sid, act_m, aud) for sid, act_m, aud, _d in c_systems],
    ]
    for sys_id, act_map, aud in eval_rows:
        scores = evaluate_alignment_against_gold(
            mappings, act_map, gold_equiv, gold_subs, contested_ids=c_set
        )
        scores["unsat_delta"] = len(aud.unsat_delta)
        scores["cons_viol"] = aud.cons_viol_count
        scores["novel_cross_entailments"] = aud.novel_cross_entailments
        scores["action_counts"] = {
            a: sum(1 for v in act_map.values() if v == a) for a in ALL_LATTICE_ACTIONS
        }
        systems_summary[sys_id] = scores

    # Paired bootstrap & Wilcoxon/McNemar over contested mappings
    cfg = load_lattice_config()
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}
    eval_ids = sorted(c_set)
    treat_scores = {
        i: rec_by_id[i].confidence * rho_map[best_active_actions.get(i, "keep_equiv")]
        for i in eval_ids
    }
    b3_scores = {
        i: rec_by_id[i].confidence * rho_map[b3_res.selected_actions.get(i, "keep_equiv")]
        for i in eval_ids
    }
    b2_scores = {
        i: rec_by_id[i].confidence * rho_map[b2_res.selected_actions.get(i, "keep_equiv")]
        for i in eval_ids
    }
    stat_raw = {
        f"{best_active_sys}_vs_B3_BinaryDelete": paired_bootstrap_and_tests(eval_ids, treat_scores, b3_scores),
        f"{best_active_sys}_vs_B2_LogMap": paired_bootstrap_and_tests(eval_ids, treat_scores, b2_scores),
    }
    stat_fdr = apply_bh_fdr(stat_raw, alpha=0.01)

    peak_rss = max(
        native.peak_rss_mb,
        raw_audit.peak_rss_mb,
        b2_res.final_audit.peak_rss_mb,
        max((it.peak_rss_mb for it in b3_res.iterations), default=0.0),
        max((it.peak_rss_mb for it in b4_res.iterations), default=0.0),
    )

    suite_metrics = {
        "num_ontologies": len(native.ontology_paths),
        "per_ont_classes": native.per_ont_class_counts,
        "num_classes": len(native.class_to_ont),
        "native_unsat": len(native.native_unsat_classes),
        "num_mappings": len(mappings),
        "gold_equiv_count": len(gold_equiv),
        "gold_subs_count": len(gold_subs),
        "contested_mappings": len(all_contested_ids),
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
        "oracle_agreement": oracle_agreement_meta,
    }

    ckpt_path = results_dir / "suites" / f"{suite_id}.json"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    ckpt_path.write_text(json.dumps(suite_metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pr_candidates = [
        (suite_id, rec_by_id[mid], best_active_actions[mid], best_active_decs.get(mid), native)
        for mid in all_contested_ids
        if mid in rec_by_id and best_active_actions.get(mid, "keep_equiv") != "keep_equiv"
    ]
    return suite_metrics, pr_candidates


def run_all_public_suites(
    suites: tuple[str, ...] = ("OAEI_BioML_2026", "Plant_Biodiv_Hub", "Disease_Phenotype_Hub"),
    root: Path = REPO_ROOT,
) -> dict:
    """Execute Phase 1-3 across all public hubs, merge with Phase0_Toy_Hub, and compile figures/PRs."""
    summary_path = root / "results" / "summary_metrics.json"
    existing_suites: dict[str, dict] = {}
    if summary_path.is_file():
        existing_suites = json.loads(summary_path.read_text(encoding="utf-8")).get("suites", {})

    all_pr_candidates: list[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit]] = []

    for s_id in suites:
        s_metrics, pr_cands = execute_single_public_suite(s_id, root=root)
        existing_suites[s_id] = s_metrics
        all_pr_candidates.extend(pr_cands)

        # Reload all per-suite checkpoint JSONs from results/suites/ so parallel suite runs never clobber
        for ckpt_file in sorted((root / "results" / "suites").glob("*.json")):
            try:
                existing_suites[ckpt_file.stem] = json.loads(ckpt_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        merged_summary = {"seed": 20261116, "suites": existing_suites}
        build_all_figures_and_macros(
            summary_metrics=merged_summary,
            figures_dir=root / "results" / "figures",
            macros_tex_path=root / "paper" / "results_macros.tex",
            summary_json_path=summary_path,
        )

    if len(suites) >= 3:
        build_top15_upstream_pr_bundle(all_pr_candidates, out_dir=root / "results" / "upstream_prs")
    else:
        rebuild_top15_from_saved_artifacts(root)
    return {"seed": 20261116, "suites": existing_suites}
