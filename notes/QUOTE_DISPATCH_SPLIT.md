# Work split: Quote vs Dispatch

**Team:** raceer + Veolzu  
**Project:** Spread dispatcher (`dispatch.py`)  
**Goal:** Reach 100% on-time delivery while maximising gross margin.

---

## 1. The split principle

We divide the solution into two independent parts that touch mostly different functions in `dispatch.py`. This lets both team members work in parallel with minimal merge conflicts.

| | Part A — Quote & Order Selection | Part B — Dispatch & Gig Logistics |
|---|---|---|
| **Owner** | One person | One person |
| **Goal** | Win **only** orders we can profitably deliver on time | Move every won order on time at the lowest driver cost |
| **Core question** | "Should we bid on this request, and at what price?" | "We won this order — how do we get a driver to deliver it by deadline?" |
| **Entry point** | `price_request` → `quote()` | `order_won` → `post()` / gig lifecycle |
| **Exit point** | `order_won` / `order_lost` | `delivered` / `undelivered` / world end |
| **Key metric** | Win rate, margin per won order, quote-to-win ratio | On-time share, driver fee per order, failed visits, undelivered |

**Hand-off:** Part A decides which orders enter `self.orders` via `quote()`. Part B takes over from `order_won` onward through delivery. Part A never posts gigs; Part B never quotes prices.

---

## 2. Part A — Quote & Order Selection

### Files to edit
- `dispatch.py`: `quote()`, plus any new helper methods and state on `Desk`.
- Optional: a new `notes/quote_strategy.md` for pricing rules.

### Current code being replaced (`dispatch.py` lines 103–106)
```python
def quote(self, request: PriceRequest) -> None:
    self.requests[request["id"]] = request
    first = request["options"][0]
    self.outbox.append({"type": "quote", "requestId": request["id"], "prices": {first["kind"]: first["listPriceCents"]}})
```

### Responsibilities
1. **Bid / no-bid filter.** Decide whether to quote at all. Decline outright (return without quoting) when:
   - The deadline gives too little slack from `request["arrivedAt"]` / `self.now`.
   - The note contains risky keywords (manual gearbox, plates, private seller slot, closing hours, tighter deadline).
   - `referenceFeeCents` is so low that drivers likely won't take the gig.
   - The dealer's recent trust/caution score says to skip.

2. **Option selection.** `request["options"]` is a list of `DeliveryOption` dicts: `{kind, deadline, listPriceCents}`. Choose which kinds to bid on (`same_day`, `next_day`, `within_3_days`). Prefer `next_day` / `within_3_days` over `same_day` where slack allows.

3. **Per-option pricing.** Quote different markups per option kind. Quote high on marginal options so you probably don't win them; quote competitively on safe options. Values must be whole cents.

4. **Dealer memory.** Track per-dealer outcomes (won / lost / late) and adjust future quotes accordingly (relevant at level 8 dealer trust).

### What Part A must NOT do
- Do not post gigs.
- Do not modify `post()`, `fees_for()`, `retry()`, or `raise_fees()`.
- Do not add handlers for `gig_booked`, `visit_failed`, etc. unless agreed with Part B.

### Success metrics for Part A
- Win rate on quoted requests.
- Average margin per won order.
- % of won orders that were "safe" (adequate deadline slack, no risky notes).
- Avoid winning more orders than drivers can physically move in shortage levels (7–8).

---

## 3. Part B — Dispatch & Gig Logistics

### Files to edit
- `dispatch.py`: `post()`, `fees_for()`, `retry()`, `retry_from()`, `raise_fees()`, `repost()`, `handle()`, plus any new helper methods and state on `Desk`.
- Optional: `notes/dispatch_strategy.md` for fee-schedule rules.

### Responsibilities
1. **Immediate gig posting.** After `order_won`, post a gig at once (`post()`). Set correct `audience` (`experienced` for premium cars, `manualLicence` when required), `withPlates` / `withCharging` from car data, and sensible stop windows.

2. **Fee schedule design.** `fees_for()` builds the opening fee and curve. Tune `rising()` (pre-deadline warning) and `steep()` (post-`deadline_close`) so fees attract drivers without overpaying. Level 1 uses one fee step; higher levels can use up to 48 steps.

3. **Trade-in splitting.** Detect >12-hour multi-car drives and post as separate gigs (starter attempts this at lines 165–169; improve if needed).

4. **Failed-visit recovery.** On `visit_failed` (`not_ready`, `closed`, `licence`, `no_plates`), re-post the car with `retry()` using `withPlates`, `manualLicence`, or a delayed `pickup_from`.

