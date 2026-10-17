"""Basis load test (docs/perf/LOAD_TESTING.md): whole studies and read-heavy reviewers.

Run from ``backEnd/`` with the load group installed (``uv sync --group dev --group load``)::

    uv run --no-sync locust -f ../tooling/load/locustfile.py --headless StudyEngineer \
        --host http://127.0.0.1:8161 -u 5 -r 5 --target-studies 5 --time-scale 0.1 \
        --run-dir ../tooling/load/runs/s5 --csv ../tooling/load/runs/s5/locust

``tooling/load/run_step.sh`` wraps this with the sampler and the report.

Users
-----
* :class:`StudyEngineer` plays the frontend for one study at a time, exactly the calls the
  live client made (``docs/review_2026-10-15/repro/live_client.py``): create the study,
  register each document and upload its bytes to the signed URL, confirm it, start, poll
  ``GET .../workflow`` every 5 s, and at each gate read what the engineer reviews (fields;
  rooms and every photo's objects; assets and evidence) and approve. Each user is its own
  engineer (uid ``load-eng-<n>``), so the per-caller rate limits apply as in production.
  The run stops when ``--target-studies`` studies have finished (completed or failed).
* :class:`ReviewReader` is an engineer with studies open: list them, poll one's workflow,
  page its fields, rooms, assets and evidence, open photos' objects. It reads the studies
  of ``load-eng-<i mod --reader-pool>`` (run it after a study run created them).

Every request is named by its route template (``GET /v1/studies/{id}/workflow``) so the
statistics are per route. Study milestones are reported as requests of type ``STUDY``
(``to appraisal_review``, ``completed``, ...) with the time since ``start`` (or for
``uploads``, since the study was created), so Locust's percentiles cover them too; each
study's timeline is also written to ``<run-dir>/studies.jsonl``.

A 429 is recorded as a failure of its route (the user saw it), then the call is retried
after ``Retry-After`` with the same ``Idempotency-Key``, as the frontend must.

Time scale: with the worker's ``LOAD_LATENCY_SCALE=s`` set ``--time-scale s`` too, so the
poll interval and the gate think time shrink with the work (the study's timeline then
scales by s; divide the STUDY milestones by s for real time, LOAD_TESTING.md section 4).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import APPRAISAL, FIXTURES, emulator_token, photo_plan, scaled, unique_bytes  # noqa: E402

GATES = ("appraisal_review", "rooms_review", "assets_review")
POLL_S = 5.0  # the frontend's (and the live client's) workflow poll interval

_lock = threading.Lock()
_ids = itertools.count()
_state: Dict[str, int] = {"started": 0, "finished": 0, "completed": 0, "failed": 0}


@events.init_command_line_parser.add_listener
def _options(parser: Any) -> None:
    parser.add_argument("--target-studies", type=int, default=1, help="stop after this many")
    parser.add_argument("--photos", type=int, default=52, help="photos per study")
    parser.add_argument("--time-scale", type=float, default=1.0, help="= LOAD_LATENCY_SCALE")
    parser.add_argument("--gate-think-s", type=float, default=0.0, help="review time per gate")
    parser.add_argument(
        "--study-timeout-s", type=float, default=6 * 3600.0, help="real seconds; x --time-scale"
    )
    parser.add_argument("--run-dir", default="", help="studies.jsonl goes here")
    parser.add_argument(
        "--firebase-project",
        default=os.environ.get("FIREBASE_PROJECT_ID", ""),
        help="emulator tokens for this project (local stack)",
    )
    parser.add_argument(
        "--token-file", default="", help="JSON {uid: ID token} instead of emulator tokens"
    )
    parser.add_argument(
        "--local-storage-dir",
        default="",
        help="LocalStorage root of the API (local-upload:// URLs); default as the API's",
    )
    parser.add_argument("--reader-pool", type=int, default=25, help="engineers readers act as")
    parser.add_argument("--uid-prefix", default="load-eng", help="engineer uid prefix")


class Engineer:
    """The shared client logic: auth, named requests, 429 handling."""

    client: Any
    environment: Any

    def _opts(self) -> Any:
        return self.environment.parsed_options

    def _token(self, uid: str) -> str:
        opts = self._opts()
        if opts.token_file:
            tokens = json.loads(Path(opts.token_file).read_text())
            return str(tokens[uid])
        if not opts.firebase_project:
            raise RuntimeError("set --firebase-project (emulator) or --token-file")
        return emulator_token(uid, opts.firebase_project)

    def login(self, uid: str) -> None:
        self.uid = uid
        self.minted = time.monotonic()
        self.client.headers["Authorization"] = f"Bearer {self._token(uid)}"
        self.rate_limited = 0

    def _refresh(self) -> None:
        if not self._opts().token_file and time.monotonic() - self.minted > 3000:
            self.login(self.uid)

    def call(
        self,
        method: str,
        path: str,
        name: str,
        *,
        ok: tuple = (200,),
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """One logical call: retried on 429 (recorded as a failure each time) after
        ``Retry-After``; any other status outside ``ok`` is a failure and returned."""
        headers = {"Idempotency-Key": str(uuid.uuid4())} if method != "GET" else {}
        while True:
            self._refresh()
            with self.client.request(
                method,
                path,
                name=name,
                json=json_body,
                params=params,
                headers=headers,
                catch_response=True,
            ) as response:
                if response.status_code == 429:
                    self.rate_limited += 1
                    response.failure("429 rate limited")
                    wait = float(response.headers.get("Retry-After", "5"))
                elif response.status_code in ok:
                    response.success()
                    return response
                else:
                    detail = response.text[:200] if response.status_code else repr(response.error)
                    response.failure(f"{response.status_code} {detail[:200]}")
                    return response
            time.sleep(wait)

    def pages(self, path: str, name: str, limit: int = 200) -> int:
        """Read every page of a cursor-paginated list; the number of items."""
        items, cursor = 0, None
        while True:
            params: Dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            r = self.call("GET", path, name, params=params)
            if r.status_code != 200:
                return items
            body = r.json()
            items += len(body.get("items", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return items


def milestone(environment: Any, name: str, seconds: float, *, ok: bool = True, err: str = "") -> None:
    events.request.fire(
        request_type="STUDY",
        name=name,
        response_time=seconds * 1000.0,
        response_length=0,
        exception=None if ok else RuntimeError(err or name),
        context={},
    )


class StudyEngineer(Engineer, HttpUser):
    """One engineer running studies end to end, one at a time."""

    wait_time = constant(0)

    def on_start(self) -> None:
        self.login(f"{self._opts().uid_prefix}-{next(_ids)}")
        run_dir = self._opts().run_dir
        self.log_path = Path(run_dir) / "studies.jsonl" if run_dir else None
        storage = self._opts().local_storage_dir
        if not storage:
            import tempfile

            storage = str(Path(tempfile.gettempdir()) / "basis-local-storage")
        self.storage_root = Path(storage)
        self.photos = photo_plan(self._opts().photos)
        self.fixture_bytes = {
            p.fixture: (FIXTURES / "photos" / p.fixture).read_bytes() for p in self.photos
        }
        self.appraisal = APPRAISAL.read_bytes()

    @task
    def run_study(self) -> None:
        opts = self._opts()
        with _lock:
            if _state["started"] >= opts.target_studies:
                raise StopUser()
            _state["started"] += 1
        record: Dict[str, Any] = {"uid": self.uid, "photos": len(self.photos)}
        try:
            self._study(record)
        except StopUser:
            raise
        except Exception as exc:  # recorded, the run goes on
            record["error"] = f"{type(exc).__name__}: {exc}"
            milestone(self.environment, "completed", 0, ok=False, err=record["error"])
        finally:
            record["rate_limited"] = self.rate_limited
            self._finish(record)

    def _finish(self, record: Dict[str, Any]) -> None:
        done = record.get("execution") == "COMPLETED"
        with _lock:
            _state["finished"] += 1
            _state["completed" if done else "failed"] += 1
            last = _state["finished"] >= self._opts().target_studies
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with _lock, self.log_path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        if last:
            self.environment.runner.quit()
        if _state["started"] >= self._opts().target_studies:
            raise StopUser()

    # ------------------------------------------------------------------ the study
    def _upload(self, sid: str, kind: str, filename: str, ctype: str, data: bytes) -> None:
        ticket = self.call(
            "POST",
            f"/v1/studies/{sid}/documents",
            "POST /v1/studies/{id}/documents",
            ok=(201,),
            json_body={
                "kind": kind,
                "filename": filename,
                "content_type": ctype,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        if ticket.status_code != 201:
            raise RuntimeError(f"register {filename}: {ticket.status_code}")
        body = ticket.json()
        doc, upload = body["document"], body["upload"]
        if upload["url"].startswith("local-upload://"):
            # The browser's PUT on GCS; LocalStorage's signed URL lives in the API process,
            # so write where its LocalStorage reads (the S2 runbook and the live client do).
            from basis.storage.base import object_name

            path = self.storage_root / "basis-local" / object_name(
                uuid.UUID(sid), uuid.UUID(doc["id"]), doc["filename"]
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        else:
            with self.client.request(
                upload["method"],
                upload["url"],
                name="PUT signed upload URL",
                data=data,
                headers=upload["headers"],
                catch_response=True,
            ) as r:
                if r.status_code >= 300:
                    r.failure(f"{r.status_code}")
                    raise RuntimeError(f"upload {filename}: {r.status_code}")
                r.success()
        done = self.call(
            "POST",
            f"/v1/studies/{sid}/documents/{doc['id']}/complete",
            "POST /v1/studies/{id}/documents/{id}/complete",
        )
        if done.status_code != 200:
            raise RuntimeError(f"complete {filename}: {done.status_code}")

    def _study(self, record: Dict[str, Any]) -> None:
        opts = self._opts()
        scale = opts.time_scale
        t_create = time.monotonic()
        r = self.call(
            "POST", "/v1/studies", "POST /v1/studies", ok=(201,),
            json_body={"property_name": f"Load test {self.uid}"},
        )
        if r.status_code != 201:
            raise RuntimeError(f"create: {r.status_code}")
        sid = r.json()["id"]
        record["study_id"] = sid
        tag = f"{sid}"
        self._upload(sid, "appraisal", "appraisal.pdf", "application/pdf",
                     unique_bytes(self.appraisal, tag))
        for i, photo in enumerate(self.photos):
            data = unique_bytes(self.fixture_bytes[photo.fixture], f"{tag}:{i}")
            self._upload(sid, "photo", photo.filename, "image/jpeg", data)
        record["uploads_s"] = round(time.monotonic() - t_create, 2)
        milestone(self.environment, "uploads", record["uploads_s"])

        r = self.call("POST", f"/v1/studies/{sid}/start", "POST /v1/studies/{id}/start", ok=(202,))
        if r.status_code != 202:
            raise RuntimeError(f"start: {r.status_code}")
        t0 = time.monotonic()
        record["started_at"] = time.time()
        seen: Dict[str, float] = {}
        polls = 0
        deadline = t0 + opts.study_timeout_s * scale
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError("study timed out")
            r = self.call("GET", f"/v1/studies/{sid}/workflow", "GET /v1/studies/{id}/workflow")
            polls += 1
            if r.status_code != 200:
                time.sleep(scaled(POLL_S, scale))
                continue
            view = r.json()
            status = view.get("status") or {}
            if view["execution_status"] != "RUNNING":
                record["execution"] = view["execution_status"]
                break
            gate = status.get("awaiting")
            if gate in GATES and gate not in seen and status.get("stage") == gate:
                seen[gate] = time.monotonic() - t0
                milestone(self.environment, f"to {gate}", seen[gate])
                self._review(sid, gate)
                if opts.gate_think_s:
                    time.sleep(scaled(opts.gate_think_s, scale))
                self._approve(sid, gate, status["revision"], scale)
            time.sleep(scaled(POLL_S, scale))
        total = time.monotonic() - t0
        record.update(
            gates={g: round(s, 2) for g, s in seen.items()},
            run_s=round(total, 2),
            polls=polls,
            finished_at=time.time(),
        )
        ok = record["execution"] == "COMPLETED"
        milestone(self.environment, "completed", total, ok=ok, err=record["execution"])
        self.call("GET", f"/v1/studies/{sid}", "GET /v1/studies/{id}")

    def _review(self, sid: str, gate: str) -> None:
        """What the engineer's review screen loads at each gate."""
        self.call("GET", f"/v1/studies/{sid}", "GET /v1/studies/{id}")
        if gate == "appraisal_review":
            self.pages(f"/v1/studies/{sid}/fields", "GET /v1/studies/{id}/fields")
        elif gate == "rooms_review":
            self.pages(f"/v1/studies/{sid}/rooms", "GET /v1/studies/{id}/rooms")
            docs = self.call("GET", f"/v1/studies/{sid}", "GET /v1/studies/{id}").json()
            for d in docs.get("documents", []):
                if d.get("kind") == "photo":
                    self.call(
                        "GET",
                        f"/v1/studies/{sid}/photos/{d['id']}/objects",
                        "GET /v1/studies/{id}/photos/{id}/objects",
                        ok=(200, 409),
                    )
        else:
            self.pages(f"/v1/studies/{sid}/assets", "GET /v1/studies/{id}/assets")
            self.pages(f"/v1/studies/{sid}/evidence", "GET /v1/studies/{id}/evidence", 100)

    def _approve(self, sid: str, gate: str, revision: int, scale: float) -> None:
        while True:
            r = self.call(
                "POST",
                f"/v1/studies/{sid}/decisions",
                "POST /v1/studies/{id}/decisions",
                ok=(202, 409),
                json_body={"stage": gate, "revision": revision, "decision": "approve"},
            )
            if r.status_code == 202:
                return
            time.sleep(scaled(POLL_S, scale))  # 409 gate-not-open: the transition is in flight


