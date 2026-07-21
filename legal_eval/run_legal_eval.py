"""
Run the legal-evaluation swarm end-to-end.

  1. Ensure an environment exists (reuses .environment_id if present).
  2. Start a session against the coordinator.
  3. Stream events — watch the panel fan out.
  4. Dispatch every custom tool call through the router, so all web access is
     filtered (date cutoff + Haiku censor). The container never has native web.
  5. Print the coordinator's synthesised answer.

Run after create_legal_specialists.py + create_legal_coordinator.py:
    python -m legal_eval.run_legal_eval
"""

import os
from pathlib import Path

from anthropic import Anthropic

from .router import default_router


QUESTION = (
    "Under Swiss law, is the non-payment of a parking fine (Parkbusse) a criminal "
    "matter or a civil/administrative matter? Run the full panel and give me a "
    "single reasoned assessment."
)


def ensure_environment(client: Anthropic) -> str:
    env_path = Path(".environment_id")
    if env_path.exists():
        return env_path.read_text().strip()
    env = client.beta.environments.create(
        name="legal-eval-env",
        # Native web is disabled on the agents and all our search is client-side,
        # so the container needs no web egress. `limited` keeps it tight; switch to
        # unrestricted only if a future tool needs package installs in-container.
        config={"type": "cloud", "networking": {"type": "limited"}},
    )
    env_path.write_text(env.id)
    print(f"Environment created: {env.id}")
    return env.id


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    coord_path = Path(".legal_coordinator_id")
    if not coord_path.exists():
        raise SystemExit("Run `python -m legal_eval.create_legal_coordinator` first.")
    coordinator_id = coord_path.read_text().strip()

    client = Anthropic(default_headers={"anthropic-beta": "managed-agents-2026-04-01"})
    environment_id = ensure_environment(client)
    router = default_router(client)  # register more custom tools here as you add them

    session = client.beta.sessions.create(
        agent=coordinator_id,
        environment_id=environment_id,
        title="Legal panel — Parkbusse criminal vs. civil",
    )
    Path(".legal_last_session_id").write_text(session.id)
    print(f"Session: {session.id}")
    print(f"Trace:   https://platform.claude.com/sessions/{session.id}\n")

    # Stream-first: open the stream, then send the kickoff.
    final_parts: list[str] = []
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": QUESTION}]}],
        )
        for event in stream:
            t = event.type

            # Route every event through the filter dispatcher. No-op unless it's a
            # custom tool call we've registered — this is the whole integration.
            if router.dispatch(session.id, event):
                continue

            if t == "session.thread_created":
                print(f"  [thread]     {getattr(event, 'agent_name', '?')}", flush=True)
            elif t == "agent.thread_message_received":
                print(f"  [reply <-]   {getattr(event, 'from_agent_name', '?')}", flush=True)
            elif t == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        final_parts.append(block.text)
                        print(block.text, end="", flush=True)
            elif t == "session.status_idle":
                # requires_action = waiting on us (e.g. a tool result); keep going.
                if getattr(event.stop_reason, "type", None) != "requires_action":
                    break
            elif t == "session.status_terminated":
                break

    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/legal-assessment.txt").write_text("".join(final_parts))
    print("\n\n[panel finished] -> outputs/legal-assessment.txt")


if __name__ == "__main__":
    main()
