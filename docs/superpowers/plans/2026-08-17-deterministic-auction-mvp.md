# Deterministic Auction MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the incompatible skeleton with a deterministic, testable auction MVP that reads the supplied Excel workbook and produces a JSON report or failure checkpoint.

**Architecture:** Keep auction rules in `core.models` and `core.auction_manager`, expose a small `Bidder` protocol in `agents`, and keep Excel, JSON, YAML, and CLI concerns in adapters/composition code. Do not add LLM code, event sourcing, a second unsold round, or real-team limits in this MVP.

**Tech Stack:** Python 3, Pydantic 2, pandas/openpyxl, PyYAML, loguru, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-deterministic-auction-mvp-design.md`

## Global Constraints

- The default initial budget is exactly 500 credits per manager.
- The required roster is exactly `P=3`, `D=8`, `C=8`, `A=6` (25 players).
- A legal positive bid is an integer starting at 1; Excel `Qt.A` is informational and never sets the opening bid.
- Every still-empty roster slot reserves 1 credit, so before a purchase the maximum legal bid is `budget_remaining - remaining_slots + 1`.
- A player can be sold at most once; unsold players are removed permanently in the first pass.
- All-zero bids and tied highest positive bids produce an unsold result immediately.
- Exhausting the pool before completion raises an explicit error, reports missing roles, and saves the current state as JSON.
- No real-team player limit, LLM client, tie-breaker, second unsold pass, or event-sourcing layer is in scope.
- New behavior is developed test-first: each production behavior has a failing test run before its implementation.

---

### Task 1: Replace domain models with coherent roster and auction contracts

**Files:**
- Modify: `core/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces `Position`, `ROSTER_REQUIREMENTS`, `PlayerStatus`, `Player`, `Squad`, `AuctionStatus`, `AuctionResult`, `Transaction`, `AuctionState`, and `SimulationReport` for later tasks.
- `Squad.max_bid_allowed` is a property; `Squad.missing_roles()` returns a `dict[str, int]`; `Squad.add_player(player, price)` mutates only after all validation passes.

- [ ] **Step 1: Write failing tests for role quotas and reserve-aware bids**

```python
from core.models import Player, Position, Squad


def player(player_id: str, position: Position) -> Player:
    return Player(id=player_id, name=player_id, position=position, team="Team", list_price=10)


def test_squad_reports_role_slots_and_reserve_aware_maximum_bid():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=30)
    assert squad.remaining_slots == 25
    assert squad.max_bid_allowed == 6
    assert squad.missing_roles() == {"P": 3, "D": 8, "C": 8, "A": 6}

    squad.add_player(player("p1", Position.P), 6)
    assert squad.budget_remaining == 24
    assert squad.missing_roles()["P"] == 2
    assert squad.max_bid_allowed == 1


def test_squad_rejects_overfilled_role_and_reserve_breaking_price():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=25)
    for index in range(3):
        squad.add_player(player(f"p{index}", Position.P), 1)
    try:
        squad.add_player(player("p3", Position.P), 1)
    except ValueError as exc:
        assert "P" in str(exc)
    else:
        raise AssertionError("expected role-capacity validation")
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the new contracts are absent**

Run: `venv/bin/pytest -q tests/test_models.py`

Expected: collection or assertion failure referencing the missing/new `Squad` contract, not a passing test.

- [ ] **Step 3: Implement the minimal domain models**

Implement Pydantic 2 models with English field names, enum values suitable for JSON, exact role quotas, and these validations:

```python
ROSTER_REQUIREMENTS = {
    Position.P: 3,
    Position.D: 8,
    Position.C: 8,
    Position.A: 6,
}

@property
def max_bid_allowed(self) -> int:
    return max(0, self.budget_remaining - self.remaining_slots + 1)
```

`Squad.add_player` must reject unavailable players, duplicate player IDs, a full role, a non-positive price, or a price above `max_bid_allowed`; then set the player to sold, set owner/price, append a deep copy, and decrement the budget. `AuctionState` must expose `available_players`, and all report models must support `model_dump(mode="json")`.

- [ ] **Step 4: Run focused tests and then the full suite**

Run: `venv/bin/pytest -q tests/test_models.py`

Expected: PASS.

Run: `venv/bin/pytest -q`

Expected: the model tests pass; later feature tests may still fail until their tasks are implemented.

- [ ] **Step 5: Commit the model slice**

```bash
git add core/models.py tests/test_models.py
git commit -m "feat: define auction domain models"
```

---

### Task 2: Add deterministic and seeded-random bidder implementations

**Files:**
- Modify: `agents/base_agent.py`
- Modify: `agents/buyer_agent.py`
- Create: `tests/test_bidders.py`

**Interfaces:**
- Consumes: `Player`, `Position`, and `Squad` from `core.models`.
- Produces `Bidder` protocol, `DeterministicBidder(buyer_id, name, priority=0)`, and `RandomBidder(buyer_id, name, rng)`.
- Both classes expose `buyer_id`, `name`, and `bid(player, squad) -> int`.

- [ ] **Step 1: Write failing tests for eligibility, deterministic output, and seeded reproducibility**

```python
import random
from agents.buyer_agent import DeterministicBidder, RandomBidder
from core.models import Player, Position, Squad


