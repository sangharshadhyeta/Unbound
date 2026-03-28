"""
Example 3: Data Analysis
========================

Problem:   given a dataset of integers, compute the sum of squares
           for each slice of 10 numbers
Sequential: [sum(x*x for x in chunk) for chunk in chunks]

Conversion to search problem
-----------------------------
  Search space:   the data chunks (slices of the dataset)
  Candidate:      one data slice (10 integers)
  Evaluation:     sum of squares of the slice
  Miner does:     evaluate one slice, return one aggregate value
  Pool does:      collect all per-slice aggregates

This demonstrates the core data-parallel pattern:
  same program × N data slices = N independent chunks
  miners process all slices simultaneously
  results reassembled in order

Real-world equivalents
-----------------------
  - Log analysis: count error lines per log file chunk
  - Financial data: compute volatility per time window
  - Genomics: count GC content per sequence chunk
  - Image processing: compute histogram per image tile

The miner knows nothing about what the integers represent.
It receives a flat integer stream and returns a number.
The submitter holds the schema: knows the integers are sensor readings,
pixel values, transaction amounts, or gene sequences.
"""

from unbound.sdk import UnboundClient, DataParallelJob
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM


# ── The evaluation kernel ─────────────────────────────────────────────────────
# Compute sum of squares for a slice of 10 values.
# Each value is passed as a separate variable v0..v9.
# (In a real system, we'd extend UVM to support lists/arrays —
# this shows the pattern with the current integer-only UVM.)

def make_sum_of_squares_program(values: list[int]) -> str:
    """Generate a UVM program that computes sum of squares for a fixed slice."""
    lines = [f"v{i} = {v}" for i, v in enumerate(values)]
    lines.append("total = 0")
    for i in range(len(values)):
        lines.append(f"total = total + v{i} * v{i}")
    lines.append("print(total)")
    return "\n".join(lines)


# ── Run locally ───────────────────────────────────────────────────────────────

def run_locally():
    print("=== Data Analysis: sum of squares per slice ===\n")

    # Simulated dataset: 50 integers
    import random
    random.seed(42)
    dataset = [random.randint(1, 100) for _ in range(50)]
    chunk_size = 10
    slices = [dataset[i:i+chunk_size] for i in range(0, len(dataset), chunk_size)]

    print(f"Dataset: {len(dataset)} integers, {len(slices)} slices of {chunk_size}")
    print(f"Dataset (first 20): {dataset[:20]}...\n")

    vm = UVM()
    results = []
    for i, chunk in enumerate(slices):
        src = make_sum_of_squares_program(chunk)
        stream, _ = compile_source(src)
        output = vm.execute(stream)
        results.append(output[0])
        print(f"  Slice {i}: {chunk}  →  sum_of_squares = {output[0]}")

    print(f"\nTotal across all slices: {sum(results)}")
    print(f"Verification: {sum(x*x for x in dataset)}")
    print(f"\nOn the network: all {len(slices)} slices processed in parallel.")
    print(f"Miner sees only integers. Never knows they represent '{{}}'.")


# ── Demonstrate schema privacy ────────────────────────────────────────────────

def demonstrate_schema_privacy():
    """Show what the miner sees vs what the submitter knows."""
    print("\n=== Schema Privacy Demonstration ===\n")

    values = [42, 17, 83, 5, 61, 29, 94, 38, 72, 11]
    src = make_sum_of_squares_program(values)

    from unbound.uvm.encoding import encode, decode
    stream, schema = compile_source(src)
    binary = encode(stream)

    print(f"Submitter knows:  these are sensor readings from probe #7")
    print(f"Submitter knows:  variable names: v0=temperature, v1=pressure, ...")
    print(f"Submitter holds:  schema with {len(schema.variables)} variable mappings")
    print()
    print(f"Miner receives:   {len(binary)} bytes of binary data")
    print(f"Miner sees:       {list(binary[:20])}...")
    print(f"Miner knows:      nothing about what these integers represent")
    print()
    result = UVM().execute(stream)
    print(f"Miner returns:    {result}")
    print(f"Submitter decodes: sum of squared sensor readings = {result[0]}")


if __name__ == "__main__":
    run_locally()
    demonstrate_schema_privacy()
