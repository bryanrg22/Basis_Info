// The entry pages' forms.
// Sign-in (signin.html) is the public face only: Google, Microsoft and an emailed sign-in link do
// not sign anyone in yet; each says so and points to the demo page. The team's real sign-in is
// the app's own page (index.html?mode=live#login), which nothing on the public pages links to.
// Book a demo (demo.html): name, work email, company and role are required; each field says what
// is wrong next to it. The request is POSTed as JSON to <basis-api>/v1/demo-requests (the page's
// basis-api meta; the API stores it and emails the team), then the page thanks the visitor by name.
// The hidden `website` field is a spam trap people never fill; the API drops requests that do.
import './terminal.js';
import './overview.js';

const $ = (s, r = document) => r.querySelector(s);

function fieldError(input, text) {
  const box = input.closest('.field'); let err = box.querySelector('.err');
  if (!text) { input.removeAttribute('aria-invalid'); err?.remove(); return true; }
  if (!err) { err = document.createElement('p'); err.className = 'err'; err.id = `${input.id}-err`; box.append(err); }
  err.textContent = text; input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', err.id);
  return false;
}
const check = (input) => {
  const v = input.value.trim();
  if (input.required && !v) return fieldError(input, input.dataset.need || 'Fill this in.');
  if (input.type === 'email' && v && !input.checkValidity()) return fieldError(input, 'Enter an email address like you@firm.com.');
  return fieldError(input, '');
};

// sign-in
const signin = $('[data-signin]');
if (signin) {
  const note = $('[data-signin-note]');
  const tell = () => { note.hidden = false; note.focus(); };
  signin.querySelectorAll('.sso button').forEach((b) => b.addEventListener('click', tell));
  signin.addEventListener('submit', (e) => { e.preventDefault(); const email = $('#email'); if (check(email)) tell(); else email.focus(); });
  $('#email').addEventListener('input', (e) => { if (e.target.hasAttribute('aria-invalid')) check(e.target); });
}

// book a demo
const demo = $('[data-demo]');
if (demo) {
  const inputs = [...demo.querySelectorAll('input,select,textarea')].filter((i) => i.name !== 'website');
  const api = document.querySelector('meta[name=basis-api]')?.content.replace(/\/$/, '');
  inputs.forEach((i) => i.addEventListener(i.tagName === 'SELECT' ? 'change' : 'input', () => { if (i.hasAttribute('aria-invalid')) check(i); }));
  demo.addEventListener('submit', async (e) => {
    e.preventDefault();
    const bad = inputs.filter((i) => !check(i));
    if (bad.length) { bad[0].focus(); return; }
    const data = Object.fromEntries(inputs.map((i) => [i.name, i.value.trim()]));
    const button = demo.querySelector('button[type=submit]');
    button.disabled = true; button.textContent = 'Sending…';
    try {
      if (!api) throw new Error('no API configured');
      const body = { ...data, page: location.href, website: demo.elements.website.value };
      if (!body.note) delete body.note;
      const r = await fetch(`${api}/v1/demo-requests`, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status });
      const done = $('[data-demo-done]');
      done.querySelector('[data-first]').textContent = data.name.split(/\s+/)[0];
      done.querySelector('[data-email]').textContent = data.email;
      $('[data-demo-form]').hidden = true; done.hidden = false; done.querySelector('h1').focus();
    } catch (err) {
      button.disabled = false; button.textContent = 'Request a demo';
      fieldError($('#email'), err.status === 429 ? 'Too many requests from here. Try again in a few minutes.'
        : err.status === 422 ? 'Check the email address and try again.'
        : 'We could not send that just now. Try again in a minute.');
    }
  });
}
