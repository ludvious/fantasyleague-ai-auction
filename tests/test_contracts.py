from datetime import datetime, timezone

from checkpoint_fixtures import make_checkpoint, make_report


def test_report_has_version_and_both_timing_scopes():
    report = make_report()

    assert report.schema_version == 1
    assert report.document_type == "auction_report"
    assert report.duration_seconds >= report.last_run_duration_seconds


def test_checkpoint_contains_autonomous_configuration():
    checkpoint = make_checkpoint()

    assert checkpoint.document_type == "auction_checkpoint"
    assert checkpoint.simulation.budget == 500
    assert checkpoint.buyers[0].strategy == "deterministic"
    assert checkpoint.error_code == "pool_exhausted"


def test_checkpoint_serializes_enums_and_datetimes_as_json_values():
    data = make_checkpoint().model_dump(mode="json")

    timestamp = data["timestamp_start"].replace("Z", "+00:00")
    assert datetime.fromisoformat(timestamp).tzinfo == timezone.utc
    assert data["players"][0]["status"] == "invenduto"
    assert data["resume"] == {"incomplete_buyer_ids": ["buyer_1"]}


def test_report_keeps_json_and_aggregate_helpers():
    report = make_report()

    assert report.model_dump(mode="json")["document_type"] == "auction_report"
    assert report.total_spent == 0
    assert report.max_price == 0
