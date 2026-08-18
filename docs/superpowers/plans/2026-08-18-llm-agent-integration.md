# LLM Agent Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Once the task is finished, use pi command /handoff next task for execute task into a new clear session

**Goal:** Add LLM-driven bidders (OpenAI-compatible function-calling loop), per-agent JSONL trace logs, a parallel bid-collection path, a `benchmark` CLI command with pure metrics, and sidecar-based resume of checkpoints containing `llm` buyers — without changing the version-1 report/checkpoint contracts.

**Architecture:** `AgentManager` implements the existing `Bidder` protocol and loops over `chat` calls until a valid `submit_bid` tool call arrives; a single shared `LlmClient` (httpx) is constructed once per run and is thread-safe, which the parallel `_collect_bids` (`ThreadPoolExecutor`) relies on. Each `AgentManager` owns a `TraceLogger` writing `logs/traces/<run_dir>/<buyer_id>.jsonl`. The engine stays sidecar-agnostic; `main.py` is the only place that maps checkpoints/snapshots to LLM configuration, writing and reading the auto-generated `checkpoint.llm.yaml` sidecar.

**Tech Stack:** Python 3, Pydantic, httpx (already in `requirements.txt`), PyYAML, stdlib `csv`/`json`/`statistics`/`concurrent.futures`, pytest. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-08-18-llm-agent-integration-design.md`

## Global Constraints

- Checkpoint contract v1 is immutable: `BidderSnapshot {id, name, strategy, priority}`, `SimulationSnapshot {budget, seed}`, `document_type: auction_checkpoint`. An LLM buyer's snapshot is a standard entry with `strategy: "llm"`.
- The engine (`core/auction_manager.py`) only ever receives `Bidder` objects; it never reads LLM config, traces, or sidecars.
- `AgentManager.bid(player, squad) -> int` returns only an int; input context is the auctioned player plus the agent's own squad/budget/`max_bid_allowed`. Opponent state is out of scope.
- OpenAI-compatible APIs only. The API key is read from `os.environ[api_key_env]`; only the env var *name* may appear in config files and the sidecar. A missing variable is a clear pre-auction error (exit 1).
- Fixed tool set `{search_news, submit_bid}`; `submit_bid` must always be enabled. `search_news` uses the Brave free tier and degrades to the tool message `"search non disponibile"` when the key is missing, the mock placeholder, or the request fails.
- Prompts are in Italian. Budget economy is not a hard constraint.
- `_collect_bids` uses a per-call `ThreadPoolExecutor(max_workers=len(bidders))`; `validate_bid` and `_record_bid_issue` stay in the main thread in bidder order, so issue ordering and auction outcomes are identical to the sequential version.
- Traces go to `logs/traces/<run_dir>/<buyer_id>.jsonl`, one JSON object per line, flushed immediately. Only `llm` buyers trace. `<run_dir>` is chosen by the caller (`main.py` or `benchmark`), never by the engine.
- Sidecar: `<checkpoint>.json` -> `<checkpoint>.llm.yaml`, `schema_version: 1`, written only when the checkpoint has at least one `llm` buyer. With `--resume`, the checkpoint (+ sidecar) is authoritative; `--config` stays ignored.
- Benchmark: run `i` uses `seed + i`; one fresh `AuctionEngine` per run with freshly deep-copied players; pool exhaustion in a run saves the partial report and records `completed: false` (not a crash); benchmark runs never resume.
- No new dependencies; metrics output uses stdlib `csv`/`json` only.
- After every task: `venv/bin/pytest -q -W error` must pass the entire suite (103 tests green at HEAD `a9c0701` before this work).

---

## File Structure

```
Create:
- agents/trace.py                 TraceLogger: per-agent JSONL events
- agents/llm_agent.py             LlmClient (shared httpx) + AgentManager (Bidder)
- benchmark/__init__.py           empty package marker
- benchmark/metrics.py            pure metric functions over report JSON + trace JSONL
- configs/llm.yaml                example LLM configuration (mock Brave key)
- tests/test_trace.py             TraceLogger tests
- tests/test_llm_client.py        LlmClient tests via httpx.MockTransport
- tests/test_llm_agent.py         AgentManager loop tests with a fake client
- tests/test_metrics.py           pure metrics tests on synthetic fixtures
- tests/test_benchmark.py         benchmark command end-to-end tests (fake client)

Modify:
- core/auction_manager.py         parallel _collect_bids + partial_report()
- main.py                         LLM config validation, _build_bidders LLM branch,
                                  trace run dirs, sidecar save/load, benchmark subcommand
- .gitignore                      ignore /logs/traces/
- tests/test_cli.py               LLM validation + sidecar resume tests, one message update
- tests/test_auction_manager.py   parallel-collection behavior tests
- tests/test_imports.py           import the new runtime modules
- README.md, docs/project.md,
  docs/roadmap.md                 document LLM agents, benchmark, sidecar resume
```

---

### Task 1: Per-agent JSONL trace logger

**Files:**
- Create: `agents/trace.py`
- Test: `tests/test_trace.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `TraceLogger(run_dir: str | Path, buyer_id: str)` with `event(player_id: str, phase: str, iteration: int | None = None, content: Any = None) -> None`. Creates `<run_dir>/<buyer_id>.jsonl`, appends one JSON object per event, flushes immediately. Later tasks construct one `TraceLogger` per LLM buyer.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trace.py`:

```python
import json

from agents.trace import TraceLogger


