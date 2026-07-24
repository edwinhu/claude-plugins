# Other Datasets

FX, corporate actions, trading calendar, ETF reference data.

## FX

Daily ECB spot rates:

```python
from bmll2 import get_fx

fx = get_fx('AUDGBP', from_date='2015-06-01', to_date='2026-03-26')
```

Returns a daily series indexed by `date`. One ticker per call — loop and `pd.concat(axis=1)` for a
panel:

```python
rates = pd.concat([get_fx(t, from_date=f, to_date=t2) for t in ['AUDGBP','AUDEUR','GBPEUR']],
                  axis=1)
rates['GBPEUR_implied'] = rates['AUDEUR'] / rates['AUDGBP']
```

These are ECB reference rates — daily, not intraday. Converting an intraday notional with them
introduces a same-day timing error. Trades Plus already carries `TradeNotionalEUR` /
`TradeNotionalUSD` converted at publication date; prefer those when they exist.

## Shares outstanding and free float

```python
from bmll2.corporate_actions import get_shares_outstanding, get_free_float

get_free_float(['US0378331005', 'GB00BH4HKS39', 'KYG875721634'])
get_shares_outstanding(['US0378331005', 'GB00BH4HKS39', 'KYG875721634'], date='2024-04-10')
```

Keyed by **ISIN**, not `ListingId`.

`get_shares_outstanding` returns `effective_date`, `ISIN`, `issuer_name`, `security_description`,
`event_type`, `late_flag`, `new_sos`, `old_sos`.
`get_free_float` returns `old_free_float`, `new_free_float` (percentages) and
`old_free_float_shares`, `new_free_float_shares`.

Field meanings that matter:

- `effective_date` — the day the value is valid **from**. Rows are events, not a daily panel; a
  point-in-time join needs an as-of merge, not an equality join on date.
- `late_flag` — the value was amended or updated *after* its effective date. A backtest that treats
  `late_flag=True` rows as known on `effective_date` has lookahead.
- `event_type` — what triggered the change; `SHOCH` (shares outstanding change) is the common one.

Shares outstanding values are **corporate-action adjusted**.

**Chunk ISIN lists to ~5000** — that is the current per-request ceiling:

```python
sos = pd.concat([get_shares_outstanding(isins[5000*i:5000*(i+1)], date='2024-04-12')
                 for i in range(len(isins)//5000 + 1)])
```

### Market cap

Combine with a price from the Data Feed:

```python
ref = bmll.reference.query(Index='buk100p')
sos = get_shares_outstanding(ref.ISIN.tolist(), date='2024-04-10')

ts = bmll.time_series.query(object_ids=ref.ListingId.tolist(),
                            start_date='2024-04-10', end_date='2024-04-10',
                            metric=['Close', 'ClosingAuctionPrice'])

df = (ref[['ISIN','ListingId','CurrencyCode','DisplayName']]
      .merge(ts, left_on='ListingId', right_on='ObjectId')
      .merge(sos, on='ISIN'))

df['MarketCap'] = df['new_sos'] * df['ClosingAuctionPrice|Executed'] / 1e11   # GBp -> £bn
```

The `1e11` divisor is `1e9` (billions) × `100` (GBp→GBP). Currency scaling is per-listing — a
mixed-currency universe needs `MinorCurrencyFactor` or an explicit per-currency factor, not one
constant.

### Free-float market cap and index construction

Free float is the better proxy for tradeable market cap:

```python
ff = pd.concat([get_free_float(isins[5000*i:5000*(i+1)], date='2024-04-12')
                for i in range(n//5000 + 1)])

ffdf = sos.merge(ff[['ISIN','new_free_float']], on='ISIN', how='left')
ffdf['new_free_float'] = ffdf['new_free_float'].fillna(100)     # no data -> assume full float

df['FreeFloatCap'] = df['new_free_float'] / 100 * df['new_sos'] * df['Close|Midpoint'] / 1e9
index = (df[df.InstrumentType == 'Equity'].drop_duplicates()
           .sort_values('FreeFloatCap', ascending=False).head(1000))
```

Use `new_sos` from `get_shares_outstanding` for the share count, **not** the
`new_free_float_shares` field — only the former is corporate-action adjusted.

## Trading calendar

```python
from bmll2 import Calendar

cal = Calendar(mic, cen_code, date)
cal.trading_days(...)
cal.utc_to_local(...)

sec.calendar          # also available on a Security
```

Covers all equities and *some* futures contracts. `cen_code` is the calendar code — a
market/segment key (`SYSA` = ASX cash equities, `PASA` = Euronext Paris continuous, `FRSA` = Xetra
DAX, `HKSA` = HKEX cash equities). One MIC can map to several codes (Aquis Exchange Europe `AQEU`
carries a separate code per national segment), and one code can serve several MICs — so the
MIC↔code relationship is many-to-many. The tutorial ships the full mapping table.

For intraday session boundaries on a specific day, deriving them from `market_info()` is usually
more reliable than the calendar, because it reflects what actually happened including auction
extensions — see [security-api.md](security-api.md#deriving-the-trading-window).

## ETF reference data

Ultumus fund data, joined on ISIN. See [reference-data.md](reference-data.md#etf-reference-data)
for the query surface.

Issuer market share, combining ETF reference data with classified trades:

```python
ref = pd.concat([
    bmll.reference.query(OPOL=['XSWX'], InstrumentType='Funds', IsAlive=True),
    bmll.reference.query(MIC=['TREU','TWEM','BMTF','BTFE'], InstrumentType='Funds', IsAlive=True),
])
ref_etf = bmll.reference.etf_data(ISIN=ref['ISIN'].unique().tolist())

ts = bmll.time_series.classified_trades(object_ids=ref['ListingId'].tolist(),
                                        start_date='2025-09-01', end_date='2025-09-30')

merged = ts.merge(ref[['ListingId','ISIN']], on='ListingId').merge(ref_etf, on='ISIN')
share = merged.groupby('UmbrellaName')['Notional'].sum().reset_index()
share['percent'] = share['Notional'] / share['Notional'].sum()
```

`UmbrellaName` is the issuer — the grouping key for market share. ETFs listed on multiple venues
appear once per listing, so aggregate to ISIN before counting funds (as opposed to notional).
