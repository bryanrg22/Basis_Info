"""SAM 3 throughput on a study's photos (EVAL_REPORT section 30, SAM3_HOSTING.md).

Runs the product's localization step (:func:`basis.vision.localize.object_labels` of each
photo's component labels, the default phrase set, :data:`CANDIDATE_THRESHOLD`, one
full-frame prediction per photo) on every photo of a directory at a given concurrency, and
records for each photo where the time went:

* ``queue_s``: the prediction's ``started_at - created_at`` (Replicate's own timestamps:
  waiting for a worker, including a cold boot);
* ``predict_s``: ``metrics.predict_time`` (what is billed);
* ``create_s``: the create call as the client saw it (with ``Prefer: wait`` it returns
  when the prediction is done, or after 60 s), retries and their sleeps included;
* ``download_s`` / ``download_bytes``: fetching the per-prompt result files;
* ``service_s``: ``create_s + download_s`` (the time the call holds a concurrency slot);
* ``total_s``: from the start of the run to the photo's result (slot wait and parsing
  included);
* the HTTP 429s and other retried statuses seen.

``--target url`` sends the same input to a self-hosted Cog container (``POST
<url>/predictions``; output files come back inline as data URIs), timed the same way
(``queue_s`` and ``predict_s`` are then unknown: Cog reports neither).

Run from ``backEnd/`` with ``REPLICATE_API_TOKEN`` in the environment (never printed)::

    uv run --no-sync python ../docs/review_2026-10-15/repro/sam3_throughput.py \\
        --photos ~/basis-eval-data/inputs/photos \\
        --components ~/basis-eval-data/predictions/live_e2e_v070_defaults/photo_components.json \\
        --concurrency 6 --out OUT_DIR

``--arrivals FILE`` replays a recorded study: each photo is localized when its analysis
finished in that run (``{file: seconds from the start}``), as the workflow schedules it;
the summary then gives each photo's lag and how long SAM 3 trailed the last analysis.

Writes ``OUT_DIR/calls_c<n>[_<tag>].jsonl`` (one line per photo) and prints a summary.
Nothing about a photo but its file name and timings is written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from basis.vision.localize import (
    CANDIDATE_THRESHOLD,
    DEFAULT_PHRASE_SET,
    L40S_USD_PER_SECOND,
    PREFER_WAIT_S,
    Localizer,
    LocalizerError,
    ReplicateSam3Localizer,
    object_labels,
    phrases_for,
)
from basis.vision.prep import prepare_image

VERSION = "1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650"  # pragma: allowlist secret  # noqa: E501


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@dataclass
class Probe:
    """Per-photo timings filled in by the instrumented localizer and the HTTP hooks."""

    statuses: Counter[int] = field(default_factory=Counter)
    predictions: dict[str, dict[str, Any]] = field(default_factory=dict)
    downloads: dict[str, tuple[float, int]] = field(default_factory=dict)
    throttled: list[dict[str, Any]] = field(default_factory=list)


class TimedReplicate(ReplicateSam3Localizer):
    """The production localizer with its phases timed (behaviour unchanged)."""

    probe: Probe

    async def _predict(
        self, input_: dict[str, Any], prompts: tuple[str, ...]
    ) -> tuple[dict[str, Any], Any]:
        started = time.monotonic()
        prediction, call = await super()._predict(input_, prompts)
        self.probe.predictions[str(prediction.get("id"))] = {
            "created_at": prediction.get("created_at"),
            "started_at": prediction.get("started_at"),
            "completed_at": prediction.get("completed_at"),
            "metrics": prediction.get("metrics"),
            "client_s": time.monotonic() - started,
        }
        return prediction, call

    async def _results(self, prediction: Mapping[str, Any], expected: int) -> list[dict[str, Any]]:
        started = time.monotonic()
        out = await super()._results(prediction, expected)
        size = sum(len(json.dumps(o)) for o in out)
        self.probe.downloads[str(prediction.get("id"))] = (time.monotonic() - started, size)
        return out


async def run(args: argparse.Namespace) -> dict[str, Any]:
    components: dict[str, list[str]] = json.loads(Path(args.components).read_text())
    files = sorted(p for p in Path(args.photos).iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    if args.limit:
        files = files[: args.limit]
    probe = Probe()

    async def on_response(response: httpx.Response) -> None:
        if response.request.method == "POST" or response.status_code >= 400:
            probe.statuses[response.status_code] += 1
        if response.status_code == 429 and len(probe.throttled) < 3:
            await response.aread()
            probe.throttled.append(
                {"body": response.text[:300], "retry_after": response.headers.get("retry-after")}
            )

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(PREFER_WAIT_S + 30.0, connect=10.0),
        event_hooks={"response": [on_response]},
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    )
    localizer: Localizer
    if args.target == "replicate":
        token = os.environ.get("REPLICATE_API_TOKEN", "")
        localizer = TimedReplicate(
            client=client, token=token, version=VERSION, concurrency=args.concurrency
        )
        localizer.probe = probe  # type: ignore[attr-defined]
    else:
        from basis.vision.localize import CogSam3Localizer

        bearer = os.environ.get("LOCALIZER_TOKEN")

        async def token() -> str | None:
            return bearer

        localizer = CogSam3Localizer(
            client=client, url=args.url, version=VERSION, token_source=token,
            concurrency=args.concurrency,
        )
    if args.full_masks:
        localizer.compact = False  # type: ignore[union-attr]

    arrivals: dict[str, float] = (
        json.loads(Path(args.arrivals).read_text()) if args.arrivals else {}
    )

    async def one(path: Path) -> dict[str, Any]:
        if arrivals:
            await asyncio.sleep(max(0.0, arrivals[path.name] - (time.monotonic() - t_start)))
        image = prepare_image(path.read_bytes())
        phrases = phrases_for(object_labels(components.get(path.name, [])), DEFAULT_PHRASE_SET)
        t0 = time.monotonic()
        error = None
        loc = None
        try:
            loc = await localizer.localize(image, phrases, threshold=CANDIDATE_THRESHOLD)
        except LocalizerError as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
        row: dict[str, Any] = {
            "file": path.name,
            "phrases": len(phrases),
            "width": image.width,
            "height": image.height,
            "start_s": t0 - t_start,
            "total_s": time.monotonic() - t0,
            "done_s": time.monotonic() - t_start,
            "error": error,
        }
        if loc is not None:
            row["instances"] = len(loc.instances)
            row["predict_s"] = loc.predict_seconds or None
            row["cost_usd"] = loc.cost_usd
            pid = loc.calls[0].prediction_id if loc.calls else None
            pred = probe.predictions.get(str(pid), {})
            created, started_at, completed = (
                _ts(pred.get("created_at")),
                _ts(pred.get("started_at")),
                _ts(pred.get("completed_at")),
            )
            if created and started_at:
                row["queue_s"] = started_at - created
            if started_at and completed:
                row["run_s"] = completed - started_at
            row["create_s"] = pred.get("client_s")
            if str(pid) in probe.downloads:
                row["download_s"], row["download_bytes"] = probe.downloads[str(pid)]
            if row.get("create_s") is not None and row.get("download_s") is not None:
                row["service_s"] = row["create_s"] + row["download_s"]
            row["prediction_id"] = pid
            if args.target == "url" and loc.calls:
                row["service_s"] = row["total_s"]  # nothing else is known for a Cog server
        return row

    t_start = time.monotonic()
    rows = await asyncio.gather(*(one(p) for p in files))
    wall = time.monotonic() - t_start
    await client.aclose()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"_{args.tag}" if args.tag else ""
    name = f"calls_c{args.concurrency}{tag}.jsonl"
    with (out / name).open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    ok = [r for r in rows if not r["error"]]

    def dist(key: str) -> dict[str, float] | None:
        values = sorted(r[key] for r in ok if r.get(key) is not None)
        if not values:
            return None
        q = statistics.quantiles(values, n=20) if len(values) >= 2 else values * 19
        return {
            "mean": round(statistics.fmean(values), 2),
            "p50": round(statistics.median(values), 2),
            "p95": round(q[18], 2),
            "max": round(values[-1], 2),
        }

    summary = {
        "target": args.target,
        "concurrency": args.concurrency,
        "tag": args.tag,
        "photos": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "wall_s": round(wall, 1),
        "statuses": dict(sorted(probe.statuses.items())),
        "predict_s_total": round(sum(r.get("predict_s") or 0 for r in ok), 1),
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in ok), 4),
        "cost_at_l40s": round(
            sum(r.get("predict_s") or 0 for r in ok) * L40S_USD_PER_SECOND, 4
        ),
        **{k: dist(k) for k in (
            "total_s", "service_s", "queue_s", "predict_s", "run_s", "create_s", "download_s"
        )},
        "download_mb_mean": round(
            statistics.fmean(r.get("download_bytes", 0) for r in ok) / 1e6, 2
        )
        if ok
        else None,
        "errors": [r["error"] for r in rows if r["error"]][:5],
        # With --arrivals: how long after its photo's analysis each localization finished,
        # and how long after the last analysis the last one did.
        "lag_s": dist("total_s") if arrivals else None,
        "trail_s": round(max(r["done_s"] for r in rows) - max(arrivals.values()), 1)
        if arrivals
        else None,
        "throttled": probe.throttled,
    }
    (out / name.replace(".jsonl", ".summary.json")).write_text(json.dumps(summary, indent=1))
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", required=True)
    parser.add_argument("--components", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--target", choices=("replicate", "url"), default="replicate")
    parser.add_argument("--url", help="the Cog server's base URL (with --target url)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--arrivals",
        help="JSON {file: seconds}: start each photo then (a recorded study's analysis "
        "completion times), instead of all at once",
    )
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--full-masks", action="store_true", help="ask for full-image masks (the code before 30)"
    )
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(args)), indent=1))


if __name__ == "__main__":
    main()
