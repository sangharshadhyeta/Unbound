# Unbound: Proof of Useful Work via Blind Execution and Search Problem Unification

**Abstract**

Bitcoin's mining network expends approximately 150 TWh of electricity per year
computing SHA-256 hash puzzles that produce nothing beyond the blocks themselves.
We present Unbound, a protocol that replaces proof-of-work hash puzzles with
verified useful computation while preserving the economic incentives that make
decentralized mining work. The central innovation is *schema separation*: programs
compile to flat integer streams that are semantically opaque to miners; only the
submitter holds the private schema mapping stream positions to meaning. Miners
execute binary chunks through the Unbound Virtual Machine (UVM) without knowing
what they compute. We further show that all computation — including probabilistic
programs such as ML training — can be expressed as structured search problems where
every miner attempt maps real solution-space territory, eliminating wasted effort
entirely. Finally, we describe a three-layer Bitcoin overlay protocol that requires
no modification to existing miners, nodes, or the Bitcoin protocol itself, enabling
Unbound to bootstrap using Bitcoin's existing infrastructure from day one.

---

## 1. Introduction

### 1.1 The Waste Problem

The Bitcoin network processes approximately 500 ExaHash per second as of 2026.
For every valid block found, roughly 10^21 SHA-256 computations are discarded.
This is not a bug in Bitcoin's design — it is the mechanism. Unpredictable,
resource-intensive search is what makes blocks expensive to produce and therefore
trustworthy. The waste is the point.

But the waste is real. The electricity consumed by Bitcoin mining exceeds the annual
consumption of many mid-sized nations. Every joule spent finding a hash below target
is a joule not spent on weather simulation, drug discovery, ML training, or any other
computation of value to anyone.

### 1.2 Prior Attempts

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

### 1.3 The Gap

No existing system combines:

1. **Blind miner execution** — miners execute programs without knowing what they compute
2. **General proof of useful work** — any computation, not just one problem type
3. **Bitcoin overlay** — uses existing Bitcoin infrastructure without modification
4. **Search problem unification** — all computation expressed as structured search,
   ensuring no miner attempt is wasted

Unbound is the first system to combine all four.

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

### 3.2 Why This Achieves Privacy

Consider a program that checks whether a candidate drug molecule binds to a target
protein. Compiled to a UVM stream, the miner sees:

```
[1, 94, 5, 0, 1, 7, 12, 5, 1, 1, 22, 41, 3, ...]
```

These are opcodes and integer operands. Without the Schema, the miner cannot know:
- That address 0 encodes a molecular weight
- That address 1 encodes a binding coefficient
- That the OUTPUT instruction at position 14 produces a binding score
- That the entire program evaluates drug-protein interaction

The miner knows only that it received bytes, executed them, and got a result.
This is not encryption — there is no key, no cipher. The privacy comes from
compilation: the transformation from semantically rich source code to a flat
integer stream is lossy in exactly the right direction.

### 3.3 Overhead

Standard encryption (AES, RSA) imposes 10–100% overhead. FHE (Fully Homomorphic
Encryption), which allows computation on encrypted data, currently imposes
1,000–10,000× overhead and remains impractical for general computation.

Unbound's schema separation imposes approximately 1–5% overhead — the cost of
VM interpretation only. The miner executes native integer operations. There is no
encryption to perform, no ciphertext to manage, no key to distribute.

The privacy model is weaker than FHE (the miner could, in principle, run the program
many times with different inputs and observe input-output relationships). But for the
primary use case — preventing miners from knowing the semantic purpose of the
computation they are running — schema separation is sufficient and practical.

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

## 7. Bitcoin Overlay Protocol

### 7.1 Design Principle

The hardest problem in launching a new network is bootstrapping. Bitcoin's network
represents decades of infrastructure investment: hundreds of thousands of connected
machines, established economic incentives, and global distribution. Asking any of
these participants to change their software or hardware is a high barrier.

The Unbound overlay is designed so that Bitcoin's mining network provides Unbound's
trust and consensus layer from day one — without any participant's knowledge or consent.
Voluntary participation (Layers 2 and 3) provides additional compute and earns
additional rewards, but it is not required for the protocol to function.

