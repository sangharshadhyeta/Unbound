"""
Miner Daemon

Polls for available chunks, executes them through the UVM,
and submits results. Knows nothing about job semantics —
only sees number streams.
"""

import asyncio
import itertools
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

import websockets

from ..protocol import pipeline_depth_cap, DEFAULT_THRESHOLD
from ..net import identity as _identity

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
        server_url: Union[str, List[str]] = "ws://localhost:8765",
        display_name: Optional[str] = None,
        capabilities: Optional[list] = None,
        volunteer: bool = False,
        stake: int = 0,
        cached_cids: Optional[List[str]] = None,
        pipeline_depth: int = 1,
        parallel_exec: bool = False,
        privacy_threshold: float = DEFAULT_THRESHOLD,
        # Path to Ed25519 identity key. Auto-generated on first run if absent.
        identity_path: Optional[Path] = None,
    ):
        # Load or generate persistent keypair identity.
        # miner_id is derived from the public key — stable across restarts,
        # portable across servers, requires no central authority.
        self._private_key, self.miner_id = _identity.load_or_create(
            identity_path or _identity.DEFAULT_PATH
        )
        self._pubkey_hex = _identity.pubkey_hex(self._private_key)
        self.display_name = display_name
        # Accept one URL or a list. Multiple URLs provide automatic failover:
        # if the active coordinator is unreachable the miner cycles to the next.
        self.server_urls: List[str] = (
            [server_url] if isinstance(server_url, str) else list(server_url)
        )
        self.server_url = self.server_urls[0]  # kept for backward compat
        self.capabilities = capabilities or []
        self.volunteer = volunteer
        self.stake = stake
        self.cached_cids: List[str] = list(cached_cids or [])
        # pipeline_depth > 1: server pro-actively fills queue after registration
        # and after each result — GPU miners declare depth to stay continuously fed.
        # Capped by the privacy threshold: a depth-D miner's aggregate in-flight
        # information stays ≤ 1 full job's worth of the chosen threshold.
        _cap = pipeline_depth_cap(privacy_threshold)
        self.pipeline_depth = max(1, min(pipeline_depth, _cap))
        # parallel_exec: each incoming chunk frame is executed in a thread-pool
        # worker (via run_in_executor) instead of blocking the event loop.
        # With pipeline_depth > 1, multiple frames arrive before any finish,
        # and run_in_executor lets them execute on separate OS threads in parallel.
        # Note: CPython's GIL limits true CPU parallelism for pure-Python UVM code;
        # a C-extension or subprocess UVM would achieve full thread-level speedup.
        self.parallel_exec = parallel_exec and self.pipeline_depth > 1
        # Maps job_token → data_cid received from server
        self._job_cids: Dict[str, Optional[str]] = {}
        self._running = False

    async def run(self):
        """Main miner loop — connect and process chunks.

        Cycles through server_urls on failure. A dead server is detected
        quickly (open_timeout=2s). Backoff only increases after a full cycle
        through all servers with no success — so one live server in the list
        is always reached without compounding delay.
        """
        self._running = True
        label = self.display_name or self.miner_id[:8]
        logger.info(f"Miner [{label}] starting — {len(self.server_urls)} server(s)")
        backoff     = _BACKOFF_BASE
        fail_streak = 0   # consecutive failures; resets on any successful connection

        for idx in itertools.count():
            if not self._running:
                break
            url = self.server_urls[idx % len(self.server_urls)]
            try:
                async with websockets.connect(url, open_timeout=2.0) as ws:
                    backoff     = _BACKOFF_BASE
                    fail_streak = 0
                    logger.info(f"Miner [{label}] connected to {url}")
                    await self._register(ws)
                    await self._work_loop(ws)
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                fail_streak += 1
                logger.warning(f"[{url}] unreachable: {e}")
                # Sleep only after a full cycle through all servers fails —
                # avoids compounding delay when one server is still alive.
                n = max(1, len(self.server_urls))
                if fail_streak % n == 0:
                    logger.warning(f"All {n} server(s) unreachable. Retrying in {backoff:.0f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _register(self, ws):
        """Send registration with public key. Server derives our ID from it."""
        msg = {
            "type":          "register",
            "pubkey":        self._pubkey_hex,
            "capabilities":  self.capabilities,
            "volunteer":     self.volunteer,
            "stake":         self.stake,
            "cached_cids":   self.cached_cids,
            "pipeline_depth": self.pipeline_depth,
        }
        if self.display_name:
            msg["display_name"] = self.display_name
        await ws.send(json.dumps(msg))
        # Wait for ack — server confirms the derived ID so we can verify alignment.
        ack = json.loads(await ws.recv())
        if ack.get("type") != "registered":
            raise RuntimeError(f"Unexpected handshake response: {ack}")
        server_id = ack["miner_id"]
        if server_id != self.miner_id:
            raise RuntimeError(
                f"ID mismatch: local={self.miner_id} server={server_id}"
            )
        logger.info(f"Miner {self.miner_id} registered"
                    + (f" ({self.display_name})" if self.display_name else ""))

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
                    # Cover traffic: send a fixed-size dummy message so idle
                    # and active periods look identical to a traffic analyser.
                    await ws.send(json.dumps({"type": "cover", "pad": "0" * 64}))
                    await asyncio.sleep(1)
                continue

            await self._exec_and_send(ws, raw)

    async def _pipeline_loop(self, ws):
        """Pipeline mode (pipeline_depth > 1): server pushes chunks proactively.

        After registration the server dispatches up to pipeline_depth chunks
        without waiting for request_chunk messages.  After each result the
        server refills the pipeline.  The miner just listens, executes, and
        returns results.  A single kick request is sent on entry in case the
        server has no work yet and needs a pull to start.

        When parallel_exec=True, each incoming frame is handed to a new asyncio
        task backed by run_in_executor, so D frames execute on separate threads
        rather than sequentially.  Wall time ≈ N×C/D + RTT instead of N×C + RTT.
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
                    await ws.send(json.dumps({"type": "cover", "pad": "0" * 64}))
                    await asyncio.sleep(0.2)
                    await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue

            if self.parallel_exec:
                # Fire-and-forget: _exec_and_send offloads the UVM call to the
                # thread pool, so multiple chunks run concurrently without
                # blocking recv() for the next frame.
                asyncio.create_task(self._exec_and_send(ws, raw))
            else:
                await self._exec_and_send(ws, raw)
            # Server's _handle_result will proactively push the next chunk.

    def _parse_frame(self, raw: bytes):
        """Parse a binary chunk frame. Returns (chunk_id, payload).

        Binary frame layout:
          wire_id (UTF-8) \\x00  job_token (8 bytes)
          cid_len (1 byte)  cid_bytes  payload
        """
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

        logger.info(f"Miner {self.miner_id} received chunk {chunk_id} ({len(payload)} bytes)")
        return chunk_id, payload

    async def _exec_and_send(self, ws, raw: bytes):
        """Parse a frame, execute the UVM in a thread-pool worker, send result.

        run_in_executor hands the synchronous _execute call to the default
        ThreadPoolExecutor, freeing the event loop to recv() new frames while
        the UVM is running.  With parallel_exec=True and multiple concurrent
        calls, UVM instances run on separate OS threads; GIL contention limits
        speedup for pure-Python code but the event loop remains unblocked.
        """
        chunk_id, payload = self._parse_frame(raw)
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._execute, payload)
        await ws.send(json.dumps({
            "type": "result",
            "chunk_id": chunk_id,
            "miner_id": self.miner_id,
            "result": result,
        }))

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
