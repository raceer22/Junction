# Spread: rules

13 November 2026. All amounts are game euros.

## The game

Your team builds a system that does the work of a car transport dispatcher in Finland for one simulated week. From Monday to Friday, car dealers ask you for prices. Each request is for one car, or for two in a trade-in. Several requests often arrive at once. Each dealer also asks a competitor. If your price is good enough, you win the car.

Every car you win has a deadline, and most cars must be delivered on the day they are ready. To move a car, you post a gig to freelance drivers. You choose the fee, how it rises over time, and which cars to put in one gig. Drivers decide for themselves, depending on where they are and what suits them. They may also drop a booking.

If a car is late, the dealer gets money back. If no driver takes a car, only a higher fee, in time, finds one.

Your score is your gross margin in euros. If fewer than 90% of the orders you win arrive on time, you are not ranked. This is the delivery minimum. A team that wins no order is not ranked. Teams with the same margin to the cent share a rank.

The final picks the 10 ranked teams with the highest margin. Each of them presents how it built its system. A jury chooses the winners from these 10, for how elegant, clear, creative and robust their approaches are. If fewer than 10 teams are ranked, only the ranked teams present.

## The week

- The API calls one simulated week a world. World time is in whole minutes from Monday 00:00. Every time that you send is a whole number of minutes, and every amount a whole number of cents.
- Price requests arrive from Monday 08:00 to Friday 18:00, almost all between 09:00 and 18:00.
- Saturday and Sunday have no new requests. Dealer sites hand over and receive won cars on Saturday at short hours, and some are closed. On Sunday every site is closed.
- The next Monday is a normal business day without new requests. The world then runs on to the Wednesday at 22:00, so late cars can still arrive. After that Monday only late cars move.
- Every option's deadline is at 20:00 on a business day. The last possible deadline is 20:00 on that Monday.

## Price requests

A price request names:

- the dealer;
- one or two legs. A leg is one car from a pickup place to a delivery place. A trade-in request has two legs: the new car out, and the customer's old car back. One price covers both;
- for each place, whether it is a private address, and a window for the stop. A pickup window starts at the car's stated ready time. At a dealer site, the window ends at the latest option's deadline, and the opening hours say when the site is open. At a private address, the window is a booked slot. A private seller's slot starts at least 1.5 hours after the response deadline. It ends late enough that a driver booked when you win the order can still reach it, with the 3 hours of the deadline warning to spare;
- the delivery options the dealer accepts, each with its deadline and a list price;
- a typical fee (`referenceFeeCents`): about what a driver is usually paid for this work in the drivers' app. It is not the least fee that a driver takes;
- a note, if the dealer wrote one;
- a response deadline, 15 minutes after the request arrives.

The options are:

| Option | Deadline day |
|---|---|
| `same_day` | The day the car is ready. Offered only when the request arrives by 15:00 on that day, and the car, collected when it is ready, can arrive 2 hours before the deadline. |
| `next_day` | 20:00 on the next business day. For a car ready on a Friday: the Monday. |
| `within_3_days` | 20:00 on the third business day after the day the car is ready, and no later than the last Monday. |

A private delivery has its own deadline (rule 5). A request with a private delivery or a trade-in carries one option only.

You answer with one price for each option you offer, which can be fewer than the dealer accepts. A quote needs at least one price, so you cannot take a quote back. To decline, quote a price far above the list price: a quote that loses costs nothing. A request that you do not quote costs a little goodwill (see below). Up to level 3 every quote wins, so there you decline by sending no quote, which costs nothing at those levels. A new quote for the same request replaces the earlier one.

The dealer decides at the response deadline, on your last quote. It compares your prices with the competitor's prices and with what each option is worth to it. Then it buys from you or from the competitor. The world refuses a quote at the response deadline or later.

The list price is a public price list by distance. It is not the competitor's price. Up to level 6 the competitor's prices are near it. At the world's middle price level, on an average lane and day, they are about the list price. At levels 7 and 8 they are well above it (see Practice and the final).

