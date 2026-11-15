#!/usr/bin/env python3
"""Check UNSAT answers with DRAT proofs, as a separate pass after the timed runs.

The timed runs (run_bench.py) write no proofs, so their times compare directly
with the SAT studies and nothing large is written while 20 solvers run at once.
This script then re-solves UNSAT answers of Kissat/CaDiCaL with a proof file,
checks each proof with drat-trim, deletes it, and appends one row per check:

  python3 check_proofs.py --results results/miter_pilot.csv \
      --manifest instances/miter_pilot/manifest.jsonl --out results/miter_pilot_proofs.csv \
      --workers 4 --tmp /tmp

* Only rows with verdict UNSAT from kissat / cadical / kissat_aig / cadical_aig, solver seed 0
  (change with --seeds), are checked.
* Before each run it waits while the scratch folder has less than --min-free-gb free,
  so a shared /tmp is never filled; a proof that grows past --max-proof-gb is stopped
  and recorded as SKIPPED_SIZE.
* Resumable like run_bench.py: rows already in --out are skipped.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_bench import CONFIGS, run_one, parse_cnf_output, parse_drat  # noqa: E402

PROOF_CONFIGS = ("kissat", "cadical", "kissat_aig", "cadical_aig")
FIELDS = ["instance", "config", "solver_seed", "resolve_verdict", "resolve_wall_s",
          "proof", "proof_mb", "proof_s", "host", "date"]


def free_gb(path):
    return shutil.disk_usage(path).free / 1e9


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, help="CSV from run_bench.py")
    ap.add_argument("--manifest", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--timeout", type=float, default=600, help="re-solve time limit (s)")
    ap.add_argument("--proof-timeout", type=float, default=1800, help="drat-trim time limit (s)")
    ap.add_argument("--max-solve-s", type=float, default=0,
                    help="skip runs whose timed solve took longer than this (0 = check all)")
    ap.add_argument("--tmp", default=tempfile.gettempdir(), help="where proofs are written")
    ap.add_argument("--min-free-gb", type=float, default=3.0)
    ap.add_argument("--max-proof-gb", type=float, default=2.0)
    args = ap.parse_args()

    for exe in ("drat-trim",):
        if shutil.which(exe) is None:
            sys.exit("%s not found on PATH" % exe)
    dirs = {}
    for m in args.manifest:
        for l in open(m):
            if l.strip():
                r = json.loads(l)
                dirs[r["name"]] = r["dir"]

    done = set()
    if os.path.exists(args.out):
        for row in csv.DictReader(open(args.out)):
            done.add((row["instance"], row["config"], row["solver_seed"]))
    todo = []
    for row in csv.DictReader(open(args.results)):
        if (row["verdict"] == "UNSAT" and row["config"] in PROOF_CONFIGS
                and int(row["solver_seed"]) in args.seeds
                and (not args.max_solve_s or float(row["wall_s"]) <= args.max_solve_s)
                and (row["instance"], row["config"], row["solver_seed"]) not in done
                and row["instance"] in dirs):
            todo.append(row)
    todo.sort(key=lambda r: float(r["wall_s"]))     # quick ones first
    print("%d UNSAT answers to check (%d already done), %d workers, proofs in %s (%.1f GB free)"
          % (len(todo), len(done), args.workers, args.tmp, free_gb(args.tmp)), flush=True)
    if not todo:
        return

    new = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    out_f = open(args.out, "a", newline="")
    w = csv.DictWriter(out_f, fieldnames=FIELDS)
    if new:
        w.writeheader()
    lock = threading.Lock()
    space = threading.Lock()
    log_dir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "proof_logs")
    os.makedirs(log_dir, exist_ok=True)
    counts = {"n": 0, "VERIFIED": 0, "FAILED": 0}
    host = os.uname()[1]
    max_bytes = int(args.max_proof_gb * 1e9)

    def work(row):
        infile, tmpl, _ = CONFIGS[row["config"]]
        cnf = os.path.join(dirs[row["instance"]], infile)
        with space:                        # wait for room before starting a proof
            while free_gb(args.tmp) < args.min_free_gb:
                time.sleep(30)
        wd = tempfile.mkdtemp(prefix="proof_", dir=args.tmp)
        pf = os.path.join(wd, "proof.drat")
        base = os.path.join(log_dir, "%s__%s__%s" % (row["instance"], row["config"], row["solver_seed"]))
        proof, mb, ps, rv, rw = "", "", "", "", ""
        try:
            stop = threading.Event()

            def watch():                   # stop a proof that grows too large
                while not stop.wait(5):
                    if os.path.exists(pf) and os.path.getsize(pf) > max_bytes:
                        subprocess.call("pkill -f %s" % pf, shell=True)
                        return
            t = threading.Thread(target=watch, daemon=True)
            t.start()
            cmd = tmpl.format(cnf=cnf, seed=row["solver_seed"]) + " " + pf
            rw, _, _, _, to = run_one(cmd, wd, args.timeout, base + ".solve.log")
            stop.set()
            rv = parse_cnf_output(open(base + ".solve.log", errors="replace").read(), 0)[0]
            size = os.path.getsize(pf) if os.path.exists(pf) else 0
            mb = "%.1f" % (size / 1048576.0)
            if size > max_bytes:
                proof = "SKIPPED_SIZE"
            elif to or rv != "UNSAT":
                proof = "SKIPPED_RESOLVE"
            else:
                ps, _, _, _, pto = run_one("drat-trim %s %s -t %d" % (cnf, pf, int(args.proof_timeout)),
                                           wd, args.proof_timeout + 30, base + ".drat.log")
                proof = "UNCHECKED" if pto else parse_drat(open(base + ".drat.log", errors="replace").read())
                ps = "%.2f" % ps
            rw = "%.3f" % rw
        finally:
            shutil.rmtree(wd, ignore_errors=True)
        with lock:
            w.writerow({"instance": row["instance"], "config": row["config"],
                        "solver_seed": row["solver_seed"], "resolve_verdict": rv,
                        "resolve_wall_s": rw, "proof": proof, "proof_mb": mb, "proof_s": ps,
                        "host": host, "date": time.strftime("%Y-%m-%d %H:%M:%S")})
            out_f.flush()
            counts["n"] += 1
            counts[proof] = counts.get(proof, 0) + 1
            if proof == "FAILED":
                print("\033[31mPROOF FAILED: %s %s (logs %s.*)\033[0m" % (row["instance"], row["config"], base),
                      flush=True)
            if counts["n"] % 20 == 0 or counts["n"] == len(todo):
                print("[%s] %d/%d checked: %s" % (time.strftime("%H:%M"), counts["n"], len(todo),
                      ", ".join("%s %d" % (k, v) for k, v in counts.items() if k != "n" and v)), flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(work, r) for r in todo]):
            if f.exception():
                print("task error:", repr(f.exception()), flush=True)
    out_f.close()
    print("finished:", ", ".join("%s %d" % (k, v) for k, v in counts.items()))


if __name__ == "__main__":
    main()
