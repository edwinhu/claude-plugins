# `Security` and `NormalisedSecurity`

Per-listing access: raw venue data, normalised data, and order-book replay. Equities and futures
only — **options are not supported** (use `get_market_data*`).

```python
from bmll2 import Security, NormalisedSecurity, reference
from bmll2.normalised import (MARKET_STATES, L2_LOB_ACTIONS, L3_LOB_ACTIONS,
                              BMLL_TRADE_TYPES, SIDES, TRADE_ACTIONS)
```

## Which class

| | `Security` | `NormalisedSecurity` |
|---|---|---|
| Fields | Venue-native flags, exactly as the exchange publishes them | Normalised across all venues |
| Use when | You need a venue-specific flag, or are auditing BMLL's normalisation | Comparing behaviour across venues or securities — the default |
| Class hierarchy | base | inherits `Security`; same interface, normalised `market_data` tables |

`NormalisedSecurity` is the right default. Cross-venue analysis on raw venue flags means writing
per-venue conditionals, which is the work the normalisation already did.

```python
sec = NormalisedSecurity.from_listing_id(listing_id=121317, date='2022-04-21')
# -> LSEEquity(listing_id=121317, date='2022-04-21')
```

The concrete class depends on venue and asset type. Dates are ISO strings or `datetime.date`.

## Metadata

Attributes are tab-completable and return `None` when unavailable:

```python
sec.currency            # 'GBp'
sec.tick_size_rule      # [[0.0001, 0.0001], [0.1, 0.0001], ..., [50000, 10]]
sec.calendar            # see other-datasets.md
sec.exchange_metadata   # named tuple of exchange-provided metadata
```

`exchange_metadata` carries venue-specific fields — for the LSE: `lis_threshold`, `segment_mic`,
`lot_size`, `allowed_book_types`, `first_trading_date`, `average_daily_turnover`, circuit-breaker
tolerances. Attributes appear only when meaningful (no ISIN attribute if no ISIN exists), so use
`getattr` rather than assuming presence.

`lis_local` from `exchange_metadata` is the Large-In-Scale threshold needed for trade
classification.

## Market data

```python
md = sec.market_data(feed='L3')      # 'L2' = aggregated per price level, 'L3' = order-by-order
```

Most methods accept `start_timestamp`, `end_timestamp` and `columns`; passing them pushes the
filter down and is materially faster than slicing afterwards.

### Tables

| Method | Contents |
|---|---|
| `all_trades()` | **All printable trades** from every source (book executions + trades table) |
| `trades()` | Trades *not* covered by book executions — off-book, reported |
| `incremental_book_L3()` | Order-by-order LOB updates |
| `incremental_book_L2()` | Per-price-level LOB updates (MBP venues) |
| `best_bid_offer()` | L1 touch with `market_state` |
| `best_bid_offer_ct()` | `best_bid_offer` filtered to continuous trading |
| `midpoint()` | L1 midpoint with `market_state` |
| `l2_snapshot(depth=...)` | Rebuilt book, default 5 levels |
| `auction_data()` | Auction imbalance / indicative price and quantity |
| `market_info()` | Market phases (state intervals) for the day |
| `exchange_metrics()` | Exchange-published statistics — settlement/closing/reference prices, price limits, open interest, auction results; one row per metric |
| `trade_summary()` | Trade-summary messages (CME, Eurex) — aggressor's perspective |
| `open_interest()` | Open-interest messages (derivatives) |
| `manual_orders()` | Manual orders |
| `si_quotes()` | Systematic internaliser quotes |
| `sip_quotes()` | SIP quotes |

**`all_trades()` vs `trades()` is the most common mistake here.** On `Security`, `trades()` returns
only off-book prints — order-book executions live in `incremental_book_L3()`. Summing `trades()`
and calling it the day's volume misses every lit continuous execution. `all_trades()` aggregates
across sources and returns only *printable* trades, which is what a volume figure wants.

In the normalised data, auction uncrossings appear in the trades table for consistency.

## Market states and normalised enums

`NormalisedSecurity` gives every table a consistent `market_state`, so filtering works uniformly:

```python
mkt = md.market_info()
# CLOSED -> OPENING_AUCTION -> CONTINUOUS_TRADING -> CLOSING_AUCTION -> POST_TRADE -> CLOSED
```

