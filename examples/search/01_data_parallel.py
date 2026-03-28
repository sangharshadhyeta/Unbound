"""
Example 1: Data Parallel Job
============================

Problem:   compute x² for every x in [1..20]
Sequential: [x*x for x in range(1, 21)]

Conversion to search problem
-----------------------------
  Search space:   the list of inputs [1, 2, 3, ..., 20]
  Candidate:      one integer x
  Evaluation:     x * x
  Miner does:     evaluate x² for one x, return the result
  Pool does:      collect all 20 results

Every miner evaluates exactly one candidate.
All 20 run in parallel. No waiting for a sequential loop.

This is the simplest form of search-problem conversion: a plain map operation.
Any [f(x) for x in items] becomes a DataParallelJob.
"""

from unbound.sdk import UnboundClient, DataParallelJob
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM
from unbound.uvm.encoding import encode


# ── Run locally (no node needed) ─────────────────────────────────────────────

def run_locally():
    """Demonstrate the pattern without needing a running node."""
    print("=== Data Parallel: x² for x in [1..20] ===\n")

    eval_body = "print(x * x)"
    inputs = list(range(1, 21))
    vm = UVM()

    results = []
    for x in inputs:
        src = f"x = {x}\n{eval_body}"
        stream, _ = compile_source(src)
        output = vm.execute(stream)
        results.append((x, output[0]))

    for x, score in results:
        print(f"  x={x:3d}  →  x²={score}")

    print(f"\nTotal chunks: {len(inputs)}")
    print(f"All evaluated in parallel on the network.")


# ── Run on Unbound network ────────────────────────────────────────────────────

def run_on_network(api_url: str = "http://localhost:8000", address: str = "alice"):
    """Submit to a running Unbound node."""
    client = UnboundClient(api_url, address=address)

    job = DataParallelJob(
        eval_body="print(x * x)",
        inputs=list(range(1, 21)),
        payment=50,
    )

    print(f"Submitting {len(job.candidates)} chunks to {api_url}...")
    results = client.run_job(job)

    print("\nResults:")
    for x, score in results:
        print(f"  x={x:3d}  →  x²={score}")


if __name__ == "__main__":
    run_locally()
    print("\n" + "─" * 50)
    print("To run on the network:")
    print("  unbound node &")
    print("  unbound mine --id miner1 &")
    print("  unbound faucet alice --amount 1000")
    print("  python examples/search/01_data_parallel.py --network")
