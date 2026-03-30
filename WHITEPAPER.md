# Unbound: Proof of Useful Work via Blind Execution, Arithmetic Mask Propagation, and Search Problem Unification

**Abstract**

Distributed computation has a trust problem. Every existing system for running programs
across untrusted workers — cloud, SLURM, BOINC, Golem — requires giving those workers
plaintext access to the code and data being computed. Sensitive computation cannot be
distributed to untrusted infrastructure; the workers must be trusted first. This trust
barrier walls off the vast pool of idle compute — personal machines, spare server
capacity, underutilized GPU rigs — from work that matters.

We present Unbound, a protocol that dissolves this barrier through a combination of
mechanisms that together allow workers to execute arbitrary programs without ever
knowing what they run. The first is the *Unbound Virtual Machine* (UVM): a sandboxed
stack machine that accepts programs as flat integer streams with no semantic content —
no variable names, no intent, no data schema. Workers execute opaque binary blobs and
return integers. The second is *schema separation*: programs compile to two artifacts,
a UVM stream transmitted to workers and a private schema held only by the submitter,
mapping stream positions to meaning. Workers see only computation; meaning remains
with the submitter. The third is *Arithmetic Mask Propagation* (AMP): each input
value is additively masked by a key-derived offset before leaving the submitter's
machine, and those masks propagate algebraically through the full computation —
including multiplication, where a quadratic cross-product correction allows the
submitter to recover exact outputs without any noise, approximation, or trusted
hardware. Together, schema separation and AMP form a two-layer privacy model: workers
cannot recover the semantic meaning of a computation (schema) nor the numeric values
of its inputs (masking). A third structural property emerges from the architecture
itself: jobs are chunked and dispatched across independent workers, giving a formal
dispersal bound — for a job split into n chunks, any coalition of m miners can learn
at most m/n of the total input information. We further show that all computation —
including probabilistic programs such as ML training — can be expressed as structured
search problems where every worker attempt maps real solution-space territory,
eliminating wasted effort entirely. Unbound operates as an independent network; no
modification to any existing infrastructure is required. Existing proof-of-work
networks such as Bitcoin currently waste approximately 150 TWh of electricity per
year on SHA-256 hash puzzles that produce nothing of value beyond the blocks
themselves; Unbound's Proof of Useful Work mechanism addresses both the trust barrier
and the wasted compute in a single architecture.

---

## 1. Introduction

### 1.1 The Trust Problem

Every system for distributed computation requires trusting the workers with what they
run. A researcher submitting a protein folding job to a cloud provider exposes both
the algorithm and the input data to the provider's infrastructure. A company running
ML training on a SLURM cluster must trust every node operator not to read the model
weights and training data. BOINC volunteers can inspect every program they execute.
Golem workers receive plaintext task definitions. In every case, the worker knows
what they compute.

This trust requirement is not a minor inconvenience — it is a structural barrier that
excludes distributed computation from an enormous class of sensitive workloads. Drug
discovery pipelines, proprietary ML models, financial risk calculations, genomics
research, and national security computation cannot be delegated to untrusted
infrastructure regardless of how cheap or abundant that infrastructure is. The idle
CPU cycles on a hundred thousand personal machines, the spare capacity of
underutilized server farms, the GPU rigs whose host processors sit idle — all of
this compute exists. It cannot be used because using it means trusting it.

Unbound removes this barrier. Workers receive binary blobs. They execute them. They
return integers. Without the private schema the submitter holds, those integers have
no recoverable meaning. Without the private masking key, the input values are
indistinguishable from random field elements. A worker learns that a computation
happened. Nothing else.

### 1.2 The Waste Dimension

A second problem exists alongside the trust barrier. Existing proof-of-work networks
— most prominently Bitcoin, which processes approximately 500 ExaHash per second as
of 2026 — expend vast compute resources on hash puzzles that produce nothing of value
beyond the blocks themselves. For every valid Bitcoin block found, roughly 10²¹
SHA-256 computations are discarded. The electricity consumed exceeds the annual
consumption of many mid-sized nations.

This is not a design flaw in Bitcoin — the waste is the security mechanism. Blocks
are trustworthy precisely because they are expensive to produce. But the waste is
real, and the insight that motivates Proof of Useful Work is that the *economic
structure* of mining — pay workers for verifiable output — does not require the
*work itself* to be useless. The same incentive mechanism that drives global
investment in mining infrastructure can drive global investment in computation
that matters.

Both problems — the trust barrier and the wasted compute — are solved by the same
mechanism: a runtime in which arbitrary programs execute blindly, verifiably, and
without any worker gaining knowledge of what they computed.

### 1.3 Prior Attempts

The observation that mining energy could be redirected is not new.

**Primecoin** (King, 2013) made miners search for Cunningham chains of prime numbers.
Every mining attempt checked the primality of a candidate — genuinely useful
mathematical work. But Primecoin solves only one problem type, and miners always know
they are searching for primes.

**Gridcoin** rewards miners for running BOINC distributed computing tasks (protein
folding, pulsar discovery, climate modeling). But participation is voluntary and
separate from the PoW mechanism — miners are not required to do useful work to earn
block rewards.

**Ofelimos** (Fitzi et al., 2022) introduces Doubly Parallel Local Search (DPLS) as
a PoW primitive, enabling miners to solve combinatorial optimization problems as their
mining work. This is the most rigorous PoUW construction to date. However, miners know
what problem they are solving, the system requires a standalone blockchain, and the
problem class is limited to DPLS-compatible optimization.

**TrueBit** provides a verification market for off-chain computation on Ethereum.
Solvers submit results; verifiers can challenge incorrect submissions via a
bisection protocol. But solvers know the task, the system is Ethereum-specific, and
verification games add latency and complexity.

### 1.4 The Gap

No existing system combines:

1. **Blind miner execution** — miners execute programs without knowing what they compute
2. **Numeric input privacy** — miners cannot recover actual data values, not just semantic meaning
3. **Dispersal privacy** — fragmentation gives a formal bound: m colluding miners learn at most m/n of the input
4. **General proof of useful work** — any computation, not just one problem type
5. **Search problem unification** — all computation expressed as structured search,
   ensuring no miner attempt is wasted

Unbound is the first system to combine all five. The second property — numeric input privacy
via arithmetic mask propagation — is a novel contribution distinguishing Unbound from all prior work.

---

## 2. The Unbound Virtual Machine (UVM)

The UVM is a sandboxed stack machine. It is intentionally simple: the simplicity
is what makes blind execution possible. A miner running the UVM needs no context
about what the program means — only whether the execution halts and produces output.

### 2.1 Design Properties

