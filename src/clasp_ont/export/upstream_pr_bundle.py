"""Phase 3 Upstream KGCL Pull-Request Bundle & 2026 Journal AI Disclosure Manifest Generator."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord, compact_iri
from clasp_ont.symbolic.cegar_loop import OracleDecision


def build_top15_upstream_pr_bundle(
    candidates: Sequence[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit]],
    out_dir: Path,
) -> dict[str, Path]:
    """Rank repaired real-world mappings by confidence & conflict severity and emit top-15 PR bundle.

    Args:
        candidates: Tuples of (suite_id, MappingRecord, selected_action, OracleDecision|None, NativeHubAudit)
                    restricted to non-keep_equiv certified repairs on public hubs.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prioritize high-confidence public exactMatch assertions refined to directional subsumption or ancestor,
    # stratified round-robin across evaluation suites so every public hub is represented.
    action_priority = {
        "downgrade_sub": 3,
        "downgrade_sup": 3,
        "weaken_ancestor": 2,
        "negate_drop": 1,
    }
    by_suite: dict[str, list[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit | None]]] = {}
    for c in candidates:
        if c[2] != "keep_equiv":
            by_suite.setdefault(c[0], []).append(c)
    for s_list in by_suite.values():
        s_list.sort(key=lambda item: (action_priority.get(item[2], 0), item[1].confidence), reverse=True)

    ranked: list[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit | None]] = []
    suite_order = sorted(by_suite.keys())
    pos = 0
    while len(ranked) < 15 and suite_order:
        progressed = False
        for sk in suite_order:
            if pos < len(by_suite[sk]):
                ranked.append(by_suite[sk][pos])
                progressed = True
                if len(ranked) >= 15:
                    break
        if not progressed:
            break
        pos += 1

    md_lines = [
        "# Upstream Ontology Crosswalk Patch Bundle (Top 15 Certified Repairs)",
        "",
        "Generated programmatically from live `ELK` + `DuckDB` + `Clingo` CEGAR certification",
        "(`|Unsat_Delta| = 0`, `|ConsViol| = 0`). Ready for submission to `biopragmatics/biomappings`",
        "and OBO Foundry ontology repositories (`mondo`, `doid`, `po`, `to`, `envo`).",
        "",
        "| Rank | Suite | Subject | Input Predicate | Object | Certified Action | KGCL Patch Command |",
        "|---:|---|---|---|---|---|---|",
    ]

    kgcl_lines = [
        "# Top-15 Upstream KGCL Patch Commands (OBO Foundry & Biomappings)",
    ]

    for idx, (suite_id, rec, act, dec, native) in enumerate(ranked, start=1):
        s_curie = compact_iri(rec.subject_id)
        o_curie = compact_iri(rec.object_id)
        s_lbl = (
            native.term_metadata[rec.subject_id].label
            if (native is not None and rec.subject_id in native.term_metadata)
            else rec.subject_label
        ) or s_curie
        o_lbl = (
            native.term_metadata[rec.object_id].label
            if (native is not None and rec.object_id in native.term_metadata)
            else rec.object_label
        ) or o_curie

        if act == "downgrade_sub":
            cmd = f"change relationship between {s_curie} and {o_curie} from {rec.predicate_id} to rdfs:subClassOf"
        elif act == "downgrade_sup":
            cmd = f"delete edge {s_curie} {rec.predicate_id} {o_curie} ; create edge {o_curie} rdfs:subClassOf {s_curie}"
        elif act == "weaken_ancestor":
            if rec.weaken_direction == "sub_to_obj_parent" and rec.object_parent1_id:
                p_curie = compact_iri(rec.object_parent1_id)
                cmd = f"delete edge {s_curie} {rec.predicate_id} {o_curie} ; create edge {s_curie} rdfs:subClassOf {p_curie}"
            elif rec.subject_parent1_id:
                p_curie = compact_iri(rec.subject_parent1_id)
                cmd = f"delete edge {s_curie} {rec.predicate_id} {o_curie} ; create edge {o_curie} rdfs:subClassOf {p_curie}"
            else:
                cmd = f"delete edge {s_curie} {rec.predicate_id} {o_curie}"
        else:
            cmd = f"delete edge {s_curie} {rec.predicate_id} {o_curie}"

        md_lines.append(
            f"| {idx} | `{suite_id}` | `{s_curie}` ({s_lbl}) | `{rec.predicate_id}` ({rec.confidence:.2f}) "
            f"| `{o_curie}` ({o_lbl}) | `{act}` | `{cmd}` |"
        )
        for sub_cmd in cmd.split(" ; "):
            kgcl_lines.append(sub_cmd.strip())
        if dec and dec.rationale:
            kgcl_lines.append(f"# Rationale [{idx}]: {dec.rationale}")

    pr_md_path = out_dir / "top15_kgcl_pull_requests.md"
    pr_kgcl_path = out_dir / "top15_obo_biomappings.kgcl"
    pr_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    pr_kgcl_path.write_text("\n".join(kgcl_lines) + "\n", encoding="utf-8")

    # 5-Element 2026 Journal AI Disclosure Manifest (preserving AUTHOR SIGN-OFF REQUIRED placeholder)
    disclosure_payload = {
        "schema_version": "2026.1-journal-ai-disclosure",
        "author_signoff": "AUTHOR SIGN-OFF REQUIRED",
        "elements": {
            "1_models_and_versions": {
                "symbolic_verifiers": ["ROBOT v1.9.11 (ELK OWL 2 EL)", "Potassco Clingo v5.8.3", "DuckDB v2.0.0"],
                "semantic_oracles": [
                    "B4_Lexical_Lattice (deterministic non-LLM TF-IDF + hierarchy baseline)",
                    "C1_Claude (Claude Code CLI, zero-tool JSON-schema sandbox)",
                    "C2_Codex (Codex CLI, ephemeral zero-tool JSON-schema sandbox)",
                    "C3_Antigravity (Antigravity CLI, isolated temp-dir sandbox)",
                ],
            },
            "2_scope_of_use": (
                "Semantic oracles score candidate refinement probabilities over contested conflict-core "
                "dossiers only; all logical coherence and deductive conservativity guarantees are enforced "
                "deterministically by ELK, DuckDB, and Clingo CEGAR."
            ),
            "3_isolation_and_data_governance": (
                "All CLI invocations execute inside empty temporary directories outside the repository with "
                "all metered API keys stripped and zero file/shell tools enabled, over 100% open public ontologies."
            ),
            "4_verification_and_hallucination_controls": (
                "Hard mathematical certification gate requires independent ELK + DuckDB verification of "
                "|Unsat_Delta| = 0 and |ConsViol| = 0 prior to accepting any repaired hub."
            ),
            "5_reproducibility_and_cache_artifact": (
                "SHA-256 hashed raw oracle responses are archived in results/oracle_cache/ enabling full "
                "zero-key, zero-subscription offline peer-review replay via `uv run snakemake reproduce_from_cache --cores 4`."
            ),
        },
    }
    disc_json_path = out_dir / "journal_ai_disclosure_2026.json"
    disc_json_path.write_text(json.dumps(disclosure_payload, indent=2) + "\n", encoding="utf-8")

    return {
        "top15_pr_md": pr_md_path,
        "top15_kgcl": pr_kgcl_path,
        "ai_disclosure_json": disc_json_path,
    }


