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