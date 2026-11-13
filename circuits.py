"""Random combinational circuits with a controlled XOR fraction.

Shared by gen_xor.py (writes instances) and run_bench.py (verifies models).
Python 3.9 compatible, standard library only.

Circuit model
-------------
Signals 0..I-1 are primary inputs. Gate k (0-based) drives signal I+k.
Each gate has two fan-ins (signal ids), an inversion flag per fan-in and an
output inversion flag. A gate is either
    AND : out = (a ^ ia) & (b ^ ib) ^ io      (covers AND/OR/NAND/NOR)
    XOR : out = (a ^ ia) ^ (b ^ ib) ^ io      (covers XOR/XNOR)
The wiring and all inversion flags depend only on the seed. The XOR
percentage only changes which gates are XOR, and the XOR sets are nested:
gates are shuffled once per seed and the first round(p*N) become XOR.
"""

import random

AND, XOR, MUX, XOR3 = "AND", "XOR", "MUX", "XOR3"
# XOR3 = a ^ b ^ s: a 3-input XOR on the same three wires a MUX reads,
# so swapping MUX <-> XOR3 changes only the gate function, not the wiring.


class Topology(object):
    """Wiring + inversion flags + output selection for one seed."""

    def __init__(self, n_in, n_gates, n_out, window, seed):
        self.n_in, self.n_gates, self.n_out = n_in, n_gates, n_out
        self.window, self.seed = window, seed
        rng = random.Random("topo-%d-%d-%d-%d-%d" % (n_in, n_gates, n_out, window, seed))
        self.fanin = []      # (a, b) signal ids
        self.inv = []        # (ia, ib, io)
        # the first gates take every primary input once, so no input is unused
        perm = list(range(n_in))
        rng.shuffle(perm)
        if len(perm) % 2:
            perm.append(rng.choice(perm[:-1]))
        fanout = [0] * (n_in + n_gates)
        for k in range(n_gates):
            sig = n_in + k
            if 2 * k + 1 < len(perm):
                a, b = perm[2 * k], perm[2 * k + 1]
            else:
                lo = max(0, sig - window)
                a = rng.randrange(lo, sig)
                b = rng.randrange(lo, sig - 1)
                if b >= a:
                    b += 1      # guard: two distinct fan-ins, never x op x
                # guard: a signal about to leave the window that nothing has read yet
                # is used now, so no input's influence dies out inside the circuit
                if sig - window >= 0 and fanout[sig - window] == 0:
                    edge = sig - window
                    if b == edge:
                        a, b = b, a
                    elif a != edge:
                        a = edge
            fanout[a] += 1
            fanout[b] += 1
            self.fanin.append((a, b))
            self.inv.append((rng.randrange(2), rng.randrange(2), rng.randrange(2)))
        # third fan-in, used only when a gate is a MUX (select line); distinct from a, b
        self.sel = []
        for k in range(n_gates):
            sig = n_in + k
            a, b = self.fanin[k]
            lo = max(0, sig - window)
            pool = [s for s in range(lo, sig) if s != a and s != b]
            if not pool:
                pool = [s for s in range(sig) if s != a and s != b] or [a]
            self.sel.append(rng.choice(pool))
        # XOR order: nested sets across XOR percentages
        self.xor_order = list(range(n_gates))
        rng.shuffle(self.xor_order)
        # outputs: prefer gates with no fan-out, latest first, then latest gates
        used = set()
        for a, b in self.fanin:
            used.add(a)
            used.add(b)
        sinks = [s for s in range(n_in + n_gates - 1, n_in - 1, -1) if s not in used]
        rest = [s for s in range(n_in + n_gates - 1, n_in - 1, -1) if s in used]
        self.outputs = (sinks + rest)[:n_out]

    def gate_types(self, xor_pct, nonlinear=AND, xor_kind=XOR):
        """nonlinear: AND (2-input AND/OR family) or MUX (balanced s ? a : b).
        xor_kind: XOR (2-input) or XOR3 (3-input, same wires as MUX)."""
        n_xor = int(round(self.n_gates * xor_pct / 100.0))
        types = [nonlinear] * self.n_gates
        for g in self.xor_order[:n_xor]:
            types[g] = xor_kind
        return types

    # ---------- structural features (independent of XOR %) ----------
    def supports(self):
        """Structural input support (set of input ids) of each output."""
        sup = [None] * (self.n_in + self.n_gates)
        for i in range(self.n_in):
            sup[i] = 1 << i
        for k, (a, b) in enumerate(self.fanin):
            sup[self.n_in + k] = sup[a] | sup[b]
        return [sup[o] for o in self.outputs]

    def depth(self):
        d = [0] * (self.n_in + self.n_gates)
        for k, (a, b) in enumerate(self.fanin):
            d[self.n_in + k] = 1 + max(d[a], d[b])
        return max(d[o] for o in self.outputs)

    def true_depth(self, types):
        """Depth counting every wire a gate actually reads (MUX/XOR3 select included)."""
        d = [0] * (self.n_in + self.n_gates)
        for k, (a, b) in enumerate(self.fanin):
            m = max(d[a], d[b])
            if types[k] in (MUX, XOR3):
                m = max(m, d[self.sel[k]])
            d[self.n_in + k] = 1 + m
        return max(d[o] for o in self.outputs)

    def n_edges(self, types):
        return sum(3 if t in (MUX, XOR3) else 2 for t in types)

    def support_ok(self, min_frac):
        sups = self.supports()
        union = 0
        for s in sups:
            union |= s
        min_sup = min(bin(s).count("1") for s in sups)
        return union == (1 << self.n_in) - 1 and min_sup >= min_frac * self.n_in, min_sup


