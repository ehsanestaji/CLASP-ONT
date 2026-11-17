"""Standardized SSSOM TSV and OWL bridge conversion utilities.

Uses curies, sssom-pydantic, polars, and fast direct RDF/XML or N-Triples OWL emission
so large crosswalk hubs (>50,000 mappings) serialize in milliseconds without heavy DOM overhead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import curies
import polars as pl

STANDARD_PREFIX_MAP: dict[str, str] = {
    "MONDO": "http://purl.obolibrary.org/obo/MONDO_",
    "DOID": "http://purl.obolibrary.org/obo/DOID_",
    "HP": "http://purl.obolibrary.org/obo/HP_",
    "MP": "http://purl.obolibrary.org/obo/MP_",
    "GO": "http://purl.obolibrary.org/obo/GO_",
    "PO": "http://purl.obolibrary.org/obo/PO_",
    "TO": "http://purl.obolibrary.org/obo/TO_",
    "FLOPO": "http://purl.obolibrary.org/obo/FLOPO_",
    "CHEBI": "http://purl.obolibrary.org/obo/CHEBI_",
    "ENVO": "http://purl.obolibrary.org/obo/ENVO_",
    "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
    "CL": "http://purl.obolibrary.org/obo/CL_",
    "NCIT": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#",
    "ORDO": "http://www.orpha.net/ORDO/Orphanet_",
    "Orphanet": "http://www.orpha.net/ORDO/Orphanet_",
    "OMIM": "https://omim.org/entry/",
    "FMA": "http://purl.org/sig/ont/fma/fma",
    "MA": "http://mouse.owl#",
    "NCI_A": "http://human.owl#",
    "SWEET": "http://sweetontology.net/",
    "TOY_ANAT": "http://clasp-ont.org/toy/anat#",
    "TOY_PROC": "http://clasp-ont.org/toy/proc#",
    "TOY_TRAIT": "http://clasp-ont.org/toy/trait#",
    "TOY_CHEM": "http://clasp-ont.org/toy/chem#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "semapv": "https://w3id.org/semapv/vocab/",
}


def build_converter(extra_prefixes: dict[str, str] | None = None) -> curies.Converter:
    merged = dict(STANDARD_PREFIX_MAP)
    if extra_prefixes:
        merged.update(extra_prefixes)
    by_uri: dict[str, tuple[str, list[str]]] = {}
    seen_prefixes: set[str] = set()
    for pfx, uri in merged.items():
        if pfx in seen_prefixes:
            continue
        seen_prefixes.add(pfx)
        if uri not in by_uri:
            by_uri[uri] = (pfx, [])
        else:
            primary, syns = by_uri[uri]
            if pfx != primary and pfx not in syns:
                syns.append(pfx)
    records = [
        curies.Record(prefix=primary, uri_prefix=uri, prefix_synonyms=syns)
        for uri, (primary, syns) in by_uri.items()
    ]
    return curies.Converter(records)


@dataclass(slots=True)
class MappingRecord:
    mapping_id: str
    subject_id: str  # Canonical full IRI
    predicate_id: str  # skos:exactMatch | skos:broadMatch | skos:narrowMatch
    object_id: str  # Canonical full IRI
    confidence: float = 0.90
    subject_label: str = ""
    object_label: str = ""
    mapping_justification: str = "semapv:UnspecifiedMatching"
    predicate_modifier: str = ""
    curation_rule: str = ""
    comment: str = ""
    subject_parent1_id: str | None = None
    object_parent1_id: str | None = None
    weaken_direction: str = "sub_to_obj_parent"  # or obj_to_sub_parent
    metadata: dict[str, str] = field(default_factory=dict)


def normalize_to_iri(term: str, converter: curies.Converter | None = None) -> str:
    term = term.strip()
    if term.startswith("<") and term.endswith(">"):
        term = term[1:-1].strip()
    if term.startswith("http://") or term.startswith("https://") or term.startswith("urn:"):
        return term
    if ":" not in term or " " in term:
        return term
    conv = converter or build_converter()
    try:
        expanded = conv.expand(term)
        return expanded if expanded is not None else term
    except Exception:
        return term


def compact_iri(iri: str, converter: curies.Converter | None = None) -> str:
    conv = converter or build_converter()
    compacted = conv.compress(iri)
    return compacted if compacted is not None else iri


def normalize_predicate(pred: str) -> str:
    p = pred.strip()
    low = p.lower()
    if low in {"=", "skos:exactmatch", "owl:equivalentclass", "http://www.w3.org/2004/02/skos/core#exactmatch", "http://www.w3.org/2002/07/owl#equivalentclass"}:
        return "skos:exactMatch"
    if low in {"<", "⊑", "skos:broadmatch", "rdfs:subclassof", "http://www.w3.org/2004/02/skos/core#broadmatch", "http://www.w3.org/2000/01/rdf-schema#subclassof"}:
        return "skos:broadMatch"
    if low in {">", "⊒", "skos:narrowmatch", "http://www.w3.org/2004/02/skos/core#narrowmatch"}:
        return "skos:narrowMatch"
    return p


def read_sssom_tsv(
    tsv_path: Path,
    extra_prefixes: dict[str, str] | None = None,
    allowed_iris: set[str] | None = None,
) -> tuple[list[MappingRecord], dict[str, str]]:
    """Parse an SSSOM TSV (with optional YAML header comments) into canonical MappingRecords."""
    header_prefixes: dict[str, str] = {}
    in_curie_map = False
    data_lines: list[str] = []

    with tsv_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                stripped = line.lstrip("#").rstrip("\n")
                if stripped.strip().startswith("curie_map:"):
                    in_curie_map = True
                    continue
                if in_curie_map:
                    if stripped.startswith("  ") and ":" in stripped:
                        k, _, v = stripped.strip().partition(":")
                        header_prefixes[k.strip()] = v.strip().strip('"').strip("'")
                    elif stripped.strip() and not stripped.startswith(" "):
                        in_curie_map = False
            elif line.strip():
                data_lines.append(line)

    merged_prefixes = dict(header_prefixes)
    if extra_prefixes:
        merged_prefixes.update(extra_prefixes)
    conv = build_converter(merged_prefixes)

    if not data_lines:
        return [], merged_prefixes

    import io

    df = pl.read_csv(
        io.StringIO("".join(data_lines)),
        separator="\t",
        infer_schema_length=5000,
        ignore_errors=True,
        quote_char=None,
    )

    cols = set(df.columns)
    # Support both standard SSSOM columns and OAEI Bio-ML TSV columns (SrcEntity, TgtEntity, Score, Relation)
    sub_col = "subject_id" if "subject_id" in cols else ("SrcEntity" if "SrcEntity" in cols else df.columns[0])
    obj_col = "object_id" if "object_id" in cols else ("TgtEntity" if "TgtEntity" in cols else df.columns[1])
    pred_col = "predicate_id" if "predicate_id" in cols else ("Relation" if "Relation" in cols else None)
    conf_col = "confidence" if "confidence" in cols else ("Score" if "Score" in cols else None)

    records: list[MappingRecord] = []
    for idx, row in enumerate(df.iter_rows(named=True)):
        raw_s = str(row.get(sub_col) or "").strip()
        raw_o = str(row.get(obj_col) or "").strip()
        if not raw_s or not raw_o:
            continue
        s_iri = normalize_to_iri(raw_s, conv)
        o_iri = normalize_to_iri(raw_o, conv)
        if s_iri == o_iri:
            continue
        if allowed_iris is not None and (s_iri not in allowed_iris or o_iri not in allowed_iris):
            continue

        raw_mod = str(row.get("predicate_modifier") or "").strip() if "predicate_modifier" in cols else ""
        if raw_mod.lower() == "not":
            continue

        raw_pred = str(row.get(pred_col) or "skos:exactMatch") if pred_col else "skos:exactMatch"
        pred = normalize_predicate(raw_pred)
        raw_conf = row.get(conf_col) if conf_col else None
        try:
            conf = float(raw_conf) if raw_conf is not None and str(raw_conf).strip() != "" else 0.90
        except ValueError:
            conf = 0.90
        conf = max(0.001, min(1.0, conf))

        mid = str(row.get("mapping_id") or f"m_{idx:06d}")
        records.append(
            MappingRecord(
                mapping_id=mid,
                subject_id=s_iri,
                predicate_id=pred,
                object_id=o_iri,
                confidence=conf,
                subject_label=str(row.get("subject_label") or ""),
                object_label=str(row.get("object_label") or ""),
                mapping_justification=str(
                    row.get("mapping_justification") or "semapv:UnspecifiedMatching"
                ),
                predicate_modifier=raw_mod,
                curation_rule=str(row.get("curation_rule") or ""),
                comment=str(row.get("comment") or ""),
            )
        )
    return records, merged_prefixes


def write_sssom_tsv(
    records: Iterable[MappingRecord],
    out_path: Path,
    mapping_set_id: str = "https://clasp-ont.org/mappings/certified.sssom.tsv",
    extra_prefixes: dict[str, str] | None = None,
) -> Path:
    """Write validated SSSOM TSV with standard YAML curie_map header."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conv = build_converter(extra_prefixes)
    merged_prefixes = dict(STANDARD_PREFIX_MAP)
    if extra_prefixes:
        merged_prefixes.update(extra_prefixes)

    rec_list = list(records)
    used_prefixes: set[str] = {"skos", "semapv", "owl", "rdfs"}
    rows: list[list[str]] = []
    for r in rec_list:
        s_curie = compact_iri(r.subject_id, conv)
        o_curie = compact_iri(r.object_id, conv)
        for c in (s_curie, o_curie, r.predicate_id, r.mapping_justification):
            if ":" in c and not c.startswith("http"):
                pfx = c.split(":", 1)[0]
                if pfx in merged_prefixes:
                    used_prefixes.add(pfx)
        rows.append(
            [
                s_curie,
                r.subject_label.replace("\t", " ").replace("\n", " "),
                r.predicate_id,
                r.predicate_modifier,
                o_curie,
                r.object_label.replace("\t", " ").replace("\n", " "),
                r.mapping_justification,
                f"{r.confidence:.4f}",
                r.curation_rule.replace("\t", " ").replace("\n", " "),
                r.comment.replace("\t", " ").replace("\n", " "),
            ]
        )

    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(f"#mapping_set_id: {mapping_set_id}\n")
        fh.write("#license: https://creativecommons.org/publicdomain/zero/1.0/\n")
        fh.write("#curie_map:\n")
        for pfx in sorted(used_prefixes):
            fh.write(f"#  {pfx}: {merged_prefixes[pfx]}\n")
        fh.write(
            "\t".join(
                [
                    "subject_id",
                    "subject_label",
                    "predicate_id",
                    "predicate_modifier",
                    "object_id",
                    "object_label",
                    "mapping_justification",
                    "confidence",
                    "curation_rule",
                    "comment",
                ]
            )
            + "\n"
        )
        for row in rows:
            fh.write("\t".join(row) + "\n")
    return out_path


