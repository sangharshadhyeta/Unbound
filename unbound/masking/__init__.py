"""
Nikhilam privacy masking for Unbound job submission.

Named after the Vedic sutra Nikhilam Navatashcaramam Dashatah.
Provides input masking and output correction so miners compute
on transformed values without learning the real data.

Key classes
-----------
NikhilamMasker   — mask inputs, correct outputs (raw key interface)
SchemaVault      — sealed local store for K + schema (recommended)
MaskPlan         — result of prepare(); holds masked_inputs + correct()
NikhilamError    — raised when a program is incompatible with masking
"""

from .nikhilam import NikhilamMasker
from .mask_compiler import MaskPlan, NikhilamError
from .schema_vault import SchemaVault
from .key_deriver import KeyDeriver, MODULUS

__all__ = [
    "NikhilamMasker",
    "MaskPlan",
    "NikhilamError",
    "SchemaVault",
    "KeyDeriver",
    "MODULUS",
]
