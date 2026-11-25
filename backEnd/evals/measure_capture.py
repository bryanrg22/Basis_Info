"""``python -m evals.measure_capture``: the property pictures of each truth house, taken by the
production capture code (EVAL_REPORT 103, step 5).

Per house: :class:`basis.capture.runner.CaptureRunner` with the real Playwright browser
(:func:`basis.capture.playwright_browser.playwright_factory`), the settings the worker runs
with (clean views, dates read; history off for this set; street level on, which needs a headed
browser, ``CAPTURE_HEADLESS=false``), the in-memory store
(:class:`basis.capture.store.MemoryCaptureStore`) and local files. No study is made and no
Google account is ever signed in to: the profile is a plain browser profile.

Gentleness, on top of the runner's own governor (6 page loads an hour, 30 a day, 8 to 20 s
between navigations):

* every page load is written to a ledger before it happens (``<out>/loads.jsonl``), and the
  store is seeded with the ledger's loads so the governor sees the real hour and day;
* at most ``--per-house`` loads per house (2: the search and the street view) and ``--budget``
  in all (22), counted from the ledger; a load past either is refused;
* before a house, the run waits until the hour has room for the house's loads, so a capture is
  never cut in half by the hour cap (a resumed capture would search again);
* the first block signal (``BASIS-CAPTURE-001`` to ``004``: the unusual-traffic page, a
  reCAPTCHA, a consent or sign-in wall, HTTP 429) stops everything, and the evidence the
  runner kept is reported.

Per house it writes ``<out>/<id>/result.json`` (outcome, views with their camera and
``pixel_size_m``, notes, incidents) and the PNGs under ``<out>/<id>/objects/``.

Usage (from ``backEnd/``)::

    CAPTURE_HEADLESS=false uv run python -m evals.measure_capture \\
        --out "$BASIS_EVAL_DATA_DIR/measure_truth/captures" \\
        --house mo1222="601 West Truman Road, Independence, MO 64050" \\
        --house ga0157=@32.02565,-84.43017
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from basis.capture.browser import CaptureBrowser
from basis.capture.governor import HOUR, GovernorConfig
from basis.capture.playwright_browser import playwright_factory
from basis.capture.runner import CaptureOptions, CaptureRunner, Done, Failed, Waiting
from basis.capture.store import MemoryCaptureStore, PinRow
from basis.config import Settings
from basis.errors.codes import E
from basis.storage.local import LocalStorage

BLOCK_CODES: Final = frozenset(
    {E.CAPTURE_UNUSUAL_TRAFFIC, E.CAPTURE_RECAPTCHA, E.CAPTURE_WALL, E.CAPTURE_RATE_LIMITED}
)


class Refused(RuntimeError):
    """A load past the budget; nothing was loaded."""


class Ledger:
    def __init__(self, path: Path, budget: int, per_house: int) -> None:
        self.path = path
        self.budget = budget
        self.per_house = per_house

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def times(self) -> list[datetime]:
        return [datetime.fromisoformat(r["at"]) for r in self.rows()]

    def record(self, house: str, url: str) -> None:
        rows = self.rows()
        if len(rows) >= self.budget:
            raise Refused(f"load budget {self.budget} spent; refusing {url}")
        if sum(1 for r in rows if r["house"] == house) >= self.per_house:
            raise Refused(f"{house}: {self.per_house} loads spent; refusing {url}")
        with self.path.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "n": len(rows) + 1,
                        "house": house,
                        "url": url,
                    }
                )
                + "\n"
            )


def _view(v: Any) -> dict[str, Any]:
    d = asdict(v)
    for k, val in list(d.items()):
        if isinstance(val, (datetime, uuid.UUID)) or hasattr(val, "isoformat"):
            d[k] = str(val)
    return d


async def capture_house(
    house: str,
    target: str,
    settings: Settings,
    ledger: Ledger,
    out: Path,
) -> dict[str, Any]:
    folder = out / house
    folder.mkdir(parents=True, exist_ok=True)
    inner = playwright_factory(settings)

    @asynccontextmanager
    async def counted() -> AsyncIterator[CaptureBrowser]:
        async with inner() as browser:
            load = browser.load

            async def ledgered(url: str) -> Any:
                ledger.record(house, url)
                return await load(url)

            browser.load = ledgered  # type: ignore[method-assign]
            yield browser

    store = MemoryCaptureStore()
    for at in ledger.times():
        store.navigations.append(("load", None, at))
    study = uuid.uuid4()
    if target.startswith("@"):
        lat, _, lon = target[1:].partition(",")
        pin = PinRow(
            study_id=study,
            address=None,
            lat=float(lat),
            lon=float(lon),
            source="engineer",
            version=1,
            confirmed=True,
        )
    else:
        pin = PinRow(
            study_id=study, address=target, lat=None, lon=None, source="engineer", version=1
        )
    capture = store.add_capture(study, pin)
    runner = CaptureRunner(
        store,
        LocalStorage(folder / "objects"),
        "measure-truth",
        counted,
        config=GovernorConfig.of(settings),
        holder="measure-truth",
        settle_s=settings.capture_settle_s,
        source_text=settings.capture_source_text,
        street_source_text=settings.capture_street_source_text,
        options=CaptureOptions.of(settings),
    )
    started = time.monotonic()
    error = None
    try:
        outcome: Any = await runner.run(capture.id)
    except Refused as exc:
        outcome, error = None, str(exc)
    result = {
        "house": house,
        "target": target,
        "outcome": repr(outcome),
        "error": error,
        "seconds": round(time.monotonic() - started, 1),
        "at": datetime.now(UTC).isoformat(),
        "pin": _view(store.pins[study]),
        "capture": _view(store.captures[capture.id]),
        "views": [_view(v) for v in store.views],
        "incidents": {k: _view(v) for k, v in store.incidents.items()},
        "events": [{**e, "study_id": str(e["study_id"])} for e in store.events],
        "alerts": list(store.alerts),
        "options": repr(CaptureOptions.of(settings)),
    }
    (folder / "result.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    return result


def hour_room(ledger: Ledger, cap: int, need: int, now: datetime) -> timedelta:
    """How long until the last hour holds at most ``cap - need`` loads."""
    recent = sorted(t for t in ledger.times() if t > now - HOUR)
    excess = len(recent) - (cap - need)
    if excess <= 0:
        return timedelta(0)
    return recent[excess - 1] + HOUR - now + timedelta(seconds=30)


async def run(args: argparse.Namespace) -> int:
    settings = Settings(
        _env_file=None,
        capture_profile_dir=str(args.profile),
        capture_history=False,
        capture_street_view=not args.no_street,
        capture_headless=args.headless,
        capture_tesseract=args.tesseract,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(args.out / "loads.jsonl", args.budget, args.per_house)
    need = 1 if args.no_street else 2
    for spec in args.house:
        house, _, target = spec.partition("=")
        if (args.out / house / "result.json").is_file() and not args.again:
            print(json.dumps({"house": house, "skipped": "already captured"}))
            continue
        wait = hour_room(ledger, settings.capture_loads_per_hour, need, datetime.now(UTC))
        if wait > timedelta(0):
            print(
                json.dumps({"house": house, "waiting_s": round(wait.total_seconds())}), flush=True
            )
            await asyncio.sleep(wait.total_seconds())
        result = await capture_house(house, target, settings, ledger, args.out)
        blocks = sorted(set(result["incidents"]) & BLOCK_CODES)
        print(
            json.dumps(
                {
                    "house": house,
                    "outcome": result["outcome"],
                    "error": result["error"],
                    "seconds": result["seconds"],
                    "views": [v["label"] for v in result["views"]],
                    "incidents": sorted(result["incidents"]),
                    "loads": len(ledger.rows()),
                }
            ),
            flush=True,
        )
        if blocks:
            print(json.dumps({"stopped": "block signal", "codes": blocks}), flush=True)
            return 3
        if result["error"]:
            return 4
        outcome = result["outcome"]
        if outcome.startswith(Waiting.__name__) and not outcome.startswith(Done.__name__):
            print(json.dumps({"stopped": "the capture is waiting", "outcome": outcome}), flush=True)
            return 5
        if outcome.startswith(Failed.__name__):
            print(json.dumps({"note": "capture failed; going on", "house": house}), flush=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.measure_capture", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--house", action="append", required=True, help="id=address or id=@lat,lon")
    parser.add_argument("--profile", type=Path, required=True, help="a plain browser profile")
    parser.add_argument("--budget", type=int, default=22)
    parser.add_argument("--per-house", type=int, default=2)
    parser.add_argument("--no-street", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--tesseract", default="/opt/homebrew/bin/tesseract")
    parser.add_argument("--again", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
