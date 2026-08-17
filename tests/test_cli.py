import json
from pathlib import Path

import pandas as pd
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


def make_checkpoint_file(tmp_path: Path, *, no_progress: bool = False) -> Path:
    checkpoint = make_pool_exhaustion_checkpoint()
    if no_progress:
        checkpoint.players[-1].position = Position.A
        checkpoint.unsold_players[0].position = Position.A
    path = tmp_path / "checkpoint.json"
    return JsonStore().save_checkpoint(checkpoint, path)


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
