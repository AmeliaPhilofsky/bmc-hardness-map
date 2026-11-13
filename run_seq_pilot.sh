#!/bin/bash
# Sequential pilot: finds sizes for the overnight registers/bound sweeps.
# 16 inputs per step, 256 or 512 gates per step, 0-32 registers, bound 4 or 8.
# Usage (from ~/bmc-baselines/bmc-harness, inside tmux):  bash run_seq_pilot.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-6}
for n in 256 512; do
  d=instances/seq_pilot/n$n
  [ -f $d/manifest.jsonl ] || python3 gen_seq.py --out $d --inputs 16 --gates $n \
      --regs 0 8 16 32 --bounds 4 8 --xor-pcts 0 50 --seeds 2
done
python3 run_bench.py --manifest instances/seq_pilot/*/manifest.jsonl --out results/seq_pilot.csv \
  --configs kissat cms_gauss_xnat sby_yices ebmc_cadical --solver-seeds 1 --timeout 120 --workers $WORKERS
python3 summarize.py results/seq_pilot.csv > results/seq_pilot_summary.txt
echo "summary written to results/seq_pilot_summary.txt"
