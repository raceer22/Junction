"""Deeper analysis of quoting vs winning, per level."""
import csv
import json
from collections import defaultdict, Counter
from pathlib import Path

BASES = [
    Path("practice_level_1"),
    Path("practice_level_5"),
    Path("practice_level_8"),
]

OPTION_KINDS = ["same_day", "next_day", "within_3_days"]


def load_requests(base):
    with open(base / "requests.csv") as f:
        return list(csv.DictReader(f))


def analyze_level(base):
    print(f"\n{'='*75}")
    print(f"LEVEL: {base.name}")
    print(f"{'='*75}")
    requests = load_requests(base)

    won = [r for r in requests if r["outcome"] == "won"]
    lost = [r for r in requests if r["outcome"] == "lost"]
    unquoted = [r for r in requests if r["outcome"] == "unquoted"]
    print(f"won={len(won)} lost={len(lost)} unquoted={len(unquoted)}")

    # For won orders: which option did we quote, and was it the winning option?
    print("\n--- Won: was the winning option the one we quoted? (quoteCents>0 means we quoted it) ---")
    won_quoted_opt = 0
    won_not_quoted_opt = 0
    for r in won:
        opt = r["option"]
        q = r.get(f"{opt}.quoteCents", "").strip()
        if q:
            won_quoted_opt += 1
        else:
            won_not_quoted_opt += 1
    print(f"  winning option quoted: {won_quoted_opt}, winning option NOT quoted: {won_not_quoted_opt}")

    # For lost requests: what options did they have, and did we quote any? Which option did dealer take (with competitor)?
    print("\n--- Lost: options available vs what we quoted ---")
    lost_quoted_any = 0
    lost_quoted_none = 0
    lost_by_available_opt = Counter()
    for r in lost:
        avail = [k for k in OPTION_KINDS if r.get(f"{k}.listPriceCents", "").strip()]
        lost_by_available_opt[tuple(sorted(avail))] += 1
        quoted_any = any(r.get(f"{k}.quoteCents", "").strip() for k in avail)
        if quoted_any:
            lost_quoted_any += 1
        else:
            lost_quoted_none += 1
    print(f"  lost where we quoted at least one option: {lost_quoted_any}")
    print(f"  lost where we quoted NO option: {lost_quoted_none}")
    print(f"  lost by available-option combo (top 8):")
    for combo, c in lost_by_available_opt.most_common(8):
        print(f"    {combo}: {c}")

    # Profitability: listPrice vs referenceFee for ALL options (not just won)
    print("\n--- All options: listPrice vs referenceFee (structural) ---")
    all_loss = 0
    all_ok = 0
    by_opt_struct = defaultdict(lambda: [0, 0])  # [loss_count, ok_count]
    for r in requests:
        ref = int(r["referenceFeeCents"])
        for k in OPTION_KINDS:
            lp = r.get(f"{k}.listPriceCents", "").strip()
            if not lp:
                continue
            lp = int(lp)
            if lp < ref:
                all_loss += 1
                by_opt_struct[k][0] += 1
            else:
                all_ok += 1
                by_opt_struct[k][1] += 1
    print(f"  total options where listPrice < refFee: {all_loss}, >= : {all_ok}")
    for k, (loss, ok) in sorted(by_opt_struct.items()):
        print(f"    {k:>12}: listPrice<refFee={loss}, listPrice>=refFee={ok}")

    # Deadline tightness: options with deadline < 12h from respondBy
    print("\n--- Options with tight deadline (deadline - respondBy < 12h) ---")
    tight = 0
    total_opt = 0
    for r in requests:
        resp = int(r["respondBy"])
        for k in OPTION_KINDS:
            dl = r.get(f"{k}.deadline", "").strip()
            if not dl:
                continue
            total_opt += 1
            if int(dl) - resp < 720:
                tight += 1
    print(f"  {tight}/{total_opt} options have deadline < 12h from respondBy")

    # Notes: risky, and whether they correlate with loss
    print("\n--- Notes and outcome ---")
    RISKY = ["manual", "manuaalinen", "välijarru", "käsivaihteisto", "plates", "rekisterikilvet",
             "kilvet", "private", "yksityinen", "closing", "suljettu", "soita", "call before",
             "ennen kuin", "booking ref"]
    for label, group in [("won", won), ("lost", lost)]:
        risky = 0
        no_note = 0
        for r in group:
            note = (r.get("note") or "").strip()
            if not note:
                no_note += 1
                continue
            if any(kw in note.lower() for kw in RISKY):
                risky += 1
        print(f"  {label}: risky_note={risky}, no_note={no_note}, total={len(group)}")

    # Dealer-level: win rate per dealer
    print("\n--- Win rate per dealer (top 10 by request count) ---")
    dealer_stats = defaultdict(lambda: [0, 0])  # [won, total]
    for r in requests:
        d = r["dealerId"]
        dealer_stats[d][1] += 1
        if r["outcome"] == "won":
            dealer_stats[d][0] += 1
    for d, (w, t) in sorted(dealer_stats.items(), key=lambda x: -x[1][1])[:10]:
        print(f"  {d}: won={w}/{t} = {w/t:.0%}")


def main():
    for base in BASES:
        analyze_level(base)


if __name__ == "__main__":
    main()
