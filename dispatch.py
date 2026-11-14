"""The starter: a whole dispatch system that you can read in five minutes, then change. HANDBOOK.md says
what it does and how to run it (`spread-starter --help` lists the options).

To change it, copy this file and run the copy: python dispatch.py
"""
from __future__ import annotations

import argparse
import contextlib
import re
import sys
import time
from typing import NamedTuple, Optional

from spread.client import Client, SpreadError, Unreachable, World
from spread.types import (
    Action, ActionResult, Audience, DeskEvent, DeskEventKind, DeskSnapshot, FeeStep, Gig, Note, Order, PostAction, PriceRequest,
    Score, StopRef, TimeWindow,
)

HOUR, DAY = 60, 24 * 60
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# The events that need an answer. Views and account lines arrive with the next event that needs an answer.
WAKE_ON: list[DeskEventKind] = ["price_request", "order_won", "order_changed", "order_cancelled", "visit_failed", "gig_expired", "deadline_close"]

LARGE_QUOTE= 1000000000000000

RISKY_NOTE_KEYWORDS = [
    # manual gearbox (driver must hold a manual licence)
    "manual", "manuaalinen", "käsivaihteisto", "välijarru",
    # missing / transfer plates (driver needs withPlates)
    "plates", "kilvet", "rekisterikilvet", "siirtokilvet", "no plates", "missing plates",
    # call-before-pickup / private seller slot / closing hours
    "soita", "call before", "ennen kun", "ennen kuin", "private", "yksityinen",
    "closing", "suljettu",
]

class Retry(NamedTuple):
    """How to post a leg again after a failed visit."""
    pickup_from: int = 0
    manual_licence: bool = False
    plates: bool = False


