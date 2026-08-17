# Resumable Auction Checkpoint P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add version-1 reports and autonomous pool-exhaustion checkpoints that can resume subsequent auction rounds using only previously unsold players and incomplete squads.

**Architecture:** Keep auction rules in `core/auction_manager.py` and represent persisted documents with typed Pydantic models in `core/models.py`. `JsonStore` validates and serializes versioned reports/checkpoints, while `main.py` composes fresh and resume runs without moving CLI configuration into the domain. A checkpoint embeds the normalized simulation and bidder configuration, so resume does not require the original Excel file or YAML file.

**Tech Stack:** Python 3, Pydantic, `random.Random`, JSON, PyYAML, pytest, existing CLI and domain models.

**Spec:** `docs/superpowers/specs/2026-08-17-resumable-auction-p1-design.md`

## Global Constraints

- Resume only from pool-exhaustion checkpoints between auction rounds; do not implement arbitrary mid-auction save, event sourcing, or exact RNG-state replay.
- A checkpoint is emitted only for pool exhaustion with incomplete squads; configuration and unexpected internal errors do not create resumable checkpoints.
- Resume uses the checkpoint's embedded players, budget, seed, and bidder definitions as authoritative data.
- A resumed round selects only players that were `UNSOLD` in the loaded checkpoint and activates only incomplete squads.
- Preserve all sold players, squad budgets, squads, transactions, cumulative `BidIssue` entries, and report aggregates across rounds.
- Serialize datetimes as ISO-8601 strings with UTC offsets.
- Keep the existing dependency set and run `venv/bin/pytest -q -W error` after every completed task.

---

### Task 1: Add the versioned report and checkpoint models

**Files:**
- Modify: `core/models.py`
- Test: `tests/test_models.py`
- Create: `tests/test_contracts.py`
- Create: `tests/checkpoint_fixtures.py`

**Interfaces:**
- `SimulationReport` exposes version/document metadata, cumulative and
  last-run timing, and `bid_issues`.
- `AuctionState` persists round number, cumulative auction count, cumulative
  and last-run timing, and accumulated `bid_issues`.
- `AuctionCheckpoint` contains the report fields plus the full `players`
  state, embedded simulation settings, normalized bidder definitions,
  `missing_roles`, `error_code`, `error`, and resume metadata.
- `BidderSnapshot` and `SimulationSnapshot` validate the autonomous
  configuration embedded in a checkpoint.

- [ ] **Step 1: Write failing model-contract tests**

Add tests that construct a report and checkpoint and assert:

```python
def test_report_has_version_and_both_timing_scopes():
    report = make_report()

    assert report.schema_version == 1
    assert report.document_type == "auction_report"
    assert report.duration_seconds >= report.last_run_duration_seconds


def test_checkpoint_contains_autonomous_configuration():
    checkpoint = make_checkpoint()

    assert checkpoint.document_type == "auction_checkpoint"
    assert checkpoint.simulation.budget == 500
    assert checkpoint.buyers[0].strategy == "deterministic"
    assert checkpoint.error_code == "pool_exhausted"
```

Define the helpers in `tests/checkpoint_fixtures.py` as plain functions; do
not add a fixture framework. `make_report()` constructs a report with UTC
timestamps, empty squads/transactions/unsold players, zero counts, and
explicit zero-duration values. `make_checkpoint()` constructs one unsold
`Player(id="u1", name="Unsold", position=Position.P, team="Team",
list_price=1, status=PlayerStatus.UNSOLD)`, one incomplete `Squad`, one empty
transaction list, one `BidderSnapshot(id="buyer_1", name="Alpha",
strategy="deterministic", priority=0)`, `SimulationSnapshot(budget=500,
seed=42)`, `missing_roles={"buyer_1": {"P": 1}}`,
`error_code="pool_exhausted"`, and
`resume={"incomplete_buyer_ids": ["buyer_1"], "pool": "unsold_players"}`.
Fill every required report field explicitly. Import these helpers from the
other persistence and CLI tests when needed.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
venv/bin/pytest -q tests/test_models.py tests/test_contracts.py
```

Expected: FAIL because the version, timing, checkpoint, and configuration
models do not exist yet.

- [ ] **Step 3: Implement the minimal typed contract**

In `core/models.py`:

1. Add `BidderSnapshot` with `id`, `name`, `strategy`, and non-negative
   `priority`.
2. Add `SimulationSnapshot` with `budget` and nullable `seed`.
3. Extend `AuctionState` with:
   - `run_number: int = 1`;
   - `auction_count: int = 0`;
   - `total_duration_seconds: float = 0.0`;
   - `last_run_started_at`, `last_run_ended_at`;
   - `last_run_duration_seconds: float = 0.0`;
   - `bid_issues: list[BidIssue]`.
4. Extend `SimulationReport` with:
   - `schema_version: int = 1`;
   - `document_type: str = "auction_report"`;
   - `run_number`;
   - `last_run_started_at`, `last_run_ended_at`;
   - `last_run_duration_seconds`;
   - `bid_issues`.
5. Add `AuctionCheckpoint` using the same report-shaped fields plus:
   - `document_type = "auction_checkpoint"`;
   - full `players`;
   - `simulation: SimulationSnapshot`;
   - `buyers: list[BidderSnapshot]`;
   - `auction_count`;
   - `missing_roles`;
   - `error_code` and `error`;
   - `incomplete_buyer_ids` and `pool` under `resume`.
6. Keep `to_dict()` based on `model_dump(mode="json")` so enum and datetime
   serialization remains centralized.

Define `duration_seconds` on reports as cumulative active auction time and
use the `last_run_*` fields for the current invocation. Keep field names from
the current report wherever possible so version 1 is additive rather than a
rewritten shape.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
venv/bin/pytest -q tests/test_models.py tests/test_contracts.py
```

