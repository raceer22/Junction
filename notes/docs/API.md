# Spread: HTTP API, version 0

This page lists the routes and their meaning. A world is one simulated week. The server serves the contract, in OpenAPI 3.1, at `GET /openapi.json`. All times are whole world minutes, and all money is whole game-euro cents. A number with a fraction gets 400.

Every request carries `Authorization: Bearer <team key>`, except the map, the leaderboards, `/openapi.json` and the kit.

The server also serves, without a key, the kit at `/kit/` (`spread-0.1.0-py3-none-any.whl`, `spread-typescript.tar.gz`, `HANDBOOK.md`, `RULES.md` and `API.md`; `/kit/` lists the files), the run viewer at `/viewer/` and the big screen at `/screen/`.

## Practice worlds

| Method and path | Body | Returns |
|---|---|---|
| `GET /v1/worlds` | | The team's worlds: `{ worldId, kind, level, parent, season, boardRun, ended }[]`. `kind` is `practice` or `final`; `parent` is the world that a copy (`fork`) came from. For a ranking run, `season` is its ranking round and `boardRun` is the world of the round that it plays, 1 to 5; both are null for other worlds. |
| `POST /v1/worlds` | `{ seed?: string, level: 1-8, board?: boolean }` | 201 `{ worldId }`. A random seed when none is given. With `board: true`, the team's next ranking run: the next world of the current ranking round (see [Practice rankings](#practice-rankings)). |
| `GET /v1/worlds/{worldId}` | | `DeskSnapshot` |
| `DELETE /v1/worlds/{worldId}` | | 204. Deletes a practice world and frees its slot. A ranking run that has not ended runs to its end first. |
| `POST /v1/worlds/{worldId}/actions` | `Action[]`, 1 to 50 | `ActionResult[]`, in the same order |
| `POST /v1/worlds/{worldId}/advance` | `{ to: minute }` or `{ by: minutes }` or `{ untilEvent: DeskEventKind[], max: minute }`, each with an optional `actions: Action[]`, 1 to 50 | `{ results?: ActionResult[], now, events: DeskEvent[] }` |
| `GET /v1/worlds/{worldId}/events?after=seq&limit=n` | | `DeskEvent[]` with a sequence number above `after`, oldest first. `after` is -1 when absent: every event. `limit` is optional. |
| `GET /v1/worlds/{worldId}/drivers` | | `DriverProfile[]`: the driver list. It can be up to 5 seconds old while the world moves. |
| `POST /v1/worlds/{worldId}/fork` | | 201 `{ worldId }`: a copy at the same minute with the same future draws. 403 for a ranking run. |
| `GET /v1/worlds/{worldId}/score` | | `Score` |
| `GET /v1/worlds/{worldId}/reveal` | | `Reveal` (see [The reveal](#the-reveal)), after the world has ended; 409 before |
| `GET /v1/worlds/{worldId}/stream?after=seq` | WebSocket | One `DeskEvent` JSON message per event, as it happens |

`advance` moves the clock. `{ untilEvent: ["price_request"], max }` stops at the next price request or at `max`, whichever comes first. The clock never passes the world's end.

An `advance` with `actions` applies the actions first, at the current minute, then moves the clock, in one request. The answer carries the actions' `results` in their order. A system that sends the actions for each stop of the clock with its next `advance` needs one request for each stop. The starters do this. A level 6 week takes them 1,000 to 1,450 requests, and the level 8 week of the seed `spread-1` takes 1,891.

A team's practice limit is 1,000 requests a second when few teams practise. It is about 170 a second when 60 teams practise at once, and 50 when 200 do (see [Limits](#limits)). At those limits, that level 8 week takes about 2 seconds on a quiet server. It takes about 11 seconds when 60 teams practise, and about 38 seconds when 200 do. While a final or a test final runs, the limit is 20 a second, and that week takes about 95 seconds. Each request also takes the network's round trip: at 20 ms, a week of 1,250 requests takes at least 25 seconds.

A practice world is deterministic. The same seed, level and actions, at the same minutes, give the same events and the same score. A copy made with `fork` has the same future draws as its parent.

The `advance` answer holds every event published since the previous `advance` answer on that world, of every kind, including events that actions caused. After a server restart, an answer can repeat events that a client has already received; drop repeats by `seq`. The first `advance` on a copy answers with the copy's whole history.

A team holds at most 8 practice worlds that have not ended. A copy counts as one. Delete a world to free its slot. An ended world holds no slot: it stays in the list, and you can still read it and its reveal.

## The map

`GET /v1/map`, no key: `{ places: [{ id, name, lat, lon }], roads: [{ from, to, km, minutes }] }`. `roads` holds every pair of places in both directions, and each place to itself. Public transport is not published.

## Practice rankings

A ranking run is a practice world on a seed that the server chooses. Teams compare their systems on ranking runs. The API calls a ranking round a season, and a ranking run a board run: `board: true`, `boardRun`, `BoardSeason`.

- A ranking round is five secret worlds at each level. They are the same five worlds for every team. The organisers start each round and give it a name, for example "Saturday 15:00". A new round has new worlds.
- `POST /v1/worlds { level, board: true }` creates the team's next ranking run at that level. The n-th ranking run of a team in a round plays world n of the round at that level. A body with `board: true` and a `seed` gets 400.
- A team plays the round's worlds in order, one at a time, and each world once. Until the current ranking run at a level ends or is deleted, `board: true` at that level gets 409. After the fifth run, `board: true` at that level gets 409 until the next round starts. Before the first round, `board: true` gets 409.
- No team can play a round's world outside its ranking run, because its seed is secret.
- A ranking run counts in the round in which the team created it. A run that has not ended when a new round starts counts in its own round when it ends.
- A ranking run uses a practice slot. `advance`, `actions` and the reads work as on any practice world. `fork` gets 403, because a copy lets a team play the same week twice. `reveal` gets 403, also after the run has ended, because every team plays the same worlds.
- `DELETE` on a ranking run that has not ended does not remove the run from the practice rankings. The world first runs to its end without your system, as the final does when a system stops: gigs keep their fee schedules, booked drivers keep working, and requests go to the competitor. The run counts with its score at the end, and the rankings show the minute of the deletion. Thus a deletion does not remove a bad week, and does not avoid the late refunds and penalties of the orders that you hold. The 204 comes when the world has ended, in less than a second.

`GET /v1/leaderboards/practice?level=n`, no key, shows the practice rankings of level n:

```
{ level,
  current:  BoardSeason | null,
  previous: BoardSeason | null }

BoardSeason: { season, name, startedAt,
               teams: [{ rank, team, name, played, ranked, marginCents, onTimeShare,
                         runs: [{ run, marginCents, onTimeShare, wonOrders, ranked, deletedAt }] }] }
```

- `current` is the round that teams play now. `previous` is the round before it. Each is null when there is no such round. `season` is the number of the round, from 1. `startedAt` is the wall-clock time at which the round started, in ISO 8601.
- `teams` holds every team. `played` is the number of the round's runs at level n that the team has ended, deleted runs included: 0 to 5.
- `runs` holds these runs in the order of the worlds. `run` is the number of the world. `wonOrders` is the number of orders that the run won, cancelled orders excluded. `ranked` on a run tells whether its score meets the delivery minimum: at least one won order, and at least 90% of the won orders on time. `deletedAt` is the minute at which the team deleted the run, else null.
- When a team has played all five worlds, `marginCents` is the sum of the five margins. `onTimeShare` is the share of all orders of the five runs that arrived on time. Before the fifth run ends, both are null and `rank` is null.
- `ranked` on a team is true when the team has played all five worlds and all five runs meet the delivery minimum. The teams that have played all five worlds come first: the ranked teams by margin, then the teams with a run that misses the minimum, by margin, as in the final. The teams that have not played all five worlds come last, by `played`.
- `rank` is the team's place among the ranked teams. Teams with the same margin to the cent share a place. A team that is not ranked has no place. A house team has no place either: its id starts with `house-`, and it is one of the organisers' own systems.
- The ranking of `previous` is final when the last runs of that round have ended.
- The practice rankings are at most 2 seconds old.

## The reveal

`GET /v1/worlds/{worldId}/reveal` answers when a practice world has ended. It never answers for a ranking run. In the final, it answers when the final has ended.

A reveal shows only what a dispatcher learns by doing the week's work:

- each price request, with your last quote and what the dealer did with it;
- for each order that you won, what was true of each car, and when a driver collected and delivered it;
- each view of your gigs, with the fee that the driver saw.

It does not show the competitor's prices, the costs or other values of a driver, or anything about a request that you did not win beyond what the request said. A week that you play without quotes shows only its requests.

| Field | One item for each | Fields of an item |
|---|---|---|
| `requests` | price request of the week | `request`, as in `price_request`. `quote`: your last price for each option that you quoted; null when you sent no quote. `outcome`: `won`, `lost` or `unquoted`. `option`: the option that the dealer took; null unless you won. |
| `orders` | order that you won, cancelled orders included | `orderId`. `legs`: for each car, `readyAt`, `releasedAt`, `pickupHours`, `deliveryHours`, `manualGearbox`, `needsPlates`, `summerTyres`, `noteDeadline`, `pickedUpAt` and `deliveredAt`. |
| `views` | view of one of your gigs | `gigId`, `driverId`, `at`, and `feeCents`: the fee that the driver saw. |

The facts of a car, as the world acted on them:

| Field of a leg | Meaning |
|---|---|
| `readyAt` | When the car was really ready. A visit before it failed as `not_ready`. |
| `releasedAt` | When a car that waited for payment was released; null when it did not wait. |
| `pickupHours`, `deliveryHours` | The real opening hours of the pickup site and of the delivery site, which a note can say differ from the stated ones; null at a private address. |
| `manualGearbox` | Only a driver with a manual licence could collect the car. |
| `needsPlates` | The car had no plates: a pickup without `withPlates` failed. |
| `summerTyres` | The car could not be driven in snow at either end. |
| `noteDeadline` | A tighter deadline from the note; null when the note set none. |
| `pickedUpAt` | When a driver collected the car; null when no driver did. |
| `deliveredAt` | When the delivery counted: the driver's arrival, or the site's next opening when the driver left the car at a closed site; null when the car was not delivered. |

Below level 5, the world does what each request says, so each car shows the facts that its request stated. Your bookings, drops, no-shows, failed visits and money are in the events (`GET /events`); the reveal does not repeat them. The kit types the reveal: `Reveal`, with `RevealedRequest`, `RevealedOrder`, `LegTruth` and `View`. The kit's script `reveal-csv` writes it to CSV files, with the events and the map (HANDBOOK.md).

## The stream

`GET /v1/worlds/{worldId}/stream` is a WebSocket with the same `Authorization` header. Without `after`, it sends the events published from the moment it opens. With `after=seq`, it first sends every event with a sequence number above `seq`, then the new ones, in order and without repeats. A client that reconnects with the last `seq` it received misses nothing. A stream that falls more than 4 MB behind is closed with code 1013; reconnect with `after`. A team holds at most 16 open streams.

## The final

| Method and path | Returns |
|---|---|
| `GET /v1/final` | `{ worldId, startsAt (wall clock, ISO 8601), now }` for the team's copy of the final world. 404 before a final is set. It gives the last final also after that final has ended: check that its world has not ended. |
| `GET /v1/leaderboard` | Server-sent events, no key: every 2 seconds, `{ status, now, endsAt, teams: [{ rank, team, name, marginCents, revenueCents, marginShare, wonOrders, onTimeShare, undeliveredShare, ranked }] }`. `status` is `none` before a final is set. It is `scheduled` from then until the start: the feed then holds `startsAt` and no teams. It is `running` during the final and `ended` after it. Ranked teams come first, then by margin. `rank` follows the same rule as in the practice rankings: no place for a team that is not ranked or for a house team. |

The final world uses the same routes, except `advance`, `fork` and `DELETE`, which return 403. `reveal` returns 403 until the final has ended. Its clock moves by itself: 18 real seconds per hour from 06:00 to 22:00 on weekdays, 6 seconds per hour from 06:00 to 22:00 on Saturday, 9 seconds per hour from 06:00 to 22:00 on the last Monday, and 3 seconds per hour at night, on Sunday, and from the last Monday 22:00 to the end, on Wednesday 22:00. The final takes about 34 minutes. Use the WebSocket stream; reconnect with `after=`, or call `GET /events?after=`, to fill any gap.

## Limits

Each team key has three token buckets. A practice world and a final world never share one.

| Bucket | Refills | Holds at most | Costs |
|---|---|---|---|
| Practice | The server's practice budget, 10,000 a second, in equal shares for the teams that sent a practice request in the last 5 seconds, at most 1,000 for a team; 20 while a final or a test final runs | One second of tokens | One token for each request on a practice world, whatever it carries: a batch of 50 actions, an `advance` with actions, a read, a `fork`, a stream opened. Creating and listing worlds cost one too. |
| Final actions | 20 a second | 50 | One token for each action in an `actions` batch on a final world |
| Final reads | 50 a second | 50 | One token for each `GET` on a final world, `GET /v1/final`, and each stream opened on a final world |

Thus a team's practice rate changes with the number of teams that practise: 1,000 a second for up to 10 teams, about 170 for 60, 50 for 200. `RateLimit-Limit` gives it on every answer. Systems on one key share the team's bucket.

A practice request over the limit waits in the server for its token, up to 1 second, and then runs. Thus two systems on one key take turns. A practice request that would wait longer gets 429 and takes no tokens. A final request never waits: over the limit, it gets 429 at once and takes no tokens. A full batch of 50 actions goes through in the final when the bucket is full. A request on a world the key may not use costs one practice token. The map, the leaderboards, `/openapi.json` and the kit cost nothing.

Each answer to a request that a bucket charged, a 429 included, gives the state of that bucket when the answer leaves the server:

| Header | Value |
|---|---|
| `RateLimit-Policy` | The bucket, as `"practice";q=166;w=1`: `practice`, `final-actions` or `final-reads`, then the tokens a second |
| `RateLimit-Limit` | The tokens that the bucket gains each second |
| `RateLimit-Remaining` | The whole tokens that the bucket holds; 0 while requests wait for tokens |
| `RateLimit-Reset` | The milliseconds until `RateLimit-Remaining` grows by one; 0 when the bucket is full |

When `RateLimit-Remaining` is less than the cost of the next request, wait `RateLimit-Reset` milliseconds, plus the time of one token for each further token that the request needs. A client that does this gets no 429. The kit clients do this.

## Errors

Errors are `{ error, field? }`.

- 400: the body or query does not parse. `field` names the field, for example `[2].prices.next_day` for the third action.
- 401: no key, or a key the server does not know.
- 403: the key may not act on this world, the route is closed in the final, or the world is a ranking run and the route is `fork` or `reveal`.
- 404: no world with this id, or no final for this team.
- 409: the request conflicts with the world or the team's limits: 8 practice worlds already, 16 streams already, `reveal` before the end, or a ranking run that cannot start: no ranking round has started, the team's ranking run at the level has not ended, or the team has played the round's five worlds at the level.
- 429: over the request limit. `Retry-After` is in milliseconds. The `RateLimit` headers (see [Limits](#limits)) let a client wait instead.
- 502, 503 or 504, without the JSON body: the server is restarting. It answers again in under a minute, and every world goes on from where it was. Send the request again, and read the world's state before you act.
- An action that parses but cannot be done returns `{ ok: false, reason }` inside a 200. Reasons never depend on hidden facts. For example, a `release` after `gig_started` gives `"Gig g12 has started: a driver can be released only before they leave"`.
