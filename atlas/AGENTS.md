# Atlas engineering instructions

This is the accepted Phase 1 architecture with authorized Phase 2 extensions of a Web-first technical-analysis product. Read README, `docs/architecture/ADR-001` through `ADR-005` and `docs/VERIFICATION.md` before changing its boundaries. Phase 1 and Phase 2 were accepted. The latest MASTER TASK explicitly authorizes continuous full V1 extensions (drawing tools, indicators, research strategies/backtests, local alerts, migrations, PWA/native configuration). Internal milestones are checkpoints, not user approval gates. Stop after full V1; do not start V2.

## Core invariants

- One Lightweight Charts implementation only, pinned to 5.2.1. Check current official release/migration notes and installed typings before changing versions or APIs.
- Use official skills at `.agent-skills/lightweight-charts/SKILL.md` and `.agent-skills/lightweight-charts-plugin-authoring/SKILL.md` for chart/primitive work. They are project-vendored because the cloud `.agents` directory is protected.
- Drawings render through official series Primitive APIs. Drawing gestures/state/history/hit testing belong outside React.
- Time/price anchors are canonical; never persist pixel positions. Logical metadata is advisory across timeframe/history changes. Future drawing uses whitespace/public logical conversion, never fake OHLC.
- Two Press → Drag → Release gestures for Trend Line, one for Horizontal Line. Commit on release. Editing keeps grab offsets and uses a movement threshold; taps only select.
- Whole-line movement always uses a drag-start snapshot. Magnet applies only to endpoint edits/placement on real bars.
- Lock means no endpoint/body edit and no draggable handles; locked objects remain selectable, and dragging them delegates to native chart pan. Recheck locked state at commit.
- Pointer movement must not trigger React state or persistence. Coalesce to one RAF; persist commits only. Restore native chart handlers on all exits. No scroll correction hacks.
- Drawings, indicators, locks and preferences are owned by symbol. Multiple SMA instances are modeled by id, never MA1/MA2. Locked MAs allow only visibility changes/unlock.
- IndexedDB is versioned. Import validates first and replaces atomically; export awaits writes. Do not silently lose or overwrite user data on errors.
- Provider response shapes stay behind MarketDataProvider/FundamentalsProvider. Demo stays deterministic and visibly simulated. Core development/testing must remain free without paid keys/licenses/cards.
- SEC implementation is Phase 2; metric nulls/provenance, conservative fiscal-quarter derivation and raw/derived auditability are required. Do not fabricate missing EPS or add Phase 3 features.

## Verification and review

Run `npm test`, `npm run lint`, `npm run build`; use `npm ci` for clean dependency validation. For drawing/gesture/storage/symbol changes also run the appropriate Playwright regressions on desktop and mobile/WebKit. Do not skip a failed interaction gate to implement the next tool.

Update ADRs deliberately when architecture changes; do not silently replace the engine or canonical models. Reviewer findings must return to the primary engineer for explicit accept/reject triage before changes. Reviewer tools cannot directly modify code. If independent review is unavailable, maintain REVIEW_PACKAGE.md and do not claim independent sign-off.

Report actual test results. Headless RAF scheduling is not device FPS or a physical Safari verification. Keep limitations and data provenance visible.