def test_trace_logger_appends_one_json_object_per_event(tmp_path):
    tracer = TraceLogger(tmp_path / "traces", "buyer_1")
    tracer.event("pl_1", "context", content={"player": "Lautaro"})
    tracer.event("pl_1", "bid", iteration=2, content={"amount": 12})
    tracer.event(
        "pl_1",
        "usage",
        iteration=2,
        content={"prompt_tokens": 5, "completion_tokens": 3},
    )

    lines = (tmp_path / "traces" / "buyer_1.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]

    assert len(records) == 3
    assert records[0]["buyer_id"] == "buyer_1"
    assert records[0]["player_id"] == "pl_1"
    assert records[0]["phase"] == "context"
    assert records[0]["content"] == {"player": "Lautaro"}
    assert "ts" in records[0]
    assert "iteration" not in records[0]
    assert records[1]["iteration"] == 2
    assert records[1]["content"] == {"amount": 12}


def test_trace_logger_creates_missing_directories(tmp_path):
    tracer = TraceLogger(tmp_path / "deep" / "nested" / "traces", "buyer_2")
    tracer.event("pl_1", "no_bid", content={"reason": "iteration_cap"})
    assert (tmp_path / "deep" / "nested" / "traces" / "buyer_2.jsonl").exists()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_trace.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.trace'`.

- [ ] **Step 3: Implement the trace logger**

Create `agents/trace.py`:

```python
"""Per-agent JSONL trace logger, separate from the app logger and documents."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
    """Append one JSON object per event to <run_dir>/<buyer_id>.jsonl."""

    def __init__(self, run_dir: str | Path, buyer_id: str):
        self.buyer_id = buyer_id
        self.path = Path(run_dir) / f"{buyer_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Single writer per agent; append mode matches resumed runs' fresh dirs.
        self._file = self.path.open("a", encoding="utf-8")

    def event(
        self,
        player_id: str,
        phase: str,
        iteration: int | None = None,
        content: Any = None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "buyer_id": self.buyer_id,
            "player_id": player_id,
            "phase": phase,
        }
        if iteration is not None:
            record["iteration"] = iteration
        if content is not None:
            record["content"] = content
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()
```

Add this line to `.gitignore` (e.g. under the data/ignore section):

```
/logs/traces/
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_trace.py`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add agents/trace.py tests/test_trace.py .gitignore
git commit -m "feat: add per-agent JSONL trace logger"
```

---

### Task 2: Shared httpx LLM client

**Files:**
- Create: `agents/llm_agent.py` (LlmClient + tool schemas only in this task)
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces: `LlmClient(base_url: str, api_key: str, brave_base_url: str, brave_api_key: str, timeout_seconds: int = 30, transport: httpx.BaseTransport | None = None)` with:
  - `chat(messages: list[dict], tools: list[dict], model: str, temperature: float) -> dict` returning `{"content": str, "tool_calls": [{"id", "name", "args"}], "finish_reason": str, "usage": {"prompt_tokens", "completion_tokens"}}`; raises on HTTP/API/JSON errors.
  - `search_news(query: str, count: int) -> str` returning an Italian tool message; best-effort.
- Produces: module constants `TOOL_SCHEMAS: dict[str, dict]` (the two spec schemas wrapped in the standard OpenAI `{"type": "function", "function": {...}}` shape) and `MOCK_BRAVE_KEY = "INSERISCI_LA_TUA_BRAVE_API_KEY"`.
- Consumed by: Task 3 (`AgentManager`) and Task 5 (`main._make_llm_client`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_client.py`:

```python
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
        assert request.url.path == "/chat/completions"
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_llm_client.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.llm_agent'`.

- [ ] **Step 3: Implement the client**

Create `agents/llm_agent.py` (only the module docstring, imports, constants, and `LlmClient` in this task; `AgentManager` arrives in Task 3):

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_llm_client.py`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add agents/llm_agent.py tests/test_llm_client.py
git commit -m "feat: add shared httpx LLM client"
```

---

### Task 3: AgentManager function-calling loop

**Files:**
- Modify: `agents/llm_agent.py` (add `AgentManager` to the file created in Task 2)
- Test: `tests/test_llm_agent.py`

**Interfaces:**
- Consumes: `LlmClient.chat` / `LlmClient.search_news` (Task 2), `TraceLogger.event` (Task 1), `Player`, `Squad` from `core.models`.
- Produces: `AgentManager(buyer_id, name, client, tracer, *, model, temperature, role=None, personality=None, system_prompt=None, max_tool_iterations=8, tools=DEFAULT_TOOLS, spending_profile=None, target_players=None)` implementing `Bidder` (`bid(player, squad) -> int`). `DEFAULT_TOOLS = ("search_news", "submit_bid")`.
- Behavior contract (for Task 5/6 consumers): a valid `submit_bid` int in `[0, max_bid_allowed]` ends the loop and returns it; out-of-range amounts get the tool error `"amount non valido, max è N"` and the loop continues; stop without `submit_bid` or iteration-cap exhaustion returns 0 with a `no_bid` trace; client exceptions are traced as `error` and re-raised.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_agent.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_llm_agent.py`
Expected: FAIL with `ImportError: cannot import name 'AgentManager'`.

- [ ] **Step 3: Implement AgentManager**

Append to `agents/llm_agent.py` (and extend the import block at the top with `from agents.trace import TraceLogger` and `from core.models import Player, Squad`):

```python
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
        max_tool_iterations: int = 8,
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
            "Sei un agente che partecipa a un'asta di fantacalcio.",
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
            "Usa gli strumenti a disposizione: puoi cercare notizie sul giocatore "
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_llm_agent.py tests/test_llm_client.py tests/test_trace.py`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add agents/llm_agent.py tests/test_llm_agent.py
git commit -m "feat: add AgentManager function-calling bidder"
```

---

### Task 4: Parallel bid collection in the engine

**Files:**
- Modify: `core/auction_manager.py`
- Test: `tests/test_auction_manager.py`

**Interfaces:**
- Consumes: existing `Bidder` protocol.
- Produces: `AuctionEngine.partial_report() -> SimulationReport` — projects the current state into a report even when the run is incomplete (used by the benchmark runner in Task 8).
- Behavior: `_collect_bids` submits one future per *eligible* bidder to a per-call pool; results, validation, and issue recording stay in bidder order in the main thread; `max_workers=len(bidders)` and not configurable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_auction_manager.py` (imports: add `import time` at the top):

```python
class SlowBidder:
    def __init__(self, buyer_id, name, bid_value, delay):
        self.buyer_id = buyer_id
        self.name = name
        self.bid_value = bid_value
        self.delay = delay

    def bid(self, player, squad):
        time.sleep(self.delay)
        return self.bid_value


def test_parallel_collect_bids_matches_sequential_outcome():
    player = make_player("a", "A")
    bidders = [
        SlowBidder("slow", "Slow", 2, delay=0.2),
        FixedBidder("fast", "Fast", 1),
    ]
    engine = AuctionEngine([player], bidders, budget=30, seed=1)

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "slow"
    assert result.all_bids == {"slow": 2, "fast": 1}


def test_parallel_collect_bids_preserves_issue_order():
    player = make_player("a", "A")
    bidders = [
        FixedBidder("bad", "Bad", "10"),
        RaisingBidder("worse", "Worse"),
        FixedBidder("good", "Good", 1),
    ]
    engine = AuctionEngine([player], bidders, budget=30, seed=1)

    result = engine.auction_player(player)

    assert result.status is AuctionStatus.SOLD
    assert [issue.buyer_id for issue in engine.bid_issues] == ["bad", "worse"]
    assert [issue.code for issue in engine.bid_issues] == [
        "invalid_type",
        "bidder_exception",
    ]
    assert result.all_bids == {"bad": 0, "worse": 0, "good": 1}


def test_parallel_collect_bids_excludes_ineligible_bidders():
    player = make_player("p", "P")
    bidders = [
        DeterministicBidder("full", "Full", priority=1),
        FixedBidder("free", "Free", 1),
    ]
    engine = AuctionEngine([player], bidders, budget=30, seed=1)
    for index in range(3):
        engine.state.squads["full"].add_player(make_player(f"old-{index}", "P"), 1)

    result = engine.auction_player(player)

    assert result.all_bids == {"full": 0, "free": 1}
    assert result.winner_id == "free"


def test_partial_report_exposes_incomplete_state():
    player = make_player("a", "A")
    engine = AuctionEngine(
        [player], [FixedBidder("b1", "One", 1)], budget=500, seed=1
    )

    with pytest.raises(AuctionIncompleteError):
        engine.run()

    report = engine.partial_report()

    assert report.document_type == "auction_report"
    assert report.players_sold == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_auction_manager.py`
Expected: FAIL with `AttributeError: 'AuctionEngine' object has no attribute 'partial_report'` (the `_collect_bids` outcome tests pass either way; the attribute error makes the module fail).

- [ ] **Step 3: Implement the parallel collection and the report helper**

In `core/auction_manager.py`, add to the imports:

```python
from concurrent.futures import Future, ThreadPoolExecutor
```

Replace `_collect_bids` with:

```python
    def _collect_bids(self, player: Player) -> dict[str, int]:
        """Collect one bid per eligible bidder in parallel worker threads.

        Bidder order determines the bids dict, validation, and issue recording;
        workers only run bidder.bid(). The per-call pool keeps the engine
        lifecycle-free and costs nothing next to LLM latency.
        """
        bids: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=len(self.bidders)) as pool:
            futures: dict[str, Future] = {}
            for bidder in self.bidders:
                squad = self.state.squads[bidder.buyer_id]
                eligible = (
                    not squad.is_complete
                    and squad.remaining_for(player.position) > 0
                )
                if not eligible:
                    bids[bidder.buyer_id] = 0
                    continue
                futures[bidder.buyer_id] = pool.submit(bidder.bid, player, squad)
            for bidder in self.bidders:
                future = futures.get(bidder.buyer_id)
                if future is None:
                    continue
                squad = self.state.squads[bidder.buyer_id]
                try:
                    bid = future.result()
                except Exception as exc:
                    self._record_bid_issue(
                        player,
                        bidder,
                        "bidder_exception",
                        f"Bidder raised {type(exc).__name__}: {exc}",
                    )
                    bids[bidder.buyer_id] = 0
                    continue
                try:
                    squad.validate_bid(player, bid)
                except BidValidationError as exc:
                    self._record_bid_issue(player, bidder, exc.code, str(exc))
                    bids[bidder.buyer_id] = 0
                    continue
                bids[bidder.buyer_id] = bid
        return bids
```

Add the public report helper right after `_report`:

```python
    def partial_report(self) -> SimulationReport:
        """Project the current state into a report even when the run is incomplete."""
        return self._report()
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_auction_manager.py tests/test_resume.py`
Expected: PASS — the existing 103-test suite is the regression net proving the parallel path matches the sequential outcome.

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add core/auction_manager.py tests/test_auction_manager.py
git commit -m "feat: parallelize bid collection with thread pool"
```

