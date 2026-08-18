"""Command-line entry point for a deterministic auction simulation."""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from agents.buyer_agent import DeterministicBidder, RandomBidder
from agents.llm_agent import AgentManager, LlmClient
from agents.trace import TraceLogger
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from core.models import BidderSnapshot, SimulationSnapshot
from utils.excel_handler import ExcelHandler
from utils.json_store import JsonStore
from utils.logger import setup_logger

DEFAULT_CONFIG = Path("configs/default.yaml")

LLM_TOOLS = {"search_news", "submit_bid"}
SPENDING_ROLES = {"P", "D", "C", "A"}
SPENDING_TOLERANCE = 0.01


def _validate_llm_buyer(llm: Any, index: int | str) -> None:
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


def _validate_global_llm(llm: Any) -> None:
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
    for key in ("base_url", "api_key"):
        if not str(brave.get(key, "")).strip():
            raise ValueError(f"'llm.brave.{key}' must be a non-empty string")


def _trace_run_dir(logs_dir: str | Path | None) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return Path(logs_dir or "logs") / "traces" / run_id


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if not isinstance(config, dict):
        raise ValueError("Config root must be a mapping")
    _validate_config(config)
    return config


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
            _validate_llm_buyer(buyer.get("llm"), index)
        priority = buyer.get("priority")
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise ValueError(f"'buyers[{index}].priority' must be an int")
    if any(
        str(buyer.get("strategy", "deterministic")).lower() == "llm"
        for buyer in buyers
    ):
        _validate_global_llm(config.get("llm"))


def _as_file_path(value: str | Path | None, default: Path, filename: str) -> Path:
    if value is None:
        return default
    path = Path(value)
    return path if path.suffix.lower() == ".json" else path / filename


def _make_llm_client(llm_config: dict[str, Any]) -> LlmClient:
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Environment variable '{api_key_env}' (llm.api_key_env) is not set; "
            "set it before running an auction with LLM bidders"
        )
    brave = llm_config.get("brave") or {}
    return LlmClient(
        base_url=str(llm_config["base_url"]),
        api_key=api_key,
        brave_base_url=str(brave["base_url"]),
        brave_api_key=str(brave["api_key"]),
        timeout_seconds=int(llm_config.get("timeout_seconds", 30)),
    )


def _build_bidders(
    configs: list[dict[str, Any]],
    seed: int | None,
    llm_config: dict[str, Any] | None = None,
    run_dir: Path | None = None,
):
    if not configs:
        raise ValueError("At least one buyer must be configured")

    llm_client: LlmClient | None = None
    bidders = []
    for index, config in enumerate(configs):
        buyer_id = str(config.get("id", "")).strip()
        name = str(config.get("name", "")).strip()
        strategy = str(config.get("strategy", "deterministic")).lower()
        if strategy == "deterministic":
            bidders.append(
                DeterministicBidder(
                    buyer_id,
                    name,
                    priority=int(config.get("priority", index)),
                )
            )
        elif strategy == "random":
            bidder_seed = None if seed is None else seed + index
            bidders.append(RandomBidder(buyer_id, name, random.Random(bidder_seed)))
        elif strategy == "llm":
            if llm_client is None:
                # Constructed once and shared: httpx clients are thread-safe.
                llm_client = _make_llm_client(llm_config or {})
            if run_dir is None:
                raise ValueError("A trace run_dir is required for LLM bidders")
            merged = {**(llm_config or {}), **(config.get("llm") or {})}
            bidders.append(
                AgentManager(
                    buyer_id,
                    name,
                    client=llm_client,
                    tracer=TraceLogger(run_dir, buyer_id),
                    model=str(merged["model"]),
                    temperature=float(merged.get("temperature", 0.7)),
                    role=merged.get("role"),
                    personality=merged.get("personality"),
                    system_prompt=merged.get("system_prompt"),
                    max_tool_iterations=int(merged.get("max_tool_iterations", 8)),
                    tools=tuple(merged.get("tools", AgentManager.DEFAULT_TOOLS)),
                    spending_profile=merged.get("spending_profile"),
                    target_players=merged.get("target_players"),
                )
            )
        else:
            raise ValueError(f"Unknown bidder strategy: {strategy}")
    return bidders


def _buyer_snapshots(configs: list[dict[str, Any]]) -> list[BidderSnapshot]:
    return [
        BidderSnapshot(
            id=str(config.get("id", "")).strip(),
            name=str(config.get("name", "")).strip(),
            strategy=str(config.get("strategy", "deterministic")).lower(),
            priority=int(config.get("priority", index)),
        )
        for index, config in enumerate(configs)
    ]


def _snapshot_configs(snapshots: list[BidderSnapshot]) -> list[dict[str, Any]]:
    return [
        {
            "id": snapshot.id,
            "name": snapshot.name,
            "strategy": snapshot.strategy,
            "priority": snapshot.priority,
        }
        for snapshot in snapshots
    ]


def _sidecar_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_suffix(".llm.yaml")


