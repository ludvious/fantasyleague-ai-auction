"""Deterministic auction engine."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Sequence

from loguru import logger

from agents.base_agent import Bidder
from core.models import (
    AuctionResult,
    AuctionState,
    AuctionStatus,
    Player,
    PlayerStatus,
    SimulationReport,
    Squad,
    Transaction,
)


class AuctionIncompleteError(RuntimeError):
    """Raised when the available player pool ends before all rosters are valid."""

    def __init__(self, missing_roles: dict[str, dict[str, int]]):
        self.missing_roles = missing_roles
        details = "; ".join(
            f"{buyer_id}: {roles}"
            for buyer_id, roles in missing_roles.items()
        )
        super().__init__(f"Player pool exhausted before roster completion: {details}")


class AuctionEngine:
    """Apply auction rules to players and bidder decisions."""

    def __init__(
        self,
        players: Sequence[Player],
        bidders: Sequence[Bidder],
        budget: int = 500,
        seed: int | None = None,
    ):
        if not bidders:
            raise ValueError("At least one bidder is required")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 25:
            raise ValueError("Budget must be an integer of at least 25 credits")

        player_list = list(players)
        bidder_list = list(bidders)
        player_ids = [player.id for player in player_list]
        bidder_ids = [bidder.buyer_id for bidder in bidder_list]
        if len(set(player_ids)) != len(player_ids):
            raise ValueError("Player IDs must be unique")
        if len(set(bidder_ids)) != len(bidder_ids):
            raise ValueError("Bidder IDs must be unique")

        self.bidders = bidder_list
        self.state = AuctionState(
            players=player_list,
            squads={
                bidder.buyer_id: Squad(
                    buyer_id=bidder.buyer_id,
                    name=bidder.name,
                    budget_initial=budget,
                )
                for bidder in bidder_list
            },
        )
        self._rng = random.Random(seed)
        self.seed = seed
        self.auction_count = 0

    def select_player(self) -> Player | None:
        """Select one available player without replacement."""
        available = self.state.available_players
        return self._rng.choice(available) if available else None

    def _collect_bids(self, player: Player) -> dict[str, int]:
        bids: dict[str, int] = {}
        for bidder in self.bidders:
            squad = self.state.squads[bidder.buyer_id]
            eligible = not squad.is_complete and squad.remaining_for(player.position) > 0
            bid = bidder.bid(player, squad) if eligible else 0
            if not isinstance(bid, int) or isinstance(bid, bool):
                raise ValueError(f"Bidder {bidder.buyer_id} returned a non-integer bid")
            if bid < 0:
                raise ValueError(f"Bidder {bidder.buyer_id} returned a negative bid")
            if bid > squad.max_bid_allowed:
                raise ValueError(
                    f"Bidder {bidder.buyer_id} bid {bid}, above legal maximum "
                    f"{squad.max_bid_allowed}"
                )
            bids[bidder.buyer_id] = bid
        return bids

    def auction_player(self, player: Player) -> AuctionResult:
        """Run exactly one first-round auction for an available player."""
        if player.status is not PlayerStatus.AVAILABLE:
            raise ValueError(f"Player {player.id} is not available")

        self.auction_count += 1
        bids = self._collect_bids(player)
        max_bid = max(bids.values(), default=0)
        positive_winners = [buyer_id for buyer_id, bid in bids.items() if bid == max_bid and bid > 0]

        if max_bid == 0:
            player.status = PlayerStatus.UNSOLD
            result = AuctionResult(
                player=player.model_copy(deep=True),
                all_bids=bids,
                status=AuctionStatus.UNSOLD_NO_BID,
            )
            logger.warning("{}: no positive bids", player.name)
            return result

        if len(positive_winners) != 1:
            player.status = PlayerStatus.UNSOLD
            result = AuctionResult(
                player=player.model_copy(deep=True),
                all_bids=bids,
                status=AuctionStatus.UNSOLD_TIE,
            )
            logger.warning("{}: tied highest bid at {} credits", player.name, max_bid)
            return result

        winner_id = positive_winners[0]
        self.state.squads[winner_id].add_player(player, max_bid)
        transaction = Transaction(
            player=player.model_copy(deep=True),
            buyer_id=winner_id,
            price=max_bid,
            all_bids=bids,
        )
        self.state.transactions.append(transaction)
        logger.info(
            "Sold {} to {} for {} credits",
            player.name,
            self.state.squads[winner_id].name,
            max_bid,
        )
        return AuctionResult(
            player=player.model_copy(deep=True),
            winner_id=winner_id,
            price=max_bid,
            all_bids=bids,
            status=AuctionStatus.SOLD,
        )

    def _report(self) -> SimulationReport:
        end = self.state.ended_at or datetime.now(timezone.utc)
        start = self.state.started_at or end
        sold = [player for player in self.state.players if player.status is PlayerStatus.SOLD]
        unsold = [player for player in self.state.players if player.status is PlayerStatus.UNSOLD]
        return SimulationReport(
            timestamp_start=start,
            timestamp_end=end,
            duration_seconds=max(0.0, (end - start).total_seconds()),
            squads={
                buyer_id: squad.model_copy(deep=True)
                for buyer_id, squad in self.state.squads.items()
            },
            transactions=[
                transaction.model_copy(deep=True)
                for transaction in self.state.transactions
            ],
            unsold_players=[player.model_copy(deep=True) for player in unsold],
            total_players=len(self.state.players),
            players_sold=len(sold),
            players_unsold=len(unsold),
        )

    def run(self) -> SimulationReport:
        """Run until all squads are complete or the pool is exhausted."""
        self.state.started_at = datetime.now(timezone.utc)
        logger.info("Starting auction with {} players", len(self.state.players))

        while True:
            incomplete = {
                buyer_id: squad.missing_roles()
                for buyer_id, squad in self.state.squads.items()
                if not squad.is_complete
            }
            if not incomplete:
                self.state.ended_at = datetime.now(timezone.utc)
                logger.success("Auction completed in {} player auctions", self.auction_count)
                return self._report()

            player = self.select_player()
            if player is None:
                self.state.ended_at = datetime.now(timezone.utc)
                raise AuctionIncompleteError(incomplete)
            self.auction_player(player)