When you win, the order's windows at dealer sites end at the deadline of the option that the dealer chose. A private slot and a booked hour do not change. When snow moves the deadline, the windows move with it.

A late or undelivered car makes its dealer accept only lower prices from you. Each car you deliver on time wins back a little. A request that you do not quote makes that dealer slightly less willing to accept your prices; a quote that loses does not.

## Gigs

A gig is one or more legs in a fixed order of stops. You post it with:

- the stops in order. Each stop can carry its own window, for example from a note. It may not start before the order's window for that stop. At a private pickup it must also end inside the slot. At other stops it may end after the order's window, so a late car can still be collected and delivered. A stop without its own window uses the order's window, to the order's deadline, cut to the site's stated hours. When that window has ended, or no driver can still reach every stop inside the order's windows, the stops without a window of their own run to the end of the world. So you can always post a late car without windows. A private seller's slot never extends;
- on a pickup: `withPlates` to fetch transfer plates on the way (30 minutes), `withCharging` to plan a charging stop (40 minutes);
- which drivers may take it (`audience`): everyone, experienced drivers only (rating at least 4.8 and at least 50 completed gigs), and whether the driver must hold a licence for manual cars;
- a fee schedule: 1 to 48 steps, each a time and a fee, in time order. The first step is the opening fee. No step's fee may be lower than the one before. The fee shown in the drivers' app is the latest step that has started. At level 1 a schedule has one step.

Rules for gigs:

- A gig with more than one car may not plan more than 12 hours of driving, from its first pickup to its last delivery, by the road minutes of the map. The 12 hours include the drive between every two stops, also from one car's delivery to the next car's pickup. Plates and charging stops do not count. A trade-in is two cars: when its two legs need more than 12 hours, post them as two gigs. A single car has no limit.
- In a trade-in gig, put the old car's pickup right after the new car's delivery. The driver then collects the old car in the same visit, however late the new car arrives. That pickup's slot does not end the gig, and it does not limit when a driver can book it. After the customer's hour, a driver can still book the gig: they bring the new car late and collect the old car in the same visit. The old car posted alone must be collected inside the customer's slot. When the slot ends with the car still there, the dealer books the same hours on the next business day, and you see `order_changed`.
- You can reprice or withdraw a gig that no driver has booked. A reprice replaces the whole fee schedule. Its steps may start now, earlier or later, and its fees may be lower than the fee in the app: only inside the new schedule may no fee fall. Until its first step starts, the app shows the first step's fee. A new fee sends no notification: a driver sees it when they next open the app.
- The world refuses a gig when one of its stop windows has already ended. The reason names the stop, for example "The delivery window of R00042 leg 0 ended at Tue 18:59: no driver can deliver the car in it".
- An open gig leaves the app in the minute after the first of its stop windows ends. You see `gig_expired`, the gig shows as withdrawn, and you can post its cars again.
- A driver books a gig only when they can reach every stop inside its window, by the road minutes of the map and the plates and charging minutes. Thus a gig can get no driver before any of its windows ends: a car with a 7-hour drive whose delivery window ends at 20:00 gets no driver after 13:00. A driver plans their day to reach each stop inside its window and when the site's stated hours show it open: they leave later rather than wait at a closed site. When no start can do that, for example when a private seller's slot ends too early for a long drive, they collect the car as early as they can. A booked driver plans again when their day changes, for example when an earlier gig runs late. If they can no longer collect the first car inside its window, they tell you at once:
  - `driver_late`, with `pickupAt`, the minute they will collect the car, when the first pickup is a dealer site whose opening hours show it open when they get there. They come, and you can release them without a fee until they leave.
  - `no_show` in every other case, for example at a private pickup. The gig is open again. The driver list does not count a no-show as a drop.
