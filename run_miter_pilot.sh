#!/bin/bash
# UNSAT pilot: equivalence miters on a slice of the width x XOR grid, with DRAT proof checks.
# Run this first to see solve times, proof sizes and proof-check times before the full grid.
# 2 circuits x 7 windows x 4 XOR levels = 56 miters, 3 solvers, 1 solver seed (worst case ~45 min, then the proof pass).
# Usage (bash, inside tmux):  bash run_miter_pilot.sh
# Timed runs write no proofs. Afterwards check_proofs.py re-solves the UNSAT answers with
# $PROOF_WORKERS workers (default 4), writing proofs to $PROOF_TMP (default /tmp) and
# deleting each after checking; it pauses while less than 3 GB is free there.
set -e
cd "$(dirname "$0")"
WORKERS=${WORKERS:-20}
PROOF_TMP=${PROOF_TMP:-/tmp}
PROOF_WORKERS=${PROOF_WORKERS:-4}
d=instances/miter_pilot
[ -f $d/manifest.jsonl ] || python3 gen_miter.py --out $d --inputs 64 --gates-per-input 24 \
  --windows 64 96 128 160 192 224 256 --xor-pcts 0 50 90 100 --seeds 2
{ date; hostname; uptime; lscpu | grep "Model name"; } > results_miter_pilot_machine.txt
python3 run_bench.py --manifest $d/manifest.jsonl --out results/miter_pilot.csv \
  --configs kissat cadical cms_gauss_xnat --solver-seeds 1 --timeout 300 --workers $WORKERS
# second pass: re-solve UNSAT answers with a DRAT proof and check it (few workers, disk guarded)
python3 check_proofs.py --results results/miter_pilot.csv --manifest $d/manifest.jsonl --out results/miter_pilot_proofs.csv \
  --workers $PROOF_WORKERS --tmp "$PROOF_TMP" --min-free-gb 3 --max-proof-gb 2
{ date; uptime; } >> results_miter_pilot_machine.txt
python3 summarize.py results/miter_pilot.csv > results/miter_pilot_summary.txt
python3 - <<'PY'
import csv, collections, os
f = "results/miter_pilot_proofs.csv"
rows = list(csv.DictReader(open(f))) if os.path.exists(f) else []
c = collections.Counter(r["proof"] for r in rows)
print("proofs:", ", ".join("%s %d" % kv for kv in sorted(c.items())))
mb = sorted(float(r["proof_mb"] or 0) for r in rows)
ps = sorted(float(r["proof_s"] or 0) for r in rows if r["proof_s"])
if mb:
    print("proof size MB: median %.1f, max %.1f" % (mb[len(mb) // 2], mb[-1]))
if ps:
    print("check time s: median %.1f, max %.1f" % (ps[len(ps) // 2], ps[-1]))
PY
echo "summary written to results/miter_pilot_summary.txt"