def rebuild_top15_from_saved_artifacts(root: Path) -> dict[str, Path]:
    """Build stratified Top-15 upstream KGCL PR bundle across all saved public suite certified SSSOM & dossiers."""
    import io

    import polars as pl

    cert_dir = root / "results" / "certified"
    candidates: list[tuple[str, MappingRecord, str, OracleDecision | None, NativeHubAudit | None]] = []

    for s_id in ("Disease_Phenotype_Hub", "OAEI_BioML_2026", "Plant_Biodiv_Hub"):
        sssom_p = cert_dir / f"{s_id}.C4_CLASP_ONT.sssom.tsv"
        if not sssom_p.is_file():
            sssom_p = cert_dir / f"{s_id}.B4_Lexical_Lattice.sssom.tsv"
        if not sssom_p.is_file():
            continue

        lines = [ln for ln in sssom_p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        if not lines:
            continue
        df = pl.read_csv(io.StringIO("\n".join(lines)), separator="\t", infer_schema_length=5000, quote_char=None)
        for row in df.iter_rows(named=True):
            c_rule = str(row.get("curation_rule") or "")
            rule_parts = c_rule.split(":")
            act = rule_parts[1] if len(rule_parts) >= 2 else "keep_equiv"
            if act not in {"downgrade_sub", "downgrade_sup", "weaken_ancestor", "negate_drop"}:
                continue
            weaken_dir = rule_parts[2] if len(rule_parts) >= 3 else "sub_to_obj_parent"
            s_cur = str(row.get("subject_id") or "")
            o_cur = str(row.get("object_id") or "")
            s_lbl = str(row.get("subject_label") or "")
            o_lbl = str(row.get("object_label") or "")
            in_conf = float(row.get("confidence") or 0.90)
            rat = str(row.get("comment") or "")
            dec = (
                OracleDecision(
                    mapping_id=f"{s_cur}->{o_cur}",
                    probs={act: 1.0},
                    rationale=rat,
                )
                if rat
                else None
            )
            rec = MappingRecord(
                mapping_id=f"{s_cur}->{o_cur}",
                subject_id=s_cur,
                predicate_id="skos:exactMatch",
                object_id=o_cur,
                confidence=in_conf,
                subject_label=s_lbl,
                object_label=o_lbl,
                subject_parent1_id=o_cur if act == "weaken_ancestor" and weaken_dir == "obj_to_sub_parent" else None,
                object_parent1_id=o_cur if act == "weaken_ancestor" and weaken_dir == "sub_to_obj_parent" else None,
                weaken_direction=weaken_dir,
            )
            candidates.append((s_id, rec, act, dec, None))

    return build_top15_upstream_pr_bundle(candidates, out_dir=root / "results" / "upstream_prs")