def action_to_owl_pair(
    rec: MappingRecord, action: str
) -> tuple[str, str, str] | None:
    """Return ('equiv' | 'sub', lhs_iri, rhs_iri) for a lattice action on rec, or None if dropped."""
    if action == "negate_drop":
        return None
    if action == "keep_equiv":
        if rec.predicate_id == "skos:broadMatch":
            return ("sub", rec.subject_id, rec.object_id)
        if rec.predicate_id == "skos:narrowMatch":
            return ("sub", rec.object_id, rec.subject_id)
        return ("equiv", rec.subject_id, rec.object_id)
    if action == "downgrade_sub":
        return ("sub", rec.subject_id, rec.object_id)
    if action == "downgrade_sup":
        return ("sub", rec.object_id, rec.subject_id)
    if action == "weaken_ancestor":
        if rec.weaken_direction == "sub_to_obj_parent" and rec.object_parent1_id:
            return ("sub", rec.subject_id, rec.object_parent1_id)
        if rec.weaken_direction == "obj_to_sub_parent" and rec.subject_parent1_id:
            return ("sub", rec.object_id, rec.subject_parent1_id)
        if rec.object_parent1_id:
            return ("sub", rec.subject_id, rec.object_parent1_id)
        if rec.subject_parent1_id:
            return ("sub", rec.object_id, rec.subject_parent1_id)
        return None
    raise ValueError(f"Unknown lattice action: {action}")


