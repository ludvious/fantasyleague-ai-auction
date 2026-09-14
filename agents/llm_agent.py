"""OpenAI-compatible LLM client and the AgentManager bidder."""

from __future__ import annotations

import json
from typing import Any

import httpx

from agents.trace import TraceLogger
from core.models import Player, Squad


MOCK_BRAVE_KEY = "INSERISCI_LA_TUA_BRAVE_API_KEY"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_news": {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Cerca notizie recenti su un giocatore (infortuni, forma, titolarità, mercato, fantacalcio).",
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
    "Cerca notizie recenti su: {query}. Riassumi brevemente in italiano le "
    "informazioni utili per un'asta di fantacalcio (infortuni, forma, "
    "titolarità, mercato)."
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
    ):
        if not api_key:
            raise ValueError("LLM API key must be a non-empty string")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
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

    def search_news(self, query: str, count: int) -> str:
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
            headers={"X-Subscription-Token": self._search["api_key"]},
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
        raise NotImplementedError


class AgentManager:
    """Bidder driven by an OpenAI-compatible function-calling loop.

    Stateless per bid: messages are rebuilt from scratch for every player.
    """

    DEFAULT_TOOLS: tuple[str, ...] = ("search_news", "submit_bid")

    def __init__(
        self,
        buyer_id: str,
        name: str,
        client: LlmClient,
        tracer: TraceLogger,
        *,
        model: str,
        temperature: float,
        role: str | None = None,
        personality: str | None = None,
        system_prompt: str | None = None,
        max_tool_iterations: int = 3,
        tools: tuple[str, ...] = DEFAULT_TOOLS,
        spending_profile: dict[str, float] | None = None,
        target_players: list[str] | None = None,
    ):
        if not buyer_id or not name:
            raise ValueError("buyer_id and name are required")
        self.buyer_id = buyer_id
        self.name = name
        self.client = client
        self.tracer = tracer
        self.model = model
        self.temperature = temperature
        self.role = role or "fantallenatore esperto"
        self.personality = personality or "equilibrata"
        self.system_prompt = system_prompt
        self.max_tool_iterations = max_tool_iterations
        self.tools = tools
        self.spending_profile = spending_profile
        self.target_players = target_players or []

    def _system_prompt(self) -> str:
        if self.system_prompt:
            return self.system_prompt
        lines = [
            "Sei un allenatore-manager che partecipa a un'asta del gioco fantacalcio.",
            f"Ruolo: {self.role}. Personalità: {self.personality}.",
        ]
        if self.spending_profile:
            spending = ", ".join(
                f"{position}: {share:.0%}"
                for position, share in self.spending_profile.items()
            )
            lines.append(f"Distribuzione di spesa ideale per ruolo: {spending}.")
        if self.target_players:
            lines.append(
                f"Giocatori obiettivo: {', '.join(self.target_players)}."
            )
        lines.append(
            "Usa gli strumenti a disposizione: puoi cercare ulteriori info, notizie sul giocatore "
            "con search_news e inviare la tua offerta con submit_bid (0 = passo)."
        )
        return "\n".join(lines)

    def _context(self, player: Player, squad: Squad) -> dict:
        return {
            "player": {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "team": player.team,
                "list_price": player.list_price,
            },
            "roster": [
                {"id": owned.id, "name": owned.name, "position": owned.position.value}
                for owned in squad.players
            ],
            "budget_remaining": squad.budget_remaining,
            "max_bid_allowed": squad.max_bid_allowed,
            "missing_roles": squad.missing_roles(),
        }

    def _user_message(self, context: dict) -> str:
        player = context["player"]
        roster = (
            ", ".join(
                f"{owned['name']} ({owned['position']})"
                for owned in context["roster"]
            )
            or "nessuno"
        )
        missing = (
            ", ".join(
                f"{position}: {count}"
                for position, count in context["missing_roles"].items()
                if count
            )
            or "nessuno"
        )
        return (
            "Giocatore all'asta:\n"
            f"- nome: {player['name']} (id {player['id']})\n"
            f"- ruolo: {player['position']}\n"
            f"- squadra: {player['team']}\n"
            f"- quotazione: {player['list_price']}\n\n"
            f"La tua rosa: {roster}\n"
            f"Budget rimanente: {context['budget_remaining']}\n"
            f"Offerta massima consentita: {context['max_bid_allowed']}\n"
            f"Ruoli mancanti: {missing}\n\n"
            "Invia la tua offerta con submit_bid (amount intero tra 0 e "
            "max_bid_allowed; 0 = passo)."
        )

    @staticmethod
    def _search_count(args: dict) -> int:
        try:
            count = int(args.get("count", 5))
        except (TypeError, ValueError):
            count = 5
        return max(1, min(count, 10))

    def bid(self, player: Player, squad: Squad) -> int:
        context = self._context(player, squad)
        self.tracer.event(player.id, "context", content=context)
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_message(context)},
        ]
        tool_schemas = [
            TOOL_SCHEMAS[name] for name in self.tools if name in TOOL_SCHEMAS
        ]
        for iteration in range(1, self.max_tool_iterations + 1):
            self.tracer.event(
                player.id, "llm_call", iteration, {"model": self.model}
            )
            try:
                response = self.client.chat(
                    messages, tool_schemas, self.model, self.temperature
                )
            except Exception as exc:
                self.tracer.event(
                    player.id,
                    "error",
                    iteration,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                raise
            self.tracer.event(player.id, "usage", iteration, response["usage"])
            if response["content"]:
                self.tracer.event(
                    player.id, "thinking", iteration, {"text": response["content"]}
                )
            if not response["tool_calls"]:
                self.tracer.event(
                    player.id,
                    "no_bid",
                    iteration,
                    {"reason": "stop_without_bid"},
                )
                return 0
            tool_results: list[tuple[str, str, str]] = []
            for call in response["tool_calls"]:
                name = call["name"]
                args = call["args"]
                self.tracer.event(
                    player.id, "tool_call", iteration, {"name": name, "args": args}
                )
                if name == "submit_bid":
                    amount = args.get("amount")
                    if (
                        type(amount) is int
                        and 0 <= amount <= squad.max_bid_allowed
                    ):
                        self.tracer.event(
                            player.id, "bid", iteration, {"amount": amount}
                        )
                        return amount
                    result = f"amount non valido, max è {squad.max_bid_allowed}"
                elif name == "search_news" and name in self.tools:
                    result = self.client.search_news(
                        str(args.get("query", "")), self._search_count(args)
                    )
                else:
                    result = f"strumento '{name}' non disponibile"
                self.tracer.event(
                    player.id, "tool_result", iteration, {"name": name, "result": result}
                )
                tool_results.append((call["id"], name, result))
            messages.append(
                {
                    "role": "assistant",
                    "content": response["content"] or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["args"]),
                            },
                        }
                        for call in response["tool_calls"]
                    ],
                }
            )
            for call_id, name, result in tool_results:
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )
        self.tracer.event(
            player.id,
            "no_bid",
            self.max_tool_iterations,
            {"reason": "iteration_cap"},
        )
        return 0
