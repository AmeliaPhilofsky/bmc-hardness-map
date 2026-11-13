#!/usr/bin/env python3
"""Run every solver configuration on every instance and append results to a CSV.

  python3 run_bench.py --manifest instances/xor_mux/manifest.jsonl \
      --out results/xor_mux.csv --workers 16 --timeout 120 --solver-seeds 3

* Resumable: rows already in the CSV are skipped, so after a crash or a
  Ctrl-C just run the same command again.
* Each run gets its own process; wall time, CPU time (user+sys, including the
  tool's child processes) and peak memory come from the kernel (wait4).
* SAT answers from CNF solvers are verified by simulating the circuit on the
  returned input values. A wrong answer is marked verified=0 and printed.
* SBY and EBMC are slow to start, so by default they only run on circuit
  seeds < --tool-seeds (see CONFIGS).

Python 3.9+, standard library only.
"""

import argparse
import csv
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuits as C  # noqa: E402
import seq as Q  # noqa: E402

# name -> (input file, command template, kind)
#   {cnf} input path, {seed} solver seed, {dir} instance dir, {work} scratch dir
CONFIGS = {
    "kissat":          ("tseitin.cnf", "kissat --seed={seed} {cnf}", "cnf"),
    "cadical":         ("tseitin.cnf", "cadical --seed={seed} {cnf}", "cnf"),
    "cms":             ("tseitin.cnf", "cryptominisat5 --seed {seed} {cnf}", "cnf"),
    "cms_gauss":       ("tseitin.cnf", "cryptominisat5 --seed {seed} --autodisablegauss 0 {cnf}", "cnf"),
    "cms_gauss_xnat":  ("xnative.cnf", "cryptominisat5 --seed {seed} --autodisablegauss 0 {cnf}", "cnf"),
    "kissat_aig":      ("aig.cnf",     "kissat --seed={seed} {cnf}", "cnf"),
    "cadical_aig":     ("aig.cnf",     "cadical --seed={seed} {cnf}", "cnf"),
    "sby_yices":       ("top.v",       "sby -f top.sby", "sby"),
    "ebmc_cadical":    ("top.v",       "ebmc top.v --top top --bound {bound} --cadical", "ebmc"),
    "ebmc_minisat":    ("top.v",       "ebmc top.v --top top --bound {bound}", "ebmc"),
}
DEFAULT_CONFIGS = ["kissat", "cadical", "cms", "cms_gauss", "cms_gauss_xnat",
                   "kissat_aig", "sby_yices", "ebmc_cadical"]
SLOW_KINDS = ("sby", "ebmc")   # run once (one "seed"), on a subset of circuits

FIELDS = ["instance", "family", "nonlinear", "inputs", "gates", "regs", "bound", "observe",
          "xor_pct", "n_xor",
          "circuit_seed", "window", "depth", "true_depth", "xor_arity", "degenerate", "config", "solver_seed",
          "verdict", "expected", "verified", "timeout", "wall_s", "cpu_s",
          "peak_mem_mb", "conflicts", "decisions", "propagations", "restarts",
          "vars", "clauses", "xor_clauses", "returncode", "host", "date", "log"]

STAT_RE = re.compile(r"^c\s+(conflicts|decisions|propagations|restarts)\s*:\s*"
                     r"([0-9]+(?:\.[0-9]+)?)\s*([KMG])?\b", re.I)
MULT = {None: 1, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_cnf_output(text, n_in):
    verdict, model, stats = "UNKNOWN", {}, {}
    for line in text.splitlines():
        if line.startswith("s "):
            if "UNSAT" in line:
                verdict = "UNSAT"
            elif "SAT" in line:
                verdict = "SAT"
        elif line.startswith("v "):
            for tok in line[2:].split():
                lit = int(tok)
                if lit != 0 and abs(lit) <= n_in:
                    model[abs(lit)] = 1 if lit > 0 else 0
        else:
            m = STAT_RE.match(line)
            if m and m.group(1).lower() not in stats:
                key = m.group(1).lower()
                stats[key] = int(float(m.group(2)) * MULT[(m.group(3) or "").upper() or None])
    return verdict, model, stats


def parse_sby(text):
    if "DONE (PASS" in text:
        return "UNSAT"      # assertion y != target holds: no preimage
    if "DONE (FAIL" in text:
        return "SAT"
    return "UNKNOWN"


def parse_ebmc(text):
    if re.search(r"PROVED", text):
        return "UNSAT"
    if re.search(r"REFUTED", text):
        return "SAT"
    return "UNKNOWN"


def _limit_cpu(timeout):
    """Kernel-enforced CPU-time cap for the solver process and everything it starts.

    The harness also kills runs at the wall-clock timeout, but only while it is alive.
    If the harness dies (lost connection, Ctrl-C), this cap still stops each solver
    shortly after its timeout instead of leaving it running for hours.
    """
    import resource
    soft = int(timeout) + 30
    resource.setrlimit(resource.RLIMIT_CPU, (soft, soft + 30))


def run_one(cmd, cwd, timeout, log_path):
    """Run cmd, return (wall, cpu, maxrss_kb, returncode, timed_out)."""
    with open(log_path, "w") as logf:
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=logf,
                             stderr=subprocess.STDOUT, start_new_session=True,
                             preexec_fn=lambda: _limit_cpu(timeout))
        timed_out = [False]

        def kill():
            timed_out[0] = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            _, status, ru = os.wait4(p.pid, 0)
        finally:
            timer.cancel()
        wall = time.perf_counter() - t0
        p.returncode = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
        try:   # kill any stragglers left in the process group
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    return wall, ru.ru_utime + ru.ru_stime, ru.ru_maxrss, p.returncode, timed_out[0]