Expected: PASS.

- [ ] **Step 5: Commit the contract models**

```bash
git add core/models.py tests/test_models.py tests/test_contracts.py
git commit -m "feat: define versioned auction documents"
```

### Task 2: Make the engine persist round state and resume unsold players

**Files:**
- Modify: `core/auction_manager.py`
- Modify: `core/models.py`
- Test: `tests/test_auction_manager.py`
- Create: `tests/test_resume.py`

**Interfaces:**
- `AuctionEngine.from_checkpoint(checkpoint: AuctionCheckpoint, bidders: Sequence[Bidder]) -> AuctionEngine` restores state without recreating squads or losing transactions.
- `AuctionEngine.build_checkpoint(simulation: SimulationSnapshot, buyers: list[BidderSnapshot], error: Exception, missing_roles: dict[str, dict[str, int]]) -> AuctionCheckpoint` projects the current state into the versioned resumable document.
- `AuctionEngine.bid_issues` and `AuctionEngine.auction_count` remain readable for existing callers while using persisted state as the source of truth.

- [ ] **Step 1: Write failing engine tests for the resume lifecycle**

Add a small scripted bidder in `tests/test_resume.py` and cover:

```python
def test_resume_auctions_only_unsold_players_and_incomplete_squads():
    checkpoint = make_pool_exhaustion_checkpoint()
    complete_bidder = RecordingBidder("complete", bids={"u1": 9})
    incomplete_bidder = RecordingBidder("incomplete", bids={"u1": 4})

    engine = AuctionEngine.from_checkpoint(
        checkpoint,
        [complete_bidder, incomplete_bidder],
    )
    report = engine.run()

    assert complete_bidder.calls == []
    assert incomplete_bidder.calls == ["u1"]
    assert report.squads["complete"].is_complete
```

Define `RecordingBidder` in this test module with `buyer_id`, `name`, a
`calls` list, and a `bid(player, squad)` method that appends `player.id` and
returns `self.bids.get(player.id, 0)`. Define
`make_pool_exhaustion_checkpoint()` in `tests/checkpoint_fixtures.py` with a
complete 25-player roster for `complete`, a 24-player roster for `incomplete`
that is missing exactly one goalkeeper, and exactly one unsold goalkeeper
`u1`. Include both bidder snapshots and all report/checkpoint fields. Import
that helper in this test instead of recreating the persisted shape.

Also add tests for:

