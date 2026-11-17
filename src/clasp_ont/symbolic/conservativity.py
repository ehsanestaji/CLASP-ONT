"""DuckDB exact set-difference detector for alignment-induced conservativity violations.

Materializes `sig_tbl` and `hub_tagged` explicitly so DuckDB probes hash tables in O(|hc|)
without ever reordering into an O(|Sig|^2) self-join on ont_id.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.symbolic.reasoner_elk import ElkClosureResult, run_elk_closure


@dataclass(slots=True)
class HubAuditReport:
    unsat_delta: list[str]
    cons_violations: list[tuple[str, str, str]]
    cons_viol_count: int
    novel_cross_entailments: int
    hub_closure_parquet: Path
    wall_seconds: float
    peak_rss_mb: float

    @property
    def is_certified(self) -> bool:
        return len(self.unsat_delta) == 0 and self.cons_viol_count == 0


def compute_conservativity_diff(
    native_audit: NativeHubAudit,
    hub_elk: ElkClosureResult,
    memory_limit: str = "3GB",
    max_sample_rows: int = 250000,
) -> HubAuditReport:
    """Compute exact Unsat_Delta and ConsViol count + sample in O(|closure|) via DuckDB ANTI JOIN."""
    all_sig = set(native_audit.class_to_ont.keys())
    unsat_delta = sorted(
        (hub_elk.unsat_classes & all_sig) - native_audit.native_unsat_classes
    )

    tmp_db = hub_elk.closure_parquet.with_suffix(".audit.duckdb")
    tmp_spill = hub_elk.closure_parquet.parent / f"{hub_elk.closure_parquet.stem}_duckspill"
    tmp_db.unlink(missing_ok=True)
    shutil.rmtree(tmp_spill, ignore_errors=True)
    tmp_spill.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(tmp_db))
    try:
        spill_esc = str(tmp_spill).replace("'", "''")
        con.execute(f"SET temp_directory = '{spill_esc}';")
        con.execute(f"SET memory_limit = '{memory_limit}';")
        con.execute("SET preserve_insertion_order = false;")
        con.execute("SET threads TO 4;")

        sig_df = pl.DataFrame(
            {
                "iri": list(native_audit.class_to_ont.keys()),
                "ont_id": list(native_audit.class_to_ont.values()),
            }
        )
        excluded_unsat = native_audit.native_unsat_classes | hub_elk.unsat_classes
        if excluded_unsat:
            sig_df = sig_df.filter(~pl.col("iri").is_in(list(excluded_unsat)))

        con.register("sig_arrow", sig_df)
        con.execute("CREATE TEMP TABLE sig_tbl AS SELECT iri, ont_id FROM sig_arrow;")

        intra_p = str(native_audit.intra_closure_parquet).replace("'", "''")
        hub_p = str(hub_elk.closure_parquet).replace("'", "''")

        # Two independent hash probes on sub_iri and sup_iri (zero predicate between s1 and s2!)
        con.execute(
            f"""
            CREATE TEMP TABLE hub_tagged AS
            SELECT hc.sub_iri AS sub, hc.sup_iri AS sup, s1.ont_id AS o1, s2.ont_id AS o2
            FROM read_parquet('{hub_p}') hc
            JOIN sig_tbl s1 ON hc.sub_iri = s1.iri
            JOIN sig_tbl s2 ON hc.sup_iri = s2.iri;
            """
        )

        con.execute(
            """
            CREATE TEMP VIEW hub_closure AS
            SELECT o1 AS ont_id, sub, sup
            FROM hub_tagged
            WHERE o1 = o2;
            """
        )
        con.execute(
            f"""
            CREATE TEMP VIEW intra_closure AS
            SELECT ont_id, sub_iri AS sub, sup_iri AS sup
            FROM read_parquet('{intra_p}');
            """
        )

        cnt_row = con.execute(
            """
            SELECT COUNT(*)
            FROM hub_closure h
            ANTI JOIN intra_closure i USING (ont_id, sub, sup)
            WHERE h.sub != h.sup;
            """
        ).fetchone()
        exact_cons_count = int(cnt_row[0]) if cnt_row else 0

        if exact_cons_count == 0:
            viol_rows = []
        elif exact_cons_count <= max_sample_rows:
            viol_rows = con.execute(
                """
                SELECT h.sub, h.sup, h.ont_id
                FROM hub_closure h
                ANTI JOIN intra_closure i USING (ont_id, sub, sup)
                WHERE h.sub != h.sup
                ORDER BY h.ont_id, h.sub, h.sup;
                """
            ).fetchall()
        else:
            viol_rows = con.execute(
                f"""
                SELECT h.sub, h.sup, h.ont_id
                FROM hub_closure h
                ANTI JOIN intra_closure i USING (ont_id, sub, sup)
                WHERE h.sub != h.sup
                LIMIT {int(max_sample_rows)};
                """
            ).fetchall()

        cross_count_row = con.execute(
            "SELECT COUNT(*) FROM hub_tagged WHERE o1 != o2;"
        ).fetchone()
        novel_cross = int(cross_count_row[0]) if cross_count_row else 0
    finally:
        con.close()
        tmp_db.unlink(missing_ok=True)
        Path(f"{tmp_db}.wal").unlink(missing_ok=True)
        shutil.rmtree(tmp_spill, ignore_errors=True)

    cons_violations = [(str(r[0]), str(r[1]), str(r[2])) for r in viol_rows]
    return HubAuditReport(
        unsat_delta=unsat_delta,
        cons_violations=cons_violations,
        cons_viol_count=exact_cons_count,
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
    duckdb_memory_limit: str = "3GB",
) -> HubAuditReport:
    """Run ELK closure on (O_1..O_k U bridge_owl_path) and compute exact Unsat_Delta + ConsViol."""
    ont_paths = [*native_audit.ontology_paths.values(), bridge_owl_path]
    hub_elk = run_elk_closure(
        ont_paths,
        work_dir=work_dir,
        tag=tag,
        heap_flag=heap_flag,
        sig_substrings=native_audit.sig_substrings or None,
    )
    return compute_conservativity_diff(
        native_audit=native_audit,
        hub_elk=hub_elk,
        memory_limit=duckdb_memory_limit,
    )
