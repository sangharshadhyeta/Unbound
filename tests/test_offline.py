"""
Tests for offline batch mode.

Exercises the full export → run → import pipeline without any network.
"""

import gzip
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from unbound.net import identity
from unbound.net.offline import export_batch, run_batch, import_results, _verify_bundle_sig
from unbound.registry.registry import Registry
from unbound.uvm.opcodes import PUSH, OUTPUT, HALT, ADD


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def key_and_id(tmp_path):
    return identity.load_or_create(tmp_path / "id.key")


@pytest.fixture
def registry_with_jobs():
    reg = Registry()
    job1 = reg.create_job("alice", "job-one", [[PUSH, 7, OUTPUT, HALT]], payment=10)
    job2 = reg.create_job("alice", "job-two",
                          [[PUSH, 3, PUSH, 4, ADD, OUTPUT, HALT],
                           [PUSH, 99, OUTPUT, HALT]],
                          payment=20)
    return reg, job1.job_id, job2.job_id


# ── Export ────────────────────────────────────────────────────────────────────

class TestExport:
    def test_returns_bytes(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        assert isinstance(bundle, bytes)
        assert len(bundle) > 0

    def test_gzip_json_structure(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, j2 = registry_with_jobs
        bundle = export_batch(reg, [j1, j2], key, node_id)
        parsed = json.loads(gzip.decompress(bundle))
        assert parsed["version"] == 1
        assert parsed["node_id"] == node_id
        assert len(parsed["jobs"]) == 2

    def test_contains_all_chunks(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, _, j2 = registry_with_jobs
        bundle = export_batch(reg, [j2], key, node_id)
        parsed = json.loads(gzip.decompress(bundle))
        assert len(parsed["jobs"][0]["chunks"]) == 2

    def test_signature_present_and_valid(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        parsed = json.loads(gzip.decompress(bundle))
        # Should not raise
        _verify_bundle_sig(parsed)

    def test_missing_job_raises(self, key_and_id):
        key, node_id = key_and_id
        reg = Registry()
        with pytest.raises(ValueError, match="not found"):
            export_batch(reg, ["nonexistent-job-id"], key, node_id)


# ── Run offline ───────────────────────────────────────────────────────────────

class TestRunBatch:
    def test_produces_uresult_bytes(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        result = run_batch(bundle, private_key=key, node_id=node_id)
        assert isinstance(result, bytes)

    def test_result_contains_correct_output(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle  = export_batch(reg, [j1], key, node_id)
        result  = run_batch(bundle, private_key=key, node_id=node_id)
        parsed  = json.loads(gzip.decompress(result))
        outputs = parsed["results"]
        assert len(outputs) == 1
        assert outputs[0]["result"] == [7]   # PUSH 7 → OUTPUT

    def test_multi_chunk_job_all_executed(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, _, j2 = registry_with_jobs
        bundle = export_batch(reg, [j2], key, node_id)
        result = run_batch(bundle, private_key=key, node_id=node_id)
        parsed = json.loads(gzip.decompress(result))
        assert len(parsed["results"]) == 2

    def test_result_signature_present(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        result = run_batch(bundle, private_key=key, node_id=node_id)
        parsed = json.loads(gzip.decompress(result))
        assert "sig" in parsed
        assert len(parsed["sig"]) > 0

    def test_tampered_bundle_raises(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        parsed = json.loads(gzip.decompress(bundle))
        parsed["sig"] = "00" * 64   # corrupt signature
        tampered = gzip.compress(json.dumps(parsed).encode())
        with pytest.raises(ValueError, match="signature"):
            run_batch(tampered, private_key=key, node_id=node_id)

    def test_auto_loads_identity(self, tmp_path, registry_with_jobs):
        key, node_id = identity.load_or_create(tmp_path / "id.key")
        reg, j1, _ = registry_with_jobs
        bundle = export_batch(reg, [j1], key, node_id)
        # Should load identity automatically from tmp_path
        result = run_batch(bundle, identity_path=tmp_path / "id.key")
        assert result is not None


# ── Import ────────────────────────────────────────────────────────────────────

class TestImportResults:
    def _round_trip(self, key, node_id, registry_with_jobs, job_key="j1"):
        reg, j1, j2 = registry_with_jobs
        job_id = j1 if job_key == "j1" else j2
        bundle  = export_batch(reg, [job_id], key, node_id)
        result  = run_batch(bundle, private_key=key, node_id=node_id)
        return reg, job_id, result

    def test_recorded_count_matches_chunks(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, result_bytes = self._round_trip(key, node_id, registry_with_jobs)
        recorded = import_results(reg, result_bytes)
        assert recorded == 1

    def test_multi_chunk_all_recorded(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, j2 = registry_with_jobs
        bundle  = export_batch(reg, [j2], key, node_id)
        result  = run_batch(bundle, private_key=key, node_id=node_id)
        recorded = import_results(reg, result)
        assert recorded == 2

    def test_tampered_result_raises(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, result_bytes = self._round_trip(key, node_id, registry_with_jobs)
        parsed = json.loads(gzip.decompress(result_bytes))
        parsed["sig"] = "00" * 64
        tampered = gzip.compress(json.dumps(parsed).encode())
        with pytest.raises(ValueError, match="signature"):
            import_results(reg, tampered)

    def test_unknown_chunk_skipped_gracefully(self, key_and_id, registry_with_jobs):
        key, node_id = key_and_id
        reg, j1, result_bytes = self._round_trip(key, node_id, registry_with_jobs)
        # Import into a fresh registry — chunk doesn't exist, should return 0
        fresh_reg = Registry()
        recorded = import_results(fresh_reg, result_bytes)
        assert recorded == 0
