"""Property pictures: dated views of a study's property, taken in a headless browser by a
worker of their own (``python -m basis.capture``), gently and one at a time
(docs/review_2026-10-15/PROPERTY_CAPTURE.md, docs/api/PROPERTY_CAPTURE.md, EVAL_REPORT 98).

* :mod:`.camera`: the viewer's camera address (``@lat,lon,alt a,range d,fov y,heading h,tilt
  t,0r``) and the pixel size of a top-down view.
* :mod:`.signals`: what a block looks like (the unusual-traffic page, reCAPTCHA, a consent or
  sign-in wall, HTTP 429 on the hosts that matter) and a blank render.
* :mod:`.governor`: the breaker and the caps, as pure functions of the shared state.
* :mod:`.store`: what the runner reads and writes, as a protocol, with an in-memory version for
  the unit tests; :mod:`.pg_store` is the Postgres one.
* :mod:`.browser`: the browser the runner drives, as a protocol; :mod:`.playwright_browser`
  drives Chromium through Playwright.
* :mod:`.runner`: one capture, start to end, and what every failure leaves behind.
* :mod:`.workflow`, :mod:`.worker`: the Temporal workflow and the worker process.
* :mod:`.health`: the ``property_capture`` member of ``GET /v1/ops/health``.
"""
