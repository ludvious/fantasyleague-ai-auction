import json

import httpx
import pytest

from agents.llm_agent import LlmClient, MOCK_BRAVE_KEY


def make_client(handler, brave_key="brave-key"):
    return LlmClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        brave_base_url="https://brave.test/search",
        brave_api_key=brave_key,
        transport=httpx.MockTransport(handler),
    )


def test_chat_posts_expected_payload_and_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert body["model"] == "gpt-4o-mini"
        assert body["temperature"] == 0.7
        assert body["messages"] == [{"role": "user", "content": "ciao"}]
        return httpx.Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "valuto il giocatore",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "submit_bid", "arguments": '{"amount": 12}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

    client = make_client(handler)
    result = client.chat([{"role": "user", "content": "ciao"}], [], "gpt-4o-mini", 0.7)

    assert result["content"] == "valuto il giocatore"
    assert result["finish_reason"] == "tool_calls"
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "submit_bid", "args": {"amount": 12}}
    ]
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}


def test_chat_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        make_client(handler).chat([], [], "gpt-4o-mini", 0.7)


def test_chat_raises_on_malformed_tool_arguments():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c",
                        "type": "function",
                        "function": {"name": "submit_bid", "arguments": "not json"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })

    with pytest.raises(ValueError, match="Malformed"):
        make_client(handler).chat([], [], "gpt-4o-mini", 0.7)


def test_search_news_returns_unavailable_message_for_mock_key():
    client = make_client(
        lambda request: httpx.Response(200, json={}),
        brave_key=MOCK_BRAVE_KEY,
    )
    assert client.search_news("Lautaro infortunio", 3) == "search non disponibile"


def test_search_news_formats_top_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "brave.test"
        assert request.headers["X-Subscription-Token"] == "brave-key"
        return httpx.Response(200, json={
            "web": {"results": [
                {"title": "Notizia 1", "url": "https://example.com/1"},
                {"title": "Notizia 2", "url": "https://example.com/2"},
            ]}
        })

    client = make_client(handler)
    result = client.search_news("Lautaro infortunio", 2)

    assert "Notizia 1" in result
    assert "https://example.com/1" in result


def test_search_news_returns_unavailable_message_on_http_error():
    def handler(request):
        return httpx.Response(503, json={})

    assert make_client(handler).search_news("Lautaro", 2) == "search non disponibile"
