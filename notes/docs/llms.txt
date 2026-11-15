# Spread

> A Bahn challenge. Build a system that does a dispatcher's job for one simulated week in Finland. It quotes car dealers and pays freelance drivers to move the cars. At least 90% of the orders it wins must arrive on time. The final picks the 10 ranked teams with the highest gross margin. A jury chooses the winners from these 10.

The documents are plain Markdown. Where they differ, RULES.md is correct.

## Documents

- [Handbook](https://spread.bahnexpress.com/kit/HANDBOOK.md): the world, the API and the kits. Read it first.
- [Rules](https://spread.bahnexpress.com/kit/RULES.md): the full rules of the challenge.
- [API reference](https://spread.bahnexpress.com/kit/API.md): every route, event and limit.
- [OpenAPI](https://spread.bahnexpress.com/openapi.json): the API as a machine-readable contract.

## Start

- Set SPREAD_URL=https://spread.bahnexpress.com and SPREAD_KEY to the team key from https://spread.bahnexpress.com/join.
- The TypeScript kit, for Bun or Node 22.18 or later: https://spread.bahnexpress.com/kit/spread-typescript.tar.gz. Run `bun starter.ts`: it plays a whole practice week.
- The Python kit, for Python 3.9 or later: the wheel in https://spread.bahnexpress.com/kit/. Run `spread-starter`.

## Pages for people

- https://spread.bahnexpress.com/how: the world in plain words.
- https://spread.bahnexpress.com/judging: the weekend, the final, what the jury looks for and what to submit.

## Team

- https://spread.bahnexpress.com/team: the team's practice weeks, ranking rounds, place in the final and submission hash.
- https://spread.bahnexpress.com/leaderboard: the live final and the ranking rounds.
