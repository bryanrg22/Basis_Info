"""Read our accounts' rate limits from the providers' response headers.

``python tooling/load/provider_limits.py [--models gpt-6.1-sol,claude-opus-5-5,...]``

One minimal request per model (a few tokens in, at most 16 out: well under a cent in
total) with the keys in ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``; prints and writes
(``--out``) only the rate-limit headers, never a key. The limits are per organization and
per model (OpenAI ``x-ratelimit-limit-requests`` / ``-tokens``; Anthropic
``anthropic-ratelimit-{requests,input-tokens,output-tokens}-limit``), so these are the
numbers the ceilings in LOAD_TESTING.md section 6 are computed from.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MODELS = "gpt-6.1-sol,gpt-6-luna,claude-opus-5-5,claude-sonnet-5-5"


def openai_limits(client: httpx.Client, model: str) -> dict[str, Any]:
    r = client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Say ok."}],
            "max_completion_tokens": 16,
            "reasoning_effort": "low",
        },
    )
    return {"status": r.status_code, **{
        k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit-")
    }}


def anthropic_limits(client: httpx.Client, model: str) -> dict[str, Any]:
    r = client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        json={"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "ok"}]},
    )
    return {"status": r.status_code, **{
        k: v for k, v in r.headers.items() if k.lower().startswith("anthropic-ratelimit-")
    }}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out: dict[str, Any] = {}
    with httpx.Client(timeout=60) as client:
        for model in args.models.split(","):
            fn = anthropic_limits if model.startswith("claude-") else openai_limits
            try:
                out[model] = fn(client, model)
            except (KeyError, httpx.HTTPError) as exc:
                out[model] = {"error": type(exc).__name__}
    text = json.dumps(out, indent=1, sort_keys=True)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")


if __name__ == "__main__":
    main()
