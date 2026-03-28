"""
Example 5: Function Optimization
=================================

Problem:   find the integer x in [0, 200] that minimizes f(x) = (x-73)² + 3*(x-73)
Sequential: min(range(0, 200), key=lambda x: (x-73)**2 + 3*(x-73))

Conversion to search problem
-----------------------------
  Search space:   integers in [0, 200]
  Candidate:      one integer x
  Evaluation:     f(x) = (x-73)² + 3*(x-73)
  Miner does:     evaluate f(x) for one x, return the score
  Pool does:      collect all 200 scores, return (argmin, min_score)

This is the simplest discrete optimization pattern.
All 200 evaluations run simultaneously — full landscape mapped in one step.

Optimization landscape
-----------------------
  f(x) = (x - 73)² + 3(x - 73)   ← quadratic with linear perturbation
  f'(x) = 2(x-73) + 3 = 0
  x* = 73 - 1.5 = 71.5   → integer minimum at x=71 or x=72

The distributed search finds this without derivatives, without iterating —
just by evaluating every candidate simultaneously.

More complex uses of MinimizeJob
----------------------------------
  - Portfolio optimization: minimize variance across asset allocation candidates
  - Combinatorial search: minimize total cost over discretized configuration space
  - Neural architecture search: minimize validation loss over architecture candidates
  - Protein design: minimize energy over candidate sequence variants

The miner is an anonymous computation engine. It receives integers, returns integers.
The meaning of x — portfolio weights, architecture code, sequence index — is known
only to the submitter who holds the schema.

MaximizeJob
-----------
  Identical pattern, reversed aggregation.
  Find the sequence variant with highest binding affinity.
  Find the policy with highest expected reward.
  Find the price with highest expected revenue.
"""

from unbound.sdk import UnboundClient, MinimizeJob, MaximizeJob
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM


# ── Evaluation kernel ──────────────────────────────────────────────────────────
# f(x) = (x - 73)² + 3*(x - 73)
# Variable x is injected by the job builder.

MINIMIZE_BODY = """
shifted = x - 73
score = shifted * shifted + 3 * shifted
print(score)
"""

# A fitness function: returns how "fit" a candidate is (higher = better).
# Simulates a biological fitness landscape — multiple local optima.
# f(x) = -(x-30)*(x-70) for x in [0,100]: negative parabola peaking between 30 and 70.
FITNESS_BODY = """
score = 0 - (x - 30) * (x - 70)
print(score)
"""


# ── Run locally ────────────────────────────────────────────────────────────────

def run_locally():
    print("=== Function Optimization: minimize f(x) = (x-73)² + 3(x-73) ===\n")

    candidates = list(range(0, 200))
    vm = UVM()

    # Evaluate all candidates locally
    scores = {}
    for x in candidates:
        src = f"x = {x}\n{MINIMIZE_BODY}"
        stream, _ = compile_source(src)
        result = vm.execute(stream)
        scores[x] = result[0] if result else 0

    best_x   = min(scores, key=scores.get)
    best_score = scores[best_x]

    print(f"Search space:  x ∈ [0, 200)  ({len(candidates)} candidates)")
    print(f"Function:      f(x) = (x-73)² + 3(x-73)")
    print()
    print(f"Scores around minimum:")
    for x in range(68, 78):
        marker = "  ← minimum" if x == best_x else ""
        print(f"  x={x:3d}  f(x) = {scores[x]:6d}{marker}")

    print()
    print(f"Result:  x = {best_x},  f(x) = {best_score}")
    print(f"Theory:  x* = 71.5  →  integer optimum at x=71 or x=72")
    print()
    print(f"On the network: all {len(candidates)} evaluations run in parallel.")
    print(f"Full landscape mapped in one network round-trip.")


def run_fitness_maximize():
    print("\n=== Fitness Maximization: f(x) = -(x-30)(x-70) ===\n")

    candidates = list(range(0, 101))
    vm = UVM()

    scores = {}
    for x in candidates:
        src = f"x = {x}\n{FITNESS_BODY}"
        stream, _ = compile_source(src)
        result = vm.execute(stream)
        scores[x] = result[0] if result else 0

    best_x = max(scores, key=scores.get)
    best_score = scores[best_x]

    # Print a small ASCII landscape
    print("Fitness landscape (x vs score):\n")
    max_score = max(scores.values())
    bar_scale = 40 / max(max_score, 1)
    for x in range(0, 101, 5):
        s = scores[x]
        bar = "█" * max(0, int(s * bar_scale))
        marker = " ← peak" if x == best_x else ""
        print(f"  x={x:3d}  {bar}{marker}")

    print()
    print(f"Peak:  x = {best_x},  fitness = {best_score}")
    print(f"Theory: x* = 50 (midpoint of roots at 30 and 70)")


# ── Using the SDK job types ────────────────────────────────────────────────────

def run_with_sdk():
    print("\n=== Same computation via MinimizeJob / MaximizeJob SDK ===\n")

    vm = UVM()

    # MinimizeJob
    minimize_job = MinimizeJob(
        eval_body=MINIMIZE_BODY,
        candidates=list(range(0, 200)),
        payment=50,
        description="quadratic_minimize",
    )

    chunks = minimize_job.build_chunks()
    raw = _run_chunks_locally(chunks, vm)
    best_candidate, best_score = minimize_job.aggregate(raw)
    print(f"MinimizeJob result:  x = {best_candidate},  f(x) = {best_score}")

    # MaximizeJob
    maximize_job = MaximizeJob(
        eval_body=FITNESS_BODY,
        candidates=list(range(0, 101)),
        payment=50,
        description="fitness_maximize",
    )

    chunks = maximize_job.build_chunks()
    raw = _run_chunks_locally(chunks, vm)
    best_candidate, best_score = maximize_job.aggregate(raw)
    print(f"MaximizeJob result:  x = {best_candidate},  fitness = {best_score}")


def _run_chunks_locally(chunks: list[bytes], vm: UVM) -> list[int]:
    from unbound.uvm.encoding import decode
    results = []
    for chunk_bytes in chunks:
        stream = decode(chunk_bytes)
        output = vm.execute(stream)
        results.append(output[0] if output else 0)
    return results


# ── Run on network ─────────────────────────────────────────────────────────────

def run_on_network(api_url: str = "http://localhost:8000", address: str = "alice"):
    client = UnboundClient(api_url, address=address)

    job = MinimizeJob(
        eval_body=MINIMIZE_BODY,
        candidates=list(range(0, 200)),
        payment=50,
        description="quadratic_minimize",
    )

    print(f"Submitting {len(job.candidates)} candidates to {api_url}...")
    best_x, best_score = client.run_job(job)
    print(f"Minimum at x={best_x}, f(x)={best_score}")


if __name__ == "__main__":
    run_locally()
    run_fitness_maximize()
    run_with_sdk()
