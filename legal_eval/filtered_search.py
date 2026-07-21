"""
The custom `legal_web_search` tool: definition, backend, and orchestrator handler.

Why a custom tool instead of the built-in web search?
  The managed agent toolset (`agent_toolset_20260401`) runs web_search / web_fetch
  server-side, inside Anthropic's agent loop — there is no client-side hook to
  filter what comes back. To apply a date cutoff + Haiku censor we must OWN the
  retrieval path. A custom tool does exactly that: the specialist emits an
  `agent.custom_tool_use` event, the session goes idle, WE run the search + filter
  here, and hand back only sanitised results via `user.custom_tool_result`.

Backend is pluggable:
  - Tavily (set TAVILY_API_KEY) — real web results with publish dates + content.
  - Offline fixtures (default) — a canned corpus incl. a deliberate "leak" so the
    demo and tests run with no third-party key.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Callable

import httpx
from anthropic import Anthropic

from .source_filter import FilterOutcome, SearchResult, filter_results


# ---------------------------------------------------------------------------
# Tool definition — attach this to each specialist agent (see create_legal_specialists.py)
# ---------------------------------------------------------------------------

LEGAL_WEB_SEARCH_TOOL = {
    "type": "custom",
    "name": "legal_web_search",
    "description": (
        "Search the web for Swiss legal sources: statutes, doctrine, and prior "
        "case law relevant to the question. Use this instead of any built-in web "
        "search. Returns a curated list of sources (title, url, snippet)."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'Ordnungsbussengesetz Parkbusse Strafrecht'.",
            }
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# Search backends
# ---------------------------------------------------------------------------

def _tavily_search(query: str, *, max_results: int = 8) -> list[SearchResult]:
    key = os.environ["TAVILY_API_KEY"]
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    out: list[SearchResult] = []
    for item in resp.json().get("results", []):
        published = None
        raw = item.get("published_date")
        if raw:
            try:
                published = date.fromisoformat(raw[:10])
            except ValueError:
                published = None
        out.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                published=published,
            )
        )
    return out


# A tiny stand-in corpus for offline runs. Deliberately mixes:
#   - legitimate background the agents SHOULD get (statutes, old doctrine)
#   - a direct leak (the bger.ch decision)
#   - a news article reporting the outcome (post-cutoff, paraphrased holding)
#   - a pre-cutoff blog that happens to *predict* the outcome (must survive dates,
#     caught by the censor only if it reveals the actual holding — here it doesn't)
_FIXTURES: list[SearchResult] = [
    SearchResult(
        title="Ordnungsbussengesetz (OBG) — Fedlex",
        url="https://www.fedlex.admin.ch/eli/cc/2017/802/de",
        snippet=(
            "Das Ordnungsbussengesetz regelt das vereinfachte Verfahren zur Ahndung "
            "bestimmter Übertretungen mittels Ordnungsbusse, u.a. im Strassenverkehr."
        ),
        published=date(2018, 1, 1),
    ),
    SearchResult(
        title="Schweizerisches Strafgesetzbuch, Art. 103 Übertretungen — Fedlex",
        url="https://www.fedlex.admin.ch/eli/cc/54/757_781_799/de",
        snippet=(
            "Übertretungen sind Taten, die mit Busse bedroht sind. Abgrenzung von "
            "Verbrechen und Vergehen."
        ),
        published=date(1937, 12, 21),
    ),
    SearchResult(
        title="Parkbusse nicht bezahlt – Strafrecht oder Verwaltungsrecht? (Kanzlei-Blog, 2019)",
        url="https://example-kanzlei.ch/blog/parkbusse-2019",
        snippet=(
            "Ein Überblick über die dogmatische Einordnung der Parkbusse. Der Beitrag "
            "diskutiert Argumente für beide Seiten, ohne ein höchstrichterliches Urteil "
            "vorwegzunehmen."
        ),
        published=date(2019, 5, 3),
    ),
    SearchResult(
        title="Urteil 6B_998/2024 vom 15. Juni 2026 — Bundesgericht",
        url="https://www.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://15-06-2026-6B_998-2024",
        snippet=(
            "Das Bundesgericht hat entschieden, dass ... [the actual holding of the "
            "protected decision] ..."
        ),
        published=date(2026, 6, 15),
    ),
    SearchResult(
        title="Bundesgericht klärt Streit um unbezahlte Parkbussen — NZZ",
        url="https://www.nzz.ch/schweiz/parkbusse-bundesgericht-urteil-ld.999999",
        snippet=(
            "In einem am Montag publizierten Leiturteil hat das oberste Gericht "
            "festgehalten, wie unbezahlte Parkbussen rechtlich zu behandeln sind. "
            "Das Urteil dürfte weitreichende Folgen haben."
        ),
        published=date(2026, 6, 16),
    ),
]


def _offline_search(query: str, *, max_results: int = 8) -> list[SearchResult]:
    # Backend is intentionally query-insensitive for the demo: it returns the
    # fixed corpus so you can watch the filter do the work.
    return _FIXTURES[:max_results]


def search_backend(query: str) -> list[SearchResult]:
    if os.environ.get("TAVILY_API_KEY"):
        return _tavily_search(query)
    return _offline_search(query)


# ---------------------------------------------------------------------------
# Reusable pieces — this is what future custom search tools build on.
# ---------------------------------------------------------------------------

def format_outcome(outcome: FilterOutcome) -> str:
    """Render a filtered result set as the text handed back to the agent."""
    # Console-side audit so you can SEE what was blocked and why.
    for red in outcome.redactions:
        print(f"    [filter] blocked {red.url}  ({red.stage}: {red.reason})", flush=True)

    if not outcome.allowed:
        return (
            "No permissible sources were found for this query. Reason from the "
            "statutes and general doctrine you already have."
        )

    lines = [f"{len(outcome.allowed)} source(s) returned:\n"]
    for i, r in enumerate(outcome.allowed, 1):
        dt = r.published.isoformat() if r.published else "n/d"
        lines.append(f"[{i}] {r.title} ({dt})\n{r.url}\n{r.snippet}\n")
    return "\n".join(lines)


def make_filtered_search(
    backend: Callable[[str], list[SearchResult]],
    *,
    client: Anthropic | None = None,
    query_key: str = "query",
    **filter_kwargs,
) -> Callable[[dict], str]:
    """
    Wrap ANY search backend into a custom-tool handler whose results are
    automatically run through the date cutoff + Haiku censor.

    A backend is just `(query: str) -> list[SearchResult]`. This is the one-liner
    for giving a future specialist filtered web access: write a backend, wrap it,
    register the handler (see router.py), attach a tool def.

        handler = make_filtered_search(my_backend)

    Extra keyword args are forwarded to `filter_results` (e.g. `fail_closed`).
    The returned handler takes the tool's input dict and returns result text.
    """
    client = client or Anthropic()

    def handler(tool_input: dict) -> str:
        query = (tool_input or {}).get(query_key, "")
        print(f"  [filtered search] {query!r}", flush=True)
        raw = backend(query)
        outcome = filter_results(raw, client=client, **filter_kwargs)
        return format_outcome(outcome)

    return handler


def run_legal_web_search(query: str, *, client: Anthropic | None = None) -> str:
    """Convenience: one filtered search over the default backend (used in demos/tests)."""
    return make_filtered_search(search_backend, client=client)({"query": query})


# ---------------------------------------------------------------------------
# Agent toolset helper — attach filtered access with one call.
# ---------------------------------------------------------------------------

def filtered_toolset(
    *,
    extra_tools: tuple[dict, ...] = (),
    disable: tuple[str, ...] = ("web_search", "web_fetch"),
    include_legal_web_search: bool = True,
) -> list[dict]:
    """
    Build a `tools=[...]` list that gives an agent the full built-in toolset with
    the native web tools DISABLED, plus filtered custom tools.

    Any agent created with this can only reach the web through tools you route
    through the filter — there's no built-in web_search to bypass it.

        tools = filtered_toolset(extra_tools=(MY_CUSTOM_SEARCH_TOOL,))
    """
    tools: list[dict] = [
        {
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": name, "enabled": False} for name in disable],
        }
    ]
    if include_legal_web_search:
        tools.append(LEGAL_WEB_SEARCH_TOOL)
    tools.extend(extra_tools)
    return tools