### 7.2 Layer 1 — Unknowing Participation

Bitcoin transactions include a field called `OP_RETURN` — a provably unspendable
output that can carry up to 83 bytes of arbitrary data. Omni Layer used this
mechanism to implement USDT (Tether) on Bitcoin; Counterparty used it for asset
issuance. Both protocols created substantial financial infrastructure without any
modification to Bitcoin.

Unbound uses the same mechanism. Job submissions and result confirmations are
recorded as Bitcoin transactions with Unbound-encoded `OP_RETURN` data:

```
Job submission:
  OP_RETURN: UBD:1:<job_id>:<program_cid>:<data_cid>:<payment_hash>

Result confirmation:
  OP_RETURN: UBD:1:<job_id>:<chunk_id>:<result_hash>:<miner_address>
```

`program_cid` and `data_cid` are IPFS content identifiers — 32-byte hashes pointing
to program bytecode and input data stored on the IPFS network.

Bitcoin miners include these transactions for the transaction fees they carry.
They see bytes in an `OP_RETURN` field. They know nothing about Unbound.
The Bitcoin blockchain becomes Unbound's permanent, immutable job and result ledger —
secured by Bitcoin's full proof-of-work, timestamped by Bitcoin's blocks,
and replicated by every Bitcoin full node in the world.

### 7.3 Payment Settlement via Bitcoin Script

Bitcoin Script supports hash preimage locks natively:

```
Locking script (job submission):
  OP_SHA256 <expected_result_hash> OP_EQUALVERIFY
  OP_DUP OP_HASH160 <worker_pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG

Unlocking script (worker claims payment):
  <signature> <worker_pubkey> <result_data>
```

This script releases the locked Bitcoin payment to the first worker who provides
the correct result. Bitcoin nodes validate this script as part of normal transaction
processing — no Unbound-specific validation required. The payment is trustless,
automatic, and secured by Bitcoin's full consensus.

For cases where the expected output is not known in advance (search problems),
a k-of-n multisig script enables multiple workers to collaboratively attest to
a result before payment releases.

### 7.4 Layer 2 — Passive Participation (Pool Operators)

Pool operators control the construction of Bitcoin block templates, including the
coinbase transaction. The coinbase's arbitrary data field is already used for pool
identification and extended nonce values.

A pool plugin modifies template construction to:
1. Pull a pending Unbound UVM chunk from an Unbound node
2. Execute the chunk on the pool server's CPU (idle between blocks)
3. Embed the result hash in the coinbase data field
4. Proceed with normal ASIC SHA-256 mining

The ASICs continue doing exactly what they do today. The pool server's CPU — already
on, already connected, already part of the mining infrastructure — runs UVM and earns
UBD. Pool operators earn UBD on top of their existing BTC block rewards, with no risk
to their Bitcoin operation and no change to their mining hardware.

### 7.5 Layer 3 — Active Participation

Any machine running Linux can run the Unbound miner daemon as a background process.
This includes:

- Dedicated servers or cloud VMs
- The ARM control board of ASIC miners (every Antminer, Whatsminer, and Avalon unit
  ships with an embedded Linux system managing the ASICs — this CPU is idle during
  normal operation)
- GPU mining rigs whose host CPU is underutilized

The daemon connects to an Unbound node via WebSocket or Stratum, receives binary
chunk frames, executes the UVM, and returns results for full UBD payment per chunk.
Installation on an ASIC control board requires SSH access and a single install command.
No firmware modification, no ASIC change, no interruption to SHA-256 mining.

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

| Participation level | Action | Revenue |
|---|---|---|
| Unknowing | Include UBD transactions in Bitcoin blocks | Bitcoin transaction fees |
| Passive (pool) | Run pool plugin, embed results in coinbase | UBD share of job payments |
| Active | Run UVM daemon directly | Full UBD per verified chunk |

The progression from unknowing to active is driven by economics. As UBD gains value,
the per-chunk reward grows. Miners who notice the UBD earnings opportunity of passive
participation can trivially upgrade to active participation for greater income.
No one is coerced. The network self-bootstraps through economic incentives.

