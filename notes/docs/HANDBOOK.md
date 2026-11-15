# Spread: the handbook

Your team builds a system that does the work of a car transport dispatcher in Finland for one simulated week. The API calls that week a world. Car dealers send you price requests. You win some of them, and each win is an order: one or two cars to move by a deadline. You post gigs in the drivers' app, and freelance drivers book them. Your score is your gross margin in game euros.

The rules (`RULES.md`) and the API reference (`API.md`) are the full references; the server serves both at `/kit/`. When this handbook and the rules differ, the rules are correct.

## The first 15 minutes

The server address is `https://spread.bahnexpress.com`. Create your team at `/join` to get your team key. Put both in two variables. All the commands below read them.

```sh
export SPREAD_URL=https://spread.bahnexpress.com
export SPREAD_KEY=<your team key>
```

**Python** 3.9 or later. The package needs no other packages.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install $SPREAD_URL/kit/spread-0.1.0-py3-none-any.whl
spread-starter
```

**TypeScript** on Bun, or on Node 22.18 or later. The kit needs no packages.

```sh
curl -fsSO $SPREAD_URL/kit/spread-typescript.tar.gz
tar -xzf spread-typescript.tar.gz
cd typescript
bun starter.ts                       # or: node starter.ts
```

Some Node versions print `ExperimentalWarning: Type Stripping is an experimental feature`. It is harmless.

The starter creates a level 8 practice world and plays it to the end. It prints the score after each day and at the end, here on the seed `spread-1`:

```text
Practice world w320bfbbab4715dd4a6c2, level 8. Ctrl-C or an error stops the run and deletes the world.
Monday     margin €     -22.26  revenue €   1,721.00   213 orders   70.2% on time   0.0% not delivered
Tuesday    margin €  -1,212.29  revenue €  10,546.00   413 orders   62.6% on time   0.0% not delivered
Wednesday  margin €  -6,281.98  revenue €  19,583.00   595 orders   57.3% on time   0.0% not delivered
Thursday   margin € -14,714.24  revenue €  30,929.00   818 orders   50.6% on time   0.0% not delivered
Friday     margin € -25,138.54  revenue €  42,037.00   950 orders   44.2% on time   0.0% not delivered
Saturday   margin € -31,304.92  revenue €  47,980.00   949 orders   44.8% on time   0.0% not delivered
Sunday     margin € -31,304.92  revenue €  47,980.00   949 orders   44.8% on time   0.0% not delivered
Monday     margin € -52,376.59  revenue €  62,570.00   949 orders   45.5% on time   0.0% not delivered
Tuesday    margin € -61,318.47  revenue €  67,725.00   949 orders   45.5% on time   0.0% not delivered
End        margin € -72,612.51  revenue €  71,332.00   949 orders   45.5% on time   1.3% not delivered
Not ranked: the delivery minimum is 90% of the orders on time.
```

The starter sends one request for each step of the world. A level 6 week takes 1,000 to 1,450 requests, and the level 8 week above takes 1,891. The practice limit is 1,000 requests a second when few teams practise. It is about 170 when 60 teams practise at once, and 50 when 200 do (see [Limits](#limits)). Systems on one key share the limit and take turns.

Thus the level 8 week above takes about 2 seconds on a quiet server. It takes about 11 seconds when 60 teams practise, and about 38 seconds when 200 do. While a final or a test final runs, the limit is 20 a second, and that week takes about 95 seconds. A distant server adds its round trip to each request: at 20 ms, 1,250 requests take at least 25 seconds.

The starter loses money on almost every week. At level 8 it loses €72,612.51 on this seed. It quotes the list price, wins most of the cars, and cannot find drivers in time for more than half of them. On the same seed, the same starter ends at −€50,442.49 at level 7 and at −€1,172.51 at level 6. Revenue counts when an order is delivered, so the daily lines lag the orders. The starter is a starting point for your system.

Both starters take the same options:

| Option | Effect |
|---|---|
| `--level 1` to `--level 8` | The level of the practice world. The default is 8, the final's level. |
| `--seed <text>` | The same seed gives the same world. Use it to compare two versions of your system. |
| `--board` | Plays your next ranking run at the level: the next world of the current ranking round. It counts in the practice rankings (see [Practice rankings](#practice-rankings)). |
| `--final` | Plays your copy of the final from the live stream. |

Then make the starter your system. In TypeScript, edit `starter.ts`. In Python, copy the starter out of the package and run the copy:

```sh
cp "$(python -c 'import spread.starter as s; print(s.__file__)')" dispatch.py
python dispatch.py --level 2
```

Read the starter first. It has about 200 lines of logic. It collects the actions for each step and sends them with the next `advance`: one request for each step. It does six things:

1. It quotes the list price of the dealer's first delivery option.
2. It posts each order at once, at the request's typical fee. It raises the fee by 10% of the typical fee every hour, up to 1.5 times.
3. It reads clock times from notes with a keyword match.
4. It posts a leg again after a failed visit, and a gig again when it expires, unless it has already posted its cars again after a change of plan.
5. From an order's deadline warning on, it posts the order's open gigs at a steep fee: 1.5 times the typical fee at once, then a quarter of it more every half hour, up to 2.5 times.
6. In the final, it first rebuilds what it knows from the world, so it can stop and start again without losing its orders.

## The game

- **The week.** World time is in whole minutes from Monday 00:00. Price requests arrive from Monday 08:00 to Friday 18:00, almost all between 09:00 and 18:00. On Saturday, dealer sites hand over and receive cars at short hours, and some are closed. On Sunday every site is closed.
- **The end of the week.** The next Monday is a business day without new requests, and the last deadlines fall on it. The world then runs on to Wednesday 22:00, so late cars can still arrive.
- **Price requests.** A request names the dealer and one or two legs. A leg is one car from a pickup place to a delivery place. A trade-in has two legs: the new car out and the old car back. The request also gives the delivery options, a [typical fee](#the-typical-fee), maybe a note, and a response deadline 15 minutes after it arrives.
- **Windows.** A pickup window starts at the car's stated ready time. At a dealer site, a request's windows end at the latest option's deadline. An order's end at the won option's deadline. The opening hours say when the site is open. At a private address, the window is the booked slot.
- **Delivery options.** Each deadline is at 20:00 on a business day, Monday to Friday. `same_day` ends on the day the car is ready, and `next_day` on the next business day. `within_3_days` ends on the third business day after the ready day, no later than the last Monday. A dealer offers `same_day` only when the request arrives by 15:00 that day and the drive can end 2 hours before the deadline. A private delivery is due 30 minutes after its booked hour ends.
- **Quotes.** You give one price for each option that you offer. To decline, quote a price far above the list price: a quote that loses costs nothing, while a request without a quote costs a little goodwill. Up to level 3 every quote wins, so there send no quote. The dealer decides at the request's `respondBy`, on your last quote; a later quote is refused. The dealer compares your prices with the prices of a competitor and with the value of each option to the dealer. The list price is a public price list by distance, and the competitor's prices start from it (see [Dealers compare prices](#dealers-compare-prices)).
- **Gigs.** A gig moves one or more legs through an ordered list of stops. It says which drivers may take it (`audience`) and has a fee schedule of 1 to 48 steps whose fees never fall. A stop can carry its own window. Except at a private pickup, that window may end after the order's window, so a late car can still go. A driver books a gig only when they can reach every stop inside its window, by the road minutes, so a gig can get no driver before any window ends. A gig that no driver has booked when the first of its windows ends leaves the app, as `gig_expired`. A gig with more than one car can plan at most 12 hours of driving, the drives between the cars included. Post a trade-in that needs more than 12 hours as two gigs. In one gig, put the old car's pickup right after the new car's delivery: the driver collects the old car in the same visit.
- **A car without a driver.** No one else moves a car for you. When an order still has no driver 3 hours before `lastPickupAt`, the last minute to collect it and still be on time, `deadline_close` warns you. Then only a higher fee or a direct offer finds a driver, and a driver needs time to see the gig and reach the pickup.
- **Score and the delivery minimum.** Your score is the sum of your account: prices in, and driver fees, refunds, penalties and fines out. Your system is ranked only when at least 90% of its orders arrive on time. A system that wins no order is not ranked. A car not delivered at the end counts as late, and costs twice its price.

The rules have the tables for your account, release fees and failed visits, and the five rules for handling cars.

## Levels

Each practice world has a level. The API is the same at all levels. Each level adds mechanisms to the one before it.

| Level | Name | What the level adds |
|---|---|---|
| 1 | Posting | Gigs with one car and one fee. Up to level 3, the dealer ignores your prices: every quote wins the first option at its list price. Dealers can cancel orders. |
| 2 | Market | Fee schedules. Drivers learn your fees and can drop bookings. |
| 3 | Network | Gigs with several cars, and direct offers, on the same world as level 2. |
| 4 | Quotes | The competitor. The dealer chooses between your options. Late orders make dealers accept lower prices from you, and so, a little, do requests that you do not quote. |
| 5 | Full | Notes, changes of plan, failed visits, the rules for handling cars, and weather. |
| 6 | Surprises | Level 5 with three events that nobody announces. **The outage**: every week, on one working day, the competitor's systems are down in one region from 08:00 to 18:00. Dealers there turn to a backup that costs about 30% more than the competitor. The region is a town and the places within 80 km of it, in a straight line, and busy regions go down more often. **The flu**: in some weeks, 30% of the drivers who meant to work stay at home for a day. **The bulk order**: in some weeks, a dealer group sends 30 to 40 cars from one yard in one go. |
| 7 | Shortage | Level 6 in a driver shortage. The world has about a third of the drivers of an average world, and the competitor's prices are about 25% above the list price. Every week has the outage, the flu and the bulk order. See [The shortage](#the-shortage). |
| 8 | Trust | Level 7 with a shortage that differs from town to town. The competitor's prices are about 40% above the list price. Every week also has a price war that nobody announces. Each dealer accepts higher prices from you for the orders that you delivered to it on time. See [The shortage](#the-shortage) and [Dealer trust](#dealer-trust). |

The final is level 8. Start at a low level to make your system work, then go up.

## Practice tools

- **You move the clock.** A practice world stands still until your code calls `advance`. It advances to a minute, by some minutes, or to the next event of the kinds that you name. The answer holds all events since the previous `advance`. An `advance` can carry the step's `actions`: the world applies them at the current minute, then moves the clock. Thus one request does a whole step.
- **The same week every time.** The world is deterministic: the same seed, level and actions, at the same minutes, give the same week to the cent. Thus one run of each version of your system on a seed is enough. A change to your system changes what drivers do from then on, so the rest of the week differs too. Thus compare two versions on several seeds, the same seeds for both. A practice week takes about a second on the server, and your network adds its round trip to each request (Limits). Thus run several weeks at once: sweeps over many seeds and settings are part of the game.
- **Copies.** `fork` copies a practice world at its current minute, with the same future draws. Thus you can try two decisions on the same future. A copy uses one of your eight practice slots.
- **The reveal.** After a practice world ends, `reveal` shows what a dispatcher learns by working the week: your quotes and what the dealers did with them, what was true of each car that you won, and who viewed your gigs at which fee. It never shows the competitor's prices or what a gig cost a driver. See [Research](#research).
- **Ranking runs.** Five worlds in each ranking round, the same for every team. See [Practice rankings](#practice-rankings).
- **Run viewer.** `$SPREAD_URL/viewer/` shows each week that your team played. It gives each order's money and story, worst margin first, and a replay of the week on a map. It uses the team key that you gave the site at `/team`.
- **Test finals.** Two short runs with a moving clock, on Saturday 14 November at 14:00 and 20:00 Helsinki time. Each plays a random level 8 week from Thursday 00:00 and takes about 19 minutes. The organisers set each one 15 minutes before its start, as a final for every team. Join it as you join the final, with `client.final()` or the starter's `--final`. A test final shows whether your code keeps up with the clock.

## Practice rankings

A ranking run is a practice world on a seed that the server chooses. All teams compare their systems on ranking runs. Create one with `client.create(level, board=True)` in Python, `client.create(level, { board: true })` in TypeScript, or the starter's `--board` option. The API calls a ranking run a board run and a ranking round a season.

- A ranking round is five secret worlds at each level, the same for every team. The organisers start and name each round. You play the round's worlds in order, each once. After five runs at a level, you wait for the next round.
- You cannot copy or reveal a ranking run. A ranking run uses a practice slot.
- A ranking run that you delete before its end runs to its end without your system, and counts with its score at the end. Thus a deletion does not remove a bad week. A run counts in the round in which you created it.
- The practice rankings of a level (`client.board(level)`) show the current round and the previous one. They show each team's runs and, after five runs, its total margin, on-time share and rank. Teams whose five runs all meet the delivery minimum come first. Each group is in order of total margin, as in the final.

Thus use your own seeds to test a change, and the rankings to compare your system with the other teams.

## The final

- The final is one secret level 8 world. It starts on Sunday 15 November at 08:00 Helsinki time. Each team plays its own copy, with the same requests, drivers and random draws. Thus a difference in score comes from a difference in decisions.
- The final picks the 10 ranked teams with the highest margin. They present how they built their systems, and a jury chooses the winners from them. If fewer than 10 teams are ranked, only the ranked teams present. Thus the final has one job: to put your team in the top 10.
- `client.final()` gives the id of your world and the start time. The organisers set the final at 07:30, and `client.final()` gets 404 before a final is set. It gives the last final also after that final has ended: check that its world has not ended. Start your system at any time before 08:00. With `--final`, the starter waits for the final when none is set, or when the last one has ended.
- The clock moves by itself. From 06:00 to 22:00, a weekday hour takes 18 real seconds, a Saturday hour 6 seconds and an hour on the last Monday 9 seconds. A night hour, a Sunday hour and every hour after the last Monday take 3 seconds. The final takes about 34 minutes. A price request gives you 15 world minutes: 4.5 real seconds in the day.
- `advance`, `fork` and `delete` are closed. `reveal` opens when the final ends.
- Read events from `world.stream()`. When the connection breaks, the stream connects again from the last event that it received, so it misses no event. When your system starts, it should rebuild what it knows from `world.snapshot()` and `world.events()`, as the starter does.
- When your code stops, the world continues. Your gigs keep their fee schedules, your drivers keep working, and requests go to the competitor. You can start your code again, change it, and act by hand.

## Limits

Each request on a practice world costs one token of your team's practice bucket, whatever it carries. The teams that practise share the server's practice budget equally: your bucket refills up to 1,000 tokens a second, about 170 when 60 teams practise, 50 when 200 do, and 20 while a final or a test final runs. A practice request over the limit waits in the server, up to a second, for its token. In the final, each action costs one token of a bucket that refills 20 a second. Each read costs one token of a bucket that refills 50 a second. API.md has the details. A final request over the limit gets 429 at once and costs no token. The clients read the `RateLimit` headers and wait for the next token, so they get almost no 429 answers.

A batch holds at most 50 actions. A team holds at most 8 practice worlds that have not ended, and at most 16 open streams. An ended world holds no slot and costs nothing: it stays in your list, and you can still read it and reveal it. The starter deletes its practice world when you stop it with Ctrl-C and when it stops on an error. To run a practice week faster, advance only to the events that you act on, and send the actions of each step with the next `advance`.

## The client

| Python | TypeScript | Route |
|---|---|---|
| `client = Client()` | `const client = new Client()` | Reads `SPREAD_URL` and `SPREAD_KEY` |
| `client.create(level=8, seed=None, board=False)` | `client.create(8, { seed, board })` | `POST /v1/worlds` |
| `client.worlds()` | `client.worlds()` | `GET /v1/worlds` |
| `client.world(world_id)` | `client.world(worldId)` | No request |
| `client.final()` | `client.final()` | `GET /v1/final` |
| `client.board(level)` | `client.board(level)` | `GET /v1/leaderboards/practice?level=` |
| `client.map()` | `client.map()` | `GET /v1/map` |
| `world.snapshot()` | `world.snapshot()` | `GET /v1/worlds/{id}` |
| `world.act(actions)` | `world.act(actions)` | `POST .../actions`, in batches of 50 |
| `world.advance(until=kinds, max=minute, actions=[...])` | `world.advance({ untilEvent: kinds, max: minute, actions })` | `POST .../advance` |
| `world.events(after=-1)` | `world.events(-1)` | `GET .../events` |
| `world.stream(after=None, idle=None)` | `world.stream({ after, idleMs })` | WebSocket `.../stream` |
| `world.roster()` | `world.roster()` | `GET .../drivers` |
| `world.fork()` | `world.fork()` | `POST .../fork` |
| `world.score()` | `world.score()` | `GET .../score` |
| `world.reveal()` | `world.reveal()` | `GET .../reveal` |
| `world.delete()` | `world.delete()` | `DELETE /v1/worlds/{id}` |

Answers are plain JSON objects with the field names of the API, for example `referenceFeeCents`. The types, from the API contract, are in `spread/types.py` and `typescript/types.ts`. All times are whole world minutes from Monday 00:00: minute 1440 is Tuesday 00:00. All money is in whole game-euro cents. The clients refuse a number with a fraction before they send it, with a `SpreadError` that names the field.

A failed request raises `SpreadError` with the reason. A request is `Unreachable`, a kind of `SpreadError`, in three cases. The server does not answer. A request gets no answer within 20 seconds. The server restarts, and its address answers 502, 503 or 504.

In the final, the starter then waits 5 seconds, rebuilds its books from the server and goes on. The server can restart during the final, and your system must survive that. An action that the world cannot do gives `{ "ok": false, "reason": ... }` in its result.

The actions:

| Action | Effect |
|---|---|
| `quote` | Gives a price for one or more options of a request. |
| `post` | Puts a gig in the drivers' app. |
| `offer` | Sends a direct offer of a gig to one driver. |
| `reprice` | Replaces the fee schedule of a gig that no driver has booked. |
| `withdraw` | Takes a gig that no driver has booked out of the app. |
| `release` | Ends the booking of a driver who has not left yet, for a release fee, or free after `driver_late`. The gig leaves the app. |

## Events

Each event has `seq`, `at`, the world minute, and `kind`. This table says when each kind comes and what your system can do then. The types files call an event `DeskEvent`.

| Event | When it comes | What your system can do |
|---|---|---|
| `price_request` | A dealer asks for a price. The request has the cars, the delivery options, the typical fee, maybe a note, and `respondBy`, 15 minutes later. | Quote a price for each option you want, before `respondBy`. To decline, quote far above the list price: a quote that loses costs nothing, and a request without a quote costs a little goodwill. |
| `order_won` | At `respondBy`, when the dealer took your price. The order has its deadline, its price and `lastPickupAt`. | Post a gig for its cars, or send a direct offer. |
| `order_lost` | At `respondBy`, when the dealer took the competitor's price. | Learn from it: your price was above what this dealer accepted on this lane today. |
| `gig_viewed` | A driver opened your gig: in the app, from a notification or from a direct offer. Most views come in the first minutes after you post a gig. Many viewers only browse. | Nothing. A view shows that a driver uses the app. It does not show the gig's cost to that driver. |
| `gig_booked` | A driver took the gig at the fee then in the app. A driver books when the fee pays for their time, their trips and what else their day is worth, and they do not expect more from waiting for a raise. A driver books a gig for a later day only when they know they will work that day. The first driver to book gets the gig. | Nothing. Until the driver leaves, you can release them, for a fee. |
| `offer_answered` | The answer to a direct offer: `accepted` inside its 10 minutes, `declined` or `expired` after them. | After `accepted` the gig is booked. Otherwise send an offer to another driver, or let the app find one. |
| `gig_started` | The booked driver has left for the gig. | Nothing. From now on you cannot release the driver. |
| `driver_late` | The booked driver cannot collect the first car inside its window, at a dealer site that is open when they arrive. `pickupAt` is the minute they will collect it. They still come. | Keep the driver, or release them without a fee until they leave, and find a driver who can come earlier. |
| `no_show` | The booked driver cannot collect the first car inside its window, at any other pickup, for example a private seller. They will not come. The gig is open again in the app. | As after `gig_dropped`. |
| `gig_dropped` | The booked driver gave the gig back. Something came up in their life, the gig cost them more than they first thought, or they took a gig that pays clearly more. The longer a driver holds a booking, the likelier a drop. Drops at night are rare. The gig is open again in the app, with its fee schedule. | Check that the fee schedule can still find a driver before the pickup. If not, raise it now. |
| `gig_expired` | No driver booked the gig before one of its stop windows ended. The gig has left the app. | Post the cars again. A stop without its own window then runs to the end of the world, so the car can still arrive, late. |
| `deadline_close` | The order has no driver 3 hours before `lastPickupAt`, the last minute to collect its first undelivered car and still deliver every car by its deadline. It comes again when the order loses its driver later, and when `lastPickupAt` moves later. | Raise the fee now, or send direct offers to drivers near the pickup. Drivers need time to see a gig and to get to the pickup, so a high fee shortly before the pickup usually finds nobody. |
| `release` | A car that the dealer held until payment, or until it was ready, can now be collected. | A pickup planned from this minute can now go ahead. |
| `picked_up` | The driver collected a car. | Nothing. |
| `visit_failed` | The driver could not collect a car. `reason` is `not_ready` (not ready or not paid for yet, or summer tyres in snow), `closed` (the site was closed), `licence` (a manual car and an automatic-only licence) or `no_plates` (the car needs plates and the gig did not fetch them). You pay half of the car's share of the fee, at least €25. In a gig with several cars, the driver continues with the rest. The failed car has no gig now. | Post the car again without the cause: a pickup after the ready time or inside the open hours, `withPlates`, or an audience with a manual licence. Most failed visits come from a note that the gig did not follow. |
| `delivered` | The driver delivered a car. A car left at a site that its stated hours show closed counts as delivered when the site next opens. | Nothing. The price comes into your account when the order's last car is delivered. |
| `order_changed` | The dealer sends a car to another place, or snow moves the deadline. It also comes when a private seller's slot ends with the car still there: the dealer books the same hours on the next business day. The world sends the car to the new place, also when a driver has booked or started the gig. | Check the new deadline and `lastPickupAt`. When the new place is farther, you pay the driver for the extra driving. |
| `order_cancelled` | The dealer cancelled the order. Its cars do not move from now on, and the order does not count. Each live gig that held one of its cars is `withdrawn`, `released`, `shortened` or `continues` (RULES.md). | Post again the other cars of a withdrawn gig. |
| `forecast` | From level 5, snow is coming to some places at some hours. | Expect slower drives there. Some drivers stay at home and drop bookings that must start that day. |
| `ledger` | A line in your account: revenue, a driver fee, a refund, a fine, a failed visit, waiting or a release fee. | Nothing. The sum of the lines is your score. |

## The map

`client.map()` gives the places of the world and the roads between them. It costs no token.

- `places`: `id`, `name`, `lat` and `lon`.
- `roads`: `from`, `to`, `km` and `minutes` by car, for every pair of places in both directions and from each place to itself.

The 12-hour rule and `lastPickupAt` use these minutes. The real driving time of a gig varies around them, and snow makes it longer. Public transport times are not published.

## How the world works

The world is a model of a dispatcher's week. Its rules are public. Its values are hidden. This section gives the mechanisms in words.

### Drivers have homes and days

Each driver lives in a town. Each morning, each driver decides whether to work that day, when to be free, how many gigs to take, how late to get home, and the longest gig to accept. A driver with a day job is free only in the evening. A weekend driver works only on Saturday. A driver who does not work today does not book.

### The cost of a gig depends on where the driver is

For a driver, a gig costs:

- the time that they drive the car;
- the trip from where they are to the pickup, by public transport, with a ticket bought ahead, when it is cheap;
- the trip home after the delivery, with a ticket bought on the day: they do not know when the delivery ends, so it costs more;
- extras: evening, weekend and private-address work, trade-ins, charging stops, plates, and work that they dislike.

Time on public transport costs a driver much less than time at the wheel. Small towns have a last connection in the evening. After it, the trip home is expensive. Thus one car can be cheap for one driver and expensive for another. A gig that ends near a driver's home, or near their next pickup, is cheap for that driver. For a driver who already has one of your gigs, a second gig costs only the extra time and tickets. Thus chains of gigs form without your help.

A driver goes as far as the fee pays. Distance has no limit of its own: it costs the driver the trip to the car, the trip home and the hours. A driver who cannot reach a far morning pickup on its day can take the train or bus the evening before and stay the night, which costs them the evening and the room. A car far from any driver needs a fee that pays for the whole trip, and a long gig draws drivers from further away than a short one. Drivers follow the gigs near where they are, so a gig far away gets fewer views, unless its fee could pay them.

You see the position of a driver while that driver works one of your gigs. Thus you know where your drivers finish. You do not see other drivers.

### The typical fee

The typical fee (`referenceFeeCents`) is about what a driver is usually paid for the work in the drivers' app. The world starts from the work's cost to a typical driver who lives in the pickup town. That cost is the drive, the trip to the pickup and the trip home. The world scales that cost by one factor, the same on every lane, and adds a noise for each request.

Up to level 6, most gigs that open at the typical fee book at that fee. At levels 7 and 8, a gig at the typical fee often finds no driver (see [The shortage](#the-shortage)). The typical fee is not what the cheapest driver needs. A driver who lives near the delivery place, or who is near the pickup after another gig, needs much less.

### Drivers see a gig, then decide

A driver sees a gig in the app, in a notification, or in your direct offer. Most views come in the first minutes after you post a gig. Each view is a `gig_viewed` event with the driver's id. Drivers also open gigs far from where they are, out of curiosity, and do not book them from there. Thus many of a gig's viewers only browse. A view shows that a driver uses the app, not that the gig suits them, and it does not show the gig's cost to that driver.

A driver with notifications on hears of a new gig when its first pickup is within 30 km of their home, in a straight line. The app does not notify a driver where your gigs have taken them. A driver hears of each order once, however often you post it again. A gig that comes back in the app after a drop or a no-show notifies again. A raise does not notify: a driver sees the new fee when they next open the app. No driver reads a notification from 23:00 to 07:00.

A driver books a gig when the fee minus the cost is more than their other options that day, and more than they expect to get when they wait. A driver books a gig for a later day only when they know they will work that day. The first driver to book gets the gig.

### Drivers learn your fees

From level 2, each driver keeps their own belief about your fees. When a driver books a gig, each driver who viewed a gig of that order and could have done it learns two numbers. The first is the booked fee over the first fee that a driver viewed for the order. The second is the minutes from that first view to the booking. The belief is a running mean over the orders that the driver learned from, and old orders fade. A driver who never viewed a gig of yours that they could do keeps the belief of a new driver.

A driver who expects a raise before another driver books the gig can wait for it. A raise sends no notification. A driver who works that day and waits for a raise plans one more look at the gig, at the time they expect it to pay them enough. Close to the pickup they expect another driver to book the gig soon, so they wait less for a raise. The longer a gig stays on the board, the less they expect another driver to book it, so they wait more. A gig keeps its age when you post its car again. Thus a system that always raises its fees teaches drivers to wait. A repost at a higher fee counts as a raise, because the order keeps its first viewed fee. An order that no driver books teaches nothing.

### Drivers drop bookings and run late

A driver can drop a booking for as long as they hold it: something comes up in their life, or a better gig at the same time. The longer a driver holds a booking, the likelier a drop, and drops at night are rare. A driver books on a quick look and plans the gig properly soon after, at the latest the next morning. If the gig then costs them more than its fee pays, they can give it back. Thus a fee that only just pays a driver gets more drops. A driver who gave a gig back books it again only for a higher fee. A driver drops for a better gig only when it pays clearly more than their word is worth to them, so a reliable driver almost never does. You get `gig_dropped`, and the gig is open again.

A driver plans each booking from where they will be: where their earlier gig ends, or at home for a gig on a later day. They plan to reach each stop inside its window and when its site's stated hours show it open, so they leave later rather than wait at a closed site. When their day changes, for example because an earlier gig runs late, the driver plans again. A driver who can no longer collect the first car inside its window tells you at once. At a dealer site that is open when they get there, you get `driver_late` with `pickupAt`. They still come, and you can release them without a fee until they leave. Elsewhere, for example at a private seller who is home only in the slot, you get `no_show`, and the gig is open again.

### Direct offers

A direct offer sends one gig to one driver for 10 minutes, at a fee that you choose. It reaches a driver whom a notification does not, for example a driver who finishes one of your gigs far from home. A driver who reads it takes it when the fee pays for the gig and what else their day is worth to them. In the app the same driver may hold out for more, because the fee may rise there, though another driver may take the gig first. Thus an offer saves most on gigs whose fee drivers expect to rise. Not every driver works every day: a driver who does not work today reads few offers, and you cannot see who works. The rules give the limits and the answers. The answers are noisy, because a driver's other options change from day to day. Thus an offer does not show the exact cost of a gig to a driver.

### Dealers compare prices

For each option that you quote, the probability that the dealer accepts your price falls when your price goes up against the competitor's price. Above the competitor's price, it falls quickly. When you quote several options, the dealer compares the price of each option with the value of its speed. Dealers differ in how much price matters to them and in the value of fast delivery to them.

The competitor's price for an option is the list price, before its rounding to whole euros, times four factors:

- A lane factor, by pickup place and delivery place. One part of it follows how hard the lane is for drivers. It is higher where little work comes back and where few drivers live near the delivery place. This part is almost the same in every world. The other part is new in each world.
- A day factor, new for each day of each world: how busy the competitor's own drivers are.
- The world's price level, from 10% below to 10% above the middle level, the list price. At level 7 it is from 12% to 38% above the list price, and at level 8 from 26% to 54%.
- A small factor for the request alone.

The competitor's prices also move during the week, and they answer what you win:

- Some mornings, its prices for cars leaving a place, or arriving there, move a step up or down. Once a week its whole price list changes shape, so short and long lanes move in opposite directions. These moves do not change its prices on average.
- Each morning it looks at each dealer's recent requests. Where you won most of them, it lowers its prices for that dealer. Where you won few, it raises them. It moves a few steps at most, and it learns only whether it won each request.

Each team plays its own copy of the competitor, so it answers only your wins. Thus a table of the competitor's prices that you learn in practice goes out of date during the week. A system that keeps learning from its own wins and losses keeps its margin, and one that takes almost every car of a dealer makes the competitor cut its prices for that dealer.

No reveal gives the competitor's price. You learn about it from your own quotes, which win or lose. Thus your quotes over several worlds show the part of each lane that holds in every world. Your wins and losses in the first hours of a week show the parts that are new. The competitor knows nothing about your drivers, your other orders or your timing. That is your advantage.

Up to level 6, when you quote the list price on all requests, you win mostly the requests where the competitor is expensive. These are often the difficult lanes. At levels 7 and 8 the list price wins most requests.

A request is good when the car is cheap for you to move compared with the competitor's price. Signs of a good request:

- it goes towards towns where drivers live;
- it pairs with an order that you have or expect: a return leg, or a second car from the same yard;
- it starts in a busy area at an hour when many drivers are free;
- the dealer accepts a later option that you can put in a gig with other cars.

A request is bad when it ends in a town with no traffic back, needs a long trip to the pickup, falls at an hour when few drivers work, or has a note that adds risk. To win all requests is a mistake for two reasons. The bad orders lose money. The extra orders also use your well-placed drivers, so your good orders cost more.

Late and undelivered orders lower the satisfaction of the dealer, and each order delivered on time raises it a little. A request that you do not quote also lowers it slightly; a quote that loses does not. A dissatisfied dealer accepts only lower prices from you. Satisfaction does not change the requests: all teams get the same requests.

At level 8 each dealer also counts your orders on time, and accepts higher prices from you for them (see [Dealer trust](#dealer-trust)).

### Notes change what happens

Many requests have a note from the dealer, in Finnish, Swedish or English. Most notes are harmless. Some notes change when the car can be collected, when it must arrive, or what the driver must bring or be allowed to drive. The world does what the note says, whether or not your system reads it. The examples below are made up.

| The note says | When you ignore it | What to do |
|---|---|---|
| Pickup hours: "Nouto arkisin ennen klo 14." | The driver arrives after closing time. The visit fails as `closed`. | Plan the pickup inside the hours. |
| The car is ready later: "Klar först på onsdag kl 12." | The driver arrives too early. The visit fails as `not_ready`, and you pay for it. | Plan the pickup from the ready time. A delay of a day or more moves the order's deadline by the same days, and `order_won` already shows the moved deadline. |
| Handover after payment: "Maksun jälkeen, ei ennen klo 13.30." | The driver arrives before the handover. The visit fails as `not_ready`, and you pay for it. | Plan the pickup from that time. A `release` event comes then. |
| A tighter deadline: "Must be at destination by tomorrow 17:00." | The car is on time by the request but late by the note. The note counts. | Plan for the deadline in the note, or quote high. |
| Delivery hours: "Vastaanotto klo 16 asti." | The driver plans by the stated hours, arrives after the real closing time and waits, at most 2 hours at €15 an hour. Then the driver leaves the car, and the delivery counts at the site's next opening time. | Plan the delivery inside the hours. |
| A driver requirement: "Manuaalivaihteisto." | A driver with an automatic-only licence cannot drive the car. The visit fails as `licence`. | Offer the gig only to drivers with a manual licence. The driver list shows the licence. |
| Plates: "Car has no plates." | The driver has no plates. The visit fails as `no_plates`. | Post the pickup with `withPlates`. |
| Summer tyres: "Auto vielä kesärenkailla." | While snow falls at its pickup or delivery place, the car cannot be collected. The visit fails as `not_ready`. | Collect it outside the snow. When snow is forecast there before the deadline, the dealer moves the deadline by one day, in `order_changed`. |
| A change of plan, in `order_changed`: "Suunnitelma muuttui: auto toimitetaan kohteeseen Pori, ei Turku." | Nothing. The world sends the car to the new place, also when a driver has booked or started the gig. | Nothing. When the new place is farther, you pay the driver for the extra driving. |

Notes are written like real dealer notes: in paraphrase, with abbreviations and typos, sometimes in two languages, with corrections, several facts, or none. The rules say how days and times read: a weekday name is 2 to 6 days after the request arrives, and an hour in words is a working hour. A reader of the common phrasings reads most notes but not all. The reveal says what the note of each order that you won meant, so you can measure your reader. The starter reads only clock times near a few keywords. A system that reads notes well makes more money.

### Weather and market conditions

From level 5, a `forecast` event can announce snow for some places and hours. Snow makes driving slower there, and some drivers stay at home.

Each world has its own conditions. Up to level 6, it has from 20% fewer to 20% more drivers than an average world. Levels 7 and 8 have far fewer (see [The shortage](#the-shortage)). Each world also has its own competitor price level and day factors, and some busy corridors. A system tuned to one practice world loses margin on a different world. A system that measures the market in the first hours of the week, and adapts, keeps its margin.

### The shortage

Levels 7 and 8 are weeks with too few drivers. The competitor has the same problem: it pays its own drivers more, so its prices are higher.

- **Level 7.** The world has from 26% to 40% of the drivers of an average world. The competitor's price level is from 12% to 38% above the list price. Every week has the outage, the flu and the bulk order of level 6.
- **Level 8.** The world has the same number of drivers as level 7. It multiplies each town's share of the drivers by a factor of its own, new in every world. Some towns keep most of their drivers, and some lose nearly all. The competitor's price level is from 26% to 54% above the list price.
- **The price war of level 8.** Every week has the three events of level 6 and a price war. On one working day without an outage, the competitor quotes 20% under its usual prices everywhere, from 08:00 to 18:00. Your usual prices then lose. The next day its prices are back.

What a shortage means for your system:

- The list price and the typical fee do not change. They are those of a normal week. A price at the list price wins most cars, and a fee at the typical fee often finds no driver.
- You can win more cars than drivers can move on time. Each late car costs a refund and counts against the delivery minimum. A system that wins every car it can is not ranked.
- A driver is easier to find for some cars than for others. It depends on where drivers live, when the car is ready, how late its deadline is, and where your own drivers are that day.
- The driver list shows how many drivers the world has and where they live. It does not show who works on a day. Your views, bookings and drops show that during the week.
- The flu and the bulk order cost more when drivers are few. A dealer group's 30 to 40 cars from one yard need 30 to 40 trips from that yard.

On the seed `spread-1`, the kit's starter loses €50,442.49 at level 7 and €72,612.51 at level 8. It is not ranked at either level. On 20 weeks, House best is ranked on all 20 at level 7 and on all 20 at level 8. House best is the best system we built, and it plays every ranking round.

### Dealer trust

At level 8, a dealer accepts higher prices from a team that delivers its cars on time.

- Each dealer counts the orders that you delivered to it on time, up to 25.
- Each counted order raises the prices that the dealer accepts from you by 1%. At 25 orders the dealer accepts prices 25% higher from you.
- An order that arrives late halves that dealer's count, rounded down. Thus a late order costs its refund and a part of that dealer's trust.
- The count starts at zero in every world.
- You can count it from your own account. An order's revenue line with no late refund is an order on time.
- The dealer still compares your price with the competitor's price. A late or undelivered car still makes its dealer accept only lower prices from you, as at the other levels.

## Research

A practice week teaches what a dispatcher learns by working it. Play a week with your system, then reveal it. The reveal (API.md) shows:

- each price request, with your quote, whether you won it, and the option that the dealer took;
- for each order that you won, what was true of each car: when it was ready, the real hours of both sites, what its note meant, and when a driver collected and delivered it;
- each view of your gigs, with the fee that the driver saw.

It does not show the competitor's prices or what a gig cost a driver. You learn about them as a dispatcher does: from the quotes that win and lose, and from the fees at which drivers book, wait or drop. Thus a week teaches you about the requests that you quote and the gigs that you post. A week that you play without quotes shows only its requests.

The kit's script writes a world and its reveal to CSV files in the folder `reveal-<world id>`:

```sh
spread-reveal-csv <world id>         # a world that has ended, for example one that your system played
spread-reveal-csv --seed study-1     # a new world, run to its end with no actions: its requests only
bun reveal-csv.ts <world id>         # the same in TypeScript, in the folder typescript
```

| File | One row for each | Columns |
|---|---|---|
| `requests.csv` | price request | the request; `outcome` (`won`, `lost` or `unquoted`) and the `option` that the dealer took; each option's deadline, list price and your quote |
| `legs.csv` | leg of a request | the car; both places with windows and stated hours; for an order that you won, the truth: ready time, release, real hours, gearbox, plates, tyres, the note's deadline, and when the car was collected and delivered |
| `drivers.csv` | driver | the driver list: home, rating, licence, completed gigs and drops |
| `views.csv` | view of one of your gigs | minute, gig, driver, the fee that the driver saw |
| `gigs.csv` | gig that your system posted | stops, which drivers may take it, fee schedule, status, fee, driver, orders |
| `events.csv` | event other than `price_request` | its fields: wins, bookings, drops, pickups, failed visits, deliveries, money in and out |
| `places.csv`, `roads.csv` | place; pair of places | the map |

A nested field has a dotted column name, for example `pickup.window.from`, and a list is one column of JSON. Questions to start with: At what price against the list price do your quotes start to lose, by lane and by day? At what fee, and how long after you post, do the gigs of a lane get booked? Which notes did your reader get wrong?
