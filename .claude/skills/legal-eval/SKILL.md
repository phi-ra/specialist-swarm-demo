---
name: legal-eval
description: Run the legal-reasoning specialist swarm with filtered web access. A panel of angle-specialists evaluates a Swiss-law question and a coordinator synthesises one reasoned answer, while a filter (date cutoff + Haiku censor) prevents any specialist from finding the protected ruling. Use whenever the user wants to run the legal panel / legal-eval swarm, evaluate a legal question with the swarm, or demo the filtered-web-access setup in legal_eval/.
---

# Legal-Eval Swarm

Runs the `legal_eval/` package: a coordinator fans a question out to a panel of
legal specialists, every web search they make is routed through a fail-closed
filter (`source_filter.filter_results`), and the coordinator returns a single
synthesised assessment. See `legal_eval/README.md` and `legal_eval/INTEGRATION.md`
for the design.

**The panel (4 angles):** Criminal-Law, Administrative/Civil-Law, Procedural, and
a **Precedent Analyst** that searches Swiss Federal Supreme Court decisions on
bger.ch via the custom `precedent_search` tool. All web access — the general
`legal_web_search` and the `precedent_search` tool — is routed through the same
filter.

**The filter (2 gates, fail-closed):** applied cheapest-first —
1. **Date cutoff** — drop anything published on/after the freeze date.
2. **Haiku censor** — a `claude-haiku-4-5` call judges each survivor for whether
   it reports on / reveals the protected ruling (even paraphrased); on error or
   no verdict, the result is dropped.

(This replaced the earlier three-gate design; the regex/docket-denylist gate was
removed — the date cutoff plus the semantic censor cover it.)

## Step 1 — Preconditions

- Use this repo's conda env: `.cenv_a_hackathon`.
- `ANTHROPIC_API_KEY` must be set in the environment (all three scripts exit
  without it). If it isn't, tell the user to run `! export ANTHROPIC_API_KEY=...`
  in the session — do NOT invent a key.
- Optional: `TAVILY_API_KEY` for real web results. Without it the search backend
  falls back to its stub (`filtered_search.py`), which is fine for a demo of the
  filter but returns canned results.
- The swarm uses the managed-agents beta (`managed-agents-2026-04-01`).

Check keys first:

```bash
[ -n "$ANTHROPIC_API_KEY" ] && echo "ANTHROPIC_API_KEY set" || echo "MISSING ANTHROPIC_API_KEY"
[ -n "$TAVILY_API_KEY" ] && echo "TAVILY set (real web)" || echo "no TAVILY (stub backend)"
```

## Step 2 — Build the swarm (idempotent)

Each step writes an id file and is skipped on rerun if that file exists. Run in
order from the repo root:

```bash
# 1. Specialists  -> .legal_specialist_ids.json
.cenv_a_hackathon/bin/python -m legal_eval.create_legal_specialists

# 2. Coordinator  -> .legal_coordinator_id  (needs the specialists file)
.cenv_a_hackathon/bin/python -m legal_eval.create_legal_coordinator
```

## Step 3 — Run the panel

```bash
# Streams the panel fan-out; writes the final answer to outputs/legal-assessment.txt
.cenv_a_hackathon/bin/python -m legal_eval.run_legal_eval
```

`run_legal_eval.py` creates/reuses the environment (`.environment_id`), starts a
session against the coordinator, streams events, dispatches every custom tool
call (`legal_web_search` and `precedent_search`) through the filtering router,
and prints + saves the coordinator's synthesised assessment.

## Step 4 — Report

Relay the coordinator's final assessment (also saved to
`outputs/legal-assessment.txt`) and share the session trace URL the script
prints (`https://platform.claude.com/sessions/<id>`). If the demo point is the
filter, note that any results dated on/after the freeze date, or that the Haiku
censor judged to reveal the protected ruling, were dropped — the run logs each
`[filter] blocked …` drop for audit.

## Notes

- The question the panel answers lives in `run_legal_eval.py::QUESTION`. To ask
  something else, edit that constant (the protected ruling the filter guards is
  the Swiss `Parkbusse` case regardless of the question asked).
- After adding/removing specialists (e.g. the Precedent Analyst), rebuild the
  roster: delete `.legal_specialist_ids.json` and `.legal_coordinator_id`, then
  rerun Step 2 so the coordinator picks up the new panel.
- Offline sanity check of the filter (no API key needed):
  `.cenv_a_hackathon/bin/python -m pytest legal_eval/test_source_filter.py`
