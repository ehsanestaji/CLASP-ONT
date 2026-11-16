"""DuckDB exact set-difference detector for alignment-induced conservativity violations.

Implements Section 2.1 & Section 5 Stage 1:
  Unsat_Delta(H(M)) = { C in U_j Sig(O_j) | H(M) |= C <= bot and O_j not|= C <= bot }
  ConsViol(H(M))    = U_j { (A, B) in Sig(O_j) x Sig(O_j) | A != B, H(M) |= A <= B, O_j not|= A <= B }
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.symbolic.reasoner_elk import ElkClosureResult, run_elk_closure


@dataclass(slots=True)
class HubAuditReport:
    unsat_delta: list[str]
    cons_violations: list[tuple[str, str, str]]  # (sub_iri, sup_iri, ont_id)
    novel_cross_entailments: int
    hub_closure_parquet: Path
    wall_seconds: float
    peak_rss_mb: float

    @property
    def is_certified(self) -> bool:
        return len(self.unsat_delta) == 0 and len(self.cons_violations) == 0


def compute_conservativity_diff(
    native_audit: NativeHubAudit,
    hub_elk: ElkClosureResult,
    memory_limit: str = "2GB",
) -> HubAuditReport:
    """Compute Unsat_Delta and ConsViol via exact DuckDB SQL ANTI JOIN."""
    all_sig = set(native_audit.class_to_ont.keys())
    unsat_delta = sorted(
        (hub_elk.unsat_classes & all_sig) - native_audit.native_unsat_classes
    )

    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET memory_limit = '{memory_limit}';")
        con.execute("SET threads TO 4;")

        sig_df = pl.DataFrame(
            {
                "iri": list(native_audit.class_to_ont.keys()),
                "ont_id": list(native_audit.class_to_ont.values()),
            }
        )
        # Exclude native unsat and newly unsat classes from ConsViol (since ElkClosureRunner
        # already excludes hub_elk.unsat_classes, also filter native_unsat_classes for purity).
        excluded_unsat = native_audit.native_unsat_classes | hub_elk.unsat_classes
        if excluded_unsat:
            sig_df = sig_df.filter(~pl.col("iri").is_in(list(excluded_unsat)))

        con.register("sig_map", sig_df)
        intra_p = str(native_audit.intra_closure_parquet).replace("'", "''")
        hub_p = str(hub_elk.closure_parquet).replace("'", "''")

        con.execute(
            f"""
            CREATE TEMP VIEW intra_closure AS
            SELECT ont_id, sub_iri AS sub, sup_iri AS sup
            FROM read_parquet('{intra_p}');
            """
        )
        con.execute(
            f"""
            CREATE TEMP VIEW hub_closure AS
            SELECT s1.ont_id AS ont_id, hc.sub_iri AS sub, hc.sup_iri AS sup
            FROM read_parquet('{hub_p}') hc
            JOIN sig_map s1 ON hc.sub_iri = s1.iri
            JOIN sig_map s2 ON hc.sup_iri = s2.iri
            WHERE s1.ont_id = s2.ont_id;
            """
        )

        # Exact SQL ANTI JOIN mandated in Section 5 Stage 1
        viol_rows = con.execute(
            """
            SELECT h.sub, h.sup, h.ont_id
            FROM hub_closure h
            ANTI JOIN intra_closure i USING (ont_id, sub, sup)
            WHERE h.sub != h.sup
            ORDER BY h.ont_id, h.sub, h.sup;
            """
        ).fetchall()

        cross_count_row = con.execute(
            f"""
            SELECT COUNT(*)
            FROM read_parquet('{hub_p}') hc
            JOIN sig_map s1 ON hc.sub_iri = s1.iri
            JOIN sig_map s2 ON hc.sup_iri = s2.iri
            WHERE s1.ont_id != s2.ont_id;
            """
        ).fetchone()
        novel_cross = int(cross_count_row[0]) if cross_count_row else 0
    finally:
        con.close()

    cons_violations = [(str(r[0]), str(r[1]), str(r[2])) for r in viol_rows]
    return HubAuditReport(
        unsat_delta=unsat_delta,
        cons_violations=cons_violations,
        novel_cross_entailments=novel_cross,
        hub_closure_parquet=hub_elk.closure_parquet,
        wall_seconds=hub_elk.wall_seconds,
        peak_rss_mb=hub_elk.peak_rss_mb,
    )


def audit_merged_hub(
    native_audit: NativeHubAudit,
    bridge_owl_path: Path,
    work_dir: Path,
    tag: str = "merged_hub",
    heap_flag: str = "-Xmx4G",
    duckdb_memory_limit: str = "2GB",
) -> HubAuditReport:
    """Run ELK closure on (O_1..O_k U bridge_owl_path) and compute exact Unsat_Delta + ConsViol."""
    ont_paths = [*native_audit.ontology_paths.values(), bridge_owl_path]
    hub_elk = run_elk_closure(ont_paths, work_dir=work_dir, tag=tag, heap_flag=heap_flag)
    return compute_conservativity_diff(
        native_audit=native_audit,
        hub_elk=hub_elk,
        memory_limit=duckdb_memory_limit,
    )
