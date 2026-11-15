import csv
import json
from collections import defaultdict

BASE = "practice_level_5/"

# --- Parse requests.csv ---
# We need: request id -> referenceFeeCents, and for the winning option: listPriceCents, quoteCents
requests = {}
with open(BASE + "requests.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rid = row["id"]
        requests[rid] = {
            "referenceFeeCents": int(row["referenceFeeCents"]),
            "outcome": row["outcome"],
            "option": row["option"],
            "next_day.listPriceCents": row.get("next_day.listPriceCents", ""),
            "next_day.quoteCents": row.get("next_day.quoteCents", ""),
            "same_day.listPriceCents": row.get("same_day.listPriceCents", ""),
            "same_day.quoteCents": row.get("same_day.quoteCents", ""),
        }

# The columns for each option: <option>_deadline, <option>_listPriceCents, <option>_quoteCents
# We need to know which option was won. The outcome is "won" for all, and order.priceCents in events tells us.

# --- Parse events.csv: order_won gives order.priceCents (revenue) ---
orders = {}  # order id -> {priceCents, option, deadline, status, legs}
order_won_events = []
with open(BASE + "events.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        kind = row["kind"]
        if kind == "order_won":
            oid = row["order.id"]
            orders[oid] = {
                "priceCents": int(row["order.priceCents"]),
                "option": row["order.option"],
                "deadline": int(row["order.deadline"]),
                "legs": len(json.loads(row["order.legs"])) if row["order.legs"] else 1,
                "status": row["order.status"],
                "dealerId": row["order.dealerId"],
            }
            order_won_events.append(row)

print(f"=== Orders won: {len(orders)} ===")

# --- Parse gigs.csv: feeCents and status per gig, matched to order via stops or orders column ---
# The "orders" column gives the order id. The "stops" JSON has orderId per stop.
gigs_by_order = defaultdict(list)
gig_count = 0
done_gigs = 0
withdrawn_gigs = 0
other_gigs = 0
with open(BASE + "gigs.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        gig_count += 1
        gid = row["id"]
        status = row["status"]
        fee = int(row["feeCents"])
        # extract order id from orders column or stops JSON
        oid = row["orders"] if row["orders"] else None
        if not oid:
            try:
                stops = json.loads(row["stops"])
                if stops:
                    oid = stops[0].get("orderId")
            except Exception:
                oid = None
        if oid:
            gigs_by_order[oid].append({"gigId": gid, "feeCents": fee, "status": status})
        if status == "done":
            done_gigs += 1
        elif status == "withdrawn":
            withdrawn_gigs += 1
        else:
            other_gigs += 1

print(f"=== Total gigs: {gig_count} (done={done_gigs}, withdrawn={withdrawn_gigs}, other={other_gigs}) ===")
print(f"=== Orders with gigs: {len(gigs_by_order)} ===")

# --- Compute margin per order ---
# Revenue = order.priceCents (dealer pays this when order is won)
# Cost = sum of feeCents for "done" gigs carrying this order
total_revenue = 0
total_cost = 0
orders_with_cost = 0
positive_margin_orders = 0
negative_margin_orders = 0
zero_margin_orders = 0
margin_details = []  # (orderId, revenue, cost, margin)

for oid, order in orders.items():
    revenue = order["priceCents"]
    total_revenue += revenue
    gigs = gigs_by_order.get(oid, [])
    cost = sum(g["feeCents"] for g in gigs if g["status"] == "done")
    if cost > 0:
        orders_with_cost += 1
    total_cost += cost
    margin = revenue - cost
    margin_details.append((oid, revenue, cost, margin, order["option"]))
    if margin > 0:
        positive_margin_orders += 1
    elif margin < 0:
        negative_margin_orders += 1
    else:
        zero_margin_orders += 1

total_margin = total_revenue - total_cost
margin_pct = (total_margin / total_revenue * 100) if total_revenue else 0

print(f"\n=== MARGIN COMPUTATION ===")
print(f"Total revenue (dealer payments):   €{total_revenue/100:>12,.2f}")
print(f"Total driver cost (fees paid):     €{total_cost/100:>12,.2f}")
print(f"Gross margin:                      €{total_margin/100:>12,.2f}")
print(f"Margin as % of revenue:            {margin_pct:>11.1f}%")
print(f"Orders with at least one done gig: {orders_with_cost}")
print(f"Positive margin orders:            {positive_margin_orders}")
print(f"Negative margin orders:            {negative_margin_orders}")
print(f"Zero margin orders:                {zero_margin_orders}")

# Show the biggest losers and winners
print(f"\n=== WORST MARGIN ORDERS (losses) ===")
losers = sorted([d for d in margin_details if d[3] < 0], key=lambda x: x[3])[:15]
for oid, rev, cost, marg, opt in losers:
    print(f"  {oid}: revenue €{rev/100:>8,.2f}  cost €{cost/100:>8,.2f}  margin €{marg/100:>8,.2f}  ({opt})")

print(f"\n=== BEST MARGIN ORDERS ===")
winners = sorted([d for d in margin_details if d[3] > 0], key=lambda x: -x[3])[:15]
for oid, rev, cost, marg, opt in winners:
    print(f"  {oid}: revenue €{rev/100:>8,.2f}  cost €{cost/100:>8,.2f}  margin €{marg/100:>8,.2f}  ({opt})")

# --- Structural analysis: listPriceCents vs referenceFeeCents ---
print(f"\n=== STRUCTURAL: listPriceCents vs referenceFeeCents (per request) ===")
# Match each won order to its request to compare listPrice vs referenceFee
list_price_total = 0
ref_fee_total = 0
list_lt_ref = 0  # list price less than reference fee => structural loss
list_ge_ref = 0
for oid, order in orders.items():
    rid = oid  # request id == order id
    req = requests.get(rid)
    if not req:
        continue
    # The winning option's list price
    opt = order["option"]
    list_price = int(req.get(f"{opt}.listPriceCents", 0)) or 0
    if list_price == 0:
        # try quoteCents
        list_price = int(req.get(f"{opt}.quoteCents", 0)) or 0
    ref_fee = req["referenceFeeCents"]
    list_price_total += list_price
    ref_fee_total += ref_fee
    if list_price < ref_fee:
        list_lt_ref += 1
    else:
        list_ge_ref += 1

print(f"Sum of winning-option list prices:   €{list_price_total/100:>12,.2f}")
print(f"Sum of reference fees:               €{ref_fee_total/100:>12,.2f}")
print(f"Implied margin (list - ref fee):     €{(list_price_total - ref_fee_total)/100:>12,.2f}")
print(f"Orders where listPrice < refFee:     {list_lt_ref} (structural loss if quoted at list)")
print(f"Orders where listPrice >= refFee:    {list_ge_ref}")

# Count orders where quote (listPrice) < referenceFee by option
print(f"\n=== listPrice < referenceFee by option ===")
by_opt = defaultdict(lambda: [0, 0, 0])  # [count, sum(list-ref), sum of losses]
for oid, order in orders.items():
    rid = oid
    req = requests.get(rid)
    if not req:
        continue
    opt = order["option"]
    list_price_str = req.get(f"{opt}.listPriceCents", "")
    list_price = int(list_price_str) if list_price_str else 0
    if list_price == 0:
        q_str = req.get(f"{opt}.quoteCents", "")
        list_price = int(q_str) if q_str else 0
    ref_fee = req["referenceFeeCents"]
    diff = list_price - ref_fee
    by_opt[opt][0] += 1
    by_opt[opt][1] += diff
    if diff < 0:
        by_opt[opt][2] += diff

for opt, (cnt, sumdiff, loss) in sorted(by_opt.items()):
    print(f"  {opt:>10}: orders={cnt:>4}  sum(list-refFee) €{sumdiff/100:>10,.2f}  total loss €{loss/100:>10,.2f}")
