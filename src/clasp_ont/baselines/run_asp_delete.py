"""B3_ASP_Binary_Delete: Exact Clingo minimal-weight hitting-set restricted to {keep_equiv, negate_drop}."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord
from clasp_ont.symbolic.cegar_loop import CertifiedRepairResult, run_clingo_cegar
from clasp_ont.symbolic.conflict_cores import ConflictCore


def run_binary_asp_delete(
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    initial_cores: Sequence[ConflictCore],
    work_dir: Path,
    heap_flag: str = "-Xmx4G",
) -> CertifiedRepairResult:
    """Run exact Clingo CEGAR restricted strictly to binary keep-or-drop actions."""
    return run_clingo_cegar(
        system_id="B3_ASP_Binary_Delete",
        native_audit=native_audit,
        mappings=mappings,
        initial_cores=initial_cores,
        oracle_decisions={},
        work_dir=work_dir,
        binary_only=True,
        heap_flag=heap_flag,
    )
