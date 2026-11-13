"""Sequential circuits for BMC: one random combinational step function with
R state registers, unrolled k times.

Per-step circuit (reuses circuits.Topology with n_in = I + R, n_out = O + R):
    inputs  0 .. I-1      primary inputs x_t (free at every step)
    inputs  I .. I+R-1    current state s_t
    outputs[:O]           observed outputs y_t
    outputs[O:O+R]        next state s_{t+1}
The initial state s_0 is a fixed random constant.

Property ("planted trace"): pick a random input sequence x*_0..x*_{k-1},
simulate, and record the outputs. Question: is there an input sequence that
reproduces the recorded outputs?
    observe = 'all'  : outputs at every step must match (default)
    observe = 'last' : only the outputs at step k-1 must match
SAT by construction; every returned input sequence is checked by simulation.

With R = 0 the steps are independent: 'all' becomes k separate combinational
preimage problems, 'last' becomes a single combinational preimage problem.
Adding registers couples the steps through the state.
"""

import random

import circuits as C


class SeqCircuit(object):
    def __init__(self, n_in, n_regs, n_gates, n_out, window, seed, xor_pct,
                 nonlinear="mux", min_support=0.5, topo_seed=None, xor_arity=2):
        self.I, self.R, self.N, self.O = n_in, n_regs, n_gates, n_out
        self.window, self.seed, self.xor_pct = window, seed, xor_pct
        self.nonlinear = nonlinear
        if topo_seed is None:
            self.topo = C.make_topology(n_in + n_regs, n_gates, n_out + n_regs, window, seed,
                                        min_support)
        else:
            self.topo = C.Topology(n_in + n_regs, n_gates, n_out + n_regs, window, topo_seed)
        self.topo_seed = self.topo.seed
        nl = C.MUX if nonlinear == "mux" else C.AND
        self.xor_arity = xor_arity
        self.types = self.topo.gate_types(xor_pct, nl, C.XOR3 if xor_arity == 3 else C.XOR)
        self.observed = self.topo.outputs[:n_out]
        self.next_state = self.topo.outputs[n_out:n_out + n_regs]
        rng = random.Random("init-%d-%d-%d-%d" % (n_in, n_regs, n_gates, seed))
        self.init = [rng.randrange(2) for _ in range(n_regs)]

    def planted_inputs(self, k):
        """Prefix-consistent: the sequence for k=4 is the start of the one for k=8."""
        rng = random.Random("plant-seq-%d-%d-%d-%d" % (self.I, self.R, self.N, self.seed))
        return [[rng.randrange(2) for _ in range(self.I)] for _ in range(k)]

    def simulate(self, xs):
        """xs: list of input vectors, one per step. Returns list of output vectors."""
        state = list(self.init)
        outs = []
        for x in xs:
            v = C.simulate(self.topo, self.types, list(x) + state)
            outs.append([v[o] for o in self.observed])
            state = [v[o] for o in self.next_state]
        return outs

    def targets(self, xs, observe):
        outs = self.simulate(xs)
        return outs if observe == "all" else [outs[-1]]

    def matches(self, xs, targets, observe):
        return self.targets(xs, observe) == targets

    # ---------------- structural features ----------------
    def features(self):
        sups = self.topo.supports()          # aligned with topo.outputs
        I, R, O = self.I, self.R, self.O
        state_mask = ((1 << R) - 1) << I
        in_mask = (1 << I) - 1

        def frac(mask, sup, width):
            return bin(sup & mask).count("1") / float(width) if width else 0.0
        obs = sups[:O]
        nxt = sups[O:O + R]
        f = {
            "depth": self.topo.depth(),
            # how much of the state each observed output reads (0 when R = 0)
            "obs_state_support": round(sum(frac(state_mask, s, R) for s in obs) / O, 3),
            # how much of the current input each next-state bit reads
            "next_input_support": round(sum(frac(in_mask, s, I) for s in nxt) / R, 3) if R else 0.0,
            "min_output_support": min(bin(s).count("1") for s in sups),
        }
        f.update(C.functional_features(self.topo, self.types, seed=self.seed))
        return f

    # ---------------- CNF ----------------
    def cnf(self, k, targets, observe, encoding):
        """Variables 1..k*I are the inputs (step t, bit i -> t*I + i + 1)."""
        I, R, N = self.I, self.R, self.N
        cls, xcls = [], []
        alloc = C.VarAlloc(k * I)
        init_vars = [alloc() for _ in range(R)]
        for j, b in enumerate(self.init):
            cls.append([init_vars[j] if b else -init_vars[j]])
        state_lits = init_vars
        n_in = I + R
        for t in range(k):
            in_lits = [t * I + i + 1 for i in range(I)] + state_lits
            gate_vars = [alloc() for _ in range(N)]
            C.encode_frame(self.topo, self.types, encoding, in_lits, gate_vars, alloc, cls, xcls)
            if observe == "all":
                tgt = targets[t]
            elif t == k - 1:
                tgt = targets[0]
            else:
                tgt = None
            if tgt is not None:
                for j, o in enumerate(self.observed):
                    v = gate_vars[o - n_in]
                    cls.append([v if tgt[j] else -v])
            state_lits = [gate_vars[o - n_in] for o in self.next_state]
        return alloc.n, cls, xcls

    # ---------------- Verilog ----------------
    def verilog(self, k, targets, observe):
        I, R, O = self.I, self.R, self.O
        topo = self.topo
        n_in = I + R
        cw = max(1, k.bit_length())

        def bits(v):
            return "%d'b%s" % (len(v), "".join(str(b) for b in reversed(v)))

        def sig(s, inv=0):
            if s < I:
                name = "x[%d]" % s
            elif s < n_in:
                name = "s[%d]" % (s - I)
            else:
                name = "g%d" % (s - n_in)
            return ("~" + name) if inv else name

        L = ["module top(input clk, input [%d:0] x, output [%d:0] y);" % (I - 1, O - 1)]
        if R:
            L.append("  reg [%d:0] s;" % (R - 1))
        L.append("  reg [%d:0] cnt;" % (cw - 1))
        if observe == "all":
            L.append("  reg ok;")
        init = ["cnt = %d'd0;" % cw]
        if R:
            init.append("s = %s;" % bits(self.init))
        if observe == "all":
            init.append("ok = 1'b1;")
        L.append("  initial begin %s end" % " ".join(init))
        for g in range(topo.n_gates):
            a, b = topo.fanin[g]
            ia, ib, io = topo.inv[g]
            if self.types[g] == C.MUX:
                expr = "(%s ? %s : %s)" % (sig(topo.sel[g]), sig(a, ia), sig(b, ib))
            elif self.types[g] == C.XOR3:
                expr = "(%s ^ %s ^ %s)" % (sig(a, ia), sig(b, ib), sig(topo.sel[g]))
            else:
                op = "&" if self.types[g] == C.AND else "^"
                expr = "(%s %s %s)" % (sig(a, ia), op, sig(b, ib))
            if io:
                expr = "~" + expr
            L.append("  wire g%d = %s;" % (g, expr))
        for j, o in enumerate(self.observed):
            L.append("  assign y[%d] = %s;" % (j, sig(o)))
        if R:
            L.append("  wire [%d:0] s_next;" % (R - 1))
            for j, o in enumerate(self.next_state):
                L.append("  assign s_next[%d] = %s;" % (j, sig(o)))
        if observe == "all":
            # expected output for the current step
            expr = bits(targets[k - 1])
            for t in range(k - 2, -1, -1):
                expr = "(cnt == %d'd%d) ? %s : %s" % (cw, t, bits(targets[t]), expr)
            L.append("  wire [%d:0] tgt = %s;" % (O - 1, expr))
        else:
            L.append("  wire [%d:0] tgt = %s;" % (O - 1, bits(targets[0])))
        L.append("  wire hit = (y == tgt);")
        upd = ["cnt <= cnt + %d'd1;" % cw]
        if R:
            upd.append("s <= s_next;")
        if observe == "all":
            upd.append("ok <= ok & hit;")
        L.append("  always @(posedge clk) begin %s end" % " ".join(upd))
        cond = "cnt == %d'd%d && hit" % (cw, k - 1)
        if observe == "all":
            cond += " && ok"
        # fails (counterexample) exactly when an input sequence reproduces the trace
        L.append("  always @* assert (!(%s));" % cond)
        L.append("endmodule")
        return "\n".join(L) + "\n"


SBY_SEQ_TEMPLATE = """[options]
mode bmc
depth %d
vcd off

[engines]
smtbmc yices

[script]
read -formal top.v
prep -top top

[files]
top.v
"""