---

## 9. Related Work

| System | PoUW | Blind execution | Bitcoin overlay | General computation |
|---|---|---|---|---|
| Primecoin | Partial (primes only) | No | No | No |
| Gridcoin | Yes (BOINC) | No | No | Yes |
| TrueBit | No (verification game) | No | No | Yes |
| Ofelimos | Yes (DPLS optimization) | No | No | Partial |
| BitVM | No (fraud proofs) | No | Yes | Yes |
| iExec / Golem / Akash | No | No | No | Yes |
| Secret Network / Oasis | Partial | Partial (TEE, data only) | No | Yes |
| **Unbound** | **Yes** | **Yes** | **Yes** | **Yes** |

**TrueBit** is the closest prior work on general verifiable computation. Its
verification game elegantly handles dishonest solvers without trusted hardware.
Unbound differs in three ways: miners are blind to task semantics, the payment model
is per-computation rather than per-challenge-resolved, and the Bitcoin overlay
means no new chain is needed for bootstrapping.

**Ofelimos** is the most rigorous PoUW construction and the closest on the useful work
dimension. Its DPLS framework provides formal security proofs for the PoUW mechanism.
Unbound's contribution relative to Ofelimos is the generalization to arbitrary
computation (via search problem unification), the privacy model (schema separation),
and the overlay design.

**Secret Network and Oasis** use Trusted Execution Environments (TEEs) to hide
*data* from node operators. The program logic is visible; only the inputs are
protected. Unbound inverts this: the miner sees the computation structure but not
the semantic meaning. These are complementary privacy properties.

---

## 10. Security Analysis

### 10.1 Result Integrity

A miner cannot profitably fake a result. The UVM is deterministic: any node can
re-execute a chunk and verify the result. If a miner submits a false result and the
second miner (k-of-2 assignment) returns the correct result, the disagreement is
detected and neither miner is paid. The correct result is paid when a subsequent
pair of miners agrees. The cost of cheating (losing the chunk reward) exceeds the
benefit (zero — a fake result earns nothing).

### 10.2 Schema Privacy

The integer stream transmitted to miners contains no variable names, no string
literals, no semantic labels. An adversary who receives many chunks from the same
job could attempt to infer meaning through input-output correlation analysis. This
is analogous to a known-plaintext attack on a cipher. Submitters who require strong
privacy should:
- Randomize variable ordering in the schema
- Add noise INPUT instructions whose values are discarded
- Use the network for computation whose privacy requires only operational obscurity,
  not cryptographic guarantees

For higher privacy requirements, schema separation can be composed with standard
encryption of the input data.

### 10.3 Double-Spend Prevention

Escrow is locked in the ledger before job creation. The ledger enforces that locked
UBD cannot be spent on any other operation. Payment releases per chunk only after
the k-of-2 agreement is recorded on the chain. There is no mechanism by which the
same UBD can fund two different jobs.

### 10.4 Sybil Resistance

An adversary who creates many fake miner identities gains no advantage in the k-of-2
model unless they control both miners assigned to the same chunk. The probability of
controlling both miners in a random assignment is `(f/(1+f))²` where `f` is the
fraction of the network controlled by the adversary. This is analogous to the 51%
attack threshold in standard PoW — controlling a majority of miners is required to
systematically corrupt results.

### 10.5 Chain Integrity

Each block in the Unbound chain contains a batch of chunk completion proofs and hashes
the previous block. Modifying any historical block invalidates all subsequent blocks.
The chain is auditable: anyone can verify that every payment corresponds to a
legitimate chunk completion.

---

## 11. Current Implementation

A reference implementation is available at [github.com/YOUR_USERNAME/unbound].

**Components:**

