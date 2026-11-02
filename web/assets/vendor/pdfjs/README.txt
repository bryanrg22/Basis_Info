PDF.js 5.6.205, Apache-2.0.
Copied from the bundled workspace dependency runtime, not fetched from a CDN.
Upstream: https://github.com/mozilla/pdf.js
LICENSE is included. The browser renderer, worker, continuous PDFViewer component, styles, and rendering resources are bundled.

Integration change: pdf_viewer.css is wrapped in @scope (.basis-pdf-scroll), with
:root selectors mapped to :scope, so upstream sidebar/dialog styles cannot affect
Basis. Requires a current browser with CSS @scope support.

Recovery normalization: trailing whitespace removed from pdf_viewer.mjs; no executable code changed.