def make_topology(n_in, n_gates, n_out, window, seed, min_support_frac=0.5, max_tries=200):
    """Regenerate (with a derived seed) until the support guard passes."""
    for t in range(max_tries):
        topo = Topology(n_in, n_gates, n_out, window, seed * 1000 + t)
        ok, _ = topo.support_ok(min_support_frac)
        if ok:
            topo.base_seed = seed
            return topo
    raise RuntimeError("could not satisfy support guard; increase gates or window")


def simulate(topo, types, x_bits):
    """x_bits: list of 0/1 for inputs. Returns list of all signal values."""
    v = list(x_bits) + [0] * topo.n_gates
    n_in = topo.n_in
    for k in range(topo.n_gates):
        a, b = topo.fanin[k]
        ia, ib, io = topo.inv[k]
        va, vb = v[a] ^ ia, v[b] ^ ib
        t = types[k]
        if t == AND:
            r = va & vb
        elif t == XOR:
            r = va ^ vb
        elif t == XOR3:
            r = va ^ vb ^ v[topo.sel[k]]
        else:
            r = va if v[topo.sel[k]] else vb
        v[n_in + k] = r ^ io
    return v


def simulate_words(topo, types, words):
    """Bit-parallel simulation: words[i] is an int holding many samples of input i."""
    mask = (1 << 64) - 1
    v = list(words) + [0] * topo.n_gates
    n_in = topo.n_in
    for k in range(topo.n_gates):
        a, b = topo.fanin[k]
        ia, ib, io = topo.inv[k]
        va = v[a] ^ (mask if ia else 0)
        vb = v[b] ^ (mask if ib else 0)
        t = types[k]
        if t == AND:
            r = va & vb
        elif t == XOR:
            r = va ^ vb
        elif t == XOR3:
            r = va ^ vb ^ v[topo.sel[k]]
        else:
            sv = v[topo.sel[k]]
            r = (sv & va) | (~sv & mask & vb)
        v[n_in + k] = r ^ (mask if io else 0)
    return [v[o] for o in topo.outputs]


