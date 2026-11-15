#!/bin/bash
# UNSAT version of the width x XOR grid: the same 420 circuits as results/grid.csv
# (64 inputs, 1,536 gates, 7 windows x 6 XOR levels x 10 seeds), each checked
# against a restructured equivalent copy. Same solvers, seeds, timeout and workers
# as the SAT grid, so the two can be compared cell by cell.
# Timed runs write no proofs; afterwards check_proofs.py re-solves the Kissat and CaDiCaL
# UNSAT answers (solver seed 0) with a DRAT proof and checks it with drat-trim.
# Worst case (everything times out): about 11 h of solving plus proof checks.
# Usage (bash, inside tmux):  bash run_miter_grid.sh      Run run_miter_pilot.sh first.
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
PROOF_TMP=${PROOF_TMP:-/tmp}
PROOF_WORKERS=${PROOF_WORKERS:-4}
d=instances/miter_grid
[ -f $d/manifest.jsonl ] || python3 gen_miter.py --out $d --inputs 64 --gates-per-input 24 \
  --windows 64 96 128 160 192 224 256 --xor-pcts 0 50 70 80 90 100 --seeds 10
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_miter_grid_machine.txt
( while true; do echo "$(date '+%F %T') $(cut -d' ' -f1-3 /proc/loadavg)"; sleep 300; done ) \
  > results/miter_grid_load.log 2>&1 &
LOADER=$!
trap "kill $LOADER 2>/dev/null" EXIT
python3 run_bench.py --manifest $d/manifest.jsonl --out results/miter_grid.csv \
  --configs kissat cadical cms_gauss_xnat --solver-seeds 2 --timeout 300 --workers $WORKERS
# second pass: re-solve UNSAT answers with a DRAT proof and check it (few workers, disk guarded)
python3 check_proofs.py --results results/miter_grid.csv --manifest $d/manifest.jsonl --out results/miter_grid_proofs.csv \
  --workers $PROOF_WORKERS --tmp "$PROOF_TMP" --min-free-gb 3 --max-proof-gb 2
{ date; uptime; } >> results_miter_grid_machine.txt
python3 summarize.py results/miter_grid.csv > results/miter_grid_summary.txt
echo "summary written to results/miter_grid_summary.txt"
