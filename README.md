# Unbound

**Distributed compute where workers never know what they run.**

Compile your program to a binary integer stream. Workers execute it, return integers,
earn payment. Without the private schema you keep, the integers are meaningless —
workers never learn what they computed.

Two ways to use it:

| | **Private cluster** | **Public network** |
|---|---|---|
| Workers | Your own machines | Anyone running the daemon |
| Payment | None | UBD token, per chunk |
| Setup | One command per machine | Node + miner daemon |
| Use case | HPC aggregation, internal jobs | Open compute market |

---

## For submitters — run compute across distributed workers

You have a program. You want it to run fast across many machines without:
- renting cloud instances and configuring them
- writing distributed code or managing a job scheduler
- exposing your data, model, or algorithm to the workers

```python
from unbound.sdk import ClusterClient, MinimizeJob

client = ClusterClient("http://coordinator:8000")

# Run any program across all workers
results = client.run("print(sum(range(1000)))")

# Or use the SDK's search job abstractions
job = MinimizeJob(eval_body=LOSS_FN, candidates=search_space, payment=0)
best_params = client.run_job(job)
```

Workers receive a flat integer stream. No variable names. No intent. No data schema.
They return integers. You decode the meaning locally with the private schema.

**Good fits** — the parallelism pays when each candidate evaluation is expensive:
- ML hyperparameter search — each candidate trains for N epochs
- Neural architecture search — each candidate is a full training run
- Protein folding / drug design — each candidate is an energy minimization
- RL policy search — each candidate runs a full environment rollout
- Monte Carlo simulation — each candidate is thousands of sampled paths
- Any embarrassingly parallel job you'd otherwise run on 100 cloud instances

---

## For workers — contribute compute, earn payment

You have machines. They sit idle. You want them earning.

**Private cluster** — contribute to an internal coordinator, no token needed:
```bash
unbound cluster mine --server ws://coordinator:8765
```

**Public network** — connect to the global network, earn UBD per chunk completed:
```bash
unbound mine --id my-miner
```

You never see what you're computing. You receive a binary blob, run it through
the UVM (sandboxed stack machine), return integers. The submitter interprets them.

---

## How it works

**Compile** — any program compiles to two artifacts:

```python
from unbound.compiler.compiler import compile_source

stream, schema = compile_source("print(sum(range(10)))")
# stream → flat integer list  (transmitted to workers)
# schema → variable map       (stays private, never transmitted)
```

**Transmit** — the stream is LEB128-encoded (same format as WebAssembly).
Opcodes and small values encode to 1 byte. 2–3× smaller than JSON.
Workers receive raw bytes with no string context.

**Execute** — workers run the UVM: a sandboxed, deterministic stack machine.
Same input always produces the same output. Output validity is the proof.

**Decode** — you use your private schema to interpret the raw integers.

```
You                                    Worker
───                                    ──────
Source code
  ↓ compile
Integer stream + Schema  →→→→→→→→   Binary blob
  │                                      ↓ UVM executes
  │ Schema stays private            Raw integers
  ↓                       ←←←←←←←←      ↓ return
Decode with schema
= meaningful result
```

This is not encryption. Overhead is ~1–5% — the cost of VM interpretation only.
Privacy comes from compilation: variable names, structure, and intent are stripped.

---

## Quick start

**Private cluster** — your own machines, no payment:

```bash
git clone https://github.com/sangharshadhyeta/Unbound && cd Unbound
pip install -e .

# Coordinator
unbound cluster node

# Each worker machine
unbound cluster mine --server ws://coordinator:8765

# Submit a job
unbound cluster run examples/hello.py
# Results: [45]
```

**Public network** — open to any worker, pay per chunk:

```bash
# Node
unbound node

# Miner (any machine)
unbound mine --id miner1

# Fund and submit
unbound faucet alice --amount 1000
unbound submit examples/hello.py --from alice --payment 100
unbound result <job_id> --wait
# Results: [45]
```

`examples/hello.py` is `print(sum(range(10)))`. The worker ran it and returned `[45]`
without knowing it was summing a range.

---

## Private cluster in depth

If you have a heterogeneous cluster — CPU nodes, a GPU node, a high-memory node —
running one logical job across all of them today requires MPI, SLURM, Kubernetes, or Ray.
Each requires shared filesystems, cluster-wide configuration, and upfront topology knowledge.

