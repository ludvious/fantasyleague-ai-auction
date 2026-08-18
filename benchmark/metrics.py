"""Pure metric functions over report JSON and per-agent trace JSONL files."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

ROLES = ("P", "D", "C", "A")
ROSTER_REQUIREMENTS = {"P": 3, "D": 8, "C": 8, "A": 6}
UNIFORM_PROFILE = {"P": 0.25, "D": 0.25, "C": 0.25, "A": 0.25}

# USD per 1M tokens (input, output); unknown models yield None.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
USD_TO_EUR = 0.92

AGGREGATABLE_SCALARS = (
    "parse_rate",
    "cost_tokens",
    "cost_eur",
    "roster_complete",
    "budget_spent",
    "budget_remaining",
    "spending_distance",
    "targets_acquired",
    "duration_seconds",
    "llm_calls",
)

CSV_FIELDS = [
    "run", "seed", "completed", "buyer_id", "model", "parse_rate",
    "cost_tokens", "cost_eur", "roster_complete", "missing_roles",
    "budget_spent", "budget_remaining", "spending_distance",
    "spending_share_P", "spending_share_D", "spending_share_C",
    "spending_share_A", "targets_acquired", "duration_seconds",
    "llm_calls", "tools_search_news", "tools_submit_bid",
]


def load_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def agent_metrics(
    report: dict, buyer_config: dict, trace: list[dict] | None
) -> dict[str, Any]:
    """Per-run per-agent metrics from a report dict and the agent's trace."""
    buyer_id = str(buyer_config["id"])
    squad = report["squads"][buyer_id]
    trace = trace or []

    context_events = [e for e in trace if e["phase"] == "context"]
    bid_events = [e for e in trace if e["phase"] == "bid"]
    parse_rate = len(bid_events) / len(context_events) if context_events else 0.0

    usage = [e["content"] for e in trace if e["phase"] == "usage"]
    tokens_in = sum(u.get("prompt_tokens", 0) for u in usage)
    tokens_out = sum(u.get("completion_tokens", 0) for u in usage)
    cost_tokens = tokens_in + tokens_out
    model = (buyer_config.get("llm") or {}).get("model")
    prices = MODEL_PRICES.get(model) if model else None
    cost_eur = None
    if prices is not None:
        cost_eur = (
            (tokens_in * prices[0] + tokens_out * prices[1])
            / 1_000_000
            * USD_TO_EUR
        )

    players = squad["players"]
    counts = {role: 0 for role in ROLES}
    spent_by_role = {role: 0 for role in ROLES}
    for player in players:
        role = player["position"]
        counts[role] = counts.get(role, 0) + 1
        spent_by_role[role] = spent_by_role.get(role, 0) + (
            player.get("selling_price") or 0
        )
    missing_roles = {
        role: max(0, ROSTER_REQUIREMENTS[role] - counts.get(role, 0))
        for role in ROLES
    }
    roster_complete = len(players) == sum(ROSTER_REQUIREMENTS.values()) and all(
        value == 0 for value in missing_roles.values()
    )
    budget_initial = squad["budget_initial"]
    budget_remaining = squad["budget_remaining"]
    budget_spent = budget_initial - budget_remaining
    total_spent = sum(spent_by_role.values())
    spending_share_by_role = {
        role: round(spent_by_role[role] / total_spent, 4) if total_spent else 0.0
        for role in ROLES
    }
    llm_block = buyer_config.get("llm") or {}
    target = llm_block.get("spending_profile") or UNIFORM_PROFILE
    spending_distance = round(
        sum(
            abs(spending_share_by_role[role] - float(target.get(role, 0.0)))
            for role in ROLES
        ),
        4,
    )
    target_names = [str(name).lower() for name in llm_block.get("target_players") or []]
    owned_names = [str(player["name"]).lower() for player in players]
    targets_acquired = sum(1 for target in target_names if target in owned_names)
    tool_calls = [e["content"] for e in trace if e["phase"] == "tool_call"]
    tools_used = {
        name: sum(1 for call in tool_calls if call.get("name") == name)
        for name in ("search_news", "submit_bid")
    }
    return {
        "model": model,
        "parse_rate": parse_rate,
        "cost_tokens": cost_tokens,
        "cost_eur": cost_eur,
        "roster_complete": roster_complete,
        "missing_roles": missing_roles,
        "budget_spent": budget_spent,
        "budget_remaining": budget_remaining,
        "spending_share_by_role": spending_share_by_role,
        "spending_distance": spending_distance,
        "targets_acquired": targets_acquired,
        "duration_seconds": report["duration_seconds"],
        "llm_calls": sum(1 for e in trace if e["phase"] == "llm_call"),
        "tools_used": tools_used,
    }