def functional_features(topo, types, rounds=8, seed=0):
    """Random-simulation checks for functional degeneracy (cancellation, constants).

    Returns dict with:
      const_outputs  : outputs that were constant over all samples
      dup_outputs    : outputs identical to an earlier output over all samples
      mean_bias      : mean |P(out=1) - 0.5| over outputs (0 = balanced)
    """
    rng = random.Random(seed)
    ones = [0] * topo.n_out
    allw = [[] for _ in range(topo.n_out)]
    total = 64 * rounds
    for _ in range(rounds):
        words = [rng.getrandbits(64) for _ in range(topo.n_in)]
        outs = simulate_words(topo, types, words)
        for j, w in enumerate(outs):
            ones[j] += bin(w).count("1")
            allw[j].append(w)
    sigs = [tuple(w) for w in allw]
    const = sum(1 for j in range(topo.n_out) if ones[j] in (0, total))
    dup = len(sigs) - len(set(sigs))
    bias = sum(abs(o / float(total) - 0.5) for o in ones) / topo.n_out
    return {"const_outputs": const, "dup_outputs": dup, "mean_bias": round(bias, 4)}


# ------------------------------------------------------------------ CNF
def _lit(var, neg):
    return -var if neg else var


def encode_frame(topo, types, encoding, in_lits, gate_vars, alloc, cls, xcls):
    """Append the clauses for one copy of the circuit.

    in_lits[i]   : literal standing for circuit input i (a variable, possibly negated)
    gate_vars[k] : variable for gate k's output
    alloc()      : returns a fresh variable (aux vars of the 'aig' encoding)
    encoding     : 'tseitin' | 'aig' | 'xnative' (see cnf_clauses)
    """
    n_in = topo.n_in

    def lit(sig, neg=0):
        v = in_lits[sig] if sig < n_in else gate_vars[sig - n_in]
        return -v if neg else v

    def and_cl(z, la, lb):
        # z <-> la & lb   (all literals)
        cls.append([-z, la])
        cls.append([-z, lb])
        cls.append([z, -la, -lb])

    for k in range(topo.n_gates):
        a, b = topo.fanin[k]
        ia, ib, io = topo.inv[k]
        la, lb = lit(a, ia), lit(b, ib)
        g = gate_vars[k]
        z = -g if io else g          # z is the un-inverted gate value
        if types[k] == AND:
            and_cl(z, la, lb)
        elif types[k] == MUX:
            sl = lit(topo.sel[k])
            if encoding == "aig":
                t1, t2 = alloc(), alloc()
                and_cl(t1, sl, la)
                and_cl(t2, -sl, lb)
                and_cl(-z, -t1, -t2)  # z = t1 | t2
            else:
                # z <-> (s ? la : lb)
                cls.append([-sl, -z, la])
                cls.append([-sl, z, -la])
                cls.append([sl, -z, lb])
                cls.append([sl, z, -lb])
        elif types[k] == XOR3:
            sl = lit(topo.sel[k])
            if encoding == "tseitin":
                # (-z) xor la xor lb xor sl = 1: forbid the 8 even-parity assignments
                lits = [-z, la, lb, sl]
                for m in range(16):
                    if bin(m).count("1") % 2 == 0:
                        cls.append([lits[i] if not (m >> i) & 1 else -lits[i] for i in range(4)])
            elif encoding == "xnative":
                xcls.append([-z, la, lb, sl])
            elif encoding == "aig":
                t = alloc()                       # t = la xor lb, then z = t xor sl
                t1, t2 = alloc(), alloc()
                and_cl(t1, la, -lb)
                and_cl(t2, -la, lb)
                and_cl(-t, -t1, -t2)
                u1, u2 = alloc(), alloc()
                and_cl(u1, t, -sl)
                and_cl(u2, -t, sl)
                and_cl(-z, -u1, -u2)
            else:
                raise ValueError(encoding)
        elif encoding == "tseitin":
            # z <-> la xor lb
            cls.append([-z, la, lb])
            cls.append([-z, -la, -lb])
            cls.append([z, -la, lb])
            cls.append([z, la, -lb])
        elif encoding == "xnative":
            # z xor la xor lb = 0   <=>  (-z) xor la xor lb = 1
            xcls.append([-z, la, lb])
        elif encoding == "aig":
            t1, t2 = alloc(), alloc()
            and_cl(t1, la, -lb)
            and_cl(t2, -la, lb)
            and_cl(-z, -t1, -t2)     # z = t1 | t2  <=>  -z = -t1 & -t2
        else:
            raise ValueError(encoding)