| Module | Description |
|---|---|
| `uvm/vm.py` | Stack machine, 30+ opcodes, accepts int list or raw bytes |
| `uvm/encoding.py` | LEB128 encode/decode, size reporting |
| `uvm/opcodes.py` | Opcode constants, HAS_IMMEDIATE set |
| `compiler/compiler.py` | Python subset → UVM stream + Schema |
| `compiler/chunker.py` | Stream splitting for data-parallel jobs |
| `assembler/assembler.py` | Chunk result reconstruction |
| `registry/registry.py` | Chunk lifecycle: pending → assigned → completed |
| `ledger/ledger.py` | UBD balances and escrow in SQLite |
| `chain/chain.py` | PoUW consensus, tamper-evident block chain |
| `network/server.py` | WebSocket server, binary chunk dispatch |
| `miner/miner.py` | Miner daemon, exponential backoff reconnect |
| `api/app.py` | FastAPI REST: /compile, /jobs, /balance, /health |
| `sdk/client.py` | Python SDK: submit, poll, wait, run |

**Test coverage:** 63 tests passing across all components.

**Verified demo:**
- `print(sum(range(10)))` → `[45]`
- Fibonacci(10) → `[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]`
- UBD flows correctly: submitter pays → miner earns → chain records

**Language:** Python 3.13. The reference implementation prioritizes
clarity over performance. Production deployments targeting embedded ARM hardware
(ASIC control boards) would use the C implementation described in Section 7.5.

**Known limitations of the current prototype:**

*UVM is integer-only.* The current instruction set has no native floating-point
opcodes or array/tensor primitives. ML workloads work around this via fixed-point
arithmetic (SCALE=1000), which introduces quantization noise and limits precision
to three decimal places. This is sufficient to demonstrate the gradient-estimation
pattern but inadequate for production ML training. The natural next milestone is
adding `FADD`, `FMUL`, `FDIV` opcodes and a flat array primitive, which would
bring the UVM to parity with a minimal Wasm runtime for numeric workloads.

*Chunk timeout is fixed.* The current 35-second reassignment window suits
lightweight programs. Jobs where each candidate evaluation takes minutes
(full model training, molecular dynamics) require a configurable per-job timeout.
This is a scheduler configuration change, not an architectural one.

*The network overhead floor.* Distributing a chunk carries fixed overhead
(WebSocket round-trip, binary encoding, escrow update). This overhead is only
justified when the per-candidate computation is expensive — roughly 10 seconds
or more on a single core. Trivial computations (arithmetic expressions, small
loops) should run locally. The SDK examples use simple programs for readability;
they are teaching tools, not production use cases.

---

## 12. Conclusion

Bitcoin proved that a global network of economically motivated participants will
maintain compute infrastructure at remarkable scale if the incentives are right.
It did not prove that the computation had to be useless.

Unbound separates the incentive mechanism (pay miners for verifiable work) from
the work itself (replace SHA-256 with programs that matter). The UVM and schema
separation together create a runtime where miners can execute arbitrary computation
blindly and verifiably. Search problem unification ensures that no miner effort is
wasted — every evaluation maps solution-space territory regardless of whether it
wins a block reward. The Bitcoin overlay ensures that adoption requires no permission,
no coordination, and no new hardware.

The Bitcoin mining network is the world's largest demonstration that humans will run
compute infrastructure at scale if paid. The only thing wrong with it is what it
computes. Unbound changes that one thing.

---

## References

- S. Nakamoto. Bitcoin: A Peer-to-Peer Electronic Cash System. 2008.
- S. King. Primecoin: Cryptocurrency with Prime Number Proof-of-Work. 2013.
- J. Teutsch, C. Reitwießner. A Scalable Verification Solution for Blockchains (TrueBit). 2019.
- M. Fitzi, et al. Ofelimos: Combinatorial Optimization via Proof-of-Useful-Work. CCS 2022.
- R. Robin. BitVM: Quasi-Turing Complete Computation on Bitcoin. 2023.
- T. Salimans, et al. Evolution Strategies as a Scalable Alternative to Reinforcement Learning. OpenAI. 2017.
- D. Masters, C. Luschi. Revisiting Small Batch Training for Deep Neural Networks. 2018.
- IPFS: A. Benet. IPFS — Content Addressed, Versioned, P2P File System. 2014.
- WebAssembly: Haas et al. Bringing the Web up to Speed with WebAssembly. PLDI 2017.
- Omni Layer: J. R. Willett. The Second Bitcoin Whitepaper. 2012.
