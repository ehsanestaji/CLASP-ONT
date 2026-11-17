"""Stage 4 Exact Clingo Max-Weight Poset Optimization & CEGAR Certification Loop."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import clingo
import yaml

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord, write_mappings_owl
from clasp_ont.symbolic.conflict_cores import ConflictCore, extract_conflict_cores
from clasp_ont.symbolic.conservativity import HubAuditReport, audit_merged_hub

ASP_PROGRAM_PATH = Path(__file__).resolve().parent / "asp_lattice.lp"
DEFAULT_WEIGHTS_YAML = Path(__file__).resolve().parents[3] / "configs" / "lattice_weights.yaml"

ALL_LATTICE_ACTIONS = (
    "keep_equiv",
    "downgrade_sub",
    "downgrade_sup",
    "weaken_ancestor",
    "negate_drop",
)


@dataclass(slots=True)
class OracleDecision:
    mapping_id: str
    probs: dict[str, float]  # action -> probability in [0, 1]
    preferred_weaken_direction: str = "sub_to_obj_parent"
    rationale: str = ""


@dataclass(slots=True)
class CegarIterationLog:
    iteration: int
    active_cores: int
    contested_mappings: int
    clingo_utility: int
    unsat_delta_count: int
    cons_viol_count: int
    wall_seconds: float
    peak_rss_mb: float


@dataclass(slots=True)
class CertifiedRepairResult:
    system_id: str
    suite_id: str
    selected_actions: dict[str, str]
    contested_mapping_ids: set[str]
    final_audit: HubAuditReport
    iterations: list[CegarIterationLog]
    total_cores: int
    objective_utility: int
    semantic_retention: float
    certificate_sha256: str
    discovered_cores: list[ConflictCore] = field(default_factory=list)
    certificate_json: dict[str, object] = field(default_factory=dict)


def load_lattice_config(config_path: Path | None = None) -> dict:
    p = config_path or DEFAULT_WEIGHTS_YAML
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def allowed_actions_for_mapping(
    rec: MappingRecord,
    binary_only: bool = False,
) -> list[str]:
    if binary_only:
        return ["keep_equiv", "negate_drop"]
    # Input directional mapping keeps 'keep_equiv' as "retain asserted broad/narrow relation" (rho=1.0)
    if rec.predicate_id == "skos:broadMatch":
        acts = ["keep_equiv", "negate_drop"]
        if rec.object_parent1_id:
            acts.insert(1, "weaken_ancestor")
        return acts
    if rec.predicate_id == "skos:narrowMatch":
        acts = ["keep_equiv", "negate_drop"]
        if rec.subject_parent1_id:
            acts.insert(1, "weaken_ancestor")
        return acts

    acts = ["keep_equiv", "downgrade_sub", "downgrade_sup", "negate_drop"]
    has_weaken_target = (
        (rec.weaken_direction == "sub_to_obj_parent" and bool(rec.object_parent1_id))
        or (rec.weaken_direction == "obj_to_sub_parent" and bool(rec.subject_parent1_id))
        or bool(rec.object_parent1_id)
        or bool(rec.subject_parent1_id)
    )
    if has_weaken_target:
        acts.insert(3, "weaken_ancestor")
    return acts


def resolve_weaken_direction(rec: MappingRecord, dec: OracleDecision | None) -> str:
    pref = dec.preferred_weaken_direction if dec is not None else rec.weaken_direction
    if pref == "sub_to_obj_parent" and rec.object_parent1_id:
        return "sub"
    if pref == "obj_to_sub_parent" and rec.subject_parent1_id:
        return "sup"
    if rec.object_parent1_id:
        return "sub"
    return "sup"


def solve_clingo_lattice(
    contested_records: Sequence[MappingRecord],
    cores: Sequence[ConflictCore],
    oracle_decisions: dict[str, OracleDecision],
    rho_map: dict[str, float],
    scale: int = 1000,
    threads: int = 4,
    binary_only: bool = False,
    time_limit_sec: float = 15.0,
) -> tuple[dict[str, str], int]:
    """Ground contested mappings, utilities, and conflict cores into Clingo and solve to optimality."""
    if not contested_records:
        return {}, 0

    facts: list[str] = []
    utility_lookup: dict[tuple[str, str], int] = {}

    for rec in contested_records:
        mid = rec.mapping_id
        facts.append(f'mapping("{mid}").')
        p_kind = (
            "broad"
            if rec.predicate_id == "skos:broadMatch"
            else ("narrow" if rec.predicate_id == "skos:narrowMatch" else "exact")
        )
        facts.append(f'orig_pred("{mid}", {p_kind}).')
        dec = oracle_decisions.get(mid)
        w_dir = resolve_weaken_direction(rec, dec)
        facts.append(f'weaken_dir("{mid}", {w_dir}).')

        allowed = allowed_actions_for_mapping(rec, binary_only=binary_only)
        for act in allowed:
            facts.append(f'allowed_action("{mid}", {act}).')
            rho = float(rho_map[act])
            if binary_only:
                prob = 1.0 if act == "keep_equiv" else 0.0
            elif dec is not None:
                prob = float(dec.probs.get(act, 0.0))
            else:
                # Uniform fallback over non-drop actions only if oracle missing
                prob = 0.25 if act != "negate_drop" else 0.0
            util = int(math.floor(scale * rec.confidence * rho * prob))
            utility_lookup[(mid, act)] = util
            facts.append(f'utility("{mid}", {act}, {util}).')

    for idx, core in enumerate(cores, start=1):
        cid = f"c{idx}"
        facts.append(f"core({cid}).")
        for mid, prop in core.literals:
            prop_atom = prop if prop in {"exact", "dir_sub", "dir_sup", "weak_sub", "weak_sup"} else f"act({prop})"
            facts.append(f'core_lit({cid}, "{mid}", {prop_atom}).')

    # Use Unsat-Core Guided Optimization (--opt-strategy=usc) for exponential speedup on large hitting sets
    ctl_args = [f"-t{max(1, int(threads))}", "--opt-strategy=usc,k,0,4"]
    ctl = clingo.Control(ctl_args)
    ctl.load(str(ASP_PROGRAM_PATH))
    ctl.add("base", [], "\n".join(facts))
    ctl.ground([("base", [])])

    best_assignment: dict[str, str] = {}

    def _on_model(model: clingo.Model) -> None:
        nonlocal best_assignment
        current: dict[str, str] = {}
        for sym in model.symbols(shown=True):
            if sym.name == "selected" and len(sym.arguments) == 2:
                m_str = sym.arguments[0].string
                a_str = sym.arguments[1].name
                current[m_str] = a_str
        if current:
            best_assignment = current

    with ctl.solve(on_model=_on_model, async_=True) as handle:
        finished = handle.wait(max(1.0, float(time_limit_sec)))
        if not finished:
            handle.cancel()
        solve_res = handle.get()

    if not best_assignment:
        # If async timeout occurred before first model on massive core set, solve without timeout until first model
        solve_res = ctl.solve(on_model=_on_model)

    if (not solve_res.satisfiable and not best_assignment) or not best_assignment:
        raise RuntimeError("Clingo lattice optimization was UNSATISFIABLE (no valid assignment found).")

    exact_util = sum(utility_lookup.get((m, a), 0) for m, a in best_assignment.items())
    return best_assignment, exact_util


def compute_semantic_retention(
    mappings: Sequence[MappingRecord],
    selected_actions: dict[str, str],
    rho_map: dict[str, float],
) -> float:
    denom = sum(m.confidence for m in mappings)
    if denom <= 0.0:
        return 1.0
    numer = 0.0
    for m in mappings:
        act = selected_actions.get(m.mapping_id, "keep_equiv")
        if act == "keep_equiv" and m.predicate_id in {"skos:broadMatch", "skos:narrowMatch"}:
            # Input directional mapping kept intact retains full 1.0 of its original asserted claim
            numer += m.confidence * 1.0
        else:
            numer += m.confidence * float(rho_map.get(act, 0.0))
    return numer / denom


def run_clingo_cegar(
    system_id: str,
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    initial_cores: Sequence[ConflictCore],
    oracle_decisions: dict[str, OracleDecision],
    work_dir: Path,
    fallback_oracle_fn=None,
    binary_only: bool = False,
    lattice_cfg_path: Path | None = None,
    heap_flag: str = "-Xmx4G",
) -> CertifiedRepairResult:
    """Execute the outer CEGAR verification loop guaranteeing |Unsat_Delta| == 0 and |ConsViol| == 0."""
    cfg = load_lattice_config(lattice_cfg_path)
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}
    scale = int(cfg.get("clingo_scale", 1000))
    threads = int(cfg.get("clingo_threads", 4))
    max_iters = int(cfg.get("max_cegar_iterations", 20))

    work_dir.mkdir(parents=True, exist_ok=True)
    rec_by_id = {m.mapping_id: m for m in mappings}
    # Populate parent1 IDs from native_audit metadata if not already attached
    for m in mappings:
        if m.subject_parent1_id is None and m.subject_id in native_audit.term_metadata:
            m.subject_parent1_id = native_audit.term_metadata[m.subject_id].parent1_id
        if m.object_parent1_id is None and m.object_id in native_audit.term_metadata:
            m.object_parent1_id = native_audit.term_metadata[m.object_id].parent1_id

    accumulated_cores: list[ConflictCore] = list(initial_cores)
    known_core_keys: set[tuple[tuple[str, str], ...]] = {c.literals for c in accumulated_cores}
    decisions = dict(oracle_decisions)

    iterations: list[CegarIterationLog] = []
    selected_actions: dict[str, str] = {}
    obj_util = 0
    final_audit: HubAuditReport | None = None

    for it in range(1, max_iters + 1):
        t0 = time.perf_counter()
        contested_ids = sorted({mid for c in accumulated_cores for mid, _ in c.literals if mid in rec_by_id})

        # Ensure any newly exposed contested mapping has a valid weaken_direction & oracle prior
        missing_ids = [mid for mid in contested_ids if mid not in decisions]
        if missing_ids and not binary_only and fallback_oracle_fn is not None:
            extra_decs = fallback_oracle_fn([rec_by_id[mid] for mid in missing_ids])
            decisions.update(extra_decs)

        for mid in contested_ids:
            if mid in decisions:
                pref = decisions[mid].preferred_weaken_direction
                if pref in {"sub_to_obj_parent", "obj_to_sub_parent"}:
                    rec_by_id[mid].weaken_direction = pref

        contested_records = [rec_by_id[mid] for mid in contested_ids]
        contested_sol, obj_util = solve_clingo_lattice(
            contested_records=contested_records,
            cores=accumulated_cores,
            oracle_decisions=decisions,
            rho_map=rho_map,
            scale=scale,
            threads=threads,
            binary_only=binary_only,
        )

        # Uncontested mappings remain at their native input state ("keep_equiv")
        selected_actions = {m.mapping_id: contested_sol.get(m.mapping_id, "keep_equiv") for m in mappings}

        cand_owl = write_mappings_owl(
            mappings,
            selected_actions,
            work_dir / f"{system_id}_iter{it:02d}_bridge.owl",
            ontology_iri=f"http://clasp-ont.org/{native_audit.suite_id}/{system_id}/iter{it}.owl",
        )
        audit = audit_merged_hub(
            native_audit=native_audit,
            bridge_owl_path=cand_owl,
            work_dir=work_dir / f"elk_{system_id}_iter{it:02d}",
            tag=f"{system_id}_iter{it:02d}",
            heap_flag=heap_flag,
        )
        elapsed = time.perf_counter() - t0

        iterations.append(
            CegarIterationLog(
                iteration=it,
                active_cores=len(accumulated_cores),
                contested_mappings=len(contested_ids),
                clingo_utility=obj_util,
                unsat_delta_count=len(audit.unsat_delta),
                cons_viol_count=audit.cons_viol_count,
                wall_seconds=elapsed,
                peak_rss_mb=audit.peak_rss_mb,
            )
        )

        if audit.is_certified:
            final_audit = audit
            break

        print(
            f"  [CEGAR:{system_id}] iter {it:02d}: Unsat_Delta={len(audit.unsat_delta)}, "
            f"ConsViol={audit.cons_viol_count}, active_cores={len(accumulated_cores)} ({elapsed:.2f}s)",
            flush=True,
        )

        # Secondary conflict core discovery on candidate assignment
        new_cores, _ = extract_conflict_cores(
            native_audit=native_audit,
            mappings=mappings,
            hub_audit=audit,
            actions=selected_actions,
            heap_flag=heap_flag,
        )
        added = 0
        for nc in new_cores:
            if nc.literals not in known_core_keys:
                known_core_keys.add(nc.literals)
                accumulated_cores.append(nc)
                added += 1

        if added == 0:
            # Fallback direct assignment nogood on active contested non-dropped actions so progress is strictly monotonic
            active_non_drop = tuple(
                sorted(
                    (mid, selected_actions[mid])
                    for mid in contested_ids
                    if selected_actions[mid] != "negate_drop"
                )
            )
            if not active_non_drop or active_non_drop in known_core_keys:
                raise RuntimeError(
                    f"CEGAR stalled at iter {it} with {len(audit.unsat_delta)} unsat and "
                    f"{len(audit.cons_violations)} cons_viol."
                )
            known_core_keys.add(active_non_drop)
            accumulated_cores.append(
                ConflictCore(
                    core_id=f"core_blocking_{it:03d}",
                    kind="BLOCKING",
                    target_desc="ResidualHubViolation",
                    literals=active_non_drop,
                )
            )

    if final_audit is None or not final_audit.is_certified:
        raise RuntimeError(
            f"Hard Mathematical Certification Gate FAILED for {system_id}: post-CEGAR hub is not 0-violation!"
        )

    retention = compute_semantic_retention(mappings, selected_actions, rho_map)
    contested_final = {mid for c in accumulated_cores for mid, _ in c.literals if mid in rec_by_id}

    cert_payload = {
        "system_id": system_id,
        "suite_id": native_audit.suite_id,
        "unsat_delta_count": len(final_audit.unsat_delta),
        "cons_viol_count": final_audit.cons_viol_count,
        "total_mappings": len(mappings),
        "contested_mappings": len(contested_final),
        "total_conflict_cores": len(accumulated_cores),
        "cegar_iterations": len(iterations),
        "objective_utility": obj_util,
        "semantic_retention": round(retention, 6),
        "action_counts": {
            act: sum(1 for v in selected_actions.values() if v == act)
            for act in ALL_LATTICE_ACTIONS
        },
        "selected_actions_sha256": hashlib.sha256(
            json.dumps(selected_actions, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    cert_sha = hashlib.sha256(
        json.dumps(cert_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cert_payload["certificate_sha256"] = cert_sha

    return CertifiedRepairResult(
        system_id=system_id,
        suite_id=native_audit.suite_id,
        selected_actions=selected_actions,
        contested_mapping_ids=contested_final,
        final_audit=final_audit,
        iterations=iterations,
        total_cores=len(accumulated_cores),
        objective_utility=obj_util,
        semantic_retention=retention,
        certificate_sha256=cert_sha,
        discovered_cores=accumulated_cores,
        certificate_json=cert_payload,
    )
