"""Firebase ID tokens for load-test users on a deployed stack (staging).

``python tooling/load/mint_tokens.py --project PROJECT --api-key WEB_API_KEY -n 25 > tokens.json``

The deployed API verifies real Firebase ID tokens (the emulator is refused outside dev and
test), so each load-test engineer (``load-eng-0`` ... ``load-eng-<n-1>``, the uids
``locustfile.py`` uses) needs one. For each uid: a custom token from the Admin SDK
(application default credentials of an account allowed to mint them in PROJECT), then
the Identity Toolkit ``signInWithCustomToken`` exchange with the project's web API key,
which returns an ID token valid for an hour. Signing in a uid the first time **creates
that user** in the project's Firebase Auth: run this only against a staging project, and
delete the users afterwards if wanted (``--delete`` does that instead of minting).
Tokens are written to stdout as JSON ``{uid: token}``; keep the file out of the repo.
"""

from __future__ import annotations

import argparse
import json
import sys

import firebase_admin
import httpx
from firebase_admin import auth

EXCHANGE = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--api-key", required=True, help="the Firebase web API key")
    parser.add_argument("-n", type=int, default=25)
    parser.add_argument("--uid-prefix", default="load-eng")
    parser.add_argument("--delete", action="store_true", help="delete the users instead")
    args = parser.parse_args()
    firebase_admin.initialize_app(options={"projectId": args.project})
    uids = [f"{args.uid_prefix}-{i}" for i in range(args.n)]
    if args.delete:
        result = auth.delete_users(uids)
        print(f"deleted {result.success_count}, failed {result.failure_count}", file=sys.stderr)
        return
    tokens = {}
    with httpx.Client(timeout=30) as client:
        for uid in uids:
            custom = auth.create_custom_token(uid).decode()
            r = client.post(
                EXCHANGE,
                params={"key": args.api_key},
                json={"token": custom, "returnSecureToken": True},
            )
            r.raise_for_status()
            tokens[uid] = r.json()["idToken"]
    json.dump(tokens, sys.stdout)


if __name__ == "__main__":
    main()