class Desk:
    """What the starter remembers, and its answer to each event. The answers wait in the outbox
    until the starter sends them, and the world applies them at the current minute."""

    def __init__(self, world: World, level: int, now: int):
        self.world, self.level, self.now = world, level, now
        self.outbox: list[Action] = []  # the actions decided since the starter last sent
        self.requests: dict[str, PriceRequest] = {}
        self.orders: dict[str, Order] = {}  # the orders won and not cancelled
        self.leg_gigs: dict[str, dict[int, str]] = {}  # order id -> leg -> the gig that carries the leg
        self.booked: set[str] = set()  # the gigs that a driver holds now
        self.posts: dict[str, PostAction] = {}  # gig id -> the post that made it
        self.urgent: set[str] = set()  # the orders whose deadline warning has come
        self._roads: Optional[dict[tuple[str, str], int]] = None  # (from, to) -> driving minutes, loaded lazily
        self.dealer_stats: dict[str, dict] = {} 

    def rejoin(self, snapshot: DeskSnapshot, history: list[DeskEvent]) -> None:
        """Rebuilds what it knows from the world, so a starter that stopped keeps its orders. Then answers
        what it missed: open requests, orders without a gig, failed visits without a new gig, and the
        deadline warnings, so the orders that had one keep their steep fees."""
        posted: dict[str, list[Gig]] = {}  # order id -> its gigs, oldest first
        self.booked = {gig["id"] for gig in snapshot["gigs"] if gig["status"] in ("booked", "working", "done")}
        for gig in snapshot["gigs"]:
            posted.setdefault(gig["stops"][0]["orderId"], []).append(gig)
            if gig["status"] in ("open", "booked", "working"):
                self.carry({"type": "post", "stops": gig["stops"], "audience": gig["audience"], "schedule": gig["schedule"]}, gig["id"])
        self.requests = {e["request"]["id"]: e["request"] for e in history if e["kind"] == "price_request"}
        self.urgent = {e["orderId"] for e in history if e["kind"] == "deadline_close"}
        self.orders = {o["id"]: o for o in snapshot["orders"] if o["id"] in self.requests and o["status"] != "cancelled"}
        for order in self.orders.values():
            if order["status"] == "waiting" and not self.gigs_of(order["id"]):
                self.post(order, undelivered(order))
        for event in history:
            order = self.orders.get(event.get("orderId", ""))
            if order is None:
                continue
            if event["kind"] == "visit_failed" and order["deliveredAt"][event["leg"]] is None:
                later = [g for g in posted.get(order["id"], []) if g["postedAt"] >= event["at"] and g["id"] != event["gigId"]]
                if not any(stop["leg"] == event["leg"] for gig in later for stop in gig["stops"]):
                    self.retry(order["id"], event["leg"], event["reason"])
        for request in snapshot["openRequests"]:
            self.quote(request)

    def handle(self, event: DeskEvent) -> None:
        if event["kind"] == "price_request":
            self.quote(event["request"])
        elif event["kind"] == "order_won":
            self.win(event["order"])
        elif event["kind"] == "order_changed":
            self.change(event["order"])
        elif event["kind"] == "order_cancelled":  # the world has stopped the car and told each of its gigs what happens
            for books in (self.orders, self.leg_gigs):
                books.pop(event["orderId"], None)
        elif event["kind"] == "visit_failed":
            self.retry(event["orderId"], event["leg"], event["reason"])
        elif event["kind"] == "gig_expired":
            self.repost(event["gigId"])
        elif event["kind"] == "deadline_close":  # it comes again when a driver drops the car after the first warning
            self.raise_fees(event["orderId"])
        elif event["kind"] == "gig_booked":
            self.booked.add(event["gigId"])
        elif event["kind"] in ("gig_dropped", "no_show"):  # after a no-show the gig is open again; a late driver still comes
            self.booked.discard(event["gigId"])
        elif event["kind"] == "delivered":
            self.delivered(event["orderId"], event["leg"], event["at"])

    def delivered(self, order_id: str, leg: int, at: int) -> None:
        self.leg_gigs.get(order_id, {}).pop(leg, None)
        if order_id in self.orders:
            self.orders[order_id]["deliveredAt"][leg] = at

    def _road_minutes(self, from_place: str, to_place: str) -> int:
        """Driving minutes between two places, from the world map (loaded lazily, cached)."""
        if self._roads is None:
            world_map = self.world.client.map()
            self._roads = {(r["from"], r["to"]): int(r["minutes"]) for r in world_map["roads"]}
        key = (from_place, to_place)
        if key in self._roads:
            return self._roads[key]
        if from_place == to_place:
            return 25  # within a place: from the map's self-roads (12 km / 25 min)
        # No road entry: conservative fallback of one hour
        return 60

    def _safe_deadline(self, request: PriceRequest, option: dict, buffer_minutes: int = 30) -> bool:
        """Check whether the option's deadline can be met safely: for every leg, the
        earliest possible pickup (start of the pickup window) plus driving time to the
        delivery plus a safety buffer must fit before the option's deadline."""
        for leg in request["legs"]:
            pickup_from = leg["pickup"]["window"]["from"]
            travel = self._road_minutes(leg["pickup"]["placeId"], leg["delivery"]["placeId"])
            if pickup_from + travel + buffer_minutes > option["deadline"]:
                return False
        return True

    def _risky_note(self, request: PriceRequest) -> bool:
        """True when the dealer's note carries a risky keyword (manual gearbox, no plates,
        call-before-pickup, private slot, closing hours). At shortage levels these are
        skipped because drivers are scarce and failed visits kill margin."""
        note = (request.get("note") or "")
        text = note.lower()
        return any(kw in text for kw in RISKY_NOTE_KEYWORDS)

    def _should_bid(self, request: PriceRequest, option: dict) -> bool:
        ref = request["referenceFeeCents"]
        lp = option["listPriceCents"]

        # 1. Profitability: list price must cover expected driver cost.
        #    Observed median fee/referenceFee: ~1.0x (L1-6), ~1.75x (L7-8).
        price_multiplier = {1: 1.2, 2: 1.2, 3: 1.2, 4: 1.5, 5: 1.5, 6: 1.5, 7: 2.0, 8: 2.0}[self.level]
        buffer_multiplier = {1: 30, 2: 30, 3: 30, 4: 60, 5: 60, 6: 60, 7: 90, 8: 90}[self.level]
        if lp < ref * price_multiplier:
            return False

        # 2. Deadline slack: enough time from now to drive there with a level-based buffer.
        if not self._safe_deadline(request, option, buffer_minutes=buffer_multiplier):
            return False

        # 3. Risky notes: manual gearbox, missing plates, call-before-pickup, private-seller slot.
        #    At shortage levels (7-8) these are too risky; at lower levels accept with caution.
        if self.level >= 7 and self._risky_note(request):
            return False
        return True

    def _quote_price(self, request: PriceRequest, option: dict, stats: dict) -> int:
        price = option["listPriceCents"]
        if self.level >= 8:
            # Dealer trust: each on-time delivery raises accepted prices by 1% (cap 25%).
            # Be conservative: take half the trust as extra margin.
            trust = min(stats["on_time"], 25)
            price = int(price * (1 + trust / 200) + 0.5)  # +0.5 = round half up
        # Levels 1-7: quote at list price (competitor is at or above list; L1-3 no competitor).
        return max(1, price)

    def quote(self, request: PriceRequest) -> None:
        self.requests[request["id"]] = request
        dealer = request["dealerId"]
        stats = self.dealer_stats.setdefault(dealer, {"won": 0, "lost": 0, "on_time": 0, "late": 0})
        prices: dict[str, int] = {}
        for option in request["options"]:
            if not self._should_bid(request, option):
                continue
            prices[option["kind"]] = self._quote_price(request, option, stats)
        if not prices:
            # Decline: quote far above list on the first option (a losing quote costs nothing;
            # not quoting at all costs dealer goodwill).
            first = request["options"][0] if request["options"] else None
            if first is not None:
                prices[first["kind"]] = LARGE_QUOTE
        self.outbox.append({"type": "quote", "requestId": request["id"], "prices": prices})

    def win(self, order: Order) -> None:
        if order["id"] in self.requests and order["id"] not in self.orders:  # rejoin can know it already
            self.orders[order["id"]] = order
            self.post(order, undelivered(order))

    def change(self, order: Order) -> None:
        if order["id"] in self.orders:
            self.orders[order["id"]] = order
            if self.withdraw(order["id"]):
                self.post(order, undelivered(order))

    def repost(self, gig_id: str) -> None:
        """Posts a gig again when one of its windows ended before a driver booked it."""
        action = self.posts.get(gig_id)
        # A gig the starter has already taken off and posted again, after a change of plan, needs nothing.
        if action is None or not any(self.leg_gigs.get(s["orderId"], {}).get(s["leg"]) == gig_id for s in action["stops"]):
            return
        for stop in action["stops"]:
            self.leg_gigs.get(stop["orderId"], {}).pop(stop["leg"], None)
        order = self.orders.get(action["stops"][0]["orderId"])
        if order is None:
            return
        # One of its windows has ended, perhaps at a site's closing time before the window's own end: post without its own stop windows.
        stops = without_windows(action["stops"], float("inf"))
        self.outbox.append({**action, "stops": stops, "schedule": self.fees_for(order, pickups(action))})

    def raise_fees(self, order_id: str) -> None:
        """From the order's deadline warning on, its gigs that no driver holds go at the steep fees."""
        order = self.orders.get(order_id)
        if order is None:
            return
        self.urgent.add(order_id)
        self.send_post_of(order_id)
        for gig_id in self.gigs_of(order_id):
            if gig_id not in self.booked:
                self.outbox.append({"type": "reprice", "gigId": gig_id, "schedule": self.fees_for(order, pickups(self.posts[gig_id]))})

    def fees_for(self, order: Order, cars: int) -> list[FeeStep]:
        """The fees for some of the order's cars from now: rising, or steep once its deadline warning has come."""
        fee = self.requests[order["id"]]["referenceFeeCents"] * cars / len(order["legs"])
        return steep(self.now, fee, self.level) if order["id"] in self.urgent else rising(self.now, fee, self.level)

    def retry(self, order_id: str, leg: int, reason: str) -> None:
        if order_id in self.orders:
            order = self.orders[order_id]
            self.leg_gigs.get(order_id, {}).pop(leg, None)  # the world has taken the car out of the failed gig
            car_not_there = reason in ("not_ready", "closed")
            pickup_from = retry_from(order, self.requests[order_id], leg, self.now) if car_not_there else 0
            self.post(order, [leg], Retry(pickup_from, manual_licence=reason == "licence", plates=reason == "no_plates"))

    def post(self, order: Order, legs: list[int], retry: Retry = Retry()) -> None:
        # A premium car needs an experienced driver: the fine is €150 a car.
        audience: Audience = {"experienced": any(leg["car"]["premium"] for leg in order["legs"]), "manualLicence": retry.manual_licence}
        stops = without_windows(stops_for(order, legs, self.requests[order["id"]]["note"], retry), self.now)
        self.outbox.append({"type": "post", "stops": stops, "audience": audience, "schedule": self.fees_for(order, len(legs))})
        if len(legs) == 1:
            return
        # A trade-in over the 12-hour limit for a gig with two cars goes as one gig per car.
        self.send()
        if not self.gigs_of(order["id"]):
            for leg in legs:
                self.post(order, [leg], retry)

    def gigs_of(self, order_id: str) -> list[str]:
        """The gigs that carry the order's undelivered legs: one, or one for each leg of a split trade-in."""
        return list(dict.fromkeys(self.leg_gigs.get(order_id, {}).values()))

    def carry(self, action: PostAction, gig_id: str) -> None:
        """Remembers a gig that the world accepted: the post that made it, and the legs it carries."""
        for stop in action["stops"]:
            self.leg_gigs.setdefault(stop["orderId"], {})[stop["leg"]] = gig_id
        self.posts[gig_id] = action

    def withdraw(self, order_id: str) -> bool:
        """Takes an order's gigs out of the drivers' app. False, and nothing withdrawn, when a driver holds one of them."""
        self.send_post_of(order_id)
        gigs = self.gigs_of(order_id)
        if any(gig_id in self.booked for gig_id in gigs):
            return False
        self.leg_gigs.pop(order_id, None)
        self.outbox += [{"type": "withdraw", "gigId": gig_id} for gig_id in gigs]
        return True

    def send_post_of(self, order_id: str) -> None:
        """A post of the order still in the outbox goes to the world now, so the starter knows its gig. Each gig carries one order."""
        if any(action["type"] == "post" and action["stops"][0]["orderId"] == order_id for action in self.outbox):
            self.send()

    def send(self) -> None:
        """Sends the outbox now. In practice, the outbox goes with the next advance instead."""
        actions, self.outbox = self.outbox, []
        if actions:
            self.read_results(actions, self.world.act(actions))

    def read_results(self, actions: list[Action], results: list[ActionResult]) -> None:
        """Remembers the gig of each post that the world accepted."""
        for action, result in zip(actions, results):
            if action["type"] == "post" and result["ok"] is True:
                self.carry(action, result["id"])


