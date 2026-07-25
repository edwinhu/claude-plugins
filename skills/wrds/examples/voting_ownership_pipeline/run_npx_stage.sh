#!/bin/bash
#$ -cwd
#$ -N npx_stage
#$ -j y
#$ -l m_mem_free=4G
#
# run_npx_stage.sh — SGE wrapper for stage_npx_link.sas.
#
# Not run_sas.sh: that builds the log filename from the sysparm, and this
# sysparm contains spaces and a path.
#
#   qsub -v "Y1=2005,Y2=2025,LINKCSV=$PWD/npx_link.csv" run_npx_stage.sh

set -u
mkdir -p logs

Y1="${Y1:-2005}"
Y2="${Y2:-2025}"
LINKCSV="${LINKCSV:-$(pwd)/npx_link.csv}"

echo "stage host=$(hostname) years=${Y1}-${Y2} csv=${LINKCSV} started=$(date -Is)"

sas -nodms -noterminal -nosyntaxcheck \
    -sysparm "${Y1} ${Y2} ${LINKCSV}" \
    -log   logs/stage_npx_link.log \
    -print logs/stage_npx_link.lst \
    stage_npx_link.sas
rc=$?

grep -hE "LINKSTAT|ITEMSTAT|^ERROR" logs/stage_npx_link.log || true
echo "stage finished=$(date -Is) rc=$rc"
exit $rc
