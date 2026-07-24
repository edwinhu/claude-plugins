# Order Book Rebuilding

Replaying the limit order book from L2/L3 events. All methods hang off a `MarketData` object
(see [security-api.md](security-api.md)).

```python
import datetime, itertools
import numpy as np, pandas as pd
from bmll2 import NormalisedSecurity

sec = NormalisedSecurity.from_listing_id(listing_id=121317, date='2022-04-21')
md = sec.market_data()

ct = md.market_info().query('market_state == "CONTINUOUS_TRADING"')
open_dt, close_dt = ct.start_timestamp.min(), ct.end_timestamp.max()
```

## Choosing a method

| Method | Returns | Use when |
|---|---|---|
| `l2_snapshot(depth=N)` | DataFrame, one row per event, `bid/ask_price_i`, `_size_i`, `_num_orders_i` | Fastest path to a book; default depth 5 |
| `rebuilt_book_L2(...)` / `rebuilt_book_L3(...)` | DataFrame over a window at a frequency | A bounded window as a frame |
| `snapshot_generator_L2/L3(...)` | Generator of `(bid, ask)` arrays | Full-day replay without materialising it |
| `rebuilt_history_L2/L3(apply(...))` | DataFrame of *your* metrics | Custom metrics over the day — the workhorse |

Materialising a full-day book at event granularity is large. If the output is a handful of metrics
rather than the book itself, use `rebuilt_history_*` — it computes as it replays instead of
building the frame and then reducing it.

## Snapshots

```python
md.l2_snapshot(depth=3)

df_l2 = md.rebuilt_book_L2(start_timestamp=open_dt,
                           end_timestamp=open_dt + datetime.timedelta(hours=1),
                           frequency=pd.Timedelta('600s'))

df_l3 = md.rebuilt_book_L3(start_timestamp=open_dt,
                           end_timestamp=open_dt + datetime.timedelta(seconds=60))
```

## Generators

```python
gen = md.snapshot_generator_L2(start_timestamp=open_dt, end_timestamp=close_dt,
                               frequency=pd.Timedelta('1s'))

for bid, ask in itertools.islice(gen, 2):
    print(bid[:10], ask[:10])
```

Each yield is a pair of structured arrays with fields `level`, `price`, `size`, `num_orders`, plus
a `.meta_data` mapping (`event_no`, `quote_no`, `timestamp`, `side`). Timestamps in `meta_data` are
**integer nanoseconds**.

Called with no frequency, the generator yields on every event, which lets you walk quotes and book
state together:

```python
gen = md.snapshot_generator_L2()
quotes = md.incremental_book_L3().itertuples()

for quote, (bid, ask) in zip(quotes, gen):
    ...
```

This zip is the general pattern for event-time features — trade-conditioned imbalance, queue
position, lookback windows — where the metric needs both the event and the resulting book.

Sub-sampling helpers from `bmll2.rebuilder` wrap a generator:

```python
from bmll2.rebuilder import (every_n_events, timestamp_sampler, date_range_sampler,
                             date_range_sampler_on_the_fly, event_number_window,
                             timestamp_window)

df_every_5 = every_n_events(5)(md.snapshot_generator_L2())
```

`date_range_sampler_on_the_fly` is the minimal-memory variant — prefer it for full-day work.

## `apply` and `rebuilt_history_*`

The efficient route to custom metrics: `apply` bundles named operations, `rebuilt_history_*`
replays the book once and evaluates all of them.

```python
from bmll2.rebuilder import apply, volume_imbalance, sma

fn = apply(vol_imb=volume_imbalance(5),
           moving_vol_imb=sma(volume_imbalance(5), 15))

hist = md.rebuilt_history_L2(fn,
                             start_timestamp=open_dt, end_timestamp=close_dt,
                             frequency=pd.Timedelta('600s'))
```

Each keyword becomes a column.

### Built-in operations

