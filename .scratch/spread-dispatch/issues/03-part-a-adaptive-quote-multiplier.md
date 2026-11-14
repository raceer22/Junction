# 03: Part A — single adaptive quote multiplier vs list price (bias up, avoid declining offers)

**What to build:** One adaptive quote multiplier applied to the list price, tuned per day with a fading running mean of quote win/loss. When the win rate is high the multiplier nudges up to capture more margin; when it is low the system keeps bidding rather than declining offers, because declining hurts dealer relationships — the multiplier rises (is held up) instead of walking away. Quotes equal list price × multiplier, always whole cents. Part A only.

**Blocked by:** 01 — Baseline reference run on seed `spread-1`.

**Status:** ready-for-agent

- [ ] `quote()` applies a single per-dealer/day multiplier to list price instead of quoting list price flat
- [ ] The multiplier nudges up when the win rate is high and does not decline offers when the win rate is low (no decline path from this lever)
- [ ] Running `spread-1` shows the win rate stabilizes and offers are not declined because of this lever, preserving dealer relationships
- [ ] Quotes remain whole cents and at or above list price when the multiplier is ≥ 1
