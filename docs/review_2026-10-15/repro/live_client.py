"""Live end-to-end client: plays the frontend against the running API (v0.7.0-live, v0.9).

API only for every action. At each gate it also snapshots the database (read-only psql)
for the record, then approves with no corrections. Outputs go to OUT (scratchpad; holds
client data, never committed).

v0.9 (EVAL_REPORT section 27): ``ROOM_ALIASES`` (a JSON file of ``{room id: room id}``)
is sent with the study, as the engineer would; at each gate the gate's contents are read
through the new routes (``/fields``, ``/rooms``, ``/assets``) and saved; the photo
stage's progress when the appraisal gate opens is logged (the gate no longer waits for
it); a decision answered 409 ``gate-not-open`` is retried; ``PG_DB`` names the database
in the ``basis-live-pg`` container (default ``basis``).

Run from ``backEnd/`` with the API, worker and relay up (docs/test-evidence/v0.7.0-live),
``FIREBASE_PROJECT_ID`` and ``FIREBASE_AUTH_EMULATOR_HOST`` set as for the API, ``API`` the
API's base URL and the database in the ``basis-live-pg`` container::

    uv run python ../docs/review_2026-10-15/repro/live_client.py OUT_DIR
    RESUME_SID=<study id> GATES_DONE=appraisal_review uv run python ... OUT_DIR   # resume

Then ``live_export.py OUT_DIR/db_final OUT_DIR/documents.json PREDICTIONS_DIR``.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from basis.api.app import LOCAL_BUCKET, LOCAL_STORAGE_DIR
from basis.storage import LocalStorage
from basis.storage.base import object_uri

API = os.environ.get("API", "http://127.0.0.1:8110")
PROJECT = os.environ["FIREBASE_PROJECT_ID"]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path.home() / "basis-eval-data" / "inputs"
POLL = 5.0
PG_DB = os.environ.get("PG_DB", "basis")
ALIASES = os.environ.get("ROOM_ALIASES")

T0 = time.monotonic()
timeline = (OUT / "timeline.jsonl").open("a")


def log(event: str, **data) -> None:
    row = {
        "t": round(time.monotonic() - T0, 1),
        "at": datetime.now(UTC).isoformat(),
        "event": event,
        **data,
    }
    timeline.write(json.dumps(row, default=str) + "\n")
    timeline.flush()
    print(json.dumps(row, default=str)[:400], flush=True)


def dump(name: str, value) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, default=str))


def segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def emulator_token(uid: str) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": uid,
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "email": f"{uid}@example.test",
        "name": "Live Run Engineer",
    }
    return f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}."


def client() -> httpx.Client:
    return httpx.Client(
        base_url=API,
        timeout=60,
        headers={"Authorization": f"Bearer {emulator_token('live-engineer')}"},
    )


http = client()
token_minted = time.monotonic()
calls = {"post": 0, "get": 0, "429": 0}


def refresh() -> None:
    global http, token_minted
    if time.monotonic() - token_minted > 3000:  # tokens live an hour
        http = client()
        token_minted = time.monotonic()


def post(path: str, body: dict | None = None, ok=(200, 201, 202)) -> httpx.Response:
    key = str(uuid.uuid4())
    while True:
        refresh()
        calls["post"] += 1
        r = http.post(path, json=body, headers={"Idempotency-Key": key})
        if r.status_code != 429:
            break
        calls["429"] += 1
        time.sleep(int(r.headers.get("Retry-After", "5")))
    if r.status_code not in ok:
        log("http_error", method="POST", path=path, status=r.status_code, body=r.text[:2000])
        r.raise_for_status()
    return r


def get(path: str, **params) -> httpx.Response:
    while True:
        refresh()
        calls["get"] += 1
        r = http.get(path, params=params or None)
        if r.status_code != 429:
            break
        calls["429"] += 1
        time.sleep(int(r.headers.get("Retry-After", "5")))
    return r


def delete(path: str) -> httpx.Response:
    refresh()
    return http.delete(path, headers={"Idempotency-Key": str(uuid.uuid4())})


def psql_json(sql: str):
    out = subprocess.run(
        [
            "docker",
            "exec",
            "basis-live-pg",
            "psql",
            "-U",
            "basis",
            "-d",
            PG_DB,
            "-At",
            "-c",
            f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def snapshot(gate: str, sid: str) -> None:
    d = OUT / f"db_{gate}"
    d.mkdir(exist_ok=True)
    by_study = f"where study_id = '{sid}'"
    q = {
        "extractions": "select id, document_id, revision, method, overall_confidence, "
        f"needs_review, audit from extractions {by_study}",
        "extraction_fields": "select section, field, value_text, value_num, value_date, "
        f"confidence, source, page from extraction_fields {by_study} order by section, field",
        "rooms": "select id, revision, name, room_type, floor, source, group_key "
        f"from rooms {by_study}",
        "photo_rooms": f"select document_id, room_id, revision from photo_rooms {by_study}",
        "components": "select id, revision, document_id, room_id, label, material, attachment, "
        f"confidence, raw from components {by_study}",
        "assets": "select id, revision, component_id, category, bucket, recovery_period, "
        "irs_section, method, confidence, needs_review, review_reason, verified "
        f"from assets {by_study}",
        "citations": "select asset_id, chunk_id, excerpt, verified_at, verification_method "
        f"from citations {by_study}",
        "cost_estimates": "select asset_id, unit_cost, total, source_row "
        f"from cost_estimates {by_study}",
        "localizations": "select document_id, revision, status, reason, instances, "
        f"model_version, predict_seconds, cost_usd from photo_localizations {by_study}",
        "objects": "select document_id, revision, label, score, counted, source, deleted_at "
        f"from photo_objects {by_study}",
        "llm_calls": "select activity, provider, model, prompt_version, input_tokens, "
        f"output_tokens, cost_usd, latency_ms, created_at from llm_calls {by_study} "
        "order by created_at",
        "activity_results": "select a.activity, a.idempotency_key, a.status, a.error, "
        "a.created_at, a.updated_at from activity_results a join runs r on r.id = a.run_id "
        f"where r.study_id = '{sid}' order by a.created_at",
        "outbox": "select id, kind, status, attempts, last_error, created_at from outbox "
        "order by created_at",
        "study": "select id, status, current_stage, revision, workflow_id, room_aliases, "
        f"failure_reason from studies where id = '{sid}'",
    }
    for name, sql in q.items():
        try:
            (d / f"{name}.json").write_text(json.dumps(psql_json(sql), indent=1, default=str))
        except subprocess.CalledProcessError as exc:
            log("snapshot_error", gate=gate, table=name, err=exc.stderr[:300])


RESUME = os.environ.get("RESUME_SID")
storage = LocalStorage(LOCAL_STORAGE_DIR)
if RESUME:
    sid = RESUME
    docs = json.loads((OUT / "documents.json").read_text())
    log("resumed", study_id=sid)
else:
    # ---------------------------------------------------------------- create + upload
    body: dict = {"property_name": "Live E2E study"}
    if ALIASES:
        body["room_aliases"] = json.loads(Path(ALIASES).read_text())
    sid = post("/v1/studies", body).json()["id"]
    log("study_created", study_id=sid, room_aliases=len(body.get("room_aliases", {})))
    storage = LocalStorage(LOCAL_STORAGE_DIR)
    uploads = [("appraisal", DATA / "appraisal.pdf", "application/pdf")]
    uploads += [
        ("photo", p, "image/jpeg")
        for p in sorted((DATA / "photos").iterdir())
        if p.suffix.lower() in (".jpg", ".jpeg")
    ]
    docs = []
    t_up = time.monotonic()
    for kind, path, ctype in uploads:
        data = path.read_bytes()
        ticket = post(
            f"/v1/studies/{sid}/documents",
            {
                "kind": kind,
                "filename": path.name,
                "content_type": ctype,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        ).json()
        doc = ticket["document"]
        # LocalStorage's signed URL lives in the API process (local-upload://): write the
        # object where the API's LocalStorage reads it, as the runbook does; GCS would be a PUT.
        uri = object_uri(LOCAL_BUCKET, uuid.UUID(sid), uuid.UUID(doc["id"]), doc["filename"])
        storage.write(uri, data)
        done = post(f"/v1/studies/{sid}/documents/{doc['id']}/complete").json()
        docs.append(
            {
                "id": doc["id"],
                "kind": kind,
                "file": path.name,
                "bytes": len(data),
                "upload_scheme": ticket["upload"]["url"].split(":")[0],
                "status": done.get("status"),
            }
        )
    dump("documents.json", docs)
    log(
        "uploads_done",
        documents=len(docs),
        seconds=round(time.monotonic() - t_up, 1),
        calls=dict(calls),
    )

# ---------------------------------------------------------------- start + poll
if not RESUME:
    started = post(f"/v1/studies/{sid}/start").json()
    log("started", **started)
photos = [d for d in docs if d["kind"] == "photo"]
last = None
gate_seen = {g: 0.0 for g in os.environ.get("GATES_DONE", "").split(",") if g}
GATES = ("appraisal_review", "rooms_review", "assets_review")
while True:
    r = get(f"/v1/studies/{sid}/workflow")
    if r.status_code != 200:
        log("workflow_get", status=r.status_code, body=r.text[:500])
        time.sleep(POLL)
        continue
    view = r.json()
    st = view.get("status") or {}
    key = (
        json.dumps({k: v for k, v in st.items() if k != "budget_remaining_usd"}, sort_keys=True)
        + view["execution_status"]
    )
    if key != last:
        log("status", execution=view["execution_status"], status=st)
        last = key
    if view["execution_status"] != "RUNNING":
        dump("workflow_final.json", view)
        break
    gate = st.get("awaiting")
    # The gate is open once the workflow's transition is done (status.stage), not when
    # it starts waiting (awaiting is set first): a decision before that is 409.
    if gate in GATES and gate not in gate_seen and st.get("stage") == gate:
        waited_from = st.get("waiting_since")
        log(
            "gate_reached",
            gate=gate,
            waiting_since=waited_from,
            pending_photos=st.get("pending_photos"),
            completed_photos=st.get("completed_photos"),
        )
        gate_seen[gate] = time.monotonic()
        dump(f"status_{gate}.json", view)
        dump(f"study_{gate}.json", get(f"/v1/studies/{sid}").json())
        snapshot(gate, sid)
        for route in ("fields", "rooms", "assets"):
            pages, cursor = [], None
            while True:
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                page = get(f"/v1/studies/{sid}/{route}", **params)
                if page.status_code != 200:
                    log("read_route", route=route, status=page.status_code, body=page.text[:300])
                    break
                pages.append(page.json())
                cursor = page.json().get("next_cursor")
                if not cursor:
                    break
            items = sum(len(p["items"]) for p in pages)
            dump(f"api_{route}_{gate}.json", pages)
            log("read_route", gate=gate, route=route, pages=len(pages), items=items)
        if gate == "rooms_review":
            per_photo = {}
            for d in photos:
                per_photo[d["id"]] = get(f"/v1/studies/{sid}/photos/{d['id']}/objects").json()
            dump("objects_rooms_review.json", per_photo)
            # Exercise the objects API on one photo: tap an uncounted candidate if any.
            target, body = None, None
            for d in photos:
                items = per_photo[d["id"]].get("items", [])
                cand = [o for o in items if not o.get("counted")]
                if cand:
                    o = cand[0]
                    target = d
                    body = {
                        "kind": "point",
                        "label": o["label"],
                        "point": {
                            "x": (o["box"]["x1"] + o["box"]["x2"]) / 2,
                            "y": (o["box"]["y1"] + o["box"]["y2"]) / 2,
                        },
                    }
                    break
            if target is None:
                target = photos[0]
                body = {
                    "kind": "box",
                    "label": "electrical_outlet",
                    "box": {"x1": 0.01, "y1": 0.01, "x2": 0.04, "y2": 0.04},
                }
            path = f"/v1/studies/{sid}/photos/{target['id']}/objects"
            before = get(path).json()
            tap = post(path, body, ok=(200, 201, 422))
            log(
                "objects_post",
                photo=target["id"],
                body=body,
                status=tap.status_code,
                response=tap.json(),
            )
            after = get(path).json()
            deleted = None
            if tap.status_code == 201:
                oid = tap.json()["object"]["id"]
                dr = delete(f"{path}/{oid}")
                deleted = {"status": dr.status_code, "body": dr.json() if dr.content else None}
                log("objects_delete", status=dr.status_code)
            final = get(path).json()
            dump(
                "objects_exercise.json",
                {
                    "photo": target["id"],
                    "before": before,
                    "post_body": body,
                    "post_status": tap.status_code,
                    "post": tap.json(),
                    "after_post": after,
                    "delete": deleted,
                    "after_delete": final,
                },
            )
        if gate == "assets_review":
            pages, cursor = [], None
            while True:
                params = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                page = get(f"/v1/studies/{sid}/evidence", **params).json()
                pages.append(page)
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            dump("evidence_assets_review.json", pages)
        t_dec = time.monotonic()
        while True:
            answer = post(
                f"/v1/studies/{sid}/decisions",
                {"stage": gate, "revision": st["revision"], "decision": "approve"},
                ok=(202, 409),
            )
            if answer.status_code == 202:
                break
            log("decision_refused", gate=gate, body=answer.text[:300])
            time.sleep(POLL)
        acc = answer.json()
        log(
            "decision_posted",
            gate=gate,
            current_stage=acc["current_stage"],
            review_seconds=round(t_dec - gate_seen[gate], 1),
        )
    time.sleep(POLL)

log("finished", execution=view["execution_status"], calls=dict(calls))
snapshot("final", sid)
export = storage.path_for(f"gs://{LOCAL_BUCKET}/studies/{sid}/exports/rev-1.json")
if export.exists():
    (OUT / "export_rev-1.json").write_bytes(export.read_bytes())
    log(
        "export",
        sha256=hashlib.sha256(export.read_bytes()).hexdigest(),
        sections=sorted(json.loads(export.read_text())),
    )
