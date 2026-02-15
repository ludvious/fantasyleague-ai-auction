from typing import List, Optional, Tuple
from loguru import logger

from core.models import Player, Team, PlayerStatus

class ValidationResult:
    
    def __init__(self, is_valid: bool, message: str = ""):
        self.is_valid = is_valid
        self.message = message
    
    def __bool__(self):
        return self.is_valid
    
    def __str__(self):
        return f"{'✓' if self.is_valid else '✗'} {self.message}"


def validate_bid(bid: int, buyer: Team, player: Player) -> ValidationResult:
    """
    Validates an offer from a buyer.
    
    Args:
        bid: Offer amount
        buyer: Buyer's team
        player: Player being auctioned
    
    Returns:
        ValidationResult with outcome and message
    """

    if bid < 0: #TODO: handle negative bid as offer equal to 0
        return ValidationResult(
            False,
            f"Offerta negativa non valida: {bid}" 
        )
    
    if bid > buyer.max_bid_allowed:
        return ValidationResult(
            False,
            f"Offerta {bid} supera la massima offerta possibile {buyer.max_bid_allowed}"
            f"(devi riservare 1 credito per ogni slot rimanente)"
        )
    
    if buyer.is_complete:
        return ValidationResult(
            False,
            f"Rosa già completa ({buyer.team_size}/25), non puoi fare offerte!"
        )
    
    return ValidationResult(True, "Offerta valida")


def validate_team_complete(team: Team) -> ValidationResult:
    """
    Verify that a team has a complete and valid roster.
    
    Args:
        team: Team to validate
        
    Returns:
        ValidationResult
    """
    
    if team.team_size < 25:
        return ValidationResult(
            False,
            f"Rosa incompleta: {team.team_size}/25 giocatori"
        )
    
    if team.team_size > 25:
        return ValidationResult(
            False,
            f"Rosa troppo grande: {team.team_size}/25 giocatori"
        )
    
    for player in team.players:
        if player.state != PlayerStatus.SOLD:
            return ValidationResult(
                False,
                f"Giocatore {player.name} non risulta venduto"
            )
        if player.buyer_id != team.buyer_id:
            return ValidationResult(
                False,
                f"Giocatore {player.name} risulta venduto a buyer diverso: {player.buyer_id}"
            )
    
    return ValidationResult(True, "Rosa completa e valida")


def check_buyer_can_bid(buyer: Team, available_players: int) -> ValidationResult:
    """
    Verifica se un buyer può ancora completare la rosa.
    
    Args:
        buyer: Team buyer
        available_players: Numero di giocatori ancora disponibili
        
    Returns:
        ValidationResult
    """
    
    slots_rimanenti = 25 - buyer.team_size
    
    if slots_rimanenti == 0:
        return ValidationResult(True, "Rosa già completa")
    
    if available_players < slots_rimanenti:
        return ValidationResult(
            False,
            f"Giocatori disponibili insufficienti: {available_players} "
            f"disponibili, {slots_rimanenti} richiesti"
        )
    
    if buyer.budget_rimanente < slots_rimanenti:
        return ValidationResult(
            False,
            f"Budget insufficiente per completare rosa: "
            f"{buyer.budget_rimanente} crediti per {slots_rimanenti} giocatori"
        )
    
    return ValidationResult(True, "Buyer può completare rosa")


if __name__ == "__main__":
    # Test validatori
    from core.models import Position
    
    # Setup logger
    from utils.logger import setup_logger
    setup_logger(log_level="DEBUG")
    
    # Test validate_bid
    player = Player(
        id="1",
        nome="Test Player",
        ruolo=Position.A,
        squadra_reale="Inter",
        quotazione=25
    )
    
    team = Team(
        buyer_id="buyer_1",
        buyer_name="Test Team",
        budget_iniziale=500,
        budget_rimanente=500
    )
    
    # Offerta valida
    result = validate_bid(50, team, player)
    logger.info(f"Test offerta valida: {result}")
    
    # Offerta troppo alta
    result = validate_bid(600, team, player)
    logger.info(f"Test offerta > budget: {result}")
    
    # Offerta che viola constraint slot
    team.budget_rimanente = 30
    result = validate_bid(10, team, player)  # max_allowed = 30 - 25 + 1 = 6
    logger.info(f"Test offerta > max_allowed: {result}")