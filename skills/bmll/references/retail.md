# Retail Trades

Tutorial: `tutorials/notebooks/retail.html` · Schema: `data_ref/retail.html`

Retail flow is executed through many different mechanisms, on- and off-exchange, and few of them
are labelled as retail by the venue. BMLL identifies retail trades from L3 data — using explicit
flags where exchanges provide them and **inference where they do not** — and exposes the result as
a `RetailTrades` metric plus per-trade fields.

The inference/flag split is the thing to keep in view: retail volume is measured with different
confidence in different places, so cross-venue and cross-region retail comparisons are partly
comparing measurement methods.

## Mechanisms

| Region | Mechanism | Shape |
|---|---|---|
| EU | SIX EBBO | Competing market makers incentivised to improve on EBBO, multiple venues |
| EU | Euronext Best of Book | Competing market makers improving on primary BBO, single venue |
| EU | Retail Service Providers (LSEG) | Market makers on an RFQ basis, off-book |
| EU | Equiduct Apex | Reflects a consolidated BBO, multiple venues |
| EU | Turquoise Retail Max | Reflects primary midpoint via an auction mechanism, single venue |
| EU | Tradegate, Lang & Schwarz, Gettex, Quotrix | Single market maker; XETR as reference (Tradegate uses a dynamic reference market) |
| US | Wholesaler / PFOF | Competing market makers offering price improvement on NBBO, multiple venues |

## Indicators

How each mechanism is identified in the data:

| Mechanism | Indicator | Notes |
|---|---|---|
| Euronext Best of Book | `original_trade_type` is `20` | |
| SIX EBBO | `execution_venue` is `XSEB` | Excludes retail that executes on Swiss-At-Mid (`XSWM`) or the SIX CLOB (`XSWX`) before resulting in an `XSEB` execution venue |
| Equiduct Apex | `trade_type` in `B`,`S` (VBBO) or `b`,`s` (ALP order) | |
| Retail Service Providers (LSEG) | Off Book On Exchange, during continuous trading, below Standard Market Size (€10,000) | **Inferred** |
| Xetra | `trade_condition` is `743` | Retail Execution Service went live **2024-05-20** — the flag does not exist before |
| CBOE EU | `bats_trade_timing_indicator` is `G` (UK entity) or `K` (EU entity) | Available from **2025-09-08** |
| BME | L2 feed: `buy_trade_type`/`sell_trade_type` is `8` or `9`; L3 feed: `retail_flag` is `True` | |
| Turquoise Retail Max | `execution_venue` is `TRQA`/`TQEA` | **Not currently in the `RetailTrades` metric** — not a sufficient indicator |
| Tradegate, Lang & Schwarz, Gettex, Quotrix | Entirely retail; no indicator needed | **Not available currently**, to be added |
| US SIP | Odd lot (`original_trade_type` contains `I`) **and** price inside the NBBO but not at the midpoint **and** sub-decimal pricing (>2 dp) | **Inferred.** Excludes PFOF trades where wholesalers internally cross aggregated retail against a counterparty. Daily TRF data on the SIP gives no transparency on the underlying off-exchange venue |

Three of these materially bound what the metric can tell you, and none is visible in the output:

- **Turquoise Retail Max and the German single-market-maker venues are excluded**, so European
  retail totals are an undercount of known size but unknown magnitude.
- **The Xetra and CBOE EU flags start mid-history** (2024-05-20, 2025-09-08). A retail time series
  crossing those dates has a level shift that is pure instrumentation.
- **US SIP retail is inferred from odd-lot + inside-NBBO + sub-penny pricing** and misses
  internalised PFOF. It is a proxy, not a measurement.

State the relevant one when reporting retail figures — a reader who assumes a clean measurement
will over-read a trend that is really a flag going live.

## Accessing retail

Three routes, at different grains.

### Trades Plus — per trade

Two fields:

- `ParticipantType` — exchange-specific values
- `BMLLParticipantType` — normalised: `RETAIL`, `INSTITUTIONAL`, `MARKET_MAKER`, `BROKER_DEALER`,
  `UNKNOWN`

```python
from bmll2 import get_market_data

(get_market_data("XPAR", "2025-10-16", "trades-plus")
 .query("BMLLParticipantType == 'RETAIL'")
 [['Ticker', 'TradeTimestamp', 'Price', 'Size', 'ParticipantType', 'BMLLParticipantType']])
```

Use `BMLLParticipantType` for cross-venue work. Report the `UNKNOWN` share rather than dropping it
— it is large, and dropping it silently converts "unclassified" into "not retail".

See [trades-plus.md](trades-plus.md).

### Time series — daily aggregate

The `RetailTrades` metric, keyed on the linked `ListingId` for the mechanism's venue:

```python
from bmll import reference, time_series

# Europe: query the venue where the mechanism runs (XPAR for Euronext Best of Book)
ref_eu = reference.query(Ticker='MC', OPOL='XPAR', object_type='Instrument')
ts_eu = time_series.query(
    object_ids=ref_eu[ref_eu.MIC.isin(['XPAR', 'XEQT'])].ListingId.unique(),
    metric=['RetailTrades'], frequency='D',
    start_date='2024-01-01', end_date='2024-05-15')

# US: retail is measured on the consolidated tape, so use the @SIP listing
ref_us = reference.query(Ticker='TSLA', OPOL='XNAS', object_type='Instrument')
ts_us = time_series.query(
    object_ids=ref_us[ref_us.MIC == '@SIP'].ListingId.unique(),
    metric=['RetailTrades'], frequency='D',
    start_date='2024-01-01', end_date='2024-05-15')
```

Returns `RetailTrades|Count`, `|Shares`, `|NotionalLocal`, `|NotionalEUR`, `|NotionalUSD`.

**US retail resolves against the `@SIP` listing, not the primary exchange listing.** Querying the
`XNAS` listing for US retail returns nothing — the mechanism is measured on the consolidated tape.

`object_type='Instrument'` then filtering by MIC is the idiom: it finds every sibling listing, from
which you select the venue where the mechanism actually runs.

### Security object — per trade, raw

`all_trades()` exposes a `retail` boolean, but **only if `retail` is in the requested columns**:

```python
from bmll2 import NormalisedSecurity

sec = NormalisedSecurity.from_listing_id(121317, "2025-10-16")
COLS = ['trade_id', 'trade_timestamp', 'publication_timestamp', 'aggressor_side',
        'price', 'execution_size', 'market_state', 'sequence_no', 'currency',
        'printable', 'bmll_trade_type', 'trade_action', 'execution_venue', 'retail']

sec.market_data().all_trades(columns=COLS).query("retail")
```

Note what the LSE result shows: retail prints come back as `bmll_trade_type == 'OTC'` with
`market_state == 'NOT_APPLICABLE'` and `aggressor_side == 'UNKNOWN'` — they are RSP off-book
prints. Retail flow is not a subset of lit continuous trading, so a filter on `LIT` excludes most
of it.

## Participant type sources

`data_ref/retail.html` documents, per MIC, the source column and value behind each
`ParticipantType`, plus the contra-type name. Venue-native values include `THIRD_PARTIES`,
`OWN_ACCOUNT`, `SPECIALIST` (XMAD) and `CUSTODIAN_PARTICIPANT`, `PROPRIETARY`,
`NON_CP_NON_PROPRIETARY` (XNSE) — see [trades-plus.md](trades-plus.md).

Member attribution (broker IDs) is documented there too, by region — see
[venues.md](venues.md#member-attribution).