class VarAlloc(object):
    def __init__(self, start):
        self.n = start

    def __call__(self):
        self.n += 1
        return self.n


def cnf_clauses(topo, types, target, encoding):
    """Return (n_vars, clauses, xor_clauses) for the preimage problem.

    Variables: input i -> i+1 ; gate k -> n_in+k+1 ; extra aux vars after.
    encoding:
      'tseitin' : AND = 3 clauses, XOR = 4 clauses (direct XOR encoding)
      'aig'     : XOR decomposed into AND gates first (what a Yosys/ABC
                  flow produces), then AND = 3 clauses
      'xnative' : AND = 3 clauses, XOR as CryptoMiniSat 'x' clause
    target: list of output bits to hit.
    """
    n_in = topo.n_in
    cls, xcls = [], []
    in_lits = [i + 1 for i in range(n_in)]
    gate_vars = [n_in + k + 1 for k in range(topo.n_gates)]
    alloc = VarAlloc(n_in + topo.n_gates)
    encode_frame(topo, types, encoding, in_lits, gate_vars, alloc, cls, xcls)
    for j, o in enumerate(topo.outputs):
        cls.append([o + 1 if target[j] else -(o + 1)])
    return alloc.n, cls, xcls


def write_dimacs(path, nv, cls, xcls, comments=()):
    with open(path, "w") as f:
        for c in comments:
            f.write("c %s\n" % c)
        f.write("p cnf %d %d\n" % (nv, len(cls) + len(xcls)))
        for c in cls:
            f.write(" ".join(map(str, c)) + " 0\n")
        for c in xcls:
            f.write("x" + " ".join(map(str, c)) + " 0\n")


# ------------------------------------------------------------------ Verilog
def verilog(topo, types, target, module="top"):
    """Module with the circuit and an assertion that y never equals target.

    The assertion FAILS (counterexample = preimage) iff a preimage exists,
    so FAIL corresponds to SAT, PASS to UNSAT.
    """
    n_in, n_out = topo.n_in, topo.n_out
    L = []
    L.append("module %s(input [%d:0] x, output [%d:0] y);" % (module, n_in - 1, n_out - 1))

    def s(sig, inv):
        name = ("x[%d]" % sig) if sig < n_in else ("g%d" % (sig - n_in))
        return ("~" + name) if inv else name

    for k in range(topo.n_gates):
        a, b = topo.fanin[k]
        ia, ib, io = topo.inv[k]
        if types[k] == MUX:
            expr = "(%s ? %s : %s)" % (s(topo.sel[k], 0), s(a, ia), s(b, ib))
        elif types[k] == XOR3:
            expr = "(%s ^ %s ^ %s)" % (s(a, ia), s(b, ib), s(topo.sel[k], 0))
        else:
            op = "&" if types[k] == AND else "^"
            expr = "(%s %s %s)" % (s(a, ia), op, s(b, ib))
        if io:
            expr = "~" + expr
        L.append("  wire g%d = %s;" % (k, expr))
    for j, o in enumerate(topo.outputs):
        L.append("  assign y[%d] = g%d;" % (j, o - n_in))
    tbits = "".join(str(target[j]) for j in range(n_out - 1, -1, -1))
    L.append("  always @* assert (y != %d'b%s);" % (n_out, tbits))
    L.append("endmodule")
    return "\n".join(L) + "\n"


SBY_TEMPLATE = """[options]
mode bmc
depth 1
vcd off

[engines]
smtbmc yices

[script]
read -formal top.v
prep -top top

[files]
top.v
"""
