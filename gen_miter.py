#!/usr/bin/env python3
"""Generate equivalence miters: UNSAT instances on the same circuits as the SAT studies.

Property ("equivalence miter"): the circuit C (built exactly as in gen_xor.py,
same seeds, same wiring, same XOR sets) is compared with a restructured copy C'
that computes the same function from different gates (see miter.py).
Question for every tool: is there an input x with C(x) != C'(x)?
The answer is UNSAT by construction. UNSAT answers can be checked with DRAT
proofs (run_bench.py --proof); a SAT answer is checked by simulating both
circuits on the returned input, so a generator bug would show up at once.

Example (the width x XOR grid, unsatisfiable version):
  python3 gen_miter.py --out instances/miter_grid --inputs 64 --gates-per-input 24 \
      --windows 64 96 128 160 192 224 256 --xor-pcts 0 50 70 80 90 100 --seeds 10

For every (size, window, seed, xor%) it writes
  <out>/<name>/tseitin.cnf  aig.cnf  xnative.cnf  top.v  top.sby  inst.json
and appends one line per instance to <out>/manifest.jsonl.
"""

import argparse
import json
import os

import circuits as C
import miter as M


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inputs", type=int, nargs="+", default=[64])
    ap.add_argument("--gates-per-input", type=int, default=24)
    ap.add_argument("--windows", type=int, nargs="+", default=[0],
                    help="fan-in windows (0 = 2*inputs)")
    ap.add_argument("--xor-pcts", type=int, nargs="+", default=[0, 50, 70, 80, 90, 100])
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--nonlinear", choices=["mux", "and"], default="mux")
    ap.add_argument("--xor-arity", type=int, choices=[2, 3], default=3)
    ap.add_argument("--min-support", type=float, default=0.5)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    man_path = os.path.join(args.out, "manifest.jsonl")
    n_written = flagged = 0
    nl = C.MUX if args.nonlinear == "mux" else C.AND
    xk = C.XOR3 if args.xor_arity == 3 else C.XOR
    with open(man_path, "w") as man:
        for n_in in args.inputs:
            n_gates = args.gates_per_input * n_in
            for w in args.windows:
                window = w or 2 * n_in
                for seed in range(args.seeds):
                    try:
                        topo = C.make_topology(n_in, n_gates, n_in, window, seed, args.min_support)
                    except RuntimeError as e:
                        print("WARNING: skipped inputs=%d window=%d seed=%d: %s" % (n_in, window, seed, e))
                        continue
                    _, min_sup = topo.support_ok(args.min_support)
                    for pct in args.xor_pcts:
                        types = topo.gate_types(pct, nl, xk)
                        copy = M.Copy(topo, types, topo.seed)
                        func = C.functional_features(topo, types, seed=seed)
                        tag = args.nonlinear + ("3" if args.xor_arity == 3 else "")
                        name = "mit%s_i%d_n%d_w%d_s%d_p%03d" % (tag, n_in, n_gates, window, seed, pct)
                        d = os.path.join(args.out, name)
                        os.makedirs(d, exist_ok=True)
                        sizes = {}
                        for enc in ("tseitin", "aig", "xnative"):
                            nv, cls, xcls = M.miter_cnf(topo, types, copy, enc)
                            C.write_dimacs(os.path.join(d, enc + ".cnf"), nv, cls, xcls,
                                           comments=[name, "encoding " + enc,
                                                     "equivalence miter (UNSAT by construction)",
                                                     "vars 1..%d = inputs" % n_in])
                            sizes[enc] = {"vars": nv, "clauses": len(cls), "xor_clauses": len(xcls)}
                        with open(os.path.join(d, "top.v"), "w") as f:
                            f.write(M.miter_verilog(topo, types, copy))
                        with open(os.path.join(d, "top.sby"), "w") as f:
                            f.write(C.SBY_TEMPLATE)
                        degenerate = func["const_outputs"] > 0 or func["dup_outputs"] > 0
                        flagged += int(degenerate)
                        rec = {
                            "name": name, "dir": os.path.abspath(d), "family": "miter",
                            "nonlinear": args.nonlinear, "xor_arity": args.xor_arity,
                            "true_depth": topo.true_depth(types), "edges": topo.n_edges(types),
                            "property": "equivalence_miter", "expected": "UNSAT",
                            "inputs": n_in, "outputs": n_in, "gates": n_gates,
                            "copy_nodes": len(copy.nodes),
                            "window": window, "seed": seed, "topo_seed": topo.seed,
                            "xor_pct": pct,
                            "n_xor": sum(1 for t in types if t in (C.XOR, C.XOR3)),
                            "depth": topo.depth(), "min_output_support": min_sup,
                            "const_outputs": func["const_outputs"],
                            "dup_outputs": func["dup_outputs"],
                            "mean_output_bias": func["mean_bias"],
                            "degenerate": degenerate,
                            "cnf": sizes,
                        }
                        with open(os.path.join(d, "inst.json"), "w") as f:
                            json.dump(rec, f)
                        man.write(json.dumps(rec) + "\n")
                        n_written += 1
    print("wrote %d instances to %s (%d flagged degenerate)" % (n_written, man_path, flagged))


if __name__ == "__main__":
    main()
