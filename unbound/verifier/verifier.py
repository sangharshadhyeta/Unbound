"""
Verifier

Validates chunk output against the schema-defined success contract.
The execution environment is the judge — no redundant execution needed.
"""

from typing import List, Optional
from dataclasses import dataclass


@dataclass
class Contract:
    """
    Defines what a valid chunk output looks like.
    Set by the submitter at job creation, stored server-side (not sent to miner).
    """
    min_outputs: int = 0           # minimum number of output values
    max_outputs: Optional[int] = None
    value_min: Optional[int] = None
    value_max: Optional[int] = None


class Verifier:
    def validate(self, result: List[int], contract: Contract) -> bool:
        """
        Returns True if result satisfies the contract.
        Returns False → chunk will be reassigned.
        """
        if not isinstance(result, list):
            return False
        if not all(isinstance(v, int) for v in result):
            return False
        if len(result) < contract.min_outputs:
            return False
        if contract.max_outputs is not None and len(result) > contract.max_outputs:
            return False
        if contract.value_min is not None:
            if any(v < contract.value_min for v in result):
                return False
        if contract.value_max is not None:
            if any(v > contract.value_max for v in result):
                return False
        return True


_default_verifier = Verifier()
_default_contract = Contract()


def validate_result(result: List[int], contract: Contract = _default_contract) -> bool:
    return _default_verifier.validate(result, contract)
