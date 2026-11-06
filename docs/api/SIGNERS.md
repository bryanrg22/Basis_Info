# Signer details: each engineer's own name, credentials and license

Each engineer saves the name, credentials and license they sign reports with once, in their
own account, and every report they sign prints them. The firm profile keeps the letterhead
(name, address, phone, logo, limiting conditions). Research, decisions and results:
EVAL_REPORT section 91. Code: `basis/api/routes/signer.py`, `basis/db/repositories/signer.py`,
`basis/report/inputs.py` (`SignerDetails`), `basis/api/routes/report.py` (`_signer`);
migration `0032_signer_details`.

## 1. Why the details are the engineer's, not the firm's

A professional engineer's seal and signature are the individual licensee's. NCEES Model Law
140.10 D: the licensee's seal contains the jurisdiction of licensure, the licensee's name, the
license number and the words "professional engineer". NCEES Model Rules 240.20 D: a licensee
seals and signs only work under their own responsible charge; 240.20 H: a digital signature is
unique to the individual and under their sole control. A firm holds a certificate of
authorization, never a seal. So the license printed under a signature belongs to the person who
signs, and only that person sets it: no route lets an owner write a member's license.

A cost segregation study is not a sealed design document (STUDY_REPORT_SPEC 11.13): Basis's
signature is an electronic signature ("Signed in Basis"), not a PE seal, and the license is
optional (the ATG's element C.1 asks for "an individual with expertise and experience", not a
PE). When a license is given, it is given as a seal gives it: number and jurisdiction together.

## 2. The routes

| Route | Who | Body / answer |
|---|---|---|
| `GET /v1/me/signer` | any signed-in user, their own | `{user_id, signer, version, updated_at}`. `signer` null and `ETag: "0"` until saved (200, not 404: the app asks on every Report & sign load). |
| `PUT /v1/me/signer` | any signed-in user, their own (a firm or not) | `SignerDetails`, written whole. `If-Match` optional: 412 `BASIS-REPORT-016` with the current `ETag` when stale; `*` only over saved details. 200 with the new `ETag`. No `Idempotency-Key` (a whole-document `PUT`, like the intake). |

`SignerDetails`:

| Field | Rule |
|---|---|
| `name` | required; one line, 1 to 500 characters, trimmed |
| `credentials` | required; same rule; e.g. `PE`, `PE, CCSP` |
| `license_number` | optional; one line, 1 to 50 characters, trimmed (the web app's limit) |
| `license_state` | with `license_number`, and only with it; a two-letter code of a jurisdiction that licenses engineers: the 50 states, `DC`, `GU`, `MP`, `PR`, `VI` (the 55 NCEES member jurisdictions; `AS` has no board). Upper case; an `enum` in the OpenAPI document. |

Anything else is 422 (an unknown field too). Saving logs `signer details saved` with the user,
the versions and the names of the changed fields (never the values).

Where others see them (read-only):

| Where | Field |
|---|---|
| `GET /v1/orgs/{org_id}/members` (owners: the Team view), and the role change and removal answers | `signer` per member, null when they saved none |
| `GET /v1/studies/{id}/report/state` (viewers) | `signer`: the signature's signer's details now, next to `signature.engineer` (what was signed); null while unsigned |

## 3. Signing with them

`POST /v1/studies/{id}/report/sign`: the report prints the signer's saved details.

| The signer has saved details | `engineer` in the body | Result |
|---|---|---|
| yes | absent | signed with the saved details; `signature.signer_details_version` is their version |
| yes | the same name, credentials and license (compared trimmed; `email` ignored) | signed with the saved details |
| yes | different | 409 `BASIS-REPORT-015`; nothing signed |
| no | present | signed with the `engineer` sent, as before this change; `signer_details_version` null. With `REPORT_SIGNER_DETAILS_REQUIRED=true`: 409 `BASIS-REPORT-014` |
| no | absent | 409 `BASIS-REPORT-014` |

These checks come after "already signed" (409 `BASIS-REPORT-009`) and before the preview
comparison (412 `BASIS-REPORT-010`). The firm profile's `engineer` is never used to sign: it
would print one licensee's name and license on another's signature.

**The preview names its reader.** `GET .../report/preview` renders the preparer as the reader
who may sign (engineer or owner) with saved details, because that is the report they would
sign; a viewer's details are never used. Without saved details it is the firm profile's
`engineer` (deprecated, below) or a placeholder, as before. Two engineers therefore see
different previews (different `ETag`s), and each signs the one they reviewed: an engineer who
sends a colleague's preview tag gets 412. Editing your details between opening the preview and
signing is the same 412: open the preview again.

**What the signature keeps.** `engineer` (the printed name, credentials and license, as
before), `signer_details_version` (null when typed at signing), and `letterhead`: the firm
profile's `name`, `address`, `phone` and the logo's sha256 as signed (null on signatures
recorded before migration 0032). The `report.signed` event's `detail` carries
`signer_details` (`saved` or `entered`), `signer_details_version` and `letterhead`.

**After signing.** `changed_since_signing` lists `signer` while the signer's saved details
differ from `signature.engineer`. It compares values, not versions: the signature already holds
the values, so an edit that is undone, or a first save of the very details typed at signing, is
not a change. Another engineer's edit never marks your signature. The signed PDF never
changes; a new revision, signed on its own, prints the details current then.

## 4. The firm profile's `engineer` (deprecated)

`PUT /v1/orgs/{org_id}/profile` still accepts `engineer` and `GET` still returns it (marked
`deprecated` in the OpenAPI document), so the current web app keeps working. It is used only as
the preparer of an unsigned draft (the export step's draft, and the preview of a reader who has
no saved details), and never reaches a signed report (it never did: signing always printed the
`engineer` the request named). It is not migrated to anyone's signer details: an owner typed it,
it is linked to no user, and copying a license onto an account its holder did not fill in would
be the misattribution this change removes. Past signatures are not copied either: each attests
the details for that report, not the signer's standing choice.

## 5. Setting

| Variable | Default | Meaning |
|---|---|---|
| `REPORT_SIGNER_DETAILS_REQUIRED` | `false` | `true`: an engineer with no saved details cannot sign (409 `BASIS-REPORT-014`) even when the request names an `engineer`. Off by default while the web app still asks for the details in the sign dialog; turn it on once the app saves them in the account (web/FRONTEND_BACKLOG.md). |

## 6. Tests

`tests/api/test_signer_details.py` (the routes, validation, the preview, every signing case,
the setting, two engineers on two revisions, changed since signing, the members list, two first
saves at once), `tests/unit/report/test_signer_details.py` (the model over every jurisdiction,
the preparer order, `_signer`, the value comparison, the setting),
`tests/integration/db/test_migrations.py` (0032). Rows in `tests/LEDGER.md`.
