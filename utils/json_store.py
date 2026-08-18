"""Versioned JSON persistence for auction reports and checkpoints."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from core.models import AuctionCheckpoint, SimulationReport


class JsonStore:
    """Serialize and validate versioned auction documents."""

    @staticmethod
    def _write(payload: dict, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def save_document(self, document: SimulationReport, path: str | Path) -> Path:
        if not isinstance(document, SimulationReport):
            raise TypeError("document must be a SimulationReport or AuctionCheckpoint")
        return self._write(document.model_dump(mode="json"), path)

    def load_checkpoint(self, path: str | Path) -> AuctionCheckpoint:
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON checkpoint: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError("Checkpoint JSON must contain an object")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("Unsupported checkpoint schema version")
        if payload.get("document_type") != "auction_checkpoint":
            raise ValueError("Invalid checkpoint document type")
        if payload.get("error_code") != "pool_exhausted":
            raise ValueError("Checkpoint is not resumable: pool_exhausted required")

        try:
            return AuctionCheckpoint.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Invalid auction checkpoint: {exc}") from exc
