"""
Tests for node identity — Ed25519 keypair and derived node ID.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

from unbound.net import identity


class TestNodeId:
    def test_node_id_is_40_hex_chars(self, tmp_path):
        _, node_id = identity.load_or_create(tmp_path / "id.key")
        assert len(node_id) == 40
        assert all(c in "0123456789abcdef" for c in node_id)

    def test_node_id_stable_across_loads(self, tmp_path):
        path = tmp_path / "id.key"
        _, id1 = identity.load_or_create(path)
        _, id2 = identity.load_or_create(path)
        assert id1 == id2

    def test_different_keys_give_different_ids(self, tmp_path):
        _, id1 = identity.load_or_create(tmp_path / "a.key")
        _, id2 = identity.load_or_create(tmp_path / "b.key")
        assert id1 != id2

    def test_node_id_from_pubkey_hex_matches(self, tmp_path):
        key, node_id = identity.load_or_create(tmp_path / "id.key")
        pub_hex = identity.pubkey_hex(key)
        assert identity.node_id_from_pubkey_hex(pub_hex) == node_id


class TestKeyPersistence:
    def test_key_file_created(self, tmp_path):
        path = tmp_path / "id.key"
        assert not path.exists()
        identity.load_or_create(path)
        assert path.exists()

    def test_key_file_permissions(self, tmp_path):
        path = tmp_path / "id.key"
        identity.load_or_create(path)
        mode = oct(path.stat().st_mode)[-3:]
        assert mode == "600"

    def test_parent_dir_created(self, tmp_path):
        path = tmp_path / "subdir" / "nested" / "id.key"
        identity.load_or_create(path)
        assert path.exists()


class TestSignVerify:
    def test_valid_signature_verifies(self, tmp_path):
        key, _ = identity.load_or_create(tmp_path / "id.key")
        pub_hex = identity.pubkey_hex(key)
        msg = b"hello unbound"
        sig = identity.sign(key, msg)
        assert identity.verify(pub_hex, msg, sig)

    def test_wrong_message_fails(self, tmp_path):
        key, _ = identity.load_or_create(tmp_path / "id.key")
        pub_hex = identity.pubkey_hex(key)
        sig = identity.sign(key, b"original")
        assert not identity.verify(pub_hex, b"tampered", sig)

    def test_wrong_key_fails(self, tmp_path):
        key1, _ = identity.load_or_create(tmp_path / "a.key")
        key2, _ = identity.load_or_create(tmp_path / "b.key")
        sig = identity.sign(key1, b"message")
        pub2 = identity.pubkey_hex(key2)
        assert not identity.verify(pub2, b"message", sig)

    def test_bad_hex_returns_false(self, tmp_path):
        key, _ = identity.load_or_create(tmp_path / "id.key")
        pub_hex = identity.pubkey_hex(key)
        sig = identity.sign(key, b"msg")
        assert not identity.verify(pub_hex, b"msg", "notvalidhex!!!")
