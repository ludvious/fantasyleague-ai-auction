import json

from agents.trace import TraceLogger


def test_trace_logger_appends_one_json_object_per_event(tmp_path):
    tracer = TraceLogger(tmp_path / "traces", "buyer_1")
    tracer.event("pl_1", "context", content={"player": "Lautaro"})
    tracer.event("pl_1", "bid", iteration=2, content={"amount": 12})
    tracer.event(
        "pl_1",
        "usage",
        iteration=2,
        content={"prompt_tokens": 5, "completion_tokens": 3},
    )

    lines = (tmp_path / "traces" / "buyer_1.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]

    assert len(records) == 3
    assert records[0]["buyer_id"] == "buyer_1"
    assert records[0]["player_id"] == "pl_1"
    assert records[0]["phase"] == "context"
    assert records[0]["content"] == {"player": "Lautaro"}
    assert "ts" in records[0]
    assert "iteration" not in records[0]
    assert records[1]["iteration"] == 2
    assert records[1]["content"] == {"amount": 12}


def test_trace_logger_creates_missing_directories(tmp_path):
    tracer = TraceLogger(tmp_path / "deep" / "nested" / "traces", "buyer_2")
    tracer.event("pl_1", "no_bid", content={"reason": "iteration_cap"})
    assert (tmp_path / "deep" / "nested" / "traces" / "buyer_2.jsonl").exists()
