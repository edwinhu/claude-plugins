# Data Schemas

`https://lab.bmlltech.com/docs/contents/data_ref/start.html`

BMLL publishes market data at three levels of processing. Choosing the wrong one is the source of
most "why doesn't this field exist" and "why don't these venues agree" confusion.

| Layer | What it is | Access |
|---|---|---|
| **Raw / per-venue** | The exchange's own fields, as published, per feed generation | `Security` |
| **Harmonised** | Common column names and types across venues; venue semantics preserved | `Security` |
| **Normalised** | Common *values* too — market states, trade types, sides, LOB actions mapped to one vocabulary | `NormalisedSecurity` |

Normalised is the default for cross-venue work. Raw is for auditing BMLL's mapping or reading a
venue-specific flag that normalisation deliberately discards.

## Normalised tables

| Table | Access function | Contents |
|---|---|---|
| Limit Order Updates | `incremental_book_L2()`, `incremental_book_L3()` | Instructions changing the book. Sufficient to reconstruct L2/L3 |
| Market State | `market_info()` | Market phases, where the exchange provides them |
| Snapshots | `best_bid_offer()`, `rebuilt_book_L2()`, `rebuilt_book_L3()` | Book state for every event that changed it |
| Trades | `trades()`, `all_trades()` | Trades and executions |
| Auction | `auction_data()` | Auction imbalance / indicative price and quantity |
| Exchange Metrics | `exchange_metrics()` | Exchange-supplied metrics with a fixed schema across every venue |

Tables not listed here have the same shape as the Harmonised data.

### Limit Order Updates — the event model

A row is one **instruction** to change the book: add, remove or modify an order (L3) or a level
(L2). `quote_no` numbers rows sequentially from 1 and uniquely identifies one.

`event_no` is different and it is the one that matters for analysis. An *event* is a valid book
state that could in principle be traded against. Many instructions can belong to one event, so
`event_no` repeats. **`end_of_event` is `True` only on the last row of an event** — filter on it
when you need genuine book states rather than intermediate ones, or you will analyse states that
never existed for anyone to trade against.

Key L3 fields:

| Field | Notes |
|---|---|
| `event_no`, `quote_no`, `end_of_event` | Event model above |
| `side`, `price`, `size`, `order_id` | State **after** the instruction. For `REMOVE`/`UNKNOWN`: price `NaN`, size `0`, order_id `''` |
| `old_price`, `old_size`, `old_order_id` | State **before** the instruction |
| `lob_action` | `INSERT` / `UPDATE` / `REMOVE` / `SKIP` / `UNKNOWN` |
| `order_executed` | `True` when the instruction was an execution |
| `execution_price`, `execution_size` | Populated on executions; `execution_size == old_size - size` |
| `price_level`, `old_price_level` | Level after/before (1 = best) |
| `original_order_id` | ID originally assigned — **the key for tracing an order's life** |
| `trade_id` | Exchange trade ID, else a synthetic ID prefixed `BMLL-` |
| `size_ahead`, `orders_ahead` | Queue position ahead of this order on INSERT/UPDATE |
| `modify_count` | Times the order has been modified |
| `best_{bid,ask}_{price,size,num_orders}` | Touch at the time of the event |
| `total_{bid,ask}_{size,orders}` | Whole-book totals after this row |
| `level_size_total`, `level_num_orders_total` | At this price level after this row |
| `is_new_best_price`, `is_new_best_size` | Whether this instruction moved the touch |
| `market_state`, `printable` | Normalised state; printability |
| `is_implied`, `level_size_implied`, `level_num_orders_implied` | Implied book (futures). `False`/absent where no implied book exists |

L2 updates carry the level-oriented equivalents (`num_orders`, `old_num_orders`, `size_implied`,
`truncate_excess_levels`, `msg_original_type`, `price_lvl_num_orders`).

`truncate_excess_levels` distinguishes a real cancellation from bookkeeping when a level falls out
of the feed's depth window — counting those as cancellations overstates cancel rates on depth-
limited L2 feeds.

## Normalised value vocabularies

These are the values `NormalisedSecurity` guarantees across every venue. Access them as objects
from `bmll2.normalised` (`MARKET_STATES`, `SIDES`, `BMLL_TRADE_TYPES`, `TRADE_ACTIONS`,
`L2_LOB_ACTIONS`, `L3_LOB_ACTIONS`) rather than as string literals.

### Market state

