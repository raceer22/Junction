# Spread — Finland Fleet Dispatcher

A dispatcher that quotes car dealers, hires freelance drivers, and moves cars across Finland for one simulated week. To be ranked, at least 90% of the orders we win must arrive on time; the final picks the top-ranked teams by gross margin, and a jury chooses the winners.

This is our version of the starter dispatcher, rewritten to bid more selectively and manage drivers more carefully.

## What it does

- **Quotes dealers.** For every price request it looks at all delivery options, checks the deadline is reachable with a safety buffer, skips risky notes (manual gearbox, missing plates, call-before-pickup, private sellers, closing hours), and only quotes when the price covers the expected driver cost. It learns from each dealer over time and adjusts quotes accordingly.
- **Moves the cars.** Once we win an order it posts a gig in the drivers' app straight away, sets the right driver audience and vehicle requirements (plates, charging, experienced drivers), and raises the fee over time as the deadline gets closer.
- **Adapts day by day.** It tracks how many orders arrived on time each day. If on-time falls below 90% it becomes more expensive and selective; if it is comfortably above 90% it relaxes to win more volume. This self-regulation is the core of the system and keeps us above the hard 90% on-time rule while protecting margin.

## What we changed from the starter

| | Starter | Our version |
|---|---|---|
| Quoting | First option only, at list price | Every option, only when safe and profitable |
| Price | List price | List price × adaptive dealer multiplier |
| Safety | No deadline check | Deadline slack buffer per level |
| Driver fee | Reference fee | Dynamic opening fee, rises near deadline |
| Risky notes | Not filtered | Skipped at shortage levels |
| Adaptation | None | Learns per dealer and per day |

## Running

```sh
source .venv/bin/activate
export SPREAD_URL=https://spread.bahnexpress.com
export SPREAD_KEY=<your team key>

python dispatch.py --level 8 --seed spread-1
```

- `--level 1..8`: practice level (default 8, the final's level)
- `--seed <text>`: same seed gives the same week — use it to compare versions
- `--custom`: override the starting constants from `custom_args.py`
- `--board`: next ranking run (counts in the practice rankings)
- `--final`: plays your copy of the live final from the stream

After a run, `spread-reveal-csv <world id>` generates post-mortem CSVs to inspect quotes, gigs, and deliveries.

## Website

`website/` is a local dashboard ("Trans-Nordic Dispatch") that lets you tune the dispatcher's constants through a browser and run simulations without the command line. It shows a map of Finnish hubs, a timeline scrubber, live KPIs (margin, revenue, on-time rate), and a form to save custom constants and start a simulation.

Run it with:

```sh
python3 website/server.py
```

then open `http://localhost:8000/`.

## Analysing results

`analysing/` contains scripts to read the post-mortem CSVs: margin per order, quote strategy, lost quotes, cost breakdown, and delivery timing.

## Notes

`notes/` holds rough ideas and the agreed split of work between the quoting part and the dispatch part. `notes/docs/` has the polished challenge docs (handbook, rules, API reference).

## Environment

- Python 3.9+ (3.12.3 on this machine)
- `spread` 0.1.0, no other packages
- Set `SPREAD_URL` and `SPREAD_KEY` before running against the server
