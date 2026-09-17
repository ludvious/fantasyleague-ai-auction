import json
import os
from pathlib import Path

import pandas as pd
import pytest
import yaml

import main as cli_module
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


def write_llm_run_config(
    path: Path, workbook: Path, *, buyers: list[dict] | None = None, logs: Path | None = None
) -> None:
    data = base_llm_config(workbook)
    if buyers is not None:
        data["buyers"] = buyers
    if logs is not None:
        data["paths"]["logs"] = str(logs)
    write_raw_config(path, data)


def use_fake_llm(monkeypatch) -> None:
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)


def make_checkpoint_file(tmp_path: Path, *, no_progress: bool = False) -> Path:
    checkpoint = make_pool_exhaustion_checkpoint()
    if no_progress:
        checkpoint.players[-1].position = Position.A
        checkpoint.unsold_players[0].position = Position.A
    path = tmp_path / "checkpoint.json"
    saved = JsonStore().save_document(checkpoint, path)
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(llm_sidecar_payload()), encoding="utf-8"
    )
    return saved


def capture_log_errors(monkeypatch) -> list[str]:
    errors: list[str] = []

    def record(message: str, *args, **kwargs) -> None:
        errors.append(message.format(*args) if args else message)

    monkeypatch.setattr(cli_module.logger, "error", record)
    return errors


