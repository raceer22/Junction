"""Analyze requests.csv across levels to inform Part A quoting strategy."""
import csv
import json
import re
from collections import defaultdict, Counter
from pathlib import Path

HOUR, DAY = 60, 24 * 60

BASES = [
    Path("practice_level_1"),
    Path("practice_level_5"),
    Path("practice_level_8"),
]

RISKY_KEYWORDS = [
    "manual", "manuaalinen", "välijarru", "käsivaihteisto", "plates", "rekisterikilvet",
    "kilvet", "private", "yksityinen", "private seller", "closing", "suljettu", "closing hours",
    "tighter", "kiire", "soita", "call before", "ennen kuin", "booking ref",
]

OPTION_KINDS = ["same_day", "next_day", "within_3_days"]


def load_requests(base):
    rows = []
    with open(base / "requests.csv") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def load_events(base):
    rows = []
    with open(base / "events.csv") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def analyze_level(base):
    print(f"\n{'='*70}")
    print(f"LEVEL: {base.name}")
    print(f"{'='*70}")

    requests = load_requests(base)
    events = load_events(base)

    print(f"Total requests: {len(requests)}")

    # Outcomes
    outcomes = Counter(r["outcome"] for r in requests)
    print(f"Outcomes: {dict(outcomes)}")

    # Options present
    opt_presence = Counter()
    for r in requests:
        for k in OPTION_KINDS:
            if r.get(f"{k}.listPriceCents", "").strip():
                opt_presence[k] += 1
    print(f"Options present (count of requests offering it): {dict(opt_presence)}")

    # Won requests detail
    won = [r for r in requests if r["outcome"] == "won"]
    lost = [r for r in requests if r["outcome"] == "lost"]
    print(f"Won: {len(won)}, Lost: {len(lost)}")

    # referenceFee vs listPrice for won orders (structural profitability)
    print("\n--- Won orders: listPrice vs referenceFee (per winning option) ---")
    structural_loss = 0
    structural_ok = 0
    total_list = 0
    total_ref = 0
    by_option = defaultdict(lambda: [0, 0, 0, 0])  # count, sum(list-ref), sum(loss), won_count
    for r in won:
        opt = r["option"]
        ref = int(r["referenceFeeCents"])
        listp = int(r.get(f"{opt}.listPriceCents", "0") or 0)
        diff = listp - ref
        total_list += listp
        total_ref += ref
        if diff < 0:
            structural_loss += 1
        else:
            structural_ok += 1
        by_option[opt][0] += 1
        by_option[opt][1] += diff
        if diff < 0:
            by_option[opt][2] += diff
        by_option[opt][3] += 1

    print(f"  Won where listPrice < refFee (structural loss at list): {structural_loss}")
    print(f"  Won where listPrice >= refFee: {structural_ok}")
    print(f"  Sum listPrice: €{total_list/100:,.2f}  Sum refFee: €{total_ref/100:,.2f}  Implied: €{(total_list-total_ref)/100:,.2f}")
    for opt, (c, sd, loss, wc) in sorted(by_option.items()):
        print(f"    {opt:>12}: won={wc}  sum(list-refFee) €{sd/100:>10,.2f}  total loss €{loss/100:>10,.2f}")

    # Quoted price vs listPrice for won
    print("\n--- Won orders: quoteCents vs listPriceCents (what we actually quoted) ---")
    quote_below_list = 0
    quote_at_list = 0
    quote_above_list = 0
    for r in won:
        opt = r["option"]
        listp = int(r.get(f"{opt}.listPriceCents", "0") or 0)
        q = int(r.get(f"{opt}.quoteCents", "0") or 0)
        if q < listp:
            quote_below_list += 1
        elif q == listp:
            quote_at_list += 1
        else:
            quote_above_list += 1
    print(f"  quote < list: {quote_below_list}, quote == list: {quote_at_list}, quote > list: {quote_above_list}")

    # Slack analysis: deadline - arrivedAt (respondBy is the quote deadline)
    print("\n--- Slack: respondBy - arrivedAt (minutes we have to decide) ---")
    slacks = []
    for r in requests:
        try:
            arr = int(r["arrivedAt"]) if r["arrivedAt"].strip() else 0
            resp = int(r["respondBy"])
            slacks.append(resp - arr)
        except ValueError:
            pass
    if slacks:
        print(f"  min={min(slacks)}  max={max(slacks)}  avg={sum(slacks)/len(slacks):.0f}")
        print(f"  slack < 60min: {sum(1 for s in slacks if s < 60)}")
        print(f"  slack < 120min: {sum(1 for s in slacks if s < 120)}")
        print(f"  slack < 360min (6h): {sum(1 for s in slacks if s < 360)}")

    # Option deadline vs now (slack to deliver)
    print("\n--- Option deadline slack (deadline - respondBy) per option ---")
    for k in OPTION_KINDS:
        dls = []
        for r in requests:
            lp = r.get(f"{k}.listPriceCents", "").strip()
            dl = r.get(f"{k}.deadline", "").strip()
            if lp and dl:
                try:
                    resp = int(r["respondBy"])
                    dls.append(int(dl) - resp)
                except ValueError:
                    pass
        if dls:
            print(f"  {k:>12}: count={len(dls)}  min={min(dls)}  max={max(dls)}  avg={sum(dls)/len(dls):.0f}  <6h={sum(1 for d in dls if d < 360)}  <12h={sum(1 for d in dls if d < 720)}")

    # Notes analysis
    print("\n--- Notes: risky keywords ---")
    risky_count = 0
    keyword_hits = Counter()
    for r in requests:
        note = (r.get("note") or "").lower()
        if not note:
            continue
        hits = [kw for kw in RISKY_KEYWORDS if kw in note]
        if hits:
            risky_count += 1
            for h in hits:
                keyword_hits[h] += 1
    print(f"  Requests with notes: {sum(1 for r in requests if r.get('note','').strip())}")
    print(f"  Requests with risky keyword: {risky_count}")
    for kw, c in keyword_hits.most_common(15):
        print(f"    '{kw}': {c}")

    # Language distribution
    langs = Counter(r.get("language", "") for r in requests)
    print(f"\n--- Languages: {dict(langs)} ---")

    # Dealer distribution
    dealers = Counter(r["dealerId"] for r in requests)
    print(f"\n--- Dealers: {len(dealers)} unique, top: {dealers.most_common(5)} ---")

    # Legs distribution
    leg_counts = Counter(int(r["legs"]) for r in requests if r["legs"].strip())
    print(f"--- Legs per request: {dict(leg_counts)} ---")

    # same_day profitability check
    print("\n--- same_day: listPrice vs refFee for all requests offering it ---")
    sd_loss = 0
    sd_ok = 0
    for r in requests:
        if r.get("same_day.listPriceCents", "").strip():
            ref = int(r["referenceFeeCents"])
            lp = int(r["same_day.listPriceCents"])
            if lp < ref:
                sd_loss += 1
            else:
                sd_ok += 1
    print(f"  same_day listPrice < refFee: {sd_loss}, >= : {sd_ok}")

    return requests, events


def main():
    for base in BASES:
        analyze_level(base)


if __name__ == "__main__":
    main()
