#!/usr/bin/env python
"""Predict whether a criminal-case request is GRANTED or REJECTED.

Loads the trained LightGBM classifier (lgb.LGBMClassifier) from
data/02_models/lgbm_criminal_cases_model.pkl and scores a single case
described by six features.

Feature order and encoding must match training (see notebooks/mini-model.ipynb):
    year              int    e.g. 2024
    merged_cases      bool   False->0, True->1
    n_judges          number 1 / 3 / 5
    proc_duration     number months, e.g. 12
    app_represented   bool   applicant represented by counsel
    resp_represented  bool   respondent represented by counsel

Target: outcome_binary where 1 == "granted", 0 == everything else (rejected).
"""
import argparse
import json
import os
import sys

FEATURES = [
    "year",
    "merged_cases",
    "n_judges",
    "proc_duration",
    "app_represented",
    "resp_represented",
]

# Distribution of the model's raw P(granted) score across the training cases
# (percentile -> score). Granted is rare (~9% base rate), so absolute
# probabilities are tiny for almost everyone; what matters is how a case ranks
# against typical cases. Regenerate from data/01_raw if the model changes.
PCTL_TABLE = [
    (0, 0.00000),
    (5, 0.00000),
    (10, 0.00000),
    (15, 0.00000),
    (20, 0.00001),
    (25, 0.00001),
    (30, 0.00003),
    (35, 0.00033),
    (40, 0.00646),
    (45, 0.01617),
    (50, 0.02847),
    (55, 0.04197),
    (60, 0.05759),
    (65, 0.07514),
    (70, 0.09651),
    (75, 0.12089),
    (80, 0.15633),
    (85, 0.20182),
    (90, 0.26690),
    (95, 0.40112),
    (97, 0.52524),
    (99, 0.75501),
    (100, 0.99841),
]

# Relative likelihood bands keyed on the percentile rank of the score. Because
# raw probabilities are almost always low, we classify by how a case compares
# to the population of past cases rather than by an absolute 0.5 threshold.
# (min_percentile_inclusive, label)
BANDS = [
    (95, "HIGHLY LIKELY"),
    (85, "MUCH MORE LIKELY"),
    (60, "MORE LIKELY"),
    (0, "NOT LIKELY"),
]


def percentile_rank(score):
    """Where the given score falls in the training-score distribution (0-100)."""
    lo = PCTL_TABLE[0]
    hi = PCTL_TABLE[-1]
    if score <= lo[1]:
        return float(lo[0])
    if score >= hi[1]:
        return float(hi[0])
    for (p0, s0), (p1, s1) in zip(PCTL_TABLE, PCTL_TABLE[1:]):
        if s0 <= score <= s1:
            if s1 == s0:
                return float(p1)
            frac = (score - s0) / (s1 - s0)
            return round(p0 + frac * (p1 - p0), 1)
    return float(hi[0])


def likelihood_band(pct):
    for min_pct, label in BANDS:
        if pct >= min_pct:
            return label
    return BANDS[-1][1]

# Default model location, resolved relative to the repo root (three levels up
# from this script: .claude/skills/predict-case-outcome/predict.py).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_MODEL = os.path.join(_REPO_ROOT, "data", "02_models", "lgbm_criminal_cases_model.pkl")


def parse_bool(value):
    """Accept true/false, yes/no, 1/0 (case-insensitive) -> 0/1 int.

    Training used LabelEncoder on the string form of the booleans, which sorts
    'False' -> 0 and 'True' -> 1, so this mapping matches the model.
    """
    s = str(value).strip().lower()
    if s in ("true", "t", "yes", "y", "1"):
        return 1
    if s in ("false", "f", "no", "n", "0"):
        return 0
    raise argparse.ArgumentTypeError(f"expected a boolean (true/false), got {value!r}")


def prompt_value(label, caster, help_text):
    """Interactively ask the user for one feature until a valid value is given."""
    while True:
        raw = input(f"  {label} ({help_text}): ").strip()
        try:
            return caster(raw)
        except (ValueError, argparse.ArgumentTypeError):
            print(f"    -> invalid value {raw!r}, try again.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Feature args are optional: any that are omitted are asked for interactively.
    p.add_argument("--year", type=int, help="Filing year, e.g. 2024")
    p.add_argument("--merged_cases", type=parse_bool, help="Were cases merged? true/false")
    p.add_argument("--n_judges", type=float, help="Number of judges (1, 3, or 5)")
    p.add_argument("--proc_duration", type=float, help="Proceeding duration (months)")
    p.add_argument("--app_represented", type=parse_bool, help="Applicant represented? true/false")
    p.add_argument("--resp_represented", type=parse_bool, help="Respondent represented? true/false")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Path to the .pkl model")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    args = p.parse_args()

    # Prompt for any feature not supplied on the command line.
    specs = [
        ("year", int, "e.g. 2024"),
        ("merged_cases", parse_bool, "true/false"),
        ("n_judges", float, "1, 3, or 5"),
        ("proc_duration", float, "months, e.g. 12"),
        ("app_represented", parse_bool, "true/false"),
        ("resp_represented", parse_bool, "true/false"),
    ]
    missing = [name for name, _, _ in specs if getattr(args, name) is None]
    if missing:
        if args.json:
            print(f"ERROR: missing required features in --json mode: {', '.join(missing)}", file=sys.stderr)
            sys.exit(2)
        if not sys.stdin.isatty():
            print(f"ERROR: no value for {', '.join(missing)} and stdin is not interactive.", file=sys.stderr)
            sys.exit(2)
        print("Enter the case features (press Enter after each):")
        for name, caster, help_text in specs:
            if getattr(args, name) is None:
                setattr(args, name, prompt_value(name, caster, help_text))
        print()

    try:
        import joblib
        import pandas as pd
    except ImportError as e:
        print(f"ERROR: missing dependency ({e}). Run with the .cenv_a_hackathon conda env.", file=sys.stderr)
        sys.exit(2)

    if not os.path.exists(args.model):
        print(f"ERROR: model not found at {args.model}", file=sys.stderr)
        sys.exit(2)

    model = joblib.load(args.model)

    row = {
        "year": args.year,
        "merged_cases": args.merged_cases,
        "n_judges": args.n_judges,
        "proc_duration": args.proc_duration,
        "app_represented": args.app_represented,
        "resp_represented": args.resp_represented,
    }
    X = pd.DataFrame([[row[f] for f in FEATURES]], columns=FEATURES)

    # class 1 == granted. Raw scores are almost always low, so we classify by
    # relative rank against the training distribution rather than a 0.5 cutoff.
    proba_granted = float(model.predict_proba(X)[0, 1])
    pct = percentile_rank(proba_granted)
    band = likelihood_band(pct)

    result = {
        "likelihood": band,
        "raw_probability_granted": round(proba_granted, 6),
        "percentile_vs_training": pct,
        "features": row,
    }

    if args.json:
        print(json.dumps(result))
        return

    print("=" * 52)
    print("  Criminal case outcome prediction")
    print("=" * 52)
    for f in FEATURES:
        print(f"  {f:<18}: {row[f]}")
    print("-" * 52)
    print(f"  GRANTING IS        : {band}")
    print(f"  raw P(granted)     : {proba_granted:.2%}")
    print(f"  ranks higher than  : ~{pct:.0f}% of past criminal cases")
    print("=" * 52)


if __name__ == "__main__":
    main()
