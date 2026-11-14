"""Analyze actual driver fees vs reference fee per level."""
import csv
import json
from collections import defaultdict
from pathlib import Path

BASES = [
    Path("practice_level_1"),
    Path("practice_level_5"),
    Path("practice_level_8"),
]

for base in BASES:
    print(f"\n{'='*70}")
    print(f"LEVEL: {base.name}")
    print(f"{'='*70}")

    # Load orders: order id -> referenceFeeCents, option, priceCents
    requests = {}
    with open(base / "requests.csv") as f:
        for row in csv.DictReader(f):
            requests[row["id"]] = row

    # Load orders won: order id -> priceCents, option
    orders = {}
    with open(base / "events.csv") as f:
        for row in csv.DictReader(f):
            if row["kind"] == "order_won":
                oid = row["order.id"]
                orders[oid] = {
                    "priceCents": int(row["order.priceCents"]),
                    "option": row["order.option"],
                    "referenceFeeCents": int(requests[oid]["referenceFeeCents"]) if oid in requests else 0,
                }

    # Load gigs: match to order, sum done fees
    order_cost = defaultdict(int)
    order_done_gigs = defaultdict(int)
    order_withdrawn_gigs = defaultdict(int)
    all_fee_ratios = []
    with open(base / "gigs.csv") as f:
        for row in csv.DictReader(f):
            oid = row["orders"] if row["orders"] else None
            if not oid:
                try:
                    stops = json.loads(row["stops"])
                    if stops:
                        oid = stops[0].get("orderId")
                except Exception:
                    oid = None
            if oid and oid in orders:
                fee = int(row["feeCents"])
                ref = orders[oid]["referenceFeeCents"]
                status = row["status"]
                if status == "done":
                    order_cost[oid] += fee
                    order_done_gigs[oid] += 1
                    if ref > 0:
                        all_fee_ratios.append(fee / ref)
                elif status == "withdrawn":
                    order_withdrawn_gigs[oid] += 1

    # Fee ratio stats
    all_fee_ratios.sort()
    n = len(all_fee_ratios)
    if n:
        p50 = all_fee_ratios[n // 2]
        p90 = all_fee_ratios[int(n * 0.9)]
        p95 = all_fee_ratios[int(n * 0.95)]
        maxr = all_fee_ratios[-1]
        print(f"Done gigs: {n}")
        print(f"Fee/referenceFee ratio: p50={p50:.2f}  p90={p90:.2f}  p95={p95:.2f}  max={maxr:.2f}")
        print(f"  ratio < 1.0: {sum(1 for r in all_fee_ratios if r < 1.0)}")
        print(f"  ratio 1.0-1.2: {sum(1 for r in all_fee_ratios if 1.0 <= r < 1.2)}")
        print(f"  ratio 1.2-1.5: {sum(1 for r in all_fee_ratios if 1.2 <= r < 1.5)}")
        print(f"  ratio 1.5-2.0: {sum(1 for r in all_fee_ratios if 1.5 <= r < 2.0)}")
        print(f"  ratio >= 2.0: {sum(1 for r in all_fee_ratios if r >= 2.0)}")

    # Withdrawn gigs
    total_withdrawn = sum(order_withdrawn_gigs.values())
    total_done = sum(order_done_gigs.values())
    print(f"Total done gigs: {total_done}, withdrawn: {total_withdrawn}")
    orders_with_withdrawn = sum(1 for v in order_withdrawn_gigs.values() if v > 0)
    print(f"Orders with at least one withdrawn gig: {orders_with_withdrawn}")

    # Margin: price vs actual cost (done gigs only)
    print(f"\n--- Margin: quoted price vs actual done-gig cost ---")
    total_rev = 0
    total_cost = 0
    neg = 0
    pos = 0
    for oid, o in orders.items():
        rev = o["priceCents"]
        cost = order_cost.get(oid, 0)
        total_rev += rev
        total_cost += cost
        if cost > rev:
            neg += 1
        elif cost < rev:
            pos += 1
    print(f"Revenue: €{total_rev/100:,.2f}  Cost (done gigs): €{total_cost/100:,.2f}  Margin: €{(total_rev-total_cost)/100:,.2f}")
    print(f"Orders where cost > price: {neg}/{len(orders)}  cost < price: {pos}/{len(orders)}")

    # By option: margin
    print(f"\n--- By option: cost vs price ---")
    by_opt = defaultdict(lambda: [0, 0, 0])  # [rev, cost, count]
    for oid, o in orders.items():
        opt = o["option"]
        by_opt[opt][0] += o["priceCents"]
        by_opt[opt][1] += order_cost.get(oid, 0)
        by_opt[opt][2] += 1
    for opt, (rev, cost, cnt) in sorted(by_opt.items()):
        print(f"  {opt:>12}: count={cnt:>4}  revenue €{rev/100:>10,.2f}  cost €{cost/100:>10,.2f}  margin €{(rev-cost)/100:>10,.2f}  avg cost/price ratio {cost/rev:.2f}")
