"""Smoke-test a deployed Basis API end to end (docs/deploy/STAGING.md, "Smoke test").

Without a token (always):
  1. GET /health is 200, reports the expected git sha (--expect-sha) and checks.db ok,
     checks.temporal ok.
  2. GET /v1/studies without a token is a 401 problem.
  3. A CORS preflight from the frontend origin (--origin) is allowed.

With a Firebase ID token (--token-file, or BASIS_ID_TOKEN):
  4. POST /v1/studies creates a study.
  5. A tiny synthetic appraisal (one PDF page of made-up URAR fields) and one synthetic
     photo (a drawn room) are registered, PUT to their signed URLs exactly as a browser
     would (with the signed Content-Type and x-goog-content-length-range headers, and an
     Origin header so the bucket's CORS is exercised), and confirmed.
  6. POST /start, then GET /workflow until the workflow waits at its first review gate
     (awaiting appraisal_review) or --timeout passes.

Nothing here is client data: both documents are generated in memory. The token is read
from a file or the environment and is never printed.

    uv run --project backEnd python infra/staging/scripts/smoke.py https://<api> \
        --expect-sha <sha> --token-file ~/.basis-staging-token
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

FRONTEND_ORIGIN = "https://cost-seg-basis.web.app"


class SmokeFailure(RuntimeError):
    pass


def step(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
    if not ok:
        raise SmokeFailure(name)


def synthetic_appraisal() -> bytes:
    """One page of invented URAR-style fields; no real property or person."""
    import pymupdf

    lines = [
        "UNIFORM RESIDENTIAL APPRAISAL REPORT (SYNTHETIC TEST DOCUMENT)",
        "Property Address: 100 Example Street",
        "City: Testville   State: CA   Zip Code: 90000",
        "Borrower: Test Borrower   Owner of Public Record: Test Owner",
        "Year Built: 1995   Gross Living Area: 1,850 sq ft   Site Area: 6,000 sf",
        "Room Count: Total 7   Bedrooms 3   Baths 2.0",
        "Opinion of Market Value: $650,000   Effective Date of Appraisal: 01/15/2026",
        "Indicated Value by Cost Approach: $640,000   Site Value: $250,000",
    ]
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((54, 72 + 22 * i), line, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def synthetic_photo() -> bytes:
    """A drawn room: floor, wall, a window and an outlet (1024 x 768 JPEG)."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1024, 768), (226, 220, 205))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 560, 1024, 768), fill=(140, 100, 60))  # wood floor
    draw.rectangle((360, 150, 660, 400), fill=(170, 210, 240), outline=(250, 250, 250), width=14)
    draw.line((510, 150, 510, 400), fill=(250, 250, 250), width=8)  # window mullion
    draw.rectangle((820, 470, 850, 515), fill=(245, 245, 245), outline=(120, 120, 120))  # outlet
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=88)
    return out.getvalue()


def problem_ok(response: httpx.Response, status: int) -> bool:
    return response.status_code == status and response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


def check_unauthenticated(api: httpx.Client, expect_sha: str | None, origin: str) -> None:
    health = api.get("/health")
    body: dict[str, Any] = health.json() if health.status_code in (200, 503) else {}
    checks = body.get("checks", {})
    step("GET /health 200", health.status_code == 200, f"{health.status_code} {json.dumps(body)}")
    if expect_sha:
        step(
            "build is the expected commit",
            body.get("git_sha") == expect_sha,
            f"git_sha={body.get('git_sha')}",
        )
    step("database check ok", checks.get("db", {}).get("status") == "ok")
    step(
        "temporal check ok",
        checks.get("temporal", {}).get("status") == "ok",
        json.dumps(checks.get("temporal")),
    )

    unauth = api.get("/v1/studies")
    step(
        "GET /v1/studies without a token is a 401 problem",
        problem_ok(unauth, 401),
        str(unauth.status_code),
    )

    preflight = api.options(
        "/v1/studies",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
        },
    )
    step(
        f"CORS preflight from {origin} allowed",
        preflight.status_code == 200
        and preflight.headers.get("access-control-allow-origin") == origin,
        str(preflight.status_code),
    )


