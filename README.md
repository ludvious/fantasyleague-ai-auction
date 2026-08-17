# fantasyleague-ai-auction

A non-interactive CLI for simulating an Italian fantasy-football auction.
The MVP is synchronous, deterministic, and reproducible through seeded
`random.Random` instances. Auction rules live in the domain, while bidder
strategies, Excel input, JSON persistence, and the CLI remain separate
adapters.

## Current status

The deterministic auction MVP is implemented and P0 is complete:

- strict bid validation is centralized in `Squad`;
- invalid bidder output and bidder exceptions are isolated and recorded as
  structured diagnostics;
- canonical player resolution prevents external player copies from mutating
  auction state;
- the CLI reads the supplied Excel workbook and writes a JSON report or a
  diagnostic failure checkpoint.

Latest verification:

- `venv/bin/pytest -q -W error`: **54 tests passed**;
- real-workbook simulation: **100 players sold**, **37 unsold**, and **4
  complete squads** of 25 players.

The report and checkpoint formats are currently functional but not yet
versioned. Compatibility guarantees and a complete resume workflow are part
of later milestones.

## MVP rules

Each squad contains exactly 25 players:

| Role | Required players |
| --- | ---: |
| `P` (goalkeepers) | 3 |
| `D` (defenders) | 8 |
| `C` (midfielders) | 8 |
| `A` (forwards) | 6 |

Other rules:

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

## Quick start

Create an environment and install the dependencies:

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
```

Run the default simulation:

```bash
venv/bin/python main.py --config configs/default.yaml
```

The default configuration uses:

- `data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx` as the player source;
- a budget of 500 credits;
- seed `42`;
- four deterministic bidders;
- `data/results/report.json` for successful reports;
- `data/checkpoints/checkpoint.json` for diagnostic failure checkpoints.

The output directories are created automatically when needed.

## CLI options

```text
--config PATH       YAML configuration file (default: configs/default.yaml)
--players PATH      Override the configured Excel workbook
--output PATH       Override the report path or output directory
--checkpoint PATH   Override the diagnostic checkpoint path or directory
--seed INTEGER      Override the configured random seed
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

The process returns `0` after a complete auction and `1` when configuration
or auction execution fails. If the player pool is exhausted before every
squad is complete, the checkpoint contains the current state, the error, and
missing roles. It is diagnostic only and cannot currently resume an auction.

## Configuration

`configs/default.yaml` is the active default configuration. Bidders support
the `deterministic` and `random` strategies:

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

## Input and output

The Excel adapter reads the `Tutti` sheet, whose real header is on the second
row, and validates these columns:

```text
Id, R, Nome, Squadra, Qt.A
```

`Qt.A` is stored as the player's informational list price. Player IDs must be
unique and roles must be one of `P`, `D`, `C`, or `A`.

Successful reports currently contain timestamps, duration, squads,
transactions, unsold players, and aggregate player counts. Failure
checkpoints contain the serialized auction state plus error and missing-role
information when available. The inclusion and versioning of `BidIssue`
diagnostics in a future report/checkpoint contract is still to be decided.

## Project structure

```text
agents/
  base_agent.py       Bidder protocol
  buyer_agent.py      DeterministicBidder and RandomBidder

core/
  models.py            Players, squads, bids, transactions, and reports
  auction_manager.py  Auction orchestration and auction outcomes

utils/
  excel_handler.py    Excel input validation and player loading
  json_store.py       JSON report/checkpoint persistence
  logger.py            Logging setup
  validator.py         Legacy validation facade

configs/
  default.yaml        Active default simulation configuration

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
exhaustion, and CLI success/failure paths.

## Roadmap

### P1 — Reports and checkpoints

- define and version the JSON report contract;
- define and version the checkpoint format;
- decide whether `BidIssue` belongs in reports and/or checkpoints;
- add serialization and compatibility tests;
- keep the MVP checkpoint diagnostic and non-restorable unless explicitly
  changed by a new decision.

### P2 — CLI and legacy compatibility

- stabilize the CLI contract, configuration, options, and error messages;
- preserve required legacy imports and APIs;
- extend CLI tests without moving CLI concerns into the domain.

## Explicitly out of scope for now

The following must not be introduced without an explicit architectural
decision:

- LLM integration, prompts, or external model providers;
- a complete resume or replay system;
- event sourcing or asynchronous bidding;
- a second auction pass for unsold players;
- tie-breaker rounds;
- real-team player limits;
- quotation-based starting prices;
- unversioned changes to the JSON report or checkpoint formats.

The earlier MVP design and implementation plan are available under
`docs/superpowers/specs/` and `docs/superpowers/plans/`.
