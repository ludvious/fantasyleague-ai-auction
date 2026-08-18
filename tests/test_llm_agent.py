import json

import pytest

from agents.llm_agent import AgentManager
from agents.trace import TraceLogger
from core.models import Player, Position, Squad


class FakeClient:
    """Scripted LlmClient double: pops one response per chat call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.messages_seen = []

    def chat(self, messages, tools, model, temperature):
        self.messages_seen.append(list(messages))
        return self.responses.pop(0)

    def search_news(self, query, count):
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
    manager = AgentManager(
        "buyer_1", "Alpha", client, tracer,
        model="gpt-4o-mini", temperature=0.7, **kwargs,
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
        chat_response(tool_call("search_news", {"query": "Lautaro infortunio"})),
        chat_response(tool_call("submit_bid", {"amount": 12})),
    ])
    manager, trace_path = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 12

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("1. Notizia")
    assistant = client.messages_seen[1][-2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "search_news"
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
    assert "amount non valido" in tool_messages[0]["content"]


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
        chat_response(tool_call("search_news", {"query": "x"})),
        chat_response(tool_call("search_news", {"query": "x"})),
    ])
    manager, trace_path = make_manager(tmp_path, client, max_tool_iterations=2)

    assert manager.bid(make_player(), make_squad()) == 0

    assert len(client.messages_seen) == 2
    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["content"] == {"reason": "iteration_cap"}


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
        chat_response(tool_call("search_news", {"query": "x"})),
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


def test_custom_system_prompt_replaces_template(tmp_path):
    client = FakeClient([chat_response(tool_call("submit_bid", {"amount": 5}))])
    manager, _ = make_manager(
        tmp_path, client, system_prompt="Sei un esperto di portieri."
    )

    manager.bid(make_player(), make_squad())

    assert client.messages_seen[0][0]["content"] == "Sei un esperto di portieri."
