import csv
import json
from collections import defaultdict

BASE = "practice_level_8/"

# --- Parse events.csv for delivered events ---
delivered_legs = defaultdict(set)  # orderId -> set of legs delivered
order_deadline = {}
order_price = {}
won_orders = set()

with open(BASE + "events.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        kind = row["kind"]
        if kind == "order_won":
            won_orders.add(row["order.id"])
            order_price[row["order.id"]] = int(row["order.priceCents"])
            try:
                order_deadline[row["order.id"]] = int(row["order.deadline"])
            except ValueError:
                order_deadline[row["order.id"]] = None
        elif kind == "delivered":
            oid = row["orderId"]
            leg = int(row["leg"])
            at = int(row["at"])
            delivered_legs[oid].add(leg)

print(f"=== Orders won: {len(won_orders)} ===")
print(f"=== Orders with at least one delivered leg: {len(delivered_legs)} ===")

# Fully delivered orders (all legs delivered)
fully_delivered = 0
partially_delivered = 0
not_delivered = 0
for oid in won_orders:
    legs = delivered_legs.get(oid, set())
    # We need to know total legs - get from requests or assume 1 mostly
    # For simplicity: check if any delivery happened
    if len(legs) > 0:
        fully_delivered += 1  # approximate
    else:
        not_delivered += 1

print(f"Orders with delivery activity: {fully_delivered}")
print(f"Orders with NO delivery: {not_delivered}")

# Count delivered events
delivered_events = 0
with open(BASE + "events.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row["kind"] == "delivered":
            delivered_events += 1
print(f"Total delivered leg events: {delivered_events}")

# Check order statuses from order_won (all waiting at win time, but final status unknown from events alone)
# The events.csv has order.status at win time. Let's check if there are order_changed or order_cancelled.
statuses = defaultdict(int)
with open(BASE + "events.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row["kind"] in ("order_won", "order_changed", "order_cancelled"):
            statuses[(row["kind"], row.get("order.status", ""))] += 1
print(f"\nOrder status events: {dict(statuses)}")

# Check for visit_failed, deadline_close, gig_expired
event_kinds = defaultdict(int)
with open(BASE + "events.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        event_kinds[row["kind"]] += 1
print(f"\nAll event kinds:")
for k, v in sorted(event_kinds.items()):
    print(f"  {k:>20}: {v}")
