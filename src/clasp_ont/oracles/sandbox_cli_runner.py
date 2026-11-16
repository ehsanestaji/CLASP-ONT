"""Sandboxed Zero-API-Spend Commercial Frontier CLI Runner (`Claude`, `Codex`, `Antigravity`).

Enforces all Five Mandatory CLI Sandbox Rules (Section 6.1):
  1. Scrub Metered Keys: Strips ANTHROPIC_API_KEY, OPENAI_API_KEY, CODEX_API_KEY, GEMINI_API_KEY, etc.
  2. Empty Temporary Working Directory: Executes strictly inside tempfile.TemporaryDirectory() outside REPO_ROOT.
  3. Zero Tool Access: Passes `--tools ""` (Claude) / `--ephemeral` (Codex) with 1-turn JSON-schema output.
  4. Micro-Batch Parity Check: Verifies 25-item micro-batch vs single-item agreement within +-2.0% on 50 items.
  5. Human-Launched Terminal Execution & SHA-256 Cache: Paces live calls with time.sleep(3) and writes
     deterministic SHA-256 cached responses to results/oracle_cache/ for zero-key peer-review replay.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml

from clasp_ont.symbolic.cegar_loop import ALL_LATTICE_ACTIONS, OracleDecision

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLI_CFG = REPO_ROOT / "configs" / "cli_oracles.yaml"
BATCH_SCHEMA_PATH = REPO_ROOT / "schemas" / "oracle_batch.schema.json"

SCRUBBED_API_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "CLAUDE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)


def build_scrubbed_env(base_env: dict[str, str] | None = None, extra_scrub: Sequence[str] = ()) -> dict[str, str]:
    """Return subprocess environment with all metered billing API keys strictly removed."""
    src = dict(os.environ if base_env is None else base_env)
    for k in (*SCRUBBED_API_KEYS, *extra_scrub):
        src.pop(k, None)
    # Also remove any wildcard *_API_KEY variable that could trigger metered billing
    for k in list(src.keys()):
        if k.upper().endswith("_API_KEY"):
            src.pop(k, None)
    return src


def assert_isolated_temp_cwd(cwd: Path, repo_root: Path = REPO_ROOT) -> None:
    """Hard sandbox assertion: cwd must exist, be empty or schema-only, and lie strictly outside repo_root."""
    resolved_cwd = cwd.resolve()
    resolved_repo = repo_root.resolve()
    if resolved_cwd == resolved_repo or resolved_repo in resolved_cwd.parents:
        raise PermissionError(
            f"CLI Sandbox Violation: cwd {resolved_cwd} is inside repository tree {resolved_repo}!"
        )
    for forbidden in ("SPEC.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "clasp_ont_master_specification.md"):
        if (resolved_cwd / forbidden).exists():
            raise PermissionError(f"CLI Sandbox Violation: forbidden file {forbidden} visible in {resolved_cwd}!")


def compute_batch_cache_key(vendor_id: str, dossiers: Sequence[dict], schema_obj: dict) -> str:
    canonical = json.dumps(
        {
            "vendor_id": vendor_id,
            "mapping_ids": [d["mapping_id"] for d in dossiers],
            "dossiers": list(dossiers),
            "schema_id": schema_obj.get("$id", "oracle_batch"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_oracle_prompt(dossiers: Sequence[dict], schema_obj: dict) -> str:
    return (
        "You are an expert biomedical ontology curator and Description Logic (OWL 2 EL) reasoner.\n"
        "For each contested cross-ontology correspondence below, evaluate its definitions, synonyms,\n"
        "2-hop hierarchy, immediate parent IDs, and formal conflict trace.\n"
        "Assign calibrated probabilities across the 5-element refinement poset that sum to 1.0:\n"
        "  - p_keep_equiv: retain s == o (exact equivalence)\n"
        "  - p_downgrade_sub: refine to s <= o (subject is a more specific subclass of object)\n"
        "  - p_downgrade_sup: refine to o <= s (object is a more specific subclass of subject)\n"
        "  - p_weaken_ancestor: refine to s <= Parent_1(o) or o <= Parent_1(s) when sibling/cousin clash blocks both sub and sup\n"
        "  - p_negate_drop: drop mapping and emit negative curation record (homonym, cross-kingdom, or disjoint mismatch)\n"
        "Also set preferred_weaken_direction ('sub_to_obj_parent' or 'obj_to_sub_parent') and a concise\n"
        "one-sentence ontological rationale referencing genus-differentia scope.\n"
        "Return ONLY valid JSON conforming strictly to this JSON Schema:\n"
        f"{json.dumps(schema_obj, separators=(',', ':'))}\n\n"
        "CONTESTED DOSSIERS:\n"
        f"{json.dumps(list(dossiers), ensure_ascii=False)}\n"
    )


def _extract_json_payload(raw_stdout: str) -> dict:
    """Parse structured JSON response from Claude (--output-format json wrapper), Codex JSONL, or raw JSON."""
    text = raw_stdout.strip()
    if not text:
        raise ValueError("Empty stdout from CLI oracle.")

    # 1. Direct JSON object
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if "decisions" in parsed:
                return parsed
            # Claude Code `-p --output-format json` wraps structured output in `structured_output` or `result`
            if isinstance(parsed.get("structured_output"), dict) and "decisions" in parsed["structured_output"]:
                return parsed["structured_output"]
            if isinstance(parsed.get("result"), str):
                inner = _extract_json_payload(parsed["result"])
                if "decisions" in inner:
                    return inner
    except json.JSONDecodeError:
        pass

    # 2. JSONL event stream (e.g., `codex exec --json`)
    for line in reversed(text.splitlines()):
        line_s = line.strip()
        if not line_s.startswith("{"):
            continue
        try:
            ev = json.loads(line_s)
            if isinstance(ev, dict):
                if "decisions" in ev:
                    return ev
                for key in ("text", "content", "message", "output"):
                    val = ev.get(key)
                    if isinstance(val, str) and "decisions" in val:
                        return _extract_json_payload(val)
        except json.JSONDecodeError:
            continue

    # 3. Markdown ```json ... ``` fenced block (e.g., `agy -p`)
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    for block in reversed(fenced):
        try:
            obj = json.loads(block)
            if isinstance(obj, dict) and "decisions" in obj:
                return obj
        except json.JSONDecodeError:
            continue

    # 4. Outermost brace span containing "decisions"
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict) and "decisions" in obj:
            return obj

    raise ValueError(f"Could not locate valid `decisions` JSON object in CLI output: {text[:300]}")


def normalize_and_validate_batch(
    payload: dict,
    expected_ids: Sequence[str],
    schema_obj: dict,
) -> dict[str, OracleDecision]:
    validator = jsonschema.Draft202012Validator(schema_obj)
    validator.validate(payload)

    out: dict[str, OracleDecision] = {}
    for item in payload["decisions"]:
        mid = item["mapping_id"]
        raw_p = {
            "keep_equiv": max(0.0, float(item["p_keep_equiv"])),
            "downgrade_sub": max(0.0, float(item["p_downgrade_sub"])),
            "downgrade_sup": max(0.0, float(item["p_downgrade_sup"])),
            "weaken_ancestor": max(0.0, float(item["p_weaken_ancestor"])),
            "negate_drop": max(0.0, float(item["p_negate_drop"])),
        }
        s = sum(raw_p.values())
        if s <= 0.0:
            probs = {a: (0.2 if a in ALL_LATTICE_ACTIONS else 0.0) for a in ALL_LATTICE_ACTIONS}
        else:
            probs = {a: round(raw_p[a] / s, 6) for a in ALL_LATTICE_ACTIONS}
        out[mid] = OracleDecision(
            mapping_id=mid,
            probs=probs,
            preferred_weaken_direction=item["preferred_weaken_direction"],
            rationale=item["rationale"],
        )

    missing = [m for m in expected_ids if m not in out]
    if missing:
        raise ValueError(f"Oracle batch response missing mapping_ids: {missing}")
    return out


def resolve_vendor_binary(candidates: Sequence[str]) -> str:
    for c in candidates:
        p = Path(c)
        if p.is_absolute() and p.is_file() and os.access(p, os.X_OK):
            return str(p)
        w = shutil.which(c)
        if w:
            return w
    raise FileNotFoundError(f"None of CLI binary candidates found on PATH: {candidates}")


def build_sandboxed_command(
    vendor_cfg: dict,
    prompt: str,
    schema_obj: dict,
    isolated_tmp_dir: Path,
    binary_override: str | None = None,
) -> tuple[list[str], str | None]:
    """Construct exact zero-tool CLI argv and optional stdin text inside isolated_tmp_dir."""
    assert_isolated_temp_cwd(isolated_tmp_dir)
    binary = binary_override or resolve_vendor_binary(vendor_cfg["binary_candidates"])
    schema_inline = json.dumps(schema_obj, separators=(",", ":"))
    schema_file = isolated_tmp_dir / "oracle_batch.schema.json"
    schema_file.write_text(schema_inline, encoding="utf-8")

    argv: list[str] = [binary]
    for tok in vendor_cfg["args_template"]:
        argv.append(
            tok.replace("{schema_inline}", schema_inline).replace("{schema_file}", str(schema_file))
        )

    mode = vendor_cfg.get("input_mode", "stdin")
    if mode == "arg_prompt":
        argv.append(prompt)
        return argv, None
    return argv, prompt


def execute_vendor_microbatch(
    vendor_id: str,
    vendor_cfg: dict,
    dossiers: Sequence[dict],
    schema_obj: dict,
    cache_dir: Path,
    allow_live_cli: bool = False,
    human_terminal_confirmed: bool = False,
    extra_scrub_keys: Sequence[str] = (),
    runner_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, OracleDecision], bool]:
    """Load cached batch decision or (only when human_terminal_confirmed) invoke isolated CLI subprocess.

    Returns (decisions_by_mapping_id, from_cache).
    """
    expected_ids = [d["mapping_id"] for d in dossiers]
    batch_sha = compute_batch_cache_key(vendor_id, dossiers, schema_obj)
    vendor_cache = cache_dir / vendor_id
    vendor_cache.mkdir(parents=True, exist_ok=True)
    cache_file = vendor_cache / f"{batch_sha}.json"

    if cache_file.is_file():
        cached_record = json.loads(cache_file.read_text(encoding="utf-8"))
        decs = normalize_and_validate_batch(cached_record["response"], expected_ids, schema_obj)
        return decs, True

    if not allow_live_cli or not human_terminal_confirmed:
        raise RuntimeError(
            f"Cache miss for {vendor_id} batch {batch_sha[:12]} and live CLI execution is locked. "
            "Run `make run-frontier-oracles` from a human terminal to populate results/oracle_cache/."
        )

    prompt = build_oracle_prompt(dossiers, schema_obj)
    scrubbed_env = build_scrubbed_env(extra_scrub=extra_scrub_keys)

    with tempfile.TemporaryDirectory(prefix=f"clasp_oracle_{vendor_id}_") as tmp:
        tmp_path = Path(tmp)
        assert_isolated_temp_cwd(tmp_path)
        argv, stdin_text = build_sandboxed_command(vendor_cfg, prompt, schema_obj, tmp_path)
        proc = runner_fn(
            argv,
            input=stdin_text,
            cwd=str(tmp_path),
            env=scrubbed_env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Sandboxed CLI {vendor_id} failed (rc={proc.returncode}): {proc.stderr[:400]}"
            )

    parsed_payload = _extract_json_payload(proc.stdout)
    decs = normalize_and_validate_batch(parsed_payload, expected_ids, schema_obj)

    cache_record = {
        "vendor_id": vendor_id,
        "batch_sha256": batch_sha,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "mapping_ids": expected_ids,
        "response": parsed_payload,
    }
    cache_file.write_text(json.dumps(cache_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return decs, False


def run_vendor_sweep(
    vendor_id: str,
    dossiers: Sequence[dict],
    cli_cfg_path: Path = DEFAULT_CLI_CFG,
    allow_live_cli: bool = False,
    human_terminal_confirmed: bool = False,
    runner_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, OracleDecision]:
    """Evaluate all dossiers in deterministic shuffled 25-item micro-batches with 3s ToS pacing."""
    cfg = yaml.safe_load(cli_cfg_path.read_text(encoding="utf-8"))
    schema_obj = json.loads(BATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
    vendor_cfg = cfg["vendors"][vendor_id]
    batch_size = int(cfg.get("micro_batch_size", 25))
    sleep_sec = float(cfg.get("inter_batch_sleep_seconds", 3.0))
    seed = int(cfg.get("seed", 20261116))
    cache_dir = REPO_ROOT / cfg.get("cache_dir", "results/oracle_cache")
    extra_scrub = cfg.get("scrubbed_env_keys", [])

    ordered = list(dossiers)
    rng = random.Random(seed)
    rng.shuffle(ordered)

    all_decisions: dict[str, OracleDecision] = {}
    for start in range(0, len(ordered), batch_size):
        chunk = ordered[start : start + batch_size]
        decs, from_cache = execute_vendor_microbatch(
            vendor_id=vendor_id,
            vendor_cfg=vendor_cfg,
            dossiers=chunk,
            schema_obj=schema_obj,
            cache_dir=cache_dir,
            allow_live_cli=allow_live_cli,
            human_terminal_confirmed=human_terminal_confirmed,
            extra_scrub_keys=extra_scrub,
            runner_fn=runner_fn,
        )
        all_decisions.update(decs)
        if not from_cache and start + batch_size < len(ordered) and sleep_sec > 0:
            time.sleep(sleep_sec)

    return all_decisions


def verify_microbatch_parity(
    single_decisions: dict[str, OracleDecision],
    batch_decisions: dict[str, OracleDecision],
    max_delta: float = 0.02,
) -> dict[str, float | bool]:
    """Verify Section 6.1 Rule 4: 25-item micro-batching matches single-item top-1 agreement within +-2.0%."""
    shared_ids = sorted(set(single_decisions.keys()) & set(batch_decisions.keys()))
    if not shared_ids:
        raise ValueError("No shared mapping IDs between single and micro-batch decisions.")

    matches = 0
    l1_diffs: list[float] = []
    for mid in shared_ids:
        p1 = single_decisions[mid].probs
        p2 = batch_decisions[mid].probs
        top1 = max(p1.items(), key=lambda kv: kv[1])[0]
        top2 = max(p2.items(), key=lambda kv: kv[1])[0]
        if top1 == top2:
            matches += 1
        l1_diffs.append(0.5 * sum(abs(p1[a] - p2[a]) for a in ALL_LATTICE_ACTIONS))

    agreement = matches / len(shared_ids)
    disagreement = (len(shared_ids) - matches) / len(shared_ids)
    mean_tvd = sum(l1_diffs) / len(l1_diffs)
    return {
        "evaluated_count": len(shared_ids),
        "top1_agreement": round(agreement, 6),
        "disagreement_rate": round(disagreement, 6),
        "mean_total_variation_distance": round(mean_tvd, 6),
        "within_tolerance": disagreement <= (max_delta + 1e-12),
    }