| Function | Returns |
|---|---|
| `volume_imbalance(levels)` | Volume imbalance to depth `levels` |
| `order_imbalance(levels)` | Order-count imbalance to depth `levels` |
| `size_up_to_nth_level(size_type, levels)` | Cumulative volume / order count to level `n` for a side |
| `liquidity_around_bbo(levels=, bps=)` | Liquidity within a band of the touch |
| `sweep_to_fill(volume_to_fill, ...)` | Price or level required to fill a given volume |
| `sma(func, window)` / `ema(func, window)` | Moving averages of another operation |
| `returns(dtype)` | Decorator marking a custom function's return type |
| `get_dataframe(events, *args, sort_by_price=)` | Snapshots as a DataFrame |
| `nested_adapter(depth, display_implied_prices=)` | Changes the dtype of generated L2 snapshots |

### Custom operations

Any `(bid, ask) -> scalar` function works, decorated with `@returns` so `apply` knows the dtype:

```python
from bmll2.rebuilder import returns

@returns(np.float64)
def avg_bid_size(bid, ask):
    return bid['size'].mean()

@returns(np.int64)
def bid_size(bid, ask):
    return bid['size'].sum()

fn = apply(avg_bid_size=avg_bid_size, bid_size=bid_size)
df = md.rebuilt_history_L3(fn, start_timestamp=open_dt, end_timestamp=close_dt,
                           frequency=pd.Timedelta('60s'))
```

The `@returns` dtype is not decoration — it is how the output column is allocated. Omitting it, or
declaring `np.int64` for a function that returns a mean, silently truncates.

`bid`/`ask` are the structured arrays described above; index by field name (`bid['size']`), not
position.

## Order tracing

L3 exposes the full life of an order. **`original_order_id`** is the tracing key — `order_id`
changes on modification, so grouping on it fragments the life of a single order:

```python
sec = Security.from_listing_id(listing_id, date)
book = sec.market_data('L3').incremental_book_L3()

freq = book.groupby('original_order_id').count()['event_no'].sort_values()
oid = freq[freq >= 35].index[0]

orders = book[book.original_order_id == oid].reset_index(drop=True)
lifetime = orders.event_timestamp.max() - orders.event_timestamp.min()
executed = orders.executed_size.sum()
```

Useful per-order fields: `event_timestamp`, `lob_action` (INSERT / AMEND / DELETE / EXECUTE),
`size`, `old_size`, `price`, `price_level`, `executed_size`, `side`.

`old_size - size` on an execution event gives the executed quantity for that event, which is how
the event-time trade reconstruction above recovers trade sizes from the book.

## Consolidated books

Cross-venue books, two routes.

**Data Feed (pre-computed, 10 levels).** Requires an entitlement and specific venue coverage:

```python
import pandas as pd
from bmll import market_data

df = market_data.instrument_cbbo(324347, '2022-05-23', level=1)
# timestamp, ask_1_price, ask_1_size, ask_1_cum_size, bid_1_price, bid_1_size, bid_1_cum_size

full = pd.concat(
    (market_data.instrument_cbbo(324347, '2022-05-23', level).set_index('timestamp')
     for level in range(1, 11)),
    axis=1).reset_index()
```

One call per level — loop to assemble the book. Keyed by **`InstrumentId`**, not `ListingId`.
`_cum_size` is cumulative size through that level.

**This consolidation ignores market state.** Venues open and close at different times, so a naive
consolidated book includes venues that are closed or in auction. Fetch per-venue states and filter:

```python
market_data.instrument_market_state(324347, '2022-05-23')
# timestamp, market_state, market  — one row per venue state change
```

**Data Lab (build it yourself):**

```python
from bmll2 import (consolidate_book, consolidated_snapshot_generator_L2,
                   consolidated_rebuilt_history_L2)

consolidate_book(order_books, timestamp=...)
```

`consolidated_rebuilt_history_L2` takes the same `apply(...)` functions as the single-venue
version, so custom cross-venue metrics reuse the operations above.