5. **Deadline-close reaction.** On `deadline_close`, raise fees steeply (`raise_fees()`) and/or send direct offers (`offer()`) to nearby drivers from `world.roster()`.

6. **Event handling.** Extend `handle()` to react to events the starter currently ignores: `gig_booked`, `gig_dropped`, `driver_late`, `no_show`, `gig_expired`, `offer_answered`, etc. Add any needed event kinds to `WAKE_ON`.

### What Part B must NOT do
- Do not change `quote()` or the bid/no-bid logic unless agreed with Part A.
- Do not modify which options are quoted or the `prices` dict structure.

### Success metrics for Part B
- On-time share of delivered orders (must stay ≥ 90% to be ranked; target 100%).
- Failed visit count.
- Undelivered share.
- Average driver fee per order.

---

## 4. Interface between Part A and Part B

```
price_request  ──►  Part A: quote()  ──►  order_won  ──►  Part B: post() / gig lifecycle
```

- Part A writes to `self.requests` and `self.dealer_state` (or similar).
- Part B reads `self.orders`, `self.leg_gigs`, `self.booked`, `self.posts`.
- The server decides win/loss at `respondBy`; neither part controls that directly.
- Part A must not mark an order as won — only `order_won` from the server does that.
- Part B assumes every `order_won` is a delivery obligation it must fulfill.

### Shared state on `Desk` (agreed, do not delete)
- `self.requests` — populated by Part A in `quote()`.
- `self.orders` — populated by the server on `order_won`; read by Part B.
- `self.urgent` — set by Part B on `deadline_close`.

If either person adds new instance attributes in `Desk.__init__`, prefix them clearly:
- Part A: `self.dealer_caution`, `self.quote_stats`, etc.
- Part B: `self.dispatch_state`, `self.driver_cache`, etc.

---

## 5. Shared infrastructure (touch lightly, coordinate)

| Item | Who decides | Notes |
|------|-------------|-------|
| `WAKE_ON` (line 24) | Part B (mostly) | Add event kinds Part B needs to react to. Part A must be consulted before removing kinds. |
| `Desk.__init__` (line 38) | Both | Add your own state dicts; do not rename existing ones. |
| `Retry` class (line 27) | Part B | `pickup_from`, `manual_licence`, `plates` are used by `retry()`. |
| CLI args (`--level`, `--seed`) | Both | Used by both for testing on identical seeds. |
| `main()` (line 378) | Neither (shared) | Do not restructure without agreeing. |

---

## 6. Testing plan

1. **Same seeds for both.** Each person tests on identical seeds (e.g. `spread-1`, `study-1`) so you can compare before/after your own changes.
2. **Reveal CSVs.** After a run ends:
   ```sh
   spread-reveal-csv <world id>
   ```
   - `requests.csv`: quotes and outcomes (Part A analyzes win/loss by price).
   - `events.csv`: bookings, drops, failed visits, deliveries (Part B analyzes gig failures and lateness).
   - `legs.csv`: truth about each car (ready time, real hours, note meaning).
3. **Fork worlds.** `world.fork()` lets each person try a different tactic on the same future without burning extra practice slots.

---

## 7. First tasks (suggested)

### Part A — Week 1
1. Replace `quote()` so it reads **all** `request["options"]`, not just `options[0]`.
2. Add a bid/no-bid filter: skip `same_day` when deadline slack < 6 hours; skip notes with risky keywords.
3. Quote different prices per option kind (e.g. `same_day` at list price, `next_day` at list + markup).
4. Add simple per-dealer caution tracking updated from `order_won` / `order_lost`.

### Part B — Week 1
1. Improve `post()`: set `withPlates` / `withCharging` from car data; set `manualLicence` from note parsing.
2. Tune `fees_for()` opening fee and `rising()` / `steep()` curves so gigs actually get booked.
3. Handle `visit_failed`: re-post with `withPlates`, `manualLicence`, or delayed `pickup_from`.
4. Add `deadline_close` reaction: steep fee + optional direct offer via `offer()`.

---

## 8. Definition of done for the split

- [ ] Part A can run `python dispatch.py --level 2 --seed <seed>` and see sensible quotes in `requests.csv`.
- [ ] Part B can run the same world and see gigs posted, booked, and delivered in `events.csv`.
- [ ] Combined run shows `onTimeShare` trending toward 100% and `marginCents` positive or minimally negative.
- [ ] No edit conflicts in `dispatch.py` because each person owns disjoint functions.

---

## 9. Escalation

If a change touches both parts (for example, adding a new event kind to `WAKE_ON`, or changing `Desk.__init__`), stop and sync in person or via a GitHub issue before merging. The `TEAM_WORKFLOW.md` rule applies: before merging, ask the other person to take a quick look.
