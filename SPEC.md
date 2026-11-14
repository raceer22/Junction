# Spec: dynamic market-aware dispatch for Spread

**Context:** Hackathon solution for Spread (car-dispatch simulator). Current starter hardcodes per-level fee tables (`OPENING_FEE`, `MAX_FEE_MULTIPLE`, quote multipliers, buffers) and reads only the level number. The final is level 8: ~1/3 of the drivers of an average world, uneven town-by-town, competitor prices 26–54% above list, an announced price-war day, outage/flu/bulk order every week, and dealer trust.

## Decisions (shared understanding)

| # | Decision | Agreement |
|---|---|---|
| D1 | Scope | Adapt both Part A (quote / order selection) and Part B (dispatch / gig fees), coordinated through shared `Desk` state |
| D2 | Objective | Maximize gross margin; ≥90% on-time is a hard constraint with headroom (not 100% at any cost) |
| D3 | Validation | Baseline first, then A/B every change on identical seeds; keep only changes that improve margin while holding ≥90% on-time |
| D4 | Signals | Views + fee seen per gig, booking conversion, drops, gig expiry, quote win/loss by lane & day, dealer trust, roster driver counts per town |
| D5 | Levers (minimal, traceable) | **Part A:** slack-based no-bid + single quote multiplier vs list price. **Part B:** opening-fee multiple (rise on unbooked expiry, fall on quick booking). Keep existing `deadline_close` steep raise and risky-note skip unchanged |
| D6 | Pace | Per-day re-tune with a fading running mean; immediate event-driven reactions on `deadline_close`, `gig_dropped`, `order_lost` streaks |
| D7 | Level-8 mechanics | Trust-based quote bump (each on-time order +1% accepted price, cap 25%), step back during the price-war day, route/post around driver-poor towns |

## Design tree (summary)

- Adapt both parts? → Yes (D1)
  - Objective → Margin subject to ≥90% on-time (D2)
  - Validation → Baseline + identical-seed A/B (D3)
    - Signals → views/conversion/drops/expiry/win-loss/trust/roster (D4)
    - Levers → minimal set: slack no-bid, quote multiplier, opening-fee multiple, existing deadline_close + risky-note skip (D5)
    - Pace → per-day fading mean + immediate reactions (D6)
    - Level-8 → trust bump, price-war retreat, driver-poor-town routing (D7)

## Implementation plan (priority order)

1. **Baseline** — run current starter at level 8 on seed `spread-1` (known reference: −€72.6k margin, 45.5% on-time, unranked). Runs ~95 s at level 8.
2. **Part A** — slack-based no-bid (skip when pickup unreachable before deadline), single adaptive quote multiplier vs list price (nudge up when win rate high, down when low), dealer trust bump at level 8, step back during suspected price war.
3. **Part B** — opening-fee multiple driven by gig expiry vs quick booking; keep `deadline_close` steep raise and risky-note skip unchanged.
4. **A/B test** — baseline vs variant on identical seeds (`spread-1`, `study-1`, plus random seeds); keep changes that improve margin at ≥90% on-time.

## Non-goals (explicitly out of scope)

- Driving every fee knob (rising/steep curve shapes, caps, buffers) — kept simple for traceability.
- Part B posting gigs / Part A quoting prices (split boundary enforced per `notes/QUOTE_DISPATCH_SPLIT.md`).
- Reading competitor prices directly (not observable; learned only from win/loss).
