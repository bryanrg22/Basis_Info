"""``python -m basis.capture.smoke``: can this machine's browser draw what the viewer needs?

The capture worker's image (``Dockerfile.capture``) runs this in CI: it launches the same
headless Chromium with the same flags the capture uses (:func:`.playwright_browser.chromium_args`),
opens a local page (no network, no third-party site), and reports the WebGL2 renderer and
whether a drawn frame is non-blank. It exits 1 when WebGL2 is missing or the frame is blank, so a
container without a working software renderer fails before any capture is attempted. Prints one
JSON line.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from basis.capture.playwright_browser import chromium_args
from basis.config import get_settings

# A page that clears a WebGL2 canvas to a known colour and reads one pixel back.
PAGE = """<!doctype html><canvas id=c width=64 height=64></canvas><script>
const g = document.getElementById('c').getContext('webgl2', {preserveDrawingBuffer: true});
let out = {webgl2: !!g, renderer: null, pixel: null};
if (g) {
  const e = g.getExtension('WEBGL_debug_renderer_info');
  out.renderer = e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER);
  g.clearColor(0.2, 0.6, 0.4, 1); g.clear(g.COLOR_BUFFER_BIT);
  const p = new Uint8Array(4); g.readPixels(32, 32, 1, 1, g.RGBA, g.UNSIGNED_BYTE, p);
  out.pixel = Array.from(p);
}
window.__smoke = out;
</script>"""
EXPECTED_PIXEL = (51, 153, 102)  # 0.2, 0.6, 0.4 of 255, rounded


async def check() -> dict[str, Any]:
    from playwright.async_api import async_playwright

    args = chromium_args(get_settings())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=args)
        try:
            page = await browser.new_page()
            await page.set_content(PAGE)
            result: dict[str, Any] = await page.evaluate("window.__smoke")
        finally:
            await browser.close()
    pixel = result.get("pixel") or []
    result["args"] = args
    result["drawn"] = len(pixel) >= 3 and all(
        abs(int(got) - want) <= 2 for got, want in zip(pixel[:3], EXPECTED_PIXEL, strict=True)
    )
    result["ok"] = bool(result.get("webgl2")) and result["drawn"]
    return result


def main() -> int:
    result = asyncio.run(check())
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