**Deterministic.** The same input always produces the same output. This is the
foundation of verification: two independent miners executing the same chunk must
produce identical results. Any divergence indicates a fault.

**Sandboxed.** The VM has no file system access, no network access, no side effects
beyond its own stack and memory. Miners can execute arbitrary programs safely.

**Verifiable.** Output validity is the proof. If the UVM halts and returns a
non-empty integer list, the chunk is valid. No separate verification step is needed.
The sandbox is the verifier.

**Compact.** All instructions are integers. The instruction set fits in 7 bits
(opcodes 1–99). Programs are flat integer streams with no metadata.

### 2.2 Instruction Set

The UVM has 30+ opcodes across six categories:

| Category | Opcodes | Description |
|---|---|---|
| Stack | PUSH, POP, DUP, SWAP | Stack manipulation |
| Memory | LOAD, STORE | Named memory cells |
| Arithmetic | ADD, SUB, MUL, DIV, MOD, NEG | Integer arithmetic |
| Comparison | EQ, NEQ, LT, LTE, GT, GTE | Returns 1 or 0 |
| Logic | AND, OR, NOT, XOR, SHL, SHR | Bitwise operations |
| Control | JMP, JT, JF | Relative jumps |
| I/O | INPUT, OUTPUT, HALT | Data in/out, termination |
| Float | FCONST, FADD, FSUB, FMUL, FDIV, FMOD, FNEG, ITOF, FTOI | IEEE 754 floating-point arithmetic |
| Array / Vector | ILOAD, ISTORE, VSUM, VDOT | Element-addressed array access and vectorised sum/dot product |

Every instruction is a small integer. A program is a flat list of these integers,
with immediates following instructions that require them.

### 2.3 Execution Model

The VM maintains a stack and a fixed-size memory array. Execution proceeds linearly
through the integer stream. Control flow instructions use relative offsets. The `INPUT`
instruction pops a value from an external input buffer — this is the interface through
which data-parallel jobs provide varying inputs to the same program. `OUTPUT` pushes
a value to the output buffer. `HALT` terminates execution and returns the output buffer.

---

## 3. Schema Separation — The Privacy Model

### 3.1 The Core Mechanism

When a program compiles, two artifacts are produced:

**The UVM stream** — a flat list of integers encoding the program's operations.
This is transmitted to miners. It contains no variable names, no comments,
no semantic labels of any kind. It is pure computation.

**The Schema** — a private map held only by the submitter. It records:
- Variable names and their memory addresses
- Output positions and their semantic meaning
- Source line → stream position mapping (for debugging)
- Total stream length

The Schema is never transmitted. Not to the node. Not to miners. Not to anyone.

### 3.2 What the Schema Hides — and What It Does Not

Schema separation provides **semantic opacity**, not structural opacity. The
distinction matters.

**What a miner can recover.** The UVM instruction set is public. A miner can
write a disassembler and reconstruct the computation structure. For a program
that computes a weighted sum of sensor readings, the disassembly looks like:

```
addr[0] = 1234        ; a constant
addr[1] = 875         ; another constant
addr[2] = addr[0] * addr[1] / 1000
addr[3] = addr[2] + addr[4]
PRINT addr[3]
```

The miner sees: arithmetic operations, integer constants, control flow, program
length. The computation structure is visible.

**What the miner cannot recover.** Without the Schema, there is no way to know:
- That `addr[0]` is named `temperature` and `addr[1]` is `pressure`
- That the division by 1000 is fixed-point scaling, not a literal divisor
- That the output represents a thermodynamic efficiency score
- That the job is drug discovery, not image processing, not financial modeling

The miner knows only that it received integers, executed them, and produced integers.
The semantic domain — what the numbers *mean* — is held entirely by the submitter.
This is not encryption. The privacy comes from compilation: the transformation from
semantically rich source code to a flat integer stream is lossy in exactly the
right direction.

### 3.3 Arithmetic Mask Propagation — Numeric Privacy

Schema separation provides semantic opacity but does not hide the *values* in a
program. A miner who disassembles the UVM stream can read integer constants and
input values verbatim. For workloads where data values are sensitive, an additional
layer is needed.

Unbound introduces **Arithmetic Mask Propagation (AMP)** — a deterministic algebraic
privacy scheme in which each input value is replaced with an additive offset before
leaving the submitter's machine, and those offsets propagate exactly through the
full computation. Additive masking over a finite field is a well-known primitive;
the contribution here is the algebraic propagation rule for multiplication, which
generates a quadratic cross-product correction that the submitter computes locally,
enabling exact output recovery from a blind evaluator without interaction, noise,
or trusted hardware.

The additive complement intuition draws from the Vedic arithmetic sutra
*Nikhilam Navatashcaramam Dashatah* (All from 9, last from 10), which formalises
complement-based reduction. AMP extends that principle into a general algebraic
propagation rule over a prime field, covering linear and quadratic operations.

#### 3.3.1 Core Mechanism

Let `M` be a large prime (Unbound uses the Ed25519 field prime, `2²⁵⁵ − 19`).
Each `INPUT` value `v` is replaced with:

```
masked = (v + r) mod M
```

where `r` is a *mask* derived deterministically from a master key `K`:

```
r_i = HMAC-SHA256(K, job_id:i) mod M
```

The counter `i` increments with each mask consumed, giving each operation a fresh
independent mask. Masks are deterministic given `K` and `job_id` — the submitter
does not need to store them.

#### 3.3.2 Algebraic Mask Propagation

The submitter dual-simulates the UVM: one simulation with real values, one with
masks. For each opcode, the mask of the output is computed algebraically from the
masks and real values of the inputs. The **fundamental invariant** is:

```
miner_stack[i] ≡ real_stack[i] + mask_stack[i]  (mod M)
```

This invariant is preserved exactly through:

**Addition / Subtraction:**
```
miner sees:  (a + ra) + (b + rb)  =  (a + b)  +  (ra + rb)
correction:  ra + rb
```

**Multiplication (quadratic cross-product correction):**
```
miner sees:  (a + ra)(b + rb)  =  ab  +  (a·rb + b·ra + ra·rb)
                                   ──     ─────────────────────
                                  real         correction
```
The correction term `a·rb + b·ra + ra·rb` is computable by the submitter because
`a`, `b`, `ra`, and `rb` are all known locally. The miner never sees them — it only
receives masked integers and a binary blob to execute.

**Negation:**
```
miner sees:  -(a + ra)  =  -a  +  (-ra)
correction:  -ra
```

**Division by public constant `d`:**
```
miner sees:  (a + ra) / d  ≈  a/d  +  ra/d
correction:  ra / d    (exact when d divides ra; approximate otherwise)
```

#### 3.3.3 Output Recovery

