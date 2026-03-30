"""
Arithmetic Mask Propagation (AMP) — privacy masking for Unbound job submission.

Provides input masking and output correction so miners compute on transformed
values without learning the real data. Masks propagate algebraically through
arithmetic including multiplication (quadratic cross-product correction).

The scheme draws its complement-arithmetic intuition from the Vedic sutra
Nikhilam Navatashcaramam Dashatah; the module and class retain the nikhilam/
NikhilamMasker names as recognised aliases.

Key classes
-----------
AMPMasker        — mask inputs, correct outputs (raw key interface)
SchemaVault      — sealed local store for K + schema (recommended)
MaskPlan         — result of prepare(); holds masked_inputs + correct()
MaskError        — raised when a program is incompatible with masking
"""

from .nikhilam import AMPMasker, NikhilamMasker  # NikhilamMasker kept for compat
from .mask_compiler import MaskPlan, MaskError, NikhilamError  # NikhilamError for compat
from .schema_vault import SchemaVault
from .key_deriver import KeyDeriver, MODULUS
from .fixedpoint import FixedPointMasker, FixedPointPlan
from .beaver import BeaverTriple, generate_triple

__all__ = [
    "AMPMasker",
    "NikhilamMasker",     # backwards-compatible alias
    "MaskPlan",
    "MaskError",
    "NikhilamError",      # backwards-compatible alias
    "SchemaVault",
    "KeyDeriver",
    "MODULUS",
    "FixedPointMasker",
    "FixedPointPlan",
    "BeaverTriple",
    "generate_triple",
]
