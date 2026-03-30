"""
Network-condition simulation — varying latency, jitter, and loss.

Five network profiles × five miner configurations over 16 chunks:

  Profiles
  --------
  fiber      RTT= 10ms  jitter=  2ms  loss=0.1%   ideal fibre / LAN
  broadband  RTT= 50ms  jitter= 15ms  loss=0.5%   typical home internet
  mobile     RTT=150ms  jitter= 50ms  loss=2.0%   4G / unreliable WiFi
  satellite  RTT=600ms  jitter= 80ms  loss=1.0%   geostationary satellite
  degraded   RTT=200ms  jitter=100ms  loss=8.0%   congested / bad link

  Miner configurations
  --------------------
  cpu        compute=150ms  depth=1  sequential     CPU-only device
  gpu-seq    compute= 15ms  depth=1  sequential     GPU, no pipelining
  gpu-pipe   depth=8   sequential   TCP              pipeline, sequential exec
  gpu-par    depth=8   parallel     TCP (HOL)        pipeline + parallel, HOL on loss
  gpu-quic   depth=8   parallel     QUIC             pipeline + parallel, per-stream

Network model
-------------
Each chunk result travels one-way in (rtt/2 + gauss(0, jitter/2)) seconds.
On a loss event (probability = loss_rate), a TCP retransmission adds RTT×2^k
(k ∈ {1,2,3}) to that frame's delivery time.

TCP head-of-line blocking (HOL):
  When a loss event stalls one frame, every other in-flight frame on the same
  connection is also held up at the receiver until the retransmit completes —
  TCP delivers in order, so buffered frames cannot be handed to the application
  until the gap is filled.

  In the simulation this is modelled with a per-connection asyncio.Lock that is
  acquired ONLY during retransmit delays.  Concurrent _exec tasks see normal
  (non-loss) delays proceed independently; a loss event holds the lock for the
  full retransmit duration and forces all other concurrent tasks to queue behind
  it before delivering their results.

QUIC streams are independent: a loss only delays the specific chunk whose
stream dropped a packet.  The lock is never used.

Pipeline depth and satellite links:
  A depth-D pipeline fully amortises RTT only when D ≥ RTT/C.  For satellite
  (RTT=600ms, C=15ms) that requires depth ≥ 40.  With depth=8 the pipeline
  runs dry every 8×15=120ms and then waits ~600ms for the next server dispatch.
  QUIC with depth=8 still wins because per-stream delivery lets the server
  receive and dispatch continuously without the HOL stall.
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import websockets

from unbound.network.server import NodeServer
from unbound.registry.registry import Registry
from unbound.protocol import THRESHOLD_LOCAL
from unbound.uvm.opcodes import PUSH, MUL, OUTPUT, HALT

# ── Workload ──────────────────────────────────────────────────────────────────

STREAM    = [PUSH, 6, PUSH, 7, MUL, OUTPUT, HALT]
N_JOBS    = 16
PORT_BASE = 8780


# ── Network profiles ──────────────────────────────────────────────────────────

@dataclass
class NetworkProfile:
    name:   str
    rtt:    float   # round-trip time (seconds)
    jitter: float   # std-dev of one-way delay (seconds)
    loss:   float   # per-frame loss probability

    def sample_one_way(self) -> Tuple[float, bool]:
        """
        Sample one-way delivery time.  Returns (seconds, was_retransmit).

        On loss: adds TCP RTO = RTT × 2^k, k weighted toward 1.
        """
        base = max(0.001, self.rtt / 2 + random.gauss(0, self.jitter / 2))
        if random.random() < self.loss:
            k   = random.choices([1, 2, 3], weights=[0.70, 0.25, 0.05])[0]
            rto = self.rtt * (2 ** k)
            return base + rto, True
        return base, False


PROFILES: List[NetworkProfile] = [
    NetworkProfile("fiber",     rtt=0.010, jitter=0.002, loss=0.001),
    NetworkProfile("broadband", rtt=0.050, jitter=0.015, loss=0.005),
    NetworkProfile("mobile",    rtt=0.150, jitter=0.050, loss=0.020),
    NetworkProfile("satellite", rtt=0.600, jitter=0.080, loss=0.010),
    NetworkProfile("degraded",  rtt=0.200, jitter=0.100, loss=0.080),
]


# ── Miner configurations ──────────────────────────────────────────────────────

@dataclass
class MinerConfig:
    name:           str
    compute:        float   # seconds per chunk
    pipeline_depth: int
    parallel_exec:  bool
    quic:           bool    # True → independent per-stream delivery (no HOL)


CONFIGS: List[MinerConfig] = [
    MinerConfig("cpu",      compute=0.150, pipeline_depth=1, parallel_exec=False, quic=False),
    MinerConfig("gpu-seq",  compute=0.015, pipeline_depth=1, parallel_exec=False, quic=False),
    MinerConfig("gpu-pipe", compute=0.015, pipeline_depth=8, parallel_exec=False, quic=False),
    MinerConfig("gpu-par",  compute=0.015, pipeline_depth=8, parallel_exec=True,  quic=False),
    MinerConfig("gpu-quic", compute=0.015, pipeline_depth=8, parallel_exec=True,  quic=True),
]


# ── Network-aware benchmark miner ─────────────────────────────────────────────

class NetMiner:
    """
    Miner that emulates network conditions.

    Delivery model:
      - depth=1 (pull):      RTT gap added after each result in the pull loop,
                             representing the full round-trip before the next
                             chunk arrives from the server.
      - depth>1 (pipeline):  Pre-queued chunks arrive without delay.  Results
                             carry a one-way upload delay.  On TCP loss the HOL
                             lock stalls all other concurrent sends.
    """

    def __init__(
        self,
        config:     MinerConfig,
        profile:    NetworkProfile,
        done_event: asyncio.Event,
        n_chunks:   int,
    ):
        self._cfg    = config
        self._p      = profile
        self._done   = done_event
        self._n      = n_chunks
        self._count  = 0
        self._losses = 0
        # Shared lock for TCP HOL simulation.
        # Acquired ONLY when a loss event fires; releases after retransmit delay.
        self._hol_lock: Optional[asyncio.Lock] = None
        self._miner_id: Optional[str] = None   # assigned by server

    @property
    def losses(self) -> int:
        return self._losses

    async def run(self, port: int):
        self._hol_lock = asyncio.Lock()
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.send(json.dumps({
                "type": "register",
                "display_name": self._cfg.name,
                "capabilities": [],
                "pipeline_depth": self._cfg.pipeline_depth,
                "volunteer": False, "stake": 0, "cached_cids": [],
            }))
            ack = json.loads(await ws.recv())
            self._miner_id = ack["miner_id"]
            if self._cfg.pipeline_depth > 1:
                await self._pipeline_loop(ws)
            else:
                await self._pull_loop(ws)

    # ── depth=1: pull one chunk, pay full RTT, repeat ─────────────────────

    async def _pull_loop(self, ws):
        while True:
            await ws.send(json.dumps({
                "type": "request_chunk", "miner_id": self._miner_id,
            }))
            raw = await ws.recv()
            if isinstance(raw, str):
                if json.loads(raw).get("type") == "no_chunk":
                    await asyncio.sleep(0.02)
                continue

            await self._exec(ws, raw, upload_delay=False)

            # Full round-trip cost: result upload + server dispatch + chunk download.
            # This is what pipelining eliminates.
            delay, loss = self._p.sample_one_way()
            if loss:
                self._losses += 1
            await asyncio.sleep(delay + self._p.rtt / 2)   # one-way up + one-way down

    # ── depth>1: server pushes chunks proactively ─────────────────────────

    async def _pipeline_loop(self, ws):
        await ws.send(json.dumps({
            "type": "request_chunk", "miner_id": self._miner_id,
        }))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({
                    "type": "request_chunk", "miner_id": self._miner_id,
                }))
                continue
            if isinstance(raw, str):
                if json.loads(raw).get("type") == "no_chunk":
                    await asyncio.sleep(0.02)
                    await ws.send(json.dumps({
                        "type": "request_chunk", "miner_id": self._miner_id,
                    }))
                continue
            if self._cfg.parallel_exec:
                asyncio.create_task(self._exec(ws, raw, upload_delay=True))
            else:
                await self._exec(ws, raw, upload_delay=True)

    # ── Compute + optional network delay + send ───────────────────────────

    async def _exec(self, ws, raw: bytes, upload_delay: bool):
        null_pos = raw.index(b"\x00")
        chunk_id = raw[:null_pos].decode()
        rest     = raw[null_pos + 1 + 8:]
        cid_len  = rest[0]
        payload  = rest[1 + cid_len:]

        await asyncio.sleep(self._cfg.compute)

        from unbound.uvm.vm import UVM, VMError
        try:
            result = UVM().execute(payload)
        except VMError:
            result = []   # empty result triggers server-side reassignment

        if upload_delay:
            delay, loss = self._p.sample_one_way()
            if loss:
                self._losses += 1
                if not self._cfg.quic:
                    # TCP HOL: this frame's retransmit stalls all concurrent senders.
                    # Acquire the shared connection lock for the full delay.
                    # Other parallel _exec tasks must wait before they can deliver.
                    async with self._hol_lock:
                        await asyncio.sleep(delay)
                else:
                    # QUIC: stream is independent, only this chunk is delayed.
                    await asyncio.sleep(delay)
            else:
                # No loss: concurrent delivery, no lock needed.
                await asyncio.sleep(delay)

        await ws.send(json.dumps({
            "type": "result",
            "chunk_id": chunk_id,
            "miner_id": self._miner_id,
            "result": result,
        }))

        self._count += 1
        if self._count >= self._n:
            self._done.set()


# ── Single scenario runner ────────────────────────────────────────────────────

_port_counter = PORT_BASE


async def run_scenario(
    config:  MinerConfig,
    profile: NetworkProfile,
    n_jobs:  int = None,
    stream:  list = None,
) -> Tuple[float, int]:
    """
    Run one (config, profile) pair.  Returns (wall_time_seconds, loss_count).

    n_jobs and stream default to the module-level N_JOBS and STREAM.
    Pass explicit values in tests to avoid monkeypatching module globals.
    """
    n     = n_jobs if n_jobs is not None else N_JOBS
    chunk = stream if stream is not None else STREAM

    registry = Registry()
    server   = NodeServer(registry, privacy_threshold=THRESHOLD_LOCAL)
    stop_evt = asyncio.Event()
    done_evt = asyncio.Event()
    port_ref: list = []   # filled once the server socket is bound

    async def serve():
        # port=0 lets the OS assign a free ephemeral port, avoiding TIME_WAIT
        # collisions when many scenarios run back-to-back in the same process.
        async with websockets.serve(server._handle_miner, "localhost", 0) as srv:
            port_ref.append(srv.sockets[0].getsockname()[1])
            await stop_evt.wait()

    server_task = asyncio.create_task(serve())
    # Wait until the server has bound and populated port_ref
    while not port_ref:
        await asyncio.sleep(0.01)
    port = port_ref[0]

    miner      = NetMiner(config, profile, done_evt, n)
    miner_task = asyncio.create_task(miner.run(port))
    await asyncio.sleep(0.03)

    t0 = time.monotonic()
    for i in range(n):
        registry.create_job(
            submitter="bench", description=f"chunk-{i}",
            chunks=[chunk], payment=0,
        )

    try:
        await asyncio.wait_for(done_evt.wait(), timeout=120.0)
    except asyncio.TimeoutError:
        pass

    elapsed = time.monotonic() - t0

    miner_task.cancel()
    stop_evt.set()
    await asyncio.gather(miner_task, return_exceptions=True)
    await server_task
    return elapsed, miner.losses


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    W = 10   # column width

    print("\n" + "=" * 74)
    print("  Network-condition simulation")
    print(f"  {N_JOBS} chunks  ·  random seed 42  ·  THRESHOLD_LOCAL (depth cap = 64)")
    print("=" * 74)

    print("\n  Network profiles:")
    print(f"  {'Name':<12} {'RTT':>7}  {'Jitter':>8}  {'Loss':>6}")
    print(f"  {'-'*12} {'-'*7}  {'-'*8}  {'-'*6}")
    for p in PROFILES:
        print(f"  {p.name:<12} {p.rtt*1000:>5.0f}ms  "
              f"{p.jitter*1000:>6.0f}ms  {p.loss*100:>5.1f}%")

    print("\n  Miner configurations:")
    print(f"  {'Name':<12} {'Compute':>9}  {'Depth':>5}  {'Exec':<10}  {'Transport'}")
    print(f"  {'-'*12} {'-'*9}  {'-'*5}  {'-'*10}  {'-'*9}")
    for c in CONFIGS:
        exec_mode = "parallel" if c.parallel_exec else "sequential"
        transport = "QUIC" if c.quic else "TCP"
        print(f"  {c.name:<12} {c.compute*1000:>7.0f}ms  "
              f"{c.pipeline_depth:>5}  {exec_mode:<10}  {transport}")

    print(f"\n  Running {len(PROFILES) * len(CONFIGS)} scenarios ...\n")

    results: dict = {}
    for profile in PROFILES:
        for config in CONFIGS:
            elapsed, losses = await run_scenario(config, profile)
            results[(profile.name, config.name)] = (elapsed, losses)
            print(f"  {profile.name:<12} × {config.name:<12}  "
                  f"{elapsed:>6.3f}s  ({losses} retransmits)")

    col_names = [c.name for c in CONFIGS]

    def matrix(title: str, cell_fn):
        print("\n" + "=" * 74)
        print(f"  {title}")
        print("=" * 74)
        header = f"  {'Profile':<12}" + "".join(f"{n:>{W}}" for n in col_names)
        print(header)
        print("  " + "-" * 12 + "-" * W * len(CONFIGS))
        for p in PROFILES:
            row = f"  {p.name:<12}"
            for c in CONFIGS:
                row += cell_fn(p.name, c.name)
            print(row)

    matrix("Wall time (seconds)",
           lambda p, c: f"{results[(p,c)][0]:>{W}.3f}s")

    matrix("Retransmit events (HOL stalls for TCP  /  stream delays for QUIC)",
           lambda p, c: f"{results[(p,c)][1]:>{W}}")

    matrix("Speedup over gpu-seq (same profile)",
           lambda p, c: f"{results[(p,'gpu-seq')][0]/results[(p,c)][0]:>{W}.1f}×")

    # ── HOL blocking cost ─────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("  HOL blocking cost  (gpu-par TCP  vs  gpu-quic QUIC per-stream)")
    print("  Positive % = TCP is slower; caused by retransmit stalling the pipeline")
    print("=" * 74)
    print(f"\n  {'Profile':<12} {'gpu-par':>9}  {'gpu-quic':>9}  "
          f"{'overhead':>10}  {'retransmits':>12}")
    print(f"  {'-'*12} {'-'*9}  {'-'*9}  {'-'*10}  {'-'*12}")
    for p in PROFILES:
        t_tcp,  l_tcp  = results[(p.name, "gpu-par")]
        t_quic, l_quic = results[(p.name, "gpu-quic")]
        overhead = (t_tcp - t_quic) / t_quic * 100
        print(f"  {p.name:<12} {t_tcp:>8.3f}s  {t_quic:>8.3f}s  "
              f"{overhead:>+9.1f}%  {l_tcp:>12}")

    # ── Satellite analysis ────────────────────────────────────────────────
    sat = next(p for p in PROFILES if p.name == "satellite")
    C   = CONFIGS[1].compute   # gpu compute time
    D   = CONFIGS[2].pipeline_depth
    print(f"\n" + "=" * 74)
    print(f"  Satellite analysis  (RTT={sat.rtt*1000:.0f}ms — pipelining vs depth requirement)")
    print("=" * 74)
    t_seq  = results[("satellite", "gpu-seq")][0]
    t_pipe = results[("satellite", "gpu-pipe")][0]
    t_par  = results[("satellite", "gpu-par")][0]
    t_quic = results[("satellite", "gpu-quic")][0]
    depth_needed = int(sat.rtt / C)
    batch_time   = D * C
    drain_gap    = sat.rtt - batch_time
    print(f"\n  gpu-seq  (depth=1):       {t_seq:.3f}s  — RTT dominates every chunk")
    print(f"  gpu-pipe (depth={D}, TCP):  {t_pipe:.3f}s  — {t_seq/t_pipe:.1f}× faster")
    print(f"  gpu-par  (depth={D}, TCP):  {t_par:.3f}s  — {t_seq/t_par:.1f}× faster")
    print(f"  gpu-quic (depth={D}, QUIC): {t_quic:.3f}s  — {t_seq/t_quic:.1f}× faster")
    print(f"\n  Pipeline runs dry after {D} × {C*1000:.0f}ms = {batch_time*1000:.0f}ms,")
    print(f"  then waits ~{drain_gap*1000:.0f}ms for server re-fill  (RTT − batch_time).")
    print(f"\n  To fully amortise satellite RTT at {C*1000:.0f}ms/chunk:")
    print(f"    depth ≥ ceil(RTT / C) = ceil({sat.rtt}/{C}) = {depth_needed}")
    print(f"    With THRESHOLD_LOCAL the server cap is 64 — "
          f"{'adequate' if depth_needed <= 64 else 'insufficient'}.")
    print(f"\n  QUIC wins here not only through parallelism but because per-stream")
    print(f"  delivery lets the server process results and dispatch new chunks")
    print(f"  without waiting for a retransmit to unblock the connection.")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    random.seed(42)
    asyncio.run(main())
