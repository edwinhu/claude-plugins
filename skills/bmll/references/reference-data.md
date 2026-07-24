# Reference Data

Finding markets, instruments and listings. Every market-data call needs an identifier that comes
from here, so this is almost always the first query.

```python
from bmll2 import reference, get_market_data, get_market_data_range
```

`bmll.reference` and `bmll2.reference` are the same service.

## Markets

```python
reference.available_markets()
```

Returns every market in the Data Lab with `MarketId`, `CountryCode`, `Description`, `DisplayName`,
`IsAlive`, `MIC`, `OperatingMIC`, `Schema` (`Equity`/`Future`/`Option`) and `StartDate` — the last
being the practical answer to "how far back does this venue go".

Use **`MIC`**, not `MarketId`, everywhere else. BMLL maps a venue's lit-book trading mechanism to
one MIC rather than splitting by segment, so Euronext Growth (`ALXP`) reports under the main market
`XPAR`. This is deliberate — it makes total-venue comparison possible — but it means a segment-level
question cannot be answered by MIC alone; use `SegmentCode`.

Consolidated feeds have their own MICs (`@SIP` for the US).

## Listings and instruments

Two routes, with different performance profiles:

| Route | Best for |
|---|---|
| `reference.query(...)` | Granular search on identifier fields |
| `get_market_data(mic, date, 'reference')` | An entire market on one date — markedly faster |

```python
cols = ['Date', 'InstrumentId', 'ListingId', 'Ticker', 'Description', 'ISIN']
reference.query(MIC='AQXE')[cols]

reference.query(ISIN='GB0007980591')[cols + ['MIC', 'OPOL', 'CurrencyCode']]

get_market_data('XNAS', '2025-10-13', 'reference')
```

### Identifier reliability

Most consistently populated: **`MIC`, `CurrencyCode`, `ISIN`, `OPOL`**. Some venues genuinely lack
ISINs (the STAR segment of Shanghai) — use other identifiers there rather than assuming a data gap.

`Ticker` is the exchange-provided ticker: repeated across venues, reused over time, and absent
entirely on some venues (Tradeweb). `FIGI` and `SegmentCode` are populated for liquid instruments
but not guaranteed. Resolving a universe on `Ticker` alone will silently mismatch.

### Dates

With no date, `reference.query` returns the **most recent** snapshot — not history. That is the
fast path. Passing `start_date`/`end_date` returns point-in-time rows and is slower.

```python
reference.query(Ticker='PPB', MIC='XLON', start_date='2019-05-26', end_date='2019-05-29')
# 2019-05-28: PPB -> FLTR, same ISIN — a ticker change mid-window
```

`IsAlive` tests whether a listing was available on the queried date.

### `object_type`

`reference.query(object_type=...)` changes what a row represents:

| `object_type` | Returns |
|---|---|
| `'Listing'` (default) | One row per listing |
| `'Instrument'` | All sibling listings sharing the `InstrumentId` |
| `'Index'` | Index definitions |
| `'IndexMarket'` | An index's projection onto a specific venue |

```python
# Vodafone's LSE listing
reference.query(Ticker='VOD', MIC='XLON', object_type='Listing')

# ...and everywhere it trades (BATE, CHIX, TRQX, AQXE, BOTC, SGMX, XEQT, @ALP)
reference.query(Ticker='VOD', MIC='XLON', object_type='Instrument')
```

`object_type='Instrument'` with a `MIC` constraint reads as "find the instrument *via* this
listing, then give me all its listings" — the `MIC` selects the anchor, it does not filter the
result.

## Indices

Constituent data needs an entitlement separate from market data. BMLL carries CBOE indices plus
synthetic "market-index" constituents that split a market by asset class.

```python
reference.query(object_type='Index')                      # available indices

# Constituents (primary listings only, by default)
reference.query(object_type='Listing', Index='buk100p', start_date='2022-12-01')

# Constituents plus all their sibling listings across venues
reference.query(object_type='Instrument', Index='buk100p', start_date='2022-12-01')
```

With no date the query defaults to **yesterday**. Index membership is point-in-time, so historical
composition needs an explicit `start_date`/`end_date` range — reconstructing history from a single
recent snapshot bakes in survivorship bias.

Projecting an index onto one venue:

```python
df = reference.query(object_type='Instrument', Index='buk100p', start_date='2022-12-01')
df.loc[df['MIC'] == 'TRQX'].query('IsAlive')
```

`IndexMarket` objects are the identifiers used by cross-venue analytics (e.g. TimeAtEBBO for the
UK 100 across markets):

```python
reference.query(object_type='IndexMarket', Index='bde30p')
reference.query(object_type='IndexMarket', MIC='TRQX')
```

## Futures

Futures use a different reference schema — pass `schema='Future'` or the fields below do not exist:

```python
reference.query(MIC='IFLL', ProductCode='Z', IsAlive=True, schema='Future')[
    ['ListingId', 'InstrumentId', 'Ticker', 'ContractType', 'ProductCode',
     'MaturityMonthYear', 'DisplayName']
]
```

`ContractType` distinguishes `Outright` from `Spread` (and TIC) contracts. Volume analysis that
does not filter on it double-counts, since a spread's legs also print.

For futures, `InstrumentId` groups the whole product (all maturities); `ListingId` is the
individual contract.

## US equity options (OPRA)

Options are **not** supported by `Security`/`NormalisedSecurity` — use `get_market_data*` only.
A day of raw OPRA is ~4TB unconflated; BMLL serves a 1-second conflated feed with `trades`,
`nbbo`, `reference` and `statistics` tables.

```python
get_market_data('OPRA', '2024-10-11', 'reference', line_number=1)
```

Reference rows carry `UnderlyingTicker`, `ExpiryDate`, `OptionType`, `StrikePrice`,
`RegularTradingHours`, `LineNumber`, `UnderlyingPrice`, `OpenInterest`, `DailyVolume`. Filtering by
`line_number` (the OPRA multicast line) is the cheap way to cut the universe when you know it.

Because a large number of contracts is created daily, prefer `get_market_data_range` with Spark for
anything beyond a single day.

## ETF reference data

Ultumus fund reference data, joined on ISIN:

```python
reference.etf_data()                                              # full dataset
reference.etf_data(UCITS='True')
reference.etf_data(UnderlyingAssetClass='Fixed Income', ISIN='HK0000921830')
reference.etf_data(ISIN=ref['ISIN'].unique().tolist())
```

Note `UCITS='True'` is a **string**. Fields include `FullName`, `UmbrellaName`
(the issuer — the natural grouping for market-share work), `UmbrellaFullName`,
`UnderlyingAssetClass`.

See [other-datasets.md](other-datasets.md) for a worked issuer market-share example.

## Availability

```python
reference.availability(mics, date=...)          # is there data for these MICs on this date
```

Check this before concluding that an empty market-data frame means no activity.

`available_listings(listing_ids, start_date, ...)` gives the dates with market data for specific
listings and feeds — the right tool when a per-listing backfill has gaps.
