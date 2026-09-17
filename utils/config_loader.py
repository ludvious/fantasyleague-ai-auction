"""Configuration loading and contract validation for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents.llm_client import TOOL_SCHEMAS
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
    max_bid_retries = llm.get("max_bid_retries")
    if max_bid_retries is not None and (
        isinstance(max_bid_retries, bool)
        or not isinstance(max_bid_retries, int)
        or max_bid_retries < 0
    ):
        raise ValueError(
            f"'buyers[{index}].llm.max_bid_retries' must be an int >= 0"
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


SEARCH_PROVIDERS = ("responses", "anthropic", "brave")


def _validate_header_map(headers: Any, where: str) -> None:
    if not isinstance(headers, dict):
        raise ValueError(f"'{where}' must be a mapping")
    for name, value in headers.items():
        if not str(name).strip() or not str(value).strip():
            raise ValueError(f"'{where}' entries must be non-empty strings")


def _validate_search_block(search: Any) -> None:
    if not isinstance(search, dict):
        raise ValueError("'llm.search' must be a mapping")
    if search.get("api_key") is not None:
        raise ValueError(
            "'llm.search.api_key' is not supported; use 'llm.search.api_key_env' "
            "with the environment variable name, never the key itself"
        )
    provider = str(search.get("provider", "")).strip()
    if provider not in SEARCH_PROVIDERS:
        raise ValueError(
            f"'llm.search.provider' must be one of {list(SEARCH_PROVIDERS)}"
        )
    if provider != "brave" and not str(search.get("model", "")).strip():
        raise ValueError(
            "'llm.search.model' must be a non-empty string for provider "
            f"'{provider}'"
        )
    for key in ("base_url", "api_key_env"):
        value = search.get(key)
        if value is not None and not str(value).strip():
            raise ValueError(f"'llm.search.{key}' must be a non-empty string")
    max_output_tokens = search.get("max_output_tokens")
    if max_output_tokens is not None and (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or max_output_tokens < 1
    ):
        raise ValueError("'llm.search.max_output_tokens' must be an int > 0")
    if search.get("headers") is not None:
        _validate_header_map(search["headers"], "llm.search.headers")


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
    search = llm.get("search")
    brave = llm.get("brave")
    if search is not None and brave is not None:
        raise ValueError(
            "'llm.search' and 'llm.brave' are mutually exclusive; "
            "'llm.brave' is the legacy search block"
        )
    if search is not None:
        _validate_search_block(search)
    elif brave is not None:
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
    if llm.get("headers") is not None:
        _validate_header_map(llm["headers"], "llm.headers")


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
    coaches = paths.get("coaches") if isinstance(paths, dict) else None
    if coaches is not None and not str(coaches).strip():
        raise ValueError("'paths.coaches' must be a non-empty string")
    if buyers is not None and not isinstance(buyers, list):
        raise ValueError("'buyers' must be a non-empty list")
    buyer_list = buyers or []
    if not buyer_list and not coaches:
        raise ValueError(
            "'buyers' must be a non-empty list when 'paths.coaches' is not set"
        )
    for index, buyer in enumerate(buyer_list):
        if not isinstance(buyer, dict):
            raise ValueError(f"'buyers[{index}]' must be a mapping")
        if not str(buyer.get("id", "")).strip():
            raise ValueError(f"'buyers[{index}].id' must be a non-empty string")
        if not str(buyer.get("name", "")).strip():
            raise ValueError(f"'buyers[{index}].name' must be a non-empty string")
        validate_llm_buyer(buyer.get("llm") or {}, index)
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
