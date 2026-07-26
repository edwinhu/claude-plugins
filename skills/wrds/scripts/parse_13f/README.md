# parse_13f — 13F EDGAR holdings scrape on the WRDS grid

Parses `13F-HR` / `13F-HR/A` filings straight out of `/wrds/sec/archives` into
gzipped TSV: one row per holding, plus a manifest row per filing.

Measured end to end on 2026-07-25: **248,500 filings, 45.31 GB, 86,444,026
holdings rows, 2 min 32 s wall** across 38 quarters (2016Q4–2026Q1).
Full numbers, method and grid limits: [`references/13f-scrape-performance.md`](../../references/13f-scrape-performance.md).

## Layout

```
parse_13f_go/        the parser (Go, stdlib only, no modules to fetch)
sge/
  make_filelists.sas  quarter filelists from /wrds/sec/sasdata/wrds_forms
  scan_sizes.py       stat every filing -> sizes.tsv  (shard planning input)
  build_shards.py     byte-balanced shard lists
  scan_shard.sh       SGE array worker, one shard per task
  submit_shards.sh    SGE array wrapper
```

## Running it

Everything runs on compute nodes. SAS is not on the login host's PATH, and the
login host has a low memory ceiling that `scan_sizes.py` will hit.

```bash
ROOT=/scratch/nyu/hue/parse_13f          # needs ~2 GB out, 300 GB free is plenty
mkdir -p $ROOT/{bin,filelists,out,logs}
cp -r sge $ROOT/ && cp parse_13f_go/parse_13f_go $ROOT/bin/

# 1. filelists, one per filing-date quarter          (~10 s)
qsub -pe onenode 2 -l m_mem_free=8G run_sas.sh $ROOT/sge/make_filelists.sas

# 2. true file sizes — wrds_forms.fsize is unusable  (~7 s)
qsub -pe onenode 2 -l m_mem_free=8G run_python.sh $ROOT/sge/scan_sizes.py $ROOT/filelists

# 3. byte-balanced shards                            (local, instant)
python3 $ROOT/sge/build_shards.py $ROOT/filelists/sizes.tsv \
        $ROOT/filelists/shards --target-mb 200

# 4. SMOKE TEST FIRST — 8 shards, ~1 min
cd $ROOT && qsub -t 1-8 sge/submit_shards.sh

# 5. full run, once the smoke test is clean
qsub -t 1-$(wc -l < filelists/shards/chunks.txt) sge/submit_shards.sh
```

Output lands in `$ROOT/out/<quarter>_<nn>.tsv.gz` plus matching
`.manifest.tsv.gz`. Shards are built within quarter, so a quarter's holdings are
`cat`-able from its shards in any order.

## Things that will bite you

- **Ten slots, total, per user in `all.q`.** Not ten tasks — ten slots, shared
  with everything else the account is running. The array self-throttles; submit
  all of it and let the scheduler meter it. `-pe onenode N` also rejects `N > 8`,
  and `ssdwork.q` is blocked by the server JSV.
- **No rclone stage, deliberately.** `/wrds/sec/archives` is mounted on the
  compute nodes. rclone is for moving filings *off* WRDS; staging them first
  would just add a pass over 45 GB.
- **Row order is not stable.** Workers write in completion order. Compare
  outputs with a canonical (sorted) dump, never with file bytes — see the
  identity section of the performance reference.
- **`fsize` on `wrds_forms` lies for recent filings.** That is why step 2 exists.
- **Filings declared `windows-1252` currently yield zero holdings rows** — a real
  open defect, ~4.7% of a recent quarter, documented in the performance
  reference. Detect with:
  ```bash
  gzip -dc out/*.manifest.tsv.gz | awk -F'\t' '$9=="xml" && $10=="ok" && $8==0'
  ```

## Rebuilding the parser

Go is not installed on the grid. Either build locally and copy the static
binary, or drop a toolchain on scratch:

```bash
cd parse_13f_go && go build -o parse_13f_go . && go test ./...
```

`-verify-fast-xml` is the equivalence harness: it parses every filing with both
the fast scanner and `encoding/xml` and prints `FASTXML-MISMATCH` on any
divergence. Run it over a quarter after touching `xml_fast.go`.
