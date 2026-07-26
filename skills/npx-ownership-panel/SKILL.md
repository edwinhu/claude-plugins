---
name: npx-ownership-panel
description: Build the meeting-level proxy-voting × ownership panel on the WRDS SGE grid — ISS N-PX fund votes reduced to (item × block) direction cells, joined to institutional and mutual-fund ownership. Use when working with risk.voteanalysis_npx, N-PX fund-level votes, ISS→CRSP fund linking, index/passive/active voting blocks, or a proxy-voting panel that needs ownership attached.
---

# N-PX × Ownership Panel

Builds `out.pass_npx` — item-level ownership joined to each item's per-block
observed For/Against/Abstain split — entirely on the WRDS grid.

**Verified end-to-end from a clean checkout on 2026-07-25.** One bash command,
WRDS credentials, nothing else: 12m 17s wall, zero errors, `out.pass_npx` at
2,018,866 rows. The measured run is in [Verification](#verification) below —
the one-command claim is measured, not asserted.

## Why this exists as a skill

`npx_agreement.sas` sat in one project for five months doing exactly what
another project needed, and nothing surfaced it. The second project rebuilt it
locally in Python and shipped 144,376,253 rows to a laptop for want of a leg
that already existed. **The failure was findability, not capability.**

## The one command

```bash
scp -r scripts/* wrds:~/projects/myproject/          # + npx_link.csv (see below)
ssh wrds "cd ~/projects/myproject && bash run_pipeline.sh"
```

It submits the whole DAG with `qsub -hold_jid` and **returns**. SGE sequences
the chain; nothing local stays alive. Disconnect, come back, collect the panel.

| Output | Grain | What it is |
|---|---|---|
| **`out.pass_npx`** | **`(itemonagendaid, block)`** | the deliverable — ownership panel + per-block vote direction |
| `out.pass` | `itemonagendaid` | item-level ownership panel alone |

## The four legs

| # | Leg | Script | Depends on |
|---|---|---|---|
| 1 | Mutual-fund holdings | `split_s12.sas` → `tfn_holdings_parallel.sas` ×N | — |
| 2 | Institutional holdings | `build_inst_own.sas` (Thomson S34) + EDGAR 13F scrape | — |
| 3 | N-PX fund votes | `build_npx.sas` (SGE array, one task per year) | **leg 4** |
| 4 | ISS→CRSP crosswalk | `npx_linking/` → `stage_npx_link.sas` | — |

Legs 1, 2, 4 start together. **Leg 4 hard-gates leg 3** — the array hash-merges
the crosswalk; without it every task opens a missing dataset and exits 0 having
written nothing.

**Leg 2 has two sources for one quantity.** Thomson S34 decayed after 2013 and
undercounts; the EDGAR scrape exists for that reason. EDGAR wins where present,
S34 is fallback-only, **never blended**.

## Before you run: build the crosswalk

Leg 4's input is a `fundid → block` crosswalk you build once, locally. It is the
hard half — see [references/npx-crsp-linking.md](references/npx-crsp-linking.md).

```bash
cd scripts/npx_linking
export SEC_USER_AGENT="your name your@email.edu"
./download_sec_series_class.py --out data/raw/sec_series_class
SEC_SERIES_RAW=data/raw/sec_series_class SEC_SERIES_OUT=data/processed ./build_sec_series_master.py
./smoke_test.sh 2023                       # 8 assertions, ~2 min — run this first
./pull_npx_funds.py  --out npx_funds.parquet
./pull_crsp_funds.py --out crsp_funds.parquet
./build_npx_crsp_link.py --npx-funds npx_funds.parquet --crsp-funds crsp_funds.parquet \
    --sec-series-master data/processed/sec_series_master.parquet \
    --sec-series-names-long data/processed/sec_series_names_long.parquet \
    --out npx_crsp_link.parquet
cd .. && ./npx_link_to_csv.py --in npx_linking/npx_crsp_link.parquet --out npx_link.csv
```

Runs from WRDS credentials alone. Links **80.8%** of vote rows.

## Two invariants, both in SAS

They must hold for an unsupervised `bash run_pipeline.sh` with no harness
anywhere, so neither lives in an orchestrator.

**One universe.** `pipeline_config.sas` declares the date window, meeting types
and vote results once; both legs `%include` it. Before it they disagreed —
2003-2024 with filters vs 2005-2025 with none — and nothing detected it.

**Two hard gates in `merge_panel.sas`**, both `abort abend`:
- *Prerequisites*: every expected output exists. `-hold_jid` releases on
  **completion, not success**, so a dead leg otherwise lets the merge start.
- *Universe*: every `out.meetings` item exists in `out.npx_items`.

Both fired during verification and caught real failures. That is the point.

## Verification

Clean checkout on WRDS, one `bash run_pipeline.sh`, 2026-07-25:

| | |
|---|---|
| Wall clock | **12m 17s** (20:49:55 → 21:02:12) |
| `ERROR` lines, all logs | **0** |
| PREREQ gate | `mf_own_chunks=2 npx_cell_years=21/21` → all inputs present |
| UNIVERSE gate | `meetings_items=623,642 npx_items=712,466` **orphans=0** |

Leg boundaries:

| Leg | Measure | Value |
|---|---|---|
| 1 S12 | partition rows | 22,369,087 (2019) · 25,076,427 (2023) |
| 3 N-PX | vote rows kept | **140,382,295** |
| 3 N-PX | cells before re-agg | 2,130,231 |
| 3 N-PX | unlinked vote rows | **0** |
| 4 crosswalk | fundids / item frame | 26,686 / 712,466 |

Final panel — `out.pass_npx`, grain `(itemonagendaid, block)`, **2.28 GB**:

```
    n_rows      n_items   items_no_npx      vote_rows
 2,018,866      623,642         27,294    134,723,487

block           cells       vote_rows
active        572,426      75,563,301
index         585,916      48,517,584
passive       533,516       8,045,939
asset_owner   299,714       2,596,663
```

**One scope caveat, stated plainly.** The S12 leg ran over 2 of 9 partition
ranges (2019, 2023). The full set is ~40 GB and this account's `/scratch` quota
is **22 GB** — measured, `dd` fails with "Disk quota exceeded". Every other leg
ran at full scale. Check `quota` and trim `S12_RANGES` in `pipeline_config.sas`
to fit; the chain completes either way over a narrower holdings window.

Three real defects surfaced by running it, all fixed:
- `tfn_holdings_parallel.sas` used **open-code `%IF`**, which errors on this SAS
  deployment. As shipped it had never run — it died before reading a row.
- `split_s12.sas` and `run_pipeline.sh` hardcoded the partition list
  **separately**. Now single-sourced from `pipeline_config.sas`.
- A greedy `sed` reading that list swallowed a `;` inside a trailing comment and
  submitted 11 jobs for a 2-partition list — the bash/SAS divergence the shared
  list exists to prevent.

## References

| File | What |
|---|---|
| [references/pipeline.md](references/pipeline.md) | full pipeline detail, benchmark, SAS traps |
| [references/npx-crsp-linking.md](references/npx-crsp-linking.md) | the crosswalk — digit guard, trust-prefix, cross-family veto, uint32 and TNA traps |
| [references/linking.md](references/linking.md) | running the linking ladder; tier coverage |

## Key facts

- `risk.voteanalysis_npx` is **238,445,215 rows / 329 GB**. Never download it.
  Aggregate on the grid: 2.25M cells instead of 144M rows, 20.8 MB instead of
  304 MB, 839s instead of a 35-minute sequential pull.
- **The date range is not the analysis universe.** `npx.meetingdate` over
  2005-2025 is 237,057,808 rows; items present in `vavoteresults` are
  144,375,860. Date alone inflates every block denominator by ~64%.
- **`vavoteresults` is not unique on `itemonagendaid`**, so an `INNER JOIN` fans
  out. A hash keyed on it cannot.
- Write `meetingdate between "01jan&year."d and "31dec&year."d`, never
  `year(meetingdate) = &year.` — a function on the indexed column defeats the
  15 GB index and full-scans 329 GB per task.
- **Budget for a straggler.** One array task took 742s against a 60s median and
  the array still reported clean at 20 of 21 outputs.

## See also

`wrds` skill — connection patterns, `references/iss-voting.md` for the tables
themselves, `references/postgres-vs-sas.md` for engine choice.
