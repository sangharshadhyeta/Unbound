# Unbound

![Unbound](unbound.png)

**A virtual machine for distributed computation where workers never know what they run.**

The UVM is a sandboxed stack machine that executes arbitrary programs represented as
flat integer streams. Workers receive binary blobs, execute them, return integers. The
submitter holds the private schema that gives those integers meaning. This separation —
execution without comprehension — is the core innovation.

Two ways to use it:

| | **Public network** | **Private cluster** |
|---|---|---|
| Workers | Anyone running the daemon | Your own machines |
| Payment | UBD token, per chunk | None |
| Setup | Node + miner daemon | One command per machine |
| Capability tags | Workers declare tags, coordinator routes | Same |
| Use case | Open compute market | HPC aggregation, internal jobs |

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
# Integer-only miner (CPU / embedded)
unbound mine --id my-miner

# GPU miner — declares capability and pipeline depth for continuous dispatch
unbound mine --id gpu-miner --capability float --capability gpu --pipeline-depth 4
```

Any machine can participate: run the daemon, receive binary chunks, execute the UVM,
return integers. You never see what you are computing. The submitter interprets the results.

GPU miners declare `pipeline_depth` to keep their hardware continuously fed — the node proactively dispatches up to `pipeline_depth` chunks so the GPU never waits for a round-trip.

---

## How it works

**Compile** — any program compiles to two artifacts:

```python
from unbound.compiler.compiler import compile_source

stream, schema = compile_source("print(sum(range(10)))")
# stream → flat integer list  (transmitted to workers)
# schema → variable map       (stays private, never transmitted)
```

**Mask** — sensitive input values are masked before submission using Arithmetic Mask
Propagation. Each input gets a unique, key-derived additive offset. The worker sees
only masked integers; you correct the output afterward to recover exact real results.

```python
from unbound.masking import AMPMasker

masker = AMPMasker(master_key)
plan   = masker.prepare(stream, real_inputs, job_id="job-001")
# plan.masked_inputs  → send to workers instead of real values
# plan.correct(out)   → recover exact results from worker output
```

**Transmit** — the stream is LEB128-encoded (same format as WebAssembly).
Opcodes and small values encode to 1 byte. 2–3× smaller than JSON.
Workers receive raw bytes with no string context.

**Execute** — workers run the UVM: a sandboxed, deterministic stack machine.
Same input always produces the same output. Output validity is the proof.

**Decode** — you apply output corrections and use your private schema to interpret results.

```
You                                         Worker
───                                         ──────
Source code
  ↓ compile
Integer stream + Schema (private)
  ↓ mask (AMP)
Masked stream + MaskPlan  →→→→→→→→→→   Binary blob
  │                                         ↓ UVM executes
  │ Schema + MaskPlan stay private     Masked integers
  ↓                        ←←←←←←←←←←      ↓ return
Correct → Decode with schema
= exact meaningful result
```

Three independent privacy layers protect your data:

1. **Schema separation** — semantic privacy. Variable names, structure, and intent
   are stripped during compilation. A worker receiving the binary blob cannot recover
   what the numbers *mean*.

2. **Arithmetic Mask Propagation** — numeric privacy. Each input value is additively
   masked with a unique, key-derived integer. Masks propagate algebraically through
   ADD, SUB, MUL, NEG, and DIV-by-constant — the worker's masked outputs can be
   corrected to exact real values. No FHE noise, no overhead beyond one HMAC per
   input on your machine. The innovation: standard additive masking, extended to
   propagate through multiplication via quadratic cross-product correction.

3. **Dispersal privacy** — structural privacy. For a job split into n chunks, any
   coalition of m workers can learn at most (m/n) of the total input information.
   At n = 100 chunks, one worker learns ≤ 1% — before masking is applied.

---

## Arithmetic Mask Propagation — numeric privacy in depth

The complement-arithmetic intuition draws from the Vedic sutra *Nikhilam
Navatashcaramam Dashatah*. AMP extends that principle into a general algebraic
propagation rule over a prime field.

Each `INPUT` value `v` gets a fresh mask `r` derived from your master key:

```
masked = (v + r) mod M        # worker sees this
```

Masks propagate exactly through arithmetic. For a multiply `a * b`:

```
(a + ra)(b + rb) = ab  +  (a·rb + b·ra + ra·rb)
                   ──        ──────────────────
                real       correction (known to submitter)
