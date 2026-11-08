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