def retry_from(order: Order, request: PriceRequest, leg: int, now: int) -> int:
    """When to try a car again after a visit found it not ready or the site closed: two hours on, or,
    when the dealer moved the deadline for a car that is late, when the car should be ready by the
    same delay."""
    promised = next((o["deadline"] for o in request["options"] if o["kind"] == order["option"]), order["deadline"])
    delay = max(0, order["deadline"] - promised)
    return max(now + 2 * HOUR, order["legs"][leg]["pickup"]["window"]["from"] + delay)


def undelivered(order: Order) -> list[int]:
    return [leg for leg in range(len(order["legs"])) if order["deliveredAt"][leg] is None]


def pickups(action: PostAction) -> int:
    """The cars a post carries."""
    return sum(1 for stop in action["stops"] if stop["kind"] == "pickup")


def without_windows(stops: list[StopRef], before: float) -> list[StopRef]:
    """Leaves out the stop windows that end before `before`, so the world's window applies: once the order's
    window has ended, it runs to the end of the world."""
    return [{k: v for k, v in stop.items() if k != "window"} if "window" in stop and stop["window"]["to"] < before else stop for stop in stops]


def rising(now: int, fee: float, level: int) -> list[FeeStep]:
    """The fee at once, then 10% of it more every hour: six steps, up to 1.5 times. Level 1 takes one fee only."""
    # int(x + 0.5) rounds halves up, as JavaScript's Math.round does, so both starters post the same fees.
    return [{"at": now + k * HOUR, "feeCents": max(1, int(fee * (1 + k / 10) + 0.5))} for k in range(1 if level == 1 else 6)]


