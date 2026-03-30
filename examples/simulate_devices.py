"""
Multi-device simulation — four hardware tiers + parallel_exec benchmark.

Part A: Multi-device dispatch
  cpu-1      integer-only, pipeline_depth=1   (basic CPU / embedded board)
  cpu-2      integer-only, pipeline_depth=1   (second CPU node)
  float-cpu  float-capable, pipeline_depth=1  (workstation CPU with FP64)
  gpu-rig    float + gpu,   pipeline_depth=4  (GPU — fast but needs pipelining)

  Job pool (each job is a single chunk — the typical data-parallel pattern):
    8 × Integer tasks   no requirements   → any miner
    3 × Float tasks     requires "float"  → float-cpu or gpu-rig
    3 × GPU tasks       requires "gpu"    → gpu-rig only

Part B: GPU pipeline × parallel_exec benchmark
  Three modes over 12 GPU-only chunks (100 ms simulated internet RTT):
    gpu-seq   depth=1, parallel_exec=False   — serial round-trips
    gpu-pipe  depth=4, parallel_exec=False   — RTT hidden, sequential exec
    gpu-par   depth=4, parallel_exec=True    — RTT hidden, parallel exec

  Expected wall times:
    gpu-seq:   N × (C + RTT)          =  12 × (40 + 100) ms = 1.68 s
    gpu-pipe:  N × C + RTT            =  12 × 40 + 100 ms   = 0.58 s
    gpu-par:   N × C / depth + RTT    =  12 × 40 / 4 + 100  = 0.22 s
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List

import websockets

from unbound.network.server import NodeServer
from unbound.registry.registry import Registry
from unbound.uvm.opcodes import ADD, MUL, OUTPUT, HALT, PUSH

# ── Compute speeds (simulated, seconds per chunk) ─────────────────────────────
SPEED: Dict[str, float] = {
    "cpu-1":     0.20,
    "cpu-2":     0.25,
    "float-cpu": 0.18,
    "gpu-rig":   0.04,
    "gpu-seq":   0.04,
    "gpu-pipe":  0.04,
    "gpu-par":   0.04,
}

# Simulated one-way network latency for Part B benchmark (seconds).
# Represents a real internet RTT of 100 ms split evenly across send/recv.
SIMULATED_RTT = 0.10

# ── Streams (no INPUT — self-contained execution) ─────────────────────────────
INT_STREAM   = [PUSH, 6, PUSH, 7, MUL, OUTPUT, HALT]            # 42
FLOAT_STREAM = [PUSH, 7, PUSH, 5, MUL, PUSH, 2, ADD, OUTPUT, HALT]  # 37
GPU_STREAM   = [PUSH, 3, PUSH, 3, ADD, PUSH, 2, MUL, OUTPUT, HALT]  # 12

# 14 jobs × 1 chunk each (Part A)
JOBS = (
    [{"desc": f"Integer task {i+1}", "stream": INT_STREAM,   "reqs": []}
     for i in range(8)]
  + [{"desc": f"Float task {i+1}",   "stream": FLOAT_STREAM, "reqs": ["float"]}
     for i in range(3)]
  + [{"desc": f"GPU task {i+1}",     "stream": GPU_STREAM,   "reqs": ["gpu"]}
     for i in range(3)]
)

PORT_A = 8771   # Part A
PORT_B = 8772   # Part B


# ── Simulation miner (Part A) ─────────────────────────────────────────────────

class SimMiner:
    """Simple pull/pipeline miner for the multi-device dispatch demo."""

    def __init__(self, label: str, caps: List[str], pipeline_depth: int = 1,
                 chunks_done: dict = None, first_chunk_at: dict = None,
                 last_chunk_at: dict = None, t0_ref: list = None):
        self.label          = label   # human-readable display name
        self.miner_id       = None    # assigned by server after registration
        self.caps           = caps
        self.pipeline_depth = pipeline_depth
        self._chunks_done   = chunks_done    if chunks_done    is not None else {}
        self._first_at      = first_chunk_at if first_chunk_at is not None else {}
        self._last_at       = last_chunk_at  if last_chunk_at  is not None else {}
        self._t0_ref        = t0_ref         if t0_ref         is not None else [0.0]

    async def run(self, port: int):
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.send(json.dumps({
                "type": "register", "display_name": self.label,
                "capabilities": self.caps, "pipeline_depth": self.pipeline_depth,
                "volunteer": False, "stake": 0, "cached_cids": [],
            }))
            ack = json.loads(await ws.recv())
            self.miner_id = ack["miner_id"]
            if self.pipeline_depth > 1:
                await self._pipeline_loop(ws)
            else:
                await self._pull_loop(ws)

    async def _pull_loop(self, ws):
        while True:
            await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
            raw = await ws.recv()
            if isinstance(raw, str):
                if json.loads(raw).get("type") == "no_chunk":
                    await asyncio.sleep(0.05)
                continue
            await self._exec(ws, raw)

    async def _pipeline_loop(self, ws):
        await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg.get("type") == "no_chunk":
                    await asyncio.sleep(0.05)
                    await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            await self._exec(ws, raw)

    async def _exec(self, ws, raw: bytes):
        null_pos = raw.index(b"\x00")
        chunk_id = raw[:null_pos].decode()
        rest     = raw[null_pos + 1 + 8:]
        cid_len  = rest[0]
        payload  = rest[1 + cid_len:]

        await asyncio.sleep(SPEED[self.label])

        from unbound.uvm.vm import UVM
        result = UVM().execute(payload)

        now = time.monotonic() - self._t0_ref[0]
        self._chunks_done[self.label] = self._chunks_done.get(self.label, 0) + 1
        self._first_at.setdefault(self.label, now)
        self._last_at[self.label] = now

        n   = self._chunks_done[self.label]
        bar = "█" * n
        print(f"  [{now:5.2f}s] {self.label:<12} {bar}  ({n})")

        await ws.send(json.dumps({
            "type": "result", "chunk_id": chunk_id,
            "miner_id": self.miner_id, "result": result,
        }))


# ── Benchmark miner (Part B) ──────────────────────────────────────────────────

class BenchMiner:
    """
    GPU benchmark miner with three modes:
      depth=1,  parallel_exec=False  →  gpu-seq   (serial)
      depth=4,  parallel_exec=False  →  gpu-pipe  (pipelined, sequential exec)
      depth=4,  parallel_exec=True   →  gpu-par   (pipelined, parallel exec)

    simulated_rtt simulates one-way internet latency: a sleep before sending
    each result so the server-side pipeline benefit becomes measurable.
    """

    def __init__(self, label: str, pipeline_depth: int = 1,
                 parallel_exec: bool = False,
                 simulated_rtt: float = 0.0,
                 done_event: asyncio.Event = None,
                 n_chunks: int = 0):
        self.label          = label   # human-readable display name
        self.miner_id       = None    # assigned by server after registration
        self.pipeline_depth = pipeline_depth
        self.parallel_exec  = parallel_exec and pipeline_depth > 1
        self.simulated_rtt  = simulated_rtt
        self.done_event     = done_event
        self.n_chunks       = n_chunks
        self._count         = 0

    async def run(self, port: int):
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.send(json.dumps({
                "type": "register", "display_name": self.label,
                "capabilities": ["float", "gpu"],
                "pipeline_depth": self.pipeline_depth,
                "volunteer": False, "stake": 0, "cached_cids": [],
            }))
            ack = json.loads(await ws.recv())
            self.miner_id = ack["miner_id"]
            if self.pipeline_depth > 1:
                await self._pipeline_loop(ws)
            else:
                await self._pull_loop(ws)

    async def _pull_loop(self, ws):
        while True:
            await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
            raw = await ws.recv()
            if isinstance(raw, str):
                if json.loads(raw).get("type") == "no_chunk":
                    await asyncio.sleep(0.02)
                continue
            await self._exec(ws, raw)
            # Simulate RTT: gap between result reaching server and next chunk
            # arriving at this miner.  Without pipelining every round-trip adds
            # this delay; the pipeline_depth loop eliminates it by pre-filling.
            if self.simulated_rtt > 0:
                await asyncio.sleep(self.simulated_rtt)

    async def _pipeline_loop(self, ws):
        # Pre-queued chunks sit in the socket buffer — no RTT gap between
        # finishing one chunk and starting the next.
        await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg.get("type") == "no_chunk":
                    await asyncio.sleep(0.02)
                    await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            if self.parallel_exec:
                # Fire off each chunk in its own task — D chunks execute
                # concurrently so wall time drops from N×C to N×C/depth.
                asyncio.create_task(self._exec(ws, raw))
            else:
                await self._exec(ws, raw)

    async def _exec(self, ws, raw: bytes):
        null_pos = raw.index(b"\x00")
        chunk_id = raw[:null_pos].decode()
        rest     = raw[null_pos + 1 + 8:]
        cid_len  = rest[0]
        payload  = rest[1 + cid_len:]

        # Simulate GPU compute time
        await asyncio.sleep(SPEED[self.label])

        from unbound.uvm.vm import UVM
        result = UVM().execute(payload)

        await ws.send(json.dumps({
            "type": "result", "chunk_id": chunk_id,
            "miner_id": self.miner_id, "result": result,
        }))

        self._count += 1
        if self._count >= self.n_chunks and self.done_event is not None:
            self.done_event.set()


# ── Part A: multi-device dispatch ─────────────────────────────────────────────

async def run_part_a():
    chunks_done:    Dict[str, int]   = {}
    first_chunk_at: Dict[str, float] = {}
    last_chunk_at:  Dict[str, float] = {}
    t0_ref = [0.0]

    registry = Registry()
    server   = NodeServer(registry=registry, ws_host="localhost", ws_port=PORT_A)
    stop_evt = asyncio.Event()

    async def serve():
        async with websockets.serve(server._handle_miner, "localhost", PORT_A):
            await stop_evt.wait()

    server_task = asyncio.create_task(serve())
    await asyncio.sleep(0.1)

    shared = dict(chunks_done=chunks_done, first_chunk_at=first_chunk_at,
                  last_chunk_at=last_chunk_at, t0_ref=t0_ref)

    devices = [
        SimMiner("cpu-1",     [],               pipeline_depth=1, **shared),
        SimMiner("cpu-2",     [],               pipeline_depth=1, **shared),
        SimMiner("float-cpu", ["float"],         pipeline_depth=1, **shared),
        SimMiner("gpu-rig",   ["float", "gpu"],  pipeline_depth=4, **shared),
    ]

    total = len(JOBS)

    print("\n" + "="*66)
    print("  Part A — multi-device dispatch")
    print("="*66)
    print()
    print(f"  {'Name':<14} {'Capabilities':<22} {'ms/chunk':>9}  {'Pipeline depth'}")
    print(f"  {'-'*14} {'-'*22} {'-'*9}  {'-'*14}")
    for d in devices:
        c = d.caps if d.caps else ["integer"]
        print(f"  {d.label:<14} {str(c):<22} {SPEED[d.label]*1000:>7.0f}ms  "
              f"depth={d.pipeline_depth}")

    print(f"\n  Job pool: {total} jobs × 1 chunk each")
    for label, count in [("any", 8), ("float", 3), ("gpu", 3)]:
        print(f"    {count} × eligible={label}")
    print()

    miner_tasks = [asyncio.create_task(d.run(PORT_A)) for d in devices]
    await asyncio.sleep(0.15)

    t0_ref[0] = time.monotonic()
    print("  Dispatching jobs...\n")

    for j in JOBS:
        registry.create_job(
            submitter="sim", description=j["desc"],
            chunks=[j["stream"]], payment=0,
            requirements=j["reqs"],
            float_mode=("float" in j["reqs"]),
        )

    deadline = time.monotonic() + 15.0
    while sum(chunks_done.values()) < total:
        if time.monotonic() > deadline:
            print("\n  [timeout — some chunks not completed]")
            break
        await asyncio.sleep(0.02)

    elapsed = time.monotonic() - t0_ref[0]

    for t in miner_tasks:
        t.cancel()
    stop_evt.set()
    await asyncio.gather(*miner_tasks, return_exceptions=True)
    await server_task

    print("\n" + "="*66)
    print("  Summary")
    print("="*66)
    print(f"  {'Miner':<14} {'Done':>4}  {'First':>7}  {'Last':>7}  "
          f"{'Compute':>8}  {'Util%':>6}")
    print(f"  {'-'*14} {'-'*4}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*6}")
    for d in devices:
        lbl  = d.label
        n    = chunks_done.get(lbl, 0)
        if n == 0:
            continue
        first = first_chunk_at.get(lbl, 0)
        last  = last_chunk_at.get(lbl, elapsed)
        ideal = n * SPEED[lbl]
        util  = ideal / elapsed * 100
        print(f"  {lbl:<14} {n:>4}  {first:>6.2f}s  {last:>6.2f}s  "
              f"{ideal:>6.2f}s    {util:>5.1f}%")
    done = sum(chunks_done.values())
    print(f"\n  Wall time:   {elapsed:.2f}s   |   Chunks: {done}/{total}")


# ── Part B: GPU pipeline × parallel_exec benchmark ───────────────────────────

async def run_benchmark(label: str, pipeline_depth: int, parallel_exec: bool,
                        n_jobs: int, port: int) -> float:
    """Run one benchmark scenario. Returns wall time in seconds."""
    registry = Registry()
    server   = NodeServer(registry=registry, ws_host="localhost", ws_port=port)
    stop_evt = asyncio.Event()

    async def serve():
        async with websockets.serve(server._handle_miner, "localhost", port):
            await stop_evt.wait()

    server_task = asyncio.create_task(serve())
    await asyncio.sleep(0.1)

    done_evt = asyncio.Event()
    miner = BenchMiner(
        label=label,
        pipeline_depth=pipeline_depth,
        parallel_exec=parallel_exec,
        simulated_rtt=SIMULATED_RTT,
        done_event=done_evt,
        n_chunks=n_jobs,
    )

    miner_task = asyncio.create_task(miner.run(port))
    await asyncio.sleep(0.05)

    t0 = time.monotonic()
    for i in range(n_jobs):
        registry.create_job(
            submitter="bench", description=f"gpu-{i}",
            chunks=[GPU_STREAM], payment=0,
            requirements=["gpu"], float_mode=False,
        )

    await asyncio.wait_for(done_evt.wait(), timeout=30.0)
    elapsed = time.monotonic() - t0

    miner_task.cancel()
    stop_evt.set()
    await asyncio.gather(miner_task, return_exceptions=True)
    await server_task

    return elapsed


async def run_part_b():
    N   = 12
    C   = SPEED["gpu-seq"]         # 40 ms
    RTT = SIMULATED_RTT            # 100 ms
    D   = 4                        # pipeline_depth

    print("\n" + "="*66)
    print("  Part B — GPU pipeline × parallel_exec benchmark")
    print(f"  {N} GPU chunks,  compute={C*1000:.0f}ms each,  simulated RTT={RTT*1000:.0f}ms")
    print("="*66)
    print()

    scenarios = [
        ("gpu-seq",  1, False, "serial (depth=1)"),
        ("gpu-pipe", D, False, f"pipeline depth={D}, sequential exec"),
        ("gpu-par",  D, True,  f"pipeline depth={D}, parallel exec"),
    ]

    results = []
    for label, depth, par, desc in scenarios:
        # Use a unique port per scenario to avoid socket reuse races
        port = PORT_B + len(results)
        print(f"  Running {label:<10} ({desc}) ...", end="", flush=True)
        elapsed = await run_benchmark(label, depth, par, N, port)
        results.append((label, desc, depth, par, elapsed))
        print(f"  {elapsed:.3f}s")

    # ── Theoretical predictions ───────────────────────────────────────────────
    t_seq  = N * (C + RTT)
    t_pipe = N * C + RTT
    t_par  = N * C / D + RTT

    print()
    print(f"  {'Mode':<12} {'Description':<38} {'Actual':>8}  {'Theory':>8}  {'Saved vs seq':>12}")
    print(f"  {'-'*12} {'-'*38} {'-'*8}  {'-'*8}  {'-'*12}")

    seq_actual = results[0][4]
    theories   = [t_seq, t_pipe, t_par]
    for (label, desc, depth, par, actual), theory in zip(results, theories):
        saved = (seq_actual - actual) / seq_actual * 100
        saved_str = f"{saved:+.0f}%" if saved != 0 else "baseline"
        print(f"  {label:<12} {desc:<38} {actual:>7.3f}s  {theory:>7.3f}s  {saved_str:>12}")

    print()
    print(f"  Theory (N={N}, C={C*1000:.0f}ms, RTT={RTT*1000:.0f}ms, depth={D}):")
    print(f"    gpu-seq:   N × (C + RTT)       = {N} × {(C+RTT)*1000:.0f}ms = {t_seq:.3f}s")
    print(f"    gpu-pipe:  N × C + RTT          = {N} × {C*1000:.0f}ms + {RTT*1000:.0f}ms = {t_pipe:.3f}s")
    print(f"    gpu-par:   N × C / depth + RTT  = {N} × {C*1000:.0f}ms / {D} + {RTT*1000:.0f}ms = {t_par:.3f}s")
    print()
    pipe_saving = (t_seq - t_pipe) / t_seq * 100
    par_saving  = (t_seq - t_par)  / t_seq * 100
    print(f"  Pipeline alone saves  {pipe_saving:.0f}% of serial wall time")
    print(f"  Pipeline + parallel saves {par_saving:.0f}% of serial wall time")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    await run_part_a()
    await run_part_b()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
