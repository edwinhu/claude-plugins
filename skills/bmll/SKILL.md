---
name: bmll
version: 1.0
description: Use when "query BMLL", "BMLL Data Lab", "Trades Plus", "markouts / mark-out analysis", "spread capture / PricePoint", "block trades", "classified trades", "lit vs dark volume", "venue market share", "liquidity fragmentation", "Level 3 / L3 order book", "rebuild the limit order book", "order book snapshot", "MBO / MBP data", "CBBO / EBBO / NBBO", "auction dislocation", "closing auction", "tick data by MIC", "ListingId / listing_id", "bmll2", or "bmll" Python package.
user-invocable: false
---

## Contents

- [Query Enforcement](#query-enforcement)
- [The Two Packages](#the-two-packages)
- [Core Data Model](#core-data-model)
- [Choosing an Access Path](#choosing-an-access-path)
- [Quick Start](#quick-start)
- [Scripts](#scripts)
- [Reference Files](#reference-files)

# BMLL Data Lab

BMLL provides full-depth historical **Level 3** (order-by-order) market data across global
equities, ETFs, futures and US options, plus derived analytics. Work happens inside the **BMLL
Data Lab** — a hosted JupyterLab environment where `bmll2` and `bmll` are pre-installed and
pre-authenticated.

Docs: `https://lab.bmlltech.com/docs/` (requires a logged-in BMLL session).

## Query Enforcement

<EXTREMELY-IMPORTANT>
## The Iron Law of BMLL Results

**NO DATA CLAIM WITHOUT INSPECTING THE ROWS. This is not negotiable.**

BMLL returns an empty frame — not an error — for a valid-but-wrong MIC, a non-trading date, a
listing that was not alive, or a missing entitlement. Handing back an unexamined frame is not
faster help; it silently converts "I asked the wrong question" into "this venue had zero volume,"
and the user builds an analysis on a hole they cannot see.
</EXTREMELY-IMPORTANT>

Before claiming any query succeeded:

1. **VALIDATE** the MIC, `ListingId` and date (`reference.query`, `reference.availability`)
2. **VALIDATE** the table name against `get_market_tables()` for that asset class
3. **EXECUTE** the query
4. **INSPECT** `.head()` — and row counts *per date*, not just overall
5. **VERIFY** the currency, enum and sentinel columns mean what you assume
6. **CLAIM** success only after 1–5 pass

### BMLL API Facts

Non-derivable behaviour of this API. Each is a real property of BMLL, not a restatement of the
rule above.

- **`get_market_data_range` returns a Spark DataFrame, and its
  `TradeTimestampNanoseconds` / `PublicationTimestampNanoseconds` columns are integer nanoseconds
  — not `datetime64` like the `get_market_data` equivalents.** Reusing single-date pandas code
  against a range pull produces timestamps interpreted as epoch-1970 nanos and time-of-day
  bucketing that is silently wrong for every row. Shipping that is the exact incompetence
  inspection exists to catch.
- **`Size` is negative for cancellations and `Printable` is the flag that decides inclusion in
  cumulative volume.** Summing `Size` without `Printable == True` both double-counts amended
  prints and nets out cancellations — a volume number that is wrong in two directions at once,
  and wrong quietly.
- **`Price` is denominated in `CurrencyCode` while `InstrumentCurrencyPrice` is denominated in
  `InstrumentCurrencyCode`, and minor-currency listings (GBp, ZAc) carry `MinorCurrencyFactor`.**
  Mixing the two yields notionals off by 100× on UK and South African names. Prefer the
  pre-computed `TradeNotionalEUR` / `TradeNotionalUSD` where they exist.
- **`ExecutionVenue` is not `MIC`.** A Liquidnet trade reported via CBOE BXTR carries
  `MIC='BOTC'` but `ExecutionVenue='LIQU'`. Venue market-share computed on `MIC` attributes every
  reported trade to the reporting facility — the headline result of the analysis is then an
  artefact of the plumbing.
- **`IsBlock` is a string enum (`'TRUE'`/`'FALSE'`/`'UNKNOWN'`), not a boolean.** `df[df.IsBlock]`
  raises; `df[df.IsBlock == True]` silently returns nothing. Reporting "no block trades" from that
  is an unverified claim presented as a finding.
- **`AggressorSide == 0` means unknown, and it is common on off-book and auction prints.** Trades
  Plus provides no side inference. Treating `0` as a third side, or dropping it silently, changes
  signed-markout results without any error surfacing.
- **`PricePoint` carries `±99999` sentinels** when best bid equals best ask (and `0.5` when price
  equals both). An unfiltered mean is dominated by sentinels and is not a spread-capture number.
- **The Data Lab local filesystem is ephemeral** — `df.to_csv(...)` output is destroyed when the
  workspace stops. Only `bmll2.put_file(...)` persists. Telling the user their results are saved
  when they are on a disk that will be wiped is worse than not saving them.
- **`reference.query` with no date returns the latest snapshot, not history**; passing a date range
  is correct but markedly slower. Use `IsAlive` to test liveness on a date.
- **Classified trades and analytics are computed T+1.** Today's data is not there today; a
  today-inclusive range returns a short series, not an error.
- **`time_series` analytics need an entitlement separate from market data.** A missing subscription
  returns empty results, not a permission error.
- **Venue schemas change at feed migrations, and every field carries an Available-From date.** The
  LSE splits into Pre-GTP and GTP generations with different field sets (11 trade fields vs 56) and
  different normalisation mappings. A multi-year study that does not check the venue page reports a
  feed migration as a regime change — a confident finding about the market that is an artefact of
  the plumbing.
- **Retail flags start mid-history and some mechanisms are excluded entirely.** Xetra's retail flag
  exists only from 2024-05-20, CBOE EU's from 2025-09-08; Turquoise Retail Max and the German
  single-market-maker venues are not in the `RetailTrades` metric at all; US SIP retail is inferred
  from odd-lot + inside-NBBO + sub-penny pricing and misses internalised PFOF. Retail "growth"
  starting exactly on a flag's go-live date is the instrument, not the market.
- **Three MMT versions coexist (v3.04, v4.1, v5.0) and the level semantics differ between them** —
  v4.1 splits several v3.04 levels into pairs, v5.0 adds 3.14 and renames 3.3. Decoding a long
  history with one version's table silently mislabels the other periods.
- **`event_no` repeats across rows; only `end_of_event == True` marks a book state that could
  actually be traded against.** Analysing every row treats intermediate states as real ones.
- **`market_state` auction values span order entry *and* uncrossing.** Filtering
  `CLOSING_AUCTION` returns the whole call phase; isolate the uncross with
  `bmll_trade_type == UNCROSSING`.

### Red Flags — STOP If You Catch Yourself:

| Action | Why Wrong | Do Instead |
|---|---|---|
| About to sum `Size` or notional without a `Printable` filter | Counts cancellations and non-printable prints | Filter `Printable == True` first |
| About to call `.toPandas()` on a full `get_market_data_range` result | Multi-venue multi-month pulls exceed driver memory and kill the session | Aggregate in Spark/Polars, then collect |
| About to write `df[df.IsBlock == True]` | `IsBlock` is a string enum; this returns an empty frame | Compare to `'TRUE'` |
| About to average `PricePoint` across all rows | `±99999` sentinels dominate the mean | Filter to the plausible band (e.g. `-2..2`) first |
| About to group venue market share by `MIC` | Attributes trades to the reporting venue, not the executing one | Group by `ExecutionVenue` |
| About to report "zero volume on venue X" from an empty frame | Empty means "not asked correctly" as often as "no trades" | Check `reference.availability` before concluding |
| About to reuse single-date pandas code on a `_range` result | Timestamps are integer nanos there | Convert timestamps explicitly |
| About to hand-roll a markout loop | Deterministic, easy to get sign-wrong | Use `scripts/bmll_markouts.py` |

### Data Validation Checklist

**For `get_market_data` / `get_market_data_range`:**
- [ ] Table valid for the asset class (`get_market_tables()`)
- [ ] `schema='Future'` set for futures; options only via `get_market_data*`
- [ ] Non-empty result asserted per date
- [ ] `Printable` filter applied before any volume/notional sum
- [ ] Currency column chosen deliberately
- [ ] For `_range`: timestamps converted from integer nanos

**For `Security` / `NormalisedSecurity`:**
- [ ] `listing_id` resolved via `reference.query`, not guessed
- [ ] Feed chosen deliberately (`L2` aggregated vs `L3` order-by-order)
- [ ] `all_trades()` vs `trades()` chosen deliberately
- [ ] Market-state filter applied where continuous trading is assumed

**For `bmll.time_series`:**
- [ ] Metric + suffix confirmed against `available()`
- [ ] `frequency` matches the metric's published frequency
- [ ] Universe chunked by MIC / date range for large pulls

## The Two Packages

Both import in the Data Lab; they are **not** interchangeable.

| Package | What it is | Main surface |
|---|---|---|
| **`bmll2`** | Data Lab API — raw + normalised market data, reference, files, clusters | `reference`, `get_market_data`, `get_market_data_range`, `Security`, `NormalisedSecurity`, `SparkHelper`, `put_file`, `get_fx`, `corporate_actions`, `rebuilder` |
| **`bmll`** | Data Feed API — pre-computed analytics over the wire | `time_series.available/query/classified_trades`, `market_data.instrument_cbbo`, `compute` (jobs), `reference`, `data` |

`bmll.reference` and `bmll2.reference` are the same service.

## Core Data Model

- **Market** — a venue, a trade-reporting facility (TRF/APA), or a consolidated feed. Keyed by
  **MIC** (`XLON`, `BATE`, `@SIP`). BMLL maps a venue's lit book to one MIC, so segments collapse
  (Euronext Growth `ALXP` → `XPAR`).
- **Instrument** — a fungible set of listings across venues (equities) or one traded product
  (futures). Keyed by `InstrumentId`. The cross-venue unit.
- **Listing** — orders and trades for one instrument on one market. Keyed by `ListingId`. The unit
  nearly every call takes.

`OPOL` (Original Place Of Listing) is the most reliable way to scope "all UK-primary names".
`ISIN`, `MIC`, `CurrencyCode`, `OPOL` are the most consistently populated identifiers; `FIGI`,
`SegmentCode`, `Ticker` are best-effort.

## Choosing an Access Path

| You want | Use | See |
|---|---|---|
| Trades + prevailing quotes + markouts, ready to analyse | `get_market_data(mic, date, 'trades-plus')` | [trades-plus.md](references/trades-plus.md) |
| A whole market's trades/quotes for one date | `get_market_data(mic, date, table)` | [market-data.md](references/market-data.md) |
| Many markets × many dates | `get_market_data_range(mics, start, end, table)` | [market-data.md](references/market-data.md) |
| One listing's order book, replayed | `NormalisedSecurity.from_listing_id(...).market_data(feed='L3')` | [security-api.md](references/security-api.md) |
| Custom order-book metrics over the day | `md.rebuilt_history_L2/L3(apply(...))` | [order-book-rebuilding.md](references/order-book-rebuilding.md) |
| Daily analytics (spread, volume, auction dislocation) | `bmll.time_series.query(...)` | [analytics-timeseries.md](references/analytics-timeseries.md) |
| Daily lit/dark/SI/OTC split | `bmll.time_series.classified_trades(...)` | [analytics-timeseries.md](references/analytics-timeseries.md) |
| Cross-venue consolidated book | `bmll.market_data.instrument_cbbo(...)` | [order-book-rebuilding.md](references/order-book-rebuilding.md) |
| Impact around non-trade events, sub-ms horizons, or cross-product | `scripts/bmll_impact.py` over an L3 book | [market-impact.md](references/market-impact.md) |
| Retail flow | `BMLLParticipantType` (Trades Plus), `RetailTrades` metric, or `all_trades(columns=[...,'retail'])` | [retail.md](references/retail.md) |
| What a venue publishes / how it is normalised | Venue dataset page | [venues.md](references/venues.md) |
| Market cap / free float / FX / calendar | `bmll2.corporate_actions`, `get_fx`, `Calendar` | [other-datasets.md](references/other-datasets.md) |

Asset-class support differs by method:

| Asset class | `get_market_data*` | `Security` / `NormalisedSecurity` |
|---|---|---|
| Equities | yes | yes |
| Futures | yes (`schema='Future'`) | yes |
| Options (OPRA) | yes | **no** |

## Quick Start

```python
from bmll2 import reference, get_market_data, NormalisedSecurity

ref = reference.query(ISIN='GB00BH4HKS39', MIC='XLON')
listing_id = int(ref.ListingId.iloc[0])          # 121317 — Vodafone on the LSE

tp = get_market_data('XLON', '2025-08-22', 'trades-plus')

md = NormalisedSecurity.from_listing_id(listing_id=listing_id, date='2022-04-21').market_data()
book = md.l2_snapshot(depth=5)
trades = md.all_trades()
```

Dates are ISO strings (`'YYYY-MM-DD'`) or `datetime.date` throughout.

## Scripts

Deterministic multi-step computations live in `scripts/` — invoke them rather than re-deriving the
logic inline. Hand-rolled versions get the markout sign convention or the sentinel filtering wrong
and the error is invisible in the output.

| Script | Purpose |
|---|---|
| `scripts/bmll_markouts.py` | Pre/post-trade markouts from a Trades Plus frame: side inference (Lee-Ready), bps conversion, aggressor-normalised sign, notional-weighted aggregation by any grouping |
| `scripts/bmll_impact.py` | Event-time market impact from an L3 book: arbitrary events, arbitrary horizons, cross-product (events in one book vs another book's midpoint) |
| `scripts/bmll_checks.py` | Validation helpers: per-date non-emptiness, `Printable`/currency sanity, `PricePoint` sentinel filtering, `IsBlock` normalisation |

Run with `python scripts/bmll_markouts.py --help` inside the Data Lab, or import the functions.
`scripts/test_bmll_scripts.py` and `scripts/test_bmll_impact.py` cover them against synthetic
frames built to the documented schema — they do not exercise the live API.

## Reference Files

Load the file that matches the task — do not read them all.

| File | Covers |
|---|---|
| [references/trades-plus.md](references/trades-plus.md) | **Trades Plus**: full ~99-field schema, markouts, `PricePoint` spread capture, block analysis, participant type, intraday classification |
| [references/schemas.md](references/schemas.md) | Raw vs harmonised vs normalised layers, LOB event model, the normalised value vocabularies (market state, trade type, LOB actions), normalisation process (icebergs, auctions, regional trade conditions, LIS), MMT |
| [references/venues.md](references/venues.md) | Per-venue datasets: MIC index for all 88 venues, feed generations and Available-From dates, member attribution |
| [references/reference-data.md](references/reference-data.md) | `reference.query`, markets/instruments/listings, indices and constituents, futures, OPRA options |
| [references/market-data.md](references/market-data.md) | `get_market_data` / `_range`, tables, dataframe engines, Spark SQL, futures and options access |
| [references/security-api.md](references/security-api.md) | `Security` vs `NormalisedSecurity`, `MarketData` tables, normalised enums, market states |
| [references/order-book-rebuilding.md](references/order-book-rebuilding.md) | `l2_snapshot`, `rebuilt_book_*`, snapshot generators, `rebuilder` operations, custom metrics, order tracing, consolidated books |
| [references/market-impact.md](references/market-impact.md) | Event-time impact from the L3 book, sign convention, window choice, cross-product (futures → ETF) impact |
| [references/retail.md](references/retail.md) | Retail mechanisms and per-venue indicators, the three access routes, coverage caveats that bound the metric |
| [references/analytics-timeseries.md](references/analytics-timeseries.md) | `bmll.time_series` metrics and tags, Mongo-style metric queries, classified-trades taxonomy, CBBO |
| [references/compute-and-storage.md](references/compute-and-storage.md) | File management API, storage areas, Spark dataframes, `SparkHelper`, clusters, scheduled jobs |
| [references/other-datasets.md](references/other-datasets.md) | FX, shares outstanding / free float / market cap, trading calendar, ETF reference data |
| [references/advanced-tutorials.md](references/advanced-tutorials.md) | Index of BMLL's advanced notebooks; thread safety and `Session`, code sharing, external data, R, simulation |
| [references/troubleshooting.md](references/troubleshooting.md) | Empty results, memory errors, Spark result-size limits, permissions, timestamp types |
