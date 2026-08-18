import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

import main as cli_module
from agents.llm_agent import MOCK_BRAVE_KEY
from checkpoint_fixtures import make_pool_exhaustion_checkpoint
from core.models import Position
from main import main
from utils.json_store import JsonStore


def write_workbook(path: Path, counts: dict[str, int]) -> None:
    rows = []
    index = 0
    for role, count in counts.items():
        for _ in range(count):
            index += 1
            rows.append(
                {
                    "Id": index,
                    "R": role,
                    "Nome": f"Player {index}",
                    "Squadra": "Team",
                    "Qt.A": 10,
                }
            )
    frame = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Tutti", index=False, startrow=1)
        writer.sheets["Tutti"]["A1"] = "Fixture"


def write_config(path: Path, workbook: Path, buyers: list[dict]) -> None:
    write_raw_config(
        path,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook)},
            "buyers": buyers,
        },
    )


def write_raw_config(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def make_checkpoint_file(tmp_path: Path, *, no_progress: bool = False) -> Path:
    checkpoint = make_pool_exhaustion_checkpoint()
    if no_progress:
        checkpoint.players[-1].position = Position.A
        checkpoint.unsold_players[0].position = Position.A
    path = tmp_path / "checkpoint.json"
    return JsonStore().save_document(checkpoint, path)


def capture_log_errors(monkeypatch) -> list[str]:
    errors: list[str] = []

    def record(message: str, *args, **kwargs) -> None:
        errors.append(message.format(*args) if args else message)

    monkeypatch.setattr(cli_module.logger, "error", record)
    return errors


def test_cli_writes_report_for_complete_fixture(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    exit_code = main(
        [
            "--config",
            str(config),
            "--output",
            str(report),
            "--checkpoint",
            str(tmp_path / "checkpoint.json"),
        ]
    )

    assert exit_code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["document_type"] == "auction_report"
    assert data["players_sold"] == 25
    assert len(data["squads"]["b1"]["players"]) == 25


def test_cli_saves_checkpoint_and_returns_error_when_pool_is_too_small(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    exit_code = main(
        [
            "--config",
            str(config),
            "--output",
            str(tmp_path / "report.json"),
            "--checkpoint",
            str(checkpoint),
        ]
    )

    assert exit_code == 1
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["document_type"] == "auction_checkpoint"
    assert data["error_code"] == "pool_exhausted"
    assert data["error"]
    assert data["missing_roles"]["b1"]["P"] == 3


def test_cli_resumes_autonomous_checkpoint_without_config_or_players(tmp_path):
    checkpoint = make_checkpoint_file(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main(
        [
            "--resume",
            str(checkpoint),
            "--config",
            str(tmp_path / "missing.yaml"),
            "--players",
            str(tmp_path / "missing.xlsx"),
            "--seed",
            "999",
            "--output",
            str(report),
        ]
    )

    assert exit_code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["document_type"] == "auction_report"
    assert data["players_sold"] == data["total_players"]


def test_cli_incomplete_resume_writes_replacement_checkpoint(tmp_path):
    checkpoint = make_checkpoint_file(tmp_path, no_progress=True)
    replacement = tmp_path / "replacement.json"

    exit_code = main(
        [
            "--resume",
            str(checkpoint),
            "--checkpoint",
            str(replacement),
        ]
    )

    assert exit_code == 1
    loaded = JsonStore().load_checkpoint(replacement)
    assert loaded.run_number == 2
    assert loaded.error_code == "pool_exhausted"


def test_cli_resume_defaults_replacement_to_input_checkpoint(tmp_path):
    checkpoint = make_checkpoint_file(tmp_path, no_progress=True)

    exit_code = main(["--resume", str(checkpoint)])

    assert exit_code == 1
    loaded = JsonStore().load_checkpoint(checkpoint)
    assert loaded.run_number == 2


def test_cli_invalid_resume_does_not_write_checkpoint(tmp_path):
    invalid = tmp_path / "invalid.json"
    output = tmp_path / "new-checkpoint.json"
    invalid.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "--resume",
            str(invalid),
            "--checkpoint",
            str(output),
        ]
    )

    assert exit_code == 1
    assert not output.exists()


def test_cli_configuration_error_does_not_write_resumable_checkpoint(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )
    config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
    config_data["simulation"]["budget"] = 24
    config.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config),
            "--checkpoint",
            str(checkpoint),
        ]
    )

    assert exit_code == 1
    assert not checkpoint.exists()


