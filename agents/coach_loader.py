"""Discovery and validation of CoachAgent markdown profiles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from utils.config_loader import validate_llm_buyer

COACH_GLOB = "coachAgent_*.md"
FRONT_MATTER_FIELDS = (
    "id",
    "name",
    "model",
    "role",
    "personality",
    "temperature",
    "max_tool_iterations",
    "max_bid_retries",
    "tools",
    "spending_profile",
    "target_players",
    "system_prompt",
)


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{path}: front-matter opened but not closed")
    raw = text[3:end].strip()
    body = text[end + 4:].strip()
    data = yaml.safe_load(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: front-matter must be a mapping")
    unknown = set(data) - set(FRONT_MATTER_FIELDS)
    if unknown:
        raise ValueError(f"{path}: unknown front-matter fields {sorted(unknown)}")
    return data, body


def _derive_id(path: Path) -> str:
    stem = path.stem
    if stem.startswith("coachAgent_"):
        stem = stem[len("coachAgent_"):]
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _derive_name(path: Path) -> str:
    return path.stem.removeprefix("coachAgent_")


def load_coaches(directory: str | Path) -> list[dict[str, Any]]:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Coaches directory not found: {directory}")
    coaches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob(COACH_GLOB)):
        data, profile = _split_front_matter(
            path.read_text(encoding="utf-8"), path
        )
        buyer_id = str(data.get("id") or _derive_id(path))
        name = str(data.get("name") or _derive_name(path))
        if not buyer_id or not name:
            raise ValueError(f"{path}: id and name must be non-empty")
        if buyer_id in seen:
            raise ValueError(f"{path}: duplicate coach id '{buyer_id}'")
        seen.add(buyer_id)
        llm = {key: data[key] for key in data if key not in ("id", "name")}
        if "system_prompt" in llm and not str(llm["system_prompt"]).strip():
            raise ValueError(f"{path}: system_prompt must be non-empty")
        validate_llm_buyer(llm, str(path))
        coaches.append(
            {
                "id": buyer_id,
                "name": name,
                "llm": llm,
                "profile": profile,
            }
        )
    return coaches


def load_buyer_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    buyers = list(config.get("buyers") or [])
    coaches_dir = (config.get("paths") or {}).get("coaches")
    if not coaches_dir:
        return buyers
    coaches = load_coaches(coaches_dir)
    yaml_ids = {str(buyer.get("id", "")).strip() for buyer in buyers}
    duplicates = sorted(yaml_ids & {coach["id"] for coach in coaches})
    if duplicates:
        raise ValueError(
            f"Duplicate buyer ids between config and coaches: {duplicates}"
        )
    return buyers + coaches
