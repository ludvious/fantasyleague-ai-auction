import pytest

from checkpoint_fixtures import make_pool_exhaustion_checkpoint
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from core.models import PlayerStatus


class RecordingBidder:
    def __init__(self, buyer_id: str, bids: dict[str, object]):
        self.buyer_id = buyer_id
        self.name = buyer_id.title()
        self.bids = bids
        self.calls: list[str] = []

    def bid(self, player, squad):
        self.calls.append(player.id)
        return self.bids.get(player.id, 0)


def test_resume_auctions_only_unsold_players_and_incomplete_squads():
    checkpoint = make_pool_exhaustion_checkpoint()
    complete_bidder = RecordingBidder("complete", {"u1": 9})
    incomplete_bidder = RecordingBidder("incomplete", {"u1": 4})

    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [complete_bidder, incomplete_bidder],
    )
    report = engine.run()

    assert complete_bidder.calls == []
    assert incomplete_bidder.calls == ["u1"]
    assert report.squads["complete"].is_complete
    assert report.players_sold == report.total_players


def test_resume_selects_only_reactivated_unsold_players():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": 4})],
    )

    selected = engine.select_player()

    assert selected is not None
    assert selected.id == "u1"
    assert selected.status is PlayerStatus.AVAILABLE
    assert all(player.id == "u1" for player in engine.state.available_players)


def test_resume_retains_transactions_and_budgets():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": 4})],
    )

    report = engine.run()

    assert len(report.transactions) == 50
    assert report.squads["complete"].budget_remaining == 475
    assert report.squads["incomplete"].budget_remaining == 472


def test_unsold_again_is_kept_in_next_checkpoint_pool():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": 0})],
    )

    with pytest.raises(AuctionIncompleteError) as caught:
        engine.run()

    next_checkpoint = engine.build_checkpoint(
        checkpoint.simulation,
        checkpoint.buyers,
        caught.value,
        caught.value.missing_roles,
    )

    assert next_checkpoint.run_number == 2
    assert [player.id for player in next_checkpoint.unsold_players] == ["u1"]
    assert next_checkpoint.players[-1].status is PlayerStatus.UNSOLD


def test_completed_resume_returns_accumulated_report():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": 4})],
    )

    report = engine.run()

    assert report.document_type == "auction_report"
    assert report.run_number == 2
    assert report.timestamp_start == checkpoint.timestamp_start
    assert report.duration_seconds >= checkpoint.duration_seconds
    assert report.last_run_duration_seconds >= 0


def test_second_pool_exhaustion_builds_a_new_checkpoint():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": 0})],
    )

    with pytest.raises(AuctionIncompleteError) as caught:
        engine.run()

    next_checkpoint = engine.build_checkpoint(
        checkpoint.simulation,
        checkpoint.buyers,
        caught.value,
        caught.value.missing_roles,
    )

    assert next_checkpoint.document_type == "auction_checkpoint"
    assert next_checkpoint.error_code == "pool_exhausted"
    assert next_checkpoint.run_number == 2
    assert next_checkpoint.auction_count == 50


def test_bid_issues_and_auction_numbers_survive_resume():
    checkpoint = make_pool_exhaustion_checkpoint()
    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [RecordingBidder("incomplete", {"u1": "invalid"})],
    )

    with pytest.raises(AuctionIncompleteError) as caught:
        engine.run()

    next_checkpoint = engine.build_checkpoint(
        checkpoint.simulation,
        checkpoint.buyers,
        caught.value,
        caught.value.missing_roles,
    )

    assert len(next_checkpoint.bid_issues) == 2
    assert next_checkpoint.bid_issues[0].auction_number == 49
    assert next_checkpoint.bid_issues[1].auction_number == 50
    assert next_checkpoint.auction_count == 50


def test_checkpoint_without_unsold_players_is_rejected():
    checkpoint = make_pool_exhaustion_checkpoint()
    checkpoint.players[-1].status = PlayerStatus.SOLD
    checkpoint.unsold_players = []
    checkpoint.players_sold = checkpoint.total_players
    checkpoint.players_unsold = 0

    with pytest.raises(ValueError, match="unsold"):
        AuctionEngine.from_checkpoint(
            checkpoint,
            [RecordingBidder("incomplete", {"u1": 4})],
        )
