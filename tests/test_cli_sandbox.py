"""Security & ToS isolation tests for sandbox_cli_runner.py (Section 6.1 Five Mandatory Rules)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from clasp_ont.oracles.sandbox_cli_runner import (
    BATCH_SCHEMA_PATH,
    DEFAULT_CLI_CFG,
    REPO_ROOT,
    assert_isolated_temp_cwd,
    build_sandboxed_command,
    build_scrubbed_env,
    execute_vendor_microbatch,
    verify_microbatch_parity,
)
from clasp_ont.symbolic.cegar_loop import OracleDecision


def test_scrubbed_env_strips_all_billing_api_keys() -> None:
    dirty_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/test",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "OPENAI_API_KEY": "sk-openai-secret",
        "CODEX_API_KEY": "sk-codex-secret",
        "GEMINI_API_KEY": "ai-gemini-secret",
        "GOOGLE_API_KEY": "ai-google-secret",
        "CUSTOM_VENDOR_API_KEY": "custom-secret",
    }
    clean = build_scrubbed_env(dirty_env)
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "CUSTOM_VENDOR_API_KEY",
    ):
        assert k not in clean
    assert clean["PATH"] == "/usr/bin:/bin"


def test_isolated_temp_cwd_rejects_repo_tree_and_governance_files() -> None:
    with pytest.raises(PermissionError):
        assert_isolated_temp_cwd(REPO_ROOT)
    with pytest.raises(PermissionError):
        assert_isolated_temp_cwd(REPO_ROOT / "src")

    with tempfile.TemporaryDirectory(prefix="clasp_test_iso_") as td:
        tp = Path(td)
        assert_isolated_temp_cwd(tp)
        (tp / "SPEC.md").write_text("leak", encoding="utf-8")
        with pytest.raises(PermissionError):
            assert_isolated_temp_cwd(tp)


def test_disabled_tools_and_offline_cache_replay(tmp_path: Path) -> None:
    cfg = yaml.safe_load(DEFAULT_CLI_CFG.read_text(encoding="utf-8"))
    schema_obj = json.loads(BATCH_SCHEMA_PATH.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="clasp_cmd_check_") as td:
        tp = Path(td)
        claude_argv, _ = build_sandboxed_command(
            cfg["vendors"]["C1_Claude"], "prompt", schema_obj, tp, binary_override="/usr/bin/true"
        )
        assert "--tools" in claude_argv
        tools_idx = claude_argv.index("--tools")
        assert claude_argv[tools_idx + 1] == ""
        assert "--max-turns" in claude_argv

        codex_argv, _ = build_sandboxed_command(
            cfg["vendors"]["C2_Codex"], "prompt", schema_obj, tp, binary_override="/usr/bin/true"
        )
        assert "--ephemeral" in codex_argv
        assert "--output-schema" in codex_argv

    dossiers = [
        {
            "mapping_id": "m_test_1",
            "suite_id": "Toy",
            "subject_id": "A:1",
            "subject_ont": "A",
            "subject_label": "cell",
            "subject_synonyms": [],
            "subject_definition": "A cell.",
            "subject_parents_2hop": [],
            "subject_children_1hop": [],
            "subject_parent1_id": None,
            "predicate_id": "skos:exactMatch",
            "confidence": 0.9,
            "object_id": "B:1",
            "object_ont": "B",
            "object_label": "broad cell",
            "object_synonyms": [],
            "object_definition": "Broad cell.",
            "object_parents_2hop": [],
            "object_children_1hop": [],
            "object_parent1_id": None,
            "conflict_trace": "Unsat(A:1)",
            "estimated_tokens": 95,
        }
    ]

    # Unconfirmed live CLI must be blocked
    with pytest.raises(RuntimeError, match="live CLI execution is locked"):
        execute_vendor_microbatch(
            "C1_Claude",
            cfg["vendors"]["C1_Claude"],
            dossiers,
            schema_obj,
            cache_dir=tmp_path / "cache",
            allow_live_cli=False,
            human_terminal_confirmed=False,
        )

    # Mocked subprocess verifies cwd is outside REPO_ROOT and API keys are stripped, then populates cache
    captured: dict[str, object] = {}

    def _fake_runner(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = Path(kwargs["cwd"]).resolve()
        captured["env"] = kwargs["env"]
        payload = {
            "decisions": [
                {
                    "mapping_id": "m_test_1",
                    "p_keep_equiv": 0.05,
                    "p_downgrade_sub": 0.80,
                    "p_downgrade_sup": 0.05,
                    "p_weaken_ancestor": 0.05,
                    "p_negate_drop": 0.05,
                    "preferred_weaken_direction": "sub_to_obj_parent",
                    "rationale": "Subject is narrower than object.",
                }
            ]
        }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    v_cfg = dict(cfg["vendors"]["C1_Claude"])
    v_cfg["binary_candidates"] = ["/usr/bin/true"]

    decs, from_cache = execute_vendor_microbatch(
        "C1_Claude",
        v_cfg,
        dossiers,
        schema_obj,
        cache_dir=tmp_path / "cache",
        allow_live_cli=True,
        human_terminal_confirmed=True,
        runner_fn=_fake_runner,
    )
    assert not from_cache
    assert REPO_ROOT.resolve() not in captured["cwd"].parents
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert decs["m_test_1"].probs["downgrade_sub"] == pytest.approx(0.80)

    # Subsequent zero-key replay hits SHA-256 cache with allow_live_cli=False
    decs_replay, from_cache_replay = execute_vendor_microbatch(
        "C1_Claude",
        v_cfg,
        dossiers,
        schema_obj,
        cache_dir=tmp_path / "cache",
        allow_live_cli=False,
        human_terminal_confirmed=False,
    )
    assert from_cache_replay
    assert decs_replay["m_test_1"].probs == decs["m_test_1"].probs


def test_microbatch_parity_checker() -> None:
    single = {
        f"m{i}": OracleDecision(
            mapping_id=f"m{i}",
            probs={
                "keep_equiv": 0.1,
                "downgrade_sub": 0.7,
                "downgrade_sup": 0.1,
                "weaken_ancestor": 0.05,
                "negate_drop": 0.05,
            },
        )
        for i in range(50)
    }
    batch = dict(single)
    # 1 disagreement out of 50 = 2.0% <= 2.0% tolerance
    batch["m0"] = OracleDecision(
        mapping_id="m0",
        probs={
            "keep_equiv": 0.7,
            "downgrade_sub": 0.1,
            "downgrade_sup": 0.1,
            "weaken_ancestor": 0.05,
            "negate_drop": 0.05,
        },
    )
    rep = verify_microbatch_parity(single, batch, max_delta=0.02)
    assert rep["evaluated_count"] == 50
    assert rep["within_tolerance"] is True
