# CoachAgent Design

## Goal

Turn the LLM bidder into a first-class **CoachAgent**: auto-discovered
per-agent profile files, a shared non-repeated common prompt fed by the
domain rules, domain-validated bids with in-loop retry, post-auction outcome
notifications that double as the future memory seam, and reasoning visible in
the console. Auction rules, engine behavior, and versioned JSON contracts stay
as they are.

## Confirmed decisions

- **Rename/split**: `AgentManager` becomes `CoachAgent`. `agents/llm_agent.py`
  splits into `agents/llm_client.py` (`LlmClient`, `TOOL_SCHEMAS`, search) and
  `agents/coach_agent.py` (`CoachAgent`).
- **Discovery**: coaches are `coachAgent_*.md` files in the `paths.coaches`
  directory (example config points at the repo `agents/` root). No YAML buyer
  entry is needed; `agents/` is not scanned unless `paths.coaches` is set.
- **Coach file**: optional YAML front-matter (technical fields) plus a free
  markdown body (the agent profile). Filename derives `id`/`name`; front-matter
  can override. Unknown front-matter fields are rejected; duplicate ids are
  rejected.
- **Prompt**: `agents/prompts/system_prompt.md` is the shared, non-repeated prompt
  (identity, goals, information priorities, decision method, uncertainty,
  tools, response format, and the league regulation). `{roster_requirements}`,
  `{budget}`, and `{max_bid_rule}` are replaced at render time from the domain,
  so the prompt can never drift from `core/models.py`. The coach profile and
  the structured fields (`role`, `personality`, `spending_profile`,
  `target_players`) are appended. An explicit `system_prompt` remains a full
  override.
- **Dynamic user message**: unchanged shape — auctioned player, current roster,
  budget remaining, `max_bid_allowed`, missing roles per role.
- **Stateless agents**: no conversation history across lots. Context is rebuilt
  for every player. `observe(result, squad)` after each lot is the seam for a
  future per-agent session/memory mode (which would then also switch to LLM
  reaction messages).
- **Outcome notification**: winner gets player, price, updated roster,
  `budget_remaining`, missing roles; losers get `{"outcome": "lost"}`. Written
  to the JSONL trace (`auction_result`) and the app log. No LLM call.
- **Bid validation**: inside the `CoachAgent` loop via `Squad.validate_bid`;
  the domain error message goes back to the model as a tool result and the
  model may retry within `max_tool_iterations`. The engine keeps its
  post-hoc validation and `BidIssue` safety net.
- **Reasoning visibility**: the existing `thinking` trace event is mirrored to
  the application log at INFO.
- **Compatibility**: YAML `strategy: llm` is unchanged. The sidecar stays
  `schema_version: 1`; the resolved system prompt is stored in each llm
  buyer's existing `system_prompt` field so `--resume` needs neither the config
  nor the `.md` files. Report and checkpoint schemas are untouched.
  Deterministic/random bidders get a no-op `observe`.
- `agents/prompt.md` is absorbed into `agents/prompts/system_prompt.md` and removed.

## File structure

```text
agents/
  llm_client.py        LlmClient, TOOL_SCHEMAS, provider search
  coach_agent.py       CoachAgent decision loop
  coach_loader.py      coach discovery, front-matter parsing, load_buyer_configs
  coach_prompt.py      common-prompt rendering and placeholder substitution
  prompts/
    system_prompt.md          shared prompt + regulation placeholders
  coachAgent_Alfa.md   example coach profiles (auto-discovered via paths.coaches)
  coachAgent_Beta.md
  coachAgent_Gamma.md
  coachAgent_Delta.md
  buyer_agent.py       deterministic/random bidders (+ no-op observe)
  base_agent.py        Bidder protocol (+ observe)
  trace.py             unchanged JSONL trace writer
```

## Coach file contract

```markdown
---
model: "gpt-5.6-luna"
temperature: 0.7
spending_profile: {P: 0.08, D: 0.20, C: 0.35, A: 0.37}
target_players: ["Lautaro Martínez"]
---

Sei il coach della Squadra Alfa. Stile prudente: ...
```

- `id` default: filename slug without the `coachAgent_` prefix
  (`coachAgent_Alfa.md` → `alfa`). Front-matter `id` overrides.
- `name` default: filename segment (`Alfa`). Front-matter `name` overrides.
- Front-matter keys: `id`, `name`, `model`, `role`, `personality`,
  `temperature`, `max_tool_iterations`, `tools`, `spending_profile`,
  `target_players`, `system_prompt`. Everything except `id`/`name` is validated
  with the existing `validate_llm_buyer` contract.
- Files are loaded in filename order; coaches get priorities after YAML buyers.
- Collisions between YAML buyer ids and coach ids are an error.

## Prompt assembly

`render_system_prompt(budget=..., profile=..., role=..., personality=...,
spending_profile=..., target_players=..., override=...)`:

1. `override` (non-blank) wins outright.
2. `system_prompt.md` with placeholders replaced from `ROSTER_REQUIREMENTS` and the
   configured budget.
3. Structured field lines when present (parity with today's YAML buyers).
4. `# Profilo dell'agente` + markdown body when present.

Missing placeholders raise `ValueError` so a broken template fails loudly.

## Auction flow

1. `load_config` validates the YAML contract; `load_buyer_configs` merges YAML
   buyers and discovered coaches. At least one buyer from either source.
2. The engine picks a random available player and calls
   `CoachAgent.bid(player, squad)`.
3. The coach searches (`search_info`), reasons (traced + logged), and submits
   with `submit_bid`; invalid offers are rejected by `Squad.validate_bid` and
   the model retries inside the loop.
4. The engine resolves the winner (unchanged rules) and records the result.
5. The engine calls `observe(result, squad)` on every active (polled) bidder;
   `CoachAgent` traces/logs `won` or `lost`.
6. Roster completion check and loop are unchanged; complete coaches are never
   polled.

## Error handling

- Missing coaches directory → `FileNotFoundError`, CLI exits 1.
- Invalid front-matter (not a mapping, unknown keys, unclosed) → `ValueError`
  naming the file.
- Invalid coach fields → `ValueError` via `validate_llm_buyer`.
- Duplicate coach ids (file-file or YAML-file) → `ValueError`.
- Empty/missing common prompt placeholders → `ValueError` at render.
- Coach exceptions and invalid engine-side bids keep producing `BidIssue`
  diagnostics exactly as today.

## Testing

- `tests/test_coach_prompt.py`: placeholder substitution, profile/structured
  assembly, override precedence, missing-placeholder failure.
- `tests/test_coach_loader.py`: discovery/order, front-matter parsing, filename
  defaults, unknown fields, duplicates, empty directory, missing directory.
- `tests/test_coach_agent.py` (renamed): existing loop tests, domain-validation
  retry (out of range, wrong type, full role), reasoning log, `observe`
  winner/loser events.
- `tests/test_auction_manager.py`: fakes gain no-op `observe`; new test that
  only active bidders are notified.
- `tests/test_cli.py`: coach discovery end-to-end with a fake client, sidecar
  carries the resolved prompt, resume uses the stored prompt without `.md`
  files; updated sidecar assertions.
- `tests/test_benchmark.py`: benchmark driven by a coaches directory, metrics
  read the front-matter fields.
- Full suite stays green with `venv/bin/pytest -q -W error`.

## Explicitly deferred

- Per-agent session memory with LLM reaction to outcome messages (the
  `observe` seam exists for this).
- Opponent state, observed prices, and available-player counts in the dynamic
  context.
- Readable transcript generator from JSONL traces.
- LLM retry/backoff and search-result caching.
