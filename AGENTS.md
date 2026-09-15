# AGENTS.md

Non-interactive CLI that simulates an Italian fantasy-football auction. Python 3, pydantic + httpx + loguru + pandas/openpyxl. See `docs/project.md` for the full architecture and config contract; `docs/roadmap.md` for scope/deferrals.

## Commands

```bash
python -m venv venv
venv/bin/python -m pip install -r requirements.txt

venv/bin/python main.py --config configs/default.yaml   # run a simulation
venv/bin/pytest -q -W error                            # full suite (CI runs exactly this)
venv/bin/pytest tests/test_cli.py -q -W error          # single file / -k for a test
venv/bin/python main.py benchmark --config configs/llm.yaml --runs 2
```

- No linter, formatter, or typechecker is configured (ruff/mypy are NOT in `requirements.txt`). CI only runs `git diff --check` and `pytest -q -W error`. Treat warnings as errors locally too.
- `pytest.ini` sets `pythonpath = .` and `testpaths = tests`, so tests import top-level packages by name and `checkpoint_fixtures` as a bare module.
- Local `venv` is Python 3.14, but CI pins **3.11** — keep code 3.11-compatible.

## Architecture

- `main.py` — CLI composition root; wires config -> bidders -> engine -> report/checkpoint.
- `core/` — pure domain: `models.py` (players, squads, bids, reports) and `auction_manager.py` (`AuctionEngine`). No I/O.
- `agents/` — bidder strategies implementing the `Bidder` protocol (`base_agent.py`). `buyer_agent.py` = `DeterministicBidder`/`RandomBidder`; `coach_agent.py` = `CoachAgent`; `llm_client.py` = `LlmClient` + tool/search schemas; `coach_loader.py` discovers `coachAgent_*.md` profiles (`paths.coaches`); `coach_prompt.py` renders `prompts/common.md` with domain rules.
- `utils/` — adapters: `config_loader`, `excel_handler`, `json_store`, `logger`.
- `benchmark/` — `benchmark` subcommand; `metrics.py` is pure functions over report JSON + trace JSONL.

## Rules an agent will likely trip on

- Bid validation is centralized in `Squad.validate_bid` (`core/models.py`). A bid must be an exact `int` (`bool`/`float`/`None`/negative invalid); `0` = pass; a tie for the top positive bid = unsold.
- Invalid/raising bidders never crash the engine: they produce a `BidIssue` diagnostic and the offer is normalized to `0`.
- Only `AuctionIncompleteError` (pool exhaustion) writes a resumable checkpoint. Config errors, file errors, and other exceptions exit `1` with no checkpoint.
- Reports/checkpoints are version-1 typed JSON (`schema_version` + `document_type: auction_report|auction_checkpoint`). Never change these formats without bumping a version (explicitly out of scope otherwise).
- Roster sizes are fixed: 25 players = P3/D8/C8/A6.
- Coach files are only discovered when `paths.coaches` is set — a `coachAgent_*.md` dropped in `agents/` does nothing unless `configs/llm.yaml` (or another config) points there. The `Bidder` protocol requires `observe(result, squad)`, so every test double must implement it (no-op is fine).

## Secrets

- API keys NEVER appear in config files or sidecars — only env-var *names* (`llm.api_key_env`, `llm.search.api_key_env`; legacy `llm.brave.api_key_env` still accepted). `validate_global_llm` rejects a literal `api_key` field. Read the key via `os.environ` only.
- LLM key: `OPENCODE_API_KEY` in the example config; a missing LLM key is a pre-auction error. Live search is optional — `llm.search` with `responses`/`anthropic`/`brave` providers (legacy `llm.brave` block still accepted); a missing key or failed request just degrades to `search non disponibile`.

## CoachAgent (LLM bidders)

- One shared `LlmClient` (httpx) per run; `CoachAgent` loops function-calling over `DEFAULT_TOOLS = ("search_info", "submit_bid")` until a domain-valid bid (`Squad.validate_bid`) or the iteration cap.
- Coaches are auto-discovered from `coachAgent_*.md` files in `paths.coaches` (example: `agents/`): optional YAML front-matter (`model`, `temperature`, `max_tool_iterations`, `tools`, `spending_profile`, `target_players`, `system_prompt`) plus a markdown profile body. No YAML `buyers` entry needed.
- The system prompt is `prompts/common.md` (shared, with placeholders filled from `core/models.py` + budget) + structured fields + profile body; an explicit `system_prompt` wins over everything.
- After every resolved lot the engine calls `bidder.observe(result, squad)` on the bidders that were polled: `CoachAgent` traces `auction_result` (`won` with player/price/updated roster, `lost` otherwise) and logs it; deterministic/random bidders are no-ops. This is the seam for future per-agent session memory.
- Traces go to `logs/traces/<run_dir>/<buyer_id>.jsonl` (one JSON object per event, flushed immediately). `<run_dir>` is chosen by `main.py`/`benchmark`, never the engine.
- Pool exhaustion with an `llm` buyer writes a `checkpoint.llm.yaml` sidecar next to the checkpoint; resuming such a checkpoint requires it and ignores `--config`. The sidecar stores the resolved `system_prompt` per buyer (schema stays 1), so resume needs neither the YAML nor the `.md` files.

## Data / config

- Excel source is `data/` + `.xlsx`, both gitignored — the workbook is not in git. Adapter reads the `Tutti` sheet, whose real header is on the **second row** (columns `Id, R, Nome, Squadra, Qt.A`).
- `configs/default.yaml` is the canonical schema (deterministic bidders); `configs/llm.yaml` is the CoachAgent example (its `paths.coaches: "agents"` discovers the example `coachAgent_*.md` files). Unknown YAML sections/fields are ignored; CLI flags override YAML.

## Conventions

- Conventional commits (`feat(llm):`, `refactor:`, `fix(security):`, `review:`). Active work is on the `dev` branch (`origin/main` is the default branch but not where changes land).
- `docs/project.md` is slightly stale in places (mentions parallel bid collection and `utils/validator.py` that no longer exist — `_collect_bids` is currently sequential). Trust the code over that prose.