| Value | Meaning |
|---|---|
| `PRE_OPEN` | Before continuous trading, generally before the opening auction |
| `OPENING_AUCTION` | Includes order entry **and** uncrossing |
| `CONTINUOUS_TRADING` | Main continuous session |
| `CONTINUOUS_TRADING_PRIMARY_CLOSED` | Secondary venue trading while the primary is closed (MTF/ATS) |
| `INTRADAY_AUCTION` | Scheduled auction interrupting continuous trading |
| `UNSCHEDULED_AUCTION` | Unscheduled auction interrupting continuous trading |
| `AUCTION_ON_DEMAND` | Auction running *alongside* continuous trading |
| `CLOSING_AUCTION` | Includes order entry and uncrossing |
| `POST_TRADE` | After continuous trading, generally after the primary closing auction. Includes trade-at-last — distinguish those via the `CLOSING_PRICE` trade type |
| `CONDITIONAL` | Uncrossing for specific mechanisms (e.g. Turquoise Plato Uncross) |
| `HALTED` | Unscheduled halt, e.g. circuit breaker |
| `CLOSED` | No trading activity possible |
| `NOT_APPLICABLE` | Venues without market phases — trade reporting facilities |
| `UNKNOWN` | Could not be resolved |

Auction states **span order entry and uncrossing**, so filtering `market_state == CLOSING_AUCTION`
gives the whole call phase, not just the uncross. Isolate the uncross with
`bmll_trade_type == UNCROSSING`.

`NOT_APPLICABLE` is not missing data — it is the correct state for a TRF/APA.

### Side

`ASK`, `BID`, `UNKNOWN` (not provided and not inferable).

### BMLL trade type

| Value | Meaning |
|---|---|
| `LIT` | Match on a lit book with full pre-trade transparency. **Includes hidden-order executions that cannot be distinguished from lit ones**, such as icebergs |
| `DARK` | Match on a book with limited or waived pre-trade transparency (e.g. mid-price book) |
| `UNCROSSING` | Paired quantity matched at the end of an auction phase |
| `CLOSING_PRICE` | Executed at the closing price from a Market-on-Close order |
| `BENCHMARK_PRICE` | Executed at a public reference price — VWAP, TWAP, EBBO, PBBO, auction reference; MiFID II `BENC` |
| `REQUEST_FOR_QUOTE` | Resulting from an RFQ |
| `OTC` | Bilateral, reported to a trade reporting facility |
| `SPECIAL_PRICE` | Negotiated, `TNCP`, dividends, technical trades |
| `EXCHANGE_FOR_RELATED_POSITION` | Futures position exchanged for related positions |
| `UNKNOWN` | Unresolved |

That `LIT` absorbs indistinguishable iceberg executions is a real limit on "visible liquidity"
analysis — the lit bucket is an upper bound, not a clean measure.

### Trade action

`NEW` (the majority), `AMEND`, `CANCEL`, `UNKNOWN`. Amendments and cancellations are why `Size`
can be negative and why `Printable` exists.

### LOB actions

**L3:** `INSERT`, `UPDATE`, `REMOVE` (cancellation *or* execution — check `order_executed` to tell
them apart), `SKIP`, `UNKNOWN`.

**L2:** `NEW`, `UPDATE`, `DELETE`, `SKIP`.

`SKIP` is reserved for instructions derived from exchange data that do **not** change book state
but may still carry relevant data. In L3, `quote_no` increments on a SKIP but `event_no` does not.
Counting `quote_no` as an activity measure therefore counts non-events.

## Normalisation process

`https://lab.bmlltech.com/docs/contents/data_ref/normalisation_process.html`

### Icebergs

Exchanges represent icebergs in three ways: a distinct non-displayed trade message; an *over-fill*
(execution larger than the visible order, sometimes with a size-remaining field); or nothing at all.

BMLL matches the exchange's representation rather than imposing one:

- On over-fills, `execution_size` is the **full** execution even when larger than the visible
  order; `revealed_qty` shows the liquidity that became visible.
- Orders are synthetically re-added when further hidden liquidity remains.
- Where no order information exists, the execution appears in the Trades table only — it cannot be
  attached to a visible quantity.

Consequently iceberg detectability varies by venue, and each venue's dataset page documents its
representation. Cross-venue hidden-liquidity comparisons are comparing different observability, not
different behaviour.

### Auctions

Venues differ in what they publish — full call-phase order book, indicative price/quantity, or
both. These are not mutually exclusive, and the venue page states which apply.

### Region-specific trade condition mapping

**US** (`original_trade_type` contains):

