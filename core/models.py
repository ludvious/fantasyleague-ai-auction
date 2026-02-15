"""
Data models.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class PlayerStatus(str, Enum):
    AVAILABLE = "disponibile"
    SOLD = "venduto"
    UNSOLD = "invenduto"


class Position(str, Enum):
    P = "P"  # Portiere
    D = "D"  # Difensore
    C = "C"  # Centrocampista
    A = "A"  # Attaccante


class Player(BaseModel):
    """Modello dati di un giocatore"""
    
    id: str = Field(..., description="ID univoco giocatore")
    name: str = Field(..., description="Nome completo giocatore")
    position: Position = Field(..., description="Ruolo (P/D/C/A)")
    team: str = Field(..., description="Squadra Serie A di appartenenza")
    list_price: int = Field(..., ge=1, le=99, description="Quotazione in crediti della redazione Fantacalcio")
    state: PlayerStatus = Field(default=PlayerStatus.AVAILABLE, description="Stato attuale")
    picks_count: int = Field(..., description="Conteggio di quante volte è stato estratto dal file per essere astato")
    buyer_id: Optional[str] = Field(default=None, description="ID buyer proprietario (se venduto)")
    selling_price: Optional[int] = Field(default=None, description="Prezzo di vendita")
    
    def to_dict(self) -> dict:
        """Serializza a dizionario"""
        return self.model_dump()
    
    def __str__(self) -> str:
        return f"{self.name} ({self.position.value}) - {self.team} [Q: {self.list_price}]"


class Team(BaseModel):
    """Team Model Fantasy League"""
    
    buyer_id: str = Field(..., description="ID buyer")
    buyer_name: str = Field(..., description="Team/Buyer Name")
    players: List[Player] = Field(default_factory=list, description="List of purchased players")
    start_budget: int = Field(..., ge=1, description="Start Budget")
    budget: int = Field(..., description="Actual Budget")
    max_bid_allowed: int = Field(..., description="Max Bid Allowed during an auction for a player")
    total_purchases: int = Field(default=0, description="Numero acquisti effettuati")
    
    @property
    def team_size(self) -> int:
        """return the actual team_size"""
        return len(self.players)
    
    @property
    def max_bid_allowed(self) -> int:
        """return the max bid allowed during an auction (constraint budget)"""
        avalaible_slots = 25 - self.team_size
        if avalaible_slots <= 0:
            return 0
        # reserve at least 1 credit for each available slot
        return max(0, self.budget - avalaible_slots + 1)
    
    @property
    def is_complete(self) -> bool:
        """return true if team size = 25, else false"""
        return self.team_size == 25
    
    @property
    def total_expense(self) -> int:
        """return total credit used"""
        return self.start_budget - self.budget_rimanente
    
    @property
    def price_media(self) -> float:
        """Prezzo medio per giocatore"""
        if self.team_size == 0:
            return 0.0
        return self.total_expense / self.team_size
    
    @property
    #TODO: add the percentuage of credit used for buy players by position role
    
    def player_for_position(self) -> Dict[str, int]:
        """return the count of player by position/role"""
        conteggio = {ruolo.value: 0 for ruolo in Position}
        for player in self.players:
            conteggio[player.position.value] += 1
        return conteggio
    
    def add_player(self, player: Player, price: int) -> None:
        """Add new acquired player to team"""
        if self.is_complete:
            raise ValueError(f"Rosa già completa ({self.team_size}/25)")
        
        if price > self.budget_rimanente:
            raise ValueError(
                f"Budget insufficiente. Richiesto: {price}, Massima Offerta Possibile: {self.max_bid_allowed}"
            )
        
        # Update player
        player.state = PlayerStatus.SOLD
        player.buyer_id = self.buyer_id
        player.selling_price = price
        
        # Aggiorna team
        self.players.append(player)
        self.budget_rimanente -= price
        self.max_bid_allowed = self.max_bid_allowed() #update the max bid allowed for next auction
        self.transazioni += 1
    
    def to_dict(self) -> dict:
        return {
            "buyer_id": self.buyer_id,
            "buyer_name": self.buyer_name,
            "rosa_size": self.team_size,
            "budget": self.budget,
            "total_expense": self.total_expense,
            "price_media": round(self.price_media, 2),
            "player_for_position": self.player_for_position(),
            "players": [p.to_dict() for p in self.players]
        }


class Transaction(BaseModel):
    """Modello di una transazione d'asta"""
    player: Player
    buyer_id: str
    prezzo: int = Field(..., ge=0)
    rivals_bids: Dict[str, int] = Field(default_factory=dict, description="Offerte dei rivali")
    is_tie_breaker: bool = Field(default=False, description="True se è un rilancio per parità")
    
    @property
    def num_competitors(self) -> int:
        """Numero di competitor che hanno offerto > 0"""
        return sum(1 for offerta in self.rivals_bid.values() if offerta > 0)
    
    def to_dict(self) -> dict:
        return {
            "player": self.player.to_dict(),
            "buyer_id": self.buyer_id,
            "prezzo": self.prezzo,
            "offerte_altri": self.rivals_bid,
            "num_competitors": self.num_competitors,
            "is_tie_breaker": self.is_tie_breaker
        }


