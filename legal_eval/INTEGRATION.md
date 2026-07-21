# Adding filtered web access to a new specialist

Every future specialist that touches the web must go through the filter (date
cutoff + Haiku censor), or it can find the protected ruling. Two rules:

1. **Never give an agent the built-in `web_search` / `web_fetch`.** Build its
   `tools` with `filtered_toolset()`, which disables them.
2. **Every web-facing tool is a custom tool whose handler runs `filter_results`.**
   Use `make_filtered_search(backend)` and register it on the router.

There is no third path — if it isn't a filtered custom tool, the agent can't reach
the web. That's the guarantee.

---

## Case A: the new specialist just needs the existing `legal_web_search`

Nothing to build. Create it with `filtered_toolset()` (which already includes
`legal_web_search`) and it works — the run loop's `default_router` already handles it.

```python
from legal_eval import filtered_toolset

agent = client.beta.agents.create(
    name="Comparative-Law Angle",
    model="claude-sonnet-4-6",
    system="...",
    tools=filtered_toolset(),      # native web off + legal_web_search on
)
```

---

## Case B: the new specialist needs a NEW custom search tool

Example: a case-law database search. Three steps.

**1. Write a backend** — `(query: str) -> list[SearchResult]`. Where results come
from is up to you (an API, a DB, scraping); just return `SearchResult`s. Fill in
`published` whenever you can — that's what powers the date cutoff.

```python
from datetime import date
from legal_eval import SearchResult

def case_law_backend(query: str) -> list[SearchResult]:
    hits = my_case_db.search(query)              # your source
    return [
        SearchResult(
            title=h.title, url=h.url, snippet=h.headnote,
            published=date.fromisoformat(h.date) if h.date else None,
        )
        for h in hits
    ]
```

**2. Declare the tool** and attach it via `filtered_toolset(extra_tools=...)`.

```python
CASE_LAW_TOOL = {
    "type": "custom",
    "name": "case_law_search",
    "description": "Search Swiss case-law database. Use instead of built-in web search.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {"query": {"type": "string"}}, "required": ["query"],
    },
}

agent = client.beta.agents.create(
    name="Case-Law Angle", model="claude-sonnet-4-6", system="...",
    tools=filtered_toolset(extra_tools=(CASE_LAW_TOOL,)),
)
```

**3. Register the handler** on the router, wrapped so results are filtered.
`make_filtered_search` is the wrapper that gives you the date cutoff + censor.

```python
from legal_eval import make_filtered_search
# in run_legal_eval.py, after `router = default_router(client)`:
router.register("case_law_search", make_filtered_search(case_law_backend, client=client))
```

That's it. Anything `case_law_backend` returns now passes the same two gates + the
Haiku censor before the agent sees it, and every drop is logged.

> If a tool's input field isn't called `query`, pass `query_key="..."` to
> `make_filtered_search`.

---

## Non-search custom tools

`make_filtered_search` is only for tools that return web/document content to
filter. A custom tool with no external content (a calculator, a DB write) just
registers a plain handler — `router.register("name", lambda inp: do_work(inp))` —
no filter needed.

---

## Where the knobs live

- **`FREEZE_DATE`, `PROTECTED_TOPIC`, denylist, case-number regex** — `source_filter.py`.
  These define "the answer" being hidden. Change them per eval.
- **`fail_closed`** — `filter_results(..., fail_closed=True)` (default). Keep it True
  for eval integrity; a censor error drops the result rather than leaking.
- **Backend selection** — `search_backend()` in `filtered_search.py` (Tavily vs.
  offline fixtures).

## Audit

`filter_results` returns `FilterOutcome.redactions` (url, stage, reason). The run
loop prints each drop. For a real eval, also log the full transcript and grep it
for the docket number after each run — prevention *and* detection.
