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

class Constants:
    MINUTES_PER_HOUR = 60
    HOURS_PER_DAY = 24
    MINUTES_PER_DAY = HOURS_PER_DAY * MINUTES_PER_HOUR
    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    WAKE_ON: tuple[DeskEventKind, ...] = (
        "price_request", "order_won", "order_changed", "order_cancelled",
        "visit_failed", "gig_expired", "deadline_close",
    )

    RETRY_DELAY_HOURS = 2
    RISING_FEE_DIVISOR = 10
    RISING_FEE_STEPS = 6
    STEEP_INITIAL_FEE_MULTIPLIER = 1.5
    STEEP_FEE_DIVISOR = 4
    STEEP_FEE_INTERVAL_MINUTES = 30
    STEEP_FEE_STEPS = 5
    SINGLE_FEE_LEVEL = 1
    MINIMUM_FEE_CENTS = 1
    ROUND_HALF_UP_OFFSET = 0.5
    LOW_BATTERY_THRESHOLD_PERCENT = 30

    MAX_NOTE_HOUR = 24
    MAX_NOTE_MINUTE = 59
    NOTE_KEYWORD_CONTEXT_CHARS = 16
    # "Not before" and ready-time phrases take precedence when a note contains both kinds of keyword.
    # re.ASCII keeps note parsing consistent with JavaScript's ASCII word boundaries.
    AFTER = re.compile(r"jälkeen|alkaen|aikaisintaan|\bafter\b|\bfrom\b|earliest|not before|ei ennen|valmis|\bready\b|efter|tidigast|inte före|klar", re.ASCII)
    BEFORE = re.compile(r"ennen|mennessä|viim|\bbefore\b|\bby\b|latest|före|senast", re.ASCII)
    DELIVERY = re.compile(r"toimit|perillä|perille|vastaanot|kohde|deliver|arrive|destination|receiv|leverera|leverans|framme|mottag", re.ASCII)
    CLOCK = re.compile(r"(?:klo|kl\.?)\s*(\d{1,2})(?:[.:](\d{2}))?|\b(\d{1,2})[.:](\d{2})\b", re.ASCII)
    RANGE = re.compile(r"\b(\d{1,2})(?:[.:](\d{2}))?\s*[-–]\s*(\d{1,2})(?:[.:](\d{2}))?\b", re.ASCII)

    DEFAULT_LEVEL = 8
    MIN_LEVEL = 1
    MAX_LEVEL = 8
    FINAL_STREAM_IDLE_SECONDS = 1.0
    FINAL_POLL_INTERVAL_SECONDS = 10
    UNREACHABLE_RETRY_SECONDS = 5
    MINIMUM_ON_TIME_SHARE = 0.9


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

    def quote(self, request: PriceRequest) -> None:
        self.requests[request["id"]] = request
        first = request["options"][0]
        self.outbox.append({"type": "quote", "requestId": request["id"], "prices": {first["kind"]: first["listPriceCents"]}})

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
    """When to try a car again after a visit found it not ready or the site closed: after the configured delay, or,
    when the dealer moved the deadline for a car that is late, when the car should be ready by the
    same delay."""
    promised = next((o["deadline"] for o in request["options"] if o["kind"] == order["option"]), order["deadline"])
    delay = max(0, order["deadline"] - promised)
    return max(now + Constants.RETRY_DELAY_HOURS * Constants.MINUTES_PER_HOUR, order["legs"][leg]["pickup"]["window"]["from"] + delay)


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
    """The fee at once, then a configured increase every hour. Level 1 takes one fee only."""
    # int(x + 0.5) rounds halves up, as JavaScript's Math.round does, so both starters post the same fees.
    steps = 1 if level == Constants.SINGLE_FEE_LEVEL else Constants.RISING_FEE_STEPS
    return [
        {
            "at": now + k * Constants.MINUTES_PER_HOUR,
            "feeCents": max(
                Constants.MINIMUM_FEE_CENTS,
                int(fee * (1 + k / Constants.RISING_FEE_DIVISOR) + Constants.ROUND_HALF_UP_OFFSET),
            ),
        }
        for k in range(steps)
    ]


def steep(now: int, fee: float, level: int) -> list[FeeStep]:
    """Starts at the configured multiplier, then increases at each configured interval. Level 1 takes one fee only."""
    steps = 1 if level == Constants.SINGLE_FEE_LEVEL else Constants.STEEP_FEE_STEPS
    return [
        {
            "at": now + k * Constants.STEEP_FEE_INTERVAL_MINUTES,
            "feeCents": max(
                Constants.MINIMUM_FEE_CENTS,
                int(fee * (Constants.STEEP_INITIAL_FEE_MULTIPLIER + k / Constants.STEEP_FEE_DIVISOR) + Constants.ROUND_HALF_UP_OFFSET),
            ),
        }
        for k in range(steps)
    ]


