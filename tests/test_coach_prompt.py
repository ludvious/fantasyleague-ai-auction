"""Tests for common-prompt rendering."""

import pytest

from agents import coach_prompt
from agents.coach_prompt import common_prompt, render_system_prompt


def test_common_prompt_substitutes_domain_placeholders():
    text = common_prompt(500)

    assert "{roster_requirements}" not in text
    assert "{budget}" not in text
    assert "{max_bid_rule}" not in text
    assert "P: 3, D: 8, C: 8, A: 6" in text
    assert "500" in text
    assert "1 credito" in text


def test_render_appends_structured_fields_and_profile():
    rendered = render_system_prompt(
        budget=500,
        profile="Joe è aggressivo.",
        role="fantallenatore esperto",
        personality="prudente",
        spending_profile={"P": 0.1, "D": 0.2, "C": 0.3, "A": 0.4},
        target_players=["Lautaro"],
    )

    assert rendered.startswith("Sei un allenatore-manager")
    assert "Ruolo: fantallenatore esperto" in rendered
    assert (
        "Distribuzione di spesa ideale per ruolo: "
        "P: 10%, D: 20%, C: 30%, A: 40%" in rendered
    )
    assert "Giocatori obiettivo: Lautaro" in rendered
    assert rendered.endswith("# Profilo dell'agente\nJoe è aggressivo.")


def test_override_wins():
    assert render_system_prompt(budget=500, override="custom") == "custom"


def test_missing_placeholder_fails(tmp_path, monkeypatch):
    broken = tmp_path / "system_prompt.md"
    broken.write_text("solo testo senza placeholder", encoding="utf-8")
    monkeypatch.setattr(coach_prompt, "COMMON_PROMPT_PATH", broken)

    with pytest.raises(ValueError, match="roster_requirements"):
        common_prompt(500)
