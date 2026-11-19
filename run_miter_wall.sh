#!/bin/bash
# Where does refutation become hard? Equivalence miters with the rebuilt (linear) copy,
# over circuit size: 8, 12, 16, 24, 32, 48 inputs, 24 gates per input, window = 2 x inputs
# (the same shape as the 64-input pilot, where Kissat and CaDiCaL failed on everything).
# XOR 50, 70, 90, 100%; 3 circuits each; Kissat, CaDiCaL, CryptoMiniSat + Gauss; 1 solver seed;
# 300 s timeout. 216 runs, worst case ~55 min, then the DRAT proof pass for solved runs.
# Usage (bash, inside tmux):  bash run_miter_wall.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
PROOF_TMP=${PROOF_TMP:-/tmp}
PROOF_WORKERS=${PROOF_WORKERS:-4}
d=instances/miter_wall
[ -f $d/manifest.jsonl ] || python3 gen_miter.py --out $d --inputs 8 12 16 24 32 48 --gates-per-input 24 \
  --windows 0 --xor-pcts 50 70 90 100 --seeds 3 --restructure linear
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_miter_wall_machine.txt
( while true; do echo "$(date '+%F %T') $(cut -d' ' -f1-3 /proc/loadavg)"; sleep 120; done ) \
  > results/miter_wall_load.log 2>&1 &
LOADER=$!
trap "kill $LOADER 2>/dev/null" EXIT
python3 run_bench.py --manifest $d/manifest.jsonl --out results/miter_wall.csv \
  --configs kissat cadical cms_gauss_xnat --solver-seeds 1 --timeout 300 --workers $WORKERS
python3 check_proofs.py --results results/miter_wall.csv --manifest $d/manifest.jsonl \
  --out results/miter_wall_proofs.csv --workers $PROOF_WORKERS --tmp "$PROOF_TMP" \
  --min-free-gb 3 --max-proof-gb 2
{ date; uptime; } >> results_miter_wall_machine.txt
python3 summarize.py results/miter_wall.csv > results/miter_wall_summary.txt
echo "summary written to results/miter_wall_summary.txt"
