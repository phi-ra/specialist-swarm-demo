"""
CustomToolRouter — one place to register and dispatch custom tool calls.

The run loop calls `router.dispatch(session_id, event)` for every event; the
router runs the matching handler and sends the `user.custom_tool_result` back
(echoing `session_thread_id` so multiagent thread routing works).

Adding a new custom tool later is three lines:

    router.register("case_law_search", make_filtered_search(case_law_backend))

Any handler you register through `make_filtered_search` gets the date cutoff +
Haiku censor for free. See INTEGRATION.md.
"""

from __future__ import annotations

from typing import Callable

from anthropic import Anthropic

from .filtered_search import LEGAL_WEB_SEARCH_TOOL, make_filtered_search, search_backend

# A handler takes the tool's input dict and returns the result text.
Handler = Callable[[dict], str]


class CustomToolRouter:
    def __init__(self, client: Anthropic | None = None):
        self.client = client or Anthropic()
        self._handlers: dict[str, Handler] = {}

    def register(self, name: str, handler: Handler) -> "CustomToolRouter":
        self._handlers[name] = handler
        return self  # chainable

    @property
    def names(self) -> set[str]:
        return set(self._handlers)

    def dispatch(self, session_id: str, event) -> bool:
        """
        If `event` is an `agent.custom_tool_use` for a registered tool, run it and
        reply. Returns True if handled. Safe to call on every event.
        """
        if getattr(event, "type", None) != "agent.custom_tool_use":
            return False
        name = getattr(event, "name", None)
        handler = self._handlers.get(name)
        if handler is None:
            return False

        result_text = handler(event.input or {})

        reply = {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": event.id,
            "content": [{"type": "text", "text": result_text}],
        }
        thread_id = getattr(event, "session_thread_id", None)
        if thread_id is not None:
            reply["session_thread_id"] = thread_id

        self.client.beta.sessions.events.send(session_id=session_id, events=[reply])
        return True


def default_router(client: Anthropic | None = None) -> CustomToolRouter:
    """A router pre-wired with the filtered `legal_web_search` tool."""
    client = client or Anthropic()
    router = CustomToolRouter(client)
    router.register(
        LEGAL_WEB_SEARCH_TOOL["name"],
        make_filtered_search(search_backend, client=client),
    )
    return router
