// Writes the entry pages' panel data from the Harbor House study exactly as the sample engine
// prices it (web/live/sample-api.js, state "signed"), so every figure on the panels is the
// product's own arithmetic, not typed in:
//  * src/data/terminal.json, the sign-in page's terminal: the asset schedule, row by row;
//  * src/data/overview.json, the demo page's study overview: the steps the study went through
//    (appraisal, rooms, pricing, reconciliation, signature) and the basis split into recovery
//    classes. The signer is the landing page's placeholder (John Doe, PE), as everywhere else on
//    the public pages; the sample engine's own signer is a different placeholder.
// Run from web/site/landing: node tools/terminal_data.cjs
const fs = require('node:fs'), path = require('node:path');
const api = require('../../../live/sample-api.js');
(async () => {
  const I = api.create({ state: 'signed', delayMs: 0, fetchAsset: async () => new Uint8Array() });
  const H = { headers: { Authorization: 'Bearer sample-token' } }, B = api.BASE;
  const get = async (u) => (await I.fetch(B + u, H)).json();
  const studies = await get('/v1/studies');
  const study = (studies.items || studies).find((s) => /Harbor/.test(JSON.stringify(s)));
  const assets = (await get(`/v1/studies/${study.id}/assets?limit=200`)).items;
  const result = await get(`/v1/studies/${study.id}/result`);
  const rows = assets.filter((a) => a.cost && a.cost.total !== null).map((a) => {
    const t = a.cost.trail || {};
    return {
      name: a.category, room: a.room ? a.room.name : null, bucket: a.bucket, irs: a.irs_section,
      line: a.cost.source_row ? a.cost.source_row.replace(/^S-/, '').replace(/^(\S+ \S+ \S+).*/, '$1') : null,
      base: t.base ? t.base.unit_cost : null, year: t.year ? t.year.factor : null, loc: a.cost.location_factor,
      qty: t.quantity ? t.quantity.value : null, unit: t.quantity ? t.quantity.unit : null,
      unit_cost: a.cost.unit_cost, total: a.cost.total, kind: t.kind,
    };
  });
  const out = {
    source: 'web/live/sample-api.js, state signed (Harbor House, synthetic)',
    study: { name: 'Harbor House', revision: result.revision ?? null },
    rows,
    by_bucket: result.schedule.by_bucket.map((b) => ({ bucket: b.bucket, rcn: b.rcn })),
    reconciliation: { status: result.reconciliation.status, gap_share: result.reconciliation.gap_share },
  };
  // the panels add the rows up live; check that they reach the schedule's own totals
  for (const b of out.by_bucket) {
    const sum = rows.filter((r) => r.bucket === b.bucket).reduce((s, r) => s + Math.round(Number(r.total) * 100), 0);
    if (sum !== Math.round(Number(b.rcn) * 100)) throw new Error(`${b.bucket}: rows add to ${sum / 100}, schedule says ${b.rcn}`);
  }
  fs.writeFileSync(path.join(__dirname, '../src/data/terminal.json'), JSON.stringify(out));

  const rooms = (await get(`/v1/studies/${study.id}/rooms`)).items;
  const w = result.worksheet, rec = result.reconciliation, al = result.allocation;
  const overview = {
    source: out.source,
    study: out.study,
    price: w.purchase_price, land: w.land, land_source: w.land_source, basis: al.total,
    photos: rooms.reduce((n, r) => n + r.photos.length, 0), rooms: rooms.length,
    priced: result.schedule.priced,
    reconciliation: { status: rec.status, gap_share: rec.gap_share, tolerance: rec.tolerance },
    signer: 'John Doe, PE',
    allocation: al.by_bucket.map((b) => ({ bucket: b.bucket, allocated: b.allocated, share: b.share })),
  };
  // the overview's classes must add up to the basis it shows
  const alSum = overview.allocation.reduce((s, b) => s + Math.round(Number(b.allocated) * 100), 0);
  if (alSum !== Math.round(Number(al.total) * 100)) throw new Error(`classes add to ${alSum / 100}, basis is ${al.total}`);
  if (rec.status !== 'reconciled') throw new Error(`the study is ${rec.status}, not reconciled`);
  fs.writeFileSync(path.join(__dirname, '../src/data/overview.json'), JSON.stringify(overview));
  console.log('overview', JSON.stringify(overview));
  console.log(rows.length, 'rows;', out.by_bucket.map((b) => `${b.bucket} ${b.rcn}`).join(', '), '; gap', out.reconciliation.gap_share);
  console.log(JSON.stringify(rows.slice(0, 4)));
})().catch((e) => { console.error(e); process.exit(1); });
