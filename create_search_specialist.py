"""
Create the Fedlex Search specialist sub-agent.

A narrow agent that takes a legal question (from a coordinator/router), in any
Swiss federal legal domain, and looks it up in the Swiss federal Classified
Compilation (Fedlex), returning a concise, source-backed answer.

Retrieval is done through the hosted, public Fedlex MCP connector
(https://mcp.fedlex-connector.ch) — a remote streamable-HTTP MCP server that
exposes search_by_title / get_law_text / get_article / list_amendments over the
official Fedlex data. No API key is required (open-reuse Swiss legal data).

The agent gets:
- A narrow system prompt (Fedlex Search Specialist)
- The standard agent toolset (fallback web_fetch to fedlex.admin.ch, file ops)
- The fedlex MCP toolset (its authoritative source of truth)
- The fedlex-search skill (attached separately by upload_skills.py)

Merges its id into .specialist_ids.json under the `search` key so it coexists
with the deal-desk specialists and so upload_skills.py can attach the skill.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python create_search_specialist.py
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic


# Hosted, public Fedlex MCP connector (remote streamable-HTTP, no auth).
# Managed Agents only accept remote (URL) MCP servers, not stdio.
FEDLEX_MCP_NAME = "fedlex"
FEDLEX_MCP_URL = "https://mcp.fedlex-connector.ch"

SEARCH_SPECIALIST = {
    "key": "search",
    "name": "Fedlex Search Specialist",
    # Legal precision matters here; bump to a larger model if you want more
    # careful synthesis. Sonnet keeps it consistent with the other specialists.
    "model": "claude-sonnet-4-6",
    "system": (
        "You are the Fedlex Search Specialist. You receive ONE legal question "
        "from the coordinator/router — in any Swiss federal legal domain (civil, "
        "contract/commercial, criminal, procedure, IP, data protection, tax, "
        "employment, social insurance, migration, financial-market regulation, "
        "etc.) — and return a concise, precise, source-backed answer grounded in "
        "the Swiss federal Classified Compilation (RS/SR).\n\n"
        "Swiss FEDERAL law only. Fedlex does not contain cantonal statutes or "
        "case law; when a question turns on those, say so rather than guess "
        "(cantonal harmonisation rules that live inside a federal act, e.g. the "
        "LHID, are in scope).\n\n"
        "Your source of truth is the `fedlex` MCP server "
        "(search_by_title / get_law_text / get_article / list_amendments), "
        "backed by the official Fedlex data. The fedlex-search skill gives you "
        "the retrieval workflow, the map of Swiss tax-law RS numbers, and the "
        "exact output format — follow it.\n\n"
        "Non-negotiable rules:\n"
        "1. Never answer from memory. Confirm every article number, deadline, "
        "and threshold with a fedlex MCP call in this turn before citing it.\n"
        "2. Quote the authoritative FR (and DE where useful) text; explain in "
        "English. The English Fedlex text is unofficial.\n"
        "3. Cite exactly: Art. X al. Y <Abbr.> (RS <number>) + the ELI source "
        "URL + the consolidated-version date you actually read.\n"
        "4. If Fedlex does not answer the question, say so plainly and name what "
        "would (case law, cantonal statute, an ESTV/AFC circular). Never invent "
        "an article.\n\n"
        "Output is a tight briefing back to a coordinator, not client-facing "
        "prose. Follow the skill's output contract verbatim."
    ),
}


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    spec = SEARCH_SPECIALIST
    agent = client.beta.agents.create(
        name=spec["name"],
        model=spec["model"],
        system=spec["system"],
        tools=[
            {"type": "agent_toolset_20260401"},
            # Expose the Fedlex MCP tools to this agent, and auto-approve them.
            # The connector is a read-only public legal-lookup server, so there's
            # nothing to gate — without always_allow the session pauses on every
            # tool call with stop_reason `requires_action` waiting for a human.
            {
                "type": "mcp_toolset",
                "mcp_server_name": FEDLEX_MCP_NAME,
                "default_config": {
                    "permission_policy": {"type": "always_allow"},
                },
            },
        ],
        mcp_servers=[
            {
                "type": "url",
                "name": FEDLEX_MCP_NAME,
                "url": FEDLEX_MCP_URL,
            }
        ],
        metadata={
            "hackathon": "partner-basecamp-2026",
            "track": "swiss-legal-swarm",
            "role": spec["key"],
        },
    )
    print(f"  Created {spec['name']:32s} -> {agent.id}")
    print(f"  MCP: {FEDLEX_MCP_NAME} -> {FEDLEX_MCP_URL} (public, no auth)")

    # Merge into .specialist_ids.json so it coexists with any deal-desk
    # specialists and so upload_skills.py can attach the fedlex-search skill.
    ids_path = Path(".specialist_ids.json")
    specialist_ids: dict[str, str] = {}
    if ids_path.exists():
        specialist_ids = json.loads(ids_path.read_text())
    specialist_ids[spec["key"]] = agent.id
    ids_path.write_text(json.dumps(specialist_ids, indent=2))

    print(f"\nSaved search specialist id to .specialist_ids.json (key: 'search')")
    print("Next: python upload_skills.py   (attaches the fedlex-search skill)")
    print("Then: python run_fedlex_search.py \"<your tax question>\"")


if __name__ == "__main__":
    main()
