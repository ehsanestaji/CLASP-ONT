"""Exact optimality verification of Potassco Clingo lattice solver against brute-force search."""

from __future__ import annotations

import itertools
import math
import random

from clasp_ont.ingest.sssom_io import MappingRecord
from clasp_ont.symbolic.cegar_loop import (
    OracleDecision,
    allowed_actions_for_mapping,
    load_lattice_config,
    resolve_weaken_direction,
    solve_clingo_lattice,
)
from clasp_ont.symbolic.conflict_cores import ConflictCore


def _prop_holds_for_action(act: str, weaken_dir: str, prop: str) -> bool:
    if prop == "exact":
        return act == "keep_equiv"
    if prop == "dir_sub":
        return act in {"keep_equiv", "downgrade_sub"}
    if prop == "dir_sup":
        return act in {"keep_equiv", "downgrade_sup"}
    if prop == "weak_sub":
        return act in {"keep_equiv", "downgrade_sub"} or (act == "weaken_ancestor" and weaken_dir == "sub")
    if prop == "weak_sup":
        return act in {"keep_equiv", "downgrade_sup"} or (act == "weaken_ancestor" and weaken_dir == "sup")
    return act == prop


def _brute_force_max_utility(
    records: list[MappingRecord],
    cores: list[ConflictCore],
    decs: dict[str, OracleDecision],
    rho_map: dict[str, float],
    scale: int = 1000,
) -> int:
    domains = [allowed_actions_for_mapping(r) for r in records]
    w_dirs = {r.mapping_id: resolve_weaken_direction(r, decs.get(r.mapping_id)) for r in records}
    best_u = -1

    for combo in itertools.product(*domains):
        assign = {records[i].mapping_id: combo[i] for i in range(len(records))}
        violated = False
        for c in cores:
            if all(_prop_holds_for_action(assign[m], w_dirs[m], prop) for m, prop in c.literals):
                violated = True
                break
        if violated:
            continue
        u = 0
        for r in records:
            a = assign[r.mapping_id]
            p = decs[r.mapping_id].probs[a]
            u += int(math.floor(scale * r.confidence * rho_map[a] * p))
        if u > best_u:
            best_u = u
    return best_u


def test_clingo_matches_bruteforce_global_optimum_and_handles_equal_weights() -> None:
    cfg = load_lattice_config()
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}
    rng = random.Random(20261116)

    # Include identical weights across distinct mappings to verify no Clingo set-deduplication bug
    records = [
        MappingRecord(
            mapping_id=f"m{i}",
            subject_id=f"http://ex.org/s{i}",
            predicate_id="skos:exactMatch",
            object_id=f"http://ex.org/o{i}",
            confidence=0.90,
            subject_parent1_id=f"http://ex.org/sp{i}",
            object_parent1_id=f"http://ex.org/op{i}",
        )
        for i in range(6)
    ]

    for trial in range(8):
        decs: dict[str, OracleDecision] = {}
        for i, r in enumerate(records):
            if trial == 0 and i < 3:
                # Identical probability distributions across m0, m1, m2
                probs = {
                    "keep_equiv": 0.40,
                    "downgrade_sub": 0.30,
                    "downgrade_sup": 0.15,
                    "weaken_ancestor": 0.10,
                    "negate_drop": 0.05,
                }
            else:
                raw = [rng.uniform(0.05, 1.0) for _ in range(5)]
                s = sum(raw)
                probs = {
                    "keep_equiv": raw[0] / s,
                    "downgrade_sub": raw[1] / s,
                    "downgrade_sup": raw[2] / s,
                    "weaken_ancestor": raw[3] / s,
                    "negate_drop": raw[4] / s,
                }
            decs[r.mapping_id] = OracleDecision(
                mapping_id=r.mapping_id,
                probs=probs,
                preferred_weaken_direction="sub_to_obj_parent" if (i % 2 == 0) else "obj_to_sub_parent",
            )

        cores = [
            ConflictCore("c1", "UNSAT", "t1", (("m0", "dir_sub"), ("m1", "dir_sup"))),
            ConflictCore("c2", "CONSVIOL", "t2", (("m1", "exact"),)),
            ConflictCore("c3", "UNSAT", "t3", (("m2", "weak_sub"), ("m3", "dir_sub"))),
            ConflictCore("c4", "CONSVIOL", "t4", (("m4", "dir_sub"),)),
            ConflictCore("c5", "CONSVIOL", "t5", (("m4", "dir_sup"),)),
            ConflictCore("c6", "UNSAT", "t6", (("m5", "weak_sup"),)),
        ]

        sol, clingo_u = solve_clingo_lattice(records, cores, decs, rho_map, scale=1000, threads=2)
        bf_u = _brute_force_max_utility(records, cores, decs, rho_map, scale=1000)
        assert len(sol) == len(records)
        assert clingo_u == bf_u
