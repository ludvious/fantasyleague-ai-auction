# fantasyleague-ai-auction

A non-interactive CLI for simulating an Italian fantasy-football auction.


## Development disclosure

This project is currently developed with full, end-to-end assistance from AI
coding agents. AI agents are used to explore ideas, implement changes, write
and review tests, inspect the codebase, document decisions, and evaluate the
project's evolving architecture.

This is intentional: the project is also an ongoing experiment in
understanding AI-assisted software development in practice—where it helps,
where it fails, and how its output must be tested, reviewed, and improved.
The codebase, workflows, and design will continue to evolve as those lessons
emerge.

Contributions are welcome when they align with this approach. Contributors
should be comfortable working in an AI-assisted workflow and with changes
that may be proposed, implemented, or documented by AI agents, subject to
human review and automated testing. Contributions that require excluding
AI-assisted development from the project are not currently aligned with its
goals.

## Quick start

Create an environment and install the dependencies:

```bash
python -m venv venv
pip install -r requirements.txt
```

Run the default simulation:

```bash
python main.py --config configs/default.yaml
```

The default configuration uses:

- `data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx` as the player source;
- a budget of 500 credits;
- seed `42`;
- four deterministic bidders;
- `data/results/report.json` for successful reports;
- `data/checkpoints/checkpoint.json` for pool-exhaustion checkpoints that can
  be resumed.

The output directories are created automatically when needed.

## CLI options

```text
--config PATH       YAML configuration file (default: configs/default.yaml)
--players PATH      Override the configured Excel workbook
--output PATH       Override the report path or output directory
--checkpoint PATH   Override the checkpoint path or directory
--resume PATH       Resume from a pool-exhaustion checkpoint
--seed INTEGER      Override the configured random seed

benchmark           Run multiple auctions and aggregate per-agent metrics
  --config PATH     YAML configuration file (default: configs/default.yaml)
  --runs N          Number of runs (default: 5)
  --seed INTEGER    Seed of run 1; run i uses seed + i (0-based)
  --output PATH     Benchmark output directory
```

For example:

```bash
venv/bin/python main.py \
  --config configs/default.yaml \
  --players data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx \
  --output /tmp/auction-report.json \
  --checkpoint /tmp/auction-checkpoint.json \
  --seed 42
```

## Resume from a checkpoint

If the player pool is exhausted before every squad is complete, the CLI writes a
version-1 `auction_checkpoint` JSON document. The checkpoint contains the
cumulative report, the full player state, the remaining `UNSOLD` players, the
missing roles, and the simulation and bidder snapshots needed for continuation.
It is therefore autonomous: the original YAML configuration and Excel workbook
are not needed for a resume.

Resume with:

```bash
venv/bin/python main.py \
  --resume /path/to/auction-checkpoint.json \
  --output /path/to/final-report.json
```

A resumed round auctions only players that were `UNSOLD` in the checkpoint and
calls only bidders whose squads are incomplete. Completed squads, cumulative
rosters, budgets, transactions, diagnostics, counters, and timing are retained.
The stored seed is reused with a fresh random generator; exact replay of the
internal state of `random.Random` is not part of P1.

When `--resume` is used, the checkpoint snapshots are authoritative, so
`--config`, `--players`, and `--seed` are not read. `--output` selects the final
report destination and defaults to `data/results/report.json`; if the resumed
round is incomplete, `--checkpoint` selects the replacement checkpoint
(destination), and without it the input checkpoint is replaced.

The process returns `0` after a complete auction and `1` for pool exhaustion,
configuration errors, invalid checkpoint data, file errors, or unexpected
auction errors. Only pool exhaustion writes a resumable checkpoint; other
failures do not write one.

## CoachAgent (LLM bidders)

`configs/llm.yaml` is the example configuration for CoachAgent-driven
auctions. Coaches are auto-discovered as `coachAgent_*.md` files in the
directory named by `paths.coaches` (the example points at `agents/coach/`):

