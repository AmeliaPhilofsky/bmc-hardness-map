#!/bin/bash
# UNSAT pilot, harder miters: each XOR region of the copy is rebuilt from its parities
# (gen_miter.py --restructure linear), so inner XOR signals have no equivalent partner
# for the solver to merge. Same circuits as the width grid; 0% XOR is left out because
# there the copy equals the local one. 3 windows x 5 XOR levels x 2 circuits = 30 miters,
# 3 solvers, 1 solver seed (worst case ~25 min), then the DRAT proof pass.
# Each miter records gold_matched (share of original gates that still have a partner)
# and its size, so "harder because bigger" can be told apart from "harder because unshared".
# Usage (bash, inside tmux):  bash run_miter_linear_pilot.sh
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
PROOF_TMP=${PROOF_TMP:-/tmp}
PROOF_WORKERS=${PROOF_WORKERS:-4}
d=instances/miter_linear_pilot
[ -f $d/manifest.jsonl ] || python3 gen_miter.py --out $d --inputs 64 --gates-per-input 24 \
  --windows 64 160 256 --xor-pcts 50 70 80 90 100 --seeds 2 --restructure linear
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_miter_linear_pilot_machine.txt
python3 run_bench.py --manifest $d/manifest.jsonl --out results/miter_linear_pilot.csv \
  --configs kissat cadical cms_gauss_xnat --solver-seeds 1 --timeout 300 --workers $WORKERS
python3 check_proofs.py --results results/miter_linear_pilot.csv --manifest $d/manifest.jsonl \
  --out results/miter_linear_pilot_proofs.csv --workers $PROOF_WORKERS --tmp "$PROOF_TMP" \
  --min-free-gb 3 --max-proof-gb 2
{ date; uptime; } >> results_miter_linear_pilot_machine.txt
python3 summarize.py results/miter_linear_pilot.csv > results/miter_linear_pilot_summary.txt
echo "summary written to results/miter_linear_pilot_summary.txt"
