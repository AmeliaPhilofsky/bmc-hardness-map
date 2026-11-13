#!/bin/bash
# Depth study: 64 inputs, 1536 gates, XOR 0/50/100%, fan-in window 64..512 (depth ~90 down to ~20).
# Usage (from ~/bmc-baselines/bmc-harness, inside tmux):  bash run_depth.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-10}
for w in 64 128 256 512; do
  d=instances/depth/w$w
  [ -f $d/manifest.jsonl ] || python3 gen_xor.py --out $d --inputs 64 --gates-per-input 24 --window $w --xor-pcts 0 50 100 --seeds 10
done
python3 run_bench.py --manifest instances/depth/*/manifest.jsonl --out results/depth.csv \
  --configs kissat cadical cms cms_gauss_xnat --solver-seeds 2 --timeout 180 --workers $WORKERS
python3 summarize.py results/depth.csv --csv results/depth_summary.csv > results/depth_summary.txt
echo "summary written to results/depth_summary.txt"
