import pytest

from core.models import Player, Position, Squad
from utils.validator import validate_bid


def make_player(player_id: str = "a1") -> Player:
    return Player(
        id=player_id,
        name=player_id,
        position=Position.A,
        team="Team",
        list_price=10,
    )


@pytest.mark.parametrize("bid", [10.0, True, "10", None, -1])
def test_legacy_validate_bid_delegates_strict_domain_validation(bid):
    result = validate_bid(bid, Squad(buyer_id="b1", name="Alpha", budget_initial=30), make_player())

    assert not result.is_valid


def test_legacy_validate_bid_accepts_zero_pass():
    result = validate_bid(0, Squad(buyer_id="b1", name="Alpha", budget_initial=30), make_player())

    assert result.is_valid
