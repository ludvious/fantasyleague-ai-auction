"""Excel adapter for the Fantacalcio player source."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
from loguru import logger

from core.models import Player, Position


class ExcelHandler:
    """Load source players without mutating the workbook."""

    SHEET_NAME = "Tutti"
    REQUIRED_COLUMNS = ("Id", "R", "Nome", "Squadra", "Qt.A")

    def __init__(self, filepath: str | Path):
        self.filepath = Path(filepath)

    @staticmethod
    def _normalize_id(value: object) -> str:
        if pd.isna(value):
            raise ValueError("Player ID cannot be empty")
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def validate_schema(self, frame: pd.DataFrame) -> None:
        missing = [column for column in self.REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"Missing Excel columns: {missing}")

        data = frame.loc[:, self.REQUIRED_COLUMNS].dropna(how="any")
        if data.empty:
            raise ValueError("Excel contains no player rows")

        roles = data["R"].astype(str).str.strip()
        valid_roles = {position.value for position in Position}
        invalid_roles = sorted(set(roles) - valid_roles)
        if invalid_roles:
            raise ValueError(f"Invalid player roles: {invalid_roles}")

        normalized_ids = data["Id"].map(self._normalize_id)
        if normalized_ids.eq("").any():
            raise ValueError("Player IDs cannot be empty")
        if normalized_ids.duplicated().any():
            duplicates = sorted(normalized_ids[normalized_ids.duplicated()].unique())
            raise ValueError(f"Duplicate player IDs: {duplicates}")

        for column in ("Nome", "Squadra"):
            if data[column].astype(str).str.strip().eq("").any():
                raise ValueError(f"Player column {column} contains an empty value")

    def _read(self) -> pd.DataFrame:
        if not self.filepath.exists():
            raise FileNotFoundError(f"Excel file not found: {self.filepath}")
        return pd.read_excel(self.filepath, sheet_name=self.SHEET_NAME, header=1)

    def load_players(self) -> list[Player]:
        """Load all valid players from the real header row of the workbook."""
        logger.info("Loading players from {}", self.filepath)
        frame = self._read()
        self.validate_schema(frame)
        data = frame.loc[:, self.REQUIRED_COLUMNS].dropna(how="any")

        players: list[Player] = []
        for row_number, row in data.iterrows():
            try:
                players.append(
                    Player(
                        id=self._normalize_id(row["Id"]),
                        name=str(row["Nome"]).strip(),
                        position=Position(str(row["R"]).strip()),
                        team=str(row["Squadra"]).strip(),
                        list_price=int(float(row["Qt.A"])),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid player row {row_number + 2}: {exc}") from exc

        logger.info("Loaded {} players", len(players))
        return players
