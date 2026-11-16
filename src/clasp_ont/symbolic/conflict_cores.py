"""Stage 2 minimal conflict core (MUPS / MUS) extractor via incremental ELK QuickXplain."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord, action_to_owl_pair, compact_iri
from clasp_ont.symbolic.conservativity import HubAuditReport
from clasp_ont.symbolic.reasoner_elk import run_elk_quickxplain_mus


@dataclass(slots=True)
class ConflictCore:
    core_id: str
    kind: str  # "UNSAT" | "CONSVIOL"
    target_desc: str
    literals: tuple[tuple[str, str], ...]  # Sorted unique ((mapping_id, prop_tag), ...)


def _select_stratified_targets(
    audit: HubAuditReport,
    max_unsat: int = 250,
    max_consviol: int = 350,
) -> list[tuple[str, str, str]]:
    """Select complete cover on moderate hubs and stratified root-diverse cover on massive cascades."""
    targets: list[tuple[str, str, str]] = []
    for u in audit.unsat_delta[:max_unsat]:
        targets.append(("UNSAT", u, ""))

    if len(audit.cons_violations) <= max_consviol:
        for sub_i, sup_i, _ont_id in audit.cons_violations:
            targets.append(("SUB", sub_i, sup_i))
    else:
        # Stratified cover across ontologies and distinct (ont_id, sup_iri) / (ont_id, sub_iri) buckets
        seen_sup: set[tuple[str, str]] = set()
        seen_sub: set[tuple[str, str]] = set()
        picked: list[tuple[str, str]] = []
        remainder: list[tuple[str, str]] = []
        for sub_i, sup_i, ont_id in audit.cons_violations:
            if (ont_id, sup_i) not in seen_sup or (ont_id, sub_i) not in seen_sub:
                seen_sup.add((ont_id, sup_i))
                seen_sub.add((ont_id, sub_i))
                picked.append((sub_i, sup_i))
                if len(picked) >= max_consviol:
                    break
            else:
                remainder.append((sub_i, sup_i))
        if len(picked) < max_consviol:
            picked.extend(remainder[: max_consviol - len(picked)])
        for sub_i, sup_i in picked:
            targets.append(("SUB", sub_i, sup_i))
    return targets


def build_directional_half_axioms(
    mappings: Sequence[MappingRecord],
) -> list[tuple[str, str, str, str, str]]:
    """Decompose input mappings into directional Horn half-edges for exact initial MUS orientation."""
    axioms: list[tuple[str, str, str, str, str]] = []
    for m in mappings:
        if m.predicate_id == "skos:broadMatch":
            axioms.append((m.mapping_id, "dir_sub", "sub", m.subject_id, m.object_id))
        elif m.predicate_id == "skos:narrowMatch":
            axioms.append((m.mapping_id, "dir_sup", "sub", m.object_id, m.subject_id))
        else:
            axioms.append((m.mapping_id, "dir_sub", "sub", m.subject_id, m.object_id))
            axioms.append((m.mapping_id, "dir_sup", "sub", m.object_id, m.subject_id))
    return axioms


def build_action_specific_axioms(
    mappings: Sequence[MappingRecord],
    actions: dict[str, str],
) -> list[tuple[str, str, str, str, str]]:
    """Decompose candidate assignment actions into grounded OWL axioms for secondary CEGAR MUS extraction."""
    axioms: list[tuple[str, str, str, str, str]] = []
    for m in mappings:
        act = actions.get(m.mapping_id, "keep_equiv")
        if act == "negate_drop":
            continue
        if act == "keep_equiv":
            if m.predicate_id == "skos:broadMatch":
                axioms.append((m.mapping_id, "dir_sub", "sub", m.subject_id, m.object_id))
            elif m.predicate_id == "skos:narrowMatch":
                axioms.append((m.mapping_id, "dir_sup", "sub", m.object_id, m.subject_id))
            else:
                axioms.append((m.mapping_id, "dir_sub", "sub", m.subject_id, m.object_id))
                axioms.append((m.mapping_id, "dir_sup", "sub", m.object_id, m.subject_id))
        elif act == "downgrade_sub":
            axioms.append((m.mapping_id, "dir_sub", "sub", m.subject_id, m.object_id))
        elif act == "downgrade_sup":
            axioms.append((m.mapping_id, "dir_sup", "sub", m.object_id, m.subject_id))
        elif act == "weaken_ancestor":
            pair = action_to_owl_pair(m, "weaken_ancestor")
            if pair is not None:
                _rel, lhs, rhs = pair
                tag = (
                    "weak_sub"
                    if (m.weaken_direction == "sub_to_obj_parent" and m.object_parent1_id)
                    else "weak_sup"
                )
                axioms.append((m.mapping_id, tag, "sub", lhs, rhs))
    return axioms


def extract_conflict_cores(
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    hub_audit: HubAuditReport,
    actions: dict[str, str] | None = None,
    max_unsat_targets: int = 250,
    max_consviol_targets: int = 350,
    heap_flag: str = "-Xmx4G",
) -> tuple[list[ConflictCore], dict[str, list[str]]]:
    """Extract deduplicated minimal conflict cores and per-mapping human-readable conflict traces."""
    targets = _select_stratified_targets(
        hub_audit, max_unsat=max_unsat_targets, max_consviol=max_consviol_targets
    )
    if not targets:
        return [], {}

    active_axioms = (
        build_directional_half_axioms(mappings)
        if actions is None
        else build_action_specific_axioms(mappings, actions)
    )
    base_onts: list[Path] = list(native_audit.ontology_paths.values())
    raw_cores = run_elk_quickxplain_mus(
        base_ontology_paths=base_onts,
        active_axioms=active_axioms,
        targets=targets,
        heap_flag=heap_flag,
    )

    seen_lits: dict[tuple[tuple[str, str], ...], ConflictCore] = {}
    traces_by_mapping: dict[str, list[str]] = {}

    for _t_idx, kind, a1, a2, members in raw_cores:
        # Collapse {(m, dir_sub), (m, dir_sup)} on same m into {(m, exact)} if both directions required
        by_m: dict[str, set[str]] = {}
        for mid, tag in members:
            by_m.setdefault(mid, set()).add(tag)
        canon_list: list[tuple[str, str]] = []
        for mid in sorted(by_m.keys()):
            tags = by_m[mid]
            if "dir_sub" in tags and "dir_sup" in tags:
                canon_list.append((mid, "exact"))
            else:
                for t in sorted(tags):
                    canon_list.append((mid, t))
        lit_key = tuple(canon_list)

        if kind == "UNSAT":
            desc = f"Unsat({compact_iri(a1)})"
            core_kind = "UNSAT"
        else:
            desc = f"ConsViol({compact_iri(a1)} <= {compact_iri(a2)})"
            core_kind = "CONSVIOL"

        if lit_key not in seen_lits:
            cid = f"core_{len(seen_lits) + 1:05d}"
            seen_lits[lit_key] = ConflictCore(
                core_id=cid,
                kind=core_kind,
                target_desc=desc,
                literals=lit_key,
            )

        chain_str = " + ".join(f"{m}:{t}" for m, t in lit_key)
        trace_msg = f"{desc} via [{chain_str}]"
        for mid, _ in lit_key:
            bucket = traces_by_mapping.setdefault(mid, [])
            if trace_msg not in bucket and len(bucket) < 3:
                bucket.append(trace_msg)

    # Prune non-minimal supersets across distinct targets so K contains strictly minimal nogoods
    all_cores = list(seen_lits.values())
    lit_sets = [(c, set(c.literals)) for c in all_cores]
    minimal_cores: list[ConflictCore] = []
    for i, (c_i, s_i) in enumerate(lit_sets):
        is_superset = False
        for j, (_c_j, s_j) in enumerate(lit_sets):
            if i != j and len(s_j) < len(s_i) and s_j.issubset(s_i):
                is_superset = True
                break
        if not is_superset:
            minimal_cores.append(c_i)

    return minimal_cores, traces_by_mapping
