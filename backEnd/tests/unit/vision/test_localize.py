"""Object localization: masks as run lengths, boxes, phrases, the Replicate SAM 3 client
(offline, through ``httpx.MockTransport``), tiling with an exact round trip, and the
per-photo pipeline with the fake localizer."""

from __future__ import annotations

import base64
import io
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from basis.llm.provider import Budget, BudgetExceededError
from basis.vision import localize as loc
from basis.vision.localize import (
    COUNTABLE,
    PHRASES,
    Box,
    FakeLocalizer,
    Instance,
    Localization,
    LocalizerBadResponse,
    LocalizerError,
    LocalizerUnavailable,
    Phrase,
    Point,
    ReplicateSam3Localizer,
    Rle,
    box_iou,
    counts,
    localize_photo,
    merge_tiled,
    tiles,
)
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

L = ComponentLabel
TOKEN = "r8_test-token-never-logged"  # pragma: allowlist secret
VERSION = (
    "1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650"  # pragma: allowlist secret
)


def image_of(
    width: int, height: int, rects: Sequence[tuple[int, int, int, int]] = ()
) -> PreparedImage:
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for x1, y1, x2, y2 in rects:
        draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill="black")
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=95)
    # Built directly: prepare_image refuses images under 64 px, and these are synthetic.
    return PreparedImage(out.getvalue(), width, height, width, height, "JPEG")


def rect_mask(height: int, width: int, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y1:y2, x1:x2] = 1
    return mask


def instance(
    label: ComponentLabel,
    score: float,
    rect: tuple[int, int, int, int],
    size: tuple[int, int],
    phrase: str = "",
) -> Instance:
    width, height = size
    x1, y1, x2, y2 = rect
    rle = Rle.encode(rect_mask(height, width, x1, y1, x2, y2))
    return Instance(
        label=label,
        phrase=phrase or PHRASES[label][0],
        score=score,
        box=Box.from_pixels(x1, y1, x2, y2, width, height),
        mask=rle,
        area=rle.area() / (width * height),
    )


# --------------------------------------------------------------------------- run lengths


def test_rle_encodes_row_major_runs_starting_with_zeros() -> None:
    mask = [[0, 0, 1], [1, 1, 0]]  # flattened: 0 0 1 1 1 0
    rle = Rle.encode(mask)
    assert (rle.height, rle.width, rle.counts) == (2, 3, (2, 3, 1))
    assert rle.area() == 3
    assert rle.decode().tolist() == [[False, False, True], [True, True, False]]


def test_rle_first_pixel_set_starts_with_an_empty_zero_run() -> None:
    assert Rle.encode([[1, 1], [0, 1]]).counts == (0, 2, 1, 1)
    assert Rle.encode([[1, 1], [1, 1]]).counts == (0, 4)
    assert Rle.encode([[0, 0], [0, 0]]).counts == (4,)
    assert Rle.encode([[0, 0], [0, 0]]).area() == 0


@pytest.mark.parametrize("seed", range(5))
def test_rle_round_trips_random_masks(seed: int) -> None:
    rng = np.random.default_rng(seed)
    mask = rng.random((37, 53)) > 0.6
    rle = Rle.encode(mask)
    assert (rle.decode() == mask).all()
    assert rle.area() == int(mask.sum())
    assert sum(rle.counts) == 37 * 53
    assert Rle.model_validate_json(rle.model_dump_json()) == rle


def test_rle_rejects_runs_that_do_not_cover_the_mask() -> None:
    with pytest.raises(ValidationError):
        Rle(height=2, width=2, counts=(1, 2))
    with pytest.raises(ValidationError):
        Rle(height=1, width=2, counts=(3, -1))
    with pytest.raises(ValueError):
        Rle.encode(np.zeros((1, 2, 2)))


def test_rle_iou_and_bbox_by_hand() -> None:
    a = Rle.encode(rect_mask(10, 10, 0, 0, 4, 5))  # 20 px
    b = Rle.encode(rect_mask(10, 10, 2, 0, 6, 5))  # 20 px, overlap 2 x 5 = 10
    assert a.iou(b) == pytest.approx(10 / 30)
    assert a.iou(a) == 1.0
    assert a.bbox() == (0, 0, 4, 5)
    assert Rle.encode(np.zeros((3, 3))).bbox() is None
    assert Rle.encode(np.zeros((3, 3))).iou(Rle.encode(np.zeros((3, 3)))) == 0.0
    with pytest.raises(ValueError):
        a.iou(Rle.encode(np.zeros((5, 5))))


def test_box_iou_and_pixels_by_hand() -> None:
    a = Box(x1=0.0, y1=0.0, x2=0.5, y2=0.5)
    b = Box(x1=0.25, y1=0.25, x2=0.75, y2=0.75)
    assert box_iou(a, b) == pytest.approx(0.0625 / (0.25 + 0.25 - 0.0625))
    assert box_iou(a, Box(x1=0.6, y1=0.6, x2=0.7, y2=0.7)) == 0.0
    assert a.iou(a) == 1.0
    pixels = Box.from_pixels(-5, 30, 820, 60, 800, 600)  # overshoot is clamped
    assert (pixels.x1, pixels.y1, pixels.x2, pixels.y2) == (0.0, 0.05, 1.0, 0.1)
    with pytest.raises(ValidationError):
        Box(x1=0.5, y1=0.0, x2=0.4, y2=1.0)


# --------------------------------------------------------------------------- phrases


def test_every_label_but_other_has_one_to_three_distinct_short_phrases() -> None:
    assert set(PHRASES) == set(ComponentLabel) - {L.OTHER}
    assert len(PHRASES) == 77  # 71 + the six labels of EVAL_REPORT section 19
    seen: dict[str, ComponentLabel] = {}
    for label, phrases in PHRASES.items():
        assert 1 <= len(phrases) <= 3, label
        for phrase in phrases:
            assert phrase == phrase.strip().lower() and 1 <= len(phrase.split()) <= 3, phrase
            assert phrase not in seen, f"{phrase!r} is used by {seen.get(phrase)} and {label}"
            seen[phrase] = label
    assert PHRASES[L.ELECTRICAL_OUTLET] == ("electrical outlet", "wall outlet")
    assert PHRASES[L.LIGHT_FIXTURE] == ("light fixture", "ceiling light")
    assert PHRASES[L.WINDOW] == ("window",)
    assert PHRASES[L.KITCHEN_CABINET] == ("kitchen cabinet",)


def test_countable_set_and_prompt_labels() -> None:
    assert COUNTABLE == {
        L.WINDOW,
        L.LIGHT_FIXTURE,
        L.OUTDOOR_LIGHTING,
        L.ELECTRICAL_OUTLET,
        L.INTERIOR_DOOR,
        L.EXTERIOR_DOOR,
        L.KITCHEN_CABINET,
    }
    labels = loc.prompt_labels(["toilet", "other", "not_a_label", L.WINDOW])
    assert set(labels) == COUNTABLE | {L.TOILET}
    order = list(ComponentLabel)
    assert labels == sorted(labels, key=order.index)  # deterministic request
    assert loc.prompt_labels([]) == sorted(COUNTABLE, key=order.index)