- A driver can drop a booking for as long as they hold it, and the longer they hold it, the likelier a drop. Drops at night are rare. A driver books on a quick look and plans the gig soon after. If it then costs them more than its fee pays, they can give it back. This is most likely when the fee only just paid them. A driver who leaves within the hour of a booking can still drop it before they leave. A driver drops a booking for a better gig only when it pays clearly more. A driver who gave a gig back books it again only for a higher fee. You then see `gig_dropped`, and the gig is open again.
- For an order without a driver, `deadline_close` comes 3 hours before `lastPickupAt`. That is the last minute to collect the first undelivered car, when its site's stated hours show it open or inside a private seller's slot, and still deliver every undelivered leg by its deadline. It counts the road minutes, an electric car's charging stop and the delivery sites' stated hours. No one else moves a car for you: after the warning, only a higher fee or a direct offer finds a driver, and a driver needs time to see the gig and reach the pickup.
- Each order shows its `lastPickupAt` in the snapshot, `order_won` and `order_changed`. The world sets it when you win the order, and again when snow, a change of plan or a delivered leg changes the work left. When no pickup can be on time, for example after a private seller's slot moves to the next business day, `lastPickupAt` is the first minute the car can be collected. The warning comes again when the order loses its driver later, and when `lastPickupAt` moves later. An order has at most one warning waiting.
- You can release a booked driver until they leave for the gig, which you see as `gig_started`. The release fee is 25% of the fee if the pickup is more than 24 hours away, 50% if less, 100% if less than 2 hours. The gig leaves the app; post its legs again.

## Drivers

Freelance drivers live in towns across Finland. Not every driver works every day, and you cannot see who does. A driver who works has a day: when they are free, how many gigs they want, how late they want to get home. A gig costs a driver their time on the road, the trip to the pickup from where they are, and the trip home or to their next gig by public transport, with the ticket. A driver buys the ticket out ahead, when it is cheap. They buy the ticket home on the day, because they do not know when the last delivery ends, and it costs more. A driver goes as far as the fee pays: a car far from any driver needs a fee that pays for the whole trip, and a long gig draws drivers from further away than a short one. A driver who cannot reach a far morning pickup on its day can travel the evening before and stay the night. The night costs them, so such a gig needs a higher fee. Evening, weekend and private-address work needs a higher fee. A driver books a gig for a later day only when they know they will work that day: some know their week, most only the next day. A driver takes a gig in the app when the fee pays for their time, their trips and what else their day is worth to them, unless they expect more from waiting for a raise. Drivers expect your gigs to rise as far as they have seen them rise. Close to the pickup, a driver expects another driver to book the gig soon, so they wait less for a raise. A driver who works that day and waits for a raise can come back to look at the gig again. A driver who does not work that day sees a raise only when they next open the app. A gig that stays on the board for a while did not pay the drivers who saw it. A driver whom it pays then expects it to rise, so they wait more for a raise. A car that you post again keeps the age of its first posting. A driver whom snow keeps at home drops a booking that must start that day.

Drivers learn, from level 2. When a driver books one of your gigs, the drivers who viewed a gig of that order and could have done it learn from it. They learn how far its fee rose and how long it stayed open. Each driver learns only from the orders that they viewed and could do.

A driver's app notifies them of a new gig when its first pickup is within 30 km of their home, in a straight line. Drivers follow gigs near where they are. A gig far away gets fewer views, unless its fee could pay them. The driver list (`GET /drivers`) shows every driver's home town, rating, licence, completed gigs and past drops. You see the position of a driver while they work one of your gigs. You see each view of your gigs with the driver's id.

## Direct offers

A direct offer sends one gig to one driver for 10 minutes at a fee you choose. The gig can also be in the app.

- At most three direct offers per driver per day.
- At most three open direct offers per gig.
- A driver reads an offer when they see its notification, or open the app while it is open. A driver who works today reads most offers in the hours they are free, and fewer outside them. A driver who does not work today reads few. A driver without notifications sees an offer only if they open the app in its 10 minutes. No driver reads an offer from 23:00 to 07:00.
- A driver who reads an offer takes it when the fee pays for the gig and what else their day is worth to them. In the app the same driver may hold out for more, because the fee may rise there, though another driver may take the gig first. Thus an offer saves most on gigs whose fee drivers expect to rise.
- A yes comes inside the 10 minutes and books the driver. A no comes after the 10 minutes as `declined`. An unread offer, and a yes for a gig that is no longer open or no longer fits the driver's day, come after the 10 minutes as `expired`.

