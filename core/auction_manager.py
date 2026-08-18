"""Deterministic auction engine."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Sequence

from loguru import logger

from agents.base_agent import Bidder
from core.models import (
    AuctionCheckpoint,
    AuctionResult,
    AuctionState,
    BidderSnapshot,
    AuctionStatus,
    BidIssue,
    BidValidationError,
    Player,
    PlayerStatus,
    SimulationReport,
    SimulationSnapshot,
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
        state: AuctionState | None = None,
    ):
        if not bidders:
            raise ValueError("At least one bidder is required")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 25:
            raise ValueError("Budget must be an integer of at least 25 credits")

        bidder_list = list(bidders)
        bidder_ids = [bidder.buyer_id for bidder in bidder_list]
        if len(set(bidder_ids)) != len(bidder_ids):
            raise ValueError("Bidder IDs must be unique")

        if state is None:
            player_list = list(players)
            player_ids = [player.id for player in player_list]
            if len(set(player_ids)) != len(player_ids):
                raise ValueError("Player IDs must be unique")
            state = AuctionState(
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
        else:
            player_ids = [player.id for player in state.players]
            if len(set(player_ids)) != len(player_ids):
                raise ValueError("Player IDs must be unique")
            if any(bidder_id not in state.squads for bidder_id in bidder_ids):
                raise ValueError("Bidder IDs must match persisted squads")

        self.bidders = bidder_list
        self.state = state
        self._rng = random.Random(seed)
        self.seed = seed

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: AuctionCheckpoint,
        bidders: Sequence[Bidder],
    ) -> AuctionEngine:
        """Restore the next pool-exhaustion round from a validated checkpoint."""
        if not isinstance(checkpoint, AuctionCheckpoint):
            raise TypeError("checkpoint must be an AuctionCheckpoint")

        unsold = [
            player for player in checkpoint.players
            if player.status is PlayerStatus.UNSOLD
        ]
        if not unsold:
            raise ValueError("Checkpoint has no unsold players to resume")

        incomplete_ids = {
            buyer_id
            for buyer_id, squad in checkpoint.squads.items()
            if not squad.is_complete
        }
        if not incomplete_ids:
            raise ValueError("Checkpoint has no incomplete squads to resume")
        if set(checkpoint.resume.incomplete_buyer_ids) != incomplete_ids:
            raise ValueError("Checkpoint resume metadata does not match squads")

        bidder_list = list(bidders)
        bidder_ids = {bidder.buyer_id for bidder in bidder_list}
        missing_bidders = incomplete_ids - bidder_ids
        if missing_bidders:
            raise ValueError(
                "Missing bidders for incomplete squads: "
                + ", ".join(sorted(missing_bidders))
            )
        if bidder_ids - set(checkpoint.squads):
            raise ValueError("Bidder IDs must match persisted squads")

        players = []
        for source in checkpoint.players:
            player = source.model_copy(deep=True)
            if player.status is PlayerStatus.UNSOLD:
                player.status = PlayerStatus.AVAILABLE
                player.buyer_id = None
                player.selling_price = None
            players.append(player)

        state = AuctionState(
            players=players,
            squads={
                buyer_id: squad.model_copy(deep=True)
                for buyer_id, squad in checkpoint.squads.items()
            },
            transactions=[
                transaction.model_copy(deep=True)
                for transaction in checkpoint.transactions
            ],
            started_at=checkpoint.timestamp_start,
            ended_at=checkpoint.timestamp_end,
            run_number=checkpoint.run_number + 1,
            auction_count=checkpoint.auction_count,
            total_duration_seconds=checkpoint.duration_seconds,
            last_run_started_at=checkpoint.last_run_started_at,
            last_run_ended_at=checkpoint.last_run_ended_at,
            last_run_duration_seconds=checkpoint.last_run_duration_seconds,
            bid_issues=[issue.model_copy(deep=True) for issue in checkpoint.bid_issues],
        )
        active_bidders = [
            bidder for bidder in bidder_list if bidder.buyer_id in incomplete_ids
        ]
        return cls(
            state.players,
            active_bidders,
            budget=checkpoint.simulation.budget,
            seed=checkpoint.simulation.seed,
            state=state,
        )

    @property
    def auction_count(self) -> int:
        return self.state.auction_count

    @property
    def bid_issues(self) -> list[BidIssue]:
        return self.state.bid_issues

    def select_player(self) -> Player | None:
        """Select one available player without replacement."""
        available = self.state.available_players
        return self._rng.choice(available) if available else None

    def _record_bid_issue(
        self, player: Player, bidder: Bidder, code: str, message: str
    ) -> None:
        issue = BidIssue(
            auction_number=self.auction_count,
            player_id=player.id,
            buyer_id=bidder.buyer_id,
            code=code,
            message=message,
        )
        self.bid_issues.append(issue)
        logger.warning(
            "Bid issue in auction {} for player {} and bidder {} [{}]: {}",
            issue.auction_number,
            issue.player_id,
            issue.buyer_id,
            issue.code,
            issue.message,
        )

    def _collect_bids(self, player: Player) -> dict[str, int]:
        bids: dict[str, int] = {}
        for bidder in self.bidders:
            squad = self.state.squads[bidder.buyer_id]
            eligible = not squad.is_complete and squad.remaining_for(player.position) > 0
            if not eligible:
                bids[bidder.buyer_id] = 0
                continue

            try:
                bid = bidder.bid(player, squad)
            except Exception as exc:
                self._record_bid_issue(
                    player,
                    bidder,
                    "bidder_exception",
                    f"Bidder raised {type(exc).__name__}: {exc}",
                )
                bids[bidder.buyer_id] = 0
                continue

            try:
                squad.validate_bid(player, bid)
            except BidValidationError as exc:
                self._record_bid_issue(player, bidder, exc.code, str(exc))
                bids[bidder.buyer_id] = 0
                continue

            bids[bidder.buyer_id] = bid
        return bids

    def _canonical_player(self, player: Player) -> Player:
        for canonical in self.state.players:
            if canonical.id == player.id:
                return canonical
        raise ValueError(f"unknown player ID {player.id}")

    def auction_player(self, player: Player) -> AuctionResult:
        """Run exactly one first-round auction for an available player."""
        player = self._canonical_player(player)
        if player.status is not PlayerStatus.AVAILABLE:
            raise ValueError(f"Player {player.id} is not available")

        self.state.auction_count += 1
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

    def _finish_run(self, ended_at: datetime) -> None:
        self.state.ended_at = ended_at
        started_at = self.state.last_run_started_at or ended_at
        self.state.last_run_ended_at = ended_at
        self.state.last_run_duration_seconds = max(
            0.0, (ended_at - started_at).total_seconds()
        )
        self.state.total_duration_seconds += self.state.last_run_duration_seconds

    def _report(self) -> SimulationReport:
        end = self.state.ended_at or datetime.now(timezone.utc)
        start = self.state.started_at or end
        last_start = self.state.last_run_started_at or start
        last_end = self.state.last_run_ended_at or end
        sold = [player for player in self.state.players if player.status is PlayerStatus.SOLD]
        unsold = [player for player in self.state.players if player.status is PlayerStatus.UNSOLD]
        return SimulationReport(
            schema_version=1,
            document_type="auction_report",
            timestamp_start=start,
            timestamp_end=end,
            duration_seconds=self.state.total_duration_seconds,
            last_run_started_at=last_start,
            last_run_ended_at=last_end,
            last_run_duration_seconds=self.state.last_run_duration_seconds,
            run_number=self.state.run_number,
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
            bid_issues=[issue.model_copy(deep=True) for issue in self.bid_issues],
        )

    def partial_report(self) -> SimulationReport:
        """Project the current state into a report even when the run is incomplete."""
        return self._report()

    def build_checkpoint(
        self,
        simulation: SimulationSnapshot,
        buyers: list[BidderSnapshot],
        error: Exception,
        missing_roles: dict[str, dict[str, int]],
    ) -> AuctionCheckpoint:
        """Project the current exhausted state into a resumable checkpoint."""
        if not isinstance(error, AuctionIncompleteError):
            raise ValueError("Only pool exhaustion can create a checkpoint")

        report = self._report()
        fields = report.model_dump()
        fields.update(
            document_type="auction_checkpoint",
            players=[player.model_copy(deep=True) for player in self.state.players],
            simulation=simulation,
            buyers=[buyer.model_copy(deep=True) for buyer in buyers],
            auction_count=self.auction_count,
            missing_roles={
                buyer_id: dict(roles)
                for buyer_id, roles in missing_roles.items()
            },
            error_code="pool_exhausted",
            error=str(error),
            resume={"incomplete_buyer_ids": list(missing_roles)},
        )
        return AuctionCheckpoint(**fields)

    def run(self) -> SimulationReport:
        """Run until all squads are complete or the pool is exhausted."""
        run_started_at = datetime.now(timezone.utc)
        if self.state.started_at is None:
            self.state.started_at = run_started_at
        self.state.last_run_started_at = run_started_at
        self.state.last_run_ended_at = None
        self.state.last_run_duration_seconds = 0.0
        logger.info("Starting auction with {} players", len(self.state.players))

        while True:
            incomplete = {
                buyer_id: squad.missing_roles()
                for buyer_id, squad in self.state.squads.items()
                if not squad.is_complete
            }
            if not incomplete:
                self._finish_run(datetime.now(timezone.utc))
                logger.success("Auction completed in {} player auctions", self.auction_count)
                return self._report()

            player = self.select_player()
            if player is None:
                self._finish_run(datetime.now(timezone.utc))
                raise AuctionIncompleteError(incomplete)
            self.auction_player(player)
