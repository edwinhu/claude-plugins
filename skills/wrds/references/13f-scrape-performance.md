# 13F EDGAR Scrape — Measured Performance on the WRDS Grid

Everything here was measured on wrds-cloud on 2026-07-25 against
`/wrds/sec/archives`, not derived. Where a number is extrapolated it says so.

## Contents

- [The scheduler is the constraint](#the-scheduler-is-the-constraint) — 10 slots per user, 8 per job
- [The corpus](#the-corpus) — 248,500 filings, 45.31 GB, 38 quarters
- [Single-task scaling](#single-task-scaling) — per-slot throughput peaks at ONE slot
- [Full-run wall clock](#full-run-wall-clock) — 8m23s shipped, 2m32s optimised
- [Where the CPU went](#where-the-cpu-went) — encoding/xml was 63%
- [Array design](#array-design) — shard on bytes, one slot per task
- [Byte-identity method](#byte-identity-method)
- [Open defect: filings declared windows-1252 parse to zero rows](#open-defect-filings-declared-windows-1252-parse-to-zero-rows)

## The scheduler is the constraint

Not CPU, not NFS. Three limits, all verified by submission:

| Limit | Value | How it was established |
|---|---|---|
| Slots per user in `all.q` | **10** | `qconf -srqs` → `max_user_jobs`: `limit users {*} queues {all.q} to slots=10`. Confirmed by `qstat`: an 8-slot job plus a 2-slot job ran, a third queued. |
| Slots per single job | **8** | `qsub -pe onenode 10` → `Unable to run job: Too many slots requested, jobs may not use over 8 slots.` (server JSV) |
| Other queues | **none available** | `qsub -q ssdwork.q` → `Unable to run job: Invalid queue ssdwork.q.` The JSV rejects it even though the queue has 300 free slots and no `user_lists`. |

`schedule_interval` is 4 s with `flush_submit_sec`/`flush_finish_sec` at 1 s, so
array dispatch is prompt; the ceiling is the slot count, not the scheduler's
reaction time.

**The practical consequence.** Ten slots is the entire budget for this leg, and
it is shared with every other job the same account runs. Any wall-clock claim
that assumes more than ten concurrent slots is wrong. In particular
*"N concurrent tasks"* is not the unit — a 4-slot task consumes four of the ten,
so only **two** run at once.

## The corpus

`13F-HR` and `13F-HR/A`, filing-date quarters 2016Q4–2026Q1, from
`/wrds/sec/sasdata/wrds_forms.sas7bdat` (grid SAS, ~10 s; no PostgreSQL):

| | |
|---|---|
| Quarters | 38 |
| Filings | **248,500** |
| Input bytes | **45.31 GB** (`os.path.getsize`, 0 missing) |
| Holdings rows produced | **86,444,026** |
| Per-quarter filings | 4,508 (2016Q4) → 9,511 (2026Q1) |

`13F-NT` (notice, no holdings table) is excluded; including it would add
131,127 filings that produce no rows.

> **`fsize` on `wrds_forms` is not usable for shard planning.** It is populated
> for older filings and collapses for recent ones — 2021Q2 reports 0.016 GB
> across 6,307 filings (~2.6 KB each) against a true ~1 GB. Stat the archive
> instead: 248,500 `getsize` calls take **7 seconds** with 48 threads.

## Single-task scaling

2024Q2, 8,114 filings, 1.44 GB, one job, `GOMAXPROCS=NSLOTS`,
`concurrency=NSLOTS*8`, shipped parser:

| Slots | Wall | filings/s | **filings/s per slot** | CPU/wall | Peak RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 90.9 s | 89.3 | **89.3** | 0.94 | 454 MB |
| 2 | 54.4 s | 149.3 | **74.6** | 1.78 | 684 MB |
| 4 | 27.9 s | 291.2 | **72.8** | 3.56 | 1,048 MB |
| 8 | 17.5 s | 462.7 | **57.8** | 5.94 | 1,217 MB |

Two things follow, and they point the same way:

1. **Per-slot throughput is highest at one slot** and decays to 65% of that by
   eight. Amdahl on the 1→8 speedup (5.18×) puts the serial fraction at ~7.8%,
   which the CPU profile then localised exactly (the single gzip writer
   goroutine, below).
2. **Under a fixed slot cap, many small tasks beat few big ones.** Ten 1-slot
   tasks and two 4-slot tasks both consume the same budget; the first shape
   does more work with it.

`m_mem_free=4G` per slot — what the previous submit script asked for — over-reserves
by roughly 8× against a measured 454 MB at one slot. 2G is ample and schedules faster.

## Full-run wall clock

All 38 quarters, 248,500 filings, measured end to end (makespan = last task
end − first task start, so scheduling gaps are included):

| Run | Parser | Shape | Makespan | filings/s | **filings/s per slot** |
|---|---|---|---:|---:|---:|
| **A** | shipped | 38 quarter shards × 4 slots | **503.4 s (8m 23s)** | 493.6 | 63.8 |
| **B** | optimised | 38 quarter shards × 4 slots | **190.0 s (3m 10s)** | 1,307.7 | 180.6 |
| **C** | optimised | 230 byte-balanced shards × 1 slot | **151.8 s (2m 32s)** | 1,637.3 | 195.9 |

A→B isolates the parser change (same array shape): **2.65× on makespan, 2.83×
on slot-seconds** (3,897 → 1,376). B→C isolates the array shape: a further
**1.25×**, from higher per-slot throughput at one slot (180.6 → 195.9 filings/s
per slot), better occupancy (7.24 → 8.36 of 10 slots) and a much shorter tail
(task p90 13.4 s → 6.1 s). End to end, **A→C is 3.32×**.

> **Read makespan with the queue depth attached.** None of these three runs had
> all ten slots to itself — mean occupancy was 7.74 (A), 7.24 (B) and 8.36 (C)
> of 10, because a second job stream shared the account throughout. Makespan is
> therefore *pessimistic and not exactly reproducible*; the contention-robust
> number is **filings/s per slot**, which is computed from slot-seconds and is
> unaffected by queue wait. Comparisons between A, B and C are sound because all
> three ran under comparable contention. On a grid where all ten slots were
> genuinely free, run C's per-slot rate implies **248,500 / (195.9 × 10) ≈ 127 s**
> — a projection, not a measurement.

**Observed-slot vs full-parallelism, stated separately as they must be:**

| Basis | Wall clock | Status |
|---|---:|---|
| One 4-slot task, serial over all 38 quarters, shipped parser | 64.9 min | computed from 3,897 slot-seconds |
| **10 slots (the real cap), shipped parser, quarter shards** | **8m 23s** | **MEASURED** (run A) |
| **10 slots, optimised parser, byte-balanced 1-slot shards** | **2m 32s** | **MEASURED** (run C) |
| 38 shards fully parallel, 4 slots each (152 slots) | ~35 s | **NOT ACHIEVABLE** — needs 15× the slot cap |

### On the previously reported 1m 23s

The extrapolation in `npx-ownership-panel` reached **1m 23s** for this leg by
dividing a 13.9 min serial estimate by "10 concurrent, the observed slot count".
The per-slot measurement underneath it is sound — 284 filings/s at 4 slots
reproduces here as 273–291 filings/s — but the divisor conflates *ten slots*
with *ten tasks*. The tasks measured were 4-slot tasks, so ten of them would
need 40 slots against a hard cap of 10. The correct divisor for that shape is
**two** concurrent tasks, which lands at 8m 23s — and 8m 23s is what a full run
actually took.

The optimised leg does reach 2m 32s, but by making each slot do 2.8× the work,
not by finding slots that do not exist.

## Where the CPU went

`runtime/pprof`, 2024Q2 at `GOMAXPROCS=4`, 104.06 s of samples:

| Component | Share | Note |
|---|---:|---|
| `encoding/xml.(*Decoder).Token` | **63.4%** | of which ~31 pp in `nsname`/`name`/`readName`/`isName` — namespace-name validation |
| `compress/flate` (gzip writer) | **9.2%** | single goroutine; this *is* the serial fraction Amdahl predicted |
| GC (`gcBgMarkWorker`, `scanobject`) | ~6.2% | allocation pressure from the token stream |

Two changes followed, both stdlib-only, no new dependencies:

- **A hand-rolled information-table scanner** (`xml_fast.go`). The information
  table is machine-generated, attribute-free and entity-simple, so it does not
  need a general XML parser. The scanner reproduces the exact
  StartElement/EndElement/CharData sequence `encoding/xml` emits, and **refuses**
  anything it cannot mirror — CDATA, DOCTYPE, multi-colon names, unknown
  entities, mismatched end tags, non-UTF-8 declarations — falling back to
  `encoding/xml` for those. Correctness never depends on the scanner being
  complete, only on it being honest about its limits. Measured fallback rate:
  **2.6% (2016Q4), 4.8% (2024Q2)**, almost entirely the windows-1252 filings
  described below.
- **`gzip.BestSpeed` instead of the default level.** Halves the serial stage.
  Measured cost on 2024Q2: 71.4 MB → 81.4 MB, **+14.0%** on disk. The
  decompressed stream is unchanged, which is the only thing downstream reads.

The text-mode parser (`text_parser.go`) was **not** touched, and should not be:
across all three sampled quarters, 100% of rows parsed in `xml` mode. Its
per-line `regexp.Compile` in `extractBCSCascade` is a real inefficiency but it is
unreachable for filings from 2013Q3 on, so optimising it would buy exactly
nothing for this window.

## Array design

Measured answers to the four questions:

| Question | Answer | Basis |
|---|---|---|
| **Shard key** | Byte-balanced packing (LPT) **within quarter**, ~200 MB per shard | Contiguous equal-*count* chunks gave a **2× duration spread** (19.4 s vs 42.8 s for two 2,254-filing chunks of 2016Q4): archive path order is CIK order, and CIK correlates with filer size. Packing on measured bytes cuts imbalance to 10.4%. Sharding within quarter keeps the output quarter-partitioned, which is what the panel consumes, at negligible balance cost. |
| **Slot count** | **1 slot per task**, array self-throttles to 10 | Per-slot throughput 89.3 f/s at 1 slot vs 57.8 at 8. Confirmed at full scale: 195.9 vs 180.6 f/s per slot (run C vs run B). |
| **Memory per task** | **`m_mem_free=2G`** | Peak RSS 454 MB at one slot. The previous 4G was an ~8× over-reserve. |
| **Staging vs parsing** | **Fused — no staging stage at all** | `/wrds/sec/archives` is mounted directly on the compute nodes. There is nothing to stage: rclone is for moving filings *off* WRDS. A copy-first design would add a pointless pass over 45 GB. `sys` CPU is 3% of `user` CPU, so NFS is not a bottleneck at ten slots. |

Shard granularity has a floor. At 200 MB the median task runs 5.6 s against a
4 s `schedule_interval`, and mean occupancy was 8.36 of 10 slots — ~16% lost to
dispatch gaps. Coarser shards waste less on dispatch but leave a longer tail.

> **Not established:** whether 200 MB is the optimum. A 400 MB / 114-shard
> comparison run was submitted and then **cancelled** to free slots for a
> concurrent job, so the tail-versus-dispatch trade is reasoned, not measured.
> 200 MB is a working choice that measured well, not a tuned one.

## Byte-identity method

Parquet and sas7bdat embed timestamps, so file bytes are the wrong test. This
leg emits gzipped TSV, and gzip embeds nothing that varies here, but the
**row order is genuinely non-deterministic** regardless: worker goroutines push
to the writer in completion order, so two runs of unmodified code produce
different row orders. Canonical-dump equality is therefore the only meaningful
test, exactly as for the parquet legs.

For each quarter: `gzip -dc | LC_ALL=C sort | sha256sum`, plus row count,
manifest row count, per-column sums for all five numeric columns
(`value`, `shares`, `voting_sole`, `voting_shared`, `voting_none`), the
`cusip_valid` count, the `parse_mode` histogram and the `parse_status`
histogram — so a mismatch localises to a column instead of a hex string.

There are no floats anywhere in this output. Every numeric column is written by
`strconv.FormatInt`, so the fixed-precision rendering that the parquet legs need
is not a concern here; integers are exact.

**Result — all 38 quarters, 86,444,026 holdings rows, 248,500 manifest rows:**

```
IDENTITY: PASS — every quarter's canonical digest identical
  shipped parser (run A)  sha256(all 38 digests) = 671ef9d298a22d815b3048b012610982d8be7d45468afdfa4c7a2338d1df939a
  optimised parser (run B) sha256(all 38 digests) = 671ef9d298a22d815b3048b012610982d8be7d45468afdfa4c7a2338d1df939a
```

Three further checks:

- **The shared `canonical_hash.py`** (the primitive the other pipeline legs use)
  agrees, reached independently through parquet rather than sorted text:

  ```
  A.parquet  rows 2,692,060 x 23  sha256 c1a62dccf1e0779208b16bbfe150a35b7dae3563ca17cca7cef49ac6ff9c4565
  B.parquet  rows 2,692,060 x 23  sha256 c1a62dccf1e0779208b16bbfe150a35b7dae3563ca17cca7cef49ac6ff9c4565
  IDENTICAL (canonical sort + 12 sig digits)
  ```


- **Differential harness.** `parse_13f_go -verify-fast-xml` parses every filing
  *both ways* and compares row structs field by field. Over 2016Q4 and 2024Q2
  (12,622 filings): **0 mismatches**.
- **Frozen pre-change baseline.** Three quarters were hashed before any code was
  written; those digests match run A exactly, confirming the canonical dump is
  stable across runs despite the non-deterministic row order.

## Open defect: filings declared windows-1252 parse to zero rows

Found while building the differential harness; **not fixed**, because fixing it
would change output and byte-identity was the requirement.

`encoding/xml` refuses any document whose declaration names a non-UTF-8 encoding
when `Decoder.CharsetReader` is nil. `parseInfoTable` breaks out of its token
loop on that error and returns **zero rows with no error surfaced** — the filing
lands in the manifest as `parse_status=ok`, `parse_mode=xml`, `n_rows=0`.

These are ordinary 13F-HR filings with real holdings tables whose only sin is
`<?xml version="1.0" encoding="windows-1252"?>`. Measured scope: 382 of 8,114
filings in 2024Q2 (**4.7%**) and 115 of 4,508 in 2016Q4 (**2.6%**).

Detection query against the manifest:

```bash
gzip -dc *.manifest.tsv.gz | awk -F'\t' '$9=="xml" && $10=="ok" && $8==0'
```

Fixing it means installing a `CharsetReader` for windows-1252 (a 32-entry table
for 0x80–0x9F; the rest is Latin-1). That is a small change but a **semantic**
one — it adds holdings rows — so it belongs in its own commit with its own
before/after row-count delta, not smuggled into a performance change.
