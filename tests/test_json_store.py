import json

import pytest

from checkpoint_fixtures import make_checkpoint, make_report
from utils.json_store import JsonStore


def _write_payload(tmp_path, payload: dict) -> None:
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_report_save_writes_versioned_json(tmp_path):
    path = JsonStore().save_report(make_report(), tmp_path / "report.json")

    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["document_type"] == "auction_report"
    assert data["bid_issues"] == []


def test_checkpoint_round_trip_preserves_resume_data(tmp_path):
    source = make_checkpoint()
    path = JsonStore().save_checkpoint(source, tmp_path / "checkpoint.json")

    loaded = JsonStore().load_checkpoint(path)

    assert loaded.schema_version == 1
    assert loaded.document_type == "auction_checkpoint"
    assert loaded.players == source.players
    assert loaded.buyers == source.buyers
    assert loaded.bid_issues == source.bid_issues
    assert loaded.simulation == source.simulation
    assert loaded.resume == source.resume


def test_load_checkpoint_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonStore().load_checkpoint(tmp_path / "missing.json")


def test_load_checkpoint_rejects_malformed_json(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        JsonStore().load_checkpoint(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("document_type", "auction_report", "document type"),
        ("schema_version", 2, "schema version"),
        ("error_code", "internal_error", "pool_exhausted"),
    ],
)
def test_load_checkpoint_rejects_wrong_version_type_or_error(
    tmp_path,
    field,
    value,
    message,
):
    payload = make_checkpoint().to_dict()
    payload[field] = value
    _write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match=message):
        JsonStore().load_checkpoint(tmp_path / "checkpoint.json")


def test_load_checkpoint_rejects_missing_embedded_configuration(tmp_path):
    payload = make_checkpoint().to_dict()
    del payload["simulation"]
    _write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="checkpoint"):
        JsonStore().load_checkpoint(tmp_path / "checkpoint.json")


def test_load_checkpoint_rejects_missing_resume_state(tmp_path):
    payload = make_checkpoint().to_dict()
    del payload["players"]
    _write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="checkpoint"):
        JsonStore().load_checkpoint(tmp_path / "checkpoint.json")
