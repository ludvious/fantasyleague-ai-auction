## Roadmap

### P1 — Versioned reports and resumable checkpoints (complete)

P1 turns the pool-exhaustion checkpoint into an autonomous continuation point
while preserving the complete partial-auction report.

- define and version the JSON report and checkpoint contracts;
- keep all report data in checkpoints, plus the full player state and the
  embedded simulation/bidder configuration needed for an autonomous resume;
- add `--resume CHECKPOINT` to continue only incomplete squads against the
  players that were unsold in the previous round;
- keep completed squads and all prior transactions intact across rounds;
- emit a final report when every squad becomes complete, or a new checkpoint
  when the unsold pool is exhausted again;
- generate resumable checkpoints only for pool exhaustion, not for unrelated
  internal or configuration errors;
- include cumulative and last-run timestamps/durations in both document types;
- include accumulated `BidIssue` diagnostics in both reports and checkpoints;
- use the stored seed with a fresh simple random generator on each resumed
  round; exact random-generator continuation is not part of the MVP;
- add serialization, loading, resume-flow, and compatibility tests.

### P2 — CLI and legacy compatibility (complete)

- stabilize the CLI contract, configuration, options, and error messages;
- preserve required legacy imports and APIs;
- extend CLI tests without moving CLI concerns into the domain.
- the configuration contract is now explicit (TODO 2): `configs/default.yaml`
  is the canonical schema and the pre-MVP root `config.yaml` was removed;

The CLI now validates the configuration contract up front (`simulation.seed`
int, `simulation.budget` int ≥ 25, non-empty `buyers` list with valid
`id`/`name`) and reports clear, uniform error messages;
`tests/test_cli.py` covers the contract without moving CLI concerns into the
domain.

### P3 — LLM agent integration (complete)

- LLM-driven bidders (`AgentManager`, renamed to `CoachAgent` in P4)
  implementing the `Bidder` protocol via
  an OpenAI-compatible function-calling loop over the fixed tool set
  `{search_info, submit_bid}`;
- one shared thread-safe `LlmClient` (httpx) per run;
- per-agent JSONL trace logs under `logs/traces/<run_dir>/<buyer_id>.jsonl`;
- parallel bid collection was considered and removed; bids are collected
  sequentially in bidder order (`_collect_bids`), keeping validation and issue
  ordering identical to the original design;
- `benchmark` CLI subcommand with pure metrics (`metrics.json`,
  `metrics.csv`, console table) and `completed: false` for exhausted runs;
- sidecar-based resume: `checkpoint.llm.yaml` written next to every
  checkpoint, required and authoritative on `--resume`, propagated on
  a second exhaustion;
- configuration contract extended with the global `llm` block and per-buyer
  `llm` blocks (temperature, max tool iterations, tools, spending profile,
  target players); API keys read from `api_key_env`, never stored in files.

Explicitly deferred:

- opponent state in the LLM context (input is the auctioned player plus the
  agent's own squad/budget/`max_bid_allowed`);
- real MCP tool integration (tools are fixed function schemas);
- retry/backoff for LLM calls (exceptions are traced and re-raised);
- search-result caching;
- multi-config comparison in one benchmark command;
- readable transcript generator from the JSONL traces.

### P4 — CoachAgent: discovery, prompt architecture, outcome events (complete)

- `AgentManager` renamed to `CoachAgent`; the client/search code split into
  `agents/llm_client.py`;
- coach profiles auto-discovered from `coachAgent_*.md` files in
  `paths.coaches` (optional YAML front-matter for technical fields plus a
  markdown strategy body), validated through the existing LLM contract, with
  id/name derived from the filename;
- shared `agents/prompts/system_prompt.md` rendered with domain placeholders
  (roster requirements, budget, max-bid rule) so the regulation can never
  drift from `core/models.py`; structured fields and the profile body are
  appended, and an explicit `system_prompt` still overrides everything;
- bids are validated inside the coach loop via `Squad.validate_bid`, with the
  domain error returned to the model as a tool result; the engine keeps its
  post-hoc validation and `BidIssue` safety net;
- reasoning (`thinking`) is traced and mirrored to the application log at INFO;
- after each lot the engine calls `observe(result, squad)` on the polled
  bidders; `CoachAgent` traces `auction_result` (won: player, price, updated
  roster, budget; lost: minimal) and logs it;
- the `checkpoint.llm.yaml` sidecar (schema unchanged) stores the resolved
  `system_prompt` per buyer, so `--resume` needs neither the YAML nor the
  `.md` files.

Still deferred: per-agent session memory with LLM reaction messages to
outcome notifications (the `observe` hook is the seam), opponent state and
observed prices in the dynamic context.

## Explicitly out of scope for now

The following must not be introduced without an explicit architectural
decision:

- full event-sourced replay or arbitrary mid-auction resume; P1 resumes only
  from pool-exhaustion checkpoints between auction rounds;
- event sourcing or asynchronous bidding;
- tie-breaker rounds;
- real-team player limits;
- quotation-based starting prices;
- unversioned changes to the JSON report or checkpoint formats.

## Backlog (candidates, not yet designed)

- interactive `--step` mode with save-and-quit (issue #9).