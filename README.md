# Unbound

**Miners execute programs without knowing what they run.**

The program compiles to a flat stream of integers — meaningless without the private
schema the submitter keeps. Any computation expressible as a search problem runs on the
network: ML training, protein folding, optimization, data analysis.

No new hardware. No hard fork. Bitcoin's existing mining infrastructure works for you.

---

## The Problem

Bitcoin's mining network runs at 500 ExaHash per second — trillions of SHA-256
computations every second, each one checked against a target and discarded.
The work proves effort was spent. It produces nothing else.

The world's largest distributed compute network exists, runs 24/7, is globally
distributed, and is economically incentivized. The only thing wrong is what it computes.

---

## The Idea

Replace hash puzzles with real computation.

A submitter compiles a program to a binary chunk — a LEB128-encoded integer stream.
A miner receives the chunk, runs it through the Unbound Virtual Machine, returns a
result, and earns UBD. The miner never knows whether it evaluated a protein energy
landscape, estimated an ML gradient, checked a prime, or something else entirely.

The submitter holds a private Schema — a map from stream positions to meaning.
The miner sees numbers. The submitter sees results.

```
Submitter                              Miner
─────────                              ─────
Python (or any language)
      ↓ compile
UVM stream + Schema          →→→    Binary blob (integers only)
      │                                    ↓ execute UVM
      │ Schema stays private          Raw result integers
      ↓                      ←←←           ↓ return
Decode result with Schema
= meaningful output
```

This is not encryption. The overhead is ~1–5% — the cost of VM interpretation only.
The privacy comes from compilation: variable names, intent, and structure are gone.
The miner has no context to reconstruct meaning.

---

## How It Works

**1. Compile**

```python
from unbound.compiler.compiler import compile_source

stream, schema = compile_source("print(sum(range(10)))")
# stream → flat list of integers (UVM opcodes + operands)
# schema → { variables: {...}, output_positions: [...] }  ← private, never sent
```

**2. Encode and transmit**

The stream is LEB128-encoded — the same binary format as WebAssembly.
Opcodes (1–99) and small addresses encode to 1 byte each. 2–3× smaller than JSON.
The miner receives raw bytes with no ASCII context.

**3. Execute**

The miner decodes bytes, runs the UVM (sandboxed stack machine), returns output
integers. The UVM is deterministic: same input always produces same output.

**4. Decode**

The submitter uses the private Schema to interpret the raw integers.

---

## Quick Demo

```bash
# Install
git clone https://github.com/YOUR_USERNAME/unbound && cd unbound
pip install -e .

# Terminal 1 — start a node (API + WebSocket server)
unbound node

# Terminal 2 — start a miner
unbound mine --id miner1

# Terminal 3 — fund an address and submit a job
unbound faucet alice --amount 1000
unbound submit demo/hello.py --from alice --payment 100
unbound result <job_id> --wait
# Results: [45]
```

`demo/hello.py` is `print(sum(range(10)))`. The miner ran it and returned `[45]`
without knowing it was summing a range.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Job Submitter                                                  │
│  Source → Compiler → UVM stream + Schema (private)              │
└──────────────────────┬──────────────────────────────────────────┘
                       │ POST /jobs  (base64 binary chunks)
┌──────────────────────▼──────────────────────────────────────────┐
│  Unbound Node                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐   │
│  │ REST API │  │ Registry │  │  Chain   │  │    Ledger      │   │
│  │ FastAPI  │  │ Chunks   │  │  PoUW    │  │ UBD / Escrow   │   │
│  └──────────┘  └────┬─────┘  └──────────┘  └────────────────┘   │
│                     │ WebSocket  (LEB128 binary frames)         │
└─────────────────────┼───────────────────────────────────────────┘
          ┌───────────┼───────────┐
    ┌─────▼──┐  ┌─────▼──┐  ┌────▼───┐
    │Miner A │  │Miner B │  │Miner C │  ← see only integer streams
    │  UVM   │  │  UVM   │  │  UVM   │  ← know nothing about intent
    └────────┘  └────────┘  └────────┘
