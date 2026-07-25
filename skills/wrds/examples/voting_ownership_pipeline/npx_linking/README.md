# ISS N-PX → SEC Series → CRSP Crosswalk

Builds the `fundid → block` crosswalk that
[`../build_npx.sas`](../build_npx.sas) hash-merges on the WRDS grid.

**This is the hard half.** The SGE array that reduces 238M N-PX rows to 2.25M
`(item, block)` cells is mechanical once you have this file; getting from an ISS
`fundid` to a CRSP fund is not. Read
[`references/npx-crsp-linking.md`](../../../references/npx-crsp-linking.md)
before touching anything here — it documents every threshold and the measured
failure each one prevents.

## The ladder

```
L1  SEC series/class annual masters  ──►  sec_series_master.parquet   82,699 class rows
L2  ISS fundid → SEC seriesId        ──►  fundid_seriesid.parquet     26,686 rows
L3  seriesId → CRSP MFDB             ──►  npx_crsp_link.parquet       26,686 × 17
                                          (crsp_fundno, wficn, index_fund_flag,
                                           tna_latest, block, match_tier)
        ↓
    ../npx_link_to_csv.py  →  npx_link.csv  →  scp to WRDS
        ↓
    ../stage_npx_link.sas  →  out.npx_link  →  hash in ../build_npx.sas
```

Exact-ID tiers do the work: **`via_seriesid` alone is 19,327 of 21,191 links.**
The fuzzy tiers are the tail. If you are tuning the matcher to raise coverage,
you are probably missing an exact-ID path.

SEC series IDs are mandatory for N-1A/N-3/N-4/N-6 registrants since 2006-02-06,
so every fund in the N-PX universe has one — but ISS only *reports* `seriesid`
from 2023. The pre-2023 link is the 2023+ ID **carried back over the stable
`fundid`**, not a fuzzy match.

## Files

| File | Role |
|---|---|
| `linking_config.py` | every threshold, regex and stoplist, each with the failure it prevents |
| `matching.py` | the engine — normalisers (l2/l3/l3b), digit guard, TF-IDF candidates, cross-family verdict |
| `build_sec_series_master.py` | L1: consolidate the SEC annual series/class CSVs |

`matching.py` requires `polars`, `numpy`, `scikit-learn`, `sparse_dot_topn`.

### What is not vendored here

The **L2 and L3 ladder drivers** are project-specific — they depend on a
particular project's CRSP extracts, adjudication CSVs and coverage reports. They
are not copied in, because untested code that cannot run is worse than none.
`matching.py` provides every primitive they need, and the reference documents the
tier ladder and thresholds they implement precisely enough to rebuild.

## Running L1

```bash
# 1. Download the SEC annual CSVs (2010-2015, 2017-2025 — the SEC publishes no
#    2016 file) from
#    https://www.sec.gov/data-research/sec-markets-data/investment-company-series-class-information
#    into data/raw/sec_series_class/

SEC_SERIES_RAW=data/raw/sec_series_class \
SEC_SERIES_OUT=data/processed \
    python build_sec_series_master.py
```

Header layouts differ across vintages — five column-naming conventions,
preamble lines before the header, a UTF-8 BOM in 2025, padded fields in 2014,
three null sentinels. All normalised in `COLUMN_ALIASES` / `read_year()`.

Each annual file is a **point-in-time snapshot of then-active registrants**, not
a cumulative history. A fund liquidated before a given year is simply absent, so
`year_first_seen` / `year_last_seen` bound *observed* life, not true birth/death.
Names and tickers are reused and restated, which is why the builder also emits a
long name-history table (`sec_series_names_long.parquet`) — the current name will
not match a 2012-vintage ISS `fundname`.

## Pushing the result to the grid

```bash
cd ..
./npx_link_to_csv.py --in npx_crsp_link.parquet --out npx_link.csv \
    --key fundid --group block --weight tna_latest
scp npx_link.csv wrds:~/projects/myproject/
```

`npx_link_to_csv.py` fails loudly on a block label longer than the SAS `$24` (a
truncated label silently merges two blocks) or containing a comma.

## Traps that will cost you a headline number

- **`n_vote_rows` is `uint32`.** A naive sum overflows: block shares summed to
  40.5% and the index block read **6.3% when the truth was 36.1%**. Cast to
  float64 before any aggregation and assert shares reconcile.
- **`fundid → crsp_fundno` is many-to-one.** Summing `tna_latest` at fundid
  grain without splitting gives **$64.43T against a true $32.38T**.
- **Resolve identity at fundid grain, never on the vote panel.** 16 fundids
  carry >1 seriesId; at 26,686 rows that is auditable, at 143.8M rows it is not.
- **Report weight coverage.** `sharesvoted` is 0% populated pre-2023;
  `tna_latest` only exists for linked funds. `build_npx.sas` emits `n_no_sv` and
  `n_no_tna` per cell so a weighted split can never look precise when it is
  computed from nothing.