def verify(inst, model):
    if inst.get("family") == "seq":
        I, k = inst["inputs"], inst["bound"]
        if len(model) < I * k:
            return 0
        xs = [[model.get(t * I + i + 1, 0) for i in range(I)] for t in range(k)]
        return int(inst["_seq"].matches(xs, inst["targets"], inst["observe"]))
    if len(model) < inst["inputs"]:
        return 0
    x = [model.get(i + 1, 0) for i in range(inst["inputs"])]
    topo = inst["_topo"]
    vals = C.simulate(topo, inst["_types"], x)
    return int([vals[o] for o in topo.outputs] == inst["target"])


def load_instance(rec):
    full = json.load(open(os.path.join(rec["dir"], "inst.json")))
    if full.get("family") == "seq":
        c = Q.SeqCircuit(full["inputs"], full["regs"], full["gates"], full["outputs"],
                         full["window"], full["seed"], full["xor_pct"], full["nonlinear"],
                         topo_seed=full["topo_seed"], xor_arity=full.get("xor_arity", 2))
        if c.init != full["init"]:
            raise RuntimeError("rebuilt circuit differs from generated one: " + rec["name"])
        full["_seq"] = c
        return full
    topo = C.Topology(full["inputs"], full["gates"], full["outputs"], full["window"],
                      full["topo_seed"])
    nl = C.MUX if full.get("nonlinear", "and") == "mux" else C.AND
    full["_topo"] = topo
    xk = C.XOR3 if full.get("xor_arity", 2) == 3 else C.XOR
    full["_types"] = topo.gate_types(full["xor_pct"], nl, xk)
    return full


