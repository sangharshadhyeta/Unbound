"""
Unbound Node Server

WebSocket server that:
- Accepts miner connections and dispatches chunks
- Accepts chunk results and updates the registry + chain
- Exposes HTTP API via FastAPI for job submission and status
- Gossips job announcements to peer coordinators (optional)
- Participates in DHT for decentralised peer discovery (optional)
"""

import asyncio
import hashlib
import json
import logging
import ssl
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set

import websockets

from ..registry.registry import Registry, ChunkStatus
from ..chain.chain import Chain
from ..chain.block import ChunkProof
from ..ledger.ledger import Ledger
from ..verifier.verifier import validate_result, results_agree, Contract
from ..protocol import pipeline_depth_cap, DEFAULT_THRESHOLD
from ..net import identity as _identity
from ..net.gossip import Gossip
from ..net.dht import DHT

logger = logging.getLogger(__name__)


class NodeServer:
    def __init__(
        self,
        registry: Registry,
        chain: Optional[Chain] = None,
        ledger: Optional[Ledger] = None,
        ws_host: str = "localhost",
        ws_port: int = 8765,
        block_interval: float = 5.0,
        slash_fraction: float = 0.25,
        privacy_threshold: float = DEFAULT_THRESHOLD,
        # Identity: path to Ed25519 key file. Auto-generated if absent.
        identity_path: Optional[Path] = None,
        # Peer coordinators to gossip jobs to (ws:// or wss:// URLs).
        peers: Optional[List[str]] = None,
        # Bootstrap nodes for DHT ((host, port) tuples). None = isolated mode.
        dht_bootstrap: Optional[List[tuple]] = None,
        dht_port: int = 4433,
        # TLS: provide cert/key paths to enable wss://
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
    ):
        self.registry = registry
        self.chain = chain
        self.ledger = ledger
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.block_interval = block_interval
        self.slash_fraction = slash_fraction
        self._pipeline_depth_cap = pipeline_depth_cap(privacy_threshold)

        # Node identity
        self._private_key, self.node_id = _identity.load_or_create(
            identity_path or _identity.DEFAULT_PATH
        )
        logger.info(f"Node identity: {self.node_id}")

        # Gossip to peer coordinators
        self._gossip = Gossip(
            node_id=self.node_id,
            peer_urls=peers or [],
            on_job=self._on_gossip_job,
        )

        # DHT for peer discovery
        self._dht: Optional[DHT] = None
        if dht_bootstrap is not None:
            self._dht = DHT(node_id=self.node_id, port=dht_port)
        self._dht_bootstrap = dht_bootstrap

        # TLS
        self._ssl_ctx: Optional[ssl.SSLContext] = None
        if tls_cert and tls_key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(tls_cert, tls_key)
            self._ssl_ctx = ctx
        self._miners: Dict[str, websockets.WebSocketServerProtocol] = {}
        self._capabilities: Dict[str, list] = {}   # miner_id → capability list
        self._volunteers: Set[str] = set()          # miners that registered as volunteer
        self._miner_stakes: Dict[str, int] = {}    # miner_id → UBD staked (0 = unstaked)
        self._miner_cids: Dict[str, Set[str]] = {} # miner_id → set of cached IPFS CIDs
        self._miner_seen_jobs: Dict[str, Set[str]] = {}  # miner_id → job_ids already sent CID for
        self._miner_pipeline_depth: Dict[str, int] = {}  # miner_id → declared pipeline depth
        self._miner_inflight: Dict[str, int] = {}         # miner_id → chunks currently in flight
        # Worker-per-job exclusivity: prevents one worker from holding two
        # chunks of the same job, making non-collusion structurally enforced
        # rather than assumed (formalises the MPC non-collusion assumption).
        self._miner_job_exclusions: Dict[str, Set[str]] = {}  # miner_id → job_ids assigned
        # Opaque wire chunk IDs: workers receive random UUIDs instead of
        # "{job_id}:{index}" strings, preventing enumeration and positional
        # inference (simulation paradigm — worker view cannot reveal job structure).
        self._wire_chunk_ids: Dict[str, str] = {}        # wire_id → internal chunk_id
        self._miner_wire_ids: Dict[str, List[str]] = {}  # miner_id → list of wire_ids assigned
        self._contract = Contract()  # default: any list of ints is valid

    async def start(self):
        scheme = "wss" if self._ssl_ctx else "ws"
        logger.info(f"Node server starting on {scheme}://{self.ws_host}:{self.ws_port}  id={self.node_id}")

        await self._gossip.start()

        if self._dht is not None:
            await self._dht.start(self._dht_bootstrap)
            ws_url = f"{scheme}://{self.ws_host}:{self.ws_port}"
            asyncio.create_task(self._dht.announce([], ws_url))

        async with websockets.serve(
            self._handle_miner, self.ws_host, self.ws_port, ssl=self._ssl_ctx
        ):
            await self._block_committer()

    async def _handle_miner(self, ws):
        miner_id = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "cover":
                    pass  # deliberate no-op — cover traffic for traffic analysis resistance

                elif mtype == "gossip_job":
                    self._gossip.handle_incoming(msg)

                elif mtype == "register":
                    # If the miner supplies a pubkey, derive its ID from that.
                    # Otherwise fall back to a server-assigned UUID so that
                    # legacy clients and tests continue to work.
                    pubkey_hex = msg.get("pubkey")
                    if pubkey_hex:
                        miner_id = _identity.node_id_from_pubkey_hex(pubkey_hex)
                    else:
                        miner_id = str(uuid.uuid4())
                    caps = msg.get("capabilities", [])
                    volunteer = msg.get("volunteer", False)
                    stake = int(msg.get("stake", 0))
                    cached_cids = msg.get("cached_cids", [])

                    # Miner self-declares their stake. Zero is fine — they just
                    # won't receive chunks whose min_miner_stake > 0.
                    if stake > 0 and self.ledger is not None:
                        from ..ledger.ledger import LedgerError
                        try:
                            self.ledger.lock_stake(miner_id, stake)
                        except LedgerError as e:
                            await ws.send(json.dumps({
                                "type": "stake_error",
                                "message": str(e),
                            }))
                            logger.warning(f"Miner {miner_id} stake lock failed: {e}")
                            return

                    # pipeline_depth: how many chunks this miner can process
                    # in parallel.  GPU miners declare depth > 1 so the server
                    # pro-actively keeps their pipeline full without extra
                    # round-trips.  Capped at 8 to prevent resource exhaustion.
                    pipeline_depth = min(int(msg.get("pipeline_depth", 1)), self._pipeline_depth_cap)

                    self._miners[miner_id] = ws
                    self._capabilities[miner_id] = caps
                    self._miner_stakes[miner_id] = stake
                    self._miner_cids[miner_id] = set(cached_cids)
                    self._miner_seen_jobs[miner_id] = set()
                    self._miner_job_exclusions[miner_id] = set()
                    self._miner_wire_ids[miner_id] = []
                    self._miner_pipeline_depth[miner_id] = pipeline_depth
                    self._miner_inflight[miner_id] = 0
                    if volunteer:
                        self._volunteers.add(miner_id)
                    display_name = msg.get("display_name", "")
                    logger.info(
                        f"Miner registered: {miner_id}"
                        + (f" ({display_name})" if display_name else "")
                        + f"  caps={caps}"
                        f"  volunteer={volunteer}  stake={stake}"
                        f"  cached_cids={len(cached_cids)}"
                        f"  pipeline_depth={pipeline_depth}"
                    )
                    # Confirm the server-assigned ID back to the miner.
                    await ws.send(json.dumps({
                        "type": "registered",
                        "miner_id": miner_id,
                    }))
                    # Pro-actively fill this miner's pipeline on registration.
                    await self._try_fill_pipeline(miner_id, ws)

                elif mtype == "request_chunk":
                    mid = msg.get("miner_id", miner_id or "unknown")
                    dispatched = await self._dispatch_chunk(mid, ws)
                    if not dispatched:
                        await ws.send(json.dumps({"type": "no_chunk"}))
                    else:
                        # Fill any remaining pipeline capacity beyond the one
                        # chunk just dispatched (depth > 1 GPU miners).
                        await self._try_fill_pipeline(mid, ws)

                elif mtype == "result":
                    await self._handle_result(msg)

        except websockets.ConnectionClosed:
            pass
        finally:
            if miner_id:
                self._miners.pop(miner_id, None)
                self._capabilities.pop(miner_id, None)
                self._volunteers.discard(miner_id)
                self._miner_cids.pop(miner_id, None)
                self._miner_seen_jobs.pop(miner_id, None)
                self._miner_job_exclusions.pop(miner_id, None)
                self._miner_pipeline_depth.pop(miner_id, None)
                self._miner_inflight.pop(miner_id, None)
                for wid in self._miner_wire_ids.pop(miner_id, []):
                    self._wire_chunk_ids.pop(wid, None)
                stake = self._miner_stakes.pop(miner_id, 0)
                if stake > 0 and self.ledger is not None:
                    self.ledger.release_stake(miner_id)
                    logger.info(f"Stake released for {miner_id} on disconnect")

    async def _dispatch_chunk(self, mid: str, ws) -> bool:
        """
        Find the next eligible chunk, assign it, and send it to the miner.

        Returns True if a chunk was dispatched, False if none was available.
        Increments _miner_inflight on success.
        """
        from ..uvm.encoding import encode

        caps         = self._capabilities.get(mid, [])
        miner_stake  = self._miner_stakes.get(mid, 0)
        miner_cids   = list(self._miner_cids.get(mid, set()))
        exclude_jobs = self._miner_job_exclusions.get(mid, set())

        chunk = self.registry.next_available_chunk(
            capabilities=caps,
            miner_stake=miner_stake,
            miner_cids=miner_cids,
            exclude_job_ids=exclude_jobs,
        )
        if chunk is None:
            return False

        self.registry.assign_chunk(chunk.chunk_id, mid)

        # Record job exclusion: one worker may not hold two chunks of the same job.
        self._miner_job_exclusions.setdefault(mid, set()).add(chunk.job_id)

        # CID is sent only on the first chunk of each job per miner.
        job     = self.registry.get_job(chunk.job_id)
        job_cid = job.data_cid if job else None
        seen    = self._miner_seen_jobs.get(mid, set())
        if job_cid and chunk.job_id not in seen:
            cid_bytes = job_cid.encode()
            seen.add(chunk.job_id)
            self._miner_seen_jobs[mid] = seen
        else:
            cid_bytes = b""

        # Opaque wire chunk ID (UUID) — hides internal chunk structure.
        wire_id = str(uuid.uuid4())
        self._wire_chunk_ids[wire_id] = chunk.chunk_id
        self._miner_wire_ids.setdefault(mid, []).append(wire_id)

        # Opaque job token: SHA256(job_id)[:8] — stable CID cache key for miner.
        job_token = hashlib.sha256(chunk.job_id.encode()).digest()[:8]

        # Binary frame: wire_id \x00 job_token(8) cid_len cid_bytes payload
        payload = encode(chunk.stream)
        frame = (
            wire_id.encode()
            + b"\x00"
            + job_token
            + bytes([len(cid_bytes)])
            + cid_bytes
            + payload
        )
        await ws.send(frame)
        self._miner_inflight[mid] = self._miner_inflight.get(mid, 0) + 1
        logger.info(
            f"Dispatched chunk {chunk.chunk_id} "
            f"({len(chunk.stream)} ops, {len(payload)} bytes) to {mid}"
            + (f"  cid={job_cid}" if cid_bytes else "")
        )
        return True

    async def _try_fill_pipeline(self, mid: str, ws=None):
        """
        Dispatch chunks until the miner's declared pipeline_depth is full
        or no more chunks are available.

        Called after registration and after each result to keep GPU miners
        continuously fed without requiring explicit request_chunk messages.
        """
        if ws is None:
            ws = self._miners.get(mid)
        if ws is None:
            return
        depth = self._miner_pipeline_depth.get(mid, 1)
        while self._miner_inflight.get(mid, 0) < depth:
            dispatched = await self._dispatch_chunk(mid, ws)
            if not dispatched:
                break

    async def _handle_result(self, msg: dict):
        # Translate opaque wire chunk ID back to internal chunk ID.
        wire_id  = msg["chunk_id"]
        chunk_id = self._wire_chunk_ids.pop(wire_id, wire_id)
        miner_id = msg["miner_id"]
        result = msg.get("result", [])

        if not validate_result(result, self._contract):
            logger.warning(f"Invalid result from {miner_id} for {chunk_id} — reassigning")
            chunk = self.registry._chunks.get(chunk_id)
            if chunk:
                chunk.status = ChunkStatus.PENDING
                chunk.assigned_miner = None
            # Slash miners that staked. Miners with stake=0 just get reassigned.
            # Slash scales with the chunk reward so the deterrent stays proportional
            # regardless of UBD market price.
            if self._miner_stakes.get(miner_id, 0) > 0 and self.ledger is not None:
                slash_ubd = max(1, int(chunk.reward * self.slash_fraction)) if chunk else 1
                slashed = self.ledger.slash_stake(miner_id, slash_ubd)
                logger.warning(
                    f"Slashed {slashed} UBD from {miner_id} "
                    f"({int(self.slash_fraction * 100)}% of chunk reward {chunk.reward if chunk else '?'})"
                    f" — stake remaining: {self.ledger.get_stake(miner_id)}"
                )
            return

        chunk = self.registry.submit_result(chunk_id, miner_id, result)

        if chunk.status == ChunkStatus.FAILED and chunk.second_miner is not None:
            # k-of-2 disagreement: two miners returned different results.
            # Slash both; neither result is trustworthy.
            disagreed_primary = chunk.assigned_miner
            disagreed_second  = chunk.second_miner
            for bad_miner in (disagreed_primary, disagreed_second):
                if self._miner_stakes.get(bad_miner, 0) > 0 and self.ledger is not None:
                    slash_ubd = max(1, int(chunk.reward * self.slash_fraction)) if chunk.reward else 1
                    slashed = self.ledger.slash_stake(bad_miner, slash_ubd)
                    logger.warning(
                        f"k-of-2 disagreement: slashed {slashed} UBD from {bad_miner}"
                    )
            # Reset chunk to PENDING for re-dispatch to fresh miners.
            chunk.status       = ChunkStatus.PENDING
            chunk.assigned_miner = None
            chunk.second_miner   = None
            chunk.assigned_at    = None
            chunk.result         = None
            chunk.result_hash    = None
            chunk.first_result_pending = False
            logger.warning(
                f"Chunk {chunk_id} disagreement between "
                f"{disagreed_primary} and {disagreed_second} — reassigning"
            )
            return

        if chunk.status == ChunkStatus.COMPLETED:
            job = self.registry.get_job(chunk.job_id)
            if job is not None and job.float_mode:
                logger.debug(
                    f"Chunk {chunk_id} completed with float tolerance "
                    f"epsilon={job.epsilon}"
                )
            if self.chain is not None:
                proof = ChunkProof(
                    chunk_id=chunk_id,
                    job_id=chunk.job_id,
                    miner_id=miner_id,
                    result_hash=chunk.result_hash,
                    reward=chunk.reward,
                )
                self.chain.add_proof(proof)
            logger.info(f"Chunk {chunk_id} completed by {miner_id}")

        # Decrement inflight count and refill this miner's pipeline so GPU
        # miners stay continuously fed without an explicit request_chunk.
        self._miner_inflight[miner_id] = max(
            0, self._miner_inflight.get(miner_id, 0) - 1
        )
        await self._try_fill_pipeline(miner_id)

    def announce_job(self, job_id: str, submitter: str, chunks_b64: list,
                     requirements: list, payment: int):
        """Gossip a newly created job to peer coordinators (fire-and-forget)."""
        asyncio.create_task(self._gossip.announce_job(
            job_id=job_id,
            submitter=submitter,
            chunks_b64=chunks_b64,
            requirements=requirements,
            payment=payment,
            sign_fn=lambda msg: _identity.sign(self._private_key, msg),
            origin_pubkey=_identity.pubkey_hex(self._private_key),
        ))

    def _on_gossip_job(self, msg: dict):
        """Handle a job announcement received from a peer coordinator."""
        import base64
        job_id       = msg["job_id"]
        submitter    = msg.get("submitter", "gossip")
        chunks_b64   = msg.get("chunks", [])
        requirements = msg.get("requirements", [])
        payment      = int(msg.get("payment", 0))

        # Skip if we already have this job
        if self.registry.get_job(job_id):
            return

        chunks = [base64.b64decode(b) for b in chunks_b64]
        # Decode each chunk from LEB128 binary back to opcode stream
        from ..uvm.encoding import decode
        streams = [decode(c) for c in chunks]

        self.registry.create_job(
            submitter=submitter,
            description=f"gossip:{job_id}",
            chunks=streams,
            payment=payment,
            requirements=requirements,
            job_id=job_id,
        )
        logger.info(f"Gossip: added job {job_id} from peer (origin={msg.get('origin')})")

    async def _block_committer(self):
        """Periodically commit pending proofs into blocks (payment mode only)."""
        while True:
            await asyncio.sleep(self.block_interval)
            if self.chain is None:
                continue
            block = self.chain.commit_block()
            if block:
                logger.info(
                    f"Block #{block.index} committed: "
                    f"{len(block.proofs)} chunks, rewards={block.rewards}"
                )