With Unbound cluster mode:
- Each machine runs one command — no configuration, no shared filesystem
- New machines join by starting the worker daemon — no cluster reconfiguration
- The coordinator dispatches chunks to whoever is available
- Workers with more capacity naturally complete more chunks
- You see one job, one result — the coordinator handles everything else

Schema separation is as useful internally as on the public network. In a multi-department
cluster, one department's jobs run on another department's hardware without either
knowing what the other is computing.

---

## Public network — Bitcoin integration

The public network overlays on Bitcoin without modifying any existing node or miner.

**Layer 1 — Unknowing (every Bitcoin miner, right now)**

Job data is embedded in `OP_RETURN` fields of standard Bitcoin transactions. Bitcoin
miners include them for fees. They see bytes. The Bitcoin chain becomes Unbound's
permanent job ledger.

```
OP_RETURN: UBD:1:<job_id>:<program_cid>:<data_cid>
```

**Layer 2 — Passive (pool operators)**

A pool plugin runs on pool servers and executes UVM chunks on idle CPU. ASICs continue
mining SHA-256 unchanged. Pool operators earn UBD from CPU cycles that were already idle.

**Layer 3 — Active (any machine)**

The Unbound daemon runs on any Linux machine: a dedicated server, a cloud VM, or the
idle ARM control board of an ASIC miner. No firmware change. One background process.

```
Bitcoin miners:   BTC (unchanged) + UBD transaction fees (automatic)
Pool operators:   existing BTC revenue + UBD from idle pool server CPU
Active workers:   full UBD per completed chunk
```

No new hardware. No hard fork. Bitcoin's existing infrastructure works for you.

---

## SDK

```python
# Public network
from unbound.sdk import UnboundClient
client = UnboundClient("http://localhost:8000", address="alice")
results = client.run("print(sum(range(10)))", payment=10)  # → [45]

# Private cluster
from unbound.sdk import ClusterClient
client = ClusterClient("http://coordinator:8000")
results = client.run("print(sum(range(10)))")              # → [45]

# Search job abstractions (both modes)
from unbound.sdk import DataParallelJob, RangeSearchJob, MinimizeJob, GradientEstimator
```

See `examples/search/` for patterns: data parallel, prime search, data analysis,
linear regression via distributed gradient estimation, function optimization, and
private computation via additive masking.

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
    │Worker A│  │Worker B│  │Worker C│  ← see only integer streams
    │  UVM   │  │  UVM   │  │  UVM   │  ← know nothing about intent
    └────────┘  └────────┘  └────────┘
```

**Components:**
- `uvm/` — stack machine, 30+ opcodes, LEB128 encode/decode
- `compiler/` — Python subset → UVM stream + Schema
- `registry/` — chunk lifecycle: pending → assigned → completed → reassigned
- `ledger/` — UBD balances and escrow in SQLite (network mode only)
- `chain/` — Proof of Useful Work consensus, tamper-evident block chain (network mode only)
- `network/` — WebSocket server dispatching binary chunk frames
- `api/` — FastAPI REST: `/jobs`, `/compile`, `/balance`, `/health`
- `sdk/` — `UnboundClient`, `ClusterClient`, search job abstractions

---

## Binary Encoding (LEB128)

Same scheme as WebAssembly. Each integer uses the minimum bytes required.

| Value range | Bytes |
|---|---|
| Opcodes 0–127 (most ops) | 1 byte |
| Small addresses / counters | 1 byte |
| 128–16383 | 2 bytes |

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
- Research teams needing distributed compute — protein folding, ML, optimization
- HPC operators wanting to aggregate heterogeneous nodes without MPI/Kubernetes
- Early miners for the public network — run the daemon, earn UBD
- Pool operators — one plugin install, UBD from idle pool server CPU
- Grant applications in progress — EF ESP, Gitcoin, Filecoin

See [WHITEPAPER.md](WHITEPAPER.md) for the full protocol specification.

---

## Stack

Python 3.13 · FastAPI · SQLite · asyncio + websockets · Click · MIT License

---

## Contributing

Issues and discussions open. Fork freely — the reference implementation lives here.
If your fork is better, the community finds it. That is how Bitcoin Core works.
