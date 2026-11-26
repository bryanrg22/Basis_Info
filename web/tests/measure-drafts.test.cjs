// The server's AI-drafted exterior measurements (EVAL_REPORT 110, docs/api/MEASURE_DRAFTS.md) in the
// measure tool's own model (web/live/property-model.js): each stored record (fixtures/measure-drafts.json,
// written by basis.domain.measure_drafts for the synthetic gable house of tests/unit/geo/test_shell.py
// and pinned there by test_the_web_fixture_is_the_servers_record) is offered as the stored measurement,
// reads "Drafted by Basis", and, sent back untouched and verified as the tool sends it, measures to the
// server's quantity to the cent. The row's quantity is that shape's times the draft's factor.
const test = require('node:test');
const assert = require('node:assert/strict');
const PM = require('../live/property-model.js');
const records = require('./fixtures/measure-drafts.json');

for (const [label, r] of Object.entries(records)) {
  test(`the ${label} draft round-trips through the tool's model at the server's quantity`, () => {
    const stored = r.measurement;
    assert.equal(stored.drawn_by, 'model:lidar-shell@v1');
    assert.equal(stored.verified, false);
    const offer = PM.measureOffer({ label, takeoff: { unit: stored.unit, inputs: { measurement: stored } } });
    assert.ok(offer.stored, 'offered as the stored measurement');
    const body = PM.bodyOfRecord(stored);
    assert.equal(PM.drawnByText(body.drawn_by), 'Drafted by Basis');
    body.verified = true;
    const got = PM.measure(body);
    assert.equal(Number(got.quantity).toFixed(2), stored.quantity);
    assert.ok(Number(r.quantity) > 0);
  });
}