## Notes and changes of plan

Many requests carry a note from the dealer, in Finnish, Swedish or English. Most notes are harmless. Some change when the car can be collected, when it must arrive, or what the driver must bring or be allowed to drive. The world acts on what the note says, whether or not your system reads it.

- Days in a note count from the day the request arrives. A time without a day is on that day, as is a time with "tänään", "idag" or "today". "huomenna", "i morgon" and "tomorrow" mean the next day, and "ylihuomenna", "i övermorgon" and "the day after tomorrow" the day after it. Short forms count too, for example "tmrw" and "imorgon".
- A weekday name, also a short or spoken form such as "to." or "Thurs", means that weekday 2 to 6 days after the request arrives. A note never names the arrival day or the next day by its weekday.
- Digits give the hour on a 24-hour clock, unless "am" or "pm" follows. An hour in words, in Finnish or Swedish, is the working hour with that number, from 9 to 17: "two" is 14:00. "Half three" is half an hour before three: 14:30.
- When a note corrects itself, the correction counts. A day or time that the note denies, such as "not 3pm", does not.
- A note can state several facts, and some notes state none.
- A car that the dealer hands over only after payment cannot be collected before the time in the note. A `release` event comes at that time.

After you win an order, the dealer can send the car to another place. You then see `order_changed`, with a message. The world sends the car to the new place, also when a driver has booked or started the gig. When the new place is farther, you pay the driver for the extra driving.

The dealer can also cancel an order, at every level. From that minute the car does not move, and the order does not count. `order_cancelled` lists each live gig that held one of the order's cars, with what happens to it:

- `withdrawn`: the gig was open and has left the app. Post its other cars again.
- `released`: the gig was booked and held no other order's car. The world releases the driver under the release fees.
- `shortened`: the gig was booked with another order's car. It keeps that car, and the driver keeps the booked fee.
- `continues`: the driver had left. They skip this car's stops, and you pay the full fee. The car is not delivered.

## Rules for handling cars

1. A premium car may only be driven by an experienced driver. Fine: €150 per car.
2. A gig with more than one car may not plan more than 12 hours of driving.
3. An electric car below 30% charge on a route over 200 km needs a charging stop. Without one, the car arrives 90 minutes late, and the fine is €100.
4. Dealer sites accept deliveries only in their opening hours. A driver who reaches a site that its stated hours show closed leaves the car at once: the delivery counts when the site next opens, and you pay no waiting. When a site is closed at a time its stated hours show open, as a note can say, the driver waits for it to open, at most 2 hours, and you pay €15 an hour. Then the driver leaves the car, and the delivery counts when the site next opens.
5. A private delivery has a booked hour. Its deadline is the end of that hour plus 30 minutes. A driver who arrives before the booked hour waits for it with the car. You do not pay for that wait.

## Your account

Your account holds every amount of money in and out. Each `ledger` event is one line of it.

| Line | Amount |
|---|---|
| Delivered order | The price you quoted |
| Late by up to 2 hours | 10% of the price back to the dealer |
| Late by 2 to 24 hours | 25% back |
| Late by more than 24 hours, up to two business days | 50% back |
| Late by more than two business days | The whole price back. The car must still be delivered. |
| Not delivered when the world ends, on the Wednesday at 22:00 | No price, and a penalty of twice the price |
| Driver fee | Paid when the gig is done |
| Failed visit | Half of the failed car's share of the gig's fee, at least €25 |
| Release of a booked driver | See Gigs |
| Fines and waiting | See Rules for handling cars |

