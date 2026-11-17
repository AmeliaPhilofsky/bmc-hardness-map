#!/usr/bin/env python3
"""Exhaustive checks of the equivalence miters on small circuits.

For every encoding and every input assignment:
  * the copy C' gives the same outputs as the original C (simulation);
  * fixing the inputs and running unit propagation on the miter CNF ends in a
    conflict, so the CNF has no model at all (Tseitin definitions propagate
    fully from the inputs, so this is a complete UNSAT proof for small n);
  * after breaking one node of the copy, some input propagates to a full
    model without conflict, so the miter really detects a difference.
Also checks the Verilog writer covers every node.

  python3 test_miter.py
"""

import itertools
import random

import circuits as C
import miter as M


def propagate(nv, cls, xcls, assign):
    """Unit propagation over OR-clauses and XOR-clauses. Returns (ok, assign)."""
    a = dict(assign)
    changed = True
    while changed:
        changed = False
        for c in cls:
            free, sat = [], False
            for l in c:
                v = a.get(abs(l))
                if v is None:
                    free.append(l)
                elif (v == 1) == (l > 0):
                    sat = True
                    break
            if sat:
                continue
            if not free:
                return False, a
            if len(free) == 1:
                l = free[0]
                a[abs(l)] = 1 if l > 0 else 0
                changed = True
        for c in xcls:            # literal xor ... xor literal = 1
            par, free = 1, []
            for l in c:
                v = a.get(abs(l))
                if v is None:
                    free.append(l)
                else:
                    par ^= v ^ (0 if l > 0 else 1)
            if not free:
                if par != 0:
                    return False, a
                continue
            if len(free) == 1:
                l = free[0]
                need = par            # value of the literal that makes the sum 1
                a[abs(l)] = need if l > 0 else 1 - need
                changed = True
    return True, a


def check(n_in, n_gates, window, seed, pct, nonlinear, xor_kind, mode="local"):
    topo = C.make_topology(n_in, n_gates, n_in, window, seed, 0.0)
    types = topo.gate_types(pct, nonlinear, xor_kind)
    copy = M.Copy(topo, types, topo.seed, mode)
    for x in itertools.product([0, 1], repeat=n_in):
        v = C.simulate(topo, types, list(x))
        assert [v[o] for o in topo.outputs] == copy.outputs_of(list(x)), "copy differs"
    for enc in ("tseitin", "aig", "xnative"):
        nv, cls, xcls = M.miter_cnf(topo, types, copy, enc)
        for x in itertools.product([0, 1], repeat=n_in):
            ok, a = propagate(nv, cls, xcls, {i + 1: x[i] for i in range(n_in)})
            assert not ok, "miter satisfiable at %s (%s)" % (x, enc)
    # break one node of the copy (AND <-> XOR) until the function changes
    rng = random.Random(seed)
    if not copy.nodes:              # copy is pure wiring (tiny linear circuits): nothing to break
        return False
    for _ in range(50):
        bad = M.Copy(topo, types, topo.seed, mode)
        j = rng.randrange(len(bad.nodes))
        op, la, lb = bad.nodes[j]
        bad.nodes[j] = (M.XOR2 if op == M.AND2 else M.AND2, la, lb)
        diff = []
        for x in itertools.product([0, 1], repeat=n_in):
            v = C.simulate(topo, types, list(x))
            if [v[o] for o in topo.outputs] != bad.outputs_of(list(x)):
                diff.append(x)
        if diff:
            break
    else:
        return False
    for enc in ("tseitin", "aig", "xnative"):
        nv, cls, xcls = M.miter_cnf(topo, types, bad, enc)
        found = 0
        for x in itertools.product([0, 1], repeat=n_in):
            ok, a = propagate(nv, cls, xcls, {i + 1: x[i] for i in range(n_in)})
            if ok:
                assert tuple(x) in [tuple(d) for d in diff], "model where circuits agree"
                assert len(a) == nv, "propagation did not reach a full assignment"
                found += 1
        assert found == len(diff), "%s: %d models, %d differing inputs" % (enc, found, len(diff))
    vtxt = M.miter_verilog(topo, types, copy)
    assert vtxt.count("wire h") == len(copy.nodes) and vtxt.count("wire g") == n_gates
    return True


def main():
    for mode in ("local", "linear"):
        n = broken = 0
        for nonlinear, xk in ((C.MUX, C.XOR3), (C.MUX, C.XOR), (C.AND, C.XOR)):
            for n_in in (3, 4, 6, 8):
                for pct in (0, 30, 50, 80, 100):
                    for seed in range(4):
                        n_gates = 4 * n_in
                        broken += int(check(n_in, n_gates, 2 * n_in, seed, pct, nonlinear, xk, mode))
                        n += 1
        print("OK (%s copy): %d circuits x 3 encodings, every input assignment conflicts; "
              "%d broken copies detected with exactly the differing inputs as models" % (mode, n, broken))


if __name__ == "__main__":
    main()
