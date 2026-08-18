import json

import yaml

import main as cli_module
from main import main
from test_cli import (
    FakeLlmClient,
    base_llm_config,
    write_raw_config,
    write_workbook,
)


def test_benchmark_command_produces_layout_and_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(config, base_llm_config(workbook))
    root = tmp_path / "bench"

    exit_code = main([
        "benchmark",
        "--config", str(config),
        "--runs", "2",
        "--seed", "42",
        "--output", str(root),
    ])

    assert exit_code == 0
    for name in ("run_001", "run_002"):
        assert (root / name / "report.json").exists()
        assert (root / name / "traces" / "b1.jsonl").exists()
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["runs"][0]["run"] == "run_001"
    assert metrics["runs"][0]["seed"] == 42
    assert metrics["runs"][1]["seed"] == 43
    assert metrics["runs"][0]["completed"] is True
    assert metrics["aggregates"]["b1"]["parse_rate"]["mean"] == 1.0
    csv_text = (root / "metrics.csv").read_text(encoding="utf-8")
    assert "buyer_id" in csv_text.splitlines()[0]
    assert len(csv_text.splitlines()) == 3  # header + 2 run rows


def test_benchmark_records_incomplete_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))
    root = tmp_path / "bench"

    exit_code = main([
        "benchmark",
        "--config", str(config),
        "--runs", "2",
        "--seed", "42",
        "--output", str(root),
    ])

    assert exit_code == 0
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert [run["completed"] for run in metrics["runs"]] == [False, False]
    report = json.loads((root / "run_001" / "report.json").read_text(encoding="utf-8"))
    assert report["document_type"] == "auction_report"
    assert not (root / "run_001" / "checkpoint.json").exists()


def test_benchmark_rejects_zero_runs():
    assert main(["benchmark", "--runs", "0"]) == 1