```

**Components:**
- `uvm/` — stack machine, 30+ opcodes, LEB128 encode/decode
- `compiler/` — Python subset → UVM stream + Schema
- `registry/` — chunk lifecycle: pending → assigned → completed → reassigned
- `ledger/` — UBD balances and escrow in SQLite
- `chain/` — Proof of Useful Work consensus, tamper-evident block chain
- `miner/` — daemon: pull chunk → execute UVM → submit result
- `network/` — WebSocket server dispatching binary chunk frames
- `api/` — FastAPI REST: `/jobs`, `/compile`, `/balance`, `/health`
- `sdk/` — Python client library for any product to submit jobs and collect results

---

## What Can Run On It

Every computation is a search problem in disguise:
`f(x) = y` → "find y such that verify(x, y) = true"

| Problem | Each miner evaluates | Miners collectively map |
|---|---|---|
| ML training | `loss(weights, data)` for one candidate | The weight space |
| Protein folding | `energy(conformation)` for one structure | The energy landscape |
| Drug discovery | `binding_score(molecule)` for one molecule | Chemical space |
| Route optimization | `cost(route)` for one route | The solution space |
| Prime search | `is_prime(N)` for one number | The integer space |
| Data analysis | Any function over one data slice | The full dataset |

Every miner attempt maps one point in the solution space. Nothing is discarded.
The pool collects all evaluations and sells the complete solution map to whoever
submitted the job.

**When Unbound makes sense:** The network overhead (WebSocket dispatch, chunk
encoding, payment escrow) is fixed per chunk — roughly the cost of a short network
round-trip. That overhead is only justified when the computation *inside* the chunk
is expensive enough to pay for it. The break-even is roughly: would you wait
10 seconds for one candidate to evaluate on your laptop? If yes, the parallelism
is worth it. If the computation takes microseconds (e.g., `x²`, a hash, a small
arithmetic expression), run it locally.

Good fits — each candidate evaluation is expensive:
- **ML hyperparameter search** — each candidate trains a model for N epochs (minutes)
- **Neural architecture search** — each candidate is a full training run
- **Protein folding / drug design** — each candidate is an energy minimization
- **RL policy search** — each candidate runs a full environment rollout
- **Monte Carlo simulation** — each candidate is thousands of sampled paths
- **Any embarrassingly parallel job** you'd otherwise rent 100 cloud instances for

The SDK examples (`examples/search/`) use simple arithmetic to keep the conversion
pattern readable. They are teaching tools, not production use cases.

---

## Cooperative Mining — No Lottery

Bitcoin pays only the winner. 99.999% of compute effort produces nothing.

Unbound pays per computation completed. The job is the unit of work:

```
Job submitted: program + dataset (N slices) + payment locked in escrow

For each slice:
  → assigned to 2 miners independently
  → both execute UVM on the same slice
  → if results agree → slice verified → both miners paid
  → if results disagree → reassigned to 2 new miners

When all N slices complete → job done → full escrow released
```

Two independent miners agreeing on the same deterministic output is the proof of
correctness. No hash puzzle needed. No lottery. Predictable income proportional to
compute contributed.

---

## Bitcoin Integration — Three Layers

Unbound overlays on Bitcoin without modifying a single node, miner, or protocol.

**Layer 1 — Unknowing (every Bitcoin miner, right now)**

Job and result data is embedded in `OP_RETURN` fields of standard Bitcoin transactions.
Bitcoin miners include these transactions for fees. They see bytes. They know nothing.
The Bitcoin blockchain becomes Unbound's permanent, tamper-evident job and result ledger.

```
OP_RETURN: UBD:1:<job_id>:<program_cid>:<data_cid>
```

**Layer 2 — Passive (pool operators)**

A pool plugin runs on pool servers. It executes UVM chunks on idle CPU, embeds result
hashes in the Bitcoin coinbase. ASICs continue mining SHA-256 unchanged. Pool operators
earn UBD in addition to BTC block rewards — from CPU cycles that were already idle.

**Layer 3 — Active (any machine)**

The Unbound miner daemon runs on any Linux machine: a dedicated server, a cloud VM,
or the idle ARM control board of an ASIC miner. No firmware change, no ASIC
modification. One install, one background process, earning UBD from idle cycles.

```
Bitcoin miners earn:   BTC (unchanged) + UBD transaction fees (automatic)
Pool operators earn:   existing BTC revenue + UBD from pool server CPU
Active miners earn:    full UBD per completed computation chunk
```

---

## Private Cluster Mode — No Payment Required

Unbound solves a second problem independent of cryptocurrency: **compute aggregation
across heterogeneous nodes without microservices complexity.**

If you have an HPC cluster — 10 machines, some CPU, one GPU node, one high-memory
node — running a single logical program across all of them today requires MPI,
SLURM, Kubernetes, or Ray. Each requires cluster-wide configuration, shared
filesystems, and a coordinator that knows the topology.

With Unbound cluster mode:
- Each machine runs `unbound cluster mine` — one command, no configuration
- The coordinator dispatches chunks to whoever is available
- A GPU node naturally picks up chunks that take longer; fast CPU nodes pick up more
- New nodes join by starting the miner — no cluster reconfiguration
- The submitter sees one job, one result

```bash
# On the coordinator machine
unbound cluster node

