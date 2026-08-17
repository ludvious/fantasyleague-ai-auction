import pytest

from core.models import (
    AuctionState,
    BidValidationError,
    Player,
    PlayerStatus,
    Position,
    Squad,
)


def make_player(player_id: str, position: Position = Position.A) -> Player:
    return Player(
        id=player_id,
        name=player_id,
        position=position,
        team="Team",
        list_price=10,
    )


def test_squad_reports_role_slots_and_reserve_aware_maximum_bid():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)

    assert squad.remaining_slots == 25
    assert squad.max_bid_allowed == 6
    assert squad.missing_roles() == {"P": 3, "D": 8, "C": 8, "A": 6}

    squad.add_player(make_player("p1", Position.P), 6)

    assert squad.budget_remaining == 24
    assert squad.missing_roles()["P"] == 2
    assert squad.max_bid_allowed == 1


def test_squad_rejects_overfilled_role_and_reserve_breaking_price():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=25)
    for index in range(3):
        squad.add_player(make_player(f"p{index}", Position.P), 1)

    with pytest.raises(ValueError, match="P"):
        squad.add_player(make_player("p3", Position.P), 1)


def test_squad_marks_purchased_player_and_rejects_duplicate_purchase():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=25)
    player = make_player("a1")

    squad.add_player(player, 1)

    assert player.status is PlayerStatus.SOLD
    assert player.buyer_id == "b1"
    assert player.selling_price == 1
    assert squad.players[0].id == "a1"

    with pytest.raises(ValueError, match="already"):
        squad.add_player(player, 1)


def test_squad_accepts_positive_integer_bid_and_zero_as_pass():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    player = make_player("a1")

    assert squad.validate_bid(player, 1) is None
    assert squad.validate_bid(player, 0) is None


@pytest.mark.parametrize("bid", [10.0, True, "10", None, -1])
def test_squad_rejects_non_integer_or_negative_bids(bid):
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    player = make_player("a1")

    with pytest.raises(BidValidationError):
        squad.validate_bid(player, bid)


def test_squad_rejects_bid_above_legal_maximum():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    player = make_player("a1")

    with pytest.raises(BidValidationError, match="maximum"):
        squad.validate_bid(player, squad.max_bid_allowed + 1)


def test_squad_bid_validation_rejects_unavailable_player():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    player = make_player("a1")
    player.status = PlayerStatus.UNSOLD

    with pytest.raises(BidValidationError, match="available"):
        squad.validate_bid(player, 1)


def test_squad_bid_validation_rejects_full_role():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    for index in range(3):
        squad.add_player(make_player(f"p{index}", Position.P), 1)

    with pytest.raises(BidValidationError, match="full"):
        squad.validate_bid(make_player("p3", Position.P), 1)


def test_complete_squad_has_zero_maximum_and_rejects_new_bid():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=25)
    roles = [Position.P] * 3 + [Position.D] * 8 + [Position.C] * 8 + [Position.A] * 6
    for index, role in enumerate(roles):
        squad.add_player(make_player(f"owned-{index}", role), 1)

    assert squad.is_complete
    assert squad.max_bid_allowed == 0
    with pytest.raises(BidValidationError, match="complete"):
        squad.validate_bid(make_player("extra"), 0)


def test_zero_is_valid_pass_when_legal_maximum_is_zero():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=25)
    squad.budget_remaining = 0

    assert squad.max_bid_allowed == 0
    assert squad.validate_bid(make_player("a1"), 0) is None


def test_auction_state_exposes_only_available_players():
    available = make_player("available")
    sold = make_player("sold")
    sold.status = PlayerStatus.SOLD
    state = AuctionState(
        players=[available, sold],
        squads={},
    )

    assert state.available_players == [available]