At each `OUTPUT` instruction, the submitter records the mask of the output slot as
a *correction*. After the miner returns results:

```
real_output_i = (miner_output_i − correction_i) mod M
```

Values above `M/2` are interpreted as negative integers (two's complement in the
prime field), ensuring that `−7` returns as `−7` rather than `M − 7`.

#### 3.3.4 Supported and Unsupported Operations

Mask propagation works for operations where the correction is computable from public
information. Some operations cannot be corrected:

| Operation | Support | Reason |
|---|---|---|
| ADD, SUB, NEG | Full | Linear correction |
| MUL | Full | Quadratic cross-product correction |
| DIV by public constant | Full | Correction ÷ same constant |
| Chained MUL + ADD (polynomials, dot products) | Full | Correction propagates |
| STORE / LOAD (memory) | Full | Mask propagates through memory |
| DUP | Full | Mask duplicated with value |
| Comparison (EQ, LT, GT, …) on masked value | **Rejected** | Boolean result would be wrong; miner branches incorrectly |
| Bitwise logic (AND, OR, XOR) on masked value | **Rejected** | Not algebraically correctable |
| DIV / MOD with masked divisor | **Rejected** | Divisor is secret; quotient uncorrectable |
| Data-dependent branch (JT/JF) on masked condition | **Rejected** | Miner would take wrong branch |
| Float opcodes | **Rejected** | Float precision breaks the correction invariant |
| Array ops (ILOAD, ISTORE) with public index | Full | Public index — element mask propagates |
| VSUM | Full | Sum of element masks (linear) |
| VDOT | Full | Quadratic cross-product over element pairs |
| Float programs via FixedPointMasker | Full | Scale to integers, mask, descale output |

Rejected operations raise `NikhilamError` at compile time — before any data leaves
the submitter's machine. Programs are validated against these constraints during
mask compilation.

#### 3.3.5 Key Derivation and Security

**Per-operation, per-job derivation.** Each operation gets a fresh mask derived as:

```
r = HMAC-SHA256(K, f"{job_id}:{counter}")
```

The counter is a monotonic integer scoped to the job. Compromising one mask reveals
one input value only; it does not compromise other inputs, other jobs, or `K` itself.

**Per-job isolation.** The same data submitted under two different `job_id` values
produces two completely different masked streams. Cross-job correlation analysis is
infeasible.

**Security basis.** Security rests on the secrecy of `K`, not on randomness. Given
`K` and `job_id`, the entire mask sequence is deterministic — the submitter can
reproduce any correction without storing anything. The scheme is not IND-CPA secure
in the formal sense (a submitter who reuses the same `K` and `job_id` for different
data leaks the *difference* of those values). In practice this is prevented by using
a fresh `job_id` per submission, which is enforced by the protocol.

**Known limitation: order and range.** The masking is additive mod `M`. A miner who
observes many masked values for the same input position across multiple jobs — and who
knows that all real values lie in a small range — can narrow the range with statistical
analysis. For workloads with highly repetitive, range-restricted inputs, composing
arithmetic mask propagation with random jitter input noise (via extra `INPUT` slots whose values
are discarded by the program) provides an additional defense.

#### 3.3.6 SchemaVault — Key Management

The master key `K` is the single secret protecting all masked jobs. The
`SchemaVault` class provides a sealed container for `K` and the program schema:

- `K` is derived from a passphrase via PBKDF2-SHA256 (600,000 iterations) and held
  inside the object with no public accessor
- The salt defaults to `SHA256(abs_schema_path)`, so the same passphrase used for
  two different programs produces two independent keys
- Pickle serialisation is blocked: `__reduce__` raises `TypeError`, preventing `K`
  from accidentally leaving the submitter's process
- `__slots__` prevents ad-hoc attribute injection

#### 3.3.7 Degree-1 Linearisation via Beaver Triples

**The degree problem.** Arithmetic mask propagation preserves the fundamental
invariant through multiplication, but with a subtlety: the correction term
`a·rb + b·ra + ra·rb` depends on both real values *and* masks. When both operands
are secret inputs (`INPUT × INPUT`), the miner's computation is *degree-2* — the
output mask is a quadratic function of two independent masks. This is correct and
recoverable, but for a GPU executing many operations in parallel, degree-2 paths
require the submitter's corrections to arrive in the right sequence.

**Beaver triples.** A *Beaver triple* `(u, v, w)` satisfies `w = u·v mod M`. Given
such a triple and two masked values `masked_a = a + ra`, `masked_b = b + rb`, define:

```
e = masked_a − u  (mod M)
f = masked_b − v  (mod M)
```

Then:

```
e·f + e·v + f·u + w  ≡  masked_a · masked_b  (mod M)
```

This identity is provable by expanding: `(masked_a − u)(masked_b − v) + (masked_a − u)·v +
(masked_b − v)·u + u·v = masked_a · masked_b`. Every term on the left is degree-1
in the original masked values — no product of two unknowns appears. The submitter
computes `masked_a · masked_b` directly (having both values), substitutes the result as
a `PUSH` constant, and replaces the `MUL` opcode in the stream with `POP, POP, PUSH
<precomputed_product>`. The miner executes only linear operations.

**Linearised stream.** For branch-free programs containing `SECRET × SECRET`
multiplications, the mask compiler generates a *linearised stream*: an alternate
version of the stream in which each degree-2 `MUL` is replaced with its precomputed
masked product. The miner executes this stream instead of the original. The submitter
recovers the real result using the same correction it would have applied to the
original stream — the output mask is identical.

Branch-free programs (`beaver_ok = True`) get a linearised stream automatically.
Programs with branches are dispatched as-is (the quadratic correction still works;
linearisation is a throughput optimisation, not a correctness requirement).

**Effect on GPU execution.** With linearisation, a GPU miner executing a dot product
across a million pairs sees only `ADD` and scalar-multiply opcodes — operations that
map directly to BLAS Level 1 SAXPY/DAXPY calls. No degree-2 correction sequencing
is required at the miner. The miner is a pure arithmetic accelerator; all masking
intelligence stays on the submitter's machine.

### 3.4 Three-Layer Privacy Model

The full privacy model has three independent layers:

| Layer | Mechanism | What it hides |
|---|---|---|
| 1 — Semantic | Schema separation | Variable names, intent, computation purpose |
| 2 — Numeric | Arithmetic mask propagation | Actual input values |
| 3 — Dispersal | Chunked distribution | Bounded fraction of total input per miner |

Layers 1 and 2 are cryptographic properties of the submission protocol. Layer 3
is a structural property with a formal information-theoretic bound.

**Dispersal Privacy Bound.** Let X be the full input to a job, split into n equal
chunks distributed across n independent miners. For any single miner j holding
fragment X_j:

