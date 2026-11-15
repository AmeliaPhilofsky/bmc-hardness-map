"""Equivalence miters: a circuit checked against a restructured copy of itself.

The copy (the "gate" circuit, C') computes exactly the same function as the
original (the "gold" circuit, C) but is built from different primitives:

  MUX  s ? A : B   ->  either  (s & A) | (~s & B)   (AND/OR form)
                       or      B ^ (s & (A ^ B))    (XOR form)
  XOR3 A ^ B ^ S   ->  two 2-input XORs, association chosen at random
  AND  A & B       ->  A & B

The miter asks: is there an input x with C(x) != C'(x)? The copies are
equivalent by construction, so the answer is UNSAT. Every C gate still has an
equivalent point inside C' (as after light logic synthesis), so this is the
"structurally similar" kind of equivalence check.

Variables in every CNF: input i -> i+1, gold gate k -> n_in+k+1, then the
copy's nodes, auxiliary variables and the per-output difference variables.
"""

import random

import circuits as C

AND2, XOR2 = "AND2", "XOR2"


class Copy(object):
    """The restructured circuit C' as a netlist of 2-input AND/XOR nodes.

    Signals 0..n_in-1 are the primary inputs; node j drives signal n_in+j.
    A literal is (signal, neg). outputs[j] is the literal for output j.
    """

    def __init__(self, topo, types, seed):
        self.n_in = topo.n_in
        self.nodes = []          # (op, lit_a, lit_b)
        rng = random.Random("copy-%d" % seed)
        sig = {i: (i, 0) for i in range(topo.n_in)}   # gold signal -> copy literal

        def node(op, la, lb):
            self.nodes.append((op, la, lb))
            return (self.n_in + len(self.nodes) - 1, 0)

        def neg(l, n=1):
            return (l[0], l[1] ^ n)

        self.forms = []
        for k in range(topo.n_gates):
            a, b = topo.fanin[k]
            ia, ib, io = topo.inv[k]
            A, B = neg(sig[a], ia), neg(sig[b], ib)
            t = types[k]
            if t == C.MUX:
                S = sig[topo.sel[k]]
                if rng.random() < 0.5:
                    t1 = node(AND2, S, A)
                    t2 = node(AND2, neg(S), B)
                    z = neg(node(AND2, neg(t1), neg(t2)))       # t1 | t2
                    self.forms.append("mux_andor")
                else:
                    d = node(XOR2, A, B)
                    z = node(XOR2, B, node(AND2, S, d))         # B ^ (s & (A ^ B))
                    self.forms.append("mux_xor")
            elif t == C.XOR3:
                S = sig[topo.sel[k]]
                order = rng.randrange(3)
                if order == 0:
                    z = node(XOR2, node(XOR2, A, B), S)
                elif order == 1:
                    z = node(XOR2, node(XOR2, A, S), B)
                else:
                    z = node(XOR2, node(XOR2, B, S), A)
                self.forms.append("xor3_%d" % order)
            elif t == C.XOR:
                z = node(XOR2, A, B)
                self.forms.append("xor2")
            else:
                z = node(AND2, A, B)
                self.forms.append("and")
            sig[topo.n_in + k] = neg(z, io)
        self.outputs = [sig[o] for o in topo.outputs]

    def simulate(self, x_bits):
        v = list(x_bits) + [0] * len(self.nodes)
        for j, (op, la, lb) in enumerate(self.nodes):
            va = v[la[0]] ^ la[1]
            vb = v[lb[0]] ^ lb[1]
            v[self.n_in + j] = (va & vb) if op == AND2 else (va ^ vb)
        return v

    def outputs_of(self, x_bits):
        v = self.simulate(x_bits)
        return [v[s] ^ n for s, n in self.outputs]


def miter_cnf(topo, types, copy, encoding):
    """(n_vars, clauses, xor_clauses) for: exists x with C(x) != C'(x)."""
    n_in, N = topo.n_in, topo.n_gates
    cls, xcls = [], []
    in_lits = [i + 1 for i in range(n_in)]
    gate_vars = [n_in + k + 1 for k in range(N)]
    alloc = C.VarAlloc(n_in + N)
    C.encode_frame(topo, types, encoding, in_lits, gate_vars, alloc, cls, xcls)

    node_var = [alloc() for _ in copy.nodes]

    def lit(l):
        s, n = l
        v = (s + 1) if s < n_in else node_var[s - n_in]
        return -v if n else v

    def and_cl(z, la, lb):
        cls.append([-z, la])
        cls.append([-z, lb])
        cls.append([z, -la, -lb])

    def xor_def(z, la, lb):
        """z <-> la xor lb, in the requested encoding."""
        if encoding == "xnative":
            xcls.append([-z, la, lb])
        elif encoding == "aig":
            t1, t2 = alloc(), alloc()
            and_cl(t1, la, -lb)
            and_cl(t2, -la, lb)
            and_cl(-z, -t1, -t2)
        else:
            cls.append([-z, la, lb])
            cls.append([-z, -la, -lb])
            cls.append([z, -la, lb])
            cls.append([z, la, -lb])

    for j, (op, la, lb) in enumerate(copy.nodes):
        z = node_var[j]
        if op == AND2:
            and_cl(z, lit(la), lit(lb))
        else:
            xor_def(z, lit(la), lit(lb))

    diffs = []
    for j, o in enumerate(topo.outputs):
        d = alloc()
        xor_def(d, o + 1, lit(copy.outputs[j]))
        diffs.append(d)
    cls.append(diffs)                    # some output differs
    return alloc.n, cls, xcls


def miter_verilog(topo, types, copy, module="top"):
    """Gold circuit and its copy side by side; asserts the outputs are equal.

    The assertion holds (PASS / PROVED) iff the circuits are equivalent, so
    PASS corresponds to UNSAT and FAIL to SAT.
    """
    n_in, n_out = topo.n_in, topo.n_out
    gold = C.verilog(topo, types, [0] * n_out, module="gold").splitlines()
    body = [l for l in gold if l.startswith("  wire") or l.startswith("  assign")]
    L = ["module %s(input [%d:0] x, output [%d:0] y, output [%d:0] z);"
         % (module, n_in - 1, n_out - 1, n_out - 1)]
    L += body

    def s(l):
        sig, n = l
        name = ("x[%d]" % sig) if sig < n_in else ("h%d" % (sig - n_in))
        return ("~" + name) if n else name

    for j, (op, la, lb) in enumerate(copy.nodes):
        L.append("  wire h%d = (%s %s %s);" % (j, s(la), "&" if op == AND2 else "^", s(lb)))
    for j, l in enumerate(copy.outputs):
        L.append("  assign z[%d] = %s;" % (j, s(l)))
    L.append("  always @* assert (y == z);")
    L.append("endmodule")
    return "\n".join(L) + "\n"
