import pytest

from core.models import AuctionState, Player, PlayerStatus, Position, Squad


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


def test_auction_state_exposes_only_available_players():
    available = make_player("available")
    sold = make_player("sold")
    sold.status = PlayerStatus.SOLD
    state = AuctionState(
        players=[available, sold],
        squads={},
    )

    assert state.available_players == [available]
