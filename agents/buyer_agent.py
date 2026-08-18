"""Deterministic bidder implementations for the MVP."""

from __future__ import annotations

import random

from core.models import Player, Squad


class DeterministicBidder:
    """Bids a stable amount based only on its configured priority."""

    def __init__(self, buyer_id: str, name: str, priority: int = 0):
        if not buyer_id or not name:
            raise ValueError("buyer_id and name are required")
        if priority < 0:
            raise ValueError("priority cannot be negative")
        self.buyer_id = buyer_id
        self.name = name
        self.priority = priority

    def bid(self, player: Player, squad: Squad) -> int:
        return min(squad.max_bid_allowed, self.priority + 1)


class RandomBidder:
    """Produces reproducible bids from an injected random generator."""

    def __init__(self, buyer_id: str, name: str, rng: random.Random):
        if not buyer_id or not name:
            raise ValueError("buyer_id and name are required")
        self.buyer_id = buyer_id
        self.name = name
        self.rng = rng

    def bid(self, player: Player, squad: Squad) -> int:
        return self.rng.randint(0, squad.max_bid_allowed)