| Code | Description | BMLL trade type |
|---|---|---|
| `7` | Qualified Contingent Trade | `SPECIAL_PRICE` |
| `4` | Derivatively Priced | `SPECIAL_PRICE` |
| `R` | Seller's Option | `SPECIAL_PRICE` |
| `C` | Cash | `SPECIAL_PRICE` |
| `Z` | Sold, Out of Sequence | `SPECIAL_PRICE` |
| `N` | Next Day Trade | `SPECIAL_PRICE` |
| `B` | Bunched Trades | `SPECIAL_PRICE` |
| `W` | Average Price | `BENCHMARK_PRICE` |
| `P` | Prior Reference Price | `BENCHMARK_PRICE` |

**Canada** maps by cross type, with columns for whether the cross updates the BBO, is subject to
interference, and sets the last trade.

**Europe** maps from MMT fields. The docs carry the full MMT→BMLL trade-type table plus a
`market_mechanism`/`trading_mode`/`execution_venue` combination rule.

### LIS thresholds

Above/below Large-In-Scale splits in the classified-trade taxonomy depend on a LIS value. BMLL
documents, per venue operator, the primary and secondary LIS source (a 25-row table keyed by
reporting venue MICs). LIS is also available per security as
`exchange_metadata.lis_local`.

Because LIS is sourced per venue operator, above/below-LIS splits are only comparable across venues
to the extent their LIS sources agree — a caveat worth stating when reporting the split.

## MMT

`https://lab.bmlltech.com/docs/contents/data_ref/mmt.html`

Market Model Typology flags, carried on European trades. BMLL uses the **efficient encoding**
(single characters), not the raw exchange strings.

**Three versions coexist: v3.04, v4.1 and v5.0**, and the level semantics change between them.
v4.1 splits several v3.04 levels into pairs (3.1 gains 3.13 RFMD Give-up; 3.2 gains 3.10 waiver
size/scale; 3.5 gains 3.11 PORT and 3.12 CONT; 4.1 gains 4.3 ILQD and 4.4 SIZE; 5 becomes 5.1/5.2/
5.3), and v5.0 adds 3.14 CLSE and renames level 3.3 to Agency Cross Trade Indicator. Decoding a
long history with one version's table mislabels the other periods.

Level 1 — Market Mechanism (v3.04):

| Value | Meaning |
|---|---|
| `1` | Central Limit Order Book |
| `2` | Quote Driven Market |
| `3` | Dark Order Book |
| `4` | Off Book |
| `5` | Periodic Auction |
| `6` | Request For Quotes |
| `7` | Any Other, Including Hybrid |

Level 2 — Trading Mode (v3.04):

| Value | Meaning |
|---|---|
| `1` | Undefined Auction |
| `2` | Continuous Trading |
| `3` | At Market Close Trading |
| `4` | Out Of Main Session |
| `5` | Trade Reporting (On Exchange) |
| `6` | Trade Reporting (Off Exchange) |
| `7` | Trade Reporting (Systematic Internaliser) |
| `O` | Scheduled Opening Auction |
| `K` | Scheduled Closing Auction |
| `I` | Scheduled Intraday Auction |
| `U` | Unscheduled Auction |

Remaining levels (3.1–3.9, 4.1–4.2, 5) are in the docs and on `https://www.fixtrading.org/mmt/`.
In Trades Plus these are the `MarketMechanism`, `TradingMode`, `TransactionCategory`,
`NegotiatedTrade`, `CrossingTrade`, `ModificationIndicator`, `BenchmarkIndicator`,
`SpecialDividendIndicator`, `OffBookAutomatedIndicator`, `PriceFormation`, `AlgorithmicTrade`,
`PublicationMode`, `DeferralType` and `DuplicativeIndicator` columns.

## Other schema pages

| Page | Contents |
|---|---|
| `data_ref/reference_data.html` | Reference-data schema — the identifier model behind `reference.query` |
| `data_ref/l2_schema.html` | Equity, OPRA and futures market-data schemas |
| `data_ref/harmonised_schema.html` | Harmonised tables, trade source / BMLL trade type / modification indicator vocabularies, SI quote, manual orders, exchange-metrics enum reference (~100 labels) |
| `data_ref/analytics.html` | Trades Plus, daily classified trades, daily analytics — see [trades-plus.md](trades-plus.md) |
| `data_ref/metadata.html` | Exchange and third-party metadata |
| `data_ref/calendar.html` | Financial calendar schema |
| `data_ref/shares_outstanding.html` | Shares outstanding / free float schema |
| `data_ref/retail.html` | Retail mechanisms and indicators — see [retail.md](retail.md) |
| `data_ref/datasets/<Venue>.html` | Per-venue detail — see [venues.md](venues.md) |
