"""
Example 2: Prime Search
=======================

Problem:   find all prime numbers between 1000 and 1100
Sequential: [n for n in range(1000, 1100) if is_prime(n)]

Conversion to search problem
-----------------------------
  Search space:   integers in [1000, 1100)
  Candidate:      one integer n
  Evaluation:     is_prime(n) → 1 if prime, 0 if not
  Miner does:     primality check for one n using trial division
  Pool does:      collect all 100 results, filter for 1s

This is a classic search problem: find candidates satisfying a condition.
Every miner attempt maps one integer — even the non-primes are "useful"
in the sense that they confirm those integers are not prime.

The pool builds a complete primality map of the range.
Nothing is wasted.

Primality check via trial division
------------------------------------
  isPrime(n):
    if n <= 1: return 0
    i = 2
    while i * i <= n:
      if n % i == 0: return 0
      i = i + 1
    return 1

This is expressible directly in our Python subset → compiles to UVM.
"""

from unbound.sdk import UnboundClient, RangeSearchJob
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM


# ── The evaluation kernel ─────────────────────────────────────────────────────
# This is the program each miner runs for one candidate n.
# Uses only our supported subset: arithmetic, comparisons, while loops.

PRIME_CHECK = """
result = 1
if n <= 1:
    result = 0
i = 2
while i * i <= n:
    if n % i == 0:
        result = 0
    i = i + 1
print(result)
"""


# ── Run locally ───────────────────────────────────────────────────────────────

def run_locally(start: int = 1000, end: int = 1100):
    print(f"=== Prime Search: primes in [{start}, {end}) ===\n")

    vm = UVM()
    primes = []

    for n in range(start, end):
        src = f"n = {n}\n{PRIME_CHECK}"
        stream, _ = compile_source(src)
        result = vm.execute(stream)
        if result and result[0] == 1:
            primes.append(n)

    print(f"Primes found: {primes}")
    print(f"\nTotal candidates evaluated: {end - start}")
    print(f"Primes found: {len(primes)}")
    print(f"Non-primes confirmed: {end - start - len(primes)}")
    print(f"\nOn the network: all {end - start} evaluations run in parallel.")
    print(f"Every evaluation is useful — primes AND confirmed composites.")


# ── Run on network ────────────────────────────────────────────────────────────

def run_on_network(start: int = 1000, end: int = 1100,
                   api_url: str = "http://localhost:8000", address: str = "alice"):
    client = UnboundClient(api_url, address=address)

    job = RangeSearchJob(
        eval_body=PRIME_CHECK,
        start=start,
        end=end,
        payment=50,
    )

    print(f"Submitting {len(job.candidates)} chunks...")
    primes = client.run_job(job)
    print(f"Primes: {primes}")


if __name__ == "__main__":
    run_locally()