def test_cli_unexpected_engine_error_does_not_write_checkpoint(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    def fail_inside_engine(self):
        raise RuntimeError("unexpected engine failure")

    monkeypatch.setattr(cli_module.AuctionEngine, "run", fail_inside_engine)

    assert main(["--config", str(config), "--checkpoint", str(checkpoint)]) == 1
    assert not checkpoint.exists()


def test_cli_resume_does_not_read_excel(monkeypatch, tmp_path):
    checkpoint = make_checkpoint_file(tmp_path)
    report = tmp_path / "report.json"

    def fail_if_excel_is_read(self):
        raise AssertionError("resume must not load Excel players")

    monkeypatch.setattr(cli_module.ExcelHandler, "load_players", fail_if_excel_is_read)

    assert main(["--resume", str(checkpoint), "--output", str(report)]) == 0


# — configuration contract (TODO 2) —


def test_cli_ignores_legacy_config_keys(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(
        config,
        {
            "simulation": {"budget_iniziale": 24, "seed": 42},
            "paths": {
                "players": str(workbook),
                "database": str(tmp_path / "missing.xlsx"),
                "checkpoints": str(tmp_path / "legacy-checkpoints"),
            },
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
        },
    )

    exit_code = main(["--config", str(config), "--output", str(report)])

    assert exit_code == 0
    assert report.exists()


def test_cli_requires_seed_in_config(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500},
            "paths": {"players": str(workbook)},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
        },
    )

    assert main(["--config", str(config), "--output", str(report)]) == 1
    assert not report.exists()


def test_cli_requires_players_path_in_config(tmp_path):
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
        },
    )

    assert main(["--config", str(config), "--output", str(report)]) == 1
    assert not report.exists()


def test_cli_seed_overrides_yaml_seed(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    exit_code = main(
        [
            "--config",
            str(config),
            "--seed",
            "999",
            "--checkpoint",
            str(checkpoint),
        ]
    )

    assert exit_code == 1
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["simulation"]["seed"] == 999


def test_cli_players_override_beats_yaml(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(tmp_path / "missing.xlsx")},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
        },
    )

    exit_code = main(
        [
            "--config",
            str(config),
            "--players",
            str(workbook),
            "--output",
            str(report),
        ]
    )

    assert exit_code == 0
    assert report.exists()


def test_cli_output_override_beats_yaml(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    yaml_report = tmp_path / "yaml-report.json"
    cli_report = tmp_path / "cli-report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook), "output": str(yaml_report)},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
        },
    )

    assert main(["--config", str(config), "--output", str(cli_report)]) == 0
    assert cli_report.exists()
    assert not yaml_report.exists()


def test_cli_requires_at_least_one_buyer(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook)},
            "buyers": [],
        },
    )

    assert main(["--config", str(config)]) == 1


# — configuration contract validation (P2) —


def test_cli_rejects_buyers_as_mapping(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": "dummy.xlsx"},
            "buyers": {"b1": {"id": "b1", "name": "Alpha"}},
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'buyers' must be a non-empty list" in error for error in errors)


def test_cli_rejects_string_seed(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": "42"},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1", "name": "Alpha"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'simulation.seed' must be an int" in error for error in errors)


def test_cli_rejects_float_seed(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42.5},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1", "name": "Alpha"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'simulation.seed' must be an int" in error for error in errors)


def test_cli_rejects_bool_seed(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": True},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1", "name": "Alpha"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'simulation.seed' must be an int" in error for error in errors)


def test_cli_rejects_non_int_budget(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": "500", "seed": 42},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1", "name": "Alpha"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'simulation.budget' must be an int >= 25" in error for error in errors)


def test_cli_rejects_unknown_strategy(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "chaos"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(
        "'buyers[0].strategy' must be 'deterministic', 'random' or 'llm'" in error
        for error in errors
    )


def test_cli_rejects_buyer_without_name(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": "dummy.xlsx"},
            "buyers": [{"id": "b1"}],
        },
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(
        "'buyers[0].name' must be a non-empty string" in error for error in errors
    )


def test_cli_buyer_strategy_defaults_to_deterministic(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook)},
            "buyers": [{"id": "b1", "name": "Alpha"}],
        },
    )

    exit_code = main(
        ["--config", str(config), "--checkpoint", str(checkpoint)]
    )

    assert exit_code == 1
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert data["buyers"][0]["strategy"] == "deterministic"


def test_cli_buyer_priority_defaults_to_index(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook)},
            "buyers": [
                {"id": "b1", "name": "Alpha"},
                {"id": "b2", "name": "Beta"},
            ],
        },
    )

    exit_code = main(
        ["--config", str(config), "--checkpoint", str(checkpoint)]
    )

    assert exit_code == 1
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert [buyer["priority"] for buyer in data["buyers"]] == [0, 1]


