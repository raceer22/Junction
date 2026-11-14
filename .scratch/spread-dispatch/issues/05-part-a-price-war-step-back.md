# 05: Part A — step back during suspected price war

**What to build:** Detect a sudden quote win-rate collapse (suspected price-war day, per D7) and raise the multiplier / decline less aggressively to avoid below-cost wins that day, then resume normal tuning the next day. Built on the win-rate running mean from ticket 03. Part A only.

**Blocked by:** 03 — Part A single adaptive quote multiplier vs list price.

**Status:** ready-for-agent

- [ ] A sudden drop in quote win rate flags a suspected price-war day
- [ ] On a price-war day the multiplier rises / the system steps back to avoid below-cost wins
- [ ] Running `spread-1` shows margin protected on the price-war day and normal tuning resumes the next day
