# Contributing to Basis

This repository is private to the Basis team. Everyone who can push has read `AGENTS.md`
(the short rules; also what every coding agent reads) and `docs/DEVELOPING.md` (the setup,
the development method, the recipes and the definition of done). This page is the
checklist you come back to.

1. **Agree the approach first** for anything that adds a stage, a model call, a table, a
   route or a setting: a short design note in `docs/review_2026-10-15/` or a conversation
   with Bryan, with the research behind it. A plain fix needs neither.
2. **Branch from `main`** (`<your-handle>/<topic>`), one change per pull request. Never commit
   to `main` directly; it is protected and CI is the gate.
3. **Develop the way the project develops**: research the standard, measure alternatives side
   by side, test every path, record every number (`docs/DEVELOPING.md` sections 4 and 5).
4. **Before you push**, every gate in `AGENTS.md` is green locally with the provider keys
   unset. `pre-commit install` once; the hooks catch the fast failures on each commit.
5. **Open the pull request** with `.github/PULL_REQUEST_TEMPLATE.md` filled in: what changed,
   what was measured, the tests, what is open, the gates. Bryan reviews and merges.

Rules that are never relaxed, private repository or not: client files and secrets never
enter the repository (they are cloned to laptops and CI runners); nothing that costs money
runs without approval; nothing is deployed; commits and pull request bodies carry no
`Co-Authored-By` line and do not credit an AI assistant; nothing a customer sees names a
provider.
