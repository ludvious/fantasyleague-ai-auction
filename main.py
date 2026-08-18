"""Command-line entry point for a deterministic auction simulation."""

from __future__ import annotations

import argparse
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
from benchmark.runner import run_benchmark
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from core.models import BidderSnapshot, SimulationSnapshot
from utils.config_loader import (
    as_file_path,
    load_config,
    validate_global_llm,
    validate_llm_buyer,
)
from utils.excel_handler import ExcelHandler
from utils.json_store import JsonStore
from utils.logger import setup_logger

DEFAULT_CONFIG = Path("configs/default.yaml")


def _trace_run_dir(logs_dir: str | Path | None) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return Path(logs_dir or "logs") / "traces" / run_id


def _make_llm_client(llm_config: dict[str, Any]) -> LlmClient:
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Environment variable '{api_key_env}' (llm.api_key_env) is not set; "
            "set it before running an auction with LLM bidders"
        )
    brave = llm_config.get("brave") or {}
    brave_api_key = os.environ.get(str(brave.get("api_key_env", "")), "")
    return LlmClient(
        base_url=str(llm_config["base_url"]),
        api_key=api_key,
        brave_base_url=str(brave["base_url"]),
        brave_api_key=brave_api_key,
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
                    max_tool_iterations=int(merged.get("max_tool_iterations", 3)),
                    tools=tuple(merged.get("tools", AgentManager.DEFAULT_TOOLS)),
                    spending_profile=merged.get("spending_profile"),
                    target_players=merged.get("target_players"),
                )
            )
        else:
            raise ValueError(f"Unknown bidder strategy: {strategy}")
    return bidders


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
        validate_global_llm(sidecar.get("llm"))
        for buyer_id, entry in buyers.items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"'buyers.{buyer_id}' must be a mapping"
                )
            validate_llm_buyer(entry.get("llm"), str(buyer_id))
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
    subparsers = parser.add_subparsers(dest="command")
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run multiple auctions and aggregate per-agent metrics",
    )
    benchmark_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    benchmark_parser.add_argument("--runs", type=int, default=5)
    benchmark_parser.add_argument("--seed", type=int)
    benchmark_parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "benchmark":
        return run_benchmark(args, _build_bidders)
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
            buyer_configs = [
                {
                    "id": snapshot.id,
                    "name": snapshot.name,
                    "strategy": snapshot.strategy,
                    "priority": snapshot.priority,
                }
                for snapshot in buyer_snapshots
            ]
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
            output_path = as_file_path(
                args.output,
                Path("data/results/report.json"),
                "report.json",
            )
            checkpoint_path = as_file_path(
                args.checkpoint,
                args.resume,
                "checkpoint.json",
            )
        else:
            config = load_config(args.config)
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
            output_path = as_file_path(
                args.output or paths.get("output"),
                Path("data/results/report.json"),
                "report.json",
            )
            checkpoint_path = as_file_path(
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
            buyer_snapshots = [
                BidderSnapshot(
                    id=str(config.get("id", "")).strip(),
                    name=str(config.get("name", "")).strip(),
                    strategy=str(config.get("strategy", "deterministic")).lower(),
                    priority=int(config.get("priority", index)),
                )
                for index, config in enumerate(buyer_configs)
            ]
            engine = AuctionEngine(players, bidders, budget=budget, seed=seed)

        report = engine.run()
        saved = store.save_document(report, output_path)
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
        saved = store.save_document(
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
