# bmc-hardness-map
Mini project: mapping which circuit structures classical SAT/BMC solvers struggle with, to find candidates for quantum hand-off

All instances so far are satisfiable by construction; unsatisfiable properties (equivalence miters, HWMCC benchmarks) are next.

## Files

| File | What it does |
|---|---|
| `circuits.py` | Circuit model, simulation, CNF encodings, Verilog writer (shared) |
| `gen_xor.py` | Generates the instances and `manifest.jsonl` |
| `run_bench.py` | Runs every solver configuration, writes a resumable CSV |
| `summarize.py` | Medians / timeouts per point, lists wrong answers |
| `seq.py`, `gen_seq.py` | Sequential circuits (registers, unrolled to bound k) |
| `run_xor_main.sh`, `run_xor3_main.sh`, `run_depth.sh`, `run_seq_pilot.sh` | One-command runs for each study |

All of it is standard-library Python (works with Rocky 9's `python3`). Run everything from `bash`, not tcsh.

## Property card: XOR-mix sweep

**Question.** At fixed circuit size, how do classical solver effort and runtime change as the share of XOR gates goes from 0% to 100%?

**Circuits.** Random combinational circuits with I inputs, I outputs and N = 8·I gates (default). Each gate reads from the previous 2·I signals (this window controls depth). Per random seed, the wiring and all inversions are fixed; only gate *types* change with XOR%, and the XOR sets are nested (the 20% circuit = the 10% circuit's XORs + more).

**Non-XOR gate.** Default `mux` (`s ? a : b`, balanced and nonlinear). The option `--nonlinear and` uses AND/OR gates instead. *Why the default is MUX:* random AND/OR circuits this deep drift toward near-constant outputs. In the pilot generation, 100% of 0%-XOR AND circuits were flagged degenerate (mean output bias 0.39), falling to ~0.02 at 100% XOR. That would make low-XOR instances look easy for reasons unrelated to XOR. With MUX the bias stays ≤ 0.06 at every XOR level, so XOR% is the only thing changing. Running a few AND seeds alongside is useful to show this confound in the report.

**Property: planted preimage.** Choose a random input x\*, set target = C(x\*), and ask: *is there an input x with C(x) = target?* The answer is SAT by construction. This is a search problem, the kind Grover's algorithm addresses, and the same shape as cryptanalytic preimage attacks. In Verilog it is `assert (y != target)`, so for SBY/EBMC a FAIL (counterexample) = SAT.

**Encodings (same circuit, three CNF files).**
- `tseitin.cnf`: XOR as its 4 direct clauses, MUX as 4 clauses, AND as 3.
- `aig.cnf`: XOR and MUX first decomposed into AND gates (what a Yosys/ABC flow produces), then encoded. Hides XOR structure.
- `xnative.cnf`: XORs as CryptoMiniSat native `x` clauses (only CMS reads this).

All three were checked exhaustively against the circuit on small instances (every input assignment, plus every single-gate flip must violate a clause).

**Futility guards.**
- No gate reads the same signal twice. Every primary input is used.
- Every output structurally depends on ≥ 50% of the inputs (regenerated otherwise).
- Random simulation (512 samples) flags outputs that are constant or duplicate another output: column `degenerate`, excluded from summaries by default.
- Every SAT answer from a CNF solver is verified by simulating the circuit on the returned inputs (`verified`). Wrong answers are printed in red.
- Solver effort (conflicts) is recorded: near-zero conflicts mean the instance was decided by preprocessing, not search.

**Expected pattern.** Plain CDCL (Kissat, CaDiCaL) should get harder as XOR% rises; at 100% XOR the problem is linear and CryptoMiniSat with Gaussian elimination should solve it almost instantly. The mixed middle region is the candidate zone where no classical method has a structural shortcut.

## Configurations (`run_bench.py --configs ...`)

| Config | Solver | Input |
|---|---|---|
| `kissat`, `cadical` | Kissat, CaDiCaL | tseitin.cnf |
| `cms` | CryptoMiniSat, default | tseitin.cnf |
| `cms_gauss` | CryptoMiniSat, Gaussian elim. always on | tseitin.cnf (XORs must be detected) |
| `cms_gauss_xnat` | CryptoMiniSat, Gaussian elim. always on | xnative.cnf (XORs given directly) |
| `kissat_aig`, `cadical_aig` | Kissat, CaDiCaL | aig.cnf (encoding study) |
| `sby_yices` | SBY + smtbmc/Yices, depth 1 | top.v |
| `ebmc_cadical`, `ebmc_minisat` | EBMC 6.1, bound 1 | top.v |

CNF solvers run with `--solver-seeds` different random seeds (default 3). SBY/EBMC run once, on circuit seeds below `--tool-seeds` (default 2).

## Output columns (`results/*.csv`)

instance, family, nonlinear, inputs, gates, xor_pct, n_xor, circuit_seed, depth, degenerate, config, solver_seed, verdict (SAT/UNSAT/TIMEOUT/UNKNOWN), expected, verified, timeout, wall_s, cpu_s, peak_mem_mb, conflicts, decisions, propagations, restarts, vars, clauses, xor_clauses, returncode, host, date, log.

Wall/CPU time and peak memory come from the kernel for each run (CPU includes the tool's child processes). Raw logs are kept in `results/logs/`, with solver model lines removed once verified.

## Notes on timing quality

- Runs execute in parallel; parallel runs share memory bandwidth, so absolute times carry some noise. Keep `--workers` well below the core count and check `uptime` before starting (other people use the server).
- Record the machine: `lscpu | grep "Model name"` and `uptime` at start and end.

## Property card: combinational → sequential (draft for review)

**Question.** Holding the logic per time step fixed, how does solver effort change as more of the circuit becomes state (registers fed back to the next step), and as the BMC bound k grows?

**Circuit.** One random step function: I primary inputs plus R current-state bits go in; O observed outputs plus R next-state bits come out; N gates per step (fixed across R). The initial state is a fixed random constant. BMC unrolls the step function k times. The XOR fraction and MUX gate work as in the XOR study.

**Property: planted trace.** Choose a random input sequence, simulate k steps and record the outputs. Question: is there an input sequence that reproduces the recorded outputs? SAT by construction; every model is checked by simulating k steps.
- `--observe all` (default): outputs must match at *every* step. Free bits = k·I, constrained bits = k·O, so the solution count stays near one at every R.
- `--observe last`: only step k−1 is checked.

**The knob.** With R = 0 the steps are independent: the problem is k separate combinational preimage problems of I bits each (fully combinational end point). Each added register couples the steps, so later outputs depend on earlier inputs. Unrolled size (k·N gates) and free bits (k·I) stay the same across R; only the coupling changes.

**Bound sweep.** Same circuit, more steps. Bound sweeps use prefix-consistent planted sequences (the k=4 trace is the start of the k=8 trace).

**Futility guards (in the manifest).** `obs_state_support` (how much of the state the outputs read; ~1.0 = fully mixed) and `next_input_support` (how much of the input enters the state). Instances with R > 0 and `obs_state_support` < 0.25 are marked `state_weak`, since they would be combinational in disguise. Degenerate (constant/duplicate) outputs are flagged as before.

**Tool details.** CNF solvers get the unrolled CNF (inputs are variables 1..k·I, step t bit i → t·I+i+1). SBY runs `depth k`; EBMC runs `--bound k−1` (k−1 transitions = steps 0..k−1). The Verilog uses a step counter and an `ok` register, and asserts that the trace is *not* reproducible, so a counterexample (FAIL/REFUTED) = SAT.

**Checks done.** For small circuits (1–2 inputs, 0–3 registers, 3–4 steps) every input sequence was enumerated: all three CNF encodings have exactly as many solutions as brute-force simulation, the Verilog netlist evaluates identically to the simulator, and the targets embedded in the Verilog match.

## Audit of the XOR study (10 Nov)

**Confound found: the original XOR sweep changed the wiring, not just the gate function.** The MUX gate reads three wires (`s ? a : b`); the original 2-input XOR reads two. Turning a MUX into an XOR therefore dropped a wire. Going from 0% to 100% XOR removed one third of all wires and about 30% of the true logic depth (e.g., 64 inputs × 2,048 gates: 6,144 → 4,096 wires, depth 91 → 63). Size and depth are the strongest hardness knobs measured so far, so part of the "more XOR = easier" trend in `xor_main.csv` may come from the circuits getting shallower. The `depth` column in that run also ignored the select wire; `true_depth` is now recorded.

**Fix: `--xor-arity 3` (now the default).** XOR gates become `a ^ b ^ s` on the same three wires the MUX reads. Wiring, wire count and depth are identical at every XOR level (checked on all main-study sizes), outputs stay balanced (bias ≤ 0.04), no instance is degenerate, and the 0% circuits are byte-identical to the original run. All three encodings of the new gate were checked exhaustively. `run_xor3_main.sh` reruns the main study with this design.

**Inherent difference that remains: number of solutions.** MUX gates discard information (the unselected input is lost), XOR gates keep it. Exact counts on 16-input circuits: 0% XOR has a median of ~740 preimages of the planted target (out of 65,536 inputs); 50% has ~25; 100% has ~2. More solutions should make search *easier*, so this works against the observed trend rather than explaining it. It matters for the quantum comparison, since Grover's cost scales with √(N/M) for M solutions.

**Property type.** All instances are satisfiable (planted). The classical hardness of XOR in the literature mostly concerns *proving unsatisfiability* (parity reasoning has exponential resolution lower bounds) and large, mostly-linear systems. In mixed circuits, XOR gates help unit propagation: any two of a gate's values determine the third, while a MUX with an unknown select determines nothing.

## Results in this repository (Phase 1, 10–12 Nov 2026, babylon04)

All instances are satisfiable by construction (planted preimage / planted trace). Unsatisfiable properties come next.

| File | Study | Runs |
|---|---|---|
| `results/xor3_main.csv` | XOR mix, 3-input XOR design, 4 sizes × 0–100% XOR, 180 s timeout | 4,576 |
| `results/seq_main.csv` | Sequential: registers 0–64, bounds 4 and 8, 0% and 50% XOR, 300 s timeout | 2,048 |
| `results/seq_cms_default.csv` | Check of CryptoMiniSat timeouts with default settings | 160 |
| `results/grid.csv` | Width × XOR grid: 7 fan-in windows × 6 XOR levels, 64 inputs, 1,536 gates, 300 s timeout | 2,520 |

`grid_load.log` is the server load logged every 5 minutes during the grid run. Excluding the 12 grid runs on degenerate circuits, 9,292 runs are reported; none returned a wrong answer.

The first XOR run (2-input XOR, which dropped a wire per gate) and the calibration and pilot runs are not included: see "Audit of the XOR study" above.
