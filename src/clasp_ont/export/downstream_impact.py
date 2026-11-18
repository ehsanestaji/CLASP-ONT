"""Downstream biological, phenomics, and clinical cohort impact evaluation over live ELK closures.

Quantifies how multi-ontology crosswalk defects (and non-destructive lattice certification)
affect real downstream genomics and clinical tasks:
  1. Strict OWL deductive subsumption entailment recall on held-out OAEI Bio-ML `refs_subs`
     (H(M') |= s <= o and H(M') |/= o <= s).
  2. Per-ontology native hierarchy corruption (% of classes collapsed to owl:Nothing or gaining
     false intra-ontology ancestors) and ancestor-set Jaccard similarity fidelity (OWLSim/Exomiser kernel).
  3. Clinical & functional disease/trait chapter cohort roll-up false-discovery rate (FDR)
     versus preserved cross-ontology subtype inclusions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import polars as pl

from clasp_ont.ingest.preclean import _matches_namespace

REPO_ROOT = Path(__file__).resolve().parents[3]


def compute_deductive_subsumption_metrics(
    closure_parquet: Path,
    gold_sub_pairs: set[tuple[str, str]],
) -> dict[str, float | int]:
    """Evaluate held-out directional subsumptions under full OWL 2 EL deductive closure."""
    if not gold_sub_pairs or not closure_parquet.is_file():
        return {
            "deductive_sub_tp": 0,
            "deductive_sub_recall": 0.0,
            "deductive_sub_collapsed": 0,
            "deductive_sub_inverted": 0,
        }

    con = duckdb.connect()
    con.execute("SET memory_limit = '3GB';")
    gold_df = pl.DataFrame(
        {"s_iri": [a for a, _ in gold_sub_pairs], "o_iri": [b for _, b in gold_sub_pairs]}
    )
    con.register("gold_sub_tbl", gold_df)
    p_str = str(closure_parquet).replace("'", "''")

    row = con.execute(
        f"""
        WITH fwd AS (
            SELECT g.s_iri, g.o_iri
            FROM gold_sub_tbl g
            SEMI JOIN '{p_str}' c ON g.s_iri = c.sub_iri AND g.o_iri = c.sup_iri
        ),
        rev AS (
            SELECT g.s_iri, g.o_iri
            FROM gold_sub_tbl g
            SEMI JOIN '{p_str}' c ON g.o_iri = c.sub_iri AND g.s_iri = c.sup_iri
        )
        SELECT
            (SELECT count(*) FROM fwd ANTI JOIN rev USING (s_iri, o_iri)) AS strict_tp,
            (SELECT count(*) FROM fwd SEMI JOIN rev USING (s_iri, o_iri)) AS collapsed_both,
            (SELECT count(*) FROM rev ANTI JOIN fwd USING (s_iri, o_iri)) AS inverted_err
        """
    ).fetchone()
    con.close()

    strict_tp = int(row[0]) if row else 0
    collapsed = int(row[1]) if row else 0
    inverted = int(row[2]) if row else 0
    total = len(gold_sub_pairs)
    return {
        "deductive_sub_tp": strict_tp,
        "deductive_sub_recall": round(strict_tp / max(1, total), 6),
        "deductive_sub_collapsed": collapsed,
        "deductive_sub_inverted": inverted,
    }


def _find_latest_closure(sys_dir: Path) -> Path | None:
    cands = list(sys_dir.glob("*/*.closure.parquet"))
    if not cands:
        cands = list(sys_dir.glob("*.closure.parquet"))
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def _load_primary_signature(work_dir: Path, suite_id: str) -> dict[str, str]:
    """Reconstruct exact primary class -> ontology mapping from native ELK closures."""
    ns_hints_by_suite = {
        "OAEI_BioML_2026": {
            "NCIT": "Thesaurus.owl#",
            "DOID": "/obo/DOID_",
            "ORDO": "Orphanet_",
            "OMIM": "omim.org/",
        },
        "Disease_Phenotype_Hub": {
            "MONDO": "/obo/MONDO_",
            "DOID": "/obo/DOID_",
            "ORDO": "Orphanet_",
            "HP": "/obo/HP_",
            "MP": "/obo/MP_",
        },
        "Plant_Biodiv_Hub": {
            "PO": "/obo/PO_",
            "TO": "/obo/TO_",
            "FLOPO": "/obo/FLOPO_",
            "GO_PLANT": "/obo/GO_",
            "CHEBI": "/obo/CHEBI_",
            "ENVO": "/obo/ENVO_",
        },
        "Phase0_Toy_Hub": {
            "TOY_ANAT": "/toy/anat#",
            "TOY_PROC": "/toy/proc#",
            "TOY_CHEM": "/toy/chem#",
            "TOY_TRAIT": "/toy/trait#",
        },
    }
    hints = ns_hints_by_suite.get(suite_id, {})
    native_dir = work_dir / "native" / "native"
    class_to_ont: dict[str, str] = {}
    for cl_file in sorted(native_dir.glob("native_*.closure.parquet")):
        ont_id = cl_file.name.removeprefix("native_").removesuffix(".closure.parquet")
        if ont_id == "base_union":
            continue
        df = pl.read_parquet(cl_file)
        iris = set(df["sub_iri"].to_list()) | set(df["sup_iri"].to_list())
        for iri in iris:
            if _matches_namespace(iri, ont_id, hints.get(ont_id)):
                class_to_ont[iri] = ont_id
    return class_to_ont


def _load_suite_gold_subs(suite_id: str, raw_root: Path, class_to_ont: dict[str, str]) -> set[tuple[str, str]]:
    if suite_id == "OAEI_BioML_2026":
        from clasp_ont.public_runner import _parse_bioml_gold_tsv, build_hub_iri_resolver

        resolver = build_hub_iri_resolver(class_to_ont)
        sr = raw_root / "OAEI_BioML_2026"
        return (
            _parse_bioml_gold_tsv(sr / "ncit_doid" / "ncit-doid" / "refs_subs" / "train.tsv", resolver)
            | _parse_bioml_gold_tsv(sr / "ncit_doid" / "ncit-doid" / "refs_subs" / "test.cands.tsv", resolver)
            | _parse_bioml_gold_tsv(sr / "omim_ordo" / "omim-ordo" / "refs_subs" / "train.tsv", resolver)
            | _parse_bioml_gold_tsv(sr / "omim_ordo" / "omim-ordo" / "refs_subs" / "test.cands.tsv", resolver)
        )
    if suite_id == "Phase0_Toy_Hub":
        import tempfile

        from clasp_ont.ingest.sssom_io import action_to_owl_pair
        from clasp_ont.ingest.toy_hub import build_toy_hub

        with tempfile.TemporaryDirectory() as td:
            bundle = build_toy_hub(Path(td))
            gold_sub: set[tuple[str, str]] = set()
            for m in bundle.mappings:
                gt_act = bundle.ground_truth_actions.get(m.mapping_id)
                if gt_act is None:
                    continue
                pair = action_to_owl_pair(m, gt_act)
                if pair is not None and pair[0] != "equiv":
                    gold_sub.add((pair[1], pair[2]))
            return gold_sub
    return set()


def compute_suite_downstream_impact(
    suite_id: str,
    work_dir: Path,
    raw_root: Path,
) -> dict:
    """Compute per-system deductive subsumption, hierarchy corruption, and cohort FDR metrics."""
    intra_parquet = work_dir / "native" / "intra_closure.parquet"
    class_to_ont = _load_primary_signature(work_dir, suite_id)
    gold_subs = _load_suite_gold_subs(suite_id, raw_root, class_to_ont)

    sys_closures: dict[str, Path] = {}
    b1_p = _find_latest_closure(work_dir / "elk_B1")
    if b1_p:
        sys_closures["B1_Raw_Hub"] = b1_p
    b2_p = _find_latest_closure(work_dir / "B2_LogMap")
    if b2_p:
        sys_closures["B2_LogMap_Repair"] = b2_p
    b3_p = _find_latest_closure(work_dir / "B3_ASP_Delete")
    if b3_p:
        sys_closures["B3_ASP_Binary_Delete"] = b3_p
    b4_p = _find_latest_closure(work_dir / "B4_Lexical_Lattice")
    if b4_p:
        sys_closures["B4_Lexical_Lattice"] = b4_p

    con = duckdb.connect()
    con.execute("SET memory_limit = '3GB';")
    sig_df = pl.DataFrame(
        {"iri": list(class_to_ont.keys()), "ont_id": list(class_to_ont.values())}
    )
    con.register("sig_tbl", sig_df)
    intra_str = str(intra_parquet).replace("'", "''")

    # Determine native satisfiable classes Sigma_{sat,j} = Sig(O_j) \ Unsat_0
    base_union_parquet = work_dir / "native" / "native" / "native_base_union.closure.parquet"
    base_filter_parq = base_union_parquet if base_union_parquet.is_file() else intra_parquet
    base_str = str(base_filter_parq).replace("'", "''")
    con.execute(
        f"""
        CREATE TEMP TABLE native_sat AS
        SELECT DISTINCT s.iri, s.ont_id
        FROM sig_tbl s
        SEMI JOIN (
            SELECT sub_iri AS iri FROM '{base_str}'
            UNION
            SELECT sup_iri AS iri FROM '{base_str}'
        ) u ON s.iri = u.iri;
        """
    )

    min_ch, max_ch = (10, 120) if suite_id == "Phase0_Toy_Hub" else (200, 4000)
    con.execute(
        f"""
        CREATE TEMP TABLE chapters AS
        SELECT n.sup_iri AS chap_iri, s1.ont_id, count(DISTINCT n.sub_iri) AS nat_sz
        FROM '{intra_str}' n
        JOIN native_sat s1 ON n.sup_iri = s1.iri
        JOIN native_sat s2 ON n.sub_iri = s2.iri AND s1.ont_id = s2.ont_id
        GROUP BY n.sup_iri, s1.ont_id
        HAVING count(DISTINCT n.sub_iri) BETWEEN {min_ch} AND {max_ch};
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE nat_chap_members AS
        SELECT n.sup_iri AS chap_iri, n.sub_iri
        FROM '{intra_str}' n
        JOIN chapters ch ON n.sup_iri = ch.chap_iri
        JOIN native_sat s2 ON n.sub_iri = s2.iri AND s2.ont_id = ch.ont_id;
        """
    )

    out_by_system: dict[str, dict] = {}
    for sys_id, cl_path in sys_closures.items():
        cl_str = str(cl_path).replace("'", "''")
        ded_metrics = compute_deductive_subsumption_metrics(cl_path, gold_subs)

        # 1. Per-ontology hierarchy corruption (unsat collapse + satisfiable ConsViol distortion)
        ont_rows = con.execute(
            f"""
            WITH hub_present AS (
                SELECT sub_iri AS iri FROM '{cl_str}'
                UNION
                SELECT sup_iri AS iri FROM '{cl_str}'
            ),
            unsat_cls AS (
                SELECT ns.iri, ns.ont_id
                FROM native_sat ns
                ANTI JOIN hub_present hp ON ns.iri = hp.iri
            ),
            consviol_cls AS (
                SELECT DISTINCT b.sub_iri AS iri, s1.ont_id
                FROM '{cl_str}' b
                JOIN native_sat s1 ON b.sub_iri = s1.iri
                JOIN native_sat s2 ON b.sup_iri = s2.iri AND s1.ont_id = s2.ont_id
                ANTI JOIN '{intra_str}' n ON b.sub_iri = n.sub_iri AND b.sup_iri = n.sup_iri
                WHERE b.sub_iri <> b.sup_iri
            ),
            nat_anc AS (
                SELECT n.sub_iri AS iri, s1.ont_id, count(*) AS n_nat
                FROM '{intra_str}' n
                JOIN native_sat s1 ON n.sub_iri = s1.iri
                JOIN native_sat s2 ON n.sup_iri = s2.iri AND s1.ont_id = s2.ont_id
                WHERE n.sub_iri <> n.sup_iri
                GROUP BY n.sub_iri, s1.ont_id
            ),
            hub_anc AS (
                SELECT b.sub_iri AS iri, s1.ont_id, count(*) AS n_hub
                FROM '{cl_str}' b
                JOIN native_sat s1 ON b.sub_iri = s1.iri
                JOIN native_sat s2 ON b.sup_iri = s2.iri AND s1.ont_id = s2.ont_id
                WHERE b.sub_iri <> b.sup_iri
                GROUP BY b.sub_iri, s1.ont_id
            ),
            jacc AS (
                SELECT na.ont_id,
                       avg(least(na.n_nat, coalesce(ha.n_hub, na.n_nat))::DOUBLE
                           / greatest(na.n_nat, coalesce(ha.n_hub, na.n_nat), 1)::DOUBLE) AS mean_jacc
                FROM nat_anc na
                JOIN hub_present hp ON na.iri = hp.iri
                LEFT JOIN hub_anc ha ON na.iri = ha.iri AND na.ont_id = ha.ont_id
                GROUP BY na.ont_id
            )
            SELECT
                ns.ont_id,
                count(*) AS total_sat_classes,
                count(u.iri) AS unsat_collapsed,
                count(cv.iri) AS consviol_distorted,
                count(CASE WHEN u.iri IS NOT NULL OR cv.iri IS NOT NULL THEN 1 END) AS total_corrupted,
                coalesce(max(j.mean_jacc), 1.0) AS mean_ancestor_jaccard
            FROM native_sat ns
            LEFT JOIN unsat_cls u ON ns.iri = u.iri
            LEFT JOIN consviol_cls cv ON ns.iri = cv.iri
            LEFT JOIN jacc j ON ns.ont_id = j.ont_id
            GROUP BY ns.ont_id
            ORDER BY ns.ont_id;
            """
        ).fetchall()

        per_ont: dict[str, dict] = {}
        tot_sat_all = 0
        tot_corr_all = 0
        for oid, tot_sat, u_c, cv_c, corr_c, m_jacc in ont_rows:
            tot_sat_all += int(tot_sat)
            tot_corr_all += int(corr_c)
            per_ont[str(oid)] = {
                "native_satisfiable_classes": int(tot_sat),
                "unsat_collapsed_classes": int(u_c),
                "consviol_distorted_classes": int(cv_c),
                "total_corrupted_classes": int(corr_c),
                "corrupted_pct": round(100.0 * int(corr_c) / max(1, int(tot_sat)), 2),
                "mean_ancestor_jaccard": round(float(m_jacc), 4),
            }

        # 2. Clinical / domain chapter cohort roll-up FDR & cross-ontology expansion
        chap_row = con.execute(
            f"""
            WITH hub_intra AS (
                SELECT b.sup_iri AS chap_iri, b.sub_iri
                FROM '{cl_str}' b
                JOIN chapters ch ON b.sup_iri = ch.chap_iri
                JOIN native_sat s2 ON b.sub_iri = s2.iri AND s2.ont_id = ch.ont_id
            ),
            fp AS (
                SELECT h.chap_iri, count(*) AS fp_cnt
                FROM hub_intra h
                ANTI JOIN nat_chap_members nm ON h.chap_iri = nm.chap_iri AND h.sub_iri = nm.sub_iri
                GROUP BY h.chap_iri
            ),
            tot_intra AS (
                SELECT chap_iri, count(*) AS tot_cnt FROM hub_intra GROUP BY chap_iri
            ),
            cross_ext AS (
                SELECT b.sup_iri AS chap_iri, count(DISTINCT b.sub_iri) AS ext_cnt
                FROM '{cl_str}' b
                JOIN chapters ch ON b.sup_iri = ch.chap_iri
                JOIN native_sat s2 ON b.sub_iri = s2.iri AND s2.ont_id <> ch.ont_id
                GROUP BY b.sup_iri
            )
            SELECT
                count(ch.chap_iri) AS num_chapters,
                coalesce(avg(coalesce(fp.fp_cnt, 0)::DOUBLE / greatest(coalesce(ti.tot_cnt, 0), 1)), 0.0) AS mean_fdr,
                coalesce(sum(coalesce(fp.fp_cnt, 0)), 0) AS total_fp,
                coalesce(sum(coalesce(ce.ext_cnt, 0)), 0) AS total_cross_inclusions
            FROM chapters ch
            LEFT JOIN tot_intra ti USING (chap_iri)
            LEFT JOIN fp USING (chap_iri)
            LEFT JOIN cross_ext ce USING (chap_iri);
            """
        ).fetchone()

        hp_mp_links = 0
        if suite_id == "Disease_Phenotype_Hub":
            hp_mp_links = int(
                con.execute(
                    f"""
                    SELECT count(*)
                    FROM '{cl_str}' b
                    JOIN native_sat s1 ON b.sub_iri = s1.iri
                    JOIN native_sat s2 ON b.sup_iri = s2.iri
                    WHERE (s1.ont_id = 'HP' AND s2.ont_id = 'MP')
                       OR (s1.ont_id = 'MP' AND s2.ont_id = 'HP')
                    """
                ).fetchone()[0]
            )

        out_by_system[sys_id] = {
            **ded_metrics,
            "hub_corrupted_classes": tot_corr_all,
            "hub_corrupted_pct": round(100.0 * tot_corr_all / max(1, tot_sat_all), 2),
            "per_ontology_corruption": per_ont,
            "chapter_cohort_rollup": {
                "num_chapters": int(chap_row[0]) if chap_row else 0,
                "mean_intra_fdr": round(float(chap_row[1]), 6) if chap_row else 0.0,
                "mean_intra_fdr_pct": round(100.0 * float(chap_row[1]), 2) if chap_row else 0.0,
                "total_intra_false_positives": int(chap_row[2]) if chap_row else 0,
                "cross_ontology_inclusions": int(chap_row[3]) if chap_row else 0,
            },
            "cross_species_hp_mp_entailments": hp_mp_links,
        }

    con.close()
    return out_by_system


def enrich_saved_suites_with_downstream_impact(root: Path = REPO_ROOT) -> dict:
    """Enrich all saved suite checkpoints with deductive subsumption & downstream impact metrics."""
    suite_work_dirs = {
        "Phase0_Toy_Hub": root / "data" / "interim" / "phase0",
        "OAEI_BioML_2026": root / "data" / "interim" / "OAEI_BioML_2026",
        "Plant_Biodiv_Hub": root / "data" / "interim" / "Plant_Biodiv_Hub",
        "Disease_Phenotype_Hub": root / "data" / "interim" / "Disease_Phenotype_Hub",
    }
    raw_root = root / "data" / "raw"
    suites_dir = root / "results" / "suites"
    merged_suites: dict[str, dict] = {}

    for s_id, w_dir in suite_work_dirs.items():
        ckpt = suites_dir / f"{s_id}.json"
        if not ckpt.is_file() or not w_dir.is_dir():
            continue
        s_data = json.loads(ckpt.read_text(encoding="utf-8"))
        ds_by_sys = compute_suite_downstream_impact(s_id, w_dir, raw_root)
        s_data["downstream_impact"] = ds_by_sys
        for sys_k, ds_vals in ds_by_sys.items():
            if sys_k in s_data.get("systems", {}):
                s_data["systems"][sys_k]["deductive_sub_recall"] = ds_vals["deductive_sub_recall"]
                s_data["systems"][sys_k]["deductive_sub_tp"] = ds_vals["deductive_sub_tp"]
                s_data["systems"][sys_k]["hub_corrupted_pct"] = ds_vals["hub_corrupted_pct"]
                s_data["systems"][sys_k]["chapter_intra_fdr_pct"] = ds_vals["chapter_cohort_rollup"][
                    "mean_intra_fdr_pct"
                ]
                s_data["systems"][sys_k]["chapter_cross_inclusions"] = ds_vals["chapter_cohort_rollup"][
                    "cross_ontology_inclusions"
                ]
        ckpt.write_text(json.dumps(s_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        merged_suites[s_id] = s_data

    # Clean up temporary toy check folder if created
    toy_tmp = root / "data" / "toy_tmp_check"
    if toy_tmp.is_dir():
        import shutil

        shutil.rmtree(toy_tmp, ignore_errors=True)

    from clasp_ont.export.build_figures import build_all_figures_and_macros

    summary_path = root / "results" / "summary_metrics.json"
    summary_obj = {"seed": 20261116, "suites": merged_suites}
    build_all_figures_and_macros(
        summary_metrics=summary_obj,
        figures_dir=root / "results" / "figures",
        macros_tex_path=root / "paper" / "results_macros.tex",
        summary_json_path=summary_path,
    )

    # Sync docs/empirical/repro_manifest.json SHA-256 & new headline anchors
    manifest_path = root / "docs" / "empirical" / "repro_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        bioml = merged_suites["OAEI_BioML_2026"]
        dis = merged_suites["Disease_Phenotype_Hub"]
        anchors = manifest.get("headline_anchors", {})
        anchors["bioml_b2_ded_sub_recall"] = {
            "value": bioml["systems"]["B2_LogMap_Repair"]["deductive_sub_recall"],
            "format": ".3f",
        }
        anchors["bioml_b3_ded_sub_recall"] = {
            "value": bioml["systems"]["B3_ASP_Binary_Delete"]["deductive_sub_recall"],
            "format": ".3f",
        }
        anchors["bioml_b4_ded_sub_recall"] = {
            "value": bioml["systems"]["B4_Lexical_Lattice"]["deductive_sub_recall"],
            "format": ".3f",
        }
        anchors["bioml_b4_ded_sub_tp"] = bioml["systems"]["B4_Lexical_Lattice"]["deductive_sub_tp"]
        dis_b1_ds = dis["downstream_impact"]["B1_Raw_Hub"]
        dis_b4_ds = dis["downstream_impact"]["B4_Lexical_Lattice"]
        anchors["disease_ordo_corrupted_pct"] = {
            "value": dis_b1_ds["per_ontology_corruption"]["ORDO"]["corrupted_pct"],
            "format": ".1f",
        }
        anchors["disease_mondo_corrupted_pct"] = {
            "value": dis_b1_ds["per_ontology_corruption"]["MONDO"]["corrupted_pct"],
            "format": ".1f",
        }
        anchors["disease_b1_chapter_fdr_pct"] = {
            "value": dis_b1_ds["chapter_cohort_rollup"]["mean_intra_fdr_pct"],
            "format": ".1f",
        }
        anchors["disease_b1_chapter_fp"] = dis_b1_ds["chapter_cohort_rollup"]["total_intra_false_positives"]
        anchors["disease_b4_chapter_cross"] = dis_b4_ds["chapter_cohort_rollup"]["cross_ontology_inclusions"]
        manifest["headline_anchors"] = anchors
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return summary_obj


if __name__ == "__main__":
    enrich_saved_suites_with_downstream_impact()