# --------------------------------------------------------------------------- replicate fake


class FakeReplicate:
    """The Replicate HTTP API as a ``MockTransport`` handler with scripted behaviour."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        *,
        polls: int = 0,
        create_failures: list[int | str] | None = None,
        status: str = "succeeded",
        predict_time: float = 1.5,
        never_finishes: bool = False,
    ) -> None:
        self.results = results
        self.polls_left = polls
        self.create_failures = list(create_failures or [])
        self.status = status
        self.predict_time = predict_time
        self.never_finishes = never_finishes
        self.requests: list[httpx.Request] = []
        self.canceled = False

    def prediction(self, status: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": "pred1",
            "status": status,
            "urls": {
                "get": "https://api.replicate.com/v1/predictions/pred1",
                "cancel": "https://api.replicate.com/v1/predictions/pred1/cancel",
            },
            "output": None,
            "error": None,
        }
        if status not in ("starting", "processing"):
            body["metrics"] = {"predict_time": self.predict_time}
            if status == "succeeded":
                body["output"] = {
                    "results": [
                        f"https://replicate.delivery/xezq/abc/prompt_{i + 1:04d}.json"
                        for i in range(len(self.results))
                    ],
                    "visualizations": [],
                }
            else:
                body["error"] = "CUDA out of memory"
        return body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url.endswith("/cancel"):
            self.canceled = True
            return httpx.Response(200, json=self.prediction("canceled"))
        if request.method == "POST":
            if self.create_failures:
                failure = self.create_failures.pop(0)
                if failure == "timeout":
                    raise httpx.ReadTimeout("read timed out", request=request)
                if failure == 429:
                    return httpx.Response(
                        429, headers={"Retry-After": "0"}, json={"detail": "slow"}
                    )
                return httpx.Response(int(failure), json={"detail": "boom"})
            running = self.never_finishes or self.polls_left > 0
            return httpx.Response(
                201, json=self.prediction("processing" if running else self.status)
            )
        if url.startswith("https://api.replicate.com/v1/predictions/"):
            if self.never_finishes:
                return httpx.Response(200, json=self.prediction("processing"))
            self.polls_left -= 1
            status = "processing" if self.polls_left > 0 else self.status
            return httpx.Response(200, json=self.prediction(status))
        if url.startswith("https://replicate.delivery/"):
            index = int(url.rsplit("_", 1)[1].split(".")[0]) - 1
            return httpx.Response(200, content=json.dumps(self.results[index]).encode())
        return httpx.Response(404, json={"detail": "not found"})


def result(
    *items: tuple[tuple[int, int, int, int], float], size: tuple[int, int]
) -> dict[str, Any]:
    width, height = size
    return {
        "boxes": [list(map(float, rect)) for rect, _ in items],
        "scores": [score for _, score in items],
        "masks": [[rect_mask(height, width, *rect).tolist()] for rect, _ in items],
    }


EMPTY: dict[str, Any] = {"boxes": [], "scores": [], "masks": []}


def localizer(
    fake: Callable[[httpx.Request], httpx.Response], budget: Budget | None = None
) -> ReplicateSam3Localizer:
    return ReplicateSam3Localizer(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        token=TOKEN,
        version=VERSION,
        concurrency=2,
        budget=budget,
        poll_interval_s=0.0,
        retry_delays=(0.0, 0.0, 0.0),
    )


SIZE = (80, 60)
Tile = loc.Tile


async def test_request_is_one_prediction_with_every_phrase_in_order() -> None:
    image = image_of(*SIZE)
    fake = FakeReplicate([EMPTY, EMPTY, EMPTY])
    phrases = [
        Phrase(label=L.WINDOW, text="window"),
        Phrase(label=L.ELECTRICAL_OUTLET, text="electrical outlet"),
        Phrase(label=L.ELECTRICAL_OUTLET, text="wall outlet"),
    ]
    result_ = await localizer(fake).localize(image, phrases, threshold=0.4)
    create = fake.requests[0]
    assert (create.method, str(create.url)) == ("POST", "https://api.replicate.com/v1/predictions")
    assert create.headers["authorization"] == f"Bearer {TOKEN}"
    assert create.headers["prefer"] == "wait=60"
    body = json.loads(create.content)
    assert body["version"] == VERSION
    assert body["input"]["image"] == "data:image/jpeg;base64," + image.base64
    assert body["input"]["prompts"] == [
        '{"text": "window"}',
        '{"text": "electrical outlet"}',
        '{"text": "wall outlet"}',
    ]
    assert body["input"]["confidence_threshold"] == 0.4
    assert body["input"]["visualize"] is False
    # Result files come from the delivery host, without the token.
    downloads = [r for r in fake.requests if r.url.host == "replicate.delivery"]
    assert len(downloads) == 3 and all("authorization" not in r.headers for r in downloads)
    assert result_.instances == ()
    assert (result_.width, result_.height, result_.image_sha256) == (*SIZE, image.sha256)
    [call] = result_.calls
    assert call.prompts == ("window", "electrical outlet", "wall outlet")
    assert call.status == "succeeded" and call.prediction_id == "pred1"


async def test_masks_become_run_lengths_and_boxes_are_normalized() -> None:
    image = image_of(*SIZE)
    fake = FakeReplicate([result(((8, 6, 24, 30), 0.9), ((40, 0, 80, 12), 0.45), size=SIZE)])
    found = await localizer(fake).localize(
        image, [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    [only] = found.instances  # the 0.45 instance is below the threshold
    assert only.label is L.WINDOW and only.phrase == "window" and only.source == "model"
    assert only.score == 0.9
    assert (only.box.x1, only.box.y1, only.box.x2, only.box.y2) == (0.1, 0.1, 0.3, 0.5)
    assert only.mask is not None and only.mask.bbox() == (8, 6, 24, 30)
    assert only.area == pytest.approx(16 * 24 / (80 * 60))
    assert counts(found) == {"window": 1}


async def test_phrases_of_one_label_are_deduped_but_labels_are_not() -> None:
    image = image_of(*SIZE)
    same = (10, 10, 30, 30)
    nearly = (10, 10, 30, 31)  # mask IoU 400/420 > 0.8
    fake = FakeReplicate(
        [
            result((same, 0.7), size=SIZE),  # electrical outlet
            result((nearly, 0.9), ((50, 40, 60, 50), 0.6), size=SIZE),  # wall outlet
            result((same, 0.8), size=SIZE),  # window: another label, kept
        ]
    )
    phrases = [
        Phrase(label=L.ELECTRICAL_OUTLET, text="electrical outlet"),
        Phrase(label=L.ELECTRICAL_OUTLET, text="wall outlet"),
        Phrase(label=L.WINDOW, text="window"),
    ]
    found = await localizer(fake).localize(image, phrases, threshold=0.5)
    outlets = [i for i in found.instances if i.label is L.ELECTRICAL_OUTLET]
    assert sorted((i.score, i.phrase) for i in outlets) == [
        (0.6, "wall outlet"),
        (0.9, "wall outlet"),
    ]
    assert counts(found) == {"electrical_outlet": 2, "window": 1}

    # Without dedupe (the phrase-pool experiment, EVAL_REPORT 19) every phrase keeps its
    # instance, so a subset of the phrases can be scored offline as the pipeline would.
    fake.requests.clear()
    raw = await localizer(fake).localize(image, phrases, threshold=0.5, dedupe=False)
    assert sorted((i.score, i.phrase) for i in raw.instances if i.label is L.ELECTRICAL_OUTLET) == [
        (0.6, "wall outlet"),
        (0.7, "electrical outlet"),
        (0.9, "wall outlet"),
    ]
    assert loc.suppress(raw.instances, loc.PHRASE_DEDUPE_IOU) == list(found.instances)


async def test_running_prediction_is_polled_and_cost_is_charged() -> None:
    budget = Budget(remaining_usd=1.0)
    fake = FakeReplicate([EMPTY], polls=3, predict_time=2.0)
    found = await localizer(fake, budget).localize(
        image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    polls = [r for r in fake.requests if r.method == "GET" and r.url.host == "api.replicate.com"]
    assert len(polls) == 3
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in polls)
    [call] = found.calls
    assert call.predict_seconds == 2.0
    assert call.cost_usd == pytest.approx(2.0 * loc.L40S_USD_PER_SECOND)
    assert budget.spent_usd == pytest.approx(call.cost_usd) and budget.calls == 1
    assert found.cost_usd == call.cost_usd and found.predict_seconds == 2.0


async def test_budget_is_reserved_before_anything_is_sent() -> None:
    fake = FakeReplicate([EMPTY])
    with pytest.raises(BudgetExceededError):
        await localizer(fake, Budget(remaining_usd=0.001)).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert fake.requests == []


@pytest.mark.parametrize("failures", [[429], [503], ["timeout"], [429, 500, "timeout"]])
async def test_transient_failures_are_retried(failures: list[int | str]) -> None:
    fake = FakeReplicate([EMPTY], create_failures=failures)
    found = await localizer(fake).localize(
        image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    assert found.calls[0].status == "succeeded"
    assert len([r for r in fake.requests if r.method == "POST"]) == len(failures) + 1


async def test_retries_are_bounded_and_the_error_is_typed(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeReplicate([EMPTY], create_failures=[503] * 10)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LocalizerUnavailable) as caught:
        await localizer(fake).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert caught.value.status == 503
    assert len(fake.requests) == 4  # one try and three retries
    assert TOKEN not in str(caught.value) and TOKEN not in caplog.text


async def test_retry_after_is_honoured() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and not slept:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return FakeReplicate([EMPTY])(request)

    client = localizer(handler)
    client.sleep = sleep
    await client.localize(image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5)
    assert slept == [7.0]


@pytest.mark.parametrize("status", [401, 404, 422])
async def test_other_client_errors_are_not_retried(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeReplicate([EMPTY], create_failures=[status])
    with pytest.raises(LocalizerError) as caught:
        await localizer(fake).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert not isinstance(caught.value, LocalizerUnavailable)
    assert caught.value.status == status and len(fake.requests) == 1
    assert "boom" in str(caught.value)
    assert TOKEN not in str(caught.value) and TOKEN not in caplog.text


async def test_failed_prediction_is_a_bad_response_and_still_charged() -> None:
    budget = Budget(remaining_usd=1.0)
    fake = FakeReplicate([EMPTY], status="failed", predict_time=3.0)
    with pytest.raises(LocalizerBadResponse, match="CUDA out of memory"):
        await localizer(fake, budget).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert budget.spent_usd == pytest.approx(3.0 * loc.L40S_USD_PER_SECOND)


async def test_a_prediction_past_its_deadline_is_canceled() -> None:
    fake = FakeReplicate([EMPTY], never_finishes=True)
    client = localizer(fake)
    client.deadline_s = 0.0
    with pytest.raises(LocalizerUnavailable, match="canceled"):
        await client.localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert fake.canceled


@pytest.mark.parametrize(
    "payload",
    [
        {"boxes": [[1, 2, 3, 4]], "scores": [], "masks": []},
        {"boxes": [[1, 2, 3, 4]], "scores": [0.9], "masks": [[[[1]]], [[[1]]]]},
        {"scores": [0.9]},
        {"boxes": [["a", 2, 3, 4]], "scores": [0.9], "masks": []},
    ],
)
async def test_malformed_results_are_bad_responses(payload: dict[str, Any]) -> None:
    with pytest.raises(LocalizerBadResponse):
        await localizer(FakeReplicate([payload])).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )


async def test_result_count_must_match_the_prompts() -> None:
    with pytest.raises(LocalizerBadResponse, match="1 results for 2 prompts"):
        await localizer(FakeReplicate([EMPTY])).localize(
            image_of(*SIZE),
            [Phrase(label=L.WINDOW, text="window"), Phrase(label=L.SINK, text="sink")],
            threshold=0.5,
        )


async def test_a_mask_of_an_unexpected_shape_is_dropped_but_the_box_kept() -> None:
    payload = {"boxes": [[8, 6, 24, 30]], "scores": [0.9], "masks": [[[[[1]]]]]}
    found = await localizer(FakeReplicate([payload])).localize(
        image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    [only] = found.instances
    assert only.mask is None and only.area == pytest.approx(0.2 * 0.4)


async def test_no_phrases_makes_no_call() -> None:
    fake = FakeReplicate([])
    found = await localizer(fake).localize(image_of(*SIZE), [], threshold=0.5)
    assert found.instances == () and found.calls == () and fake.requests == []


def test_a_localizer_needs_a_token_and_hides_it() -> None:
    with pytest.raises(LocalizerError):
        ReplicateSam3Localizer(client=httpx.AsyncClient(), token="", version=VERSION)
    client = ReplicateSam3Localizer(client=httpx.AsyncClient(), token=TOKEN, version=VERSION)
    assert TOKEN not in repr(client)


async def test_segment_prompt_sends_points_and_boxes_and_keeps_the_tapped_instance() -> None:
    """One prompt object per v3 phrase of the label (the phrases the photo was localized
    with, not v1's ``electrical outlet``), each carrying the points and boxes; the
    instances of both are pooled, then narrowed to the tapped one and the box's match."""
    image = image_of(*SIZE)
    fake = FakeReplicate(
        [
            result(((8, 6, 24, 30), 0.6), ((40, 30, 70, 50), 0.9), ((0, 0, 4, 4), 0.95), size=SIZE),
            result(((41, 31, 70, 50), 0.97), size=SIZE),
        ],
        predict_time=2.0,
    )
    found = await localizer(fake).segment_prompt(
        image,
        label=L.ELECTRICAL_OUTLET,
        points=[Point(x=0.2, y=0.3)],  # pixel (16, 18): inside the first instance
        boxes=[Box(x1=0.5, y1=0.5, x2=0.875, y2=0.8333)],  # the second instance exactly
        negative_points=[Point(x=0.9, y=0.9)],
    )
    prompts = [json.loads(p) for p in json.loads(fake.requests[0].content)["input"]["prompts"]]
    assert [p["text"] for p in prompts] == ["wall outlet", "power outlet"]
    for prompt in prompts:
        assert prompt["positive_points"] == [[0.2, 0.3]]
        assert prompt["negative_points"] == [[0.9, 0.9]]
        [[cx, cy, w, h]] = prompt["positive_boxes"]
        assert (cx, cy, w, h) == pytest.approx((0.6875, 0.66665, 0.375, 0.3333))
    # The box keeps its best match (IoU 1.0 at 0.9) over a near copy with a higher score.
    assert [(i.score, i.source, i.label, i.phrase) for i in found.instances] == [
        (0.6, "manual", L.ELECTRICAL_OUTLET, "wall outlet"),
        (0.9, "manual", L.ELECTRICAL_OUTLET, "wall outlet"),
    ]
    assert found.returned == 4
    [call] = found.calls
    assert call.prompts == ("wall outlet", "power outlet")
    assert found.predict_seconds == 2.0 and found.cost_usd == pytest.approx(2.0 * 0.000975)
    with pytest.raises(ValueError):
        await localizer(fake).segment_prompt(image, label=L.WINDOW)


async def test_segment_prompt_box_gate_needs_iou_half_and_the_threshold() -> None:
    """In concept mode the model returns every similar instance: one overlapping the box
    below IoU 0.5 is not the drawn object, and one scored under the threshold is dropped
    even when it fits the box exactly."""
    image = image_of(*SIZE)
    box = Box(x1=0.5, y1=0.5, x2=0.875, y2=0.8333)  # pixels (40, 30, 70, 50)
    beside = result(((55, 30, 80, 50), 0.95), size=SIZE)  # IoU 300/800 = 0.375 with the box
    found = await localizer(FakeReplicate([beside])).segment_prompt(
        image, label=L.WINDOW, boxes=[box]
    )
    assert found.instances == () and found.returned == 1
    faint = result(((40, 30, 70, 50), 0.45), ((40, 30, 66, 50), 0.6), size=SIZE)
    found = await localizer(FakeReplicate([faint])).segment_prompt(
        image, label=L.WINDOW, boxes=[box]
    )
    [kept] = found.instances
    assert kept.score == 0.6 and found.returned == 1  # 0.45 is below DEFAULT_THRESHOLD
    sent = FakeReplicate([faint])
    await localizer(sent).segment_prompt(image, label=L.WINDOW, boxes=[box])
    body = json.loads(sent.requests[0].content)["input"]
    assert body["confidence_threshold"] == loc.DEFAULT_THRESHOLD == 0.5
    assert [json.loads(p)["text"] for p in body["prompts"]] == ["house window"]


async def test_segment_prompt_of_a_label_without_phrases_uses_its_name() -> None:
    fake = FakeReplicate([EMPTY])
    found = await localizer(fake).segment_prompt(
        image_of(*SIZE), label=L.OTHER, boxes=[Box(x1=0.1, y1=0.1, x2=0.2, y2=0.2)]
    )
    prompt = json.loads(json.loads(fake.requests[0].content)["input"]["prompts"][0])
    assert prompt["text"] == "other" and found.instances == ()
    assert [p.text for p in loc.prompt_phrases(L.LIGHT_FIXTURE)] == [
        "recessed light",
        "ceiling lamp",
    ]
    assert [p.text for p in loc.prompt_phrases(L.LIGHT_FIXTURE, "v1")] == list(
        PHRASES[L.LIGHT_FIXTURE]
    )


def test_best_box_match_gate_ties_and_under_point() -> None:
    size = (100, 100)
    a = instance(L.WINDOW, 0.6, (10, 10, 50, 50), size)
    b = instance(L.WINDOW, 0.9, (10, 10, 50, 50), size)  # same box, higher score
    c = instance(L.WINDOW, 0.99, (30, 30, 70, 70), size)  # IoU 400/2800 with a and b
    drawn = Box(x1=0.1, y1=0.1, x2=0.5, y2=0.5)
    assert loc.best_box_match([a, b, c], drawn) == 1  # IoU 1.0 twice: the higher score
    assert loc.best_box_match([a, a], drawn) == 0  # full tie: the first
    assert loc.best_box_match([c], drawn) is None  # IoU 0.14 < 0.5
    assert loc.best_box_match([c], drawn, min_iou=0.1) == 0
    assert loc.best_box_match([], drawn) is None
    exact = Box(x1=0.0, y1=0.0, x2=0.5, y2=0.5)
    half = instance(L.WINDOW, 0.5, (0, 0, 50, 25), size)  # IoU exactly 0.5 with exact
    assert loc.best_box_match([half], exact) == 0  # the gate is inclusive
    # A tap: the highest score among the masks covering it; none: None.
    assert loc.under_point([a, b, c], Point(x=0.4, y=0.4)) == 2
    assert loc.under_point([a, b], Point(x=0.2, y=0.2)) == 1
    assert loc.under_point([a, b], Point(x=0.9, y=0.9)) is None
    assert loc.pick_prompted([a, c], [], [drawn]) == [a.model_copy(update={"source": "manual"})]


# --------------------------------------------------------------------------- tiling

FULL = (200, 150)


def test_tiles_cover_the_image_with_the_requested_overlap() -> None:
    pieces = tiles(image_of(*FULL), grid=2, overlap=0.2)
    assert len(pieces) == 4
    # Tile side = ceil(200 / 1.8) = 112 and ceil(150 / 1.8) = 84; the last ends on the border.
    assert [t.offset for t in pieces] == [(0, 0), (88, 0), (0, 66), (88, 66)]
    assert {t.crop_size for t in pieces} == {(112, 84)}
    assert all(t.full_size == FULL and t.scale == 1.0 for t in pieces)
    assert (112 - 88) / 112 == pytest.approx(0.2, abs=0.02)
    assert (pieces[3].image.width, pieces[3].image.height) == (112, 84)
    assert pieces[0].crop == pieces[0].image.jpeg
    assert len(tiles(image_of(*FULL), grid=1)) == 1
    with pytest.raises(ValueError):
        tiles(image_of(*FULL), grid=0)


def test_tile_pixels_are_the_crop_of_the_full_image() -> None:
    rect = (120, 90, 150, 120)  # only in the bottom-right tile
    pieces = tiles(image_of(*FULL, [rect]))
    tile = pieces[3]
    crop = np.asarray(Image.open(io.BytesIO(tile.crop)).convert("L")) < 128
    rows, cols = np.flatnonzero(crop.any(axis=1)), np.flatnonzero(crop.any(axis=0))
    x0, y0 = tile.offset
    found = (cols[0] + x0, rows[0] + y0, cols[-1] + 1 + x0, rows[-1] + 1 + y0)
    assert all(abs(a - b) <= 1 for a, b in zip(found, rect, strict=True))


RECTS = [
    (10, 10, 30, 30),  # top-left tile only
    (95, 20, 105, 40),  # inside the horizontal overlap (both top tiles)
    (95, 70, 105, 80),  # inside all four tiles
    (150, 100, 190, 140),  # bottom-right only
    (60, 5, 130, 15),  # crosses the overlap: whole in no tile
]


def tile_localization(
    tile: Tile, rects: list[tuple[int, int, int, int]], score: float
) -> Localization:
    """What a perfect detector returns on ``tile``: every rectangle clipped to it, in tile
    coordinates (the sent size: crop x scale)."""
    (x0, y0), (cw, ch) = tile.offset, tile.crop_size
    sw, sh = tile.image.width, tile.image.height
    found = []
    for x1, y1, x2, y2 in rects:
        cx1, cy1, cx2, cy2 = max(x1, x0), max(y1, y0), min(x2, x0 + cw), min(y2, y0 + ch)
        if cx1 >= cx2 or cy1 >= cy2:
            continue
        sx = sw / cw
        local = (
            round((cx1 - x0) * sx),
            round((cy1 - y0) * sx),
            round((cx2 - x0) * sx),
            round((cy2 - y0) * sx),
        )
        found.append(instance(L.ELECTRICAL_OUTLET, score, local, (sw, sh)))
    return Localization(image_sha256=tile.image.sha256, width=sw, height=sh, instances=tuple(found))


def test_merge_maps_tiles_back_exactly_and_suppresses_duplicates() -> None:
    image = image_of(*FULL)
    pieces = tiles(image)
    empty = Localization(image_sha256=image.sha256, width=FULL[0], height=FULL[1])
    merged = merge_tiled(empty, [(t, tile_localization(t, RECTS[:4], 0.8)) for t in pieces])
    assert len(merged.instances) == 4  # the overlap copies are suppressed
    got = sorted(i.mask.bbox() for i in merged.instances if i.mask is not None)
    assert got == sorted(RECTS[:4])
    for inst in merged.instances:
        assert inst.mask is not None
        x1, y1, x2, y2 = inst.mask.bbox() or (0, 0, 0, 0)
        assert (inst.box.x1, inst.box.y1, inst.box.x2, inst.box.y2) == pytest.approx(
            (x1 / 200, y1 / 150, x2 / 200, y2 / 150)
        )
        assert (inst.mask.height, inst.mask.width) == (150, 200)
        assert inst.area == pytest.approx((x2 - x1) * (y2 - y1) / (200 * 150))


def test_merge_with_a_scaled_tile_maps_back_exactly() -> None:
    image = image_of(*FULL)
    pieces = tiles(image, long_side=224)  # each 112 x 84 crop sent at 224 x 168
    assert {(t.image.width, t.image.height, t.scale) for t in pieces} == {(224, 168, 2.0)}
    empty = Localization(image_sha256=image.sha256, width=FULL[0], height=FULL[1])
    merged = merge_tiled(empty, [(t, tile_localization(t, RECTS[:4], 0.8)) for t in pieces])
    assert sorted(i.mask.bbox() for i in merged.instances if i.mask is not None) == sorted(
        RECTS[:4]
    )


def test_a_cut_object_with_its_full_image_detection() -> None:
    """The rectangle crossing the overlap is whole in the full-image pass and cut in both
    top tiles. Complete instances rank first; IoU keeps a cut piece that is less than
    half of the object, intersection-over-smaller suppresses it."""
    image = image_of(*FULL)
    pieces = tiles(image)
    whole = instance(L.ELECTRICAL_OUTLET, 0.6, RECTS[4], FULL)
    full = Localization(
        image_sha256=image.sha256, width=FULL[0], height=FULL[1], instances=(whole,)
    )
    per_tile = [(t, tile_localization(t, [RECTS[4]], 0.9)) for t in pieces]
    by_iou = merge_tiled(full, per_tile)
    by_ios = merge_tiled(full, per_tile, metric="ios")
    assert by_iou.instances[0] == whole  # the complete one wins although it scores lower
    # Pieces: x 60..112 (52 of 70 px, IoU 0.74: suppressed) and 88..130 (42 px, IoU 0.6:
    # suppressed); both above 0.5 here, so IoU and IoS agree on this one.
    assert len(by_iou.instances) == len(by_ios.instances) == 1
    small = Localization(
        image_sha256=image.sha256,
        width=FULL[0],
        height=FULL[1],
        instances=(instance(L.ELECTRICAL_OUTLET, 0.6, (10, 50, 120, 60), FULL),),
    )
    cut = [(t, tile_localization(t, [(10, 50, 120, 60)], 0.9)) for t in pieces[:2]]
    # Top-right piece: x 88..120 = 32 of 110 px (IoU 0.29): kept by IoU, not by IoS.
    assert len(merge_tiled(small, cut).instances) == 2
    assert len(merge_tiled(small, cut, metric="ios").instances) == 1


def test_merge_rejects_a_tile_of_another_image() -> None:
    image = image_of(*FULL)
    other = tiles(image_of(100, 100))
    empty = Localization(image_sha256=image.sha256, width=FULL[0], height=FULL[1])
    with pytest.raises(ValueError):
        merge_tiled(empty, [(other[0], tile_localization(other[0], [], 0.9))])


# --------------------------------------------------------------------------- pipeline


async def test_localize_photo_prompts_vlm_labels_and_countable_and_tiles_countable_only() -> None:
    image = image_of(*FULL)
    fake = FakeLocalizer(
        {
            "toilet": [instance(L.TOILET, 0.9, (10, 10, 50, 60), FULL)],
            "window": lambda img: (
                [instance(L.WINDOW, 0.8, (100, 20, 140, 60), FULL)]
                if (img.width, img.height) == FULL
                else []
            ),
        }
    )
    plain = await localize_photo(
        image, ["toilet"], fake, tiling=False, threshold=0.5, phrase_set="v1"
    )
    [(sha, texts, threshold)] = fake.calls
    expected = {t for label in COUNTABLE | {L.TOILET} for t in PHRASES[label]}
    assert sha == image.sha256 and set(texts) == expected and threshold == 0.5
    assert counts(plain) == {"toilet": 1, "window": 1}

    fake.calls.clear()
    tiled = await localize_photo(
        image, ["toilet"], fake, tiling=True, threshold=0.5, phrase_set="v1"
    )
    assert len(fake.calls) == 5  # the full image and four tiles
    countable = {t for label in COUNTABLE for t in PHRASES[label]}
    assert all(set(texts) == countable for _, texts, _ in fake.calls[1:])
    assert counts(tiled) == {"toilet": 1, "window": 1}
    assert tiled.image_sha256 == image.sha256 and len(tiled.calls) == 5
    assert loc.unlocalized(["toilet", "sink", "other"], tiled) == ["sink"]


async def test_fake_localizer_applies_threshold_and_records_prompts() -> None:
    image = image_of(*SIZE)
    fake = FakeLocalizer({"window": [instance(L.WINDOW, 0.3, (1, 1, 5, 5), SIZE)]})
    found = await fake.localize(image, [Phrase(label=L.WINDOW, text="window")], threshold=0.5)
    assert found.instances == ()
    tapped = await fake.segment_prompt(
        image, label=L.WINDOW, points=[Point(x=3 / 80, y=3 / 60)], threshold=0.2, phrase_set="v1"
    )
    assert [i.source for i in tapped.instances] == ["manual"] and tapped.returned == 1
    assert fake.prompted[0][1]["label"] is L.WINDOW
    assert fake.prompted[0][1]["phrases"] == ["window"]
    assert (
        await fake.segment_prompt(image, label=L.WINDOW, points=[Point(x=0.1, y=0.1)])
    ).instances == ()  # the default (v3) phrase "house window" has no script


def test_counts_with_a_minimum_score() -> None:
    loc_ = Localization(
        image_sha256="x",
        width=10,
        height=10,
        instances=(
            instance(L.WINDOW, 0.9, (0, 0, 2, 2), (10, 10)),
            instance(L.WINDOW, 0.4, (5, 5, 7, 7), (10, 10)),
            instance(L.SINK, 0.6, (3, 3, 4, 4), (10, 10)),
        ),
    )
    assert counts(loc_) == {"sink": 1, "window": 2}
    assert counts(loc_, min_score=0.5) == {"sink": 1, "window": 1}
    assert loc_.counts == counts(loc_)


def test_phrase_sets_share_the_table_except_the_rephrased_classes() -> None:
    from basis.vision.localize import PHRASE_SETS, phrases_for

    assert PHRASE_SETS["v1"] is PHRASES
    changed = {k for k in PHRASES if PHRASE_SETS["v2"][k] != PHRASES[k]}
    assert changed == {
        ComponentLabel.LIGHT_FIXTURE,
        ComponentLabel.OUTDOOR_LIGHTING,
        ComponentLabel.KITCHEN_CABINET,
        ComponentLabel.INTERIOR_DOOR,
    }
    # v3 (EVAL_REPORT 19) and the pool re-phrase exactly the countable classes
    for name in ("v3", "pool"):
        assert {k for k in PHRASES if PHRASE_SETS[name][k] != PHRASES[k]} == set(loc.COUNTABLE)
        assert set(PHRASE_SETS[name]) == set(PHRASES)
    # No phrase is shared between two labels in any set (a hit must map to one label).
    for table in PHRASE_SETS.values():
        seen: dict[str, ComponentLabel] = {}
        for label, texts in table.items():
            for text in texts:
                assert text not in seen, (text, seen.get(text), label)
                seen[text] = label
    assert [p.text for p in phrases_for([ComponentLabel.KITCHEN_CABINET], "v2")] == ["cabinet door"]
    assert [p.text for p in phrases_for([ComponentLabel.KITCHEN_CABINET], "v1")] == [
        "kitchen cabinet"
    ]


def test_v3_is_the_default_and_every_v3_phrase_comes_from_the_pool() -> None:
    """The shipped set is a per-class choice from the measured pool (EVAL_REPORT 19), so
    every v3 phrase of a countable class was in the pool run that chose it."""
    from basis.vision.localize import DEFAULT_PHRASE_SET, PHRASE_SETS, phrases_for

    assert DEFAULT_PHRASE_SET == "v3"
    for label in loc.COUNTABLE:
        assert set(PHRASE_SETS["v3"][label]) <= set(PHRASE_SETS["pool"][label]), label
    assert [p.text for p in phrases_for([ComponentLabel.WINDOW])] == ["house window"]
    assert [p.text for p in phrases_for([ComponentLabel.LIGHT_FIXTURE])] == [
        "recessed light",
        "ceiling lamp",
    ]
    assert PHRASE_SETS["v3"][ComponentLabel.TOILET] == PHRASES[ComponentLabel.TOILET]


# --------------------------------------------------------------------------- product step


def test_surface_labels_are_the_surfaces_prompts_list() -> None:
    """``SURFACE_LABELS`` mirrors the released photo_surfaces prompt's list, so the product
    step never prompts a label the surfaces call owns and prompts every one the countables
    call owns."""
    from basis.llm import prompts
    from basis.vision.analyze import SURFACES_PROMPT_NAME, SURFACES_PROMPT_VERSION

    text = prompts.load(SURFACES_PROMPT_NAME, SURFACES_PROMPT_VERSION).text
    listed = text.split("ONLY surfaces from this list, at most one entry per label, count 1:")[1]
    listed = listed.split("Floors:")[0]
    names = {part.strip().split(" ")[0].strip(".") for part in listed.replace("\n", " ").split(",")}
    assert {label.value for label in loc.SURFACE_LABELS} == names


def test_object_labels_drop_surfaces_and_other_and_keep_the_countable_set() -> None:
    chosen = loc.object_labels(["wall_finish", "toilet", "other", "carpet", "not_a_label"])
    assert ComponentLabel.TOILET in chosen
    assert not {ComponentLabel.WALL_FINISH, ComponentLabel.CARPET, ComponentLabel.OTHER} & set(
        chosen
    )
    assert loc.COUNTABLE <= set(chosen)
    assert loc.object_labels([]) == loc.prompt_labels([])  # the countable set alone


def test_count_thresholds_are_versioned_with_their_phrase_set() -> None:
    """v1: EVAL_REPORT 10.2 (window 0.6, others 0.5); v3, the default: section 19's
    leave-one-photo-out choice per class. Candidates are kept below every count threshold."""
    assert loc.count_threshold(ComponentLabel.WINDOW, "v1") == 0.6
    assert loc.count_threshold("light_fixture", "v1") == 0.5
    expected = {
        "window": 0.65,
        "interior_door": 0.5,
        "exterior_door": 0.85,
        "light_fixture": 0.7,
        "outdoor_lighting": 0.3,
        "electrical_outlet": 0.55,
        "kitchen_cabinet": 0.7,
    }
    for label, value in expected.items():
        assert loc.count_threshold(label) == value == loc.count_threshold(label, "v3")
    assert {k.value: v for k, v in loc.COUNT_THRESHOLDS.items()} == expected
    assert set(loc.COUNT_THRESHOLDS) == set(loc.COUNTABLE)
    assert loc.count_threshold("toilet") == loc.COUNT_THRESHOLD_DEFAULT == 0.5
    assert loc.count_threshold("not_a_label") == 0.5
    assert loc.count_threshold("window", "v2") == 0.5  # a set without a table: the default
    assert loc.CANDIDATE_THRESHOLD < min(
        loc.COUNT_THRESHOLD_DEFAULT,
        *(v for table in loc.COUNT_THRESHOLD_SETS.values() for v in table.values()),
    )


def test_class_counts_use_each_classes_threshold() -> None:
    found = Localization(
        image_sha256="x",
        width=10,
        height=10,
        instances=(
            instance(L.WINDOW, 0.7, (0, 0, 2, 2), (10, 10)),
            instance(L.WINDOW, 0.6, (5, 5, 7, 7), (10, 10)),  # below window's 0.65
            instance(L.OUTDOOR_LIGHTING, 0.32, (3, 3, 4, 4), (10, 10)),  # above its 0.3
            instance(L.TOILET, 0.45, (8, 8, 9, 9), (10, 10)),  # below the default 0.5
        ),
    )
    assert loc.class_counts(found) == {"outdoor_lighting": 1, "window": 1}
    assert loc.class_counts(found, "v1") == {"window": 2}


# --------------------------------------------------------------------------- compact output


def offset_result(
    *items: tuple[tuple[int, int, int, int], float], size: tuple[int, int]
) -> dict[str, Any]:
    """The model's ``offset_masks`` answer: each mask cut to its box, with the cut-out's
    upper-left corner (what vufinder/sam3 returns, EVAL_REPORT 30)."""
    width, height = size
    full = result(*items, size=size)
    cutouts, offsets = [], []
    for rect, _ in items:
        x1, y1, x2, y2 = rect
        cutouts.append(rect_mask(height, width, x1, y1, x2, y2)[y1:y2, x1:x2].tolist())
        offsets.append([x1, y1])
    return {**full, "masks": cutouts, "masks_offset": offsets}


def test_offset_masks_paste_back_to_the_full_mask_exactly() -> None:
    items = (((8, 6, 24, 30), 0.9), ((0, 0, 5, 3), 0.8), ((70, 50, 80, 60), 0.7))
    phrase = Phrase(label=L.WINDOW, text="window")
    full = loc.parse_result(result(*items, size=SIZE), phrase, *SIZE, 0.5)
    cut = loc.parse_result(offset_result(*items, size=SIZE), phrase, *SIZE, 0.5)
    assert cut == full
    assert [i.mask.bbox() for i in cut if i.mask] == [
        (8, 6, 24, 30),
        (0, 0, 5, 3),
        (70, 50, 80, 60),
    ]


def test_an_offset_cutout_past_the_border_is_clipped() -> None:
    data = {
        "boxes": [[70, 50, 80, 60]],
        "scores": [0.9],
        "masks": [np.ones((20, 20), dtype=int).tolist()],
        "masks_offset": [[70, 50]],
    }
    [only] = loc.parse_result(data, Phrase(label=L.WINDOW, text="window"), *SIZE, 0.5)
    assert only.mask is not None and only.mask.bbox() == (70, 50, 80, 60)
    assert only.mask.area() == 10 * 10


@pytest.mark.parametrize(
    "offsets", [[[1, 2], [3, 4]], [["a", 2]], [[1]], "0,0"], ids=["count", "nan", "short", "str"]
)
def test_malformed_offsets_are_bad_responses(offsets: Any) -> None:
    data = {"boxes": [[0, 0, 2, 2]], "scores": [0.9], "masks": [[[1, 1], [1, 1]]]}
    with pytest.raises(LocalizerBadResponse):
        loc.parse_result(
            {**data, "masks_offset": offsets}, Phrase(label=L.WINDOW, text="window"), *SIZE, 0.5
        )


class CompactReplicate(FakeReplicate):
    """Answers as the model does with ``split_output`` off: one file, a list of results."""

    def prediction(self, status: str) -> dict[str, Any]:
        body = super().prediction(status)
        if body.get("output"):
            body["output"]["results"] = ["https://replicate.delivery/xezq/abc/prompt_0001.json"]
        return body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://replicate.delivery/"):
            self.requests.append(request)
            return httpx.Response(200, content=json.dumps(self.results).encode())
        return super().__call__(request)


async def test_compact_output_is_requested_and_one_file_holds_every_prompt() -> None:
    fake = CompactReplicate(
        [offset_result(((8, 6, 24, 30), 0.9), size=SIZE), EMPTY], predict_time=1.0
    )
    phrases = [Phrase(label=L.WINDOW, text="window"), Phrase(label=L.SINK, text="sink")]
    found = await localizer(fake).localize(image_of(*SIZE), phrases, threshold=0.5)
    sent = json.loads(fake.requests[0].content)["input"]
    assert (sent["offset_masks"], sent["split_output"]) == (True, False)
    assert [r.url.host for r in fake.requests].count("replicate.delivery") == 1
    [window] = found.instances
    assert window.mask is not None and window.mask.bbox() == (8, 6, 24, 30)

    # compact=False sends the input as before section 30: full masks, a file per prompt.
    plain = FakeReplicate([result(((8, 6, 24, 30), 0.9), size=SIZE), EMPTY])
    client = localizer(plain)
    client.compact = False
    again = await client.localize(image_of(*SIZE), phrases, threshold=0.5)
    sent = json.loads(plain.requests[0].content)["input"]
    assert "offset_masks" not in sent and "split_output" not in sent
    assert again.instances == found.instances


async def test_a_result_file_not_served_yet_is_fetched_again() -> None:
    """Live, one result file of 52 photos answered 404 right after its prediction
    succeeded (EVAL_REPORT 30); it is retried instead of failing the photo."""
    fake = FakeReplicate([result(((8, 6, 24, 30), 0.9), size=SIZE)])
    misses = [1]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "replicate.delivery" and misses:
            misses.pop()
            fake.requests.append(request)
            return httpx.Response(404, json={"detail": "requested file not found"})
        return fake(request)

    found = await localizer(handler).localize(
        image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    assert len(found.instances) == 1
    assert [r.url.host for r in fake.requests].count("replicate.delivery") == 2
    # Only the result files: a 404 from the API (an unknown version) still fails at once.
    with pytest.raises(LocalizerError) as caught:
        await localizer(FakeReplicate([EMPTY], create_failures=[404])).localize(
            image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
        )
    assert not isinstance(caught.value, LocalizerUnavailable)


# --------------------------------------------------------------------------- self-hosted Cog

COG = "https://sam3-test.a.run.app"
ID_TOKEN = "id-token-never-logged"  # pragma: allowlist secret


def data_uri(document: Any) -> str:
    return (
        "data:application/json;base64," + base64.b64encode(json.dumps(document).encode()).decode()
    )


class FakeCog:
    """A Cog HTTP server: ``POST /predictions`` answers when done, files as data URIs."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        *,
        failures: Sequence[int | str] = (),
        status: str = "succeeded",
        split: bool = False,
        output: Any = None,
    ) -> None:
        self.results = results
        self.failures = list(failures)
        self.status = status
        self.split = split
        self.output = output
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            failure = self.failures.pop(0)
            if failure == "timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            return httpx.Response(int(failure), json={"detail": "busy"})
        files = [data_uri(r) for r in self.results] if self.split else [data_uri(self.results)]
        body: dict[str, Any] = {
            "status": self.status,
            "output": self.output if self.output is not None else {"results": files},
            "metrics": {"predict_time": 1.25},
            "error": "CUDA error" if self.status != "succeeded" else None,
        }
        return httpx.Response(200, json=body)


def cog(
    fake: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = ID_TOKEN,
    seconds: float = 2.0,
) -> loc.CogSam3Localizer:
    ticks = iter([100.0, 100.0 + seconds] * 10)
    return loc.CogSam3Localizer(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        url=COG + "/",
        version=VERSION,
        token_source=loc.static_token(token) if token else None,
        concurrency=2,
        retry_delays=(0.0, 0.0, 0.0),
        clock=lambda: next(ticks),
    )


async def test_cog_localizes_in_one_request_with_the_bearer_and_charges_wall_time() -> None:
    image = image_of(*SIZE)
    fake = FakeCog([offset_result(((8, 6, 24, 30), 0.9), size=SIZE), EMPTY])
    phrases = [Phrase(label=L.WINDOW, text="window"), Phrase(label=L.SINK, text="sink")]
    found = await cog(fake, seconds=2.0).localize(image, phrases, threshold=0.4)
    [request] = fake.requests
    assert (request.method, str(request.url)) == ("POST", f"{COG}/predictions")
    assert request.headers["authorization"] == f"Bearer {ID_TOKEN}"
    sent = json.loads(request.content)["input"]
    assert sent["image"] == "data:image/jpeg;base64," + image.base64
    assert sent["prompts"] == ['{"text": "window"}', '{"text": "sink"}']
    assert (sent["confidence_threshold"], sent["offset_masks"], sent["split_output"]) == (
        0.4,
        True,
        False,
    )
    [window] = found.instances
    assert window.mask is not None and window.mask.bbox() == (8, 6, 24, 30)
    [call] = found.calls
    assert call.predict_seconds == 1.25 and call.prediction_id is None
    assert call.cost_usd == pytest.approx(2.0 * loc.CLOUD_RUN_L4_USD_PER_SECOND, abs=1e-6)
    # A file per prompt (split_output on) reads the same.
    split = FakeCog([offset_result(((8, 6, 24, 30), 0.9), size=SIZE), EMPTY], split=True)
    assert (await cog(split).localize(image, phrases, threshold=0.4)).instances == found.instances


@pytest.mark.parametrize("failures", [[409], [429], [503], ["timeout"], [409, 429, 500]])
async def test_cog_retries_busy_throttled_and_transient(failures: list[int | str]) -> None:
    fake = FakeCog([EMPTY], failures=failures)
    found = await cog(fake).localize(
        image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5
    )
    assert found.calls[0].status == "succeeded" and len(fake.requests) == len(failures) + 1


async def test_cog_errors_are_typed_and_never_carry_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    one = [Phrase(label=L.WINDOW, text="window")]
    with pytest.raises(LocalizerUnavailable) as busy:
        await cog(FakeCog([EMPTY], failures=[409] * 10)).localize(
            image_of(*SIZE), one, threshold=0.5
        )
    with pytest.raises(LocalizerError) as denied:
        await cog(FakeCog([EMPTY], failures=[403])).localize(image_of(*SIZE), one, threshold=0.5)
    assert denied.value.status == 403 and not isinstance(denied.value, LocalizerUnavailable)
    with pytest.raises(LocalizerBadResponse, match="CUDA error"):
        await cog(FakeCog([EMPTY], status="failed")).localize(image_of(*SIZE), one, threshold=0.5)
    with pytest.raises(LocalizerBadResponse, match="data URI"):
        await cog(FakeCog([EMPTY], output={"results": ["https://x/1.json"]})).localize(
            image_of(*SIZE), one, threshold=0.5
        )
    with pytest.raises(LocalizerBadResponse, match="1 results for 2 prompts"):
        await cog(FakeCog([EMPTY])).localize(
            image_of(*SIZE), [*one, Phrase(label=L.SINK, text="sink")], threshold=0.5
        )
    for error in (busy.value, denied.value):
        assert ID_TOKEN not in str(error)
    assert ID_TOKEN not in caplog.text
    assert ID_TOKEN not in repr(cog(FakeCog([EMPTY])))


async def test_cog_without_a_token_sends_no_authorization_and_no_phrases_no_call() -> None:
    fake = FakeCog([EMPTY])
    client = cog(fake, token=None)
    await client.localize(image_of(*SIZE), [Phrase(label=L.WINDOW, text="window")], threshold=0.5)
    assert "authorization" not in fake.requests[0].headers
    found = await client.localize(image_of(*SIZE), [], threshold=0.5)
    assert found.calls == () and len(fake.requests) == 1
    with pytest.raises(LocalizerError, match="http"):
        loc.CogSam3Localizer(client=httpx.AsyncClient(), url="sam3:5000", version=VERSION)


async def test_cog_segment_prompt_keeps_the_tapped_instance() -> None:
    fake = FakeCog(
        [
            offset_result(((8, 6, 24, 30), 0.6), ((40, 30, 70, 50), 0.9), size=SIZE),
            offset_result(size=SIZE),
        ]
    )
    found = await cog(fake).segment_prompt(
        image_of(*SIZE), label=L.ELECTRICAL_OUTLET, points=[Point(x=0.2, y=0.3)]
    )
    assert [(i.score, i.source) for i in found.instances] == [(0.6, "manual")]
    assert found.cost_usd == pytest.approx(2.0 * loc.CLOUD_RUN_L4_USD_PER_SECOND, abs=1e-6)
    prompts = [json.loads(p) for p in json.loads(fake.requests[0].content)["input"]["prompts"]]
    assert prompts == [
        {"text": "wall outlet", "positive_points": [[0.2, 0.3]]},
        {"text": "power outlet", "positive_points": [[0.2, 0.3]]},
    ]


async def test_google_id_tokens_are_cached_and_refreshed_before_expiry() -> None:
    fetched: list[str] = []
    now = [0.0]

    def fetch(audience: str) -> str:
        fetched.append(audience)
        return f"token-{len(fetched)}"

    source = loc.google_id_token(COG, fetch=fetch, clock=lambda: now[0])
    assert await source() == "token-1"
    now[0] = loc.ID_TOKEN_LIFETIME_S - loc.ID_TOKEN_REFRESH_S - 1
    assert await source() == "token-1"
    now[0] += 2
    assert await source() == "token-2"
    assert fetched == [COG, COG]
