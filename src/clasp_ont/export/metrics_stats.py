"""Objective evaluation metrics, H1 pairwise-vs-hub compositional audit, and statistical tests.

Implements Section 7.2:
  - Equivalence Precision, Recall, F1
  - Directional Subsumption Precision, Recall, F1
  - Semantic Knowledge Retention & Contested Bridge Preservation
  - 10,000-iteration paired bootstrap 95% CIs (seed=20261116)
  - Two-sided paired Wilcoxon signed-rank test, McNemar's test, and Benjamini-Hochberg FDR (alpha=0.01)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy import stats
from statsmodels.stats.contingency_tables import mcnemar
from statsmodels.stats.multitest import multipletests

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord, action_to_owl_pair, write_mappings_owl
from clasp_ont.symbolic.cegar_loop import compute_semantic_retention, load_lattice_config
from clasp_ont.symbolic.conservativity import HubAuditReport, audit_merged_hub


@dataclass(slots=True)
class PairwiseVsHubBreakdown:
    suite_id: str
    pairwise_audits: dict[str, dict[str, int]]
    total_pairwise_unsat: int
    total_pairwise_consviol: int
    hub_unsat_delta: int
    hub_consviol: int
    pairwise_conflict_mapping_rate: float


def run_pairwise_vs_hub_audit(
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    full_hub_audit: HubAuditReport,
    work_dir: Path,
    heap_flag: str = "-Xmx4G",
) -> PairwiseVsHubBreakdown:
    """Quantify H1: pairwise ontology crosswalk audit vs. full composed k-ontology hub explosion."""
    ont_ids = sorted(native_audit.ontology_paths.keys())
    pairwise_audits: dict[str, dict[str, int]] = {}
    tot_pw_unsat = 0
    tot_pw_cons = 0
    conflicting_pw_mappings = 0

    for o1, o2 in combinations(ont_ids, 2):
        pair_key = f"{o1}__{o2}"
        sub_mappings = [
            m
            for m in mappings
            if {
                native_audit.class_to_ont.get(m.subject_id),
                native_audit.class_to_ont.get(m.object_id),
            }
            == {o1, o2}
        ]
        if not sub_mappings:
            pairwise_audits[pair_key] = {
                "mappings": 0,
                "unsat_delta": 0,
                "cons_viol": 0,
            }
            continue

        sub_sig = {
            iri: oid for iri, oid in native_audit.class_to_ont.items() if oid in {o1, o2}
        }
        pair_native = NativeHubAudit(
            suite_id=f"{native_audit.suite_id}_{pair_key}",
            ontology_paths={
                o1: native_audit.ontology_paths[o1],
                o2: native_audit.ontology_paths[o2],
            },
            class_to_ont=sub_sig,
            native_unsat_classes={u for u in native_audit.native_unsat_classes if u in sub_sig},
            intra_closure_parquet=native_audit.intra_closure_parquet,
            term_metadata={},
            per_ont_class_counts={
                o1: native_audit.per_ont_class_counts.get(o1, 0),
                o2: native_audit.per_ont_class_counts.get(o2, 0),
            },
            per_ont_unsat_counts={
                o1: native_audit.per_ont_unsat_counts.get(o1, 0),
                o2: native_audit.per_ont_unsat_counts.get(o2, 0),
            },
            wall_seconds=0.0,
            peak_rss_mb=native_audit.peak_rss_mb,
            sig_substrings=native_audit.sig_substrings,
        )
        pw_bridge = write_mappings_owl(
            sub_mappings, None, work_dir / f"pw_{pair_key}" / "bridge.owl"
        )
        pw_rep = audit_merged_hub(
            native_audit=pair_native,
            bridge_owl_path=pw_bridge,
            work_dir=work_dir / f"pw_{pair_key}" / "elk",
            tag=f"pw_{pair_key}",
            heap_flag=heap_flag,
        )
        u_cnt = len(pw_rep.unsat_delta)
        c_cnt = pw_rep.cons_viol_count
        tot_pw_unsat += u_cnt
        tot_pw_cons += c_cnt
        if u_cnt > 0 or c_cnt > 0:
            # Upper-bound pairwise conflict estimate before MUS
            conflicting_pw_mappings += min(len(sub_mappings), u_cnt + c_cnt)
        pairwise_audits[pair_key] = {
            "mappings": len(sub_mappings),
            "unsat_delta": u_cnt,
            "cons_viol": c_cnt,
        }

    pw_rate = conflicting_pw_mappings / max(1, len(mappings))
    return PairwiseVsHubBreakdown(
        suite_id=native_audit.suite_id,
        pairwise_audits=pairwise_audits,
        total_pairwise_unsat=tot_pw_unsat,
        total_pairwise_consviol=tot_pw_cons,
        hub_unsat_delta=len(full_hub_audit.unsat_delta),
        hub_consviol=full_hub_audit.cons_viol_count,
        pairwise_conflict_mapping_rate=round(pw_rate, 6),
    )


def _prf1(pred_set: set[tuple[str, str]], gold_set: set[tuple[str, str]]) -> dict[str, float]:
    if not gold_set and not pred_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    tp = len(pred_set & gold_set)
    p = tp / len(pred_set) if pred_set else 0.0
    r = tp / len(gold_set) if gold_set else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
    return {"precision": round(p, 6), "recall": round(r, 6), "f1": round(f1, 6)}


def evaluate_alignment_against_gold(
    mappings: Sequence[MappingRecord],
    selected_actions: dict[str, str],
    gold_equiv_pairs: set[tuple[str, str]],
    gold_sub_pairs: set[tuple[str, str]],
    contested_ids: set[str] | None = None,
) -> dict[str, float]:
    """Compute Equivalence P/R/F1, Directional Subsumption P/R/F1, Retention, and Bridge Preservation."""
    cfg = load_lattice_config()
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}

    # Canonicalize undirected equivalence pairs as sorted (min_iri, max_iri)
    canon_gold_equiv = {(min(a, b), max(a, b)) for a, b in gold_equiv_pairs}
    pred_equiv: set[tuple[str, str]] = set()
    pred_sub: set[tuple[str, str]] = set()

    for m in mappings:
        act = selected_actions.get(m.mapping_id, "keep_equiv")
        pair = action_to_owl_pair(m, act)
        if pair is None:
            continue
        rel, lhs, rhs = pair
        if rel == "equiv":
            pred_equiv.add((min(lhs, rhs), max(lhs, rhs)))
        else:
            pred_sub.add((lhs, rhs))

    eq_metrics = _prf1(pred_equiv, canon_gold_equiv)
    sub_metrics = _prf1(pred_sub, gold_sub_pairs)
    retention = compute_semantic_retention(mappings, selected_actions, rho_map)

    c_ids = contested_ids if contested_ids is not None else {m.mapping_id for m in mappings}
    contested_total = len(c_ids)
    preserved_directional = sum(
        1
        for mid in c_ids
        if selected_actions.get(mid, "keep_equiv") in {"downgrade_sub", "downgrade_sup", "weaken_ancestor"}
    )
    preserved_any = sum(
        1
        for mid in c_ids
        if selected_actions.get(mid, "keep_equiv") != "negate_drop"
    )

    return {
        "equiv_precision": eq_metrics["precision"],
        "equiv_recall": eq_metrics["recall"],
        "equiv_f1": eq_metrics["f1"],
        "sub_precision": sub_metrics["precision"],
        "sub_recall": sub_metrics["recall"],
        "sub_f1": sub_metrics["f1"],
        "semantic_retention": round(retention, 6),
        "contested_directional_preservation": round(
            preserved_directional / max(1, contested_total), 6
        ),
        "contested_non_drop_rate": round(preserved_any / max(1, contested_total), 6),
    }


def paired_bootstrap_and_tests(
    item_ids: Sequence[str],
    treatment_scores: dict[str, float],
    baseline_scores: dict[str, float],
    treatment_correct: dict[str, bool] | None = None,
    baseline_correct: dict[str, bool] | None = None,
    n_boot: int = 10000,
    seed: int = 20261116,
) -> dict[str, float]:
    """Compute 10,000-iteration paired bootstrap 95% CIs, two-sided Wilcoxon signed-rank, and McNemar's test."""
    ids = list(item_ids)
    n = len(ids)
    if n == 0:
        return {
            "n": 0,
            "treatment_mean": 0.0,
            "baseline_mean": 0.0,
            "mean_diff": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "wilcoxon_p": 1.0,
            "mcnemar_p": 1.0,
        }

    t_arr = np.array([float(treatment_scores[i]) for i in ids], dtype=np.float64)
    b_arr = np.array([float(baseline_scores[i]) for i in ids], dtype=np.float64)
    diffs = t_arr - b_arr

    rng = np.random.default_rng(seed)
    boot_idx = rng.integers(0, n, size=(n_boot, n))
    boot_means = diffs[boot_idx].mean(axis=1)
    ci_low, ci_high = np.percentile(boot_means, [2.5, 97.5])

    if np.allclose(diffs, 0.0):
        w_p = 1.0
    else:
        try:
            w_res = stats.wilcoxon(t_arr, b_arr, zero_method="wilcox", alternative="two-sided")
            w_p = float(w_res.pvalue)
        except ValueError:
            w_p = 1.0

    mc_p = 1.0
    if treatment_correct is not None and baseline_correct is not None:
        t_c = np.array([bool(treatment_correct[i]) for i in ids])
        b_c = np.array([bool(baseline_correct[i]) for i in ids])
        n11 = int(np.sum(t_c & b_c))
        n10 = int(np.sum(t_c & (~b_c)))
        n01 = int(np.sum((~t_c) & b_c))
        n00 = int(np.sum((~t_c) & (~b_c)))
        if (n10 + n01) > 0:
            mc_res = mcnemar([[n11, n10], [n01, n00]], exact=(n10 + n01) < 25)
            mc_p = float(mc_res.pvalue)

    return {
        "n": int(n),
        "treatment_mean": round(float(t_arr.mean()), 6),
        "baseline_mean": round(float(b_arr.mean()), 6),
        "mean_diff": round(float(diffs.mean()), 6),
        "ci95_low": round(float(ci_low), 6),
        "ci95_high": round(float(ci_high), 6),
        "wilcoxon_p": round(w_p, 8),
        "mcnemar_p": round(mc_p, 8),
    }


def apply_bh_fdr(comparisons: dict[str, dict[str, float]], alpha: float = 0.01) -> dict[str, dict[str, float | bool]]:
    """Apply Benjamini-Hochberg FDR correction across multiple hypothesis comparisons."""
    keys = sorted(comparisons.keys())
    if not keys:
        return {}
    raw_ps = [float(comparisons[k]["wilcoxon_p"]) for k in keys]
    reject, qvals, _, _ = multipletests(raw_ps, alpha=alpha, method="fdr_bh")
    enriched: dict[str, dict[str, float | bool]] = {}
    for idx, k in enumerate(keys):
        row = dict(comparisons[k])
        row["wilcoxon_q_fdr_bh"] = round(float(qvals[idx]), 8)
        row["significant_fdr_001"] = bool(reject[idx])
        enriched[k] = row
    return enriched
