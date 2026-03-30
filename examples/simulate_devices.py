"""
Multi-device simulation — four hardware tiers on one Unbound node.

Devices:
  cpu-1      integer-only, pipeline_depth=1   (basic CPU / embedded board)
  cpu-2      integer-only, pipeline_depth=1   (second CPU node)
  float-cpu  float-capable, pipeline_depth=1  (workstation CPU with FP64)
  gpu-rig    float + gpu,   pipeline_depth=4  (GPU — fast but needs pipelining)

Job pool (each job is a single chunk — the typical data-parallel pattern):
  8 × Integer tasks   no requirements   → any miner
  3 × Float tasks     requires "float"  → float-cpu or gpu-rig
  3 × GPU tasks       requires "gpu"    → gpu-rig only

One chunk per job means job-exclusion (structural non-collusion) is
irrelevant here — it becomes relevant in k-of-2 verification where a
miner cannot verify its own result.

The key observable: gpu-rig gets pipeline_depth=4 chunks dispatched
immediately on registration, stays continuously fed, and finishes its
share of work long before the CPU miners.
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
}

# ── Streams (no INPUT — self-contained execution) ─────────────────────────────
INT_STREAM   = [PUSH, 6, PUSH, 7, MUL, OUTPUT, HALT]            # 42
FLOAT_STREAM = [PUSH, 7, PUSH, 5, MUL, PUSH, 2, ADD, OUTPUT, HALT]  # 37
GPU_STREAM   = [PUSH, 3, PUSH, 3, ADD, PUSH, 2, MUL, OUTPUT, HALT]  # 12

# 14 jobs × 1 chunk each
JOBS = (
    [{"desc": f"Integer task {i+1}", "stream": INT_STREAM,   "reqs": []}
     for i in range(8)]
  + [{"desc": f"Float task {i+1}",   "stream": FLOAT_STREAM, "reqs": ["float"]}
     for i in range(3)]
  + [{"desc": f"GPU task {i+1}",     "stream": GPU_STREAM,   "reqs": ["gpu"]}
     for i in range(3)]
)

PORT = 8771

# ── Shared state ──────────────────────────────────────────────────────────────
chunks_done:    Dict[str, int]   = defaultdict(int)
first_chunk_at: Dict[str, float] = {}
last_chunk_at:  Dict[str, float] = {}
t0: float = 0.0


# ── Simulation miner ──────────────────────────────────────────────────────────

class SimMiner:
    def __init__(self, miner_id: str, caps: List[str], pipeline_depth: int = 1):
        self.miner_id      = miner_id
        self.caps          = caps
        self.pipeline_depth = pipeline_depth

    async def run(self):
        async with websockets.connect(f"ws://localhost:{PORT}") as ws:
            await ws.send(json.dumps({
                "type": "register", "miner_id": self.miner_id,
                "capabilities": self.caps, "pipeline_depth": self.pipeline_depth,
                "volunteer": False, "stake": 0, "cached_cids": [],
            }))
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
        # Server already pushed up to pipeline_depth frames on registration.
        # One kick request for robustness (handles retry / no-work case).
        await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            if isinstance(raw, str):
                if json.loads(raw).get("type") == "no_chunk":
                    await asyncio.sleep(0.05)
                    await ws.send(json.dumps({"type": "request_chunk", "miner_id": self.miner_id}))
                continue
            await self._exec(ws, raw)

    async def _exec(self, ws, raw: bytes):
        null_pos = raw.index(b"\x00")
        chunk_id = raw[:null_pos].decode()
        rest     = raw[null_pos + 1 + 8:]    # skip 8-byte job_token
        cid_len  = rest[0]
        payload  = rest[1 + cid_len:]

        # Simulate hardware compute time
        await asyncio.sleep(SPEED[self.miner_id])

        from unbound.uvm.vm import UVM
        result = UVM().execute(payload)

        now = time.monotonic() - t0
        chunks_done[self.miner_id] += 1
        first_chunk_at.setdefault(self.miner_id, now)
        last_chunk_at[self.miner_id] = now

        n = chunks_done[self.miner_id]
        bar = "█" * n
        print(f"  [{now:5.2f}s] {self.miner_id:<12} {bar}  ({n})")

        await ws.send(json.dumps({
            "type": "result", "chunk_id": chunk_id,
            "miner_id": self.miner_id, "result": result,
        }))


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    global t0

    registry  = Registry()
    server    = NodeServer(registry=registry, ws_host="localhost", ws_port=PORT)
    stop_evt  = asyncio.Event()

    async def serve():
        async with websockets.serve(server._handle_miner, "localhost", PORT):
            await stop_evt.wait()

    server_task = asyncio.create_task(serve())
    await asyncio.sleep(0.1)

    devices = [
        SimMiner("cpu-1",     [],              pipeline_depth=1),
        SimMiner("cpu-2",     [],              pipeline_depth=1),
        SimMiner("float-cpu", ["float"],        pipeline_depth=1),
        SimMiner("gpu-rig",   ["float", "gpu"], pipeline_depth=4),
    ]

    total = len(JOBS)

    print("\n" + "="*64)
    print("  Unbound — multi-device simulation")
    print("="*64)
    print()
    print("  Devices")
    print(f"  {'Name':<14} {'Capabilities':<22} {'ms/chunk':>9}  {'Pipeline depth'}")
    print(f"  {'-'*14} {'-'*22} {'-'*9}  {'-'*14}")
    for d in devices:
        c = d.caps if d.caps else ["integer"]
        print(f"  {d.miner_id:<14} {str(c):<22} {SPEED[d.miner_id]*1000:>7.0f}ms  "
              f"depth={d.pipeline_depth}")

    print(f"\n  Job pool: {total} jobs × 1 chunk each")
    counts = {"any": 8, "float": 3, "gpu": 3}
    for k, v in counts.items():
        print(f"    {v} × eligible={k}")
    print()

    miner_tasks = [asyncio.create_task(d.run()) for d in devices]
    await asyncio.sleep(0.15)

    t0 = time.monotonic()
    print("  Dispatching jobs...\n")

    for j in JOBS:
        registry.create_job(
            submitter="sim", description=j["desc"],
            chunks=[j["stream"]], payment=0,
            requirements=j["reqs"],
            float_mode=("float" in j["reqs"]),
        )

    # Wait for all chunks to complete (timeout 15s)
    deadline = time.monotonic() + 15.0
    while sum(chunks_done.values()) < total:
        if time.monotonic() > deadline:
            print("\n  [timeout — some chunks not completed]")
            break
        await asyncio.sleep(0.02)

    elapsed = time.monotonic() - t0

    for t in miner_tasks:
        t.cancel()
    stop_evt.set()
    await asyncio.gather(*miner_tasks, return_exceptions=True)
    await server_task

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "="*64)
    print("  Summary")
    print("="*64)
    print(f"  {'Miner':<14} {'Done':>4}  {'First':>7}  {'Last':>7}  "
          f"{'Compute':>8}  {'Util%':>6}")
    print(f"  {'-'*14} {'-'*4}  {'-'*7}  {'-'*7}  {'-'*8}  {'-'*6}")

    for d in devices:
        mid   = d.miner_id
        n     = chunks_done.get(mid, 0)
        if n == 0:
            continue
        first = first_chunk_at.get(mid, 0)
        last  = last_chunk_at.get(mid, elapsed)
        ideal = n * SPEED[mid]
        util  = ideal / elapsed * 100
        print(f"  {mid:<14} {n:>4}  {first:>6.2f}s  {last:>6.2f}s  "
              f"{ideal:>6.2f}s    {util:>5.1f}%")

    done = sum(chunks_done.values())
    print(f"\n  Wall time:   {elapsed:.2f}s   |   Chunks: {done}/{total}")

    # ── GPU pipeline analysis ─────────────────────────────────────────────────
    gpu_n     = chunks_done.get("gpu-rig", 0)
    rtt       = 0.10   # simulated 100 ms internet RTT
    serial    = gpu_n * (SPEED["gpu-rig"] + rtt)
    pipelined = gpu_n * SPEED["gpu-rig"] + rtt   # RTT amortised
    saved     = serial - pipelined

    print(f"\n  GPU pipeline analysis  (100 ms simulated internet RTT)")
    print(f"  {'─'*50}")
    print(f"  gpu-rig: {gpu_n} chunks  @  {SPEED['gpu-rig']*1000:.0f} ms compute each")
    print()
    print(f"  Without pipeline_depth:")
    print(f"    {gpu_n} × ({SPEED['gpu-rig']*1000:.0f}ms compute + 100ms RTT) = {serial:.2f}s")
    print(f"    GPU utilisation: {SPEED['gpu-rig']/(SPEED['gpu-rig']+rtt)*100:.0f}%")
    print()
    print(f"  With pipeline_depth=4:")
    print(f"    {gpu_n} × {SPEED['gpu-rig']*1000:.0f}ms compute  (RTT hidden) = {pipelined:.3f}s")
    print(f"    GPU utilisation: ~100%")
    print()
    print(f"  Network overhead saved: {saved:.2f}s  "
          f"({saved/serial*100:.0f}% of serial wall time)")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
