import random

from agents.buyer_agent import DeterministicBidder, RandomBidder
from core.models import Player, Position, Squad


def make_player(player_id: str, position: Position = Position.A, list_price: int = 99) -> Player:
    return Player(
        id=player_id,
        name=player_id,
        position=position,
        team="Team",
        list_price=list_price,
    )


def test_deterministic_bidder_bids_zero_when_role_is_full():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=100)
    for index in range(3):
        squad.add_player(make_player(f"p{index}", Position.P, 1), 1)

    bidder = DeterministicBidder("b1", "Alpha", priority=2)

    assert bidder.bid(make_player("new", Position.P, 1), squad) == 0


def test_deterministic_bidder_uses_priority_not_excel_quotation_as_starting_price():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=500)
    bidder = DeterministicBidder("b1", "Alpha", priority=2)

    assert bidder.bid(make_player("a", list_price=99), squad) == 3


class AlwaysZeroRandom:
    def randint(self, lower, upper):
        assert lower == 0
        assert upper >= 0
        return 0


def test_random_bidder_can_return_zero():
    player = make_player("a")
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=500)

    bid = RandomBidder("b1", "Alpha", AlwaysZeroRandom()).bid(player, squad)

    assert bid == 0


def test_random_bidder_returns_zero_when_legal_maximum_is_zero():
    player = make_player("a")
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=500)
    squad.budget_remaining = 0

    assert squad.max_bid_allowed == 0
    assert RandomBidder("b1", "Alpha", AlwaysZeroRandom()).bid(player, squad) == 0


def test_random_bidder_is_reproducible_with_equal_seeds():
    player = make_player("a")
    first = RandomBidder(
        "b1", "Alpha", random.Random(42)
    ).bid(player, Squad(buyer_id="b1", name="Alpha", budget_initial=500))
    second = RandomBidder(
        "b1", "Alpha", random.Random(42)
    ).bid(player, Squad(buyer_id="b1", name="Alpha", budget_initial=500))

    assert first == second
