"""
Example 6: Private Computation via Additive Masking
=====================================================

Schema separation hides the *meaning* of a computation — variable names,
semantic domain, output interpretation. But the integer constants baked into
the program are visible to a miner with a disassembler.

For most use cases this is fine. If a miner sees `addr[0] = 1234` and `addr[1]
= 875`, they cannot know whether those are temperatures, stock prices, or gene
expression levels. Schema separation is sufficient.

But if the data values themselves are sensitive — exact patient measurements,
proprietary financial figures, classified readings — an additional layer is
needed. This example shows that layer: additive masking.

How additive masking works
---------------------------
Goal: compute  result = Σ wᵢ · xᵢ  without the miner seeing the xᵢ values.

Step 1 (submitter):  generate random masks mᵢ for each xᵢ
Step 2 (submitter):  bake  (xᵢ + mᵢ)  into the program instead of xᵢ
Step 3 (miner):      computes Σ wᵢ · (xᵢ + mᵢ)  — sees only masked integers
Step 4 (submitter):  true result = masked_result − Σ wᵢ · mᵢ

Algebraically:
  Σ wᵢ · (xᵢ + mᵢ)
= Σ wᵢ · xᵢ  +  Σ wᵢ · mᵢ
= true_result  +  correction

The miner receives and returns integers that are meaningless without the masks.
The submitter knows the correction and recovers the true answer locally.

Limitation: works for computations that are linear in the data values.
Non-linear operations (squaring, products of two masked values) require
additional techniques. See the note at the bottom of this file.

Real-world equivalents
-----------------------
  - Medical: compute a risk score over patient vitals without exposing exact values
  - Financial: compute a portfolio return without revealing individual positions
  - Survey analytics: aggregate sensitive responses without exposing individual answers
  - Genomics: compute a GC-content score without revealing the exact sequence
"""

import random
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM
from unbound.uvm.encoding import encode, decode


# ── The computation kernel ─────────────────────────────────────────────────────
# Weighted sum: result = Σ wᵢ · xᵢ
# Variables w0..w4 and x0..x4 are injected by the submitter.
# The kernel itself is generic — no domain knowledge embedded in it.

WEIGHTED_SUM_BODY = """
total = w0 * x0 + w1 * x1 + w2 * x2 + w3 * x3 + w4 * x4
print(total)
"""


def run_without_masking():
    """Baseline: no masking. Miner sees raw data values."""
    print("=== Without masking (miner sees raw values) ===\n")

    # Sensitive data: patient vitals (integer values)
    # temperature (×10), systolic BP, SpO2, heart rate, glucose
    data    = [370, 120, 98, 72, 145]   # 37.0°C, 120 mmHg, 98%, 72 bpm, 145 mg/dL
    weights = [2, 3, 1, 2, 1]           # clinical importance weights

    src = _make_source(weights, data)
    stream, _ = compile_source(src)
    binary = encode(stream)

    print(f"Submitter's data (sensitive): {data}  (temperature×10, BP, SpO2, HR, glucose)")
    print(f"Weights:                      {weights}")
    print()
    print(f"What miner receives ({len(binary)} bytes):")
    print(f"  visible constants in program: {data}  ← patient values exposed")
    print()

    result = UVM().execute(stream)
    score  = result[0]
    print(f"Risk score = {score}")
    print(f"Miner can see: these are clinical-range integers (70–400). Domain is obvious.")


