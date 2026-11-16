"""Stage 3 OAK-backed compact conflict-core dossier builder (<= 350 tokens, JSON-Schema validated)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import jsonschema

from clasp_ont.ingest.preclean import NativeHubAudit, TermMetadata
from clasp_ont.ingest.sssom_io import MappingRecord, compact_iri

DOSSIER_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "dossier.schema.json"


def _estimate_tokens(payload: dict) -> int:
    """Conservative token count estimator (~3.8 chars per token for compact JSON)."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, (len(raw) + 3) // 4)


def _load_oak_fallback_labels(owl_path: Path, missing_iris: list[str]) -> dict[str, tuple[str, str]]:
    """Optional OAK rdflib adapter lookup for any terms missing annotations in fast scan."""
    if not missing_iris:
        return {}
    out: dict[str, tuple[str, str]] = {}
    try:
        from oaklib import get_adapter

        adapter = get_adapter(f"pronto:{owl_path}") if str(owl_path).endswith(".obo") else get_adapter(f"rdflib:{owl_path}")
        for iri in missing_iris:
            curie = adapter.uri_to_curie(iri) or iri
            lbl = adapter.label(curie) or ""
            defn = adapter.definition(curie) or ""
            if lbl or defn:
                out[iri] = (lbl, defn[:300])
    except Exception:
        pass
    return out


def _fmt_rel_list(iris: list[str], meta_map: dict[str, TermMetadata], limit: int) -> list[str]:
    res: list[str] = []
    for iri in iris[:limit]:
        curie = compact_iri(iri)
        lbl = meta_map[iri].label if iri in meta_map else ""
        entry = f"{curie} ({lbl[:45]})" if lbl else curie
        res.append(entry)
    return res


def build_dossiers(
    native_audit: NativeHubAudit,
    contested_mappings: Sequence[MappingRecord],
    conflict_traces: dict[str, list[str]],
    out_jsonl_path: Path | None = None,
) -> list[dict]:
    """Build schema-validated <= 350-token JSON dossiers for all contested mappings."""
    schema = json.loads(DOSSIER_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    # Check if any contested term needs OAK fallback enrichment
    missing_by_ont: dict[str, list[str]] = {}
    for m in contested_mappings:
        for iri in (m.subject_id, m.object_id):
            tm = native_audit.term_metadata.get(iri)
            if tm is not None and not tm.label and not tm.definition:
                missing_by_ont.setdefault(tm.ont_id, []).append(iri)

    for ont_id, miss_list in missing_by_ont.items():
        ont_path = native_audit.ontology_paths.get(ont_id)
        if ont_path and ont_path.is_file():
            enriched = _load_oak_fallback_labels(ont_path, miss_list)
            for iri, (lbl, defn) in enriched.items():
                if iri in native_audit.term_metadata:
                    if lbl:
                        native_audit.term_metadata[iri].label = lbl
                    if defn:
                        native_audit.term_metadata[iri].definition = defn

    dossiers: list[dict] = []
    for m in contested_mappings:
        s_meta = native_audit.term_metadata.get(m.subject_id)
        o_meta = native_audit.term_metadata.get(m.object_id)

        s_ont = s_meta.ont_id if s_meta else native_audit.class_to_ont.get(m.subject_id, "SRC")
        o_ont = o_meta.ont_id if o_meta else native_audit.class_to_ont.get(m.object_id, "TGT")

        s_lbl = (s_meta.label if s_meta and s_meta.label else m.subject_label) or compact_iri(m.subject_id)
        o_lbl = (o_meta.label if o_meta and o_meta.label else m.object_label) or compact_iri(m.object_id)

        s_def = (s_meta.definition if s_meta else "")[:220]
        o_def = (o_meta.definition if o_meta else "")[:220]

        s_syns = (s_meta.synonyms[:3] if s_meta else [])
        o_syns = (o_meta.synonyms[:3] if o_meta else [])

        s_p2 = _fmt_rel_list(s_meta.parents_2hop if s_meta else [], native_audit.term_metadata, 3)
        o_p2 = _fmt_rel_list(o_meta.parents_2hop if o_meta else [], native_audit.term_metadata, 3)

        s_c1 = _fmt_rel_list(s_meta.children_1hop if s_meta else [], native_audit.term_metadata, 2)
        o_c1 = _fmt_rel_list(o_meta.children_1hop if o_meta else [], native_audit.term_metadata, 2)

        s_p1 = compact_iri(s_meta.parent1_id) if (s_meta and s_meta.parent1_id) else None
        o_p1 = compact_iri(o_meta.parent1_id) if (o_meta and o_meta.parent1_id) else None

        raw_traces = conflict_traces.get(m.mapping_id, ["Contested in hub minimal conflict core"])
        trace_str = "; ".join(raw_traces)[:220]

        dossier = {
            "mapping_id": m.mapping_id,
            "suite_id": native_audit.suite_id,
            "subject_id": compact_iri(m.subject_id),
            "subject_ont": s_ont,
            "subject_label": s_lbl[:90],
            "subject_synonyms": [s[:60] for s in s_syns],
            "subject_definition": s_def,
            "subject_parents_2hop": s_p2,
            "subject_children_1hop": s_c1,
            "subject_parent1_id": s_p1,
            "predicate_id": m.predicate_id,
            "confidence": round(float(m.confidence), 4),
            "object_id": compact_iri(m.object_id),
            "object_ont": o_ont,
            "object_label": o_lbl[:90],
            "object_synonyms": [s[:60] for s in o_syns],
            "object_definition": o_def,
            "object_parents_2hop": o_p2,
            "object_children_1hop": o_c1,
            "object_parent1_id": o_p1,
            "conflict_trace": trace_str,
            "estimated_tokens": 100,
        }

        # Tighten definition/synonym budgets iteratively if estimated_tokens > 340
        tok = _estimate_tokens(dossier)
        if tok > 340:
            dossier["subject_definition"] = dossier["subject_definition"][:110]
            dossier["object_definition"] = dossier["object_definition"][:110]
            dossier["subject_synonyms"] = dossier["subject_synonyms"][:2]
            dossier["object_synonyms"] = dossier["object_synonyms"][:2]
            dossier["conflict_trace"] = dossier["conflict_trace"][:130]
            tok = _estimate_tokens(dossier)
        if tok > 345:
            dossier["subject_parents_2hop"] = dossier["subject_parents_2hop"][:2]
            dossier["object_parents_2hop"] = dossier["object_parents_2hop"][:2]
            dossier["subject_children_1hop"] = []
            dossier["object_children_1hop"] = []
            dossier["subject_definition"] = dossier["subject_definition"][:70]
            dossier["object_definition"] = dossier["object_definition"][:70]
            tok = min(350, _estimate_tokens(dossier))

        dossier["estimated_tokens"] = tok
        validator.validate(dossier)
        dossiers.append(dossier)

    if out_jsonl_path is not None:
        out_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with out_jsonl_path.open("w", encoding="utf-8") as fh:
            for d in dossiers:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    return dossiers
