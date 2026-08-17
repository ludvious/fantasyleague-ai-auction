"""JSON output adapter for reports and failure checkpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


class JsonStore:
    """Serialize domain objects or plain dictionaries to JSON files."""

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return JsonStore._jsonable(value.model_dump(mode="json"))
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): JsonStore._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [JsonStore._jsonable(item) for item in value]
        return value

    @staticmethod
    def _write(payload: Any, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(JsonStore._jsonable(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def save_report(self, report: Any, path: str | Path) -> Path:
        return self._write(report, path)

    def save_checkpoint(
        self,
        state: Any,
        path: str | Path,
        error: str | Exception | None = None,
        missing_roles: dict[str, dict[str, int]] | None = None,
    ) -> Path:
        payload = self._jsonable(state)
        if not isinstance(payload, dict):
            payload = {"state": payload}

        if missing_roles is None:
            source_squads = state.get("squads", {}) if isinstance(state, dict) else getattr(state, "squads", {})
            missing_roles = {}
            for buyer_id, squad in source_squads.items():
                roles = squad.get("missing_roles") if isinstance(squad, dict) else squad.missing_roles()
                if roles and any(roles.values()):
                    missing_roles[str(buyer_id)] = roles

        if missing_roles:
            payload["missing_roles"] = missing_roles
            squads = payload.get("squads")
            if isinstance(squads, dict):
                for buyer_id, roles in missing_roles.items():
                    if isinstance(squads.get(buyer_id), dict):
                        squads[buyer_id]["missing_roles"] = roles
        if error is not None:
            payload["error"] = str(error)
        return self._write(payload, path)
