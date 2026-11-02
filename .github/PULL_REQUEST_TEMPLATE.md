<!-- One change per pull request. Plain sentences, no attribution lines, no AI credits.
     docs/DEVELOPING.md section 9 explains each part. Delete what does not apply. -->

One paragraph: what an engineer or the system can do after this that it could not before, and why.

## What changed

- The behaviour, by area (API, workflow, activity, vision, classify, report, web).
- New or changed setting, with its default and the EVAL_REPORT section that chose it.
- New error codes (`BASIS-<AREA>-<nnn>`), routes, migration number, workflow patch name.

## What was measured

- EVAL_REPORT section number and its headline numbers (accuracy, cost, tokens, latency), or
  "nothing measured: <why>".
- Where the run records are (verification tree component/version, run ids).

## Tests

- New test files and the invariant each protects (the same words as the LEDGER rows).
- Edge cases and races covered; the regression check that the existing path is unchanged.
- Anything not deterministic, and how the assertion handles it.

## Not verified, and open

- What this PR does not cover, what it leaves to a follow-up, what could not be tested here.

## Gates

From `backEnd/`, provider keys unset: `ruff check .`, `ruff format --check .`, `mypy basis`,
`python -m basis.errors.catalogue --check`, `python -m basis.api.openapi --check`,
`python -m evals.freeze_rule_fingerprints --check`, unit suite, integration and slow suite,
`node --test web/tests/*.test.cjs` when `web/` changed, `python3 tooling/check_no_client_data.py --ci`.
State each result (counts, duration) or why one was skipped.

## Checklist

- [ ] Research the standard first: the design cites what the established work does.
- [ ] Every number is recorded (EVAL_REPORT section, LEDGER rows, verification tree).
- [ ] Docs updated: `docs/api/*.md` and `openapi.json` for routes, `ERROR_CODES.md` for codes,
      `README.md` or `docs/DEVELOPING.md` where behaviour or process changed.
- [ ] No client data, no secrets, no provider name in customer-facing text.
- [ ] No paid run and no cloud change without approval recorded in the PR.
- [ ] EVAL_REPORT section number and migration number checked against open PRs.
