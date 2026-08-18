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

## LLM bidders

`configs/llm.yaml` is the example configuration for LLM-driven bidders. It
adds a global `llm` block and a per-buyer `llm` block:

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  model: "gpt-4o-mini"
  temperature: 0.7
  timeout_seconds: 30
  brave:
    base_url: "https://api.search.brave.com/res/v1/web/search"
    api_key_env: "BRAVE_API_KEY"

buyers:
  - id: "buyer_1"
    name: "Squadra Alfa"
    strategy: "llm"
    llm:
      role: "fantallenatore esperto"
      personality: "prudente"
      spending_profile: {P: 0.08, D: 0.20, C: 0.35, A: 0.37}
```

Each `strategy: "llm"` buyer is an `AgentManager` that loops over
OpenAI-compatible `chat` calls with the fixed tool set
`{search_news, submit_bid}` until it returns a valid bid. The API key is read
from the environment variable named by `llm.api_key_env` (`OPENAI_API_KEY` in
the example); only the variable name may appear in configuration files and
sidecars. A missing variable is a pre-auction error.

`search_news` uses the Brave free tier and degrades to the tool message
`"search non disponibile"` when the key is missing, is the mock placeholder, or
the request fails. The Brave key is read from the environment variable named
by `llm.brave.api_key_env` (`BRAVE_API_KEY` in the example); a missing or
placeholder value disables live search. Like the LLM key, the Brave key never
appears in configuration files or sidecars, only the variable name.

Every LLM buyer writes one JSON object per event to
`logs/traces/<run_dir>/<buyer_id>.jsonl`; the `<run_dir>` is chosen by the
caller, never by the engine.

### Resuming LLM checkpoints

When a checkpoint contains `strategy: "llm"` buyers, the CLI writes an
auto-generated `checkpoint.llm.yaml` sidecar next to it (`schema_version: 1`)
with the global `llm` block and the per-buyer `llm` blocks. Resuming such a
checkpoint requires the sidecar; a missing or invalid sidecar exits `1` before
the auction starts. With `--resume`, the checkpoint plus sidecar are the only
inputs: `--config` stays ignored. A second pool exhaustion propagates the
sidecar next to the new checkpoint.

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
