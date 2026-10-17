"""Studies per hour each provider allows: ``python tooling/load/ceilings.py LIMITS.json``.

Standard library only. Per study, the requests and tokens each model takes come from the
latency profile of the live study (``backEnd/tests/load/latency_profile_live_27_8.json``:
every call's count and mean tokens), with the per-photo surfaces call moved to its current
model (``--surfaces-model``, gpt-6-luna since EVAL_REPORT 28). The limits are our
accounts' own, read from the providers' response headers by ``provider_limits.py``
(``LIMITS.json``); Replicate and Azure DI publish theirs (constants below).

The ceiling of one dimension is ``limit per minute x 60 / use per study``; a provider's
ceiling is its tightest dimension. It is the sustained rate: the limits are token
buckets (Anthropic) or per-minute windows refilled continuously (OpenAI), so a burst of
studies that all classify in the same minute meets the limit sooner (EVAL_REPORT 31.5).
OpenAI counts the tokens actually used, not ``max_tokens`` (measured: a request with
``max_completion_tokens=6000`` took 4 to 17 tokens off ``x-ratelimit-remaining-tokens``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

PROFILE = (
    Path(__file__).resolve().parents[2]
    / "backEnd" / "tests" / "load" / "latency_profile_live_27_8.json"
)
# Published limits (2026-10-17): Replicate, creates per minute (docs "Rate limits"; 60 a
# minute with a burst of 5 while the account has under $10 of credit, from its 429 body,
# EVAL_REPORT 30.2); Azure DI, analyze requests per second per resource (service limits).
REPLICATE_CREATES_PER_MIN = 600
REPLICATE_LOW_CREDIT_PER_MIN = 60
AZURE_DI_ANALYZE_PER_SECOND = 15
SAM3_PREDICTIONS_PER_STUDY = 52  # one per photo


def per_study(profile: Dict[str, Any], surfaces_model: str) -> Dict[str, Dict[str, float]]:
    """``{model: {requests, input_tokens, output_tokens}}`` for one live-shaped study."""
    use: Dict[str, Dict[str, float]] = {}
    for key, entry in profile["llm"].items():
        family, model = key.split("@", 1)
        if family == "photo_surfaces":
            model = surfaces_model
        row = use.setdefault(model, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        row["requests"] += entry["calls"]
        row["input_tokens"] += entry["calls"] * entry["input_tokens"]
        row["output_tokens"] += entry["calls"] * entry["output_tokens"]
    return use


def ceilings(use: Dict[str, Dict[str, float]], limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for model, u in sorted(use.items()):
        headers = {k.lower(): v for k, v in limits.get(model, {}).items()}
        dims: Dict[str, float] = {}
        if model.startswith("claude-"):
            pairs = (
                ("RPM", "anthropic-ratelimit-requests-limit", u["requests"]),
                ("ITPM", "anthropic-ratelimit-input-tokens-limit", u["input_tokens"]),
                ("OTPM", "anthropic-ratelimit-output-tokens-limit", u["output_tokens"]),
            )
        else:
            pairs = (
                ("RPM", "x-ratelimit-limit-requests", u["requests"]),
                ("TPM", "x-ratelimit-limit-tokens", u["input_tokens"] + u["output_tokens"]),
            )
        limit_text = []
        for name, header, used in pairs:
            if header in headers and used:
                limit = float(headers[header])
                dims[name] = limit * 60 / used
                limit_text.append(f"{name} {int(limit):,}")
        bound = min(dims, key=lambda k: dims[k]) if dims else None
        rows.append({
            "provider_model": model,
            "per_study": {k: round(v) for k, v in u.items()},
            "limits": ", ".join(limit_text) or "not read",
            "ceiling_studies_per_hour": round(dims[bound]) if bound else None,
            "binding": bound,
        })
    rows.append({
        "provider_model": "replicate (SAM 3)",
        "per_study": {"requests": SAM3_PREDICTIONS_PER_STUDY},
        "limits": f"{REPLICATE_CREATES_PER_MIN} creates/min ({REPLICATE_LOW_CREDIT_PER_MIN} "
                  "under $10 credit)",
        "ceiling_studies_per_hour": round(REPLICATE_CREATES_PER_MIN * 60 / SAM3_PREDICTIONS_PER_STUDY),
        "ceiling_low_credit": round(REPLICATE_LOW_CREDIT_PER_MIN * 60 / SAM3_PREDICTIONS_PER_STUDY),
        "binding": "creates/min",
    })
    rows.append({
        "provider_model": "azure document intelligence",
        "per_study": {"requests": 1},
        "limits": f"{AZURE_DI_ANALYZE_PER_SECOND} analyze/s per resource",
        "ceiling_studies_per_hour": AZURE_DI_ANALYZE_PER_SECOND * 3600,
        "binding": "analyze/s",
    })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("limits", type=Path, help="provider_limits.py output")
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--surfaces-model", default="gpt-6-luna")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    rows = ceilings(per_study(profile, args.surfaces_model), json.loads(args.limits.read_text()))
    print("| Provider / model | Per study | Our limit | Ceiling (studies/hour) | Binding |")
    print("|---|---|---|---|---|")
    for r in rows:
        per = ", ".join(f"{k} {v:,}" for k, v in r["per_study"].items())
        print(f"| {r['provider_model']} | {per} | {r['limits']} | "
              f"{r['ceiling_studies_per_hour']:,} | {r['binding']} |")


if __name__ == "__main__":
    main()
