# Thomson Reuters / Refinitiv Institutional Ownership & Mutual Fund Holdings

WRDS PostgreSQL reference for 13-F institutional holdings (S34) and mutual fund holdings (S12), including MFLINKS and CRSP mutual fund integration.

## Tables

### 13-F Institutional Holdings

| Table | Description | Grain |
|-------|-------------|-------|
| `tfn.s34type1` | Manager-quarter header | mgrno-rdate-fdate |
| `tfn.s34type3` | Stock-level holdings | mgrno-fdate-cusip |

**s34type1 fields:** `rdate` (report date), `fdate` (file/vintage date), `mgrno`, `mgrname`

**s34type3 fields:** `fdate`, `mgrno`, `cusip`, `shares`, `type`, `sole`, `shared`, `no`

### Mutual Fund Holdings (S12)

| Table | Description | Grain |
|-------|-------------|-------|
| `tfn.s12` (SAS) / `tr_mutualfunds.s12` (PG) | Fund-level stock holdings | fundno-rdate-cusip |

**s12 fields:** `rdate`, `fdate`, `fundno`, `cusip`, `shares`, `fundname`

### MFLINKS

| Table | Description |
|-------|-------------|
| `mfl.mflink1` | TFN fundno -> wficn -> CRSP crsp_fundno |
| `mfl.mflink2` | TFN fundno -> wficn (with rdate alignment) |

**mflink1 fields:** `wficn`, `crsp_fundno`, `fundno`

**mflink2 fields:** `wficn`, `fundno`, `rdate` (use this for date-aligned joins)

### CRSP Mutual Fund Tables

| Table | Description |
|-------|-------------|
| `crsp.portnomap` | crsp_fundno -> crsp_portno mapping with date ranges (`begdt`, `enddt`), `index_fund_flag`, `et_flag` |
| `crsp.fund_fees` | Expense ratios by crsp_fundno with date ranges |
| `crsp.fund_summary` | Monthly TNA and returns |
| `crsp.fund_hdr` | Fund header (`mgmt_name`, `fund_name`, `ticker`) |

## Building 13-F Institutional Ownership

### Step 1: First vintage per manager-quarter

```sql
SELECT DISTINCT rdate, fdate, mgrno, mgrname
FROM tfn.s34type1
GROUP BY mgrno, rdate
HAVING fdate = MIN(fdate)
ORDER BY mgrno, rdate
```

### Step 2: Merge with holdings

```sql
SELECT a.rdate, a.fdate, a.mgrno, a.numinst,
       a.first_report, a.last_report,
       b.permno, a.shares
FROM first_vint a
INNER JOIN tfn.s34type3 b ON a.fdate = b.fdate AND a.mgrno = b.mgrno
-- Map cusip to permno via crsp.msenames
INNER JOIN (
    SELECT DISTINCT ncusip, permno FROM crsp.msenames
    WHERE ncusip IS NOT NULL
) c ON b.cusip = c.ncusip
WHERE b.shares > 0
```

### Step 3: Adjust shares via CRSP factors

```python
shares_adj = shares * cfacshr  # CRSP adjustment factor aligned at vintage date (fdate)
```

> **This step is where the panel breaks around splits.** Thomson has *already*
> pre-adjusted `shares` to the FDATE vintage, and does so incorrectly in a documented
> fraction of cases. Multiplying by `cfacshr` again compounds the error rather than
> fixing it. See **Known Data Defects → D1** below before trusting any split-era quarter.

### Step 4: Aggregate to permno-quarter

```python
# Key output variables
IO_total = shares_adj_sum           # total institutional shares held
IOR = IO_total / TSO                # institutional ownership ratio (0-1)
NumOwners = count(distinct mgrno)
IOC_HHI = sum((shares_i / IO_total) ** 2)  # concentration
```

## Building Mutual Fund Holdings (TFN S12 -> CRSP MF)

### Linking chain: TFN fundno -> wficn -> crsp_fundno -> crsp_portno

```sql
-- Step 1: Link TFN S12 to MFLINKS (date-aligned)
SELECT b.wficn, b.crsp_fundno, c.crsp_portno,
       a.rdate, a.fdate, a.fundno, a.cusip, a.shares, a.fundname,
       c.index_fund_flag, c.et_flag
FROM tfn.s12 a
INNER JOIN mfl.mflink2 b  -- or out.mfl2 if pre-built
  ON a.fundno = b.fundno AND a.rdate = b.rdate
INNER JOIN crsp.portnomap c
  ON b.crsp_fundno = c.crsp_fundno
  AND a.rdate >= c.begdt AND a.rdate <= c.enddt
WHERE b.wficn IS NOT NULL AND c.crsp_fundno IS NOT NULL AND a.shares > 0
```

