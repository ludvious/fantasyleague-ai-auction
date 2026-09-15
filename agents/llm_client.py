"""HTTP client, tool schemas, and web search for CoachAgent."""

from __future__ import annotations

import json
from typing import Any

import httpx


MOCK_BRAVE_KEY = "INSERISCI_LA_TUA_BRAVE_API_KEY"

USER_AGENT = "fantasyleague-auction/0.1"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_info": {
        "type": "function",
        "function": {
            "name": "search_info",
            "description": "Cerca info recenti su un giocatore (infortuni, forma, titolarità, ruolo, competenze, mercato, fantacalcio).",
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


SEARCH_PROMPT = (
    "Cerca info recenti su: {query}. Riassumi brevemente in italiano le "
    "informazioni utili per un'asta di fantacalcio (infortuni, forma, "
    "titolarità, ruolo, competenze, mercato)."
)


def _format_search_result(
    summary: str, sources: list[tuple[str, str]], count: int
) -> str:
    by_url: dict[str, tuple[str, str]] = {}
    for title, url in sources:
        by_url.setdefault(url, (title, url))
    unique = list(by_url.values())[: max(1, count)]
    lines = []
    if summary:
        lines.append(summary)
    if unique:
        if lines:
            lines.append("")
        lines.append("Fonti:")
        lines.extend(
            f"{index}. {title} — {url}"
            for index, (title, url) in enumerate(unique, start=1)
        )
    return "\n".join(lines)


class LlmClient:
    """One shared httpx client for chat completions and web search."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        search: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        if not api_key:
            raise ValueError("LLM API key must be a non-empty string")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": USER_AGENT, **(extra_headers or {})},
        )
        self._api_key = api_key
        self._search = search

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

    def search_info(self, query: str, count: int) -> str:
        """Best-effort web search; returns an Italian tool message."""
        search = self._search
        if (
            not search
            or not search.get("api_key")
            or search.get("api_key") == MOCK_BRAVE_KEY
        ):
            return "search non disponibile"
        provider = str(search.get("provider", ""))
        try:
            if provider == "responses":
                return self._search_responses(query, count)
            if provider == "anthropic":
                return self._search_anthropic(query, count)
            return self._search_brave(query, count)
        except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError):
            return "search non disponibile"

    def _search_brave(self, query: str, count: int) -> str:
        response = self._http.get(
            self._search["base_url"],
            params={"q": query, "count": count},
            headers={
                **(self._search.get("headers") or {}),
                "X-Subscription-Token": self._search["api_key"],
            },
        )
        response.raise_for_status()
        payload = response.json()
        results = (payload.get("web") or {}).get("results") or []
        lines = [
            f"{index + 1}. {result.get('title', '')} — {result.get('url', '')}"
            for index, result in enumerate(results)
        ]
        return "\n".join(lines) if lines else "nessun risultato"

    def _search_responses(self, query: str, count: int) -> str:
        response = self._http.post(
            f"{self._search['base_url']}/responses",
            headers={
                "Authorization": f"Bearer {self._search['api_key']}",
                **(self._search.get("headers") or {}),
            },
            json={
                "model": self._search["model"],
                "input": SEARCH_PROMPT.format(query=query),
                "tools": [{"type": "web_search"}],
                "max_output_tokens": int(
                    self._search.get("max_output_tokens", 400)
                ),
            },
        )
        response.raise_for_status()
        payload = response.json()
        summary_parts = []
        sources: list[tuple[str, str]] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                summary_parts.append(str(part.get("text") or ""))
                for annotation in part.get("annotations") or []:
                    if (
                        annotation.get("type") == "url_citation"
                        and annotation.get("url")
                    ):
                        sources.append(
                            (
                                str(annotation.get("title") or ""),
                                str(annotation["url"]),
                            )
                        )
        summary = "\n".join(part for part in summary_parts if part).strip()
        if not summary and not sources:
            return "nessun risultato"
        return _format_search_result(summary, sources, count)

    def _search_anthropic(self, query: str, count: int) -> str:
        response = self._http.post(
            f"{self._search['base_url']}/v1/messages",
            headers={
                "x-api-key": self._search["api_key"],
                "anthropic-version": "2023-06-01",
                **(self._search.get("headers") or {}),
            },
            json={
                "model": self._search["model"],
                "max_tokens": int(
                    self._search.get("max_output_tokens", 400)
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": SEARCH_PROMPT.format(query=query),
                    }
                ],
                "tools": [
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 1,
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        summary_parts = []
        sources: list[tuple[str, str]] = []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                summary_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "web_search_tool_result":
                for result in block.get("content") or []:
                    if (
                        result.get("type") == "web_search_result"
                        and result.get("url")
                    ):
                        sources.append(
                            (
                                str(result.get("title") or ""),
                                str(result["url"]),
                            )
                        )
        summary = "\n".join(part for part in summary_parts if part).strip()
        if not summary and not sources:
            return "nessun risultato"
        return _format_search_result(summary, sources, count)