def post(api: httpx.Client, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    response = api.post(
        path,
        json=body,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
    )
    if response.status_code >= 300:
        raise SmokeFailure(f"POST {path}: {response.status_code} {response.text[:500]}")
    return response.json()


def upload(
    api: httpx.Client,
    token: str,
    study_id: str,
    kind: str,
    filename: str,
    content_type: str,
    data: bytes,
    origin: str,
) -> str:
    ticket = post(
        api,
        f"/v1/studies/{study_id}/documents",
        token,
        {
            "kind": kind,
            "filename": filename,
            "content_type": content_type,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    signed = ticket["upload"]
    host = httpx.URL(signed["url"]).host
    # The browser's preflight for the PUT, answered by the bucket's CORS configuration.
    preflight = httpx.options(
        signed["url"],
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": ",".join(h.lower() for h in signed["headers"]),
        },
    )
    step(
        f"{kind}: bucket CORS allows the browser PUT",
        preflight.status_code == 200
        and preflight.headers.get("access-control-allow-origin") == origin,
        str(preflight.status_code),
    )
    put = httpx.request(
        signed["method"],
        signed["url"],
        content=data,
        headers={**signed["headers"], "Origin": origin},
        timeout=60,
    )
    step(f"{kind}: signed PUT to {host}", put.status_code == 200, str(put.status_code))
    document_id: str = ticket["document"]["id"]
    done = post(api, f"/v1/studies/{study_id}/documents/{document_id}/complete", token)
    step(f"{kind}: upload confirmed", done.get("uploaded_at") is not None)
    return document_id


def run_study(api: httpx.Client, token: str, origin: str, timeout_s: float) -> None:
    study = post(
        api,
        "/v1/studies",
        token,
        {"property_name": f"Staging smoke {time.strftime('%Y-%m-%d %H:%M')}"},
    )
    study_id = study["id"]
    step("POST /v1/studies", bool(study_id), study_id)

    upload(
        api,
        token,
        study_id,
        "appraisal",
        "synthetic-appraisal.pdf",
        "application/pdf",
        synthetic_appraisal(),
        origin,
    )
    upload(
        api, token, study_id, "photo", "1_living_room.jpg", "image/jpeg", synthetic_photo(), origin
    )

    started = post(api, f"/v1/studies/{study_id}/start", token)
    step("POST /start", bool(started.get("workflow_id")), started.get("workflow_id", ""))

    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        response = api.get(
            f"/v1/studies/{study_id}/workflow", headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 200:
            last = response.json()
            progress = last.get("status") or {}
            print(
                f"  +{time.monotonic() - t0:5.0f}s {last.get('execution_status')} "
                f"stage={progress.get('stage')} awaiting={progress.get('awaiting')} "
                f"photos {progress.get('completed_photos')}/{progress.get('pending_photos')}"
            )
            if last.get("execution_status") not in (None, "RUNNING"):
                break
            if progress.get("awaiting"):
                break
        time.sleep(10)
    progress = last.get("status") or {}
    step(
        "workflow reached its first review gate",
        progress.get("awaiting") == "appraisal_review",
        f"execution {last.get('execution_status')}, awaiting {progress.get('awaiting')}, "
        f"study {study_id}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base_url")
    parser.add_argument("--expect-sha")
    parser.add_argument("--origin", default=FRONTEND_ORIGIN)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the gate")
    args = parser.parse_args(argv)

    token = os.environ.get("BASIS_ID_TOKEN", "")
    if args.token_file:
        token = args.token_file.expanduser().read_text().strip()
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30) as api:
            check_unauthenticated(api, args.expect_sha, args.origin)
            if not token:
                print("No ID token given: the authenticated study flow was not run.")
                return 0
            run_study(api, token, args.origin, args.timeout)
    except SmokeFailure as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
