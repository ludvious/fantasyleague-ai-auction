"""OpenAI-compatible LLM client and the AgentManager bidder."""

from __future__ import annotations

import json
from typing import Any

import httpx


MOCK_BRAVE_KEY = "INSERISCI_LA_TUA_BRAVE_API_KEY"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_news": {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Cerca notizie recenti su un giocatore (infortuni, forma, mercato).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    "submit_bid": {
        "type": "function",
        "function": {
            "name": "submit_bid",
            "description": (
                "Invia la tua offerta per il giocatore. amount intero tra 0 e "
                "max_bid_allowed (0 = passo)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer", "minimum": 0},
                },
                "required": ["amount"],
            },
        },
    },
}


class LlmClient:
    """One shared httpx client for chat completions and Brave search."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        brave_base_url: str,
        brave_api_key: str,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("LLM API key must be a non-empty string")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
        )
        self._api_key = api_key
        self.brave_base_url = brave_base_url
        self.brave_api_key = brave_api_key

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        temperature: float,
    ) -> dict:
        response = self._http.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Malformed chat completion response: {exc}") from exc
        tool_calls = []
        for call in message.get("tool_calls") or []:
            try:
                args = json.loads(call["function"]["arguments"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Malformed tool call arguments: {exc}") from exc
            if not isinstance(args, dict):
                raise ValueError("Malformed tool call arguments: not a mapping")
            tool_calls.append(
                {
                    "id": str(call.get("id", "")),
                    "name": str(call["function"]["name"]),
                    "args": args,
                }
            )
        usage = payload.get("usage") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
            "finish_reason": choice.get("finish_reason") or "",
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
            },
        }

    def search_news(self, query: str, count: int) -> str:
        """Best-effort Brave search; returns an Italian tool message."""
        if not self.brave_api_key or self.brave_api_key == MOCK_BRAVE_KEY:
            return "search non disponibile"
        try:
            response = self._http.get(
                self.brave_base_url,
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": self.brave_api_key},
            )
            response.raise_for_status()
            payload = response.json()
            results = (payload.get("web") or {}).get("results") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            return "search non disponibile"
        lines = [
            f"{index + 1}. {result.get('title', '')} — {result.get('url', '')}"
            for index, result in enumerate(results)
        ]
        return "\n".join(lines) if lines else "nessun risultato"
