"""Configuration loading and contract validation for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents.llm_agent import TOOL_SCHEMAS
from core.models import Position

LLM_TOOLS = set(TOOL_SCHEMAS)
SPENDING_ROLES = {position.value for position in Position}
SPENDING_TOLERANCE = 0.01


def validate_llm_buyer(llm: Any, index: int | str) -> None:
    if not isinstance(llm, dict):
        raise ValueError(f"'buyers[{index}].llm' must be a mapping")
    for key in ("model", "role", "personality", "system_prompt"):
        value = llm.get(key)
        if value is not None and not str(value).strip():
            raise ValueError(
                f"'buyers[{index}].llm.{key}' must be a non-empty string"
            )
    temperature = llm.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise ValueError(
            f"'buyers[{index}].llm.temperature' must be a number in [0, 2]"
        )
    max_tool_iterations = llm.get("max_tool_iterations")
    if max_tool_iterations is not None and (
        isinstance(max_tool_iterations, bool)
        or not isinstance(max_tool_iterations, int)
        or max_tool_iterations < 1
    ):
        raise ValueError(
            f"'buyers[{index}].llm.max_tool_iterations' must be an int >= 1"
        )
    tools = llm.get("tools")
    if tools is not None and (
        not isinstance(tools, list)
        or not tools
        or not set(tools) <= LLM_TOOLS
        or "submit_bid" not in set(tools)
    ):
        raise ValueError(
            f"'buyers[{index}].llm.tools' must be a non-empty subset of "
            f"{sorted(LLM_TOOLS)} and must contain 'submit_bid'"
        )
    spending_profile = llm.get("spending_profile")
    if spending_profile is not None:
        if not isinstance(spending_profile, dict) or not spending_profile:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' must be a non-empty mapping"
            )
        if not set(spending_profile) <= SPENDING_ROLES:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' keys must be a subset "
                f"of {sorted(SPENDING_ROLES)}"
            )
        shares = []
        for role, share in spending_profile.items():
            if (
                isinstance(share, bool)
                or not isinstance(share, (int, float))
                or not 0 <= share <= 1
            ):
                raise ValueError(
                    f"'buyers[{index}].llm.spending_profile.{role}' must be a "
                    "number in [0, 1]"
                )
            shares.append(float(share))
        if abs(sum(shares) - 1.0) > SPENDING_TOLERANCE:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' shares must sum to 1 "
                "(within 0.01)"
            )
    target_players = llm.get("target_players")
    if target_players is not None and (
        not isinstance(target_players, list)
        or any(
            not isinstance(target, str) or not target.strip()
            for target in target_players
        )
    ):
        raise ValueError(
            f"'buyers[{index}].llm.target_players' must be a list of non-empty strings"
        )


def validate_global_llm(llm: Any) -> None:
    if not isinstance(llm, dict):
        raise ValueError("'llm' must be a mapping")
    for key in ("base_url", "api_key_env", "model"):
        if not str(llm.get(key, "")).strip():
            raise ValueError(f"'llm.{key}' must be a non-empty string")
    temperature = llm.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise ValueError("'llm.temperature' must be a number in [0, 2]")
    timeout_seconds = llm.get("timeout_seconds")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise ValueError("'llm.timeout_seconds' must be an int > 0")
    brave = llm.get("brave")
    if not isinstance(brave, dict):
        raise ValueError("'llm.brave' must be a mapping")
    if brave.get("api_key") is not None:
        raise ValueError(
            "'llm.brave.api_key' is not supported; use 'llm.brave.api_key_env' "
            "with the environment variable name, never the key itself"
        )
    for key in ("base_url", "api_key_env"):
        if not str(brave.get(key, "")).strip():
            raise ValueError(f"'llm.brave.{key}' must be a non-empty string")


def _validate_config(config: dict[str, Any]) -> None:
    simulation = config.get("simulation", {})
    paths = config.get("paths", {})
    if not isinstance(simulation, dict):
        raise ValueError("'simulation' must be a mapping")
    seed = simulation.get("seed")
    if seed is None:
        raise ValueError("'simulation.seed' is required")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("'simulation.seed' must be an int")
    budget = simulation.get("budget")
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int) or budget < 25
    ):
        raise ValueError("'simulation.budget' must be an int >= 25")
    if not isinstance(paths, dict) or not paths.get("players"):
        raise ValueError("'paths.players' is required")
    buyers = config.get("buyers")
    if not isinstance(buyers, list) or not buyers:
        raise ValueError("'buyers' must be a non-empty list")
    for index, buyer in enumerate(buyers):
        if not isinstance(buyer, dict):
            raise ValueError(f"'buyers[{index}]' must be a mapping")
        if not str(buyer.get("id", "")).strip():
            raise ValueError(f"'buyers[{index}].id' must be a non-empty string")
        if not str(buyer.get("name", "")).strip():
            raise ValueError(f"'buyers[{index}].name' must be a non-empty string")
        strategy = str(buyer.get("strategy", "deterministic")).lower()
        if strategy not in ("deterministic", "random", "llm"):
            raise ValueError(
                f"'buyers[{index}].strategy' must be 'deterministic', 'random' or 'llm'"
            )
        if strategy == "llm":
            validate_llm_buyer(buyer.get("llm"), index)
        priority = buyer.get("priority")
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise ValueError(f"'buyers[{index}].priority' must be an int")
    if any(
        str(buyer.get("strategy", "deterministic")).lower() == "llm"
        for buyer in buyers
    ):
        validate_global_llm(config.get("llm"))


def load_config(path: Path) -> dict[str, Any]:
    """Read and validate a YAML configuration file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if not isinstance(config, dict):
        raise ValueError("Config root must be a mapping")
    _validate_config(config)
    return config


def as_file_path(value: str | Path | None, default: Path, filename: str) -> Path:
    """Treat a path as a file: directories get the given filename appended."""
    if value is None:
        return default
    path = Path(value)
    return path if path.suffix.lower() == ".json" else path / filename