def test_deterministic_bidder_bids_zero_when_role_is_full():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=100)
    for index in range(3):
        squad.add_player(Player(id=f"p{index}", name="P", position=Position.P, team="T", list_price=1), 1)
    bidder = DeterministicBidder("b1", "Alpha", priority=2)
    assert bidder.bid(Player(id="new", name="P", position=Position.P, team="T", list_price=1), squad) == 0


def test_deterministic_bidder_uses_priority_not_excel_quotation_as_starting_price():
    squad = Squad(buyer_id="b1", name="Alpha", budget_initial=500)
    bidder = DeterministicBidder("b1", "Alpha", priority=2)
    assert bidder.bid(Player(id="a", name="A", position=Position.A, team="T", list_price=99), squad) == 3


def test_random_bidder_is_reproducible_with_equal_seeds():
    player = Player(id="a", name="A", position=Position.A, team="T", list_price=99)
    first = RandomBidder("b1", "Alpha", random.Random(42)).bid(player, Squad(buyer_id="b1", name="Alpha", budget_initial=500))
    second = RandomBidder("b1", "Alpha", random.Random(42)).bid(player, Squad(buyer_id="b1", name="Alpha", budget_initial=500))
    assert first == second
```

- [ ] **Step 2: Run the focused tests and confirm the expected import/API failure**

Run: `venv/bin/pytest -q tests/test_bidders.py`

Expected: FAIL because the new bidder classes/protocol are not implemented.

- [ ] **Step 3: Implement the smallest bidder boundary**

Use a `Protocol` in `agents/base_agent.py`. `DeterministicBidder` returns zero for an unavailable/full role, otherwise `min(squad.max_bid_allowed, max(1, priority + 1))`. `RandomBidder` returns zero when the role is unavailable or the squad has no legal positive bid, otherwise `rng.randint(1, squad.max_bid_allowed)`. Do not mutate the player or squad.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `venv/bin/pytest -q tests/test_bidders.py`

Expected: PASS.

- [ ] **Step 5: Commit the bidder slice**

```bash
git add agents/base_agent.py agents/buyer_agent.py tests/test_bidders.py
git commit -m "feat: add deterministic auction bidders"
```

---

### Task 3: Replace the Excel adapter and handle the title row

**Files:**
- Modify: `utils/excel_handler.py`
- Create: `tests/test_excel_handler.py`

**Interfaces:**
- Produces `ExcelHandler(filepath).load_players(validate=True) -> list[Player]`.
- `validate_schema(dataframe) -> None` raises `ValueError` with a useful message for missing columns, invalid roles, duplicate IDs, or empty required values.

- [ ] **Step 1: Write failing adapter tests**

```python
from pathlib import Path
import pandas as pd
import pytest
from core.models import Position
from utils.excel_handler import ExcelHandler

DATA_FILE = Path("data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx")


def test_load_players_skips_title_row_and_reads_all_525_players():
    players = ExcelHandler(DATA_FILE).load_players()
    assert len(players) == 525
    assert {position: sum(p.position.value == position for p in players) for position in "PDCA"} == {
        "P": 65, "D": 184, "C": 178, "A": 98,
    }
    assert len({p.id for p in players}) == 525