class ReviewReader(Engineer, HttpUser):
    """An engineer with studies open, reading and polling (the read-heavy scenario)."""

    wait_time = between(2, 5)

    def on_start(self) -> None:
        opts = self._opts()
        self.login(f"{opts.uid_prefix}-{next(_ids) % max(opts.reader_pool, 1)}")
        r = self.call("GET", "/v1/studies", "GET /v1/studies", params={"limit": 50})
        items = r.json().get("items", []) if r.status_code == 200 else []
        self.studies: List[str] = [s["id"] for s in items]
        self.photos: Dict[str, List[str]] = {}
        self.turn = 0

    def _study(self) -> Optional[str]:
        if not self.studies:
            return None
        self.turn += 1
        return self.studies[self.turn % len(self.studies)]

    @task(10)
    def poll_workflow(self) -> None:
        sid = self._study()
        if sid:
            self.call("GET", f"/v1/studies/{sid}/workflow", "GET /v1/studies/{id}/workflow")

    @task(3)
    def open_study(self) -> None:
        sid = self._study()
        if sid:
            r = self.call("GET", f"/v1/studies/{sid}", "GET /v1/studies/{id}")
            if r.status_code == 200:
                self.photos[sid] = [
                    d["id"] for d in r.json().get("documents", []) if d.get("kind") == "photo"
                ]

    @task(3)
    def assets(self) -> None:
        sid = self._study()
        if sid:
            self.pages(f"/v1/studies/{sid}/assets", "GET /v1/studies/{id}/assets", 50)

    @task(2)
    def rooms(self) -> None:
        sid = self._study()
        if sid:
            self.pages(f"/v1/studies/{sid}/rooms", "GET /v1/studies/{id}/rooms", 50)

    @task(2)
    def fields(self) -> None:
        sid = self._study()
        if sid:
            self.pages(f"/v1/studies/{sid}/fields", "GET /v1/studies/{id}/fields", 100)

    @task(2)
    def evidence(self) -> None:
        sid = self._study()
        if sid:
            self.call(
                "GET",
                f"/v1/studies/{sid}/evidence",
                "GET /v1/studies/{id}/evidence",
                params={"limit": 50},
            )

    @task(3)
    def photo_objects(self) -> None:
        sid = self._study()
        photos = self.photos.get(sid or "", [])
        if sid and photos:
            pid = photos[self.turn % len(photos)]
            self.call(
                "GET",
                f"/v1/studies/{sid}/photos/{pid}/objects",
                "GET /v1/studies/{id}/photos/{id}/objects",
            )

    @task(1)
    def list_studies(self) -> None:
        self.call("GET", "/v1/studies", "GET /v1/studies", params={"limit": 50})
