import json

import httpx
import pytest

from agents.llm_client import LlmClient, MOCK_BRAVE_KEY, _format_search_result


def make_client(handler, search=None):
    return LlmClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        search=search,
        transport=httpx.MockTransport(handler),
    )


def brave_search(**overrides):
    config = {
        "provider": "brave",
        "base_url": "https://brave.test/search",
        "api_key": "brave-key",
    }
    config.update(overrides)
    return config


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


def test_search_info_returns_unavailable_message_for_mock_key():
    client = make_client(
        lambda request: httpx.Response(200, json={}),
        search=brave_search(api_key=MOCK_BRAVE_KEY),
    )
    assert client.search_info("Lautaro infortunio", 3) == "search non disponibile"


def test_search_info_returns_unavailable_message_without_search_config():
    client = make_client(lambda request: httpx.Response(200, json={}))
    assert client.search_info("Lautaro infortunio", 3) == "search non disponibile"


def test_search_info_formats_top_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "brave.test"
        assert request.headers["X-Subscription-Token"] == "brave-key"
        assert request.headers["x-custom"] == "custom-value"
        return httpx.Response(200, json={
            "web": {"results": [
                {"title": "Notizia 1", "url": "https://example.com/1"},
                {"title": "Notizia 2", "url": "https://example.com/2"},
            ]}
        })

    client = make_client(
        handler, search=brave_search(headers={"x-custom": "custom-value"})
    )
    result = client.search_info("Lautaro infortunio", 2)

    assert "Notizia 1" in result
    assert "https://example.com/1" in result


def test_format_search_result_without_summary_has_no_leading_blank_line():
    result = _format_search_result("", [("Titolo", "https://u")], 5)

    assert not result.startswith("\n")
    assert "Fonti:" in result


def test_search_info_returns_unavailable_message_on_http_error():
    def handler(request):
        return httpx.Response(503, json={})

    client = make_client(handler, search=brave_search())
    assert client.search_info("Lautaro", 2) == "search non disponibile"


def responses_payload():
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "web_search_call",
                "action": {"type": "search", "query": "Lautaro"},
            },
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Lautaro è in forma e titolare.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Inter Match Center",
                                "url": "https://www.inter.it/it/match",
                            },
                            {
                                "type": "url_citation",
                                "title": "Inter Match Center (dup)",
                                "url": "https://www.inter.it/it/match",
                            },
                            {
                                "type": "url_citation",
                                "title": "Fantacalcio.it",
                                "url": "https://www.fantacalcio.it/news",
                            },
                        ],
                    }
                ],
            },
        ],
    }


def responses_search(**overrides):
    config = {
        "provider": "responses",
        "base_url": "https://search.test",
        "api_key": "search-key",
        "model": "gpt-5.6-luna",
    }
    config.update(overrides)
    return config


def test_search_info_responses_posts_web_search_tool_and_formats():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "search.test"
        assert request.url.path == "/responses"
        assert request.headers["Authorization"] == "Bearer search-key"
        assert body["model"] == "gpt-5.6-luna"
        assert body["tools"] == [{"type": "web_search"}]
        assert "Lautaro infortunio" in body["input"]
        assert body["max_output_tokens"] == 400
        return httpx.Response(200, json=responses_payload())

    client = make_client(handler, search=responses_search())
    result = client.search_info("Lautaro infortunio", 5)

    assert result.startswith("Lautaro è in forma e titolare.")
    assert "Fonti:" in result
    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "2. Fantacalcio.it — https://www.fantacalcio.it/news" in result
    assert "dup" not in result


def test_search_info_responses_slices_sources_to_count():
    def handler(request):
        return httpx.Response(200, json=responses_payload())

    client = make_client(handler, search=responses_search())
    result = client.search_info("Lautaro", 1)

    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "fantacalcio.it" not in result


def test_search_info_responses_without_message_returns_no_results():
    def handler(request):
        payload = responses_payload()
        payload["output"] = [payload["output"][0]]
        return httpx.Response(200, json=payload)

    client = make_client(handler, search=responses_search())
    assert client.search_info("Lautaro", 5) == "nessun risultato"


def test_search_info_responses_on_http_error_returns_unavailable():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler, search=responses_search())
    assert client.search_info("Lautaro", 5) == "search non disponibile"


def anthropic_payload():
    return {
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_01",
                "name": "web_search",
                "input": {"query": "Lautaro"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_01",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "Inter Match Center",
                        "url": "https://www.inter.it/it/match",
                    },
                    {
                        "type": "web_search_result",
                        "title": "Fantacalcio.it",
                        "url": "https://www.fantacalcio.it/news",
                    },
                ],
            },
            {
                "type": "text",
                "text": "Lautaro è in forma e titolare.",
            },
        ]
    }


def anthropic_search(**overrides):
    config = {
        "provider": "anthropic",
        "base_url": "https://anthropic.test",
        "api_key": "anthropic-key",
        "model": "claude-opus-4-8",
    }
    config.update(overrides)
    return config


def test_search_info_anthropic_posts_server_tool_and_formats():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "anthropic.test"
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert body["model"] == "claude-opus-4-8"
        assert body["max_tokens"] == 400
        assert body["tools"] == [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
        ]
        assert "Lautaro infortunio" in body["messages"][0]["content"]
        return httpx.Response(200, json=anthropic_payload())

    client = make_client(handler, search=anthropic_search())
    result = client.search_info("Lautaro infortunio", 5)

    assert result.startswith("Lautaro è in forma e titolare.")
    assert "Fonti:" in result
    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "2. Fantacalcio.it — https://www.fantacalcio.it/news" in result


def test_search_info_anthropic_on_http_error_returns_unavailable():
    def handler(request):
        return httpx.Response(401, json={"error": "no key"})

    client = make_client(handler, search=anthropic_search())
    assert client.search_info("Lautaro", 5) == "search non disponibile"


def test_client_sends_user_agent_and_extra_headers_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "fantasyleague-auction/0.1"
        assert request.headers["x-opencode-session"] == "session-1"
        return httpx.Response(200, json={
            "choices": [{
                "message": {"role": "assistant", "content": "ok", "tool_calls": []},
                "finish_reason": "stop",
            }],
        })

    client = LlmClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        extra_headers={"x-opencode-session": "session-1"},
    )
    client.chat([{"role": "user", "content": "ciao"}], [], "gpt-4o-mini", 0.7)
