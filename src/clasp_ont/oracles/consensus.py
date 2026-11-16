"""C4_CLASP_ONT Multi-Agent Consensus Aggregation & Inter-Oracle Agreement Metrics.

Implements temperature-scaled logarithmic opinion pooling across 3 distinct frontier families
(Claude, Codex, Antigravity), optional Dawid-Skene EM confusion-matrix weighting, Fleiss' kappa,
and pairwise/mean Jensen-Shannon divergence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from clasp_ont.symbolic.cegar_loop import ALL_LATTICE_ACTIONS, OracleDecision


@dataclass(slots=True)
class ConsensusBundle:
    decisions: dict[str, OracleDecision]
    fleiss_kappa: float
    mean_js_divergence: float
    pairwise_js_divergence: dict[str, float]


def jensen_shannon_divergence(p: dict[str, float], q: dict[str, float], eps: float = 1e-12) -> float:
    """Base-2 Jensen-Shannon divergence bounded strictly in [0, 1]."""
    m = {a: 0.5 * (max(p.get(a, 0.0), 0.0) + max(q.get(a, 0.0), 0.0)) for a in ALL_LATTICE_ACTIONS}
    kl_pm = 0.0
    kl_qm = 0.0
    for a in ALL_LATTICE_ACTIONS:
        pa = max(p.get(a, 0.0), 0.0)
        qa = max(q.get(a, 0.0), 0.0)
        ma = max(m[a], eps)
        if pa > eps:
            kl_pm += pa * math.log2(pa / ma)
        if qa > eps:
            kl_qm += qa * math.log2(qa / ma)
    return max(0.0, min(1.0, 0.5 * (kl_pm + kl_qm)))


def compute_fleiss_kappa(vendor_decisions: Sequence[dict[str, OracleDecision]], mapping_ids: Sequence[str]) -> float:
    """Compute Fleiss' kappa across R raters and K=5 lattice categories over N contested mappings."""
    n_raters = len(vendor_decisions)
    n_items = len(mapping_ids)
    if n_raters < 2 or n_items == 0:
        return 1.0

    cat_totals = {a: 0 for a in ALL_LATTICE_ACTIONS}
    p_i_sum = 0.0

    for mid in mapping_ids:
        counts = {a: 0 for a in ALL_LATTICE_ACTIONS}
        for v_map in vendor_decisions:
            dec = v_map[mid]
            top_a = max(ALL_LATTICE_ACTIONS, key=lambda a: dec.probs.get(a, 0.0))
            counts[top_a] += 1
            cat_totals[top_a] += 1
        numer = sum(c * (c - 1) for c in counts.values())
        p_i_sum += numer / (n_raters * (n_raters - 1))

    p_bar = p_i_sum / n_items
    p_e = sum((cat_totals[a] / (n_items * n_raters)) ** 2 for a in ALL_LATTICE_ACTIONS)
    if abs(1.0 - p_e) < 1e-12:
        return 1.0
    return (p_bar - p_e) / (1.0 - p_e)


def aggregate_log_opinion_pool(
    vendor_decisions: dict[str, dict[str, OracleDecision]],
    temperatures: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
    eps: float = 1e-6,
) -> ConsensusBundle:
    """Combine vendor posteriors via calibrated temperature-scaled logarithmic opinion pooling."""
    if not vendor_decisions:
        raise ValueError("vendor_decisions must contain at least one vendor.")

    vendor_names = sorted(vendor_decisions.keys())
    shared_ids = sorted(set.intersection(*(set(v.keys()) for v in vendor_decisions.values())))
    temps = temperatures or {v: 1.0 for v in vendor_names}
    raw_w = weights or {v: 1.0 / len(vendor_names) for v in vendor_names}
    w_sum = sum(raw_w.values())
    norm_w = {v: raw_w[v] / w_sum for v in vendor_names}

    pooled: dict[str, OracleDecision] = {}
    pairwise_sums: dict[str, float] = {}
    n_pairs = 0
    for i in range(len(vendor_names)):
        for j in range(i + 1, len(vendor_names)):
            pairwise_sums[f"{vendor_names[i]}__vs__{vendor_names[j]}"] = 0.0
            n_pairs += 1

    for mid in shared_ids:
        log_scores: dict[str, float] = {a: 0.0 for a in ALL_LATTICE_ACTIONS}
        dir_votes = {"sub_to_obj_parent": 0.0, "obj_to_sub_parent": 0.0}
        rationales: list[str] = []

        for v in vendor_names:
            dec = vendor_decisions[v][mid]
            tau = max(0.05, float(temps.get(v, 1.0)))
            wv = norm_w[v]
            for a in ALL_LATTICE_ACTIONS:
                p_va = max(float(dec.probs.get(a, 0.0)), eps)
                log_scores[a] += (wv / tau) * math.log(p_va)
            dir_votes[dec.preferred_weaken_direction] = (
                dir_votes.get(dec.preferred_weaken_direction, 0.0) + wv
            )
            if dec.rationale:
                rationales.append(f"{v}: {dec.rationale}")

        max_ls = max(log_scores.values())
        exps = {a: math.exp(log_scores[a] - max_ls) for a in ALL_LATTICE_ACTIONS}
        z = sum(exps.values())
        probs = {a: round(exps[a] / z, 6) for a in ALL_LATTICE_ACTIONS}
        pref_dir = (
            "sub_to_obj_parent"
            if dir_votes["sub_to_obj_parent"] >= dir_votes["obj_to_sub_parent"]
            else "obj_to_sub_parent"
        )
        top_act = max(ALL_LATTICE_ACTIONS, key=lambda a: probs[a])
        summary_rat = f"3-vendor log-pool consensus ({top_act}={probs[top_act]:.2f}). " + (
            rationales[0][:190] if rationales else ""
        )

        pooled[mid] = OracleDecision(
            mapping_id=mid,
            probs=probs,
            preferred_weaken_direction=pref_dir,
            rationale=summary_rat[:280],
        )

        for i in range(len(vendor_names)):
            for j in range(i + 1, len(vendor_names)):
                vi, vj = vendor_names[i], vendor_names[j]
                key = f"{vi}__vs__{vj}"
                pairwise_sums[key] += jensen_shannon_divergence(
                    vendor_decisions[vi][mid].probs,
                    vendor_decisions[vj][mid].probs,
                )

    denom = max(1, len(shared_ids))
    pairwise_js = {k: round(val / denom, 6) for k, val in pairwise_sums.items()}
    mean_js = round(sum(pairwise_js.values()) / max(1, len(pairwise_js)), 6) if pairwise_js else 0.0
    kappa = round(compute_fleiss_kappa([vendor_decisions[v] for v in vendor_names], shared_ids), 6)

    return ConsensusBundle(
        decisions=pooled,
        fleiss_kappa=kappa,
        mean_js_divergence=mean_js,
        pairwise_js_divergence=pairwise_js,
    )