```
I(X ; X_j) ≤ H(X) / n
```

For any coalition of m colluding miners holding fragments X_{j₁}, …, X_{jₘ}:

```
I(X ; X_{j₁}, …, X_{jₘ}) ≤ (m / n) · H(X)
```

where H(X) is the Shannon entropy of the full input distribution and I denotes
mutual information. Privacy improves with job granularity: doubling n halves the
maximum leakage per miner. For a job with n = 100 chunks, a single miner learns
at most 1% of the total information — before Layer 2 masking is applied. With
masking active, the m/n fraction a coalition receives is itself masked, reducing
effective leakage toward zero.

This bound follows directly from the additivity of mutual information and the
fragment independence guaranteed by the dispatch model, and mirrors the
information-theoretic security argument of Rabin's Information Dispersal Algorithm
(1989).

The threshold 1/n is a deployment parameter. Operators choose a *privacy threshold*
— the maximum acceptable leakage fraction — which determines the minimum job size
and the pipeline-depth cap for their network. Public deployments default to 12.5%
(n ≥ 8 chunks); private clusters where all nodes are trusted may disable the
structural constraint entirely. See §8.4 for the full threshold table.

### 3.5 Overhead

Standard encryption (AES, RSA) imposes 10–100% overhead. FHE (Fully Homomorphic
Encryption), which allows computation on ciphertext, currently imposes 1,000–10,000×
overhead and remains impractical for general computation. Secret sharing requires
multiple cooperating parties.

Unbound's schema separation imposes approximately 1–5% overhead — the cost of VM
interpretation only. The miner executes native integer operations. There is no
encryption to perform, no ciphertext to manage, no key to distribute.

Arithmetic mask propagation adds negligible submitter-side overhead: one HMAC and one modular
addition per input, one modular subtraction per output, all executed locally before
and after the network round-trip. No overhead is added to the miner's computation.

The privacy model is weaker than FHE for programs with comparisons and branches on
sensitive data (those operations are rejected). For the primary use cases — polynomial
computations, dot products, weighted sums, gradient estimation — arithmetic mask propagation
provides exact numeric privacy at near-zero cost.

---

## 4. LEB128 Binary Encoding

UVM streams are transmitted as LEB128-encoded bytes — the same variable-length
encoding used by WebAssembly.

### 4.1 The Encoding

LEB128 (Little Endian Base 128) encodes each integer using the minimum number of
bytes required. Each byte carries 7 bits of data; the high bit is a continuation
flag (1 = more bytes follow, 0 = last byte).

Opcodes use unsigned LEB128 (always non-negative). Immediates (PUSH values, jump
offsets) use signed LEB128 (handles negative jump offsets in backward loops).

| Value range | Bytes required |
|---|---|
| 0 – 127 (all opcodes, small addresses) | 1 byte |
| 128 – 16,383 | 2 bytes |
| −64 – 63 (signed, for jump offsets) | 1 byte |

### 4.2 Why This Matters

A UVM program consists mostly of small integers: opcodes (1–99), memory addresses
(typically 0–15 for a small program), loop counters, and jump offsets. Nearly all
of these encode to 1 byte.

The result is that LEB128-encoded UVM programs are 2–3× smaller than the equivalent
JSON representation and approach the information-theoretic minimum for the data. This
directly reduces network bandwidth consumed per chunk dispatch and result return.

More importantly, the encoding strips all ASCII context from the program. A JSON
representation would contain printable characters that might give a sophisticated
miner hints about the program's structure. LEB128 binary is opaque bytes — the
same bytes whether the program searches for primes or evaluates neural network
activations.

---

## 5. Search Problem Unification

### 5.1 All Computation as Search

Every computable function `f(x) = y` can be restated as a search problem:

> Find `y` such that `verify(x, y) = true`

Verification is always at least as easy as computation (P ⊆ NP). For most practical
problems, verification is significantly cheaper — checking a solution is easier than
finding one. This asymmetry is precisely what makes distributed search economical:
many workers search in parallel, and verification is cheap once a candidate is found.

This means that any computation a user wants to perform can be expressed as a UVM
program that:

1. Takes a candidate input via `INPUT`
2. Evaluates the candidate
3. Outputs a score or boolean result via `OUTPUT`

Miners search over the candidate space, each evaluating one candidate per chunk.
The pool collects all evaluations. The aggregated evaluations constitute the
useful work product — whether or not any individual evaluation wins a block reward.

### 5.2 Application to Machine Learning

Gradient descent for ML training is not obviously a search problem — it uses
gradient information to navigate the loss landscape efficiently. But gradient descent
is itself a search: it searches for weights `W` such that `loss(W, data) < ε`.

**Gradient-free formulation.** Given current weights `W`, estimate the gradient by
finite differences: for each basis direction `e_i`, evaluate `loss(W + δ·e_i, data)`.
The gradient in direction `i` is approximated as `(loss(W + δ·e_i) - loss(W)) / δ`.

Each miner evaluates one direction. The pool aggregates all directions into a full
gradient estimate. This is **distributed finite-difference gradient estimation** —
equivalent to backpropagation in expectation, using search instead of calculus.

This approach (Evans Strategies / OpenAI ES) has been demonstrated to train
non-trivial neural networks without backpropagation. Every miner evaluation
contributes to the gradient estimate. No evaluation is discarded.

**Hyperparameter search.** Finding optimal learning rate, architecture, and
regularization is a search problem by nature. Each miner runs one training configuration.
The pool builds a Gaussian Process over the hyperparameter space — Bayesian optimization
at massive scale, with every evaluation contributing.

### 5.3 Why Structure Matters

Bitcoin's search space is structureless by design. SHA-256 outputs are uniformly
distributed — no evaluation of `SHA256(header + nonce_i)` tells you anything about
`SHA256(header + nonce_j)`. Every failed attempt is truly wasted.

Useful search spaces have structure. The energy landscape of a protein has gradients.
The loss landscape of a neural network has smooth regions. The prime distribution has
patterns. Failed evaluations in structured spaces are not wasted — they map territory
that the next search round can exploit.

In Unbound, the pool coordinator uses failed evaluations to direct future candidate
generation. The entire network collectively navigates the solution space. Finding a
block is incidental to the real work of exploration.

---

## 6. Cooperative Mining — No Lottery

### 6.1 The Lottery Problem

Bitcoin's payment model creates perverse incentives. Only the miner who finds a valid
block is paid. For a small miner, the expected time between payouts is measured in
years. Mining pools exist precisely to smooth this variance — pooling hashrate and
splitting rewards. But the underlying mechanism is still a winner-takes-all lottery.

The lottery structure has a deeper problem: it means the mining network is oriented
toward one thing — finding the winning nonce — and everything else is overhead.