def test_validate_schema_rejects_duplicate_ids():
    frame = pd.DataFrame({"Id": [1, 1], "R": ["P", "P"], "Nome": ["A", "B"], "Squadra": ["T", "T"], "Qt.A": [1, 1]})
    with pytest.raises(ValueError, match="duplic"):
        ExcelHandler("unused.xlsx").validate_schema(frame)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_excel_handler.py`

Expected: FAIL because the current adapter reads the first title row and uses incompatible lowercase/Italian field names.

- [ ] **Step 3: Implement the adapter**

Read `sheet_name="Tutti", header=1`, drop rows missing `Id`, `R`, `Nome`, `Squadra`, or `Qt.A`, and map those columns to the domain model. Normalize numeric IDs without a `.0` suffix. Validate exactly the required source columns, roles in `P/D/C/A`, and unique IDs. Leave `Qt.A` in `Player.list_price` only as informational data. Do not add auction state columns or write back to the source workbook.

- [ ] **Step 4: Run the adapter tests and full suite**

Run: `venv/bin/pytest -q tests/test_excel_handler.py`

Expected: PASS.

Run: `venv/bin/pytest -q`

Expected: model, bidder, and Excel tests pass.

- [ ] **Step 5: Commit the adapter slice**

```bash
git add utils/excel_handler.py tests/test_excel_handler.py
git commit -m "fix: load players from the real Excel header"
```

---

### Task 4: Implement the auction engine and explicit incomplete-auction failure

**Files:**
- Modify: `core/auction_manager.py`
- Create: `tests/test_auction_manager.py`

**Interfaces:**
- Consumes: `Player`, `AuctionState`, `AuctionResult`, `Transaction`, `Squad`, and `Bidder`.
- Produces `AuctionEngine(players, bidders, budget=500, seed=None)`, `AuctionIncompleteError`, and `AuctionEngine.run() -> SimulationReport`.
- `AuctionEngine.state` remains available after success or failure; `AuctionIncompleteError.missing_roles` is a `dict[str, dict[str, int]]`.

- [ ] **Step 1: Write failing tests for the core rules**

```python
import pytest
from agents.buyer_agent import DeterministicBidder
from core.auction_manager import AuctionEngine, AuctionIncompleteError
from core.models import Player, Position, AuctionStatus


def make_player(i, role):
    return Player(id=str(i), name=str(i), position=Position(role), team="T", list_price=50)


def test_tied_highest_bid_makes_player_unsold_and_removes_it():
    players = [make_player("a", "A")]
    bidders = [DeterministicBidder("b1", "One", priority=1), DeterministicBidder("b2", "Two", priority=1)]
    engine = AuctionEngine(players, bidders, budget=25, seed=1)
    result = engine.auction_player(players[0])
    assert result.status is AuctionStatus.UNSOLD_TIE
    assert players[0].status.value == "invenduto"
    assert engine.state.available_players == []


def test_unique_positive_bid_records_one_purchase():
    players = [make_player("a", "A")]
    bidders = [DeterministicBidder("b1", "One", priority=1), DeterministicBidder("b2", "Two", priority=0)]
    engine = AuctionEngine(players, bidders, budget=30, seed=1)
    result = engine.auction_player(players[0])
    assert result.status is AuctionStatus.SOLD
    assert result.winner_id == "b1"
    assert result.price == 2
    assert len(engine.state.transactions) == 1


def test_pool_exhaustion_reports_missing_roles():
    players = [make_player("a", "A")]
    bidders = [
        DeterministicBidder("b1", "One", priority=1),
        DeterministicBidder("b2", "Two", priority=0),
    ]
    engine = AuctionEngine(players, bidders, budget=500, seed=1)
    with pytest.raises(AuctionIncompleteError) as caught:
        engine.run()
    assert caught.value.missing_roles["b1"]["P"] == 3
    assert engine.state.players[0].status.value == "venduto"
```

Add tests for all-zero bids and for a squad being excluded from bids when its role quota is already full. Use a tiny custom bidder in the test file for the all-zero case rather than mocking engine internals.

- [ ] **Step 2: Run the focused tests and verify failure before implementation**

Run: `venv/bin/pytest -q tests/test_auction_manager.py`

Expected: FAIL because `AuctionEngine` and the new result semantics are absent.

- [ ] **Step 3: Implement the engine minimally**

Use `random.Random(seed)` rather than global random state. Validate non-empty bidders, unique bidder IDs, unique player IDs, and `budget >= 25`. Build one `Squad` per bidder. `auction_player` collects a bid for every bidder ID, uses zero for a completed squad or full role, rejects negative or over-maximum bids with a clear `ValueError`, and applies exactly one outcome:

```python
if max_bid == 0:
    status = AuctionStatus.UNSOLD_NO_BID
elif sum(bid == max_bid for bid in bids.values()) > 1:
    status = AuctionStatus.UNSOLD_TIE
else:
    status = AuctionStatus.SOLD
