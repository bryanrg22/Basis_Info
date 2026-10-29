# Field regions: highlighting an extracted value on the appraisal PDF

`GET /v1/studies/{study_id}/fields` returns, on every field, where the value is printed:

```json
{
  "key": "subject.county",
  "value_text": "Montrose",
  "document_id": "0b7e...",
  "pdf_document_id": "9d42...",
  "page": 1,
  "regions": [
    { "page": 1, "bbox": [0.7918, 0.085, 0.8497, 0.0943] }
  ]
}
```

**Which PDF.** The boxes are on the PDF of `pdf_document_id`, fetched with
`GET /v1/studies/{id}/documents/{pdf_document_id}/pdf`: the appraisal itself for a PDF upload,
the report PDF inside a UAD 3.6 ZIP (same id as `document_id`), or a bare MISMO XML's companion
PDF (docs/api/UAD36_INTAKE.md). It is recorded on the field's extraction revision; never draw a
revision's regions on another PDF (for example the latest upload). `null`: there is no report
PDF (an XML alone): draw nothing and say so (the extraction has the `no_report_pdf` issue;
`GET /v1/studies/{id}/extraction` summarizes the revision).

| Member | Meaning |
|---|---|
| `regions` | A list of boxes to draw (usually one). Empty when the value was not located. |
| `page` | The 1-based PDF page of that box (always the field's own `page`). |
| `bbox` | `[x0, y0, x1, y1]` as fractions (0..1) of the page's width and height, origin at the **top-left** corner of the page. |

## Drawing it

Fractions do not depend on the zoom or the PDF's units. For a page rendered `width` x
`height` pixels (pdf.js: the page's viewport at the current scale), draw an absolutely
positioned rectangle over the page:

```js
for (const r of field.regions.filter((r) => r.page === renderedPage)) {
  const [x0, y0, x1, y1] = r.bbox;
  const box = document.createElement("div");
  box.className = "field-highlight";
  Object.assign(box.style, {
    position: "absolute",
    left: `${x0 * width}px`,
    top: `${y0 * height}px`,
    width: `${(x1 - x0) * width}px`,
    height: `${(y1 - y0) * height}px`,
  });
  pageLayer.appendChild(box); // a layer the same size as the rendered page canvas
}
```

Scroll to the first box. Draw every box in `regions`: a checkbox under a column header (the
utilities grid's "Public") has two, the header and the checked box in the field's row.

When `regions` is empty, keep the current behaviour (search the page's text layer for the
value and say how many matches there are), on the `pdf_document_id` PDF. Fields stored
before regions existed have `[]`; fields stored before `pdf_document_id` was recorded report
the appraisal itself (or `null` for a bare XML).

## How the boxes are chosen (backend)

`basis/extraction/regions.py`, from Azure Document Intelligence's word, checkbox and key-value
boxes, on the field's cited page only:

1. **Checkbox fields** (occupant, assignment type, utilities, foundation, car storage...): the
   checked box whose label names the value, the one nearest the field's caption; the box is
   drawn with its label ("☒ Purchase Transaction"), not the same words elsewhere on the page.
2. **A value Azure DI read as a key-value pair**: that pair's value box.
3. **Otherwise** the value's words nearest the field's printed caption ("Montrose" beside
   "County", not the city's or the neighborhood's "Montrose"); with no caption on the page,
   the value's first occurrence.
4. **No region** when the value's words are not on its cited page (never a box on another
   page).

Measured on the appraisal evaluation set (41 documents, replayed; scanned pages have no text
layer to check against and are left out): the first box holds the value for 99.3% of the
correctly extracted fields (3,224 of 3,248 for the production pipeline). Of the rest, 19 are
on another page because the extraction cited the wrong page, 3 are a wrong box and 2 have no
region.