### 6.2 Pay Per Computation

Unbound replaces the lottery with direct payment per computation completed.

A job submitter locks payment in escrow upfront. The payment is divided equally among
chunks. When a chunk is verified complete, its share of the escrow is released to the
miner who completed it. No lottery. No variance. Predictable income proportional to
compute contributed.

### 6.3 Verification Without Trust — k-of-2 Agreement

Without a hash puzzle, how do we know a miner didn't fake its result?

The UVM is deterministic: the same input always produces the same output. If two
independent miners execute the same chunk and return the same result, the result
is almost certainly correct. The probability of two independent miners both
producing the same incorrect output by chance is negligible.

The protocol assigns each chunk to exactly 2 miners simultaneously, without either
miner knowing the other is working on the same chunk. If both return identical results,
the chunk is verified. If they disagree, both are flagged and the chunk is reassigned.

```
Chunk assigned to:  Miner A  and  Miner B  (neither knows about the other)

Case 1:  A returns [42, 7]    B returns [42, 7]   → agree → verified → both paid
Case 2:  A returns [42, 7]    B returns [99, 0]   → disagree → reassign → neither paid
```

This is Byzantine fault tolerance applied to a deterministic computation. Two
independent agreeing miners is the proof of correctness. No cryptographic puzzle needed.

### 6.4 Job Completion as the Settlement Event

In Bitcoin, the settlement event is block discovery — a random, unpredictable moment.
In Unbound, the settlement event is job completion — deterministic, predictable,
triggered when the last chunk is verified.

When all N chunks of a job complete, the full escrow is released and distributed.
Job submitters know exactly when they will have results: when the last chunk returns.
Miners know exactly what they will earn: chunk reward × number of chunks completed.

---

## 7. Network Architecture

### 7.1 Standalone Design

Unbound operates as an independent network. It does not require Bitcoin or any
existing blockchain infrastructure. The consensus mechanism — Proof of Useful Work
— is self-contained: blocks are produced when verified chunk results are submitted,
not by solving hash puzzles. The ledger, escrow, and payment settlement are all
native to Unbound.

Any machine running Linux can participate as a miner: dedicated servers, cloud VMs,
or GPU rigs whose host CPU is underutilized. The miner daemon connects to an Unbound
node via WebSocket, receives binary chunk frames, executes the UVM, and returns
results for UBD payment per chunk.

### 7.2 Optional Anchoring

For deployments that require an external source of timestamping or finality,
Unbound result hashes can be anchored to any external ledger via its data-embedding
mechanism. This is an optional deployment choice, not a protocol requirement.
The core network functions with full integrity without any external anchor.

The precedent for embedding structured protocol data inside an existing chain's
transaction fields was established by Omni Layer (Willett, 2012), which built the
USDT token protocol on Bitcoin's `OP_RETURN` output without any modification to
Bitcoin itself. Counterparty followed with the same approach for asset issuance.
Both demonstrated that a complete protocol can live as a data layer above a chain
it does not control. Unbound's optional anchoring draws on this pattern — but
applied to any chain, not one in particular, and as an audit trail rather than a
consensus dependency.

---

## 8. Economic Model

### 8.1 UBD Token

UBD (Unbound) is the unit of account for computation on the network.
Job submitters pay UBD for computation. Miners earn UBD for verified results.

### 8.2 Escrow and Payment Flow

```
1. Submitter calls POST /jobs with binary chunks and locks N UBD in escrow
2. N UBD is divided equally among chunks: reward = N / total_chunks
3. Each chunk is assigned to 2 miners
4. When both miners agree on a result: each receives reward / 2
5. When all chunks complete: job is marked done, all escrow disbursed
6. No valid result: chunk is reassigned, no payment for the failed attempt
```

No UBD is created by the network. The only source of miner income is job payment
from submitters. Tokens flow only when real work completes. There are no empty blocks,
no inflation, no mining subsidy decoupled from useful computation.

### 8.3 Job Submitter Economics

Submitters pay for computation at a rate determined by market demand for UBD and
available miner capacity. As the miner network grows, competition among miners drives
prices toward marginal cost of computation. Submitters compare Unbound's rate against
cloud compute alternatives (AWS, GCP, Azure) and switch when Unbound is cheaper —
which it approaches as idle mining infrastructure is monetized.

### 8.4 Three Revenue Streams for Miners

Miners on the Unbound network participate directly. There are three levels of
engagement, each earning a greater share of available computation revenue:

**Passive participation.** A miner connects to the network, receives chunk
assignments, executes the UVM, and returns results. The daemon handles all
coordination. Income is proportional to chunks completed — no lottery, no luck.

**Capability-tagged participation.** Miners who declare hardware capabilities
(GPU, high-memory, specific instruction sets) receive routing priority for jobs
that match those tags. Capability-matched chunks may carry higher per-chunk
payment when submitters bid for specific hardware.

**CID-cached participation.** Miners who have cached a dataset identified by
its content hash (CID) receive routing priority for jobs that reference that
dataset. Caching reduces redundant data transfer and increases the effective
throughput a miner can sustain, directly increasing earnings per unit time.

**Pipeline-depth declaration and parallel execution.** GPU miners face a structural
throughput bottleneck distinct from compute capacity: network round-trip latency.
A GPU that processes a chunk in 40 ms but waits 100 ms per round-trip for the next
chunk is idle 71% of the time. Two mechanisms address this:

*pipeline\_depth* — a miner declares how many chunks it can process concurrently.
The server dispatches up to that many chunks immediately on registration and refills
the pipeline after each result, without waiting for explicit pull requests. Network
round-trips are amortised across all in-flight chunks rather than paid per chunk.

*parallel\_exec* — a miner with `pipeline_depth > 1` offloads each incoming frame
to a thread-pool worker (`run_in_executor`). The event loop stays free to receive
the next frame while the previous one executes. Where sequential pipelining keeps the
GPU fed (eliminates idle time between chunks), parallel execution reduces wall time
by running D chunks simultaneously on separate threads.

The two mechanisms compound. For N chunks with compute time C per chunk, internet
round-trip RTT, and pipeline depth D:

```
Without pipeline:   N × (C + RTT)          — idle gap after every chunk
With pipeline:      N × C + RTT            — RTT paid once at the end
With parallel:      N × C / D + RTT        — compute parallelised across D threads
```

**Measured results** (N = 12 GPU chunks, C = 40 ms, RTT = 100 ms, D = 4):

| Mode | Wall time | vs serial |
|---|---|---|
| Serial (depth=1) | 1.60 s | baseline |
| Pipelined (depth=4, sequential exec) | 0.50 s | −69% |
| Pipelined + parallel (depth=4, parallel exec) | 0.13 s | −92% |

