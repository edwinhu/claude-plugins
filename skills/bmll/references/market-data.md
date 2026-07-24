# Market Data: `get_market_data` and `get_market_data_range`

Whole-market access, indexed by MIC and date. For per-listing order-book work use
[security-api.md](security-api.md) instead.

```python
import pandas as pd, polars as pl
from bmll2 import get_market_data, get_market_data_range, get_market_tables, get_df_engines
pd.options.display.max_columns = None
```

## Picking a table

`get_market_tables()` is the authority — table availability varies by asset class:

| asset-class | reference | cbbo | trades-plus | l1 | l2 | l3 | trades | nbbo | market-state | imbalance | statistics |
|---|---|---|---|---|---|---|---|---|---|---|
| Equity | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Future | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ |
| Option | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ |

Call it rather than trusting this table — it reflects entitlements as well as coverage.

## Single date

```python
df = get_market_data('AQXE', '2024-03-18', 'trades')

get_market_data('XLON', '2024-01-12', 'trades',
                ticker=['BP.', 'VOD'],
                columns=['Ticker', 'MIC', 'ListingId', 'Price', 'Size',
                         'TradeTimestampNanoseconds'])
```

Optional filters: `ticker`, `listing_id`, `columns`. Pushing `columns` down is the single cheapest
optimisation — these tables are wide.

Returns **pandas** by default.

## Dataframe engines

```python
get_df_engines()          # ['pandas', 'polars', 'spark']

df = get_market_data('XLON', '2024-04-25', 'trades', df_engine='polars')
lf = get_market_data('XLON', '2025-06-23', 'trades', df_engine='polars', lazy_load=True)
```

Polars and Spark natively use every core on the workspace (up to 192). `lazy_load=True` returns a
Polars `LazyFrame` for out-of-memory work on a single machine — usually the right answer before
reaching for a cluster.

## Date ranges

```python
df = get_market_data_range(['XLON', 'XPAR', 'XTRA'], '2024-01-01', '2024-01-30', 'trades')
```

Two differences from `get_market_data` that break naive code reuse:

1. Returns a **PySpark** DataFrame, not pandas.
2. `TradeTimestampNanoseconds` and `PublicationTimestampNanoseconds` are **integer nanoseconds**,
   not `numpy.datetime64`.

Use `scripts/bmll_checks.py::coerce_range_timestamps` to normalise (2) — it is idempotent, so it is
safe on either result type.

**Aggregate in Spark before collecting.** `.toPandas()` on a full multi-venue, multi-month result
loads everything into driver memory and takes the session with it:

```python
import pyspark.sql.functions as F

sdf = get_market_data_range(['XLON','BATE','CHIX','TRQX','AQXE','SGMX','BOTC'],
                            '2024-01-01', '2024-10-30', 'trades')
res = (sdf[sdf['InstrumentId'].isin([121429])]
       .groupBy(['TradeDate', 'ExecutionVenue'])
       .agg(F.sum('Size').alias('Size'))
       .toPandas())          # collect only the aggregate
```

That aggregation over 7 venues year-to-date runs in well under a minute.

### Spark SQL

```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

sdf = get_market_data_range(['XLON'], '2024-01-01', '2024-10-28', 'trades')
sdf.createOrReplaceTempView('sdf')

spark.sql("""
    SELECT TradeDate, ExecutionVenue, sum(Size) AS TotalSize
    FROM sdf WHERE InstrumentId = 121429
    GROUP BY TradeDate, ExecutionVenue
""").toPandas()
```

### Raising the driver result limit

When a legitimately large collect is unavoidable:

```python
from pyspark.sql import SparkSession
conf = spark.sparkContext.getConf()
conf.set('spark.driver.maxResultSize', '10g')
spark.stop()
spark = SparkSession.Builder().master('local[*]').config(conf=conf).getOrCreate()
```

Do this at the top of the cell, before any query — it restarts the session.

## Futures

Set `schema='Future'`, and narrow with `product_code` / `maturity_month_year` so only the relevant
slice is read:

```python
tr = get_market_data_range('XCME', '2025-06-01', '2025-09-30', 'trades',
                           schema='Future',
                           product_code=['ES'], maturity_month_year=['202509'],
                           df_engine='polars', lazy_load=True)

daily = (tr.group_by(['TradeDate', 'ContractType'])
           .agg(pl.col('Size').sum().alias('DailyVolume'))
           .collect().to_pandas())
```

Group by or filter on `ContractType` — `Outright` vs `Spread` — or spread legs inflate volume.

## Options (OPRA)

Options are only reachable through these methods (`Security` does not support them). Prefer
`get_market_data_range` even for one day; the conflated feed is still large.

```python
ref = get_market_data_range('OPRA', '2024-11-04', '2024-11-04', 'reference')
ref = ref[ref['UnderlyingTicker'] == 'TSLA']

grouped = (ref.groupby(['ExpiryDate', 'OptionType', 'StrikePrice']).sum()
              .withColumnRenamed('sum(DailyVolume)', 'DailyVolume'))
grouped = grouped[grouped.DailyVolume > 0].sort(['ExpiryDate', 'OptionType', 'StrikePrice'])
```

Tables available: `trades`, `nbbo`, `reference`, `statistics`.

## Consolidated tapes

Where a consolidated tape exists, query it by its MIC — `@SIP` for the US. The `cbbo` and `nbbo`
tables give consolidated touch data; see [order-book-rebuilding.md](order-book-rebuilding.md) for
the deeper `instrument_cbbo` API.

## Before aggregating anything

```python
from bmll_checks import printable_only, assert_non_empty_per_date, describe_coverage

df = printable_only(df)                                   # Size < 0 are cancellations
assert_non_empty_per_date(df, expected_trading_days)      # a blank date hides in a total
describe_coverage(df)                                     # rows per date × venue
```