def run_with_masking():
    """With additive masking: miner sees only noise."""
    print("\n=== With additive masking (miner sees only noise) ===\n")

    data    = [370, 120, 98, 72, 145]
    weights = [2, 3, 1, 2, 1]

    # Step 1: generate random masks — large enough to obscure the data values
    random.seed(42)
    masks = [random.randint(100_000, 999_999) for _ in data]

    # Step 2: mask the data before baking into the program
    masked_data = [x + m for x, m in zip(data, masks)]

    # Step 3: send program with masked values — miner sees only large random integers
    src = _make_source(weights, masked_data)
    stream, _ = compile_source(src)
    binary = encode(stream)

    print(f"True data    (never sent): {data}")
    print(f"Masks        (never sent): {masks}")
    print(f"Masked data  (sent):       {masked_data}")
    print()
    print(f"What miner receives ({len(binary)} bytes):")
    print(f"  visible constants: {masked_data}  ← look like random large integers")
    print()

    # Miner executes — returns masked sum
    masked_result = UVM().execute(stream)[0]
    print(f"Miner returns: {masked_result}  (meaningless without the masks)")

    # Step 4: submitter removes the mask contribution locally
    #   masked_result = Σ wᵢ·(xᵢ+mᵢ) = Σ wᵢ·xᵢ + Σ wᵢ·mᵢ
    #   true_result   = masked_result  - Σ wᵢ·mᵢ
    correction  = sum(w * m for w, m in zip(weights, masks))
    true_result = masked_result - correction

    print()
    print(f"Correction (Σ wᵢ·mᵢ):  {correction}")
    print(f"True score = {masked_result} − {correction} = {true_result}")

    expected = sum(w * x for w, x in zip(weights, data))
    print(f"Verification (local):  {expected}")
    print(f"Match: {true_result == expected}")

    print()
    print("Miner perspective:")
    print(f"  Received: integers in range {min(masked_data)}–{max(masked_data)}")
    print(f"  Returned: {masked_result}")
    print(f"  Learned:  nothing  (large random-looking integers, no structure)")


def demonstrate_algebra():
    """Show the masking algebra explicitly."""
    print("\n=== Masking algebra ===\n")

    # Simple case: two values, two weights
    x0, x1 = 370, 120   # true values (patient data)
    w0, w1 = 2, 3        # weights
    m0, m1 = 54321, 87654  # random masks

    true       = w0 * x0 + w1 * x1
    masked     = w0 * (x0 + m0) + w1 * (x1 + m1)
    correction = w0 * m0 + w1 * m1
    recovered  = masked - correction

    print(f"True result:   {w0}×{x0} + {w1}×{x1} = {true}")
    print(f"Masked result: {w0}×{x0+m0} + {w1}×{x1+m1} = {masked}")
    print(f"Correction:    {w0}×{m0} + {w1}×{m1} = {correction}")
    print(f"Recovered:     {masked} − {correction} = {recovered}")
    print(f"Matches true:  {recovered == true}")


def _make_source(weights: list[int], values: list[int]) -> str:
    lines = [f"w{i} = {w}" for i, w in enumerate(weights)]
    lines += [f"x{i} = {x}" for i, x in enumerate(values)]
    lines.append(WEIGHTED_SUM_BODY)
    return "\n".join(lines)


# ── Note on non-linear computations ───────────────────────────────────────────
#
# Additive masking works for computations linear in the data:
#   Σ wᵢ · xᵢ,  mean(x),  Σ xᵢ
#
# It breaks for non-linear operations:
#   (x + m)²  =  x²  +  2xm  +  m²    ← cross term 2xm depends on both x and m
#                                          cannot be corrected without knowing x
#
# For non-linear workloads where data privacy is required:
#   - Secret sharing (Shamir): split x into n shares, each miner gets one share
#   - Differential privacy: add calibrated noise to outputs, not inputs
#   - FHE: fully homomorphic encryption — correct but 1000x overhead
#   - Trusted execution environments (SGX/TrustZone): hardware isolation
#
# Unbound's architecture does not prevent any of these. The application layer
# can apply whichever technique fits the workload's privacy/performance trade-off.
# Schema separation handles the common case (semantic privacy) at ~1% overhead.
# Additive masking handles the sensitive-values case for linear workloads at ~0%
# additional overhead. Heavier schemes are available when the threat model demands.


if __name__ == "__main__":
    run_without_masking()
    run_with_masking()
    demonstrate_algebra()
