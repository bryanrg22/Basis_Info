# Seeing the app locally: sign-in and a study at every step

For a developer, or a coding agent (Claude Code, Codex), who needs to look at the web app's
screens: to review the UI, take screenshots, or walk someone through a study. It covers the
three ways to get there, how to sign in from an agent's browser, and why sessions get stuck
on the sign-in page. Every step here was run on 2026-11-05 (see "How this was checked").

`docs/DEVELOPING.md` section 3 and `web/README.md` "Live mode, locally" are the general
setup; this file is the part they leave out.

## 1. Pick the way in

| You need | Use | Sign-in | Data |
|---|---|---|---|
| The design prototype, including a study still running (older five-step flow) | **Mock mode** (section 2) | none | fixtures in `web/review-data.js` |
| The real app on a finished study, all seven steps | **An existing live stack** (section 3) | test account | a real study in its database |
| A live stack of your own | **Live mode from scratch** (section 4) | test account you create | empty until you run a study |

Mock mode is the older five-step prototype: it has no Components, Results or Report & sign
screens and no live drag-to-room editor. For the current seven-step app, use a live stack.

## 2. Mock mode (no sign-in, no backend)

```bash
python3 web/serve.py --port 3100
```

Open `http://127.0.0.1:3100/#dashboard` (any free port works). Nothing is sent anywhere and
nothing is saved.
Screens are reached by the hash: `#landing`, `#login`, `#dashboard`, `#upload`,
`#processing` (a study in progress), `#appraisal`, `#viewer` (`?fixture=dense#viewer` adds a
38-object stress case), `#audit`, `#asset-detail`, `#activity`, `#team`, `#settings`,
`#roadmap`. The room photos are not in git; copy them into `web/assets/` from
`design-review/assets/` (`web/README.md` "Mock mode") or the frames show empty.

It stops at the assets screens: Components, Results and Report & sign exist only in live mode.
Mock and live share the CSS and helpers but not every screen's markup (`web/README.md`
"Editing the design"): a change seen in mock mode is checked in live mode before it ships.

## 3. An existing live stack

A machine may already run a stack with a finished study. On Bryan's machine it is the
cofounder demo, described in `~/basis-eval-data/demo/README.md` (outside the repository,
because its database is client data). Use it; do not start a second stack.

1. Check it is up: `~/basis-eval-data/demo/demo.sh status`. If anything says `DOWN`,
   `~/basis-eval-data/demo/demo.sh start`. Do not run `docker compose up`, `uvicorn` or
   another API yourself: a new stack has an empty database and no account in its firm,
   which is the usual way an agent ends up stuck at sign-in.
2. Open `http://127.0.0.1:3600/?mode=live&api=http://127.0.0.1:8600#dashboard`.
3. Sign in with the **Local test account** form (section 5), with the email and password in
   `~/basis-eval-data/demo/signin.txt`. They are Auth-emulator test values that exist only
   on that machine.
4. The finished study: `#processing/49a9a90b-5a61-4ed6-9b4e-ae2aace6f594`. Every step from
   Upload to Report & sign is complete. The step pages open from the step bar.

