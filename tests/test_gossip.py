"""
Tests for gossip — epidemic job announcement between coordinators.

These tests exercise the Gossip class in isolation using in-process
callbacks rather than real WebSocket connections.
"""

import asyncio
import pytest

from unbound.net.gossip import Gossip


@pytest.fixture
def node_a_gossip():
    received = []
    g = Gossip(node_id="node-a", peer_urls=[], on_job=received.append)
    return g, received


@pytest.fixture
def node_b_gossip():
    received = []
    g = Gossip(node_id="node-b", peer_urls=[], on_job=received.append)
    return g, received


class TestSeenSet:
    def test_duplicate_announcement_ignored(self, node_a_gossip):
        g, received = node_a_gossip
        msg = {"type": "gossip_job", "job_id": "j1", "origin": "node-x"}
        g.handle_incoming(msg)
        g.handle_incoming(msg)
        assert len(received) == 1

    def test_different_jobs_both_received(self, node_a_gossip):
        g, received = node_a_gossip
        g.handle_incoming({"type": "gossip_job", "job_id": "j1", "origin": "node-x"})
        g.handle_incoming({"type": "gossip_job", "job_id": "j2", "origin": "node-x"})
        assert len(received) == 2

    def test_own_announcement_not_delivered_to_self(self):
        received = []
        g = Gossip(node_id="node-a", peer_urls=[], on_job=received.append)
        # Simulate own announcement being echoed back
        msg = {"type": "gossip_job", "job_id": "j1", "origin": "node-a"}
        # First announce marks it seen; incoming echo is dropped
        g._seen.add("j1")
        g.handle_incoming(msg)
        assert len(received) == 0


class TestCallback:
    def test_on_job_called_with_message(self, node_a_gossip):
        g, received = node_a_gossip
        msg = {"type": "gossip_job", "job_id": "j42", "origin": "peer",
               "submitter": "alice", "payment": 100}
        g.handle_incoming(msg)
        assert received[0]["job_id"] == "j42"
        assert received[0]["payment"] == 100

    def test_no_callback_does_not_raise(self):
        g = Gossip(node_id="node-a", peer_urls=[], on_job=None)
        g.handle_incoming({"type": "gossip_job", "job_id": "j1", "origin": "x"})


class TestAnnounce:
    @pytest.mark.asyncio
    async def test_announce_marks_seen(self):
        g = Gossip(node_id="node-a", peer_urls=[], on_job=None)
        await g.announce_job(
            job_id="j99",
            submitter="alice",
            chunks_b64=[],
            requirements=[],
            payment=50,
            sign_fn=lambda m: "deadsig",
        )
        assert "j99" in g._seen

    @pytest.mark.asyncio
    async def test_announce_does_not_call_own_callback(self):
        received = []
        g = Gossip(node_id="node-a", peer_urls=[], on_job=received.append)
        await g.announce_job(
            job_id="j100",
            submitter="alice",
            chunks_b64=[],
            requirements=[],
            payment=0,
            sign_fn=lambda m: "sig",
        )
        assert len(received) == 0
