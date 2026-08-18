# LLM Agent Integration Design

## Goal

Add LLM-driven bidders to the deterministic auction engine so agent
configurations (model, temperature, system prompt, tools) can be benchmarked
over multiple runs. An LLM bidder implements the existing `Bidder` protocol,
receives only the player under auction and its own squad state, and returns
only a bid. Behavior is observed through per-agent trace logs and a dedicated
`benchmark` CLI command with a separate metrics module; the version-1 report
and checkpoint contracts do not change.

## Confirmed decisions

- `AgentManager` is the AI evolution of the current buyer: it implements
  `Bidder` (`agents/base_agent.py`) with `bid(player, squad) -> int`.
- Input context: the auctioned player plus the agent's own squad, budget, and
  `max_bid_allowed`. No information about other squads.
- Thinking traces are per-agent log files, separate from the application
  logger and from report/checkpoint documents.
- Per-agent configuration: role/personality, custom system prompt, model,
  temperature, tools, per-role spending distribution, target players.
- OpenAI-compatible APIs only, through a single shared `httpx.Client`
  (`base_url` + `model` + `api_key` read from an environment variable).
- Fixed tool set defined by the project: `search_news` (Brave free tier) and
  `submit_bid`. The model chooses which to call; real MCP is deferred.
- Benchmarks measure: parse rate, token/currency cost, roster completeness,
  budget spent/remaining, spending distribution per role vs. target, target
  players acquired, duration, plus qualitative log analysis.
- Opponent state is out of scope (roadmap).
- Prompts are in Italian; budget economy is not a hard constraint.
- `_collect_bids` parallelizes bidder calls with `ThreadPoolExecutor`; the
  engine remains synchronous and the auction outcome is unchanged.
- Resuming a checkpoint with `llm` buyers rebuilds the agents from an
  auto-generated sidecar file next to the checkpoint (`checkpoint.llm.yaml`);
  the checkpoint document and `BidderSnapshot` stay version-1 unchanged, and
  `--resume PATH` remains the only required input.

## Components

```
agents/llm_agent.py    LlmClient (shared httpx) + AgentManager (Bidder)
agents/trace.py        TraceLogger (JSONL per-agent events)
core/auction_manager.py  _collect_bids parallelized
main.py                config contract + benchmark subcommand + LLM resume sidecar
benchmark/metrics.py   pure metric functions reading reports + traces
configs/llm.yaml       example LLM configuration (mock Brave key)
```

`LlmClient` is constructed once and shared by every `AgentManager`; `httpx`
clients are thread-safe, which the parallel `_collect_bids` relies on. Each
`AgentManager` owns its `TraceLogger`, so every trace file has a single
writer and needs no locking.

## AgentManager and the function-calling loop

`AgentManager.bid(player, squad)` runs this loop:

1. Build `messages = [system_prompt, user_message]`. The user message
   contains: player (id, name, position, team, list_price), the agent's own
   roster, remaining budget, `max_bid_allowed`, and missing slots per role.
2. Call `LlmClient.chat(messages, tools, model, temperature)`.
3. On `tool_calls`, execute each call:
   - `search_news(query, count)` queries the Brave web search API and
     returns the top results as a tool message.
   - `submit_bid(amount)` validates the amount and ends the loop.
4. Append tool results to `messages` and repeat until `submit_bid` succeeds
   or `max_tool_iterations` (default 8, from config) is exhausted.

Outcome rules:

- `submit_bid` with an amount outside `[0, max_bid_allowed]` returns an
  error tool message ("amount not valid, max is N"); the model self-corrects
  within the same loop.
- Loop ends with a valid `submit_bid` -> return that amount.
- Loop exhausts the iteration cap without a valid bid, or the API returns
  `finish_reason: stop` without a `submit_bid` -> return `0` and log a
  `no_bid` trace event.
- HTTP/API/JSON errors first log an `error` trace event, then raise; the
  engine's existing path records `bidder_exception`, sets the bid to 0, and
  the auction continues.
- `BRAVE_API_KEY` absent or the configured mock value rejects the request:
  `search_news` returns the tool message "search non disponibile" and the
  agent must still bid.

Tool schemas sent to the API:

```json
{
  "name": "search_news",
  "description": "Cerca notizie recenti su un giocatore (infortuni, forma, mercato).",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "count": {"type": "integer", "minimum": 1, "maximum": 10}
    },
    "required": ["query"]
  }
}
```