def steep(now: int, fee: float, level: int) -> list[FeeStep]:
    """1.5 times the fee at once, then a quarter of it more every half hour, up to 2.5 times. Level 1 takes one fee only."""
    return [{"at": now + k * 30, "feeCents": max(1, int(fee * (1.5 + k / 4) + 0.5))} for k in range(1 if level == 1 else 5)]


def stops_for(order: Order, legs: list[int], note: Optional[Note], retry: Retry) -> list[StopRef]:
    """A pickup and a delivery for each leg. An electric car under 30% gets a charging stop.
    A stop has its own window only when the note or the retry makes it narrower. Without
    one, the world plans the pickup in the order's window and the delivery by the deadline."""
    stops: list[StopRef] = []
    for leg in legs:
        car, pickup, delivery = order["legs"][leg]["car"], order["legs"][leg]["pickup"], order["legs"][leg]["delivery"]
        noted = window_from_note(note, pickup["window"], "pickup")
        start = max(noted["from"], retry.pickup_from)
        pickup_window: TimeWindow = {"from": start, "to": max(noted["to"], start)}
        delivery_base: TimeWindow = {"from": delivery["window"]["from"], "to": max(delivery["window"]["from"], order["deadline"])}
        delivery_window = window_from_note(note, delivery_base, "delivery")
        low_battery = car["electric"] and car["chargePercent"] is not None and car["chargePercent"] < 30
        first: StopRef = {"orderId": order["id"], "leg": leg, "kind": "pickup", "withPlates": retry.plates, "withCharging": low_battery}
        second: StopRef = {"orderId": order["id"], "leg": leg, "kind": "delivery"}
        stops += [narrower(first, pickup_window, pickup["window"]), narrower(second, delivery_window, delivery_base)]
    return stops


