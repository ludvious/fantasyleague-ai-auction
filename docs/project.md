# fantasyleague-ai-auction

A non-interactive CLI for simulating an Italian fantasy-football auction.
The MVP is synchronous, deterministic, and reproducible through seeded
`random.Random` instances. Auction rules live in the domain, while bidder
strategies, Excel input, JSON persistence, and the CLI remain separate
adapters.

## Current status

The deterministic auction MVP is implemented and P1, P2, and P3 are complete:

- strict bid validation is centralized in `Squad`;
- invalid bidder output and bidder exceptions are isolated and recorded as
  structured diagnostics;
- canonical player resolution prevents external player copies from mutating
  auction state;
- reports and checkpoints use version-1 typed JSON contracts;
- pool-exhaustion checkpoints are autonomous and resume with `--resume`;
- LLM-driven bidders (`AgentManager`) loop over OpenAI-compatible
  function-calling until a valid `submit_bid` arrives, with per-agent JSONL
  traces under `logs/traces/`;
- bids are collected in parallel with a per-call thread pool while validation
  and issue recording stay sequential in bidder order;
- checkpoints containing `llm` buyers save a `checkpoint.llm.yaml` sidecar and
  resume from it;
- the `benchmark` subcommand runs multiple auctions and aggregates pure
  per-agent metrics into `metrics.json`, `metrics.csv`, and a console table.

Latest verification:

- `venv/bin/pytest -q -W error`: **161 tests passed**;
- real-workbook simulation: **100 players sold**, **37 unsold**, and **4
  complete squads** of 25 players.

P1 resumes only between auction rounds, after the current player pool is
exhausted. It does not persist an arbitrary mid-auction state, event history,
or the exact internal state of `random.Random`.

## Squads rules

Each squad contains exactly 25 players:

| Role | Required players |
| --- | ---: |
| `P` (goalkeepers) | 3 |
| `D` (defenders) | 8 |
| `C` (midfielders) | 8 |
| `A` (forwards) | 6 |

### Other rules:

- the minimum initial budget is 25 credits; the default is 500;
- every empty roster slot reserves one credit;
- the maximum legal bid is calculated from the remaining budget and reserved
  slots;
- a bid of `0` means pass;
- a Python `int` is required exactly: `bool`, floats, strings, `None`, negative
  values, and bids above the legal maximum are invalid;
- no positive bids make the player unsold;
- a tie for the highest positive bid makes the player unsold;
- a player is auctioned at most once in this MVP;
- the Excel quotation is informational and is not used as a starting bid;
- internal engine errors and state invariant violations remain fail-fast.

When a bidder returns an invalid value or raises an exception, the engine
emits a warning, stores a `BidIssue`, normalizes that bidder's offer to `0`,
and continues with the remaining bidders.


## Configuration

`configs/default.yaml` is the active default configuration. Bidders support
the `deterministic`, `random`, and `llm` strategies:

```yaml
simulation:
  budget: 500
  seed: 42

buyers:
  - id: buyer_1
    name: Squadra Alfa
    strategy: deterministic
    priority: 0
  - id: buyer_2
    name: Squadra Beta
    strategy: random
```

`DeterministicBidder` produces a stable priority-based bid. `RandomBidder`
uses an injected seeded random generator and can return zero. Neither bidder
mutates the squad or player; the domain validates and applies purchases.
`configs/llm.yaml` is the example configuration for LLM-driven bidders; see
the contract table below.

### Configuration contract

`configs/default.yaml` is the only supported configuration schema. The CLI
reads these fields:

| Section | Field | Required | Notes |
| --- | --- | --- | --- |
| `simulation` | `budget` | no | int, default `500`, minimum 25 |
| `simulation` | `seed` | yes | int, seeds `random.Random` |
| `paths` | `players` | yes | Excel workbook path |
| `paths` | `output` | no | report path or directory |
| `paths` | `checkpoint` | no | checkpoint path or directory |
| `paths` | `logs` | no | log directory, default `logs` |
| `buyers` | list | yes | non-empty; each entry has `id`, `name`, `strategy` (`deterministic`, `random` or `llm`, default `deterministic`), `priority` (default: list index), and `llm` (required mapping when `strategy: "llm"`) |
| `llm` | `base_url` | yes* | non-empty string, OpenAI-compatible endpoint |
| `llm` | `api_key_env` | yes* | environment variable name holding the API key; the key itself never appears in config files |
| `llm` | `model` | yes* | model name passed to the chat API |
| `llm` | `temperature` | no | number in `[0, 2]`, default `0.7` |
| `llm` | `timeout_seconds` | no | int > 0, default `30` |
| `llm` | `brave` | yes* | mapping with non-empty `base_url` and `api_key_env` (environment variable name holding the Brave key; a missing or placeholder value disables live search); a literal `api_key` field is rejected |
| `buyers[].llm` | `model`/`role`/`personality`/`system_prompt` | no | non-empty strings; per-buyer `model` overrides the global one |
| `buyers[].llm` | `temperature` | no | number in `[0, 2]`, overrides the global default |
| `buyers[].llm` | `max_tool_iterations` | no | int >= 1, default `3` |
| `buyers[].llm` | `tools` | no | non-empty subset of `{search_news, submit_bid}` containing `submit_bid`; default: both |
| `buyers[].llm` | `spending_profile` | no | mapping role → share in `[0, 1]`, keys ⊆ `{P, D, C, A}`, shares sum to 1 (± 0.01); used only by metrics (absent → uniform target) |
| `buyers[].llm` | `target_players` | no | list of non-empty strings |
| `logging` | `level` | no | default `INFO` |
| `logging` | `log_to_file` | no | default `false` |

