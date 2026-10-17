"""Add the model-provider keys to the staging Secret Manager secrets.

Reads each key from the operator's environment first, then from a dotenv file
(``--env-file``, default ``backEnd/.env``), strips surrounding whitespace (the local
``REPLICATE_API_TOKEN`` has a trailing one), and pipes it to ``gcloud secrets versions
add --data-file=-``. A value is never printed, logged or passed on a command line; the
output names the secret and the version added, or why it was skipped.

A secret whose latest version already holds the same value is skipped (compared by
SHA-256 inside this process), so re-running adds no versions.

    uv run --project backEnd python infra/staging/scripts/load_secrets.py
    uv run --project backEnd python infra/staging/scripts/load_secrets.py \
        --only staging-openai-api-key
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

PROJECT = "cost-seg-basis"

# Secret Manager name -> the environment variable that holds the value locally.
SECRETS: dict[str, str] = {
    "staging-openai-api-key": "OPENAI_API_KEY",
    "staging-anthropic-api-key": "ANTHROPIC_API_KEY",
    "staging-azure-di-key": "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "staging-replicate-api-token": "REPLICATE_API_TOKEN",
}


def latest_digest(secret: str, project: str) -> str | None:
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--secret",
            secret,
            "--project",
            project,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def add_version(secret: str, value: str, project: str) -> str:
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            secret,
            "--project",
            project,
            "--data-file=-",
            "--format=value(name)",
        ],
        input=value.encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        # gcloud's error names the secret and the permission, never the payload.
        raise SystemExit(f"{secret}: gcloud failed: {result.stderr.decode().strip()}")
    return result.stdout.decode().strip().rsplit("/", 1)[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file", type=Path, default=Path("backEnd/.env"))
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--only", action="append", choices=sorted(SECRETS), default=[])
    args = parser.parse_args(argv)

    from_file = dotenv_values(args.env_file) if args.env_file.is_file() else {}
    status = 0
    for secret, variable in SECRETS.items():
        if args.only and secret not in args.only:
            continue
        raw = os.environ.get(variable) or from_file.get(variable) or ""
        value = raw.strip()
        if not value:
            print(f"{secret}: skipped, {variable} is not set in the environment or {args.env_file}")
            status = 1
            continue
        if latest_digest(secret, args.project) == hashlib.sha256(value.encode()).hexdigest():
            print(f"{secret}: unchanged (latest version already holds this value)")
            continue
        print(f"{secret}: added version {add_version(secret, value, args.project)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
