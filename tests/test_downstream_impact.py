"""Tests for downstream clinical/phenomics impact and OWL deductive subsumption evaluation."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from clasp_ont.export.downstream_impact import (
    compute_deductive_subsumption_metrics,
    compute_suite_downstream_impact,
)


def test_deductive_subsumption_strict_vs_collapsed(tmp_path: Path) -> None:
    closure_path = tmp_path / "hub.closure.parquet"
    pl.DataFrame(
        {
            "sub_iri": ["urn:a:1", "urn:b:1", "urn:b:2", "urn:c:2"],
            "sup_iri": ["urn:b:1", "urn:b:2", "urn:b:1", "urn:c:1"],
        }
    ).write_parquet(closure_path)

    # (a:1 <= b:1) is strict; (b:1 <= b:2) is symmetrically collapsed; (c:1 <= c:2) is inverted
    gold = {("urn:a:1", "urn:b:1"), ("urn:b:1", "urn:b:2"), ("urn:c:1", "urn:c:2")}
    res = compute_deductive_subsumption_metrics(closure_path, gold)
    assert res["deductive_sub_tp"] == 1
    assert res["deductive_sub_collapsed"] == 1
    assert res["deductive_sub_inverted"] == 1
    assert abs(float(res["deductive_sub_recall"]) - 1 / 3) < 1e-5


def test_phase0_toy_hub_downstream_zero_corruption() -> None:
    root = Path(__file__).resolve().parents[1]
    work_dir = root / "data" / "interim" / "phase0"
    if not (work_dir / "native" / "intra_closure.parquet").is_file():
        return
    ds = compute_suite_downstream_impact("Phase0_Toy_Hub", work_dir, root / "data" / "raw")
    assert ds["B1_Raw_Hub"]["hub_corrupted_pct"] > 0.0
    assert ds["B4_Lexical_Lattice"]["hub_corrupted_pct"] == 0.0
    assert ds["B4_Lexical_Lattice"]["chapter_cohort_rollup"]["mean_intra_fdr"] == 0.0
    assert ds["B4_Lexical_Lattice"]["deductive_sub_recall"] == 1.0