def compute_run_metrics(
    report: dict, buyer_configs: list[dict], traces_dir: Path
) -> dict[str, dict]:
    return {
        str(buyer["id"]): agent_metrics(
            report,
            buyer,
            load_trace(traces_dir / f"{str(buyer['id'])}.jsonl"),
        )
        for buyer in buyer_configs
    }


def _mean_std(values: list) -> dict[str, float | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"mean": None, "std": None}
    return {
        "mean": round(statistics.mean(clean), 4),
        "std": round(statistics.pstdev(clean), 4) if len(clean) > 1 else 0.0,
    }


def aggregate_metrics(run_metrics: list[dict[str, dict]]) -> dict:
    if not run_metrics:
        return {}
    aggregates: dict = {}
    for buyer_id in run_metrics[0]:
        aggregates[buyer_id] = {}
        for metric in AGGREGATABLE_SCALARS:
            values = [run[buyer_id].get(metric) for run in run_metrics]
            if metric == "roster_complete":
                values = [1 if value else 0 for value in values]
            aggregates[buyer_id][metric] = _mean_std(values)
        for role in ROLES:
            values = [
                run[buyer_id].get("spending_share_by_role", {}).get(role, 0.0)
                for run in run_metrics
            ]
            aggregates[buyer_id][f"spending_share_{role}"] = _mean_std(values)
        for tool in ("search_news", "submit_bid"):
            values = [
                run[buyer_id].get("tools_used", {}).get(tool, 0)
                for run in run_metrics
            ]
            aggregates[buyer_id][f"tools_{tool}"] = _mean_std(values)
    return aggregates


def build_metrics_document(
    run_id: str,
    config_path: str,
    run_records: list[dict],
    aggregates: dict,
) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "config": config_path,
        "runs": run_records,
        "aggregates": aggregates,
    }


def csv_rows(run_records: list[dict]) -> list[dict]:
    rows = []
    for run in run_records:
        for buyer_id, metrics in run["buyers"].items():
            row = {
                "run": run["run"],
                "seed": run["seed"],
                "completed": run["completed"],
                "buyer_id": buyer_id,
                "model": metrics.get("model"),
                "parse_rate": metrics["parse_rate"],
                "cost_tokens": metrics["cost_tokens"],
                "cost_eur": metrics["cost_eur"],
                "roster_complete": metrics["roster_complete"],
                "missing_roles": json.dumps(metrics["missing_roles"]),
                "budget_spent": metrics["budget_spent"],
                "budget_remaining": metrics["budget_remaining"],
                "spending_distance": metrics["spending_distance"],
            }
            for role in ROLES:
                row[f"spending_share_{role}"] = metrics["spending_share_by_role"][role]
            row.update(
                {
                    "targets_acquired": metrics["targets_acquired"],
                    "duration_seconds": metrics["duration_seconds"],
                    "llm_calls": metrics["llm_calls"],
                    "tools_search_news": metrics["tools_used"]["search_news"],
                    "tools_submit_bid": metrics["tools_used"]["submit_bid"],
                }
            )
            rows.append(row)
    return rows


def write_metrics_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def print_summary_table(aggregates: dict) -> None:
    columns = (
        "parse_rate",
        "cost_eur",
        "roster_complete",
        "targets_acquired",
        "duration_seconds",
    )
    header = "".join(
        f"{column:<22}" for column in ("buyer",) + columns
    ).rstrip()
    print(header)
    for buyer_id, metrics in aggregates.items():
        cells = [buyer_id]
        for column in columns:
            stats = metrics.get(column, {})
            mean = stats.get("mean")
            std = stats.get("std")
            cells.append("-" if mean is None else f"{mean:.3f} ± {std:.3f}")
        print("".join(f"{cell:<22}" for cell in cells))