Normalised categorical fields: `market_state`, `side` / `aggressor_side`, `bmll_trade_type`,
`lob_action`. Values are text (readable, still performant). Enumerate them:

```python
SIDES.categories          # Index(['ASK', 'BID', 'UNKNOWN'])
MARKET_STATES.categories
BMLL_TRADE_TYPES.categories
```

Use the enum objects in `query()` rather than string literals — a typo in a literal silently
matches nothing:

```python
book = md.incremental_book_L3()

opening = book.query('market_state == @MARKET_STATES.OPENING_AUCTION')
bid_inserts = opening.query('(side == @SIDES.BID) & (lob_action == @L3_LOB_ACTIONS.INSERT)')

trades = md.trades()
otc = trades.query('bmll_trade_type == @BMLL_TRADE_TYPES.OTC')
uncross = trades.query('(bmll_trade_type == @BMLL_TRADE_TYPES.UNCROSSING) & '
                       '(market_state == @MARKET_STATES.OPENING_AUCTION)')
```

## Key columns

`incremental_book_L3()`: `event_no`, `quote_no`, `side`, `price`, `size`, `order_id`,
`original_order_id`, `event_timestamp`, `lob_action`, `order_executed`, `execution_price`,
`execution_size`, `price_level`, `end_of_event`, `market_state`, `printable`, `old_size`.
(The full frame is ~110 columns.)

Trades tables: `trade_id`, `trade_timestamp`, `publication_timestamp`, `aggressor_side`, `price`,
`execution_size`, `market_state`, `sequence_no`, `currency`, `printable`, `bmll_trade_type`,
`trade_action`, `execution_venue`.

`best_bid_offer()`: `event_timestamp`, `best_bid_size`, `best_bid_price`, `best_ask_price`,
`best_ask_size`, `market_state`.

`auction_data()`: `imbalance_qty`, `paired_qty`, `ref_price`, `sequence_no`, `side`,
`event_timestamp`, `market_state`.

Note `aggressor_side` is frequently `UNKNOWN` on off-book and auction prints — the same limitation
as Trades Plus. Continuous lit executions carry `BID`/`ASK`.

## Deriving the trading window

Almost every intraday analysis wants the continuous session, not the whole file:

```python
ct = md.market_info().query('market_state == "CONTINUOUS_TRADING"')
open_dt, close_dt = ct.start_timestamp.min(), ct.end_timestamp.max()
```

Hard-coding session times instead breaks on half-days, auction extensions and venue-specific
schedules — and does so silently, by returning fewer rows rather than an error.

## Trade classification

The daily classified-trades taxonomy can be recomputed per security. It needs a Large-In-Scale
value plus the primary venue's `market_info` (addressable liquidity is defined relative to the
primary market's hours and currency):

```python
import bmll
from bmll2.beta import NormalisedSecurity
from bmll2.analytics.security_metrics import trades_classification

ref = bmll.reference.query(ISIN=['GB00BH4HKS39'])
instrument_ref = ref[ref.InstrumentId == ref[ref.ListingId == 121317].InstrumentId.iloc[0]]

security = NormalisedSecurity.from_listing_id(121317, '2022-01-24')
primary_listing_id = instrument_ref.query('IsPrimary').ListingId.iloc[0]
primary_market_info = (NormalisedSecurity.from_listing_id(primary_listing_id, '2022-01-24')
                       .market_data().market_info())

botc = NormalisedSecurity.from_listing_id(
    instrument_ref.query("MIC=='BOTC'").ListingId.iloc[0], '2022-01-24')

ct = trades_classification.get_granular_trades_classification(
    security, primary_listing_id, primary_market_info,
    'EUR', float(botc.exchange_metadata.lis_local), botc.currency)
```

For most purposes the pre-computed daily feed
(`bmll.time_series.classified_trades`, [analytics-timeseries.md](analytics-timeseries.md)) or the
`Classification` column in Trades Plus is the better route — recompute only when you need a
different LIS or a custom taxonomy.

## Parallelising across listings

`Security` calls are per-listing and serial by default. `SparkHelper` parallelises them across the
workspace's cores — see [compute-and-storage.md](compute-and-storage.md).
