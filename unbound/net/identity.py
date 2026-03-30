"""
Node identity — Ed25519 keypair and derived node ID.

Generated once on first run, persisted to disk.
Node ID = first 20 bytes of SHA-256(public_key), hex-encoded (40 chars).

This ID is self-sovereign: no server assigns it, no authority can revoke it.
Two nodes with the same keypair are the same node. A node that loses its key
is a new node.
"""

import hashlib
import os
from pathlib import Path
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)
from cryptography.exceptions import InvalidSignature

DEFAULT_PATH = Path.home() / ".unbound" / "identity.key"


def load_or_create(path: Path = DEFAULT_PATH) -> Tuple[Ed25519PrivateKey, str]:
    """
    Load an existing identity from path, or generate and save a new one.

    Returns (private_key, node_id).
    node_id is a 40-char hex string — stable, portable, requires no server.
    """
    path = Path(path)
    if path.exists():
        private_key = load_pem_private_key(path.read_bytes(), password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        path.write_bytes(pem)
        os.chmod(path, 0o600)  # owner read/write only

    return private_key, node_id_from_key(private_key.public_key())


def node_id_from_key(public_key: Ed25519PublicKey) -> str:
    """Derive a stable node ID from a public key."""
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:40]


def pubkey_hex(private_key: Ed25519PrivateKey) -> str:
    """Return the hex-encoded raw public key (64 chars)."""
    return private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign(private_key: Ed25519PrivateKey, message: bytes) -> str:
    """Sign a message. Returns hex-encoded signature."""
    return private_key.sign(message).hex()


def verify(pubkey_hex_str: str, message: bytes, signature_hex: str) -> bool:
    """Verify a signature against a hex-encoded public key. Returns False on failure."""
    try:
        raw = bytes.fromhex(pubkey_hex_str)
        pub = Ed25519PublicKey.from_public_bytes(raw)
        pub.verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def node_id_from_pubkey_hex(pubkey_hex_str: str) -> str:
    """Derive a node ID from a hex-encoded public key string."""
    raw = bytes.fromhex(pubkey_hex_str)
    return hashlib.sha256(raw).hexdigest()[:40]
