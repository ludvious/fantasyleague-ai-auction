"""Small validation helpers for the current domain models."""

from __future__ import annotations

from core.models import Player, PlayerStatus, Squad


class ValidationResult:
    def __init__(self, is_valid: bool, message: str = ""):
        self.is_valid = is_valid
        self.message = message

    def __bool__(self) -> bool:
        return self.is_valid

    def __str__(self) -> str:
        return f"{'✓' if self.is_valid else '✗'} {self.message}"


def validate_bid(bid: int, buyer: Squad, player: Player) -> ValidationResult:
    if bid < 0:
        return ValidationResult(False, f"Negative bid: {bid}")
    if buyer.is_complete:
        return ValidationResult(False, "Roster is already complete")
    if buyer.remaining_for(player.position) == 0:
        return ValidationResult(False, f"Role {player.position.value} is already full")
    if bid > buyer.max_bid_allowed:
        return ValidationResult(False, f"Bid exceeds maximum {buyer.max_bid_allowed}")
    return ValidationResult(True, "Valid bid")


def validate_team_complete(team: Squad) -> ValidationResult:
    if not team.is_complete:
        return ValidationResult(False, f"Incomplete roster: {team.team_size}/25 players")
    for player in team.players:
        if player.status is not PlayerStatus.SOLD:
            return ValidationResult(False, f"Player {player.name} is not sold")
        if player.buyer_id != team.buyer_id:
            return ValidationResult(False, f"Player {player.name} has another owner")
    return ValidationResult(True, "Complete roster")


def check_buyer_can_bid(buyer: Squad, available_players: int) -> ValidationResult:
    if buyer.is_complete:
        return ValidationResult(True, "Roster is already complete")
    if available_players < buyer.remaining_slots:
        return ValidationResult(
            False,
            f"Not enough players: {available_players} available, {buyer.remaining_slots} required",
        )
    if buyer.budget_remaining < buyer.remaining_slots:
        return ValidationResult(False, "Not enough budget to reserve remaining slots")
    return ValidationResult(True, "Buyer can continue")
