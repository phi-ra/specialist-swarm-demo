"""
Source filter for the legal-evaluation swarm.

Goal: keep the specialists from ever seeing the recent Bundesgericht ruling
(6B_998/2024, decided 15 June 2026) — or any news / commentary that reports on
it — so their legal reasoning is tested, not their ability to copy the answer.

Two gates, applied cheapest-first:

  1. Date cutoff  (deterministic) — drop anything dated on/after FREEZE_DATE.
                                     The ruling is newer than any precedent, so
                                     one rule blocks it and all commentary on it.
  2. Haiku censor (semantic)      — drop anything that *reports on* the ruling or
                                     reveals its holding, even paraphrased and
                                     undated.

No domain denylist, no docket-number regex — a semantic censor is what screens
content; the date cutoff is the one cheap deterministic gate. The censor fails
CLOSED: if Haiku errors or is unsure, the result is dropped. For an eval, a false
drop is cheap; a leak is not.

This module is backend-agnostic: it filters `SearchResult`s, wherever they came
from. See filtered_search.py for how results are fetched and how this plugs into
the managed-agents custom tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Configuration — the two things that define "the answer" we're hiding.
# ---------------------------------------------------------------------------

# The ruling was issued 15 June 2026. Freeze before it AND before the wave of
# commentary that precedes a high-profile decision (oral hearing coverage etc.).
# One rule blocks the decision and everything written about it.
FREEZE_DATE = date(2026, 5, 1)

# What the specialists must NOT be handed. Written for the Haiku censor to read.
PROTECTED_TOPIC = (
    "A Swiss Federal Supreme Court (Bundesgericht / Tribunal fédéral) criminal-law "
    "decision, docket 6B_998/2024, issued 15 June 2026, on whether the "
    "non-payment of a parking fine (Parkbusse / Ordnungsbusse) is a criminal "
    "matter or a civil/administrative matter under Swiss law — and, more broadly, "
    "any source that states or strongly implies HOW that specific case was decided."
)

HAIKU_MODEL = "claude-haiku-4-5"


@dataclass
class SearchResult:
    """One web result, normalised across whatever backend produced it."""

    title: str
    url: str
    snippet: str
    # Publication date if the backend knows it; None if undated.
    published: date | None = None


@dataclass
class Redaction:
    """Audit record: what got dropped, at which gate, and why."""

    url: str
    stage: str          # "date_cutoff" | "denylist" | "case_number" | "haiku"
    reason: str


@dataclass
class FilterOutcome:
    allowed: list[SearchResult] = field(default_factory=list)
    redactions: list[Redaction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gate 1: date cutoff (deterministic)
# ---------------------------------------------------------------------------

def _fails_date_cutoff(result: SearchResult) -> bool:
    # Undated results are NOT dropped here — many statute pages are undated.
    # They fall through to the Haiku censor instead.
    return result.published is not None and result.published >= FREEZE_DATE


# ---------------------------------------------------------------------------
# Gate 2: Haiku semantic censor
# ---------------------------------------------------------------------------

_CENSOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "block", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "block": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

_CENSOR_SYSTEM = f"""\
You are a source screener protecting the integrity of a legal-reasoning \
evaluation. The agents being evaluated must reason from statute and general \
doctrine WITHOUT access to one specific recent court decision or any reporting \
on it.

PROTECTED (must be blocked):
{PROTECTED_TOPIC}

Block a source if it does ANY of the following:
- names or links to that specific decision (docket 6B_998/2024), or
- reports on / summarises / comments on that decision, or
- states or strongly implies the ANSWER to whether unpaid Swiss parking fines \
are criminal vs. civil AS DECIDED BY THE FEDERAL SUPREME COURT (e.g. "the \
Bundesgericht ruled that…", news coverage of the outcome).

Do NOT block general legal background that an agent could legitimately reason \
from: the text of statutes (StGB, StPO, SVG, OBG/Ordnungsbussengesetz, \
cantonal law), academic doctrine written before the case, or unrelated older \
case law. Uncertain whether a source reveals the specific holding? BLOCK IT — \
in this eval a false block is acceptable, a leak is not.

Return a verdict for every source index you are given."""


def _haiku_censor(
    client: Anthropic,
    candidates: list[tuple[int, SearchResult]],
    *,
    fail_closed: bool = True,
) -> dict[int, tuple[bool, str]]:
    """Return {original_index: (block, reason)} for the given candidates."""
    if not candidates:
        return {}

    payload = [
        {
            "index": idx,
            "title": r.title,
            "url": r.url,
            "text": r.snippet[:1500],
        }
        for idx, r in candidates
    ]

    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2048,
            system=_CENSOR_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Screen these sources. Respond with a verdict per index.\n\n"
                        + json.dumps(payload, ensure_ascii=False, indent=2)
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _CENSOR_SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        verdicts = json.loads(text)["verdicts"]
    except Exception as exc:  # noqa: BLE001 — any failure must not leak
        if fail_closed:
            return {
                idx: (True, f"censor unavailable, failed closed ({type(exc).__name__})")
                for idx, _ in candidates
            }
        return {}

    out: dict[int, tuple[bool, str]] = {}
    for v in verdicts:
        out[int(v["index"])] = (bool(v["block"]), str(v.get("reason", "")))

    # Any index Haiku forgot to rule on: fail closed.
    if fail_closed:
        for idx, _ in candidates:
            out.setdefault(idx, (True, "no verdict returned, failed closed"))
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def filter_results(
    results: Iterable[SearchResult],
    *,
    client: Anthropic | None = None,
    fail_closed: bool = True,
) -> FilterOutcome:
    """Run the pipeline (date cutoff -> Haiku censor). Returns allowed + audit trail."""
    client = client or Anthropic()
    outcome = FilterOutcome()

    survivors: list[tuple[int, SearchResult]] = []
    for idx, r in enumerate(results):
        if _fails_date_cutoff(r):
            outcome.redactions.append(
                Redaction(r.url, "date_cutoff", f"published {r.published} >= {FREEZE_DATE}")
            )
            continue
        survivors.append((idx, r))

    verdicts = _haiku_censor(client, survivors, fail_closed=fail_closed)
    for idx, r in survivors:
        block, reason = verdicts.get(idx, (fail_closed, "no verdict"))
        if block:
            outcome.redactions.append(Redaction(r.url, "haiku", reason))
        else:
            outcome.allowed.append(r)

    return outcome