Serving your own copy of `web/` (a branch's UI changes) against that API is fine: start
`python3 <your-worktree>/web/serve.py --port <free port>` and open
`http://127.0.0.1:<port>/?mode=live&api=http://127.0.0.1:8600#dashboard`. The demo API
accepts any origin (`CORS_ALLOWED_ORIGINS="*"`, which the API allows only with
`BASIS_ENV=dev`). Another stack may list origins one by one; then see section 6, first row.

What changes the shared study: saving the firm profile or the study details after signing
marks the report **Report needs updating** (the signed PDF is kept; a new revision must be
signed). That is the product working as designed. Look, do not save, unless the change is
the point.

## 4. Live mode from scratch

The stack and the web app, as `web/README.md` "Live mode, locally" says: the compose files
with the web overlay, then `python3 web/serve.py --port 3100`. The overlay allows only the
origin `http://127.0.0.1:3100`, so serve the web app on 3100, or add your origin to
`CORS_ALLOWED_ORIGINS` and restart the API (it reads the setting once, at start).

**The Auth emulator, with its accounts kept.** The emulator forgets every account when it
stops, and a firm membership belongs to the account's uid, so a restarted emulator means the
same email is a stranger to its firm. Keep its state:

```bash
firebase emulators:start --only auth --project demo-basis --export-on-exit=.emulator-auth           # first time
firebase emulators:start --only auth --project demo-basis --import=.emulator-auth --export-on-exit  # after that
```

Keep `.emulator-auth` outside the repository, or untracked.

**A test account that can join a firm.** Firms are invite-only, and accepting an invitation
needs a verified email. Email-and-password accounts in the emulator are unverified, and the
Google popup is hard for an agent to drive, so create the account and mark it verified
through the emulator's REST API (these calls reach only the local emulator):

```bash
E=http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1
EMAIL=you@example.test; PASSWORD=<a test password, at least 6 characters>
LOCAL_ID=$(curl -s "$E/accounts:signUp?key=demo-api-key" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"returnSecureToken\":true}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["localId"])')
curl -s "$E/projects/demo-basis/accounts:update" -H 'Authorization: Bearer owner' \
  -H 'Content-Type: application/json' -d "{\"localId\":\"$LOCAL_ID\",\"emailVerified\":true}"
```

`Authorization: Bearer owner` is the emulator's admin credential; real Firebase does not
accept it. Write the test password into an untracked file beside your notes, not into chat
or the repository.

**A firm, with that account as its owner:**

```bash
docker compose -f docker-compose.yml -f docker-compose.web.yml exec api \
  python -m basis.admin create-firm --name "Local firm" --owner-email you@example.test
```

Then sign in with the Local test account form (section 5). The first screen is **Firm
invitation**; choose **Accept and join** and the dashboard opens.

**A study.** The database is empty. A study run needs provider keys in `backEnd/.env` and
costs money (about $2 to $4 with firm conventions, $7 to $8 without; `AGENTS.md`): nothing
paid runs without Bryan's approval in the session. Without that, use mock mode for the
in-progress screens, or a stack that already holds a study (section 3).

## 5. Signing in from an agent's browser

- Use the **Local test account** form (email, password, **Sign in**). It appears only when
  the page talks to the emulator. Do not use **Continue with Google**: it opens a popup with
  the emulator's account picker, which browser tools cannot drive. If Google is needed,
  `?signin=redirect` uses a full-page redirect instead.
- If filling the fields does not register (the button stays inert, or the form says the
  email is missing), set them the way a keystroke would, then submit:

  ```js
  const set=(el,v)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}))};
  set(document.querySelector('#live-email'),EMAIL);
  set(document.querySelector('#live-password'),PASSWORD);
  document.querySelector('form[data-live-form="email"] button[type=submit]').click();
  ```

- Signed in, the page title reads `Basis · Home` (or `Basis · Firm invitation` for a new
  member), and the bar at the top shows the account's email.
- Sign-in is kept per origin: `127.0.0.1:3600`, `localhost:3600` and `127.0.0.1:3610` each
  have their own. A new port means signing in again.
- Entering these values is allowed for agents because they are test credentials for the
  user's own app on a local host. Never type a real password or a production key.

## 6. Stuck at the sign-in page: causes and fixes

| What you see | Cause | Fix |
|---|---|---|
| Sign-in seems to succeed, then the sign-in page again, or "could not reach Basis"; the API log shows `OPTIONS /v1/me ... 400` | The web app's origin (scheme, host and port) is not in the API's `CORS_ALLOWED_ORIGINS`, so the browser blocks every call | Serve on an allowed port, or add the origin (in dev, `*`) and **restart the API**: editing the variable or a start script does nothing to an API already running |
| A popup opens and the agent can go no further | **Continue with Google** | The Local test account form, or `?signin=redirect` |
| "The sign-in service could not be reached. Is the Auth emulator running?" | Nothing on `127.0.0.1:9099` | Start the emulator (section 4), with `--import` if it has saved state |
| "Not a member of a firm yet" | The account is not in a firm, or the emulator restarted and the account has a new uid | `python -m basis.admin create-firm` or `invite` for that email; keep emulator state with `--export-on-exit` |
| The invitation shows but **Accept and join** is refused | The email is not verified (every email-and-password account) | Mark it verified (section 4) |
| The wrong password, or the account does not exist | The emulator was restarted without its saved state | Re-import the saved state, or create the account again and re-invite it |
| Signed in, but the dashboard is empty | A new database | Mock mode, an existing stack, or an approved study run |
| Signed in, the study opens, and the API returns 401 on every call | The API is not in dev (`BASIS_ENV=dev`), or points at another emulator (`FIREBASE_AUTH_EMULATOR_HOST`, `FIREBASE_PROJECT_ID=demo-basis`) | Start the API with those set; it accepts emulator tokens only in dev |

## How this was checked (2026-11-05)

- **The CORS cause** was found on the demo stack. A session served its own `web/` copy on
  port 3610. The demo start script had been edited to allow 3610, but the API had been
  running since before the edit, so its preflights for `/v1/me` returned 400 from 3610 and
  200 from 3600. After the API restarted with `CORS_ALLOWED_ORIGINS="*"`, the same 3610 page
  signed in and listed the studies.
- **The from-scratch path** was run on a new database (`alembic upgrade head` on an empty
  Postgres database, an API on another port, `create-firm`, the two emulator REST calls,
  the Local test account form). It reached **Firm invitation**, and **Accept and join** led
  to "You joined ... as an admin". The database, the API and the account were removed
  afterwards.
- **Not checked here:** the `--export-on-exit` and `--import` round trip (the demo stack's
  `--import` was tested when it was built), and Google sign-in with `?signin=redirect`.