def narrower(stop: StopRef, window: TimeWindow, base: TimeWindow) -> StopRef:
    return {**stop, "window": window} if window["from"] > base["from"] or window["to"] < base["to"] else stop


# "Not before", "at the earliest" and a ready time all mean from then on; read them before BEFORE.
# re.ASCII makes \b and \d mean what they mean in JavaScript, so both starters read a note alike.
AFTER = re.compile(r"jälkeen|alkaen|aikaisintaan|\bafter\b|\bfrom\b|earliest|not before|ei ennen|valmis|\bready\b|efter|tidigast|inte före|klar", re.ASCII)
BEFORE = re.compile(r"ennen|mennessä|viim|\bbefore\b|\bby\b|latest|före|senast", re.ASCII)
DELIVERY = re.compile(r"toimit|perillä|perille|vastaanot|kohde|deliver|arrive|destination|receiv|leverera|leverans|framme|mottag", re.ASCII)
CLOCK = re.compile(r"(?:klo|kl\.?)\s*(\d{1,2})(?:[.:](\d{2}))?|\b(\d{1,2})[.:](\d{2})\b", re.ASCII)
# Opening hours as a range: "9-16", "klo 9.00-14.30", "kl 10-14".
RANGE = re.compile(r"\b(\d{1,2})(?:[.:](\d{2}))?\s*[-–]\s*(\d{1,2})(?:[.:](\d{2}))?\b", re.ASCII)


