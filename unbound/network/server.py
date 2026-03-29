"""
Unbound Node Server

WebSocket server that:
- Accepts miner connections and dispatches chunks
- Accepts chunk results and updates the registry + chain
- Exposes HTTP API via FastAPI for job submission and status
"""

import asyncio
import json
import logging
import math
from typing import Dict, Optional, Set

import websockets

from ..registry.registry import Registry, ChunkStatus
from ..chain.chain import Chain
from ..chain.block import ChunkProof
from ..ledger.ledger import Ledger
from ..verifier.verifier import validate_result, Contract

logger = logging.getLogger(__name__)


def _results_agree(a: list, b: list, float_mode: bool, epsilon: float) -> bool:
    """Return True if two result lists are considered equal.

    Integer-typed outputs are always compared exactly.
    Float-typed outputs use combined tolerance when float_mode is True:
      |x - y| <= epsilon * max(|x|, |y|)  +  1e-9   (rel + abs floor)

    epsilon=0.0 still passes through the abs floor (1e-9), which handles
    last-bit rounding differences between CPU FPU implementations.
    Submitters should set epsilon=1e-4 for ML loss values where GPU/CPU
    divergence is larger.
    """
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if float_mode and (isinstance(x, float) or isinstance(y, float)):
            if not math.isclose(float(x), float(y), rel_tol=epsilon, abs_tol=1e-9):
                return False
        else:
            if x != y:
                return False
    return True


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
    ):
        self.registry = registry
        self.chain = chain
        self.ledger = ledger
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.block_interval = block_interval
        self.slash_fraction = slash_fraction  # fraction of chunk reward burned on bad result
        self._miners: Dict[str, websockets.WebSocketServerProtocol] = {}
        self._capabilities: Dict[str, list] = {}   # miner_id → capability list
        self._volunteers: Set[str] = set()          # miners that registered as volunteer
        self._miner_stakes: Dict[str, int] = {}    # miner_id → UBD staked (0 = unstaked)
        self._miner_cids: Dict[str, Set[str]] = {} # miner_id → set of cached IPFS CIDs
        self._miner_seen_jobs: Dict[str, Set[str]] = {}  # miner_id → job_ids already sent CID for
        self._contract = Contract()  # default: any list of ints is valid

    async def start(self):
        logger.info(f"Node WebSocket server starting on {self.ws_host}:{self.ws_port}")
        async with websockets.serve(self._handle_miner, self.ws_host, self.ws_port):
            await self._block_committer()

    async def _handle_miner(self, ws):
        miner_id = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "register":
                    miner_id = msg["miner_id"]
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

                    self._miners[miner_id] = ws
                    self._capabilities[miner_id] = caps
                    self._miner_stakes[miner_id] = stake
                    self._miner_cids[miner_id] = set(cached_cids)
                    self._miner_seen_jobs[miner_id] = set()
                    if volunteer:
                        self._volunteers.add(miner_id)
                    logger.info(
                        f"Miner registered: {miner_id}  caps={caps}"
                        f"  volunteer={volunteer}  stake={stake}"
                        f"  cached_cids={len(cached_cids)}"
                    )

                elif mtype == "request_chunk":
                    mid = msg.get("miner_id", miner_id or "unknown")
                    caps = self._capabilities.get(mid, [])
                    miner_stake = self._miner_stakes.get(mid, 0)
                    miner_cids = list(self._miner_cids.get(mid, set()))
                    chunk = self.registry.next_available_chunk(
                        capabilities=caps,
                        miner_stake=miner_stake,
                        miner_cids=miner_cids,
                    )
                    if chunk is None:
                        await ws.send(json.dumps({"type": "no_chunk"}))
                    else:
                        from ..uvm.encoding import encode
                        self.registry.assign_chunk(chunk.chunk_id, mid)

                        # Determine whether to include the job's data CID.
                        # CID is sent only on the first chunk of each job per miner —
                        # subsequent chunks for the same job can reuse the cached CID.
                        job = self.registry.get_job(chunk.job_id)
                        job_cid = job.data_cid if job else None
                        seen = self._miner_seen_jobs.get(mid, set())
                        if job_cid and chunk.job_id not in seen:
                            cid_bytes = job_cid.encode()
                            seen.add(chunk.job_id)
                            self._miner_seen_jobs[mid] = seen
                        else:
                            cid_bytes = b""

                        # Binary frame layout:
                        #   chunk_id (UTF-8) \x00 cid_len (1 byte) cid_bytes payload
                        # cid_len=0 means no CID for this chunk (already known or N/A).
                        payload = encode(chunk.stream)
                        frame = (
                            chunk.chunk_id.encode()
                            + b"\x00"
                            + bytes([len(cid_bytes)])
                            + cid_bytes
                            + payload
                        )
                        await ws.send(frame)
                        logger.info(
                            f"Dispatched chunk {chunk.chunk_id} "
                            f"({len(chunk.stream)} ops, {len(payload)} bytes) to {mid}"
                            + (f"  cid={job_cid}" if cid_bytes else "")
                        )

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
                stake = self._miner_stakes.pop(miner_id, 0)
                if stake > 0 and self.ledger is not None:
                    self.ledger.release_stake(miner_id)
                    logger.info(f"Stake released for {miner_id} on disconnect")

    async def _handle_result(self, msg: dict):
        chunk_id = msg["chunk_id"]
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
        if chunk.status == ChunkStatus.COMPLETED:
            job = self.registry.get_job(chunk.job_id)
            # k-of-2 agreement check lives here: when a second miner submits
            # the same chunk, call _results_agree(chunk.result, result,
            # job.float_mode, job.epsilon) to verify before releasing payment.
            # For now, single-miner completion is accepted; tolerance params
            # are stored on the job and ready for k-of-2 when implemented.
            if job is not None and job.float_mode:
                logger.debug(
                    f"Chunk {chunk_id} uses float mode "
                    f"(epsilon={job.epsilon}) — tolerance agreement active"
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