- sold players are never selected during resume;
- transactions and budgets from the checkpoint are retained;
- a player unsold again remains in the next checkpoint pool;
- a completed resume returns a report;
- a second pool exhaustion returns a new checkpoint;
- accumulated `BidIssue` entries and auction numbers survive resume;
- a checkpoint with no unsold players is rejected rather than retried forever.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
venv/bin/pytest -q tests/test_auction_manager.py tests/test_resume.py
```

Expected: FAIL because the engine cannot load checkpoints, restore state, or
select an `UNSOLD` pool for a new round.

- [ ] **Step 3: Implement state restoration and round transitions**

In `core/auction_manager.py`:

1. Allow construction from an existing `AuctionState` without rebuilding
   empty squads.
2. Implement `from_checkpoint`:
   - validate checkpoint version/type before entering the engine;
   - reconstruct the state from checkpoint data;
   - retain all squads and transactions;
   - increment `run_number`;
   - copy `UNSOLD` players as `AVAILABLE` for the new in-memory round;
   - filter active bidders to squads where `is_complete` is false;
   - initialize a new simple `random.Random` from the checkpoint seed;
   - restore cumulative `auction_count`, duration, and `bid_issues`.
3. Change player selection so a fresh run selects `AVAILABLE` players while
   a resumed engine selects only the reactivated previous `UNSOLD` players.
4. Keep canonical player identity and existing bid validation unchanged.
5. Move the authoritative issue and auction counters into `AuctionState`,
   retaining engine properties for compatibility.
6. Update `run()` to preserve the original lifecycle start, record the current
   run's start/end/duration, add it to cumulative duration, and increment the
   round only when a checkpoint is resumed.
7. Include accumulated issues and both timing scopes in `_report()`.
8. Implement `build_checkpoint()` with `error_code="pool_exhausted"`, current
   report fields, complete player state, missing roles, and resume metadata.

Do not import bidder implementation classes into the domain engine. The CLI
will rebuild bidder objects from the checkpoint's normalized snapshots.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
venv/bin/pytest -q tests/test_auction_manager.py tests/test_resume.py
```

Expected: PASS.

- [ ] **Step 5: Commit the engine resume behavior**

```bash
git add core/auction_manager.py core/models.py tests/test_auction_manager.py tests/test_resume.py
git commit -m "feat: resume auctions from unsold players"
```

### Task 3: Add version-aware JSON save/load

**Files:**
- Modify: `utils/json_store.py`
- Modify: `tests/test_json_store.py`

**Interfaces:**
- `JsonStore.save_report(report: SimulationReport, path: str | Path) -> Path` writes a version-1 report.
- `JsonStore.save_checkpoint(checkpoint: AuctionCheckpoint, path: str | Path) -> Path` writes a version-1 checkpoint.
- `JsonStore.load_checkpoint(path: str | Path) -> AuctionCheckpoint` parses and validates an autonomous pool-exhaustion checkpoint.

- [ ] **Step 1: Write failing persistence tests**

Add tests that:

```python
def test_checkpoint_round_trip_preserves_resume_data(tmp_path):
    source = make_checkpoint()
    path = JsonStore().save_checkpoint(source, tmp_path / "checkpoint.json")

    loaded = JsonStore().load_checkpoint(path)

    assert loaded.schema_version == 1
    assert loaded.document_type == "auction_checkpoint"
    assert loaded.players == source.players
    assert loaded.buyers == source.buyers
    assert loaded.bid_issues == source.bid_issues
```

Also test that loading rejects missing files, malformed JSON, wrong document
types, unsupported schema versions, missing embedded configuration, and an
error code other than `pool_exhausted`.

- [ ] **Step 2: Run the persistence tests and verify they fail**

Run:

```bash
venv/bin/pytest -q tests/test_json_store.py
```

Expected: FAIL because `JsonStore` has no typed checkpoint loader or version
validation.

- [ ] **Step 3: Implement typed JSON persistence**

In `utils/json_store.py`:

1. Keep directory creation and UTF-8 pretty JSON output.
2. Serialize typed Pydantic documents through `model_dump(mode="json")`.
3. Add `load_checkpoint` using `json.loads` and
   `AuctionCheckpoint.model_validate`.
4. Reject documents unless `schema_version == 1`,
   `document_type == "auction_checkpoint"`, and
   `error_code == "pool_exhausted"`.
5. Preserve the existing `error` string and all report-shaped fields without
   silently dropping unknown state data needed by the resume flow.
6. Remove the old generic checkpoint behavior that inferred missing roles from
   arbitrary dictionaries; callers should pass a validated
   `AuctionCheckpoint`.

- [ ] **Step 4: Run the persistence tests and verify they pass**

Run:

```bash
venv/bin/pytest -q tests/test_json_store.py
```

Expected: PASS.

- [ ] **Step 5: Commit version-aware persistence**

```bash
git add utils/json_store.py tests/test_json_store.py
git commit -m "feat: load versioned auction checkpoints"
```

### Task 4: Wire fresh and resumed runs into the CLI

