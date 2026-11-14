# 06: Part B — opening-fee multiple driven by gig expiry vs quick booking

**What to build:** The opening-fee multiple rises on unbooked gig expiry and falls on quick booking (fading running mean), so gigs book at the opening fee more often and the average driver fee falls. The existing `deadline_close` steep raise and risky-note skip stay unchanged (per D5). Part B only — never quotes prices or touches `quote()`.

**Blocked by:** 01 — Baseline reference run on seed `spread-1`.

**Status:** ready-for-agent

- [ ] Opening-fee multiple rises on gig expiry and falls on quick booking via a fading running mean
- [ ] `deadline_close` steep raise and risky-note skip are unchanged
- [ ] Running `spread-1` shows more gigs booked at opening fee, lower average driver fee, and on-time share holding ≥ 90% headroom
