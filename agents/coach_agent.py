"""LLM-driven bidder: the CoachAgent decision loop."""

from __future__ import annotations

import json

from loguru import logger

from agents.llm_client import LlmClient, TOOL_SCHEMAS
from agents.trace import TraceLogger
from core.models import AuctionResult, BidValidationError, Player, Squad


class CoachAgent:
    """Bidder driven by an OpenAI-compatible function-calling loop.

    Stateless per bid: messages are rebuilt from scratch for every player.
    """

    DEFAULT_TOOLS: tuple[str, ...] = ("search_info", "submit_bid")

    def __init__(
        self,
        buyer_id: str,
        name: str,
        client: LlmClient,
        tracer: TraceLogger,
        *,
        model: str,
        temperature: float,
        system_prompt: str,
        max_tool_iterations: int = 3,
        tools: tuple[str, ...] = DEFAULT_TOOLS,
    ):
        if not buyer_id or not name:
            raise ValueError("buyer_id and name are required")
        if not system_prompt.strip():
            raise ValueError("system_prompt is required")
        self.buyer_id = buyer_id
        self.name = name
        self.client = client
        self.tracer = tracer
        self.model = model
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.max_tool_iterations = max_tool_iterations
        self.tools = tools

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

    def observe(self, result: AuctionResult, squad: Squad) -> None:
        won = result.winner_id == self.buyer_id
        if won:
            content: dict = {
                "outcome": "won",
                "player": {
                    "id": result.player.id,
                    "name": result.player.name,
                    "position": result.player.position.value,
                    "team": result.player.team,
                },
                "price": result.price,
                "budget_remaining": squad.budget_remaining,
                "roster": [
                    {
                        "id": owned.id,
                        "name": owned.name,
                        "position": owned.position.value,
                    }
                    for owned in squad.players
                ],
                "missing_roles": squad.missing_roles(),
            }
            logger.info(
                "Coach {} ha vinto {} per {} crediti",
                self.buyer_id,
                result.player.name,
                result.price,
            )
        else:
            content = {"outcome": "lost"}
            logger.info(
                "Coach {} non ha vinto {}",
                self.buyer_id,
                result.player.name,
            )
        self.tracer.event(result.player.id, "auction_result", content=content)

    def bid(self, player: Player, squad: Squad) -> int:
        context = self._context(player, squad)
        self.tracer.event(player.id, "context", content=context)
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
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
                logger.info(
                    "Coach {} su {}: {}",
                    self.buyer_id,
                    player.name,
                    response["content"].strip(),
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
                    try:
                        squad.validate_bid(player, amount)
                    except BidValidationError as exc:
                        result = f"offerta rifiutata: {exc}"
                    else:
                        self.tracer.event(
                            player.id, "bid", iteration, {"amount": amount}
                        )
                        return amount
                elif name == "search_info" and name in self.tools:
                    result = self.client.search_info(
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