# On each worker (CPU, GPU, whatever)
unbound cluster mine --server ws://coordinator:8765

# Submit a job from anywhere on the network
unbound cluster run my_program.py
```

From Python:

```python
from unbound.sdk import ClusterClient

client = ClusterClient("http://coordinator:8000")

# Same API as the public network — just no payment
results = client.run("print(sum(range(10)))")

# All SearchJob types work identically
job = MinimizeJob(eval_body=LOSS_FN, candidates=search_space, payment=0)
best = client.run_job(job)
```

No ledger. No chain. No token. Just chunk dispatch and result aggregation across
your machines. Schema separation still applies — workers see integer streams, not
the semantic meaning of what they compute. Useful for multi-department clusters
where IP isolation between teams matters.

---

## SDK — Connect Any Product

```python
from unbound.sdk import UnboundClient

client = UnboundClient("http://localhost:8000", address="alice")

# Compile and submit in one call
results = client.run("print(sum(range(10)))", payment=10)
# → [45]

# Or bring your own compiled binary chunks (any language, any compiler)
job_id = client.submit(chunks=[my_binary_blob], payment=10)
results = client.wait(job_id)
```

The SDK is language-agnostic. Any compiler that targets the UVM instruction set can
produce chunks. Unbound runs them. The compilation step is the caller's concern.

---

## Binary Encoding (LEB128)

Same scheme as WebAssembly. Variable-length: each integer takes the minimum bytes it needs.

| Value range | Bytes |
|---|---|
| Opcodes 0–127 (most ops) | 1 byte |
| Small addresses / counters | 1 byte |
| 128–16383 | 2 bytes |

Measured compression on real programs:

| Program | JSON | Fixed binary | LEB128 |
|---|---|---|---|
| Large literals | 45 B | 47 B | 19 B |
| Loop (range 100) | 91 B | 131 B | 40 B |
| Fibonacci (20 terms) | 106 B | 155 B | 47 B |

---

## UVM Instruction Set

| Opcode | Int | Description |
|---|---|---|
| PUSH | 1 | Push literal onto stack |
| POP | 2 | Discard top of stack |
| LOAD | 5 | Push value from memory[addr] |
| STORE | 6 | Pop and store to memory[addr] |
| ADD / SUB / MUL / DIV / MOD | 10–14 | Arithmetic |
| NEG | 15 | Negate top of stack |
| EQ / NEQ / LT / LTE / GT / GTE | 20–25 | Comparisons → 1 or 0 |
| AND / OR / NOT / XOR | 30–33 | Logic |
| JMP / JT / JF | 40–42 | Relative jumps |
| INPUT | 50 | Push from input buffer |
| OUTPUT | 51 | Pop to output buffer |
| HALT | 99 | Stop execution |

---

## Tests

```bash
pytest tests/ -v
# 63 tests, all passing
```

---

## Current Status

Prototype complete. Working demo. 63 passing tests.

**Seeking:**
- Early miners — run the daemon, earn UBD, help bootstrap the network
- Research partnerships — protein folding, ML, optimization compute workloads
- Pool operators — one plugin install, UBD earnings from idle pool server CPU
- Grant applications in progress — EF ESP, Gitcoin, Filecoin

See [WHITEPAPER.md](WHITEPAPER.md) for the full protocol specification.

---

## Stack

Python 3.13 · FastAPI · SQLite · asyncio + websockets · Click · MIT License

---

## Contributing

Issues and discussions open. Main branch protected — fork freely for modifications.
The reference implementation lives here. If your fork is better, the community finds it.

That is how Bitcoin Core works. It is how this works too.
