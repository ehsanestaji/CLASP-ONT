"""Wrapper for headless ROBOT/ELK (via vendor/robot.jar + ElkClosureRunner) and py-horned-owl.

Enforces the strict -Xmx4G JVM memory cap and tracks wall-clock runtime and RSS memory telemetry.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import psutil

REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_DIR = REPO_ROOT / "vendor"
ROBOT_JAR = VENDOR_DIR / "robot.jar"
CLASSES_DIR = VENDOR_DIR / "classes"


@dataclass(slots=True)
class ElkClosureResult:
    unsat_classes: set[str]
    closure_parquet: Path
    direct_parents_parquet: Path
    disjoint_pairs: list[tuple[str, str]]
    num_closure_pairs: int
    wall_seconds: float
    peak_rss_mb: float
    conj_rules: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


def resolve_java_bin() -> Path:
    """Discover working OpenJDK 17+ java executable."""
    import sys

    sys.path.insert(0, str(VENDOR_DIR))
    from fetch_jars import compile_elk_helper, find_jdk_tool

    java_bin = find_jdk_tool("java")
    helper_src = VENDOR_DIR / "ElkClosureRunner.java"
    if helper_src.is_file() and not (CLASSES_DIR / "ElkClosureRunner.class").is_file():
        compile_elk_helper(helper_src)
    return java_bin


def _jvm_env(heap_flag: str = "-Xmx4G", sig_substrings: Sequence[str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    # Strip any third-party API keys unconditionally and cap JVM heap at 4GB
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        env.pop(k, None)
    env["JAVA_TOOL_OPTIONS"] = heap_flag
    if sig_substrings:
        env["CLASP_SIG_SUBSTRINGS"] = ",".join(s for s in sig_substrings if s)
    else:
        env.pop("CLASP_SIG_SUBSTRINGS", None)
    return env


def run_elk_closure(
    ontology_paths: Sequence[Path],
    work_dir: Path,
    tag: str = "hub",
    heap_flag: str = "-Xmx4G",
    sig_substrings: Sequence[str] | None = None,
    emit_conj_rules: bool = False,
) -> ElkClosureResult:
    """Compute exact OWL 2 EL unsatisfiable classes and named-class subsumption closure."""
    if not ROBOT_JAR.is_file():
        raise FileNotFoundError(
            f"Missing {ROBOT_JAR}. Run `uv run python vendor/fetch_jars.py` first."
        )
    java_bin = resolve_java_bin()
    work_dir.mkdir(parents=True, exist_ok=True)

    unsat_tsv = work_dir / f"{tag}.unsat.tsv"
    closure_tsv = work_dir / f"{tag}.closure.tsv"
    direct_tsv = work_dir / f"{tag}.direct.tsv"
    disjoint_tsv = work_dir / f"{tag}.disjoint.tsv"
    conj_tsv = work_dir / f"{tag}.conj.tsv"
    closure_parquet = work_dir / f"{tag}.closure.parquet"
    direct_parquet = work_dir / f"{tag}.direct.parquet"

    classpath = f"{CLASSES_DIR}{os.pathsep}{ROBOT_JAR}"
    cmd = [
        str(java_bin),
        heap_flag,
        "-cp",
        classpath,
        "ElkClosureRunner",
        "closure",
        str(unsat_tsv),
        str(closure_tsv),
        str(direct_tsv),
        str(disjoint_tsv),
        *[str(p) for p in ontology_paths],
    ]

    env = _jvm_env(heap_flag, sig_substrings=sig_substrings)
    if emit_conj_rules:
        env["CLASP_CONJ_OUT"] = str(conj_tsv)
    else:
        env.pop("CLASP_CONJ_OUT", None)

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    peak_rss = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        while proc.poll() is None:
            try:
                rss = ps_proc.memory_info().rss / (1024 * 1024)
                if rss > peak_rss:
                    peak_rss = rss
            except psutil.Error:
                pass
            time.sleep(0.02)
    except psutil.Error:
        pass
    stdout, stderr = proc.communicate()
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(
            f"ElkClosureRunner closure failed (rc={proc.returncode}):\n{stderr}\n{stdout}"
        )

    self_rss = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    total_peak_mb = max(peak_rss + self_rss, self_rss)

    unsat_df = pl.read_csv(unsat_tsv, separator="\t", schema_overrides={"class_iri": pl.Utf8})
    unsat_set = set(unsat_df["class_iri"].to_list()) if unsat_df.height > 0 else set()

    dj_df = pl.read_csv(
        disjoint_tsv, separator="\t", schema_overrides={"cls_a": pl.Utf8, "cls_b": pl.Utf8}, quote_char=None
    )
    dj_pairs = [(r[0], r[1]) for r in dj_df.iter_rows()] if dj_df.height > 0 else []

    # ElkClosureRunner emits exact unique (sub, sup) pairs per class; stream directly to Parquet
    pl.scan_csv(
        closure_tsv,
        separator="\t",
        schema_overrides={"sub_iri": pl.Utf8, "sup_iri": pl.Utf8},
        quote_char=None,
    ).sink_parquet(closure_parquet)

    pl.scan_csv(
        direct_tsv,
        separator="\t",
        schema_overrides={"sub_iri": pl.Utf8, "direct_sup_iri": pl.Utf8},
        quote_char=None,
    ).sink_parquet(direct_parquet)

    num_pairs = (
        pl.scan_parquet(closure_parquet).select(pl.len()).collect().item()
    )

    conj_rules: list[tuple[str, tuple[str, ...]]] = []
    if emit_conj_rules and conj_tsv.is_file():
        conj_df = pl.read_csv(
            conj_tsv,
            separator="\t",
            schema_overrides={"target_iri": pl.Utf8, "conjuncts": pl.Utf8},
            quote_char=None,
        )
        for tgt_i, conjs_s in conj_df.iter_rows():
            if tgt_i and conjs_s:
                uniq_c = tuple(sorted({c for c in conjs_s.split(";") if c}))
                if len(uniq_c) >= 2:
                    conj_rules.append((tgt_i, uniq_c))

    unsat_tsv.unlink(missing_ok=True)
    closure_tsv.unlink(missing_ok=True)
    direct_tsv.unlink(missing_ok=True)
    disjoint_tsv.unlink(missing_ok=True)
    conj_tsv.unlink(missing_ok=True)

    return ElkClosureResult(
        unsat_classes=unsat_set,
        closure_parquet=closure_parquet,
        direct_parents_parquet=direct_parquet,
        disjoint_pairs=dj_pairs,
        num_closure_pairs=int(num_pairs),
        wall_seconds=elapsed,
        peak_rss_mb=total_peak_mb,
        conj_rules=conj_rules,
    )


def run_elk_quickxplain_mus(
    base_ontology_paths: Sequence[Path],
    active_axioms: Sequence[tuple[str, str, str, str, str]],
    targets: Sequence[tuple[str, str, str]],
    heap_flag: str = "-Xmx4G",
) -> list[tuple[int, str, str, str, list[tuple[str, str]]]]:
    """Run incremental QuickXplain MUS inside ElkClosureRunner to extract minimal conflict cores.

    Args:
        base_ontology_paths: Native OWL ontology files O_1..O_k.
        active_axioms: Tuples of (mapping_id, action_tag, rel['equiv'|'sub'], lhs_iri, rhs_iri).
        targets: Tuples of ('UNSAT', class_iri, '') or ('SUB', sub_iri, sup_iri).
    Returns:
        List of (target_idx, kind, arg1, arg2, [(mapping_id, action_tag), ...]).
    """
    if not targets or not active_axioms:
        return []
    java_bin = resolve_java_bin()
    classpath = f"{CLASSES_DIR}{os.pathsep}{ROBOT_JAR}"

    with tempfile.TemporaryDirectory(prefix="clasp_mus_") as tmp:
        tmp_dir = Path(tmp)
        map_tsv = tmp_dir / "mappings.tsv"
        tgt_tsv = tmp_dir / "targets.tsv"
        out_tsv = tmp_dir / "cores.tsv"

        with map_tsv.open("w", encoding="utf-8") as fh:
            fh.write("mapping_id\taction_tag\trel\tlhs_iri\trhs_iri\n")
            for mid, tag, rel, lhs, rhs in active_axioms:
                fh.write(f"{mid}\t{tag}\t{rel}\t{lhs}\t{rhs}\n")

        with tgt_tsv.open("w", encoding="utf-8") as fh:
            fh.write("kind\targ1\targ2\n")
            for kind, a1, a2 in targets:
                fh.write(f"{kind}\t{a1}\t{a2}\n")

        cmd = [
            str(java_bin),
            heap_flag,
            "-cp",
            classpath,
            "ElkClosureRunner",
            "mus",
            str(map_tsv),
            str(tgt_tsv),
            str(out_tsv),
            *[str(p) for p in base_ontology_paths],
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_jvm_env(heap_flag),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ElkClosureRunner mus failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
            )

        results: list[tuple[int, str, str, str, list[tuple[str, str]]]] = []
        with out_tsv.open("r", encoding="utf-8") as fh:
            _ = fh.readline()  # header
            for line in fh:
                if not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5 or not parts[4]:
                    continue
                t_idx = int(parts[0])
                kind, a1, a2 = parts[1], parts[2], parts[3]
                members: list[tuple[str, str]] = []
                for item in parts[4].split(";"):
                    mid, _, tag = item.partition(":")
                    if mid and tag:
                        members.append((mid, tag))
                if members:
                    results.append((t_idx, kind, a1, a2, members))
        return results


def run_robot_cli(args: Sequence[str], heap_flag: str = "-Xmx4G") -> subprocess.CompletedProcess[str]:
    """Invoke standard ROBOT CLI command (`java -Xmx4G -jar vendor/robot.jar ...`)."""
    java_bin = resolve_java_bin()
    cmd = [str(java_bin), heap_flag, "-jar", str(ROBOT_JAR), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_jvm_env(heap_flag))
    if proc.returncode != 0:
        raise RuntimeError(f"ROBOT CLI failed ({' '.join(args)}):\n{proc.stderr}\n{proc.stdout}")
    return proc
