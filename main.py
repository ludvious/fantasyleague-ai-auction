"""Command-line entry point for a deterministic auction simulation."""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from agents.buyer_agent import DeterministicBidder, RandomBidder
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from utils.excel_handler import ExcelHandler
from utils.json_store import JsonStore
from utils.logger import setup_logger

DEFAULT_CONFIG = Path("configs/default.yaml")
DEFAULT_PLAYERS = Path("data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx")


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if not isinstance(config, dict):
        raise ValueError("Config root must be a mapping")
    return config


def _as_file_path(value: str | Path | None, default: Path, filename: str) -> Path:
    if value is None:
        return default
    path = Path(value)
    return path if path.suffix.lower() == ".json" else path / filename


def _build_bidders(configs: list[dict[str, Any]], seed: int | None):
    if not configs:
        raise ValueError("At least one buyer must be configured")

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
        else:
            raise ValueError(f"Unknown bidder strategy: {strategy}")
    return bidders


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a non-interactive fantasy auction")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--players", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--seed", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine: AuctionEngine | None = None

    try:
        config = _load_config(args.config)
        simulation = config.get("simulation", {})
        paths = config.get("paths", {})
        logging_config = config.get("logging", {})
        setup_logger(
            log_level=str(logging_config.get("level", "INFO")),
            log_dir=str(paths.get("logs", "logs")),
            log_to_file=bool(logging_config.get("log_to_file", False)),
        )

        budget = int(simulation.get("budget", simulation.get("budget_iniziale", 500)))
        seed = args.seed if args.seed is not None else simulation.get("seed")
        players_path = args.players or Path(
            paths.get("players", paths.get("database", DEFAULT_PLAYERS))
        )
        output_path = _as_file_path(
            args.output or paths.get("output"),
            Path("data/results/report.json"),
            "report.json",
        )
        checkpoint_path = _as_file_path(
            args.checkpoint or paths.get("checkpoint") or paths.get("checkpoints"),
            Path("data/checkpoints/checkpoint.json"),
            "checkpoint.json",
        )

        players = ExcelHandler(players_path).load_players()
        bidders = _build_bidders(list(config.get("buyers", [])), seed)
        engine = AuctionEngine(players, bidders, budget=budget, seed=seed)
        report = engine.run()
        saved = JsonStore().save_report(report, output_path)
        logger.success("Report saved to {}", saved)
        return 0
    except AuctionIncompleteError as exc:
        if engine is not None:
            checkpoint_path = locals().get("checkpoint_path", Path("data/checkpoints/checkpoint.json"))
            saved = JsonStore().save_checkpoint(
                engine.state,
                checkpoint_path,
                error=exc,
                missing_roles=exc.missing_roles,
            )
            logger.error("{}; checkpoint saved to {}", exc, saved)
        else:
            logger.error(str(exc))
        return 1
    except Exception as exc:
        if engine is not None:
            checkpoint_path = locals().get("checkpoint_path", Path("data/checkpoints/checkpoint.json"))
            JsonStore().save_checkpoint(engine.state, checkpoint_path, error=exc)
        logger.error("Auction failed: {}", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
