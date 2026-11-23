# Repository-wide agent rules

These rules apply to every directory in this repository. Directory-level AGENTS.md files (for example `atlas/AGENTS.md`) add their own rules on top, but none of them overrides this section.

## Cost rules (keep work proportional to the change)

- **Tests:** run only the tests related to the change (the touched modules' unit tests, plus Playwright specs for the touched screens). GitHub CI runs the full suites on every PR, so do not rerun the full Vitest or Playwright suites locally across every browser and viewport.
- **Keep these safety gates:** when a change touches drawing, gestures, storage or symbols, still run the related Playwright regressions on mobile WebKit. Never skip, disable or loosen a failing test to move on: fix it first.
- **No verification JSON:** do not create `docs/research-verification/*.json` files or SHA-256 evidence files. CI results and git history are the record.
- **Screenshots:** only for UI layout changes, and then only one 390px-wide screenshot. None for backend, data, or logic fixes.
- **REVIEW_PACKAGE.md:** at most 3 lines per PR.
- **Docs and ADRs:** update them only when the architecture changes, not for routine fixes.
- **PR description:** short — what changed, why, how it was tested, and any deploy steps. No long reports.

## Done means green CI

- **A task is done only when every CI check on the PR's latest commit is green.** Local runs stay limited to related tests (above), but always read the PR's CI result after pushing. If a check fails, find the root cause and fix it before reporting the work as done; never report done while CI is still running or red.
- **Shared modules have more callers than the touched tests.** When a runtime module gains or changes an import or export, update every test fixture that loads it. For example, `atlas/tests` loads Supabase functions with `runInNewContext` and injects their imports by hand, so a new import must be added to each fixture that loads that function.
- **Dependency versions must install everywhere they run.** When changing `requirements.txt` or any other version constraint, check every `python-version` in `.github/workflows/*.yml` and every Dockerfile base image. A lower bound may not exceed what any of those environments can install.

## Before committing

- Run `git status` and make sure tests did not modify `docs/report/kline-corrections.json` or other files under `data/` and `docs/report/`. Revert such changes; never commit them.
- Never rewrite published history: no rebase, amend or force-push on a shared branch.
