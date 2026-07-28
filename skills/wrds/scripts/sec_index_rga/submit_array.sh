#!/bin/bash
#$ -N sec_idx
#$ -l m_mem_free=2G
#$ -cwd
#$ -j y
#$ -o logs/
#
# submit_array.sh — SGE array wrapper for scan_shard.sh
#
# PATHS. Nothing here names a user or an institution. The scratch root is
# derived, and every consumer honours an override:
#
#   WRDS_SCRATCH   root for shard list, binaries and output
#                  (default: /scratch/${WRDS_INST:-nyu}/$(whoami))
#   SHARD_LIST     shard list file      (default: $WRDS_SCRATCH/sec_index_rga/shards.txt)
#   OUT_DIR        gzipped TSV output   (default: $WRDS_SCRATCH/sec_index)
#   SCAN_BIN       scanner to exec      (default: $WRDS_SCRATCH/sec_index_rga/bin/scan_shard.sh)
#   ARCHIVE_ROOT   EDGAR archive root   (default: /wrds/sec/archives)
#
# `#$ -o logs/` is relative to -cwd on purpose: SGE parses the `#$` block
# BEFORE the shell runs, so a ${VAR} in that directive is a literal, not an
# expansion. A user-specific absolute path there is therefore NOT fixable by
# environment — it would keep writing into one account's scratch no matter who
# submitted. Override at submit time with `qsub -o <dir>` if you want it
# elsewhere.
#
# Submit ALL 164 shards:
#   cd "$WRDS_SCRATCH/sec_index_rga" && mkdir -p logs
#   ls -d /wrds/sec/archives/*/ | sed 's|/wrds/sec/archives/||;s|/$||' | sort > shards.txt
#   qsub -t 1-$(wc -l < shards.txt) submit_array.sh
#
# Submit first 5 shards (validation):
#   qsub -t 1-5 submit_array.sh

set -u

WRDS_SCRATCH="${WRDS_SCRATCH:-/scratch/${WRDS_INST:-nyu}/$(whoami)}"

export SHARD_LIST="${SHARD_LIST:-$WRDS_SCRATCH/sec_index_rga/shards.txt}"
export OUT_DIR="${OUT_DIR:-$WRDS_SCRATCH/sec_index}"
export ARCHIVE_ROOT="${ARCHIVE_ROOT:-/wrds/sec/archives}"
SCAN_BIN="${SCAN_BIN:-$WRDS_SCRATCH/sec_index_rga/bin/scan_shard.sh}"

mkdir -p "$OUT_DIR" logs

exec "$SCAN_BIN"
