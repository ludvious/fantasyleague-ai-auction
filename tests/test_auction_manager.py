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


class FixedBidder:
    def __init__(self, buyer_id: str, name: str, bid_value):
        self.buyer_id = buyer_id
        self.name = name
        self.bid_value = bid_value

    def bid(self, player, squad):
        return self.bid_value


class RaisingBidder:
    def __init__(self, buyer_id: str, name: str):
        self.buyer_id = buyer_id
        self.name = name

    def bid(self, player, squad):
        raise RuntimeError("bidder exploded")



def test_invalid_bidder_is_isolated_and_valid_bidder_can_buy():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player],
        [FixedBidder("bad", "Bad", "10"), FixedBidder("good", "Good", 1)],
        budget=30,
        seed=1,
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "good"
    assert result.all_bids == {"bad": 0, "good": 1}
    assert len(engine.bid_issues) == 1
    issue = engine.bid_issues[0]
    assert issue.auction_number == 1
    assert issue.player_id == "a"
    assert issue.buyer_id == "bad"
    assert issue.code == "invalid_type"
    assert "10" not in issue.model_dump_json()


@pytest.mark.parametrize(
    ("invalid_bid", "code"),
    [
        (10.0, "invalid_type"),
        (True, "invalid_type"),
        ("10", "invalid_type"),
        (None, "invalid_type"),
        (-1, "negative"),
    ],
)
def test_invalid_offer_is_normalized_to_zero_without_stopping_auction(invalid_bid, code):
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player],
        [FixedBidder("bad", "Bad", invalid_bid), FixedBidder("good", "Good", 1)],
        budget=30,
        seed=1,
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.all_bids["bad"] == 0
    assert engine.bid_issues[0].code == code



def test_bidder_exception_is_isolated_and_other_bidders_continue():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player],
        [RaisingBidder("bad", "Bad"), FixedBidder("good", "Good", 1)],
        budget=30,
        seed=1,
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "good"
    assert result.all_bids == {"bad": 0, "good": 1}
    assert engine.bid_issues[0].code == "bidder_exception"
    assert "RuntimeError" in engine.bid_issues[0].message



def test_all_invalid_bidders_leave_player_unsold():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player],
        [FixedBidder("bad", "Bad", 10.0), RaisingBidder("worse", "Worse")],
        budget=30,
        seed=1,
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.UNSOLD_NO_BID
    assert result.all_bids == {"bad": 0, "worse": 0}
    assert player.status is PlayerStatus.UNSOLD
    assert len(engine.bid_issues) == 2



def test_auction_player_uses_canonical_player_for_external_clone():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player], [FixedBidder("b1", "One", 1)], budget=30, seed=1
    )
    canonical = engine.state.players[0]
    clone = canonical.model_copy(deep=True)

    result = engine.auction_player(clone)

    assert result.status is AuctionStatus.SOLD
    assert engine.state.players[0] is canonical
    assert canonical.status is PlayerStatus.SOLD
    assert clone.status is PlayerStatus.AVAILABLE
    assert engine.state.squads["b1"].players[0].id == canonical.id



def test_auction_player_rejects_unknown_player_id():
    engine = AuctionEngine(
        [make_player("a", "A")], [FixedBidder("b1", "One", 1)], budget=30, seed=1
    )

    with pytest.raises(ValueError, match="unknown"):
        engine.auction_player(make_player("missing", "A"))

    assert engine.auction_count == 0


@pytest.mark.parametrize("status", [PlayerStatus.SOLD, PlayerStatus.UNSOLD])
def test_auction_player_rejects_canonical_player_that_is_already_concluded(status):
    engine = AuctionEngine(
        [make_player("a", "A")], [FixedBidder("b1", "One", 1)], budget=30, seed=1
    )
    canonical = engine.state.players[0]
    canonical.status = status
    clone = canonical.model_copy(deep=True)
    clone.status = PlayerStatus.AVAILABLE

    with pytest.raises(ValueError, match="not available"):
        engine.auction_player(clone)

    assert engine.auction_count == 0



def test_purchase_at_maximum_legal_bid_succeeds():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player], [FixedBidder("b1", "One", 6)], budget=30, seed=1
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.price == 6
    assert engine.state.squads["b1"].budget_remaining == 24



def test_offer_above_maximum_is_isolated_and_player_is_unsold():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player], [FixedBidder("b1", "One", 7)], budget=30, seed=1
    )

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.UNSOLD_NO_BID
    assert result.all_bids == {"b1": 0}
    assert engine.bid_issues[0].code == "above_maximum"



def _fill_squad_with_24_players(squad):
    roles = ["P"] * 3 + ["D"] * 8 + ["C"] * 8 + ["A"] * 5
    for index, role in enumerate(roles):
        squad.add_player(make_player(f"owned-{index}", role), 1)



def test_exact_remaining_budget_can_complete_the_roster():
    target = make_player("target", "A")
    engine = AuctionEngine(
        [target], [FixedBidder("b1", "One", 1)], budget=25, seed=1
    )
    squad = engine.state.squads["b1"]
    _fill_squad_with_24_players(squad)

    result = engine.auction_player(target)

    assert result.status is AuctionStatus.SOLD
    assert result.price == 1
    assert squad.budget_remaining == 0
    assert squad.is_complete
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


def test_auction_counters_are_persisted_in_state():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player], [FixedBidder("b1", "One", 1)], budget=30, seed=1
    )

    engine.auction_player(player)

    assert engine.auction_count == 1
    assert engine.state.auction_count == 1
    assert engine.bid_issues is engine.state.bid_issues


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