def test_cli_writes_report_for_complete_fixture(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_llm_run_config(config, workbook, logs=tmp_path / "logs")

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


def test_cli_saves_checkpoint_and_returns_error_when_pool_is_too_small(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_llm_run_config(config, workbook, logs=tmp_path / "logs")

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


def test_cli_resumes_autonomous_checkpoint_without_config_or_players(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
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


def test_cli_incomplete_resume_writes_replacement_checkpoint(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
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


def test_cli_resume_defaults_replacement_to_input_checkpoint(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
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
        [{"id": "b1", "name": "Alpha", "llm": {}}],
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
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_llm_run_config(config, workbook, logs=tmp_path / "logs")

    def fail_inside_engine(self):
        raise RuntimeError("unexpected engine failure")

    monkeypatch.setattr(cli_module.AuctionEngine, "run", fail_inside_engine)

    assert main(["--config", str(config), "--checkpoint", str(checkpoint)]) == 1
    assert not checkpoint.exists()


def test_cli_resume_does_not_read_excel(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    checkpoint = make_checkpoint_file(tmp_path)
    report = tmp_path / "report.json"

    def fail_if_excel_is_read(self):
        raise AssertionError("resume must not load Excel players")

    monkeypatch.setattr(cli_module.ExcelHandler, "load_players", fail_if_excel_is_read)

    assert main(["--resume", str(checkpoint), "--output", str(report)]) == 0


# — configuration contract (TODO 2) —


def test_cli_ignores_legacy_config_keys(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
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
                "logs": str(tmp_path / "logs"),
            },
            "llm": base_llm_config(workbook)["llm"],
            "buyers": [{"id": "b1", "name": "Alpha", "llm": {}}],
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
            "buyers": [{"id": "b1", "name": "Alpha", "llm": {}}],
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
            "buyers": [{"id": "b1", "name": "Alpha", "llm": {}}],
        },
    )

    assert main(["--config", str(config), "--output", str(report)]) == 1
    assert not report.exists()


def test_cli_seed_overrides_yaml_seed(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_llm_run_config(config, workbook, logs=tmp_path / "logs")

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


def test_cli_players_override_beats_yaml(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["paths"]["players"] = str(tmp_path / "missing.xlsx")
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

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


def test_cli_output_override_beats_yaml(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    yaml_report = tmp_path / "yaml-report.json"
    cli_report = tmp_path / "cli-report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["paths"]["output"] = str(yaml_report)
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

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


def test_cli_buyer_priority_defaults_to_index(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_llm_run_config(
        config,
        workbook,
        logs=tmp_path / "logs",
        buyers=[
            {"id": "b1", "name": "Alpha", "llm": {}},
            {"id": "b2", "name": "Beta", "llm": {}},
        ],
    )

    exit_code = main(
        ["--config", str(config), "--checkpoint", str(checkpoint)]
    )

    assert exit_code == 1
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert [buyer["priority"] for buyer in data["buyers"]] == [0, 1]


def test_cli_log_to_file_creates_log_file(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    log_dir = tmp_path / "logs"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["paths"]["logs"] = str(log_dir)
    data["logging"] = {"level": "INFO", "log_to_file": True}
    write_raw_config(config, data)

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
    assert default["paths"]["coaches"]
    assert default["llm"]["api_key_env"]


def test_legacy_root_config_removed():
    repo_root = Path(__file__).resolve().parent.parent
    assert not repo_root.joinpath("config.yaml").exists()


class FakeLlmClient:
    """Scripted LlmClient replacement for CLI tests (no network)."""

    instances: list["FakeLlmClient"] = []

    def __init__(
        self,
        base_url,
        api_key,
        search=None,
        timeout_seconds=30,
        transport=None,
        extra_headers=None,
    ):
        self.calls = 0
        self.search = search
        self.extra_headers = extra_headers
        self.provider = search.get("provider") if search else None
        self.base_url = search.get("base_url") if search else None
        self.api_key = search.get("api_key") if search else None
        self.model = search.get("model") if search else None
        self.messages_seen = []
        FakeLlmClient.instances.append(self)

    def chat(self, messages, tools, model, temperature):
        self.calls += 1
        self.messages_seen.append(messages)
        return {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "submit_bid", "args": {"amount": 1}}
            ],
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def search_info(self, query, count):
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
                "api_key_env": "TEST_BRAVE_API_KEY",
            },
        },
        "buyers": [{"id": "b1", "name": "Alpha", "llm": {}}],
    }


def search_llm_config(workbook: Path) -> dict:
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    data["llm"]["search"] = {
        "provider": "responses",
        "model": "deepseek-v4-flash",
    }
    return data


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


def test_cli_inline_buyer_without_llm_block(monkeypatch, tmp_path):
    use_fake_llm(monkeypatch)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_llm_run_config(
        config,
        workbook,
        buyers=[{"id": "b1", "name": "Alpha"}],
        logs=tmp_path / "logs",
    )

    exit_code = main(["--config", str(config), "--output", str(report)])

    assert exit_code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["players_sold"] == 25


def test_build_bidders_passes_max_bid_retries(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "model": "gpt-4o-mini",
    }
    buyers = [
        {
            "id": "b1",
            "name": "Alpha",
            "llm": {"max_bid_retries": 0},
        }
    ]

    bidders = cli_module._build_bidders(
        buyers, llm_config=llm_config, run_dir=tmp_path, budget=500
    )

    assert bidders[0].max_bid_retries == 0


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
            [{"id": "b1", "name": "Alpha", "llm": {"temperature": "hot"}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"temperature": 2.5}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"max_tool_iterations": 0}}],
            "'buyers[0].llm.max_tool_iterations' must be an int >= 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"max_bid_retries": -1}}],
            "'buyers[0].llm.max_bid_retries' must be an int >= 0",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"max_bid_retries": True}}],
            "'buyers[0].llm.max_bid_retries' must be an int >= 0",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"tools": ["search_info"]}}],
            "must contain 'submit_bid'",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"tools": ["submit_bid", "mystery"]}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"tools": []}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"spending_profile": {"P": 0.5, "D": 0.5, "C": 0.5, "A": 0.5}}}],
            "must sum to 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"spending_profile": {"P": 0.5, "X": 0.5}}}],
            "keys must be a subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"spending_profile": {"P": -0.1, "D": 0.2, "C": 0.4, "A": 0.5}}}],
            "must be a number in [0, 1]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "llm": {"target_players": ["Lautaro", ""]}}],
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