Theory predicts 1.68 s / 0.58 s / 0.22 s; actuals are slightly better because
asyncio task scheduling overhead is smaller than the 100 ms RTT granularity. The
structural result is correct: each doubling of effective parallelism halves the
compute-bound portion, with RTT as the irreducible floor.

**Privacy-derived pipeline cap and deployment thresholds.** The dispersal bound
constrains how deep a pipeline any one miner may declare. A miner with
`pipeline_depth = D` holds D chunks from D different jobs simultaneously; with
job-exclusion, leakage per job is 1/n. To bound a miner's aggregate in-flight
exposure to at most one full job's worth of information, D must not exceed the
minimum job size — which is set by the operator's chosen privacy threshold:

```
cap = ceil(1 / privacy_threshold)
```

The threshold is a deployment parameter, not a protocol constant. Three named
presets cover the common cases:

| Preset | Threshold | Cap | Recommended when |
|---|---|---|---|
| `THRESHOLD_PUBLIC` | 12.5% | 8 | Public network, anonymous miners |
| `THRESHOLD_INTERNAL` | 25% | 4 | Org cluster, vetted contractors |
| `THRESHOLD_LOCAL` | 100% | 64 | Owned machines, no untrusted party |

For a local cluster — where all machines are controlled by the operator and
chunking exists for throughput rather than privacy — the dispersal bound is
irrelevant. Setting `THRESHOLD_LOCAL` removes the privacy-derived cap entirely
(the 64 ceiling is a practical socket limit, not a security limit). AMP masking
remains available for submitters who still want numeric privacy from system
administrators, but it is no longer required by the deployment model.

The threshold can be set to any value. `pipeline_depth_cap(0.0625)` yields 16
for operators who want stronger guarantees than the public default.

The progression from passive to CID-cached participation is driven by economics.
As compute demand grows, miners with cached datasets, declared capabilities, and
configured pipeline depth earn disproportionately more than unconfigured miners —
the network self-optimizes through individual economic incentive without coordination.

---

## 9. Private Cluster Mode — Compute Aggregation Without Cryptocurrency

The Unbound protocol solves a second problem independent of its cryptocurrency
application: aggregating heterogeneous compute resources without the complexity
of traditional distributed computing frameworks.

### 9.1 The HPC Aggregation Problem

A typical HPC cluster consists of heterogeneous nodes — CPU nodes, GPU nodes,
high-memory nodes — each with different capabilities. Running a single logical
program across all of them today requires one of:

- **MPI** — tight coupling, shared memory model, requires homogeneous topology
- **SLURM / PBS** — job scheduler, requires cluster-wide configuration and shared filesystem
- **Kubernetes** — containerization, service mesh, network topology awareness
- **Ray / Dask** — Python-only, coordinator bottleneck, requires upfront cluster definition

All require nodes to know about each other. All require upfront configuration.
None handle heterogeneous capability routing automatically.

### 9.2 Unbound as a Compute Aggregation Protocol

In cluster mode, Unbound runs without the ledger, chain, or token. The protocol
reduces to:

```
Coordinator: Registry + WebSocket server + REST API
Workers:     Miner daemons (one per machine, or one per GPU/CPU)
Submitter:   ClusterClient — submit chunks, wait for results
```

A worker joins by running one command: `unbound cluster mine --server ws://coordinator:8765`.
No cluster reconfiguration. No shared filesystem. No topology knowledge.

The coordinator dispatches chunks to available workers regardless of hardware.
A GPU worker that takes longer per chunk naturally receives fewer chunks; a fast
CPU worker picks up more. Heterogeneous capability is handled implicitly by the
chunk dispatch model — the same mechanism that handles heterogeneous miners on
the public network.

### 9.3 Schema Separation in Private Clusters

Schema separation is as valuable in private clusters as on the public network.
In a university HPC cluster where multiple departments contribute nodes, schema
separation ensures that one department's research programs run on another
department's hardware without either department knowing what the other is computing.
IP isolation and compliance requirements are satisfied without access control lists
or network segmentation.

### 9.4 The Unification

The same protocol, the same binary format, the same miner daemon, and the same
SDK serve both use cases:

| Mode | Payment | Chain | Privacy threshold | Use case |
|---|---|---|---|---|
| Network | UBD escrow | PoUW blockchain | `THRESHOLD_PUBLIC` (12.5%) | Public compute market, proof of useful work |
| Cluster | None | None | `THRESHOLD_LOCAL` (100%) | Private HPC aggregation, no cryptocurrency needed |

A private cluster can migrate to the public network by adding a ledger and enabling
payment — the rest of the stack is unchanged. A public network node can run in
cluster mode for internal jobs by omitting payment. The protocol is the same; the
economic layer is optional. The privacy threshold travels with the deployment choice:
`THRESHOLD_LOCAL` on a cluster removes the pipeline-depth constraint and trusts
the operator's physical security; `THRESHOLD_PUBLIC` on the open network enforces
the dispersal bound against anonymous miners.

---

## 10. Related Work

| System | PoUW | Blind execution | Numeric privacy | Dispersal privacy | General computation |
|---|---|---|---|---|---|
| Primecoin | Partial (primes only) | No | No | No | No |
| Gridcoin | Yes (BOINC) | No | No | No | Yes |
| TrueBit | No (verification game) | No | No | No | Yes |
| Ofelimos | Yes (DPLS optimization) | No | No | No | Partial |
| BitVM | No (fraud proofs) | No | No | No | Yes |
| iExec / Golem / Akash | No | No | No | No | Yes |
| Secret Network / Oasis | Partial | Partial (TEE, data only) | Partial (TEE) | No | Yes |
| **Unbound** | **Yes** | **Yes** | **Yes (AMP)** | **Yes** | **Yes** |

**TrueBit** is the closest prior work on general verifiable computation. Its
verification game elegantly handles dishonest solvers without trusted hardware.
Unbound differs in three ways: miners are blind to task semantics, the payment model
is per-computation rather than per-challenge-resolved, and Unbound operates as a
self-contained network with no dependency on an external chain.

**Ofelimos** is the most rigorous PoUW construction and the closest on the useful work
dimension. Its DPLS framework provides formal security proofs for the PoUW mechanism.
Unbound's contribution relative to Ofelimos is the generalization to arbitrary
computation (via search problem unification), the three-layer privacy model (schema
separation, arithmetic mask propagation, dispersal privacy bound).

**Secret Network and Oasis** use Trusted Execution Environments (TEEs) to hide
*data* from node operators. The program logic is visible; only the inputs are
protected. Unbound inverts the semantic layer (program structure visible, meaning
hidden via schema) and adds an orthogonal numeric layer (arithmetic mask propagation hides
the values themselves, without trusted hardware). These approaches are complementary.