### Passive/Index fund classification

Use CRSP `index_fund_flag` plus regex on fundname:

```python
passive = (index_fund_flag != '') | bool(re.search(
    r'Index|Idx|Indx|Ind |Russell|S \& P|S and P|S&P|SP|Dow|DJ|'
    r'MSCI|Bloomberg|KBW|Nasdaq|NYSE|STOXX|FTSE|Wilshire|Morningstar|'
    r'[14569]00|(10|15|20|50)00',
    fundname, re.IGNORECASE
))
pure_index = (index_fund_flag == 'D')
```

### Aggregate to permno-quarter

```python
# Map cusip to permno via crsp.msenames, adjust shares by cfacshr
# Then aggregate:
MF_TOTAL = sum(shares_adj)                      # total MF shares
PASSIVE_TOTAL = sum(shares_adj * passive)        # passive fund shares
PURE_INDEX_TOTAL = sum(shares_adj * pure_index)
MF_PCT = MF_TOTAL / TSO                         # bound 0-1
PASSIVE_PCT = PASSIVE_TOTAL / TSO
INDEX_PCT = PURE_INDEX_TOTAL / TSO
EXP_RATIO_VW = sum(exp_ratio * shares_adj) / sum(shares_adj)  # value-weighted
```

## Merging Ownership with Meetings/Events

Use an as-of merge: for each event (by permno + recorddate), find the most recent ownership quarter:

```python
# pandas merge_asof
ownership = ownership.sort_values(['permno', 'rdate'])
events = events.sort_values(['permno', 'recorddate'])
merged = pd.merge_asof(
    events, ownership,
    left_on='recorddate', right_on='rdate',
    by='permno', direction='backward'
)
```

## ETL Performance Notes

### 13-F Institutional Ownership (S34)

**Recommended approach:** PostgreSQL with per-year server-side CTEs. Each year's CTE joins s34type1 + s34type3 + CUSIP mapping and aggregates to permno-quarter, returning ~30–50K rows. Total: 674K rows in 5.2 minutes for 2002–2024.

**Key settings:** `SET work_mem = '256MB'` and `SET statement_timeout = '3600s'` before querying.

**Anti-pattern:** Downloading raw s34type3 holdings locally — recent years (2020+) have millions of rows and can OOM the compute node.

### Mutual Fund Holdings (S12)

**Recommended approach:** SAS with SGE parallelism + PostgreSQL for bulk reads. The MFLINKS join chain (fundno → wficn → crsp_fundno → portnomap) creates large intermediates that PostgreSQL cannot store (read-only, no temp tables), so SAS handles the ETL. But the initial S12 read uses PostgreSQL to avoid NFS contention.

**Working pipeline:** `examples/voting_ownership_pipeline/` in the WRDS skill — see `README.md` for architecture, `run_pipeline.sh` for orchestration.

**Pipeline pattern:**
1. SAS: Build mfl2/mfl3 prereqs (`build_mflinks.sas`, ~1 min)
2. SAS: Read S12 via PostgreSQL, write year-range partitions to /scratch (`split_s12.sas`, ~15 min)
3. SAS: 9 parallel SGE jobs reading /scratch partitions (`tfn_holdings_parallel.sas`), each doing MFLINKS join + CUSIP→PERMNO + TSO + permno-quarter aggregation → small `mf_own_YYYY_YYYY.sas7bdat`
4. SAS: Merge all outputs (`merge_panel.sas`)

**NFS contention:** `tfn.s12` is 44GB on NFS. Running 7+ parallel SAS jobs reading it causes each to take ~40 min instead of ~5 min. The `split_s12.sas` step reads via WRDS PostgreSQL (`tr_mutualfunds.s12`) — a single sequential read through the database, no NFS contention. Partitions total ~40GB on /scratch (check quota before running).

**Year range balancing:** S12 data exploded from ~4M rows/year (2003-2016) to ~20-26M rows/year (2018-2024). Year ranges must be balanced by row count, not year count: 2003-2010 (34M), 2011-2016 (27M), 2017-2018 (30M), then 1 year each for 2019-2024 (~22-27M each).

