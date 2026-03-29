"""
SchemaVault — sealed local store for master key K and UVM schema.

K and schema never leave this object.  The vault exposes only two
operations: mask a job's inputs before submission, and correct a job's
outputs after the miner returns results.

The master key is derived from a passphrase using PBKDF2-SHA256 (600k
iterations) and held inside the object without an accessible attribute.
Pickle serialisation is blocked so K cannot accidentally be transmitted.

Security guarantee
------------------
  • K is derived fresh each session — never written to disk in raw form.
  • The schema file stores only structural info (variable names, output
    positions) — no key material.
  • The salt for PBKDF2 defaults to SHA256(abs_schema_path), so the
    same passphrase used for two different schema files produces two
    independent keys.
  • Attempting to pickle or repr the vault produces no key material.

Usage
-----
    vault = SchemaVault.from_passphrase(
        passphrase = "my secret phrase",
        schema_path = "job/program.schema",
    )
    plan = vault.prepare(stream, inputs, job_id="job-001")
    # ... submit plan.masked_inputs to the network ...
    real_results = plan.correct(miner_outputs)
"""

import hashlib
import json
import os
from typing import List, Optional

from .nikhilam import NikhilamMasker
from .mask_compiler import MaskPlan, NikhilamError  # noqa: F401  (re-export)
from .key_deriver import MODULUS

# PBKDF2 parameters — deliberately slow to resist passphrase brute-force.
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_HASH       = "sha256"
_KEY_LEN           = 32          # 256-bit master key


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        _PBKDF2_HASH,
        passphrase.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_KEY_LEN,
    )


def _load_schema(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


class SchemaVault:
    """
    Sealed local store for master key K and UVM schema.

    K is held inside NikhilamMasker — inaccessible as a public attribute.
    Only prepare() and read-only schema metadata are exposed.
    """

    # __slots__ prevents ad-hoc attribute injection
    __slots__ = ("_SchemaVault__masker", "_SchemaVault__schema")

    def __init__(self, _masker: NikhilamMasker, _schema: dict):
        self.__masker = _masker
        self.__schema = _schema

    # ── Factory constructors ─────────────────────────────────────────

    @classmethod
    def from_passphrase(
        cls,
        passphrase: str,
        schema_path: str,
        salt: Optional[bytes] = None,
    ) -> "SchemaVault":
        """
        Create a vault from a passphrase and a local schema file.

        passphrase   — secret phrase; only the submitter knows this
        schema_path  — local path to the .schema JSON file
                       (never transmitted; chmod 600 recommended)
        salt         — PBKDF2 salt; defaults to SHA256(abs_schema_path)
                       so different schema files produce different keys
                       even with the same passphrase
        """
        if salt is None:
            salt = hashlib.sha256(
                os.path.abspath(schema_path).encode("utf-8")
            ).digest()
        k      = _derive_key(passphrase, salt)
        schema = _load_schema(schema_path)
        return cls(NikhilamMasker(k), schema)

    @classmethod
    def from_key(cls, master_key: bytes, schema_path: str) -> "SchemaVault":
        """
        Create a vault from a raw key bytes (programmatic use / testing).
        In production prefer from_passphrase so the raw key is never held
        by the caller.
        """
        schema = _load_schema(schema_path)
        return cls(NikhilamMasker(master_key), schema)

    # ── Public interface ─────────────────────────────────────────────

    def prepare(
        self,
        stream: List[int],
        inputs: List[int],
        job_id: str,
    ) -> MaskPlan:
        """
        Mask inputs before job submission.

        Returns a MaskPlan — call plan.correct(miner_outputs) after
        the miner returns results to recover the real values.
        """
        return self.__masker.prepare(stream, inputs, job_id)

    @property
    def variables(self) -> dict:
        """Variable name → memory address map (structural only, not sensitive)."""
        return dict(self.__schema.get("variables", {}))

    @property
    def output_positions(self) -> list:
        """Stream positions of OUTPUT instructions."""
        return list(self.__schema.get("output_positions", []))

    # ── Safety guards ────────────────────────────────────────────────

    def __repr__(self) -> str:
        return "<SchemaVault [sealed]>"

    def __str__(self) -> str:
        return "<SchemaVault [sealed]>"

    def __reduce__(self):
        # Block pickle / copy — K must never leave this process
        raise TypeError(
            "SchemaVault cannot be serialised. "
            "K must never leave the submitter's machine."
        )
