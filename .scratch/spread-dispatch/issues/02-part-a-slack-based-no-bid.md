# 02: Part A — slack-based no-bid

**What to build:** `quote()` declines any option whose pickup cannot be reached before its deadline with adequate slack (reusing the existing road-time / `_safe_deadline` check), so no losing quote is sent for unreachable pickups. Part A only — never posts gigs, never touches `post()`, `fees_for()`, `retry()`, or `raise_fees()` (per the quote/dispatch split).

**Blocked by:** 01 — Baseline reference run on seed `spread-1`.

**Status:** ready-for-agent

- [ ] `quote()` skips options that fail the deadline-slack check instead of quoting them
- [ ] Running `spread-1` shows fewer quotes sent for unreachable pickups vs baseline
- [ ] Win rate and on-time share hold or improve vs baseline on `spread-1`
