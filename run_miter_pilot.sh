#!/bin/bash
# UNSAT pilot: equivalence miters on a slice of the width x XOR grid, with DRAT proof checks.
# Run this first to see solve times, proof sizes and proof-check times before the full grid.
# 2 circuits x 7 windows x 4 XOR levels = 56 miters, 3 solvers, 1 solver seed (worst case ~45 min).
# Usage (bash, inside tmux):  bash run_miter_pilot.sh
# Proofs are written to $PROOF_TMP (default /tmp) and deleted after checking:
# check free space first with  df -h /tmp ~
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
PROOF_TMP=${PROOF_TMP:-/tmp}
d=instances/miter_pilot
[ -f $d/manifest.jsonl ] || python3 gen_miter.py --out $d --inputs 64 --gates-per-input 24 \
  --windows 64 96 128 160 192 224 256 --xor-pcts 0 50 90 100 --seeds 2
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_miter_pilot_machine.txt
python3 run_bench.py --manifest $d/manifest.jsonl --out results/miter_pilot.csv \
  --configs kissat cadical cms_gauss_xnat --solver-seeds 1 --timeout 300 --workers $WORKERS \
  --proof --proof-timeout 600 --tmp "$PROOF_TMP"
{ date; uptime; } >> results_miter_pilot_machine.txt
python3 summarize.py results/miter_pilot.csv > results/miter_pilot_summary.txt
python3 - <<'PY'
import csv
rows = [r for r in csv.DictReader(open("results/miter_pilot.csv")) if r.get("proof")]
if rows:
    mb = sorted(float(r["proof_mb"] or 0) for r in rows)
    ps = sorted(float(r["proof_s"] or 0) for r in rows)
    print("proofs: %d checked, %d VERIFIED, %d FAILED, %d UNCHECKED"
          % (len(rows), sum(r["proof"] == "VERIFIED" for r in rows),
             sum(r["proof"] == "FAILED" for r in rows), sum(r["proof"] == "UNCHECKED" for r in rows)))
    print("proof size MB: median %.1f, max %.1f;  check time s: median %.1f, max %.1f"
          % (mb[len(mb) // 2], mb[-1], ps[len(ps) // 2], ps[-1]))
PY
echo "summary written to results/miter_pilot_summary.txt"
