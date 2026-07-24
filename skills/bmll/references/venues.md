# Per-Venue Datasets

`https://lab.bmlltech.com/docs/contents/data_ref/datasets/`

88 venue pages. Each documents what is special about that dataset: which MICs it serves, the raw
fields the exchange publishes, how BMLL maps them onto the normalised vocabulary, and behaviour
that differs from other venues (iceberg representation, auction transparency, member attribution).

**Consult the venue page whenever an analysis depends on venue-specific behaviour** — hidden
liquidity, auction mechanics, retail flags, member/broker IDs, or any comparison that assumes two
venues publish the same thing. They frequently do not, and the difference is documented there
rather than being visible in the data.

## What a venue page contains

Using LSE as the template:

| Section | Contents |
|---|---|
| Introduction → Key Features | Available MIC codes, venue background |
| Data Schema | Raw fields per table (Market State, Trades, Limit Order Updates, Exchange Metadata), each with an **Available From** date |
| Normalised Data Schema | The mapping: BMLL Market State ← venue fields; BMLL Trade Type ← venue trade types, again dated |
| Reference | Venue documentation links |

### Feed generations

Venue schemas change when the exchange migrates feeds, and BMLL documents the generations
separately — the LSE page splits every table into **Pre-GTP** and **GTP**, with different field
sets (the GTP trades table has 56 fields against Pre-GTP's 11) and different normalisation
mappings.

Every field carries an **Available From** date. A backtest spanning a migration sees fields appear
mid-sample, and normalisation mappings that differ either side of the boundary. Checking the venue
page before running a multi-year study is the difference between a real regime change and an
artefact of the feed.

The same applies to flags added later — Xetra's retail flag (`trade_condition` 743) only exists
from 2024-05-20, and CBOE EU's from 2025-09-08. Volume "growth" that begins exactly on a flag's
start date is the flag, not the market.

## Venue index

MICs as documented on each page. Multi-MIC venues cover their whole operator family.

### Europe / UK

| Venue | MICs |
|---|---|
| LSE | `XLON` |
| Cboe Europe | `BATE`, `CHIX`, `CEUX` |
| Cboe BXTR | `BOTC` |
| Turquoise | `TRQX`, `TQEX` |
| Aquis | `AQXE`, `AQEU` |
| Sigma X MTF | `SGMX`, `SGMU` |
| Equiduct | `XEQT`, `EQTA`, `EQTB`, `EQTC` |
| Equiduct Apex | `@ALP` |
| Euronext | `XPAR`, `XAMS`, `XBRU`, `XLIS`, `XDUB` |
| Deutsche Boerse Xetra | `XETR`, `XEUB` |
| German Regionals | `XBER`, `XDUS`, `XFRA`, `XHAM`, `XHAN`, `XMUN`, `XSTU` |
| Tradegate | `XGAT` |
| Borsa Italiana | `XMIL`, `MTAA`, `MTAH`, `ETFP`, `MOTX`, `SEDX`, `XAIM`, `ATFX` |
| BME | `XMAD` |
| SIX | `XSWX`, `XSWM`, `XSEB`, `XICB`, `EBBO`, `SIX` |
| Nasdaq Nordic | `XSTO`, `XCSE`, `XHEL`, `XICE`, `XTAL`, `XRIS`, `XLIT`, `ESTO`, `FNSE`, `FNDK`, `FNFI`, `FNIS`, `FNEE`, `FNLT`, `FNLV`, `ONSE` |
| Oslo Børs | `XOSL` |
| Vienna | `XWBO` |
| Warsaw | `XWAR` |
| Prague | `XPRA` |
| Budapest | `XBUD` |
| Athens | `XATH` |
| Istanbul | `XIST` |
| AQSE | `AQSE` |
| ARTEX | `ARTX` |
| Bloomberg MTF | `BMTF`, `BTFE` |
| Tradeweb | `TREU`, `TWEM` |
| Cinnober Boat | `BOAT` |
| Lynx Periodic Match | `LYNX` |
| Eurex | `XEUR`, `XERT`, `XERE`, `XEUB`, `XEUP` |
| ICE | `IFEU`, `IFLL`, `IFLO`, `IFLX`, `IFUS`, `IFED`, `NDEX` |

### Americas

| Venue | MICs |
|---|---|
| The U.S. Consolidated Tape | `@SIP` |
| NYSE | `XNYS`, `ARCX`, `XASE`, `XCHI`, `XCIS` |
| Nasdaq | `XNAS`, `XBOS`, `XPSX` |
| Cboe US | `BATS`, `BATY`, `EDGA`, `EDGX` |
| IEX | `IEXG` |
| MEMX | `MEMX` |
| MIAX Pearl Equities | `EPRL` |
| LTSE | `LTSE` |
| Blue Ocean | `OCEA` |
| OTC Markets | `OTCM` |
| OPRA (US equity options) | `OPRA` |
| CME Group | `XCME`, `XCBT`, `XNYM`, `XCEC`, `CME`, `CBOT`, `NYMEX`, `COMEX` |
| Toronto Stock Exchange & TSX Venture | `XTSE`, `XTSX`, `TSX` |
| TSX Alpha | `XATS` |
| Canadian Consolidated Book | `@CCB` |
| Aequitas NEO Exchange | `NEOE`, `NEON`, `NEO` |
| Nasdaq Canada | `CHIC` |
| Nasdaq CX2 | `XCX2` |
| Omega ATS | `OMGA` |
| CSE | `XCNQ` |
| B3 (Brasil) | `BVMF` |
| Mexico | `XMEX` |
| Colombia | `XBOG` |
| Lima | `XLIM` |
| Santiago | `XSGO` |

### APAC

| Venue | MICs |
|---|---|
| ASX | `XASX` |
| ASX24 | `XSFE` |
| Cboe Australia | `CHIA` |
| Tokyo | `XTKS` |
| Cboe Japan | `CHIJ` |
| Japannext | `XSBI`, `SBIJ`, `SBIU` |
| Hong Kong | `XHKG` |
| Shanghai | `XSHG` |
| Shenzhen | `XSHE` |
| Korea | `XKRX` |
| Singapore | `XSES` |
| Taiwan | `XTAI` |
| Taipei | `ROCO` |
| Malaysia | `XKLS` |
| Thailand | `XBKK` |
| Philippines | `XPHS` |
| New Zealand | `XNZE` |
| NSE (India) | `XNSE` |
| BSE (India) | `XBOM` |
| Hanoi | `HSTC` |
| Ho Chi Minh | `XSTC` |

### EMEA (non-European)

| Venue | MICs |
|---|---|
| JSE | `XJSE` |
| JSE Derivatives | `XSAF`, `ZFXM` |
| A2X | `A2XX` |
| Tadawul | `XSAU` |
| Abu Dhabi | `XADS` |
| Dubai | `XDFM` |
| Qatar | `DSMD` |
| Kuwait | `XKUW` |
| Bahrain | `XBAH` |
| Egypt | `XCAI` |
| Tel Aviv | `XTAE` |

Confirm coverage and start dates at runtime with `reference.available_markets()` — it returns
`StartDate` and `IsAlive` per market, which this static table cannot.

## Consolidated and synthetic MICs

`@SIP` (US), `@CCB` (Canada) and `@ALP` (Equiduct Apex) are consolidated or venue-family feeds
rather than single order books. Treating `@SIP` as one more venue in a venue-share breakdown
double-counts, since its prints are also attributed to the executing venues.

## Member attribution

The retail page documents which venues expose member/broker identifiers, by region, with the exact
table and field names — Americas (11 markets), APAC (3), EMEA (2). Broker attribution exists on far
fewer venues than people assume; `BrokerIdBuyer`/`BrokerIdSeller` in Trades Plus are populated only
where the exchange provides them, and only for the **passive** side.

See [retail.md](retail.md).