```

For sold players call `Squad.add_player` and append a transaction; for unsold players set `PlayerStatus.UNSOLD`. `run` draws from the remaining available pool until all squads are complete or raises `AuctionIncompleteError` with every squad's missing roles. Do not implement a tie-breaker loop or a second unsold pass.

- [ ] **Step 4: Run focused and full tests**

Run: `venv/bin/pytest -q tests/test_auction_manager.py`

Expected: PASS.

Run: `venv/bin/pytest -q`

Expected: all current tests pass.

- [ ] **Step 5: Commit the engine slice**

```bash
git add core/auction_manager.py tests/test_auction_manager.py
git commit -m "feat: implement deterministic auction engine"
```

---

### Task 5: Add JSON persistence, thin CLI, and default configuration

**Files:**
- Create: `utils/json_store.py`
- Modify: `main.py`
- Modify: `configs/default.yaml`
- Create: `tests/test_json_store.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- `JsonStore.save_report(report, path) -> Path` writes UTF-8 JSON.
- `JsonStore.save_checkpoint(state, path, error=None) -> Path` writes players, squads, transactions, missing roles, and error text when supplied.
- `main.main(argv=None) -> int` returns `0` on success and `1` on incomplete auction/config failure.

- [ ] **Step 1: Write failing persistence and CLI tests**

```python
import json
from pathlib import Path
from utils.json_store import JsonStore


def test_checkpoint_contains_error_and_serialized_state(tmp_path):
    state = {"players": [], "squads": {"b1": {"missing_roles": {"P": 3}}}, "transactions": []}
    path = JsonStore().save_checkpoint(state, tmp_path / "checkpoint.json", error="pool exhausted")
    data = json.loads(path.read_text())
    assert data["error"] == "pool exhausted"
    assert data["squads"]["b1"]["missing_roles"]["P"] == 3
```

The CLI integration test should create a temporary YAML config pointing to a four-player fixture or a generated Excel fixture, invoke `main([...])`, assert exit code `0`, and inspect the report JSON. A second invocation with too few players must return `1` and create a checkpoint containing `missing_roles`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `venv/bin/pytest -q tests/test_json_store.py tests/test_cli.py`

Expected: FAIL because the JSON adapter and CLI composition are not implemented.

- [ ] **Step 3: Implement JSON serialization**

`JsonStore` accepts either Pydantic models or plain dictionaries, creates parent directories, and writes `ensure_ascii=False, indent=2`. Reports must include statistics, squads, transactions, unsold players, and timestamps. Checkpoints must include the serializable state plus `error` and `missing_roles` when provided.

- [ ] **Step 4: Implement the CLI and config**

Use `argparse` options `--config`, `--players`, `--output`, `--checkpoint`, and `--seed`. Load YAML, defaulting to `configs/default.yaml`; create deterministic or random bidders in config order. For random bidders derive per-bidder RNGs from the configured seed so equal config/seed runs are reproducible. Set up console/file logging without requiring an LLM environment variable. On success save the report and return `0`; on `AuctionIncompleteError` save a checkpoint, log the explicit missing-role error, and return `1`.

Update `configs/default.yaml` to use the actual workbook path and `data/results/report.json`, default budget `500`, seed `42`, and four deterministic bidders.

- [ ] **Step 5: Run focused and full tests**

Run: `venv/bin/pytest -q tests/test_json_store.py tests/test_cli.py`

Expected: PASS.

Run: `venv/bin/pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the CLI slice**

```bash
git add utils/json_store.py main.py configs/default.yaml tests/test_json_store.py tests/test_cli.py
git commit -m "feat: add JSON reports and auction CLI"
```

---

### Task 6: End-to-end verification against the supplied workbook

**Files:**
- Modify only if a test exposes a defect in the preceding tasks.

- [ ] **Step 1: Run the complete test suite with warnings visible**

Run: `venv/bin/pytest -q -W error`

Expected: all tests pass without warnings.

- [ ] **Step 2: Run the default simulation**

Run: `venv/bin/python main.py --config configs/default.yaml --output /tmp/fantasyleague-report.json --checkpoint /tmp/fantasyleague-checkpoint.json`

Expected: exit code `0`, console logs showing the auction, and a JSON report containing four complete squads with 25 players each. No checkpoint is required on success.

- [ ] **Step 3: Verify reproducibility**

Run the default command twice with two different output paths and compare the normalized JSON fields excluding timestamps/duration. Expected: identical player ownership, prices, bids, and transaction order.

- [ ] **Step 4: Verify explicit failure and checkpoint**

Run a temporary config with a pool smaller than the required 100 purchases. Expected: exit code `1`, checkpoint exists, and its `error` plus per-manager `missing_roles` identify the incomplete roles.

- [ ] **Step 5: Review the final diff and status**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Confirm that existing user modifications were intentionally replaced as requested, generated data/log files remain ignored, and no LLM/event-sourcing code was added.
