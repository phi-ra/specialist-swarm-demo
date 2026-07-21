"""
Precedent search over the Swiss Federal Supreme Court (bger.ch).

Flow:
  1. Keyword search:  GET .../index.php?type=simple_query&query_words=<kw>
  2. Parse the results page for hits — each links to a decision via a
     `highlight_docid=aza://DD-MM-YYYY-<CASE>` parameter, which hands us the
     decision DATE and DOCKET NUMBER for free.
  3. Dig deeper: fetch the top few decisions and pull a text snippet.
  4. Return SearchResults — which the router runs through the SAME filter as
     every other tool.

Censoring: results pass through the same filter as every other tool — the date
cutoff keeps the target case 6B_998/2024 out (it's newer than any precedent), and
the Haiku censor backstops the decision text. Precedents (which are older) pass.

Live fetching is client-side (in the run loop), so the agent container needs no
web egress. Set BGER_OFFLINE=1 to use the built-in fixtures (demo/tests/no network).
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse
from datetime import date

import httpx

from .source_filter import SearchResult


PRECEDENT_SEARCH_TOOL = {
    "type": "custom",
    "name": "precedent_search",
    "description": (
        "Search Swiss Federal Supreme Court (Bundesgericht) decisions by keyword to "
        "find PRECEDENT cases relevant to the legal question, then read into the top "
        "hits. Use this instead of any built-in web search. Returns decisions with "
        "docket number, date, and an excerpt."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords, e.g. 'Parkbusse Ordnungsbusse Übertretung'.",
            }
        },
        "required": ["query"],
    },
}

_SEARCH_URL = "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php"
_DEEP_FETCH_TOP_N = 4
_HTTP_TIMEOUT = 30.0
_UA = {"User-Agent": "legal-eval-precedent-bot/1.0"}

# aza://15-06-2026-6B_998-2024  (also matches the URL-encoded aza%3A%2F%2F form
# once the href is unescaped). Captures date (DD-MM-YYYY) and docket (6B_998-2024).
_DOCID_RE = re.compile(
    r"aza://(\d{2})-(\d{2})-(\d{4})-([0-9A-Za-z]+_\d+-\d{4})",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _docket_display(raw: str) -> str:
    # "6B_998-2024" -> "6B_998/2024"
    return raw.rsplit("-", 1)[0] + "/" + raw.rsplit("-", 1)[1]


def _decision_url(docid: str) -> str:
    q = urllib.parse.urlencode(
        {"lang": "de", "type": "show_document", "highlight_docid": f"aza://{docid}"}
    )
    return f"{_SEARCH_URL}?{q}"


def parse_bger_results(page_html: str) -> list[SearchResult]:
    """
    Pure parser (no network) — pull one SearchResult per unique decision found in
    a results page. Kept separate from the HTTP call so it's unit-testable.
    """
    unescaped = html.unescape(urllib.parse.unquote(page_html))
    seen: set[str] = set()
    out: list[SearchResult] = []
    for m in _DOCID_RE.finditer(unescaped):
        dd, mm, yyyy, docket = m.group(1), m.group(2), m.group(3), m.group(4)
        docid = f"{dd}-{mm}-{yyyy}-{docket}"
        if docid in seen:
            continue
        seen.add(docid)
        try:
            published: date | None = date(int(yyyy), int(mm), int(dd))
        except ValueError:
            published = None
        out.append(
            SearchResult(
                title=f"Bundesgericht {_docket_display(docket)}",
                url=_decision_url(docid),
                snippet="",  # filled by deep fetch
                published=published,
            )
        )
    return out


def _extract_text(page_html: str, limit: int = 1800) -> str:
    text = _TAG_RE.sub(" ", html.unescape(page_html))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _deep_fetch(client: httpx.Client, results: list[SearchResult]) -> None:
    """Fetch the top few decisions and fill in their snippets, in place."""
    for r in results[:_DEEP_FETCH_TOP_N]:
        try:
            resp = client.get(r.url, headers=_UA, timeout=_HTTP_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            r.snippet = _extract_text(resp.text)
        except Exception as exc:  # noqa: BLE001 — a fetch failure just leaves snippet empty
            print(f"    [precedent] deep-fetch failed for {r.url}: {exc}", flush=True)


def bger_backend(query: str) -> list[SearchResult]:
    """Live keyword search on bger.ch + deep fetch. Honors BGER_OFFLINE=1."""
    if os.environ.get("BGER_OFFLINE") == "1":
        return _offline_fixtures(query)

    params = {"lang": "de", "type": "simple_query", "query_words": query}
    try:
        with httpx.Client() as client:
            resp = client.get(
                _SEARCH_URL, params=params, headers=_UA,
                timeout=_HTTP_TIMEOUT, follow_redirects=True,
            )
            resp.raise_for_status()
            results = parse_bger_results(resp.text)
            _deep_fetch(client, results)
    except Exception as exc:  # noqa: BLE001 — surface the failure, don't crash the agent
        print(f"    [precedent] search failed: {exc}", flush=True)
        return []
    return results


# ---------------------------------------------------------------------------
# Offline fixtures: a results page containing precedents + the protected case,
# so the demo/tests show the filter keeping precedents and dropping the target.
# ---------------------------------------------------------------------------

_FIXTURE_RESULTS_HTML = """
<html><body>
<a href="index.php?type=highlight_simple_query&highlight_docid=aza%3A%2F%2F04-11-2019-6B_1123-2018">6B_1123/2018</a>
<a href="index.php?type=highlight_simple_query&highlight_docid=aza%3A%2F%2F22-03-2021-6B_242-2020">6B_242/2020</a>
<a href="index.php?type=highlight_simple_query&highlight_docid=aza%3A%2F%2F15-06-2026-6B_998-2024">6B_998/2024</a>
</body></html>
"""

_FIXTURE_TEXT = {
    "6B_1123-2018": (
        "Urteil 6B_1123/2018: Erwägungen zur Abgrenzung von Übertretungen und zum "
        "Ordnungsbussenverfahren im Strassenverkehr. Allgemeine dogmatische Grundsätze."
    ),
    "6B_242-2020": (
        "Urteil 6B_242/2020: Zur Rechtsnatur von Bussen im vereinfachten Verfahren; "
        "Verhältnis von Verwaltungs- und Strafrecht."
    ),
    "6B_998-2024": (
        "Urteil 6B_998/2024 vom 15. Juni 2026: Das Bundesgericht hat entschieden, dass "
        "unbezahlte Parkbussen ... [protected holding] ..."
    ),
}


def _offline_fixtures(query: str) -> list[SearchResult]:
    results = parse_bger_results(_FIXTURE_RESULTS_HTML)
    for r in results:
        m = re.search(r"(\w+_\d+-\d{4})", r.url)
        if m and m.group(1) in _FIXTURE_TEXT:
            r.snippet = _FIXTURE_TEXT[m.group(1)]
    return results
