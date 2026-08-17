import pytest

from agents.buyer_agent import DeterministicBidder
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from core.models import AuctionStatus, Player, PlayerStatus, Position


def make_player(player_id: str, role: str) -> Player:
    return Player(
        id=player_id,
        name=player_id,
        position=Position(role),
        team="Team",
        list_price=50,
    )


class ZeroBidder:
    def __init__(self, buyer_id: str, name: str):
        self.buyer_id = buyer_id
        self.name = name

    def bid(self, player, squad):
        return 0


def test_tied_highest_bid_makes_player_unsold_and_removes_it():
    players = [make_player("a", "A")]
    bidders = [
        DeterministicBidder("b1", "One", priority=1),
        DeterministicBidder("b2", "Two", priority=1),
    ]
    engine = AuctionEngine(players, bidders, budget=25, seed=1)

    result = engine.auction_player(players[0])

    assert result.status is AuctionStatus.UNSOLD_TIE
    assert players[0].status is PlayerStatus.UNSOLD
    assert engine.state.available_players == []
    assert engine.state.transactions == []


def test_all_zero_bids_make_player_unsold():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player],
        [ZeroBidder("b1", "One"), ZeroBidder("b2", "Two")],
        budget=25,
        seed=1,
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.UNSOLD_NO_BID
    assert result.max_bid == 0
    assert player.status is PlayerStatus.UNSOLD


def test_unique_positive_bid_records_one_purchase():
    players = [make_player("a", "A")]
    bidders = [
        DeterministicBidder("b1", "One", priority=1),
        DeterministicBidder("b2", "Two", priority=0),
    ]
    engine = AuctionEngine(players, bidders, budget=30, seed=1)

    result = engine.auction_player(players[0])

    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "b1"
    assert result.price == 2
    assert len(engine.state.transactions) == 1
    assert engine.state.squads["b1"].team_size == 1


def test_pool_exhaustion_reports_missing_roles():
    players = [make_player("a", "A")]
    bidders = [
        DeterministicBidder("b1", "One", priority=1),
        DeterministicBidder("b2", "Two", priority=0),
    ]
    engine = AuctionEngine(players, bidders, budget=500, seed=1)

    with pytest.raises(AuctionIncompleteError) as caught:
        engine.run()

    assert caught.value.missing_roles["b1"]["P"] == 3
    assert engine.state.players[0].status is PlayerStatus.SOLD


def test_complete_role_is_excluded_from_bidding():
    player = make_player("new", "P")
    bidders = [
        DeterministicBidder("b1", "One", priority=1),
        DeterministicBidder("b2", "Two", priority=0),
    ]
    engine = AuctionEngine([player], bidders, budget=30, seed=1)
    squad = engine.state.squads["b1"]
    for index in range(3):
        squad.add_player(make_player(f"old-{index}", "P"), 1)

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "b2"
    assert result.all_bids == {"b1": 0, "b2": 1}