def test_cli_log_to_file_creates_log_file(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    log_dir = tmp_path / "logs"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(
        config,
        {
            "simulation": {"budget": 500, "seed": 42},
            "paths": {"players": str(workbook), "logs": str(log_dir)},
            "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
            "logging": {"level": "INFO", "log_to_file": True},
        },
    )

    assert main(["--config", str(config), "--output", str(report)]) == 0
    assert list(log_dir.glob("fantacalcio_*.log"))


def test_cli_missing_config_file_returns_error(tmp_path):
    assert main(["--config", str(tmp_path / "missing.yaml")]) == 1


def test_default_config_satisfies_contract():
    repo_root = Path(__file__).resolve().parent.parent
    default = yaml.safe_load(
        repo_root.joinpath("configs/default.yaml").read_text(encoding="utf-8")
    )

    assert default["simulation"]["budget"] == 500
    assert default["simulation"]["seed"] == 42
    assert default["paths"]["players"]
    assert default["buyers"]
    for buyer in default["buyers"]:
        assert buyer["id"] and buyer["name"]
        assert buyer["strategy"] in ("deterministic", "random")


def test_legacy_root_config_removed():
    repo_root = Path(__file__).resolve().parent.parent
    assert not repo_root.joinpath("config.yaml").exists()


class FakeLlmClient:
    """Scripted LlmClient replacement for CLI tests (no network)."""

    def __init__(
        self,
        base_url,
        api_key,
        brave_base_url,
        brave_api_key,
        timeout_seconds=30,
        transport=None,
    ):
        self.calls = 0

    def chat(self, messages, tools, model, temperature):
        self.calls += 1
        return {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "submit_bid", "args": {"amount": 1}}
            ],
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def search_news(self, query, count):
        return "search non disponibile"


def base_llm_config(workbook: Path) -> dict:
    return {
        "simulation": {"budget": 500, "seed": 42},
        "paths": {"players": str(workbook)},
        "llm": {
            "base_url": "https://api.test/v1",
            "api_key_env": "TEST_LLM_API_KEY",
            "model": "gpt-4o-mini",
            "temperature": 0.7,
            "timeout_seconds": 30,
            "brave": {
                "base_url": "https://api.search.brave.com/res/v1/web/search",
                "api_key": MOCK_BRAVE_KEY,
            },
        },
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {}}],
    }


def test_cli_llm_run_completes_with_fake_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

    exit_code = main(["--config", str(config), "--output", str(report)])

    assert exit_code == 0
    report_data = json.loads(report.read_text(encoding="utf-8"))
    assert report_data["players_sold"] == 25
    traces = list((tmp_path / "logs" / "traces").glob("*/b1.jsonl"))
    assert len(traces) == 1
    lines = traces[0].read_text(encoding="utf-8").splitlines()
    assert sum(
        json.loads(line)["phase"] == "bid" for line in lines
    ) == 25


def test_cli_missing_llm_api_key_fails_before_auction(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("TEST_LLM_API_KEY" in error for error in errors)


@pytest.mark.parametrize(
    ("buyers_override", "message"),
    [
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm"}],
            "'buyers[0].llm' must be a mapping",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"temperature": "hot"}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"temperature": 2.5}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"max_tool_iterations": 0}}],
            "'buyers[0].llm.max_tool_iterations' must be an int >= 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": ["search_news"]}}],
            "must contain 'submit_bid'",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": ["submit_bid", "mystery"]}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": []}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": 0.5, "D": 0.5, "C": 0.5, "A": 0.5}}}],
            "must sum to 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": 0.5, "X": 0.5}}}],
            "keys must be a subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": -0.1, "D": 0.2, "C": 0.4, "A": 0.5}}}],
            "must be a number in [0, 1]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"target_players": ["Lautaro", ""]}}],
            "list of non-empty strings",
        ),
    ],
)
def test_cli_rejects_invalid_buyer_llm_block(monkeypatch, tmp_path, buyers_override, message):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["buyers"] = buyers_override
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(message in error for error in errors)


