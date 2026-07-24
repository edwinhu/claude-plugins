# Analytics: `bmll.time_series` and the Data Feed

Pre-computed analytics built from Level 3 data, served over the wire by the **`bmll`** package
(not `bmll2`). Every Lab user can connect; the analytics themselves need a **separate
subscription** — without it queries return empty results rather than a permission error.

Computed **T+1**. Currently equity venues only.

```python
import bmll
```

## Discovering metrics

Never guess a metric name — `available()` is the catalogue:

```python
bmll.time_series.available()
# metric | suffix | group | type | frequency | tags | description | unit
```

A metric is identified by `metric` **plus `suffix`**; the suffix encodes the variant. `tags` holds
the semantics behind the suffix, and `explode_tags=True` turns them into columns for filtering:

```python
bmll.time_series.available(explode_tags=True).query(
    'metric == "AuctionDislocation" and frequency == "D"'
).dropna(axis=1)
# -> Phase=C, Horizon=1m/5m/Last
```

Example groups: `Liquidation cost` (`TradeImbalance` by side and level), `Auction analysis`
(`AuctionDislocation`), `Liquidity classification` (`TradeVolume`/`TradeCount` by classification).
Frequencies include `D` and `30M` — a metric published at `30M` will not answer a daily query.

## Querying

```python
bmll.time_series.query(Ticker='AAL', MIC='XLON',
                       metric='TradeVolume',
                       start_date='2020-01-02', end_date='2020-01-10')
# ListingId Ticker MIC Date TradeVolume|Dark TradeVolume|Lit TradeVolume|Bi TradeVolume|Non
```

Result columns are `metric|suffix`. A bare `metric='TradeVolume'` returns **every** suffix — often
what you want, but it makes the frame wide and the column names are the only place the variant is
recorded.

Multiple metrics:

```python
bmll.time_series.query(Ticker='AAL', MIC='XLON',
                       metric=['TradeCount', 'TradeVolume'],
                       start_date='2020-01-01', end_date='2020-03-31')
```

### Narrowing with a Mongo-style filter

`metric` accepts a MongoDB-style query document, matched against the catalogue:

```python
bmll.time_series.query(Ticker='AALl', MIC='BOTC',
                       metric={'metric': {'$in': ['TradeVolume', 'TradeCount']},
                               'tags.Classification': 'DarkAddressable'},
                       start_date='2020-01-02', end_date='2020-01-10')
```

Tags are addressed as `tags.<Name>`. Reference: MongoDB `db.collection.find` operators.

### Passing the catalogue frame directly

Often clearest — filter `available()` with pandas, hand the result to `query`:

```python
metrics = bmll.time_series.available(explode_tags=True).query(
    'metric in ["TradeVolume","TradeCount"] and Classification == "DarkAddressable" '
    'and frequency == "D"').dropna(axis=1)

bmll.time_series.query(Ticker='AALl', MIC='BOTC', metric=metrics,
                       start_date='2020-01-01', end_date='2020-01-10')
```

The metric selection is then visible and checkable as a frame before any data is pulled.

### Querying a universe

`object_ids` takes listing IDs, so reference data defines the universe:

```python
ref = bmll.reference.query(MIC='XHEL', start_date='2019-12-01', end_date='2019-12-02')

ts = bmll.time_series.query(object_ids=ref.ListingId,
                            metric={'metric': 'TradeVolume', 'suffix': 'Lit'},
                            start_date='2018-01-01', end_date='2020-01-01')
# -> ObjectId | Date | TradeVolume|Lit
```

When queried by `object_ids` the key column is **`ObjectId`**, not `ListingId` — join on it:

```python
df = pd.merge(ref[['ListingId', 'Ticker']], ts, left_on='ListingId', right_on='ObjectId')
df.pivot_table(index='Date', columns='Ticker', values='TradeVolume|Lit').fillna(0)
```

### Large universes

Split by MIC, and for ranges beyond a year by date too:

```python
ref = bmll.reference.query(OPOL=['XLON'])
out = []
for mic in ref.MIC.unique():
    lids = ref.loc[ref.MIC == mic, 'ListingId'].unique().tolist()
    out.append(bmll.time_series.query(object_ids=lids,
                                      metric=['Spread', 'TradeNotional'],
                                      frequency='D',
                                      start_date='2022-01-01', end_date='2022-12-31'))
ts = pd.concat(out, ignore_index=True)
```

(BMLL's published version of this loop appends outside the loop and keeps only the last MIC —
append inside, as above.)

## Classified trades

Daily traded volume, count and notional broken down by a 22-bucket taxonomy and execution venue —
the standard tool for liquidity-fragmentation questions: where liquidity is, how venue mix evolves,
which mechanisms are growing.

```python
ref = bmll.reference.query(ISIN='GB00BH4HKS39',
                           MIC=['XLON','TRQX','BOTC','AQXE','XEQT','XPAR','XAMS'])

trades = bmll.time_series.classified_trades(object_ids=ref['ListingId'],
                                            start_date='2022-02-02',
                                            end_date='2022-02-07')
# Date | PublicationDate | ListingId | ExecutionVenue | Notional | Shares | Count | Classification
```

Currency is normalised to **EUR for Europe/UK and USD elsewhere**. (Via the `bmll2` route you can
choose the currency.)

### The taxonomy

Three levels. Bracketed names are groupings, not values in the data.

