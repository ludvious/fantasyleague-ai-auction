from datetime import datetime, timezone

from core.models import (
    AuctionCheckpoint,
    BidderSnapshot,
    BidIssue,
    Player,
    PlayerStatus,
    Position,
    SimulationReport,
    SimulationSnapshot,
    Squad,
    Transaction,
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


def make_pool_exhaustion_checkpoint() -> AuctionCheckpoint:
    complete = Squad(buyer_id="complete", name="Complete", budget_initial=500)
    incomplete = Squad(buyer_id="incomplete", name="Incomplete", budget_initial=500)
    players = []
    transactions = []

    def add_owned(squad: Squad, player_id: str, role: Position) -> None:
        player = Player(
            id=player_id,
            name=player_id,
            position=role,
            team="Team",
            list_price=1,
        )
        squad.add_player(player, 1)
        players.append(player)
        transactions.append(
            Transaction(
                player=player.model_copy(deep=True),
                buyer_id=squad.buyer_id,
                price=1,
                all_bids={squad.buyer_id: 1},
            )
        )

    complete_roles = [Position.P] * 3 + [Position.D] * 8 + [Position.C] * 8 + [Position.A] * 6
    incomplete_roles = [Position.P] * 2 + [Position.D] * 8 + [Position.C] * 8 + [Position.A] * 6
    for index, role in enumerate(complete_roles):
        add_owned(complete, f"complete-{index}", role)
    for index, role in enumerate(incomplete_roles):
        add_owned(incomplete, f"incomplete-{index}", role)

    unsold = Player(
        id="u1",
        name="Unsold goalkeeper",
        position=Position.P,
        team="Team",
        list_price=1,
        status=PlayerStatus.UNSOLD,
    )
    players.append(unsold)

    return AuctionCheckpoint(
        schema_version=1,
        document_type="auction_checkpoint",
        timestamp_start=_START,
        timestamp_end=_END,
        duration_seconds=3.0,
        last_run_started_at=_START,
        last_run_ended_at=_END,
        last_run_duration_seconds=3.0,
        run_number=1,
        squads={"complete": complete, "incomplete": incomplete},
        transactions=transactions,
        unsold_players=[unsold],
        total_players=len(players),
        players_sold=len(transactions),
        players_unsold=1,
        bid_issues=[
            BidIssue(
                auction_number=49,
                player_id="incomplete-23",
                buyer_id="incomplete",
                code="previous_issue",
                message="preserved diagnostic",
            )
        ],
        players=players,
        simulation=SimulationSnapshot(budget=500, seed=42),
        buyers=[
            BidderSnapshot(
                id="complete",
                name="Complete",
                strategy="deterministic",
                priority=0,
            ),
            BidderSnapshot(
                id="incomplete",
                name="Incomplete",
                strategy="deterministic",
                priority=1,
            ),
        ],
        auction_count=len(transactions),
        missing_roles={"incomplete": {"P": 1}},
        error_code="pool_exhausted",
        error="Player pool exhausted before roster completion",
        resume={
            "incomplete_buyer_ids": ["incomplete"],
            "pool": "unsold_players",
        },
    )
