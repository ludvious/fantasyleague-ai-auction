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

### P2 — CLI and legacy compatibility

- stabilize the CLI contract, configuration, options, and error messages;
- preserve required legacy imports and APIs;
- extend CLI tests without moving CLI concerns into the domain.
- the configuration contract is now explicit (TODO 2): `configs/default.yaml`
  is the canonical schema and the pre-MVP root `config.yaml` was removed;
- reintroduce LLM bidder configuration when AI agents are approved. The
  historical fields from the removed `config.yaml` are the starting point:
  `llm.provider`, `llm.model`, `llm.max_tokens`, `llm.temperature`,
  `llm.timeout`, `llm.retry_attempts`, `llm.retry_delay`,
  `buyers[].personality` (kept in git history).

## Explicitly out of scope for now

The following must not be introduced without an explicit architectural
decision:

- LLM integration, prompts, or external model providers;
- full event-sourced replay or arbitrary mid-auction resume; P1 resumes only
  from pool-exhaustion checkpoints between auction rounds;
- event sourcing or asynchronous bidding;
- tie-breaker rounds;
- real-team player limits;
- quotation-based starting prices;
- unversioned changes to the JSON report or checkpoint formats.

The earlier MVP design and implementation plan are available under
`docs/superpowers/specs/` and `docs/superpowers/plans/`.