**Paillier / ElGamal homomorphic encryption** allow computation on encrypted values
but are restricted to either addition (Paillier) or multiplication (ElGamal) only.
Arithmetic mask propagation handles *both* addition and multiplication exactly, within a single
evaluation, by propagating corrections algebraically through the dual-simulation.
It is not IND-CPA secure in the formal sense, but it operates without any key
distribution to the evaluator, without noise, and without the multiplicative depth
limits of FHE.

---

## 11. Security Analysis

### 11.1 Result Integrity

A miner cannot profitably fake a result. The UVM is deterministic: any node can
re-execute a chunk and verify the result. If a miner submits a false result and the
second miner (k-of-2 assignment) returns the correct result, the disagreement is
detected and neither miner is paid. The correct result is paid when a subsequent
pair of miners agrees. The cost of cheating (losing the chunk reward) exceeds the
benefit (zero — a fake result earns nothing).

### 11.2 Schema Privacy

The integer stream transmitted to miners contains no variable names, no string
literals, no semantic labels. An adversary who receives many chunks from the same
job could attempt to infer meaning through input-output correlation analysis. This
is analogous to a known-plaintext attack on a cipher. Submitters who require strong
privacy should:
- Randomize variable ordering in the schema
- Add noise `INPUT` instructions whose values are discarded
- Use the network for computation whose privacy requires only operational obscurity,
  not cryptographic guarantees

For higher privacy requirements, schema separation can be composed with arithmetic mask propagation
masking or standard encryption of the input data.

### 11.3 Arithmetic Mask Propagation Security

**Correctness.** The mask propagation invariant
`miner_value = real_value + mask (mod M)` is preserved exactly through ADD, SUB,
MUL, NEG, and DIV-by-constant. Output corrections are always exact. No approximation
or rounding occurs.

**Confidentiality.** A miner who observes a masked input `v + r mod M` and does not
know `r` (equivalently, does not know `K`) cannot determine `v`. The set of possible
real values is the entire field `Z_M` for any observed masked value. Security is
computational, conditioned on the HMAC-SHA256 PRF assumption.

**Cross-job isolation.** Different `job_id` values produce different HMAC inputs and
therefore different mask sequences. Observing masked values across multiple jobs does
not help an adversary recover real values, as each job's masks are independently
derived.

**Known weaknesses:**
1. **Job ID reuse.** If the same `K` and `job_id` are used for two submissions with
   different inputs, the *difference* of inputs is recoverable from the *difference*
   of masked values. Mitigation: use a fresh `job_id` per submission (enforced by
   the protocol).
2. **Range restriction.** Additive masking is uniform mod `M`. If an adversary knows
   that real inputs lie in a narrow range `[a, b]`, the masked value is uniform in
   `[a + r, b + r] mod M` — not the full field — but only if `r` is known. Since `r`
   is unknown, the masked value remains indistinguishable from a uniform field element.
3. **Comparison / branch rejection.** Programs requiring data-dependent branches on
   sensitive values cannot be masked. The `MaskCompiler` rejects such programs at
   submission time with `NikhilamError`. This is a limitation on program structure,
   not a security weakness.

The scheme is not formally IND-CPA secure in the cryptographic sense. It is best
characterised as *computationally private* under the PRF assumption on HMAC-SHA256:
an adversary without `K` cannot distinguish masked inputs from random field elements.

### 11.4 Double-Spend Prevention

Escrow is locked in the ledger before job creation. The ledger enforces that locked
UBD cannot be spent on any other operation. Payment releases per chunk only after
the k-of-2 agreement is recorded on the chain. There is no mechanism by which the
same UBD can fund two different jobs.

### 11.5 Sybil Resistance

An adversary who creates many fake miner identities gains no advantage in the k-of-2
model unless they control both miners assigned to the same chunk. The probability of
controlling both miners in a random assignment is `(f/(1+f))²` where `f` is the
fraction of the network controlled by the adversary. This is analogous to the 51%
attack threshold in standard PoW — controlling a majority of miners is required to
systematically corrupt results.

### 11.6 Chain Integrity

Each block in the Unbound chain contains a batch of chunk completion proofs and hashes
the previous block. Modifying any historical block invalidates all subsequent blocks.
The chain is auditable: anyone can verify that every payment corresponds to a
legitimate chunk completion.

---

## 12. Current Implementation

