"""
Offline tests for the source filter — no API key required.

The deterministic gates (date cutoff, denylist, case number) are tested for real.
The Haiku gate is exercised with a stubbed censor so the pipeline wiring and the
fail-closed behaviour are verified without a network call. Run:

    python -m legal_eval.test_source_filter
"""

from __future__ import annotations

from datetime import date

from . import source_filter as sf
from .source_filter import SearchResult, filter_results
from .precedent_search import bger_backend, parse_bger_results, _FIXTURE_RESULTS_HTML


_SENTINEL_CLIENT = object()  # never used once _haiku_censor is stubbed


def _corpus() -> list[SearchResult]:
    return [
        SearchResult("OBG statute", "https://www.fedlex.admin.ch/eli/cc/2017/802/de",
                     "Ordnungsbussengesetz ...", date(2018, 1, 1)),
        SearchResult("StGB Art. 103", "https://www.fedlex.admin.ch/eli/cc/54/757/de",
                     "Übertretungen ...", date(1937, 12, 21)),
        # post-cutoff news reporting the outcome -> date gate
        SearchResult("NZZ: Bundesgericht klärt Parkbussen",
                     "https://www.nzz.ch/schweiz/parkbusse-ld.999999",
                     "Leiturteil zu unbezahlten Parkbussen ...", date(2026, 6, 16)),
        # the decision itself -> date gate (also domain + case number)
        SearchResult("Urteil 6B_998/2024", "https://www.bger.ch/...docid=6B_998-2024",
                     "Das Bundesgericht ...", date(2026, 6, 15)),
        # UNDATED case note that paraphrases the holding -> only the censor can catch it
        SearchResult("Case note: was das Gericht zu Parkbussen sagte",
                     "https://example-kanzlei.ch/notes/parkbusse",
                     "Das Bundesgericht hat entschieden, dass unbezahlte Parkbussen ...",
                     None),
    ]


def _fake_censor(client, candidates, *, fail_closed=True):
    """Block any survivor whose snippet reports a court holding; allow the rest."""
    out = {}
    for idx, r in candidates:
        reveals = "das bundesgericht hat entschieden" in r.snippet.lower()
        out[idx] = (reveals, "reveals holding" if reveals else "background only")
    return out


def test_date_cutoff() -> None:
    assert sf._fails_date_cutoff(_corpus()[2]) is True   # NZZ, 2026-06-16
    assert sf._fails_date_cutoff(_corpus()[3]) is True   # decision, 2026-06-15
    assert sf._fails_date_cutoff(_corpus()[0]) is False  # OBG, 2018
    assert sf._fails_date_cutoff(_corpus()[4]) is False  # undated -> falls through
    print("  ok  date cutoff")


def test_full_pipeline() -> None:
    sf._haiku_censor, real = _fake_censor, sf._haiku_censor
    try:
        outcome = filter_results(_corpus(), client=_SENTINEL_CLIENT)
    finally:
        sf._haiku_censor = real

    allowed_urls = {r.url for r in outcome.allowed}
    blocked = {(r.url, r.stage) for r in outcome.redactions}

    # the two statutes survive everything
    assert any("fedlex" in u and "802" in u for u in allowed_urls)
    assert len(outcome.allowed) == 2, outcome.allowed

    stages = {stage for _, stage in blocked}
    assert "date_cutoff" in stages       # NZZ + decision caught by date
    assert "haiku" in stages             # undated case note caught by censor
    # the undated leak was NOT caught by a deterministic gate — the censor earned it
    assert any("notes/parkbusse" in u and s == "haiku" for u, s in blocked)
    print("  ok  full pipeline (date cutoff + censor)")


def test_fail_closed() -> None:
    # Mirror the real censor's fail-closed contract: when the model call errors,
    # every survivor is dropped rather than passed through.
    def failed_closed(client, candidates, *, fail_closed=True):
        return {idx: (True, "failed closed") for idx, _ in candidates}

    sf._haiku_censor, real = failed_closed, sf._haiku_censor
    try:
        outcome = filter_results(_corpus(), client=_SENTINEL_CLIENT)
    finally:
        sf._haiku_censor = real

    assert outcome.allowed == [], "fail-closed must drop everything on censor failure"
    print("  ok  fail-closed on censor failure")


def test_precedent_parser() -> None:
    results = parse_bger_results(_FIXTURE_RESULTS_HTML)
    dockets = {r.title for r in results}
    assert "Bundesgericht 6B_1123/2018" in dockets
    assert "Bundesgericht 6B_998/2024" in dockets   # target is present in raw results
    # docid gives us date + a real decision URL
    target = next(r for r in results if "998" in r.title)
    assert target.published == date(2026, 6, 15)
    assert "show_document" in target.url and "6B_998-2024" in target.url
    print("  ok  precedent parser extracts docket + date + url")


def test_precedent_filtering() -> None:
    import os
    os.environ["BGER_OFFLINE"] = "1"
    raw = bger_backend("Parkbusse Ordnungsbusse")

    # Precedents pass; the target ruling is dropped by the date cutoff (newer than
    # any precedent). The censor is the backstop for anything that slips the date.
    sf._haiku_censor, real = _fake_censor, sf._haiku_censor
    try:
        outcome = filter_results(raw, client=_SENTINEL_CLIENT)
    finally:
        sf._haiku_censor = real
        del os.environ["BGER_OFFLINE"]

    allowed = {r.title for r in outcome.allowed}
    blocked = {(r.url, r.stage) for r in outcome.redactions}

    # precedents survive; the target ruling does not
    assert "Bundesgericht 6B_1123/2018" in allowed
    assert "Bundesgericht 6B_242/2020" in allowed
    assert not any("998" in t for t in allowed), f"target leaked: {allowed}"
    assert any("6B_998-2024" in u for u, _ in blocked)
    print("  ok  precedent search returns precedents, drops the protected ruling")


if __name__ == "__main__":
    test_date_cutoff()
    test_full_pipeline()
    test_fail_closed()
    test_precedent_parser()
    test_precedent_filtering()
    print("\nAll source-filter tests passed.")