```
(ON_EXCHANGE)                        execution_venue not XOFF/SINT
    LIT_CONTINUOUS                   on-book continuous, pre-trade transparent (incl. visible iceberg parts)
    (AUCTIONS)
        LIT_OPENING_AUCTION
        LIT_INTRADAY_AUCTION
        LIT_CLOSING_AUCTION
        AUCTION_ON_DEMAND            periodic auction
    (CLOSING_PRICE)
        CLOSING_PRICE                mechanism guaranteeing the closing price
        POST_TRADE_UNCROSSING        post-close auction (CBOE 3C)
    (DARK)
        DARK_BELOW_LIS
        DARK_ABOVE_LIS
        DARK_CONDITIONAL_BELOW_LIS   hybrid mechanisms (Turquoise UNCROSS + Block Discovery)
        DARK_CONDITIONAL_ABOVE_LIS
    (REQUEST_FOR_QUOTE)
        REQUEST_FOR_QUOTE_BELOW_LIS
        REQUEST_FOR_QUOTE_ABOVE_LIS
    OFF_BOOK_ON_EXCHANGE
(OFF_EXCHANGE)                       execution_venue is XOFF or SINT
    (SI)                             systematic internaliser
        SI_ADDRESSABLE_BELOW_LIS
        SI_ADDRESSABLE_ABOVE_LIS
        SI_NON_ADDRESSABLE_BELOW_LIS
        SI_NON_ADDRESSABLE_ABOVE_LIS
    (OTC)
        BENCHMARK_PRICE              MMT BENC flag
        SPECIAL_PRICE                non-price-forming (dividends, physical delivery, ...)
        OTC                          vanilla OTC
```

**Addressable** means: not `SPECIAL_PRICE`, executed during the primary market's regular hours, and
in the primary market's currency.

Larger buckets are reconstructed by summing, using these maps:

```python
LEVEL3_TO_LEVEL2 = {
  "POST_CLOSE_UNCROSSING": "CLOSING_PRICE",
  "REQUEST_FOR_QUOTES_BELOW_LIS": "REQUEST_FOR_QUOTES",
  "REQUEST_FOR_QUOTES_ABOVE_LIS": "REQUEST_FOR_QUOTES",
  "DARK_BELOW_LIS": "DARK", "DARK_ABOVE_LIS": "DARK", "DARK_CONDITIONAL": "DARK",
  "SI_ADDRESSABLE_BELOW_LIS": "SI", "SI_ADDRESSABLE_ABOVE_LIS": "SI",
  "SI_NON_ADDRESSABLE_BELOW_LIS": "SI", "SI_NON_ADDRESSABLE_ABOVE_LIS": "SI",
  "BENCHMARK_PRICE": "OTC", "SPECIAL_PRICE": "OTC",
  "LIT_OPENING_AUCTION": "AUCTION", "LIT_CLOSING_AUCTION": "AUCTION",
  "LIT_INTRADAY_AUCTION": "AUCTION", "LIT_UNSCHEDULED_AUCTION": "AUCTION",
  "AUCTION_ON_DEMAND": "AUCTION",
}

LEVEL2_TO_LEVEL1 = {
  "LIT_CONTINUOUS": "ON_EXCHANGE", "CLOSING_PRICE": "ON_EXCHANGE", "DARK": "ON_EXCHANGE",
  "AUCTION": "ON_EXCHANGE", "REQUEST_FOR_QUOTE": "ON_EXCHANGE",
  "OFF_BOOK_ON_EXCHANGE": "ON_EXCHANGE",
  "OTC": "OFF_EXCHANGE", "SI": "OFF_EXCHANGE",
}

trades['L2'] = trades['Classification'].replace(LEVEL3_TO_LEVEL2)
trades['L1'] = trades['L2'].replace(LEVEL2_TO_LEVEL1)
```

Note the maps carry BMLL's own key spellings (`POST_CLOSE_UNCROSSING`, `REQUEST_FOR_QUOTES_*`,
`DARK_CONDITIONAL`) which differ from the documented bucket names (`POST_TRADE_UNCROSSING`,
`REQUEST_FOR_QUOTE_*`, `DARK_CONDITIONAL_{BELOW,ABOVE}_LIS`). Unmapped values pass through
`replace` unchanged and land in the L1 layer as themselves — check for leftovers after mapping
rather than assuming a clean two-level rollup:

```python
assert set(trades.L1) <= {"ON_EXCHANGE", "OFF_EXCHANGE"}, set(trades.L1)
```

Hierarchy view:

```python
cols = ['ExecutionVenue', 'L1', 'L2', 'Classification']
px.treemap(trades[cols + ['Notional']].dropna(), path=cols, values='Notional',
           color='ExecutionVenue')
```

### Versus Trades Plus

| | `classified_trades` | Trades Plus `Classification` |
|---|---|---|
| Grain | Daily aggregate | Per trade |
| Intraday | no | yes |
| Currency | EUR/USD by region | plus `TradeNotional` in native currency |
| `OFF_BOOK_ON_EXCHANGE` | includes `BENCHMARK_PRICE`/`SPECIAL_PRICE` | `OTC` type only, non-`SINT`/`XOFF` venue |

The two will not reconcile exactly. Use Trades Plus when intraday or per-trade context matters,
`classified_trades` for long daily histories.

## Consolidated BBO

`bmll.market_data.instrument_cbbo` — see
[order-book-rebuilding.md](order-book-rebuilding.md#consolidated-books).

## Data SDK

`bmll.data` works inside and outside the Data Lab, for dataset discovery and generic queries:

```python
bmll.data.datasets(describe=..., dataset=...)
bmll.data.query(dataset, startDate=..., endDate=...)
```
