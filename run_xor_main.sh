#!/bin/bash
# Main XOR-mix study + encoding comparison (same circuits, three CNF encodings).
# Usage (from ~/bmc-baselines/bmc-harness, inside tmux):  bash run_xor_main.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
for spec in "64 16" "64 24" "64 32" "96 16"; do
  set -- $spec
  d=instances/main/i$1_g$2
  [ -f $d/manifest.jsonl ] || python3 gen_xor.py --out $d --inputs $1 --gates-per-input $2 --seeds 10
done
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_main_machine.txt
python3 run_bench.py --manifest instances/main/*/manifest.jsonl --out results/xor_main.csv \
  --configs kissat cadical cms cms_gauss cms_gauss_xnat kissat_aig sby_yices ebmc_cadical \
  --solver-seeds 2 --timeout 180 --workers $WORKERS
{ date; uptime; } >> results_main_machine.txt
python3 summarize.py results/xor_main.csv --csv results/xor_main_summary.csv > results/xor_main_summary.txt
echo "summary written to results/xor_main_summary.txt"