```json
{
  "name": "submit_bid",
  "description": "Invia la tua offerta per il giocatore. amount intero tra 0 e max_bid_allowed (0 = passo).",
  "parameters": {
    "type": "object",
    "properties": {
      "amount": {"type": "integer", "minimum": 0}
    },
    "required": ["amount"]
  }
}
```

`max_bid_allowed` is dynamic and cannot live in the static schema; it is
stated in the user message and enforced by the tool result message.

The default system prompt is an Italian template assembled from
`role`, `personality`, `spending_profile`, and `target_players`; a configured
`system_prompt` replaces the template entirely.

## Configuration

Two blocks extend `configs/*.yaml`. The existing `deterministic` and
`random` buyers are untouched.

Global `llm` section (client defaults):

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"     # env var name; the key never lives in the file
  model: "gpt-4o-mini"              # default, overridable per buyer
  temperature: 0.7                  # default, overridable per buyer
  timeout_seconds: 30
  brave:
    base_url: "https://api.search.brave.com/res/v1/web/search"
    api_key: "INSERISCI_LA_TUA_BRAVE_API_KEY"   # mock value, replaced manually later
```

Per-buyer `llm` block (`strategy: "llm"`):

```yaml
buyers:
  - id: "buyer_1"
    name: "Squadra Alfa"
    strategy: "llm"
    llm:
      model: "gpt-4o-mini"            # optional, global default
      temperature: 0.7                # optional
      role: "fantallenatore esperto"  # optional, injected into the prompt
      personality: "prudente"         # optional
      system_prompt: |                # optional full template override
        Sei un fantallenatore...
      max_tool_iterations: 8          # optional, default 8
      tools: ["search_news", "submit_bid"]   # optional, default both
      spending_profile:               # optional target spend shares per role
        P: 0.08
        D: 0.20
        C: 0.35
        A: 0.37
      target_players:                 # optional
        - "Lautaro Martínez"
```

Validation is up front in `_validate_config` (`main.py`), extending the P2
contract:

- `strategy` may be `"llm"` and then requires an `llm` mapping.
- `temperature` numeric in `[0, 2]`; `max_tool_iterations` int >= 1;
  `tools` a non-empty subset of `{search_news, submit_bid}` that always
  contains `submit_bid`; `spending_profile` keys subset of `{P, D, C, A}`
  with values in `[0, 1]` summing to 1 within a 0.01 tolerance;
  `target_players` a list of non-empty strings.
- Global `llm` section (when any buyer is `llm`): non-empty `base_url`,
  `api_key_env`, `model`, `brave.base_url`, `brave.api_key`; `timeout_seconds`
  int > 0.
- The LLM API key is read from `os.environ[api_key_env]` at startup; a
  missing variable is a clear error before the auction starts.

`spending_profile` absent -> uniform 25% shares (not injected into the
prompt, but used by metrics as the neutral target).

## Trace logs

`TraceLogger` appends one JSON object per event to
`logs/traces/<run_dir>/<buyer_id>.jsonl`; `<run_dir>` is chosen by the
caller (`main.py` or `benchmark`), not by the engine. Deterministic and
random bidders trace nothing. The file is opened in append mode at
construction; every event is written and flushed immediately.

Event shape:

```json
{"ts": "2026-08-18T10:00:00+00:00", "buyer_id": "buyer_1", "player_id": "pl_123",
 "phase": "tool_call", "iteration": 2,
 "content": {"name": "search_news", "args": {"query": "Lautaro infortunio"}}}