---

### Task 5: LLM configuration contract, bidder building, and fresh-run wiring

**Files:**
- Modify: `main.py`
- Modify: `tests/test_cli.py`
- Create: `configs/llm.yaml`

**Interfaces:**
- Consumes: `AgentManager` / `LlmClient` / `TraceLogger` (Tasks 1-3).
- Produces for Task 6: `_validate_llm_buyer(llm: Any, index: int) -> None`, `_validate_global_llm(llm: Any) -> None`, `_make_llm_client(llm_config: dict) -> LlmClient`, `_trace_run_dir(logs_dir) -> Path`, and `_build_bidders(configs, seed, llm_config=None, run_dir=None)` with the `llm` branch. `main()` exposes `llm_config` and `buyer_configs` variables used by the Task 6 sidecar handler.
- Error messages are the contract: `'buyers[i].strategy' must be 'deterministic', 'random' or 'llm'` (existing test updated), `'buyers[i].llm' must be a mapping`, `'buyers[i].llm.temperature' must be a number in [0, 2]`, `'buyers[i].llm.max_tool_iterations' must be an int >= 1`, `'buyers[i].llm.tools' must be a non-empty subset of ['search_news', 'submit_bid'] containing 'submit_bid'`, `'buyers[i].llm.spending_profile' shares must sum to 1 (within 0.01)`, `'buyers[i].llm.target_players' must be a list of non-empty strings`, `'llm' must be a mapping`, `'llm.base_url' must be a non-empty string`, `'llm.timeout_seconds' must be an int > 0`, `'llm.brave' must be a mapping`, and the missing-env error `Environment variable 'X' (llm.api_key_env) is not set`.

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/test_cli.py` (imports: add `import json` if not already present — it is — plus `from agents.llm_agent import MOCK_BRAVE_KEY`):

```python
class FakeLlmClient:
    """Scripted LlmClient replacement for CLI tests (no network)."""

    def __init__(
        self,
        base_url,
        api_key,
        brave_base_url,
        brave_api_key,
        timeout_seconds=30,
        transport=None,
    ):
        self.calls = 0

    def chat(self, messages, tools, model, temperature):
        self.calls += 1
        return {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "submit_bid", "args": {"amount": 1}}
            ],
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def search_news(self, query, count):
        return "search non disponibile"


def base_llm_config(workbook: Path) -> dict:
    return {
        "simulation": {"budget": 500, "seed": 42},
        "paths": {"players": str(workbook)},
        "llm": {
            "base_url": "https://api.test/v1",
            "api_key_env": "TEST_LLM_API_KEY",
            "model": "gpt-4o-mini",
            "temperature": 0.7,
            "timeout_seconds": 30,
            "brave": {
                "base_url": "https://api.search.brave.com/res/v1/web/search",
                "api_key": MOCK_BRAVE_KEY,
            },
        },
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {}}],
    }


def test_cli_llm_run_completes_with_fake_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

    exit_code = main(["--config", str(config), "--output", str(report)])

    assert exit_code == 0
    report_data = json.loads(report.read_text(encoding="utf-8"))
    assert report_data["players_sold"] == 25
    traces = list((tmp_path / "logs" / "traces").glob("*/b1.jsonl"))
    assert len(traces) == 1
    lines = traces[0].read_text(encoding="utf-8").splitlines()
    assert sum(
        json.loads(line)["phase"] == "bid" for line in lines
    ) == 25


def test_cli_missing_llm_api_key_fails_before_auction(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_LLM_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("TEST_LLM_API_KEY" in error for error in errors)


@pytest.mark.parametrize(
    ("buyers_override", "message"),
    [
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm"}],
            "'buyers[0].llm' must be a mapping",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"temperature": "hot"}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"temperature": 2.5}}],
            "'buyers[0].llm.temperature' must be a number in [0, 2]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"max_tool_iterations": 0}}],
            "'buyers[0].llm.max_tool_iterations' must be an int >= 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": ["search_news"]}}],
            "must contain 'submit_bid'",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": ["submit_bid", "mystery"]}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"tools": []}}],
            "must be a non-empty subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": 0.5, "D": 0.5, "C": 0.5, "A": 0.5}}}],
            "must sum to 1",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": 0.5, "X": 0.5}}}],
            "keys must be a subset",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"spending_profile": {"P": -0.1, "D": 0.2, "C": 0.4, "A": 0.5}}}],
            "must be a number in [0, 1]",
        ),
        (
            [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {"target_players": ["Lautaro", ""]}}],
            "list of non-empty strings",
        ),
    ],
)
def test_cli_rejects_invalid_buyer_llm_block(monkeypatch, tmp_path, buyers_override, message):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["buyers"] = buyers_override
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(message in error for error in errors)


