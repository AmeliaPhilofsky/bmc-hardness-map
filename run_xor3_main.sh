#!/bin/bash
# XOR study, corrected design: XOR gates are 3-input (a ^ b ^ s) on the same wires
# the MUX reads, so wiring and depth are identical at every XOR level.
# Same sizes and seeds as run_xor_main.sh, so the 0% points are the same circuits.
# Usage (from ~/bmc-baselines/bmc-harness, inside tmux):  bash run_xor3_main.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
for spec in "64 16" "64 24" "64 32" "96 16"; do
  set -- $spec
  d=instances/main3/i$1_g$2
  [ -f $d/manifest.jsonl ] || python3 gen_xor.py --out $d --inputs $1 --gates-per-input $2 --seeds 10 --xor-arity 3
done
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_xor3_machine.txt
python3 run_bench.py --manifest instances/main3/*/manifest.jsonl --out results/xor3_main.csv \
  --configs kissat cadical cms cms_gauss_xnat kissat_aig sby_yices ebmc_cadical \
  --solver-seeds 2 --timeout 180 --workers $WORKERS
{ date; uptime; } >> results_xor3_machine.txt
python3 summarize.py results/xor3_main.csv --csv results/xor3_main_summary.csv > results/xor3_main_summary.txt
echo "summary written to results/xor3_main_summary.txt"
