# Troubleshooting

## Empty result

The most common BMLL failure, and the one that does not raise. Work through these in order:

| Check | How |
|---|---|
| Does the venue have data on that date? | `reference.availability(mics, date=...)` |
| Was it a trading day for that venue? | `Calendar`, or `market_info()` for a known-good listing |
| Was the listing alive? | `reference.query(..., start_date=, end_date=)` → `IsAlive` |
| Is the table valid for the asset class? | `get_market_tables()` |
| Is `schema='Future'` needed? | Futures reference/market data need it |
| Is the MIC right? | `reference.available_markets()`; note segment MICs collapse to the main MIC |
| Does the venue's history start later? | `available_markets()` → `StartDate` |
| Is this an entitlement gap? | Analytics (`time_series`), index constituents and CBBO need separate subscriptions and return empty, not 403 |
| Did the date range end today? | Derived data is T+1 |

For per-listing coverage gaps, `available_listings(listing_ids, start_date, ...)` gives the dates
with data per listing and feed.

Do not conclude "no activity" until availability has been checked. An empty frame means "the query
found nothing", which is a statement about the query at least as often as about the market.

## Wrong-looking numbers

| Symptom | Likely cause |
|---|---|
| Volume ~2× too high, or oddly netted | Missing `Printable == True` filter; `Size` is negative on cancellations |
| Notionals off by exactly 100× | Mixed `Price` (in `CurrencyCode`) with `InstrumentCurrencyPrice`; minor currency (GBp, ZAc) needs `MinorCurrencyFactor` |
| Venue market share attributes everything to a reporting venue | Grouped by `MIC` instead of `ExecutionVenue` |
| Block filter returns nothing | `IsBlock` is the string `'TRUE'`, not a bool |
| Mean `PricePoint` is enormous | `±99999` sentinels (bid == ask) not filtered |
| Markout curve is flat near zero | Sign not normalised for `AggressorSide == 2`; buys and sells cancelled |
| Markouts drop most rows | `AggressorSide == 0` not inferred |
| Futures volume too high | `ContractType == 'Spread'` legs counted alongside outrights |
| Timestamps in 1970 | Integer-nanosecond columns from `get_market_data_range` treated as datetimes |
| `pd.Grouper(freq='5T')` raises | `'T'` removed in pandas 2.2; use `'5min'` |
| Daily volume missing lit executions | Used `trades()` instead of `all_trades()`; on `Security`, `trades()` is off-book only |
| Two classification products disagree | `OFF_BOOK_ON_EXCHANGE` is scoped differently in Trades Plus vs `classified_trades` — they are not reconcilable |
| Market-cap panel has lookahead | `late_flag=True` rows were amended after `effective_date` |

`scripts/bmll_checks.py` covers the mechanical ones — `printable_only`,
`drop_price_point_sentinels`, `normalise_enum_flags`, `coerce_range_timestamps`.

## Memory and Spark

**Driver OOM / session death** — almost always `.toPandas()` on a full `get_market_data_range`
result. Aggregate in Spark first, collect the aggregate.

**`spark.driver.maxResultSize` exceeded** — raise it *before* running any query, at the top of the
cell (this restarts the session):

```python
from pyspark.sql import SparkSession
conf = spark.sparkContext.getConf()
conf.set('spark.driver.maxResultSize', '10g')
spark.stop()
spark = SparkSession.Builder().master('local[*]').config(conf=conf).getOrCreate()
```

**Out-of-memory on a single machine** — use Polars lazy frames:

```python
df = get_market_data(mic, date, table, df_engine='polars', lazy_load=True)
```

**Full-day L3 book too large to materialise** — use `rebuilt_history_L2/L3` with `apply(...)` so
metrics are computed during the replay, or `date_range_sampler_on_the_fly` for the
minimal-memory generator. See [order-book-rebuilding.md](order-book-rebuilding.md).

**Slow per-listing loops** — parallelise with `SparkHelper.map` before considering a cluster; a
workspace has up to 192 cores. See [compute-and-storage.md](compute-and-storage.md).

## Lost files

Local writes are destroyed when the workspace stops. If the workspace is still running, persist
now with `b2.put_file(...)`. If it stopped, the local copy is gone — but files that were in a
persistent area and then deleted are recoverable for **7 days**:

```python
b2.list_deleted_files('path')
b2.get_file_history(path='path/file.csv')
b2.recover_file_version('path/file.csv', recovery_file_name='recovered.csv')
```

## Slow reference queries

`reference.query` without a date returns the latest snapshot and is the fast path. Adding
`start_date`/`end_date` makes it point-in-time and markedly slower.

For a whole market on one date, `get_market_data(mic, date, 'reference')` is faster than
`reference.query`.

For large `time_series` universes, split the query by MIC, and by date for ranges beyond a year.

## Cluster jobs with no diagnostics

`create_cluster` only logs if a log path is supplied. Without one, a failed job leaves nothing to
read. Always pass the log path.

## Scheduled job fired before the data landed

`CronTrigger` fires on wall-clock time regardless of data availability, and BMLL data is T+1 with
variable arrival. Use `L3Availability` as the trigger for data-dependent pipelines.
