"""System prompt assembly for CoachAgent."""

from __future__ import annotations

from pathlib import Path

from core.models import ROSTER_REQUIREMENTS

COMMON_PROMPT_PATH = Path(__file__).with_name("prompts") / "common.md"

MAX_BID_RULE = (
    "Ogni slot libero riserva 1 credito: la tua offerta massima consentita è "
    "budget_rimanente - slot_liberi + 1 (ricevi il valore esatto nel contesto)."
)


def common_prompt(budget: int) -> str:
    text = COMMON_PROMPT_PATH.read_text(encoding="utf-8")
    requirements = ", ".join(
        f"{position.value}: {count}"
        for position, count in ROSTER_REQUIREMENTS.items()
    )
    replacements = {
        "{roster_requirements}": requirements,
        "{budget}": str(budget),
        "{max_bid_rule}": MAX_BID_RULE,
    }
    for placeholder, value in replacements.items():
        if placeholder not in text:
            raise ValueError(
                f"common prompt is missing placeholder {placeholder}"
            )
        text = text.replace(placeholder, value)
    return text


def render_system_prompt(
    *,
    budget: int,
    profile: str | None = None,
    role: str | None = None,
    personality: str | None = None,
    spending_profile: dict[str, float] | None = None,
    target_players: list[str] | None = None,
    override: str | None = None,
) -> str:
    if override and override.strip():
        return override.strip()
    sections = [common_prompt(budget)]
    structured = []
    if role:
        structured.append(f"Ruolo: {role}")
    if personality:
        structured.append(f"Personalità: {personality}")
    if spending_profile:
        spending = ", ".join(
            f"{position}: {share:.0%}"
            for position, share in spending_profile.items()
        )
        structured.append(f"Distribuzione di spesa ideale per ruolo: {spending}")
    if target_players:
        structured.append(f"Giocatori obiettivo: {', '.join(target_players)}")
    if structured:
        sections.append("\n".join(structured))
    if profile and profile.strip():
        sections.append("# Profilo dell'agente\n" + profile.strip())
    return "\n\n".join(sections)
