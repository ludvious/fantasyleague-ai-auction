# CoachAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the LLM bidder into a `CoachAgent` with auto-discovered markdown profiles, a shared common prompt fed by domain rules, domain-validated bids with in-loop retry, post-auction outcome notifications, and console-visible reasoning — without touching auction rules or versioned JSON contracts.

**Architecture:** `LlmClient` (HTTP/search) is split from `CoachAgent` (decision loop). Coach profiles are `coachAgent_*.md` files discovered from `paths.coaches` (example points at the repo `agents/` root); front-matter carries technical fields, the body is the agent profile. A shared `agents/prompts/common.md` is rendered with placeholders from `core/models.py` and `simulation.budget`, then combined with the profile and structured fields. The engine gains an `observe(result, squad)` hook after each lot; `CoachAgent` turns it into a trace event + log line (the future memory seam). The sidecar stays version 1 and stores the resolved system prompt, so resume needs neither YAML nor `.md` files.

**Tech Stack:** Python 3.11 (CI pin; local venv 3.14), pydantic, httpx, loguru, PyYAML, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-coach-agent-design.md`

## Global Constraints

- Run `venv/bin/pytest -q -W error` before every commit; all tests green (baseline: 177).
- Python 3.11-compatible code only.
- Report/checkpoint schemas are untouched (`schema_version: 1`); the LLM sidecar stays `schema_version: 1` and only gains the resolved `system_prompt` inside the existing per-buyer `llm` mapping.
- YAML `strategy: llm` keeps its name; only the class/module names change to CoachAgent.
- API keys never appear in configs or sidecars: env-var names only, keys read via `os.environ`.
- Conventional commits on the `dev` branch (`refactor:`, `feat(llm):`, `test:`, `docs:`). `git add` explicit paths only; never sweep unrelated files.
- No comments in code unless a `ponytail:` deliberate-shortcut note is needed.
- No linter/formatter/typechecker is configured; CI runs `git diff --check` and `pytest -q -W error`.
- Repo root: `/home/ludovg/projects/fantasyleague-ai-auction` (all paths below are relative to it).

---

### Task 1: Split `LlmClient` from `CoachAgent`

**Files:**
- Move: `agents/llm_agent.py` → `agents/llm_client.py` (module docstring, imports, `MOCK_BRAVE_KEY`, `USER_AGENT`, `TOOL_SCHEMAS`, `SEARCH_PROMPT`, `_format_search_result`, `LlmClient`)
- Create: `agents/coach_agent.py` (`CoachAgent`, today's `AgentManager` code)
- Modify: `main.py:16,132,143`
- Modify: `utils/config_loader.py:10`
- Move: `tests/test_llm_agent.py` → `tests/test_coach_agent.py`
- Modify: `tests/test_llm_client.py:6`
- Modify: `tests/test_imports.py:10`

**Interfaces:**
- Produces: `agents.llm_client.LlmClient` (constructor unchanged) plus `TOOL_SCHEMAS`, `MOCK_BRAVE_KEY`, `USER_AGENT`, `SEARCH_PROMPT`, `_format_search_result`; `agents.coach_agent.CoachAgent` with the same constructor and methods as `AgentManager`.
- Consumes: nothing new.

- [ ] **Step 1: Baseline**

Run: `venv/bin/pytest -q -W error`
Expected: `177 passed`.

- [ ] **Step 2: Move the module**

```bash
git mv agents/llm_agent.py agents/llm_client.py
```

- [ ] **Step 3: Trim `agents/llm_client.py`**

Change the module docstring to `"""HTTP client, tool schemas, and web search for CoachAgent."""`.

Delete the whole `class AgentManager:` block (today's lines 288–503) and the imports it alone uses: `from agents.trace import TraceLogger` and `from core.models import Player, Squad`. Keep `json`, `typing.Any`, `httpx`.

- [ ] **Step 4: Create `agents/coach_agent.py`**

```python
"""LLM-driven bidder: the CoachAgent decision loop."""

from __future__ import annotations

import json

from agents.llm_client import TOOL_SCHEMAS
from agents.trace import TraceLogger
from core.models import Player, Squad


class CoachAgent:
    """Bidder driven by an OpenAI-compatible function-calling loop.

    Stateless per bid: messages are rebuilt from scratch for every player.
    """

    DEFAULT_TOOLS: tuple[str, ...] = ("search_info", "submit_bid")

    # ... constructor and methods moved verbatim from AgentManager ...