def _write_llm_sidecar(
    checkpoint_path: Path,
    buyer_configs: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> Path | None:
    """Write the LLM sidecar next to a checkpoint; None when no llm buyer."""
    llm_buyers = [
        buyer
        for buyer in buyer_configs
        if str(buyer.get("strategy", "")).lower() == "llm"
    ]
    if not llm_buyers:
        return None
    payload = {
        "schema_version": 1,
        "llm": llm_config,
        # Per-buyer blocks, only where present in the config; api_key_env is
        # a variable name, never the key itself.
        "buyers": {
            str(buyer["id"]): {"llm": buyer["llm"]}
            for buyer in llm_buyers
            if buyer.get("llm")
        },
    }
    path = _sidecar_path(checkpoint_path)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _load_llm_sidecar(checkpoint_path: Path) -> dict[str, Any]:
    path = _sidecar_path(checkpoint_path)
    if not path.exists():
        raise ValueError(
            f"LLM sidecar missing: {path}; checkpoints with LLM buyers "
            "cannot be resumed without it"
        )
    with path.open(encoding="utf-8") as stream:
        sidecar = yaml.safe_load(stream) or {}
    if not isinstance(sidecar, dict):
        raise ValueError(f"Invalid LLM sidecar {path}: root must be a mapping")
    if sidecar.get("schema_version") != 1:
        raise ValueError(f"Invalid LLM sidecar {path}: schema_version must be 1")
    buyers = sidecar.get("buyers") or {}
    if not isinstance(buyers, dict):
        raise ValueError(f"Invalid LLM sidecar {path}: 'buyers' must be a mapping")
    try:
        _validate_global_llm(sidecar.get("llm"))
        for buyer_id, entry in buyers.items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"'buyers.{buyer_id}' must be a mapping"
                )
            _validate_llm_buyer(entry.get("llm"), str(buyer_id))
    except ValueError as exc:
        raise ValueError(f"Invalid LLM sidecar {path}: {exc}") from exc
    return sidecar


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a non-interactive fantasy auction")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--players", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--seed", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine: AuctionEngine | None = None
    simulation_snapshot: SimulationSnapshot | None = None
    buyer_snapshots: list[BidderSnapshot] | None = None
    checkpoint_path: Path | None = None
    llm_config: dict[str, Any] | None = None
    buyer_configs: list[dict[str, Any]] = []
    store = JsonStore()

    try:
        if args.resume is not None:
            setup_logger()
            source = store.load_checkpoint(args.resume)
            simulation_snapshot = source.simulation
            buyer_snapshots = [
                buyer.model_copy(deep=True) for buyer in source.buyers
            ]
            buyer_configs = _snapshot_configs(buyer_snapshots)
            llm_config = None
            if any(snapshot.strategy == "llm" for snapshot in buyer_snapshots):
                sidecar = _load_llm_sidecar(args.resume)
                llm_config = sidecar["llm"]
                per_buyer = sidecar.get("buyers") or {}
                for config in buyer_configs:
                    if config["strategy"] == "llm":
                        entry = per_buyer.get(config["id"], {})
                        config["llm"] = entry.get("llm") or {}
            bidders = _build_bidders(
                buyer_configs,
                source.simulation.seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(None),
            )
            engine = AuctionEngine.from_checkpoint(source, bidders)
            output_path = _as_file_path(
                args.output,
                Path("data/results/report.json"),
                "report.json",
            )
            checkpoint_path = _as_file_path(
                args.checkpoint,
                args.resume,
                "checkpoint.json",
            )
        else:
            config = _load_config(args.config)
            simulation = config.get("simulation", {})
            paths = config.get("paths", {})
            logging_config = config.get("logging", {})
            setup_logger(
                log_level=str(logging_config.get("level", "INFO")),
                log_dir=str(paths.get("logs", "logs")),
                log_to_file=bool(logging_config.get("log_to_file", False)),
            )

            budget = int(simulation.get("budget", 500))
            seed = args.seed if args.seed is not None else simulation["seed"]
            players_path = args.players or Path(paths["players"])
            output_path = _as_file_path(
                args.output or paths.get("output"),
                Path("data/results/report.json"),
                "report.json",
            )
            checkpoint_path = _as_file_path(
                args.checkpoint or paths.get("checkpoint"),
                Path("data/checkpoints/checkpoint.json"),
                "checkpoint.json",
            )

            players = ExcelHandler(players_path).load_players()
            buyer_configs = list(config.get("buyers", []))
            llm_config = config.get("llm")
            bidders = _build_bidders(
                buyer_configs,
                seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(paths.get("logs")),
            )
            simulation_snapshot = SimulationSnapshot(budget=budget, seed=seed)
            buyer_snapshots = _buyer_snapshots(buyer_configs)
            engine = AuctionEngine(players, bidders, budget=budget, seed=seed)

        report = engine.run()
        saved = store.save_report(report, output_path)
        logger.success("Report saved to {}", saved)
        return 0
    except AuctionIncompleteError as exc:
        if engine is None or simulation_snapshot is None or buyer_snapshots is None:
            logger.error(str(exc))
            return 1
        checkpoint = engine.build_checkpoint(
            simulation_snapshot,
            buyer_snapshots,
            exc,
            exc.missing_roles,
        )
        saved = store.save_checkpoint(
            checkpoint,
            checkpoint_path or Path("data/checkpoints/checkpoint.json"),
        )
        if llm_config is not None:
            try:
                sidecar_path = _write_llm_sidecar(
                    saved, buyer_configs, llm_config
                )
                if sidecar_path is not None:
                    logger.info("LLM sidecar saved to {}", sidecar_path)
            except OSError as write_exc:
                logger.error(
                    "Failed to write LLM sidecar next to {}: {}",
                    saved,
                    write_exc,
                )
        logger.error("{}; checkpoint saved to {}", exc, saved)
        return 1
    except Exception as exc:
        logger.error("Auction failed: {}", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
