# Trades Plus

`get_market_data(mic, date, 'trades-plus')` — Equities only.

The single most useful BMLL dataset for execution quality. Trades are rarely analysable in
isolation: judging a fill requires the prevailing quote, the consolidated touch, and the
subsequent price path. Trades Plus ships all of that **pre-joined onto every trade row** —
venue/primary/consolidated BBO as of the trade, 30 pre- and post-trade midpoint columns, the
BMLL trade classification, block thresholds, lot type and participant type.

That means the questions below need no merge against a quotes table:

- intraday volume by lit/dark/SI/OTC classification
- block liquidity by venue and time of day
- spread capture / price improvement against the consolidated touch
- pre- and post-trade markouts (market impact)
- retail vs institutional participation

## Loading

```python
import pandas as pd
from bmll2 import get_market_data

pd.options.display.max_columns = None

tp, ref = [], []
for mic in ['XLON', 'BATE', 'CHIX', 'TRQX', 'AQXE', 'SGMX', 'BOTC']:
    tp.append(get_market_data(mic, '2025-08-22', 'trades-plus'))
    ref.append(get_market_data(mic, '2025-08-22', 'reference'))

tp = pd.concat(tp, ignore_index=True, copy=False)
ref = pd.concat(ref, ignore_index=True, copy=False)
```

Trades Plus has no `InstrumentType`; join the `reference` table for it:

```python
tp = tp.merge(ref[['ListingId', 'InstrumentType']], on='ListingId', how='left')
equity = tp[(tp.InstrumentType == 'Equity') & (tp.Jurisdiction == 'UK')].copy()
```

For multi-day or multi-region pulls use `get_market_data_range` (Spark; see
[market-data.md](market-data.md)) and aggregate before collecting.

## Traps specific to this dataset

- `IsBlock` / `LotType` / `ParticipantType` / `BMLLParticipantType` are **string enums**, including
  a literal `'UNKNOWN'` level. `IsBlock == True` matches nothing.
- `PricePoint*` uses `±99999` when best bid equals best ask, and `0.5` when the trade price equals
  both. Filter before aggregating.
