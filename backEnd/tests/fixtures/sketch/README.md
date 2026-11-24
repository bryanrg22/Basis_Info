# Fabricated appraisal sketch pages, and the takeoff before the sketch

`pages.json` holds fabricated building-sketch pages as the text Azure Document Intelligence
returns for them (one string per line, in reading order): the drawing's room names and edge
labels first, then the "Area Calculations Summary" table. The layouts follow the two styles
seen in public sample reports (a la mode TOTAL Sketch: "Living Area" / "Non-living Area" with
whole square feet; the redesigned URAR's samples: "Total Above Grade" / "Total Below Grade"
with two decimals), with the OCR damage seen there (a row's calculation split across lines,
an equals sign read as a dash, a lost multiplication sign, a lost operand). Every number is
invented; no client or sample report figure is copied. Used by
`tests/unit/extraction/test_sketch.py`, `tests/unit/domain/test_measurements_sketch.py`,
`tests/unit/extraction/test_pipeline_sketch.py` and `tests/unit/evals/test_sketch_takeoff.py`.
Written by hand (not by `tooling/fixtures/make_fixtures.py`, which owns `../synthetic/`).

`measurements_before_sketch.json` is the takeoff of five fabricated sets of appraisal fields
as main produced it at e885ca1, before `basis/domain/measurements.py` read the sketch
(`tests/unit/domain/measurement_snapshot.py` holds the cases and regenerates it). The
regression test compares today's output to it string for string.
