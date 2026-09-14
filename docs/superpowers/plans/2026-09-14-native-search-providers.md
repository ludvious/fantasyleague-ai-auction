# Native Web Search Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Brave-only `search_news` backend in `LlmClient` with config-driven native web search supporting `responses` (OpenCode Go / OpenAI), `anthropic`, and legacy `brave` providers.

**Architecture:** Provider dispatch stays inside `LlmClient.search_news` (three branches, no new files/classes). The bidding loop (`chat/completions`) is untouched. `main.py` resolves `llm.search` (or legacy `llm.brave`) into one search dict with the API key already read from the environment, and passes client-wide default headers (User-Agent, `x-opencode-session`).

**Tech Stack:** Python 3.11 (CI pin; local venv is 3.14), httpx (MockTransport for tests), pytest, PyYAML. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-14-native-search-providers-design.md`

## Global Constraints

- Run `venv/bin/pytest -q -W error` before every commit; all tests green (baseline: 153).
- Python 3.11-compatible code only.
- API keys appear only as env-var *names* in configs; a literal `api_key` field is rejected by validation; keys are read via `os.environ` only.
- Version-1 report/checkpoint/sidecar formats are unchanged: `schema_version` stays 1, `llm.brave` stays accepted as legacy so existing sidecars resume without a bump.
- Conventional commits on the `dev` branch (e.g. `feat(llm):`, `refactor:`, `test:`, `docs:`). Never sweep unrelated files into a commit — `git add` explicit paths.
- No comments in code unless a `ponytail:` deliberate-shortcut note is needed.
- Repo root: `/home/ludovg/projects/fantasyleague-ai-auction` (all paths below are relative to it).

---

### Task 1: LlmClient takes a search dict; brave becomes a provider branch

**Files:**
- Modify: `agents/llm_agent.py:52-144` (constructor + `search_news`)
- Modify: `main.py:39-55` (`_make_llm_client`)
- Modify: `tests/test_llm_client.py` (helper + brave tests)
- Modify: `tests/test_cli.py:628-655` (`FakeLlmClient` signature)

**Interfaces:**
- Consumes: today's `LlmClient(base_url, api_key, brave_base_url, brave_api_key, timeout_seconds=30, transport=None)`.
- Produces: `LlmClient(base_url: str, api_key: str, search: dict | None = None, timeout_seconds: int = 30, transport: httpx.BaseTransport | None = None)`. The `search` dict has resolved values (no env names): `{"provider": "brave", "base_url": str, "api_key": str}`. `search_news(query: str, count: int) -> str` signature unchanged; returns `"search non disponibile"` when `search` is None, the key is empty, or the key equals `MOCK_BRAVE_KEY`.

- [ ] **Step 0: Commit the pre-existing system-prompt wording WIP separately**

The working tree has a 2-line wording change in `agents/llm_agent.py` from a previous session. Commit it alone so feature commits stay clean:

```bash
git add agents/llm_agent.py
git commit -m "feat(llm): refine generated system prompt wording"
```

- [ ] **Step 1: Write the failing tests (rewrite brave tests onto the search dict)**

In `tests/test_llm_client.py`, replace the `make_client` helper and the three brave tests:

```python
def make_client(handler, search=None):
    return LlmClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        search=search,
        transport=httpx.MockTransport(handler),
    )


def brave_search(**overrides):
    config = {
        "provider": "brave",
        "base_url": "https://brave.test/search",
        "api_key": "brave-key",
    }
    config.update(overrides)
    return config


def test_search_news_returns_unavailable_message_for_mock_key():
    client = make_client(
        lambda request: httpx.Response(200, json={}),
        search=brave_search(api_key=MOCK_BRAVE_KEY),
    )
    assert client.search_news("Lautaro infortunio", 3) == "search non disponibile"


def test_search_news_returns_unavailable_message_without_search_config():
    client = make_client(lambda request: httpx.Response(200, json={}))
    assert client.search_news("Lautaro infortunio", 3) == "search non disponibile"


def test_search_news_formats_top_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "brave.test"
        assert request.headers["X-Subscription-Token"] == "brave-key"
        return httpx.Response(200, json={
            "web": {"results": [
                {"title": "Notizia 1", "url": "https://example.com/1"},
                {"title": "Notizia 2", "url": "https://example.com/2"},
            ]}
        })

    client = make_client(handler, search=brave_search())
    result = client.search_news("Lautaro infortunio", 2)

    assert "Notizia 1" in result
    assert "https://example.com/1" in result


def test_search_news_returns_unavailable_message_on_http_error():
    def handler(request):
        return httpx.Response(503, json={})

    client = make_client(handler, search=brave_search())
    assert client.search_news("Lautaro", 2) == "search non disponibile"
