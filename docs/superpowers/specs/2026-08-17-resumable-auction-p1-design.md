# Resumable Auction Checkpoint Design

## Goal

Extend P1 from passive JSON persistence to a versioned, autonomous checkpoint
workflow. A checkpoint is a partial report created when the complete player
pool has been processed but one or more squads are still incomplete. A later
run can load that checkpoint, re-auction only the previously unsold players,
and involve only the incomplete squads.

This design resumes only at pool-exhaustion boundaries. It does not provide an
arbitrary mid-auction save, event-sourced replay, or exact random-generator
continuation.

## Confirmed decisions

- A resumed run is started with `--resume CHECKPOINT`.
- The checkpoint is autonomous. It embeds the budget, seed, and normalized
  bidder definitions, so `--config` and `--players` are not required to resume.
- Randomness uses the stored seed with a new simple generator for each resumed
  run. The exact internal state of `random.Random` is not persisted.
- Both lifecycle and per-run timing are retained:
  - lifecycle timestamps and cumulative active duration;
  - the latest run's timestamps and duration.
- `BidIssue` entries are accumulated in both reports and checkpoints.
- The checkpoint is emitted only for pool exhaustion before all squads are
  complete. Configuration errors and unexpected internal errors do not create
  resumable checkpoints.

## Auction lifecycle

### Fresh run

1. Load the Excel player pool and the configured bidders.
2. Auction each selected available player once.
3. Mark a positive unique winner as `SOLD`; mark no-bid and tied-highest
   outcomes as `UNSOLD`.
4. If every squad is complete, write the final report.
5. If no available player remains while squads are incomplete, write a
   checkpoint with the complete state, report aggregates, diagnostics, and
   embedded resume configuration.

### Resumed run

1. Load and validate a version-1 `auction_checkpoint`.
2. Keep all `SOLD` players, squads, budgets, transactions, and diagnostics.
3. Convert the checkpoint's `UNSOLD` players into the next run's available
   pool. No player outside that set may be selected.
4. Activate only bidders whose squads are incomplete. Completed squads remain
   in the state and in the output but receive no bid calls.
5. Increment the run number and continue the transaction and diagnostic
   history.
6. If all squads become complete, write one final report containing the
   accumulated state.
7. If the new pool is exhausted while squads remain incomplete, write a new
   checkpoint containing the updated state and only the currently unsold pool.

A checkpoint with no unsold players cannot make progress. Loading such a file
as a resume input must fail with a clear error rather than starting an
infinite sequence of identical runs.

## Versioned document contract

All persisted documents have a numeric `schema_version` and a
`document_type`. Version 1 uses these document types:

- `auction_report` for a completed auction;
- `auction_checkpoint` for a resumable partial auction.

Common report fields:

```json
{
  "schema_version": 1,
  "document_type": "auction_report",
  "timestamp_start": "2026-08-17T18:00:00+00:00",
  "timestamp_end": "2026-08-17T18:00:01+00:00",
  "duration_seconds": 1.0,
  "last_run_started_at": "2026-08-17T18:00:00+00:00",
  "last_run_ended_at": "2026-08-17T18:00:01+00:00",
  "last_run_duration_seconds": 1.0,
  "run_number": 1,
  "squads": {},
  "transactions": [],
  "unsold_players": [],
  "total_players": 0,
  "players_sold": 0,
  "players_unsold": 0,
  "bid_issues": []
}
```

Field semantics:

- `timestamp_start` is the start of the first run in the lifecycle.
- `timestamp_end` is the end of the most recent run.
- `duration_seconds` is cumulative active auction time; time between runs is
  not counted.
- `last_run_*` fields describe only the most recent invocation.
- `run_number` starts at `1` and increments for every resume.
- Counts describe the current player statuses, not the number of attempts.
- `bid_issues` contains all isolated bidder failures from all runs.
- Datetimes are serialized as ISO-8601 strings with UTC offsets.

A version-1 checkpoint contains all common report fields plus:

```json
{
  "document_type": "auction_checkpoint",
  "players": [],
  "simulation": {
    "budget": 500,
    "seed": 42
  },
  "buyers": [
    {
      "id": "buyer_1",
      "name": "Squadra Alfa",
      "strategy": "deterministic",
      "priority": 0
    }
  ],
  "auction_count": 137,
  "missing_roles": {},
  "error_code": "pool_exhausted",
  "error": "Player pool exhausted before roster completion",
  "resume": {
    "incomplete_buyer_ids": ["buyer_1"],
    "pool": "unsold_players"
  }
}
```

`players` is the authoritative full state needed to reconstruct the next run.
The report-shaped `squads`, `transactions`, `unsold_players`, and aggregate
fields are retained so the checkpoint can be inspected as a partial report
without loading it. `buyers` is a normalized snapshot of the bidder
configuration; `priority` is persisted for every entry so deterministic
bidder behavior can be rebuilt without the original YAML. `auction_count` is
cumulative and keeps diagnostic auction numbers meaningful across resumes.

Existing unversioned checkpoint files are legacy diagnostic JSON and are not
accepted by `--resume`: they do not contain enough information to guarantee an
autonomous continuation. New version-1 files are the only files produced by
the P1 workflow.

## CLI behavior

- Fresh runs retain the existing `--config`, `--players`, `--output`,
  `--checkpoint`, and `--seed` behavior.
- `--resume PATH` reads an autonomous checkpoint. The checkpoint's embedded
  players, simulation settings, and bidder definitions are authoritative.
- `--output PATH` remains the final report destination.
- `--checkpoint PATH` overrides the output destination for a subsequent
  checkpoint. When omitted during resume, the input checkpoint path is reused
  for the updated checkpoint.
- Fresh-run-only inputs are not needed for resume and must not override the
  embedded checkpoint configuration.

## Error handling

Only `AuctionIncompleteError` caused by an exhausted player pool produces a
checkpoint. The checkpoint carries `error_code: "pool_exhausted"` and the
missing roles. Invalid configuration, invalid checkpoint data, file errors,
and unexpected engine errors return failure without writing a resumable
checkpoint.

## Acceptance criteria

- A complete fresh auction produces a version-1 report.
- An incomplete fresh auction produces a version-1 autonomous checkpoint.
- A resume uses only the checkpoint's unsold players and incomplete squads.
- Completed squads never receive bid calls during a resumed run.
- Transactions, budgets, statuses, missing roles, and `BidIssue` entries carry
  across every round.
- A resumed completion produces a final report with cumulative and last-run
  timing.
- A second incomplete round produces another valid checkpoint.
- Invalid or legacy checkpoints fail clearly.
- The full existing test suite remains green with warnings treated as errors.
