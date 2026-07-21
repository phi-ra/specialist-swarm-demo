"""
Wiring example: create legal-angle specialists that can ONLY reach the web
through the filtered `legal_web_search` tool.

Two things make the isolation airtight at the agent level:

  1. The built-in `web_search` and `web_fetch` tools are DISABLED in the
     agent toolset — otherwise a specialist could bypass the filter entirely.
  2. The only network-facing tool they have is our custom `legal_web_search`,
     whose results pass through the date cutoff + Haiku censor.

Run this once, store the IDs, then reference them from a coordinator + run loop
that calls `handle_custom_tool_event` on every `agent.custom_tool_use` event
(see the docstring at the bottom for the run-loop shape).
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic

from .filtered_search import filtered_toolset
from .precedent_search import PRECEDENT_SEARCH_TOOL


# Each specialist attacks the question from a different legal angle.
# `extra_tools` = custom tools beyond legal_web_search (all still filtered).
SPECIALISTS = [
    {
        "key": "criminal_law",
        "name": "Criminal-Law Angle",
        "system": (
            "You analyse whether non-payment of a parking fine is a CRIMINAL matter "
            "under Swiss law. Reason from the StGB, StPO, SVG, and the "
            "Ordnungsbussengesetz (OBG). Use `legal_web_search` for statutes and "
            "doctrine. State your conclusion and the strongest counter-argument."
        ),
    },
    {
        "key": "administrative_law",
        "name": "Administrative/Civil-Law Angle",
        "system": (
            "You analyse whether non-payment of a parking fine is an "
            "ADMINISTRATIVE or civil matter under Swiss law. Reason from cantonal "
            "administrative law and the OBG. Use `legal_web_search`. State your "
            "conclusion and the strongest counter-argument."
        ),
    },
    {
        "key": "procedure",
        "name": "Procedural Angle",
        "system": (
            "You analyse which PROCEDURE applies when a parking fine goes unpaid "
            "(Ordnungsbussenverfahren vs. ordentliches Strafverfahren) and what that "
            "implies about the matter's legal character. Use `legal_web_search`."
        ),
    },
    {
        "key": "precedent",
        "name": "Precedent Analyst",
        "system": (
            "You find and analyse PRECEDENT decisions of the Swiss Federal Supreme "
            "Court (Bundesgericht) relevant to how unpaid parking fines are "
            "classified. Use `precedent_search` to search decisions by keyword "
            "(e.g. 'Parkbusse', 'Ordnungsbusse', 'Übertretung Strassenverkehr') and "
            "read into the top hits. For each relevant precedent, give the docket "
            "number, date, the holding, and how it bears on the question. If a "
            "controlling recent decision seems to be missing, say so — do not "
            "speculate about how it was decided."
        ),
        "extra_tools": (PRECEDENT_SEARCH_TOOL,),
    },
]


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic(
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    ids: dict[str, str] = {}
    for spec in SPECIALISTS:
        agent = client.beta.agents.create(
            name=spec["name"],
            model="claude-sonnet-4-6",
            system=spec["system"],
            tools=filtered_toolset(extra_tools=spec.get("extra_tools", ())),
            metadata={"track": "legal-eval", "role": spec["key"]},
        )
        ids[spec["key"]] = agent.id
        print(f"  Created {spec['name']:32s} -> {agent.id}")

    Path(".legal_specialist_ids.json").write_text(json.dumps(ids, indent=2))
    print(f"\nSaved {len(ids)} specialist IDs to .legal_specialist_ids.json")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# The coordinator + run loop live in create_legal_coordinator.py and
# run_legal_eval.py. The run loop dispatches custom tool calls with:
#
#   from legal_eval import default_router
#   router = default_router(client)
#   ...
#   for event in stream:
#       router.dispatch(session.id, event)   # safe to call on every event
#
# See INTEGRATION.md for how to add filtered web access to future specialists.
# ---------------------------------------------------------------------------
