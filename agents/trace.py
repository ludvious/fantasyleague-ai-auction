"""Per-agent JSONL trace logger, separate from the app logger and documents."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
    """Append one JSON object per event to <run_dir>/<buyer_id>.jsonl."""

    def __init__(self, run_dir: str | Path, buyer_id: str):
        self.buyer_id = buyer_id
        self.path = Path(run_dir) / f"{buyer_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(
        self,
        player_id: str,
        phase: str,
        iteration: int | None = None,
        content: Any = None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "buyer_id": self.buyer_id,
            "player_id": player_id,
            "phase": phase,
        }
        if iteration is not None:
            record["iteration"] = iteration
        if content is not None:
            record["content"] = content
        # Open-append-close per event: flush-safe, no open handle leaks.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
