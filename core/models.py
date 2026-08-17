"""Domain models and roster rules for the deterministic auction MVP."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Position(str, Enum):
    P = "P"
    D = "D"
    C = "C"
    A = "A"


ROSTER_REQUIREMENTS: dict[Position, int] = {
    Position.P: 3,
    Position.D: 8,
    Position.C: 8,
    Position.A: 6,
}


class PlayerStatus(str, Enum):
    AVAILABLE = "disponibile"
    SOLD = "venduto"
    UNSOLD = "invenduto"


class BidValidationError(ValueError):
    """Raised when a bidder offer violates a domain rule."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Player(BaseModel):
    """A player from the source list and its auction state."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    position: Position
    team: str = Field(min_length=1)
    list_price: int = Field(ge=0, description="Informational Excel quotation")
    status: PlayerStatus = PlayerStatus.AVAILABLE
    buyer_id: Optional[str] = None
    selling_price: Optional[int] = Field(default=None, ge=1)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class Squad(BaseModel):
    """A manager's roster, budget, and role constraints."""

    model_config = ConfigDict(validate_assignment=True)

    buyer_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    budget_initial: int = Field(ge=25)
    budget_remaining: Optional[int] = Field(default=None, ge=0)
    players: list[Player] = Field(default_factory=list)

    @model_validator(mode="after")
    def initialize_budget(self) -> Squad:
        if self.budget_remaining is None:
            self.budget_remaining = self.budget_initial
        elif self.budget_remaining > self.budget_initial:
            raise ValueError("budget_remaining cannot exceed budget_initial")
        return self

    @property
    def team_size(self) -> int:
        return len(self.players)

    @property
    def remaining_slots(self) -> int:
        return max(0, sum(ROSTER_REQUIREMENTS.values()) - self.team_size)

    @property
    def is_complete(self) -> bool:
        return self.team_size == sum(ROSTER_REQUIREMENTS.values()) and not any(
            self.missing_roles().values()
        )

    @property
    def total_expense(self) -> int:
        return self.budget_initial - self.budget_remaining

    @property
    def max_bid_allowed(self) -> int:
        """Maximum offer while retaining one credit for every empty slot."""
        if self.remaining_slots == 0:
            return 0
        return max(0, self.budget_remaining - self.remaining_slots + 1)

    def role_counts(self) -> dict[str, int]:
        return {
            position.value: sum(player.position is position for player in self.players)
            for position in Position
        }

    def missing_roles(self) -> dict[str, int]:
        counts = self.role_counts()
        return {
            position.value: max(0, required - counts[position.value])
            for position, required in ROSTER_REQUIREMENTS.items()
        }

    def remaining_for(self, position: Position) -> int:
        return max(0, ROSTER_REQUIREMENTS[position] - self.role_counts()[position.value])

    def validate_bid(self, player: Player, bid: object) -> None:
        """Validate a bidder offer without mutating the squad or player."""
        if type(bid) is not int:
            raise BidValidationError(
                "invalid_type",
                "Bid must be a Python int; bool and other numeric types are not accepted",
            )
        if bid < 0:
            raise BidValidationError("negative", "Bid cannot be negative")
        if self.is_complete:
            raise BidValidationError("roster_complete", "Roster is already complete")
        if player.status is not PlayerStatus.AVAILABLE:
            raise BidValidationError(
                "player_unavailable", f"Player {player.id} is not available"
            )
        if self.remaining_for(player.position) == 0:
            raise BidValidationError(
                "role_full", f"Role {player.position.value} is already full"
            )
        if bid > self.max_bid_allowed:
            raise BidValidationError(
                "above_maximum",
                f"Bid exceeds the legal maximum {self.max_bid_allowed}",
            )

    def add_player(self, player: Player, price: int) -> None:
        """Validate and record a purchase."""
        if any(existing.id == player.id for existing in self.players):
            raise ValueError(f"Player {player.id} was already purchased")
        self.validate_bid(player, price)
        if price == 0:
            raise ValueError("Purchase price must be at least 1")

        player.status = PlayerStatus.SOLD
        player.buyer_id = self.buyer_id
        player.selling_price = price
        self.players.append(player.model_copy(deep=True))
        self.budget_remaining -= price

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class AuctionStatus(str, Enum):
    SOLD = "venduto"
    UNSOLD_NO_BID = "invenduto_nessuna_offerta"
    UNSOLD_TIE = "invenduto_parita"


class BidIssue(BaseModel):
    """A bidder failure isolated from the auction engine."""

    auction_number: int = Field(ge=1)
    player_id: str
    buyer_id: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class AuctionResult(BaseModel):
    player: Player
    winner_id: Optional[str] = None
    price: int = Field(default=0, ge=0)
    all_bids: dict[str, int] = Field(default_factory=dict)
    status: AuctionStatus

    @property
    def max_bid(self) -> int:
        return max(self.all_bids.values(), default=0)

    @property
    def num_bidders(self) -> int:
        return sum(bid > 0 for bid in self.all_bids.values())

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class Transaction(BaseModel):
    player: Player
    buyer_id: str
    price: int = Field(ge=1)
    all_bids: dict[str, int] = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class AuctionState(BaseModel):
    players: list[Player] = Field(default_factory=list)
    squads: dict[str, Squad] = Field(default_factory=dict)
    transactions: list[Transaction] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    @property
    def available_players(self) -> list[Player]:
        return [player for player in self.players if player.status is PlayerStatus.AVAILABLE]

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class SimulationReport(BaseModel):
    timestamp_start: datetime
    timestamp_end: datetime
    duration_seconds: float
    squads: dict[str, Squad]
    transactions: list[Transaction]
    unsold_players: list[Player]
    total_players: int
    players_sold: int
    players_unsold: int

    @property
    def total_spent(self) -> int:
        return sum(squad.total_expense for squad in self.squads.values())

    @property
    def max_price(self) -> int:
        return max((transaction.price for transaction in self.transactions), default=0)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
