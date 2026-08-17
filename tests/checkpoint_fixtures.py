from datetime import datetime, timezone

from core.models import (
    AuctionCheckpoint,
    BidderSnapshot,
    Player,
    PlayerStatus,
    Position,
    SimulationReport,
    SimulationSnapshot,
    Squad,
)


_START = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)
_END = datetime(2026, 8, 17, 18, 0, 1, tzinfo=timezone.utc)


def make_report() -> SimulationReport:
    return SimulationReport(
        schema_version=1,
        document_type="auction_report",
        timestamp_start=_START,
        timestamp_end=_END,
        duration_seconds=0.0,
        last_run_started_at=_START,
        last_run_ended_at=_END,
        last_run_duration_seconds=0.0,
        run_number=1,
        squads={},
        transactions=[],
        unsold_players=[],
        total_players=0,
        players_sold=0,
        players_unsold=0,
        bid_issues=[],
    )


def make_checkpoint() -> AuctionCheckpoint:
    unsold = Player(
        id="u1",
        name="Unsold",
        position=Position.P,
        team="Team",
        list_price=1,
        status=PlayerStatus.UNSOLD,
    )
    squad = Squad(buyer_id="buyer_1", name="Alpha", budget_initial=500)
    return AuctionCheckpoint(
        schema_version=1,
        document_type="auction_checkpoint",
        timestamp_start=_START,
        timestamp_end=_END,
        duration_seconds=0.0,
        last_run_started_at=_START,
        last_run_ended_at=_END,
        last_run_duration_seconds=0.0,
        run_number=1,
        squads={"buyer_1": squad},
        transactions=[],
        unsold_players=[unsold],
        total_players=1,
        players_sold=0,
        players_unsold=1,
        bid_issues=[],
        players=[unsold],
        simulation=SimulationSnapshot(budget=500, seed=42),
        buyers=[
            BidderSnapshot(
                id="buyer_1",
                name="Alpha",
                strategy="deterministic",
                priority=0,
            )
        ],
        auction_count=0,
        missing_roles={"buyer_1": {"P": 1}},
        error_code="pool_exhausted",
        error="Player pool exhausted before roster completion",
        resume={
            "incomplete_buyer_ids": ["buyer_1"],
            "pool": "unsold_players",
        },
    )
