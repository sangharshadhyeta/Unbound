"""
Miner Daemon

Polls for available chunks, executes them through the UVM,
and submits results. Knows nothing about job semantics —
only sees number streams.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

# How long to wait for the server to send a chunk frame after requesting one.
# If the server stalls beyond this, we drop the connection and reconnect.
RECV_TIMEOUT = 30.0

# Backoff config for reconnects (seconds)
_BACKOFF_BASE = 2.0
_BACKOFF_MAX  = 60.0


class Miner:
    def __init__(
        self,
        miner_id: Optional[str] = None,
        server_url: str = "ws://localhost:8765",
        capabilities: Optional[list] = None,
        volunteer: bool = False,
        stake: int = 0,
        cached_cids: Optional[List[str]] = None,
        pipeline_depth: int = 1,
    ):
        self.miner_id = miner_id or str(uuid.uuid4())[:8]
        self.server_url = server_url
        self.capabilities = capabilities or []
        self.volunteer = volunteer
        self.stake = stake
        self.cached_cids: List[str] = list(cached_cids or [])
        # pipeline_depth > 1: server pro-actively fills queue after registration
        # and after each result — GPU miners declare depth to stay continuously fed.
        self.pipeline_depth = max(1, min(pipeline_depth, 8))
        # Maps job_token → data_cid received from server
        self._job_cids: Dict[str, Optional[str]] = {}
        self._running = False

    async def run(self):
        """Main miner loop — connect and process chunks."""
        self._running = True
        logger.info(f"Miner {self.miner_id} starting, connecting to {self.server_url}")
        backoff = _BACKOFF_BASE
        while self._running:
            try:
                async with websockets.connect(self.server_url) as ws:
                    backoff = _BACKOFF_BASE  # reset on successful connect
                    await self._register(ws)
                    await self._work_loop(ws)
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                logger.warning(f"Connection lost: {e}. Retrying in {backoff:.0f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _register(self, ws):
        await ws.send(json.dumps({
            "type": "register",
            "miner_id": self.miner_id,
            "capabilities": self.capabilities,
            "volunteer": self.volunteer,
            "stake": self.stake,
            "cached_cids": self.cached_cids,
            "pipeline_depth": self.pipeline_depth,
        }))

    async def _work_loop(self, ws):
        if self.pipeline_depth > 1:
            await self._pipeline_loop(ws)
        else:
            await self._pull_loop(ws)

    async def _pull_loop(self, ws):
        """Pull model (pipeline_depth=1): request one chunk, wait, repeat."""
        while self._running:
            await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for server response — reconnecting")
                raise

            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg["type"] == "no_chunk":
                    await asyncio.sleep(1)
                continue

            chunk_id, result = self._parse_and_execute(raw)
            await ws.send(json.dumps({
                "type": "result",
                "chunk_id": chunk_id,
                "miner_id": self.miner_id,
                "result": result,
            }))

    async def _pipeline_loop(self, ws):
        """Pipeline mode (pipeline_depth > 1): server pushes chunks proactively.

        After registration the server dispatches up to pipeline_depth chunks
        without waiting for request_chunk messages.  After each result the
        server refills the pipeline.  The miner just listens, executes, and
        returns results.  A single kick request is sent on entry in case the
        server has no work yet and needs a pull to start.
        """
        await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))

        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            except asyncio.TimeoutError:
                # No proactive push arrived — nudge the server.
                await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue

            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg["type"] == "no_chunk":
                    await asyncio.sleep(0.2)
                    await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue

            chunk_id, result = self._parse_and_execute(raw)
            await ws.send(json.dumps({
                "type": "result",
                "chunk_id": chunk_id,
                "miner_id": self.miner_id,
                "result": result,
            }))
            # Server's _handle_result will proactively push the next chunk.

    def _parse_and_execute(self, raw: bytes):
        """Parse a binary chunk frame and execute it. Returns (chunk_id, result)."""
        # Binary frame layout:
        #   wire_id (UTF-8) \x00  job_token (8 bytes)
        #   cid_len (1 byte)  cid_bytes  payload
        null_pos  = raw.index(b"\x00")
        chunk_id  = raw[:null_pos].decode()
        rest      = raw[null_pos + 1:]

        job_token = rest[:8].hex()
        rest      = rest[8:]

        cid_len = rest[0]
        if cid_len > 0:
            job_cid = rest[1:1 + cid_len].decode()
            payload = rest[1 + cid_len:]
            self._job_cids[job_token] = job_cid
            logger.info(f"Received dataset CID (token={job_token}): {job_cid}")
        else:
            payload = rest[1:]

        logger.info(f"Miner {self.miner_id} executing chunk {chunk_id} ({len(payload)} bytes)")
        return chunk_id, self._execute(payload)

    def _execute(self, stream) -> list:
        """Run the UVM on the stream. Miner sees only numbers."""
        from ..uvm.vm import UVM, VMError
        try:
            return UVM().execute(stream)
        except VMError as e:
            logger.warning(f"UVM error on chunk: {e}")
            return []  # empty result triggers server-side reassignment

    def stop(self):
        self._running = False