```

After the worker returns, subtract the correction to get exact results — no rounding, no noise.

**What operations are supported:**

| Operation | Support |
|---|---|
| ADD, SUB, NEG | Full (linear correction) |
| MUL | Full (quadratic cross-product correction) |
| DIV by public constant | Full |
| Chained MUL/ADD (polynomials, dot products) | Full |
| Comparison / branch on masked value | Rejected (raises `MaskError`) |
| Float ops | Rejected (separate extension needed) |
| Array ops (ILOAD, ISTORE, VSUM, VDOT) | Full (vector mask propagation) |
| Float programs (FCONST, FADD, etc.) | Via FixedPointMasker (scale to integers, mask, descale) |

**Degree-1 linearisation (Beaver triples).** Multiplication between two masked SECRET values is degree-2: the correction involves a cross-product term. For branch-free programs, the mask compiler automatically detects every SECRET × SECRET MUL, precomputes the masked product, and emits a `linearised_stream` where MUL is replaced with `POP, POP, PUSH <constant>`. Miners executing the linearised stream perform only degree-1 operations — additions and PUBLIC-constant × masked-value multiplications — which map directly to BLAS operations on GPU hardware. `plan.degree2_muls` is zero when the program is already uniformly degree-1 (all MULs have at least one PUSH-literal operand), in which case no transformation is needed.

**Security model:** Security comes from the secrecy of the master key `K`, not randomness.
Given `K` and `job_id`, masks are deterministic — reproduce them to correct any job without
storing anything. For production, derive `K` from a passphrase using `SchemaVault`:

```python
from unbound.masking import SchemaVault

vault = SchemaVault.from_passphrase("my secret phrase", "job/program.schema")
plan  = vault.prepare(stream, inputs, job_id="job-001")
# K is derived via PBKDF2-SHA256 (600k iterations), never written to disk
# vault cannot be pickled — K never leaves the submitter's process
```

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

## Public network

Unbound is a standalone Proof of Useful Work network. No hash puzzles. Pay per verified
chunk. Workers declare capability tags on startup — the coordinator routes matching
chunks to them:

```bash
unbound mine --capability gpu --capability high-memory --pipeline-depth 4
```

Chunks are content-addressed (CID): workers who have already cached a dataset get
routing priority, reducing redundant data transfer across the network.

```
Any machine:   UBD per completed chunk (proportional to compute contributed)
```

The protocol is designed to allow optional integration with existing mining
infrastructure. Pool operators and miners who wish to contribute compute can do so
by running the miner daemon alongside their existing setup — participation is purely
additive and requires no changes to their primary operation. Anchoring of result
hashes to external ledgers for audit purposes is also supported as an optional
deployment choice.

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
- `masking/` — AMP: `KeyDeriver`, `MaskCompiler`, `AMPMasker`, `SchemaVault`
- `masking/beaver.py` — Beaver triple generation; degree-2 → degree-1 identity
- `masking/fixedpoint.py` — Float masking via FixedPointMasker (scale → int → mask → descale)
- `verifier/verifier.py` — k-of-2 result agreement (exact integers; float tolerance via epsilon)
- `registry/` — chunk lifecycle: pending → assigned → completed → reassigned
- `ledger/` — UBD balances and escrow in SQLite (network mode only)
- `chain/` — Proof of Useful Work consensus, tamper-evident block chain (network mode only)
- `network/` — WebSocket server dispatching binary chunk frames with CID routing
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
| FCONST / FADD / FSUB / FMUL / FDIV / FNEG / ITOF / FTOI | 60–68 | Floating-point operations |
| ILOAD / ISTORE | 70–71 | Array element read / write (base + dynamic index) |
| VSUM / VDOT | 72–73 | Vectorised sum and dot product over memory arrays |

---

## Tests

```bash
pytest tests/ -v
# 303 tests, all passing
```

---

## Current Status

Prototype complete. Working demo. 303 passing tests.

**Recently added:** array/tensor primitives (ILOAD, ISTORE, VSUM, VDOT), float masking (FixedPointMasker), k-of-2 verification with slash-on-disagreement, Beaver triple linearisation (degree-2 → degree-1 for GPU), PUBLIC/SECRET MUL classification, and GPU pipeline depth dispatch.

**Seeking:**
- Research teams needing distributed compute — protein folding, ML, optimization
- HPC operators wanting to aggregate heterogeneous nodes without MPI/Kubernetes
- Early miners for the public network — run the daemon, earn UBD
- Grant applications in progress — EF ESP, Gitcoin, Filecoin

See [WHITEPAPER.md](WHITEPAPER.md) for the full protocol specification.

---

## Stack

Python 3.13 · FastAPI · SQLite · asyncio + websockets · Click · MIT License

---

## Contributing

Issues and discussions open. Fork freely — the reference implementation lives here.