```

Phases:

| phase | content |
|---|---|
| `context` | full user payload (player + own squad/budget/`max_bid_allowed`) |
| `thinking` | model reasoning text before tool calls |
| `tool_call` / `tool_result` | tool name, args, result |
| `llm_call` | model and iteration number |
| `bid` | final amount |
| `no_bid` / `error` | failure reason (cap exceeded, HTTP error, stop without bid) |
| `usage` | `prompt_tokens` / `completion_tokens` for one API call |

Events are keyed on `player_id`, which uniquely identifies an auction within
a run (every player is auctioned exactly once), so the `Bidder` protocol
needs no change. The full message sequence is reconstructible from `context`
plus `tool_call`/`tool_result` events, so raw messages are not logged.

## Parallel `_collect_bids`

`core/auction_manager.py::_collect_bids` submits one future per eligible
bidder to a per-call `ThreadPoolExecutor(max_workers=len(bidders))` and
collects results in bidder order:

- workers only call `bidder.bid(player, squad)`;
- `squad.validate_bid` and `_record_bid_issue` run in the main thread, in
  bidder order, so issue ordering stays deterministic and the engine needs
  no locks;
- exceptions from `future.result()` follow the existing sequential path
  (`bidder_exception`, bid 0, auction continues);
- the bids dict is assembled in bidder order, so the outcome (including the
  max-bid tie rule) is identical to the sequential version;
- per-call pool with a context manager: no engine lifecycle or shutdown
  state; pool creation cost is irrelevant next to LLM latency;
- `max_workers` is not configurable.

## Benchmark command and metrics

New argparse subcommand in `main.py`; existing invocations are unchanged
(subparsers are optional):

```
python main.py benchmark --config configs/llm.yaml --runs 5 [--seed 42] [--output data/benchmarks/]
```

Each run `i` uses `seed + i`; players are loaded once, one fresh
`AuctionEngine` per run. An `AuctionIncompleteError` in a run saves the
partial report and records `completed: false` (roster completeness is a
metric, not a benchmark crash).

Output layout (`<run_id>` is a timestamp unless `--output` names a
directory):

```
data/benchmarks/<run_id>/
  run_001/report.json
  run_001/traces/<buyer_id>.jsonl
  ...
  metrics.json
  metrics.csv
```

`benchmark/metrics.py` contains pure functions that read report JSON and
trace JSONL files (no engine coupling). Per agent per run:

| metric | source |
|---|---|
| `parse_rate` | `bid` events / `context` events |
| `cost_tokens`, `cost_eur` | sum of `usage` events; a constant price map per model (USD per 1M tokens, in/out) — unknown models yield `null` |
| `roster_complete`, `missing_roles` | report squads |
| `budget_spent`, `budget_remaining` | report squads |
| `spending_share_by_role`, `spending_distance` | share of total spent per role vs `spending_profile`; distance = Σ\|actual − target\| (uniform target if absent) |
| `targets_acquired` | `target_players` names ∩ the buyer's sold players (case-insensitive match) |
| `duration_seconds` | report |
| `tools_used` | `tool_call` counts by name |
| `llm_calls` | count of `llm_call` events |

Aggregates across runs: mean and population standard deviation per metric.
Output is a plain-print console table, `metrics.json`, and `metrics.csv`
(stdlib csv). No third-party dependencies.

Not included: multi-config comparison in one command (run `benchmark` once
per config and compare tables), a readable transcript generator (qualitative
analysis reads the JSONL directly), retry/backoff on transient HTTP errors,
search-result caching.

## Resuming checkpoints with LLM buyers (sidecar)

The version-1 checkpoint contract does not change: an LLM buyer's snapshot is
the standard `{id, name, strategy: "llm", priority}` entry (`priority`
defaults to the buyer index, as today), and the checkpoint document contains
no LLM configuration. To keep `--resume PATH` autonomous, `main.py` persists
the LLM configuration in a machine-generated sidecar file next to the
checkpoint, derived from the checkpoint path by replacing the `.json` suffix:
`checkpoint.json` -> `checkpoint.llm.yaml`. The sidecar is written only when
the checkpoint contains at least one `strategy: "llm"` buyer; pure
deterministic/random resumes are unchanged. The sidecar stores the
`api_key_env` variable *name*, never the key itself, so it contains no
secrets and can be stored next to the checkpoint.

Sidecar shape (`schema_version: 1`):

```yaml
schema_version: 1
llm:                      # global block, copied from the loaded config
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  model: "gpt-4o-mini"
  temperature: 0.7
  timeout_seconds: 30
  brave:
    base_url: "https://api.search.brave.com/res/v1/web/search"
    api_key: "..."
buyers:                   # per-buyer blocks, only where present in the config
  buyer_1:
    llm:
      model: "gpt-4o-mini"
      role: "fantallenatore esperto"
      spending_profile: {P: 0.08, D: 0.20, C: 0.35, A: 0.37}