```

In `tests/test_cli.py`, update `FakeLlmClient.__init__` (it must match how `main.py` will call `LlmClient`):

```python
    def __init__(
        self,
        base_url,
        api_key,
        search=None,
        timeout_seconds=30,
        transport=None,
    ):
        self.calls = 0
        self.search = search
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_llm_client.py -q -W error`
Expected: FAIL — `LlmClient.__init__() got an unexpected keyword argument 'search'`.

- [ ] **Step 3: Implement the constructor swap and the brave branch**

In `agents/llm_agent.py`, replace the constructor and `search_news`:

```python
class LlmClient:
    """One shared httpx client for chat completions and web search."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        search: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("LLM API key must be a non-empty string")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
        )
        self._api_key = api_key
        self._search = search
```

(`chat` stays exactly as is.)

```python
    def search_news(self, query: str, count: int) -> str:
        """Best-effort web search; returns an Italian tool message."""
        search = self._search
        if (
            not search
            or not search.get("api_key")
            or search.get("api_key") == MOCK_BRAVE_KEY
        ):
            return "search non disponibile"
        provider = str(search.get("provider", ""))
        try:
            if provider == "responses":
                return self._search_responses(query, count)
            if provider == "anthropic":
                return self._search_anthropic(query, count)
            return self._search_brave(query, count)
        except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError):
            return "search non disponibile"

    def _search_brave(self, query: str, count: int) -> str:
        response = self._http.get(
            self._search["base_url"],
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": self._search["api_key"]},
        )
        response.raise_for_status()
        payload = response.json()
        results = (payload.get("web") or {}).get("results") or []
        lines = [
            f"{index + 1}. {result.get('title', '')} — {result.get('url', '')}"
            for index, result in enumerate(results)
        ]
        return "\n".join(lines) if lines else "nessun risultato"
```

Add stubs so the file imports and the dispatcher is complete (Tasks 2-3 fill them):

```python
    def _search_responses(self, query: str, count: int) -> str:
        raise NotImplementedError

    def _search_anthropic(self, query: str, count: int) -> str:
        raise NotImplementedError
```

In `main.py`, replace `_make_llm_client` body after the api_key check:

```python
def _make_llm_client(llm_config: dict[str, Any]) -> LlmClient:
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Environment variable '{api_key_env}' (llm.api_key_env) is not set; "
            "set it before running an auction with LLM bidders"
        )
    search = None
    brave = llm_config.get("brave")
    if brave is not None:
        search = {
            "provider": "brave",
            "base_url": str(brave["base_url"]),
            "api_key": os.environ.get(str(brave["api_key_env"]), ""),
        }
    return LlmClient(
        base_url=str(llm_config["base_url"]),
        api_key=api_key,
        search=search,
        timeout_seconds=int(llm_config.get("timeout_seconds", 30)),
    )
```

(Do not read `llm.search` yet — its validation and provider branches land in Tasks 2-4; at this point a config containing `search` must keep behaving as today, i.e. the field is ignored.)

- [ ] **Step 4: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (154 tests; legacy `llm.brave` configs flow through the brave branch).

- [ ] **Step 5: Commit**

```bash
git add agents/llm_agent.py main.py tests/test_llm_client.py tests/test_cli.py
git commit -m "refactor(llm): pass search config as a dict to LlmClient"
```

---

### Task 2: `responses` provider (OpenCode Go / OpenAI)

**Files:**
- Modify: `agents/llm_agent.py` (fill `_search_responses`, add `SEARCH_PROMPT` + `_format_search_result`)
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: search dict extended by wiring (Task 4) with optional `model`, `max_output_tokens`, `headers` keys; in this task tests pass them directly.
- Produces: module-level `SEARCH_PROMPT` (str, `{query}` placeholder) and `_format_search_result(summary: str, sources: list[tuple[str, str]], count: int) -> str` used by both native providers. Output format: summary text, blank line, `Fonti:`, then numbered `title — url` lines deduped by URL and sliced to `count`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_client.py`:

```python
def responses_payload():
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "web_search_call",
                "action": {"type": "search", "query": "Lautaro"},
            },
            {
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Lautaro è in forma e titolare.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "title": "Inter Match Center",
                                "url": "https://www.inter.it/it/match",
                            },
                            {
                                "type": "url_citation",
                                "title": "Inter Match Center (dup)",
                                "url": "https://www.inter.it/it/match",
                            },
                            {
                                "type": "url_citation",
                                "title": "Fantacalcio.it",
                                "url": "https://www.fantacalcio.it/news",
                            },
                        ],
                    }
                ],
            },
        ],
    }


def responses_search(**overrides):
    config = {
        "provider": "responses",
        "base_url": "https://search.test",
        "api_key": "search-key",
        "model": "gpt-5.6-luna",
    }
    config.update(overrides)
    return config


def test_search_news_responses_posts_web_search_tool_and_formats():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "search.test"
        assert request.url.path == "/responses"
        assert request.headers["Authorization"] == "Bearer search-key"
        assert body["model"] == "gpt-5.6-luna"
        assert body["tools"] == [{"type": "web_search"}]
        assert "Lautaro infortunio" in body["input"]
        assert body["max_output_tokens"] == 400
        return httpx.Response(200, json=responses_payload())

    client = make_client(handler, search=responses_search())
    result = client.search_news("Lautaro infortunio", 5)

    assert result.startswith("Lautaro è in forma e titolare.")
    assert "Fonti:" in result
    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "2. Fantacalcio.it — https://www.fantacalcio.it/news" in result
    assert "dup" not in result


def test_search_news_responses_slices_sources_to_count():
    def handler(request):
        return httpx.Response(200, json=responses_payload())

    client = make_client(handler, search=responses_search())
    result = client.search_news("Lautaro", 1)

    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "fantacalcio.it" not in result


def test_search_news_responses_without_message_returns_no_results():
    def handler(request):
        payload = responses_payload()
        payload["output"] = [payload["output"][0]]
        return httpx.Response(200, json=payload)

    client = make_client(handler, search=responses_search())
    assert client.search_news("Lautaro", 5) == "nessun risultato"


def test_search_news_responses_on_http_error_returns_unavailable():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler, search=responses_search())
    assert client.search_news("Lautaro", 5) == "search non disponibile"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_llm_client.py -q -W error`
Expected: FAIL — `NotImplementedError` (or KeyError on `self._search["model"]`).

- [ ] **Step 3: Implement `_search_responses` plus shared helpers**

In `agents/llm_agent.py`, add above `class LlmClient`:

```python
SEARCH_PROMPT = (
    "Cerca notizie recenti su: {query}. Riassumi brevemente in italiano le "
    "informazioni utili per un'asta di fantacalcio (infortuni, forma, "
    "titolarità, mercato)."
)


def _format_search_result(
    summary: str, sources: list[tuple[str, str]], count: int
) -> str:
    unique = list(dict.fromkeys(sources))[: max(1, count)]
    lines = []
    if summary:
        lines.append(summary)
    if unique:
        lines.append("")
        lines.append("Fonti:")
        lines.extend(
            f"{index}. {title} — {url}"
            for index, (title, url) in enumerate(unique, start=1)
        )
    return "\n".join(lines)
```

Replace the `_search_responses` stub:

```python
    def _search_responses(self, query: str, count: int) -> str:
        response = self._http.post(
            f"{self._search['base_url']}/responses",
            headers={
                "Authorization": f"Bearer {self._search['api_key']}",
                **(self._search.get("headers") or {}),
            },
            json={
                "model": self._search["model"],
                "input": SEARCH_PROMPT.format(query=query),
                "tools": [{"type": "web_search"}],
                "max_output_tokens": int(
                    self._search.get("max_output_tokens", 400)
                ),
            },
        )
        response.raise_for_status()
        payload = response.json()
        summary_parts = []
        sources: list[tuple[str, str]] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                summary_parts.append(str(part.get("text") or ""))
                for annotation in part.get("annotations") or []:
                    if (
                        annotation.get("type") == "url_citation"
                        and annotation.get("url")
                    ):
                        sources.append(
                            (
                                str(annotation.get("title") or ""),
                                str(annotation["url"]),
                            )
                        )
        summary = "\n".join(part for part in summary_parts if part).strip()
        if not summary and not sources:
            return "nessun risultato"
        return _format_search_result(summary, sources, count)
```

- [ ] **Step 4: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (158 tests).

- [ ] **Step 5: Commit**

```bash
git add agents/llm_agent.py tests/test_llm_client.py
git commit -m "feat(llm): native web search via responses provider"
```

---

### Task 3: `anthropic` provider (Messages API server tool)

**Files:**
- Modify: `agents/llm_agent.py` (fill `_search_anthropic`)
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: same search dict keys as `responses`; request goes to `POST {base_url}/v1/messages` with `x-api-key` + `anthropic-version: 2023-06-01` headers and tool `{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}`.
- Produces: same summary+`Fonti:` output via the shared `_format_search_result`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_client.py`:

```python
def anthropic_payload():
    return {
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_01",
                "name": "web_search",
                "input": {"query": "Lautaro"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_01",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "Inter Match Center",
                        "url": "https://www.inter.it/it/match",
                    },
                    {
                        "type": "web_search_result",
                        "title": "Fantacalcio.it",
                        "url": "https://www.fantacalcio.it/news",
                    },
                ],
            },
            {
                "type": "text",
                "text": "Lautaro è in forma e titolare.",
            },
        ]
    }


def anthropic_search(**overrides):
    config = {
        "provider": "anthropic",
        "base_url": "https://anthropic.test",
        "api_key": "anthropic-key",
        "model": "claude-opus-4-8",
    }
    config.update(overrides)
    return config


def test_search_news_anthropic_posts_server_tool_and_formats():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "anthropic.test"
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert body["model"] == "claude-opus-4-8"
        assert body["max_tokens"] == 400
        assert body["tools"] == [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
        ]
        assert "Lautaro infortunio" in body["messages"][0]["content"]
        return httpx.Response(200, json=anthropic_payload())

    client = make_client(handler, search=anthropic_search())
    result = client.search_news("Lautaro infortunio", 5)

    assert result.startswith("Lautaro è in forma e titolare.")
    assert "Fonti:" in result
    assert "1. Inter Match Center — https://www.inter.it/it/match" in result
    assert "2. Fantacalcio.it — https://www.fantacalcio.it/news" in result


def test_search_news_anthropic_on_http_error_returns_unavailable():
    def handler(request):
        return httpx.Response(401, json={"error": "no key"})

    client = make_client(handler, search=anthropic_search())
    assert client.search_news("Lautaro", 5) == "search non disponibile"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_llm_client.py -q -W error`
Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Implement `_search_anthropic`**

Replace the stub in `agents/llm_agent.py`:

```python
    def _search_anthropic(self, query: str, count: int) -> str:
        response = self._http.post(
            f"{self._search['base_url']}/v1/messages",
            headers={
                "x-api-key": self._search["api_key"],
                "anthropic-version": "2023-06-01",
                **(self._search.get("headers") or {}),
            },
            json={
                "model": self._search["model"],
                "max_tokens": int(
                    self._search.get("max_output_tokens", 400)
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": SEARCH_PROMPT.format(query=query),
                    }
                ],
                "tools": [
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 1,
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        summary_parts = []
        sources: list[tuple[str, str]] = []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                summary_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "web_search_tool_result":
                for result in block.get("content") or []:
                    if (
                        result.get("type") == "web_search_result"
                        and result.get("url")
                    ):
                        sources.append(
                            (
                                str(result.get("title") or ""),
                                str(result["url"]),
                            )
                        )
        summary = "\n".join(part for part in summary_parts if part).strip()
        if not summary and not sources:
            return "nessun risultato"
        return _format_search_result(summary, sources, count)
```

- [ ] **Step 4: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (160 tests).

- [ ] **Step 5: Commit**

```bash
git add agents/llm_agent.py tests/test_llm_client.py
git commit -m "feat(llm): anthropic messages web search provider"
```

---

### Task 4: Config contract (`llm.search`, `llm.headers`) + wiring defaults

**Files:**
- Modify: `utils/config_loader.py:97-127` (`validate_global_llm`)
- Modify: `main.py:39-67` (`_make_llm_client`: per-provider defaults, key resolution, UA/session headers)
- Modify: `agents/llm_agent.py:55-73` (constructor: `extra_headers` param, User-Agent default)
- Modify: `tests/test_cli.py` (rewrite brave-contract tests, extend `FakeLlmClient`, add search-contract tests)
- Test: `tests/test_llm_client.py` (User-Agent/default-headers assertions)

**Interfaces:**
- Consumes: `LlmClient` from Tasks 1-3.
- Produces: `LlmClient(..., extra_headers: dict[str, str] | None = None)` — headers merged as `{"User-Agent": "fantasyleague-auction/0.1", **(extra_headers or {})}` on the httpx client (all requests, chat included). `validate_global_llm` accepts: optional `llm.search` (provider enum `responses|anthropic|brave`, optional non-empty `base_url`/`api_key_env`, `model` required for non-brave, optional int `max_output_tokens >= 1`, optional header mapping, literal `api_key` rejected), optional `llm.headers` mapping, legacy `llm.brave` only when `llm.search` is absent (both → `'llm.search' and 'llm.brave' are mutually exclusive`), and neither (search disabled). Wiring constants: `SEARCH_PROVIDER_DEFAULTS = {"anthropic": {"base_url": "https://api.anthropic.com"}, "brave": {"base_url": "https://api.search.brave.com/res/v1/web/search"}}`.

- [ ] **Step 1: Write the failing config-contract tests**

In `tests/test_cli.py`:

1. Add a search-based config helper next to `base_llm_config` (keep `base_llm_config` on brave — it pins the legacy path):

```python
def search_llm_config(workbook: Path) -> dict:
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    data["llm"]["search"] = {
        "provider": "responses",
        "model": "gpt-5.6-luna",
    }
    return data
```

2. Replace `test_cli_rejects_missing_brave_block` with the new contract tests. Note: a single LLM buyer only completes (exit 0) on a full-roster workbook `{"P": 3, "D": 8, "C": 8, "A": 6}` — the pattern of `test_cli_llm_run_completes_with_fake_client` (test_cli.py:676). On `{"A": 1}` the auction exhausts the pool and exits 1, which is the pattern for the exhaustion/sidecar tests:

```python
def test_cli_search_block_optional_when_no_brave(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"P": 3, "D": 8, "C": 8, "A": 6})
    data = base_llm_config(workbook)
    data["llm"].pop("brave")
    data["paths"]["logs"] = str(tmp_path / "logs")
    write_raw_config(config, data)

    assert main(["--config", str(config), "--output", str(tmp_path / "r.json")]) == 0


@pytest.mark.parametrize(
    ("search_block", "message"),
    [
        (
            {"provider": "perplexity"},
            "'llm.search.provider' must be one of",
        ),
        (
            {"provider": "responses"},
            "'llm.search.model' must be a non-empty string",
        ),
        (
            {"provider": "anthropic", "model": "claude-opus-4-8", "api_key": "k"},
            "'llm.search.api_key' is not supported",
        ),
        (
            {"provider": "responses", "model": "m", "max_output_tokens": 0},
            "'llm.search.max_output_tokens' must be an int > 0",
        ),
        (
            {"provider": "responses", "model": "m", "base_url": "  "},
            "'llm.search.base_url' must be a non-empty string",
        ),
        (
            {"provider": "responses", "model": "m", "headers": "nope"},
            "'llm.search.headers' must be a mapping",
        ),
    ],
)
def test_cli_rejects_invalid_search_block(
    monkeypatch, tmp_path, search_block, message
):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["search"] = search_block
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any(message in error for error in errors)


def test_cli_rejects_search_and_brave_together(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["brave"] = {
        "base_url": "https://api.search.brave.com/res/v1/web/search",
        "api_key_env": "TEST_BRAVE_API_KEY",
    }
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("mutually exclusive" in error for error in errors)


def test_cli_rejects_invalid_llm_headers(monkeypatch, tmp_path):
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    write_workbook(workbook, {"A": 1})
    data = search_llm_config(workbook)
    data["llm"]["headers"] = {"x-empty": ""}
    write_raw_config(config, data)
    errors = capture_log_errors(monkeypatch)

    assert main(["--config", str(config)]) == 1
    assert any("'llm.headers' entries must be non-empty strings" in error for error in errors)
```

3. Add the wiring-resolution test. It calls `_make_llm_client` directly (no CLI run needed); because `LlmClient` is monkeypatched to `FakeLlmClient`, the returned object is a fake that records the resolved search dict:

```python
def test_cli_resolves_search_config_with_defaults(monkeypatch):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setenv("TEST_SEARCH_API_KEY", "search-dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    llm_config = {
        "base_url": "https://api.test/v1",
        "api_key_env": "TEST_LLM_API_KEY",
        "search": {
            "provider": "responses",
            "model": "gpt-5.6-luna",
            "api_key_env": "TEST_SEARCH_API_KEY",
        },
    }

    client = cli_module._make_llm_client(llm_config)

    assert client.provider == "responses"
    assert client.base_url == "https://api.test/v1"
    assert client.api_key == "search-dummy"
    assert client.model == "gpt-5.6-luna"
    assert client.extra_headers["x-opencode-session"].startswith("fantasyleague-")
```

For these asserts to work, `FakeLlmClient.__init__` must record the resolved fields:

```python
    def __init__(
        self,
        base_url,
        api_key,
        search=None,
        timeout_seconds=30,
        transport=None,
        extra_headers=None,
    ):
        self.calls = 0
        self.search = search
        self.extra_headers = extra_headers
        self.provider = search.get("provider") if search else None
        self.base_url = search.get("base_url") if search else None
        self.api_key = search.get("api_key") if search else None
        self.model = search.get("model") if search else None

(Step 3 explains why the assert targets `FakeLlmClient` attributes: `_make_llm_client` returns `LlmClient`, monkeypatched to `FakeLlmClient` in module scope, so the call in the assert constructs a fake with the same resolution.)

4. Keep `test_cli_rejects_brave_api_key_field` unchanged (legacy block still rejects literal keys).

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_cli.py -q -W error`
Expected: FAIL — `'llm.search.provider' must be one of` not raised (search block unvalidated), mutual-exclusion missing, `extra_headers` unexpected keyword.

- [ ] **Step 3: Implement validation and wiring**

In `utils/config_loader.py`, add above `validate_global_llm`:

```python
SEARCH_PROVIDERS = ("responses", "anthropic", "brave")


def _validate_header_map(headers: Any, where: str) -> None:
    if not isinstance(headers, dict):
        raise ValueError(f"'{where}' must be a mapping")
    for name, value in headers.items():
        if not str(name).strip() or not str(value).strip():
            raise ValueError(f"'{where}' entries must be non-empty strings")


def _validate_search_block(search: Any) -> None:
    if not isinstance(search, dict):
        raise ValueError("'llm.search' must be a mapping")
    if search.get("api_key") is not None:
        raise ValueError(
            "'llm.search.api_key' is not supported; use 'llm.search.api_key_env' "
            "with the environment variable name, never the key itself"
        )
    provider = str(search.get("provider", "")).strip()
    if provider not in SEARCH_PROVIDERS:
        raise ValueError(
            f"'llm.search.provider' must be one of {list(SEARCH_PROVIDERS)}"
        )
    if provider != "brave" and not str(search.get("model", "")).strip():
        raise ValueError(
            "'llm.search.model' must be a non-empty string for provider "
            f"'{provider}'"
        )
    for key in ("base_url", "api_key_env"):
        value = search.get(key)
        if value is not None and not str(value).strip():
            raise ValueError(f"'llm.search.{key}' must be a non-empty string")
    max_output_tokens = search.get("max_output_tokens")
    if max_output_tokens is not None and (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or max_output_tokens < 1
    ):
        raise ValueError("'llm.search.max_output_tokens' must be an int > 0")
    if search.get("headers") is not None:
        _validate_header_map(search["headers"], "llm.search.headers")
```

In `validate_global_llm`, replace the brave block (lines 117-127) with:

```python
    search = llm.get("search")
    brave = llm.get("brave")
    if search is not None and brave is not None:
        raise ValueError(
            "'llm.search' and 'llm.brave' are mutually exclusive; "
            "'llm.brave' is the legacy search block"
        )
    if search is not None:
        _validate_search_block(search)
    elif brave is not None:
        if not isinstance(brave, dict):
            raise ValueError("'llm.brave' must be a mapping")
        if brave.get("api_key") is not None:
            raise ValueError(
                "'llm.brave.api_key' is not supported; use 'llm.brave.api_key_env' "
                "with the environment variable name, never the key itself"
            )
        for key in ("base_url", "api_key_env"):
            if not str(brave.get(key, "")).strip():
                raise ValueError(f"'llm.brave.{key}' must be a non-empty string")
    if llm.get("headers") is not None:
        _validate_header_map(llm["headers"], "llm.headers")
```

In `main.py`, extend `_make_llm_client` (after Task 1's version) to apply provider defaults, copy optional keys, and build client headers:

```python
SEARCH_PROVIDER_DEFAULTS = {
    "anthropic": {"base_url": "https://api.anthropic.com"},
    "brave": {"base_url": "https://api.search.brave.com/res/v1/web/search"},
}


def _make_llm_client(llm_config: dict[str, Any]) -> LlmClient:
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Environment variable '{api_key_env}' (llm.api_key_env) is not set; "
            "set it before running an auction with LLM bidders"
        )
    search_config = llm_config.get("search")
    if search_config is None and llm_config.get("brave") is not None:
        search_config = {"provider": "brave", **llm_config["brave"]}
    search = None
    if search_config is not None:
        provider = str(search_config.get("provider", ""))
        defaults = SEARCH_PROVIDER_DEFAULTS.get(provider, {})
        search = {
            "provider": provider,
            "base_url": str(
                search_config.get("base_url")
                or defaults.get("base_url")
                or llm_config.get("base_url")
                or ""
            ),
            "api_key": os.environ.get(
                str(
                    search_config.get("api_key_env")
                    or llm_config.get("api_key_env")
                    or ""
                ),
                "",
            ),
        }
        for key in ("model", "max_output_tokens", "headers"):
            if search_config.get(key) is not None:
                search[key] = search_config[key]
    headers = {
        str(name): str(value)
        for name, value in (llm_config.get("headers") or {}).items()
    }
    headers.setdefault(
        "x-opencode-session",
        "fantasyleague-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
    )
    return LlmClient(
        base_url=str(llm_config["base_url"]),
        api_key=api_key,
        search=search,
        timeout_seconds=int(llm_config.get("timeout_seconds", 30)),
        extra_headers=headers,
    )
```

(`datetime`/`timezone` are already imported in `main.py`.)

In `agents/llm_agent.py`, add the `extra_headers` param and the User-Agent default:

```python
USER_AGENT = "fantasyleague-auction/0.1"
```

```python
    def __init__(
        self,
        base_url: str,
        api_key: str,
        search: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
        transport: httpx.BaseTransport | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        if not api_key:
            raise ValueError("LLM API key must be a non-empty string")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": USER_AGENT, **(extra_headers or {})},
        )
        self._api_key = api_key
        self._search = search
```

- [ ] **Step 4: Add default-headers assertions to `tests/test_llm_client.py`**

```python
def test_client_sends_user_agent_and_extra_headers_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "fantasyleague-auction/0.1"
        assert request.headers["x-opencode-session"] == "session-1"
        return httpx.Response(200, json={
            "choices": [{
                "message": {"role": "assistant", "content": "ok", "tool_calls": []},
                "finish_reason": "stop",
            }],
        })

    client = LlmClient(
        base_url="https://api.test/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        extra_headers={"x-opencode-session": "session-1"},
    )
    client.chat([{"role": "user", "content": "ciao"}], [], "gpt-4o-mini", 0.7)
```

- [ ] **Step 5: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (170 tests). If `test_chat_posts_expected_payload_and_parses_tool_calls` fails on header asserts, no fix needed — it only asserts `Authorization`.

- [ ] **Step 6: Commit**

```bash
git add agents/llm_agent.py utils/config_loader.py main.py tests/test_llm_client.py tests/test_cli.py
git commit -m "feat(config): pluggable search providers with llm.search contract"
```

---

### Task 5: Sidecar round-trip with `llm.search` + legacy resume pin

**Files:**
- Test: `tests/test_cli.py` (sidecar payload variant + resume test)

**Interfaces:**
- Consumes: `_write_llm_sidecar` dumps `llm` config verbatim (no code change expected); `_load_llm_sidecar` revalidates via the Task 4 rules.
- Produces: regression pin — a checkpoint written from a `llm.search` config resumes cleanly, and the sidecar contains the search block verbatim with no key material.

- [ ] **Step 1: Write the tests**

In `tests/test_cli.py`, next to `llm_sidecar_payload`:

```python
def search_sidecar_payload() -> dict:
    payload = llm_sidecar_payload()
    payload["llm"].pop("brave")
    payload["llm"]["search"] = {
        "provider": "responses",
        "model": "gpt-5.6-luna",
    }
    return payload


def make_llm_search_checkpoint(tmp_path, *, no_progress: bool = False) -> Path:
    checkpoint = make_llm_checkpoint(tmp_path, no_progress=no_progress)
    (tmp_path / "checkpoint.llm.yaml").write_text(
        yaml.safe_dump(search_sidecar_payload()), encoding="utf-8"
    )
    return checkpoint


def test_cli_resumes_llm_checkpoint_with_search_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    monkeypatch.setattr(
        cli_module, "_trace_run_dir", lambda logs_dir=None: tmp_path / "traces" / "resume"
    )
    checkpoint = make_llm_search_checkpoint(tmp_path)
    report = tmp_path / "report.json"

    exit_code = main([
        "--resume", str(checkpoint),
        "--config", str(tmp_path / "missing.yaml"),
        "--output", str(report),
    ])

    assert exit_code == 0
    assert (tmp_path / "traces" / "resume" / "incomplete.jsonl").exists()


def test_cli_exhaustion_sidecar_contains_search_block(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_LLM_API_KEY", "dummy")
    monkeypatch.setattr(cli_module, "LlmClient", FakeLlmClient)
    workbook = tmp_path / "players.xlsx"
    config = tmp_path / "config.yaml"
    checkpoint = tmp_path / "checkpoint.json"
    write_workbook(workbook, {"A": 1})
    write_raw_config(config, search_llm_config(workbook))

    exit_code = main([
        "--config", str(config),
        "--checkpoint", str(checkpoint),
    ])

    assert exit_code == 1
    data = yaml.safe_load(
        (tmp_path / "checkpoint.llm.yaml").read_text(encoding="utf-8")
    )
    assert data["llm"]["search"] == {
        "provider": "responses",
        "model": "gpt-5.6-luna",
    }
    assert "api_key" not in data["llm"]["search"]
```

- [ ] **Step 2: Run the tests**

Run: `venv/bin/pytest tests/test_cli.py -q -W error -k "search_sidecar or search_block"`
Expected: PASS immediately — this task pins behavior that Tasks 1-4 already implement. If either test fails, the sidecar write/load path dropped the search block; fix `_write_llm_sidecar`/`_load_llm_sidecar` in `main.py` (they should need no changes) and re-run.

- [ ] **Step 3: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (172 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(llm): pin sidecar round-trip with llm.search config"
```

---

### Task 6: `configs/llm.yaml` example on OpenCode Go + docs

**Files:**
- Modify: `configs/llm.yaml`
- Modify: `docs/project.md` (contract table row for `llm.brave` → `llm.search` / `llm.headers`)
- Modify: `docs/roadmap.md` (Backlog: mark the web-search item done)
- Modify: `README.md` (search example block)
- Modify: `AGENTS.md` (Secrets + LLM bidders sections — file is currently untracked; update content, leave untracked)

**Interfaces:**
- Consumes: the Task 4 config contract.
- Produces: docs matching the implemented contract; no code changes.

- [ ] **Step 1: Update `configs/llm.yaml`**

Replace the `llm:` block (lines 33-41) with:

```yaml
llm:
  base_url: "https://opencode.ai/zen/go/v1"
  api_key_env: "OPENCODE_API_KEY"
  model: "glm-5.3"
  temperature: 0.7
  timeout_seconds: 30
  # Header applicati a tutte le richieste. x-opencode-session è richiesto
  # da OpenCode Go; se omesso viene generato un default per-run.
  # headers:
  #   x-opencode-session: "asta-2026"
  search:
    provider: "responses"
    model: "gpt-5.6-luna"
    # base_url e api_key_env ereditano da llm sopra; max_output_tokens
    # default 400. Altri provider:
    #   anthropic → provider + model (base_url default https://api.anthropic.com)
    #   brave     → search classica "titolo — url" (provider + api_key_env)
```

Update every buyer's `model: "gpt-4o-mini"` to `model: "glm-5.3"` (4 occurrences) and the header comment listing buyer fields is unchanged.

- [ ] **Step 2: Update `docs/project.md`**

Find the contract-table row `| `llm` | `brave` | yes* | ... |` (line ~116). Replace it with:

```markdown
| `llm` | `search` | no | mapping with `provider` in {responses, anthropic, brave}; `model` required for responses/anthropic; optional `base_url`/`api_key_env` (inherit from `llm`), `max_output_tokens` (default 400), `headers`; literal `api_key` rejected. Legacy `brave` block still accepted when `search` is absent (mutually exclusive together); when both are absent, live search is disabled (`search non disponibile`) |
| `llm` | `headers` | no | mapping of extra headers applied to every request; `x-opencode-session` gets a per-run default when omitted |
```

Adjust the `yes*` footnote that referenced Brave accordingly (search is now optional; keep the note that a missing/placeholder key disables live search).

- [ ] **Step 3: Update `docs/roadmap.md`**

In the Backlog section, delete the bullet about web search not tied to Brave (approximately "web search senza Brave / provider di ricerca non legato a Brave") — this plan delivers it.

- [ ] **Step 4: Update `README.md`**

Replace the brave example block (lines ~127-151) with the `search:` block from Step 1 and rewrite the sentence that follows to explain: native search summarizes with cited sources (`Fonti:`), falls back to `search non disponibile` on missing key or errors; `brave` remains available as legacy provider.

- [ ] **Step 5: Update `AGENTS.md`**

In the Secrets section, replace `llm.brave.api_key_env` mentions with `llm.search.api_key_env` (noting `llm.brave` is accepted legacy). In the LLM bidders section, replace "missing/placeholder Brave key just disables live search" wording with the provider phrasing (`llm.search` with `responses`/`anthropic`/`brave` providers; missing key → `search non disponibile`). Leave the file untracked (matches its current state).

- [ ] **Step 6: Run the full suite and verify green**

Run: `venv/bin/pytest -q -W error`
Expected: PASS (172 tests; `configs/llm.yaml` is not loaded by tests but `git diff --check` must stay clean).

- [ ] **Step 7: Commit tracked docs**

```bash
git add configs/llm.yaml docs/project.md docs/roadmap.md README.md
git commit -m "docs: switch llm.yaml to opencode go, document pluggable search"
```

---

## Final verification (after Task 6)

- `venv/bin/pytest -q -W error` → all green (172 expected).
- `git diff --check` clean; `git log --oneline` shows 7 new commits (1 wording + 6 plan tasks) on `dev`.
- Optional live smoke (requires `OPENCODE_API_KEY` exported by the user): `venv/bin/python main.py --config configs/llm.yaml` and check `logs/traces/<run>/buyer_1.jsonl` for `tool_result` events with `Fonti:` content.
- The user-facing report (novità + differenze vs Brave) is delivered in chat, not committed.
