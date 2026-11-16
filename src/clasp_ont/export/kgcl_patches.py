"""KGCL (Knowledge Graph Change Language) patch generator and certified SSSOM exporter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from clasp_ont.ingest.sssom_io import MappingRecord, compact_iri, write_sssom_tsv
from clasp_ont.symbolic.cegar_loop import OracleDecision


def export_certified_sssom_and_kgcl(
    mappings: Sequence[MappingRecord],
    selected_actions: dict[str, str],
    oracle_decisions: dict[str, OracleDecision] | None,
    out_sssom_path: Path,
    out_kgcl_path: Path,
    system_id: str = "C4_CLASP_ONT",
    suite_id: str = "Hub",
) -> tuple[Path, Path, list[str]]:
    """Export certified SSSOM TSV (including negative drop records) and standard KGCL patch script."""
    decs = oracle_decisions or {}
    certified_records: list[MappingRecord] = []
    kgcl_commands: list[str] = []

    for m in mappings:
        act = selected_actions.get(m.mapping_id, "keep_equiv")
        dec = decs.get(m.mapping_id)
        rationale = dec.rationale if dec else ""
        s_curie = compact_iri(m.subject_id)
        o_curie = compact_iri(m.object_id)

        if act == "keep_equiv":
            certified_records.append(
                replace(
                    m,
                    curation_rule=f"{system_id}:keep_equiv",
                    comment=rationale or "Certified coherent & conservative",
                )
            )
            continue

        if act == "downgrade_sub":
            certified_records.append(
                replace(
                    m,
                    predicate_id="skos:broadMatch",
                    mapping_justification="semapv:LogicalReasoning",
                    curation_rule=f"{system_id}:downgrade_sub",
                    comment=rationale or "Refined exactMatch -> broadMatch (s <= o) for hub certification",
                )
            )
            kgcl_commands.append(
                f"change relationship between {s_curie} and {o_curie} from {m.predicate_id} to rdfs:subClassOf"
            )
        elif act == "downgrade_sup":
            certified_records.append(
                replace(
                    m,
                    predicate_id="skos:narrowMatch",
                    mapping_justification="semapv:LogicalReasoning",
                    curation_rule=f"{system_id}:downgrade_sup",
                    comment=rationale or "Refined exactMatch -> narrowMatch (o <= s) for hub certification",
                )
            )
            kgcl_commands.append(f"delete edge {s_curie} {m.predicate_id} {o_curie}")
            kgcl_commands.append(f"create edge {o_curie} rdfs:subClassOf {s_curie}")
        elif act == "weaken_ancestor":
            if m.weaken_direction == "sub_to_obj_parent" and m.object_parent1_id:
                p_curie = compact_iri(m.object_parent1_id)
                certified_records.append(
                    replace(
                        m,
                        predicate_id="skos:broadMatch",
                        object_id=m.object_parent1_id,
                        object_label=f"Parent_1({m.object_label or o_curie})",
                        mapping_justification="semapv:LogicalReasoning",
                        curation_rule=f"{system_id}:weaken_ancestor:sub_to_obj_parent",
                        comment=rationale or f"Weakened object {o_curie} -> immediate parent {p_curie}",
                    )
                )
                kgcl_commands.append(f"delete edge {s_curie} {m.predicate_id} {o_curie}")
                kgcl_commands.append(f"create edge {s_curie} rdfs:subClassOf {p_curie}")
            elif m.subject_parent1_id:
                p_curie = compact_iri(m.subject_parent1_id)
                certified_records.append(
                    replace(
                        m,
                        subject_id=m.subject_parent1_id,
                        subject_label=f"Parent_1({m.subject_label or s_curie})",
                        predicate_id="skos:narrowMatch",
                        mapping_justification="semapv:LogicalReasoning",
                        curation_rule=f"{system_id}:weaken_ancestor:obj_to_sub_parent",
                        comment=rationale or f"Weakened subject {s_curie} -> immediate parent {p_curie}",
                    )
                )
                kgcl_commands.append(f"delete edge {s_curie} {m.predicate_id} {o_curie}")
                kgcl_commands.append(f"create edge {o_curie} rdfs:subClassOf {p_curie}")
        elif act == "negate_drop":
            certified_records.append(
                replace(
                    m,
                    predicate_modifier="Not",
                    mapping_justification="semapv:LogicalReasoning",
                    curation_rule=f"{system_id}:negate_drop",
                    comment=rationale or "Dropped conflicting correspondence; emitted negative curation record",
                )
            )
            kgcl_commands.append(f"delete edge {s_curie} {m.predicate_id} {o_curie}")

    write_sssom_tsv(
        certified_records,
        out_sssom_path,
        mapping_set_id=f"https://clasp-ont.org/certified/{suite_id}/{system_id}.sssom.tsv",
    )

    out_kgcl_path.parent.mkdir(parents=True, exist_ok=True)
    with out_kgcl_path.open("w", encoding="utf-8") as fh:
        fh.write(f"# KGCL Certified Patch Bundle | Suite: {suite_id} | System: {system_id}\n")
        for cmd in kgcl_commands:
            fh.write(cmd + "\n")

    return out_sssom_path, out_kgcl_path, kgcl_commands