```

Save flow (in `main.py`'s `AuctionIncompleteError` handler): after the
checkpoint is written, if any buyer snapshot has `strategy: "llm"`, extract
the global `llm` block and the per-buyer `llm` blocks from the loaded config
and write the sidecar next to the checkpoint path (including a
`--checkpoint`-overridden destination). A failed sidecar write is reported
clearly; the run already exits 1 on pool exhaustion.

Resume flow (`--resume PATH`): if no checkpoint buyer is `llm`, the sidecar
is ignored and the version-1 flow is unchanged. Otherwise the sidecar is
required: a missing file is a clear pre-auction error, exit 1. The sidecar is
shape/version checked, then merged into the buyer configs rebuilt from the
snapshots (`buyers.<id>.llm` is attached to the matching buyer; a buyer
without an entry uses the global defaults), and the normal `_build_bidders`
LLM branch rebuilds the `AgentManager`s. `--config` passed with `--resume`
remains ignored: the checkpoint (plus sidecar) is authoritative, per the P1
rule. A missing `api_key_env` variable at resume time fails before the
auction, as for fresh runs.

If a resumed run exhausts the pool again, the new checkpoint is written and
the sidecar is propagated next to the new path from the in-memory
configuration. Traces of a resumed run go to a new run directory, as for
fresh runs.

Resuming an LLM auction restarts the policy from the stored configuration
with fresh decisions; as with `RandomBidder` re-seeding in P1, a resumed run
is not an exact replay of the interrupted one.

The engine stays sidecar-agnostic: it only receives `Bidder` objects.

Benchmark runs do not resume: each run is fresh and pool-exhaustion runs are
recorded as `completed: false`.

## Error handling

- Missing LLM `api_key_env` variable -> clear pre-auction error, exit 1.
- Invalid LLM configuration -> `_validate_config` error before any work.
- Brave key missing or mock value -> `search_news` returns "search non
  disponibile"; the agent can still bid.
- `submit_bid` out of range -> tool error, self-correction loop.
- Iteration cap exhausted or stop without a bid -> bid 0 + `no_bid` trace.
- HTTP/API/JSON failure -> raise; engine records `bidder_exception`, bid 0,
  auction continues.
- `--resume` on a checkpoint with `llm` buyers and a missing or invalid
  sidecar -> clear pre-auction error, exit 1.

## Testing (TDD)

- `AgentManager` loop with an injected fake client: search-then-submit
  returns the bid; no-submit and cap-exhaustion return 0 with `no_bid`;
  out-of-range submits self-correct; exceptions propagate.
- `LlmClient` request building and tool result parsing against
  `httpx.MockTransport` (no network in tests).
- `TraceLogger` emits one valid JSON line per event with the documented
  fields.
- `_collect_bids` parallel version produces the same outcome as sequential,
  including issue recording (existing 103 tests) plus a smoke test with slow
  bidders asserting the final result, no timing assertions.
- The sidecar is written next to checkpoints containing `llm` buyers and is
  absent otherwise; its content mirrors the loaded config.
- Resume with a valid sidecar rebuilds `AgentManager`s (fake client) and
  completes the auction; a missing or malformed sidecar fails with a clear
  pre-auction error.
- A second pool exhaustion during a resumed run propagates the sidecar next
  to the new checkpoint path.
- `_validate_config` accepts valid LLM configs and rejects each invalid
  shape with a clear message; missing API key error is covered.
- `benchmark/metrics.py` computed on synthetic report + trace fixtures;
  benchmark runner end-to-end with a fake LLM client (mock transport).
- Real-API behavior (Brave, LLM provider) is exercised manually, not in the
  test suite.

## Acceptance criteria

- `AgentManager` implements `Bidder` and returns only an int.
- The full function-calling loop works against a mocked transport:
  search-then-submit, no-bid, out-of-range self-correction, cap.
- LLM configuration is validated up front with clear errors.
- A missing LLM API key stops the run before the auction.
- Trace events follow the documented schema in per-agent JSONL files,
  separate from report/checkpoint documents.
- `_collect_bids` is parallel and its outcome is identical to the
  sequential version; the existing suite stays green.
- `benchmark --config --runs N` produces reports, traces, `metrics.json`,
  `metrics.csv`, and the console table; pool-exhausted runs are recorded as
  `completed: false` and the benchmark continues.
- Report and checkpoint documents remain version-1; `--resume` on a
  checkpoint with `llm` buyers continues autonomously from the sidecar, and
  a missing sidecar fails with a clear error.
- The full existing test suite remains green with warnings treated as
  errors.
