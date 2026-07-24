# Market Impact

Tutorial: `tutorials/notebooks/market_impact.html`

Price impact is the correlation between an incoming order and the subsequent price change. It is a
cost: a trader's second trade is on average more expensive than the first because of their own
impact. A **markout** — the price change measured at offsets around an event — is how it is
quantified.

BMLL gives two routes, and they answer different questions.

| | Trades Plus markouts | Event-time impact (this page) |
|---|---|---|
| Source | Pre-computed midpoint columns on each trade row | Rebuilt from the L3 book |
| Events | Executed trades only | Any query on the incremental book |
| Horizons | 15 fixed intervals, 1ms–5m | Arbitrary, including sub-millisecond |
| Universe | A whole market in one call | Per listing (parallelise for more) |
| Cross-product | No | **Yes** — events from one book vs another book's midpoint |
| Cost | Cheap | Rebuilds the book |

Use Trades Plus ([trades-plus.md](trades-plus.md)) for breadth — impact across a market by
classification or venue. Use event-time impact when the event is not a trade, the horizon is not on
the published grid, or the response is in a *different* instrument.

## The script

The computation is deterministic and easy to get subtly wrong — the sign convention especially.
Use `scripts/bmll_impact.py`:

```python
from bmll2 import NormalisedSecurity
from bmll_impact import (MarketImpactComputer, liquidity_removal_events,
                         mid_from_book, geometric_window)

book = (NormalisedSecurity.from_listing_id(121317, "2021-05-05")   # VOD on XLON
        .market_data().incremental_book_L3()
        .set_index("event_timestamp"))

im = MarketImpactComputer(
    events=liquidity_removal_events(book),
    books={"VOD:XLON": book},
    impact_metric=mid_from_book,
    window=geometric_window(seconds=10, n=20),
)

curve = im.mean_curve()      # offset_seconds | ID | impact_bps
raw = im.raw_impact          # per-event, per-offset
```

`mean_curve()` returns a tidy frame ready to plot: `offset_seconds` runs negative (pre-event)
through zero to positive (post-event).

```python
import plotly.express as px
px.line(curve, x="offset_seconds", y="impact_bps", color="ID",
        labels={"impact_bps": "Midpoint impact (bps)", "offset_seconds": "Markout time (s)"})
```

## How it works, and what to get right

**The reference is taken 1ns before the event.** At the event timestamp the book is already
impacted, so measuring deviation from the event-time midpoint understates impact by exactly the
part you are trying to measure.

**Sign is normalised to the aggressor.** `events['side']` is the side of the *resting* order that
was executed, so an executed ASK means an aggressive **buyer** and an executed BID an aggressive
**seller**. `impact_bps` flips the sign for seller-initiated events so both add.

Without that normalisation the mean curve collapses toward zero — buy and sell impact cancel — and
reads as "no impact" rather than "two opposite signs were averaged". The test in
`scripts/test_bmll_impact.py` asserts exactly this: on a fixture where each event moves the mid 1%
in its own direction, the normalised mean is ~99.5 bps while the unnormalised mean is ~0.5.

(BMLL's tutorial reaches the same place by a double negation — the measure multiplies by `-1` for
ASK and the plotting code multiplies by `-1` again "to look at the aggressive side". The script
does it once, explicitly.)

**Windows are geometric by default.** Impact decays fast and nearly all the structure is in the
first milliseconds; evenly spaced offsets spend their resolution on the flat tail.
`geometric_window(seconds=10, n=20)` covers 10s densest near zero. Use
`linear_window(seconds=1e-4, n=80)` for sub-millisecond work.

**Events are user-defined.** `liquidity_removal_events` selects
`lob_action == 'REMOVE' & execution_size > 0`, largest first — but any query works. Passive
inserts, cancellations, iceberg reveals, or events filtered to a size band are all valid; supply a
frame with `event_timestamp` and `side`.

`num_events` bounds the set to the N largest, which is usually the question and also bounds cost —
the reindexing is per event per offset.

## Cross-product impact

The distinctive capability: measure events in one book against the midpoint of another. Pass a
different book in `books` than the one the events came from.

```python
from bmll2 import Security, NormalisedSecurity

SPY = (NormalisedSecurity.from_listing_id(366216, "2021-05-05")      # SPY on XNYS
       .market_data().incremental_book_L3().set_index("event_timestamp"))
ES = (Security.from_listing_id(415907260, date="2021-05-05")         # ES future
      .market_data(feed="L3").incremental_book_L3().set_index("event_timestamp"))

im = MarketImpactComputer(
    events=liquidity_removal_events(ES, num_events=10_000),   # events from the future
    books={"SPY:XNYS": SPY},                                  # impact measured on the ETF
    window=linear_window(seconds=1e-4, n=80),
)
curve = im.mean_curve()
```

In BMLL's run the SPY response appears about **4ms** after the ES event — the New York/Chicago
round-trip time. That is the shape of the result to expect: a flat pre-event line, then a step at
the propagation delay rather than at zero.

Two things follow. The window must be fine enough to resolve the delay — a geometric 10s window
puts the whole effect in one bucket. And the futures book comes from `Security` rather than
`NormalisedSecurity`, so its columns are venue-native: the tutorial's futures event query uses
`lob_action == 3` and `executed_size` where the normalised equity book uses `'REMOVE'` and
`execution_size`. Check the column names on a raw book before assuming the normalised ones.

Expect this to be slow — BMLL's 10,000-event cross-product example takes ~45s after the books are
loaded.

## Related

- Trades Plus markouts across a whole market: [trades-plus.md](trades-plus.md)
- Rebuilding books and custom metrics: [order-book-rebuilding.md](order-book-rebuilding.md)
- Parallelising per-listing work: [compute-and-storage.md](compute-and-storage.md)
