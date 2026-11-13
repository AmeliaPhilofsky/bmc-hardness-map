#!/usr/bin/env python3
"""Generate the combinational -> sequential study (see seq.py for the model).

Two sweeps come out of the same generator:
  registers sweep : fixed bound, vary --regs (0 = fully combinational)
  bound sweep     : fixed registers, vary --bounds (same circuit, unrolled more)

Example (registers sweep):
  python3 gen_seq.py --out instances/seq_regs --inputs 16 --gates 256 \
      --regs 0 4 8 16 32 --bounds 8 --xor-pcts 0 50 --seeds 10

Per instance: tseitin.cnf aig.cnf xnative.cnf top.v top.sby inst.json,
plus one line in <out>/manifest.jsonl.
"""

import argparse
import json
import os

import circuits as C
import seq as Q


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inputs", type=int, default=16, help="primary inputs per step")
    ap.add_argument("--outputs", type=int, default=0, help="observed outputs per step (0 = inputs)")
    ap.add_argument("--gates", type=int, default=256, help="gates per step (fixed across --regs)")
    ap.add_argument("--regs", type=int, nargs="+", default=[0, 4, 8, 16, 32])
    ap.add_argument("--bounds", type=int, nargs="+", default=[8], help="BMC bound k (time steps)")
    ap.add_argument("--window", type=int, default=0, help="fan-in window (0 = 2*inputs)")
    ap.add_argument("--xor-pcts", type=int, nargs="+", default=[0, 50])
    ap.add_argument("--observe", choices=["all", "last"], default="all",
                    help="outputs must match at every step (all) or only the last (last)")
    ap.add_argument("--nonlinear", choices=["mux", "and"], default="mux")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--xor-arity", type=int, choices=[2, 3], default=3,
                    help="3: XOR gates read the same three wires as the MUX (default); 2: 2-input XOR")
    ap.add_argument("--min-support", type=float, default=0.5)
    args = ap.parse_args()

    I = args.inputs
    O = args.outputs or I
    W = args.window or 2 * I
    os.makedirs(args.out, exist_ok=True)
    n_written = flagged = weak = 0
    with open(os.path.join(args.out, "manifest.jsonl"), "w") as man:
        for R in args.regs:
            for seed in range(args.seeds):
                try:
                    base = Q.SeqCircuit(I, R, args.gates, O, W, seed, 0, args.nonlinear,
                                        args.min_support)
                except RuntimeError as e:
                    print("WARNING: skipped regs=%d seed=%d: %s" % (R, seed, e))
                    continue
                for pct in args.xor_pcts:
                    c = Q.SeqCircuit(I, R, args.gates, O, W, seed, pct, args.nonlinear,
                                     topo_seed=base.topo_seed, xor_arity=args.xor_arity)
                    feats = c.features()
                    for k in args.bounds:
                        xs = c.planted_inputs(k)
                        tg = c.targets(xs, args.observe)
                        name = "seq%s%s_i%d_n%d_r%d_k%d_w%d_s%d_p%03d" % (
                            args.observe, "x3" if args.xor_arity == 3 else "", I, args.gates, R, k, W,
                            seed, pct)
                        d = os.path.join(args.out, name)
                        os.makedirs(d, exist_ok=True)
                        sizes = {}
                        for enc in ("tseitin", "aig", "xnative"):
                            nv, cls, xcls = c.cnf(k, tg, args.observe, enc)
                            C.write_dimacs(os.path.join(d, enc + ".cnf"), nv, cls, xcls,
                                           comments=[name, "encoding " + enc,
                                                     "planted trace (SAT by construction)",
                                                     "vars 1..%d = inputs, step t bit i -> t*%d+i+1"
                                                     % (k * I, I)])
                            sizes[enc] = {"vars": nv, "clauses": len(cls), "xor_clauses": len(xcls)}
                        with open(os.path.join(d, "top.v"), "w") as f:
                            f.write(c.verilog(k, tg, args.observe))
                        with open(os.path.join(d, "top.sby"), "w") as f:
                            f.write(Q.SBY_SEQ_TEMPLATE % k)
                        degenerate = feats["const_outputs"] > 0 or feats["dup_outputs"] > 0
                        state_weak = R > 0 and feats["obs_state_support"] < 0.25
                        flagged += int(degenerate)
                        weak += int(state_weak)
                        rec = {
                            "name": name, "dir": os.path.abspath(d), "family": "seq",
                            "nonlinear": args.nonlinear, "xor_arity": args.xor_arity,
                            "true_depth": c.topo.true_depth(c.types), "edges": c.topo.n_edges(c.types),
                            "property": "planted_trace",
                            "observe": args.observe, "expected": "SAT",
                            "inputs": I, "outputs": O, "gates": args.gates, "window": W,
                            "seed": seed, "topo_seed": c.topo_seed, "xor_pct": pct,
                            "n_xor": sum(1 for t in c.types if t == C.XOR),
                            "regs": R, "bound": k, "model_vars": k * I, "ebmc_bound": k - 1,
                            "depth": feats["depth"],
                            "min_output_support": feats["min_output_support"],
                            "obs_state_support": feats["obs_state_support"],
                            "next_input_support": feats["next_input_support"],
                            "const_outputs": feats["const_outputs"],
                            "dup_outputs": feats["dup_outputs"],
                            "mean_output_bias": feats["mean_bias"],
                            "degenerate": degenerate, "state_weak": state_weak,
                            "cnf": sizes,
                        }
                        with open(os.path.join(d, "inst.json"), "w") as f:
                            json.dump(dict(rec, init=c.init, x_star=xs, targets=tg), f)
                        man.write(json.dumps(rec) + "\n")
                        n_written += 1
    print("wrote %d instances to %s (%d degenerate, %d with weak state influence)"
          % (n_written, os.path.join(args.out, "manifest.jsonl"), flagged, weak))


if __name__ == "__main__":
    main()