def stops_for(order: Order, legs: list[int], note: Optional[Note], retry: Retry) -> list[StopRef]:
    """A pickup and a delivery for each leg. An electric car below the configured battery threshold gets a charging stop.
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
        low_battery = car["electric"] and car["chargePercent"] is not None and car["chargePercent"] < Constants.LOW_BATTERY_THRESHOLD_PERCENT
        first: StopRef = {"orderId": order["id"], "leg": leg, "kind": "pickup", "withPlates": retry.plates, "withCharging": low_battery}
        second: StopRef = {"orderId": order["id"], "leg": leg, "kind": "delivery"}
        stops += [narrower(first, pickup_window, pickup["window"]), narrower(second, delivery_window, delivery_base)]
    return stops


def narrower(stop: StopRef, window: TimeWindow, base: TimeWindow) -> StopRef:
    return {**stop, "window": window} if window["from"] > base["from"] or window["to"] < base["to"] else stop


def window_from_note(note: Optional[Note], window: TimeWindow, stop: str) -> TimeWindow:
    """Narrows a stop's window by clock times in the note, found by keywords: "nouto ennen 15.30",
    "klo 12 jälkeen", "deliver by 19:00", "efter kl. 12", "aikaisintaan klo 13.30", "not before
    16:00", and opening hours as a range, "arkisin 9-16". The time is on the window's own day."""
    if not note:
        return window
    text = note["text"].lower()
    if bool(Constants.DELIVERY.search(text)) != (stop == "delivery"):
        return window

    def minutes(hour: int, minute: int) -> Optional[int]:
        return None if hour > Constants.MAX_NOTE_HOUR or minute > Constants.MAX_NOTE_MINUTE else hour * Constants.MINUTES_PER_HOUR + minute

    start, end = window["from"], window["to"]
    # Opening hours hold every day: they move the window's start to the first day's opening and its end to the last day's closing.
    for match in Constants.RANGE.finditer(text):
        opens, closes = minutes(int(match[1]), int(match[2] or 0)), minutes(int(match[3]), int(match[4] or 0))
        if opens is None or closes is None or opens >= closes:
            continue
        start = max(start, start // Constants.MINUTES_PER_DAY * Constants.MINUTES_PER_DAY + opens)
        end = min(end, end // Constants.MINUTES_PER_DAY * Constants.MINUTES_PER_DAY + closes)
    text = Constants.RANGE.sub(" ", text)
    for match in Constants.CLOCK.finditer(text):
        m = minutes(int(match[1] or match[3]), int(match[2] or match[4] or 0))
        if m is None:
            continue
        time = window["from"] // Constants.MINUTES_PER_DAY * Constants.MINUTES_PER_DAY + m
        around = text[max(0, match.start() - Constants.NOTE_KEYWORD_CONTEXT_CHARS) : match.end() + Constants.NOTE_KEYWORD_CONTEXT_CHARS]
        if Constants.AFTER.search(around):
            start = max(start, time)
        elif Constants.BEFORE.search(around):
            end = min(end, time)
    return {"from": start, "to": end} if start <= end else window


def play_practice(world: World, level: int) -> None:
    """Steps the world from one event that needs an answer to the next, and stops at each midnight for a report."""
    snapshot = world.snapshot()
    desk, ends_at = Desk(world, level, snapshot["now"]), snapshot["endsAt"]
    while desk.now < ends_at:
        day, midnight = desk.now // Constants.MINUTES_PER_DAY, (desk.now // Constants.MINUTES_PER_DAY + 1) * Constants.MINUTES_PER_DAY
        # One request a step: the advance applies the outbox, then moves the clock.
        actions, desk.outbox = desk.outbox, []
        step = world.advance(until=list(Constants.WAKE_ON), max=min(ends_at, midnight), actions=actions)
        desk.read_results(actions, step["results"])
        desk.now = step["now"]
        for event in step["events"]:
            desk.handle(event)
        if desk.now // Constants.MINUTES_PER_DAY > day:
            report(Constants.WEEKDAYS[day % len(Constants.WEEKDAYS)], world.score())


def play_final(world: World) -> None:
    """Rebuilds the books, then answers events as they come. After a second without an event,
    it reads the clock."""
    history = world.events()
    snapshot = world.snapshot()
    if snapshot["now"] >= snapshot["endsAt"]:
        return
    level = next((w["level"] for w in world.client.worlds() if w["worldId"] == world.id), Constants.DEFAULT_LEVEL)
    desk = Desk(world, level, snapshot["now"])
    desk.rejoin(snapshot, history)
    desk.send()
    for event in world.stream(after=history[-1]["seq"] if history else -1, idle=Constants.FINAL_STREAM_IDLE_SECONDS):
        day = desk.now // Constants.MINUTES_PER_DAY
        desk.now = world.client.final()["now"] if event is None else max(desk.now, event["at"])
        if event is not None:
            desk.handle(event)
        desk.send()
        if desk.now // Constants.MINUTES_PER_DAY > day:
            report(Constants.WEEKDAYS[day % len(Constants.WEEKDAYS)], world.score())
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
        time.sleep(Constants.FINAL_POLL_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plays a Spread world with the starter. Set SPREAD_URL and SPREAD_KEY first.")
    parser.add_argument(
        "--level",
        type=int,
        choices=range(Constants.MIN_LEVEL, Constants.MAX_LEVEL + 1),
        default=Constants.DEFAULT_LEVEL,
        help=f"the practice level, {Constants.MIN_LEVEL} to {Constants.MAX_LEVEL} (default {Constants.DEFAULT_LEVEL})",
    )
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
                    print(f"spread: {error}. Trying again in {Constants.UNREACHABLE_RETRY_SECONDS} seconds.", file=sys.stderr, flush=True)
                    time.sleep(Constants.UNREACHABLE_RETRY_SECONDS)
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
        print("Ranked." if score["ranked"] else "Not ranked: no order won." if score["wonOrders"] == 0 else f"Not ranked: the delivery minimum is {Constants.MINIMUM_ON_TIME_SHARE:.0%} of the orders on time.")
    except SpreadError as error:
        sys.exit(f"spread: {error}")
    except KeyboardInterrupt:
        sys.exit("Stopped. The final world keeps running without your system; start it again with --final.")


if __name__ == "__main__":
    main()
