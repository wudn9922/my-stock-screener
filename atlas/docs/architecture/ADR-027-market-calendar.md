# ADR-027: Read-only published exchange sessions

Date: 2026-11-05

The browser previously knew weekdays and regular hours only. A small generated-owned calendar,
produced from XTAI/XNYS exchange-calendars, now supplies exact session boundaries, holidays and
early closes for the following year. It contains no prices or private user state.

The shared LiveQuoteFeed loads this document once per UTC day without introducing a second poll
loop. Initial last-close reads remain available on closed days. Periodic polling stops on known
holidays and at the exact early close; ordinary close grace remains unchanged. Unavailable,
invalid or out-of-coverage calendars retain the original weekday/hour fallback.

An independent daily generated owner preserves unrelated publishers with existing optimistic
retry semantics. Public extended-quote collection checks the same calendar library by New York
date before fetching. No permanent archive/price records, authentication, Edge Functions or GCP
monitoring are modified. Exchange emergency changes require updating the upstream calendar and
republishing; the browser never guesses future exchange holidays.
