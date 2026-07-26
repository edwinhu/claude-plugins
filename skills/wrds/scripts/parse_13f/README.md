# parse_13f — 13F EDGAR holdings scrape on the WRDS grid

Parses `13F-HR` / `13F-HR/A` filings straight out of `/wrds/sec/archives` into
gzipped TSV: one row per holding, plus a manifest row per filing.

Measured end to end on 2026-07-25: **248,500 filings, 45.31 GB, 89,072,489
holdings rows, ~2.5 min wall** across 38 quarters (2016Q4–2026Q1).
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

## The XML library was two thirds of the cost

Worth knowing before you try to tune anything else. `runtime/pprof` over a full
quarter (2024Q2, `GOMAXPROCS=4`, 104.06 s of samples):

| Component | Share of CPU |
|---|---:|
| `encoding/xml.(*Decoder).Token` | **63.4%** — ~31 pp of it in `nsname`/`name`/`readName`/`isName`, i.e. namespace-name validation |
| `compress/flate` (the single gzip writer goroutine) | **9.2%** — exactly the serial fraction Amdahl predicted from the slot-scaling curve |
| GC | ~6.2% |

Nothing about the *parsing logic* was slow. The general-purpose XML parser was.
Replacing it for the information table — which is machine-generated,
attribute-free and entity-simple — is where the 2.8x came from. If you are
looking for more speed, that profile is the map; do not start by micro-tuning
the regexes.

## Two things about correctness

**A canonical-hash identity test proves a refactor was faithful, not that the
behaviour was right.** The optimisation here passed identity against the old
parser on all 38 quarters while both were silently dropping 3% of holdings. The
hashes matched *because* both were wrong in the same way. Identity answers "did
this change alter behaviour?"; it cannot answer "was the behaviour correct?"

**A filing that parses to zero rows is invisible.** It looks exactly like an
institution that did not file — no orphan, no row-count mismatch, nothing a
universe check can catch. Assert on it directly:

```bash
gzip -dc out/*.manifest.tsv.gz | awk -F'\t' '$9=="xml" && $10=="ok" && $8==0'
```

That query is how the windows-1252 defect was found: **7,023 filings and
2,628,463 holdings rows** (+3.04% rows, +3.84% of reported value, 768
institutions) were being dropped. Fixed in `charset.go`; an undecodable charset
now returns `parse_status=error` rather than an empty table.

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
- **Legacy-encoded filings were dropped until v0.2.1.** If you have output from
  an earlier build, it is missing ~3% of holdings — regenerate it. The count of
  affected filings jumped ~5x in 2023Q3 when several filing agents switched to
  emitting windows-1252, so the loss is time-varying, not a constant offset.

## Rebuilding the parser

Go is not installed on the grid. Either build locally and copy the static
binary, or drop a toolchain on scratch:

```bash
cd parse_13f_go && go build -o parse_13f_go . && go test ./...
```

`-verify-fast-xml` is the equivalence harness: it parses every filing with both
the fast scanner and `encoding/xml` and prints `FASTXML-MISMATCH` on any
divergence. Run it over a quarter after touching `xml_fast.go`.