def test_cli_llm_buyer_requires_global_llm_block(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data.pop("llm")
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm' must be a mapping" in error for error in errors)


def test_cli_rejects_empty_llm_base_url(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"]["base_url"] = ""
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.base_url' must be a non-empty string" in error for error in errors)


def test_cli_rejects_missing_brave_block(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.brave' must be a mapping" in error for error in errors)


def test_cli_rejects_zero_timeout(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["llm"]["timeout_seconds"] = 0
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.timeout_seconds' must be an int > 0" in error for error in errors)
```

Update the existing `test_cli_rejects_unknown_strategy` assertion to the new message:

```python
    assert any(
        "'buyers[0].strategy' must be 'deterministic', 'random' or 'llm'" in error
        for error in errors
    )
```

Create `configs/llm.yaml`:

```yaml
simulation:
  budget: 500
  seed: 42

paths:
  players: "data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx"
  output: "data/results/report.json"
  checkpoint: "data/checkpoints/checkpoint.json"
  logs: "logs"

llm:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  model: "gpt-4o-mini"
  temperature: 0.7
  timeout_seconds: 30
  brave:
    base_url: "https://api.search.brave.com/res/v1/web/search"
    api_key: "INSERISCI_LA_TUA_BRAVE_API_KEY"

buyers:
  - id: "buyer_1"
    name: "Squadra Alfa"
    strategy: "llm"
    llm:
      role: "fantallenatore esperto"
      personality: "prudente"
      spending_profile: {P: 0.08, D: 0.20, C: 0.35, A: 0.37}
      target_players:
        - "Lautaro Martínez"
  - id: "buyer_2"
    name: "Squadra Beta"
    strategy: "llm"
    llm:
      role: "fantallenatore esperto"
      personality: "aggressiva"
      spending_profile: {P: 0.05, D: 0.15, C: 0.30, A: 0.50}
  - id: "buyer_3"
    name: "Squadra Gamma"
    strategy: "llm"
    llm:
      personality: "equilibrata"
      temperature: 0.3
  - id: "buyer_4"
    name: "Squadra Delta"
    strategy: "deterministic"
    priority: 3
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_cli.py`
Expected: FAIL — LLM configs are rejected by `_validate_config` ("strategy must be 'deterministic' or 'random'"), the fake-client run exits 1, and the parametrized cases fail.

- [ ] **Step 3: Implement validation, bidder building, and trace wiring**

In `main.py`, extend the imports:

```python
import json
import os
from datetime import datetime, timezone

from agents.llm_agent import AgentManager, LlmClient
from agents.trace import TraceLogger
```

Add module constants and helpers after `DEFAULT_CONFIG`:

```python
LLM_TOOLS = {"search_news", "submit_bid"}
SPENDING_ROLES = {"P", "D", "C", "A"}
SPENDING_TOLERANCE = 0.01


def _validate_llm_buyer(llm: Any, index: int | str) -> None:
    if not isinstance(llm, dict):
        raise ValueError(f"'buyers[{index}].llm' must be a mapping")
    for key in ("model", "role", "personality", "system_prompt"):
        value = llm.get(key)
        if value is not None and not str(value).strip():
            raise ValueError(
                f"'buyers[{index}].llm.{key}' must be a non-empty string"
            )
    temperature = llm.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise ValueError(
            f"'buyers[{index}].llm.temperature' must be a number in [0, 2]"
        )
    max_tool_iterations = llm.get("max_tool_iterations")
    if max_tool_iterations is not None and (
        isinstance(max_tool_iterations, bool)
        or not isinstance(max_tool_iterations, int)
        or max_tool_iterations < 1
    ):
        raise ValueError(
            f"'buyers[{index}].llm.max_tool_iterations' must be an int >= 1"
        )
    tools = llm.get("tools")
    if tools is not None and (
        not isinstance(tools, list)
        or not tools
        or not set(tools) <= LLM_TOOLS
        or "submit_bid" not in set(tools)
    ):
        raise ValueError(
            f"'buyers[{index}].llm.tools' must be a non-empty subset of "
            f"{sorted(LLM_TOOLS)} containing 'submit_bid'"
        )
    spending_profile = llm.get("spending_profile")
    if spending_profile is not None:
        if not isinstance(spending_profile, dict) or not spending_profile:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' must be a non-empty mapping"
            )
        if not set(spending_profile) <= SPENDING_ROLES:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' keys must be a subset "
                f"of {sorted(SPENDING_ROLES)}"
            )
        shares = []
        for role, share in spending_profile.items():
            if (
                isinstance(share, bool)
                or not isinstance(share, (int, float))
                or not 0 <= share <= 1
            ):
                raise ValueError(
                    f"'buyers[{index}].llm.spending_profile.{role}' must be a "
                    "number in [0, 1]"
                )
            shares.append(float(share))
        if abs(sum(shares) - 1.0) > SPENDING_TOLERANCE:
            raise ValueError(
                f"'buyers[{index}].llm.spending_profile' shares must sum to 1 "
                "(within 0.01)"
            )
    target_players = llm.get("target_players")
    if target_players is not None and (
        not isinstance(target_players, list)
        or any(
            not isinstance(target, str) or not target.strip()
            for target in target_players
        )
    ):
        raise ValueError(
            f"'buyers[{index}].llm.target_players' must be a list of non-empty strings"
        )


def _validate_global_llm(llm: Any) -> None:
    if not isinstance(llm, dict):
        raise ValueError("'llm' must be a mapping")
    for key in ("base_url", "api_key_env", "model"):
        if not str(llm.get(key, "")).strip():
            raise ValueError(f"'llm.{key}' must be a non-empty string")
    temperature = llm.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise ValueError("'llm.temperature' must be a number in [0, 2]")
    timeout_seconds = llm.get("timeout_seconds")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise ValueError("'llm.timeout_seconds' must be an int > 0")
    brave = llm.get("brave")
    if not isinstance(brave, dict):
        raise ValueError("'llm.brave' must be a mapping")
    for key in ("base_url", "api_key"):
        if not str(brave.get(key, "")).strip():
            raise ValueError(f"'llm.brave.{key}' must be a non-empty string")


def _trace_run_dir(logs_dir: str | Path | None) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return Path(logs_dir or "logs") / "traces" / run_id
```

Extend `_validate_config`: in the buyer loop, change the strategy check and add the llm branch:

```python
        strategy = str(buyer.get("strategy", "deterministic")).lower()
        if strategy not in ("deterministic", "random", "llm"):
            raise ValueError(
                f"'buyers[{index}].strategy' must be 'deterministic', 'random' or 'llm'"
            )
        if strategy == "llm":
            _validate_llm_buyer(buyer.get("llm"), index)
        priority = buyer.get("priority")
        ...
```

After the buyer loop, before the end of `_validate_config`:

```python
    if any(
        str(buyer.get("strategy", "deterministic")).lower() == "llm"
        for buyer in buyers
    ):
        _validate_global_llm(config.get("llm"))
```

Add `_make_llm_client` and extend `_build_bidders`:

```python
def _make_llm_client(llm_config: dict[str, Any]) -> LlmClient:
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Environment variable '{api_key_env}' (llm.api_key_env) is not set; "
            "set it before running an auction with LLM bidders"
        )
    brave = llm_config.get("brave") or {}
    return LlmClient(
        base_url=str(llm_config["base_url"]),
        api_key=api_key,
        brave_base_url=str(brave["base_url"]),
        brave_api_key=str(brave["api_key"]),
        timeout_seconds=int(llm_config.get("timeout_seconds", 30)),
    )


def _build_bidders(
    configs: list[dict[str, Any]],
    seed: int | None,
    llm_config: dict[str, Any] | None = None,
    run_dir: Path | None = None,
):
    if not configs:
        raise ValueError("At least one buyer must be configured")

    llm_client: LlmClient | None = None
    bidders = []
    for index, config in enumerate(configs):
        buyer_id = str(config.get("id", "")).strip()
        name = str(config.get("name", "")).strip()
        strategy = str(config.get("strategy", "deterministic")).lower()
        if strategy == "deterministic":
            bidders.append(
                DeterministicBidder(
                    buyer_id,
                    name,
                    priority=int(config.get("priority", index)),
                )
            )
        elif strategy == "random":
            bidder_seed = None if seed is None else seed + index
            bidders.append(RandomBidder(buyer_id, name, random.Random(bidder_seed)))
        elif strategy == "llm":
            if llm_client is None:
                # Constructed once and shared: httpx clients are thread-safe.
                llm_client = _make_llm_client(llm_config or {})
            if run_dir is None:
                raise ValueError("A trace run_dir is required for LLM bidders")
            merged = {**(llm_config or {}), **(config.get("llm") or {})}
            bidders.append(
                AgentManager(
                    buyer_id,
                    name,
                    client=llm_client,
                    tracer=TraceLogger(run_dir, buyer_id),
                    model=str(merged["model"]),
                    temperature=float(merged.get("temperature", 0.7)),
                    role=merged.get("role"),
                    personality=merged.get("personality"),
                    system_prompt=merged.get("system_prompt"),
                    max_tool_iterations=int(merged.get("max_tool_iterations", 8)),
                    tools=tuple(merged.get("tools", AgentManager.DEFAULT_TOOLS)),
                    spending_profile=merged.get("spending_profile"),
                    target_players=merged.get("target_players"),
                )
            )
        else:
            raise ValueError(f"Unknown bidder strategy: {strategy}")
    return bidders
```

Wire `main()`: before the `try`, add the two new tracking variables:

```python
    engine: AuctionEngine | None = None
    simulation_snapshot: SimulationSnapshot | None = None
    buyer_snapshots: list[BidderSnapshot] | None = None
    checkpoint_path: Path | None = None
    llm_config: dict[str, Any] | None = None
    buyer_configs: list[dict[str, Any]] = []
    store = JsonStore()
```

In the resume branch, replace the `_build_bidders` call:

```python
            buyer_configs = _snapshot_configs(buyer_snapshots)
            bidders = _build_bidders(
                buyer_configs,
                source.simulation.seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(None),
            )
```

In the fresh branch, replace the corresponding lines:

```python
            buyer_configs = list(config.get("buyers", []))
            llm_config = config.get("llm")
            bidders = _build_bidders(
                buyer_configs,
                seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(paths.get("logs")),
            )
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_cli.py`
Expected: PASS (all new validation cases, the fake-client run, and the updated unknown-strategy message).

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add main.py tests/test_cli.py configs/llm.yaml
git commit -m "feat: validate and build LLM bidder configuration"
```

---

### Task 6: LLM sidecar save/load and resume flow

**Files:**
- Modify: `main.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `_validate_llm_buyer` / `_validate_global_llm` / `_build_bidders` (Task 5), `JsonStore.save_checkpoint`.
- Produces: `_sidecar_path(checkpoint_path: Path) -> Path` (`.json` suffix -> `.llm.yaml`), `_write_llm_sidecar(checkpoint_path, buyer_configs, llm_config) -> Path | None` (`None` when no `llm` buyer), `_load_llm_sidecar(checkpoint_path) -> dict` (raises `ValueError` with clear messages when missing or invalid).
- Behavior: save flow writes the sidecar next to the saved checkpoint when any snapshot is `llm`; resume with `llm` buyers requires a valid sidecar (missing/malformed -> exit 1 before the auction) and merges `buyers.<id>.llm` into snapshot-rebuilt configs; a second exhaustion propagates the sidecar next to the new checkpoint path.

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/test_cli.py`:

```python
def llm_sidecar_payload() -> dict:
    return {
        "schema_version": 1,
        "llm": {
            "base_url": "https://api.test/v1",
            "api_key_env": "TEST_LLM_API_KEY",
            "model": "gpt-4o-mini",
            "temperature": 0.7,
            "timeout_seconds": 30,
            "brave": {
                "base_url": "https://api.search.brave.com/res/v1/web/search",
                "api_key": MOCK_BRAVE_KEY,
            },
        },
        "buyers": {
            "incomplete": {"llm": {"temperature": 0.3}},
        },
    }


def test_cli_llm_exhaustion_writes_checkpoint_and_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    sidecar = tmp_path / "checkpoint.llm.yaml"
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["llm"]["model"] == "gpt-4o-mini"
    assert data["llm"]["api_key_env"] == "TEST_LLM_API_KEY"
    assert "sk-" not in sidecar.read_text(encoding="utf-8")
    # b1 has no per-buyer block in the config, so none is written
    assert data["buyers"] == {}


def test_cli_deterministic_exhaustion_writes_no_sidecar(tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_config(
        config,
        workbook,
        [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    )

    assert main(["--config", str(config), "--checkpoint", str(checkpoint)]) == 1
    assert checkpoint.exists()
    assert not (tmp_path / "checkpoint.llm.yaml").exists()


def make_llm_checkpoint(tmp_path, *, no_progress: bool = False) -> Path:
    checkpoint = make_checkpoint_file(tmp_path, no_progress=no_progress)
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    data["buyers"] = [
        {"id": "complete", "name": "Complete", "strategy": "deterministic", "priority": 0},
        {"id": "incomplete", "name": "Incomplete", "strategy": "llm", "priority": 1},
    ]
    checkpoint.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(llm_sidecar_payload()), encoding="utf-8"
    )
    return checkpoint


def test_cli_resumes_llm_checkpoint_with_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_checkpoint(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(report),
    ])

    assert exit_code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["players_sold"] == data["total_players"]
    assert (tmp_path / "traces" / "resume" / "incomplete.jsonl").exists()


def test_cli_resume_llm_without_sidecar_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    checkpoint = make_llm_checkpoint(tmp_path)
    (tmp_path / "checkpoint.llm.yaml").unlink()
    report = tmp_path / "report.json"
    errors = capture_log_errors(monkeypatch)

    exit_code = main(["--resume", str(checkpoint), "--output", str(report)])

    assert exit_code == 1
    assert not report.exists()
    assert any("sidecar" in error for error in errors)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "llm": llm_sidecar_payload()["llm"]},
        [1, 2, 3],
        {"schema_version": 1},
    ],
)
def test_cli_resume_llm_with_malformed_sidecar_fails(monkeypatch, tmp_path, payload):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    checkpoint = make_llm_checkpoint(tmp_path)
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    errors = capture_log_errors(monkeypatch)

    assert main(["--resume", str(checkpoint)]) == 1
    assert any("sidecar" in error for error in errors)


def test_cli_second_exhaustion_propagates_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_checkpoint(tmp_path, no_progress=True)

    exit_code = main(["--resume", str(checkpoint)])

    assert exit_code == 1
    loaded = JsonStore().load_checkpoint(checkpoint)
    assert loaded.run_number == 2
    sidecar = tmp_path / "checkpoint.llm.yaml"
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["buyers"]["incomplete"]["llm"] == {"temperature": 0.3}
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_cli.py -k "sidecar or resume_llm or llm_exhaustion or deterministic_exhaustion"`
Expected: FAIL — the sidecar file is never written and the resume flow rejects the `llm` strategy.

- [ ] **Step 3: Implement sidecar save/load and the resume merge**

In `main.py`, add the three helpers after `_snapshot_configs`:

```python
def _sidecar_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_suffix(".llm.yaml")


def _write_llm_sidecar(
    checkpoint_path: Path,
    buyer_configs: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> Path | None:
    """Write the LLM sidecar next to a checkpoint; None when no llm buyer."""
    llm_buyers = [
        buyer
        for buyer in buyer_configs
        if str(buyer.get("strategy", "")).lower() == "llm"
    ]
    if not llm_buyers:
        return None
    payload = {
        "schema_version": 1,
        "llm": llm_config,
        # Per-buyer blocks, only where present in the config; api_key_env is
        # a variable name, never the key itself.
        "buyers": {
            str(buyer["id"]): {"llm": buyer["llm"]}
            for buyer in llm_buyers
            if buyer.get("llm")
        },
    }
    path = _sidecar_path(checkpoint_path)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _load_llm_sidecar(checkpoint_path: Path) -> dict[str, Any]:
    path = _sidecar_path(checkpoint_path)
    if not path.exists():
        raise ValueError(
            f"LLM sidecar missing: {path}; checkpoints with LLM buyers "
            "cannot be resumed without it"
        )
    with path.open(encoding="utf-8") as stream:
        sidecar = yaml.safe_load(stream) or {}
    if not isinstance(sidecar, dict):
        raise ValueError(f"Invalid LLM sidecar {path}: root must be a mapping")
    if sidecar.get("schema_version") != 1:
        raise ValueError(f"Invalid LLM sidecar {path}: schema_version must be 1")
    buyers = sidecar.get("buyers") or {}
    if not isinstance(buyers, dict):
        raise ValueError(f"Invalid LLM sidecar {path}: 'buyers' must be a mapping")
    try:
        _validate_global_llm(sidecar.get("llm"))
        for buyer_id, entry in buyers.items():
            if not isinstance(entry, dict):
                raise ValueError(
                    f"'buyers.{buyer_id}' must be a mapping"
                )
            _validate_llm_buyer(entry.get("llm"), str(buyer_id))
    except ValueError as exc:
        raise ValueError(f"Invalid LLM sidecar {path}: {exc}") from exc
    return sidecar
```

In `main()`, replace the resume branch (from `buyer_snapshots = ...` down to the `engine = ...` line):

```python
            buyer_snapshots = [
                buyer.model_copy(deep=True) for buyer in source.buyers
            ]
            buyer_configs = _snapshot_configs(buyer_snapshots)
            llm_config = None
            if any(snapshot.strategy == "llm" for snapshot in buyer_snapshots):
                sidecar = _load_llm_sidecar(args.resume)
                llm_config = sidecar["llm"]
                per_buyer = sidecar.get("buyers") or {}
                for config in buyer_configs:
                    if config["strategy"] == "llm":
                        entry = per_buyer.get(config["id"], {})
                        config["llm"] = entry.get("llm") or {}
            bidders = _build_bidders(
                buyer_configs,
                source.simulation.seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(None),
            )
            engine = AuctionEngine.from_checkpoint(source, bidders)
```

In the `AuctionIncompleteError` handler, after `saved = store.save_checkpoint(...)`:

```python
        if llm_config is not None:
            try:
                sidecar_path = _write_llm_sidecar(
                    saved, buyer_configs, llm_config
                )
                if sidecar_path is not None:
                    logger.info("LLM sidecar saved to {}", sidecar_path)
            except OSError as write_exc:
                logger.error(
                    "Failed to write LLM sidecar next to {}: {}",
                    saved,
                    write_exc,
                )
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_cli.py`
Expected: PASS — sidecar written/absent as appropriate, LLM resume completes, missing/malformed sidecars exit 1 before the auction, second exhaustion propagates the sidecar.

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add main.py tests/test_cli.py
git commit -m "feat: resume LLM checkpoints via sidecar"
```

---

### Task 7: Pure benchmark metrics module

**Files:**
- Create: `benchmark/__init__.py` (empty)
- Create: `benchmark/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `agent_metrics(report: dict, buyer_config: dict, trace: list[dict] | None) -> dict`, `compute_run_metrics(report: dict, buyer_configs: list[dict], traces_dir: Path) -> dict[str, dict]`, `aggregate_metrics(run_metrics: list[dict[str, dict]]) -> dict`, `build_metrics_document(run_id: str, config_path: str, run_records: list[dict], aggregates: dict) -> dict`, `csv_rows(run_records: list[dict]) -> list[dict]`, `write_metrics_csv(path: Path, rows: list[dict]) -> None`, `print_summary_table(aggregates: dict) -> None`, plus constants `MODEL_PRICES` and `USD_TO_EUR`.
- Consumed by: Task 8 (`_run_benchmark`). No engine imports; only `json`/`csv`/`statistics` and report/trace file reads.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics.py`:

```python
import json

import pytest

from benchmark.metrics import (
    agent_metrics,
    aggregate_metrics,
    compute_run_metrics,
    csv_rows,
    write_metrics_csv,
)

REPORT = {
    "duration_seconds": 12.5,
    "squads": {
        "buyer_1": {
            "budget_initial": 500,
            "budget_remaining": 400,
            "players": [
                {"id": "p1", "name": "Portiere Uno", "position": "P", "selling_price": 10},
                {"id": "p2", "name": "Lautaro Martínez", "position": "A", "selling_price": 90},
            ],
        },
    },
}

BUYER = {
    "id": "buyer_1",
    "name": "Alpha",
    "strategy": "llm",
    "llm": {
        "model": "gpt-4o-mini",
        "spending_profile": {"P": 0.1, "D": 0.2, "C": 0.3, "A": 0.4},
        "target_players": ["lautaro martínez"],
    },
}

TRACE_EVENTS = [
    {"phase": "context"},
    {"phase": "llm_call"},
    {"phase": "usage", "content": {"prompt_tokens": 100, "completion_tokens": 50}},
    {"phase": "tool_call", "content": {"name": "search_news", "args": {}}},
    {"phase": "bid", "content": {"amount": 10}},
    {"phase": "context"},
    {"phase": "no_bid"},
]


def test_agent_metrics_computed_from_report_and_trace():
    metrics = agent_metrics(REPORT, BUYER, TRACE_EVENTS)

    assert metrics["parse_rate"] == 0.5  # 1 bid / 2 contexts
    assert metrics["cost_tokens"] == 150
    assert metrics["cost_eur"] == pytest.approx(45 / 1_000_000 * 0.92)
    assert metrics["roster_complete"] is False
    assert metrics["missing_roles"] == {"P": 2, "D": 8, "C": 8, "A": 5}
    assert metrics["budget_spent"] == 100
    assert metrics["budget_remaining"] == 400
    assert metrics["spending_share_by_role"] == pytest.approx(
        {"P": 0.1, "D": 0.0, "C": 0.0, "A": 0.9}
    )
    assert metrics["spending_distance"] == pytest.approx(1.0)
    assert metrics["targets_acquired"] == 1  # case-insensitive match
    assert metrics["duration_seconds"] == 12.5
    assert metrics["llm_calls"] == 1
    assert metrics["tools_used"] == {"search_news": 1, "submit_bid": 0}
    assert metrics["model"] == "gpt-4o-mini"


def test_unknown_model_yields_null_cost():
    buyer = {
        "id": "buyer_1",
        "name": "Alpha",
        "strategy": "llm",
        "llm": {"model": "misterioso-1"},
    }
    metrics = agent_metrics(REPORT, buyer, TRACE_EVENTS)

    assert metrics["cost_eur"] is None


def test_absent_profile_uses_uniform_target_and_no_trace_is_safe():
    buyer = {"id": "buyer_1", "name": "Alpha", "strategy": "llm"}
    metrics = agent_metrics(REPORT, buyer, None)

    assert metrics["parse_rate"] == 0.0
    assert metrics["cost_tokens"] == 0
    assert metrics["llm_calls"] == 0
    assert metrics["spending_distance"] == pytest.approx(1.3)
    assert metrics["targets_acquired"] == 0


def test_compute_run_metrics_reads_trace_files(tmp_path):
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "buyer_1.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in TRACE_EVENTS),
        encoding="utf-8",
    )

    metrics = compute_run_metrics(REPORT, [BUYER], traces_dir)

    assert metrics["buyer_1"]["parse_rate"] == 0.5


