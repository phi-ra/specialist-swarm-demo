"""
Create the coordinator for the legal-evaluation swarm.

The coordinator fans the question out to the angle-specialists (created by
create_legal_specialists.py), then synthesises their analyses into one reasoned
assessment. It also gets filtered web access itself — via `filtered_toolset()`,
native web is off, so neither the coordinator nor any specialist can reach the
protected ruling.

Run once, after create_legal_specialists.py:
    python -m legal_eval.create_legal_coordinator
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic

from .filtered_search import filtered_toolset


COORDINATOR_SYSTEM = """\
You coordinate a panel of legal specialists answering one question:

  Under Swiss law, is the non-payment of a parking fine (Parkbusse) a CRIMINAL
  matter or a CIVIL/ADMINISTRATIVE matter?

# Your panel
- Criminal-Law Angle: argues the criminal-law characterisation
- Administrative/Civil-Law Angle: argues the administrative/civil characterisation
- Procedural Angle: analyses which procedure applies and what it implies

# How to run the panel
1. Delegate the question to ALL THREE specialists in parallel, each with a clear
   brief ("~300 words, cite the statutes/doctrine you rely on").
2. When their analyses come back, SYNTHESISE — do not just concatenate. Produce:
   - A direct answer (criminal / civil-administrative / mixed) with your confidence
   - The decisive statutory and doctrinal reasons
   - The strongest counter-argument and why it does or doesn't prevail
   - Open questions a court would still have to resolve

# Important
You and your specialists reason from statutes and general doctrine. You will NOT
find a single controlling Federal Supreme Court decision that settles this — that
is by design. Build the best-reasoned answer from first principles; do not claim
a specific recent judgment decided it.

Be rigorous and terse.
"""


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    ids_path = Path(".legal_specialist_ids.json")
    if not ids_path.exists():
        raise SystemExit("Run `python -m legal_eval.create_legal_specialists` first.")
    specialist_ids = json.loads(ids_path.read_text())

    client = Anthropic(
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    coordinator = client.beta.agents.create(
        name="Legal Panel Coordinator",
        model="claude-opus-4-8",
        system=COORDINATOR_SYSTEM,
        tools=filtered_toolset(),
        multiagent={
            "type": "coordinator",
            "agents": [{"type": "agent", "id": aid} for aid in specialist_ids.values()],
        },
        metadata={"track": "legal-eval", "role": "coordinator"},
    )

    Path(".legal_coordinator_id").write_text(coordinator.id)
    print(f"Coordinator created: {coordinator.id}")
    print(f"Roster: {list(specialist_ids.keys())}")
    print("\nNext: python -m legal_eval.run_legal_eval")


if __name__ == "__main__":
    main()