**Files:**
- Modify: `main.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Add `--resume PATH` to the parser.
- Fresh runs continue to use `--config`, `--players`, `--output`,
  `--checkpoint`, and `--seed`.
- Resume runs use the checkpoint's embedded players, simulation snapshot, and
  bidder snapshots; `--output` controls the final report and `--checkpoint`
  controls the next-checkpoint destination.

- [ ] **Step 1: Write failing CLI tests**

Add tests for:

```python
def test_cli_resumes_autonomous_checkpoint_without_config_or_players(tmp_path):
    checkpoint = make_checkpoint_file(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main(["--resume", str(checkpoint), "--output", str(report)])

    assert exit_code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["document_type"] == "auction_report"
    assert data["players_sold"] == data["total_players"]
```

Define `make_checkpoint_file(tmp_path)` in `tests/test_cli.py` by importing
`make_pool_exhaustion_checkpoint` from `tests/checkpoint_fixtures.py` and
calling `JsonStore.save_checkpoint` on the requested path. Also test that:

- an incomplete resumed round writes a replacement checkpoint;
- the default replacement path is the input checkpoint when no output path is
  supplied;
- fresh-run CLI behavior remains unchanged;
- invalid checkpoints return `1` without writing a new checkpoint;
- configuration or unexpected engine errors return `1` without writing a
  resumable checkpoint;
- a resumed run does not read the Excel workbook.

- [ ] **Step 2: Run the CLI tests and verify they fail**

Run:

```bash
venv/bin/pytest -q tests/test_cli.py
```

Expected: FAIL because `--resume` and autonomous checkpoint loading are not
implemented.

- [ ] **Step 3: Implement the resume command path**

In `main.py`:

1. Add `--resume` and make fresh-run config resolution conditional so resume
   does not require a YAML file or Excel path.
2. For resume:
   - call `JsonStore.load_checkpoint`;
   - rebuild bidder objects from the embedded `buyers` and `simulation.seed`;
   - construct `AuctionEngine.from_checkpoint`;
   - derive the output checkpoint path from `--checkpoint` or the input
     resume path;
   - do not load players from Excel.
3. For fresh runs, create normalized `SimulationSnapshot` and
   `BidderSnapshot` data for checkpoint creation.
4. On successful `engine.run()`, save the version-1 report.
5. On `AuctionIncompleteError`, build and save a checkpoint with the full
   partial report and autonomous configuration.
6. On all other exceptions, log the error and return `1` without saving a
   checkpoint.
7. Preserve existing exit codes and output path defaults.

With `--resume`, the embedded checkpoint configuration is authoritative;
explicit fresh-run inputs must not replace it.

- [ ] **Step 4: Run the CLI tests and verify they pass**

Run:

```bash
venv/bin/pytest -q tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 5: Commit the CLI workflow**

```bash
git add main.py tests/test_cli.py
git commit -m "feat: add autonomous auction resume CLI"
```

### Task 5: Update project documentation and run the complete verification

**Files:**
- Modify: `docs/project.md`
- Modify: `docs/roadmap.md`
- Test: all files under `tests/`

- [ ] **Step 1: Update the project status documentation**

Document:

- version-1 report and checkpoint metadata;
- `--resume CHECKPOINT` usage;
- the unsold-only/incomplete-squads rule;
- autonomous checkpoint configuration;
- cumulative and last-run timing;
- the fact that exact random-generator continuation is intentionally not
  supported;
- that only pool exhaustion writes a checkpoint.

Do not modify the user's unrelated working-tree changes in `README.md`.

- [ ] **Step 2: Run the complete test suite with warnings treated as errors**

Run:

```bash
venv/bin/pytest -q -W error
```

Expected: all existing and new tests pass.

- [ ] **Step 3: Run a real fresh simulation**

Run:

```bash
venv/bin/python main.py --config configs/default.yaml --output /tmp/auction-p1-report.json --checkpoint /tmp/auction-p1-checkpoint.json
```

Expected: exit code `0`, a version-1 `auction_report`, and the documented
real-workbook player counts.

- [ ] **Step 4: Run a real resume smoke test with a deliberately incomplete
fixture**

Use a temporary YAML/Excel fixture that exhausts the first pool with at least
one incomplete squad and at least one unsold player. Run the first auction,
then run:

```bash
venv/bin/python main.py --resume /tmp/auction-p1-checkpoint.json --output /tmp/auction-p1-final.json
```

Expected: the second run reads only the checkpoint, uses only its unsold
players, excludes complete squads, and writes either a final report or an
updated checkpoint according to the remaining pool.

- [ ] **Step 5: Inspect the final diff and report the verification evidence**

Run:

```bash
git status --short
git diff --check
```

Confirm that only the planned files changed and preserve any pre-existing
user changes. Commit the documentation and verification updates:

```bash
git add docs/project.md docs/roadmap.md
git commit -m "docs: describe resumable auction checkpoints"
```