def window_from_note(note: Optional[Note], window: TimeWindow, stop: str) -> TimeWindow:
    """Narrows a stop's window by clock times in the note, found by keywords: "nouto ennen 15.30",
    "klo 12 jälkeen", "deliver by 19:00", "efter kl. 12", "aikaisintaan klo 13.30", "not before
    16:00", and opening hours as a range, "arkisin 9-16". The time is on the window's own day."""
    if not note:
        return window
    text = note["text"].lower()
    if bool(DELIVERY.search(text)) != (stop == "delivery"):
        return window

    def minutes(hour: int, minute: int) -> Optional[int]:
        return None if hour > 24 or minute > 59 else hour * HOUR + minute

    start, end = window["from"], window["to"]
    # Opening hours hold every day: they move the window's start to the first day's opening and its end to the last day's closing.
    for match in RANGE.finditer(text):
        opens, closes = minutes(int(match[1]), int(match[2] or 0)), minutes(int(match[3]), int(match[4] or 0))
        if opens is None or closes is None or opens >= closes:
            continue
        start = max(start, start // DAY * DAY + opens)
        end = min(end, end // DAY * DAY + closes)
    text = RANGE.sub(" ", text)
    for match in CLOCK.finditer(text):
        m = minutes(int(match[1] or match[3]), int(match[2] or match[4] or 0))
        if m is None:
            continue
        time = window["from"] // DAY * DAY + m
        around = text[max(0, match.start() - 16) : match.end() + 16]
        if AFTER.search(around):
            start = max(start, time)
        elif BEFORE.search(around):
            end = min(end, time)
    return {"from": start, "to": end} if start <= end else window


def play_practice(world: World, level: int) -> None:
    """Steps the world from one event that needs an answer to the next, and stops at each midnight for a report."""
    snapshot = world.snapshot()
    desk, ends_at = Desk(world, level, snapshot["now"]), snapshot["endsAt"]
    while desk.now < ends_at:
        day, midnight = desk.now // DAY, (desk.now // DAY + 1) * DAY
        # One request a step: the advance applies the outbox, then moves the clock.
        actions, desk.outbox = desk.outbox, []
        step = world.advance(until=WAKE_ON, max=min(ends_at, midnight), actions=actions)
        desk.read_results(actions, step["results"])
        desk.now = step["now"]
        for event in step["events"]:
            desk.handle(event)
        if desk.now // DAY > day:
            report(WEEKDAYS[day % 7], world.score())


def play_final(world: World) -> None:
    """Rebuilds the books, then answers events as they come. After a second without an event,
    it reads the clock."""
    history = world.events()
    snapshot = world.snapshot()
    if snapshot["now"] >= snapshot["endsAt"]:
        return
    level = next((w["level"] for w in world.client.worlds() if w["worldId"] == world.id), 8)
    desk = Desk(world, level, snapshot["now"])
    desk.rejoin(snapshot, history)
    desk.send()
    for event in world.stream(after=history[-1]["seq"] if history else -1, idle=1.0):
        day = desk.now // DAY
        desk.now = world.client.final()["now"] if event is None else max(desk.now, event["at"])
        if event is not None:
            desk.handle(event)
        desk.send()
        if desk.now // DAY > day:
            report(WEEKDAYS[day % 7], world.score())
        if desk.now >= snapshot["endsAt"]:
            return


def report(label: str, score: Score) -> None:
    orders = f"{score['wonOrders']:>4} orders  {score['onTimeShare']:6.1%} on time  {score['undeliveredShare']:5.1%} not delivered"
    print(f"{label:<9}  margin €{score['marginCents'] / 100:>11,.2f}  revenue €{score['revenueCents'] / 100:>11,.2f}  {orders}", flush=True)


def next_final(client: Client) -> World:
    """Your copy of the final that is set or runs now. Waits while none is set and while the last one has
    ended: the organisers set a test final 15 minutes before its start, and the final 30 minutes before."""
    said = False
    while True:
        try:
            world: Optional[World] = client.world(client.final()["worldId"])
        except SpreadError as error:
            if error.status != 404:
                raise
            world = None
        if world is not None:
            snapshot = world.snapshot()
            if snapshot["now"] < snapshot["endsAt"]:
                return world
        if not said:
            print("No final is set. Waiting for it: the organisers set a test final 15 minutes before its start, and the final 30 minutes before.", flush=True)
            said = True
        time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plays a Spread world with the starter. Set SPREAD_URL and SPREAD_KEY first.")
    parser.add_argument("--level", type=int, choices=range(1, 9), default=8, help="the practice level, 1 to 8 (default 8)")
    parser.add_argument("--seed", help="any text; the same seed gives the same world")
    parser.add_argument("--board", action="store_true", help="play your next ranking run at the level; it counts in the practice rankings")
    parser.add_argument("--final", action="store_true", help="play your copy of the final from the live stream")
    args = parser.parse_args()
    try:
        client = Client()
        score = None
        if args.final:
            world = None
            # A server restart drops every connection. The desk then rebuilds its books from the server, as after its own restart.
            while True:
                try:
                    if world is None:
                        world = next_final(client)
                        print(f"Final world {world.id}.", flush=True)
                    play_final(world)
                    score = world.score()
                    break
                except Unreachable as error:
                    print(f"spread: {error}. Trying again in 5 seconds.", file=sys.stderr, flush=True)
                    time.sleep(5)
        else:
            world = client.create(level=args.level, seed=args.seed, board=args.board)
            # A ranking run that a team deletes plays to its end without the team's system, and counts.
            name = f"{'Ranking run' if args.board else 'Practice world'} {world.id}"
            stops = "Ctrl-C or an error stops your system: the run then plays to its end without it, and counts." if args.board else "Ctrl-C or an error stops the run and deletes the world."
            print(f"{name}, level {args.level}. {stops}", flush=True)
            try:
                play_practice(world, args.level)
            except BaseException as error:  # a world left behind holds one of the team's eight practice slots
                try:
                    world.delete()
                    deleted = True
                except SpreadError:
                    deleted = False
                after = (f"{name} plays to its end without your system, and counts." if args.board
                         else f"{name} is deleted." if deleted else f"{name} is not deleted: it holds one of your eight practice slots.")
                if isinstance(error, KeyboardInterrupt):
                    sys.exit(f"Stopped. {after}")
                print(f"Stopped by the error below. {after}", file=sys.stderr)
                raise
        score = score or world.score()
        report("End", score)
        print("Ranked." if score["ranked"] else "Not ranked: no order won." if score["wonOrders"] == 0 else "Not ranked: the delivery minimum is 90% of the orders on time.")
    except SpreadError as error:
        sys.exit(f"spread: {error}")
    except KeyboardInterrupt:
        sys.exit("Stopped. The final world keeps running without your system; start it again with --final.")


if __name__ == "__main__":
    main()
