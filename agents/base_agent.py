"""Common bidder contract used by the auction engine."""

from typing import Protocol

from core.models import Player, Squad


class Bidder(Protocol):
    buyer_id: str
    name: str

    def bid(self, player: Player, squad: Squad) -> int:
        """Return zero to pass or a positive legal bid candidate."""
        ...