def task_key(inst_name, config, seed):
    return "%s|%s|%s" % (inst_name, config, seed)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, nargs="+")
    ap.add_argument("--out", required=True, help="results CSV (appended)")
    ap.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS,
                    help="choices: " + " ".join(CONFIGS))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--solver-seeds", type=int, default=3,
                    help="repetitions with different solver seeds (CNF solvers)")
    ap.add_argument("--tool-seeds", type=int, default=2,
                    help="SBY/EBMC only run on circuit seeds below this")
    ap.add_argument("--filter", default="", help="only instances whose name contains this")
    ap.add_argument("--logs", default="", help="keep raw logs here (default: next to CSV)")
    ap.add_argument("--dry-run", action="store_true", help="print the task count and exit")
    args = ap.parse_args()

    for c in args.configs:
        if c not in CONFIGS:
            sys.exit("unknown config %s" % c)
        exe = CONFIGS[c][1].split()[0]
        if shutil.which(exe) is None and not args.dry_run:
            sys.exit("%s not found on PATH (needed by config %s)" % (exe, c))

    recs = []
    for m in args.manifest:
        recs += [json.loads(l) for l in open(m) if l.strip()]
    if args.filter:
        recs = [r for r in recs if args.filter in r["name"]]

    done = set()
    if os.path.exists(args.out):
        with open(args.out) as f:
            for row in csv.DictReader(f):
                done.add(task_key(row["instance"], row["config"], row["solver_seed"]))

    tasks = []
    for r in recs:
        for c in args.configs:
            kind = CONFIGS[c][2]
            if kind in SLOW_KINDS:
                if r["seed"] >= args.tool_seeds:
                    continue
                seeds = [0]
            else:
                seeds = list(range(args.solver_seeds))
            for s in seeds:
                if task_key(r["name"], c, s) not in done:
                    tasks.append((r, c, s))
    random.Random(1).shuffle(tasks)   # spread configs/sizes evenly over the run
    worst_h = len(tasks) * args.timeout / max(1, args.workers) / 3600.0
    print("%d instances, %d tasks to run (%d already done), %d workers, timeout %ds"
          % (len(recs), len(tasks), len(done), args.workers, args.timeout))
    print("worst case (everything times out): %.1f h" % worst_h)
    if args.dry_run or not tasks:
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    log_dir = args.logs or os.path.join(os.path.dirname(os.path.abspath(args.out)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    new_file = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    fields = FIELDS
    if not new_file:   # appending: keep the column order already in the file
        with open(args.out) as f:
            fields = next(csv.reader(f))
    out_f = open(args.out, "a", newline="")
    writer = csv.DictWriter(out_f, fieldnames=fields, extrasaction="ignore", restval="")
    if new_file:
        writer.writeheader()
        out_f.flush()
    lock = threading.Lock()
    host = socket.gethostname()
    inst_cache, cache_lock = {}, threading.Lock()
    counts = {"done": 0, "wrong": 0, "timeout": 0}
    t_start = time.time()

    def work(task):
        r, c, s = task
        with cache_lock:
            if r["name"] not in inst_cache:
                inst_cache[r["name"]] = load_instance(r)
            inst = inst_cache[r["name"]]
        infile, tmpl, kind = CONFIGS[c]
        log_path = os.path.join(log_dir, "%s__%s__%d.log" % (r["name"], c, s))
        work_dir = tempfile.mkdtemp(prefix="bench_")
        try:
            if kind == "cnf":
                cmd = tmpl.format(cnf=os.path.join(r["dir"], infile), seed=s)
                cwd = work_dir
            else:
                for fn in ("top.v", "top.sby"):
                    shutil.copy(os.path.join(r["dir"], fn), work_dir)
                cmd, cwd = tmpl.format(bound=r.get("ebmc_bound", 1)), work_dir
            wall, cpu, rss, rc, to = run_one(cmd, cwd, args.timeout, log_path)
            text = open(log_path, errors="replace").read()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        stats, verified = {}, ""
        if kind == "cnf":
            verdict, model, stats = parse_cnf_output(text, r.get("model_vars", r["inputs"]))
            if verdict == "SAT":
                verified = verify(inst, model)
                if verified:   # keep disk use down: models are long and already checked
                    with open(log_path, "w") as f:
                        f.write("\n".join(l for l in text.splitlines() if not l.startswith("v ")))
        elif kind == "sby":
            verdict = parse_sby(text)
        else:
            verdict = parse_ebmc(text)
        if to:
            verdict = "TIMEOUT"
        size = r["cnf"].get(infile.replace(".cnf", ""), {}) if kind == "cnf" else {}
        row = {
            "instance": r["name"], "family": r["family"], "nonlinear": r.get("nonlinear", ""),
            "inputs": r["inputs"], "gates": r["gates"], "regs": r.get("regs", 0),
            "bound": r.get("bound", 1), "observe": r.get("observe", ""), "xor_pct": r["xor_pct"],
            "n_xor": r["n_xor"], "circuit_seed": r["seed"], "window": r.get("window", ""),
            "depth": r["depth"], "true_depth": r.get("true_depth", ""),
            "xor_arity": r.get("xor_arity", 2),
            "degenerate": int(r["degenerate"]), "config": c, "solver_seed": s,
            "verdict": verdict, "expected": r["expected"], "verified": verified,
            "timeout": int(to), "wall_s": "%.4f" % wall, "cpu_s": "%.4f" % cpu,
            "peak_mem_mb": "%.1f" % (rss / 1024.0),
            "conflicts": stats.get("conflicts", ""), "decisions": stats.get("decisions", ""),
            "propagations": stats.get("propagations", ""), "restarts": stats.get("restarts", ""),
            "vars": size.get("vars", ""), "clauses": size.get("clauses", ""),
            "xor_clauses": size.get("xor_clauses", ""), "returncode": rc,
            "host": host, "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "log": os.path.basename(log_path),
        }
        wrong = (verdict not in ("TIMEOUT", "UNKNOWN") and verdict != r["expected"]) or verified == 0
        with lock:
            writer.writerow(row)
            out_f.flush()
            counts["done"] += 1
            counts["timeout"] += int(to)
            if wrong:
                counts["wrong"] += 1
                print("\033[31mWRONG/UNVERIFIED: %s %s seed %d -> %s (log %s)\033[0m"
                      % (r["name"], c, s, verdict, log_path), flush=True)
            if verdict == "UNKNOWN":
                print("unknown result: %s %s (check %s)" % (r["name"], c, log_path), flush=True)
            n = counts["done"]
            if n % 50 == 0 or n == len(tasks):
                el = time.time() - t_start
                print("[%s] %d/%d done, %d timeouts, %d wrong, elapsed %.0f min, ~%.0f min left"
                      % (time.strftime("%H:%M"), n, len(tasks), counts["timeout"], counts["wrong"],
                         el / 60, el / n * (len(tasks) - n) / 60), flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for f in as_completed(futs):
            exc = f.exception()
            if exc:
                print("task error:", repr(exc), flush=True)
    out_f.close()
    print("finished: %d runs, %d timeouts, %d wrong" % (counts["done"], counts["timeout"], counts["wrong"]))


if __name__ == "__main__":
    main()
