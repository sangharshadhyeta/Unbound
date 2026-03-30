"""
Gossip — epidemic job announcement between coordinators.

When a NodeServer receives a new job it broadcasts a signed announcement
to all its known peer coordinators. Each peer forwards it once to their
own peers. A seen-set prevents loops.

Message format (WebSocket JSON):
  {
    "type":         "gossip_job",
    "job_id":       "<id>",
    "submitter":    "<address>",
    "chunks":       [<b64>, ...],
    "requirements": [...],
    "payment":      <int>,
    "origin":       "<node_id>",   # who created the job
    "sig":          "<hex>",       # sign(origin_privkey, job_id+origin)
  }

Coordinators that receive a gossip_job add it to their local Registry
so their connected miners can pick it up.
"""

import asyncio
import json
import logging
from typing import Callable, List, Optional, Set

import websockets

logger = logging.getLogger(__name__)


class GossipPeer:
    """
    Outbound connection to a peer coordinator for gossip exchange.

    Maintains a persistent WebSocket connection. Reconnects automatically.
    """

    def __init__(self, url: str, on_message: Callable[[dict], None]):
        self._url        = url
        self._on_message = on_message
        self._ws         = None
        self._running    = False

    async def start(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    logger.info(f"Gossip connected to peer {self._url}")
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "gossip_job":
                            self._on_message(msg)
            except Exception as e:
                logger.debug(f"Gossip peer {self._url} disconnected: {e}")
                self._ws = None
                await asyncio.sleep(5)

    async def send(self, msg: dict):
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(msg))
            except Exception:
                pass  # will reconnect; message is best-effort

    def stop(self):
        self._running = False


class Gossip:
    """
    Manages gossip connections and announcement fanout.

    Usage:
        gossip = Gossip(node_id, peer_urls=["ws://peer1:8765", ...])
        await gossip.start()
        await gossip.announce_job(job_id, submitter, chunks_b64, reqs, payment, sign_fn)
    """

    def __init__(self, node_id: str, peer_urls: Optional[List[str]] = None,
                 on_job: Optional[Callable[[dict], None]] = None):
        self._node_id  = node_id
        self._on_job   = on_job          # called when a gossip job arrives
        self._seen: Set[str] = set()     # job_ids already processed
        self._peers: List[GossipPeer] = [
            GossipPeer(url, self._handle) for url in (peer_urls or [])
        ]

    async def start(self):
        for peer in self._peers:
            asyncio.create_task(peer.start())

    def handle_incoming(self, msg: dict):
        """Called by NodeServer when it receives a gossip_job over its own WebSocket."""
        self._handle(msg)

    def _handle(self, msg: dict):
        job_id = msg.get("job_id")
        if not job_id or job_id in self._seen:
            return
        self._seen.add(job_id)
        if self._on_job:
            self._on_job(msg)
        # Forward to our own peers (one hop only — origin already forwarded once)
        if msg.get("origin") != self._node_id:
            try:
                asyncio.get_running_loop().create_task(self._fanout(msg))
            except RuntimeError:
                pass  # no running loop (e.g. sync test context) — skip fanout

    async def _fanout(self, msg: dict):
        for peer in self._peers:
            await peer.send(msg)

    async def announce_job(
        self,
        job_id: str,
        submitter: str,
        chunks_b64: list,
        requirements: list,
        payment: int,
        sign_fn: Callable[[bytes], str],
    ):
        """Broadcast a new job to all peer coordinators."""
        self._seen.add(job_id)  # don't re-process our own announcement
        payload = (job_id + self._node_id).encode()
        msg = {
            "type":         "gossip_job",
            "job_id":       job_id,
            "submitter":    submitter,
            "chunks":       chunks_b64,
            "requirements": requirements,
            "payment":      payment,
            "origin":       self._node_id,
            "sig":          sign_fn(payload),
        }
        await self._fanout(msg)

    def stop(self):
        for peer in self._peers:
            peer.stop()