```

Move the `__init__`, `_system_prompt`, `_context`, `_user_message`, `_search_count`, and `bid` methods verbatim; only the class name changes.

- [ ] **Step 5: Rename the agent test module**

```bash
git mv tests/test_llm_agent.py tests/test_coach_agent.py
```

In `tests/test_coach_agent.py`: `from agents.llm_agent import AgentManager` → `from agents.coach_agent import CoachAgent`, and `AgentManager(` in `make_manager` → `CoachAgent(`.

- [ ] **Step 6: Update the remaining imports**

`tests/test_llm_client.py:6`:
```python
from agents.llm_client import LlmClient, MOCK_BRAVE_KEY, _format_search_result
```

`tests/test_imports.py` list:
```python
    [
        "agents.trace",
        "agents.llm_client",
        "agents.coach_agent",
        "benchmark.metrics",
    ]
```

`main.py:16`:
```python
from agents.coach_agent import CoachAgent
from agents.llm_client import LlmClient
```
plus `AgentManager(` → `CoachAgent(` (line 132) and `AgentManager.DEFAULT_TOOLS` → `CoachAgent.DEFAULT_TOOLS` (line 143).

`utils/config_loader.py:10`:
```python
from agents.llm_client import TOOL_SCHEMAS
```

- [ ] **Step 7: Verify and commit**

Run: `venv/bin/pytest -q -W error`
Expected: `177 passed`.

```bash
git add agents/llm_client.py agents/coach_agent.py main.py utils/config_loader.py tests/test_coach_agent.py tests/test_llm_client.py tests/test_imports.py
git commit -m "refactor(agents): split LlmClient from CoachAgent and rename AgentManager"
```

---

### Task 2: Common prompt template and renderer

**Files:**
- Create: `agents/prompts/common.md`
- Create: `agents/coach_prompt.py`
- Test: `tests/test_coach_prompt.py`

**Interfaces:**
- Produces: `coach_prompt.common_prompt(budget: int) -> str` and `coach_prompt.render_system_prompt(*, budget: int, profile: str | None = None, role: str | None = None, personality: str | None = None, spending_profile: dict[str, float] | None = None, target_players: list[str] | None = None, override: str | None = None) -> str`.
- Consumes: `core.models.ROSTER_REQUIREMENTS`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for common-prompt rendering."""

import pytest

from agents import coach_prompt
from agents.coach_prompt import common_prompt, render_system_prompt


def test_common_prompt_substitutes_domain_placeholders():
    text = common_prompt(500)

    assert "{roster_requirements}" not in text
    assert "{budget}" not in text
    assert "{max_bid_rule}" not in text
    assert "P: 3, D: 8, C: 8, A: 6" in text
    assert "500" in text
    assert "1 credito" in text


def test_render_appends_structured_fields_and_profile():
    rendered = render_system_prompt(
        budget=500,
        profile="Joe è aggressivo.",
        role="fantallenatore esperto",
        personality="prudente",
        spending_profile={"P": 0.1, "D": 0.2, "C": 0.3, "A": 0.4},
        target_players=["Lautaro"],
    )

    assert rendered.startswith("Sei un allenatore-manager")
    assert "Ruolo: fantallenatore esperto" in rendered
    assert (
        "Distribuzione di spesa ideale per ruolo: "
        "P: 10%, D: 20%, C: 30%, A: 40%" in rendered
    )
    assert "Giocatori obiettivo: Lautaro" in rendered
    assert rendered.endswith("# Profilo dell'agente\nJoe è aggressivo.")


def test_override_wins():
    assert render_system_prompt(budget=500, override="custom") == "custom"


def test_missing_placeholder_fails(tmp_path, monkeypatch):
    broken = tmp_path / "common.md"
    broken.write_text("solo testo senza placeholder", encoding="utf-8")
    monkeypatch.setattr(coach_prompt, "COMMON_PROMPT_PATH", broken)

    with pytest.raises(ValueError, match="roster_requirements"):
        common_prompt(500)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_coach_prompt.py -q -W error`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.coach_prompt'`.

- [ ] **Step 3: Create `agents/prompts/common.md`**

```markdown
Sei un allenatore-manager che partecipa a un'asta di fantacalcio italiano.
Il tuo compito è decidere, per ogni giocatore all'asta, un'offerta razionale che rispetti i vincoli della lega e la strategia della tua squadra.
L'obiettivo non è aggiudicarti più giocatori possibile né vincere ogni lotto: devi costruire una rosa completa e competitiva, preservando il budget per le occasioni future.

## Regolamento vigente

- Rosa richiesta: {roster_requirements}.
- Budget iniziale: {budget} crediti.
- {max_bid_rule}
- Un'offerta pari a 0 significa passare.
- In caso di parità tra le offerte più alte il giocatore resta invenduto.
- La quotazione del giocatore è informativa e non è il prezzo di partenza.

## Priorità delle informazioni

1. I vincoli tecnici forniti nel contesto sono obbligatori.
2. I dati strutturati sul giocatore sono preferibili alle supposizioni.
3. Le notizie ottenute tramite ricerca sono informazioni esterne: considerane data, attendibilità e incertezza.
4. Se un dato non è disponibile, non inventarlo e non presentarlo come certo.

## Metodo decisionale

Prima di offrire:

1. Verifica che il giocatore sia utile per completare la rosa.
2. Controlla quanti slot restano nel suo ruolo.
3. Valuta rendimento atteso, bonus, titolarità, rischio e scarsità del ruolo.
4. Considera budget residuo, slot da riempire e costo delle occasioni future.
5. Stima il tuo prezzo massimo e confrontalo con max_bid_allowed.
6. Se il prezzo non è conveniente o compromette la rosa futura, passa.

Non inseguire un giocatore solo perché te lo eri prefissato.
Non spendere tutto il budget su un singolo giocatore senza una ragione strategica esplicita.

## Incertezza

Distingui tra valore atteso, scenario positivo, scenario negativo e livello di rischio.
Riduci il prezzo massimo quando hai dubbi rilevanti su titolarità, infortuni, trasferimenti o continuità.
Se la ricerca non è disponibile o non produce risultati, continua la valutazione con le informazioni già presenti.

## Strumenti

`search_info` cerca notizie recenti utili alla valutazione (infortuni, titolarità, trasferimenti, situazione tattica). Usala quando l'informazione mancante ha un impatto significativo sul prezzo massimo, non automaticamente per ogni giocatore.

`submit_bid` comunica la decisione finale e va chiamata esattamente una volta con un numero intero tra 0 e max_bid_allowed (0 = passo).

Il limite `max_bid_allowed` fornito dal motore è sempre vincolante.

## Risposta

Ragiona in modo strutturato ma breve: prezzo massimo stimato, rischio principale, motivo della decisione.
La decisione finale deve sempre arrivare tramite `submit_bid`.
```

- [ ] **Step 4: Create `agents/coach_prompt.py`**

```python
"""System prompt assembly for CoachAgent."""

from __future__ import annotations

from pathlib import Path

from core.models import ROSTER_REQUIREMENTS

COMMON_PROMPT_PATH = Path(__file__).with_name("prompts") / "common.md"

MAX_BID_RULE = (
    "Ogni slot libero riserva 1 credito: la tua offerta massima consentita è "
    "budget_rimanente - slot_liberi + 1 (ricevi il valore esatto nel contesto)."
)


def common_prompt(budget: int) -> str:
    text = COMMON_PROMPT_PATH.read_text(encoding="utf-8")
    requirements = ", ".join(
        f"{position.value}: {count}"
        for position, count in ROSTER_REQUIREMENTS.items()
    )
    replacements = {
        "{roster_requirements}": requirements,
        "{budget}": str(budget),
        "{max_bid_rule}": MAX_BID_RULE,
    }
    for placeholder, value in replacements.items():
        if placeholder not in text:
            raise ValueError(
                f"common prompt is missing placeholder {placeholder}"
            )
        text = text.replace(placeholder, value)
    return text


def render_system_prompt(
    *,
    budget: int,
    profile: str | None = None,
    role: str | None = None,
    personality: str | None = None,
    spending_profile: dict[str, float] | None = None,
    target_players: list[str] | None = None,
    override: str | None = None,
) -> str:
    if override and override.strip():
        return override.strip()
    sections = [common_prompt(budget)]
    structured = []
    if role:
        structured.append(f"Ruolo: {role}")
    if personality:
        structured.append(f"Personalità: {personality}")
    if spending_profile:
        spending = ", ".join(
            f"{position}: {share:.0%}"
            for position, share in spending_profile.items()
        )
        structured.append(f"Distribuzione di spesa ideale per ruolo: {spending}")
    if target_players:
        structured.append(f"Giocatori obiettivo: {', '.join(target_players)}")
    if structured:
        sections.append("\n".join(structured))
    if profile and profile.strip():
        sections.append("# Profilo dell'agente\n" + profile.strip())
    return "\n\n".join(sections)
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest tests/test_coach_prompt.py -q -W error`
Expected: `4 passed`.

- [ ] **Step 6: Full suite and commit**

Run: `venv/bin/pytest -q -W error`
Expected: `181 passed`.

```bash
git add agents/prompts/common.md agents/coach_prompt.py tests/test_coach_prompt.py
git commit -m "feat(llm): add shared common prompt with domain-rendered rules"
```

---

### Task 3: Coach discovery and front-matter loader

**Files:**
- Create: `agents/coach_loader.py`
- Test: `tests/test_coach_loader.py`

**Interfaces:**
- Consumes: `utils.config_loader.validate_llm_buyer`.
- Produces:
  - `load_coaches(directory: str | Path) -> list[dict[str, Any]]` — buyer-shaped dicts `{"id", "name", "strategy": "llm", "priority", "llm", "profile"}`;
  - `load_buyer_configs(config: dict[str, Any]) -> list[dict[str, Any]]` — YAML buyers first, then coaches with renumbered priorities.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for coach discovery and front-matter parsing."""

import pytest

from agents.coach_loader import load_buyer_configs, load_coaches


def write_coach(directory, filename: str, text: str) -> None:
    (directory / filename).write_text(text, encoding="utf-8")


def test_loads_front_matter_and_body(tmp_path):
    write_coach(
        tmp_path,
        "coachAgent_Joe.md",
        "---\nmodel: gpt-4o-mini\ntemperature: 0.4\n"
        "spending_profile: {P: 0.1, D: 0.2, C: 0.3, A: 0.4}\n"
        "target_players: [Lautaro]\n---\n\nJoe è aggressivo.\n",
    )

    coaches = load_coaches(tmp_path)

    assert len(coaches) == 1
    coach = coaches[0]
    assert coach["id"] == "joe"
    assert coach["name"] == "Joe"
    assert coach["strategy"] == "llm"
    assert coach["priority"] == 0
    assert coach["llm"]["model"] == "gpt-4o-mini"
    assert coach["llm"]["temperature"] == 0.4
    assert coach["profile"] == "Joe è aggressivo."


def test_body_only_file_uses_filename_defaults(tmp_path):
    write_coach(tmp_path, "coachAgent_Beta.md", "Solo corpo.\n")

    coach = load_coaches(tmp_path)[0]

    assert coach["id"] == "beta"
    assert coach["name"] == "Beta"
    assert coach["llm"] == {}
    assert coach["profile"] == "Solo corpo."


def test_files_are_sorted_and_numbered(tmp_path):
    write_coach(tmp_path, "coachAgent_Zeta.md", "Z")
    write_coach(tmp_path, "coachAgent_Alfa.md", "A")

    coaches = load_coaches(tmp_path)

    assert [coach["name"] for coach in coaches] == ["Alfa", "Zeta"]
    assert [coach["priority"] for coach in coaches] == [0, 1]


def test_unknown_front_matter_field_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nunknown: 1\n---\nbody")

    with pytest.raises(ValueError, match="unknown front-matter"):
        load_coaches(tmp_path)


def test_unclosed_front_matter_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nmodel: gpt\nbody")

    with pytest.raises(ValueError, match="not closed"):
        load_coaches(tmp_path)


def test_invalid_field_rejected_with_path(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\ntemperature: 5\n---\nbody")

    with pytest.raises(ValueError, match="coachAgent_Joe.md"):
        load_coaches(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "---\nid: same\n---\nA")
    write_coach(tmp_path, "coachAgent_Jim.md", "---\nid: same\n---\nB")

    with pytest.raises(ValueError, match="duplicate coach id"):
        load_coaches(tmp_path)


def test_empty_directory_returns_empty_list(tmp_path):
    assert load_coaches(tmp_path) == []


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_coaches(tmp_path / "missing")


def test_non_coach_files_ignored(tmp_path):
    write_coach(tmp_path, "prompt.md", "not a coach")

    assert load_coaches(tmp_path) == []


def test_load_buyer_configs_merges_yaml_and_coaches(tmp_path):
    write_coach(tmp_path, "coachAgent_Joe.md", "body")
    config = {
        "paths": {"coaches": str(tmp_path)},
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}],
    }

    buyers = load_buyer_configs(config)

    assert [buyer["id"] for buyer in buyers] == ["b1", "joe"]
    assert buyers[0]["priority"] == 0
    assert buyers[1]["priority"] == 1


def test_load_buyer_configs_without_coaches_returns_yaml_buyers():
    config = {
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "deterministic"}]
    }

    assert load_buyer_configs(config) == config["buyers"]


def test_load_buyer_configs_rejects_id_collisions(tmp_path):
    write_coach(tmp_path, "coachAgent_b1.md", "body")
    config = {
        "paths": {"coaches": str(tmp_path)},
        "buyers": [{"id": "b1", "name": "Alpha", "strategy": "llm", "llm": {}}],
    }

    with pytest.raises(ValueError, match="Duplicate buyer ids"):
        load_buyer_configs(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_coach_loader.py -q -W error`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.coach_loader'`.

- [ ] **Step 3: Create `agents/coach_loader.py`**

```python
"""Discovery and validation of CoachAgent markdown profiles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from utils.config_loader import validate_llm_buyer

COACH_GLOB = "coachAgent_*.md"
FRONT_MATTER_FIELDS = (
    "id",
    "name",
    "model",
    "role",
    "personality",
    "temperature",
    "max_tool_iterations",
    "tools",
    "spending_profile",
    "target_players",
    "system_prompt",
)


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{path}: front-matter opened but not closed")
    raw = text[3:end].strip()
    body = text[end + 4:].strip()
    data = yaml.safe_load(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: front-matter must be a mapping")
    unknown = set(data) - set(FRONT_MATTER_FIELDS)
    if unknown:
        raise ValueError(f"{path}: unknown front-matter fields {sorted(unknown)}")
    return data, body


def _derive_id(path: Path) -> str:
    stem = path.stem
    if stem.startswith("coachAgent_"):
        stem = stem[len("coachAgent_"):]
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _derive_name(path: Path) -> str:
    return path.stem.removeprefix("coachAgent_")


def load_coaches(directory: str | Path) -> list[dict[str, Any]]:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Coaches directory not found: {directory}")
    coaches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob(COACH_GLOB)):
        data, profile = _split_front_matter(
            path.read_text(encoding="utf-8"), path
        )
        buyer_id = str(data.get("id") or _derive_id(path))
        name = str(data.get("name") or _derive_name(path))
        if not buyer_id or not name:
            raise ValueError(f"{path}: id and name must be non-empty")
        if buyer_id in seen:
            raise ValueError(f"{path}: duplicate coach id '{buyer_id}'")
        seen.add(buyer_id)
        llm = {key: data[key] for key in data if key not in ("id", "name")}
        if "system_prompt" in llm and not str(llm["system_prompt"]).strip():
            raise ValueError(f"{path}: system_prompt must be non-empty")
        validate_llm_buyer(llm, str(path))
        coaches.append(
            {
                "id": buyer_id,
                "name": name,
                "strategy": "llm",
                "priority": len(coaches),
                "llm": llm,
                "profile": profile,
            }
        )
    return coaches


def load_buyer_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    buyers = list(config.get("buyers") or [])
    coaches_dir = (config.get("paths") or {}).get("coaches")
    if not coaches_dir:
        return buyers
    coaches = load_coaches(coaches_dir)
    yaml_ids = {str(buyer.get("id", "")).strip() for buyer in buyers}
    duplicates = sorted(yaml_ids & {coach["id"] for coach in coaches})
    if duplicates:
        raise ValueError(
            f"Duplicate buyer ids between config and coaches: {duplicates}"
        )
    for index, coach in enumerate(coaches):
        coach["priority"] = len(buyers) + index
    return buyers + coaches
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest tests/test_coach_loader.py -q -W error`
Expected: `12 passed`.

- [ ] **Step 5: Full suite and commit**

Run: `venv/bin/pytest -q -W error`
Expected: `193 passed`.

```bash
git add agents/coach_loader.py tests/test_coach_loader.py
git commit -m "feat(llm): discover CoachAgent profiles from markdown front-matter"
```

---

### Task 4: Wire discovery into the CLI and benchmark

**Files:**
- Modify: `utils/config_loader.py` (`_validate_config`)
- Modify: `agents/coach_agent.py` (constructor takes `system_prompt`, drops prompt generation)
- Modify: `main.py` (`_build_bidders`, `_write_llm_sidecar`, resume call sites)
- Modify: `benchmark/runner.py`
- Modify: `tests/test_coach_agent.py` (`make_manager`)
- Modify: `tests/test_cli.py` (`FakeLlmClient`, sidecar assertions, new tests)
- Modify: `tests/test_benchmark.py` (coaches-dir test)
- Modify: `configs/llm.yaml`
- Create: `agents/coachAgent_Alfa.md`, `agents/coachAgent_Beta.md`, `agents/coachAgent_Gamma.md`, `agents/coachAgent_Delta.md`

**Interfaces:**
- Consumes: `load_buyer_configs` (Task 3), `render_system_prompt` (Task 2).
- Produces: `_build_bidders(configs, seed, llm_config=None, run_dir=None, budget=500)`; `CoachAgent(buyer_id, name, client, tracer, *, model, temperature, system_prompt, max_tool_iterations=3, tools=DEFAULT_TOOLS)`; `_write_llm_sidecar(checkpoint_path, buyer_configs, llm_config, budget)`.

- [ ] **Step 1: Relax config validation for coaches**

In `utils/config_loader.py`, replace the buyers block inside `_validate_config` (today's lines 202–228) with:

```python
    buyers = config.get("buyers")
    coaches = paths.get("coaches") if isinstance(paths, dict) else None
    if coaches is not None and not str(coaches).strip():
        raise ValueError("'paths.coaches' must be a non-empty string")
    if buyers is not None and not isinstance(buyers, list):
        # Existing contract tests assert this exact substring.
        raise ValueError("'buyers' must be a non-empty list")
    buyer_list = buyers or []
    if not buyer_list and not coaches:
        raise ValueError(
            "'buyers' must be a non-empty list when 'paths.coaches' is not set"
        )
    for index, buyer in enumerate(buyer_list):
        if not isinstance(buyer, dict):
            raise ValueError(f"'buyers[{index}]' must be a mapping")
        if not str(buyer.get("id", "")).strip():
            raise ValueError(f"'buyers[{index}].id' must be a non-empty string")
        if not str(buyer.get("name", "")).strip():
            raise ValueError(f"'buyers[{index}].name' must be a non-empty string")
        strategy = str(buyer.get("strategy", "deterministic")).lower()
        if strategy not in ("deterministic", "random", "llm"):
            raise ValueError(
                f"'buyers[{index}].strategy' must be 'deterministic', 'random' or 'llm'"
            )
        if strategy == "llm":
            validate_llm_buyer(buyer.get("llm"), index)
        priority = buyer.get("priority")
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise ValueError(f"'buyers[{index}].priority' must be an int")
    if coaches or any(
        str(buyer.get("strategy", "deterministic")).lower() == "llm"
        for buyer in buyer_list
    ):
        validate_global_llm(config.get("llm"))
```

- [ ] **Step 2: Simplify `CoachAgent.__init__`**

Replace the constructor signature and prompt handling in `agents/coach_agent.py`:

```python
    def __init__(
        self,
        buyer_id: str,
        name: str,
        client: LlmClient,
        tracer: TraceLogger,
        *,
        model: str,
        temperature: float,
        system_prompt: str,
        max_tool_iterations: int = 3,
        tools: tuple[str, ...] = DEFAULT_TOOLS,
    ):
        if not buyer_id or not name:
            raise ValueError("buyer_id and name are required")
        if not system_prompt.strip():
            raise ValueError("system_prompt is required")
        self.buyer_id = buyer_id
        self.name = name
        self.client = client
        self.tracer = tracer
        self.model = model
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.max_tool_iterations = max_tool_iterations
        self.tools = tools
```

Delete the `_system_prompt` method; in `bid()` use `{"role": "system", "content": self.system_prompt}`. Add `from agents.llm_client import LlmClient, TOOL_SCHEMAS` so the annotation resolves (or annotate with a string). Remove the `role`/`personality`/`spending_profile`/`target_players` attributes.

- [ ] **Step 3: Update `_build_bidders`**

```python
def _build_bidders(
    configs: list[dict[str, Any]],
    seed: int | None,
    llm_config: dict[str, Any] | None = None,
    run_dir: Path | None = None,
    budget: int = 500,
):
    if not configs:
        raise ValueError("At least one buyer must be configured")
    ...
        elif strategy == "llm":
            if llm_client is None:
                # Constructed once and shared: httpx clients are thread-safe.
                llm_client = _make_llm_client(llm_config or {})
            if run_dir is None:
                raise ValueError("A trace run_dir is required for LLM bidders")
            merged = {**(llm_config or {}), **(config.get("llm") or {})}
            bidders.append(
                CoachAgent(
                    buyer_id,
                    name,
                    client=llm_client,
                    tracer=TraceLogger(run_dir, buyer_id),
                    model=str(merged["model"]),
                    temperature=float(merged.get("temperature", 0.7)),
                    system_prompt=render_system_prompt(
                        budget=budget,
                        profile=config.get("profile"),
                        role=merged.get("role"),
                        personality=merged.get("personality"),
                        spending_profile=merged.get("spending_profile"),
                        target_players=merged.get("target_players"),
                        override=merged.get("system_prompt"),
                    ),
                    max_tool_iterations=int(merged.get("max_tool_iterations", 3)),
                    tools=tuple(merged.get("tools", CoachAgent.DEFAULT_TOOLS)),
                )
            )
```

Add `from agents.coach_loader import load_buyer_configs` and `from agents.coach_prompt import render_system_prompt` to `main.py`.

- [ ] **Step 4: Sidecar stores the resolved prompt**

```python
def _write_llm_sidecar(
    checkpoint_path: Path,
    buyer_configs: list[dict[str, Any]],
    llm_config: dict[str, Any],
    budget: int,
) -> Path | None:
    """Write the LLM sidecar next to a checkpoint; None when no llm buyer."""
    llm_buyers = [
        buyer
        for buyer in buyer_configs
        if str(buyer.get("strategy", "")).lower() == "llm"
    ]
    if not llm_buyers:
        return None
    resolved: dict[str, dict[str, Any]] = {}
    for buyer in llm_buyers:
        buyer_llm = buyer.get("llm") or {}
        merged = {**(llm_config or {}), **buyer_llm}
        resolved[str(buyer["id"])] = {
            "llm": {
                **buyer_llm,
                "system_prompt": render_system_prompt(
                    budget=budget,
                    profile=buyer.get("profile"),
                    role=merged.get("role"),
                    personality=merged.get("personality"),
                    spending_profile=merged.get("spending_profile"),
                    target_players=merged.get("target_players"),
                    override=merged.get("system_prompt"),
                ),
            }
        }
    payload = {
        "schema_version": 1,
        "llm": llm_config,
        "buyers": resolved,
    }
    path = _sidecar_path(checkpoint_path)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 5: Update call sites**

Non-resume branch in `main()`:
```python
            buyer_configs = load_buyer_configs(config)
            ...
            bidders = _build_bidders(
                buyer_configs,
                seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(paths.get("logs")),
                budget=budget,
            )
```

Resume branch:
```python
            bidders = _build_bidders(
                buyer_configs,
                source.simulation.seed,
                llm_config=llm_config,
                run_dir=_trace_run_dir(None),
                budget=source.simulation.budget,
            )
```

Exhaustion handler:
```python
                sidecar_path = _write_llm_sidecar(
                    saved,
                    buyer_configs,
                    llm_config,
                    simulation_snapshot.budget,
                )
```

`benchmark/runner.py`: import `load_buyer_configs`, replace `buyer_configs = list(config.get("buyers", []))` with `buyer_configs = load_buyer_configs(config)`, and pass `budget=budget` in the `build_bidders(...)` call.

- [ ] **Step 6: Update `tests/test_coach_agent.py`**

`make_manager` now passes a prompt:
```python
def make_manager(tmp_path, client, **kwargs):
    tracer = TraceLogger(tmp_path / "traces", "buyer_1")
    manager = CoachAgent(
        "buyer_1", "Alpha", client, tracer,
        model="gpt-4o-mini", temperature=0.7,
        system_prompt="Sei un coach di prova.", **kwargs,
    )
    return manager, tmp_path / "traces" / "buyer_1.jsonl"
```

Replace `test_custom_system_prompt_replaces_template` with:
```python
def test_system_prompt_is_sent_as_first_message(tmp_path):
    client = FakeClient([chat_response(tool_call("submit_bid", {"amount": 5}))])
    manager, _ = make_manager(tmp_path, client)

    manager.bid(make_player(), make_squad())

    assert client.messages_seen[0][0] == {
        "role": "system",
        "content": "Sei un coach di prova.",
    }
```

- [ ] **Step 7: Update `tests/test_cli.py` and add wiring tests**

`FakeLlmClient` records what it sees:
```python
class FakeLlmClient:
    """Scripted LlmClient replacement for CLI tests (no network)."""

    instances: list["FakeLlmClient"] = []

    def __init__(
        self,
        base_url,
        api_key,
        search=None,
        timeout_seconds=30,
        transport=None,
        extra_headers=None,
    ):
        ...
        self.messages_seen = []
        FakeLlmClient.instances.append(self)

    def chat(self, messages, tools, model, temperature):
        self.calls += 1
        self.messages_seen.append(messages)
        ...
```

Update the two sidecar assertions:
```python
def test_cli_llm_exhaustion_writes_checkpoint_and_sidecar(...):
    ...
    assert data["schema_version"] == 1
    assert data["llm"]["model"] == "gpt-4o-mini"
    assert data["llm"]["api_key_env"] == "TEST_LLM_API_KEY"
    assert "sk-" not in sidecar.read_text(encoding="utf-8")
    rendered = data["buyers"]["b1"]["llm"]["system_prompt"]
    assert rendered.startswith("Sei un allenatore-manager")
    assert "P: 3, D: 8, C: 8, A: 6" in rendered


def test_cli_second_exhaustion_propagates_sidecar(...):
    ...
    assert data["schema_version"] == 1
    assert data["buyers"]["incomplete"]["llm"] == {
        "temperature": 0.3,
        "system_prompt": "PROMPT DAL SIDECAR",
    }
```

For the second test, set the stored prompt before resuming: in `make_llm_checkpoint`, accept and write it, or overwrite the sidecar in the test:
```python
    checkpoint = make_llm_checkpoint(tmp_path, no_progress=True)
    payload = llm_sidecar_payload()
    payload["buyers"]["incomplete"]["llm"]["system_prompt"] = "PROMPT DAL SIDECAR"
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
```

New discovery test:
```python
def test_cli_discovers_coaches_and_writes_resolved_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    write_workbook(workbook, {"A": 1})
    coaches = tmp_path / "coaches"
    coaches.mkdir()
    (coaches / "coachAgent_Joe.md").write_text(
        "---\nmodel: gpt-4o-mini\ntemperature: 0.2\n---\n\nJoe è aggressivo.\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    data = base_llm_config(workbook)
    data.pop("buyers")
    data["paths"]["coaches"] = str(coaches)
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)
    checkpoint = tmp_path / "checkpoint.json"

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    sidecar = yaml.safe_load(
        (tmp_path / "checkpoint.llm.yaml").read_text(encoding="utf-8")
    )
    joe = sidecar["buyers"]["joe"]["llm"]
    assert joe["temperature"] == 0.2
    assert joe["system_prompt"].startswith("Sei un allenatore-manager")
    assert "Joe è aggressivo." in joe["system_prompt"]
    assert "P: 3, D: 8, C: 8, A: 6" in joe["system_prompt"]
```

New resume test:
```python
def test_cli_resume_uses_stored_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    FakeLlmClient.instances.clear()
    checkpoint = make_llm_checkpoint(tmp_path)
    payload = llm_sidecar_payload()
    payload["buyers"]["incomplete"]["llm"]["system_prompt"] = "PROMPT DAL SIDECAR"
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(tmp_path / "report.json"),
    ])

    assert exit_code == 0
    prompts = [
        client.messages_seen[0][0]["content"]
        for client in FakeLlmClient.instances
    ]
    assert prompts == ["PROMPT DAL SIDECAR"]
```

New validation tests:
```python
def test_cli_rejects_empty_buyers_without_coaches(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = base_llm_config(workbook)
    data["buyers"] = []
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("when 'paths.coaches' is not set" in error for error in errors)


def test_cli_rejects_duplicate_buyer_and_coach_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    write_workbook(workbook, {"A": 1})
    coaches = tmp_path / "coaches"
    coaches.mkdir()
    (coaches / "coachAgent_b1.md").write_text("body", encoding="utf-8")
    config = tmp_path / "config.yaml"
    data = base_llm_config(workbook)
    data["paths"]["coaches"] = str(coaches)
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("Duplicate buyer ids" in error for error in errors)
```

- [ ] **Step 8: Add the benchmark coaches test**

```python
def test_benchmark_with_coaches_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    coaches = tmp_path / "coaches"
    coaches.mkdir()
    (coaches / "coachAgent_Joe.md").write_text(
        "---\nmodel: gpt-4o-mini\n"
        "spending_profile: {P: 0.1, D: 0.2, C: 0.3, A: 0.4}\n---\n\nJoe.\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    data = base_llm_config(workbook)
    data.pop("buyers")
    data["paths"]["coaches"] = str(coaches)
    write_raw_config(config, data)
    root = tmp_path / "bench"

    exit_code = main([
        "benchmark",
        "--config", str(config),
        "--runs", "1",
        "--seed", "42",
        "--output", str(root),
    ])

    assert exit_code == 0
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["aggregates"]["joe"]["roster_complete"]["mean"] == 1.0
    assert metrics["runs"][0]["buyers"]["joe"]["model"] == "gpt-4o-mini"
```

- [ ] **Step 9: Rewrite `configs/llm.yaml` and add example coaches**

`configs/llm.yaml`:
```yaml
# Configurazione di esempio per aste con CoachAgent.
# I coach vivono in agents/coachAgent_*.md: crea o modifica un file
# markdown per aggiungere un agente, senza toccare questo YAML.
#
# Uso:
#   venv/bin/python main.py --config configs/llm.yaml

simulation:
  budget: 500
  seed: 42

paths:
  players: "data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx"
  output: "data/results/report.json"
  checkpoint: "data/checkpoints/checkpoint.json"
  logs: "logs"
  coaches: "agents"

llm:
  base_url: "https://opencode.ai/zen/go/v1"
  api_key_env: "OPENCODE_API_KEY"
  model: "glm-5.3"
  temperature: 0.7
  timeout_seconds: 30
  search:
    provider: "responses"
    model: "gpt-5.6-luna"
```

`agents/coachAgent_Alfa.md`:
```markdown
---
model: "glm-5.3"
temperature: 0.7
spending_profile:
  P: 0.08
  D: 0.20
  C: 0.35
  A: 0.37
target_players:
  - "Lautaro Martínez"
---

Sei il coach della **Squadra Alfa**.

Stile strategico: prudente.
Tolleranza al rischio: bassa.
Priorità: completare una rosa equilibrata senza pagare molto sopra il valore.
Tendenza all'asta: evita di inseguire i giocatori e conserva budget per le fasi successive.
```

`agents/coachAgent_Beta.md`: front-matter `temperature: 0.9`, profile P 0.05 / D 0.15 / C 0.30 / A 0.50, target `Marcus Thuram`; body: coach Squadra Beta, stile aggressivo, tolleranza alta, disposto a concentrare il budget sui titolari di fascia alta mantenendo possibile il completamento della rosa.

`agents/coachAgent_Gamma.md`: front-matter `temperature: 0.3`, profile P 0.06 / D 0.24 / C 0.35 / A 0.35, target `Pulisic`; body: coach Squadra Gamma, stile equilibrato, cerca valore in ogni ruolo senza forzare.

`agents/coachAgent_Delta.md`: front-matter `temperature: 1.0`, profile P 0.07 / D 0.18 / C 0.30 / A 0.45, target `Calhanoglu`; body: coach Squadra Delta, stile opportunista, attento alle occasioni sottovalutate dagli avversari.

- [ ] **Step 10: Verify and commit**

Run: `venv/bin/pytest -q -W error`
Expected: all green (193 + 5 new tests + 1 updated).

```bash
git add utils/config_loader.py agents/coach_agent.py main.py benchmark/runner.py \
  tests/test_coach_agent.py tests/test_cli.py tests/test_benchmark.py \
  configs/llm.yaml agents/coachAgent_Alfa.md agents/coachAgent_Beta.md \
  agents/coachAgent_Gamma.md agents/coachAgent_Delta.md
git commit -m "feat(llm): discover coaches from agents directory and resolve prompts end to end"
```

---

### Task 5: Domain-validated bids and console reasoning

**Files:**
- Modify: `agents/coach_agent.py`
- Test: `tests/test_coach_agent.py`

**Interfaces:**
- Consumes: `core.models.Squad.validate_bid`, `core.models.BidValidationError`.
- Produces: unchanged `bid()` return contract; invalid offers now round-trip through the domain message.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coach_agent.py` (imports at top: add `from loguru import logger`):

```python
def test_submit_bid_out_of_range_self_corrects(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 9999})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, _ = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 10

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert "offerta rifiutata" in tool_messages[0]["content"]
    assert "legal maximum" in tool_messages[0]["content"]


def test_wrong_type_bid_self_corrects(tmp_path):
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": "10"})),
        chat_response(tool_call("submit_bid", {"amount": 10})),
    ])
    manager, _ = make_manager(tmp_path, client)

    assert manager.bid(make_player(), make_squad()) == 10

    tool_messages = [m for m in client.messages_seen[1] if m["role"] == "tool"]
    assert "Python int" in tool_messages[0]["content"]


def test_unfixable_role_bid_exhausts_iterations(tmp_path):
    squad = make_squad()
    for index in range(6):
        player = Player(
            id=f"a{index}", name=f"Attaccante {index}",
            position=Position.A, team="Inter", list_price=1,
        )
        squad.add_player(player, 1)
    client = FakeClient([
        chat_response(tool_call("submit_bid", {"amount": 1})),
        chat_response(tool_call("submit_bid", {"amount": 0})),
    ])
    manager, trace_path = make_manager(tmp_path, client, max_tool_iterations=2)

    assert manager.bid(make_player(), squad) == 0

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["content"] == {"reason": "iteration_cap"}


def test_reasoning_is_logged(tmp_path):
    client = FakeClient([
        chat_response(
            tool_call("submit_bid", {"amount": 5}),
            content="Stimo 5 crediti.",
        )
    ])
    manager, _ = make_manager(tmp_path, client)
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="INFO", format="{message}")
    try:
        manager.bid(make_player(), make_squad())
    finally:
        logger.remove(sink_id)

    assert any("Stimo 5 crediti." in str(message) for message in messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_coach_agent.py -q -W error`
Expected: FAIL on `"offerta rifiutata"` (current message is `amount non valido`) and on the missing log line.

- [ ] **Step 3: Implement the changes**

Imports in `agents/coach_agent.py`:
```python
from loguru import logger

from agents.llm_client import TOOL_SCHEMAS
from agents.trace import TraceLogger
from core.models import BidValidationError, Player, Squad
```

In the thinking block of `bid()`:
```python
            if response["content"]:
                self.tracer.event(
                    player.id, "thinking", iteration, {"text": response["content"]}
                )
                logger.info(
                    "Coach {} su {}: {}",
                    self.buyer_id,
                    player.name,
                    response["content"].strip(),
                )
```

In the tool-call loop, replace the range check with domain validation:
```python
                if name == "submit_bid":
                    amount = args.get("amount")
                    try:
                        squad.validate_bid(player, amount)
                    except BidValidationError as exc:
                        result = f"offerta rifiutata: {exc}"
                    else:
                        self.tracer.event(
                            player.id, "bid", iteration, {"amount": amount}
                        )
                        return amount
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest tests/test_coach_agent.py -q -W error`
Expected: all pass.

- [ ] **Step 5: Full suite and commit**

Run: `venv/bin/pytest -q -W error`
Expected: all green.

```bash
git add agents/coach_agent.py tests/test_coach_agent.py
git commit -m "feat(llm): validate bids against domain rules inside the coach loop"
```

---

### Task 6: Outcome notifications (`observe`)

**Files:**
- Modify: `agents/base_agent.py`
- Modify: `agents/buyer_agent.py`
- Modify: `agents/coach_agent.py`
- Modify: `core/auction_manager.py`
- Modify: `tests/test_auction_manager.py`
- Modify: `tests/test_resume.py`
- Test: `tests/test_coach_agent.py`

**Interfaces:**
- Produces: `Bidder.observe(result: AuctionResult, squad: Squad) -> None`; `AuctionEngine._collect_bids` returns `(bids, active_ids)`; `CoachAgent.observe` writes the `auction_result` trace event.
- Consumes: `core.models.AuctionResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coach_agent.py` (add `AuctionResult`, `AuctionStatus` to the `core.models` import):

```python
def test_observe_winner_traces_updated_roster(tmp_path):
    manager, trace_path = make_manager(tmp_path, FakeClient([]))
    player = make_player()
    squad = make_squad()
    squad.add_player(player, 7)
    result = AuctionResult(
        player=player.model_copy(deep=True),
        winner_id="buyer_1",
        price=7,
        all_bids={"buyer_1": 7},
        status=AuctionStatus.SOLD,
    )

    manager.observe(result, squad)

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "auction_result"
    assert last["content"]["outcome"] == "won"
    assert last["content"]["price"] == 7
    assert last["content"]["budget_remaining"] == 493
    assert last["content"]["roster"] == [
        {"id": "pl_1", "name": "Lautaro", "position": "A"}
    ]
    assert last["content"]["missing_roles"]["A"] == 5


def test_observe_loser_is_minimal(tmp_path):
    manager, trace_path = make_manager(tmp_path, FakeClient([]))
    result = AuctionResult(
        player=make_player().model_copy(deep=True),
        winner_id="other",
        price=3,
        all_bids={"buyer_1": 0, "other": 3},
        status=AuctionStatus.SOLD,
    )

    manager.observe(result, make_squad())

    last = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["phase"] == "auction_result"
    assert last["content"] == {"outcome": "lost"}
```

Add to `tests/test_auction_manager.py` (imports: add `AuctionState`, `Squad`):

```python
class RecordingBidder:
    def __init__(self, buyer_id: str, bid_value: int):
        self.buyer_id = buyer_id
        self.name = buyer_id
        self.bid_value = bid_value
        self.observed = []

    def bid(self, player, squad):
        return self.bid_value

    def observe(self, result, squad):
        self.observed.append(
            {
                "player": result.player.id,
                "winner": result.winner_id,
                "squad": squad.buyer_id,
            }
        )


def test_engine_notifies_winner_and_losers():
    players = [make_player("p1", "A")]
    winner = RecordingBidder("winner", 5)
    loser = RecordingBidder("loser", 3)
    engine = AuctionEngine(players, [winner, loser], budget=30, seed=1)

    engine.auction_player(players[0])

    assert winner.observed == [
        {"player": "p1", "winner": "winner", "squad": "winner"}
    ]
    assert loser.observed == [
        {"player": "p1", "winner": "winner", "squad": "loser"}
    ]


def test_engine_skips_notification_for_full_roles():
    players = [make_player(f"a{index}", "A") for index in range(7)]
    state = AuctionState(players=players, squads={})
    full = Squad(buyer_id="full", name="Full", budget_initial=30)
    for player in players[:6]:
        full.add_player(player.model_copy(deep=True), 1)
    state.squads["full"] = full
    state.squads["other"] = Squad(buyer_id="other", name="Other", budget_initial=30)
    full_bidder = RecordingBidder("full", 5)
    other_bidder = RecordingBidder("other", 5)
    engine = AuctionEngine(
        players, [full_bidder, other_bidder], budget=30, seed=1, state=state
    )

    engine.auction_player(players[6])

    assert full_bidder.observed == []
    assert len(other_bidder.observed) == 1
```

Add a no-op `observe` to `ZeroBidder`, `FixedBidder`, `RaisingBidder`, `SlowBidder` in `tests/test_auction_manager.py` and to `RecordingBidder` in `tests/test_resume.py`:

```python
    def observe(self, result, squad):
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_coach_agent.py tests/test_auction_manager.py -q -W error`
Expected: FAIL with `AttributeError: 'CoachAgent' object has no attribute 'observe'`.

- [ ] **Step 3: Extend the bidder contracts**

`agents/base_agent.py`:
```python
"""Common bidder contract used by the auction engine."""

from typing import Protocol

from core.models import AuctionResult, Player, Squad


class Bidder(Protocol):
    buyer_id: str
    name: str

    def bid(self, player: Player, squad: Squad) -> int:
        """Return zero to pass or a positive legal bid candidate."""
        ...

    def observe(self, result: AuctionResult, squad: Squad) -> None:
        """Receive the auction outcome after the engine resolved it."""
        ...
```

`agents/buyer_agent.py`: add `AuctionResult` to the import and to both classes:
```python
    def observe(self, result: AuctionResult, squad: Squad) -> None:
        """Deterministic bidders ignore auction outcomes."""
```

- [ ] **Step 4: Implement `CoachAgent.observe`**

In `agents/coach_agent.py` (import `AuctionResult` in the `core.models` line):

```python
    def observe(self, result: AuctionResult, squad: Squad) -> None:
        won = result.winner_id == self.buyer_id
        if won:
            content: dict = {
                "outcome": "won",
                "player": {
                    "id": result.player.id,
                    "name": result.player.name,
                    "position": result.player.position.value,
                    "team": result.player.team,
                },
                "price": result.price,
                "budget_remaining": squad.budget_remaining,
                "roster": [
                    {
                        "id": owned.id,
                        "name": owned.name,
                        "position": owned.position.value,
                    }
                    for owned in squad.players
                ],
                "missing_roles": squad.missing_roles(),
            }
            logger.info(
                "Coach {} ha vinto {} per {} crediti",
                self.buyer_id,
                result.player.name,
                result.price,
            )
        else:
            content = {"outcome": "lost"}
            logger.info(
                "Coach {} non ha vinto {}",
                self.buyer_id,
                result.player.name,
            )
        self.tracer.event(result.player.id, "auction_result", content=content)
```

- [ ] **Step 5: Notify from the engine**

In `core/auction_manager.py`, change `_collect_bids` to track active bidders:

```python
    def _collect_bids(self, player: Player) -> tuple[dict[str, int], list[str]]:
        bids: dict[str, int] = {}
        active: list[str] = []
        for bidder in self.bidders:
            squad = self.state.squads[bidder.buyer_id]
            eligible = not squad.is_complete and squad.remaining_for(player.position) > 0
            if not eligible:
                bids[bidder.buyer_id] = 0
                continue

            active.append(bidder.buyer_id)
            try:
                bid = bidder.bid(player, squad)
            except Exception as exc:
                self._record_bid_issue(...)
                bids[bidder.buyer_id] = 0
                continue

            try:
                squad.validate_bid(player, bid)
            except BidValidationError as exc:
                self._record_bid_issue(player, bidder, exc.code, str(exc))
                bids[bidder.buyer_id] = 0
                continue

            bids[bidder.buyer_id] = bid
        return bids, active
```

Add:
```python
    def _notify(self, result: AuctionResult, active_ids: list[str]) -> None:
        for bidder in self.bidders:
            if bidder.buyer_id in active_ids:
                bidder.observe(result, self.state.squads[bidder.buyer_id])
```

Restructure `auction_player` to a single exit (`bids, active_ids = self._collect_bids(player)`, then the existing three branches setting `result`, then `self._notify(result, active_ids)` and `return result`); keep all existing log lines and the exact same `AuctionResult` fields.

- [ ] **Step 6: Run the tests**

Run: `venv/bin/pytest tests/test_coach_agent.py tests/test_auction_manager.py tests/test_resume.py -q -W error`
Expected: all pass.

- [ ] **Step 7: Full suite and commit**

Run: `venv/bin/pytest -q -W error`
Expected: all green.

```bash
git add agents/base_agent.py agents/buyer_agent.py agents/coach_agent.py \
  core/auction_manager.py tests/test_auction_manager.py tests/test_resume.py \
  tests/test_coach_agent.py
git commit -m "feat(agents): notify bidders of auction outcomes via observe hook"
```

---

### Task 7: Documentation

**Files:**
- Modify: `AGENTS.md` (currently untracked; add it here)
- Modify: `docs/project.md`
- Modify: `docs/roadmap.md`
- Delete: `agents/prompt.md`

- [ ] **Step 1: Update `AGENTS.md`**

- Architecture bullet: `agents/` — add `coach_loader.py`, `coach_prompt.py`, `prompts/common.md`, `coachAgent_*.md`; rename `llm_agent.py` → `coach_agent.py` + `llm_client.py`.
- "LLM bidders" section: rename to CoachAgent; explain discovery via `paths.coaches` (example `agents/`), front-matter + profile body, common prompt, domain-validated bid retry, `auction_result` observe events, sidecar stores the resolved system prompt (schema stays 1).
- Config table in `docs/project.md`: add `paths.coaches` (optional string; the directory is only scanned when set).

- [ ] **Step 2: Update `docs/project.md`**

- Status bullets: CoachAgent naming, discovery, common prompt, observe.
- Configuration contract table: `paths.coaches`.
- "Traces and the LLM sidecar": mention `auction_result` events and the resolved `system_prompt` in the sidecar.
- Project structure tree: new files and example coaches.

- [ ] **Step 3: Update `docs/roadmap.md`**

- Move "configurable prompt architecture" from backlog into P4-style description marked complete after this change; note the deferred per-agent memory + LLM outcome reactions as the next candidate.

- [ ] **Step 4: Remove the absorbed design file**

```bash
git rm --cached agents/prompt.md 2>/dev/null || true
rm agents/prompt.md
```

(`agents/prompt.md` is untracked, so a plain `rm` is enough.)

- [ ] **Step 5: Verify and commit**

Run: `venv/bin/pytest -q -W error`
Expected: all green.

```bash
git add AGENTS.md docs/project.md docs/roadmap.md
git commit -m "docs: document CoachAgent discovery, prompt, and outcome events"
```

---

### Task 8: Final verification

- [ ] **Step 1: Full suite with warnings as errors**

Run: `venv/bin/pytest -q -W error`
Expected: all tests pass, zero warnings.

- [ ] **Step 2: Whitespace/conflict check**

Run: `git diff --check origin/dev...HEAD`
Expected: no output.

- [ ] **Step 3: Optional live smoke test (manual, needs `OPENCODE_API_KEY`)**

Run: `venv/bin/python main.py --config configs/llm.yaml`
Expected: either a complete auction or a resumable checkpoint; `agents/` example coaches discovered; trace files contain `context`, `thinking`, `auction_result` events. Skip if the key is unavailable and report that.

- [ ] **Step 4: Report**

Summarize: test count, commits per task, any deviation from the plan.
