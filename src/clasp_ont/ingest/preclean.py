"""Stage 1 native single-ontology baseline audit, CURIE/namespace normalization, and metadata indexing."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import rdflib
from rdflib.namespace import OWL, RDF, RDFS

from clasp_ont.symbolic.reasoner_elk import run_elk_closure

IAO_DEF = rdflib.URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
EXACT_SYN = rdflib.URIRef("http://www.geneontology.org/formats/oboInOwl#hasExactSynonym")
SKOS_ALT = rdflib.URIRef("http://www.w3.org/2004/02/skos/core#altLabel")
SKOS_DEF = rdflib.URIRef("http://www.w3.org/2004/02/skos/core#definition")
NCIT_DEF = rdflib.URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#P97")
NCIT_SYN = rdflib.URIRef("http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#P90")


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


def _matches_namespace(iri: str, ont_id: str, ns_hint: str | None = None) -> bool:
    if ns_hint and iri.startswith(ns_hint):
        return True
    up = ont_id.upper().split("_")[0]
    if f"/obo/{up}_" in iri:
        return True
    if up == "NCIT" and "Thesaurus.owl#" in iri:
        return True
    if up == "ORDO" and ("Orphanet_" in iri or "/ORDO/" in iri):
        return True
    if up == "FMA" and "/fma/" in iri.lower():
        return True
    return False


def _extract_annotations_fast(owl_path: Path, target_iris: set[str]) -> dict[str, tuple[str, list[str], str]]:
    """Extract rdfs:label, exact synonyms, and text definitions for target_iris."""
    out: dict[str, tuple[str, list[str], str]] = {}
    # Use py_horned_owl first for speed on large RDF/XML OWL files; fallback to rdflib if needed
    try:
        import py_horned_owl

        onto = py_horned_owl.open_ontology(str(owl_path))
        labels: dict[str, str] = {}
        syns: dict[str, list[str]] = {}
        defs: dict[str, str] = {}
        for comp in onto.get_axioms():
            ax = getattr(comp, "component", comp)
            cls_name = type(ax).__name__
            if cls_name == "AnnotationAssertion":
                subj = str(getattr(ax, "subject", ""))
                if subj not in target_iris:
                    continue
                ann = getattr(ax, "ann", None)
                if ann is None:
                    continue
                prop_iri = str(getattr(getattr(ann, "ap", None), "0", getattr(ann, "ap", "")))
                val_obj = getattr(ann, "av", None)
                lit = getattr(val_obj, "literal", None) if val_obj is not None else None
                text = str(lit) if lit is not None else str(val_obj or "")
                text = text.strip().strip('"')
                if not text:
                    continue
                if "rdf-schema#label" in prop_iri:
                    labels[subj] = text
                elif any(k in prop_iri for k in ("IAO_0000115", "core#definition", "Thesaurus.owl#P97")):
                    defs[subj] = text[:450]
                elif any(k in prop_iri for k in ("hasExactSynonym", "core#altLabel", "Thesaurus.owl#P90")):
                    bucket = syns.setdefault(subj, [])
                    if text not in bucket and len(bucket) < 6:
                        bucket.append(text)
        if labels or defs:
            for iri in target_iris:
                out[iri] = (labels.get(iri, ""), syns.get(iri, []), defs.get(iri, ""))
            return out
    except Exception:
        pass

    g = rdflib.Graph()
    g.parse(str(owl_path))
    for iri in target_iris:
        ref = rdflib.URIRef(iri)
        lbl = ""
        for o in g.objects(ref, RDFS.label):
            lbl = str(o).strip()
            if lbl:
                break
        defn = ""
        for p in (IAO_DEF, SKOS_DEF, NCIT_DEF, RDFS.comment):
            for o in g.objects(ref, p):
                defn = str(o).strip()[:450]
                if defn:
                    break
            if defn:
                break
        syn_list: list[str] = []
        for p in (EXACT_SYN, SKOS_ALT, NCIT_SYN):
            for o in g.objects(ref, p):
                s = str(o).strip()
                if s and s != lbl and s not in syn_list and len(syn_list) < 6:
                    syn_list.append(s)
        out[iri] = (lbl, syn_list, defn)
    return out


def _extract_declared_classes(owl_path: Path) -> set[str]:
    try:
        import py_horned_owl

        onto = py_horned_owl.open_ontology(str(owl_path))
        classes = {
            str(c)
            for c in onto.get_classes()
            if str(c) not in {"http://www.w3.org/2002/07/owl#Thing", "http://www.w3.org/2002/07/owl#Nothing"}
        }
        if classes:
            return classes
    except Exception:
        pass

    g = rdflib.Graph()
    g.parse(str(owl_path))
    return {
        str(s)
        for s in g.subjects(RDF.type, OWL.Class)
        if isinstance(s, rdflib.URIRef)
        and str(s) not in {"http://www.w3.org/2002/07/owl#Thing", "http://www.w3.org/2002/07/owl#Nothing"}
    }


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

    raw_classes_by_ont: dict[str, set[str]] = {}
    elk_by_ont: dict[str, object] = {}
    total_wall = 0.0
    peak_rss = 0.0

    for ont_id, path in ontology_paths.items():
        elk_res = run_elk_closure([path], work_dir=work_dir / "native", tag=f"native_{ont_id}", heap_flag=heap_flag)
        elk_by_ont[ont_id] = elk_res
        total_wall += elk_res.wall_seconds
        peak_rss = max(peak_rss, elk_res.peak_rss_mb)

        closure_df = pl.read_parquet(elk_res.closure_parquet)
        seen = set(closure_df["sub_iri"].to_list()) | set(closure_df["sup_iri"].to_list()) | set(elk_res.unsat_classes)
        if not seen:
            seen = _extract_declared_classes(path)
        raw_classes_by_ont[ont_id] = seen

    # Enforce pairwise-disjoint Sig(O_j): primary namespace match wins; ties broken by first ontology only if unique
    iri_appearances: dict[str, list[str]] = {}
    for ont_id, cls_set in raw_classes_by_ont.items():
        for iri in cls_set:
            iri_appearances.setdefault(iri, []).append(ont_id)

    class_to_ont: dict[str, str] = {}
    for iri, onts in iri_appearances.items():
        if len(onts) == 1:
            # Check if it's an imported foreign OBO term when ns_hints or standard prefixes exist
            owner = onts[0]
            class_to_ont[iri] = owner
        else:
            matched = [o for o in onts if _matches_namespace(iri, o, ns_hints.get(o))]
            if len(matched) == 1:
                class_to_ont[iri] = matched[0]
            # Shared upper-level BFO/RO imported classes appearing in multiple ontologies without a unique owner
            # are intentionally excluded from Sig(O_j) so Sig(O_1)..Sig(O_k) remain strictly pairwise disjoint.

    native_unsat: set[str] = set()
    per_ont_counts: dict[str, int] = {o: 0 for o in ontology_paths}
    per_ont_unsat: dict[str, int] = {o: 0 for o in ontology_paths}
    for _iri, o in class_to_ont.items():
        per_ont_counts[o] += 1

    intra_frames: list[pl.DataFrame] = []
    term_metadata: dict[str, TermMetadata] = {}

    for ont_id, path in ontology_paths.items():
        elk_res = elk_by_ont[ont_id]
        sig_j = {iri for iri, o in class_to_ont.items() if o == ont_id}
        unsat_j = elk_res.unsat_classes & sig_j
        native_unsat |= unsat_j
        per_ont_unsat[ont_id] = len(unsat_j)

        c_df = pl.read_parquet(elk_res.closure_parquet)
        sig_list = list(sig_j - unsat_j)
        c_df = c_df.filter(pl.col("sub_iri").is_in(sig_list) & pl.col("sup_iri").is_in(sig_list))
        c_df = c_df.with_columns(pl.lit(ont_id).alias("ont_id")).select(["ont_id", "sub_iri", "sup_iri"])
        intra_frames.append(c_df)

        # Build hierarchy metadata (direct parents, 2-hop parents, 1-hop children, depth, descendants)
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

        # Compute tree depth via BFS from roots (classes in sig_j with no intra-ontology direct parent)
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

        # Descendant counts from transitive closure
        desc_counts_df = (
            c_df.filter(pl.col("sub_iri") != pl.col("sup_iri"))
            .group_by("sup_iri")
            .len()
        )
        desc_map = {row[0]: int(row[1]) for row in desc_counts_df.iter_rows()}

        ann_map = _extract_annotations_fast(path, sig_j)
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
    )