def test_cli_search_block_optional_when_no_brave(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

    assert main(["--config", str(config), "--output", str(tmp_path / "r.json")]) == 0


@pytest.mark.parametrize(
    ("search_block", "message"),
    [
        (
            {"provider": "perplexity"},
            "'llm.search.provider' must be one of",
        ),
        (
            {"provider": "responses"},
            "'llm.search.model' must be a non-empty string",
        ),
        (
            {"provider": "anthropic", "model": "claude-opus-4-8", "api_key": "k"},
            "'llm.search.api_key' is not supported",
        ),
        (
            {"provider": "responses", "model": "m", "max_output_tokens": 0},
            "'llm.search.max_output_tokens' must be an int > 0",
        ),
        (
            {"provider": "responses", "model": "m", "base_url": "  "},
            "'llm.search.base_url' must be a non-empty string",
        ),
        (
            {"provider": "responses", "model": "m", "headers": "nope"},
            "'llm.search.headers' must be a mapping",
        ),
    ],
)
def test_cli_rejects_invalid_search_block(
    monkeypatch, tmp_path, search_block, message
):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["search"] = search_block
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(message in error for error in errors)


def test_cli_rejects_search_and_brave_together(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["brave"] = {
        "base_url": "https://api.search.brave.com/res/v1/web/search",
        "api_key_env": "TEST_BRAVE_API_KEY",
    }
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("mutually exclusive" in error for error in errors)


def test_cli_rejects_invalid_llm_headers(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["headers"] = {"x-empty": ""}
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.headers' entries must be non-empty strings" in error for error in errors)


def test_cli_resolves_search_config_with_defaults(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setenv("TEST_SEARCH_API_KEY", "search-dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "search": {
            "provider": "responses",
            "model": "deepseek-v4-flash",
            "api_key_env": "TEST_SEARCH_API_KEY",
        },
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.provider == "responses"
    assert client.base_url == "https://api.test/v1"
    assert client.api_key == "search-dummy"
    assert client.model == "deepseek-v4-flash"
    assert client.extra_headers["x-opencode-session"].startswith("fantasyleague-")


def test_cli_resolves_legacy_brave_block(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setenv("TEST_BRAVE_API_KEY", "brave-dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "brave": {
            "base_url": "https://api.search.brave.com/res/v1/web/search",
            "api_key_env": "TEST_BRAVE_API_KEY",
        },
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.search == {
        "provider": "brave",
        "base_url": "https://api.search.brave.com/res/v1/web/search",
        "api_key": "brave-dummy",
    }


def test_cli_search_inherits_api_key_env_from_llm(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "search": {"provider": "anthropic", "model": "claude-opus-4-8"},
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.api_key == "dummy"


def test_cli_search_uses_provider_default_base_url(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "search": {"provider": "anthropic", "model": "claude-opus-4-8"},
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.base_url == "https://api.anthropic.com"


def test_cli_search_passes_through_max_tokens_and_headers(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "search": {
            "provider": "responses",
            "model": "deepseek-v4-flash",
            "max_output_tokens": 123,
            "headers": {"x-custom": "custom-value"},
        },
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.search["max_output_tokens"] == 123
    assert client.search["headers"] == {"x-custom": "custom-value"}


def test_cli_rejects_brave_api_key_field(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"]["brave"]["api_key"] = "secret-key"
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.brave.api_key' is not supported" in error for error in errors)


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
                "api_key_env": "TEST_BRAVE_API_KEY",
            },
        },
        "buyers": {
            "incomplete": {"llm": {"temperature": 0.3}},
        },
    }


def search_sidecar_payload() -> dict:
    payload = llm_sidecar_payload()
    payload["llm"].pop("brave")
    payload["llm"]["search"] = {
        "provider": "responses",
        "model": "deepseek-v4-flash",
    }
    return payload


def make_llm_search_checkpoint(tmp_path, *, no_progress: bool = False) -> Path:
    checkpoint = make_llm_checkpoint(tmp_path, no_progress=no_progress)
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(search_sidecar_payload()), encoding="utf-8"
    )
    return checkpoint


def test_cli_resumes_llm_checkpoint_with_search_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_search_checkpoint(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(report),
    ])

    assert exit_code == 0
    assert (tmp_path / "traces" / "resume" / "incomplete.jsonl").exists()


def test_cli_exhaustion_sidecar_contains_search_block(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, search_llm_config(workbook))

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    data = yaml.safe_load(
        (tmp_path / "checkpoint.llm.yaml").read_text(encoding="utf-8")
    )
    assert data["llm"]["search"] == {
        "provider": "responses",
        "model": "deepseek-v4-flash",
    }
    assert "api_key" not in data["llm"]["search"]


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
    rendered = data["buyers"]["b1"]["llm"]["system_prompt"]
    assert rendered.startswith("Sei un allenatore-manager")
    assert "P: 3, D: 8, C: 8, A: 6" in rendered


def make_llm_checkpoint(tmp_path, *, no_progress: bool = False) -> Path:
    checkpoint = make_checkpoint_file(tmp_path, no_progress=no_progress)
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    data["buyers"] = [
        {"id": "complete", "name": "Complete", "strategy": "llm", "priority": 0},
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
    payload = llm_sidecar_payload()
    payload["buyers"]["incomplete"]["llm"]["system_prompt"] = "PROMPT DAL SIDECAR"
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )

    exit_code = main(["--resume", str(checkpoint)])

    assert exit_code == 1
    loaded = JsonStore().load_checkpoint(checkpoint)
    assert loaded.run_number == 2
    sidecar = tmp_path / "checkpoint.llm.yaml"
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["buyers"]["incomplete"]["llm"] == {
        "temperature": 0.3,
        "system_prompt": "PROMPT DAL SIDECAR",
    }


def test_cli_discovers_coaches_and_writes_resolved_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    write_workbook(workbook, {"A": 1})
    coaches = tmp_path / "coaches"
    coaches.mkdir()
    (coaches / "coachAgent_Joe.md").write_text(
        "---\nmodel: gpt-4o-mini\ntemperature: 0.2\n---\n\nJoe è aggressivo.\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    data = base_llm_config(workbook)
    data.pop("buyers")
    data["paths"]["coaches"] = str(coaches)
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)
    checkpoint = tmp_path / "checkpoint.json"

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    sidecar = yaml.safe_load(
        (tmp_path / "checkpoint.llm.yaml").read_text(encoding="utf-8")
    )
    joe = sidecar["buyers"]["joe"]["llm"]
    assert joe["temperature"] == 0.2
    assert joe["system_prompt"].startswith("Sei un allenatore-manager")
    assert "Joe è aggressivo." in joe["system_prompt"]
    assert "P: 3, D: 8, C: 8, A: 6" in joe["system_prompt"]


def test_cli_resume_uses_stored_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    FakeLlmClient.instances.clear()
    checkpoint = make_llm_checkpoint(tmp_path)
    payload = llm_sidecar_payload()
    payload["buyers"]["incomplete"]["llm"]["system_prompt"] = "PROMPT DAL SIDECAR"
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(tmp_path / "report.json"),
    ])

    assert exit_code == 0
    prompts = [
        client.messages_seen[0][0]["content"]
        for client in FakeLlmClient.instances
    ]
    assert prompts == ["PROMPT DAL SIDECAR"]


def test_cli_rejects_empty_buyers_without_coaches(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["buyers"] = []
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("when 'paths.coaches' is not set" in error for error in errors)


def test_cli_rejects_duplicate_buyer_and_coach_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    write_workbook(workbook, {"A": 1})
    coaches = tmp_path / "coaches"
    coaches.mkdir()
    (coaches / "coachAgent_b1.md").write_text("body", encoding="utf-8")
    config = tmp_path / "config.yaml"
    data = base_llm_config(workbook)
    data["paths"]["coaches"] = str(coaches)
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("Duplicate buyer ids" in error for error in errors)


# — .env loading —


def test_load_dotenv_sets_missing_vars_and_keeps_shell_values(monkeypatch, tmp_path):
    for name in ("TEST_DOTENV_KEY", "TEST_DOTENV_UNQUOTED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_DOTENV_EXISTING", "shell")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "export TEST_DOTENV_KEY=\"from-file\"\n"
        "TEST_DOTENV_UNQUOTED='single-quoted'\n"
        "TEST_DOTENV_EXISTING=from-file\n"
        "MALFORMED_LINE\n",
        encoding="utf-8",
    )

    cli_module._load_dotenv(env_file)

    assert os.environ["TEST_DOTENV_KEY"] == "from-file"
    assert os.environ["TEST_DOTENV_UNQUOTED"] == "single-quoted"
    assert os.environ["TEST_DOTENV_EXISTING"] == "shell"


def test_load_dotenv_missing_file_is_a_noop(tmp_path):
    cli_module._load_dotenv(tmp_path / "missing.env")


def test_cli_reads_api_key_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TEST_DOTENV_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    data["llm"]["api_key_env"] = "TEST_DOTENV_LLM_API_KEY"
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)
    (tmp_path / ".env").write_text(
        "TEST_DOTENV_LLM_API_KEY=from-dotenv\n", encoding="utf-8"
    )

    exit_code = main(["--config", str(config), "--output", str(report)])

    assert exit_code == 0
    assert report.exists()
