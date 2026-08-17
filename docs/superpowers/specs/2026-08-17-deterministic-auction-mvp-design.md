# Deterministic Fantasy Auction MVP Design

## Goal

Build the first non-interactive MVP of `fantasyleague-ai-auction`: load players from the supplied Excel workbook, run a reproducible deterministic auction, log progress to the console, and write a JSON report or failure checkpoint.

## Scope

Included:

- Excel input from the `Tutti` sheet, whose real header is on row 2 (`header=1`).
- Four roster roles with exact limits: `P=3`, `D=8`, `C=8`, `A=6`.
- One unique purchase per player.
- Initial budget of 500 credits per manager by default.
- One-credit reserve for every slot still empty.
- Deterministic and seeded random bidders.
- Zero bids and tied highest positive bids both produce a permanently unsold player in the first round.
- Explicit failure when the player pool ends before all rosters are complete, with missing roles and a JSON checkpoint.
- Console logging and JSON report output.

Deferred:

- LLM clients and prompts.
- A second pass over unsold players.
- Full event sourcing, replay, and asynchronous bidding.
- Real-team limits and quotation-based starting prices.

## Domain model

`core.models` owns the rules and state:

- `Position`: `P`, `D`, `C`, `A`.
- `Player`: immutable source identity plus mutable auction status, owner, and selling price. `list_price` is informational only.
- `Squad`: manager identity, budget, and purchased players. It calculates role counts, missing roles, remaining slots, and the maximum legal bid (`budget - remaining_slots + 1`).
- `AuctionResult`: player, all bids, winner, price, and one of sold/no-bid/tie statuses.
- `Transaction`: immutable purchase record.
- `AuctionState`: all players, squads, and transactions; serializable for checkpoints.
- `SimulationReport`: final statistics and squad/transaction data for successful runs.

`Squad.add_player` is the single mutation point for a purchase and validates availability, role capacity, budget, and the reserve rule.

## Bidder boundary

`agents.base_agent.Bidder` is a small protocol:

```python
bidder_id: str
name: str

def bid(self, player: Player, squad: Squad) -> int: ...
```

The engine passes current squad state to the bidder. `DeterministicBidder` uses a stable priority-dependent positive bid when the role is needed. `RandomBidder` uses an injected `random.Random` instance and chooses reproducibly between zero and the current legal maximum. Neither bidder knows how to mutate auction state.

This boundary is sufficient for a future LLM bidder without coupling LLM code to auction rules.

## Auction flow

`core.auction_manager.AuctionEngine` owns orchestration and uses a private seeded RNG for player selection:

1. Create one squad per bidder and validate unique IDs and initial budget.
2. While any squad is incomplete, select one available player without replacement.
3. Ask every bidder for a bid; an ineligible squad contributes zero.
4. Validate each bid against that squad's current legal maximum.
5. All zero bids produce `UNSOLD_NO_BID`; a tied positive maximum produces `UNSOLD_TIE`; both remove the player permanently.
6. A unique maximum transfers the player to the winner and records a transaction.
7. When every squad is complete, return a report.
8. If no available player remains first, raise `AuctionIncompleteError` carrying missing roles. The CLI serializes the current `AuctionState` before returning a non-zero exit code.

No tie-breaker round is implemented in this MVP, matching the confirmed rule that ties are immediately unsold. No real-team constraint is checked.

## Adapters

- `utils.excel_handler.ExcelHandler` reads and validates the workbook, normalizes the title-row offset, and returns domain `Player` objects. It does not mutate the source workbook.
- `utils.json_store.JsonStore` writes JSON reports and checkpoints. Checkpoints include the error message and missing roles when applicable.
- `main.py` is a thin argparse/YAML composition root: load config, create bidders, run the engine, save the report/checkpoint, and set the process exit code.

## Configuration

`configs/default.yaml` defines the default workbook path, output paths, seed, budget, and four deterministic bidders. Each bidder can select `deterministic` or `random` strategy. The Excel quotation is never used as a starting bid.

## Testing

Tests cover:

- Exact role quotas and reserve-aware maximum bids.
- Invalid roster additions and duplicate purchases.
- No-bid and tie outcomes, including permanent removal.
- Seeded player selection and random bidder reproducibility.
- Pool exhaustion with role-specific missing-slot reporting and checkpoint serialization.
- Excel header offset, 525-player load, role counts, and duplicate-ID validation.
- JSON report/checkpoint shape.
- A small CLI integration run using a temporary configuration and fixture players.

The full Excel workbook is used only for the adapter smoke test; engine tests use small in-memory players so failures stay fast and explainable.

## Future event compatibility

The engine will expose serializable state and result boundaries, but it will not introduce an event bus or event-sourcing storage now. A later `EventRecorder` can observe auction results and state transitions without moving budget and roster rules out of the domain core.
