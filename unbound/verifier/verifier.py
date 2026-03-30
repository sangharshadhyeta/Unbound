"""
Verifier

Validates chunk output against the schema-defined success contract.
"""

from typing import List, Optional
from dataclasses import dataclass


@dataclass
class Contract:
    """
    Defines what a valid chunk output looks like.
    Set by the submitter at job creation, stored server-side (not sent to miner).
    """
    min_outputs: int = 0
    max_outputs: Optional[int] = None
    value_min: Optional[int] = None
    value_max: Optional[int] = None


_default_contract = Contract()


def validate_result(result: List[int], contract: Contract = _default_contract) -> bool:
    if not isinstance(result, list):
        return False
    if not all(isinstance(v, int) for v in result):
        return False
    if len(result) < contract.min_outputs:
        return False
    if contract.max_outputs is not None and len(result) > contract.max_outputs:
        return False
    if contract.value_min is not None and any(v < contract.value_min for v in result):
        return False
    if contract.value_max is not None and any(v > contract.value_max for v in result):
        return False
    return True


def results_agree(
    a: list,
    b: list,
    float_mode: bool = False,
    epsilon: float = 0.0,
) -> bool:
    """
    Return True if two result lists are considered equal.

    Integer outputs are compared exactly.  Float outputs use combined tolerance
    when float_mode is True:
      |x - y| <= epsilon * max(|x|, |y|) + 1e-9   (relative + absolute floor)

    epsilon=0.0 still passes through the abs floor (1e-9), which handles
    last-bit rounding differences between CPU FPU implementations.
    Submitters should set epsilon=1e-4 for ML loss values where GPU/CPU
    divergence is larger.
    """
    import math
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if float_mode and (isinstance(x, float) or isinstance(y, float)):
            if not math.isclose(float(x), float(y), rel_tol=epsilon, abs_tol=1e-9):
                return False
        else:
            if x != y:
                return False
    return True