A reference implementation is available at [github.com/sangharshadhyeta/Unbound](https://github.com/sangharshadhyeta/Unbound).

**Components:**

| Module | Description |
|---|---|
| `uvm/vm.py` | Stack machine, 30+ opcodes, accepts int list or raw bytes |
| `uvm/encoding.py` | LEB128 encode/decode, size reporting |
| `uvm/opcodes.py` | Opcode constants, HAS_IMMEDIATE set |
| `compiler/compiler.py` | Python subset → UVM stream + Schema |
| `compiler/chunker.py` | Stream splitting for data-parallel jobs |
| `assembler/assembler.py` | Chunk result reconstruction |
| `masking/key_deriver.py` | HMAC-SHA256 per-operation key derivation over Ed25519 prime field |
| `masking/mask_compiler.py` | Dual-simulation mask propagation; degree-1/2 classification; `MaskPlan`, linearised stream |
| `masking/beaver.py` | Beaver triple generation; `linearise()` identity for SECRET×SECRET MUL |
| `masking/nikhilam.py` | `AMPMasker` — user-facing masking interface |
| `masking/schema_vault.py` | Sealed key + schema container; PBKDF2 passphrase derivation |
| `protocol.py` | Privacy threshold presets, `pipeline_depth_cap()`, `recommend_threshold()` |
| `registry/registry.py` | Chunk lifecycle: pending → assigned → completed |
| `ledger/ledger.py` | UBD balances and escrow in SQLite |
| `chain/chain.py` | PoUW consensus, tamper-evident block chain |
| `network/server.py` | WebSocket server, binary chunk dispatch |
| `miner/miner.py` | Miner daemon, exponential backoff reconnect, `pipeline_depth`, `parallel_exec` |
| `api/app.py` | FastAPI REST: /compile, /jobs, /balance, /health |
| `sdk/client.py` | Python SDK: submit, poll, wait, run |

**Test coverage:** 304 tests passing across all components.

**Verified demo:**
- `print(sum(range(10)))` → `[45]`
- Fibonacci(10) → `[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]`
- UBD flows correctly: submitter pays → miner earns → chain records
- Beaver linearisation: `INPUT × INPUT` (degree-2) dispatched as `PUSH <masked_product>` — miner executes no MUL
- Pipeline × parallel benchmark: 12 GPU chunks at 40 ms each, 100 ms simulated RTT — serial 1.60 s, pipelined 0.50 s, parallel 0.13 s (−92% vs serial)

**Language:** Python 3.13. The reference implementation prioritizes
clarity over performance.

**Known limitations of the current prototype:**

*Array and tensor primitives are not yet implemented.* The instruction set includes
native floating-point opcodes (`FADD`, `FMUL`, `FDIV`, `FSUB`, `FMOD`, `FNEG`,
`ITOF`, `FTOI`) sufficient for scalar and fixed-point numeric workloads. What is
missing is a flat array primitive for bulk data operations, which would bring the
UVM to parity with a minimal Wasm runtime for tensor-heavy ML workloads.

*Chunk timeout is configurable but not per-job.* The reassignment window is
configurable at node startup. Jobs where each candidate evaluation takes minutes
(full model training, molecular dynamics) would benefit from per-job timeout
overrides — a scheduler extension, not an architectural change.

*parallel\_exec and the CPython GIL.* The `parallel_exec` flag offloads UVM
execution to a `ThreadPoolExecutor` via `run_in_executor`, keeping the event loop
free and allowing multiple frames to execute concurrently. In CPython, the Global
Interpreter Lock limits true CPU parallelism for pure-Python code — threads may
still contend. A C-extension UVM, a Rust implementation, or running miners on
PyPy would achieve full thread-level speedup. The architecture is correct; the
performance ceiling is a property of the reference interpreter, not the design.

*The network overhead floor.* Distributing a chunk carries fixed overhead
(WebSocket round-trip, binary encoding, escrow update). This overhead is only
justified when the per-candidate computation is expensive — roughly 10 seconds
or more on a single core. Trivial computations (arithmetic expressions, small
loops) should run locally. The SDK examples use simple programs for readability;
they are teaching tools, not production use cases.

---

## 13. Conclusion

The trust problem in distributed compute is not a matter of contract or legal
assurance — it is structural. Every existing system that runs code on untrusted
workers exposes what is being computed. This single fact excludes the majority of
valuable computation from the distributed infrastructure that exists to run it.

The UVM and schema separation create a runtime where workers execute arbitrary
computation blindly and verifiably — they know neither what they compute nor what
the numbers mean. Arithmetic mask propagation extends this guarantee to the values
themselves: workers see only key-derived additive offsets, and the submitter
recovers exact results by applying algebraically derived corrections. The two
layers together — semantic opacity from schema separation, numeric opacity from
arithmetic mask propagation — provide practical privacy for data-sensitive
distributed computation without trusted hardware, without FHE overhead, and
without noise. Distribution across independent workers adds a structural third
layer at no extra cost: the architecture already fragments every job across the
network, so no single worker ever holds enough context to infer what it computed.

Search problem unification ensures that no worker effort is wasted — every
evaluation maps solution-space territory regardless of whether it wins a block
reward. Proof of Work established that a global network of economically motivated
participants will maintain compute infrastructure at remarkable scale if the
incentives are right. Unbound keeps that incentive structure, replaces useless
hash puzzles with programs that matter, and adds the privacy properties that make
it safe to run sensitive computation on untrusted machines for the first time.

---

## References

- S. Nakamoto. Bitcoin: A Peer-to-Peer Electronic Cash System. 2008.
- S. King. Primecoin: Cryptocurrency with Prime Number Proof-of-Work. 2013.
- J. Teutsch, C. Reitwießner. A Scalable Verification Solution for Blockchains (TrueBit). 2019.
- M. Fitzi, et al. Ofelimos: Combinatorial Optimization via Proof-of-Useful-Work. CCS 2022.
- R. Robin. BitVM: Quasi-Turing Complete Computation on Bitcoin. 2023.
- T. Salimans, et al. Evolution Strategies as a Scalable Alternative to Reinforcement Learning. OpenAI. 2017.
- D. Masters, C. Luschi. Revisiting Small Batch Training for Deep Neural Networks. 2018.
- Bharati Krishna Tirtha. Vedic Mathematics. Motilal Banarsidass Publishers. 1965.
  (Source of the Nikhilam sutra — complement arithmetic intuition underlying AMP)
- C. E. Shannon. A Mathematical Theory of Communication. Bell System Technical Journal, 27(3), 1948.
  (Basis for the dispersal privacy bound: mutual information and entropy)
- P. Paillier. Public-Key Cryptosystems Based on Composite Degree Residuosity Classes. EUROCRYPT 1999.
- T. El Gamal. A Public Key Cryptosystem and a Signature Scheme Based on Discrete Logarithms. IEEE Transactions on Information Theory. 1985.
- IPFS: A. Benet. IPFS — Content Addressed, Versioned, P2P File System. 2014.
- WebAssembly: Haas et al. Bringing the Web up to Speed with WebAssembly. PLDI 2017.
- D. R. Karger, E. Lehman, F. T. Leighton, R. Panigrahy, M. S. Levine, D. Lewin. Consistent Hashing and Random Trees: Distributed Caching Protocols for Relieving Hot Spots on the World Wide Web. ACM STOC 1997.
- I. Stoica, R. Morris, D. Karger, M. F. Kaashoek, H. Balakrishnan. Chord: A Scalable Peer-to-peer Lookup Service for Internet Applications. ACM SIGCOMM 2001.
- P. Maymounkov, D. Mazières. Kademlia: A Peer-to-Peer Information System Based on the XOR Metric. IPTPS 2002.
- A. Rowstron, P. Druschel. Pastry: Scalable, Decentralized Object Location and Routing for Large-Scale Peer-to-Peer Systems. IFIP/ACM Middleware 2001.
- S. Ratnasamy, P. Francis, M. Handley, R. Karp, S. Shenker. A Scalable Content-Addressable Network. ACM SIGCOMM 2001.
- R. C. Merkle. A Digital Signature Based on a Conventional Encryption Function. Advances in Cryptology — CRYPTO '87, LNCS vol. 293, Springer, 1987.
- I. S. Reed, G. Solomon. Polynomial Codes over Certain Finite Fields. Journal of the Society for Industrial and Applied Mathematics, 8(2), 1960.
- M. O. Rabin. Efficient Dispersal of Information for Security, Load Balancing, and Fault Tolerance. Journal of the ACM, 36(2), 1989.
- A. Shamir. How to Share a Secret. Communications of the ACM, 22(11), 1979.
- G. R. Blakley. Safeguarding Cryptographic Keys. Proceedings of the National Computer Conference (AFIPS), vol. 48, 1979.
- J. R. Willett. The Second Bitcoin Whitepaper (Omni Layer). 2012.
  (Precedent for embedding structured protocol data in an existing chain's transaction fields without modifying the chain)
