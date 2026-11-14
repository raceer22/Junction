# SPEC — Junction / Spread dispatch bot (one-hour plan)

**Goal:** Make `python dispatch.py --level 8 --seed <fixed-seed>` end **Ranked**: at least one won order, `onTimeShare ≥ 90%`, `marginCents ≥ 0`.

**Scope:** Only `dispatch.py`. The `website/` visualizer is out of scope and untouched.

**Part split (kept from existing work):**
- **Part A — Quote & order selection:** `quote()`, `_should_bid()`, `_quote_price()`, dealer tracking. Decides which orders to win and at what price.
- **Part B — Dispatch & gig logistics:** `post()`, `fees_for()`, `retry()`, `raise_fees()`, `repost()`, `handle()`. Moves won orders on time at the lowest driver cost.

**Hand-off rule:** Part A never posts gigs; Part B never quotes prices. Each task touches one part only, so both can be verified independently.

---

## Prerequisites (before Task 1)

1. Get a real `SPREAD_KEY` from https://spread.bahnexpress.com/join and write it into `.env`. The current `.env` has `SPREAD_KEY=<your team key>` (placeholder) — runs cannot create worlds without it.
2. `SPREAD_URL=https://spread.bahnexpress.com` is already set.
3. Pick 2–3 fixed seeds and use the **same seeds for every task** so you can compare before/after. The world is deterministic: same seed + same actions = same week to the cent.

**Recommended fixed seeds:** `spread-1`, `study-1`, `race-1`
4. Baseline: run `python dispatch.py --level 8 --seed spread-1` and record `onTimeShare`, `marginCents`, and `Ranked` vs `Not ranked`.

---

## Tasks (vertical slices; each ends with a measured run)

### Task 1 — Baseline measurement (no code change)

**Goal:** Establish the starting point at level 8.

**Change:** None. Run the current `dispatch.py`.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Record the "End" line: `onTimeShare`, `marginCents`, won/undelivered counts, `Ranked` vs `Not ranked`.

**Success:** You have before-numbers for every metric. (Expect: not ranked, negative margin — the starter loses ~€72k at L8 on `spread-1`.)

**Time:** 5 min

---

### Task 2 — Part A: Load cap, don't over-win (delivery-minimum guard)

**Goal:** At level 8 you can win more orders than drivers can move. Decline new bids when you already have too many undelivered orders, so you never fall under the 90% on-time minimum.

**Change in `dispatch.py`:**
- Add a level-based active-order cap (undelivered orders in `self.orders`), e.g. levels 7–8: ~20, lower levels: unlimited/high.
- In `_should_bid()` or `quote()`, skip the option when `len(undelivered orders) >= cap`.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check `wonOrders` is lower than baseline and `onTimeShare` improves.

**Success:** `wonOrders` drops meaningfully; `onTimeShare` moves toward ≥90%.

**Time:** 7 min

---

### Task 3 — Part B: Higher opening fees at level 8 (get drivers)

**Goal:** At shortage levels the typical fee often finds no driver. Start gigs at a higher multiple of `referenceFeeCents` so drivers actually book.

**Change in `dispatch.py`:**
- In `fees_for()` / `rising()` / `steep()`, add a level-based opening-fee multiplier: at levels 7–8 open at ~1.5×–2× `referenceFeeCents` instead of 1×. Keep fees non-decreasing across steps.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check fewer `gig_expired` events and more `gig_booked` (inspect local reveal CSVs with `check_delivery.py`, or watch the event stream).

**Success:** More gigs booked at the opening fee; fewer gigs expire unbooked.

**Time:** 7 min

---

### Task 4 — Part B: Direct offers on `deadline_close` (rescue stranded orders)

**Goal:** At level 8 fees alone may not find a driver in time. When `deadline_close` fires (no driver 3h before `lastPickupAt`), send direct offers to nearby drivers from `world.roster()`.

**Change in `dispatch.py`:**
- In `handle()` for `deadline_close` (or in `raise_fees()`), after repricing, call `world.roster()`, filter drivers near the pickup place (use `_road_minutes` or straight-line distance), and append `{"type": "offer", "gigId": ..., "driverId": ..., "feeCents": <steep fee>}` to the outbox (max 3 open offers per gig, 3 per driver per day).
- Handle `offer_answered`: on `accepted` mark the gig booked; on `declined`/`expired`, offer to another driver or let the app find one.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check fewer undelivered orders after `deadline_close` events.

**Success:** Orders that get a `deadline_close` warning are more likely to be delivered on time.

**Time:** 10 min (most involved task)

---

### Task 5 — Part A: Aggressive dealer trust pricing (margin)

