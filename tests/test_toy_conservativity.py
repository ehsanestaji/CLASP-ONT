"""Unit & integration tests for ELK/DuckDB conservativity detection, native unsat isolation, and H1."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from clasp_ont.export.metrics_stats import run_pairwise_vs_hub_audit
from clasp_ont.ingest.preclean import run_native_preclean
from clasp_ont.ingest.sssom_io import MappingRecord, write_mappings_owl
from clasp_ont.ingest.toy_hub import build_toy_hub
from clasp_ont.symbolic.conservativity import audit_merged_hub


def _write_mini_owl(path: Path, iri: str, classes: list[str], subs: list[tuple[str, str]], disjoints: list[tuple[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?>\n')
        fh.write(
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
            '         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
            '         xmlns:owl="http://www.w3.org/2002/07/owl#">\n'
        )
        fh.write(f'  <owl:Ontology rdf:about="{xml_escape(iri)}"/>\n')
        for c in classes:
            fh.write(f'  <owl:Class rdf:about="{xml_escape(c)}"/>\n')
        for s, p in subs:
            fh.write(
                f'  <owl:Class rdf:about="{xml_escape(s)}"><rdfs:subClassOf rdf:resource="{xml_escape(p)}"/></owl:Class>\n'
            )
        for a, b in disjoints:
            fh.write(
                f'  <owl:Class rdf:about="{xml_escape(a)}"><owl:disjointWith rdf:resource="{xml_escape(b)}"/></owl:Class>\n'
            )
        fh.write("</rdf:RDF>\n")
    return path


def test_native_unsat_excluded_and_diamond_consviol_detected(tmp_path: Path) -> None:
    o1_ns = "http://example.org/o1#"
    o2_ns = "http://example.org/o2#"

    # O1 has one pre-existing native unsat class (NativeBad <= D1 and D2 where D1 disjoint D2),
    # plus satisfiable siblings SibA and SibB.
    o1 = _write_mini_owl(
        tmp_path / "o1.owl",
        "http://example.org/o1",
        [f"{o1_ns}Root", f"{o1_ns}SibA", f"{o1_ns}SibB", f"{o1_ns}D1", f"{o1_ns}D2", f"{o1_ns}NativeBad"],
        [
            (f"{o1_ns}SibA", f"{o1_ns}Root"),
            (f"{o1_ns}SibB", f"{o1_ns}Root"),
            (f"{o1_ns}NativeBad", f"{o1_ns}D1"),
            (f"{o1_ns}NativeBad", f"{o1_ns}D2"),
        ],
        [(f"{o1_ns}D1", f"{o1_ns}D2")],
    )
    o2 = _write_mini_owl(
        tmp_path / "o2.owl",
        "http://example.org/o2",
        [f"{o2_ns}BroadX"],
        [],
        [],
    )

    native = run_native_preclean("mini", {"O1": o1, "O2": o2}, tmp_path / "work")
    assert f"{o1_ns}NativeBad" in native.native_unsat_classes

    # SibA == BroadX and SibB <= BroadX induces silent intra-O1 ConsViol: SibB <= SibA
    mappings = [
        MappingRecord("m1", f"{o1_ns}SibA", "skos:exactMatch", f"{o2_ns}BroadX", 0.9),
        MappingRecord("m2", f"{o1_ns}SibB", "skos:broadMatch", f"{o2_ns}BroadX", 0.95),
    ]
    raw_bridge = write_mappings_owl(mappings, None, tmp_path / "raw_bridge.owl")
    raw_audit = audit_merged_hub(native, raw_bridge, tmp_path / "audit_raw")

    # Pre-existing NativeBad must NOT be counted as alignment-induced Unsat_Delta
    assert raw_audit.unsat_delta == []
    assert (f"{o1_ns}SibB", f"{o1_ns}SibA", "O1") in raw_audit.cons_violations

    # Downgrading m1 to downgrade_sub (SibA <= BroadX) resolves the diamond violation completely
    rep_bridge = write_mappings_owl(
        mappings, {"m1": "downgrade_sub", "m2": "keep_equiv"}, tmp_path / "rep_bridge.owl"
    )
    rep_audit = audit_merged_hub(native, rep_bridge, tmp_path / "audit_rep")
    assert rep_audit.is_certified


def test_200_class_toy_hub_compositional_breakdown_and_gt_certification(tmp_path: Path) -> None:
    bundle = build_toy_hub(tmp_path / "toy")
    native = run_native_preclean(
        "Phase0_Toy_Hub", bundle.ontology_paths, tmp_path / "work", bundle.namespace_hints
    )
    assert len(native.class_to_ont) == 200
    assert len(native.native_unsat_classes) == 0
    assert len(bundle.mappings) == 60
    assert len(bundle.trap_ids) == 15

    raw_bridge = write_mappings_owl(bundle.mappings, None, tmp_path / "raw.owl")
    raw_audit = audit_merged_hub(native, raw_bridge, tmp_path / "elk_raw")
    assert len(raw_audit.unsat_delta) >= 10
    assert len(raw_audit.cons_violations) >= 15

    # Compositional explosion check: merged hub has strictly more violations than sum of pairwise audits
    bd = run_pairwise_vs_hub_audit(native, bundle.mappings, raw_audit, tmp_path / "pw")
    assert (bd.hub_unsat_delta + bd.hub_consviol) > (bd.total_pairwise_unsat + bd.total_pairwise_consviol)

    # Ground-truth lattice repair achieves exact 0 Unsat_Delta and 0 ConsViol
    gt_bridge = write_mappings_owl(bundle.mappings, bundle.ground_truth_actions, tmp_path / "gt.owl")
    gt_audit = audit_merged_hub(native, gt_bridge, tmp_path / "elk_gt")
    assert gt_audit.unsat_delta == []
    assert gt_audit.cons_violations == []
    assert gt_audit.is_certified