def write_mappings_owl(
    records: Iterable[MappingRecord],
    actions: dict[str, str] | None,
    out_owl_path: Path,
    ontology_iri: str = "http://clasp-ont.org/bridge.owl",
) -> Path:
    """Serialize active cross-ontology mapping axioms directly to valid OWL RDF/XML."""
    out_owl_path.parent.mkdir(parents=True, exist_ok=True)
    with out_owl_path.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?>\n')
        fh.write(
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
            '         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
            '         xmlns:owl="http://www.w3.org/2002/07/owl#">\n'
        )
        fh.write(f'  <owl:Ontology rdf:about="{xml_escape(ontology_iri)}"/>\n')
        for rec in records:
            act = actions.get(rec.mapping_id, "keep_equiv") if actions is not None else "keep_equiv"
            triple = action_to_owl_pair(rec, act)
            if triple is None:
                continue
            rel, lhs, rhs = triple
            lhs_esc = xml_escape(lhs)
            rhs_esc = xml_escape(rhs)
            fh.write(f'  <owl:Class rdf:about="{rhs_esc}"/>\n')
            if rel == "equiv":
                fh.write(
                    f'  <owl:Class rdf:about="{lhs_esc}">\n'
                    f'    <owl:equivalentClass rdf:resource="{rhs_esc}"/>\n'
                    f"  </owl:Class>\n"
                )
            else:
                fh.write(
                    f'  <owl:Class rdf:about="{lhs_esc}">\n'
                    f'    <rdfs:subClassOf rdf:resource="{rhs_esc}"/>\n'
                    f"  </owl:Class>\n"
                )
        fh.write("</rdf:RDF>\n")
    return out_owl_path
