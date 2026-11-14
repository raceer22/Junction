# 04: Part A — dealer trust bump at level 8 (+1% per on-time order, cap 25%)

**What to build:** Each fully delivered on-time order raises that dealer's accepted-price bump by +1%, capped at 25%, applied only at level 8 in the quote price (per D7). Track per-dealer on-time outcomes from `delivered` and apply the bump to future quotes to that dealer. Part A only.

**Blocked by:** 01 — Baseline reference run on seed `spread-1`.

**Status:** ready-for-agent

- [ ] Per-dealer on-time outcome is recorded when an order's last leg delivers (on time vs late)
- [ ] At level 8, each on-time order adds +1% to that dealer's accepted-price bump, capped at 25%
- [ ] Running `spread-1` shows accepted prices rising for reliable dealers with improved margin
