# legal_eval — filtered web access for the legal-reasoning swarm

Specialists evaluate a legal question from different angles. They may search the
web, but they must **not** be able to find one specific recent ruling —
Bundesgericht **6B_998/2024** (15 June 2026, on whether an unpaid parking fine is
a criminal or civil matter) — or any news/commentary reporting on it. Otherwise
we'd be testing their ability to copy the answer, not to reason.

## Why a custom tool (and not `blocked_domains`)

The managed agent toolset (`agent_toolset_20260401`) runs `web_search` / `web_fetch`
**server-side**, so there's no client-side hook to inspect results. To filter, we
own the retrieval path: the built-in web tools are disabled and specialists get a
custom `legal_web_search` tool whose results we fetch and sanitise before handing
back.

## The filter: two deterministic gates + one semantic backstop

Applied cheapest-first, fail-closed (`source_filter.filter_results`):

1. **Date cutoff** — drop anything dated on/after `FREEZE_DATE` (2026-05-01).
   One rule kills the decision *and* all commentary about it.
2. **Regex + denylist** — drop anything naming the docket (all spellings:
   `6B_998/2024`, `6B 998/2024`, `6B.998-2024`, the bger.ch docid) or served from
   a primary-source domain (bger.ch, entscheidsuche.ch, …).
3. **Haiku censor** — a `claude-haiku-4-5` call with structured output rules on
   each survivor: does it *report on* the ruling or reveal its holding, even
   paraphrased and undated? This catches what the first two miss. If Haiku errors
   or returns no verdict, the result is **dropped** (a false block is cheap; a
   leak is not).

Every drop is recorded in `FilterOutcome.redactions` (url, stage, reason) so you
can audit — prevention *and* detection.

## Files

| File | What |
|---|---|
| `source_filter.py` | The filter pipeline. Backend-agnostic; the piece to reuse. |
| `filtered_search.py` | `legal_web_search` tool def, backends, `make_filtered_search`, `filtered_toolset`. |
| `router.py` | `CustomToolRouter` — register + dispatch custom tool calls (with the filter). |
| `create_legal_specialists.py` | The angle-specialists (native web off, filtered tool on). |
| `create_legal_coordinator.py` | The coordinator that fans out to the panel and synthesises. |
| `run_legal_eval.py` | The run loop: stream events, dispatch tool calls through the router. |
| `test_source_filter.py` | Offline tests (stubbed censor) + deterministic-gate tests. |
| `INTEGRATION.md` | **How to give future specialists filtered web access.** |

## Architecture

```
run_legal_eval  ──starts──▶  Coordinator (opus)  ──delegates──▶  3 angle-specialists (sonnet)
      │                                                                  │
      │  every event                                    legal_web_search │ (custom tool)
      ▼                                                                  ▼
 CustomToolRouter.dispatch ───▶ make_filtered_search(backend) ───▶ filter_results
                                                                   (date cutoff → denylist
                                                                    → case# → Haiku censor)
```

All web access is a **filtered custom tool** running in the run loop; the agents
have no native `web_search`. Adding a new filtered tool later is 3 lines — see
`INTEGRATION.md`.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# offline tests (no key needed for the deterministic gates)
.venv/bin/python -m legal_eval.test_source_filter

# see the filter block the leaks and pass the statutes
.venv/bin/python -c "from legal_eval.filtered_search import run_legal_web_search; \
print(run_legal_web_search('Parkbusse Strafrecht Schweiz'))"

# full swarm (needs managed-agents access on your workspace)
.venv/bin/python -m legal_eval.create_legal_specialists
.venv/bin/python -m legal_eval.create_legal_coordinator
.venv/bin/python -m legal_eval.run_legal_eval        # -> outputs/legal-assessment.txt
```

## Search backend

`search_backend()` uses **Tavily** if `TAVILY_API_KEY` is set (real results with
publish dates), otherwise a small **offline fixture corpus** so the demo/tests run
with no third-party key. Swap in Brave/Serper by editing that one function — the
filter doesn't care where results come from.

## Tuning the isolation

- `FREEZE_DATE` and `PROTECTED_TOPIC` in `source_filter.py` define "the answer".
- To make it fully airtight for a real eval, add a **whitelist**: restrict the
  backend to primary-law domains (fedlex.admin.ch) — default-deny beats
  default-block. The censor then only has to guard the gaps.
