"""B2_LogMap_Repair: Classical greedy Horn-logic repair wrapper via standalone LogMap 4.0 DEBUGGER."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord, write_mappings_owl
from clasp_ont.symbolic.cegar_loop import compute_semantic_retention, load_lattice_config
from clasp_ont.symbolic.conservativity import HubAuditReport, audit_merged_hub
from clasp_ont.symbolic.reasoner_elk import resolve_java_bin, run_robot_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
LOGMAP_JAR = REPO_ROOT / "vendor" / "logmap-standalone" / "logmap-matcher-4.0.jar"


@dataclass(slots=True)
class LogMapRepairResult:
    system_id: str
    suite_id: str
    selected_actions: dict[str, str]
    final_audit: HubAuditReport
    semantic_retention: float
    wall_seconds: float


def _write_oriented_oaei_rdf(
    oriented_cells: Sequence[tuple[str, str, str, float]],
    onto1_iri: str,
    onto2_iri: str,
    out_rdf: Path,
) -> None:
    """Write OAEI Alignment RDF with entity1 strictly in onto1 and entity2 strictly in onto2."""
    out_rdf.parent.mkdir(parents=True, exist_ok=True)
    with out_rdf.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="utf-8"?>\n')
        fh.write(
            '<rdf:RDF xmlns="http://knowledgeweb.semanticweb.org/heterogeneity/alignment"\n'
            '         xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
            '         xmlns:xsd="http://www.w3.org/2001/XMLSchema#">\n'
            "  <Alignment>\n"
            "    <xml>yes</xml>\n"
            "    <level>0</level>\n"
            "    <type>??</type>\n"
            f"    <onto1>{xml_escape(onto1_iri)}</onto1>\n"
            f"    <onto2>{xml_escape(onto2_iri)}</onto2>\n"
        )
        for e1, e2, rel, conf in oriented_cells:
            fh.write(
                "    <map>\n"
                "      <Cell>\n"
                f'        <entity1 rdf:resource="{xml_escape(e1)}"/>\n'
                f'        <entity2 rdf:resource="{xml_escape(e2)}"/>\n'
                f'        <measure rdf:datatype="http://www.w3.org/2001/XMLSchema#float">{conf:.4f}</measure>\n'
                f"        <relation>{rel}</relation>\n"
                "      </Cell>\n"
                "    </map>\n"
            )
        fh.write("  </Alignment>\n</rdf:RDF>\n")


def _parse_logmap_txt(out_dir: Path) -> dict[tuple[str, str], str]:
    """Parse mappings_repaired_with_LogMap.txt -> {(e1, e2): '=' | '<' | '>'}."""
    results: dict[tuple[str, str], str] = {}
    txt_file = out_dir / "mappings_repaired_with_LogMap.txt"
    if txt_file.is_file():
        for line in txt_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0].startswith("http") and parts[1].startswith("http"):
                results[(parts[0], parts[1])] = parts[2]
        return results

    for tf in sorted(out_dir.glob("*.tsv")):
        for line in tf.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 2 and parts[0].startswith("http") and parts[1].startswith("http"):
                results[(parts[0], parts[1])] = "="
    return results


def _run_single_logmap_debugger(
    java_bin: Path,
    ont1_path: Path,
    ont2_path: Path,
    ont1_iris: set[str],
    subset_mappings: Sequence[MappingRecord],
    current_actions: dict[str, str],
    heap_flag: str = "-Xmx4G",
) -> dict[str, str]:
    """Run LogMap DEBUGGER on bipartition (ont1, ont2) with properly oriented OAEI cells."""
    if not subset_mappings:
        return {}

    oriented_cells: list[tuple[str, str, str, float]] = []
    swap_flags: dict[str, bool] = {}

    for m in subset_mappings:
        act = current_actions.get(m.mapping_id, "keep_equiv")
        if act == "negate_drop":
            continue
        swapped = m.subject_id not in ont1_iris
        swap_flags[m.mapping_id] = swapped
        e1, e2 = (m.object_id, m.subject_id) if swapped else (m.subject_id, m.object_id)

        # Determine current relation symbol in (e1, e2) orientation
        eff_pred = m.predicate_id
        if act == "downgrade_sub":
            eff_pred = "skos:broadMatch"
        elif act == "downgrade_sup":
            eff_pred = "skos:narrowMatch"

        if eff_pred == "skos:exactMatch":
            rel = "="
        elif eff_pred == "skos:broadMatch":
            # s <= o; if not swapped e1 <= e2 (&lt;), if swapped e2 <= e1 (&gt;)
            rel = "&gt;" if swapped else "&lt;"
        else:
            rel = "&lt;" if swapped else "&gt;"
        oriented_cells.append((e1, e2, rel, m.confidence))

    if not oriented_cells:
        return {}

    with tempfile.TemporaryDirectory(prefix="clasp_logmap_") as tmp:
        tmp_dir = Path(tmp)
        in_rdf = tmp_dir / "input_mappings.rdf"
        out_dir = tmp_dir / "repaired_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_oriented_oaei_rdf(
            oriented_cells,
            onto1_iri=ont1_path.resolve().as_uri(),
            onto2_iri=ont2_path.resolve().as_uri(),
            out_rdf=in_rdf,
        )

        env = dict(os.environ)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY"):
            env.pop(k, None)
        env["JAVA_TOOL_OPTIONS"] = heap_flag

        cmd = [
            str(java_bin),
            heap_flag,
            "--add-opens",
            "java.base/java.lang=ALL-UNNAMED",
            "--add-opens",
            "java.base/java.util=ALL-UNNAMED",
            "-jar",
            str(LOGMAP_JAR),
            "DEBUGGER",
            f"file:{ont1_path.resolve()}",
            f"file:{ont2_path.resolve()}",
            "RDF",
            str(in_rdf.resolve()),
            str(out_dir.resolve()) + "/",
            "false",
            "false",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"LogMap DEBUGGER failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}")

        repaired_rel_map = _parse_logmap_txt(out_dir)
        updated: dict[str, str] = {}
        for m in subset_mappings:
            if current_actions.get(m.mapping_id) == "negate_drop":
                updated[m.mapping_id] = "negate_drop"
                continue
            swapped = swap_flags[m.mapping_id]
            e1, e2 = (m.object_id, m.subject_id) if swapped else (m.subject_id, m.object_id)
            rel_out = repaired_rel_map.get((e1, e2))
            if rel_out is None:
                updated[m.mapping_id] = "negate_drop"
            elif rel_out == "=":
                updated[m.mapping_id] = "keep_equiv"
            elif rel_out == "<":
                # e1 <= e2
                if m.predicate_id in {"skos:broadMatch", "skos:narrowMatch"}:
                    updated[m.mapping_id] = "keep_equiv"
                else:
                    updated[m.mapping_id] = "downgrade_sup" if swapped else "downgrade_sub"
            elif rel_out == ">":
                # e2 <= e1
                if m.predicate_id in {"skos:broadMatch", "skos:narrowMatch"}:
                    updated[m.mapping_id] = "keep_equiv"
                else:
                    updated[m.mapping_id] = "downgrade_sub" if swapped else "downgrade_sup"
            else:
                updated[m.mapping_id] = "keep_equiv"
        return updated


def run_logmap_repair(
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    work_dir: Path,
    heap_flag: str = "-Xmx4G",
) -> LogMapRepairResult:
    """Execute LogMap standalone DEBUGGER repair across 2-ontology or k-ontology hubs."""
    if not LOGMAP_JAR.is_file():
        raise FileNotFoundError(
            f"Missing {LOGMAP_JAR}. Run `uv run python vendor/fetch_jars.py` first."
        )
    t0 = time.perf_counter()
    java_bin = resolve_java_bin()
    work_dir.mkdir(parents=True, exist_ok=True)

    ont_items = list(native_audit.ontology_paths.items())
    selected_actions: dict[str, str] = {m.mapping_id: "keep_equiv" for m in mappings}

    if len(ont_items) == 2:
        o1_id, o1_p = ont_items[0]
        _o2_id, o2_p = ont_items[1]
        o1_iris = {iri for iri, oid in native_audit.class_to_ont.items() if oid == o1_id}
        selected_actions = _run_single_logmap_debugger(
            java_bin, o1_p, o2_p, o1_iris, mappings, selected_actions, heap_flag=heap_flag
        )
    else:
        for idx, (ont_id, ont_path) in enumerate(ont_items):
            other_onts = [p for j, (_oid, p) in enumerate(ont_items) if j != idx]
            o1_iris = {iri for iri, oid in native_audit.class_to_ont.items() if oid == ont_id}
            internal_records = [
                m
                for m in mappings
                if selected_actions.get(m.mapping_id) != "negate_drop"
                and m.subject_id not in o1_iris
                and m.object_id not in o1_iris
            ]
            cross_records = [
                m
                for m in mappings
                if selected_actions.get(m.mapping_id) != "negate_drop"
                and (m.subject_id in o1_iris or m.object_id in o1_iris)
            ]
            if not cross_records:
                continue

            internal_bridge = work_dir / f"logmap_rest_bridge_{idx}.owl"
            write_mappings_owl(internal_records, selected_actions, internal_bridge)
            merged_rest = work_dir / f"logmap_rest_merged_{idx}.owl"
            merge_args = ["merge"]
            for op in other_onts:
                merge_args.extend(["--input", str(op)])
            merge_args.extend(["--input", str(internal_bridge), "--output", str(merged_rest)])
            run_robot_cli(merge_args, heap_flag=heap_flag)

            upd = _run_single_logmap_debugger(
                java_bin, ont_path, merged_rest, o1_iris, cross_records, selected_actions, heap_flag=heap_flag
            )
            selected_actions.update(upd)

    repaired_bridge = write_mappings_owl(
        mappings,
        selected_actions,
        work_dir / "B2_LogMap_Repair_bridge.owl",
    )
    final_audit = audit_merged_hub(
        native_audit=native_audit,
        bridge_owl_path=repaired_bridge,
        work_dir=work_dir / "elk_B2_LogMap",
        tag="B2_LogMap_Repair",
        heap_flag=heap_flag,
    )
    elapsed = time.perf_counter() - t0
    cfg = load_lattice_config()
    rho_map = {k: float(v["rho"]) for k, v in cfg["actions"].items()}
    retention = compute_semantic_retention(mappings, selected_actions, rho_map)

    return LogMapRepairResult(
        system_id="B2_LogMap_Repair",
        suite_id=native_audit.suite_id,
        selected_actions=selected_actions,
        final_audit=final_audit,
        semantic_retention=retention,
        wall_seconds=elapsed,
    )
