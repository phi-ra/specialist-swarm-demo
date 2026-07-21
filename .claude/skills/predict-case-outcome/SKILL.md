---
name: predict-case-outcome
description: Predict how likely a criminal-case request is to be GRANTED using the trained LightGBM model in data/02_models. Use whenever the user wants to score, predict, or check the likely outcome of a criminal case. Collects six features (year, merged_cases, n_judges, proc_duration, app_represented, resp_represented), runs the model, and reports a relative likelihood band (NOT LIKELY / MORE LIKELY / MUCH MORE LIKELY / HIGHLY LIKELY).
---

# Predict Criminal Case Outcome

Scores a single criminal case with the trained `lgb.LGBMClassifier`
(`data/02_models/lgbm_criminal_cases_model.pkl`) and reports **how likely the
request is to be granted**, as a relative band.

Granting is rare in the training data (~9% base rate), so the model's raw
`P(granted)` is very low for almost every case. A 0.5 threshold would label
essentially everything "rejected" and tell the user nothing. Instead the skill
ranks the raw score against the distribution of scores across all past criminal
cases and reports a **relative likelihood band** — while still surfacing the raw
probability.

## Step 1 — Collect the six features

**When this skill is invoked, always collect these six inputs from the user
first — do NOT guess or assume defaults.** Use the `AskUserQuestion` tool (or
ask directly), then pass the answers to the script in Step 2.

| Feature | Meaning | Format |
|---|---|---|
| `year` | Filing/decision year | integer, e.g. `2024` |
| `merged_cases` | Were multiple cases merged? | true / false |
| `n_judges` | Number of judges on the panel | `1`, `3`, or `5` |
| `proc_duration` | Proceeding duration (months) | number, e.g. `12` |
| `app_represented` | Is the applicant represented by counsel? | true / false |
| `resp_represented` | Is the respondent represented by counsel? | true / false |

Booleans accept true/false, yes/no, or 1/0.

## Step 2 — Run the model

Run the bundled script with this repo's conda env (`.cenv_a_hackathon`), passing
the collected values:

```bash
.cenv_a_hackathon/bin/python .claude/skills/predict-case-outcome/predict.py \
  --year 2024 \
  --merged_cases false \
  --n_judges 3 \
  --proc_duration 12 \
  --app_represented true \
  --resp_represented false
```

Add `--json` for machine-readable output.

**Interactive fallback:** any feature flag you omit is prompted for on stdin, so
the script can also be run bare and will ask for each value itself:

```bash
.cenv_a_hackathon/bin/python .claude/skills/predict-case-outcome/predict.py
```

(In `--json` or non-interactive contexts, missing features are an error rather
than a prompt.)

## Step 3 — Report the result

Relay the likelihood band and the raw probability the script prints:

| Band | Meaning (percentile of raw score vs. past cases) |
|---|---|
| `NOT LIKELY` | below the 60th percentile — a typical, low-chance case |
| `MORE LIKELY` | 60th–85th percentile — above-average odds of granting |
| `MUCH MORE LIKELY` | 85th–95th percentile — strongly favours granting |
| `HIGHLY LIKELY` | 95th percentile and above — among the strongest cases |

Always state the band **and** the raw `P(granted)` and the "ranks higher than
~X% of past cases" figure, so the user understands these are *relative* odds:
even `HIGHLY LIKELY` can correspond to a raw probability well under 50%, because
granting is rare across the board. Class `1` = granted.