def test_aggregate_metrics_computes_mean_and_std():
    run_1 = {"b1": {"parse_rate": 0.5, "roster_complete": False, "cost_eur": 0.1, "spending_share_by_role": {"P": 0.5, "D": 0.5, "C": 0.0, "A": 0.0}, "tools_used": {"search_news": 1, "submit_bid": 2}}}
    run_2 = {"b1": {"parse_rate": 0.7, "roster_complete": True, "cost_eur": 0.3, "spending_share_by_role": {"P": 0.3, "D": 0.3, "C": 0.2, "A": 0.2}, "tools_used": {"search_news": 0, "submit_bid": 1}}}

    aggregates = aggregate_metrics([run_1, run_2])

    assert aggregates["b1"]["parse_rate"] == {"mean": 0.6, "std": 0.1}
    assert aggregates["b1"]["roster_complete"] == {"mean": 0.5, "std": 0.5}
    assert aggregates["b1"]["cost_eur"] == {"mean": 0.2, "std": 0.1}
    assert aggregates["b1"]["spending_share_P"]["mean"] == 0.4
    assert aggregates["b1"]["tools_search_news"]["mean"] == 0.5


def test_aggregate_metrics_skips_null_costs():
    run_1 = {"b1": {"cost_eur": 0.1}}
    run_2 = {"b1": {"cost_eur": None}}

    aggregates = aggregate_metrics([run_1, run_2])

    assert aggregates["b1"]["cost_eur"] == {"mean": 0.1, "std": 0.0}


