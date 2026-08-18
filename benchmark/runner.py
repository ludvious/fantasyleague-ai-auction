"""Benchmark subcommand: run multiple auctions and aggregate metrics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from benchmark.metrics import (
    aggregate_metrics,
    compute_run_metrics,
    csv_rows,
    print_summary_table,
    write_metrics_csv,
)
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from utils.config_loader import load_config
from utils.excel_handler import ExcelHandler
from utils.json_store import JsonStore
from utils.logger import setup_logger


def run_benchmark(
    args: argparse.Namespace,
    build_bidders: Callable[..., list[Any]],
) -> int:
    """Run `args.runs` auctions with seeds `base_seed + i` and aggregate metrics."""
    if args.runs < 1:
        logger.error("--runs must be an int >= 1")
        return 1
    config = load_config(args.config)
    simulation = config.get("simulation", {})
    paths = config.get("paths", {})
    logging_config = config.get("logging", {})
    setup_logger(
        log_level=str(logging_config.get("level", "INFO")),
        log_dir=str(paths.get("logs", "logs")),
        log_to_file=bool(logging_config.get("log_to_file", False)),
    )
    players = ExcelHandler(Path(paths["players"])).load_players()
    base_seed = args.seed if args.seed is not None else int(simulation["seed"])
    budget = int(simulation.get("budget", 500))
    buyer_configs = list(config.get("buyers", []))
    llm_config = config.get("llm")

    root = (
        Path(args.output)
        if args.output is not None
        else Path("data/benchmarks")
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    )
    run_id = root.name

    store = JsonStore()
    run_records = []
    for index in range(args.runs):
        run_name = f"run_{index + 1:03d}"
        run_dir = root / run_name
        seed_i = base_seed + index
        # Players are loaded once; deep copies keep runs independent.
        engine = AuctionEngine(
            [player.model_copy(deep=True) for player in players],
            build_bidders(
                buyer_configs,
                seed_i,
                llm_config=llm_config,
                run_dir=run_dir / "traces",
            ),
            budget=budget,
            seed=seed_i,
        )
        completed = True
        try:
            report = engine.run()
        except AuctionIncompleteError:
            report = engine.partial_report()
            completed = False
        store.save_document(report, run_dir / "report.json")
        run_records.append(
            {
                "run": run_name,
                "seed": seed_i,
                "completed": completed,
                "buyers": compute_run_metrics(
                    report.model_dump(mode="json"), buyer_configs, run_dir / "traces"
                ),
            }
        )
        logger.info("Benchmark run {} completed={}", run_name, completed)

    aggregates = aggregate_metrics([record["buyers"] for record in run_records])
    document = {
        "schema_version": 1,
        "run_id": run_id,
        "config": str(args.config),
        "runs": run_records,
        "aggregates": aggregates,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "metrics.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_metrics_csv(root / "metrics.csv", csv_rows(run_records))
    print_summary_table(aggregates)
    logger.success("Benchmark complete: {}", root)
    return 0
