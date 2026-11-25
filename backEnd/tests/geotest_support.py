"""Fakes for the public-geo unit tests: sources, images, an HTTP router (no test touches a
public server)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import httpx
from PIL import Image

from basis.geo.http import PoliteClient
from basis.geo.registry import Source


def source(**kw: Any) -> Source:
    base: dict[str, Any] = {
        "id": "s",
        "kind": "imagery",
        "name": "S",
        "states": ["WI"],
        "bbox": [-97.0, 40.0, -86.0, 47.5],
        "protocol": "arcgis-imageserver",
        "url": "https://img.example.test/arcgis/rest/services/S/ImageServer",
        "native_pixel_m": 0.15,
        "year": 2020,
        "terms_class": "public-domain",
        "terms_quote": "q",
        "terms_url": "https://example.test/terms",
        "credit": "Credit",
    }
    base.update(kw)
    return Source.model_validate(base)


def jpeg(width: int, height: int, *, flat: bool = False) -> bytes:
    img = Image.new("RGB", (width, height), (200, 200, 200))
    if not flat:
        for x in range(0, width, 8):
            for y in range(height):
                img.putpixel((x, y), (10, 40, 20))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


def png(width: int, height: int, *, transparent: bool = False) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0) if transparent else (90, 90, 90, 255))
    if not transparent:
        for x in range(0, width, 5):
            img.putpixel((x, 0), (255, 255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


Handler = Callable[[httpx.Request], httpx.Response]


class Router:
    """Answers by the first matching URL substring; records every request."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, Handler]] = []
        self.requests: list[httpx.Request] = []

    def on(self, fragment: str, handler: Handler | httpx.Response | bytes | dict[str, Any]) -> None:
        if isinstance(handler, httpx.Response):
            response = handler
            self.routes.append((fragment, lambda _r: response))
        elif isinstance(handler, bytes):
            data = handler
            kind = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            self.routes.append(
                (
                    fragment,
                    lambda _r: httpx.Response(200, content=data, headers={"content-type": kind}),
                )
            )
        elif isinstance(handler, dict):
            body = json.dumps(handler).encode()
            self.routes.append(
                (
                    fragment,
                    lambda _r: httpx.Response(
                        200, content=body, headers={"content-type": "application/json"}
                    ),
                )
            )
        else:
            self.routes.append((fragment, handler))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for fragment, handler in self.routes:
            if fragment in url:
                return handler(request)
        return httpx.Response(404, content=b"no route")

    def client(self, **kw: Any) -> PoliteClient:
        async def no_sleep(_s: float) -> None:
            return None

        return PoliteClient(
            transport=httpx.MockTransport(self), sleep=no_sleep, host_interval_s=0, **kw
        )