def test_csv_rows_and_writer(tmp_path):
    run_records = [
        {
            "run": "run_001",
            "seed": 42,
            "completed": True,
            "buyers": {"buyer_1": agent_metrics(REPORT, BUYER, TRACE_EVENTS)},
        }
    ]
    rows = csv_rows(run_records)
    assert rows[0]["buyer_id"] == "buyer_1"
    assert rows[0]["run"] == "run_001"
    assert rows[0]["missing_roles"] == '{"P": 2, "D": 8, "C": 8, "A": 5}'
    assert rows[0]["tools_search_news"] == 1

    path = tmp_path / "metrics.csv"
    write_metrics_csv(path, rows)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",")[0] == "run"
    assert "buyer_id" in lines[0]
    assert len(lines) == 2
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_metrics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark.metrics'`.

- [ ] **Step 3: Implement the metrics module**

Create `benchmark/__init__.py` (empty file) and `benchmark/metrics.py`:

```python
"""Pure metric functions over report JSON and per-agent trace JSONL files."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

ROLES = ("P", "D", "C", "A")
ROSTER_REQUIREMENTS = {"P": 3, "D": 8, "C": 8, "A": 6}
UNIFORM_PROFILE = {"P": 0.25, "D": 0.25, "C": 0.25, "A": 0.25}

# USD per 1M tokens (input, output); unknown models yield None.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
USD_TO_EUR = 0.92

AGGREGATABLE_SCALARS = (
    "parse_rate",
    "cost_tokens",
    "cost_eur",
    "roster_complete",
    "budget_spent",
    "budget_remaining",
    "spending_distance",
    "targets_acquired",
    "duration_seconds",
    "llm_calls",
)

CSV_FIELDS = [
    "run", "seed", "completed", "buyer_id", "model", "parse_rate",
    "cost_tokens", "cost_eur", "roster_complete", "missing_roles",
    "budget_spent", "budget_remaining", "spending_distance",
    "spending_share_P", "spending_share_D", "spending_share_C",
    "spending_share_A", "targets_acquired", "duration_seconds",
    "llm_calls", "tools_search_news", "tools_submit_bid",
]


def load_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def agent_metrics(
    report: dict, buyer_config: dict, trace: list[dict] | None
) -> dict[str, Any]:
    """Per-run per-agent metrics from a report dict and the agent's trace."""
    buyer_id = str(buyer_config["id"])
    squad = report["squads"][buyer_id]
    trace = trace or []

    context_events = [e for e in trace if e["phase"] == "context"]
    bid_events = [e for e in trace if e["phase"] == "bid"]
    parse_rate = len(bid_events) / len(context_events) if context_events else 0.0

    usage = [e["content"] for e in trace if e["phase"] == "usage"]
    tokens_in = sum(u.get("prompt_tokens", 0) for u in usage)
    tokens_out = sum(u.get("completion_tokens", 0) for u in usage)
    cost_tokens = tokens_in + tokens_out
    model = (buyer_config.get("llm") or {}).get("model")
    prices = MODEL_PRICES.get(model) if model else None
    cost_eur = None
    if prices is not None:
        cost_eur = round(
            (tokens_in * prices[0] + tokens_out * prices[1])
            / 1_000_000
            * USD_TO_EUR,
            6,
        )

    players = squad["players"]
    counts = {role: 0 for role in ROLES}
    spent_by_role = {role: 0 for role in ROLES}
    for player in players:
        role = player["position"]
        counts[role] = counts.get(role, 0) + 1
        spent_by_role[role] = spent_by_role.get(role, 0) + (
            player.get("selling_price") or 0
        )
    missing_roles = {
        role: max(0, ROSTER_REQUIREMENTS[role] - counts.get(role, 0))
        for role in ROLES
    }
    roster_complete = len(players) == sum(ROSTER_REQUIREMENTS.values()) and all(
        value == 0 for value in missing_roles.values()
    )
    budget_initial = squad["budget_initial"]
    budget_remaining = squad["budget_remaining"]
    budget_spent = budget_initial - budget_remaining
    total_spent = sum(spent_by_role.values())
    spending_share_by_role = {
        role: round(spent_by_role[role] / total_spent, 4) if total_spent else 0.0
        for role in ROLES
    }
    llm_block = buyer_config.get("llm") or {}
    target = llm_block.get("spending_profile") or UNIFORM_PROFILE
    spending_distance = round(
        sum(
            abs(spending_share_by_role[role] - float(target.get(role, 0.0)))
            for role in ROLES
        ),
        4,
    )
    target_names = [str(name).lower() for name in llm_block.get("target_players") or []]
    owned_names = [str(player["name"]).lower() for player in players]
    targets_acquired = sum(1 for target in target_names if target in owned_names)
    tool_calls = [e["content"] for e in trace if e["phase"] == "tool_call"]
    tools_used = {
        name: sum(1 for call in tool_calls if call.get("name") == name)
        for name in ("search_news", "submit_bid")
    }
    return {
        "model": model,
        "parse_rate": parse_rate,
        "cost_tokens": cost_tokens,
        "cost_eur": cost_eur,
        "roster_complete": roster_complete,
        "missing_roles": missing_roles,
        "budget_spent": budget_spent,
        "budget_remaining": budget_remaining,
        "spending_share_by_role": spending_share_by_role,
        "spending_distance": spending_distance,
        "targets_acquired": targets_acquired,
        "duration_seconds": report["duration_seconds"],
        "llm_calls": sum(1 for e in trace if e["phase"] == "llm_call"),
        "tools_used": tools_used,
    }


def compute_run_metrics(
    report: dict, buyer_configs: list[dict], traces_dir: Path
) -> dict[str, dict]:
    return {
        str(buyer["id"]): agent_metrics(
            report,
            buyer,
            load_trace(traces_dir / f"{str(buyer['id'])}.jsonl"),
        )
        for buyer in buyer_configs
    }


def _mean_std(values: list) -> dict[str, float | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"mean": None, "std": None}
    return {
        "mean": round(statistics.mean(clean), 4),
        "std": round(statistics.pstdev(clean), 4) if len(clean) > 1 else 0.0,
    }


def aggregate_metrics(run_metrics: list[dict[str, dict]]) -> dict:
    if not run_metrics:
        return {}
    aggregates: dict = {}
    for buyer_id in run_metrics[0]:
        aggregates[buyer_id] = {}
        for metric in AGGREGATABLE_SCALARS:
            values = [run[buyer_id].get(metric) for run in run_metrics]
            if metric == "roster_complete":
                values = [1 if value else 0 for value in values]
            aggregates[buyer_id][metric] = _mean_std(values)
        for role in ROLES:
            values = [
                run[buyer_id]["spending_share_by_role"].get(role, 0.0)
                for run in run_metrics
            ]
            aggregates[buyer_id][f"spending_share_{role}"] = _mean_std(values)
        for tool in ("search_news", "submit_bid"):
            values = [
                run[buyer_id]["tools_used"].get(tool, 0)
                for run in run_metrics
            ]
            aggregates[buyer_id][f"tools_{tool}"] = _mean_std(values)
    return aggregates


def build_metrics_document(
    run_id: str,
    config_path: str,
    run_records: list[dict],
    aggregates: dict,
) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "config": config_path,
        "runs": run_records,
        "aggregates": aggregates,
    }


def csv_rows(run_records: list[dict]) -> list[dict]:
    rows = []
    for run in run_records:
        for buyer_id, metrics in run["buyers"].items():
            row = {
                "run": run["run"],
                "seed": run["seed"],
                "completed": run["completed"],
                "buyer_id": buyer_id,
                "model": metrics.get("model"),
                "parse_rate": metrics["parse_rate"],
                "cost_tokens": metrics["cost_tokens"],
                "cost_eur": metrics["cost_eur"],
                "roster_complete": metrics["roster_complete"],
                "missing_roles": json.dumps(metrics["missing_roles"]),
                "budget_spent": metrics["budget_spent"],
                "budget_remaining": metrics["budget_remaining"],
                "spending_distance": metrics["spending_distance"],
            }
            for role in ROLES:
                row[f"spending_share_{role}"] = metrics["spending_share_by_role"][role]
            row.update(
                {
                    "targets_acquired": metrics["targets_acquired"],
                    "duration_seconds": metrics["duration_seconds"],
                    "llm_calls": metrics["llm_calls"],
                    "tools_search_news": metrics["tools_used"]["search_news"],
                    "tools_submit_bid": metrics["tools_used"]["submit_bid"],
                }
            )
            rows.append(row)
    return rows


def write_metrics_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def print_summary_table(aggregates: dict) -> None:
    columns = (
        "parse_rate",
        "cost_eur",
        "roster_complete",
        "targets_acquired",
        "duration_seconds",
    )
    header = "".join(
        f"{column:<22}" for column in ("buyer",) + columns
    ).rstrip()
    print(header)
    for buyer_id, metrics in aggregates.items():
        cells = [buyer_id]
        for column in columns:
            stats = metrics.get(column, {})
            mean = stats.get("mean")
            std = stats.get("std")
            cells.append("-" if mean is None else f"{mean:.3f} ± {std:.3f}")
        print("".join(f"{cell:<22}" for cell in cells))
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_metrics.py`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add benchmark/__init__.py benchmark/metrics.py tests/test_metrics.py
git commit -m "feat: add pure benchmark metrics module"
```

---

### Task 8: Benchmark CLI subcommand

**Files:**
- Modify: `main.py`
- Create: `tests/test_benchmark.py`

**Interfaces:**
- Consumes: `compute_run_metrics` / `aggregate_metrics` / `build_metrics_document` / `csv_rows` / `write_metrics_csv` / `print_summary_table` (Task 7), `_build_bidders` (Task 5), `AuctionEngine.partial_report` (Task 4), `JsonStore.save_report`.
- Produces: `main.py benchmark --config PATH --runs N [--seed S] [--output DIR]`, exit 0; layout `DIR/run_NNN/report.json`, `DIR/run_NNN/traces/<buyer_id>.jsonl`, `DIR/metrics.json`, `DIR/metrics.csv`; default root `data/benchmarks/<timestamp>/`.
- Behavior: players loaded once and deep-copied per run; each run uses `seed + i` (0-based); pool exhaustion in a run saves the partial report with `completed: false` and the benchmark continues; `--runs < 1` returns 1 without loading config.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_benchmark.py`:

