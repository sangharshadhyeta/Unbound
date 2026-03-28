"""
Unbound SDK — Search Job Abstractions

Every computation is a search problem:
  f(candidate) = score  →  submit one chunk per candidate
                            miners evaluate in parallel
                            pool aggregates all scores

The key insight: instead of running f over all candidates sequentially on one
machine, each candidate becomes an independent chunk. Miners evaluate them in
parallel. Nothing is wasted — every evaluation maps one point in the solution space.

Three concrete job types here:

  DataParallelJob   — same function over many inputs (map)
  RangeSearchJob    — search an integer range for candidates satisfying a condition
  MinimizeJob       — find the candidate with the lowest score
"""

from __future__ import annotations

from typing import Any


class SearchJob:
    """
    Base class. One chunk per candidate. One output integer per chunk.

    Subclasses implement:
      _make_source(candidate) → str     Python source for one candidate's evaluation
      aggregate(results)      → Any     Interpret the flat result list
    """

    def build_chunks(self) -> list[bytes]:
        """Compile and encode one chunk per candidate."""
        from unbound.compiler.compiler import compile_source
        from unbound.uvm.encoding import encode

        chunks = []
        for candidate in self._candidates:
            src = self._make_source(candidate)
            stream, _ = compile_source(src)
            chunks.append(encode(stream))
        return chunks

    def _make_source(self, candidate: Any) -> str:
        raise NotImplementedError

    def aggregate(self, results: list[int]) -> Any:
        """Default: zip candidates with their scores."""
        return list(zip(self._candidates, results))

    @property
    def candidates(self) -> list:
        return self._candidates

    @property
    def payment(self) -> int:
        return self._payment

    @property
    def description(self) -> str:
        return self._description


class DataParallelJob(SearchJob):
    """
    Run the same evaluation body over a list of integer inputs.

    The evaluation body is Python source that uses a variable `x`
    and ends with a print() call to emit the result.

    Example
    -------
    >>> job = DataParallelJob(
    ...     eval_body="print(x * x)",
    ...     inputs=[1, 2, 3, 4, 5],
    ...     payment=50,
    ... )
    >>> client.run_job(job)
    [(1, 1), (2, 4), (3, 9), (4, 16), (5, 25)]

    Conversion pattern
    ------------------
    Sequential:  [f(x) for x in inputs]
    Search:      each x is a candidate; miner evaluates f(x) for one x
    """

    def __init__(self, eval_body: str, inputs: list[int], payment: int, description: str = "data_parallel"):
        self._eval_body = eval_body
        self._candidates = inputs
        self._payment = payment
        self._description = description

    def _make_source(self, x: int) -> str:
        return f"x = {x}\n{self._eval_body}"


class RangeSearchJob(SearchJob):
    """
    Search an integer range for candidates satisfying a condition.

    The evaluation body uses variable `n` and prints 1 (match) or 0 (no match).

    Example
    -------
    >>> job = RangeSearchJob(
    ...     eval_body=PRIME_CHECK,
    ...     start=1000, end=1100,
    ...     payment=50,
    ... )
    >>> client.run_job(job)
    [1009, 1013, 1019, 1021, ...]   # only candidates where score == 1

    Conversion pattern
    ------------------
    Sequential:  [n for n in range(start, end) if condition(n)]
    Search:      each n is a candidate; miner evaluates condition(n) for one n
    """

    def __init__(self, eval_body: str, start: int, end: int, payment: int, description: str = "range_search"):
        self._eval_body = eval_body
        self._candidates = list(range(start, end))
        self._payment = payment
        self._description = description

    def _make_source(self, n: int) -> str:
        return f"n = {n}\n{self._eval_body}"

    def aggregate(self, results: list[int]) -> list[int]:
        """Return only candidates where the evaluation returned non-zero."""
        return [c for c, score in zip(self._candidates, results) if score != 0]


class MinimizeJob(SearchJob):
    """
    Find the candidate that minimizes a score function.

    The evaluation body uses variable `x` and prints the cost/score.
    Lower score = better candidate.

    Example
    -------
    >>> job = MinimizeJob(
    ...     eval_body="print((x - 37) * (x - 37))",
    ...     candidates=list(range(0, 100)),
    ...     payment=50,
    ... )
    >>> client.run_job(job)
    (37, 0)   # (best_candidate, best_score)

    Conversion pattern
    ------------------
    Sequential:  min(candidates, key=f)
    Search:      each candidate is a chunk; miner evaluates f(candidate)
                 pool aggregates: return argmin
    """

    def __init__(self, eval_body: str, candidates: list[int], payment: int, description: str = "minimize"):
        self._eval_body = eval_body
        self._candidates = candidates
        self._payment = payment
        self._description = description

    def _make_source(self, x: int) -> str:
        return f"x = {x}\n{self._eval_body}"

    def aggregate(self, results: list[int]) -> tuple[int, int]:
        """Return (best_candidate, best_score)."""
        best_idx = min(range(len(results)), key=lambda i: results[i])
        return (self._candidates[best_idx], results[best_idx])


class MaximizeJob(MinimizeJob):
    """
    Find the candidate that maximizes a score function.

    Same as MinimizeJob but aggregates as argmax.
    """

    def __init__(self, eval_body: str, candidates: list[int], payment: int, description: str = "maximize"):
        super().__init__(eval_body, candidates, payment, description)

    def aggregate(self, results: list[int]) -> tuple[int, int]:
        """Return (best_candidate, best_score)."""
        best_idx = max(range(len(results)), key=lambda i: results[i])
        return (self._candidates[best_idx], results[best_idx])
