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
