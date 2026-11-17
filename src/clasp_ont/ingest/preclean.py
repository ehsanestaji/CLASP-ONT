"""Stage 1 native single-ontology baseline audit, CURIE/namespace normalization, and fast streaming metadata indexing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from clasp_ont.ingest.sssom_io import build_converter, normalize_to_iri
from clasp_ont.symbolic.reasoner_elk import run_elk_closure

RDF_ABOUT = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
RDF_RESOURCE = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource"
OWL_CLASS_TAGS = {
    "{http://www.w3.org/2002/07/owl#}Class",
    "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description",
}
LABEL_TAGS = {
    "{http://www.w3.org/2000/01/rdf-schema#}label",
    "{http://www.w3.org/2004/02/skos/core#}prefLabel",
}
DEF_TAGS = {
    "{http://purl.obolibrary.org/obo/}IAO_0000115",
    "{http://www.w3.org/2004/02/skos/core#}definition",
    "{http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#}P97",
    "{http://www.ebi.ac.uk/efo/}definition",
    "{http://www.w3.org/2000/01/rdf-schema#}comment",
}
SYN_TAGS = {
    "{http://www.geneontology.org/formats/oboInOwl#}hasExactSynonym",
    "{http://www.w3.org/2004/02/skos/core#}altLabel",
    "{http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#}P90",
}
XREF_TAGS = {
    "{http://www.geneontology.org/formats/oboInOwl#}hasDbXref",
    "{http://www.w3.org/2004/02/skos/core#}exactMatch",
    "{http://www.w3.org/2004/02/skos/core#}broadMatch",
    "{http://www.w3.org/2004/02/skos/core#}narrowMatch",
}
DISJOINT_TAG = "{http://www.w3.org/2002/07/owl#}disjointWith"
ALL_DISJOINT_TAG = "{http://www.w3.org/2002/07/owl#}AllDisjointClasses"


@dataclass(slots=True)
class TermMetadata:
    iri: str
    ont_id: str
    label: str = ""
    synonyms: list[str] = field(default_factory=list)
    definition: str = ""
    direct_parents: list[str] = field(default_factory=list)
    parents_2hop: list[str] = field(default_factory=list)
    children_1hop: list[str] = field(default_factory=list)
    depth: int = 1
    descendant_count: int = 0

    @property
    def parent1_id(self) -> str | None:
        return self.direct_parents[0] if self.direct_parents else None


@dataclass(slots=True)
class NativeHubAudit:
    suite_id: str
    ontology_paths: dict[str, Path]
    class_to_ont: dict[str, str]
    native_unsat_classes: set[str]
    intra_closure_parquet: Path
    term_metadata: dict[str, TermMetadata]
    per_ont_class_counts: dict[str, int]
    per_ont_unsat_counts: dict[str, int]
    wall_seconds: float
    peak_rss_mb: float
    native_disjoints: list[tuple[str, str]] = field(default_factory=list)
    full_direct_parents: dict[str, list[str]] = field(default_factory=dict)
    sig_substrings: list[str] = field(default_factory=list)
    conj_rules: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


def _matches_namespace(iri: str, ont_id: str, ns_hint: str | None = None) -> bool:
    if ns_hint and (iri.startswith(ns_hint) or ns_hint in iri):
        return True
    up = ont_id.upper().split("_")[0]
    if f"/obo/{up}_" in iri:
        return True
    if up == "NCIT" and ("Thesaurus.owl#" in iri or "/obo/NCIT_" in iri):
        return True
    if up == "ORDO" and ("Orphanet_" in iri or "/ORDO/" in iri):
        return True
    if up == "OMIM" and ("omim.org" in iri or "/OMIM_" in iri or "OMIM:" in iri):
        return True
    if up == "FMA" and "/fma/" in iri.lower():
        return True
    return False


def stream_owl_annotations_and_xrefs(
    owl_path: Path,
    target_iris: set[str] | None = None,
) -> tuple[dict[str, tuple[str, list[str], str]], list[tuple[str, str, str]], list[tuple[str, str]]]:
    """O(1)-memory C-accelerated ElementTree streaming parser for RDF/XML OWL files.

    Returns:
      - annotations: {iri: (label, synonyms[:6], definition[:450])}
      - raw_xrefs: [(subject_iri, predicate_curie, raw_target_str), ...]
      - disjoint_pairs: [(cls_a_iri, cls_b_iri), ...]
    """
    labels: dict[str, str] = {}
    syns: dict[str, list[str]] = {}
    defs: dict[str, str] = {}
    xrefs: list[tuple[str, str, str]] = []
    disjoints: list[tuple[str, str]] = []
    seen_dj: set[tuple[str, str]] = set()

    try:
        context = ET.iterparse(str(owl_path), events=("start", "end"))
        _, root = next(context)
        depth = 0
        current_subj: str | None = None
        in_all_disjoint = False
        all_dj_members: list[str] = []

        for event, elem in context:
            if event == "start":
                depth += 1
                if depth == 1:
                    if elem.tag in OWL_CLASS_TAGS:
                        about = elem.attrib.get(RDF_ABOUT)
                        if about and (target_iris is None or about in target_iris):
                            current_subj = about
                        else:
                            current_subj = None
                    elif elem.tag == ALL_DISJOINT_TAG:
                        in_all_disjoint = True
                        all_dj_members = []
                elif in_all_disjoint and elem.tag in OWL_CLASS_TAGS:
                    ab = elem.attrib.get(RDF_ABOUT) or elem.attrib.get(RDF_RESOURCE)
                    if ab:
                        all_dj_members.append(ab)
            else:
                if depth == 2 and current_subj is not None:
                    tag = elem.tag
                    if tag in LABEL_TAGS:
                        txt = (elem.text or "").strip()
                        if txt and not labels.get(current_subj):
                            labels[current_subj] = txt
                    elif tag in DEF_TAGS:
                        txt = (elem.text or "").strip()
                        if txt and not defs.get(current_subj):
                            defs[current_subj] = txt[:450]
                    elif tag in SYN_TAGS:
                        txt = (elem.text or "").strip()
                        if txt:
                            bucket = syns.setdefault(current_subj, [])
                            if txt not in bucket and len(bucket) < 6:
                                bucket.append(txt)
                    elif tag in XREF_TAGS:
                        res = elem.attrib.get(RDF_RESOURCE) or (elem.text or "").strip()
                        if res:
                            pred = "skos:exactMatch"
                            if tag.endswith("broadMatch"):
                                pred = "skos:broadMatch"
                            elif tag.endswith("narrowMatch"):
                                pred = "skos:narrowMatch"
                            xrefs.append((current_subj, pred, res))
                    elif tag == DISJOINT_TAG:
                        dj_t = elem.attrib.get(RDF_RESOURCE)
                        if dj_t and dj_t != current_subj:
                            pk = (min(current_subj, dj_t), max(current_subj, dj_t))
                            if pk not in seen_dj:
                                seen_dj.add(pk)
                                disjoints.append(pk)
                elif depth == 1:
                    if in_all_disjoint and len(all_dj_members) >= 2:
                        for i in range(len(all_dj_members)):
                            for j in range(i + 1, min(len(all_dj_members), 30)):
                                a, b = all_dj_members[i], all_dj_members[j]
                                if a != b:
                                    pk = (min(a, b), max(a, b))
                                    if pk not in seen_dj:
                                        seen_dj.add(pk)
                                        disjoints.append(pk)
                    in_all_disjoint = False
                    current_subj = None
                    elem.clear()
                    root.clear()
                depth -= 1
    except ET.ParseError:
        try:
            import py_horned_owl

            onto = py_horned_owl.open_ontology(str(owl_path))
            for iri in (target_iris or set()):
                lbl = onto.get_annotation(iri, "http://www.w3.org/2000/01/rdf-schema#label")
                if lbl:
                    labels[iri] = str(lbl).strip()
                dfn = onto.get_annotation(iri, "http://purl.obolibrary.org/obo/IAO_0000115")
                if dfn:
                    defs[iri] = str(dfn).strip()[:450]
        except Exception:
            pass

    keys = target_iris if target_iris is not None else (set(labels) | set(defs) | set(syns))
    ann_out = {k: (labels.get(k, ""), syns.get(k, []), defs.get(k, "")) for k in keys}
    return ann_out, xrefs, disjoints


def extract_embedded_crosswalks_fast(
    ontology_paths: dict[str, Path],
    class_to_ont: dict[str, str],
) -> list[tuple[str, str, str]]:
    """Extract cross-ontology embedded hasDbXref / skos:*Match bridges across Sig(O_1)..Sig(O_k)."""
    conv = build_converter()
    allowed_iris = set(class_to_ont.keys())
    # Also index local suffix lookup (e.g. "NCIT:C1234" -> full IRI in hub if NCIT uses #C1234 or NCIT_C1234)
    local_code_index: dict[str, str] = {}
    for iri in allowed_iris:
        if "/obo/" in iri:
            tail = iri.rsplit("/obo/", 1)[-1]  # e.g. PO_0009001
            if "_" in tail:
                pfx, code = tail.split("_", 1)
                local_code_index[f"{pfx.upper()}:{code}"] = iri
        elif "Thesaurus.owl#" in iri:
            code = iri.rsplit("#", 1)[-1]
            local_code_index[f"NCIT:{code}"] = iri
            local_code_index[f"NCI:{code}"] = iri
        elif "Orphanet_" in iri:
            code = iri.rsplit("Orphanet_", 1)[-1]
            local_code_index[f"ORDO:{code}"] = iri
            local_code_index[f"ORPHA:{code}"] = iri
            local_code_index[f"Orphanet:{code}"] = iri
        elif "omim.org" in iri or "OMIM" in iri:
            code = iri.rstrip("/").rsplit("/", 1)[-1].rsplit("_", 1)[-1].rsplit("#", 1)[-1]
            local_code_index[f"OMIM:{code}"] = iri

    bridges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for ont_id, owl_path in ontology_paths.items():
        sig_j = {iri for iri, o in class_to_ont.items() if o == ont_id}
        _ann, raw_xrefs, _dj = stream_owl_annotations_and_xrefs(owl_path, target_iris=sig_j)
        for s_iri, pred, raw_tgt in raw_xrefs:
            t_clean = raw_tgt.strip()
            o_iri = None
            if t_clean in allowed_iris:
                o_iri = t_clean
            else:
                up_key = (
                    f"{t_clean.split(':', 1)[0].upper()}:{t_clean.split(':', 1)[1]}"
                    if ":" in t_clean and not t_clean.startswith("http")
                    else ""
                )
                if up_key and up_key in local_code_index:
                    o_iri = local_code_index[up_key]
                else:
                    cand = normalize_to_iri(t_clean, conv)
                    if cand in allowed_iris:
                        o_iri = cand
            if o_iri and o_iri != s_iri and class_to_ont.get(o_iri) != ont_id:
                pair_key = (min(s_iri, o_iri), max(s_iri, o_iri))
                if pair_key not in seen:
                    seen.add(pair_key)
                    bridges.append((s_iri, pred, o_iri))
    return bridges


def run_native_preclean(
    suite_id: str,
    ontology_paths: dict[str, Path],
    work_dir: Path,
    namespace_hints: dict[str, str] | None = None,
    heap_flag: str = "-Xmx4G",
) -> NativeHubAudit:
    """Audit each input ontology O_j natively with ELK and materialize intra_closure.parquet."""
    work_dir.mkdir(parents=True, exist_ok=True)
    ns_hints = namespace_hints or {}
    sig_subs = sorted({v for v in ns_hints.values() if v})

    raw_classes_by_ont: dict[str, set[str]] = {}
    elk_by_ont: dict[str, object] = {}
    total_wall = 0.0
    peak_rss = 0.0

    for ont_id, path in ontology_paths.items():
        elk_res = run_elk_closure(
            [path],
            work_dir=work_dir / "native",
            tag=f"native_{ont_id}",
            heap_flag=heap_flag,
            sig_substrings=sig_subs or None,
        )
        elk_by_ont[ont_id] = elk_res
        total_wall += elk_res.wall_seconds
        peak_rss = max(peak_rss, elk_res.peak_rss_mb)

        closure_df = pl.read_parquet(elk_res.closure_parquet)
        seen = set(closure_df["sub_iri"].to_list()) | set(closure_df["sup_iri"].to_list()) | set(elk_res.unsat_classes)
        raw_classes_by_ont[ont_id] = seen

    # Enforce pairwise-disjoint Sig(O_j)
    iri_appearances: dict[str, list[str]] = {}
    for ont_id, cls_set in raw_classes_by_ont.items():
        for iri in cls_set:
            iri_appearances.setdefault(iri, []).append(ont_id)

    class_to_ont: dict[str, str] = {}
    for iri, onts in iri_appearances.items():
        if ns_hints:
            matched = [o for o in onts if _matches_namespace(iri, o, ns_hints.get(o))]
            if len(matched) == 1:
                class_to_ont[iri] = matched[0]
        elif len(onts) == 1:
            owner = onts[0]
            other_known = [o for o in ontology_paths if o != owner and _matches_namespace(iri, o, ns_hints.get(o))]
            if not other_known:
                class_to_ont[iri] = owner
        else:
            matched = [o for o in onts if _matches_namespace(iri, o, ns_hints.get(o))]
            if len(matched) == 1:
                class_to_ont[iri] = matched[0]

    native_unsat: set[str] = set()
    per_ont_counts: dict[str, int] = {o: 0 for o in ontology_paths}
    per_ont_unsat: dict[str, int] = {o: 0 for o in ontology_paths}
    for _iri, o in class_to_ont.items():
        per_ont_counts[o] += 1

    intra_frames: list[pl.DataFrame] = []
    term_metadata: dict[str, TermMetadata] = {}
    all_disjoints: list[tuple[str, str]] = []
    seen_all_dj: set[tuple[str, str]] = set()
    full_direct_parents: dict[str, list[str]] = {}

    for ont_id, path in ontology_paths.items():
        elk_res = elk_by_ont[ont_id]
        for da, db in elk_res.disjoint_pairs:
            pk = (min(da, db), max(da, db))
            if pk not in seen_all_dj:
                seen_all_dj.add(pk)
                all_disjoints.append(pk)

        full_d_df = pl.read_parquet(elk_res.direct_parents_parquet)
        for sub_i, sup_i in full_d_df.iter_rows():
            if sub_i != sup_i:
                bucket = full_direct_parents.setdefault(sub_i, [])
                if sup_i not in bucket:
                    bucket.append(sup_i)
        sig_j = {iri for iri, o in class_to_ont.items() if o == ont_id}
        unsat_j = elk_res.unsat_classes & sig_j
        native_unsat |= unsat_j
        per_ont_unsat[ont_id] = len(unsat_j)

        c_df = pl.read_parquet(elk_res.closure_parquet)
        sig_list = list(sig_j - unsat_j)
        c_df = c_df.filter(pl.col("sub_iri").is_in(sig_list) & pl.col("sup_iri").is_in(sig_list))
        c_df = c_df.with_columns(pl.lit(ont_id).alias("ont_id")).select(["ont_id", "sub_iri", "sup_iri"])
        intra_frames.append(c_df)

        d_df = pl.read_parquet(elk_res.direct_parents_parquet).filter(
            pl.col("sub_iri").is_in(sig_list) & pl.col("direct_sup_iri").is_in(sig_list)
        )
        direct_parents_map: dict[str, list[str]] = {}
        children_map: dict[str, list[str]] = {}
        for sub_i, sup_i in d_df.iter_rows():
            if sub_i != sup_i:
                direct_parents_map.setdefault(sub_i, []).append(sup_i)
                children_map.setdefault(sup_i, []).append(sub_i)

        for k in direct_parents_map:
            direct_parents_map[k].sort()
        for k in children_map:
            children_map[k].sort()

        depths: dict[str, int] = {}
        roots = [c for c in sig_list if not direct_parents_map.get(c)]
        queue: deque[tuple[str, int]] = deque((r, 1) for r in roots)
        for r in roots:
            depths[r] = 1
        while queue:
            curr, d = queue.popleft()
            for ch in children_map.get(curr, []):
                if ch not in depths or d + 1 < depths[ch]:
                    depths[ch] = d + 1
                    queue.append((ch, d + 1))

        desc_counts_df = (
            c_df.filter(pl.col("sub_iri") != pl.col("sup_iri"))
            .group_by("sup_iri")
            .len()
        )
        desc_map = {row[0]: int(row[1]) for row in desc_counts_df.iter_rows()}

        ann_map, _, dj_pairs = stream_owl_annotations_and_xrefs(path, target_iris=sig_j)
        for da, db in dj_pairs:
            if da not in seen_all_dj and (da, db) not in seen_all_dj:
                seen_all_dj.add((da, db))
                all_disjoints.append((da, db))
        for iri in sig_j:
            lbl, syns, defn = ann_map.get(iri, ("", [], ""))
            if not lbl:
                lbl = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            p1 = direct_parents_map.get(iri, [])
            p2_set: list[str] = list(p1)
            for p in p1:
                for gp in direct_parents_map.get(p, []):
                    if gp not in p2_set and len(p2_set) < 8:
                        p2_set.append(gp)
            ch1 = children_map.get(iri, [])[:6]
            term_metadata[iri] = TermMetadata(
                iri=iri,
                ont_id=ont_id,
                label=lbl,
                synonyms=syns,
                definition=defn,
                direct_parents=p1,
                parents_2hop=p2_set[:8],
                children_1hop=ch1,
                depth=depths.get(iri, 1),
                descendant_count=desc_map.get(iri, 0),
            )

    # Also audit the unmapped base ontology union H(empty) = U_j O_j (with ZERO crosswalk mappings)
    # so embedded foreign-namespace axioms inside OBO releases (e.g. FLOPO/MONDO) are isolated in the
    # native baseline, ensuring Unsat_Delta and ConsViol strictly measure alignment-induced defects!
    conj_rules: list[tuple[str, tuple[str, ...]]] = []
    if len(ontology_paths) > 1:
        base_union_elk = run_elk_closure(
            list(ontology_paths.values()),
            work_dir=work_dir / "native",
            tag="native_base_union",
            heap_flag=heap_flag,
            sig_substrings=sig_subs or None,
            emit_conj_rules=True,
        )
        conj_rules = list(base_union_elk.conj_rules)
        total_wall += base_union_elk.wall_seconds
        peak_rss = max(peak_rss, base_union_elk.peak_rss_mb)

        all_sig_set = set(class_to_ont.keys())
        native_unsat |= (base_union_elk.unsat_classes & all_sig_set)

        for da, db in base_union_elk.disjoint_pairs:
            pk = (min(da, db), max(da, db))
            if pk not in seen_all_dj:
                seen_all_dj.add(pk)
                all_disjoints.append(pk)

        bu_d_df = pl.read_parquet(base_union_elk.direct_parents_parquet)
        for sub_i, sup_i in bu_d_df.iter_rows():
            if sub_i != sup_i:
                bucket = full_direct_parents.setdefault(sub_i, [])
                if sup_i not in bucket:
                    bucket.append(sup_i)

        # Include any pre-existing intra-ontology subsumptions already entailed by H(empty)
        bu_c_df = pl.read_parquet(base_union_elk.closure_parquet)
        if bu_c_df.height > 0:
            sig_lookup_df = pl.DataFrame(
                {
                    "iri": [k for k in class_to_ont if k not in native_unsat],
                    "ont_id": [class_to_ont[k] for k in class_to_ont if k not in native_unsat],
                }
            )
            bu_intra = (
                bu_c_df.join(sig_lookup_df, left_on="sub_iri", right_on="iri")
                .rename({"ont_id": "o1"})
                .join(sig_lookup_df, left_on="sup_iri", right_on="iri")
                .filter(pl.col("o1") == pl.col("ont_id"))
                .select(["ont_id", "sub_iri", "sup_iri"])
            )
            if bu_intra.height > 0:
                intra_frames.append(bu_intra)

    intra_parquet = work_dir / "intra_closure.parquet"
    if intra_frames:
        pl.concat(intra_frames).unique().write_parquet(intra_parquet)
    else:
        pl.DataFrame(
            schema={"ont_id": pl.Utf8, "sub_iri": pl.Utf8, "sup_iri": pl.Utf8}
        ).write_parquet(intra_parquet)

    return NativeHubAudit(
        suite_id=suite_id,
        ontology_paths=ontology_paths,
        class_to_ont=class_to_ont,
        native_unsat_classes=native_unsat,
        intra_closure_parquet=intra_parquet,
        term_metadata=term_metadata,
        per_ont_class_counts=per_ont_counts,
        per_ont_unsat_counts=per_ont_unsat,
        wall_seconds=total_wall,
        peak_rss_mb=peak_rss,
        native_disjoints=all_disjoints,
        full_direct_parents=full_direct_parents,
        sig_substrings=sig_subs,
        conj_rules=conj_rules,
    )
