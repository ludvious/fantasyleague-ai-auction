import json

from utils.json_store import JsonStore


def test_checkpoint_contains_error_and_serialized_state(tmp_path):
    state = {
        "players": [],
        "squads": {"b1": {"missing_roles": {"P": 3}}},
        "transactions": [],
    }

    path = JsonStore().save_checkpoint(
        state,
        tmp_path / "checkpoint.json",
        error="pool exhausted",
    )
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["error"] == "pool exhausted"
    assert data["squads"]["b1"]["missing_roles"]["P"] == 3
