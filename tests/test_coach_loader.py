"""Tests for coach discovery and front-matter parsing."""

import pytest

from agents.coach_loader import load_buyer_configs, load_coaches


def write_coach(directory, filename: str, text: str) -> None:
    (directory / filename).write_text(text, encoding="utf-8")


def test_loads_front_matter_and_body(tmp_path):
    write_coach(
        tmp_path,
        "coachAgent_Joe.md",
        "---\nmodel: gpt-4o-mini\ntemperature: 0.4\n"
        "spending_profile: {P: 0.1, D: 0.2, C: 0.3, A: 0.4}\n"
        "target_players: [Lautaro]\n---\n\nJoe è aggressivo.\n",
    )

    coaches = load_coaches(tmp_path)

    assert len(coaches) == 1
    coach = coaches[0]
    assert coach["id"] == "joe"
    assert coach["name"] == "Joe"
    assert coach["strategy"] == "llm"
    assert coach["priority"] == 0
    assert coach["llm"]["model"] == "gpt-4o-mini"
    assert coach["llm"]["temperature"] == 0.4
    assert coach["profile"] == "Joe è aggressivo."


def test_body_only_file_uses_filename_defaults(tmp_path):
    write_coach(tmp_path, "coachAgent_Beta.md", "Solo corpo.\n")

    coach = load_coaches(tmp_path)[0]

    assert coach["id"] == "beta"
    assert coach["name"] == "Beta"
    assert coach["llm"] == {}
    assert coach["profile"] == "Solo corpo."


def test_files_are_sorted_and_numbered(tmp_path):
    write_coach(tmp_path, "coachAgent_Zeta.md", "Z")
    write_coach(tmp_path, "coachAgent_Alfa.md", "A")

    coaches = load_coaches(tmp_path)

    assert [coach["name"] for coach in coaches] == ["Alfa", "Zeta"]
    assert [coach["priority"] for coach in coaches] == [0, 1]


def test_unknown_front_matter_field_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nunknown: 1\n---\nbody")

    with pytest.raises(ValueError, match="unknown front-matter"):
        load_coaches(tmp_path)


def test_unclosed_front_matter_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nmodel: gpt\nbody")

    with pytest.raises(ValueError, match="not closed"):
        load_coaches(tmp_path)


def test_invalid_field_rejected_with_path(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\ntemperature: 5\n---\nbody")

    with pytest.raises(ValueError, match="coachAgent_Joe.md"):
        load_coaches(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nid: same\n---\nA")
    write_coach(tmp_path, "coachAgent_Jim.md", "---\nid: same\n---\nB")

    with pytest.raises(ValueError, match="duplicate coach id"):
        load_coaches(tmp_path)


def test_empty_directory_returns_empty_list(tmp_path):
    assert load_coaches(tmp_path) == []


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_coaches(tmp_path / "missing")


def test_non_coach_files_ignored(tmp_path):
    write_coach(tmp_path, "prompt.md", "not a coach")

    assert load_coaches(tmp_path) == []


def test_load_buyer_configs_merges_yaml_and_coaches(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "body")
    config = {
        "paths": {"coaches": str(tmp_path)},
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    }

    buyers = load_buyer_configs(config)

    assert [buyer["id"] for buyer in buyers] == ["b1", "joe"]
    assert buyers[1]["priority"] == 1


def test_load_buyer_configs_without_coaches_returns_yaml_buyers():
    config = {
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}]
    }

    assert load_buyer_configs(config) == config["buyers"]


def test_load_buyer_configs_rejects_id_collisions(tmp_path):
    write_coach(tmp_path, "coachAgent_b1.md", "body")
    config = {
        "paths": {"coaches": str(tmp_path)},
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {}}],
    }

    with pytest.raises(ValueError, match="Duplicate buyer ids"):
        load_buyer_configs(config)
