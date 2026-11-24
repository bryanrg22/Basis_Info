"""Public geographic sources for a study's property (docs/review_2026-10-15/PROPERTY_CAPTURE.md
section 10, docs/api/PUBLIC_GEO.md, EVAL_REPORT 101): government imagery (state programs and
federal NAIP), the dated series for history, the state's open parcel layer and the federal
lidar, gathered by a worker of their own (``python -m basis.geo``).

* :mod:`.registry` and ``sources.json``: every source, what it covers and how to ask it.
* :mod:`.geometry`: Web Mercator boxes, pixel to lon/lat, areas on the ellipsoid.
* :mod:`.http`: the polite client (one request a second per host, timeouts, retries).
* :mod:`.imagery`, :mod:`.parcels`, :mod:`.lidar`: one module per kind of source.
* :mod:`.site_check`: the parcel's area against the appraisal's site area.
* :mod:`.gather`: one gather, start to end; :mod:`.store` what it writes.
* :mod:`.workflow`, :mod:`.worker`, :mod:`.starter`: Temporal.
"""