**PostgreSQL schema mapping:** SAS `tfn.s12` → PostgreSQL `tr_mutualfunds.s12`. SAS `tfn.s34` → PostgreSQL `tr_13f.s34`. Connection via `PROC SQL; CONNECT TO POSTGRES (server='wrds-pgdata-ident-w.wharton.private' port=9737 ...)`. Credentials in `~/.pgpass`.

**Critical lesson:** SAS must aggregate to permno-quarter before outputting. If SAS outputs raw fund-level holdings (millions of rows, 1–12GB per chunk), Python will OOM reading the SAS7BDAT files.

**Why PostgreSQL fails for the full S12 ETL:**
- Read-only: cannot `CREATE TEMP TABLE` for the MFLINKS chain
- Full join query (s12 × mflink2 × mflink1 × portnomap) is too complex for the query planner
- But PostgreSQL works well for the initial bulk read with server-side WHERE filtering

See `postgres-vs-sas.md` for the full decision framework.

## Common Gotchas

1. **Vintage dates** -- s34type1 has multiple fdate per rdate (restatements); keep the earliest fdate.
2. **First/last report flags** -- track gaps in 13-F reporting; useful for clean time-series analysis.
3. **CUSIP is historical** -- map through `crsp.msenames.ncusip`, not the current CUSIP.
4. **IOR > 1 — two distinct root causes; treat accordingly.**
    - **(a) Timing mismatch** (benign, small): 13-F quarter rdate vs CRSP shrout-date offset by a few weeks around splits/issuances. Cap at 1.0 or filter `IOR > 1.2`.
    - **(b) cfacshr units mismatch** (systematic, can produce 100-2000% IOR): when combining a cfacshr-adjusted numerator with an as-reported denominator. `IO_TOTAL` in the build_inst_own pipeline is `sum(shares × cfacshr)` — cumulatively adjusted to today. If you divide it by ISS `outstandingshare` (as-reported at meeting date, pre-split for split-era meetings), you get impossible values. AAPL 2014: `IO_TOTAL = 15.25B` (cfacshr=28× applied) / `tso_iss = 892M` = 1708%.
        - **Fix:** use `TSO = shrout × 1000 × cfacshr` as the denominator (matches IO_TOTAL's convention). The standard `IOR_crsp` from build_inst_own.sas is correct.
        - If you need **company-wide** IOR (across share classes) for dual-class firms, aggregate to `permco` before dividing. See `crsp.md` §Dual-class.
    - **(c) Dual-class per-class numerator** (systematic): 13F reports per CUSIP. For STZ, BRK.B, GOOGL etc., a single-permno `IO_TOTAL` covers only one share class while ISS `outstandingshare` is company-wide. Fix: sum IO_TOTAL across all permnos sharing a permco before dividing.
5. **S12 data coverage** -- starts ~2003, drops off in 2024; check year-by-year counts.
6. **Deduplication — critical** -- after all joins, dedup by `(wficn, crsp_portno, crsp_fundno, rqdate, cusip8)`. If you dedup by `(fundno, ...)` instead of `(wficn, ...)`, you will inflate shares ~3-4× because one wficn maps to multiple crsp_fundno (share classes). **This bug existed in a stale SAS build and produced MF_TOTAL values ~3.95× higher than correct.**
7. **CRSP adjustment factors** -- align `cfacshr` at the vintage date (fdate), not the report date.
8. **mflink2 vs mflink1** -- mflink2 has rdate for date-aligned joins; mflink1 is static mapping with wficn and crsp_fundno.
9. **portnomap date ranges** -- always filter rdate between begdt and enddt.
10. **exp_ratio sentinel** -- value of -99 means missing in `crsp.fund_fees`; replace with NULL.
11. **Cartesian multiplicity risk in mfl3** -- `mfl3 = mfl2 × mflink1` is a cartesian expansion on wficn. `mflink1` has **mean ~3.5 crsp_fundno per wficn (1-101 range)**. Each s12 holding gets multiplied into N rows downstream. The `proc sort nodupkey by wficn rqdate cusip8` MUST run after aggregation or shares inflate.
12. **Bridge coverage is structurally limited (MFLINKS is US-centric)** -- `mfl.mflink2` only bridges ~**12% of tfn.s12 fundno globally** (e.g., 7,330 of 59,450 at 2022-12-31) because MFLINKS links Thomson S12 ↔ CRSP Mutual Fund Database, which is US-centric. Even filtering s12 by `country='UNITED STATES'` (which in s12 appears to reflect the manager/advisor's country rather than fund domicile — it includes UCITS and global funds run out of US), coverage rates are:
    - **Pre-2017**: ~77% of US-country funds bridged
    - **2017 onward**: drops to **~58-66%** (S12 added ~5k more US-country funds from 2016→2017 that MFLINKS did not grow to match)

    The unbridged ~40% post-2017 is **not ETFs specifically** (ETF name-match share is ~2.4% in both bridged and unbridged). The real pattern: **CRSP Mutual Fund Database covers open-end traditional mutual funds; MFLINKS inherits that limit.** Large unbridged US-country funds cluster in categories CRSP does NOT cover:
    - **Closed-end funds** (e.g., Cohen Steers TaxAdv Pref $110B, Nuveen Quality Pref $69B)
    - **Variable annuity separate accounts** (CREF Stock Account $104B, VIT products)
    - **UCITS / Luxembourg-domiciled funds** (Vanguard SP 500 UCITS $33B; `country='UNITED STATES'` reflects manager, not domicile)
    - **Sub-advised wrapper products** (Fidelity Strategic Advisers family)
    - **Pension-only products** (JFM Tracker Pension $40B)
    - **Some ETFs** — mixed (e.g., QQQ unbridged, iShares SP500 bridged; no systematic ETF exclusion)

    Bridge rate is also strongly correlated with fund SIZE:
    - $10B+ funds: 80% bridged
    - $100M–$1B: 68%
    - $10–100M: 56%
    - <$10M: 30–46% (tiny funds often below CRSP's coverage threshold)

    Implications:
    - For US MF ownership analysis pre-2017: MFLINKS is reasonably clean
    - For post-2017 analysis: expect ~40% of "US country" s12 funds unbridged; report coverage alongside MF aggregates
    - For global analysis: build your own bridge (by fund name, ticker, or manager ID)
13. **passive_pct 100× scaling in `pass.sas7bdat`** -- `1-make.sas` line 705 multiplies `PASSIVE_PCT` by 100 when merging `index_own` into the panel. If `index_own.PASSIVE_PCT` is already in percent scale (0-100) rather than fraction (0-1), the stored value ends up at 0-10000 scale. Always inspect `describe()` after loading; divide by 100 if max > 100.

---

## Known Data Defects (WRDS research notes + measured)

WRDS publishes its S12/S34 defect notes at
[Manuals and Overviews → LSEG → Mutual Fund and Investment Company](https://wrds-www.wharton.upenn.edu/pages/support/manuals-and-overviews/lseg/mutual-fund-and-investment-company/)
(institutional auth required). The notes are filed under Thomson Reuters, Refinitiv, and
LSEG interchangeably — same feed, three vendor names, `tfn` libname throughout.

**Detectors for every defect below:** `skills/wrds/scripts/ownership_dq.py`.
**Tests:** `tests/ownership_dq_test.py` (stdlib only — `uv run python3 tests/ownership_dq_test.py`).

### D1. Split adjustment is wrong around split dates — the big one

**Source:** *Note on Splits in TR Mutual Funds and 13F: S12 and S34*, WRDS Research,
March 7 2017.

The note's running example is **permno 14593 (Apple)** — the same firm where our panel
breaks. Documented failure modes:

| Case | What Thomson reports | What is correct |
|------|---------------------|-----------------|
| Double adjustment | 226,331 = 4,619 × 7 × 7 | 32,333 = 4,619 × 7 |
| Adjustment at the wrong date | MasterCard shares adjusted at 2013-12-31, before the 2014-01-21 ex-date | adjust at ex-date |
| Compounded on carry-forward | stale rows re-adjusted each vintage | adjust once |

**This is primarily an S12 finding.** Every worked example in the note is drawn from
mutual-fund data — "These cases are selected from Mutual Fund data, though the 13F data
demonstrate similar patterns" — and funds fare *worse* than 13Fs at large splits:

| Split factor | Mutual funds | 13F |
|---|---|---|
| 2 | 12.2% | 13.3% |
| 3 | 15.2% | 14.8% |
| 4 | 35.1% | 32.4% |
| >4 | **40.7%** | 34.5% |

Around split quarters generally, outlier rates run **13.8% at Qtr(0) and 14.1% at Qtr(−1)
for 13Fs** (12.9% / 11.5% for funds) against a 5% null. The note's own tests rule out the
obvious explanations: restricting to `rdate == fdate` (no carry-forwards) does *not*
help, and neither does restricting to splits whose ex-date and record date share a
quarter.

**WRDS's own conclusion: there is no clean systemic fix.** Their suggestion is to trim or
winsorize Qtr(−1), Qtr(0), Qtr(+1) around each split.

**This explains measured finding #1** — the AAPL 4.5× swing with a flat owner count.
AAPL's split ex-dates (2014-06-09, 7:1; 2020-08-31, 4:1) both fall in Jun/Sep quarters,
and a handful of catastrophically over-adjusted quarters is enough to move a 23-quarter
mean by 4.5× while the set of reporting managers never changes. The owner count is flat
because the filers are all still there — only the share *units* are wrong.

**Consequence for the build:** repairing `HAVING fdate = MIN(fdate)` vintage selection
alone will **not** fix this. The defect is in Thomson's pre-adjustment of `shares`, not in
which vintage you pick. Winsorizing split-adjacent quarters is the documented mitigation;
sourcing post-2013 holdings from EDGAR 13F is the alternative.

### D2. FDATE vs RDATE — and a contradiction between two WRDS documents

- `RDATE` = report date, the date the holdings are actually valid for.
- `FDATE` = **vintage** date; the primary key for table joins and the date Thomson's
  share adjustments are made *to*.

*WRDS Overview of Thomson Reuters Mutual Fund and Investment Company Data* is explicit:
"SHARES values are adjusted for stock splits that occur between the linked RDATE and
FDATE… the pre-adjustment may not be correct in all cases." The May 2017 S34 note agrees:
"Thomson's FDATE is a vintage date, and is used primarily for share adjustments using
CRSP cumulative share adjustment factors."

⚠️ **The splits note contradicts both.** Its table legend reads "Column 1 is the vintage
date of the holding data, Rdate… Column 2, Fdate, is the date when holdings are valid" —
exactly backwards. The Overview and the S34 note agree with each other and with the
observed data, so **FDATE = vintage** is the reading to use; the splits note's legend is
an error in the note. Recorded here because acting on the splits note's legend inverts
your join and silently produces the D1 defect on purpose.

### D3. Coverage collapses after ~2013, then again in 2019

**Source:** *Research Note Regarding Thomson-Reuters Ownership Data Issues*, May 2017.

- **Stale and dropped filers.** BlackRock (mgrno 9385) carried forward stale from 2013Q3,
  then **entirely missing 2014-06 through 2015-03**, then returned at $48B against a true
  ~$700B+. Fixed in the 2018 regeneration.
- **Excluded securities.** ~30% of the universe / ~15% of market cap dropped after June
  2013; **all ETFs gone by 2015**. Do not use S34 for ETF holdings, ever.
- **AAPL specifically: zero institutional owners from 2015-06-30 onward** in the vintage
  current at that time — and the 2014-06-30 row jumps 516,786,227 → 3,665,520,154, the
  unadjusted 7:1 split (D1 again).
- Aggregate error: 2015 institutional ownership is 58.25% in S34 against 71.8% in the
  actual 13F filings — **19.4% of total institutional ownership missing**.

**Feb 2022 note** (*Thomson/Refinitiv Data Issues 13F (S34) and Mutual Fund Holdings
(S12)*): 13F holdings largely missing in **2019Q3–Q4** — filers per stock fell from 1,500+
to under 500 for AAPL/IBM/MSFT; 1,386 of 12,224 stocks lost >10% of institutional
ownership, median −48%. Also flags **2011-03 to 2013-03** as significantly incomplete
(consistent with the Koijen–Yogo footnote). WRDS froze the web query at 2019Q2 and moved
later data to `/wrds/tfn/sasdata/s34/incomplete`.
**April/August 2022 notes:** Refinitiv patched 2019Q3–Q4 and the S12 2017Q4/2019Q4 gaps.
Pre-2011 issues are **permanently unfixable** — "the data vendor no longer provide the
support for the vintage raw data."

→ Detector: `detect_owner_dropout` (owners collapse = feed defect) as opposed to
`detect_flat_owner_share_swing` (owners flat = units defect). Run both; the pair
*classifies* the break.

### D4. 2010–2016 was regenerated; an archive of the corrupt data exists

**Source:** *S12/S34 Regenerated Data (2010–2016)*, June 2018. Thomson's legacy systems
were "losing and corrupting data" from 2010. The regenerated data is in `/wrds/tfn/sasdata`;
the corrupted original is preserved at **`/wrds/tfn/sasdata_archive`** — use it to
reconcile against results published before 2018. Residual known issues: S12TYPE8 lost a
third of its coverage after 2013; a 2014 mutual-fund ownership blip (29%→35%→30%) likely
from double-counted funds; S34TYPE2 still excludes ~$1T of CRSP securities; a 5% ownership
drop at the 2010-12→2011-03 feed migration.

### D5. S12 feed change at 2017Q4 — a coverage *increase* that looks like a defect

**Source:** *Thomson/Refinitiv Data Feed Change from Legacy SP to Strategic Collection in
S12*, 2023-01-24. From 2017Q4 the S12 feed switched from legacy SP to "strategic
collection", backfilled in 2022Q4. **CUSIPs in fund holdings +613%, unique funds +113%,
fund-CUSIP observations +265%**, and 34,385 funds appear that did not exist in the old
feed. Cause: overseas holdings, ADRs, preferreds and bonds got CUSIPs assigned that the
legacy feed lacked — it is a genuine coverage expansion, not corruption. But any
level-comparison spanning 2017Q4 is invalid, and **MFLINKS was not backfilled** for
2017Q4–2020Q2, so `wficn` bridge rates drop precisely there. WRDS's own advice is to use
Factset or CRSP to identify US equity funds over that window.

This corroborates the measured MFLINKS bridge-rate cliff (§Common Gotchas #12: ~77%
pre-2017 → ~58–66% after).

→ Detectors: `detect_coverage_step` (counts step while values do not — the signature of a
feed migration, and it fires in both directions so it also catches the 2019Q3–Q4 S34
contraction) and `detect_bridge_rate_regression` (a join that quietly stops matching).

### D5b. S12 duplicate reporting

The June 2018 note attributes the 2014 mutual-fund ownership blip (29% → 35% → 30%) to
funds being **listed twice**. Separately, `fundno` is frequently a *share-class*
identifier rather than a fund: one `wficn` maps to a mean ~3.5 `crsp_fundno`, so
deduplicating on the wrong key inflates `MF_TOTAL` by **~3.95×** (§Common Gotchas #6).
Both are the same class of defect — a grain that is not unique where the code assumes it
is — and neither is visible in a spot check.

→ Detector: `detect_duplicate_grain`, which reports the resulting inflation factor rather
than just a duplicate count.

### D6. 13F `value` units change at 2023Q1 — measured, NOT documented by WRDS

Form 13F `value` moves from thousands to whole dollars. Measured on the EDGAR panel:
mean value per holding 152,644 (2022Q4) → 12,794,119 (2023Q1); **median 442×, p90 494×,
p10 only 38×**.

The break is **not a clean 1000×**. The post-2023Q1 population is mixed — filers converted
on different schedules — so **no scalar repairs it**, and any sum or average of `value`
spanning 2023Q1 is meaningless. Note the legacy S34 spec still labels the field
"Holding Value (x$1000)".

**No WRDS note documents this.** The gap is itself worth knowing: the S12/S34 defect notes
stop at the 2023 S12 feed change and say nothing about 13F value units. Treated as a
source property to detect and refuse to aggregate across, not to repair.

→ Detector: `detect_unit_discontinuity`, which reports `clean_break=False` when the
quantile shifts disagree — the signal that rescaling is not an option.

### D7. Encoding-driven silent parse failures — measured, parser-side

Windows-1252 filings parsed to **zero** holdings rows: 7,023 filings / 2,628,463 rows /
768 institutions across all 38 quarters, with a **3.47× step at 2023Q3** (5.54% of
holdings dropped after vs 1.60% before), concentrated by filing agent. Now fixed, but the
class recurs with any new vendor encoding — a parser that yields nothing is
indistinguishable from a filer that held nothing.

→ Detector: `detect_zero_row_cohort`, grouped by encoding *or* filing agent, with
per-period rates so a time-varying step is visible rather than averaged away.

### Cross-check: EDGAR 13F is clean where Thomson is not

Same firm (AAPL), EDGAR `13F-HR` / `shares_type=SH` / no amendments: quarterly variation
of a few percent, filer counts monotone, and the 2020 4:1 split appearing exactly once and
correctly unadjusted (2.59bn → 9.70bn at 2020Q3). The defect is Thomson's, not the
concept's. Thomson's remaining advantage is history: it starts 1980, EDGAR electronic
filings start 1999 and XML only in 2013.