A failed visit happens when the driver cannot collect the car: it is not ready or not handed over yet, the site is closed, the seller is not at the private address outside the booked slot, the driver's licence does not fit, or the driver has no plates for a car that needs them. In a gig with several cars, the driver continues with the rest. The account lines for a failed visit and for waiting come when the driver arrives, and name the stop and the place.

An order is on time when every leg is delivered by its deadline. When a car is ready a day or more later than the request says, the order's deadline moves by the same days. The order in `order_won` already has the moved deadline. Lateness in business days counts as dealers count it: Saturday and Sunday do not count, so a car due on Friday and delivered on Monday is late by one business day.

**Score**: the sum of your account. **Delivery minimum**: at least 90% of won orders delivered on time. A car not delivered when the world ends counts as late. Orders the dealer cancels do not count. A team that wins no order is not ranked.

## Practice and the final

- **Practice worlds** stand still until your code calls `advance`. The world is deterministic: the same seed, level and actions give the same week, to the cent. You can copy a practice world at any minute with `fork`: the copy has the same future, so you can try two decisions on it. After a practice world ends, its reveal shows what a dispatcher learns by working the week: your quotes and their outcomes, what was true of the cars that you won, and who viewed your gigs. It never shows the competitor's prices or what a gig cost a driver. Ranking runs, the practice worlds that all teams compare on, cannot be copied or revealed.
- **Levels**: 1 posting, 2 market, 3 network, 4 quotes, 5 full, 6 surprises, 7 shortage, 8 trust. The final is level 8.
- Level 3 plays the same world as level 2 and adds gigs with several cars and direct offers. Up to level 3 there is no competitor, and the dealer ignores your prices: every quote wins the first option at its list price.
- Level 6 is level 5 with three events that nobody announces. The outage: every week the competitor is down in one region for a working day, from 08:00 to 18:00. That region is a town and the places within 80 km of it, in a straight line. In some weeks a flu keeps drivers at home, and in some a dealer group sends a bulk order (see the handbook).
- Level 7 is level 6 in a driver shortage, and every week has all three events.
- Level 8 is level 7 with four changes. The shortage differs from town to town. The competitor is dearer. Every week has a price war that nobody announces. Dealers trust a team that delivers on time.
- **The final** is one secret world. Every team plays its own copy of it, with the same requests, drivers and random events. The clock runs by itself: about 18 real seconds per weekday hour. A request you do not answer in time goes to the competitor. If your code stops, the world keeps going.
- Every world draws its own drivers and market conditions. Up to level 6, a world has from 20% fewer to 20% more drivers than an average world. Its competitor price level is then from 10% below to 10% above the middle level, the list price. Levels 7 and 8 have their own ranges, below. Part of the competitor's price on each lane is the same in every world (see the handbook).
- **The shortage of levels 7 and 8.** A world of level 7 or 8 has about a third of the drivers: from 26% to 40% of an average world's number. The competitor pays its own drivers more, so its price level is higher. It is from 12% to 38% above the list price at level 7, and from 26% to 54% at level 8. The typical fee and the list price are those of a normal week.
- In a shortage you can win more cars than drivers can move on time. A system that wins every car it can falls under the delivery minimum, and is not ranked.
- **Level 8.** The shortage is uneven. The world multiplies each town's share of the drivers by a factor of its own, new in every world. Some towns keep most of their drivers, and some lose nearly all. The driver list shows where the drivers live.
- **The price war of level 8.** Every week has the three events of level 6 and a price war. On one working day without an outage, the competitor quotes 20% under its usual prices everywhere, from 08:00 to 18:00.
- **Dealer trust at level 8.** Each dealer counts the orders that you delivered to it on time, up to 25. Each counted order raises the prices that the dealer accepts from you by 1%. At 25 orders the dealer accepts prices 25% higher from you. An order that arrives late halves that dealer's count, rounded down. The count starts at zero in every world.
- You can count it from your own account: an order's revenue line with no late refund is an order on time. The dealer still compares your price with the competitor's price. A late or undelivered car still makes its dealer accept only lower prices from you, as at the other levels.