```python
import json

import yaml

import main as cli_module
from main import main
from test_cli import (
    FakeLlmClient,
    base_llm_config,
    write_raw_config,
    write_workbook,
)


def test_benchmark_command_produces_layout_and_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    write_raw_config(config, base_llm_config(workbook))
    root = tmp_path / "bench"

    exit_code = main([
        "benchmark",
        "--config", str(config),
        "--runs", "2",
        "--seed", "42",
        "--output", str(root),
    ])

    assert exit_code == 0
    for name in ("run_001", "run_002"):
        assert (root / name / "report.json").exists()
        assert (root / name / "traces" / "b1.jsonl").exists()
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["runs"][0]["run"] == "run_001"
    assert metrics["runs"][0]["seed"] == 42
    assert metrics["runs"][1]["seed"] == 43
    assert metrics["runs"][0]["completed"] is True
    assert metrics["aggregates"]["b1"]["parse_rate"]["mean"] == 1.0
    csv_text = (root / "metrics.csv").read_text(encoding="utf-8")
    assert "buyer_id" in csv_text.splitlines()[0]
    assert len(csv_text.splitlines()) == 3  # header + 2 run rows


def test_benchmark_records_incomplete_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, base_llm_config(workbook))
    root = tmp_path / "bench"

    exit_code = main([
        "benchmark",
        "--config", str(config),
        "--runs", "2",
        "--seed", "42",
        "--output", str(root),
    ])

    assert exit_code == 0
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert [run["completed"] for run in metrics["runs"]] == [False, False]
    report = json.loads((root / "run_001" / "report.json").read_text(encoding="utf-8"))
    assert report["document_type"] == "auction_report"
    assert not (root / "run_001" / "checkpoint.json").exists()


def test_benchmark_rejects_zero_runs():
    assert main(["benchmark", "--runs", "0"]) == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_benchmark.py`
