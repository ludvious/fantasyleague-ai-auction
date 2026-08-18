import json

import pytest

from benchmark.metrics import (
    agent_metrics,
    aggregate_metrics,
    compute_run_metrics,
    csv_rows,
    write_metrics_csv,
)

REPORT = {
    "duration_seconds": 12.5,
    "squads": {
        "buyer_1": {
            "budget_initial": 500,
            "budget_remaining": 400,
            "players": [
                {"id": "p1", "name": "Portiere Uno", "position": "P", "selling_price": 10},
                {"id": "p2", "name": "Lautaro Martínez", "position": "A", "selling_price": 90},
            ],
        },
    },
}

BUYER = {
    "id": "buyer_1",
    "name": "Alpha",
    "strategy": "llm",
    "llm": {
        "model": "gpt-4o-mini",
        "spending_profile": {"P": 0.1, "D": 0.2, "C": 0.3, "A": 0.4},
        "target_players": ["lautaro martínez"],
    },
}

TRACE_EVENTS = [
    {"phase": "context"},
    {"phase": "llm_call"},
    {"phase": "usage", "content": {"prompt_tokens": 100, "completion_tokens": 50}},
    {"phase": "tool_call", "content": {"name": "search_news", "args": {}}},
    {"phase": "bid", "content": {"amount": 10}},
    {"phase": "context"},
    {"phase": "no_bid"},
]


def test_agent_metrics_computed_from_report_and_trace():
    metrics = agent_metrics(REPORT, BUYER, TRACE_EVENTS)

    assert metrics["parse_rate"] == 0.5  # 1 bid / 2 contexts
    assert metrics["cost_tokens"] == 150
    assert metrics["cost_eur"] == pytest.approx(45 / 1_000_000 * 0.92)
    assert metrics["roster_complete"] is False
    assert metrics["missing_roles"] == {"P": 2, "D": 8, "C": 8, "A": 5}
    assert metrics["budget_spent"] == 100
    assert metrics["budget_remaining"] == 400
    assert metrics["spending_share_by_role"] == pytest.approx(
        {"P": 0.1, "D": 0.0, "C": 0.0, "A": 0.9}
    )
    assert metrics["spending_distance"] == pytest.approx(1.0)
    assert metrics["targets_acquired"] == 1  # case-insensitive match
    assert metrics["duration_seconds"] == 12.5
    assert metrics["llm_calls"] == 1
    assert metrics["tools_used"] == {"search_news": 1, "submit_bid": 0}
    assert metrics["model"] == "gpt-4o-mini"


def test_unknown_model_yields_null_cost():
    buyer = {
        "id": "buyer_1",
        "name": "Alpha",
        "strategy": "llm",
        "llm": {"model": "misterioso-1"},
    }
    metrics = agent_metrics(REPORT, buyer, TRACE_EVENTS)

    assert metrics["cost_eur"] is None


def test_absent_profile_uses_uniform_target_and_no_trace_is_safe():
    buyer = {"id": "buyer_1", "name": "Alpha", "strategy": "llm"}
    metrics = agent_metrics(REPORT, buyer, None)

    assert metrics["parse_rate"] == 0.0
    assert metrics["cost_tokens"] == 0
    assert metrics["llm_calls"] == 0
    assert metrics["spending_distance"] == pytest.approx(1.3)
    assert metrics["targets_acquired"] == 0


def test_compute_run_metrics_reads_trace_files(tmp_path):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "buyer_1.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in TRACE_EVENTS),
        encoding="utf-8",
    )

    metrics = compute_run_metrics(REPORT, [BUYER], traces_dir)

    assert metrics["buyer_1"]["parse_rate"] == 0.5


def test_aggregate_metrics_computes_mean_and_std():
    run_1 = {"b1": {"parse_rate": 0.5, "roster_complete": False, "cost_eur": 0.1, "spending_share_by_role": {"P": 0.5, "D": 0.5, "C": 0.0, "A": 0.0}, "tools_used": {"search_news": 1, "submit_bid": 2}}}
    run_2 = {"b1": {"parse_rate": 0.7, "roster_complete": True, "cost_eur": 0.3, "spending_share_by_role": {"P": 0.3, "D": 0.3, "C": 0.2, "A": 0.2}, "tools_used": {"search_news": 0, "submit_bid": 1}}}

    aggregates = aggregate_metrics([run_1, run_2])

    assert aggregates["b1"]["parse_rate"] == {"mean": 0.6, "std": 0.1}
    assert aggregates["b1"]["roster_complete"] == {"mean": 0.5, "std": 0.5}
    assert aggregates["b1"]["cost_eur"] == {"mean": 0.2, "std": 0.1}
    assert aggregates["b1"]["spending_share_P"]["mean"] == 0.4
    assert aggregates["b1"]["tools_search_news"]["mean"] == 0.5


def test_aggregate_metrics_skips_null_costs():
    run_1 = {"b1": {"cost_eur": 0.1}}
    run_2 = {"b1": {"cost_eur": None}}

    aggregates = aggregate_metrics([run_1, run_2])

    assert aggregates["b1"]["cost_eur"] == {"mean": 0.1, "std": 0.0}


def test_csv_rows_and_writer(tmp_path):
    run_records = [
        {
            "run": "run_001",
            "seed": 42,
            "completed": True,
            "buyers": {"buyer_1": agent_metrics(REPORT, BUYER, TRACE_EVENTS)},
        }
    ]
    rows = csv_rows(run_records)
    assert rows[0]["buyer_id"] == "buyer_1"
    assert rows[0]["run"] == "run_001"
    assert rows[0]["missing_roles"] == '{"P": 2, "D": 8, "C": 8, "A": 5}'
    assert rows[0]["tools_search_news"] == 1

    path = tmp_path / "metrics.csv"
    write_metrics_csv(path, rows)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",")[0] == "run"
    assert "buyer_id" in lines[0]
    assert len(lines) == 2
