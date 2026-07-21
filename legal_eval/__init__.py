"""Legal-evaluation swarm: filtered web access so specialists can't find the answer."""

from .source_filter import (
    FREEZE_DATE,
    PROTECTED_TOPIC,
    FilterOutcome,
    Redaction,
    SearchResult,
    filter_results,
)
from .filtered_search import (
    LEGAL_WEB_SEARCH_TOOL,
    filtered_toolset,
    format_outcome,
    make_filtered_search,
    run_legal_web_search,
    search_backend,
)
from .router import CustomToolRouter, default_router

__all__ = [
    # filter core
    "FREEZE_DATE",
    "PROTECTED_TOPIC",
    "FilterOutcome",
    "Redaction",
    "SearchResult",
    "filter_results",
    # search + tooling
    "LEGAL_WEB_SEARCH_TOOL",
    "filtered_toolset",
    "format_outcome",
    "make_filtered_search",
    "run_legal_web_search",
    "search_backend",
    # dispatch
    "CustomToolRouter",
    "default_router",
]
