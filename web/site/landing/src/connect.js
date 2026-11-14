// Connector lines between two elements inside a root, drawn in an SVG overlay that covers the
// root. Adds the 'below' route for the hero's footnotes. A link draws itself with
// stroke-dashoffset and retracts when cleared. Pure geometry; the root needs position:relative.
const SVGNS = 'http://www.w3.org/2000/svg';
const EASE = 'cubic-bezier(.22,.61,.36,1)';
const reduce = matchMedia('(prefers-reduced-motion: reduce)');

export function overlay(root, cls = 'link-layer') {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  root.append(svg);
  const size = () => { const r = root.getBoundingClientRect(); svg.setAttribute('viewBox', `0 0 ${r.width} ${r.height}`); svg.setAttribute('width', r.width); svg.setAttribute('height', r.height); };
  size();
  new ResizeObserver(size).observe(root);
  return svg;
}

// The path from a to b: leaves a's side that faces b, enters b's facing side, as a smooth S.
export function route(root, a, b, exit = 'side', gapY = null) {
  const R = root.getBoundingClientRect(), A = a.getBoundingClientRect(), B = b.getBoundingClientRect();
  const ax = A.left - R.left, ay = A.top - R.top, bx = B.left - R.left, by = B.top - R.top;
  if (exit === 'row') {
    // From the cell's bottom edge onto its row's bottom rule (gapY), along the rule past the
    // table, then into the target's facing side: the line never crosses another cell.
    const x1 = ax + A.width / 2, y0 = ay + A.height, yb = gapY ?? y0 + 8, x2 = bx - 4, y2 = by + Math.min(B.height / 2, 26);
    const xm = x2 - 28, bend = Math.min(12, Math.abs(y2 - yb) / 2), dir = y2 < yb ? -1 : 1;
    const vert = Math.abs(y2 - yb) < 2 ? '' : `Q${xm} ${yb} ${xm} ${yb + dir * bend}L${xm} ${y2 - dir * bend}Q${xm} ${y2} ${xm + bend} ${y2}`;
    return [`M${x1} ${y0}L${x1} ${yb - 6}Q${x1} ${yb} ${x1 + 6} ${yb}L${xm - (vert ? bend : 0)} ${yb}${vert}L${x2} ${y2}`, [x1, y0], [x2, y2]];
  }
  if (exit === 'below') {
    // Down from the chip into the gap under its paragraph (gapY), along the gap clear of the
    // text, then up or down into the target's facing side. The chips sit on the paragraph's
    // last line, so the drop crosses no words.
    const x1 = ax + A.width / 2, y0 = ay + A.height + 2, yb = gapY ?? y0 + 12, x2 = bx - 4, y2 = by + B.height / 2;
    const xm = x2 - 40, bend = Math.min(16, Math.abs(y2 - yb) / 2), dir = y2 < yb ? -1 : 1;
    return [`M${x1} ${y0}L${x1} ${yb - 8}Q${x1} ${yb} ${x1 + 8} ${yb}L${xm - bend} ${yb}Q${xm} ${yb} ${xm} ${yb + dir * bend}L${xm} ${y2 - dir * bend}Q${xm} ${y2} ${xm + bend} ${y2}L${x2} ${y2}`, [x1, y0], [x2, y2]];
  }
  if (exit === 'top') {
    // Up into the gap between two lines of text, along the gap past the text, then into the
    // target's facing side: the line never crosses a word.
    const x1 = ax + A.width / 2, y1 = gapY ?? ay - 4, x2 = bx - 4, y2 = by + B.height / 2;
    if (x2 - x1 < 80) return [`M${x1} ${y1}C${x1} ${y1 - 40} ${x2} ${y2 - 40} ${x2} ${y2}`, [x1, y1], [x2, y2]];
    const xm = x2 - 56;
    return [`M${x1} ${y1}L${xm} ${y1}C${x2 - 22} ${y1} ${x2 - 30} ${y2} ${x2} ${y2}`, [x1, y1], [x2, y2]];
  }
  const horizontal = B.left >= A.right - 4 || B.right <= A.left + 4;
  if (horizontal) {
    const right = B.left >= A.right - 4;
    const x1 = right ? ax + A.width + 4 : ax - 4, y1 = ay + A.height / 2;
    const x2 = right ? bx - 4 : bx + B.width + 4, y2 = by + Math.min(B.height / 2, 22);
    const dx = Math.max(24, Math.abs(x2 - x1) * .45) * (right ? 1 : -1);
    return [`M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`, [x1, y1], [x2, y2]];
  }
  const down = B.top >= A.bottom - 4;
  const x1 = ax + A.width / 2, y1 = down ? ay + A.height + 4 : ay - 4;
  const x2 = bx + Math.min(B.width / 2, 40), y2 = down ? by - 4 : by + B.height + 4;
  const dy = Math.max(20, Math.abs(y2 - y1) * .45) * (down ? 1 : -1);
  return [`M${x1} ${y1}C${x1} ${y1 + dy} ${x2} ${y2 - dy} ${x2} ${y2}`, [x1, y1], [x2, y2]];
}

export function link(svg, root, a, b, { delay = 0, cls = 'link', exit = 'side', gapY = null } = {}) {
  const [d, , end] = route(root, a, b, exit, gapY);
  const g = document.createElementNS(SVGNS, 'g');
  g.setAttribute('class', cls);
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', d); path.setAttribute('pathLength', '1');
  const dot = document.createElementNS(SVGNS, 'circle');
  dot.setAttribute('cx', end[0]); dot.setAttribute('cy', end[1]); dot.setAttribute('r', '3.5');
  g.append(path, dot);
  svg.append(g);
  if (!reduce.matches) {
    path.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 520, delay, easing: EASE, fill: 'backwards' });
    dot.animate([{ opacity: 0, transform: 'scale(.2)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 260, delay: delay + 420, easing: EASE, fill: 'backwards' });
  }
  return g;
}

export function clear(svg) {
  for (const g of [...svg.children]) {
    if (reduce.matches) { g.remove(); continue; }
    const path = g.querySelector('path');
    g.querySelector('circle')?.remove();
    path.animate([{ strokeDashoffset: 0, opacity: 1 }, { strokeDashoffset: -1, opacity: .2 }], { duration: 320, easing: 'ease-in' }).finished.then(() => g.remove(), () => g.remove());
  }
}
