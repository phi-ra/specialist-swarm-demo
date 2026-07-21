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


def test_deterministic_gates() -> None:
    # date cutoff
    assert sf._fails_date_cutoff(_corpus()[2]) is True   # NZZ, 2026-06-16
    assert sf._fails_date_cutoff(_corpus()[0]) is False  # OBG, 2018
    assert sf._fails_date_cutoff(_corpus()[4]) is False  # undated -> falls through
    # denylist + case number
    assert sf._domain_blocked("https://www.bger.ch/x") is True
    assert sf._domain_blocked("https://www.fedlex.admin.ch/x") is False
    for variant in ["6B_998/2024", "6B 998/2024", "6B.998/2024", "BGer 6B_998-2024"]:
        assert sf._names_the_case(variant), variant
    assert not sf._names_the_case("6B_997/2024")
    print("  ok  deterministic gates")


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


if __name__ == "__main__":
    test_deterministic_gates()
    test_full_pipeline()
    test_fail_closed()
    print("\nAll source-filter tests passed.")