```yaml
paths:
  coaches: "agents/coach"

llm:
  base_url: "https://opencode.ai/zen/go/v1"
  api_key_env: "OPENCODE_API_KEY"
  model: "deepseek-v4-flash"
  temperature: 0.7
  timeout_seconds: 30
  # Header applicati a tutte le richieste. x-opencode-session è richiesto
  # da OpenCode Go; se omesso viene generato un default per-run.
  # headers:
  #   x-opencode-session: "asta-2026"
  search:
    provider: "responses"
    model: "deepseek-v4-flash"
    # base_url e api_key_env ereditano da llm sopra; max_output_tokens
    # default 400. Altri provider:
    #   anthropic → provider + model (base_url default https://api.anthropic.com)
    #   brave     → search classica "titolo — url" (provider + api_key_env)
```

Adding a coach means adding a markdown file, without touching the YAML:

```markdown
---
model: "deepseek-v4-flash"
temperature: 0.7
spending_profile: {P: 0.08, D: 0.20, C: 0.35, A: 0.37}
target_players: ["Lautaro Martínez"]
---

Sei il coach della Squadra Alfa. Stile prudente: ...
```

The filename derives the coach id (`coachAgent_Alfa.md` → `alfa`); the optional
front-matter carries technical fields (`model`, `temperature`,
`max_tool_iterations`, `tools`, `spending_profile`, `target_players`,
`system_prompt`) validated with the LLM contract, and the markdown body is the
agent profile. The system prompt is the shared `agents/prompts/system_prompt.md`
(regulation rendered from the domain) plus the profile; an explicit
`system_prompt` replaces it entirely. YAML `buyers` still work, and remain
required for `deterministic`/`random` bidders.

Each coach loops over OpenAI-compatible `chat` calls with the fixed tool set
`{search_info, submit_bid}` until it returns a bid that passes
`Squad.validate_bid`; the domain error goes back to the model for a retry.
Reasoning is traced and logged at INFO. After every resolved lot the engine
notifies the polled coaches through `observe`: winners receive the player,
price, and updated roster, losers a `lost` event, all traced as
`auction_result`. The API key is read from the environment variable named by
`llm.api_key_env` (`OPENCODE_API_KEY` in the example); only the variable name
may appear in configuration files and sidecars. A missing variable is a
pre-auction error. At startup the CLI automatically loads a gitignored `.env`
file from the working directory (real shell variables win); copy
`.env.example` and fill in the key instead of exporting it on every run.

`search_info` is configured through the optional `llm.search` block, which
supports the `responses`, `anthropic`, and `brave` providers. The native
providers (`responses`, `anthropic`) return a summary of the news with cited
sources (`Fonti:`); `brave` keeps the classic `titolo — url` listing and
remains available as the legacy provider (the old `llm.brave` block is still
accepted when `llm.search` is absent). The search key is read from the
environment variable named by `llm.search.api_key_env` (inherited from
`llm.api_key_env` when omitted); a missing or placeholder key, or a failed
request, degrades to the tool message `search non disponibile`. Like the LLM
key, the search key never appears in configuration files or sidecars, only
the variable name.

Every coach writes one JSON object per event to
`logs/traces/<run_dir>/<buyer_id>.jsonl`; the `<run_dir>` is chosen by the
caller, never by the engine.

### Resuming LLM checkpoints

When a checkpoint contains `strategy: "llm"` buyers, the CLI writes an
auto-generated `checkpoint.llm.yaml` sidecar next to it (`schema_version: 1`)
with the global `llm` block and one per-buyer `llm` block carrying the fully
resolved `system_prompt`. Resuming such a checkpoint requires the sidecar; a
missing or invalid sidecar exits `1` before the auction starts. With
`--resume`, the checkpoint plus sidecar are the only inputs: `--config` (and
the original `coachAgent_*.md` files) stay unread. A second pool exhaustion
propagates the sidecar next to the new checkpoint.

## Benchmark

```bash
venv/bin/python main.py benchmark \
  --config configs/llm.yaml \
  --runs 2 \
  --seed 42 \
  --output data/benchmarks/2026-08-18/
```

Run `i` uses seed `seed + i` (0-based) and a fresh `AuctionEngine` with
deep-copied players. The output layout is:

```text
DIR/run_NNN/report.json           per-run report
DIR/run_NNN/traces/<buyer>.jsonl  per-run per-agent traces
DIR/metrics.json                  run records + aggregates
DIR/metrics.csv                   one row per buyer per run
```

Pool exhaustion inside a run saves the partial report and records
`completed: false`; the benchmark continues with the next run and never
resumes. Without `--output`, the root is `data/benchmarks/<timestamp>/`.