class ResultStatus(str, Enum):
    """Auction state"""
    VENDUTO = "venduto"
    INVENDUTO_NESSUNA_OFFERTA = "invenduto_nessuna_offerta"
    INVENDUTO_PARITA = "invenduto_parita"
    ASTA_RILANCIO = "asta_rilancio"


class AuctionResult(BaseModel):
    """Auction result"""
    player: Player
    winner_id: Optional[str] = None
    prezzo: int = 0
    all_bids: Dict[str, int] = Field(default_factory=dict)
    status: ResultStatus
    
    @property
    def max_bid(self) -> int:
        """Max bid sended"""
        return max(self.all_bids.values()) if self.all_bids else 0
    
    @property
    def num_bidders(self) -> int:
        """Number of buyers who made an offer"""
        return sum(1 for bid in self.all_bids.values() if bid > 0)
    
    @property
    def has_tie(self) -> bool:
        """return true if there is a tie in the highest bid"""
        if not self.all_bids:
            return False
        max_bid = self.max_bid
        return sum(1 for bid in self.all_bids.values() if bid == max_bid) > 1
    
    def get_tied_buyers(self) -> List[str]:
        """Returns buyers to parity"""
        if not self.has_tie:
            return []
        max_bid = self.max_bid
        return [buyer_id for buyer_id, bid in self.all_bids.items() if bid == max_bid]
    
    def to_dict(self) -> dict:
        return {
            "player": self.player.to_dict(),
            "winner_id": self.winner_id,
            "prezzo": self.prezzo,
            "all_bids": self.all_bids,
            "status": self.status.value,
            "max_bid": self.max_bid,
            "num_bidders": self.num_bidders,
            "has_tie": self.has_tie
        }


class SimulationReport(BaseModel):
    timestamp_start: datetime
    timestamp_end: datetime
    duration_seconds: float
    
    teams: Dict[str, Team]
    transactions: List[Transaction]
    unsold_players: List[Player]
    
    total_players: int
    players_sold: int
    players_unsold: int
    
    @property
    def total_spent(self) -> int:
        """Total spent by all buyers"""
        return sum(team.total_expense for team in self.teams.values())
    
    @property
    def max_price(self) -> int:
        """Max price spent for a player"""
        if not self.transactions:
            return 0
        return max(t.prezzo for t in self.transactions)
    
    def to_dict(self) -> dict:
        return {
            "timestamp_start": self.timestamp_start.isoformat(),
            "timestamp_end": self.timestamp_end.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
            "statistics": {
                "total_players": self.total_players,
                "players_sold": self.players_sold,
                "players_unsold": self.players_unsold,
                "total_spent": self.total_spent,
                "max_price": self.max_price,
            },
            "teams": {buyer_id: team.to_dict() for buyer_id, team in self.teams.items()},
            "transactions": [t.to_dict() for t in self.transactions],
            "unsold_players": [p.to_dict() for p in self.unsold_players]
        }