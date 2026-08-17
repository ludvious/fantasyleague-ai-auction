import json
from pathlib import Path

import pandas as pd
import yaml

from main import main


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
    path.write_text(
        yaml.safe_dump(
            {
                "simulation": {"budget": 500, "seed": 42},
                "paths": {"players": str(workbook)},
                "buyers": buyers,
            }
        ),
        encoding="utf-8",
    )


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
    assert data["error"]
    assert data["missing_roles"]["b1"]["P"] == 3