*Required only when at least one buyer has `strategy: "llm"`.

Unknown sections and fields are ignored. Precedence:

- `--seed`, `--players`, `--output`, and `--checkpoint` override the
  corresponding YAML values;
- `--config` replaces `configs/default.yaml` entirely, without merging;
- with `--resume`, the checkpoint snapshots are authoritative: YAML, the
  Excel workbook, and `--seed` are ignored; when the checkpoint has `llm`
  buyers, the `checkpoint.llm.yaml` sidecar next to it supplies the LLM
  configuration and is required.

The legacy root `config.yaml` (pre-MVP, with `budget_iniziale`, `database`,
`checkpoints`, `llm`, and `auction` sections) has been removed.

## Input and output

The Excel adapter reads the `Tutti` sheet, whose real header is on the second
row, and validates these columns:

```text
Id, R, Nome, Squadra, Qt.A
```

`Qt.A` is stored as the player's informational list price. Player IDs must be
unique and roles must be one of `P`, `D`, `C`, or `A`.

Successful reports are version-1 `auction_report` documents containing
`schema_version`, lifecycle timestamps, cumulative `duration_seconds`, the
latest run's timestamps and duration, `run_number`, squads, transactions,
unsold players, aggregate player counts, and all accumulated `BidIssue`
diagnostics.

Pool-exhaustion checkpoints are version-1 `auction_checkpoint` documents.
They contain the same report fields plus the authoritative full player state,
`missing_roles`, `error_code: "pool_exhausted"`, the error text, and resume
metadata. They also embed the normalized bidder snapshots (`id`, `name`,
`strategy`, `priority`) and simulation snapshot (`budget`, `seed`), so the
original YAML and Excel files are not needed to resume.

`duration_seconds` is cumulative active auction time across rounds;
`last_run_started_at`, `last_run_ended_at`, and
`last_run_duration_seconds` describe only the latest invocation. A resumed
round re-auctions only players that were `UNSOLD` in the checkpoint and calls
only bidders whose squads are incomplete. Complete squads remain in the
report and state but receive no offers.

Use the CLI like this:

```bash
venv/bin/python main.py --resume /path/to/auction-checkpoint.json \
  --output /path/to/final-report.json
```

If the resumed pool is exhausted again, `--checkpoint PATH` selects the new
checkpoint destination; without it, the input checkpoint is replaced. Only
pool exhaustion creates a resumable checkpoint. Configuration errors, invalid
checkpoint data, file errors, and unexpected engine errors return failure
without writing one.

### Traces and the LLM sidecar

Every `llm` buyer writes one JSON object per event (context, llm_call, usage,
tool_call, tool_result, bid, no_bid, error, ...) to
`logs/traces/<run_dir>/<buyer_id>.jsonl`, flushed immediately. The `<run_dir>`
is chosen by the caller (`main.py` or `benchmark`), never by the engine; each
invocation uses a fresh timestamped directory.

On pool exhaustion with at least one `llm` buyer, the CLI writes
`<checkpoint>.llm.yaml` next to the checkpoint (`schema_version: 1`) with the
global `llm` block and per-buyer `llm` blocks (only where configured; the
`api_key_env` variable name, never the key itself). Resuming a checkpoint with
`llm` buyers requires a valid sidecar: missing or malformed sidecars exit `1`
before the auction. A second exhaustion propagates the sidecar next to the new
checkpoint. Checkpoints without `llm` buyers never write one.

### Benchmark output

The `benchmark` subcommand writes `DIR/run_NNN/report.json`,
`DIR/run_NNN/traces/<buyer_id>.jsonl`, `DIR/metrics.json` (run records plus
aggregates), and `DIR/metrics.csv` (one row per buyer per run), and prints a
console summary table. Run `i` uses seed `seed + i` (0-based) with a fresh
engine and deep-copied players; pool exhaustion inside a run saves the partial
report with `completed: false` and the benchmark continues.

## Project structure

```text
agents/
  base_agent.py       Bidder protocol
  buyer_agent.py      DeterministicBidder and RandomBidder
  trace.py            TraceLogger: per-agent JSONL events
  llm_agent.py        LlmClient (shared httpx) and AgentManager (Bidder)

benchmark/
  metrics.py          Pure metric functions over report JSON and trace JSONL
  runner.py           Benchmark subcommand: N runs + metric aggregation

core/
  models.py            Players, squads, bids, transactions, and reports
  auction_manager.py  Auction orchestration and auction outcomes

utils/
  config_loader.py    YAML config loading and contract validation
  excel_handler.py    Excel input validation and player loading
  json_store.py       JSON report/checkpoint persistence
  logger.py            Logging setup
  validator.py         Legacy validation facade

configs/
  default.yaml        Active default simulation configuration
  llm.yaml            Example LLM-driven auction configuration

data/
  *.xlsx              Player source workbook

main.py               CLI composition root
tests/                Domain, adapter, persistence, and CLI tests
docs/                 MVP design and implementation history
```

## Verification

Run the complete test suite with warnings treated as errors:

```bash
venv/bin/pytest -q -W error
```

The test suite covers roster invariants, strict bid validation, invalid and
raising bidders, tie and no-bid outcomes, deterministic and random bidder
behavior, canonical players, Excel schema handling, JSON persistence, pool
exhaustion, configuration contract validation, LLM configuration validation,
sidecar save/resume flows, trace logging, the LLM function-calling loop,
parallel bid collection, benchmark metrics, and CLI success/failure paths.