Expected: FAIL — `main()` exits with `unrecognized arguments: benchmark ...`.

- [ ] **Step 3: Implement the subcommand and runner**

In `main.py`, extend the imports:

```python
from benchmark.metrics import (
    aggregate_metrics,
    build_metrics_document,
    compute_run_metrics,
    csv_rows,
    print_summary_table,
    write_metrics_csv,
)
```

Extend `_parser()`:

```python
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a non-interactive fantasy auction")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--players", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--seed", type=int)
    subparsers = parser.add_subparsers(dest="command")
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run multiple auctions and aggregate per-agent metrics",
    )
    benchmark_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    benchmark_parser.add_argument("--runs", type=int, default=5)
    benchmark_parser.add_argument("--seed", type=int)
    benchmark_parser.add_argument("--output", type=Path)
    return parser
```

Add `_run_benchmark` before `main()`:

```python
def _run_benchmark(args: argparse.Namespace) -> int:
    if args.runs < 1:
        logger.error("--runs must be an int >= 1")
        return 1
    config = _load_config(args.config)
    simulation = config.get("simulation", {})
    paths = config.get("paths", {})
    logging_config = config.get("logging", {})
    setup_logger(
        log_level=str(logging_config.get("level", "INFO")),
        log_dir=str(paths.get("logs", "logs")),
        log_to_file=bool(logging_config.get("log_to_file", False)),
    )
    players = ExcelHandler(Path(paths["players"])).load_players()
    base_seed = args.seed if args.seed is not None else int(simulation["seed"])
    budget = int(simulation.get("budget", 500))
    buyer_configs = list(config.get("buyers", []))
    llm_config = config.get("llm")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    root = (
        Path(args.output)
        if args.output is not None
        else Path("data/benchmarks") / run_id
    )
    if args.output is not None:
        run_id = root.name

    store = JsonStore()
    run_records = []
    for index in range(args.runs):
        run_name = f"run_{index + 1:03d}"
        run_dir = root / run_name
        seed_i = base_seed + index
        # Players are loaded once; deep copies keep runs independent.
        engine = AuctionEngine(
            [player.model_copy(deep=True) for player in players],
            _build_bidders(
                buyer_configs,
                seed_i,
                llm_config=llm_config,
                run_dir=run_dir / "traces",
            ),
            budget=budget,
            seed=seed_i,
        )
        completed = True
        try:
            report = engine.run()
        except AuctionIncompleteError:
            report = engine.partial_report()
            completed = False
        store.save_report(report, run_dir / "report.json")
        run_records.append(
            {
                "run": run_name,
                "seed": seed_i,
                "completed": completed,
                "buyers": compute_run_metrics(
                    report.to_dict(), buyer_configs, run_dir / "traces"
                ),
            }
        )
        logger.info("Benchmark run {} completed={}", run_name, completed)

    aggregates = aggregate_metrics([record["buyers"] for record in run_records])
    document = build_metrics_document(
        run_id, str(args.config), run_records, aggregates
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "metrics.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_metrics_csv(root / "metrics.csv", csv_rows(run_records))
    print_summary_table(aggregates)
    logger.success("Benchmark complete: {}", root)
    return 0
```

In `main()`, dispatch right after parsing:

```python
    args = _parser().parse_args(argv)
    if args.command == "benchmark":
        return _run_benchmark(args)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_benchmark.py tests/test_cli.py`
Expected: PASS — new benchmark tests green and existing invocations unchanged.

- [ ] **Step 5: Run the full suite and commit**

```bash
venv/bin/pytest -q -W error
git add main.py tests/test_benchmark.py
git commit -m "feat: add benchmark CLI subcommand"
```

---

### Task 9: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/project.md`
- Modify: `docs/roadmap.md`
- Modify: `tests/test_imports.py`
- Test: all files under `tests/`

- [ ] **Step 1: Update the import smoke test**

In `tests/test_imports.py`, extend the module list:

```python
@pytest.mark.parametrize(
    "module_name",
    [
        "core.notary",
        "utils.validator",
        "agents.trace",
        "agents.llm_agent",
        "benchmark.metrics",
    ],
)
def test_all_runtime_modules_are_importable(module_name):
    assert importlib.import_module(module_name)
```

- [ ] **Step 2: Update the README**

Add after the "Resume from a checkpoint" section:

- An "LLM bidders" section: `configs/llm.yaml` as the example, the `llm` global block and per-buyer block, the `OPENAI_API_KEY` environment variable requirement, and a warning that the Brave key is a placeholder.
- Resume note: when a checkpoint contains `strategy: "llm"` buyers, the auto-generated `checkpoint.llm.yaml` sidecar next to it is required; `--resume` stays the only input and `--config` remains ignored.
- A "Benchmark" section: the exact command, the output layout, the `completed: false` semantics for exhausted runs, and the `metrics.json`/`metrics.csv`/console-table outputs.

- [ ] **Step 3: Update the project documentation**

In `docs/project.md`:

- Current status: P3 (LLM agents + benchmark + sidecar resume) complete; update the verification numbers with the new suite count after Step 4.
- Configuration contract table: add the global `llm` block (fields, requiredness, constraints) and the per-buyer `llm` block (`strategy: "llm"` allowed, `llm` mapping required, per-field constraints and defaults, `spending_profile` absent -> uniform target used only by metrics).
- Input and output: describe `logs/traces/<run_dir>/<buyer_id>.jsonl` trace files and the sidecar lifecycle (written on exhaustion with LLM buyers, required on resume, propagated on a second exhaustion).
- Project structure: add `agents/llm_agent.py`, `agents/trace.py`, `benchmark/metrics.py`, `configs/llm.yaml`.
- Remove the "TODO / debito tecnico" item 1 (LLM configuration) — it is now implemented.

In `docs/roadmap.md`:

- Add a "P3 — LLM agent integration (complete)" section listing what shipped and the explicitly deferred items (opponent state, real MCP, retry/backoff, search-result caching, multi-config comparison in one command, readable transcript generator).
- Remove "LLM integration, prompts, or external model providers" from the out-of-scope list.

- [ ] **Step 4: Run the complete suite with warnings treated as errors**

Run: `venv/bin/pytest -q -W error`
Expected: the entire suite passes (all prior 103 tests plus the new tasks' tests).

- [ ] **Step 5: Manual real-API smoke (no network in the test suite)**

With a real key exported:

```bash
export OPENAI_API_KEY=sk-...
venv/bin/python main.py --config configs/llm.yaml --output /tmp/llm-report.json --checkpoint /tmp/llm-checkpoint.json
venv/bin/python main.py benchmark --config configs/llm.yaml --runs 2 --seed 42 --output /tmp/bench-smoke/
```

Expected: a complete or checkpointed run with per-agent trace files under `logs/traces/`; the benchmark produces `run_001/`, `run_002/`, `metrics.json`, `metrics.csv`, and the console table. To smoke the resume path, put the placeholder Brave key in the config, run with a pool that exhausts, then `--resume` the checkpoint and confirm the sidecar is consumed.

- [ ] **Step 6: Inspect the final diff and commit**

```bash
git status --short
git diff --check
git add README.md docs/project.md docs/roadmap.md tests/test_imports.py
git commit -m "docs: document LLM agents, benchmark, and sidecar resume"
```
