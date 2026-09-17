import json

import pytest
from loguru import logger

from agents.coach_agent import CoachAgent
from agents.trace import TraceLogger
from core.models import (
    AuctionResult,
    AuctionStatus,
    Player,
    Position,
    Squad,
)


class FakeClient:
    """Scripted LlmClient double: pops one response per chat call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.messages_seen = []
        self.tools_seen = []

    def chat(self, messages, tools, model, temperature):
        self.messages_seen.append(list(messages))
        self.tools_seen.append(list(tools))
        return self.responses.pop(0)

    def search_info(self, query, count):
        return "1. Notizia di prova — https://example.com"


def tool_call(name, args, call_id="call_1"):
    return {"id": call_id, "name": name, "args": args}


def chat_response(*calls, content="", finish_reason="tool_calls"):
    return {
        "content": content,
        "tool_calls": list(calls),
        "finish_reason": finish_reason,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def make_manager(tmp_path, client, **kwargs):
    tracer = TraceLogger(tmp_path / "traces", "buyer_1")
    manager = CoachAgent(
        "buyer_1", "Alpha", client, tracer,
        model="gpt-4o-mini", temperature=0.7,
        system_prompt="Sei un coach di prova.", **kwargs,
    )
    return manager, tmp_path / "traces" / "buyer_1.jsonl"


def make_player():
    return Player(
        id="pl_1", name="Lautaro", position=Position.A,
        team="Inter", list_price=50,
    )


def make_squad():
    return Squad(buyer_id="buyer_1", name="Alpha", budget_initial=500)


def phases(trace_path):
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["phase"] for line in lines]


def test_search_then_submit_returns_bid(tmp_path):
    client = FakeClient([
        chat_response(tool_call("search_info", {"query": "Lautaro infortunio"})),
        chat_response(tool_call("submit_bid", {"amount": 12})),
    ])
    manager, trace_path = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 12

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("1. Notizia")
    assistant = client.messages_seen[1][-2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_info"
    assert phases(trace_path) == [
        "context", "llm_call", "usage", "tool_call", "tool_result",
        "llm_call", "usage", "tool_call", "bid",
    ]


def test_submit_bid_out_of_range_self_corrects(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, trace_path = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 10

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert "offerta rifiutata" in tool_messages[0]["content"]
    assert "legal maximum" in tool_messages[0]["content"]


def test_wrong_type_bid_self_corrects(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": "10"})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, trace_path = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 10

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert "Python int" in tool_messages[0]["content"]


def test_unfixable_role_bid_exhausts_iterations(tmp_path):
    squad = make_squad()
    for index in range(6):
        player = Player(
            id=f"a{index}", name=f"Attaccante {index}",
            position=Position.A, team="Inter", list_price=1,
        )
        squad.add_player(player, 1)
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 1})),
        chat_response(tool_call("submit_bid", {"amount": 0})),
    ])
    manager, trace_path = make_manager(tmp_path, client, max_tool_iterations=2)

    assert manager.bid(make_player(), squad) == 0

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["content"] == {"reason": "iteration_cap"}


def test_reasoning_is_logged(tmp_path):
    client = FakeClient([
        chat_response(
            tool_call("submit_bid", {"amount": 5}),
            content="Stimo 5 crediti.",
        )
    ])
    manager, _ = make_manager(tmp_path, client)
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="INFO", format="{message}")
    try:
        manager.bid(make_player(), make_squad())
    finally:
        logger.remove(sink_id)

    assert any("Stimo 5 crediti." in str(message) for message in messages)


def test_stop_without_submit_bid_returns_zero(tmp_path):
    client = FakeClient([
        chat_response(content="non posso offrire", finish_reason="stop")
    ])
    manager, trace_path = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 0

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "no_bid"
    assert last["content"] == {"reason": "stop_without_bid"}


def test_iteration_cap_returns_zero(tmp_path):
    client = FakeClient([
        chat_response(tool_call("search_info", {"query": "x"})),
        chat_response(tool_call("search_info", {"query": "x"})),
    ])
    manager, trace_path = make_manager(tmp_path, client, max_tool_iterations=2)

    assert manager.bid(make_player(), make_squad()) == 0

    assert len(client.messages_seen) == 2
    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["content"] == {"reason": "iteration_cap"}


def test_rejected_bid_earns_extra_llm_call(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, _ = make_manager(
        tmp_path, client, max_tool_iterations=1, max_bid_retries=1
    )

    assert manager.bid(make_player(), make_squad()) == 10

    assert len(client.messages_seen) == 2


def test_rejected_bid_stops_after_retry_budget(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("submit_bid", {"amount": 8888})),
    ])
    manager, trace_path = make_manager(
        tmp_path, client, max_tool_iterations=1, max_bid_retries=1
    )

    assert manager.bid(make_player(), make_squad()) == 0

    assert len(client.messages_seen) == 2
    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "no_bid"
    assert last["content"] == {"reason": "iteration_cap"}


def test_retry_after_search_offers_only_submit_bid(tmp_path):
    client = FakeClient([
        chat_response(tool_call("search_info", {"query": "Lautaro"})),
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, _ = make_manager(
        tmp_path, client, max_tool_iterations=2, max_bid_retries=1
    )

    assert manager.bid(make_player(), make_squad()) == 10

    names = [schema["function"]["name"] for schema in client.tools_seen[2]]
    assert names == ["submit_bid"]


def test_retry_without_prior_search_keeps_search_available(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("search_info", {"query": "Lautaro"})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, _ = make_manager(
        tmp_path, client, max_tool_iterations=2, max_bid_retries=1
    )

    assert manager.bid(make_player(), make_squad()) == 10

    names = [schema["function"]["name"] for schema in client.tools_seen[1]]
    assert names == ["search_info", "submit_bid"]


def test_chat_exception_is_traced_and_propagates(tmp_path):
    def explode(messages, tools, model, temperature):
        raise RuntimeError("network down")

    client = FakeClient([])
    client.chat = explode
    manager, trace_path = make_manager(tmp_path, client)

    with pytest.raises(RuntimeError, match="network down"):
        manager.bid(make_player(), make_squad())

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "error"
    assert "network down" in last["content"]["error"]


def test_disabled_search_tool_is_rejected(tmp_path):
    client = FakeClient([
        chat_response(tool_call("search_info", {"query": "x"})),
        chat_response(tool_call("submit_bid", {"amount": 3})),
    ])
    manager, trace_path = make_manager(tmp_path, client, tools=("submit_bid",))

    assert manager.bid(make_player(), make_squad()) == 3

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert "non disponibile" in tool_messages[0]["content"]


def test_context_trace_contains_full_payload(tmp_path):
    client = FakeClient([chat_response(tool_call("submit_bid", {"amount": 5}))])
    manager, trace_path = make_manager(tmp_path, client)

    manager.bid(make_player(), make_squad())

    first = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["phase"] == "context"
    assert first["content"]["player"]["name"] == "Lautaro"
    assert first["content"]["budget_remaining"] == 500
    assert first["content"]["max_bid_allowed"] == 476
    assert first["content"]["missing_roles"] == {"P": 3, "D": 8, "C": 8, "A": 6}


def test_system_prompt_is_sent_as_first_message(tmp_path):
    client = FakeClient([chat_response(tool_call("submit_bid", {"amount": 5}))])
    manager, _ = make_manager(tmp_path, client)

    manager.bid(make_player(), make_squad())

    assert client.messages_seen[0][0] == {
        "role": "system",
        "content": "Sei un coach di prova.",
    }


def test_observe_winner_traces_updated_roster(tmp_path):
    manager, trace_path = make_manager(tmp_path, FakeClient([]))
    player = make_player()
    squad = make_squad()
    squad.add_player(player, 7)
    result = AuctionResult(
        player=player.model_copy(deep=True),
        winner_id="buyer_1",
        price=7,
        all_bids={"buyer_1": 7},
        status=AuctionStatus.SOLD,
    )

    manager.observe(result, squad)

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "auction_result"
    assert last["content"]["outcome"] == "won"
    assert last["content"]["price"] == 7
    assert last["content"]["budget_remaining"] == 493
    assert last["content"]["roster"] == [
        {"id": "pl_1", "name": "Lautaro", "position": "A"}
    ]
    assert last["content"]["missing_roles"]["A"] == 5


def test_observe_loser_is_minimal(tmp_path):
    manager, trace_path = make_manager(tmp_path, FakeClient([]))
    result = AuctionResult(
        player=make_player().model_copy(deep=True),
        winner_id="other",
        price=3,
        all_bids={"buyer_1": 0, "other": 3},
        status=AuctionStatus.SOLD,
    )

    manager.observe(result, make_squad())

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "auction_result"
    assert last["content"] == {"outcome": "lost"}
