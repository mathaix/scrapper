# Working on issues in this repo

Read `docs/prd-signals-db.md` and, for anything touching storage or SQL, `docs/designs/final.md` before
changing code. The issue's acceptance criteria are the contract; these steps decide when you are done.

## Steps

1. **Build an acceptance ledger.** List every acceptance criterion and every "Scope" bullet of the issue,
   one line each. Done when each line maps to a file you will change or a test you will write.
2. **Make the change on the ledger, nothing beside it.** Where the issue says "exactly" or points at a design
   doc, match that doc; record each deliberate deviation and its reason for the PR summary. Done when every
   ledger line has code behind it.
3. **Prove each new test red.** Run it against the code before your change and see it fail, then green after.
   Cover the shapes that break real code: multi-element lists (order), ties (equal timestamps), unknown
   values (NULL vs 0/empty), re-running the same input twice (idempotency), and failure paths. Done when every
   new test has been seen red once.
4. **Run the whole suite with zero skips.** A skipped test is an unverified test. Start what tests need (e.g.
   Postgres for `tests/test_schema.py`) and run `python -m pytest -q -rs`. Done when the summary shows 0 failed
   and 0 skipped, or each remaining skip is listed in the PR summary with why it cannot run in CI.
5. **Keep the tree clean.** Put downloads, wheels, caches, scratch scripts and logs under `/tmp`. Run
   `git status --porcelain` and account for every path. Done when only files your ledger needs are changed.
6. **Review your own diff as a hostile reviewer.** Read `git diff` top to bottom and check: deterministic order
   (`order by` on every array/list you build), tie-breaks (`(scraped_at, id)`), NULL handling, destructive
   statements, new config. Done when you would approve it.
7. **Check deploy safety** (see below). Done when the PR either has no deploy-order risk or names it.
8. **Write the summary as the ledger.** For each ledger line: done / not done / needs a maintainer, with the
   test or file that proves it. State only what the final diff contains.

## How this repo ships (facts the code won't tell you)

- **Merging to `main` deploys to production** (`https://claramap--signals.modal.run`) via
  `.github/workflows/deploy.yml`. The live app keeps running the previous code until then.
- **`schema.sql` is applied to the live Supabase database by hand**, separately from deploys. Any statement
  that drops, renames or tightens something the deployed code still uses breaks production the moment it is
  applied. Keep schema changes backward compatible with the deployed code, or add a "Deploy order" section to
  the PR saying exactly what must ship first.
- **New required config breaks the deploy.** A new Modal secret or env var must be optional at startup (feature
  degrades when missing), or listed under "Needs a maintainer" — Modal refuses to deploy an app that references
  a secret that does not exist.
- **The issue agent cannot change `.github/`**: those edits are dropped before push. When CI must change (new
  service, env var, step), put the exact YAML under "Needs a maintainer" in the summary and leave it out of the
  claimed work.

## Conventions

- Tests that touch a database read **`TEST_DATABASE_URL`** and work inside the throwaway `signals_test` schema
  (see `tests/test_schema.py`). Production credentials (`SUPABASE_*`, `DATABASE_URL`) stay out of tests.
- Dependencies: app imports → `requirements.txt` (also add to the Modal image in `modal_app.py`); test-only →
  `requirements-dev.txt`. Add a dependency only when the issue allows it or the stdlib cannot do the job.
- Data model: typed columns for anything queried; NULL means unknown, 0 or `'{}'` means checked-and-none.
  Extraction is rule-based (no AI). The web endpoint is intentionally unauthenticated.
- Keep `create_app(...)` dependency injection: handlers take their collaborators as arguments so tests pass fakes.