**Goal:** At level 8 each on-time delivery raises accepted prices by 1% (cap 25). The starter takes only half the trust (`trust / 200`). Take more of it to compound margin on trusted dealers.

**Change in `dispatch.py`:**
- In `_quote_price()`, change `price = int(price * (1 + trust / 200) + 0.5)` to `price = int(price * (1 + trust / 100) + 0.5)` (full 1% per on-time order, still capped at 25% by `min(trust, 25)`).

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check `marginCents` improves vs Task 4, especially on repeat dealers.

**Success:** Margin increases; win rate on trusted dealers holds up.

**Time:** 5 min

---

### Task 6 — Part A: Prefer later delivery options (less driver pressure)

**Goal:** `same_day` is the hardest to staff at level 8. Prefer `next_day` / `within_3_days` where slack allows, and price `same_day` higher so you probably don't win it unless it's safe.

**Change in `dispatch.py`:**
- In `quote()`, sort `request["options"]` by deadline (latest first). Only bid on the earliest option if a later one isn't viable, or quote `same_day` at a higher markup (e.g. list × 1.2) so you win it less often.
- Keep `_should_bid` for safety; only change which options get prices and at what markup.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check more wins are on `next_day`/`within_3_days`; fewer on `same_day`.

**Success:** Shift in option mix toward less pressured deadlines; on-time share improves.

**Time:** 7 min

---

### Task 7 — Part B: Better failed-visit recovery (reduce wasted trips)

**Goal:** Failed visits cost ≥€25 each and can strand a car. Improve `retry()` to act on the note: `withPlates` for plate notes, `manualLicence` for gearbox notes, `pickup_from` delayed for not-ready/closed, and `withCharging` for low-battery electric cars.

**Change in `dispatch.py`:**
- In `retry()`, parse the request note (reuse `window_from_note` / keyword logic) to set `Retry(plates=..., manual_licence=..., pickup_from=...)`.
- Confirm `stops_for()` sets `withCharging` when `car["electric"]` and `chargePercent < 30`, including on retries.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
```
Check fewer `visit_failed` events (reasons `not_ready`, `closed`, `licence`, `no_plates`).

**Success:** Failed-visit count drops; fewer repeated retries on the same leg.

**Time:** 7 min

---

### Task 8 — Final verification on multiple seeds

**Goal:** Confirm the solution is ranked and profitable at level 8 across seeds, not just one lucky world.

**Change:** None. Run on 2–3 fixed seeds.

**Verify:**
```sh
python dispatch.py --level 8 --seed spread-1
python dispatch.py --level 8 --seed study-1
python dispatch.py --level 8 --seed race-1
```
For each: confirm `onTimeShare ≥ 90%`, `marginCents ≥ 0`, and status is `Ranked`.

**Success:** All seeds meet the delivery minimum with non-negative margin. If any seed fails, note which task to iterate (return to Tasks 2–7) — that's your buffer.

**Time:** 10 min

---

## Time budget

| # | Task | Part | Time | Cumulative |
|---|------|------|------|------------|
| 1 | Baseline | — | 5 min | 5 |
| 2 | Load cap | A | 7 min | 12 |
| 3 | Higher opening fees | B | 7 min | 19 |
| 4 | Direct offers on deadline_close | B | 10 min | 29 |
| 5 | Aggressive trust pricing | A | 5 min | 34 |
| 6 | Prefer later options | A | 7 min | 41 |
| 7 | Better failed-visit recovery | B | 7 min | 48 |
| 8 | Multi-seed verification | — | 10 min | 58 |

Buffer: ~2 min. If a task runs long, drop Task 6 (option selection) first — it's the least critical for level 8.

Each task ends with a measurable run, so you can stop after any task and know exactly where you stand.

---

## Definition of done (one hour)

A level-8 practice run ends **Ranked**: `onTimeShare ≥ 90%` and `marginCents ≥ 0` on at least your primary seed, ideally on 2–3 seeds.

---

## Notes on what to measure

- Use the same fixed seeds across all tasks for comparability.
- `check_delivery.py` (in `analysing/`) reads local `events.csv` / `requests.csv` / `gigs.csv` from a reveal export — no live server needed — to inspect won/delivered counts and event-kind histograms (`visit_failed`, `deadline_close`, `gig_expired`, etc.).
- After a practice world ends, `spread-reveal-csv <world id>` exports the CSVs for analysis (requires a real key / ended world).
- Level 8 adds: uneven driver shortage by town, competitor ~26–54% above list price, an unannounced price-war day (competitor 20% under usual prices 08:00–18:00), and dealer trust (on-time delivery → accepted prices +1% each, cap 25).