def test_cli_llm_buyer_requires_global_llm_block(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data.pop("llm")
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm' must be a mapping" in error for error in errors)


def test_cli_rejects_empty_llm_base_url(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"]["base_url"] = ""
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.base_url' must be a non-empty string" in error for error in errors)


def test_cli_rejects_missing_brave_block(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.brave' must be a mapping" in error for error in errors)


def test_cli_rejects_zero_timeout(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"]["timeout_seconds"] = 0
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.timeout_seconds' must be an int > 0" in error for error in errors)


def llm_sidecar_payload() -> dict:
    return {
        "schema_version": 1,
        "llm": {
            "base_url": "https://api.test/v1",
            "api_key_env": "TEST_LLM_API_KEY",
            "model": "gpt-4o-mini",
            "temperature": 0.7,
            "timeout_seconds": 30,
            "brave": {
                "base_url": "https://api.search.brave.com/res/v1/web/search",
                "api_key": MOCK_BRAVE_KEY,
            },
        },
        "buyers": {
            "incomplete": {"llm": {"temperature": 0.3}},
        },
    }


def test_cli_llm_exhaustion_writes_checkpoint_and_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    sidecar = tmp_path / "checkpoint.llm.yaml"
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["llm"]["model"] == "gpt-4o-mini"
    assert data["llm"]["api_key_env"] == "TEST_LLM_API_KEY"
    assert "sk-" not in sidecar.read_text(encoding="utf-8")
    # b1 has no per-buyer block in the config, so none is written
    assert data["buyers"] == {}


def test_cli_deterministic_exhaustion_writes_no_sidecar(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    assert main(["--config", str(config), "--checkpoint", str(checkpoint)]) == 1
    assert checkpoint.exists()
    assert not (tmp_path / "checkpoint.llm.yaml").exists()


def make_llm_checkpoint(tmp_path, *, no_progress: bool = False) -> Path:
    checkpoint = make_checkpoint_file(tmp_path, no_progress=no_progress)
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    data["buyers"] = [
        {"id": "complete", "name": "Complete", "strategy": "deterministic", "priority": 0},
        {"id": "incomplete", "name": "Incomplete", "strategy": "llm", "priority": 1},
    ]
    checkpoint.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(llm_sidecar_payload()), encoding="utf-8"
    )
    return checkpoint


def test_cli_resumes_llm_checkpoint_with_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_checkpoint(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(report),
    ])

    assert exit_code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["players_sold"] == data["total_players"]
    assert (tmp_path / "traces" / "resume" / "incomplete.jsonl").exists()


def test_cli_resume_llm_without_sidecar_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    checkpoint = make_llm_checkpoint(tmp_path)
    (tmp_path / "checkpoint.llm.yaml").unlink()
    report = tmp_path / "report.json"
    errors = capture_log_errors(monkeypatch)

    exit_code = main(["--resume", str(checkpoint), "--output", str(report)])

    assert exit_code == 1
    assert not report.exists()
    assert any("sidecar" in error for error in errors)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "llm": llm_sidecar_payload()["llm"]},
        [1, 2, 3],
        {"schema_version": 1},
    ],
)
def test_cli_resume_llm_with_malformed_sidecar_fails(monkeypatch, tmp_path, payload):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    checkpoint = make_llm_checkpoint(tmp_path)
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--resume", str(checkpoint)]) == 1
    assert any("sidecar" in error for error in errors)


def test_cli_second_exhaustion_propagates_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_checkpoint(tmp_path, no_progress=True)

    exit_code = main(["--resume", str(checkpoint)])

    assert exit_code == 1
    loaded = JsonStore().load_checkpoint(checkpoint)
    assert loaded.run_number == 2
    sidecar = tmp_path / "checkpoint.llm.yaml"
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["buyers"]["incomplete"]["llm"] == {"temperature": 0.3}