- `AggressorSide` is `0` (unknown) on most off-book and auction prints. No side inference is
  supplied — see [Markouts](#markouts).
- `Size` may be negative (cancellation); `Printable` decides inclusion in cumulative volume.
- `MIC` is the *reporting* venue; `ExecutionVenue` is where it actually executed.
- `BestBidPriceAtVenue` / `BestAskPriceAtVenue` are **only populated for lit exchanges** — null on
  dark and off-book rows, which quietly drops those rows from any venue-relative calculation.
- `Classification`'s `OFF_BOOK_ON_EXCHANGE` bucket is scoped **differently** from the daily
  `classified_trades` product: here it holds only `BMLLTradeType == 'OTC'` with a non-`SINT`/`XOFF`
  `ExecutionVenue`, whereas the daily product also folds in `BENCHMARK_PRICE`/`SPECIAL_PRICE`.
  The two will not reconcile to the share; do not present them as cross-checks of each other.
- MMT flags use BMLL's **efficient encoding** (single chars), not the raw exchange strings.
- BMLL's published notebooks use `pd.Grouper(freq='5T')`; `'T'` was removed in pandas 2.2 — use
  `'5min'`.

## Recipes

### Intraday volume by classification

`Classification` carries the same taxonomy as the daily classified-trades product, so intraday
bars need no extra work:

```python
equity['TradingDate'] = pd.to_datetime(equity['TradeTimestamp'].dt.date)

intr = (equity[equity.TradingDate == '2025-08-22']
        .groupby(['Classification',
                  pd.Grouper(key='TradeTimestamp', freq='5min', label='right')],
                 as_index=False)[['TradeNotionalEUR']].sum())

px.bar(intr, x='TradeTimestamp', y='TradeNotionalEUR', color='Classification',
       template='plotly_white')
```

### Block liquidity by venue and time of day

Shows when in the day large size is actually available:

```python
blocks = equity[equity.IsBlock == 'TRUE']          # string enum
intr_blocks = (blocks[blocks.TradingDate == '2025-08-22']
               .groupby(['ExecutionVenue',
                         pd.Grouper(key='TradeTimestamp', freq='5min', label='right')],
                        as_index=False)[['TradeNotionalEUR']].sum())
```

`TradeNotionalBlockBps` (10,000 × trade notional / block threshold) gives a continuous measure of
size relative to the jurisdiction's block threshold, and is `NaN` where the threshold is unknown.
`BlockSize` and `BlockNotional` are independent thresholds — the notional threshold is *not*
derived from the size threshold.

### Spread capture against the consolidated touch

`PricePoint = (TradePrice − BidPrice) / (AskPrice − BidPrice)` against the consolidated/national
book (or primary, per region). `0` = traded at bid, `1` = at ask, `0.5` = at mid, outside `[0,1]`
= outside the touch.

```python
d = equity[equity.TradingDate == '2025-08-22'].copy()
d['PricePointRounded'] = d.PricePoint.round(1)

pp = d.groupby(['PricePointRounded', 'ExecutionVenue'],
               as_index=False)[['TradeNotionalEUR']].sum()

px.bar(pp[pp.PricePointRounded.between(-2, 2)],      # excludes ±99999 sentinels
       x='PricePointRounded', y='TradeNotionalEUR', color='ExecutionVenue')
```

The `between(-2, 2)` filter is doing real work — without it the sentinel rows swamp the chart and
any mean.

Variants: `PricePointAtVenue` (vs the reporting venue's own book, lit only),
`PricePointAtPrimary` (vs the primary exchange).

### Markouts

The dataset supplies midpoints at 15 pre- and 15 post-trade intervals — 1ms, 2, 5, 10, 25, 50,
100, 200, 500ms, 1s, 5s, 15s, 30s, 60s, 300s — in two families:

- `PreTradeMid{X}ms` / `PostTradeMid{X}ms` — consolidated/national (or primary, per region)
- `PreTradeMid{X}msAtPrimary` / `PostTradeMid{X}msAtPrimary` — primary exchange

Plus `PostTradeMid`, `PostTradeMidAtPrimary`, and volume-clock marks
`PostTradeBestBid/Ask{1,3,10}V` (the touch after 1×/3×/10× the trade's volume has printed across
venues, counting lit continuous trades only).

**Sign convention is the whole game.** A markout must be normalised to one perspective —
conventionally an aggressive buyer — or buys and sells cancel and the series flattens toward zero,
which reads as "no impact" rather than "you averaged two opposite signs."

Because `AggressorSide == 0` is common, the side must be inferred. BMLL's own notebook uses
Lee-Ready: price above mid → buyer-initiated; below → seller-initiated; at mid → use the next
tick.

**Do not hand-roll this.** Use the script:

```bash
python scripts/bmll_markouts.py --help
```

```python
from scripts.bmll_markouts import infer_aggressor_side, compute_markouts, aggregate_markouts

d = infer_aggressor_side(d, benchmark='primary')         # fills AggressorSide == 0
d, markout_cols = compute_markouts(d, benchmark='primary')   # bps, sign-normalised
agg = aggregate_markouts(d, markout_cols, group_by=['Classification'])  # notional-weighted
```

`aggregate_markouts` weights by `TradeNotionalEUR` rather than taking a simple mean — an unweighted
average lets a cloud of tiny odd-lot prints outvote the institutional flow the analysis is about.

Group by `Classification` to compare impact of lit continuous vs dark vs SI; by `ExecutionVenue`
for venue comparison; by `BMLLParticipantType` for retail vs institutional.

### Retail participation

`ParticipantType` holds venue-specific values (`RETAIL`, `THIRD_PARTIES` (XMAD),
`OWN_ACCOUNT` (XMAD), `SPECIALIST` (XMAD), `CUSTODIAN_PARTICIPANT` (XNSE), `PROPRIETARY` (XNSE),
`NON_CP_NON_PROPRIETARY` (XNSE), `UNKNOWN`), derived from exchange flags where available and
heuristics otherwise. `BMLLParticipantType` normalises these globally to `RETAIL`,
`INSTITUTIONAL`, `MARKET_MAKER`, `BROKER_DEALER`, `UNKNOWN`.

Use `BMLLParticipantType` for cross-venue work; `ParticipantType` only when you need the
venue-native distinction. Both carry a large `UNKNOWN` share — report it rather than dropping it.

## Schema

~99 fields. Grouped by purpose.

### Identifiers and timestamps

| Field | Type | Notes |
|---|---|---|
| `ExchangeTicker` | varchar | Ticker as provided by the reporting venue |
| `Ticker` | varchar | FactSet ticker |
| `MIC` | char(4) | Exchange the trade is **reported** for |
| `OPOL` | char(4) | MIC of the Original Place Of Listing |
| `ISOCountryCode` | char(3) | ISO country of the primary listing exchange |
| `SegmentCode` | varchar | Exchange segment of execution |
| `InstrumentCurrencyCode` | varchar(3) | Currency the instrument quotes in |
| `ListingId` | bigint | BMLL listing identifier |
| `InstrumentId` | bigint | BMLL instrument identifier (0 if absent) |
| `TradeDate` | datetime | Date the trade was published |
| `TradeTimestamp` | timestamp | Execution time, UTC, µs precision |
| `PublicationTimestamp` | timestamp | Report time, UTC, µs precision |
| `LocalTradeTimestamp` | timestamp | Execution time, reporting venue's local zone |
| `LocalPublicationTimestamp` | timestamp | Report time, local zone |
| `TradeTimestampNanoseconds` | timestamp | Execution time, UTC, ns precision |
| `PublicationTimestampNanoseconds` | timestamp | Report time, UTC, ns precision |
| `TZOffset` | integer | Local−UTC offset in seconds, DST-adjusted; based on the primary listing's location |
| `TradeId` | varchar | Exchange-provided trade ID |
| `ExchangeSequenceNo` | bigint | Exchange sequence number, where provided |
| `BMLLSequenceNo` | bigint | Synthetic sequence allowing message reordering within security/date |
| `BMLLSequenceSource` | bigint | Feed source, where on/off-book arrive on different feeds |

### Price, size, currency

| Field | Type | Notes |
|---|---|---|
| `AggressorSide` | integer | `1` ask aggressed by buy, `2` bid aggressed by sell, `0` unknown |
| `Price` | double | In `CurrencyCode` |
| `Size` | double | Negative if the execution is a cancellation |
| `CurrencyCode` | char(3) | Currency the trade executed in |
| `InstrumentCurrencyPrice` | double | Price in `InstrumentCurrencyCode` |
| `MinorCurrencyFactor` | double | Minor→major scale where the instrument currency is a minor (GBp, ZAc) |
| `Printable` | bool | Whether to include in cumulative volume |
| `ExecutionVenue` | char(4) | MIC where execution occurred; differs from `MIC` for reported trades |
| `TradeNotional` | double | In `CurrencyCode` |
| `TradeNotionalUSD` | double | FX as of publication date |
| `TradeNotionalEUR` | double | FX as of publication date |

### Trade type, MMT flags, classification

| Field | Type | Notes |
|---|---|---|
| `OriginalTradeType` | varchar | Concatenated exchange-reported flags; exchange-dependent |
| `MarketMechanism` | char(1) | MMT Level 1 |
| `TradingMode` | char(1) | MMT Level 2 |
| `TransactionCategory` | char(1) | MMT Level 3.1 |
| `NegotiatedTrade` | char(1) | MMT 3.2 |
| `CrossingTrade` | char(1) | MMT 3.3 |
| `ModificationIndicator` | char(1) | MMT 3.4 |
| `BenchmarkIndicator` | char(1) | MMT 3.5 |
| `SpecialDividendIndicator` | char(1) | MMT 3.6 |
| `OffBookAutomatedIndicator` | char(1) | MMT 3.7 |
| `PriceFormation` | char(1) | MMT 3.8 |
| `AlgorithmicTrade` | char(1) | MMT 3.9 |
| `PublicationMode` | char(1) | MMT 4.1 |
| `DeferralType` | char(1) | MMT 4.2 |
| `DuplicativeIndicator` | char(1) | MMT 5 |
| `Jurisdiction` | char(2) | ISO country of execution/publication venue; `EU` for Europe |
| `MarketState` | varchar | BMLL-standardised market state |
| `MarketStateAtPrimary` | varchar | BMLL-standardised state of the primary exchange |
| `BMLLTradeType` | varchar | BMLL-standardised trade type |
| `Classification` | varchar | One of the 22 classified-trade buckets (see caveat above) |
| `BrokerIdBuyer` | varchar | Passive buy-side broker, where the exchange provides it |
| `BrokerIdSeller` | varchar | Passive sell-side broker, where provided |

MMT reference: `https://www.fixtrading.org/mmt/`

### Block, lot and participant

| Field | Type | Notes |
|---|---|---|
| `BlockSize` | double | Jurisdictional block threshold in shares; `None` if N/A |
| `BlockNotional` | double | Block notional threshold; independent of `BlockSize` |
| `BlockNotionalCurrency` | char(3) | EUR for Europe/UK, USD elsewhere |
| `TradeNotionalBlockBps` | double | 10,000 × notional / block threshold; `NaN` if threshold unknown |
| `IsBlock` | varchar | `TRUE` / `FALSE` / `UNKNOWN` (string) |
| `LotType` | varchar | `ODD` / `ROUND` / `MIXED` / `UNKNOWN`; odd-lot threshold is region-specific |
| `ParticipantType` | varchar | Venue-specific participant enum (see above) |
| `BMLLParticipantType` | varchar | `RETAIL` / `INSTITUTIONAL` / `MARKET_MAKER` / `BROKER_DEALER` / `UNKNOWN` |

### Prevailing quotes

All are as of the trade timestamp, **before** the trade.

| Field | Notes |
|---|---|
| `BestBidPrice` / `BestBidSize` / `BestBidVenue` | Consolidated/national or primary, per region. US NBBO size is round lots converted to shares. Venue = largest size at the touch, ties broken by first quote at that size |
| `BestAskPrice` / `BestAskSize` / `BestAskVenue` | As above |
| `BestBidPriceAtVenue` / `BestBidSizeAtVenue` | The `MIC` venue's own book — **lit exchanges only** |
| `BestAskPriceAtVenue` / `BestAskSizeAtVenue` | As above |
| `BestBidPriceAtPrimary` / `BestBidSizeAtPrimary` | Primary exchange |
| `BestAskPriceAtPrimary` / `BestAskSizeAtPrimary` | Primary exchange |
| `PriorQuoteSizeRank` | Rank of the execution venue by prevailing quote size across cross-listed venues; `0` = venue was not at BB/BO. Lit trades only, else null |

### Timing, spread capture, impact

| Field | Notes |
|---|---|
| `PreTradeElapsedTimeChg` | Nanoseconds since the last price change on the consolidated/primary book before this trade |
| `PostTradeElapsedTimeChg` | Nanoseconds since the last price change after this trade |
| `PreTradeElapsedTimeChgAtPrimary` / `PostTradeElapsedTimeChgAtPrimary` | Same, primary BBO |
| `PricePoint` | `(Price − Bid) / (Ask − Bid)` vs consolidated/primary. Sentinels: `+99999` if Price > Bid when Bid == Ask, `−99999` if Price < Bid, `0.5` if equal |
| `PricePointAtVenue` | Same vs the `MIC` venue's book — lit only |
| `PricePointAtPrimary` | Same vs the primary book |
| `OrderRemainingQty` | Remaining qty of the passively executed order. Lit on-book only, else null |
| `BBORemainingQtyAtVenue` | Remaining qty at the trade price on the execution venue's touch after the trade; computed from the side opposite the aggressor. On-book only |
| `PreTradeMid{X}ms` / `PostTradeMid{X}ms` | 15 intervals each, consolidated/primary |
| `PreTradeMid{X}msAtPrimary` / `PostTradeMid{X}msAtPrimary` | 15 intervals each, primary |
| `PostTradeMid` / `PostTradeMidAtPrimary` | Midpoint immediately after the trade |
| `PostTradeBestBid{1,3,10}V` / `PostTradeBestAsk{1,3,10}V` | Touch after 1×/3×/10× the trade's volume prints across venues; lit continuous only |

Interval set for the `{X}ms` families: `1, 2, 5, 10, 25, 50, 100, 200, 500, 1000, 5000, 15000,
30000, 60000, 300000`.

## Related

- Daily aggregated equivalent: `bmll.time_series.classified_trades` —
  [analytics-timeseries.md](analytics-timeseries.md)
- Order-book context beyond the touch: [order-book-rebuilding.md](order-book-rebuilding.md)
- Full-market pulls across dates: [market-data.md](market-data.md)
