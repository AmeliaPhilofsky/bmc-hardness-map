#!/usr/bin/env python3
"""Generate the XOR/AND-mix study: fixed circuit size, varying XOR percentage.

Property ("planted preimage"): for a random input x*, target = C(x*).
Question for every tool: is there an input x with C(x) = target?
The answer is SAT by construction, and any model a solver returns is checked
by simulation, so a wrong answer can't slip into the data.

Example:
  python3 gen_xor.py --out instances/xor --inputs 24 32 40 --gates-per-input 8 \
      --xor-pcts 0 10 20 30 40 50 60 70 80 90 100 --seeds 10

For every (size, seed, xor%) it writes
  <out>/<name>/tseitin.cnf  aig.cnf  xnative.cnf  top.v  top.sby  inst.json
and appends one line per instance to <out>/manifest.jsonl.
"""

import argparse
import json
import os
import random

import circuits as C


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inputs", type=int, nargs="+", default=[24, 32, 40],
                    help="input counts (outputs = inputs)")
    ap.add_argument("--gates-per-input", type=int, default=8,
                    help="gates N = this * inputs (fixed size per input count)")
    ap.add_argument("--window", type=int, default=0,
                    help="fan-in window (controls depth); 0 = 2*inputs")
    ap.add_argument("--xor-pcts", type=int, nargs="+",
                    default=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--nonlinear", choices=["mux", "and"], default="mux",
                    help="the non-XOR gate: mux (balanced s?a:b, default) or and (AND/OR family)")
    ap.add_argument("--xor-arity", type=int, choices=[2, 3], default=3,
                    help="3 (default): XOR gates read the same three wires as the MUX they replace, "
                         "so wiring and depth are identical at every XOR level; 2: original 2-input XOR")
    ap.add_argument("--min-support", type=float, default=0.5,
                    help="every output must structurally depend on >= this fraction of inputs")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    man_path = os.path.join(args.out, "manifest.jsonl")
    n_written = 0
    flagged = 0
    with open(man_path, "w") as man:
        for n_in in args.inputs:
            n_gates = args.gates_per_input * n_in
            window = args.window or 2 * n_in
            for seed in range(args.seeds):
                try:
                    topo = C.make_topology(n_in, n_gates, n_in, window, seed, args.min_support)
                except RuntimeError as e:
                    print("WARNING: skipped inputs=%d gates=%d seed=%d: %s" % (n_in, n_gates, seed, e))
                    continue
                _, min_sup = topo.support_ok(args.min_support)
                depth = topo.depth()
                rng = random.Random("plant-%d-%d" % (n_in, seed))
                x_star = [rng.randrange(2) for _ in range(n_in)]
                for pct in args.xor_pcts:
                    xk = C.XOR3 if args.xor_arity == 3 else C.XOR
                    types = topo.gate_types(pct, C.MUX if args.nonlinear == "mux" else C.AND, xk)
                    vals = C.simulate(topo, types, x_star)
                    target = [vals[o] for o in topo.outputs]
                    func = C.functional_features(topo, types, seed=seed)
                    tag = args.nonlinear + ("3" if args.xor_arity == 3 else "")
                    name = "xor%s_i%d_n%d_w%d_s%d_p%03d" % (tag, n_in, n_gates, window, seed, pct)
                    d = os.path.join(args.out, name)
                    os.makedirs(d, exist_ok=True)
                    sizes = {}
                    for enc in ("tseitin", "aig", "xnative"):
                        nv, cls, xcls = C.cnf_clauses(topo, types, target, enc)
                        C.write_dimacs(os.path.join(d, enc + ".cnf"), nv, cls, xcls,
                                       comments=[name, "encoding " + enc,
                                                 "planted preimage (SAT by construction)"])
                        sizes[enc] = {"vars": nv, "clauses": len(cls), "xor_clauses": len(xcls)}
                    with open(os.path.join(d, "top.v"), "w") as f:
                        f.write(C.verilog(topo, types, target))
                    with open(os.path.join(d, "top.sby"), "w") as f:
                        f.write(C.SBY_TEMPLATE)
                    n_xor = sum(1 for t in types if t == C.XOR)
                    degenerate = func["const_outputs"] > 0 or func["dup_outputs"] > 0
                    flagged += int(degenerate)
                    rec = {
                        "name": name, "dir": os.path.abspath(d), "family": "xor_mix",
                        "nonlinear": args.nonlinear, "xor_arity": args.xor_arity,
                        "true_depth": topo.true_depth(types), "edges": topo.n_edges(types),
                        "property": "planted_preimage", "expected": "SAT",
                        "inputs": n_in, "outputs": n_in, "gates": n_gates,
                        "window": window, "seed": seed, "topo_seed": topo.seed,
                        "xor_pct": pct, "n_xor": n_xor,
                        "depth": depth, "min_output_support": min_sup,
                        "const_outputs": func["const_outputs"],
                        "dup_outputs": func["dup_outputs"],
                        "mean_output_bias": func["mean_bias"],
                        "degenerate": degenerate,
                        "cnf": sizes,
                    }
                    with open(os.path.join(d, "inst.json"), "w") as f:
                        json.dump(dict(rec, x_star=x_star, target=target), f)
                    man.write(json.dumps(rec) + "\n")
                    n_written += 1
    print("wrote %d instances to %s (%d flagged degenerate: constant or duplicate outputs)"
          % (n_written, man_path, flagged))


if __name__ == "__main__":
    main()
