#!/usr/bin/env python3
"""Summarize a results CSV: median time / conflicts and timeout share per point.

  python3 summarize.py results/xor_mux.csv                 # all configs
  python3 summarize.py results/pilot.csv --config kissat   # one config
  python3 summarize.py results/xor_mux.csv --csv summary.csv

Rows marked degenerate are excluded unless --keep-degenerate is given.
Timeouts count as the timeout value in medians (so a median equal to the
timeout means at least half the runs timed out).
"""

import argparse
import collections
import csv
import statistics


def med(xs):
    return statistics.median(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csvfile", nargs="+")
    ap.add_argument("--config", nargs="*", default=None)
    ap.add_argument("--keep-degenerate", action="store_true")
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    rows = []
    for fn in args.csvfile:
        rows += list(csv.DictReader(open(fn)))
    wrong = [r for r in rows if r["verified"] == "0" or
             (r["verdict"] not in ("TIMEOUT", "UNKNOWN") and r["verdict"] != r["expected"])]
    if not args.keep_degenerate:
        rows = [r for r in rows if r["degenerate"] != "1"]
    if args.config:
        rows = [r for r in rows if r["config"] in args.config]

    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["config"], r["nonlinear"], int(r["inputs"]), int(r["gates"]), int(r.get("window") or 0),
                 int(r.get("regs") or 0), int(r.get("bound") or 1), int(r["xor_pct"]))].append(r)

    out = []
    hdr = "%-15s %-4s %4s %6s %5s %4s %3s %5s %5s %6s %10s %12s %8s %7s" % (
        "config", "nl", "in", "gates", "win", "regs", "k", "xor%", "runs", "depth", "med_wall_s", "med_confl*", "timeout%", "unknown")
    print(hdr)
    print("-" * len(hdr))
    last = None
    for key in sorted(groups):
        g = groups[key]
        walls = [float(r["wall_s"]) for r in g]
        confl = [int(r["conflicts"]) for r in g if r["conflicts"] not in ("", None)]
        to = sum(int(r["timeout"]) for r in g)
        unk = sum(1 for r in g if r["verdict"] == "UNKNOWN")
        if last and key[:7] != last:
            print()
        last = key[:7]
        depth = med([int(r["depth"]) for r in g])
        print("%-15s %-4s %4d %6d %5d %4d %3d %5d %5d %6d %10.3f %12s %7.0f%% %7d" % (
            key[0], key[1], key[2], key[3], key[4], key[5], key[6], key[7], len(g), depth, med(walls),
            ("%d" % med(confl)) if confl else "-", 100.0 * to / len(g), unk))
        out.append({"config": key[0], "nonlinear": key[1], "inputs": key[2], "gates": key[3],
                    "window": key[4], "regs": key[5], "bound": key[6], "xor_pct": key[7],
                    "median_depth": depth,
                    "runs": len(g), "median_wall_s": med(walls),
                    "median_conflicts": med(confl) if confl else "",
                    "timeout_frac": to / float(len(g)), "unknown": unk})
    print("\n* med_confl covers finished runs only (timed-out runs print no statistics)")
    print("wrong or unverified answers: %d" % len(wrong))
    for r in wrong[:20]:
        print("  ", r["instance"], r["config"], r["solver_seed"], r["verdict"], r["log"])
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)


if __name__ == "__main__":
    main()
